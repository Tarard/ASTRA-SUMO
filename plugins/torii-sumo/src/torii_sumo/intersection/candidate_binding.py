from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree as ET

from torii_sumo.corridor.netxml import normalized_net_sha256
from torii_sumo.road_semantics import classify_turn_from_signed_delta

from .geometry import normalize_signed_angle


def bind_materialized_candidate_to_dag(
    *,
    candidate_net: Path,
    target_junction_id: str,
    expected_controller_ids: tuple[str, ...],
    physical_cell: Mapping[str, Any],
    movement_hypotheses: Mapping[str, Any],
    candidate_dag: Mapping[str, Any],
    tls_ownership: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind code-parsed target connections to one declarative DAG node."""

    source = candidate_net.resolve(strict=True)
    root = ET.parse(source).getroot()
    approaches = {item["physical_approach_id"]: item for item in physical_cell["physical_approaches"]}
    way_to_approaches: dict[str, list[str]] = {}
    for approach_id, approach in approaches.items():
        for way_id in approach["source_way_ids"]:
            way_to_approaches.setdefault(str(way_id), []).append(approach_id)

    target_prefix = f":{target_junction_id}_"
    target_connections = [
        connection
        for connection in root.findall("connection")
        if not str(connection.attrib.get("from", "")).startswith(":")
        and str(connection.attrib.get("via", "")).startswith(target_prefix)
    ]
    connection_records = []
    unmapped_connections = []
    direction_mismatches = []
    for connection in target_connections:
        record = _bind_connection(
            connection,
            approaches=approaches,
            way_to_approaches=way_to_approaches,
        )
        if record["binding_status"] != "pass":
            unmapped_connections.append(record)
            continue
        connection_records.append(record)
        if record["sumo_direction_status"] != "match":
            direction_mismatches.append(record["stable_movement_id"])

    actual_ids = {item["stable_movement_id"] for item in connection_records}
    duplicate_actual_ids = sorted(
        movement_id
        for movement_id in actual_ids
        if sum(item["stable_movement_id"] == movement_id for item in connection_records) > 1
    )
    variant_matches = []
    for variant in movement_hypotheses["variants"]:
        expected_ids = {item["stable_movement_id"] for item in variant["atomic_movements"]}
        variant_matches.append(
            {
                "variant_id": variant["variant_id"],
                "method": variant["method"],
                "status": "exact" if actual_ids == expected_ids else "review_required",
                "expected_movement_count": len(expected_ids),
                "actual_movement_count": len(actual_ids),
                "missing_movement_ids": sorted(expected_ids - actual_ids),
                "unexpected_movement_ids": sorted(actual_ids - expected_ids),
            }
        )
    exact_variant_ids = sorted(item["variant_id"] for item in variant_matches if item["status"] == "exact")
    semantic_class_ids = sorted(
        {
            str(node["semantic_class_id"])
            for node in candidate_dag["nodes"]
            if node.get("node_kind") == "movement_semantic_class"
            and set(node["movement_variant_ids"]) & set(exact_variant_ids)
        }
    )
    bound_candidate_ids = sorted(
        str(node["candidate_id"])
        for node in candidate_dag["nodes"]
        if node.get("node_kind") == "candidate_variant"
        and node.get("topology_hypothesis") == "merge_physical_cell"
        and node.get("semantic_class_id") in semantic_class_ids
    )

    junction = root.find(f"junction[@id='{target_junction_id}']")
    actual_controller_ids = sorted(
        {str(connection.attrib.get("tl", "")) for connection in target_connections if connection.attrib.get("tl")}
    )
    structural_findings = []
    if junction is None:
        structural_findings.append("target_candidate_junction_missing")
    if not target_connections:
        structural_findings.append("target_direct_connections_missing")
    if unmapped_connections:
        structural_findings.append("candidate_connections_cannot_map_to_physical_approaches")
    if duplicate_actual_ids:
        structural_findings.append("duplicate_stable_candidate_movement")
    if direction_mismatches:
        structural_findings.append("sumo_direction_disagrees_with_physical_approach_geometry")
    if actual_controller_ids != sorted(set(expected_controller_ids)):
        structural_findings.append("candidate_controller_identity_disagrees")
    if tls_ownership.get("status") != "pass":
        structural_findings.append("tls_ownership_rebuild_not_verified")
    if tls_ownership.get("residual_source_junction_ids"):
        structural_findings.append("obsolete_source_tls_junction_identity_remains")
    if tls_ownership.get("residual_old_controller_ids"):
        structural_findings.append("obsolete_source_tls_controller_identity_remains")
    if len(bound_candidate_ids) != 1:
        structural_findings.append("materialized_candidate_does_not_bind_one_merge_dag_node")

    semantic_findings = []
    if not exact_variant_ids:
        semantic_findings.append("candidate_matches_no_movement_variant")
    if movement_hypotheses["variant_comparison"]["status"] != "exact":
        semantic_findings.append("source_evidence_movement_variants_disagree")
    if movement_hypotheses["nested_restriction_ids"]:
        semantic_findings.append("nested_turn_restrictions_unresolved")

    payload = {
        "schema": "torii.materialized-candidate-dag-binding/v1",
        "candidate_net": {
            "path": str(source),
            "sha256": _file_sha256(source),
            "normalized_sha256": normalized_net_sha256(source),
        },
        "candidate_dag_id": candidate_dag["candidate_dag_id"],
        "target_junction_id": target_junction_id,
        "binding_status": "fail" if structural_findings else "pass",
        "semantic_disposition": "review" if semantic_findings else "suggest",
        "automatic_promotion_gate": "blocked",
        "binding_identity_basis": (
            "normalized network semantics plus stable DAG/movement evidence; "
            "the raw SHA-256 remains provenance evidence but includes "
            "netconvert generation metadata"
        ),
        "bound_candidate_id": (bound_candidate_ids[0] if len(bound_candidate_ids) == 1 else None),
        "bound_semantic_class_ids": semantic_class_ids,
        "exact_movement_variant_ids": exact_variant_ids,
        "target_connection_count": len(target_connections),
        "mapped_connection_count": len(connection_records),
        "connection_records": sorted(
            connection_records,
            key=lambda item: item["stable_movement_id"],
        ),
        "unmapped_connections": unmapped_connections,
        "duplicate_stable_movement_ids": duplicate_actual_ids,
        "direction_mismatch_movement_ids": sorted(direction_mismatches),
        "variant_matches": variant_matches,
        "expected_controller_ids": sorted(set(expected_controller_ids)),
        "actual_controller_ids": actual_controller_ids,
        "obsolete_tls_identity_absence_verified": bool(
            tls_ownership.get("status") == "pass"
            and not tls_ownership.get("residual_source_junction_ids")
            and not tls_ownership.get("residual_old_controller_ids")
        ),
        "structural_findings": structural_findings,
        "semantic_findings": semantic_findings,
        "claim_boundary": (
            "A pass proves that the parsed candidate connections and TLS owner "
            "identity exactly bind to one declared merge candidate. It does not "
            "prove that the selected movement semantics or merge topology match "
            "the real road."
        ),
    }
    binding_identity = {
        **payload,
        "candidate_net": {"normalized_sha256": payload["candidate_net"]["normalized_sha256"]},
    }
    return {
        **payload,
        "binding_id": (f"candidate-binding-{_stable_digest(binding_identity)[:20]}"),
    }


def _bind_connection(
    connection: ET.Element,
    *,
    approaches: Mapping[str, Mapping[str, Any]],
    way_to_approaches: Mapping[str, list[str]],
) -> dict[str, Any]:
    from_edge = str(connection.attrib.get("from", ""))
    to_edge = str(connection.attrib.get("to", ""))
    from_way_id = _osm_way_id_from_edge(from_edge)
    to_way_id = _osm_way_id_from_edge(to_edge)
    source_ids = way_to_approaches.get(from_way_id, [])
    target_ids = way_to_approaches.get(to_way_id, [])
    raw = {
        "from_edge_id": from_edge,
        "from_lane_index": connection.attrib.get("fromLane"),
        "to_edge_id": to_edge,
        "to_lane_index": connection.attrib.get("toLane"),
        "via_lane_id": connection.attrib.get("via"),
        "tl": connection.attrib.get("tl"),
        "link_index": connection.attrib.get("linkIndex"),
        "dir": connection.attrib.get("dir"),
    }
    if len(source_ids) != 1 or len(target_ids) != 1:
        return {
            "binding_status": "fail",
            "reason": "external_edge_way_identity_not_uniquely_mapped",
            "from_osm_way_id": from_way_id,
            "to_osm_way_id": to_way_id,
            "candidate_source_approach_ids": sorted(source_ids),
            "candidate_target_approach_ids": sorted(target_ids),
            "raw_connection": raw,
        }
    try:
        from_lane = int(str(connection.attrib["fromLane"]))
        to_lane = int(str(connection.attrib["toLane"]))
    except (KeyError, ValueError):
        return {
            "binding_status": "fail",
            "reason": "connection_lane_index_invalid",
            "raw_connection": raw,
        }
    source = approaches[source_ids[0]]
    target = approaches[target_ids[0]]
    signed_delta = normalize_signed_angle(
        float(target["bearing_from_seed_deg"]) - ((float(source["bearing_from_seed_deg"]) + 180.0) % 360.0)
    )
    turn = classify_turn_from_signed_delta(signed_delta)
    movement_payload = {
        "from_physical_approach_id": source_ids[0],
        "to_physical_approach_id": target_ids[0],
        "turn": turn,
        "mode": "passenger",
        "from_lane_index": from_lane,
        "to_lane_index": to_lane,
    }
    observed_dir = str(connection.attrib.get("dir", "")).lower()
    expected_dir = {
        "right": "r",
        "straight": "s",
        "left": "l",
        "uturn": "t",
    }[turn]
    return {
        "binding_status": "pass",
        "stable_movement_id": f"movement-{_stable_digest(movement_payload)[:16]}",
        **movement_payload,
        "signed_turn_delta_deg": round(signed_delta, 3),
        "from_osm_way_id": from_way_id,
        "to_osm_way_id": to_way_id,
        "sumo_direction": observed_dir,
        "expected_sumo_direction": expected_dir,
        "sumo_direction_status": "match" if observed_dir == expected_dir else "review_required",
        "raw_connection": raw,
    }


def _osm_way_id_from_edge(edge_id: str) -> str:
    value = edge_id[1:] if edge_id.startswith("-") else edge_id
    return value.split("#", 1)[0]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_digest(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
