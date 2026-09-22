"""Plan official MAP-to-HH-SIB directional corridor stitches.

The planner joins two already official, hash-bound products without using
OpenStreetMap, Google Maps, road names, or a human review decision:

* Hamburg MAP/KML-to-MAPEM binding reports provide lane geometry and travel
  direction; and
* the HH-SIB PlainXML candidate provides directed road-axis intervals and
  official directional lane counts.

The result is evidence only.  A unique endpoint/tangent match can authorize an
approach-level corridor stitch.  It never assigns a MAP lane to a SUMO lane
index because an HH-SIB centre axis plus ``numLanes`` does not prove lateral
lane order.  Ambiguity causes deterministic, non-human abstention.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pyproj import CRS, Transformer

from torii_sumo.core.candidate_contracts import file_sha256

from .official_plainxml import OFFICIAL_PLAINXML_SCHEMA


OFFICIAL_LANE_AXIS_STITCH_SCHEMA = "torii.hamburg-official-map-hh-sib-lane-axis-stitch-plan/v1"
HAMBURG_MAP_BINDING_SCHEMA = "torii.hamburg-map-kml-mapem-binding/v1"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_STATION_DIRECTIONS = frozenset({"with_stationing", "against_stationing"})


class OfficialLaneAxisStitchError(ValueError):
    """Raised when the official inputs cannot be verified structurally."""


@dataclass(frozen=True)
class OfficialLaneAxisStitchThresholds:
    """Explicit geometry and uniqueness thresholds for the stitch planner."""

    max_endpoint_distance_m: float = 15.0
    max_heading_delta_deg: float = 30.0
    minimum_normalized_score_margin: float = 0.25
    endpoint_identity_tolerance_m: float = 1.0
    edge_node_alignment_tolerance_m: float = 1.0
    max_intersection_span_m: float = 250.0
    profile_transition_station_tolerance_m: float = 5.0

    def validated(self) -> OfficialLaneAxisStitchThresholds:
        positive = {
            "max_endpoint_distance_m": self.max_endpoint_distance_m,
            "max_heading_delta_deg": self.max_heading_delta_deg,
            "endpoint_identity_tolerance_m": self.endpoint_identity_tolerance_m,
            "edge_node_alignment_tolerance_m": self.edge_node_alignment_tolerance_m,
            "max_intersection_span_m": self.max_intersection_span_m,
            "profile_transition_station_tolerance_m": self.profile_transition_station_tolerance_m,
        }
        for name, value in positive.items():
            if not math.isfinite(value) or value <= 0:
                raise OfficialLaneAxisStitchError(f"{name} must be finite and positive")
        if self.max_heading_delta_deg > 180:
            raise OfficialLaneAxisStitchError("max_heading_delta_deg cannot exceed 180")
        margin = self.minimum_normalized_score_margin
        if not math.isfinite(margin) or margin < 0:
            raise OfficialLaneAxisStitchError(
                "minimum_normalized_score_margin must be finite and non-negative"
            )
        return self


@dataclass(frozen=True)
class _Edge:
    edge_id: str
    from_node: str
    to_node: str
    num_lanes: int
    shape: tuple[tuple[float, float], ...]
    official_link_id: str
    station_direction: str
    station_from_m: float
    station_to_m: float
    source_sha256: str


def plan_hamburg_official_map_lane_axis_stitch(
    *,
    map_binding_reports: Sequence[Mapping[str, Any] | str | Path],
    nodes_file: str | Path,
    edges_file: str | Path,
    plainxml_manifest_file: str | Path,
    thresholds: OfficialLaneAxisStitchThresholds | None = None,
) -> dict[str, Any]:
    """Return a hash-bound, non-materializing official lane-axis stitch plan.

    Multiple MAP reports are accepted so the planner can prove overlap between
    an upstream egress envelope and a downstream ingress envelope.  The result
    is order-independent: reports, lanes, approaches, and alternatives are
    sorted by official identifiers before the plan id is computed.
    """

    limits = (thresholds or OfficialLaneAxisStitchThresholds()).validated()
    node_path = Path(nodes_file).expanduser().resolve()
    edge_path = Path(edges_file).expanduser().resolve()
    manifest_path = Path(plainxml_manifest_file).expanduser().resolve()
    manifest, manifest_identity = _load_json_file(manifest_path, "PlainXML manifest")
    _validate_manifest(manifest, node_path=node_path, edge_path=edge_path)

    nodes = _parse_nodes(node_path)
    edges = _parse_edges(
        edge_path,
        nodes=nodes,
        source_sha256=str(manifest["source"]["sha256"]),
        alignment_tolerance_m=limits.edge_node_alignment_tolerance_m,
    )
    corridors = _build_corridors(edges)
    projection = _projection(manifest)
    transformer = Transformer.from_crs(
        projection["source_crs"], projection["crs"], always_xy=True
    )

    loaded_reports = [_load_map_binding_report(item) for item in map_binding_reports]
    if not loaded_reports:
        raise OfficialLaneAxisStitchError("map_binding_reports must not be empty")
    loaded_reports.sort(key=lambda item: _identifier_sort_key(str(item[0]["node_id"])))
    node_ids = [str(report["node_id"]) for report, _identity in loaded_reports]
    if len(node_ids) != len(set(node_ids)):
        raise OfficialLaneAxisStitchError("map_binding_reports contain duplicate node_id values")

    lane_results: list[dict[str, Any]] = []
    report_identities: list[dict[str, Any]] = []
    for report, identity in loaded_reports:
        _validate_map_binding_report(report)
        report_identities.append(identity)
        lane_results.extend(
            _plan_report_lanes(
                report,
                corridors=corridors,
                transformer=transformer,
                thresholds=limits,
            )
        )
    lane_results.sort(
        key=lambda item: (
            _identifier_sort_key(item["node_id"]),
            _identifier_sort_key(item["lane_id"]),
        )
    )
    approaches = _build_approaches(lane_results)
    approach_geometry_conflicts = _build_approach_geometry_conflicts(
        lane_results,
        approaches,
    )
    stitches = _build_intersection_stitches(
        approaches,
        corridors=corridors,
        max_intersection_span_m=limits.max_intersection_span_m,
        profile_transition_station_tolerance_m=limits.profile_transition_station_tolerance_m,
    )

    passed_lanes = sum(item["status"] == "pass" for item in lane_results)
    passed_approaches = sum(item["status"] == "pass" for item in approaches)
    passed_stitches = sum(item["status"] == "pass" for item in stitches)
    overall_status = "pass" if passed_lanes == len(lane_results) else "review_required"
    decision = (
        "approach_level_plan_verified"
        if overall_status == "pass"
        else "automatic_abstention_no_materialization_for_unmatched_lanes"
    )

    input_identity = {
        "map_binding_reports": report_identities,
        "plainxml_manifest": manifest_identity,
        "nodes": _file_identity(node_path),
        "edges": _file_identity(edge_path),
        "hh_sib_source_sha256": str(manifest["source"]["sha256"]),
        "plainxml_candidate_id": str(manifest["candidate_id"]),
    }
    result: dict[str, Any] = {
        "schema": OFFICIAL_LANE_AXIS_STITCH_SCHEMA,
        "status": overall_status,
        "decision": decision,
        "human_action_required": False,
        "materialization_performed": False,
        "inputs": input_identity,
        "projection": projection,
        "thresholds": asdict(limits),
        "matching_contract": {
            "features_used": [
                "official_MAP_lane_boundary_endpoint_A",
                "official_MAP_connection_geometry_proof_that_endpoint_B_is_junction_side",
                "projected_endpoint_to_directed_HH_SIB_axis_distance",
                "MAP_boundary_travel_tangent_to_HH_SIB_directed_axis_heading",
                "unique_normalized_score_margin",
                "official_MAP_approach_sibling_corridor_coherence",
            ],
            "features_excluded": [
                "road_name",
                "OpenStreetMap",
                "Google_Maps",
                "manual_map_review",
                "guessed_SUMO_lane_index",
            ],
            "score": "endpoint_distance/max_endpoint_distance + heading_delta/max_heading_delta",
            "ambiguity_action": (
                "automatic_abstention_no_materialization_unless_all_other_official_MAP_"
                "approach_lanes_prove_one_compatible_directed_corridor"
            ),
        },
        "lanes": lane_results,
        "approaches": approaches,
        "approach_geometry_conflicts": approach_geometry_conflicts,
        "stitch_candidates": stitches,
        "counts": {
            "map_node_count": len(loaded_reports),
            "vehicle_lane_count": len(lane_results),
            "matched_vehicle_lane_count": passed_lanes,
            "abstained_vehicle_lane_count": len(lane_results) - passed_lanes,
            "approach_count": len(approaches),
            "matched_approach_count": passed_approaches,
            "abstained_approach_count": len(approaches) - passed_approaches,
            "stitch_candidate_count": len(stitches),
            "authorized_approach_stitch_count": passed_stitches,
        },
        "gates": {
            "official_input_hashes": "pass",
            "plainxml_manifest_binding": "pass",
            "directed_axis_structure": "pass",
            "all_vehicle_lane_corridors_unique": overall_status,
            "approach_geometry_and_lane_count": (
                "pass"
                if all(item["status"] == "pass" for item in approach_geometry_conflicts)
                else "review_required"
            ),
            "individual_lane_index_allocation": "review_required",
            "final_network_materialization": "not_run",
        },
        "claim_boundary": (
            "A passing lane record proves only a MAP lane's directed HH-SIB road-axis corridor and projected "
            "station envelope. It does not prove a SUMO lane index, lateral lane order, legal lane-to-lane "
            "connection, stop line, conflict area, signal group binding, or final network correctness. Passing "
            "stitch candidates authorize only an approach-level corridor cut at an exact HH-SIB lane-profile "
            "transition; every lane-index assignment remains an automatic abstention."
        ),
    }
    result["plan_id"] = "official-lane-axis-stitch-" + _stable_digest(
        {
            "schema": OFFICIAL_LANE_AXIS_STITCH_SCHEMA,
            "inputs": _without_runtime_paths(input_identity),
            "thresholds": result["thresholds"],
            "lanes": lane_results,
            "approaches": approaches,
            "approach_geometry_conflicts": approach_geometry_conflicts,
            "stitch_candidates": stitches,
        }
    )[:24]
    return result


def lane_axis_stitch_identity_payload(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Return plan evidence without machine-local input locations."""

    payload = json.loads(json.dumps(dict(plan), ensure_ascii=False))
    inputs = payload.get("inputs")
    if isinstance(inputs, Mapping):
        payload["inputs"] = _without_runtime_paths(inputs)
    return payload


def _without_runtime_paths(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_runtime_paths(item)
            for key, item in value.items()
            if key != "path"
        }
    if isinstance(value, list):
        return [_without_runtime_paths(item) for item in value]
    return value


def _load_map_binding_report(
    source: Mapping[str, Any] | str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(source, Mapping):
        report = json.loads(json.dumps(dict(source), ensure_ascii=False))
        identity = {
            "path": None,
            "sha256": _stable_digest(report),
            "identity_method": "canonical_json_sha256",
        }
        return report, identity
    return _load_json_file(Path(source).expanduser().resolve(), "MAP binding report")


def _load_json_file(path: Path, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise OfficialLaneAxisStitchError(f"cannot read {label}: {path}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialLaneAxisStitchError(f"invalid {label} JSON: {path}") from exc
    if not isinstance(value, dict):
        raise OfficialLaneAxisStitchError(f"{label} root must be an object")
    return value, {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "identity_method": "file_bytes_sha256",
    }


def _validate_manifest(manifest: Mapping[str, Any], *, node_path: Path, edge_path: Path) -> None:
    if manifest.get("schema") != OFFICIAL_PLAINXML_SCHEMA:
        raise OfficialLaneAxisStitchError("official HH-SIB PlainXML manifest schema is required")
    candidate_id = str(manifest.get("candidate_id", "")).strip()
    if not candidate_id:
        raise OfficialLaneAxisStitchError("PlainXML manifest candidate_id is required")
    source = manifest.get("source")
    if not isinstance(source, Mapping) or not _is_sha256(source.get("sha256")):
        raise OfficialLaneAxisStitchError("PlainXML manifest source.sha256 is invalid")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise OfficialLaneAxisStitchError("PlainXML manifest artifacts are required")
    for key, path in (("nodes", node_path), ("edges", edge_path)):
        entry = artifacts.get(key)
        if not isinstance(entry, Mapping) or not _is_sha256(entry.get("sha256")):
            raise OfficialLaneAxisStitchError(f"PlainXML manifest artifact {key!r} is invalid")
        if not path.is_file() or file_sha256(path) != str(entry["sha256"]).lower():
            raise OfficialLaneAxisStitchError(f"PlainXML {key} file does not match manifest SHA-256")
    _projection(manifest)


def _projection(manifest: Mapping[str, Any]) -> dict[str, str]:
    raw = manifest.get("projection")
    if not isinstance(raw, Mapping):
        raise OfficialLaneAxisStitchError("PlainXML manifest projection is required")
    source_crs = str(raw.get("source_crs", "")).strip()
    target_crs = str(raw.get("crs", "")).strip()
    try:
        source = CRS.from_user_input(source_crs)
        target = CRS.from_user_input(target_crs)
    except Exception as exc:  # pyproj has several version-specific CRS exceptions
        raise OfficialLaneAxisStitchError("PlainXML manifest projection CRS is invalid") from exc
    if not source.is_geographic or not target.is_projected:
        raise OfficialLaneAxisStitchError("lane stitch requires geographic source and projected target CRS")
    return {"source_crs": source.to_string(), "crs": target.to_string()}


def _validate_map_binding_report(report: Mapping[str, Any]) -> None:
    if report.get("schema") != HAMBURG_MAP_BINDING_SCHEMA or report.get("status") != "pass":
        raise OfficialLaneAxisStitchError("a passing Hamburg MAP/KML-to-MAPEM binding report is required")
    if not str(report.get("node_id", "")).strip():
        raise OfficialLaneAxisStitchError("MAP binding report node_id is required")
    source = report.get("source")
    if not isinstance(source, Mapping) or not _is_sha256(source.get("sha256")):
        raise OfficialLaneAxisStitchError("MAP binding report source.sha256 is invalid")
    source_path = source.get("path")
    if source_path:
        path = Path(str(source_path)).expanduser().resolve()
        if path.is_file() and file_sha256(path) != str(source["sha256"]).lower():
            raise OfficialLaneAxisStitchError("MAP KML source file no longer matches its binding report hash")
    lanes = report.get("lanes")
    connections = report.get("connections")
    if not isinstance(lanes, list) or not isinstance(connections, list):
        raise OfficialLaneAxisStitchError("MAP binding report lanes and connections lists are required")


def _parse_nodes(path: Path) -> dict[str, tuple[float, float]]:
    root = _parse_xml(path, "nodes")
    result: dict[str, tuple[float, float]] = {}
    for element in root.findall("node"):
        node_id = str(element.get("id", ""))
        if not node_id or node_id in result:
            raise OfficialLaneAxisStitchError("PlainXML nodes contain a missing or duplicate id")
        result[node_id] = (
            _finite_float(element.get("x"), f"node {node_id} x"),
            _finite_float(element.get("y"), f"node {node_id} y"),
        )
    if not result:
        raise OfficialLaneAxisStitchError("PlainXML nodes file is empty")
    return result


def _parse_edges(
    path: Path,
    *,
    nodes: Mapping[str, tuple[float, float]],
    source_sha256: str,
    alignment_tolerance_m: float,
) -> list[_Edge]:
    root = _parse_xml(path, "edges")
    result: list[_Edge] = []
    seen: set[str] = set()
    for element in root.findall("edge"):
        edge_id = str(element.get("id", ""))
        if not edge_id or edge_id in seen:
            raise OfficialLaneAxisStitchError("PlainXML edges contain a missing or duplicate id")
        seen.add(edge_id)
        from_node = str(element.get("from", ""))
        to_node = str(element.get("to", ""))
        if from_node not in nodes or to_node not in nodes:
            raise OfficialLaneAxisStitchError(f"edge {edge_id} references an unknown node")
        shape = _parse_shape(element.get("shape"), edge_id=edge_id)
        if _distance(shape[0], nodes[from_node]) > alignment_tolerance_m:
            raise OfficialLaneAxisStitchError(f"edge {edge_id} shape does not start at its from-node")
        if _distance(shape[-1], nodes[to_node]) > alignment_tolerance_m:
            raise OfficialLaneAxisStitchError(f"edge {edge_id} shape does not end at its to-node")
        params = _unique_params(element, edge_id=edge_id)
        official_link_id = str(params.get("origId", "")).strip()
        station_direction = str(params.get("torii:station_direction", ""))
        if not official_link_id or station_direction not in _STATION_DIRECTIONS:
            raise OfficialLaneAxisStitchError(f"edge {edge_id} lacks official link/direction identity")
        edge_source_sha256 = str(params.get("torii:source_sha256", "")).lower()
        if edge_source_sha256 != source_sha256.lower():
            raise OfficialLaneAxisStitchError(f"edge {edge_id} HH-SIB source hash disagrees with manifest")
        station_from = _finite_float(params.get("torii:station_from_m"), "station_from_m")
        station_to = _finite_float(params.get("torii:station_to_m"), "station_to_m")
        if station_to <= station_from:
            raise OfficialLaneAxisStitchError(f"edge {edge_id} has a non-increasing station interval")
        try:
            num_lanes = int(str(element.get("numLanes", "")))
        except ValueError as exc:
            raise OfficialLaneAxisStitchError(f"edge {edge_id} numLanes must be an integer") from exc
        if num_lanes <= 0:
            raise OfficialLaneAxisStitchError(f"edge {edge_id} numLanes must be positive")
        result.append(
            _Edge(
                edge_id=edge_id,
                from_node=from_node,
                to_node=to_node,
                num_lanes=num_lanes,
                shape=shape,
                official_link_id=official_link_id,
                station_direction=station_direction,
                station_from_m=station_from,
                station_to_m=station_to,
                source_sha256=edge_source_sha256,
            )
        )
    if not result:
        raise OfficialLaneAxisStitchError("PlainXML edges file is empty")
    return sorted(result, key=lambda edge: edge.edge_id)


def _build_corridors(edges: Sequence[_Edge]) -> dict[str, dict[str, Any]]:
    grouped: dict[tuple[str, str], list[_Edge]] = defaultdict(list)
    for edge in edges:
        grouped[(edge.official_link_id, edge.station_direction)].append(edge)
    result: dict[str, dict[str, Any]] = {}
    for key in sorted(grouped):
        official_link_id, direction = key
        intervals = sorted(grouped[key], key=lambda edge: (edge.station_from_m, edge.station_to_m, edge.edge_id))
        corridor_id = "hh-sib-axis-" + hashlib.sha256(
            (official_link_id + "\0" + direction).encode("utf-8")
        ).hexdigest()[:16]
        transitions: list[dict[str, Any]] = []
        for low, high in zip(intervals, intervals[1:]):
            if not math.isclose(low.station_to_m, high.station_from_m, abs_tol=1e-6):
                continue
            if low.num_lanes == high.num_lanes:
                continue
            if direction == "with_stationing":
                before_count, after_count = low.num_lanes, high.num_lanes
                before_edge, after_edge = low.edge_id, high.edge_id
            else:
                before_count, after_count = high.num_lanes, low.num_lanes
                before_edge, after_edge = high.edge_id, low.edge_id
            transitions.append(
                {
                    "station_m": low.station_to_m,
                    "before_lane_count": before_count,
                    "after_lane_count": after_count,
                    "before_edge_id": before_edge,
                    "after_edge_id": after_edge,
                }
            )
        result[corridor_id] = {
            "corridor_id": corridor_id,
            "official_link_id": official_link_id,
            "station_direction": direction,
            "edges": intervals,
            "edge_ids": sorted(edge.edge_id for edge in intervals),
            "station_range_m": [
                min(edge.station_from_m for edge in intervals),
                max(edge.station_to_m for edge in intervals),
            ],
            "lane_profile_transitions": transitions,
        }
    return result


def _plan_report_lanes(
    report: Mapping[str, Any],
    *,
    corridors: Mapping[str, Mapping[str, Any]],
    transformer: Transformer,
    thresholds: OfficialLaneAxisStitchThresholds,
) -> list[dict[str, Any]]:
    node_id = str(report["node_id"])
    connections = [item for item in report["connections"] if isinstance(item, Mapping)]
    result: list[dict[str, Any]] = []
    for raw_lane in report["lanes"]:
        if not isinstance(raw_lane, Mapping) or str(raw_lane.get("lane_type")) != "vehicle":
            continue
        lane_id = str(raw_lane.get("lane_id", ""))
        role = str(raw_lane.get("kml_direction_role", ""))
        approach_id = str(
            raw_lane.get("ingress_approach" if role == "ingress" else "egress_approach", "")
        )
        base: dict[str, Any] = {
            "node_id": node_id,
            "lane_id": lane_id,
            "map_role": role,
            "approach_id": approach_id,
            "merge_points": (
                json.loads(json.dumps(raw_lane.get("merge_points", []), ensure_ascii=False))
                if isinstance(raw_lane.get("merge_points", []), list)
                else []
            ),
            "binding_level": "directed_edge_corridor",
            "sumo_lane_index": None,
            "individual_lane_allocation": {
                "status": "review_required",
                "decision": "automatic_abstention",
                "human_action_required": False,
                "reason": (
                    "HH-SIB PlainXML proves a directional axis and lane count, not lateral MAP-lane to "
                    "SUMO-lane ordering"
                ),
            },
        }
        preparation = _prepare_map_lane(
            raw_lane,
            connections=connections,
            transformer=transformer,
            tolerance_m=thresholds.endpoint_identity_tolerance_m,
        )
        if preparation["status"] != "pass":
            result.append(
                {
                    **base,
                    "status": "review_required",
                    "decision": "automatic_abstention_no_materialization",
                    "reason": preparation["reason"],
                    "boundary_evidence": preparation.get("boundary_evidence"),
                    "alternatives": [],
                    "binding": None,
                }
            )
            continue

        alternatives = [
            _evaluate_corridor(
                preparation["boundary_xy"],
                map_heading_deg=preparation["travel_heading_deg"],
                corridor=corridor,
                thresholds=thresholds,
            )
            for corridor in corridors.values()
        ]
        alternatives.sort(key=lambda item: (item["score"], item["corridor_id"]))
        compatible = [item for item in alternatives if item["compatible"]]
        if not compatible:
            status = "review_required"
            reason = "no_compatible_directed_corridor_within_distance_and_heading_thresholds"
            selected = None
            score_margin = None
        else:
            score_margin = None if len(compatible) == 1 else compatible[1]["score"] - compatible[0]["score"]
            unique = len(compatible) == 1 or score_margin >= thresholds.minimum_normalized_score_margin
            if unique:
                status = "pass"
                reason = "unique_directed_corridor_by_threshold_and_score_margin"
                selected = compatible[0]
            else:
                status = "review_required"
                reason = "ambiguous_directed_corridor_score_margin"
                selected = None

        binding = (
            _build_lane_corridor_binding(
                preparation=preparation,
                selected=selected,
                corridor=corridors[str(selected["corridor_id"])],
            )
            if selected is not None
            else None
        )
        result.append(
            {
                **base,
                "status": status,
                "decision": (
                    "bind_at_approach_directional_corridor_level"
                    if status == "pass"
                    else "automatic_abstention_no_materialization"
                ),
                "reason": reason,
                "score_margin": _rounded(score_margin),
                "boundary_evidence": preparation["boundary_evidence"],
                "map_travel_heading_deg": _rounded(preparation["travel_heading_deg"]),
                "alternatives": alternatives,
                "binding": binding,
            }
        )
    _resolve_ambiguous_lanes_by_approach_coherence(
        result,
        raw_lanes=report["lanes"],
        connections=connections,
        corridors=corridors,
        transformer=transformer,
        thresholds=thresholds,
    )
    return result


def _resolve_ambiguous_lanes_by_approach_coherence(
    lanes: list[dict[str, Any]],
    *,
    raw_lanes: Sequence[Mapping[str, Any]],
    connections: Sequence[Mapping[str, Any]],
    corridors: Mapping[str, Mapping[str, Any]],
    transformer: Transformer,
    thresholds: OfficialLaneAxisStitchThresholds,
) -> None:
    """Resolve one ambiguous lane when every official sibling proves one axis.

    This is deliberately narrower than a nearest-road fallback: the official
    MAP approach must contain exactly one unresolved lane, at least two other
    lanes must already bind to the same directed HH-SIB corridor, and that
    corridor must remain a compatible alternative for the unresolved lane.
    """

    raw_by_id_candidates: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in raw_lanes:
        if isinstance(item, Mapping) and str(item.get("lane_id", "")):
            raw_by_id_candidates[str(item["lane_id"])].append(item)
    raw_by_id = {
        lane_id: members[0]
        for lane_id, members in raw_by_id_candidates.items()
        if len(members) == 1
    }
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for lane in lanes:
        grouped[
            (str(lane["node_id"]), str(lane["map_role"]), str(lane["approach_id"]))
        ].append(lane)

    for (_node_id, _map_role, approach_id), members in grouped.items():
        if not approach_id.strip():
            continue
        unresolved = [
            lane
            for lane in members
            if lane["status"] != "pass"
            and lane.get("reason") == "ambiguous_directed_corridor_score_margin"
        ]
        passed = [lane for lane in members if lane["status"] == "pass"]
        if len(unresolved) != 1 or len(passed) < 2 or len(passed) + 1 != len(members):
            continue
        sibling_corridors = {
            str(lane["binding"]["corridor_id"])
            for lane in passed
            if isinstance(lane.get("binding"), Mapping)
        }
        if len(sibling_corridors) != 1:
            continue
        corridor_id = next(iter(sibling_corridors))
        target = unresolved[0]
        alternatives = [
            item
            for item in target.get("alternatives", [])
            if isinstance(item, Mapping)
            and item.get("compatible") is True
            and str(item.get("corridor_id", "")) == corridor_id
        ]
        raw_lane = raw_by_id.get(str(target["lane_id"]))
        corridor = corridors.get(corridor_id)
        if len(alternatives) != 1 or raw_lane is None or corridor is None:
            continue
        preparation = _prepare_map_lane(
            raw_lane,
            connections=connections,
            transformer=transformer,
            tolerance_m=thresholds.endpoint_identity_tolerance_m,
        )
        if preparation["status"] != "pass":
            continue
        target.update(
            {
                "status": "pass",
                "decision": "bind_at_approach_directional_corridor_level",
                "reason": "official_MAP_approach_sibling_corridor_coherence",
                "binding": _build_lane_corridor_binding(
                    preparation=preparation,
                    selected=alternatives[0],
                    corridor=corridor,
                ),
                "approach_coherence_evidence": {
                    "status": "pass",
                    "basis": "all_other_official_MAP_approach_lanes_bind_same_directed_corridor",
                    "corridor_id": corridor_id,
                    "supporting_lane_ids": sorted(
                        (str(lane["lane_id"]) for lane in passed),
                        key=_identifier_sort_key,
                    ),
                },
            }
        )


def _prepare_map_lane(
    lane: Mapping[str, Any],
    *,
    connections: Sequence[Mapping[str, Any]],
    transformer: Transformer,
    tolerance_m: float,
) -> dict[str, Any]:
    role = str(lane.get("kml_direction_role", ""))
    lane_id = str(lane.get("lane_id", ""))
    if role not in {"ingress", "egress"}:
        return {"status": "review_required", "reason": "vehicle_lane_has_no_ingress_or_egress_role"}
    try:
        coordinates = [_project_coordinate(value, transformer) for value in lane["coordinates"]]
        endpoint_a = _project_coordinate(lane["endpoint_a"], transformer)
        endpoint_b = _project_coordinate(lane["endpoint_b"], transformer)
    except (KeyError, TypeError, OfficialLaneAxisStitchError):
        return {"status": "review_required", "reason": "invalid_or_missing_MAP_lane_geometry"}
    if len(coordinates) < 2:
        return {"status": "review_required", "reason": "MAP_lane_geometry_has_fewer_than_two_points"}
    direct_error = _distance(coordinates[0], endpoint_b) + _distance(coordinates[-1], endpoint_a)
    reverse_error = _distance(coordinates[0], endpoint_a) + _distance(coordinates[-1], endpoint_b)
    if min(direct_error, reverse_error) > 2 * tolerance_m:
        return {
            "status": "review_required",
            "reason": "MAP_lane_terminal_points_do_not_match_official_A_B_endpoints",
        }
    geometry_b_to_a = coordinates if direct_error <= reverse_error else list(reversed(coordinates))
    if _distance(geometry_b_to_a[-1], geometry_b_to_a[-2]) <= 1e-9:
        return {"status": "review_required", "reason": "MAP_lane_boundary_tangent_is_degenerate"}

    junction_points: list[tuple[float, float]] = []
    for connection in connections:
        relevant = (
            role == "ingress" and str(connection.get("ingress_lane_id")) == lane_id
        ) or (role == "egress" and str(connection.get("egress_lane_id")) == lane_id)
        if not relevant:
            continue
        raw = connection.get("connection_coordinates")
        if not isinstance(raw, list) or len(raw) < 2:
            continue
        point = raw[0] if role == "ingress" else raw[-1]
        junction_points.append(_project_coordinate(point, transformer))
    junction_errors = [_distance(point, endpoint_b) for point in junction_points]
    if not junction_errors or max(junction_errors) > tolerance_m:
        return {
            "status": "review_required",
            "reason": "MAP_connections_do_not_prove_endpoint_B_is_the_junction_endpoint",
            "boundary_evidence": {
                "connection_count": len(junction_points),
                "maximum_connection_to_endpoint_b_error_m": _rounded(max(junction_errors) if junction_errors else None),
            },
        }

    adjacent = geometry_b_to_a[-2]
    if role == "ingress":
        dx, dy = adjacent[0] - endpoint_a[0], adjacent[1] - endpoint_a[1]
    else:
        dx, dy = endpoint_a[0] - adjacent[0], endpoint_a[1] - adjacent[1]
    heading = math.degrees(math.atan2(dy, dx)) % 360
    return {
        "status": "pass",
        "boundary_xy": endpoint_a,
        "junction_xy": endpoint_b,
        "geometry_xy": geometry_b_to_a,
        "travel_heading_deg": heading,
        "boundary_evidence": {
            "boundary_endpoint": "A",
            "junction_endpoint": "B",
            "basis": "official_connection_geometry_endpoint_identity",
            "connection_count": len(junction_points),
            "maximum_connection_to_endpoint_b_error_m": _rounded(max(junction_errors)),
            "lane_terminal_alignment_error_m": _rounded(min(direct_error, reverse_error)),
            "geometry_order_normalized_to": "junction_B_to_boundary_A",
            "boundary_xy": [_rounded(endpoint_a[0]), _rounded(endpoint_a[1])],
            "junction_xy": [_rounded(endpoint_b[0]), _rounded(endpoint_b[1])],
        },
    }


def _evaluate_corridor(
    point: tuple[float, float],
    *,
    map_heading_deg: float,
    corridor: Mapping[str, Any],
    thresholds: OfficialLaneAxisStitchThresholds,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for edge in corridor["edges"]:
        for projection in _segment_projections(point, edge):
            heading_delta = _heading_delta(map_heading_deg, projection["axis_heading_deg"])
            score = (
                projection["endpoint_distance_m"] / thresholds.max_endpoint_distance_m
                + heading_delta / thresholds.max_heading_delta_deg
            )
            candidates.append({**projection, "heading_delta_deg": heading_delta, "score": score})
    best = min(candidates, key=lambda item: (item["score"], item["nearest_edge_id"], item["segment_index"]))
    rejections: list[str] = []
    if best["endpoint_distance_m"] > thresholds.max_endpoint_distance_m:
        rejections.append("endpoint_distance_exceeds_threshold")
    if best["heading_delta_deg"] > thresholds.max_heading_delta_deg:
        rejections.append("heading_delta_exceeds_threshold")
    return {
        "corridor_id": corridor["corridor_id"],
        "official_link_id": corridor["official_link_id"],
        "station_direction": corridor["station_direction"],
        "corridor_edge_ids": corridor["edge_ids"],
        "nearest_edge_id": best["nearest_edge_id"],
        "axis_lane_count_at_projection": best["axis_lane_count_at_projection"],
        "projected_station_m": _rounded(best["projected_station_m"]),
        "endpoint_distance_m": _rounded(best["endpoint_distance_m"]),
        "map_heading_deg": _rounded(map_heading_deg),
        "axis_heading_deg": _rounded(best["axis_heading_deg"]),
        "heading_delta_deg": _rounded(best["heading_delta_deg"]),
        "projected_xy": [_rounded(value) for value in best["projected_xy"]],
        "score": _rounded(best["score"]),
        "compatible": not rejections,
        "rejection_reasons": rejections,
    }


def _build_lane_corridor_binding(
    *,
    preparation: Mapping[str, Any],
    selected: Mapping[str, Any],
    corridor: Mapping[str, Any],
) -> dict[str, Any]:
    geometry_projection = _project_lane_geometry(preparation["geometry_xy"], corridor)
    junction_projection = _project_point_to_corridor(preparation["junction_xy"], corridor)
    return {
        "corridor_id": selected["corridor_id"],
        "official_link_id": selected["official_link_id"],
        "station_direction": selected["station_direction"],
        "corridor_edge_ids": selected["corridor_edge_ids"],
        "nearest_edge_id": selected["nearest_edge_id"],
        "axis_lane_count_at_boundary": selected["axis_lane_count_at_projection"],
        "boundary_station_m": selected["projected_station_m"],
        "junction_station_m": junction_projection["projected_station_m"],
        "geometry_station_range_m": geometry_projection["station_range_m"],
        "geometry_max_axis_distance_m": geometry_projection["max_axis_distance_m"],
        "sumo_lane_index": None,
    }


def _segment_projections(point: tuple[float, float], edge: _Edge) -> list[dict[str, Any]]:
    lengths = [_distance(first, second) for first, second in zip(edge.shape, edge.shape[1:])]
    total_length = sum(lengths)
    if total_length <= 0:
        raise OfficialLaneAxisStitchError(f"edge {edge.edge_id} shape length is zero")
    result: list[dict[str, Any]] = []
    preceding = 0.0
    for index, (first, second, segment_length) in enumerate(zip(edge.shape, edge.shape[1:], lengths)):
        if segment_length <= 0:
            preceding += segment_length
            continue
        dx, dy = second[0] - first[0], second[1] - first[1]
        fraction = max(
            0.0,
            min(1.0, ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / (segment_length**2)),
        )
        projected = (first[0] + fraction * dx, first[1] + fraction * dy)
        edge_fraction = (preceding + fraction * segment_length) / total_length
        if edge.station_direction == "with_stationing":
            station = edge.station_from_m + edge_fraction * (edge.station_to_m - edge.station_from_m)
        else:
            station = edge.station_to_m - edge_fraction * (edge.station_to_m - edge.station_from_m)
        result.append(
            {
                "nearest_edge_id": edge.edge_id,
                "segment_index": index,
                "endpoint_distance_m": _distance(point, projected),
                "axis_heading_deg": math.degrees(math.atan2(dy, dx)) % 360,
                "projected_xy": projected,
                "projected_station_m": station,
                "axis_lane_count_at_projection": edge.num_lanes,
            }
        )
        preceding += segment_length
    return result


def _project_point_to_corridor(
    point: tuple[float, float], corridor: Mapping[str, Any]
) -> dict[str, Any]:
    projections = [
        projection
        for edge in corridor["edges"]
        for projection in _segment_projections(point, edge)
    ]
    best = min(
        projections,
        key=lambda item: (item["endpoint_distance_m"], item["nearest_edge_id"], item["segment_index"]),
    )
    return {
        "projected_station_m": _rounded(best["projected_station_m"]),
        "axis_distance_m": _rounded(best["endpoint_distance_m"]),
        "nearest_edge_id": best["nearest_edge_id"],
    }


def _project_lane_geometry(
    points: Sequence[tuple[float, float]], corridor: Mapping[str, Any]
) -> dict[str, Any]:
    projections = [_project_point_to_corridor(point, corridor) for point in points]
    stations = [float(item["projected_station_m"]) for item in projections]
    return {
        "station_range_m": [_rounded(min(stations)), _rounded(max(stations))],
        "max_axis_distance_m": _rounded(max(float(item["axis_distance_m"]) for item in projections)),
    }


def _build_approaches(lanes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for lane in lanes:
        grouped[(str(lane["node_id"]), str(lane["map_role"]), str(lane["approach_id"]))].append(lane)
    result: list[dict[str, Any]] = []
    for (node_id, role, approach_id), members in sorted(grouped.items()):
        passed = [lane for lane in members if lane["status"] == "pass"]
        corridor_ids = {
            str(lane["binding"]["corridor_id"])
            for lane in passed
            if isinstance(lane.get("binding"), Mapping)
        }
        complete = len(passed) == len(members) and len(corridor_ids) == 1
        binding = None
        if complete:
            corridor_id = next(iter(corridor_ids))
            lane_bindings = [lane["binding"] for lane in passed]
            geometry_ranges = [binding["geometry_station_range_m"] for binding in lane_bindings]
            boundary_stations = [float(binding["boundary_station_m"]) for binding in lane_bindings]
            junction_stations = [float(binding["junction_station_m"]) for binding in lane_bindings]
            binding = {
                "corridor_id": corridor_id,
                "official_link_id": lane_bindings[0]["official_link_id"],
                "station_direction": lane_bindings[0]["station_direction"],
                "vehicle_lane_count": len(members),
                "boundary_station_envelope_m": [
                    _rounded(min(boundary_stations)),
                    _rounded(max(boundary_stations)),
                ],
                "junction_station_envelope_m": [
                    _rounded(min(junction_stations)),
                    _rounded(max(junction_stations)),
                ],
                "geometry_station_envelope_m": [
                    _rounded(min(float(value[0]) for value in geometry_ranges)),
                    _rounded(max(float(value[1]) for value in geometry_ranges)),
                ],
                "sumo_lane_indices": None,
            }
        result.append(
            {
                "node_id": node_id,
                "map_role": role,
                "approach_id": approach_id,
                "vehicle_lane_ids": sorted(
                    (str(lane["lane_id"]) for lane in members), key=_identifier_sort_key
                ),
                "status": "pass" if complete else "review_required",
                "decision": (
                    "bind_approach_to_directional_corridor"
                    if complete
                    else "automatic_abstention_no_materialization"
                ),
                "binding": binding,
                "individual_lane_index_status": "review_required",
                "individual_lane_index_decision": "automatic_abstention",
                "human_action_required": False,
            }
        )
    return result


def _build_approach_geometry_conflicts(
    lanes: Sequence[Mapping[str, Any]],
    approaches: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Expose lane-group facts that prevent a one-boundary-node materialization.

    A passing axis match is intentionally weaker than a lane-index assignment.
    A MAP approach may contain a lane drop/merge before the stop line, while
    HH-SIB reports the downstream lane profile.  In that case a single SUMO
    edge with one outer node gives valid XML but a false surface in Netedit.
    Keep this diagnostic deterministic and evidence-only; the joiner must split
    at the official merge point before it can materialize the group.
    """

    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for lane in lanes:
        grouped[
            (str(lane.get("node_id", "")), str(lane.get("map_role", "")), str(lane.get("approach_id", "")))
        ].append(lane)

    conflicts: list[dict[str, Any]] = []
    for approach in approaches:
        key = (
            str(approach.get("node_id", "")),
            str(approach.get("map_role", "")),
            str(approach.get("approach_id", "")),
        )
        members = grouped.get(key, [])
        reasons: list[str] = []
        axis_counts = sorted(
            {
                int(lane["binding"]["axis_lane_count_at_boundary"])
                for lane in members
                if isinstance(lane.get("binding"), Mapping)
                and lane["binding"].get("axis_lane_count_at_boundary") is not None
            }
        )
        map_lane_count = len(members)
        if not members or str(approach.get("status")) != "pass":
            reasons.append("approach_axis_match_not_pass")
        if len(axis_counts) > 1:
            reasons.append("axis_lane_count_varies_within_map_approach")
        if axis_counts and any(value != map_lane_count for value in axis_counts):
            reasons.append("map_lane_count_differs_from_hh_sib_boundary_profile")

        stations = [
            float(lane["binding"]["boundary_station_m"])
            for lane in members
            if isinstance(lane.get("binding"), Mapping)
            and lane["binding"].get("boundary_station_m") is not None
        ]
        station_spread = max(stations) - min(stations) if stations else None
        if station_spread is not None and station_spread > 5.0:
            reasons.append("boundary_station_span_exceeds_lane_width")

        points = [
            lane["boundary_evidence"]["boundary_xy"]
            for lane in members
            if isinstance(lane.get("boundary_evidence"), Mapping)
            and isinstance(lane["boundary_evidence"].get("boundary_xy"), Sequence)
            and len(lane["boundary_evidence"]["boundary_xy"]) >= 2
        ]
        boundary_spread = (
            max(_distance(first, second) for first in points for second in points)
            if len(points) >= 2
            else None
        )
        if boundary_spread is not None and boundary_spread > 15.0:
            reasons.append("boundary_geometry_span_exceeds_lane_width")

        merge_lane_ids = sorted(
            str(lane.get("lane_id"))
            for lane in members
            if lane.get("merge_points")
        )
        if merge_lane_ids:
            reasons.append("official_map_merge_point_requires_segment_split")

        conflicts.append(
            {
                "node_id": key[0],
                "map_role": key[1],
                "approach_id": key[2],
                "status": "pass" if not reasons else "review_required",
                "decision": (
                    "materialize_one_boundary_edge"
                    if not reasons
                    else "split_at_official_merge_points_before_boundary_join"
                ),
                "map_lane_count": map_lane_count,
                "hh_sib_axis_lane_counts_at_boundary": axis_counts,
                "boundary_station_spread_m": (
                    _rounded(station_spread) if station_spread is not None else None
                ),
                "boundary_geometry_spread_m": (
                    _rounded(boundary_spread) if boundary_spread is not None else None
                ),
                "official_merge_lane_ids": merge_lane_ids,
                "reasons": reasons,
            }
        )
    return conflicts


def _build_intersection_stitches(
    approaches: Sequence[Mapping[str, Any]],
    *,
    corridors: Mapping[str, Mapping[str, Any]],
    max_intersection_span_m: float,
    profile_transition_station_tolerance_m: float,
) -> list[dict[str, Any]]:
    egresses = [item for item in approaches if item["map_role"] == "egress" and item["status"] == "pass"]
    ingresses = [item for item in approaches if item["map_role"] == "ingress" and item["status"] == "pass"]
    result: list[dict[str, Any]] = []
    for egress in egresses:
        for ingress in ingresses:
            if egress["node_id"] == ingress["node_id"]:
                continue
            first_binding = egress["binding"]
            second_binding = ingress["binding"]
            if first_binding["corridor_id"] != second_binding["corridor_id"]:
                continue
            corridor = corridors[str(first_binding["corridor_id"])]
            direction = str(corridor["station_direction"])
            egress_junction = _mean(first_binding["junction_station_envelope_m"])
            ingress_junction = _mean(second_binding["junction_station_envelope_m"])
            oriented_egress = egress_junction if direction == "with_stationing" else -egress_junction
            oriented_ingress = ingress_junction if direction == "with_stationing" else -ingress_junction
            if oriented_ingress <= oriented_egress:
                continue
            span = oriented_ingress - oriented_egress
            if span > max_intersection_span_m:
                continue

            first_range = [float(value) for value in first_binding["geometry_station_envelope_m"]]
            second_range = [float(value) for value in second_binding["geometry_station_envelope_m"]]
            overlap_start = max(first_range[0], second_range[0])
            overlap_end = min(first_range[1], second_range[1])
            if overlap_start <= overlap_end:
                envelope_relation = "overlap"
                overlap = [_rounded(overlap_start), _rounded(overlap_end)]
                gap = None
            else:
                envelope_relation = "gap"
                overlap = None
                gap = [_rounded(min(overlap_start, overlap_end)), _rounded(max(overlap_start, overlap_end))]

            low_junction, high_junction = sorted((egress_junction, ingress_junction))
            transitions = [
                item
                for item in corridor["lane_profile_transitions"]
                if low_junction - profile_transition_station_tolerance_m
                <= float(item["station_m"])
                <= high_junction + profile_transition_station_tolerance_m
                and int(item["before_lane_count"]) == int(first_binding["vehicle_lane_count"])
                and int(item["after_lane_count"]) == int(second_binding["vehicle_lane_count"])
            ]
            if len(transitions) == 1:
                status = "pass"
                decision = "authorize_approach_level_cut_only"
                selected_transition = transitions[0]
                authorized = True
                reason = "unique_official_lane_profile_transition_matches_both_approach_lane_counts"
            else:
                status = "review_required"
                decision = "automatic_abstention_no_materialization"
                selected_transition = None
                authorized = False
                reason = (
                    "no_matching_official_lane_profile_transition"
                    if not transitions
                    else "multiple_matching_official_lane_profile_transitions"
                )
            candidate_id = "approach-stitch-" + _stable_digest(
                {
                    "egress": [egress["node_id"], egress["approach_id"]],
                    "ingress": [ingress["node_id"], ingress["approach_id"]],
                    "corridor": corridor["corridor_id"],
                }
            )[:16]
            result.append(
                {
                    "candidate_id": candidate_id,
                    "status": status,
                    "decision": decision,
                    "reason": reason,
                    "human_action_required": False,
                    "approach_level_materialization_authorized": authorized,
                    "lane_index_materialization_authorized": False,
                    "authorized_operation": (
                        "split_directional_HH_SIB_corridor_at_exact_official_lane_profile_transition"
                        if authorized
                        else None
                    ),
                    "upstream_egress": {
                        "node_id": egress["node_id"],
                        "approach_id": egress["approach_id"],
                        "vehicle_lane_count": first_binding["vehicle_lane_count"],
                    },
                    "downstream_ingress": {
                        "node_id": ingress["node_id"],
                        "approach_id": ingress["approach_id"],
                        "vehicle_lane_count": second_binding["vehicle_lane_count"],
                    },
                    "corridor_id": corridor["corridor_id"],
                    "official_link_id": corridor["official_link_id"],
                    "station_direction": direction,
                    "intersection_span_m": _rounded(span),
                    "envelope_relation": envelope_relation,
                    "overlap_interval_m": overlap,
                    "gap_interval_m": gap,
                    "upstream_geometry_station_envelope_m": first_range,
                    "downstream_geometry_station_envelope_m": second_range,
                    "matching_profile_transitions": transitions,
                    "selected_cut": (
                        {
                            "station_m": selected_transition["station_m"],
                            "before_lane_count": selected_transition["before_lane_count"],
                            "after_lane_count": selected_transition["after_lane_count"],
                            "basis": "exact_HH_SIB_adjacent_interval_lane_count_change",
                        }
                        if selected_transition is not None
                        else None
                    ),
                    "lane_index_decision": "automatic_abstention",
                    "claim_boundary": (
                        "The cut preserves official approach-level direction and lane-count transition only; "
                        "it does not select which upstream lane continues, drops, or feeds a downstream lane."
                    ),
                }
            )
    return sorted(result, key=lambda item: item["candidate_id"])


def _parse_xml(path: Path, expected_root: str) -> ET.Element:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise OfficialLaneAxisStitchError(f"invalid PlainXML file: {path}") from exc
    if root.tag != expected_root:
        raise OfficialLaneAxisStitchError(f"PlainXML {path.name} root must be {expected_root}")
    return root


def _parse_shape(value: str | None, *, edge_id: str) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for token in str(value or "").split():
        parts = token.split(",")
        if len(parts) != 2:
            raise OfficialLaneAxisStitchError(f"edge {edge_id} has an invalid shape coordinate")
        points.append((_finite_float(parts[0], "shape x"), _finite_float(parts[1], "shape y")))
    if len(points) < 2:
        raise OfficialLaneAxisStitchError(f"edge {edge_id} shape requires at least two points")
    return tuple(points)


def _unique_params(element: ET.Element, *, edge_id: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for param in element.findall("param"):
        key = str(param.get("key", ""))
        if not key or key in result:
            raise OfficialLaneAxisStitchError(f"edge {edge_id} has a missing or duplicate param key")
        result[key] = str(param.get("value", ""))
    return result


def _project_coordinate(value: Any, transformer: Transformer) -> tuple[float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < 2:
        raise OfficialLaneAxisStitchError("MAP coordinate requires longitude and latitude")
    lon = _finite_float(value[0], "longitude")
    lat = _finite_float(value[1], "latitude")
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise OfficialLaneAxisStitchError("MAP coordinate is outside longitude/latitude bounds")
    x, y = transformer.transform(lon, lat)
    if not math.isfinite(x) or not math.isfinite(y):
        raise OfficialLaneAxisStitchError("MAP coordinate projection is non-finite")
    return float(x), float(y)


def _file_identity(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": file_sha256(path), "bytes": path.stat().st_size}


def _finite_float(value: Any, field_name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise OfficialLaneAxisStitchError(f"{field_name} must be finite") from exc
    if not math.isfinite(numeric):
        raise OfficialLaneAxisStitchError(f"{field_name} must be finite")
    return numeric


def _distance(first: Sequence[float], second: Sequence[float]) -> float:
    return math.hypot(float(first[0]) - float(second[0]), float(first[1]) - float(second[1]))


def _heading_delta(first: float, second: float) -> float:
    return abs((first - second + 180) % 360 - 180)


def _mean(values: Sequence[float]) -> float:
    return sum(float(value) for value in values) / len(values)


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _stable_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: Any) -> bool:
    return bool(_SHA256_PATTERN.fullmatch(str(value or "").lower()))


def _identifier_sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


__all__ = [
    "OFFICIAL_LANE_AXIS_STITCH_SCHEMA",
    "OfficialLaneAxisStitchError",
    "OfficialLaneAxisStitchThresholds",
    "lane_axis_stitch_identity_payload",
    "plan_hamburg_official_map_lane_axis_stitch",
]
