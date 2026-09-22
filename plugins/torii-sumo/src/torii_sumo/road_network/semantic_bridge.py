"""Evidence-bound bridge from frozen road sources to intersection road detail.

This module composes the read-only OSM, SUMO, and HH-SIB adapters with the
existing conflation helpers.  It deliberately keeps three different facts
separate:

* raw HH-SIB inventory attributes (including ``klasse``),
* explicit, reviewed jurisdictional/function-category assignments, and
* OSM-to-SUMO lineage.

In particular, raw HH-SIB ``klasse`` is never interpreted as Hamburg HVS
membership.  HVS (or any other official category) must be supplied as a
separate frozen category source plus a reviewed assignment to an HH-SIB link.
The returned ``road_network_evidence`` object is compatible with
``intersection.road_detail`` but never authorizes SUMO mutation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from torii_sumo.road_network.adapters.hamburg_hh_sib import read_hamburg_hh_sib_snapshot
from torii_sumo.road_network.adapters.osm import read_osm_road_snapshot
from torii_sumo.road_network.adapters.sumo import read_sumo_road_snapshot
from torii_sumo.road_network.conflation import (
    build_osm_sumo_lineage_relations,
    generate_official_osm_conflation_candidates,
)
from torii_sumo.road_network.contracts import (
    ROAD_NETWORK_SCHEMA,
    ConflationEvidence,
    RoadObjectRef,
    RoadPropertyAssignment,
    build_conflation_relation,
    project_road_detail_evidence,
)


ROAD_SEMANTIC_BRIDGE_SCHEMA = f"{ROAD_NETWORK_SCHEMA}/semantic-bridge/v1"
CANONICAL_CATEGORY_PROPERTY_NAMES = frozenset(
    {
        "hamburg_membership",
        "network_role",
        "rin_category",
    }
)
_EVIDENCE_STATUS_VALUES = frozenset({"pass", "review_required", "blocked", "not_applicable"})
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


def build_road_semantic_bridge(
    *,
    osm_path: str | Path,
    sumo_path: str | Path,
    hh_sib_snapshot_file: str | Path,
    hh_sib_request_url: str,
    hh_sib_bbox: tuple[float, float, float, float],
    target_time: datetime,
    retrieved_at: datetime,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    osm_expected_sha256: str | None = None,
    sumo_expected_sha256: str | None = None,
    hh_sib_expected_sha256: str | None = None,
    sumo_imported_from: str = "unknown",
    sumo_imported_source_sha256: str | None = None,
    official_osm_search_radius_m: float = 30.0,
    official_osm_overlap_tolerance_m: float = 10.0,
    osm_sumo_overlap_tolerance_m: float = 8.0,
    reviewed_official_osm_selections: Sequence[Mapping[str, Any]] = (),
    reviewed_property_assignments: Sequence[RoadPropertyAssignment | Mapping[str, Any]] = (),
    official_category_sources: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build a classification-only, hash-bound road-semantic bridge report.

    ``reviewed_official_osm_selections`` is intentionally small and references
    a generated candidate set by ``candidate_set_id``.  A selection requires a
    nonempty ``review_decision_id`` so a generated candidate is never silently
    promoted.  The bridge derives the exact relation/evidence from the frozen
    candidate set instead of trusting caller-provided geometry scores.

    Bounded OSM-fragment candidates are intentionally outside that selection
    path.  They remain a separate external-reference-map review queue because
    a locally contained OSM way is not a complete official link and cannot
    project a canonical category through the full-link compatibility view.

    ``reviewed_property_assignments`` may contain ``RoadPropertyAssignment``
    instances or their ``as_dict()`` representation.  Each accepted assignment
    targets an HH-SIB ``road_link_assertion`` in this exact snapshot and must
    cite at least one distinct, declared ``official_category_sources`` hash in
    ``evidence_refs``.  This is the explicit hook for a frozen Hamburg HVS
    dataset (and for RIN or local functional-road sources); HH-SIB's raw
    ``klasse`` never fills these properties.
    """

    _require_aware(target_time, "target_time")
    _require_aware(retrieved_at, "retrieved_at")
    _validate_optional_time(valid_from, "valid_from")
    _validate_optional_time(valid_to, "valid_to")

    # These adapters read exact bytes and create independent reports.  The
    # bridge neither writes to nor mutates the input artifacts or caller maps.
    osm_report = read_osm_road_snapshot(
        osm_path,
        target_time=target_time,
        retrieved_at=retrieved_at,
        valid_from=valid_from,
        valid_to=valid_to,
        expected_sha256=osm_expected_sha256,
    )
    sumo_report = read_sumo_road_snapshot(
        sumo_path,
        target_time=target_time,
        retrieved_at=retrieved_at,
        valid_from=valid_from,
        valid_to=valid_to,
        expected_sha256=sumo_expected_sha256,
        imported_from=sumo_imported_from,
        imported_source_sha256=sumo_imported_source_sha256,
    )
    hh_sib_report = read_hamburg_hh_sib_snapshot(
        Path(hh_sib_snapshot_file),
        request_url=hh_sib_request_url,
        bbox=hh_sib_bbox,
        target_time=target_time,
        retrieved_at=retrieved_at,
        valid_from=valid_from,
        valid_to=valid_to,
        expected_sha256=hh_sib_expected_sha256,
    )

    official_osm_candidates = generate_official_osm_conflation_candidates(
        hh_sib_report,
        osm_report,
        target_time=target_time,
        search_radius_m=official_osm_search_radius_m,
        overlap_tolerance_m=official_osm_overlap_tolerance_m,
    )
    osm_sumo_lineage = build_osm_sumo_lineage_relations(
        osm_report,
        sumo_report,
        target_time=target_time,
        overlap_tolerance_m=osm_sumo_overlap_tolerance_m,
    )

    source_blocking_reasons = _source_blocking_reasons(
        osm_report=osm_report,
        sumo_report=sumo_report,
        hh_sib_report=hh_sib_report,
    )
    hh_sib_sha256 = str(hh_sib_report.get("source_snapshot", {}).get("sha256", ""))
    category_sources, category_source_blockers, category_source_reviews = _normalize_category_sources(
        official_category_sources,
        hh_sib_sha256=hh_sib_sha256,
    )
    accepted_selections, selection_records, selection_blockers, selection_reviews = _reviewed_selection_relations(
        official_osm_candidates,
        reviewed_official_osm_selections,
        target_time=target_time,
    )
    accepted_assignments, assignment_records, assignment_blockers, assignment_reviews = _reviewed_category_assignments(
        reviewed_property_assignments,
        hh_sib_report=hh_sib_report,
        category_sources=category_sources,
    )

    projection = project_road_detail_evidence(accepted_selections, accepted_assignments)
    sumo_by_osm_way_id = _sumo_lineage_by_osm_way_id(osm_sumo_lineage)
    semantic_dimensions = _semantic_dimension_coverage(accepted_assignments)

    blocking_reasons = [
        *source_blocking_reasons,
        *category_source_blockers,
        *selection_blockers,
        *assignment_blockers,
    ]
    review_reasons = [
        *_source_review_reasons(
            osm_report=osm_report,
            sumo_report=sumo_report,
            hh_sib_report=hh_sib_report,
        ),
        *category_source_reviews,
        *selection_reviews,
        *assignment_reviews,
        *_unresolved_candidate_report_reviews(official_osm_candidates),
        *_unselected_candidate_set_reviews(official_osm_candidates, selection_records),
        *_projection_review_reasons(projection),
        *_semantic_dimension_reviews(semantic_dimensions),
    ]
    lineage_status = str(osm_sumo_lineage.get("status", "blocked"))
    if lineage_status == "blocked":
        blocking_reasons.extend(
            f"osm_sumo_lineage:{reason}" for reason in osm_sumo_lineage.get("blocking_reasons", ())
        )
    elif lineage_status != "pass":
        review_reasons.extend(
            f"osm_sumo_lineage:{reason}" for reason in osm_sumo_lineage.get("review_reasons", ())
        )

    bridge_identity = {
        "schema": ROAD_SEMANTIC_BRIDGE_SCHEMA,
        "target_time": target_time.isoformat(),
        "source_sha256s": _source_sha256s(osm_report, sumo_report, hh_sib_report, category_sources),
        "sumo_import": {
            "imported_from": str(sumo_imported_from),
            "imported_source_sha256": sumo_imported_source_sha256,
        },
        "parameters": {
            "official_osm_search_radius_m": float(official_osm_search_radius_m),
            "official_osm_overlap_tolerance_m": float(official_osm_overlap_tolerance_m),
            "osm_sumo_overlap_tolerance_m": float(osm_sumo_overlap_tolerance_m),
        },
        "category_sources": [_category_source_identity(item) for item in category_sources],
        "selection_records": selection_records,
        "canonical_property_assignments": [item.as_dict() for item in accepted_assignments],
    }
    bridge_id = f"road-semantic-bridge-{_stable_digest(bridge_identity)[:20]}"

    road_network_evidence = copy.deepcopy(projection)
    _enrich_projection_with_assignment_evidence(road_network_evidence, accepted_assignments)
    road_network_evidence.update(
        {
            "bridge_id": bridge_id,
            "source_sha256s": bridge_identity["source_sha256s"],
            "sumo_source_sha256": str(sumo_report.get("source_snapshot", {}).get("sha256", "")),
            "sumo_lineage_by_osm_way_id": sumo_by_osm_way_id,
            "classification_only": True,
            "automatic_promotion_gate": "blocked",
            "claim_boundary": (
                "This compatibility projection carries only reviewed official road-category assertions and "
                "OSM-to-SUMO lineage. It does not establish lane-to-lane connections, stop lines, "
                "junction ownership, signal binding, or permission to materialize a SUMO network."
            ),
        }
    )

    if blocking_reasons:
        status = "blocked"
    elif review_reasons:
        status = "review_required"
    else:
        status = "pass"
    return {
        "schema": ROAD_SEMANTIC_BRIDGE_SCHEMA,
        "bridge_id": bridge_id,
        "status": status,
        "target_time": target_time.isoformat(),
        "source_reports": {
            "osm": copy.deepcopy(osm_report),
            "sumo": copy.deepcopy(sumo_report),
            "hh_sib": copy.deepcopy(hh_sib_report),
            "official_category_sources": copy.deepcopy(category_sources),
        },
        "source_sha256s": bridge_identity["source_sha256s"],
        "raw_hh_sib_inventory": {
            "source_sha256": hh_sib_sha256,
            "road_link_count": len(hh_sib_report.get("road_link_assertions", ())),
            "raw_property_assignment_count": len(hh_sib_report.get("property_assignments", ())),
            "raw_property_names": _raw_hh_sib_property_names(hh_sib_report),
            "canonical_category_inference": "disabled",
            "reason": "Raw HH-SIB attributes, including klasse, are retained as inventory evidence only.",
        },
        "official_osm_candidates": official_osm_candidates,
        "bounded_official_osm_segment_candidates": copy.deepcopy(
            official_osm_candidates.get("bounded_segment_candidates", ())
        ),
        "bounded_official_osm_segment_relation_proposals": copy.deepcopy(
            official_osm_candidates.get("bounded_segment_relation_proposals", ())
        ),
        "reviewed_official_osm_selections": selection_records,
        "reviewed_official_osm_relations": [item.as_dict() for item in accepted_selections],
        "canonical_official_property_assignments": [item.as_dict() for item in accepted_assignments],
        "canonical_assignment_records": assignment_records,
        "semantic_dimension_coverage": semantic_dimensions,
        "osm_sumo_lineage": osm_sumo_lineage,
        "sumo_lineage_by_osm_way_id": sumo_by_osm_way_id,
        "road_network_evidence": road_network_evidence,
        "gates": {
            "source_snapshots": "blocked" if source_blocking_reasons else "pass",
            "official_osm_reviewed_selection": _gate_status(selection_blockers, selection_reviews),
            "canonical_official_categories": _gate_status(assignment_blockers, assignment_reviews),
            "road_detail_projection": str(projection.get("status", "review_required")),
            "osm_sumo_lineage": lineage_status,
            "automatic_promotion": "blocked",
        },
        "blocking_reasons": sorted(set(str(item) for item in blocking_reasons if str(item))),
        "review_reasons": sorted(set(str(item) for item in review_reasons if str(item))),
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "The bridge is an evidence and classification adapter. It preserves raw source snapshots, requires "
            "explicit reviewed category assignments, and reports OSM-to-SUMO lineage. It does not infer HVS "
            "from HH-SIB klasse, determine legal movements, reconstruct channelization, bind a signal controller, "
            "or authorize node, lane, connection, or SUMO-network edits."
        ),
    }


def _normalize_category_sources(
    values: Sequence[Mapping[str, Any]],
    *,
    hh_sib_sha256: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Validate frozen external category-source manifests without interpreting them."""

    normalized: list[dict[str, Any]] = []
    blockers: list[str] = []
    reviews: list[str] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            blockers.append(f"official_category_source_not_object:{index}")
            continue
        source_id = str(value.get("source_id", "")).strip()
        source_snapshot = value.get("source_snapshot")
        status = str(value.get("status", "")).strip()
        if not source_id:
            blockers.append(f"official_category_source_id_required:{index}")
            continue
        if source_id in seen_ids:
            blockers.append(f"duplicate_official_category_source_id:{source_id}")
            continue
        seen_ids.add(source_id)
        if not isinstance(source_snapshot, Mapping):
            blockers.append(f"official_category_source_snapshot_required:{source_id}")
            continue
        sha256 = str(source_snapshot.get("sha256", "")).casefold()
        if not _SHA256_RE.fullmatch(sha256):
            blockers.append(f"official_category_source_sha256_required:{source_id}")
            continue
        if sha256 == hh_sib_sha256:
            blockers.append(f"official_category_source_must_be_distinct_from_hh_sib:{source_id}")
            continue
        if sha256 in seen_hashes:
            blockers.append(f"duplicate_official_category_source_sha256:{sha256}")
            continue
        seen_hashes.add(sha256)
        if status not in _EVIDENCE_STATUS_VALUES:
            blockers.append(f"official_category_source_status_invalid:{source_id}:{status}")
            continue
        if status == "blocked":
            blockers.append(f"official_category_source_blocked:{source_id}")
        elif status != "pass":
            reviews.append(f"official_category_source_not_pass:{source_id}:{status}")
        item = copy.deepcopy(dict(value))
        item["source_id"] = source_id
        item["status"] = status
        item["source_snapshot"] = dict(item["source_snapshot"])
        item["source_snapshot"]["sha256"] = sha256
        normalized.append(item)
    return sorted(normalized, key=lambda item: item["source_id"]), blockers, reviews


def _reviewed_selection_relations(
    candidate_report: Mapping[str, Any],
    selections: Sequence[Mapping[str, Any]],
    *,
    target_time: datetime,
) -> tuple[tuple[Any, ...], list[dict[str, Any]], list[str], list[str]]:
    """Resolve reviewed candidate-set choices into pass-only conflation relations."""

    candidate_sets = {
        str(item.get("candidate_set_id", "")): item
        for item in candidate_report.get("candidate_sets", ())
        if isinstance(item, Mapping) and str(item.get("candidate_set_id", ""))
    }
    relations: list[Any] = []
    records: list[dict[str, Any]] = []
    blockers: list[str] = []
    reviews: list[str] = []
    seen_candidate_set_ids: set[str] = set()
    for index, selection in enumerate(selections):
        if not isinstance(selection, Mapping):
            blockers.append(f"reviewed_official_osm_selection_not_object:{index}")
            continue
        candidate_set_id = str(selection.get("candidate_set_id", "")).strip()
        review_decision_id = str(selection.get("review_decision_id", "")).strip()
        if not candidate_set_id:
            blockers.append(f"reviewed_official_osm_selection_candidate_set_id_required:{index}")
            continue
        if candidate_set_id in seen_candidate_set_ids:
            blockers.append(f"duplicate_reviewed_official_osm_selection:{candidate_set_id}")
            continue
        seen_candidate_set_ids.add(candidate_set_id)
        if not review_decision_id:
            blockers.append(f"reviewed_official_osm_selection_decision_id_required:{candidate_set_id}")
            continue
        candidate_set = candidate_sets.get(candidate_set_id)
        if candidate_set is None:
            blockers.append(f"reviewed_official_osm_selection_unknown_candidate_set:{candidate_set_id}")
            continue
        if str(candidate_set.get("decision", "")) != "eligible":
            blockers.append(f"reviewed_official_osm_selection_candidate_not_eligible:{candidate_set_id}")
            continue
        try:
            official_ref = _road_ref_from_mapping(candidate_set["official_ref"])
            osm_refs = tuple(_road_ref_from_mapping(item) for item in candidate_set.get("osm_refs", ()))
            evidence = _conflation_evidence_from_mapping(candidate_set["evidence"])
        except (KeyError, TypeError, ValueError) as exc:
            blockers.append(f"reviewed_official_osm_selection_invalid_candidate_evidence:{candidate_set_id}:{type(exc).__name__}")
            continue
        if not osm_refs:
            blockers.append(f"reviewed_official_osm_selection_empty_osm_set:{candidate_set_id}")
            continue
        relation = build_conflation_relation(
            left_refs=(official_ref,),
            right_refs=osm_refs,
            relation_kind="covers" if len(osm_refs) > 1 else "equivalent",
            direction="both",
            target_time=target_time,
            evidence=evidence,
            reason=(
                f"Explicit reviewed candidate-set selection {review_decision_id}; "
                f"candidate set {candidate_set_id} remains traceable in this bridge."
            ),
        )
        record = {
            "candidate_set_id": candidate_set_id,
            "review_decision_id": review_decision_id,
            "reason": str(selection.get("reason", "")),
            "candidate_review_reasons": sorted(map(str, candidate_set.get("review_reasons", ()))),
            "candidate_property_projection_gate_failures": sorted(
                map(str, candidate_set.get("property_projection_gate_failures", ()))
            ),
            "relation_id": relation.relation_id,
            "status": relation.status,
        }
        records.append(record)
        if relation.status == "pass":
            relations.append(relation)
        elif relation.status == "blocked":
            blockers.extend(f"reviewed_official_osm_relation:{item}" for item in relation.hard_gate_failures)
        else:
            reviews.extend(f"reviewed_official_osm_relation:{item}" for item in relation.review_reasons)
    return tuple(relations), sorted(records, key=lambda item: item["candidate_set_id"]), blockers, reviews


def _reviewed_category_assignments(
    values: Sequence[RoadPropertyAssignment | Mapping[str, Any]],
    *,
    hh_sib_report: Mapping[str, Any],
    category_sources: Sequence[Mapping[str, Any]],
) -> tuple[tuple[RoadPropertyAssignment, ...], list[dict[str, Any]], list[str], list[str]]:
    """Filter only explicit, global canonical-category assignments for projection."""

    official_target_refs = {
        _road_ref_from_mapping(item["source_ref"]).identity_key
        for item in hh_sib_report.get("road_link_assertions", ())
        if isinstance(item, Mapping)
        and isinstance(item.get("source_ref"), Mapping)
        and str(item.get("road_link_identity_status", "pass")) == "pass"
    }
    unresolved_official_target_refs = {
        _road_ref_from_mapping(item["source_ref"]).identity_key
        for item in hh_sib_report.get("road_link_assertions", ())
        if isinstance(item, Mapping)
        and isinstance(item.get("source_ref"), Mapping)
        and str(item.get("road_link_identity_status", "pass")) != "pass"
    }
    category_source_status_by_sha = {
        str(item["source_snapshot"]["sha256"]): str(item["status"])
        for item in category_sources
    }
    accepted: list[RoadPropertyAssignment] = []
    records: list[dict[str, Any]] = []
    blockers: list[str] = []
    reviews: list[str] = []
    seen_ids: set[str] = set()
    for index, value in enumerate(values):
        try:
            assignment = value if isinstance(value, RoadPropertyAssignment) else _assignment_from_mapping(value)
        except (KeyError, TypeError, ValueError) as exc:
            blockers.append(f"canonical_assignment_invalid:{index}:{type(exc).__name__}")
            continue
        record = {
            "assignment_id": assignment.assignment_id,
            "property_name": assignment.property_name,
            "status": assignment.status,
            "projection_status": "excluded",
            "reasons": [],
        }
        records.append(record)
        if assignment.assignment_id in seen_ids:
            record["reasons"].append("duplicate_assignment_id")
            blockers.append(f"duplicate_canonical_assignment_id:{assignment.assignment_id}")
            continue
        seen_ids.add(assignment.assignment_id)
        if assignment.property_name not in CANONICAL_CATEGORY_PROPERTY_NAMES:
            record["reasons"].append("unsupported_canonical_property_name")
            blockers.append(f"unsupported_canonical_property_name:{assignment.assignment_id}:{assignment.property_name}")
            continue
        if assignment.target_ref.identity_key not in official_target_refs:
            if assignment.target_ref.identity_key in unresolved_official_target_refs:
                record["reasons"].append("target_hh_sib_link_identity_unresolved")
                blockers.append(
                    f"canonical_assignment_target_hh_sib_link_identity_unresolved:{assignment.assignment_id}"
                )
            else:
                record["reasons"].append("target_not_current_hh_sib_link")
                blockers.append(f"canonical_assignment_target_not_current_hh_sib_link:{assignment.assignment_id}")
            continue
        if assignment.s_from_m is not None or assignment.s_to_m is not None:
            record["reasons"].append("stationed_assignment_not_projectable_to_whole_osm_way")
            reviews.append(f"stationed_canonical_assignment_not_projected:{assignment.assignment_id}")
            continue
        category_evidence_hashes = {
            ref.source_sha256
            for ref in assignment.evidence_refs
            if ref.source_sha256 in category_source_status_by_sha
        }
        if not category_evidence_hashes:
            record["reasons"].append("declared_distinct_category_source_evidence_required")
            blockers.append(f"canonical_assignment_category_source_evidence_required:{assignment.assignment_id}")
            continue
        non_pass_sources = sorted(
            sha256
            for sha256 in category_evidence_hashes
            if category_source_status_by_sha[sha256] != "pass"
        )
        if non_pass_sources:
            record["reasons"].append("category_source_not_pass")
            reviews.append(f"canonical_assignment_category_source_not_pass:{assignment.assignment_id}")
            continue
        if assignment.status != "pass":
            record["reasons"].append("assignment_not_review_pass")
            reviews.append(f"canonical_assignment_not_pass:{assignment.assignment_id}:{assignment.status}")
            continue
        record["projection_status"] = "accepted"
        record["category_evidence_sha256s"] = sorted(category_evidence_hashes)
        accepted.append(assignment)
    return tuple(sorted(accepted, key=lambda item: item.assignment_id)), records, blockers, reviews


def _sumo_lineage_by_osm_way_id(lineage_report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Project retained lineage to way IDs without pretending it proves lane binding."""

    by_way: dict[str, dict[str, Any]] = {}
    for relation in lineage_report.get("relations", ()):
        if not isinstance(relation, Mapping):
            continue
        osm_way_ids = sorted(
            str(ref.get("object_id", ""))
            for ref in relation.get("left_refs", ())
            if isinstance(ref, Mapping) and ref.get("namespace") == "osm" and ref.get("object_type") == "way"
        )
        sumo_edge_ids = sorted(
            str(ref.get("object_id", ""))
            for ref in relation.get("right_refs", ())
            if isinstance(ref, Mapping) and ref.get("namespace") == "sumo" and ref.get("object_type") == "edge"
        )
        for way_id in osm_way_ids:
            if not way_id:
                continue
            item = by_way.setdefault(
                way_id,
                {
                    "sumo_edge_ids": set(),
                    "source_relation_ids": set(),
                    "relation_statuses": set(),
                    "source_sha256s": set(),
                },
            )
            item["sumo_edge_ids"].update(edge_id for edge_id in sumo_edge_ids if edge_id)
            item["source_relation_ids"].add(str(relation.get("relation_id", "")))
            item["relation_statuses"].add(str(relation.get("status", "")))
            item["source_sha256s"].update(str(value) for value in relation.get("source_sha256s", ()) if value)
    result: dict[str, dict[str, Any]] = {}
    for way_id, item in sorted(by_way.items()):
        statuses = item["relation_statuses"]
        result[way_id] = {
            "sumo_edge_ids": sorted(item["sumo_edge_ids"]),
            "source_relation_ids": sorted(value for value in item["source_relation_ids"] if value),
            "mapping_status": "pass" if statuses == {"pass"} else "review_required",
            "relation_statuses": sorted(value for value in statuses if value),
            "source_sha256s": sorted(item["source_sha256s"]),
            "claim_boundary": "OSM-way to SUMO-edge lineage only; no lane, connection, or TLS binding is implied.",
        }
    return result


def _enrich_projection_with_assignment_evidence(
    projection: dict[str, Any],
    assignments: Sequence[RoadPropertyAssignment],
) -> None:
    """Keep category-source hashes visible at each projected OSM way.

    ``project_road_detail_evidence`` intentionally stays a narrow compatibility
    projection.  The bridge enriches only its own copy, leaving the shared
    contract and its source records untouched.
    """

    assignment_by_id = {item.assignment_id: item for item in assignments}
    by_way_id = projection.get("by_way_id", {})
    if not isinstance(by_way_id, Mapping):
        return
    for value in by_way_id.values():
        if not isinstance(value, dict):
            continue
        source_assignments = [
            assignment_by_id[assignment_id]
            for assignment_id in value.get("source_assignment_ids", ())
            if assignment_id in assignment_by_id
        ]
        evidence_refs = [
            ref.as_dict()
            for assignment in source_assignments
            for ref in assignment.evidence_refs
        ]
        category_hashes = sorted({ref["source_sha256"] for ref in evidence_refs if ref.get("source_sha256")})
        value["category_source_sha256s"] = category_hashes
        value["assignment_evidence_refs"] = sorted(
            evidence_refs,
            key=lambda ref: (
                str(ref.get("namespace", "")),
                str(ref.get("object_type", "")),
                str(ref.get("object_id", "")),
                str(ref.get("source_sha256", "")),
            ),
        )


def _source_blocking_reasons(
    *,
    osm_report: Mapping[str, Any],
    sumo_report: Mapping[str, Any],
    hh_sib_report: Mapping[str, Any],
) -> list[str]:
    reports = (("osm", osm_report), ("sumo", sumo_report), ("hh_sib", hh_sib_report))
    reasons: list[str] = []
    for name, report in reports:
        if str(report.get("status", "")) == "blocked":
            reasons.extend(f"{name}_source:{reason}" for reason in report.get("blocking_reasons", ()))
    return reasons


def _source_review_reasons(
    *,
    osm_report: Mapping[str, Any],
    sumo_report: Mapping[str, Any],
    hh_sib_report: Mapping[str, Any],
) -> list[str]:
    reports = (("osm", osm_report), ("sumo", sumo_report), ("hh_sib", hh_sib_report))
    reasons: list[str] = []
    for name, report in reports:
        if str(report.get("status", "")) == "review_required":
            reasons.extend(f"{name}_source:{reason}" for reason in report.get("review_reasons", ()))
    return reasons


def _unselected_candidate_set_reviews(
    candidate_report: Mapping[str, Any],
    selection_records: Sequence[Mapping[str, Any]],
) -> list[str]:
    selected = {
        str(item.get("candidate_set_id", ""))
        for item in selection_records
        if str(item.get("status", "")) == "pass"
    }
    return [
        f"eligible_official_osm_candidate_set_unreviewed:{item.get('candidate_set_id', '')}"
        for item in candidate_report.get("candidate_sets", ())
        if isinstance(item, Mapping)
        and str(item.get("decision", "")) == "eligible"
        and str(item.get("candidate_set_id", "")) not in selected
    ]


def _unresolved_candidate_report_reviews(candidate_report: Mapping[str, Any]) -> list[str]:
    """Preserve source-conflation issues that no selection can resolve."""

    return [
        f"official_osm_candidates:{reason}"
        for reason in candidate_report.get("review_reasons", ())
        if str(reason) != "unreviewed_group_selection"
    ]


def _projection_review_reasons(projection: Mapping[str, Any]) -> list[str]:
    if str(projection.get("status", "")) == "pass":
        return []
    reasons = [f"road_detail_projection_conflict:{item.get('property_name', '')}" for item in projection.get("conflicts", ())]
    reasons.extend(
        f"road_detail_projection_excluded_relation:{relation_id}"
        for relation_id in projection.get("excluded_relation_ids", ())
    )
    if not reasons:
        reasons.append("road_detail_projection_not_pass")
    return reasons


def _semantic_dimension_coverage(assignments: Sequence[RoadPropertyAssignment]) -> dict[str, Any]:
    values: dict[str, set[Any]] = {name: set() for name in CANONICAL_CATEGORY_PROPERTY_NAMES}
    for assignment in assignments:
        values[assignment.property_name].add(assignment.value)
    return {
        "known_properties": {
            name: sorted(value_set, key=lambda value: str(value))
            for name, value_set in sorted(values.items())
            if value_set
        },
        "missing_properties": sorted(name for name, value_set in values.items() if not value_set),
        "status": "pass" if values["hamburg_membership"] else "review_required",
        "claim_boundary": (
            "Missing dimensions remain unknown. The bridge does not synthesize HVS, network role, or RIN values "
            "from HH-SIB raw fields or OSM tags."
        ),
    }


def _semantic_dimension_reviews(coverage: Mapping[str, Any]) -> list[str]:
    missing = [str(item) for item in coverage.get("missing_properties", ())]
    if not coverage.get("known_properties"):
        return ["no_explicit_canonical_category_assignments"]
    return [f"canonical_category_dimension_missing:{name}" for name in missing]


def _raw_hh_sib_property_names(report: Mapping[str, Any]) -> list[str]:
    names: set[str] = set()
    for assignment in report.get("property_assignments", ()):
        if isinstance(assignment, Mapping) and assignment.get("property_name"):
            names.add(str(assignment["property_name"]))
    return sorted(names)


def _source_sha256s(
    osm_report: Mapping[str, Any],
    sumo_report: Mapping[str, Any],
    hh_sib_report: Mapping[str, Any],
    category_sources: Sequence[Mapping[str, Any]],
) -> list[str]:
    values = {
        str(report.get("source_snapshot", {}).get("sha256", ""))
        for report in (osm_report, sumo_report, hh_sib_report)
    }
    values.update(str(item.get("source_snapshot", {}).get("sha256", "")) for item in category_sources)
    return sorted(value for value in values if value)


def _category_source_identity(value: Mapping[str, Any]) -> dict[str, str]:
    return {
        "source_id": str(value.get("source_id", "")),
        "status": str(value.get("status", "")),
        "sha256": str(value.get("source_snapshot", {}).get("sha256", "")),
    }


def _assignment_from_mapping(value: Mapping[str, Any]) -> RoadPropertyAssignment:
    if not isinstance(value, Mapping):
        raise TypeError("assignment must be an object")
    return RoadPropertyAssignment(
        assignment_id=str(value["assignment_id"]),
        target_ref=_road_ref_from_mapping(value["target_ref"]),
        property_name=str(value["property_name"]),
        classification_scheme=str(value["classification_scheme"]),
        value=value.get("value"),
        direction=str(value["direction"]),  # type: ignore[arg-type]
        evidence_refs=tuple(_road_ref_from_mapping(item) for item in value["evidence_refs"]),
        status=str(value["status"]),  # type: ignore[arg-type]
        s_from_m=value.get("s_from_m"),
        s_to_m=value.get("s_to_m"),
        reason=str(value.get("reason", "")),
    )


def _road_ref_from_mapping(value: Mapping[str, Any]) -> RoadObjectRef:
    if not isinstance(value, Mapping):
        raise TypeError("road object ref must be an object")
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


def _conflation_evidence_from_mapping(value: Mapping[str, Any]) -> ConflationEvidence:
    if not isinstance(value, Mapping):
        raise TypeError("conflation evidence must be an object")
    return ConflationEvidence(
        geometry_overlap_ratio=_optional_float(value.get("geometry_overlap_ratio")),
        lateral_distance_m=_optional_float(value.get("lateral_distance_m")),
        heading_delta_deg=_optional_float(value.get("heading_delta_deg")),
        topology_agreement=_optional_float(value.get("topology_agreement")),
        source_object_id_agreement=_optional_float(value.get("source_object_id_agreement")),
        name_agreement=_optional_float(value.get("name_agreement")),
        road_ref_agreement=_optional_float(value.get("road_ref_agreement")),
        official_road_key_agreement=_optional_float(value.get("official_road_key_agreement")),
        carriageway_agreement=_optional_float(value.get("carriageway_agreement")),
        lane_profile_agreement=_optional_float(value.get("lane_profile_agreement")),
    )


def _optional_datetime(value: Any) -> datetime | None:
    return None if value in (None, "") else datetime.fromisoformat(str(value))


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _gate_status(blocking_reasons: Sequence[str], review_reasons: Sequence[str]) -> str:
    if blocking_reasons:
        return "blocked"
    if review_reasons:
        return "review_required"
    return "pass"


def _validate_optional_time(value: datetime | None, field_name: str) -> None:
    if value is not None:
        _require_aware(value, field_name)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
