"""Build reviewable Hamburg movement geometry from official data and aerial imagery.

Official MAP/KML remains the movement authority. A rectangle-guided trace may
replace one official drive line only when its two directed mean errors stay
within the declared limit. Every other movement keeps the official geometry.
"""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from pyproj import Transformer
from scipy import ndimage
from scipy.spatial import ConvexHull

from .aerial_curve_trace import curve_has_support, trace_probability_curve
from .candidate_contracts import file_sha256
from .digital_twin import parse_mapem
from .hamburg_map_kml import bind_hamburg_map_kml_to_mapem, parse_hamburg_map_kml
from .network_source_policy import _year, resolve_network_source_policy


REQUEST_SCHEMA = "torii.hamburg-aerial-movement-request/v1"
PLAN_SCHEMA = "torii.hamburg-aerial-movement-plan/v1"
CORRIDOR_SCHEMA = "torii.hamburg-aerial-corridor-plan/v1"
Point = tuple[float, float]
Bbox = tuple[float, float, float, float]


def build_aerial_road_likelihood(
    image: Image.Image,
    *,
    allowed_polygon: Sequence[Point],
    bbox: Bbox,
    road_prior: np.ndarray | None = None,
) -> np.ndarray:
    """Return a normalized road-likelihood raster inside one declared area."""
    _validate_bbox(bbox)
    if len(allowed_polygon) < 3:
        raise ValueError("allowed_polygon must contain at least three points")
    rgb = np.asarray(image.convert("RGB"), dtype=float) / 255.0
    brightness = rgb.mean(axis=2)
    saturation = rgb.max(axis=2) - rgb.min(axis=2)
    gray_surface = np.exp(-((brightness - 0.68) / 0.30) ** 2) * np.exp(-saturation / 0.22)
    vegetation = np.clip((rgb[:, :, 1] - (rgb[:, :, 0] + rgb[:, :, 2]) / 2) * 5.0, 0.0, 1.0)
    cycle_red = np.clip((rgb[:, :, 0] - rgb[:, :, 1]) * 4.0, 0.0, 1.0)

    mask_image = Image.new("1", image.size, 0)
    ImageDraw.Draw(mask_image).polygon(
        [_world_to_pixel(point, image.size, bbox) for point in allowed_polygon],
        fill=1,
    )
    allowed = np.asarray(mask_image, dtype=bool)
    raw = gray_surface * (1.0 - vegetation) * (1.0 - 0.75 * cycle_red) * allowed
    road = ndimage.binary_closing(raw > 0.18, iterations=2) & allowed
    clearance = np.minimum(ndimage.distance_transform_edt(road) / 18.0, 1.0)
    likelihood = (0.65 * ndimage.gaussian_filter(raw, 2.0) + 0.35 * clearance) * allowed
    if road_prior is not None:
        if road_prior.shape != likelihood.shape or not np.isfinite(road_prior).all() or np.min(road_prior) < 0.35 - 1e-6 or np.max(road_prior) > 1:
            raise ValueError("road prior must match the image and remain within [0.35, 1]")
        likelihood *= road_prior
    return likelihood / max(float(likelihood.max()), 1e-9)


def select_movement_geometry(
    official_shape: Sequence[Point],
    traced_shape: Sequence[Point] | None,
    *,
    max_error_m: float,
    target_year: int | None = None,
    aerial_year: int | None = None,
) -> dict[str, Any]:
    """Select a trace only when both directed errors meet the declared limit."""
    official = _points(official_shape, "official_shape")
    if not math.isfinite(max_error_m) or max_error_m <= 0:
        raise ValueError("max_error_m must be finite and positive")
    _year(target_year, 'target_year')
    _year(aerial_year, 'aerial_year')
    if target_year is not None and aerial_year != target_year:
        return dict(selected_source='official_map', selected_shape=official, reason='aerial_not_from_target_year',
                    mean_trace_to_official_m=None, mean_official_to_trace_m=None)
    if traced_shape is None:
        return {
            "selected_source": "official_map",
            "selected_shape": official,
            "reason": "trace_failed",
            "mean_trace_to_official_m": None,
            "mean_official_to_trace_m": None,
        }
    traced = _points(traced_shape, "traced_shape")
    trace_to_official = _mean_to_polyline(traced, official)
    official_to_trace = _mean_to_polyline(official, traced)
    accepted = max(trace_to_official, official_to_trace) <= max_error_m
    return {
        "selected_source": "aerial_trace" if accepted else "official_map",
        "selected_shape": traced if accepted else official,
        "reason": "trace_error_within_limit" if accepted else "trace_error_exceeds_limit",
        "mean_trace_to_official_m": round(trace_to_official, 6),
        "mean_official_to_trace_m": round(official_to_trace, 6),
    }


def build_hamburg_aerial_corridor_plan(
    *,
    request_file: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Build immutable movement plans for every intersection in one request."""
    request_path = Path(request_file).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    request = _read_request(request_path)
    source_policy = resolve_network_source_policy({'intersections': request['intersections'],
        'scenario': {'target_year': request.get('target_year')}})
    if destination.exists():
        raise ValueError("output_dir must not already exist")
    source_paths = [
        Path(row[key]["path"]).expanduser().resolve()
        for row in request["intersections"]
        for key in ("map_xml", "map_kml", "aerial_image", "road_prior") if key in row
    ]
    if any(_is_relative_to(path, destination) for path in source_paths):
        raise ValueError("output_dir must not contain source artifacts")
    for row in request["intersections"]:
        _validate_source_hashes(
            {role: Path(row[role]["path"]) for role in ("map_xml", "map_kml", "aerial_image", "road_prior") if role in row},
            {role: str(row[role]["sha256"]) for role in ("map_xml", "map_kml", "aerial_image", "road_prior") if role in row},
        )
    destination.mkdir(parents=True)

    plans = []
    for row in request["intersections"]:
        node_id = str(row["node_id"])
        plan = build_hamburg_aerial_movement_plan(
            node_id=node_id,
            map_xml_file=Path(row["map_xml"]["path"]),
            map_kml_file=Path(row["map_kml"]["path"]),
            aerial_image_file=Path(row["aerial_image"]["path"]),
            bbox=tuple(float(value) for value in row["bbox_epsg25832"]),
            aerial_year=int(row["aerial_year"]),
            max_error_m=float(request["max_error_m"]),
            target_year=source_policy['target_year'],
            output_dir=destination / f"lsa-{node_id}",
            expected_sha256={
                "map_xml": str(row["map_xml"]["sha256"]),
                "map_kml": str(row["map_kml"]["sha256"]),
                "aerial_image": str(row["aerial_image"]["sha256"]),
            },
            road_prior=row.get("road_prior"),
        )
        plans.append(plan)

    totals = {
        "official_vehicle_movements": sum(plan["counts"]["official_vehicle_movements"] for plan in plans),
        "accepted_aerial_traces": sum(plan["counts"]["accepted_aerial_traces"] for plan in plans),
        "official_map_fallbacks": sum(plan["counts"]["official_map_fallbacks"] for plan in plans),
        "trace_failures": sum(plan["counts"]["trace_failures"] for plan in plans),
    }
    totals["automatic_acceptance_fraction"] = round(
        totals["accepted_aerial_traces"] / max(totals["official_vehicle_movements"], 1),
        6,
    )
    overview = destination / "corridor-movement-overview.png"
    _render_corridor_overview(plans, overview)
    report = {
        "schema": CORRIDOR_SCHEMA,
        "status": "pass",
        "decision": "review_required",
        "claim_status": "diagnostic-demo",
        "crs": "EPSG:25832",
        "request": {"path": str(request_path), "sha256": file_sha256(request_path)},
        "max_error_m": float(request["max_error_m"]),
        "ordered_node_ids": [str(row["node_id"]) for row in request["intersections"]],
        "totals": totals,
        "intersections": [
            {
                "node_id": plan["node_id"],
                "status": plan["status"],
                "decision": plan["decision"],
                "counts": plan["counts"],
                "plan_file": plan["artifacts"]["plan"]["path"],
                "plan_sha256": plan["artifacts"]["plan"]["sha256"],
                "review_image": plan["artifacts"]["review_image"]["path"],
            }
            for plan in plans
        ],
        "artifacts": {
            "overview": {"path": str(overview), "sha256": file_sha256(overview)},
        },
        "gates": {
            "all_official_movements_have_selected_geometry": "pass",
            "source_hashes_match_request": "pass",
            "combined_sumo_network": "not_run",
            "physical_conflict_core_classification": "not_run",
        },
        "claim_boundary": (
            "The corridor plan selects movement geometry for review. It does not classify physical conflict cores, "
            "write SUMO connections, reconstruct signal timing, or validate a combined corridor network. "
            "Full official geometry is used for trace acceptance and fallback, so the comparison is not independent validation."
        ),
    }
    summary_file = destination / "corridor-summary.json"
    _write_json(summary_file, report)
    report["artifacts"]["summary"] = {"path": str(summary_file), "sha256": file_sha256(summary_file)}
    return report


def build_hamburg_aerial_movement_plan(
    *,
    node_id: str,
    map_xml_file: Path | str,
    map_kml_file: Path | str,
    aerial_image_file: Path | str,
    bbox: Bbox,
    aerial_year: int,
    max_error_m: float,
    output_dir: Path | str,
    expected_sha256: Mapping[str, str],
    road_prior: Mapping[str, Any] | None = None,
    target_year: int | None = None,
) -> dict[str, Any]:
    """Select one complete set of official or aerial-traced movement shapes."""
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise ValueError("intersection output directory must not already exist")
    sources = {
        "map_xml": Path(map_xml_file).expanduser().resolve(),
        "map_kml": Path(map_kml_file).expanduser().resolve(),
        "aerial_image": Path(aerial_image_file).expanduser().resolve(),
    }
    _validate_source_hashes(sources, expected_sha256)
    _validate_bbox(bbox)
    prior = None
    if road_prior is not None:
        if road_prior.get("bbox_epsg25832") != list(bbox) or road_prior.get("image_sha256") != expected_sha256["aerial_image"]:
            raise ValueError("road prior does not match the exact aerial image and bounds")
        prior_path = Path(road_prior["path"]).resolve(strict=True)
        _validate_source_hashes({"road_prior": prior_path}, {"road_prior": str(road_prior["sha256"])})
        prior = np.load(prior_path, allow_pickle=False)
        sources["road_prior"] = prior_path
        expected_sha256 = {**expected_sha256, "road_prior": str(road_prior["sha256"])}
    destination.mkdir(parents=True)

    map_lanes, map_connections = parse_mapem(sources["map_xml"])
    intersection_parts = _intersection_part_by_lane_pair(sources["map_xml"])
    binding = bind_hamburg_map_kml_to_mapem(
        parse_hamburg_map_kml(sources["map_kml"]),
        map_lanes,
        map_connections,
        expected_node_id=node_id,
    )
    lane_types = {
        str(lane["lane_id"]): str(lane["lane_type"]).lower()
        for lane in binding["lanes"]
    }
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
    map_lane_metadata = {str(lane.lane_id): lane.permission_metadata for lane in map_lanes}
    lanes = [
        {
            "lane_id": str(lane["lane_id"]),
            "lane_type": str(lane["lane_type"]),
            "direction_role": str(lane["kml_direction_role"]),
            "ingress_approach": str(lane["ingress_approach"]),
            "egress_approach": str(lane["egress_approach"]),
            **map_lane_metadata[str(lane["lane_id"])],
            "shape_epsg25832": [
                list(point)
                for point in _points(_project_coordinates(lane["coordinates"], transformer), "official lane shape")
            ],
        }
        for lane in binding["lanes"]
    ]
    movements = []
    for connection in binding["connections"]:
        if lane_types.get(str(connection["ingress_lane_id"])) != "vehicle":
            continue
        if lane_types.get(str(connection["egress_lane_id"])) != "vehicle":
            continue
        official = _project_coordinates(
            connection.get("drive_line_coordinates") or [],
            transformer,
        )
        if len(official) >= 2 and math.dist(official[0], official[-1]) > 1.0:
            movements.append((connection, official))
    if not movements:
        raise ValueError("official MAP/KML binding has no usable vehicle movements")

    image = Image.open(sources["aerial_image"]).convert("RGB")
    likelihood = build_aerial_road_likelihood(
        image,
        allowed_polygon=_endpoint_hull([official for _, official in movements]),
        bbox=bbox,
        road_prior=prior,
    )
    pixels_per_m = image.width / (bbox[2] - bbox[0])
    results = []
    raw_traces = []
    for connection, official in movements:
        start_pixel = _world_to_pixel(official[0], image.size, bbox)
        end_pixel = _world_to_pixel(official[-1], image.size, bbox)
        start_heading = _pixel_heading(official[0], official[1], image.size, bbox)
        end_heading = _pixel_heading(official[-2], official[-1], image.size, bbox)
        guide = _best_endpoint_guide(
            likelihood,
            start_pixel,
            end_pixel,
            start_heading,
            end_heading,
        )
        traced: list[Point] | None
        trace_error = ""
        try:
            guided = _guided_probability(likelihood, guide)
            traced_pixels = trace_probability_curve(
                guided,
                start=start_pixel,
                end=end_pixel,
                start_heading_deg=start_heading,
                end_heading_deg=end_heading,
                step_px=0.7 * pixels_per_m,
                probe_length_px=2.4 * pixels_per_m,
                probe_width_px=1.6 * pixels_per_m,
                beam_width=28,
            )
            smooth_pixels = _smooth(traced_pixels)
            # Smoothing must not reintroduce a shortcut across unsupported image space.
            selected_pixels = smooth_pixels if curve_has_support(guided, smooth_pixels) else traced_pixels
            traced = [_pixel_to_world(point, image.size, bbox) for point in selected_pixels]
            raw_traces.append(traced)
        except ValueError as error:
            traced = None
            trace_error = str(error)
        selected = select_movement_geometry(
            official,
            traced,
            max_error_m=max_error_m,
            target_year=target_year, aerial_year=aerial_year,
        )
        results.append(
            {
                "movement_id": str(connection["connection_id"]),
                "ingress_lane_id": str(connection["ingress_lane_id"]),
                "egress_lane_id": str(connection["egress_lane_id"]),
                "signal_group": str(connection["signal_group"]),
                "intersection_part": intersection_parts.get(
                    (
                        str(connection["ingress_lane_id"]),
                        str(connection["egress_lane_id"]),
                    ),
                    "0",
                ),
                "selected_source": selected["selected_source"],
                "reason": selected["reason"],
                "trace_error": trace_error,
                "mean_trace_to_official_m": selected["mean_trace_to_official_m"],
                "mean_official_to_trace_m": selected["mean_official_to_trace_m"],
                "selected_shape_epsg25832": [list(point) for point in selected["selected_shape"]],
                "official_shape_epsg25832": [list(point) for point in official],
                "traced_shape_epsg25832": (
                    [list(point) for point in traced] if traced is not None else None
                ),
            }
        )

    plan_file = destination / "selected-movements.json"
    plan_payload = {
        "schema": PLAN_SCHEMA,
        "status": "pass",
        "decision": "review_required",
        "claim_status": "diagnostic-demo",
        "node_id": node_id,
        "crs": "EPSG:25832",
        "max_error_m": max_error_m,
        "inputs": {
            role: {"path": str(path), "sha256": file_sha256(path)}
            for role, path in sources.items()
        },
        "aerial_year": aerial_year,
        "target_year": target_year,
        "aerial_geometry_role": 'candidate_geometry' if target_year is None or aerial_year == target_year else 'context_only',
        "bbox_epsg25832": list(bbox),
        "lanes": lanes,
        "movements": results,
        "selection_reference_basis": "official_geometry_used_for_acceptance_and_fallback",
        "independent_geometry_validation": False,
        "claim_boundary": (
            "Official MAP/KML remains authoritative. Aerial tracing changes only review candidate geometry "
            "and does not authorize SUMO materialization. Full official geometry is used for trace acceptance "
            "and fallback, so the comparison is not independent validation."
        ),
    }
    _write_json(plan_file, plan_payload)
    review_image = destination / "movement-review.png"
    _render_intersection_review(
        image=image,
        likelihood=likelihood,
        bbox=bbox,
        official=[official for _, official in movements],
        traced=raw_traces,
        selected=results,
        output=review_image,
    )
    counts = {
        "official_vehicle_movements": len(results),
        "accepted_aerial_traces": sum(row["selected_source"] == "aerial_trace" for row in results),
        "official_map_fallbacks": sum(row["selected_source"] == "official_map" for row in results),
        "trace_failures": sum(row["reason"] == "trace_failed" for row in results),
    }
    return {
        "schema": PLAN_SCHEMA,
        "status": "pass",
        "decision": "review_required",
        "claim_status": "diagnostic-demo",
        "node_id": node_id,
        "crs": "EPSG:25832",
        "counts": counts,
        "inputs": {
            role: {"path": str(path), "sha256": file_sha256(path)}
            for role, path in sources.items()
        },
        "aerial_year": aerial_year,
        "bbox_epsg25832": list(bbox),
        "artifacts": {
            "plan": {"path": str(plan_file), "sha256": file_sha256(plan_file)},
            "review_image": {"path": str(review_image), "sha256": file_sha256(review_image)},
        },
    }


def _read_request(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid UTF-8 request JSON: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema") != REQUEST_SCHEMA:
        raise ValueError(f"request schema must be {REQUEST_SCHEMA}")
    rows = payload.get("intersections")
    if not isinstance(rows, list) or not rows:
        raise ValueError("request intersections must be a non-empty list")
    node_ids = [str(row.get("node_id", "")) for row in rows if isinstance(row, dict)]
    if len(node_ids) != len(rows) or any(not value for value in node_ids):
        raise ValueError("each request intersection must have a node_id")
    if any(value in {".", ".."} or any(character in value for character in "/\\:") for value in node_ids):
        raise ValueError("request node_id must be an identifier, not a path")
    if len(set(node_ids)) != len(node_ids):
        raise ValueError("request node_id values must be unique")
    max_error = payload.get("max_error_m")
    if (
        isinstance(max_error, bool)
        or not isinstance(max_error, (int, float))
        or not math.isfinite(max_error)
        or max_error <= 0
    ):
        raise ValueError("request max_error_m must be finite and positive")
    for row in rows:
        if not isinstance(row.get("bbox_epsg25832"), list) or len(row["bbox_epsg25832"]) != 4:
            raise ValueError("each intersection bbox_epsg25832 must contain four values")
        for role in ("map_xml", "map_kml", "aerial_image", "road_prior"):
            if role == "road_prior" and role not in row:
                continue
            artifact = row.get(role)
            if not isinstance(artifact, dict) or not artifact.get("path") or not artifact.get("sha256"):
                raise ValueError(f"each intersection {role} must contain path and sha256")
            source = Path(artifact["path"]).expanduser()
            artifact["path"] = str((source if source.is_absolute() else path.parent / source).resolve())
        if isinstance(row.get("aerial_year"), bool) or not isinstance(row.get("aerial_year"), int):
            raise ValueError("each intersection aerial_year must be an integer")
    return payload


def _intersection_part_by_lane_pair(path: Path) -> dict[tuple[str, str], str]:
    root = ET.parse(path).getroot()
    result = {}
    for stream in root.iter():
        if stream.tag.split("}")[-1] != "TrafficStreamConfigData":
            continue
        values = {
            child.tag.split("}")[-1]: (child.text or "").strip()
            for child in stream
        }
        ingress, egress = values.get("refLaneId", ""), values.get("refConnectTo", "")
        if ingress and egress:
            result[(ingress, egress)] = values.get("intersectionPart", "0") or "0"
    return result


def _validate_source_hashes(
    sources: Mapping[str, Path],
    expected: Mapping[str, str],
) -> None:
    for role, path in sources.items():
        if not path.is_file():
            raise ValueError(f"{role} is not a file: {path}")
        digest = str(expected.get(role, "")).lower()
        if len(digest) != 64 or file_sha256(path).lower() != digest:
            raise ValueError(f"{role} SHA-256 does not match the request")


def _endpoint_hull(movements: Sequence[Sequence[Point]], margin_m: float = 8.0) -> list[Point]:
    points = []
    for movement in movements:
        for x, y in (movement[0], movement[-1]):
            points.extend(
                [
                    (x - margin_m, y - margin_m),
                    (x - margin_m, y + margin_m),
                    (x + margin_m, y - margin_m),
                    (x + margin_m, y + margin_m),
                ]
            )
    hull = ConvexHull(np.asarray(points, dtype=float))
    return [points[index] for index in hull.vertices]


def _endpoint_guide(
    start: Point,
    end: Point,
    start_heading_deg: float,
    end_heading_deg: float,
    *,
    control_fraction: float,
) -> list[Point]:
    distance = math.dist(start, end)
    control = min(distance * control_fraction, 320.0)
    start_angle, end_angle = math.radians(start_heading_deg), math.radians(end_heading_deg)
    first = (
        start[0] + math.cos(start_angle) * control,
        start[1] + math.sin(start_angle) * control,
    )
    second = (
        end[0] - math.cos(end_angle) * control,
        end[1] - math.sin(end_angle) * control,
    )
    return [
        (
            (1 - t) ** 3 * start[0]
            + 3 * (1 - t) ** 2 * t * first[0]
            + 3 * (1 - t) * t**2 * second[0]
            + t**3 * end[0],
            (1 - t) ** 3 * start[1]
            + 3 * (1 - t) ** 2 * t * first[1]
            + 3 * (1 - t) * t**2 * second[1]
            + t**3 * end[1],
        )
        for t in np.linspace(0.0, 1.0, 120)
    ]


def _best_endpoint_guide(
    likelihood: np.ndarray,
    start: Point,
    end: Point,
    start_heading: float,
    end_heading: float,
) -> list[Point]:
    guides = [
        _endpoint_guide(
            start,
            end,
            start_heading,
            end_heading,
            control_fraction=fraction,
        )
        for fraction in (0.35, 0.5, 0.65, 0.8)
    ]

    def score(guide: Sequence[Point]) -> float:
        values = [
            likelihood[round(y), round(x)]
            for x, y in guide
            if 0 <= round(x) < likelihood.shape[1]
            and 0 <= round(y) < likelihood.shape[0]
        ]
        return float(np.mean(values)) if values else -1.0

    return max(guides, key=score)


def _guided_probability(likelihood: np.ndarray, guide: Sequence[Point]) -> np.ndarray:
    image = Image.new("1", (likelihood.shape[1], likelihood.shape[0]), 0)
    ImageDraw.Draw(image).line(list(guide), fill=1, width=3)
    distance = ndimage.distance_transform_edt(~np.asarray(image, dtype=bool))
    return likelihood * (0.03 + 0.97 * np.exp(-0.5 * (distance / 18.0) ** 2))


def _smooth(points: Sequence[Point]) -> list[Point]:
    values = list(points)
    for _ in range(4):
        values = [
            values[0],
            *[
                ((a[0] + 2 * b[0] + c[0]) / 4, (a[1] + 2 * b[1] + c[1]) / 4)
                for a, b, c in zip(values, values[1:], values[2:])
            ],
            values[-1],
        ]
    return values


def _project_coordinates(
    coordinates: Sequence[Sequence[float]],
    transformer: Transformer,
) -> list[Point]:
    result = []
    for coordinate in coordinates:
        point = transformer.transform(float(coordinate[0]), float(coordinate[1]))
        if not result or math.dist(result[-1], point) > 0.01:
            result.append(point)
    return result


def _render_intersection_review(
    *,
    image: Image.Image,
    likelihood: np.ndarray,
    bbox: Bbox,
    official: Sequence[Sequence[Point]],
    traced: Sequence[Sequence[Point]],
    selected: Sequence[Mapping[str, Any]],
    output: Path,
) -> None:
    width, height = image.size
    canvas = Image.new("RGB", (width * 4, height), "white")
    official_image, trace_image, selected_image = image.copy(), image.copy(), image.copy()
    official_draw = ImageDraw.Draw(official_image)
    trace_draw = ImageDraw.Draw(trace_image)
    selected_draw = ImageDraw.Draw(selected_image)
    for line in official:
        official_draw.line(
            [_world_to_pixel(point, image.size, bbox) for point in line],
            fill=(0, 220, 255),
            width=4,
        )
    for line in traced:
        trace_draw.line(
            [_world_to_pixel(point, image.size, bbox) for point in line],
            fill=(255, 0, 180),
            width=4,
        )
    for movement in selected:
        color = (20, 190, 80) if movement["selected_source"] == "aerial_trace" else (255, 145, 0)
        selected_draw.line(
            [
                _world_to_pixel(tuple(point), image.size, bbox)
                for point in movement["selected_shape_epsg25832"]
            ],
            fill=color,
            width=4,
        )
    likelihood_image = Image.fromarray(
        np.uint8(np.clip(likelihood, 0, 1) * 255),
        mode="L",
    ).convert("RGB")
    for index, panel in enumerate(
        (official_image, likelihood_image, trace_image, selected_image)
    ):
        canvas.paste(panel, (index * width, 0))
    draw = ImageDraw.Draw(canvas)
    for index, label in enumerate(
        ("official MAP", "aerial road likelihood", "raw traces", "selected: green trace / orange official")
    ):
        draw.text(
            (index * width + 12, 12),
            label,
            fill="white",
            stroke_width=2,
            stroke_fill="black",
        )
    canvas.save(output)


def _render_corridor_overview(plans: Sequence[Mapping[str, Any]], output: Path) -> None:
    tiles = []
    for plan in plans:
        source = Image.open(plan["artifacts"]["review_image"]["path"]).convert("RGB")
        width = source.width // 4
        selected = source.crop((width * 3, 0, width * 4, source.height))
        selected.thumbnail((700, 500))
        tile = Image.new("RGB", (720, 540), "white")
        tile.paste(selected, ((720 - selected.width) // 2, 35))
        ImageDraw.Draw(tile).text((12, 10), f"LSA {plan['node_id']}", fill="black")
        tiles.append(tile)
    columns = min(3, len(tiles))
    rows = math.ceil(len(tiles) / columns)
    overview = Image.new("RGB", (columns * 720, rows * 540), "white")
    for index, tile in enumerate(tiles):
        overview.paste(tile, ((index % columns) * 720, (index // columns) * 540))
    overview.save(output)


def _world_to_pixel(point: Point, size: tuple[int, int], bbox: Bbox) -> Point:
    min_x, min_y, max_x, max_y = bbox
    return (
        (point[0] - min_x) / (max_x - min_x) * (size[0] - 1),
        (max_y - point[1]) / (max_y - min_y) * (size[1] - 1),
    )


def _pixel_to_world(point: Point, size: tuple[int, int], bbox: Bbox) -> Point:
    min_x, min_y, max_x, max_y = bbox
    return (
        min_x + point[0] / (size[0] - 1) * (max_x - min_x),
        max_y - point[1] / (size[1] - 1) * (max_y - min_y),
    )


def _pixel_heading(start: Point, end: Point, size: tuple[int, int], bbox: Bbox) -> float:
    left, right = _world_to_pixel(start, size, bbox), _world_to_pixel(end, size, bbox)
    return math.degrees(math.atan2(right[1] - left[1], right[0] - left[0]))


def _mean_to_polyline(points: Sequence[Point], line: Sequence[Point]) -> float:
    segments = list(zip(line, line[1:]))
    return float(
        np.mean(
            [
                min(_point_segment_distance(point, start, end) for start, end in segments)
                for point in points
            ]
        )
    )


def _point_segment_distance(point: Point, start: Point, end: Point) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == dy == 0:
        return math.dist(point, start)
    ratio = max(
        0.0,
        min(
            1.0,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
            / (dx * dx + dy * dy),
        ),
    )
    return math.dist(point, (start[0] + ratio * dx, start[1] + ratio * dy))


def _points(values: Sequence[Point], name: str) -> list[Point]:
    points = [(float(point[0]), float(point[1])) for point in values]
    if len(points) < 2 or any(not math.isfinite(value) for point in points for value in point):
        raise ValueError(f"{name} must contain at least two finite points")
    return points


def _validate_bbox(bbox: Sequence[float]) -> None:
    if len(bbox) != 4 or any(not math.isfinite(float(value)) for value in bbox):
        raise ValueError("bbox must contain four finite values")
    if float(bbox[0]) >= float(bbox[2]) or float(bbox[1]) >= float(bbox[3]):
        raise ValueError("bbox minimums must be smaller than maximums")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


__all__ = [
    "CORRIDOR_SCHEMA",
    "PLAN_SCHEMA",
    "REQUEST_SCHEMA",
    "build_aerial_road_likelihood",
    "build_hamburg_aerial_corridor_plan",
    "build_hamburg_aerial_movement_plan",
    "select_movement_geometry",
]
