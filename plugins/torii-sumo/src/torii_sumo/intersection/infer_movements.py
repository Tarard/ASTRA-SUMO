from __future__ import annotations

from torii_sumo.road_semantics import classify_approach_mode_layer, classify_turn_from_signed_delta

from .geometry import normalize_signed_angle
from .osm_restrictions import extract_turn_restrictions, restriction_for_movement
from .schema import Approach, IntersectionCore, Movement, MovementMatrix, OSMPatch, RoadPairRelationGraph


def core_connection_movements(movements: list[Movement]) -> list[Movement]:
    allowed = [movement for movement in movements if movement.allowed]
    vehicle = [movement for movement in allowed if "passenger" in movement.allowed_modes]
    return vehicle or allowed


def infer_movement_matrix(
    core: IntersectionCore,
    approaches: list[Approach],
    road_pair_graph: RoadPairRelationGraph,
    patch: OSMPatch | None = None,
) -> MovementMatrix:
    by_pair = {
        frozenset((relation.road_a_id, relation.road_b_id)): relation
        for relation in road_pair_graph.relations
    }
    restrictions = []
    restriction_warnings = []
    if patch is not None:
        restrictions, restriction_warnings = extract_turn_restrictions(patch, core)
    restriction_blocked_count = 0
    movements: list[Movement] = []
    for source in approaches:
        for target in approaches:
            if source.approach_id == target.approach_id:
                continue
            relation = by_pair[frozenset((source.approach_id, target.approach_id))]
            allowed_modes = source.allowed_modes & target.allowed_modes
            signed_delta = normalize_signed_angle(target.bearing_from_core - ((source.bearing_from_core + 180) % 360))
            turn = classify_turn_from_signed_delta(signed_delta)
            allowed = _movement_allowed(source, target, allowed_modes, relation.expected_relation, turn)
            direction_evidence = _movement_direction_evidence(source, target, allowed_modes)
            from_lane_indices = _source_lane_indices(source, turn)
            to_lane_indices = _target_lane_indices(target, len(from_lane_indices))
            evidence = [f"road_pair_relation:{relation.relation_id}", "inferred_default", *direction_evidence]
            restriction = restriction_for_movement(restrictions, source, target, turn, approaches)
            if restriction is not None:
                was_allowed = allowed
                allowed = False
                evidence.append(restriction.evidence)
                if was_allowed:
                    restriction_blocked_count += 1
            movements.append(
                Movement(
                    movement_id=f"{source.approach_id}_to_{target.approach_id}",
                    from_approach_id=source.approach_id,
                    to_approach_id=target.approach_id,
                    road_pair_relation_id=relation.relation_id,
                    turn=turn,
                    allowed=allowed,
                    from_lane_indices=from_lane_indices,
                    to_lane_indices=to_lane_indices,
                    allowed_modes=allowed_modes,
                    evidence=evidence,
                    confidence=min(source.incoming_lane_count, target.outgoing_lane_count, 1) * relation.confidence,
                    notes=[],
                )
            )
    legal_count = sum(movement.allowed for movement in movements)
    return MovementMatrix(
        movements=movements,
        legal_movement_count=legal_count,
        forbidden_movement_count=len(movements) - legal_count,
        inferred_movement_count=len(movements),
        restriction_blocked_count=restriction_blocked_count,
        restriction_warnings=restriction_warnings,
    )


def _movement_allowed(source: Approach, target: Approach, modes: set[str], expected_relation: str, turn: str) -> bool:
    if not modes or expected_relation != "should_connect":
        return False
    if turn == "uturn" and not _turn_lanes_allow_uturn(source):
        return False
    source_layer = _mode_layer_for(source)
    target_layer = _mode_layer_for(target)
    if "passenger" in modes:
        return (
            source_layer.is_vehicle_approach
            and target_layer.is_vehicle_approach
            and source.has_incoming_vehicle_flow
            and target.has_outgoing_vehicle_flow
        )
    return source_layer.is_support_only and target_layer.is_support_only


def _movement_direction_evidence(source: Approach, target: Approach, modes: set[str]) -> list[str]:
    if "passenger" not in modes:
        return []
    evidence = []
    if source.direction_evidence:
        evidence.extend(f"source_direction:{item}" for item in source.direction_evidence)
    elif not source.has_incoming_vehicle_flow:
        evidence.append("source_direction:blocked_incoming_vehicle_flow")
    if target.direction_evidence:
        evidence.extend(f"target_direction:{item}" for item in target.direction_evidence)
    elif not target.has_outgoing_vehicle_flow:
        evidence.append("target_direction:blocked_outgoing_vehicle_flow")
    return evidence


def _mode_layer_for(approach: Approach):
    return classify_approach_mode_layer(
        approach.allowed_modes,
        approach.incoming_extra_lane_modes,
        approach.outgoing_extra_lane_modes,
    )


def _turn_lanes_allow_uturn(source: Approach) -> bool:
    return bool(source.turn_lanes_raw and any(_lane_allows_turn(lane, "uturn") for lane in source.turn_lanes_raw.split("|")))


def _source_lane_indices(source: Approach, turn: str) -> list[int]:
    if not source.turn_lanes_raw:
        return list(range(source.incoming_lane_count))
    matching = [
        index
        for index, raw_lane in enumerate(source.turn_lanes_raw.split("|"))
        if _lane_allows_turn(raw_lane, turn)
    ]
    return matching or list(range(source.incoming_lane_count))


def _target_lane_indices(target: Approach, source_lane_count: int) -> list[int]:
    return list(range(min(target.outgoing_lane_count, max(1, source_lane_count))))


def _lane_allows_turn(raw_lane: str, turn: str) -> bool:
    tokens = {token.strip() for token in raw_lane.split(";") if token.strip()}
    if turn == "straight":
        return bool(tokens & {"through", "straight"})
    if turn == "uturn":
        return bool(tokens & {"reverse", "uturn"})
    return turn in tokens
