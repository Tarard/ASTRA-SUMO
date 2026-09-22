"""Inspectable, read-only road-source conflation candidates.

This module proposes relationships between official, OSM, and SUMO objects. It
never mutates nodes, channelization, connections, lanes, or signal bindings.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from pyproj import CRS, Transformer

from torii_sumo.road_network.contracts import (
    ROAD_NETWORK_SCHEMA,
    ConflationEvidence,
    RoadObjectRef,
    build_conflation_relation,
)


ROAD_CONFLATION_SCHEMA = f"{ROAD_NETWORK_SCHEMA}/conflation-candidates/v1"
_EVIDENCE_KEYS = (
    "geometry_overlap_ratio",
    "official_coverage_ratio",
    "lateral_distance_m",
    "lateral_distance_p95_m",
    "lateral_distance_max_m",
    "official_to_candidate_lateral_p95_m",
    "candidate_to_official_lateral_p95_m",
    "heading_delta_deg",
    "topology_agreement",
    "name_agreement",
    "road_ref_agreement",
    "official_road_key_agreement",
    "carriageway_agreement",
    "lane_profile_agreement",
    "role_agreement",
)

# A bounded OSM extract needs enough physical extent to distinguish a road
# fragment from a point-crossing or a short digitisation artefact.  These are
# deliberately conservative defaults, not an assertion about a jurisdiction's
# legal stationing system.
_DEFAULT_MIN_BOUNDED_SEGMENT_LENGTH_M = 20.0
_DEFAULT_MIN_BOUNDED_CANDIDATE_COVERAGE = 0.90
_DEFAULT_MAX_BOUNDED_LOCAL_HEADING_DELTA_DEG = 25.0
_FULL_LINK_COVERAGE_THRESHOLD = 0.80


def generate_official_osm_conflation_candidates(
    official_report: Mapping[str, Any],
    osm_report: Mapping[str, Any],
    *,
    target_time: datetime,
    search_radius_m: float = 30.0,
    overlap_tolerance_m: float = 10.0,
    min_bounded_segment_length_m: float = _DEFAULT_MIN_BOUNDED_SEGMENT_LENGTH_M,
    min_bounded_candidate_coverage: float = _DEFAULT_MIN_BOUNDED_CANDIDATE_COVERAGE,
    max_bounded_local_heading_delta_deg: float = _DEFAULT_MAX_BOUNDED_LOCAL_HEADING_DELTA_DEG,
) -> dict[str, Any]:
    """Generate full-link and bounded-segment official-to-OSM review candidates.

    A bounded-segment candidate says only that a single OSM way appears to be
    geometrically contained within an official link.  It is intentionally not
    a full-link equivalence or ``covers`` relation, is never automatically
    aggregated with another same-name OSM fragment, and cannot project
    official road categories.
    """

    _require_aware(target_time, "target_time")
    _require_positive(search_radius_m, "search_radius_m")
    _require_positive(overlap_tolerance_m, "overlap_tolerance_m")
    _require_positive(min_bounded_segment_length_m, "min_bounded_segment_length_m")
    _require_fraction(min_bounded_candidate_coverage, "min_bounded_candidate_coverage")
    _require_positive(max_bounded_local_heading_delta_deg, "max_bounded_local_heading_delta_deg")
    blocking_reasons = _blocked_input_reasons(("official", official_report), ("osm", osm_report))
    if blocking_reasons:
        return _empty_official_osm_report(blocking_reasons)

    raw_official = {
        str(item.get("source_ref", {}).get("object_id", item.get("feature_id", ""))): item
        for item in official_report.get("raw_feature_assertions", ())
    }
    all_official_links = list(official_report.get("motor_road_link_assertions", ()))
    official_links = [
        item
        for item in all_official_links
        if str(item.get("road_link_identity_status", "pass")) == "pass"
    ]
    unresolved_identity_links = [
        item
        for item in all_official_links
        if str(item.get("road_link_identity_status", "pass")) != "pass"
    ]
    osm_ways = list(osm_report.get("way_assertions", ()))
    osm_way_by_id = {str(item.get("way_id", "")): item for item in osm_ways}
    pairwise: list[dict[str, Any]] = []
    link_contexts: dict[str, dict[str, Any]] = {}

    for link in official_links:
        link_ref = _ref_from_dict(link["source_ref"])
        features = [raw_official.get(str(ref.get("object_id", ""))) for ref in link.get("feature_refs", ())]
        features = [item for item in features if item is not None]
        official_geometry = _feature_geometries(features)
        official_properties = dict(features[0].get("properties", {})) if features else {}
        link_contexts[link_ref.object_id] = {
            "link": link,
            "ref": link_ref,
            "geometry": official_geometry,
            "properties": official_properties,
        }

    # The prefilter is deliberately weaker than the exact geometry evaluator:
    # it can only rule out a pair when *both* search-radius-expanded metric
    # envelopes are disjoint.  It is calculated once over the full candidate
    # input extent, so a large official/OSM snapshot does not create one CRS
    # transformer and envelope projection per Cartesian pair.
    bbox_prefilter = _build_official_osm_bbox_prefilter(link_contexts, osm_ways)
    for link in official_links:
        link_ref = _ref_from_dict(link["source_ref"])
        context = link_contexts[link_ref.object_id]
        official_geometry = context["geometry"]
        official_properties = context["properties"]
        for way in osm_ways:
            way_ref = _ref_from_dict(way["source_ref"])
            prefilter_rejected = _bbox_prefilter_rejects_pair(
                bbox_prefilter,
                official_link_id=link_ref.object_id,
                osm_way_id=way_ref.object_id,
                search_radius_m=search_radius_m,
            )
            if prefilter_rejected:
                semantic_evidence = _official_osm_semantic_evidence(link, official_properties, way)
                evidence = _prefilter_rejected_official_osm_evidence(semantic_evidence)
                hard_gates = _prefilter_rejected_official_osm_hard_gates(way, semantic_evidence)
                geometry_evaluation = "bbox_prefilter_rejected"
                prefilter_basis = "expanded_metric_bbox_disjoint"
            else:
                evidence = _official_osm_evidence(
                    link,
                    official_properties,
                    official_geometry,
                    way,
                    overlap_tolerance_m=overlap_tolerance_m,
                )
                hard_gates = _official_osm_hard_gates(
                    official_geometry,
                    way,
                    evidence,
                    search_radius_m=search_radius_m,
                )
                geometry_evaluation = "full_geometry_evaluated"
                prefilter_basis = _bbox_prefilter_non_rejection_basis(
                    bbox_prefilter,
                    official_link_id=link_ref.object_id,
                    osm_way_id=way_ref.object_id,
                )
            projection_gates = _official_osm_property_projection_gates(link, way)
            pair_review_reasons = _official_osm_review_reasons(evidence, projection_gates)
            selection_disposition = (
                "review_only" if "transport_role_mismatch" in projection_gates else "include_in_group_candidate"
            )
            identity = {
                "official_ref": link_ref.as_dict(),
                "osm_ref": way_ref.as_dict(),
                "target_time": target_time.isoformat(),
                "evidence": evidence,
                "hard_gate_failures": hard_gates,
            }
            pairwise.append(
                {
                    "candidate_id": f"official-osm-pair-{_stable_digest(identity)[:20]}",
                    "official_link_id": link_ref.object_id,
                    "osm_way_id": way_ref.object_id,
                    "official_ref": link_ref.as_dict(),
                    "osm_ref": way_ref.as_dict(),
                    "evidence": evidence,
                    "hard_gate_failures": hard_gates,
                    "geometry_evaluation": geometry_evaluation,
                    "prefilter_basis": prefilter_basis,
                    "decision": "blocked" if hard_gates else "eligible",
                    "selection_disposition": selection_disposition,
                    "review_reasons": pair_review_reasons,
                    "property_projection_gate_failures": projection_gates,
                    "classification_only": True,
                    "automatic_promotion_gate": "blocked",
                }
            )

    candidate_sets = _build_official_osm_candidate_sets(
        link_contexts,
        pairwise,
        osm_way_by_id,
        search_radius_m=search_radius_m,
        overlap_tolerance_m=overlap_tolerance_m,
    )
    bounded_segment_candidates, bounded_segment_relation_proposals = _build_bounded_osm_segment_candidates(
        link_contexts,
        pairwise,
        osm_way_by_id,
        target_time=target_time,
        overlap_tolerance_m=overlap_tolerance_m,
        min_segment_length_m=min_bounded_segment_length_m,
        min_candidate_coverage=min_bounded_candidate_coverage,
        max_local_heading_delta_deg=max_bounded_local_heading_delta_deg,
    )
    proposals: list[dict[str, Any]] = []
    report_review_reasons: list[str] = [
        "official_link_identity_not_resolved:"
        f"{item.get('source_ref', {}).get('object_id', item.get('feature_id', ''))}"
        for item in unresolved_identity_links
    ]
    for link_id, context in sorted(link_contexts.items()):
        eligible_sets = [
            item for item in candidate_sets if item["official_link_id"] == link_id and item["decision"] == "eligible"
        ]
        if not eligible_sets:
            report_review_reasons.append(f"official_link_without_eligible_osm_candidate_set:{link_id}")
            continue
        eligible_sets.sort(key=_candidate_set_selection_rank)
        selected_set = eligible_sets[0]
        alternative_set_ids = tuple(item["candidate_set_id"] for item in eligible_sets[1:])
        right_refs = tuple(_ref_from_dict(item) for item in selected_set["osm_refs"])
        aggregate = _relation_evidence_from_mapping(selected_set["evidence"])
        proposal_review_reasons = ["unreviewed_group_selection", *selected_set["review_reasons"]]
        if alternative_set_ids:
            proposal_review_reasons.append("alternative_candidate_sets_present")
            report_review_reasons.append(f"ambiguous_candidate_sets:{link_id}")
        relation = build_conflation_relation(
            left_refs=(context["ref"],),
            right_refs=right_refs,
            relation_kind="covers" if len(right_refs) > 1 else "equivalent",
            direction="both",
            target_time=target_time,
            evidence=aggregate,
            alternative_relation_ids=alternative_set_ids,
            review_reasons=tuple(proposal_review_reasons),
            reason=(
                "Candidate grouping from inspectable geometry/name/classification components; "
                "human review is required before any compatibility projection or network edit."
            ),
        )
        proposal = relation.as_dict()
        proposal["candidate_set_id"] = selected_set["candidate_set_id"]
        proposal["candidate_pair_ids"] = selected_set["member_pair_ids"]
        proposal["selection_components"] = selected_set["selection_components"]
        proposals.append(proposal)
        report_review_reasons.append("unreviewed_group_selection")

    report_review_reasons.extend(
        f"bounded_osm_segment_requires_reference_map_review:{item['segment_candidate_id']}"
        for item in bounded_segment_candidates
        if item["decision"] == "review_candidate"
    )

    identity_eligible_count = sum(item["decision"] == "eligible" for item in pairwise)
    groupable_count = sum(
        item["decision"] == "eligible" and item["selection_disposition"] == "include_in_group_candidate"
        for item in pairwise
    )
    review_only_count = sum(
        item["decision"] == "eligible" and item["selection_disposition"] == "review_only" for item in pairwise
    )
    hard_gated_count = len(pairwise) - identity_eligible_count
    status = "review_required" if proposals or report_review_reasons else "pass"
    return {
        "schema": ROAD_CONFLATION_SCHEMA,
        "relation_layer": "official_to_osm",
        "status": status,
        "target_time": target_time.isoformat(),
        "parameters": {
            "search_radius_m": float(search_radius_m),
            "overlap_tolerance_m": float(overlap_tolerance_m),
            "min_bounded_segment_length_m": float(min_bounded_segment_length_m),
            "min_bounded_candidate_coverage": float(min_bounded_candidate_coverage),
            "max_bounded_local_heading_delta_deg": float(max_bounded_local_heading_delta_deg),
        },
        "pairwise_candidates": sorted(
            pairwise,
            key=lambda item: (item["official_link_id"], item["osm_way_id"]),
        ),
        "candidate_sets": sorted(
            candidate_sets,
            key=lambda item: (item["official_link_id"], item["candidate_set_id"]),
        ),
        "bounded_segment_candidates": sorted(
            bounded_segment_candidates,
            key=lambda item: (item["official_link_id"], item["osm_way_id"], item["segment_candidate_id"]),
        ),
        "relation_proposals": proposals,
        "bounded_segment_relation_proposals": sorted(
            bounded_segment_relation_proposals,
            key=lambda item: (item["left_refs"][0]["object_id"], item["right_refs"][0]["object_id"], item["relation_id"]),
        ),
        "counts": {
            "official_motor_link_count": len(official_links),
            "osm_way_count": len(osm_ways),
            "pairwise_candidate_count": len(pairwise),
            "identity_eligible_pair_count": identity_eligible_count,
            "groupable_pair_count": groupable_count,
            "review_only_pair_count": review_only_count,
            "hard_gated_pair_count": hard_gated_count,
            "candidate_set_count": len(candidate_sets),
            "eligible_candidate_set_count": sum(item["decision"] == "eligible" for item in candidate_sets),
            "relation_proposal_count": len(proposals),
            "bounded_segment_candidate_count": len(bounded_segment_candidates),
            "bounded_segment_review_candidate_count": sum(
                item["decision"] == "review_candidate" for item in bounded_segment_candidates
            ),
            "bounded_segment_blocked_candidate_count": sum(
                item["decision"] == "blocked" for item in bounded_segment_candidates
            ),
            "bounded_segment_relation_proposal_count": len(bounded_segment_relation_proposals),
        },
        "blocking_reasons": [],
        "review_reasons": sorted(set(report_review_reasons)),
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "These are inspectable relationship candidates only. They do not merge OSM nodes, infer legal "
            "movements, channelize approaches, bind traffic lights, write SUMO XML, or treat a bounded OSM "
            "fragment as a full official link."
        ),
    }


def build_osm_sumo_lineage_relations(
    osm_report: Mapping[str, Any],
    sumo_report: Mapping[str, Any],
    *,
    target_time: datetime,
    overlap_tolerance_m: float = 8.0,
) -> dict[str, Any]:
    """Build N:M OSM-way ↔ SUMO-edge lineage relations from declared evidence."""

    _require_aware(target_time, "target_time")
    _require_positive(overlap_tolerance_m, "overlap_tolerance_m")
    blocking_reasons = _blocked_input_reasons(("osm", osm_report), ("sumo", sumo_report))
    osm_sha = str(osm_report.get("source_snapshot", {}).get("sha256", "")).casefold()
    sumo_sha = str(sumo_report.get("source_snapshot", {}).get("sha256", "")).casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", osm_sha):
        blocking_reasons.append("osm_source_sha256_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", sumo_sha):
        blocking_reasons.append("sumo_source_sha256_invalid")
    if sumo_report.get("imported_from") != "osm":
        blocking_reasons.append("sumo_import_source_not_declared_as_osm")
    imported_sha = str(sumo_report.get("imported_source_sha256") or "")
    if not imported_sha:
        blocking_reasons.append("sumo_imported_osm_sha256_not_declared")
    elif imported_sha != osm_sha:
        blocking_reasons.append("sumo_imported_osm_sha256_mismatch")
    if blocking_reasons:
        return _empty_osm_sumo_report(blocking_reasons)

    ways = {str(item.get("source_ref", {}).get("object_id", "")): item for item in osm_report.get("way_assertions", ())}
    edges = {
        str(item.get("source_ref", {}).get("object_id", "")): item for item in sumo_report.get("edge_assertions", ())
    }
    relations: list[dict[str, Any]] = []
    review_reasons: list[str] = []
    unmatched_source_way_ids: list[str] = []

    for way_id, source_index in sorted(sumo_report.get("osm_source_index", {}).items()):
        way = ways.get(str(way_id))
        if way is None:
            unmatched_source_way_ids.append(str(way_id))
            review_reasons.append(f"sumo_lineage_osm_way_not_in_snapshot:{way_id}")
            continue
        selected_edges = [edges[edge_id] for edge_id in source_index.get("edge_ids", ()) if edge_id in edges]
        if not selected_edges:
            review_reasons.append(f"sumo_lineage_without_road_edges:{way_id}")
            continue
        lineage_status = str(source_index.get("lineage_status", "unresolved"))
        relation_review_reasons: list[str] = []
        if lineage_status == "rule_derived":
            relation_review_reasons.append("sumo_osm_lineage_rule_derived")
        elif lineage_status != "observed":
            relation_review_reasons.append("sumo_osm_lineage_mixed_or_unresolved")
        if any(edge.get("origin_evidence", {}).get("conflict") for edge in selected_edges):
            relation_review_reasons.append("sumo_origin_evidence_conflict")

        direction_evidence = _verify_sumo_directions_against_osm(way, selected_edges)
        if direction_evidence["status"] != "geometry_verified":
            relation_review_reasons.append("sumo_relative_direction_not_geometry_verified")

        evidence = _osm_sumo_evidence(
            way,
            selected_edges,
            overlap_tolerance_m=overlap_tolerance_m,
        )
        directions = set(direction_evidence["directions"])
        direction = "both" if {"with", "against"} <= directions else next(iter(directions), "unknown")
        relation = build_conflation_relation(
            left_refs=(_ref_from_dict(way["source_ref"]),),
            right_refs=tuple(_ref_from_dict(edge["source_ref"]) for edge in selected_edges),
            relation_kind="covers" if len(selected_edges) > 1 else "equivalent",
            direction=direction,
            target_time=target_time,
            evidence=evidence,
            review_reasons=tuple(relation_review_reasons),
            reason=(
                "SUMO edge lineage to an OSM way; explicit origId/origID is observed evidence, "
                "while numeric edge-root recovery remains rule-derived."
            ),
        )
        relation_payload = relation.as_dict()
        relation_payload["direction_evidence"] = direction_evidence
        relations.append(relation_payload)
        review_reasons.extend(relation_review_reasons)

    status = "review_required" if review_reasons or any(item["status"] != "pass" for item in relations) else "pass"
    return {
        "schema": ROAD_CONFLATION_SCHEMA,
        "relation_layer": "osm_to_sumo",
        "status": status,
        "target_time": target_time.isoformat(),
        "source_sha256_binding": {
            "osm_source_sha256": osm_sha,
            "sumo_source_sha256": sumo_sha,
            "sumo_declared_imported_source_sha256": imported_sha,
            "status": "pass",
        },
        "relations": relations,
        "unmatched_source_way_ids": unmatched_source_way_ids,
        "counts": {
            "relation_count": len(relations),
            "pass_relation_count": sum(item["status"] == "pass" for item in relations),
            "review_relation_count": sum(item["status"] == "review_required" for item in relations),
            "blocked_relation_count": sum(item["status"] == "blocked" for item in relations),
            "unmatched_source_way_count": len(unmatched_source_way_ids),
        },
        "blocking_reasons": [],
        "review_reasons": sorted(set(review_reasons)),
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "Lineage relations preserve source identity only. They do not authorize SUMO edge rewrites, "
            "junction joins, connections, lane changes, or traffic-light bindings."
        ),
    }


def build_osm_subset_derivation_relations(
    parent_osm_report: Mapping[str, Any],
    derived_osm_report: Mapping[str, Any],
    *,
    target_time: datetime,
) -> dict[str, Any]:
    """Audit a filtered OSM snapshot against its declared full-source snapshot."""

    _require_aware(target_time, "target_time")
    blocking_reasons = _blocked_input_reasons(
        ("parent_osm", parent_osm_report),
        ("derived_osm", derived_osm_report),
    )
    parent_sha = str(parent_osm_report.get("source_snapshot", {}).get("sha256", ""))
    declared_parent_sha = str(
        derived_osm_report.get("derived_from_source_sha256")
        or derived_osm_report.get("source_snapshot", {}).get("derived_from_source_sha256")
        or ""
    )
    if not declared_parent_sha:
        blocking_reasons.append("derived_osm_parent_sha256_not_declared")
    elif declared_parent_sha != parent_sha:
        blocking_reasons.append("derived_osm_parent_sha256_mismatch")
    if blocking_reasons:
        return _empty_osm_derivation_report(blocking_reasons)

    parent_ways = {str(item.get("way_id", "")): item for item in parent_osm_report.get("way_assertions", ())}
    derived_ways = {str(item.get("way_id", "")): item for item in derived_osm_report.get("way_assertions", ())}
    missing_parent_way_ids = sorted(set(derived_ways) - set(parent_ways))
    relations: list[dict[str, Any]] = []
    changed_way_ids: list[str] = []
    for way_id in sorted(set(derived_ways) & set(parent_ways)):
        parent_way = parent_ways[way_id]
        derived_way = derived_ways[way_id]
        content_matches = _osm_way_content_identity(parent_way) == _osm_way_content_identity(derived_way)
        if not content_matches:
            changed_way_ids.append(way_id)
        direction = str(derived_way.get("oneway_direction", "unknown"))
        relation = build_conflation_relation(
            left_refs=(_ref_from_dict(parent_way["source_ref"]),),
            right_refs=(_ref_from_dict(derived_way["source_ref"]),),
            relation_kind="equivalent",
            direction=direction if direction in {"with", "against", "both", "unknown"} else "unknown",
            target_time=target_time,
            evidence=ConflationEvidence(
                geometry_overlap_ratio=1.0 if content_matches else 0.0,
                lateral_distance_m=0.0 if content_matches else None,
                heading_delta_deg=0.0 if content_matches else None,
                topology_agreement=1.0 if content_matches else 0.0,
                source_object_id_agreement=1.0,
                name_agreement=_text_agreement(
                    str(parent_way.get("tags", {}).get("name", "")),
                    str(derived_way.get("tags", {}).get("name", "")),
                ),
            ),
            hard_gate_failures=() if content_matches else ("derived_osm_way_content_mismatch",),
            reason="Same OSM way ID retained by a declared subset/filter derivation; raw tags, nodes, and geometry compared.",
        )
        relations.append(relation.as_dict())

    omitted_way_ids = sorted(set(parent_ways) - set(derived_ways))
    omitted_role_counts = {
        role: sum(role in set(parent_ways[way_id].get("derived_mode_roles", ())) for way_id in omitted_way_ids)
        for role in ("motor_vehicle", "pedestrian", "bicycle")
    }
    if missing_parent_way_ids or changed_way_ids:
        status = "blocked"
    elif any(item["status"] != "pass" for item in relations):
        status = "review_required"
    else:
        status = "pass"
    return {
        "schema": ROAD_CONFLATION_SCHEMA,
        "relation_layer": "osm_source_to_filtered_osm",
        "status": status,
        "target_time": target_time.isoformat(),
        "derivation_kind": derived_osm_report.get("derivation_kind", "unknown"),
        "source_sha256_binding": {
            "parent_osm_source_sha256": parent_sha,
            "derived_declared_parent_sha256": declared_parent_sha,
            "derived_osm_source_sha256": derived_osm_report.get("source_snapshot", {}).get("sha256"),
            "status": "pass",
        },
        "relations": relations,
        "omitted_parent_way_ids": omitted_way_ids,
        "missing_parent_way_ids": missing_parent_way_ids,
        "changed_way_ids": changed_way_ids,
        "omitted_role_counts": omitted_role_counts,
        "counts": {
            "parent_highway_way_count": len(parent_ways),
            "derived_highway_way_count": len(derived_ways),
            "retained_way_relation_count": len(relations),
            "omitted_parent_way_count": len(omitted_way_ids),
            "missing_parent_way_count": len(missing_parent_way_ids),
            "changed_way_count": len(changed_way_ids),
        },
        "blocking_reasons": sorted(
            [
                *(f"derived_way_missing_from_parent:{way_id}" for way_id in missing_parent_way_ids),
                *(f"derived_way_content_mismatch:{way_id}" for way_id in changed_way_ids),
            ]
        ),
        "review_reasons": (
            ["source_validity_not_declared"]
            if status == "review_required"
            and any("source_validity_not_declared" in reason for item in relations for reason in item["review_reasons"])
            else []
        ),
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "This proves retained OSM object identity across two exact snapshots only. Omitted ways remain visible; "
            "the derivation does not prove that the filtered network is multimodally complete."
        ),
    }


def _build_bounded_osm_segment_candidates(
    link_contexts: Mapping[str, Mapping[str, Any]],
    pairwise: Sequence[Mapping[str, Any]],
    osm_way_by_id: Mapping[str, Mapping[str, Any]],
    *,
    target_time: datetime,
    overlap_tolerance_m: float,
    min_segment_length_m: float,
    min_candidate_coverage: float,
    max_local_heading_delta_deg: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return individual, non-aggregated official-link containment candidates.

    This intentionally works per OSM way.  Equal road names are not enough to
    aggregate fragments across an intersection, a service road, or a boundary
    of the caller's extraction.  A future explicit review operation may group
    reviewed fragments, but this discovery step never does.
    """

    candidates: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for pair in sorted(
        pairwise,
        key=lambda item: (str(item.get("official_link_id", "")), str(item.get("osm_way_id", ""))),
    ):
        link_id = str(pair.get("official_link_id", ""))
        way_id = str(pair.get("osm_way_id", ""))
        context = link_contexts.get(link_id)
        way = osm_way_by_id.get(way_id)
        evidence = pair.get("evidence")
        if context is None or way is None or not isinstance(evidence, Mapping):
            continue

        # A containment candidate is specifically for an OSM *slice* of a
        # longer official object.  Full-link candidates remain in the existing
        # candidate-set path.
        official_coverage = _optional_float(evidence.get("official_coverage_ratio"))
        if official_coverage is None or official_coverage >= _FULL_LINK_COVERAGE_THRESHOLD:
            continue
        # Do not surface a same-proximity road merely because it is nearby.  A
        # known name is required here; road ref/key agreement supplements, but
        # does not replace, the name gate for this deliberately narrow mode.
        if _optional_float(evidence.get("name_agreement")) != 1.0:
            continue

        segment_evidence = _bounded_segment_evidence(
            context["link"],
            context["geometry"],
            way,
            overlap_tolerance_m=overlap_tolerance_m,
        )
        hard_gates = _bounded_segment_hard_gates(
            context["link"],
            way,
            evidence,
            segment_evidence,
            overlap_tolerance_m=overlap_tolerance_m,
            min_segment_length_m=min_segment_length_m,
            min_candidate_coverage=min_candidate_coverage,
            max_local_heading_delta_deg=max_local_heading_delta_deg,
        )
        identity = {
            "official_ref": context["ref"].as_dict(),
            "osm_ref": pair["osm_ref"],
            "relation_kind": "contains_bounded_segment",
            "target_time": target_time.isoformat(),
            "evidence": dict(evidence),
            "segment_evidence": segment_evidence,
            "hard_gate_failures": hard_gates,
        }
        segment_candidate_id = f"official-osm-bounded-segment-{_stable_digest(identity)[:20]}"
        review_reasons = [
            "bounded_osm_segment_not_full_link_equivalence",
            "reference_map_review_required",
            "official_stationing_direction_not_verified_by_nullpunkte",
            "automatic_osm_fragment_aggregation_disabled",
        ]
        if segment_evidence["geometry_linear_reference"]["status"] != "pass":
            review_reasons.append("geometry_linear_reference_unavailable")
        if segment_evidence["hh_sib_stationing_hypotheses"]["status"] != "orientation_hypotheses_available":
            review_reasons.append("hh_sib_stationing_hypotheses_unavailable")
        candidate = {
            "segment_candidate_id": segment_candidate_id,
            "candidate_kind": "official_link_contains_bounded_osm_segment",
            "relation_kind": "contains_bounded_segment",
            "official_link_id": link_id,
            "osm_way_id": way_id,
            "official_ref": context["ref"].as_dict(),
            "osm_ref": pair["osm_ref"],
            "evidence": dict(evidence),
            "segment_evidence": segment_evidence,
            "hard_gate_failures": hard_gates,
            "decision": "blocked" if hard_gates else "review_candidate",
            "selection_disposition": "external_reference_map_review_only",
            "property_projection_disposition": "excluded_pending_explicit_segment_review_contract",
            "review_reasons": sorted(set(review_reasons)),
            "reference_map_review_required": True,
            "reference_map_review": {
                "required": True,
                "status": "not_provided",
                "purpose": (
                    "Human visual confirmation of the bounded-fragment continuity and boundaries; official HH-SIB "
                    "remains the road identity/range source."
                ),
                "automated_acquisition": "disabled",
                "examples": ["Google Maps", "Hamburg Geoportal", "aerial imagery"],
            },
            "fragment_aggregation": {
                "automatic_aggregation": "disabled",
                "candidate_osm_way_ids": [way_id],
                "reason": (
                    "Equal normalized OSM name/ref does not prove continuity across a junction, separate "
                    "carriageway, service road, or extraction boundary."
                ),
            },
            "fragment_cause": {
                "value": "unknown",
                "status": "not_inferred",
                "reason": (
                    "A bounded OSM way may result from directional carriageway modelling, local intersection "
                    "segmentation, or the caller's extraction boundary; this matcher does not choose among them."
                ),
            },
            "classification_only": True,
            "automatic_promotion_gate": "blocked",
            "claim_boundary": (
                "The candidate claims only local geometric containment of this one OSM way inside the official "
                "link. It does not establish full-link equivalence, legal road continuity, authoritative HH-SIB "
                "stationing, category transfer, lane binding, or SUMO edits."
            ),
        }
        candidates.append(candidate)
        if hard_gates:
            continue

        relation = build_conflation_relation(
            left_refs=(context["ref"],),
            right_refs=(_ref_from_dict(pair["osm_ref"]),),
            relation_kind="contains_bounded_segment",
            direction=_bounded_segment_direction(way, context["geometry"]),
            target_time=target_time,
            evidence=_relation_evidence_from_mapping(evidence),
            review_reasons=tuple(review_reasons),
            reason=(
                "The frozen OSM way is a locally supported bounded fragment of an official link. "
                "It is not a complete-link equivalence and requires external reference-map review."
            ),
        )
        proposal = relation.as_dict()
        proposal.update(
            {
                "segment_candidate_id": segment_candidate_id,
                "segment_evidence": segment_evidence,
                "reference_map_review_required": True,
                "property_projection_disposition": "excluded_pending_explicit_segment_review_contract",
            }
        )
        proposals.append(proposal)
    return candidates, proposals


def _bounded_segment_direction(
    way: Mapping[str, Any],
    official_geometry: list[list[list[float]]],
) -> str:
    if str(way.get("directionality", "")) == "bidirectional":
        return "both"
    inferred = _way_flow_against_axis(way, official_geometry)
    return inferred if inferred in {"with", "against"} else "unknown"


def _bounded_segment_hard_gates(
    link: Mapping[str, Any],
    way: Mapping[str, Any],
    evidence: Mapping[str, Any],
    segment_evidence: Mapping[str, Any],
    *,
    overlap_tolerance_m: float,
    min_segment_length_m: float,
    min_candidate_coverage: float,
    max_local_heading_delta_deg: float,
) -> list[str]:
    gates: list[str] = []
    if _optional_float(evidence.get("name_agreement")) != 1.0:
        gates.append("bounded_segment_name_agreement_required")
    if _optional_float(evidence.get("road_ref_agreement")) == 0.0:
        gates.append("bounded_segment_explicit_road_ref_conflict")
    if _optional_float(evidence.get("role_agreement")) != 1.0:
        gates.append("bounded_segment_transport_role_mismatch")
    coverage = _optional_float(segment_evidence.get("candidate_geometry_coverage_ratio"))
    if coverage is None or coverage < min_candidate_coverage:
        gates.append("bounded_segment_candidate_geometry_coverage_insufficient")
    lateral_p95 = _optional_float(evidence.get("candidate_to_official_lateral_p95_m"))
    if lateral_p95 is None or lateral_p95 > overlap_tolerance_m:
        gates.append("bounded_segment_lateral_alignment_insufficient")
    local_heading = _optional_float(segment_evidence.get("local_heading_delta_p95_deg"))
    if local_heading is None or local_heading > max_local_heading_delta_deg:
        gates.append("bounded_segment_local_heading_incompatible")
    length_m = _optional_float(segment_evidence.get("osm_fragment_geometry_length_m"))
    if length_m is None or length_m < min_segment_length_m:
        gates.append("bounded_segment_geometry_too_short")
    if not link.get("station_coverage"):
        gates.append("bounded_segment_official_station_coverage_missing")
    if way.get("geometry_role") == "area_boundary":
        gates.append("bounded_segment_osm_area_not_linear_road")
    return list(dict.fromkeys(gates))


def _bounded_segment_evidence(
    link: Mapping[str, Any],
    official_geometry: list[list[list[float]]],
    way: Mapping[str, Any],
    *,
    overlap_tolerance_m: float,
) -> dict[str, Any]:
    """Measure a local fragment against official geometry without inventing stationing.

    The geometry-derived linear reference has an arbitrary coordinate-order
    orientation.  HH-SIB stationing is therefore returned as forward/reverse
    hypotheses, never as an authoritative ``s_from_m``/``s_to_m`` assignment.
    """

    osm_geometry = _normalize_lines([way.get("geometry_lonlat", ())])
    if not official_geometry or not osm_geometry:
        return _empty_bounded_segment_evidence()
    transformer = _local_metric_transformer((*official_geometry, *osm_geometry))
    official_lines = [_project_line(line, transformer) for line in official_geometry]
    osm_lines = [_project_line(line, transformer) for line in osm_geometry]
    official_length = _linework_length(official_lines)
    osm_length = _linework_length(osm_lines)
    samples = _sample_lines(osm_lines)
    projections = [_nearest_projection_with_station(point, official_lines) for point in samples]
    matched = [item for item in projections if item is not None and item["distance_m"] <= overlap_tolerance_m]
    stations = [float(item["geometry_s_m"]) for item in matched]
    candidate_coverage = len(matched) / len(samples) if samples else None
    heading_deltas = _local_heading_deltas(osm_lines, official_lines, overlap_tolerance_m=overlap_tolerance_m)
    geometry_linear_reference: dict[str, Any]
    if official_length > 0 and stations:
        s_from = min(stations)
        s_to = max(stations)
        geometry_linear_reference = {
            "status": "pass",
            "basis": "nearest projections onto frozen official geometry; geometry coordinate order only",
            "official_geometry_length_m": _rounded(official_length, digits=3),
            "s_from_m": _rounded(s_from, digits=3),
            "s_to_m": _rounded(s_to, digits=3),
            "s_from_fraction": _rounded(s_from / official_length),
            "s_to_fraction": _rounded(s_to / official_length),
            "covered_span_ratio": _rounded(max(0.0, s_to - s_from) / official_length),
            "matched_sample_count": len(matched),
            "sample_count": len(samples),
        }
    else:
        geometry_linear_reference = {
            "status": "review_required",
            "basis": "official geometry or projected bounded-fragment samples unavailable",
            "official_geometry_length_m": _rounded(official_length, digits=3) if official_length else None,
            "s_from_m": None,
            "s_to_m": None,
            "s_from_fraction": None,
            "s_to_fraction": None,
            "covered_span_ratio": None,
            "matched_sample_count": len(matched),
            "sample_count": len(samples),
        }
    return {
        "official_geometry_length_m": _rounded(official_length, digits=3) if official_length else None,
        "osm_fragment_geometry_length_m": _rounded(osm_length, digits=3) if osm_length else None,
        "candidate_geometry_coverage_ratio": _rounded(candidate_coverage),
        "local_heading_delta_p95_deg": _rounded(_percentile(heading_deltas, 0.95), digits=3)
        if heading_deltas
        else None,
        "local_heading_delta_max_deg": _rounded(max(heading_deltas), digits=3) if heading_deltas else None,
        "geometry_linear_reference": geometry_linear_reference,
        "hh_sib_stationing_hypotheses": _hh_sib_stationing_hypotheses(
            link,
            geometry_linear_reference,
        ),
    }


def _empty_bounded_segment_evidence() -> dict[str, Any]:
    return {
        "official_geometry_length_m": None,
        "osm_fragment_geometry_length_m": None,
        "candidate_geometry_coverage_ratio": None,
        "local_heading_delta_p95_deg": None,
        "local_heading_delta_max_deg": None,
        "geometry_linear_reference": {
            "status": "review_required",
            "basis": "official geometry or bounded OSM geometry unavailable",
            "official_geometry_length_m": None,
            "s_from_m": None,
            "s_to_m": None,
            "s_from_fraction": None,
            "s_to_fraction": None,
            "covered_span_ratio": None,
            "matched_sample_count": 0,
            "sample_count": 0,
        },
        "hh_sib_stationing_hypotheses": {
            "status": "not_available",
            "authoritative_station_interval_m": None,
            "reason": "geometry linear reference unavailable",
        },
    }


def _hh_sib_stationing_hypotheses(
    link: Mapping[str, Any],
    geometry_linear_reference: Mapping[str, Any],
) -> dict[str, Any]:
    coverage = link.get("station_coverage", {})
    begin = _optional_float(coverage.get("begin_m")) if isinstance(coverage, Mapping) else None
    end = _optional_float(coverage.get("end_m")) if isinstance(coverage, Mapping) else None
    start_fraction = _optional_float(geometry_linear_reference.get("s_from_fraction"))
    end_fraction = _optional_float(geometry_linear_reference.get("s_to_fraction"))
    if begin is None or end is None or end <= begin or start_fraction is None or end_fraction is None:
        return {
            "status": "not_available",
            "authoritative_station_interval_m": None,
            "reason": "official station coverage or geometry linear reference unavailable",
        }
    span = end - begin
    forward = [begin + span * start_fraction, begin + span * end_fraction]
    reverse = [end - span * end_fraction, end - span * start_fraction]
    return {
        "status": "orientation_hypotheses_available",
        "authoritative_station_interval_m": None,
        "official_link_station_coverage_m": [begin, end],
        "forward_geometry_order_interval_estimate_m": [_rounded(forward[0], digits=3), _rounded(forward[1], digits=3)],
        "reverse_geometry_order_interval_estimate_m": [_rounded(reverse[0], digits=3), _rounded(reverse[1], digits=3)],
        "reason": (
            "Geometry coordinate order is not verified against HH-SIB Nullpunkte/stationing direction; "
            "both orientation hypotheses are retained and neither is a property-assignment interval."
        ),
    }


def _linework_length(lines: Sequence[Sequence[tuple[float, float]]]) -> float:
    return sum(math.dist(first, second) for line in lines for first, second in zip(line, line[1:], strict=False))


def _nearest_projection_with_station(
    point: tuple[float, float],
    lines: Sequence[Sequence[tuple[float, float]]],
) -> dict[str, float] | None:
    best: dict[str, float] | None = None
    line_offset = 0.0
    for line in lines:
        segment_offset = 0.0
        for first, second in zip(line, line[1:], strict=False):
            length = math.dist(first, second)
            if length <= 0:
                continue
            distance, factor = _point_segment_projection(point, first, second)
            candidate = {
                "distance_m": distance,
                "geometry_s_m": line_offset + segment_offset + factor * length,
                "segment_heading_deg": _line_heading((first, second)),
            }
            if best is None or candidate["distance_m"] < best["distance_m"]:
                best = candidate
            segment_offset += length
        line_offset += segment_offset
    return best


def _point_segment_projection(
    point: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
) -> tuple[float, float]:
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    denominator = dx * dx + dy * dy
    if denominator == 0:
        return math.dist(point, first), 0.0
    factor = ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / denominator
    factor = min(1.0, max(0.0, factor))
    projected = first[0] + factor * dx, first[1] + factor * dy
    return math.dist(point, projected), factor


def _local_heading_deltas(
    osm_lines: Sequence[Sequence[tuple[float, float]]],
    official_lines: Sequence[Sequence[tuple[float, float]]],
    *,
    overlap_tolerance_m: float,
) -> list[float]:
    deltas: list[float] = []
    for line in osm_lines:
        for first, second in zip(line, line[1:], strict=False):
            if math.dist(first, second) <= 0:
                continue
            midpoint = ((first[0] + second[0]) / 2, (first[1] + second[1]) / 2)
            nearest = _nearest_projection_with_station(midpoint, official_lines)
            if nearest is None or nearest["distance_m"] > overlap_tolerance_m:
                continue
            deltas.append(
                _undirected_heading_delta(
                    _line_heading((first, second)),
                    float(nearest["segment_heading_deg"]),
                )
            )
    return deltas


def _build_official_osm_candidate_sets(
    link_contexts: Mapping[str, Mapping[str, Any]],
    pairwise: Sequence[Mapping[str, Any]],
    osm_way_by_id: Mapping[str, Mapping[str, Any]],
    *,
    search_radius_m: float,
    overlap_tolerance_m: float,
) -> list[dict[str, Any]]:
    candidate_sets: list[dict[str, Any]] = []
    for link_id, context in sorted(link_contexts.items()):
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for pair in pairwise:
            if (
                pair["official_link_id"] != link_id
                or pair["decision"] != "eligible"
                or pair["selection_disposition"] != "include_in_group_candidate"
            ):
                continue
            way = osm_way_by_id.get(str(pair["osm_way_id"]))
            if way is None:
                continue
            groups.setdefault(_candidate_semantic_group_key(way), []).append(pair)

        for semantic_group_key, member_pairs in sorted(groups.items()):
            ways = [osm_way_by_id[str(item["osm_way_id"])] for item in member_pairs]
            endpoint_topology = _osm_endpoint_topology(ways, context["geometry"])
            evidence = _official_osm_set_evidence(
                context,
                ways,
                overlap_tolerance_m=overlap_tolerance_m,
            )
            evidence["topology_agreement"] = endpoint_topology["topology_agreement"]
            hard_gates = _official_osm_set_hard_gates(
                evidence,
                endpoint_topology,
                search_radius_m=search_radius_m,
            )
            member_way_ids = sorted(str(item["way_id"]) for item in ways)
            identity = {
                "official_ref": context["ref"].as_dict(),
                "semantic_group_key": semantic_group_key,
                "member_osm_way_ids": member_way_ids,
                "evidence": evidence,
                "endpoint_topology": endpoint_topology,
                "hard_gate_failures": hard_gates,
            }
            candidate_set_id = f"official-osm-set-{_stable_digest(identity)[:20]}"
            projection_gates = sorted(
                {gate for pair in member_pairs for gate in pair.get("property_projection_gate_failures", ())}
            )
            review_reasons = sorted(
                {
                    "candidate_strand_decomposition_unreviewed",
                    *(reason for pair in member_pairs for reason in pair.get("review_reasons", ())),
                    *(
                        ["osm_directionality_rule_derived_in_candidate_set"]
                        if any(way.get("directionality_status") == "rule_derived" for way in ways)
                        else []
                    ),
                    *(
                        ["ambiguous_endpoint_branching"]
                        if endpoint_topology["finite_form"] == "ambiguous_branching"
                        else []
                    ),
                }
            )
            candidate_sets.append(
                {
                    "candidate_set_id": candidate_set_id,
                    "official_link_id": link_id,
                    "official_ref": context["ref"].as_dict(),
                    "semantic_group_key": semantic_group_key,
                    "grouping_basis": (
                        "shared normalized OSM name/ref after local geometry eligibility, then exact endpoint-node "
                        "topology and maximal-strand decomposition"
                    ),
                    "member_osm_way_ids": member_way_ids,
                    "member_pair_ids": sorted(str(item["candidate_id"]) for item in member_pairs),
                    "osm_refs": sorted(
                        (item["osm_ref"] for item in member_pairs),
                        key=lambda item: str(item["object_id"]),
                    ),
                    "evidence": evidence,
                    "endpoint_topology": endpoint_topology,
                    "hard_gate_failures": hard_gates,
                    "decision": "blocked" if hard_gates else "eligible",
                    "selection_components": {
                        "name_agreement": evidence["name_agreement"],
                        "official_coverage_ratio": evidence["official_coverage_ratio"],
                        "candidate_coverage_ratio": evidence["geometry_overlap_ratio"],
                        "lateral_distance_p95_m": evidence["lateral_distance_p95_m"],
                        "member_way_count": len(member_way_ids),
                        "finite_form": endpoint_topology["finite_form"],
                    },
                    "review_reasons": review_reasons,
                    "property_projection_gate_failures": projection_gates,
                    "classification_only": True,
                    "automatic_promotion_gate": "blocked",
                }
            )
    return candidate_sets


def _candidate_semantic_group_key(way: Mapping[str, Any]) -> str:
    tags = way.get("tags", {})
    name = _normalize_text(str(tags.get("name", "")))
    if name:
        return f"name:{name}"
    road_ref = _normalize_text(str(tags.get("ref", "")))
    if road_ref:
        return f"ref:{road_ref}"
    roles = "+".join(sorted(str(item) for item in way.get("derived_mode_roles", ()))) or "unknown"
    return f"unnamed:{_normalize_text(str(tags.get('highway', '')))}:{roles}"


def _osm_endpoint_topology(
    ways: Sequence[Mapping[str, Any]],
    official_geometry: list[list[list[float]]],
) -> dict[str, Any]:
    edges: list[tuple[str, str, str]] = []
    for way in ways:
        node_refs = [str(item) for item in way.get("node_refs", ())]
        if len(node_refs) >= 2:
            edges.append((str(way["way_id"]), node_refs[0], node_refs[-1]))
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for way_id, first, second in edges:
        adjacency.setdefault(first, []).append((way_id, second))
        adjacency.setdefault(second, []).append((way_id, first))
    components: list[set[str]] = []
    unseen = set(adjacency)
    while unseen:
        start = min(unseen)
        stack = [start]
        component: set[str] = set()
        while stack:
            node_id = stack.pop()
            if node_id in component:
                continue
            component.add(node_id)
            unseen.discard(node_id)
            stack.extend(neighbor for _, neighbor in adjacency.get(node_id, ()) if neighbor not in component)
        components.append(component)
    degree_histogram: dict[str, int] = {}
    for incident in adjacency.values():
        key = str(len(incident))
        degree_histogram[key] = degree_histogram.get(key, 0) + 1
    cycle_rank = max(0, len(edges) - len(adjacency) + len(components))
    branching_nodes = sorted(node_id for node_id, incident in adjacency.items() if len(incident) > 2)
    strands = _maximal_endpoint_strands(edges, adjacency)
    flow_counts = {"with": 0, "against": 0, "unknown": 0, "bidirectional": 0}
    for way in ways:
        if way.get("directionality") == "bidirectional":
            flow_counts["bidirectional"] += 1
            continue
        flow = _way_flow_against_axis(way, official_geometry)
        flow_counts[flow] += 1
    max_degree = max((len(item) for item in adjacency.values()), default=0)
    if (
        len(components) == 1
        and cycle_rank == 0
        and max_degree == 3
        and len(branching_nodes) == 1
        and flow_counts["bidirectional"] > 0
        and flow_counts["with"] > 0
        and flow_counts["against"] > 0
    ):
        finite_form = "directional_pair_with_common_trunk"
    elif (
        len(components) == 2
        and cycle_rank == 0
        and flow_counts["bidirectional"] == 0
        and flow_counts["with"] > 0
        and flow_counts["against"] > 0
    ):
        finite_form = "directional_pair"
    elif len(components) == 1 and cycle_rank == 0 and max_degree <= 2 and flow_counts["bidirectional"] == len(ways):
        finite_form = "single_bidirectional_chain"
    elif len(components) == 1 and cycle_rank == 0 and max_degree <= 2:
        finite_form = "single_directional_chain"
    else:
        finite_form = "ambiguous_branching"
    topology_agreement = 1.0 if finite_form != "ambiguous_branching" and cycle_rank == 0 else 0.5
    return {
        "finite_form": finite_form,
        "edge_count": len(edges),
        "vertex_count": len(adjacency),
        "component_count": len(components),
        "cycle_rank": cycle_rank,
        "degree_histogram": dict(sorted(degree_histogram.items(), key=lambda item: int(item[0]))),
        "branching_node_ids": branching_nodes,
        "flow_counts": flow_counts,
        "strand_count": len(strands),
        "strands": strands,
        "virtual_gap_count": 0,
        "topology_agreement": topology_agreement,
    }


def _maximal_endpoint_strands(
    edges: Sequence[tuple[str, str, str]],
    adjacency: Mapping[str, Sequence[tuple[str, str]]],
) -> list[dict[str, Any]]:
    unused = {way_id for way_id, _, _ in edges}
    edge_endpoints = {way_id: (first, second) for way_id, first, second in edges}
    strands: list[dict[str, Any]] = []
    starts = sorted(node_id for node_id, incident in adjacency.items() if len(incident) != 2)
    for start in starts:
        for first_way_id, neighbor in sorted(adjacency.get(start, ())):
            if first_way_id not in unused:
                continue
            way_ids = [first_way_id]
            node_ids = [start, neighbor]
            unused.remove(first_way_id)
            previous = start
            current = neighbor
            while len(adjacency.get(current, ())) == 2:
                choices = [item for item in adjacency[current] if item[0] in unused and item[1] != previous]
                if not choices:
                    break
                next_way_id, next_node = sorted(choices)[0]
                unused.remove(next_way_id)
                way_ids.append(next_way_id)
                node_ids.append(next_node)
                previous, current = current, next_node
            strands.append({"way_ids": way_ids, "endpoint_node_ids": [node_ids[0], node_ids[-1]]})
    for way_id in sorted(unused):
        first, second = edge_endpoints[way_id]
        strands.append({"way_ids": [way_id], "endpoint_node_ids": [first, second], "cycle_remainder": True})
    return strands


def _way_flow_against_axis(
    way: Mapping[str, Any],
    official_geometry: list[list[list[float]]],
) -> str:
    way_lines = _normalize_lines([way.get("geometry_lonlat", ())])
    if not way_lines or not official_geometry:
        return "unknown"
    transformer = _local_metric_transformer((*official_geometry, *way_lines))
    axis_heading = _line_heading(_project_line(official_geometry[0], transformer))
    way_heading = _line_heading(_project_line(way_lines[0], transformer))
    delta = abs((way_heading - axis_heading + 180) % 360 - 180)
    if delta <= 45:
        return "with"
    if delta >= 135:
        return "against"
    return "unknown"


def _official_osm_set_evidence(
    context: Mapping[str, Any],
    ways: Sequence[Mapping[str, Any]],
    *,
    overlap_tolerance_m: float,
) -> dict[str, float | None]:
    candidate_geometry = _normalize_lines(way.get("geometry_lonlat", ()) for way in ways)
    metrics = _geometry_metrics(
        context["geometry"],
        candidate_geometry,
        overlap_tolerance_m=overlap_tolerance_m,
    )
    link = context["link"]
    group_names = {str(way.get("tags", {}).get("name", "")) for way in ways if way.get("tags", {}).get("name")}
    group_refs = {str(way.get("tags", {}).get("ref", "")) for way in ways if way.get("tags", {}).get("ref")}
    pair_semantics = [
        _official_osm_evidence(
            link,
            context["properties"],
            context["geometry"],
            way,
            overlap_tolerance_m=overlap_tolerance_m,
        )
        for way in ways
    ]
    metrics.update(
        {
            "name_agreement": _agreement_with_group(str(link.get("road_name", "")), group_names),
            "road_ref_agreement": _agreement_with_group(str(link.get("way_number", "")), group_refs),
            "official_road_key_agreement": _minimum(pair_semantics, "official_road_key_agreement"),
            "carriageway_agreement": _minimum(pair_semantics, "carriageway_agreement"),
            "lane_profile_agreement": _minimum(pair_semantics, "lane_profile_agreement"),
            "role_agreement": _minimum(pair_semantics, "role_agreement"),
        }
    )
    return {key: metrics.get(key) for key in _EVIDENCE_KEYS}


def _official_osm_set_hard_gates(
    evidence: Mapping[str, float | None],
    endpoint_topology: Mapping[str, Any],
    *,
    search_radius_m: float,
) -> list[str]:
    gates: list[str] = []
    official_coverage = evidence.get("official_coverage_ratio")
    candidate_coverage = evidence.get("geometry_overlap_ratio")
    if official_coverage is None or candidate_coverage is None or min(official_coverage, candidate_coverage) < 0.8:
        gates.append("candidate_set_insufficient_bidirectional_coverage")
    lateral_p95 = evidence.get("lateral_distance_p95_m")
    if lateral_p95 is None or lateral_p95 > search_radius_m:
        gates.append("candidate_set_gross_lateral_separation")
    heading = evidence.get("heading_delta_deg")
    if heading is None or heading > 45:
        gates.append("candidate_set_heading_incompatible")
    if evidence.get("road_ref_agreement") == 0.0:
        gates.append("candidate_set_explicit_road_ref_conflict")
    if int(endpoint_topology.get("cycle_rank", 0)) > 0:
        gates.append("candidate_set_endpoint_cycle")
    if any(int(value) > 3 for value in endpoint_topology.get("degree_histogram", {})):
        gates.append("candidate_set_unbounded_endpoint_branching")
    return gates


def _candidate_set_selection_rank(item: Mapping[str, Any]) -> tuple[Any, ...]:
    components = item["selection_components"]
    name_agreement = components.get("name_agreement")
    official_coverage = components.get("official_coverage_ratio")
    candidate_coverage = components.get("candidate_coverage_ratio")
    lateral_p95 = components.get("lateral_distance_p95_m")
    return (
        -float(name_agreement if name_agreement is not None else -1.0),
        -float(official_coverage if official_coverage is not None else -1.0),
        -float(candidate_coverage if candidate_coverage is not None else -1.0),
        float(lateral_p95 if lateral_p95 is not None else math.inf),
        str(item["candidate_set_id"]),
    )


def _relation_evidence_from_mapping(value: Mapping[str, float | None]) -> ConflationEvidence:
    return ConflationEvidence(
        geometry_overlap_ratio=value.get("geometry_overlap_ratio"),
        lateral_distance_m=value.get("lateral_distance_m"),
        heading_delta_deg=value.get("heading_delta_deg"),
        topology_agreement=value.get("topology_agreement"),
        name_agreement=value.get("name_agreement"),
        road_ref_agreement=value.get("road_ref_agreement"),
        official_road_key_agreement=value.get("official_road_key_agreement"),
        carriageway_agreement=value.get("carriageway_agreement"),
        lane_profile_agreement=value.get("lane_profile_agreement"),
    )


def _agreement_with_group(expected: str, candidates: set[str]) -> float | None:
    if not expected.strip() or not candidates:
        return None
    scores = [_text_agreement(expected, candidate) for candidate in candidates]
    return max(float(score) for score in scores if score is not None)


def _official_osm_evidence(
    link: Mapping[str, Any],
    official_properties: Mapping[str, Any],
    official_geometry: list[list[list[float]]],
    way: Mapping[str, Any],
    *,
    overlap_tolerance_m: float,
) -> dict[str, float | None]:
    osm_geometry = _normalize_lines([way.get("geometry_lonlat", ())])
    metrics = _geometry_metrics(
        official_geometry,
        osm_geometry,
        overlap_tolerance_m=overlap_tolerance_m,
    )
    metrics.update(_official_osm_semantic_evidence(link, official_properties, way))
    return {key: metrics.get(key) for key in _EVIDENCE_KEYS}


def _official_osm_semantic_evidence(
    link: Mapping[str, Any],
    official_properties: Mapping[str, Any],
    way: Mapping[str, Any],
) -> dict[str, float | None]:
    """Return the non-geometric evidence that is safe before exact geometry.

    This deliberately keeps road identity, carriageway, lane, and role signals
    separate from geometry.  The bbox prefilter can therefore retain a useful
    audit record for clearly remote pairs without calculating or pretending to
    know exact overlap, lateral-distance, or heading values.
    """

    tags = way.get("tags", {})
    official_name = str(link.get("road_name", ""))
    osm_name = str(tags.get("name", ""))
    official_ref = str(link.get("way_number", ""))
    osm_ref = str(tags.get("ref", ""))
    official_key = str(link.get("road_key") or "")
    osm_key = next(
        (
            str(tags[key])
            for key in ("hh_sib:road_key", "official_road_key", "de:hh:sib_road_key")
            if tags.get(key) not in (None, "")
        ),
        "",
    )
    return {
        "name_agreement": _text_agreement(official_name, osm_name),
        "road_ref_agreement": _text_agreement(official_ref, osm_ref),
        "official_road_key_agreement": _text_agreement(official_key, osm_key),
        "carriageway_agreement": _carriageway_agreement(official_properties, way),
        "lane_profile_agreement": _lane_profile_agreement(official_properties, way),
        "role_agreement": (
            1.0 if str(link.get("derived_transport_role", "")) in set(way.get("derived_mode_roles", ())) else 0.0
        ),
    }


def _prefilter_rejected_official_osm_evidence(
    semantic_evidence: Mapping[str, float | None],
) -> dict[str, float | None]:
    """Return an audit payload that explicitly withholds exact geometry values."""

    evidence = _empty_geometry_metrics()
    evidence.update(semantic_evidence)
    return {key: evidence.get(key) for key in _EVIDENCE_KEYS}


def _empty_geometry_metrics() -> dict[str, float | None]:
    """Return the exact-geometry fields withheld after a bbox rejection."""

    return {
        "geometry_overlap_ratio": None,
        "official_coverage_ratio": None,
        "lateral_distance_m": None,
        "lateral_distance_p95_m": None,
        "lateral_distance_max_m": None,
        "official_to_candidate_lateral_p95_m": None,
        "candidate_to_official_lateral_p95_m": None,
        "heading_delta_deg": None,
        "topology_agreement": None,
    }


def _prefilter_rejected_official_osm_hard_gates(
    way: Mapping[str, Any],
    semantic_evidence: Mapping[str, float | None],
) -> list[str]:
    """Hard gates proven by a disjoint expanded metric-envelope test only."""

    gates = ["bbox_prefilter_expanded_metric_bbox_disjoint", "gross_lateral_separation"]
    if way.get("geometry_role") == "area_boundary":
        gates.append("osm_area_not_linear_road")
    if semantic_evidence.get("road_ref_agreement") == 0.0:
        gates.append("explicit_road_ref_conflict")
    return list(dict.fromkeys(gates))


def _official_osm_hard_gates(
    official_geometry: list[list[list[float]]],
    way: Mapping[str, Any],
    evidence: Mapping[str, float | None],
    *,
    search_radius_m: float,
) -> list[str]:
    gates: list[str] = []
    if way.get("geometry_role") == "area_boundary":
        gates.append("osm_area_not_linear_road")
    if not official_geometry or not _normalize_lines([way.get("geometry_lonlat", ())]):
        gates.append("geometry_missing")
    lateral = evidence.get("candidate_to_official_lateral_p95_m")
    if lateral is None or lateral > search_radius_m:
        gates.append("gross_lateral_separation")
    overlap = evidence.get("geometry_overlap_ratio")
    if overlap is None or overlap < 0.2:
        gates.append("insufficient_longitudinal_overlap")
    heading = evidence.get("heading_delta_deg")
    if heading is not None and heading > 45:
        gates.append("point_crossing_or_heading_incompatible")
    if evidence.get("road_ref_agreement") == 0.0:
        gates.append("explicit_road_ref_conflict")
    return list(dict.fromkeys(gates))


def _official_osm_property_projection_gates(
    link: Mapping[str, Any],
    way: Mapping[str, Any],
) -> list[str]:
    gates = ["official_stationing_direction_not_verified_by_nullpunkte"]
    if str(link.get("derived_transport_role", "")) not in set(way.get("derived_mode_roles", ())):
        gates.append("transport_role_mismatch")
    return gates


def _official_osm_review_reasons(
    evidence: Mapping[str, float | None],
    projection_gates: Sequence[str],
) -> list[str]:
    reasons: list[str] = []
    if evidence.get("name_agreement") == 0.0:
        reasons.append("explicit_name_mismatch_soft_evidence")
    if evidence.get("carriageway_agreement") == 0.0:
        reasons.append("carriageway_mismatch_soft_evidence")
    if evidence.get("lane_profile_agreement") == 0.0:
        reasons.append("lane_profile_mismatch_soft_evidence")
    if "transport_role_mismatch" in projection_gates:
        reasons.append("transport_role_mismatch_requires_identity_review")
    return reasons


def _osm_sumo_evidence(
    way: Mapping[str, Any],
    edges: Sequence[Mapping[str, Any]],
    *,
    overlap_tolerance_m: float,
) -> ConflationEvidence:
    osm_geometry = _normalize_lines([way.get("geometry_lonlat", ())])
    sumo_geometry = _normalize_lines(edge.get("geometry_lonlat", ()) for edge in edges)
    metrics = _geometry_metrics(
        osm_geometry,
        sumo_geometry,
        overlap_tolerance_m=overlap_tolerance_m,
    )
    osm_name = str(way.get("tags", {}).get("name", ""))
    name_scores = [_text_agreement(osm_name, str(edge.get("name", ""))) for edge in edges]
    osm_lanes = _positive_int(way.get("tags", {}).get("lanes"))
    sumo_lane_count = max((int(edge.get("lane_count", 0)) for edge in edges), default=0)
    return ConflationEvidence(
        geometry_overlap_ratio=metrics["geometry_overlap_ratio"],
        lateral_distance_m=metrics["lateral_distance_m"],
        heading_delta_deg=metrics["heading_delta_deg"],
        topology_agreement=metrics["official_coverage_ratio"],
        source_object_id_agreement=1.0,
        name_agreement=_minimum_values(name_scores),
        road_ref_agreement=None,
        official_road_key_agreement=None,
        carriageway_agreement=1.0,
        lane_profile_agreement=_numeric_agreement(osm_lanes, sumo_lane_count),
    )


def _verify_sumo_directions_against_osm(
    way: Mapping[str, Any],
    edges: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    osm_lines = _normalize_lines([way.get("geometry_lonlat", ())])
    edge_lines = _normalize_lines(edge.get("geometry_lonlat", ()) for edge in edges)
    if not osm_lines or len(edge_lines) != len(edges):
        return {"status": "review_required", "directions": ["unknown"], "edge_results": []}
    transformer = _local_metric_transformer((*osm_lines, *edge_lines))
    osm_heading = _line_heading(_project_line(osm_lines[0], transformer))
    edge_results: list[dict[str, Any]] = []
    for edge, line in zip(edges, edge_lines, strict=True):
        edge_heading = _line_heading(_project_line(line, transformer))
        delta = abs((edge_heading - osm_heading + 180) % 360 - 180)
        if delta <= 30:
            verified_direction = "with"
        elif delta >= 150:
            verified_direction = "against"
        else:
            verified_direction = "unknown"
        asserted_direction = str(edge.get("relative_direction", "unknown"))
        edge_results.append(
            {
                "sumo_edge_id": edge["edge_id"],
                "verified_direction": verified_direction,
                "directed_heading_delta_deg": _rounded(delta, digits=3),
                "edge_id_rule_direction": asserted_direction,
                "edge_id_rule_agrees": verified_direction != "unknown" and verified_direction == asserted_direction,
            }
        )
    verified = {
        str(item["verified_direction"]) for item in edge_results if item["verified_direction"] in {"with", "against"}
    }
    status = (
        "geometry_verified"
        if len(verified) > 0 and all(item["edge_id_rule_agrees"] for item in edge_results)
        else "review_required"
    )
    return {
        "status": status,
        "directions": sorted(verified) if verified else ["unknown"],
        "edge_results": edge_results,
        "basis": "directed SUMO edge shape heading compared with OSM way coordinate order",
    }


def _geometry_metrics(
    left_lines_lonlat: list[list[list[float]]],
    right_lines_lonlat: list[list[list[float]]],
    *,
    overlap_tolerance_m: float,
) -> dict[str, float | None]:
    if not left_lines_lonlat or not right_lines_lonlat:
        return {
            "geometry_overlap_ratio": None,
            "official_coverage_ratio": None,
            "lateral_distance_m": None,
            "lateral_distance_p95_m": None,
            "lateral_distance_max_m": None,
            "official_to_candidate_lateral_p95_m": None,
            "candidate_to_official_lateral_p95_m": None,
            "heading_delta_deg": None,
            "topology_agreement": None,
        }
    transformer = _local_metric_transformer((*left_lines_lonlat, *right_lines_lonlat))
    left_lines = [_project_line(line, transformer) for line in left_lines_lonlat]
    right_lines = [_project_line(line, transformer) for line in right_lines_lonlat]
    left_samples = _sample_lines(left_lines)
    right_samples = _sample_lines(right_lines)
    if not left_samples or not right_samples:
        return {
            "geometry_overlap_ratio": None,
            "official_coverage_ratio": None,
            "lateral_distance_m": None,
            "lateral_distance_p95_m": None,
            "lateral_distance_max_m": None,
            "official_to_candidate_lateral_p95_m": None,
            "candidate_to_official_lateral_p95_m": None,
            "heading_delta_deg": None,
            "topology_agreement": None,
        }
    left_distances = [_distance_to_lines(point, right_lines) for point in left_samples]
    right_distances = [_distance_to_lines(point, left_lines) for point in right_samples]
    left_coverage = sum(value <= overlap_tolerance_m for value in left_distances) / len(left_distances)
    right_coverage = sum(value <= overlap_tolerance_m for value in right_distances) / len(right_distances)
    lateral = (sum(left_distances) / len(left_distances) + sum(right_distances) / len(right_distances)) / 2
    all_distances = [*left_distances, *right_distances]
    heading_delta = min(
        (
            _undirected_heading_delta(_line_heading(left), _line_heading(right))
            for left in left_lines
            for right in right_lines
        ),
        default=None,
    )
    return {
        "geometry_overlap_ratio": _rounded(right_coverage),
        "official_coverage_ratio": _rounded(left_coverage),
        "lateral_distance_m": _rounded(lateral, digits=3),
        "lateral_distance_p95_m": _rounded(_percentile(all_distances, 0.95), digits=3),
        "lateral_distance_max_m": _rounded(max(all_distances), digits=3),
        "official_to_candidate_lateral_p95_m": _rounded(_percentile(left_distances, 0.95), digits=3),
        "candidate_to_official_lateral_p95_m": _rounded(_percentile(right_distances, 0.95), digits=3),
        "heading_delta_deg": _rounded(heading_delta, digits=3),
        "topology_agreement": _rounded(min(left_coverage, right_coverage)),
    }


def _build_official_osm_bbox_prefilter(
    link_contexts: Mapping[str, Mapping[str, Any]],
    osm_ways: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, tuple[float, float, float, float]]]:
    """Project all candidate geometry once and cache conservative metric bboxes.

    The prefilter is only an optimization and a hard-gate witness for clearly
    disjoint inputs.  Missing or malformed geometry deliberately receives no
    bbox so the caller falls back to the exact evaluator rather than treating
    missing data as proof of separation.
    """

    official_lines = [
        line
        for context in link_contexts.values()
        for line in context.get("geometry", ())
    ]
    osm_lines = [
        line
        for way in osm_ways
        for line in _normalize_lines([way.get("geometry_lonlat", ())])
    ]
    all_lines = [*official_lines, *osm_lines]
    if not all_lines:
        return {"official": {}, "osm": {}}
    transformer = _local_metric_transformer(all_lines)
    official_bboxes: dict[str, tuple[float, float, float, float]] = {}
    for link_id, context in link_contexts.items():
        projected = [_project_line(line, transformer) for line in context.get("geometry", ())]
        bbox = _metric_bbox(projected)
        if bbox is not None:
            official_bboxes[link_id] = bbox
    osm_bboxes: dict[str, tuple[float, float, float, float]] = {}
    for way in osm_ways:
        way_id = str(way.get("source_ref", {}).get("object_id", ""))
        projected = [
            _project_line(line, transformer)
            for line in _normalize_lines([way.get("geometry_lonlat", ())])
        ]
        bbox = _metric_bbox(projected)
        if way_id and bbox is not None:
            osm_bboxes[way_id] = bbox
    return {"official": official_bboxes, "osm": osm_bboxes}


def _bbox_prefilter_rejects_pair(
    prefilter: Mapping[str, Mapping[str, tuple[float, float, float, float]]],
    *,
    official_link_id: str,
    osm_way_id: str,
    search_radius_m: float,
) -> bool:
    official = prefilter.get("official", {}).get(official_link_id)
    osm = prefilter.get("osm", {}).get(osm_way_id)
    if official is None or osm is None:
        return False
    return not _expanded_bboxes_intersect(official, osm, search_radius_m)


def _bbox_prefilter_non_rejection_basis(
    prefilter: Mapping[str, Mapping[str, tuple[float, float, float, float]]],
    *,
    official_link_id: str,
    osm_way_id: str,
) -> str:
    if official_link_id not in prefilter.get("official", {}):
        return "official_geometry_bbox_unavailable"
    if osm_way_id not in prefilter.get("osm", {}):
        return "osm_geometry_bbox_unavailable"
    return "expanded_metric_bbox_not_disjoint"


def _metric_bbox(
    lines: Sequence[Sequence[tuple[float, float]]],
) -> tuple[float, float, float, float] | None:
    points = [point for line in lines for point in line]
    if not points:
        return None
    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    return min(x_values), min(y_values), max(x_values), max(y_values)


def _expanded_bboxes_intersect(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    margin: float,
) -> bool:
    return not (
        first[2] + margin < second[0]
        or second[2] + margin < first[0]
        or first[3] + margin < second[1]
        or second[3] + margin < first[1]
    )


def _feature_geometries(features: Sequence[Mapping[str, Any]]) -> list[list[list[float]]]:
    lines: list[list[list[float]]] = []
    for feature in features:
        geometry = feature.get("geometry")
        if not isinstance(geometry, Mapping):
            continue
        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates")
        if geometry_type == "LineString":
            lines.extend(_normalize_lines([coordinates]))
        elif geometry_type == "MultiLineString":
            lines.extend(_normalize_lines(coordinates or ()))
    return lines


def _normalize_lines(values: Iterable[Any]) -> list[list[list[float]]]:
    lines: list[list[list[float]]] = []
    for raw_line in values:
        if not isinstance(raw_line, (list, tuple)):
            continue
        line: list[list[float]] = []
        for raw_point in raw_line:
            if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
                continue
            try:
                lon, lat = float(raw_point[0]), float(raw_point[1])
            except (TypeError, ValueError):
                continue
            if math.isfinite(lon) and math.isfinite(lat):
                line.append([lon, lat])
        if len(line) >= 2:
            lines.append(line)
    return lines


def _local_metric_transformer(lines: Sequence[Sequence[Sequence[float]]]) -> Transformer:
    points = [point for line in lines for point in line]
    mean_lon = sum(point[0] for point in points) / len(points)
    mean_lat = sum(point[1] for point in points) / len(points)
    if 5.5 <= mean_lon <= 12.0 and 47.0 <= mean_lat <= 56.0:
        epsg = 25832  # ETRS89 / UTM zone 32N, the Hamburg/German official-geodata metric CRS.
    else:
        zone = max(1, min(60, int((mean_lon + 180) // 6) + 1))
        epsg = (32600 if mean_lat >= 0 else 32700) + zone
    return Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg), always_xy=True)


def _project_line(line: Sequence[Sequence[float]], transformer: Transformer) -> list[tuple[float, float]]:
    return [tuple(map(float, transformer.transform(point[0], point[1]))) for point in line]


def _sample_lines(lines: Sequence[Sequence[tuple[float, float]]], spacing_m: float = 5.0) -> list[tuple[float, float]]:
    samples: list[tuple[float, float]] = []
    for line in lines:
        if not line:
            continue
        samples.append(line[0])
        for first, second in zip(line, line[1:], strict=False):
            length = math.dist(first, second)
            steps = max(1, math.ceil(length / spacing_m))
            samples.extend(
                (
                    first[0] + (second[0] - first[0]) * index / steps,
                    first[1] + (second[1] - first[1]) * index / steps,
                )
                for index in range(1, steps + 1)
            )
    return samples


def _distance_to_lines(point: tuple[float, float], lines: Sequence[Sequence[tuple[float, float]]]) -> float:
    return min(
        _point_segment_distance(point, first, second)
        for line in lines
        for first, second in zip(line, line[1:], strict=False)
    )


def _point_segment_distance(
    point: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    denominator = dx * dx + dy * dy
    if denominator == 0:
        return math.dist(point, first)
    factor = ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / denominator
    factor = min(1.0, max(0.0, factor))
    projected = first[0] + factor * dx, first[1] + factor * dy
    return math.dist(point, projected)


def _line_heading(line: Sequence[tuple[float, float]]) -> float:
    first, last = line[0], line[-1]
    return math.degrees(math.atan2(last[1] - first[1], last[0] - first[0])) % 360


def _undirected_heading_delta(first: float, second: float) -> float:
    delta = abs((first - second + 180) % 360 - 180)
    return min(delta, 180 - delta)


def _carriageway_agreement(properties: Mapping[str, Any], way: Mapping[str, Any]) -> float | None:
    carriageways = _positive_int(properties.get("bahnigkeit"))
    if carriageways is None:
        return None
    directionality = str(way.get("directionality", "unknown"))
    if carriageways >= 2:
        return 1.0 if directionality == "one_way" else 0.0
    return 1.0 if directionality == "bidirectional" else 0.0


def _lane_profile_agreement(properties: Mapping[str, Any], way: Mapping[str, Any]) -> float | None:
    osm_lanes = _positive_int(way.get("tags", {}).get("lanes"))
    with_lanes = _non_negative_int(properties.get("fahrstreifenanzahl_in_stationierungsrichtung"))
    against_lanes = _non_negative_int(properties.get("fahrstreifenanzahl_gegen_stationierungsrichtung"))
    both_lanes = _non_negative_int(properties.get("fahrstreifenanzahl_in_beide_richtungen"))
    if str(way.get("directionality")) == "one_way":
        expected = max(with_lanes, against_lanes, both_lanes)
    else:
        expected = with_lanes + against_lanes + both_lanes
    return _numeric_agreement(osm_lanes, expected or None)


def _numeric_agreement(first: int | None, second: int | None) -> float | None:
    if first is None or second is None or max(first, second) <= 0:
        return None
    return _rounded(1 - abs(first - second) / max(first, second))


def _text_agreement(first: str, second: str) -> float | None:
    if not first.strip() or not second.strip():
        return None
    return 1.0 if _normalize_text(first) == _normalize_text(second) else 0.0


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return re.sub(r"[^a-z0-9]+", "", normalized)


def _minimum(items: Sequence[Mapping[str, Any]], key: str) -> float | None:
    return _minimum_values(item.get(key) for item in items)


def _minimum_values(values: Iterable[Any]) -> float | None:
    available = [float(value) for value in values if value is not None]
    return min(available) if available else None


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _ref_from_dict(value: Mapping[str, Any]) -> RoadObjectRef:
    return RoadObjectRef(
        namespace=str(value["namespace"]),
        object_type=str(value["object_type"]),
        object_id=str(value["object_id"]),
        source_sha256=str(value.get("source_sha256", "")),
        valid_from=_optional_datetime(value.get("valid_from")),
        valid_to=_optional_datetime(value.get("valid_to")),
        provider=str(value.get("provider", "")),
        dataset=str(value.get("dataset", "")),
        edition=str(value.get("edition", "")),
        jurisdiction=str(value.get("jurisdiction", "")),
    )


def _optional_datetime(value: Any) -> datetime | None:
    return None if value in (None, "") else datetime.fromisoformat(str(value))


def _blocked_input_reasons(*reports: tuple[str, Mapping[str, Any]]) -> list[str]:
    return [f"{name}_source_report_blocked" for name, report in reports if report.get("status") == "blocked"]


def _osm_way_content_identity(way: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "way_id": str(way.get("way_id", "")),
        "node_refs": list(way.get("node_refs", ())),
        "tags": dict(way.get("tags", {})),
        "geometry_lonlat": list(way.get("geometry_lonlat", ())),
    }


def _empty_osm_derivation_report(blocking_reasons: Sequence[str]) -> dict[str, Any]:
    return {
        "schema": ROAD_CONFLATION_SCHEMA,
        "relation_layer": "osm_source_to_filtered_osm",
        "status": "blocked",
        "relations": [],
        "omitted_parent_way_ids": [],
        "missing_parent_way_ids": [],
        "changed_way_ids": [],
        "omitted_role_counts": {"motor_vehicle": 0, "pedestrian": 0, "bicycle": 0},
        "counts": {
            "parent_highway_way_count": 0,
            "derived_highway_way_count": 0,
            "retained_way_relation_count": 0,
            "omitted_parent_way_count": 0,
            "missing_parent_way_count": 0,
            "changed_way_count": 0,
        },
        "blocking_reasons": sorted(set(blocking_reasons)),
        "review_reasons": [],
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
    }


def _empty_official_osm_report(blocking_reasons: Sequence[str]) -> dict[str, Any]:
    return {
        "schema": ROAD_CONFLATION_SCHEMA,
        "relation_layer": "official_to_osm",
        "status": "blocked",
        "pairwise_candidates": [],
        "candidate_sets": [],
        "bounded_segment_candidates": [],
        "relation_proposals": [],
        "bounded_segment_relation_proposals": [],
        "counts": {
            "official_motor_link_count": 0,
            "osm_way_count": 0,
            "pairwise_candidate_count": 0,
            "identity_eligible_pair_count": 0,
            "groupable_pair_count": 0,
            "review_only_pair_count": 0,
            "hard_gated_pair_count": 0,
            "candidate_set_count": 0,
            "eligible_candidate_set_count": 0,
            "relation_proposal_count": 0,
            "bounded_segment_candidate_count": 0,
            "bounded_segment_review_candidate_count": 0,
            "bounded_segment_blocked_candidate_count": 0,
            "bounded_segment_relation_proposal_count": 0,
        },
        "blocking_reasons": sorted(set(blocking_reasons)),
        "review_reasons": [],
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
    }


def _empty_osm_sumo_report(blocking_reasons: Sequence[str]) -> dict[str, Any]:
    return {
        "schema": ROAD_CONFLATION_SCHEMA,
        "relation_layer": "osm_to_sumo",
        "status": "blocked",
        "relations": [],
        "unmatched_source_way_ids": [],
        "counts": {
            "relation_count": 0,
            "pass_relation_count": 0,
            "review_relation_count": 0,
            "blocked_relation_count": 0,
            "unmatched_source_way_count": 0,
        },
        "blocking_reasons": sorted(set(blocking_reasons)),
        "review_reasons": [],
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
    }


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _require_positive(value: float, field_name: str) -> None:
    if not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError(f"{field_name} must be finite and positive")


def _require_fraction(value: float, field_name: str) -> None:
    parsed = float(value)
    if not math.isfinite(parsed) or not 0 < parsed <= 1:
        raise ValueError(f"{field_name} must be finite and in (0, 1]")


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _rounded(value: float | None, *, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
