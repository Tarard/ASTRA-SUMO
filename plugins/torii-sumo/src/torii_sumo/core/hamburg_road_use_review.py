"""Review local road-use evidence without assigning lanes or changing access."""

from collections import Counter
import json
import math
from pathlib import Path

from .candidate_contracts import file_sha256
from ..road_network.official_splice_materializer import (
    _clip_polyline_fraction, _polyline_fraction_point, _project_polyline,
)

REQUEST_SCHEMA = "torii.hamburg-road-use-review-request/v1"
DEFAULTS = dict(context_distance_m=3.0, profile_error_m=1.5, half_window_m=5.0,
                minimum_extent_m=6.0, orientation_error_deg=25.0,
                side_deadband_m=0.5, axis_distance_m=30.0, axis_margin_m=0.5)


def _point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2 or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value
    ):
        raise ValueError("Use two finite numeric coordinates for each point.")
    return tuple(value)


def _line(value):
    points = [_point(p) for p in value]
    if len(points) < 2 or not any(a != b for a, b in zip(points, points[1:])):
        raise ValueError("Each line must contain two distinct points.")
    return points


def _settings(overrides):
    if set(overrides) - DEFAULTS.keys():
        raise ValueError("Unknown road-use review setting.")
    values = {**DEFAULTS, **overrides}
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
           for v in values.values()):
        raise ValueError("Review settings must be finite positive numbers.")
    if values["orientation_error_deg"] > 90 or values["minimum_extent_m"] > 2 * values["half_window_m"]:
        raise ValueError("Use an angle at most 90 degrees and an extent within the review window.")
    return values


def _profile(point, line, half_window):
    projection = _project_polyline(point, line)
    length = sum(math.dist(a, b) for a, b in zip(line, line[1:]))
    lo = max(0, projection["fraction"] - half_window / length)
    hi = min(1, projection["fraction"] + half_window / length)
    clipped = _clip_polyline_fraction(line, lo, hi)
    extent = (hi - lo) * length
    # ponytail: one-metre samples for local review; use exact curve distance for certified geometry.
    count = max(1, math.ceil(extent))
    points = clipped + [_polyline_fraction_point(line, lo + (hi - lo) * i / count) for i in range(count + 1)]
    delta = (clipped[-1][0] - clipped[0][0], clipped[-1][1] - clipped[0][1])
    norm = math.hypot(*delta)
    return dict(projection=projection, line=clipped, samples=points, extent=extent,
                unit=(delta[0] / norm, delta[1] / norm) if norm > 1e-9 else None)


def _side(point, axis, deadband):
    base, unit = axis["projection"]["point"], axis["unit"]
    offset = unit[0] * (point[1] - base[1]) - unit[1] * (point[0] - base[0])
    return 1 if offset > deadband else -1 if offset < -deadband else 0


def inspect_road_use_sample(sample, **overrides):
    """Compare source geometry locally. A compatible pair does not prove lane ownership."""
    cfg = _settings(overrides)
    point = _point(sample["point_epsg25832"])
    image_year = sample.get("image_year")
    if image_year is not None and (type(image_year) is not int or not 1900 <= image_year <= 2100):
        raise ValueError("Use a four-digit image year or null.")
    lanes = []
    lane_ids = set()
    for lane in sample["map_lanes"]:
        if lane.get("revocable") is not None and type(lane["revocable"]) is not bool:
            raise ValueError("Use true, false, or null for revocable use.")
        if lane["id"] in lane_ids:
            raise ValueError("MAP lane IDs must be unique within each sample.")
        lane_ids.add(lane["id"])
        line = _line(lane["line"])
        profile = _profile(point, line, cfg["half_window_m"])
        if profile["projection"]["distance_m"] <= cfg["context_distance_m"]:
            lanes.append((lane, profile))
    axes = []
    for row in sample.get("road_axes", []):
        profile = _profile(point, _line(row["line"]), cfg["half_window_m"])
        if profile["projection"]["distance_m"] <= cfg["axis_distance_m"]:
            axes.append((row["id"], profile))
    axes.sort(key=lambda row: row[1]["projection"]["distance_m"])
    axis = None
    if axes and (len(axes) == 1 or axes[1][1]["projection"]["distance_m"] - axes[0][1]["projection"]["distance_m"] >= cfg["axis_margin_m"]):
        proposed = axes[0][1]
        if proposed["unit"] and proposed["extent"] >= cfg["minimum_extent_m"]:
            axis = proposed
    pairs = []
    missing_geometry = []
    reference_parts = 0
    for reference in sample["references"]:
        year = reference.get("year")
        if year is not None and (type(year) is not int or not 1900 <= year <= 2100):
            raise ValueError("Use a four-digit reference year or null.")
        if not reference["lines"]:
            missing_geometry.append(reference["id"])
        for part, raw in enumerate(reference["lines"]):
            profile = _profile(point, _line(raw), cfg["half_window_m"])
            if profile["projection"]["distance_m"] > cfg["context_distance_m"]:
                continue
            reference_parts += 1
            for lane, target in lanes:
                reasons = []
                angle = None
                if profile["unit"] and target["unit"]:
                    dot = sum(a * b for a, b in zip(profile["unit"], target["unit"]))
                    angle = math.degrees(math.acos(min(1, abs(dot))))
                    if angle > cfg["orientation_error_deg"]:
                        reasons.append("different_line_orientation")
                else:
                    reasons.append("undefined_line_orientation")
                if min(profile["extent"], target["extent"]) < cfg["minimum_extent_m"]:
                    reasons.append("insufficient_local_extent")
                error = max(
                    max(_project_polyline(p, target["line"])["distance_m"] for p in profile["samples"]),
                    max(_project_polyline(p, profile["line"])["distance_m"] for p in target["samples"]),
                )
                if error > cfg["profile_error_m"]:
                    reasons.append("local_profiles_separated")
                sides = None
                if axis:
                    sides = [_side(p, axis, cfg["side_deadband_m"]) for p in
                             (point, profile["projection"]["point"], target["projection"]["point"])]
                    if -1 in sides and 1 in sides:
                        reasons.append("opposite_road_side")
                cycling = sample.get("visual_function") == "cycling_indicated" or any(
                    term in reference.get("category", "") for term in ("Radweg", "Radfahrstreifen", "Schutzstreifen")
                )
                pairs.append(dict(
                    reference_id=reference["id"], reference_part=part, reference_year=year,
                    category=reference.get("category"), map_lane_id=lane["id"],
                    map_lane_type=lane.get("lane_type"), map_direction_role=lane.get("direction_role"),
                    reference_distance_m=round(profile["projection"]["distance_m"], 3),
                    map_distance_m=round(target["projection"]["distance_m"], 3),
                    orientation_error_deg=round(angle, 3) if angle is not None else None,
                    local_profile_error_m=round(error, 3),
                    local_extent_m=[round(profile["extent"], 3), round(target["extent"], 3)],
                    side_signs_observation_reference_map=sides,
                    side_status="unverified" if sides is None or 0 in sides else "opposite_sides" if len(set(sides)) > 1 else "same_side",
                    geometry_status="compatible_candidate" if not reasons else "not_supported",
                    reasons=reasons,
                    temporal_status="unknown" if year is None or image_year is None else "same_year_only" if year == image_year else "different_year",
                    map_validity_status="unverified",
                    function_relation="cycling_and_vehicle_records_need_review" if cycling and lane.get("lane_type") == "vehicle" else "not_certified",
                    observation_relation="parking_observation_near_cycling_records" if cycling and sample.get("visual_function") == "parking" else "unverified",
                    revocable=lane.get("revocable"), allowed_vehicle_classes=lane.get("allowed_vehicle_classes"),
                ))
    candidates = sorted({p["map_lane_id"] for p in pairs if p["geometry_status"] == "compatible_candidate"})
    return dict(
        id=sample["id"], scene=sample.get("scene"), point_epsg25832=point,
        visual_function=sample.get("visual_function"), image_year=image_year,
        comparison_status="no_nearby_reference_geometry" if not reference_parts else "no_nearby_map_lane" if not lanes else "compared",
        association_status="single_geometric_candidate" if len(candidates) == 1 else "ambiguous_geometric_candidates" if candidates else "no_geometric_candidate",
        geometric_candidate_lane_ids=candidates, assigned_lane_id=None,
        decision="review_required", permitted_users_status="unverified", travel_direction_status="unverified",
        road_axis_id=axes[0][0] if axis else None, missing_reference_geometry_ids=missing_geometry,
        revocable_map_lane_ids=sorted(lane["id"] for lane, _ in lanes if lane.get("revocable")),
        nearby_map_lane_ids=[lane["id"] for lane, _ in sorted(lanes, key=lambda row: row[1]["projection"]["distance_m"])],
        pairs=pairs,
    )


def build_hamburg_road_use_review(*, request_file, output_dir):
    """Write a separate, source-bound road-use review from normalized local records."""
    request_path = Path(request_file).resolve(strict=True)
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("Choose a new output directory.")
    request_sha256 = file_sha256(request_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("schema") != REQUEST_SCHEMA or request.get("crs") != "EPSG:25832":
        raise ValueError("Use the road-use review request schema and EPSG:25832 coordinates.")
    if not request.get("sources") or not request.get("samples"):
        raise ValueError("Provide source identities and at least one sample.")
    sources = []
    for row in request["sources"]:
        path = (request_path.parent / row["path"]).resolve(strict=True)
        if file_sha256(path) != row["sha256"]:
            raise ValueError(f"Source hash mismatch: {path.name}")
        sources.append({**row, "path": str(path)})
    settings = _settings(request.get("settings", {}))
    ids = [row["id"] for row in request["samples"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Sample IDs must be unique.")
    results = [inspect_road_use_sample(row, **settings) for row in request["samples"]]
    report = dict(
        schema="torii.hamburg-road-use-review/v1", status="complete", decision="review_required", crs="EPSG:25832",
        request_file=str(request_path), request_sha256=request_sha256, sources=sources, settings=settings,
        implementation_sha256=file_sha256(Path(__file__)),
        claim_boundary="Diagnostic source comparison. Geometry agreement does not establish lane ownership, legal access, travel direction, or current operation. Same-year records need not share a capture date. Input records are caller-normalized; file hashes verify identity, not extraction correctness.",
        network_changed=False, calibration_performed=False, results=results,
        summary=dict(sample_count=len(results), association_counts=dict(Counter(r["association_status"] for r in results)),
                     comparison_counts=dict(Counter(r["comparison_status"] for r in results)),
                     pair_geometry_counts=dict(Counter(p["geometry_status"] for r in results for p in r["pairs"])),
                     rejected_pair_reasons=dict(Counter(reason for r in results for p in r["pairs"] for reason in p["reasons"])),
                     road_axis_available_count=sum(r["road_axis_id"] is not None for r in results),
                     confirmed_lane_assignments=0, confirmed_permissions=0),
    )
    if file_sha256(request_path) != request_sha256 or any(file_sha256(Path(r["path"])) != r["sha256"] for r in sources):
        raise ValueError("An input changed during the review.")
    destination.mkdir(parents=True, exist_ok=False)
    output = destination / "road-use-review.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return dict(status="complete", decision=report["decision"], report_file=str(output), report_sha256=file_sha256(output), **report["summary"])
