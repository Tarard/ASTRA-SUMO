from __future__ import annotations

import hashlib
import json
from typing import Any

from torii_sumo.road_semantics import classify_turn_from_signed_delta

from .geometry import normalize_signed_angle
from .osm_restrictions import SUPPORTED_RESTRICTIONS
from .schema import OSMPatch


_TURN_TOKENS = {
    "left": {"left", "slight_left", "sharp_left"},
    "straight": {"through", "straight"},
    "right": {"right", "slight_right", "sharp_right"},
    "uturn": {"reverse", "uturn"},
}


def build_vehicle_movement_hypotheses(
    patch: OSMPatch,
    physical_cell: dict[str, Any],
    *,
    traffic_side: str,
) -> dict[str, Any]:
    """Build review-only lane movement variants from OSM and cell geometry.

    The strict variant treats present ``turn:lanes`` values literally. The
    continuity variant treats those tags as evidence while preserving outer
    turn lanes and same-road lane continuity. Their disagreement is an
    explicit review result, never an implicit repair authorization.
    """

    approaches = sorted(
        physical_cell.get("physical_approaches", []),
        key=lambda item: item["physical_approach_id"],
    )
    domain_blockers = _domain_blockers(approaches, traffic_side=traffic_side)
    restriction_inventory = _restriction_inventory(
        patch,
        physical_cell=physical_cell,
        approaches=approaches,
    )
    direct_rules = _direct_restriction_rules(restriction_inventory)

    variants = [
        _build_variant(
            approaches,
            method="osm_turn_lanes_strict",
            direct_rules=direct_rules,
            domain_blockers=domain_blockers,
        ),
        _build_variant(
            approaches,
            method="geometry_continuity",
            direct_rules=direct_rules,
            domain_blockers=domain_blockers,
        ),
    ]
    comparison = _compare_variants(variants)
    nested_restrictions = [
        item["restriction_id"]
        for item in restriction_inventory
        if item["scope_class"] in {"boundary_to_internal_chain", "interior_cell_chain"}
    ]
    unresolved_restriction_ids = [
        item["restriction_id"] for item in restriction_inventory if item["support_status"] != "supported"
    ]
    ambiguous_direct_restriction_ids = [
        item["restriction_id"]
        for item in restriction_inventory
        if item["scope_class"] == "direct_boundary_pair"
        and (len(item["source_physical_approach_ids"]) != 1 or len(item["target_physical_approach_ids"]) != 1)
    ]
    unresolved_reasons = []
    if domain_blockers:
        unresolved_reasons.extend(domain_blockers)
    if comparison["status"] != "exact":
        unresolved_reasons.append("movement_variants_disagree")
    if nested_restrictions:
        unresolved_reasons.append("nested_turn_restrictions_require_path_level_resolution")
    if unresolved_restriction_ids:
        unresolved_reasons.append("unsupported_or_incomplete_turn_restriction_evidence")
    if ambiguous_direct_restriction_ids:
        unresolved_reasons.append("direct_turn_restriction_approach_mapping_ambiguous")
    unresolved_reasons.extend(sorted({reason for variant in variants for reason in variant["unresolved_reasons"]}))

    consensus_variant_id = None
    if comparison["status"] == "exact" and not domain_blockers:
        consensus_variant_id = variants[0]["variant_id"]
    payload = {
        "schema": "torii.vehicle-movement-hypotheses/v1",
        "parent_physical_cell_hypothesis_id": physical_cell.get("hypothesis_id"),
        "generation_status": "blocked" if domain_blockers else "pass",
        "disposition": "review" if unresolved_reasons else "suggest",
        "automatic_promotion_gate": "blocked",
        "traffic_side": traffic_side,
        "applicability": "vehicle_only_standard_t3_x4",
        "approach_count": len(approaches),
        "restriction_inventory": restriction_inventory,
        "nested_restriction_ids": sorted(nested_restrictions),
        "unresolved_restriction_ids": sorted(unresolved_restriction_ids),
        "ambiguous_direct_restriction_ids": sorted(ambiguous_direct_restriction_ids),
        "variants": variants,
        "variant_comparison": comparison,
        "consensus_variant_id": consensus_variant_id,
        "unresolved_reasons": sorted(set(unresolved_reasons)),
        "assumptions": [
            "SUMO lane index 0 is the rightmost lane",
            "OSM turn:lanes tokens are ordered left-to-right in travel direction",
            "missing turn:lanes is absence of evidence, not a prohibited movement",
            "nested restriction chains are not flattened into one physical-cell turn",
            "all generated movements remain review-only until independent conflict verification",
        ],
        "claim_boundary": (
            "These are mutually exclusive lane-movement hypotheses. A count match, "
            "routeability pass, or agreement between variants does not authorize a "
            "connection or TLS rewrite."
        ),
    }
    return {
        **payload,
        "hypothesis_set_id": f"movement-set-{_stable_digest(payload)[:20]}",
    }


def _domain_blockers(
    approaches: list[dict[str, Any]],
    *,
    traffic_side: str,
) -> list[str]:
    blockers = []
    if traffic_side != "right":
        blockers.append("traffic_side_outside_certified_domain")
    if len(approaches) not in {3, 4}:
        blockers.append("physical_approach_count_outside_standard_t3_x4_domain")
    if any(item.get("grouping_status") != "pass" for item in approaches):
        blockers.append("physical_approach_grouping_unresolved")
    if any(int(item.get("incoming_lane_count", 0)) <= 0 for item in approaches):
        blockers.append("incoming_lane_count_missing")
    if any(int(item.get("outgoing_lane_count", 0)) <= 0 for item in approaches):
        blockers.append("outgoing_lane_count_missing")
    return blockers


def _build_variant(
    approaches: list[dict[str, Any]],
    *,
    method: str,
    direct_rules: list[dict[str, Any]],
    domain_blockers: list[str],
) -> dict[str, Any]:
    atomic_movements: list[dict[str, Any]] = []
    omitted_families: list[dict[str, Any]] = []
    unresolved_reasons: list[str] = []

    for source in approaches:
        for target in approaches:
            if source["physical_approach_id"] == target["physical_approach_id"]:
                continue
            signed_delta = normalize_signed_angle(
                float(target["bearing_from_seed_deg"]) - ((float(source["bearing_from_seed_deg"]) + 180.0) % 360.0)
            )
            turn = classify_turn_from_signed_delta(signed_delta)
            family_payload = {
                "from_physical_approach_id": source["physical_approach_id"],
                "to_physical_approach_id": target["physical_approach_id"],
                "turn": turn,
                "mode": "passenger",
            }
            family_id = f"movement-family-{_stable_digest(family_payload)[:16]}"
            if turn == "uturn":
                omitted_families.append(
                    {
                        **family_payload,
                        "movement_family_id": family_id,
                        "reason": "distinct_physical_approaches_classify_as_uturn",
                    }
                )
                unresolved_reasons.append("distinct_approach_pair_classifies_as_uturn")
                continue
            restriction = _blocking_direct_rule(
                direct_rules,
                source_id=source["physical_approach_id"],
                target_id=target["physical_approach_id"],
                turn=turn,
            )
            if restriction is not None:
                omitted_families.append(
                    {
                        **family_payload,
                        "movement_family_id": family_id,
                        "reason": "direct_osm_turn_restriction",
                        "restriction_id": restriction["restriction_id"],
                    }
                )
                continue

            source_lanes, lane_evidence, lane_unresolved = _source_lanes(
                source,
                target,
                turn=turn,
                method=method,
            )
            unresolved_reasons.extend(lane_unresolved)
            if not source_lanes:
                omitted_families.append(
                    {
                        **family_payload,
                        "movement_family_id": family_id,
                        "reason": "turn_not_listed_in_present_osm_turn_lanes",
                        "source_turn_lanes_raw": source.get("incoming_turn_lanes_raw"),
                    }
                )
                continue
            target_lanes = _target_lanes(
                source_lanes,
                source_lane_count=int(source["incoming_lane_count"]),
                target_lane_count=int(target["outgoing_lane_count"]),
                turn=turn,
            )
            for source_lane, target_lane in zip(source_lanes, target_lanes, strict=True):
                movement_payload = {
                    **family_payload,
                    "from_lane_index": source_lane,
                    "to_lane_index": target_lane,
                }
                atomic_movements.append(
                    {
                        "stable_movement_id": (f"movement-{_stable_digest(movement_payload)[:16]}"),
                        "movement_family_id": family_id,
                        **movement_payload,
                        "signed_turn_delta_deg": round(signed_delta, 3),
                        "evidence": [
                            "physical_approach_geometry",
                            *lane_evidence,
                        ],
                    }
                )

    atomic_movements.sort(key=lambda item: item["stable_movement_id"])
    omitted_families.sort(key=lambda item: item["movement_family_id"])
    coverage = _lane_coverage(approaches, atomic_movements)
    if coverage["uncovered_incoming_lanes"]:
        unresolved_reasons.append("incoming_lane_without_generated_movement")
    if coverage["uncovered_outgoing_lanes"]:
        unresolved_reasons.append("outgoing_lane_without_generated_source")
    semantic_payload = [_movement_signature_payload(item) for item in atomic_movements]
    payload = {
        "method": method,
        "parent_kind": "physical_cell_hypothesis",
        "generation_status": "blocked" if domain_blockers else "pass",
        "disposition": "review",
        "automatic_promotion_gate": "blocked",
        "movement_family_count": len({item["movement_family_id"] for item in atomic_movements}),
        "atomic_movement_count": len(atomic_movements),
        "atomic_movements": atomic_movements,
        "omitted_families": omitted_families,
        "lane_coverage": coverage,
        "semantic_signature": _stable_digest(semantic_payload),
        "unresolved_reasons": sorted(set(unresolved_reasons)),
    }
    return {
        **payload,
        "variant_id": f"movement-variant-{_stable_digest(payload)[:20]}",
    }


def _source_lanes(
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    turn: str,
    method: str,
) -> tuple[list[int], list[str], list[str]]:
    lane_count = int(source["incoming_lane_count"])
    raw = source.get("incoming_turn_lanes_raw")
    tagged, tag_warnings = _tagged_source_lanes(raw, lane_count=lane_count, turn=turn)
    evidence = []
    unresolved = list(tag_warnings)

    if method == "osm_turn_lanes_strict" and raw:
        if tagged:
            evidence.append("source_lanes:literal_osm_turn_lanes")
            return tagged, evidence, unresolved
        unresolved.append("present_turn_lanes_omit_geometric_turn")
        return [], ["source_lanes:literal_osm_turn_lanes_omission"], unresolved

    if turn == "right":
        lanes = [0]
        evidence.append("source_lanes:rightmost_lane_geometry_default")
    elif turn == "left":
        lanes = [lane_count - 1]
        evidence.append("source_lanes:leftmost_lane_geometry_default")
    else:
        same_road = bool(set(source.get("road_identities", [])) & set(target.get("road_identities", [])))
        target_lane_count = int(target["outgoing_lane_count"])
        if method == "geometry_continuity" and same_road and lane_count == target_lane_count:
            lanes = list(range(lane_count))
            evidence.append("source_lanes:same_road_equal_cardinality_continuity")
            if raw and set(lanes) != set(tagged):
                unresolved.append("straight_lane_continuity_augments_osm_turn_lanes")
        elif tagged:
            lanes = tagged[:target_lane_count]
            evidence.append("source_lanes:osm_through_lane_evidence")
        else:
            lanes = list(range(min(lane_count, target_lane_count)))
            evidence.append("source_lanes:monotonic_geometry_default_missing_turn_lanes")

    if raw and set(lanes) != set(tagged):
        unresolved.append("geometry_lane_assignment_disagrees_with_osm_turn_lanes")
    return sorted(set(lanes)), evidence, unresolved


def _tagged_source_lanes(
    raw: str | None,
    *,
    lane_count: int,
    turn: str,
) -> tuple[list[int], list[str]]:
    if not raw:
        return [], []
    osm_lanes = raw.split("|")
    warnings = []
    if len(osm_lanes) != lane_count:
        warnings.append("turn_lane_token_count_disagrees_with_incoming_lane_count")
    matching = []
    for osm_index, lane_value in enumerate(osm_lanes):
        sumo_index = lane_count - 1 - osm_index
        if sumo_index < 0 or sumo_index >= lane_count:
            continue
        tokens = {token.strip().lower() for token in lane_value.split(";") if token.strip()}
        if tokens & _TURN_TOKENS[turn]:
            matching.append(sumo_index)
    return sorted(set(matching)), warnings


def _target_lanes(
    source_lanes: list[int],
    *,
    source_lane_count: int,
    target_lane_count: int,
    turn: str,
) -> list[int]:
    if target_lane_count <= 1:
        return [0] * len(source_lanes)
    if turn == "right":
        return [min(index, target_lane_count - 1) for index in range(len(source_lanes))]
    if turn == "left":
        start = max(0, target_lane_count - len(source_lanes))
        return [min(start + index, target_lane_count - 1) for index in range(len(source_lanes))]
    if source_lane_count <= 1:
        return [0] * len(source_lanes)
    return [
        min(
            target_lane_count - 1,
            max(0, round(source_lane / (source_lane_count - 1) * (target_lane_count - 1))),
        )
        for source_lane in source_lanes
    ]


def _lane_coverage(
    approaches: list[dict[str, Any]],
    movements: list[dict[str, Any]],
) -> dict[str, Any]:
    used_incoming = {(item["from_physical_approach_id"], int(item["from_lane_index"])) for item in movements}
    used_outgoing = {(item["to_physical_approach_id"], int(item["to_lane_index"])) for item in movements}
    expected_incoming = {
        (approach["physical_approach_id"], lane)
        for approach in approaches
        for lane in range(int(approach["incoming_lane_count"]))
    }
    expected_outgoing = {
        (approach["physical_approach_id"], lane)
        for approach in approaches
        for lane in range(int(approach["outgoing_lane_count"]))
    }
    return {
        "status": (
            "pass" if expected_incoming <= used_incoming and expected_outgoing <= used_outgoing else "review_required"
        ),
        "uncovered_incoming_lanes": _lane_refs(expected_incoming - used_incoming),
        "uncovered_outgoing_lanes": _lane_refs(expected_outgoing - used_outgoing),
    }


def _lane_refs(values: set[tuple[str, int]]) -> list[dict[str, Any]]:
    return [
        {"physical_approach_id": approach_id, "lane_index": lane_index} for approach_id, lane_index in sorted(values)
    ]


def _restriction_inventory(
    patch: OSMPatch,
    *,
    physical_cell: dict[str, Any],
    approaches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scope_nodes = set(map(str, physical_cell.get("path_closure_node_ids", [])))
    boundary_way_to_approaches: dict[str, list[str]] = {}
    for approach in approaches:
        for way_id in approach.get("source_way_ids", []):
            boundary_way_to_approaches.setdefault(str(way_id), []).append(approach["physical_approach_id"])
    records = []
    for relation in patch.relations.values():
        if relation.tags.get("type") != "restriction":
            continue
        via_refs = _member_refs(relation.members, role="via")
        from_way_ids = [ref for member_type, ref in _member_refs(relation.members, role="from") if member_type == "way"]
        to_way_ids = [ref for member_type, ref in _member_refs(relation.members, role="to") if member_type == "way"]
        via_in_scope = any(
            (member_type == "node" and member_ref in scope_nodes)
            or (member_type == "way" and member_ref in boundary_way_to_approaches)
            for member_type, member_ref in via_refs
        )
        boundary_way_relevant = any(way_id in boundary_way_to_approaches for way_id in (*from_way_ids, *to_way_ids))
        if not via_in_scope and not boundary_way_relevant:
            continue
        source_approach_ids = sorted(
            {approach_id for way_id in from_way_ids for approach_id in boundary_way_to_approaches.get(way_id, [])}
        )
        target_approach_ids = sorted(
            {approach_id for way_id in to_way_ids for approach_id in boundary_way_to_approaches.get(way_id, [])}
        )
        if source_approach_ids and target_approach_ids:
            scope_class = "direct_boundary_pair"
        elif source_approach_ids or target_approach_ids:
            scope_class = "boundary_to_internal_chain"
        else:
            scope_class = "interior_cell_chain"
        restriction_type = str(relation.tags.get("restriction", "")).strip().lower()
        semantics = SUPPORTED_RESTRICTIONS.get(restriction_type)
        mode_specific_keys = sorted(key for key in relation.tags if key.startswith("restriction:"))
        evidence_issues = []
        if not via_refs:
            evidence_issues.append("restriction_via_member_missing")
        elif not via_in_scope:
            evidence_issues.append("restriction_via_outside_physical_cell")
        if mode_specific_keys:
            evidence_issues.append("mode_specific_restriction_requires_access_hierarchy_resolution")
        if not restriction_type:
            evidence_issues.append("restriction_type_missing")
        elif semantics is None:
            evidence_issues.append("restriction_type_unsupported")
        records.append(
            {
                "restriction_id": str(relation.id),
                "restriction_type": restriction_type or None,
                "restriction_tag_keys": sorted(
                    key for key in relation.tags if key == "restriction" or key.startswith("restriction:")
                ),
                "mode_specific_restriction_keys": mode_specific_keys,
                "support_status": ("supported" if semantics is not None and not evidence_issues else "review_required"),
                "evidence_issues": evidence_issues,
                "mode": semantics[0] if semantics else None,
                "turn": semantics[1] if semantics else None,
                "from_way_ids": sorted(from_way_ids),
                "to_way_ids": sorted(to_way_ids),
                "via_refs": [{"type": member_type, "ref": member_ref} for member_type, member_ref in via_refs],
                "source_physical_approach_ids": source_approach_ids,
                "target_physical_approach_ids": target_approach_ids,
                "scope_class": scope_class,
            }
        )
    return sorted(records, key=lambda item: item["restriction_id"])


def _member_refs(
    members: list[dict[str, str]],
    *,
    role: str,
) -> list[tuple[str, str]]:
    return sorted(
        {
            (str(member.get("type", "")), str(member.get("ref", "")))
            for member in members
            if member.get("role") == role and member.get("ref")
        }
    )


def _direct_restriction_rules(
    inventory: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rules = []
    for item in inventory:
        if (
            item["scope_class"] != "direct_boundary_pair"
            or item["support_status"] != "supported"
            or len(item["source_physical_approach_ids"]) != 1
            or len(item["target_physical_approach_ids"]) != 1
        ):
            continue
        rules.append(
            {
                "restriction_id": item["restriction_id"],
                "mode": item["mode"],
                "turn": item["turn"],
                "source_id": item["source_physical_approach_ids"][0],
                "target_id": item["target_physical_approach_ids"][0],
            }
        )
    return rules


def _blocking_direct_rule(
    rules: list[dict[str, Any]],
    *,
    source_id: str,
    target_id: str,
    turn: str,
) -> dict[str, Any] | None:
    for rule in rules:
        if rule["source_id"] != source_id:
            continue
        if rule["mode"] == "no" and rule["target_id"] == target_id:
            return rule
        if rule["mode"] == "only" and (rule["target_id"] != target_id or rule["turn"] != turn):
            return rule
    return None


def _compare_variants(variants: list[dict[str, Any]]) -> dict[str, Any]:
    first, second = variants
    first_ids = {item["stable_movement_id"] for item in first["atomic_movements"]}
    second_ids = {item["stable_movement_id"] for item in second["atomic_movements"]}
    return {
        "status": "exact" if first_ids == second_ids else "review_required",
        "first_variant_id": first["variant_id"],
        "second_variant_id": second["variant_id"],
        "only_in_first_movement_ids": sorted(first_ids - second_ids),
        "only_in_second_movement_ids": sorted(second_ids - first_ids),
        "shared_movement_count": len(first_ids & second_ids),
    }


def _movement_signature_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in (
            "from_physical_approach_id",
            "from_lane_index",
            "to_physical_approach_id",
            "to_lane_index",
            "turn",
            "mode",
        )
    }


def _stable_digest(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
