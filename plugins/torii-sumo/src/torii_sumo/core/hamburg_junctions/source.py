"""Validate Hamburg candidate inputs and project official MAP geometry."""

from __future__ import annotations

import json
import math
from copy import deepcopy
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from pyproj import CRS, Transformer
from ..candidate_contracts import file_sha256
from ..digital_twin import parse_mapem
from ..hamburg_map_kml import parse_hamburg_map_kml
from ..hamburg_official_intersection_plainxml import _clip_drive_line_to_lane_b_endpoints
from .geometry import Point, _net_offset


REQUEST_SCHEMA = "torii.hamburg-aerial-corridor-candidate-request/v1"


def _read_request(path: Path) -> dict[str, Any]:
    payload = _load_json(path, "request")
    if payload.get("schema") != REQUEST_SCHEMA:
        raise ValueError(f"request schema must be {REQUEST_SCHEMA}")
    for key in ("source_net", "cluster_binding", "movement_summary"):
        value = payload.get(key)
        if not isinstance(value, dict) or not value.get("path") or not value.get("sha256"):
            raise ValueError(f"request {key} must contain path and sha256")
        artifact_path = Path(str(value["path"])).expanduser()
        if not artifact_path.is_absolute():
            value["path"] = str((path.parent / artifact_path).resolve())
    defaults = {
        "seed": 104,
        "vehicle_count": 100,
        "simulation_end": 600,
        "timeout_seconds": 240.0,
        "maximum_endpoint_error_m": 20.0,
        "maximum_lane_projection_error_m": 10.0,
        "minimum_lane_match_margin_m": 0.5,
        "junction_corner_radius_m": 8.0,
    }
    result = {**defaults, **payload}
    if result.setdefault("junction_contours", "preserve") not in ("preserve", "guarded", "fused"):
        raise ValueError("junction_contours must be preserve, guarded, or fused")
    radius = result["junction_corner_radius_m"]
    if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not math.isfinite(radius) or radius <= 0:
        raise ValueError("junction_corner_radius_m must be finite and positive")
    context_groups = result.setdefault("context_joins", {})
    if result.setdefault("context_lane_change_policy", "fixed_paths") not in ("fixed_paths", "permitted_interior"):
        raise ValueError("context_lane_change_policy must be fixed_paths or permitted_interior")
    if not isinstance(context_groups, dict):
        raise ValueError("context_joins must map join ids to source node lists")
    seen_members = set()
    for join_id, members in context_groups.items():
        if not isinstance(join_id, str) or not join_id or join_id.startswith(":") or any(char.isspace() for char in join_id):
            raise ValueError("context_joins requires non-empty external junction ids")
        if not isinstance(members, list) or len(members) < 2 or any(not isinstance(node, str) or not node or any(char.isspace() for char in node) for node in members):
            raise ValueError("context_joins requires at least two source node ids per group")
        if len(set(members)) != len(members) or seen_members.intersection(members):
            raise ValueError("context_joins source node groups must be unique and disjoint")
        seen_members.update(members)
    neighbors = result.setdefault("context_geometry_neighbors", [])
    if not isinstance(neighbors, list) or any(not isinstance(node, str) or not node or any(char.isspace() for char in node) for node in neighbors) or len(set(neighbors)) != len(neighbors) or (neighbors and not context_groups):
        raise ValueError("context_geometry_neighbors requires unique adjacent node ids and declared context_joins")
    result.setdefault("simulation_max_end", result["simulation_end"])
    result.setdefault(
        "maximum_anchor_projection_error_m",
        min(10.0, float(result["maximum_endpoint_error_m"])),
    )
    for key in ("seed", "vehicle_count", "simulation_end", "simulation_max_end"):
        if isinstance(result[key], bool) or int(result[key]) <= 0:
            raise ValueError(f"request {key} must be a positive integer")
    if int(result["simulation_max_end"]) < int(result["simulation_end"]):
        raise ValueError("simulation_max_end must not precede simulation_end")
    for key in (
        "timeout_seconds",
        "maximum_endpoint_error_m",
        "maximum_anchor_projection_error_m",
        "maximum_lane_projection_error_m",
        "minimum_lane_match_margin_m",
    ):
        if not math.isfinite(float(result[key])) or float(result[key]) <= 0:
            raise ValueError(f"request {key} must be positive")
    return result


def _load_movement_plans(summary: Mapping[str, Any], *, base_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    plans = {}
    for row in summary.get("intersections", []):
        node_id = str(row.get("node_id", ""))
        if not node_id or node_id in plans:
            raise ValueError("movement summary must have unique non-empty node ids")
        plan_path = Path(row["plan_file"]).expanduser()
        if not plan_path.is_absolute() and base_dir is not None:
            plan_path = base_dir / plan_path
        path = _verified_artifact(
            {"path": str(plan_path), "sha256": row.get("plan_sha256", "")},
            f"movement plan {node_id}",
        )
        plan = _load_json(path, f"movement plan {node_id}")
        if plan.get("schema") != "torii.hamburg-aerial-movement-plan/v1" or str(plan.get("node_id")) != node_id:
            raise ValueError(f"movement plan {node_id} has an invalid schema or node id")
        if not plan.get("movements") or not plan.get("lanes"):
            raise ValueError(f"movement plan {node_id} requires official movements and lane geometry")
        plans[node_id] = plan
    if not plans:
        raise ValueError("movement summary has no intersections")
    return plans


def _project_plan_to_network(plan: Mapping[str, Any], root: ET.Element) -> dict[str, Any]:
    location = root.find("location")
    projection = location.get("projParameter", "") if location is not None else ""
    if not projection or projection in {"!", "-", "."}:
        raise ValueError("source network requires an explicit geographic projection")
    try:
        source_crs = CRS.from_user_input(plan["crs"])
        target_crs = CRS.from_user_input(projection)
        if source_crs != CRS.from_epsg(25832):
            raise ValueError("shape_epsg25832 fields require plan crs EPSG:25832")
        transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
        offset = _net_offset(root)
    except (KeyError, ValueError) as error:
        raise ValueError(f"invalid plan/network projection: {error}") from error
    result = deepcopy(dict(plan))
    for rows, source_key, target_key in (
        (result.get("lanes", []), "shape_epsg25832", "shape_network"),
        (result.get("movements", []), "selected_shape_epsg25832", "selected_shape_network"),
        ([row for row in result.get("movements", []) if "official_shape_epsg25832" in row],
         "official_shape_epsg25832", "official_shape_network"),
    ):
        for row in rows:
            values = []
            for x, y, *_ in row[source_key]:
                tx, ty = transformer.transform(float(x), float(y), errcheck=True)
                values.append((tx + offset[0], ty + offset[1]))
            if len(values) < 2 or any(not math.isfinite(v) for point in values for v in point):
                raise ValueError("official lane/movement geometry must contain at least two finite points")
            row[target_key] = values
    boundary_points = _official_lane_boundary_points(plan, root)
    for lane in result.get("lanes", []):
        if str(lane.get("lane_id")) in boundary_points:
            lane["junction_endpoint_network"] = boundary_points[str(lane["lane_id"])]
    if plan.get("inputs", {}).get("map_xml"):
        map_path = _verified_artifact(plan["inputs"]["map_xml"], "official MAP lane permissions")
        metadata = {str(lane.lane_id): lane.permission_metadata for lane in parse_mapem(map_path)[0]}
        for lane in result.get("lanes", []):
            if str(lane.get("lane_id")) in metadata:
                lane.update(metadata[str(lane["lane_id"])])
    result["network_projection"] = projection
    result["network_offset"] = list(offset)
    return result


def _verified_artifact(value: Mapping[str, Any], label: str) -> Path:
    path = Path(str(value["path"])).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"{label} is not a file: {path}")
    expected = str(value["sha256"]).lower()
    if len(expected) != 64 or file_sha256(path).lower() != expected:
        raise ValueError(f"{label} SHA-256 does not match the request")
    return path


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid {label} JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _official_lane_boundary_points(plan: Mapping[str, Any], root: ET.Element) -> dict[str, Point]:
    artifact = plan.get("inputs", {}).get("map_kml")
    if not artifact:
        return {}
    source = _verified_artifact(artifact, "official MAP boundary KML")
    geometry = parse_hamburg_map_kml(source, expected_sha256=str(artifact["sha256"]))
    location = root.find("location")
    if location is None or location.get("projParameter", "") in {"", "!", "-", "."}:
        raise ValueError("official boundary points require the source network projection")
    transformer = Transformer.from_crs("EPSG:4326", location.attrib["projParameter"], always_xy=True)
    offset = _net_offset(root)
    result = {}
    for row in geometry["endpoints"]:
        if row["endpoint"] != "B" or row["feature_kind"] != "lane":
            continue
        x, y = transformer.transform(*row["coordinate"][:2], errcheck=True)
        result[str(row["lane_id"])] = (x + offset[0], y + offset[1])
    return result


def _official_boundary_widths(plan: Mapping[str, Any]) -> dict[str, float]:
    """Read MAP centimetre widths at an explicit stop-line node."""
    artifact = plan.get("inputs", {}).get("map_xml")
    if not artifact:
        return {}
    root = ET.parse(_verified_artifact(artifact, "official MAP boundary widths")).getroot()
    result = {}
    for geometry in root.iter():
        if geometry.tag.split("}")[-1] != "IntersectionGeometry":
            continue
        default = next((int(child.text or "0") for child in geometry if child.tag.split("}")[-1] == "laneWidth"), 0)
        if default <= 0:
            continue
        for lane in geometry.iter():
            if lane.tag.split("}")[-1] != "GenericLane":
                continue
            lane_id = next(((child.text or "").strip() for child in lane if child.tag.split("}")[-1] == "laneID"), "")
            width = default
            found_stopline = False
            has_deltas = False
            for node in lane.iter():
                if node.tag.split("}")[-1] != "NodeXY":
                    continue
                for attribute in node.iter():
                    if attribute.tag.split("}")[-1] == "dWidth":
                        width += int(attribute.text or "0")
                        has_deltas = True
                if any(attribute.tag.split("}")[-1] == "stopLine" for attribute in node.iter()):
                    found_stopline = True
                    break
            if lane_id and width > 0 and (found_stopline or not has_deltas):
                result[lane_id] = width / 100.0
    return result


def _official_internal_paths(plan: Mapping[str, Any], root: ET.Element, points: Mapping[str, Point]) -> dict[str, Any]:
    """Use only official, B-to-B clipped paths to identify junction fragments."""
    artifact = plan.get("inputs", {}).get("map_kml")
    if not artifact:
        return {"paths": {}, "unusable": []}
    source = _verified_artifact(artifact, "official internal movement KML")
    geometry = parse_hamburg_map_kml(source, expected_sha256=str(artifact["sha256"]))
    location = root.find("location")
    transformer = Transformer.from_crs("EPSG:4326", location.attrib["projParameter"], always_xy=True)
    offset = _net_offset(root)
    required = {(str(row["ingress_lane_id"]), str(row["egress_lane_id"])) for row in plan.get("movements", [])}
    paths = {}
    unusable = []
    for row in geometry["drive_lines"]:
        key = (str(row["from_lane_id"]), str(row["to_lane_id"]))
        if key not in required or any(lane_id not in points for lane_id in key):
            continue
        shape = [tuple(value + offset[i] for i, value in enumerate(transformer.transform(*point[:2], errcheck=True))) for point in row["coordinates"]]
        try:
            clipped, _ = _clip_drive_line_to_lane_b_endpoints(shape, ingress_b=points[key[0]], egress_b=points[key[1]], tolerance_m=0.1)
            paths[key] = clipped
        except ValueError as error:
            unusable.append({"ingress_lane_id": key[0], "egress_lane_id": key[1], "reason": str(error)})
    return {"paths": paths, "unusable": unusable}
