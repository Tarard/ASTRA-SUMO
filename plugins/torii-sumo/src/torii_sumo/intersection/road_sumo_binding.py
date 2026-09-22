"""Bind classified road arms to declared OSM-to-SUMO lineage evidence.

The road-detail classifier describes the semantic boundary of a physical
intersection cell.  This module puts those arms beside the SUMO road edges
that are already traceable to the same OSM ways.  It deliberately stops one
step before a SUMO lane connection: a way-to-edge lineage does not locate a
stop line, identify a legal turn, or choose a lane-to-lane movement.

The result is therefore a deterministic review plan.  It helps a later
rebuild stage ask the right question (which semantic arm is represented by
which SUMO edges?) without treating a source-ID relationship as geometry or
traffic-control proof.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


ROAD_SUMO_BINDING_SCHEMA = "torii.intersection-road-sumo-binding/v1"
ROAD_DETAIL_SCHEMA = "torii.intersection-road-detail/v1"
ROAD_CONFLATION_SCHEMA_SUFFIX = "/conflation-candidates/v1"


def bind_intersection_road_detail_to_sumo(
    road_detail: Mapping[str, Any],
    osm_sumo_lineage: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a review-only arm-to-edge and connector-intent binding.

    ``osm_sumo_lineage`` must be the output of
    :func:`build_osm_sumo_lineage_relations`.  Only pass-status source
    relations are considered *bound*.  Review-status lineage remains visible
    as a candidate but never makes a connection intent ready for lane review.
    """

    _validate_road_detail(road_detail)
    _validate_lineage(osm_sumo_lineage)

    lineage_by_way = _lineage_by_osm_way(osm_sumo_lineage)
    arm_bindings = [
        _bind_arm(arm, lineage_by_way)
        for arm in sorted(
            _mapping_sequence(road_detail.get("road_arms"), "road_arms"),
            key=lambda item: str(item.get("road_arm_id", "")),
        )
    ]
    arm_by_physical_approach = {
        str(item["physical_approach_id"]): item for item in arm_bindings
    }
    connection_intents = [
        _connection_intent(relation, arm_by_physical_approach)
        for relation in sorted(
            _mapping_sequence(road_detail.get("connection_relations"), "connection_relations"),
            key=lambda item: str(item.get("connector_id", "")),
        )
    ]

    unresolved_arm_ids = [
        str(item["road_arm_id"])
        for item in arm_bindings
        if item["binding_status"] != "bound"
    ]
    blocked_intent_ids = [
        str(item["connection_intent_id"])
        for item in connection_intents
        if item["status"] != "ready_for_lane_connection_review"
    ]
    lineage_binding = dict(osm_sumo_lineage["source_sha256_binding"])
    payload: dict[str, Any] = {
        "schema": ROAD_SUMO_BINDING_SCHEMA,
        "status": (
            "review_required"
            if unresolved_arm_ids or blocked_intent_ids
            else "ready_for_lane_connection_review"
        ),
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "road_detail_id": str(road_detail.get("road_detail_id", "")),
        "road_detail_parent_physical_cell_hypothesis_id": str(
            road_detail.get("parent_physical_cell_hypothesis_id", "")
        ),
        "lineage_source_sha256_binding": {
            "osm_source_sha256": str(lineage_binding["osm_source_sha256"]),
            "sumo_source_sha256": str(lineage_binding["sumo_source_sha256"]),
            "status": "pass",
        },
        "road_arm_bindings": arm_bindings,
        "connection_intents": connection_intents,
        "counts": {
            "road_arm_count": len(arm_bindings),
            "bound_road_arm_count": sum(
                item["binding_status"] == "bound" for item in arm_bindings
            ),
            "connection_intent_count": len(connection_intents),
            "lane_connection_review_ready_count": sum(
                item["status"] == "ready_for_lane_connection_review"
                for item in connection_intents
            ),
        },
        "unresolved_road_arm_ids": unresolved_arm_ids,
        "blocked_connection_intent_ids": blocked_intent_ids,
        "claim_boundary": (
            "This binds physical road-arm semantics to declared OSM-to-SUMO road-edge "
            "lineage. It does not prove a SUMO lane mapping, legal movement, stop line, "
            "junction owner, conflict geometry, or traffic-signal binding."
        ),
    }
    payload["road_sumo_binding_id"] = f"road-sumo-binding-{_stable_digest(payload)[:20]}"
    return payload


def _validate_road_detail(road_detail: Mapping[str, Any]) -> None:
    if str(road_detail.get("schema", "")) != ROAD_DETAIL_SCHEMA:
        raise ValueError("road_detail schema must be torii.intersection-road-detail/v1")
    if road_detail.get("automatic_promotion_gate") != "blocked":
        raise ValueError("road_detail must retain automatic_promotion_gate=blocked")
    _mapping_sequence(road_detail.get("road_arms"), "road_arms")
    _mapping_sequence(road_detail.get("connection_relations"), "connection_relations")


def _validate_lineage(osm_sumo_lineage: Mapping[str, Any]) -> None:
    schema = str(osm_sumo_lineage.get("schema", ""))
    if not schema.endswith(ROAD_CONFLATION_SCHEMA_SUFFIX):
        raise ValueError("osm_sumo_lineage must be a road conflation-candidates report")
    if osm_sumo_lineage.get("relation_layer") != "osm_to_sumo":
        raise ValueError("osm_sumo_lineage relation_layer must be osm_to_sumo")
    if osm_sumo_lineage.get("automatic_promotion_gate") != "blocked":
        raise ValueError("osm_sumo_lineage must retain automatic_promotion_gate=blocked")
    source_binding = osm_sumo_lineage.get("source_sha256_binding")
    if not isinstance(source_binding, Mapping):
        raise ValueError("osm_sumo_lineage source_sha256_binding is required")
    osm_sha = str(source_binding.get("osm_source_sha256", "")).lower()
    sumo_sha = str(source_binding.get("sumo_source_sha256", "")).lower()
    if not _is_sha256(osm_sha) or not _is_sha256(sumo_sha):
        raise ValueError("osm_sumo_lineage must bind valid OSM and SUMO source SHA-256 values")
    if source_binding.get("status") != "pass":
        raise ValueError("osm_sumo_lineage source_sha256_binding must pass")
    _mapping_sequence(osm_sumo_lineage.get("relations"), "osm_sumo_lineage relations")


def _lineage_by_osm_way(osm_sumo_lineage: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    source_binding = osm_sumo_lineage["source_sha256_binding"]
    expected_osm_sha = str(source_binding["osm_source_sha256"]).casefold()
    expected_sumo_sha = str(source_binding["sumo_source_sha256"]).casefold()
    for relation in _mapping_sequence(osm_sumo_lineage.get("relations"), "osm_sumo_lineage relations"):
        relation_id = str(relation.get("relation_id", "")).strip()
        if not relation_id:
            continue
        left_refs = _mapping_sequence(relation.get("left_refs"), "lineage left_refs")
        right_refs = _mapping_sequence(relation.get("right_refs"), "lineage right_refs")
        osm_way_ids = sorted(
            {
                str(ref.get("object_id", ""))
                for ref in left_refs
                if ref.get("namespace") == "osm" and ref.get("object_type") == "way"
                and str(ref.get("object_id", ""))
            }
        )
        sumo_edge_ids = sorted(
            {
                str(ref.get("object_id", ""))
                for ref in right_refs
                if ref.get("namespace") == "sumo" and ref.get("object_type") == "edge"
                and str(ref.get("object_id", ""))
            }
        )
        _validate_ref_hashes(
            left_refs,
            namespace="osm",
            object_type="way",
            expected_sha256=expected_osm_sha,
            relation_id=relation_id,
        )
        _validate_ref_hashes(
            right_refs,
            namespace="sumo",
            object_type="edge",
            expected_sha256=expected_sumo_sha,
            relation_id=relation_id,
        )
        if not osm_way_ids or not sumo_edge_ids:
            continue
        candidate = {
            "relation_id": relation_id,
            "status": str(relation.get("status", "review_required")),
            "direction": str(relation.get("direction", "unknown")),
            "sumo_edge_ids": sumo_edge_ids,
            "review_reasons": sorted(
                {str(reason) for reason in relation.get("review_reasons", ()) if str(reason)}
            ),
        }
        for way_id in osm_way_ids:
            result.setdefault(way_id, []).append(candidate)
    for candidates in result.values():
        candidates.sort(key=lambda item: item["relation_id"])
    return result


def _validate_ref_hashes(
    refs: Sequence[Mapping[str, Any]],
    *,
    namespace: str,
    object_type: str,
    expected_sha256: str,
    relation_id: str,
) -> None:
    """Reject a relation that contradicts its enclosing immutable snapshot.

    Older/synthetic fixtures may omit a reference-level hash, because the
    enclosing lineage binding is still sufficient.  Once a source hash is
    supplied, however, it must agree with that binding rather than be silently
    accepted as a second version of the same road edge.
    """

    for ref in refs:
        if ref.get("namespace") != namespace or ref.get("object_type") != object_type:
            continue
        declared = str(ref.get("source_sha256", "")).casefold()
        if declared and declared != expected_sha256:
            raise ValueError(
                f"lineage relation {relation_id!r} {namespace}:{object_type} ref "
                "does not bind the enclosing source SHA-256"
            )


def _bind_arm(
    arm: Mapping[str, Any],
    lineage_by_way: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    arm_id = str(arm.get("road_arm_id", "")).strip()
    physical_approach_id = str(arm.get("physical_approach_id", "")).strip()
    if not arm_id or not physical_approach_id:
        raise ValueError("each road arm requires road_arm_id and physical_approach_id")
    way_ids = sorted({str(value) for value in arm.get("source_way_ids", ()) if str(value)})
    per_way: list[dict[str, Any]] = []
    all_candidate_edges: set[str] = set()
    all_trusted_edges: set[str] = set()
    candidate_relation_ids: set[str] = set()
    trusted_relation_ids: set[str] = set()
    missing_way_ids: list[str] = []
    review_way_ids: list[str] = []
    for way_id in way_ids:
        candidates = list(lineage_by_way.get(way_id, ()))
        trusted = [item for item in candidates if item["status"] == "pass"]
        candidate_edges = sorted(
            {edge_id for item in candidates for edge_id in item["sumo_edge_ids"]}
        )
        trusted_edges = sorted(
            {edge_id for item in trusted for edge_id in item["sumo_edge_ids"]}
        )
        if not candidates:
            missing_way_ids.append(way_id)
        elif not trusted:
            review_way_ids.append(way_id)
        all_candidate_edges.update(candidate_edges)
        all_trusted_edges.update(trusted_edges)
        candidate_relation_ids.update(item["relation_id"] for item in candidates)
        trusted_relation_ids.update(item["relation_id"] for item in trusted)
        per_way.append(
            {
                "osm_way_id": way_id,
                "candidate_relation_ids": [item["relation_id"] for item in candidates],
                "trusted_relation_ids": [item["relation_id"] for item in trusted],
                "candidate_sumo_edge_ids": candidate_edges,
                "trusted_sumo_edge_ids": trusted_edges,
                "status": (
                    "bound" if trusted else "review_required" if candidates else "unmapped"
                ),
            }
        )
    if not way_ids:
        binding_status = "unmapped"
        missing_way_ids.append("<road_arm_has_no_source_way>")
    elif missing_way_ids:
        binding_status = "partial" if all_trusted_edges else "unmapped"
    elif review_way_ids:
        binding_status = "partial" if all_trusted_edges else "review_required"
    else:
        binding_status = "bound"
    return {
        "road_arm_id": arm_id,
        "physical_approach_id": physical_approach_id,
        "source_way_ids": way_ids,
        "binding_status": binding_status,
        "per_way_bindings": per_way,
        "candidate_sumo_edge_ids": sorted(all_candidate_edges),
        "trusted_sumo_edge_ids": sorted(all_trusted_edges),
        "candidate_relation_ids": sorted(candidate_relation_ids),
        "trusted_relation_ids": sorted(trusted_relation_ids),
        "unmapped_osm_way_ids": missing_way_ids,
        "review_required_osm_way_ids": review_way_ids,
        "claim_boundary": (
            "A bound arm identifies road-edge lineage only. Its lane indices and exact "
            "junction-facing endpoint still require dedicated geometry and legal-movement evidence."
        ),
    }


def _connection_intent(
    relation: Mapping[str, Any],
    arm_by_physical_approach: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    connector_id = str(relation.get("connector_id", "")).strip()
    source_id = str(relation.get("from_physical_approach_id", "")).strip()
    target_id = str(relation.get("to_physical_approach_id", "")).strip()
    if not connector_id or not source_id or not target_id:
        raise ValueError("each connection relation requires connector and physical approach identifiers")
    source = arm_by_physical_approach.get(source_id)
    target = arm_by_physical_approach.get(target_id)
    both_bound = (
        source is not None
        and target is not None
        and source["binding_status"] == "bound"
        and target["binding_status"] == "bound"
    )
    status = "ready_for_lane_connection_review" if both_bound else "blocked"
    identity = {
        "connector_id": connector_id,
        "from_physical_approach_id": source_id,
        "to_physical_approach_id": target_id,
    }
    return {
        "connection_intent_id": f"connection-intent-{_stable_digest(identity)[:20]}",
        "connector_id": connector_id,
        "relation": str(relation.get("relation", "arm_to_arm")),
        "from_physical_approach_id": source_id,
        "to_physical_approach_id": target_id,
        "from_trusted_sumo_edge_ids": list(source["trusted_sumo_edge_ids"]) if source else [],
        "to_trusted_sumo_edge_ids": list(target["trusted_sumo_edge_ids"]) if target else [],
        "status": status,
        "required_before_materialization": [
            "junction_facing_lane_and_stop_line_binding",
            "physical_connector_geometry_or_map_evidence",
            "legal_turn_or_signal_control_evidence",
            "SUMO_owner_and_link_index_decision",
        ],
        "evidence_ids": sorted(
            {
                connector_id,
                *map(str, relation.get("evidence_ids", ())),
                *(source["trusted_relation_ids"] if source else ()),
                *(target["trusted_relation_ids"] if target else ()),
            }
        ),
        "claim_boundary": (
            "This is a semantic routing intent, not an emitted SUMO connection or a traffic-light link."
        ),
    }


def _mapping_sequence(value: Any, field_name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field_name} must be a sequence of mappings")
    if any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"{field_name} must be a sequence of mappings")
    return list(value)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
