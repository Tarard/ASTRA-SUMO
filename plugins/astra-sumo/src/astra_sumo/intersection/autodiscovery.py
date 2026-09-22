from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .hypothesis import build_teacher_free_intersection_hypothesis
from .osm_patch import parse_osm_xml
from .pedestrian_facility import build_osm_pedestrian_facility_audits
from .physical_cell import (
    build_osm_vehicle_graph,
    infer_signal_anchor_physical_cell,
    shortest_paths,
)
from .schema import OSMPatch


def discover_teacher_free_intersections(
    osm_file: Path,
    *,
    traffic_side: str,
) -> dict[str, Any]:
    """Discover and canonicalize signal cells from one frozen OSM bbox."""

    source = osm_file.resolve(strict=True)
    patch = parse_osm_xml(source)
    source_sha256 = _file_sha256(source)
    patch_report = discover_teacher_free_intersections_from_patch(
        patch,
        traffic_side=traffic_side,
        source_sha256=source_sha256,
    )
    source_record = {
        "path": source.name,
        "sha256": source_sha256,
    }
    payload = {
        **{key: value for key, value in patch_report.items() if key != "discovery_id"},
        "patch_discovery_id": patch_report["discovery_id"],
        "source_osm": source_record,
    }
    return {
        **payload,
        "discovery_id": f"bbox-discovery-{_stable_digest(payload)[:20]}",
    }


def discover_teacher_free_intersections_from_patch(
    patch: OSMPatch,
    *,
    traffic_side: str,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Discover signal cells without seed, teacher, scope, or expected answers."""

    anchor_ids = sorted(node.id for node in patch.nodes.values() if _is_signal_anchor(node.tags))
    observations = []
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for anchor_id in anchor_ids:
        cell = infer_signal_anchor_physical_cell(
            patch,
            seed_node_id=anchor_id,
        )
        anchor_signature = tuple(cell["signal_anchor_node_ids"])
        observation = {
            "seed_anchor_node_id": anchor_id,
            "anchor_signature": list(anchor_signature),
            "physical_cell_hypothesis_id": cell["hypothesis_id"],
            "proposed_source_junction_ids": cell["proposed_source_junction_ids"],
            "physical_approach_count": len(cell["physical_approaches"]),
            "risks": cell["risks"],
        }
        observations.append(observation)
        groups[anchor_signature].append(observation)

    group_ids = {signature: f"anchor-group-{_stable_digest(list(signature))[:16]}" for signature in groups}
    overlapping_groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
    signatures = sorted(groups)
    for index, first in enumerate(signatures):
        for second in signatures[index + 1 :]:
            if set(first) & set(second):
                overlapping_groups[first].append(group_ids[second])
                overlapping_groups[second].append(group_ids[first])

    graph, _, _ = build_osm_vehicle_graph(patch)
    candidates = []
    for signature in signatures:
        group_observations = groups[signature]
        member_signatures = {tuple(item["proposed_source_junction_ids"]) for item in group_observations}
        member_candidates = sorted({node_id for members in member_signatures for node_id in members})
        seed_selection = _select_vehicle_graph_medoid(
            graph,
            anchor_ids=signature,
            candidate_node_ids=member_candidates,
        )
        canonical_seed = seed_selection["selected_node_id"]
        canonical_node = patch.nodes[canonical_seed]
        hypothesis = build_teacher_free_intersection_hypothesis(
            patch,
            seed_node_id=canonical_seed,
            traffic_side=traffic_side,
            seed_authority="machine_selected_vehicle_graph_medoid",
        )
        canonical_cell = hypothesis["physical_cell"]
        blockers = []
        if len(member_signatures) != 1:
            blockers.append("anchor_seeds_disagree_on_physical_cell_membership")
        if overlapping_groups.get(signature):
            blockers.append("anchor_group_overlaps_nonidentical_group")
        if seed_selection["status"] != "pass":
            blockers.append("canonical_vehicle_graph_medoid_unavailable")
        if tuple(canonical_cell["signal_anchor_node_ids"]) != signature:
            blockers.append("canonical_seed_changes_anchor_group_signature")
        classification = _classify_signal_cell(
            patch,
            anchor_ids=signature,
            physical_cell=canonical_cell,
        )
        pedestrian_facility_audits = build_osm_pedestrian_facility_audits(
            patch,
            crossing_node_ids=signature,
            traffic_side=traffic_side,
            source_sha256=source_sha256,
        )
        if hypothesis["generation_status"] != "pass":
            blockers.append("teacher_free_hypothesis_generation_blocked")
        if hypothesis["unresolved_reasons"]:
            blockers.append("teacher_free_hypothesis_has_unresolved_semantics")
        disposition = (
            "suggest"
            if classification["kind"] == "vehicle_intersection"
            and classification["status"] == "suggest"
            and not blockers
            else "review"
        )
        group_payload = {
            "group_id": group_ids[signature],
            "anchor_node_ids": list(signature),
            "anchor_observation_seed_ids": sorted(item["seed_anchor_node_id"] for item in group_observations),
            "overlapping_group_ids": sorted(overlapping_groups.get(signature, [])),
            "membership_variant_count": len(member_signatures),
            "membership_variants": [list(item) for item in sorted(member_signatures)],
            "canonical_seed_selection": seed_selection,
            "canonical_location": {
                "node_id": canonical_seed,
                "lat": canonical_node.lat,
                "lon": canonical_node.lon,
                "local_x_m": canonical_node.x,
                "local_y_m": canonical_node.y,
            },
            "classification": classification,
            "pedestrian_facility_audits": pedestrian_facility_audits,
            "pedestrian_facility_audit_status": _aggregate_pedestrian_audit_status(pedestrian_facility_audits),
            "disposition": disposition,
            "automatic_promotion_gate": "blocked",
            "discovery_blockers": sorted(set(blockers)),
            "hypothesis": hypothesis,
        }
        candidate_identity = {
            "group_id": group_payload["group_id"],
            "anchor_node_ids": group_payload["anchor_node_ids"],
            "proposed_source_junction_ids": canonical_cell["proposed_source_junction_ids"],
            "classification_kind": classification["kind"],
        }
        candidates.append(
            {
                **group_payload,
                "candidate_identity_basis": candidate_identity,
                "candidate_id": (f"discovered-cell-{_stable_digest(candidate_identity)[:20]}"),
            }
        )

    payload = {
        "schema": "torii.teacher-free-bbox-discovery/v1",
        "generation_status": "pass",
        "automatic_promotion_gate": "blocked",
        "traffic_side": traffic_side,
        "bbox": patch.bbox.model_dump(),
        "signal_anchor_count": len(anchor_ids),
        "signal_anchor_node_ids": anchor_ids,
        "anchor_observation_count": len(observations),
        "anchor_observations": sorted(
            observations,
            key=lambda item: item["seed_anchor_node_id"],
        ),
        "candidate_count": len(candidates),
        "candidates": sorted(candidates, key=lambda item: item["group_id"]),
        "vehicle_intersection_candidate_count": sum(
            item["classification"]["kind"] == "vehicle_intersection" for item in candidates
        ),
        "review_ready_vehicle_candidate_count": sum(
            item["classification"]["kind"] == "vehicle_intersection" and item["disposition"] == "suggest"
            for item in candidates
        ),
        "pedestrian_facility_candidate_count": sum(
            item["classification"]["kind"] == "pedestrian_crossing_facility" for item in candidates
        ),
        "pedestrian_facility_audit_count": sum(len(item["pedestrian_facility_audits"]) for item in candidates),
        "pedestrian_facility_audit_ready_count": sum(
            audit["audit_status"] == "review_ready"
            for item in candidates
            for audit in item["pedestrian_facility_audits"]
        ),
        "pedestrian_facility_audit_blocked_count": sum(
            audit["audit_status"] == "blocked" for item in candidates for audit in item["pedestrian_facility_audits"]
        ),
        "forbidden_generation_inputs": [
            "teacher_network",
            "reviewed_scope",
            "expected_topology",
            "expected_approach_count",
            "expected_movement_count",
            "materialized_candidate_network",
        ],
        "claim_boundary": (
            "This artifact discovers OSM signal cells and proposes canonical "
            "teacher-free hypotheses. Exact anchor-set grouping is deliberately "
            "conservative: overlapping nonidentical groups remain review items. "
            "No candidate is automatically selected or materialized."
        ),
    }
    return {
        **payload,
        "discovery_id": f"bbox-discovery-{_stable_digest(payload)[:20]}",
    }


def _select_vehicle_graph_medoid(
    graph: dict[str, list[tuple[str, float, str]]],
    *,
    anchor_ids: tuple[str, ...],
    candidate_node_ids: list[str],
) -> dict[str, Any]:
    scores = []
    for node_id in sorted(candidate_node_ids):
        distances, _ = shortest_paths(graph, node_id)
        values = [distances.get(anchor_id, math.inf) for anchor_id in anchor_ids]
        if not values or any(not math.isfinite(value) for value in values):
            continue
        scores.append(
            {
                "node_id": node_id,
                "maximum_anchor_graph_distance_m": round(max(values), 6),
                "total_anchor_graph_distance_m": round(sum(values), 6),
            }
        )
    scores.sort(
        key=lambda item: (
            item["maximum_anchor_graph_distance_m"],
            item["total_anchor_graph_distance_m"],
            item["node_id"],
        )
    )
    if scores:
        selected = scores[0]["node_id"]
        status = "pass"
        reason = "minimum_maximum_then_total_anchor_graph_distance"
    else:
        selected = min(anchor_ids) if anchor_ids else min(candidate_node_ids)
        status = "review_required"
        reason = "no_candidate_reaches_all_anchors_on_vehicle_graph"
    return {
        "status": status,
        "method": "vehicle_graph_one_center_medoid",
        "reason": reason,
        "selected_node_id": selected,
        "candidate_scores": scores,
    }


def _classify_signal_cell(
    patch: OSMPatch,
    *,
    anchor_ids: tuple[str, ...],
    physical_cell: dict[str, Any],
) -> dict[str, Any]:
    approach_count = len(physical_cell["physical_approaches"])
    has_vehicle_signal = any(
        patch.nodes[anchor_id].tags.get("highway") == "traffic_signals" for anchor_id in anchor_ids
    )
    has_crossing_signal = any(
        patch.nodes[anchor_id].tags.get("crossing") == "traffic_signals" for anchor_id in anchor_ids
    )
    if approach_count in {3, 4} and has_vehicle_signal:
        kind = "vehicle_intersection"
        status = "suggest" if not physical_cell["risks"] else "review"
        reason = "standard_vehicle_approach_count_with_signal_anchor"
    elif has_crossing_signal:
        kind = "pedestrian_crossing_facility"
        status = "review"
        reason = "signalized_crossing_requires_facility_and_row_audit"
    else:
        kind = "unsupported_or_nonstandard_signal_cell"
        status = "review"
        reason = "outside_standard_vehicle_or_pedestrian_discovery_domain"
    return {
        "kind": kind,
        "status": status,
        "reason": reason,
        "physical_approach_count": approach_count,
        "has_vehicle_signal_anchor": has_vehicle_signal,
        "has_signalized_crossing_anchor": has_crossing_signal,
        "physical_cell_risks": physical_cell["risks"],
    }


def _is_signal_anchor(tags: dict[str, str]) -> bool:
    return bool(tags.get("highway") == "traffic_signals" or tags.get("crossing") == "traffic_signals")


def _aggregate_pedestrian_audit_status(audits: list[dict[str, Any]]) -> str:
    if not audits:
        return "not_applicable"
    if any(audit["audit_status"] == "blocked" for audit in audits):
        return "blocked"
    return "review_ready"


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
