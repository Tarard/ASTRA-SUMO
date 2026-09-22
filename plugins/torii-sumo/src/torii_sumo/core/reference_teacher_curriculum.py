from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.reference_spatial_registration import (
    build_reference_spatial_registration,
)


SCHEMA = "torii.reference-teacher-curriculum/v1"
DEFAULT_SPATIAL_TILE_M = 500.0
DEFAULT_HELD_OUT_MODULUS = 5
DEFAULT_SCREENSHOT_CASE_LIMIT = 18


def build_reference_teacher_curriculum(
    *,
    teacher_action_contracts_file: str | Path,
    reference_join_audit_file: str | Path,
    raw_net_file: str | Path,
    teacher_net_file: str | Path,
    output_dir: str | Path,
    spatial_tile_m: float = DEFAULT_SPATIAL_TILE_M,
    held_out_modulus: int = DEFAULT_HELD_OUT_MODULUS,
    screenshot_case_limit: int = DEFAULT_SCREENSHOT_CASE_LIMIT,
) -> dict[str, Any]:
    """Build a spatially held-out curriculum from existing Ingolstadt evidence.

    This function does not infer or materialize a target-city edit.  It joins the
    existing Torii reference audit, topology estimator, and human-cleaned network
    into immutable state/action records suitable for later rule learning.
    """

    if spatial_tile_m <= 0:
        raise ValueError("spatial_tile_m must be positive")
    if held_out_modulus < 2:
        raise ValueError("held_out_modulus must be at least 2")
    if screenshot_case_limit < 1:
        raise ValueError("screenshot_case_limit must be positive")

    action_path = Path(teacher_action_contracts_file).expanduser().resolve(strict=True)
    audit_path = Path(reference_join_audit_file).expanduser().resolve(strict=True)
    raw_net_path = Path(raw_net_file).expanduser().resolve(strict=True)
    teacher_net_path = Path(teacher_net_file).expanduser().resolve(strict=True)
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    actions_document = _read_json_object(action_path, "teacher action contracts")
    if actions_document.get("schema") != "torii.reference_teacher_action_contracts.v1":
        raise ValueError("teacher action contracts must use the v1 reference schema")
    audit = _read_json_object(audit_path, "reference join audit")
    audited_candidate_path = Path(str(audit.get("candidate_net_file", ""))).resolve()
    if not audited_candidate_path.is_file():
        raise ValueError("reference join audit does not bind its candidate network")
    if file_sha256(audited_candidate_path) != file_sha256(raw_net_path):
        raise ValueError(
            "raw_net_file must be the exact candidate network audited by the reference join report"
        )
    topology_path = Path(str(audit.get("candidate_topology_audit_file", ""))).resolve()
    if not topology_path.is_file():
        raise ValueError("reference join audit does not bind a candidate topology audit")
    topology = _read_json_object(topology_path, "candidate topology audit")

    action_by_reference = {
        str(action.get("reference_id", "")): action
        for action in actions_document.get("actions", [])
        if isinstance(action, dict) and str(action.get("reference_id", ""))
    }
    topology_by_cluster = {
        str(cluster.get("cluster_id", "")): cluster
        for cluster in topology.get("suspicious_clusters", [])
        if isinstance(cluster, dict) and str(cluster.get("cluster_id", ""))
    }
    raw_index = _read_net_index(raw_net_path)
    teacher_index = _read_net_index(teacher_net_path)
    raw_net_sha256 = file_sha256(raw_net_path)
    teacher_net_sha256 = file_sha256(teacher_net_path)
    registration = build_reference_spatial_registration(
        teacher_action_contracts_file=action_path,
        raw_net_file=raw_net_path,
        teacher_net_file=teacher_net_path,
        output_dir=destination / "spatial_registration",
    )
    registration_by_reference = {
        str(case.get("reference_id", "")): case
        for case in registration.get("cases", [])
        if isinstance(case, dict) and str(case.get("reference_id", ""))
    }

    cases: list[dict[str, Any]] = []
    for audit_case in audit.get("all_cases", []):
        if not isinstance(audit_case, dict):
            continue
        reference_id = str(audit_case.get("reference_id", ""))
        action = action_by_reference.get(reference_id)
        if action is None:
            continue
        cluster_id = str(audit_case.get("matched_candidate_cluster_id", ""))
        cluster = topology_by_cluster.get(cluster_id, {})
        estimated_raw_center = _center(cluster, "centroid_x", "centroid_y")
        registration_case = registration_by_reference.get(reference_id, {})
        aligned_view = registration_case.get("aligned_view", {})
        raw_center = _list_point(aligned_view.get("raw_local_center"))
        teacher_center = _list_point(aligned_view.get("human_cleaned_local_center"))
        projected_center = _list_point(aligned_view.get("projected_center"))
        tile_id = _spatial_tile_id(projected_center, spatial_tile_m)
        raw_target = _nearest_junction_id(
            raw_index["junctions"],
            [str(value) for value in audit_case.get("matched_candidate_node_ids", [])],
            estimated_raw_center,
        )
        human_state = _human_state(reference_id, teacher_index)
        raw_state = _raw_state(cluster, audit_case, raw_index)
        stratum = _stratum(action, raw_state, human_state)
        teaching_disposition = _teaching_disposition(action)
        cases.append(
            {
                "case_id": _stable_id(
                    "teacher-case",
                    reference_id,
                    raw_net_sha256,
                    teacher_net_sha256,
                ),
                "reference_id": reference_id,
                "spatial_group_id": tile_id,
                "stratum": stratum,
                "input_state": raw_state,
                "human_cleaned_state": human_state,
                "human_action": action.get("teacher_action", {}),
                "action_family": str(action.get("action_family", "")),
                "teaching_disposition": teaching_disposition,
                "spatial_registration": registration_case,
                "action_dimensions": {
                    "physical_cell": (
                        "join_bounded_conflict_core"
                        if teaching_disposition == "positive_human_join_example"
                        else "review_or_abstain"
                    ),
                    "controller_scope": (
                        "keep_separate_shared_controller_owners"
                        if human_state["shared_controller"]
                        else "single_owner_or_uncontrolled"
                    ),
                    "boundary": "retain_declared_boundary_and_approach_edges",
                },
                "counterexample_evidence": action.get("counterexample_evidence", {}),
                "transfer_gate_status": "blocked",
                "review_targets": {
                    "raw": {
                        "net_file": str(raw_net_path),
                        "junction_id": raw_target,
                        "view_center": list(raw_center) if raw_center else None,
                    },
                    "human_cleaned": {
                        "net_file": str(teacher_net_path),
                        "junction_id": reference_id if reference_id in teacher_index["junctions"] else "",
                        "view_center": list(teacher_center) if teacher_center else None,
                    },
                },
                "evidence_refs": {
                    "teacher_action_contracts": str(action_path),
                    "reference_join_audit": str(audit_path),
                    "candidate_topology_audit": str(topology_path),
                },
            }
        )

    _assign_leakage_groups_and_splits(cases, held_out_modulus)
    cases.sort(key=lambda item: (item["dataset_split"], item["stratum"], item["reference_id"]))
    screenshot_queue = _representative_screenshot_queue(cases, screenshot_case_limit)
    report = {
        "schema": SCHEMA,
        "status": "review_ready" if cases else "blocked",
        "claim_status": "human-cleaning-teacher-evidence" if cases else "blocked",
        "promotion_gate_status": "blocked",
        "promotion_gate_reason": (
            "Ingolstadt human edits are priors only; Hamburg official evidence must authorize every edit"
        ),
        "scope": {
            "purpose": "learn typed human-cleaning actions before applying them to Hamburg",
            "spatial_tile_m": spatial_tile_m,
            "held_out_policy": f"sha256(leakage_group_id) modulo {held_out_modulus} equals zero",
            "neighbor_leakage_policy": (
                "cases sharing a spatial tile, source node, OSM way root, or human controller stay in one split"
            ),
        },
        "source_artifacts": {
            "teacher_action_contracts": _artifact(action_path),
            "reference_join_audit": _artifact(audit_path),
            "candidate_topology_audit": _artifact(topology_path),
            "raw_net": _artifact(raw_net_path),
            "human_cleaned_net": _artifact(teacher_net_path),
            "spatial_registration": _artifact(Path(registration["report_file"])),
        },
        "coordinate_registration": {
            "status": registration["status"],
            "registered_case_count": registration.get("status_counts", {}).get(
                "registered", 0
            ),
            "blocked_case_count": registration.get("status_counts", {}).get("blocked", 0),
            "exact_core_teaching_example_count": registration.get(
                "exact_core_teaching_example_count", 0
            ),
            "screenshot_policy": registration["screenshot_policy"],
        },
        "case_count": len(cases),
        "split_counts": dict(sorted(Counter(case["dataset_split"] for case in cases).items())),
        "action_family_counts": dict(
            sorted(Counter(case["action_family"] for case in cases).items())
        ),
        "teaching_disposition_counts": dict(
            sorted(Counter(case["teaching_disposition"] for case in cases).items())
        ),
        "stratum_counts": dict(sorted(Counter(case["stratum"] for case in cases).items())),
        "screenshot_queue": screenshot_queue,
        "cases": cases,
        "known_limits": [
            "v1 action contracts cover reference joined junctions and abstentions only",
            "TLS, movement, crossing, road-type, and geometry deltas require typed v2 actions",
            "a passing Ingolstadt held-out case does not prove Hamburg field correctness",
            "screenshots are invalid unless both view centers resolve to one projected location",
        ],
    }
    report_file = destination / "ingolstadt-reference-teacher-curriculum.json"
    write_json_atomic(report_file, report, ensure_ascii=False, sort_keys=True)
    manifest = {
        "schema": "torii.reference-teacher-curriculum-manifest/v1",
        "status": report["status"],
        "promotion_gate_status": "blocked",
        "artifacts": [
            *report["source_artifacts"].values(),
            _artifact(report_file),
        ],
    }
    manifest_file = destination / "ingolstadt-reference-teacher-curriculum.manifest.json"
    write_json_atomic(manifest_file, manifest, ensure_ascii=False, sort_keys=True)
    return {
        **report,
        "report_file": str(report_file),
        "report_sha256": file_sha256(report_file),
        "manifest_file": str(manifest_file),
        "manifest_sha256": file_sha256(manifest_file),
    }


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _artifact(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": file_sha256(resolved),
    }


def _read_net_index(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    junctions = {
        element.get("id", ""): dict(element.attrib)
        for element in root.findall("junction")
        if element.get("id")
    }
    edges = {
        element.get("id", ""): {
            **dict(element.attrib),
            "lane_count": len(element.findall("lane")),
            "lane_speeds": [lane.get("speed", "") for lane in element.findall("lane")],
            "lane_permissions": [
                {"allow": lane.get("allow", ""), "disallow": lane.get("disallow", "")}
                for lane in element.findall("lane")
            ],
        }
        for element in root.findall("edge")
        if element.get("id") and element.get("function") != "internal"
    }
    connections = [dict(element.attrib) for element in root.findall("connection")]
    return {"junctions": junctions, "edges": edges, "connections": connections}


def _center(value: dict[str, Any], x_key: str, y_key: str) -> tuple[float, float] | None:
    try:
        return float(value[x_key]), float(value[y_key])
    except (KeyError, TypeError, ValueError):
        return None


def _list_point(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, list | tuple) or len(value) != 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _spatial_tile_id(center: tuple[float, float] | None, tile_size_m: float) -> str:
    if center is None:
        return "tile_unknown"
    return f"tile_{math.floor(center[0] / tile_size_m)}_{math.floor(center[1] / tile_size_m)}"


def _split_for_tile(tile_id: str, held_out_modulus: int) -> str:
    digest = hashlib.sha256(tile_id.encode("utf-8")).digest()
    return "held_out" if int.from_bytes(digest[:4], "big") % held_out_modulus == 0 else "train"


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"rtc_{digest[:16]}"


def _nearest_junction_id(
    junctions: dict[str, dict[str, str]],
    candidates: list[str],
    center: tuple[float, float] | None,
) -> str:
    existing = [candidate for candidate in candidates if candidate in junctions]
    if not existing:
        return ""
    if center is None:
        return sorted(existing)[0]

    def distance(junction_id: str) -> tuple[float, str]:
        attributes = junctions[junction_id]
        try:
            delta_x = float(attributes["x"]) - center[0]
            delta_y = float(attributes["y"]) - center[1]
            return math.hypot(delta_x, delta_y), junction_id
        except (KeyError, TypeError, ValueError):
            return math.inf, junction_id

    return min(existing, key=distance)


def _raw_state(
    cluster: dict[str, Any],
    audit_case: dict[str, Any],
    raw_index: dict[str, Any],
) -> dict[str, Any]:
    boundary_ids = [str(value) for value in cluster.get("boundary_edge_ids", [])]
    boundary_edges = [raw_index["edges"][edge_id] for edge_id in boundary_ids if edge_id in raw_index["edges"]]
    type_counts = Counter(str(edge.get("type", "untyped")) or "untyped" for edge in boundary_edges)
    lane_count_histogram = Counter(int(edge.get("lane_count", 0)) for edge in boundary_edges)
    return {
        "candidate_cluster_id": str(cluster.get("cluster_id", "")),
        "reference_type": str(audit_case.get("reference_type", "")),
        "node_count": int(cluster.get("node_count", 0) or 0),
        "internal_edge_count": int(cluster.get("internal_edge_count", 0) or 0),
        "boundary_edge_count": int(cluster.get("boundary_edge_count", 0) or 0),
        "approach_count": int(cluster.get("approach_count", 0) or 0),
        "traffic_light_node_count": int(cluster.get("traffic_light_node_count", 0) or 0),
        "physical_intersection_shape": str(cluster.get("physical_intersection_shape", "unknown")),
        "physical_intersection_score": cluster.get("physical_intersection_score"),
        "approach_axis_count": int(cluster.get("approach_axis_count", 0) or 0),
        "internal_edge_total_length_m": cluster.get("internal_edge_total_length_m"),
        "internal_edge_max_length_m": cluster.get("internal_edge_max_length_m"),
        "internal_edge_overlap_pair_count": int(cluster.get("internal_edge_overlap_pair_count", 0) or 0),
        "aggregation_decision": str(cluster.get("aggregation_decision", "unknown")),
        "aggregation_confidence": str(cluster.get("aggregation_confidence", "unknown")),
        "modal_primary_role": str(cluster.get("modal_primary_role", "unknown")),
        "risk_flags": sorted(str(value) for value in cluster.get("risk_flags", [])),
        "boundary_road_type_counts": dict(sorted(type_counts.items())),
        "boundary_lane_count_histogram": {
            str(key): value for key, value in sorted(lane_count_histogram.items())
        },
    }


def _human_state(reference_id: str, teacher_index: dict[str, Any]) -> dict[str, Any]:
    junction = teacher_index["junctions"].get(reference_id, {})
    incoming_lanes = str(junction.get("incLanes", "")).split()
    controlled = [
        connection
        for connection in teacher_index["connections"]
        if connection.get("tl") == reference_id
    ]
    owner_junction_ids = sorted(
        {
            str(teacher_index["edges"].get(str(connection.get("from", "")), {}).get("to", ""))
            for connection in controlled
            if teacher_index["edges"].get(str(connection.get("from", "")), {}).get("to")
        }
    )
    incoming_edge_ids = sorted({lane.rsplit("_", 1)[0] for lane in incoming_lanes if "_" in lane})
    incoming_types = Counter(
        str(teacher_index["edges"].get(edge_id, {}).get("type", "untyped")) or "untyped"
        for edge_id in incoming_edge_ids
    )
    return {
        "junction_present": bool(junction),
        "junction_type": str(junction.get("type", "")),
        "incoming_lane_count": len(incoming_lanes),
        "incoming_edge_count": len(incoming_edge_ids),
        "incoming_road_type_counts": dict(sorted(incoming_types.items())),
        "junction_shape_point_count": len(str(junction.get("shape", "")).split()),
        "controlled_connection_count": len(controlled),
        "controller_ids": sorted({str(item.get("tl", "")) for item in controlled if item.get("tl")}),
        "controller_owner_junction_ids": owner_junction_ids,
        "controller_owner_count": len(owner_junction_ids),
        "shared_controller": len(owner_junction_ids) > 1,
        "signal_link_indices": sorted(
            {
                int(item["linkIndex"])
                for item in controlled
                if str(item.get("linkIndex", "")).isdigit()
            }
        ),
    }


def _stratum(
    action: dict[str, Any],
    raw_state: dict[str, Any],
    human_state: dict[str, Any],
) -> str:
    node_count = int(raw_state.get("node_count", 0))
    size = "n2" if node_count == 2 else "n3" if node_count == 3 else "n4" if node_count == 4 else "n5plus"
    control = "signal" if raw_state.get("traffic_light_node_count", 0) or human_state.get("controlled_connection_count", 0) else "unsignalized"
    return "|".join(
        (
            str(action.get("action_family", "unknown")),
            str(raw_state.get("physical_intersection_shape", "unknown")),
            size,
            control,
        )
    )


def _teaching_disposition(action: dict[str, Any]) -> str:
    family = str(action.get("action_family", ""))
    teacher_action = action.get("teacher_action") if isinstance(action.get("teacher_action"), dict) else {}
    evidence = (
        action.get("applicability_evidence")
        if isinstance(action.get("applicability_evidence"), dict)
        else {}
    )
    counterexample = (
        action.get("counterexample_evidence")
        if isinstance(action.get("counterexample_evidence"), dict)
        else {}
    )
    exact_positive = bool(
        family == "bounded_conflict_core_join"
        and evidence.get("source_identity_complete", True)
        and teacher_action.get("absorbed_internal_edge_ids")
        and len(teacher_action.get("retained_boundary_edge_ids", [])) >= 2
        and not counterexample.get("candidate_nodes_outside_teacher_core", [])
        and not counterexample.get("blockers", [])
    )
    if exact_positive:
        return "positive_human_join_example"
    if family.startswith("abstain_"):
        return "negative_or_unidentified_example"
    return "review_counterexample"


def _osm_way_root(edge_id: str) -> str:
    value = edge_id.lstrip("-")
    return value.split("#", 1)[0]


def _assign_leakage_groups_and_splits(
    cases: list[dict[str, Any]],
    held_out_modulus: int,
) -> None:
    parent = list(range(len(cases)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    owner_by_token: dict[str, int] = {}
    for index, case in enumerate(cases):
        human_action = case.get("human_action", {})
        human_state = case.get("human_cleaned_state", {})
        tokens = {f"tile:{case['spatial_group_id']}"}
        tokens.update(
            f"node:{node_id}"
            for node_id in human_action.get("absorbed_source_node_ids", [])
        )
        # Internal OSM-way lineage prevents variants of one conflict core from
        # leaking across folds.  Boundary/approach ways are intentionally not
        # grouping keys: one long arterial may otherwise collapse a whole city
        # into a single dataset group.
        tokens.update(
            f"way:{_osm_way_root(str(edge_id))}"
            for edge_id in human_action.get("absorbed_internal_edge_ids", [])
            if str(edge_id)
        )
        tokens.update(
            f"controller:{controller_id}"
            for controller_id in human_state.get("controller_ids", [])
        )
        for token in sorted(tokens):
            previous = owner_by_token.get(token)
            if previous is None:
                owner_by_token[token] = index
            else:
                union(index, previous)

    members_by_root: dict[int, list[int]] = {}
    for index in range(len(cases)):
        members_by_root.setdefault(find(index), []).append(index)
    for members in members_by_root.values():
        references = sorted(cases[index]["reference_id"] for index in members)
        leakage_group_id = _stable_id("leakage-group", *references)
        split = _split_for_tile(leakage_group_id, held_out_modulus)
        for index in members:
            cases[index]["leakage_group_id"] = leakage_group_id
            cases[index]["dataset_split"] = split


def _feature_tags(case: dict[str, Any]) -> set[str]:
    state = case["input_state"]
    tags = {
        f"split:{case['dataset_split']}",
        f"action:{case['action_family']}",
        f"shape:{state['physical_intersection_shape']}",
        f"nodes:{'5plus' if state['node_count'] >= 5 else state['node_count']}",
        f"control:{'signal' if state['traffic_light_node_count'] else 'unsignalized'}",
    }
    tags.update(f"risk:{risk}" for risk in state.get("risk_flags", []))
    tags.update(f"road:{road_type}" for road_type in state.get("boundary_road_type_counts", {}))
    return tags


def _representative_screenshot_queue(
    cases: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    eligible = [
        case
        for case in cases
        if case["review_targets"]["raw"]["junction_id"]
        and case["review_targets"]["human_cleaned"]["junction_id"]
    ]
    selected: list[dict[str, Any]] = []
    covered: set[str] = set()
    while eligible and len(selected) < limit:
        candidate = max(
            eligible,
            key=lambda case: (
                len(_feature_tags(case) - covered),
                case["dataset_split"] == "held_out",
                case["teaching_disposition"] == "positive_human_join_example",
                case["reference_id"],
            ),
        )
        selected.append(candidate)
        covered.update(_feature_tags(candidate))
        eligible.remove(candidate)
    return [
        {
            "case_id": case["case_id"],
            "reference_id": case["reference_id"],
            "dataset_split": case["dataset_split"],
            "stratum": case["stratum"],
            "teaching_disposition": case["teaching_disposition"],
            "review_targets": case["review_targets"],
            "feature_tags": sorted(_feature_tags(case)),
        }
        for case in selected
    ]
