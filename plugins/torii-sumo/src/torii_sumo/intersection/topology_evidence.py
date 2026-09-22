from __future__ import annotations

import hashlib
import json
from itertools import combinations
from typing import Any, Mapping

from .physical_cell import build_osm_vehicle_graph, shortest_paths
from .schema import OSMPatch


_STORAGE_CAPABLE_CONNECTOR_MIN_M = 12.0
_GRADE_SEPARATION_TAGS = {"bridge", "tunnel", "layer", "level"}
_SUPPORT_HIGHWAYS = {"footway", "cycleway", "pedestrian", "path", "steps"}


def build_protected_topology_features(
    patch: OSMPatch,
    *,
    path_node_ids: set[str],
) -> dict[str, Any]:
    """Extract mode, grade, and access evidence over an explicit node closure."""

    normalized_path_node_ids = set(map(str, path_node_ids))
    incident_way_ids = sorted(
        way.id for way in patch.ways.values() if normalized_path_node_ids.intersection(map(str, way.node_refs))
    )
    _, vehicle_way_ids, _ = build_osm_vehicle_graph(patch)
    return _protected_features(
        patch,
        incident_way_ids=incident_way_ids,
        path_node_ids=normalized_path_node_ids,
        vehicle_way_ids=vehicle_way_ids,
    )


def build_topology_evidence(
    patch: OSMPatch,
    physical_cell: Mapping[str, Any],
) -> dict[str, Any]:
    """Build topology evidence independently from controller membership.

    The evidence graph uses only the frozen OSM patch and the machine-derived
    physical-cell closure.  It does not inspect a candidate network and cannot
    authorize a topology choice.
    """

    graph, vehicle_way_ids, _ = build_osm_vehicle_graph(patch)
    path_node_ids = set(map(str, physical_cell.get("path_closure_node_ids", ())))
    source_junction_ids = set(map(str, physical_cell.get("proposed_source_junction_ids", ())))
    signal_anchor_ids = set(map(str, physical_cell.get("signal_anchor_node_ids", ())))

    vehicle_degrees = {
        node_id: len({str(neighbor) for neighbor, _, _ in graph.get(node_id, ())}) for node_id in sorted(path_node_ids)
    }
    branch_node_ids = sorted(
        node_id for node_id, degree in vehicle_degrees.items() if degree >= 3 and node_id in source_junction_ids
    )
    inline_signal_anchor_ids = sorted(node_id for node_id in signal_anchor_ids if vehicle_degrees.get(node_id, 0) <= 2)
    branch_signal_anchor_ids = sorted(signal_anchor_ids & set(branch_node_ids))

    branch_connectors = []
    for first_id, second_id in combinations(branch_node_ids, 2):
        distances, _ = shortest_paths(graph, first_id)
        distance_m = distances.get(second_id)
        if distance_m is None:
            continue
        branch_connectors.append(
            {
                "from_branch_node_id": first_id,
                "to_branch_node_id": second_id,
                "graph_distance_m": round(float(distance_m), 3),
                "storage_capable": (float(distance_m) >= _STORAGE_CAPABLE_CONNECTOR_MIN_M),
            }
        )

    unique_conflict_center_id = branch_node_ids[0] if len(branch_node_ids) == 1 else None
    anchor_distances = []
    if unique_conflict_center_id is not None:
        distances, _ = shortest_paths(graph, unique_conflict_center_id)
        anchor_distances = [
            {
                "signal_anchor_node_id": anchor_id,
                "graph_distance_to_conflict_center_m": round(
                    float(distances[anchor_id]),
                    3,
                ),
                "vehicle_degree": vehicle_degrees.get(anchor_id, 0),
            }
            for anchor_id in sorted(signal_anchor_ids)
            if anchor_id in distances
        ]

    incident_way_ids = sorted(way.id for way in patch.ways.values() if path_node_ids.intersection(way.node_refs))
    protected_features = _protected_features(
        patch,
        incident_way_ids=incident_way_ids,
        path_node_ids=path_node_ids,
        vehicle_way_ids=vehicle_way_ids,
    )
    storage_connectors = [item for item in branch_connectors if item["storage_capable"]]
    if len(branch_node_ids) == 1:
        morphology = "single_conflict_center"
    elif len(branch_node_ids) > 1:
        morphology = "paired_or_offset_conflict_centers"
    else:
        morphology = "no_vehicle_branch_conflict_center"

    payload = {
        "schema": "torii.teacher-free-topology-evidence/v1",
        "source": "frozen_osm_only",
        "physical_cell_hypothesis_id": physical_cell.get("hypothesis_id"),
        "automatic_topology_selection": False,
        "automatic_promotion_gate": "blocked",
        "morphology": morphology,
        "source_junction_ids": sorted(source_junction_ids),
        "path_closure_node_ids": sorted(path_node_ids),
        "signal_anchor_node_ids": sorted(signal_anchor_ids),
        "signal_anchor_count": len(signal_anchor_ids),
        "inline_signal_anchor_ids": inline_signal_anchor_ids,
        "branch_signal_anchor_ids": branch_signal_anchor_ids,
        "vehicle_degree_by_node": vehicle_degrees,
        "branch_node_ids": branch_node_ids,
        "branch_node_count": len(branch_node_ids),
        "unique_conflict_center_node_id": unique_conflict_center_id,
        "branch_connectors": branch_connectors,
        "storage_capable_connector_min_m": _STORAGE_CAPABLE_CONNECTOR_MIN_M,
        "storage_capable_connectors": storage_connectors,
        "signal_anchor_distances": anchor_distances,
        "incident_way_ids": incident_way_ids,
        "protected_features": protected_features,
        "claim_boundary": (
            "Vehicle-graph branching, connector length, and protected OSM tags "
            "are topology evidence. They are not controller truth, stop-line "
            "truth, or automatic merge authorization."
        ),
    }
    return {
        **payload,
        "topology_evidence_id": f"topology-evidence-{_stable_digest(payload)[:20]}",
    }


def assess_topology_hypothesis(
    topology_evidence: Mapping[str, Any],
    topology_hypothesis: str,
) -> dict[str, Any]:
    """Return falsifiers and support without selecting a topology."""

    branch_count = int(topology_evidence.get("branch_node_count", 0))
    signal_count = int(topology_evidence.get("signal_anchor_count", 0))
    storage = list(topology_evidence.get("storage_capable_connectors", ()))
    protected = topology_evidence.get("protected_features", {})
    rail = list(protected.get("rail_way_ids", ()))
    grade = list(protected.get("grade_separation_way_ids", ()))
    blockers: list[str] = []
    supporting: list[str] = []
    review: list[str] = []

    if topology_hypothesis == "preserve_split_shared_controller":
        if signal_count < 2:
            blockers.append("shared_controller_requires_multiple_signal_owners")
        if branch_count > 1:
            supporting.append("multiple_vehicle_conflict_centers_support_split")
        if storage:
            supporting.append("storage_capable_intersection_connector_supports_split")
        if branch_count <= 1 and not storage:
            review.append("split_has_no_independent_conflict_cell_or_storage_evidence")
    elif topology_hypothesis == "merge_physical_cell":
        if branch_count != 1:
            blockers.append("merge_requires_exactly_one_vehicle_conflict_center")
        if storage:
            blockers.append("storage_capable_connector_falsifies_single_cell_merge")
        if rail:
            blockers.append("rail_evidence_blocks_vehicle_only_merge")
        if grade:
            blockers.append("grade_separation_evidence_blocks_planar_merge")
        if branch_count == 1:
            supporting.append("unique_vehicle_conflict_center_supports_merge_hypothesis")
        if signal_count > 1 and len(topology_evidence.get("inline_signal_anchor_ids", ())) == signal_count:
            supporting.append("all_signal_anchors_are_inline_degree_two_candidates")
    elif topology_hypothesis == "partial_internal_repair":
        if branch_count != 1:
            blockers.append("partial_repair_requires_unique_conflict_center")
        if signal_count < 2:
            blockers.append("partial_repair_requires_multiple_inline_signal_artifacts")
        if rail:
            blockers.append("rail_evidence_outside_vehicle_only_partial_repair")
        if grade:
            blockers.append("grade_separation_outside_planar_partial_repair")
        if branch_count == 1:
            supporting.append("unique_conflict_center_can_receive_tls_ownership")
        if signal_count > 1:
            supporting.append("multiple_signal_heads_can_be_demoted_without_node_removal")
    else:
        raise ValueError(f"Unknown topology hypothesis: {topology_hypothesis}")

    protected_review = []
    if protected.get("pedestrian_or_bicycle_way_ids"):
        protected_review.append("multimodal_geometry_and_control_closure_unverified")
    if protected.get("access_way_ids"):
        protected_review.append("interior_access_intent_requires_review")

    if blockers:
        status = "blocked"
    elif supporting:
        status = "supported_for_falsification"
    else:
        status = "review_only"
    return {
        "topology_hypothesis": topology_hypothesis,
        "status": status,
        "materialization_blockers": sorted(set(blockers)),
        "supporting_evidence": sorted(set(supporting)),
        "review_requirements": sorted(set(review)),
        "protected_review_dimensions": sorted(set(protected_review)),
        "selection_authority": "none",
        "automatic_topology_selection": False,
        "automatic_promotion_gate": "blocked",
    }


def _protected_features(
    patch: OSMPatch,
    *,
    incident_way_ids: list[str],
    path_node_ids: set[str],
    vehicle_way_ids: set[str],
) -> dict[str, Any]:
    rail_way_ids: list[str] = []
    grade_way_ids: list[str] = []
    pedestrian_way_ids: list[str] = []
    bicycle_way_ids: list[str] = []
    support_way_ids: list[str] = []
    access_way_ids: list[str] = []
    for way_id in incident_way_ids:
        way = patch.ways[way_id]
        tags = way.tags
        if tags.get("railway") not in (None, "", "abandoned", "disused"):
            rail_way_ids.append(way_id)
        if any(key in tags and tags.get(key) not in (None, "", "no", "0", "false") for key in _GRADE_SEPARATION_TAGS):
            grade_way_ids.append(way_id)
        highway = tags.get("highway")
        if highway in _SUPPORT_HIGHWAYS:
            support_way_ids.append(way_id)
            if (highway in {"footway", "pedestrian", "steps"} and tags.get("foot") != "no") or tags.get("foot") in {
                "yes",
                "designated",
                "permissive",
            }:
                pedestrian_way_ids.append(way_id)
            if (highway == "cycleway" and tags.get("bicycle") != "no") or tags.get("bicycle") in {
                "yes",
                "designated",
                "permissive",
            }:
                bicycle_way_ids.append(way_id)
        if (way_id not in vehicle_way_ids and tags.get("highway") in {"service", "living_street"}) or tags.get(
            "service"
        ) in {"driveway", "parking_aisle"}:
            access_way_ids.append(way_id)

    crossing_candidate_ids = sorted(
        node_id
        for node_id in path_node_ids
        if node_id in patch.nodes
        and (
            patch.nodes[node_id].tags.get("highway") == "crossing"
            or patch.nodes[node_id].tags.get("crossing") not in {None, "", "no", "none"}
        )
    )
    crossing_node_ids = sorted(
        node_id for node_id in crossing_candidate_ids if patch.nodes[node_id].tags.get("foot") != "no"
    )
    bicycle_crossing_node_ids = sorted(
        node_id
        for node_id in crossing_candidate_ids
        if patch.nodes[node_id].tags.get("bicycle") in {"yes", "designated", "permissive"}
    )
    return {
        "rail_way_ids": sorted(set(rail_way_ids)),
        "grade_separation_way_ids": sorted(set(grade_way_ids)),
        "pedestrian_way_ids": sorted(set(pedestrian_way_ids)),
        "bicycle_way_ids": sorted(set(bicycle_way_ids)),
        "pedestrian_or_bicycle_way_ids": sorted(set(support_way_ids)),
        "access_way_ids": sorted(set(access_way_ids)),
        "crossing_node_ids": crossing_node_ids,
        "bicycle_crossing_node_ids": bicycle_crossing_node_ids,
    }


def _stable_digest(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
