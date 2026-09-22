from __future__ import annotations

import math

from torii_sumo.road_semantics import classify_approach_mode_layer

from .schema import Approach, ControlModel, IntersectionCore, Movement, MovementMatrix, OSMPatch, TLSPhase
from .infer_movements import core_connection_movements


def infer_control_model(
    patch: OSMPatch,
    core: IntersectionCore,
    approaches: list[Approach],
    movements: MovementMatrix,
) -> ControlModel:
    signal_source = _traffic_signal_source(patch, core)
    has_signal = bool(signal_source)
    if not has_signal:
        return ControlModel(
            control_type="priority",
            source=["inferred:no_traffic_signal_tag"],
            priority_approach_ids=[approach.approach_id for approach in approaches[:1]],
            tls_id=None,
            phases=[],
            link_index_map={},
            confidence=0.6,
        )

    allowed_movements = _controlled_movements(movements.movements, approaches)
    link_index_map = {
        movement.movement_id: index for index, movement in enumerate(allowed_movements)
    }
    approaches_by_id = {approach.approach_id: approach for approach in approaches}
    phases = _conflict_grouped_phases(allowed_movements, link_index_map, approaches_by_id)
    if phases is None:
        phases = _alternating_phases(allowed_movements, link_index_map)
        synthetic_source = "synthetic:alternating_placeholder"
    else:
        synthetic_source = "synthetic:conflict_grouped"

    return ControlModel(
        control_type="traffic_light",
        source=[signal_source, synthetic_source],
        priority_approach_ids=[],
        tls_id=core.core_id,
        phases=phases,
        link_index_map=link_index_map,
        confidence=0.9,
    )


def _alternating_phases(allowed_movements: list[Movement], link_index_map: dict[str, int]) -> list[TLSPhase]:
    phase_a = "".join("G" if index % 2 == 0 else "r" for index in range(len(allowed_movements)))
    phase_b = "".join("r" if char == "G" else "G" for char in phase_a)
    movement_ids = list(link_index_map)
    return [
        TLSPhase(phase_id="p0", duration=30.0, state=phase_a, movement_ids=movement_ids),
        TLSPhase(phase_id="p1", duration=30.0, state=phase_b, movement_ids=movement_ids),
    ]


def _can_group_conflicts(approaches_by_id: dict[str, Approach], movements: list[Movement]) -> bool:
    return all(
        approaches_by_id.get(movement.from_approach_id) is not None
        and approaches_by_id.get(movement.to_approach_id) is not None
        and approaches_by_id[movement.from_approach_id].endpoint_xy is not None
        and approaches_by_id[movement.to_approach_id].endpoint_xy is not None
        for movement in movements
    )


def _compatible(left: Movement, right: Movement, approaches_by_id: dict[str, Approach]) -> bool:
    left_support = _movement_is_support_only(left, approaches_by_id)
    right_support = _movement_is_support_only(right, approaches_by_id)
    if left_support or right_support:
        return (
            left_support
            and right_support
            and left.from_approach_id != right.from_approach_id
            and left.to_approach_id != right.to_approach_id
        )
    if left.turn in {"left", "uturn"} or right.turn in {"left", "uturn"}:
        return False
    if left.from_approach_id == right.from_approach_id or left.to_approach_id == right.to_approach_id:
        return False
    return _approaches_are_opposite(
        approaches_by_id[left.from_approach_id],
        approaches_by_id[right.from_approach_id],
    )


def _movement_is_support_only(movement: Movement, approaches_by_id: dict[str, Approach]) -> bool:
    source = approaches_by_id.get(movement.from_approach_id)
    target = approaches_by_id.get(movement.to_approach_id)
    return source is not None and target is not None and _is_support_only(source) and _is_support_only(target)


def _approaches_are_opposite(first: Approach, second: Approach) -> bool:
    delta = abs(((first.bearing_from_core - second.bearing_from_core + 180) % 360) - 180)
    return delta >= 135


def _conflict_grouped_phases(
    allowed_movements: list[Movement],
    link_index_map: dict[str, int],
    approaches_by_id: dict[str, Approach],
) -> list[TLSPhase] | None:
    if not allowed_movements or not _can_group_conflicts(approaches_by_id, allowed_movements):
        return None
    groups: list[list[Movement]] = []
    for movement in allowed_movements:
        placed = False
        for group in groups:
            if all(_compatible(movement, other, approaches_by_id) for other in group):
                group.append(movement)
                placed = True
                break
        if not placed:
            groups.append([movement])
    phases: list[TLSPhase] = []
    for index, group in enumerate(groups):
        state = ["r"] * len(allowed_movements)
        for movement in group:
            state[link_index_map[movement.movement_id]] = "G"
        phases.append(
            TLSPhase(
                phase_id=f"p{index}",
                duration=30.0,
                state="".join(state),
                movement_ids=[movement.movement_id for movement in group],
            )
        )
    return phases


def _controlled_movements(movements: list[Movement], approaches: list[Approach]) -> list[Movement]:
    vehicle_movements = core_connection_movements(movements)
    vehicle_ids = {movement.movement_id for movement in vehicle_movements}
    approaches_by_id = {approach.approach_id: approach for approach in approaches}
    seen_same_way_pairs: set[tuple[str, str]] = set()
    seen_mixed_feeder_targets: set[tuple[str, tuple[str, ...]]] = set()
    support_movements = [
        movement
        for movement in movements
        if movement.allowed
        and movement.movement_id not in vehicle_ids
        and "bicycle" in movement.allowed_modes
        and "passenger" not in movement.allowed_modes
        and _is_support_only(approaches_by_id.get(movement.from_approach_id))
        and _is_support_only(approaches_by_id.get(movement.to_approach_id))
        and _keep_support_direction_once(movement, approaches_by_id, seen_same_way_pairs)
        and _keep_mixed_feeder_to_corridor_once(movement, approaches_by_id, seen_mixed_feeder_targets)
    ]
    return [*vehicle_movements, *support_movements]


def _is_support_only(approach: Approach | None) -> bool:
    return approach is not None and _mode_layer_for(approach).is_support_only


def _mode_layer_for(approach: Approach):
    return classify_approach_mode_layer(
        approach.allowed_modes,
        approach.incoming_extra_lane_modes,
        approach.outgoing_extra_lane_modes,
    )


def _keep_support_direction_once(
    movement: Movement,
    approaches_by_id: dict[str, Approach],
    seen_same_way_pairs: set[tuple[str, str]],
) -> bool:
    source = approaches_by_id[movement.from_approach_id]
    target = approaches_by_id[movement.to_approach_id]
    if set(source.source_way_ids) != set(target.source_way_ids):
        return True
    pair = tuple(sorted((source.approach_id, target.approach_id)))
    if pair in seen_same_way_pairs:
        return False
    seen_same_way_pairs.add(pair)
    return True


def _keep_mixed_feeder_to_corridor_once(
    movement: Movement,
    approaches_by_id: dict[str, Approach],
    seen_feeder_targets: set[tuple[str, tuple[str, ...]]],
) -> bool:
    source = approaches_by_id[movement.from_approach_id]
    target = approaches_by_id[movement.to_approach_id]
    if not ({"bicycle", "pedestrian"} <= source.allowed_modes and "passenger" not in source.allowed_modes):
        return True
    if set(source.source_way_ids) == set(target.source_way_ids):
        return True
    target_way_key = tuple(sorted(target.source_way_ids))
    pair = (source.approach_id, target_way_key)
    if pair in seen_feeder_targets:
        return False
    seen_feeder_targets.add(pair)
    return True


def _traffic_signal_source(patch: OSMPatch, core: IntersectionCore) -> str:
    if any(
        patch.nodes[node_id].tags.get("highway") == "traffic_signals"
        for node_id in core.core_osm_node_ids
        if node_id in patch.nodes
    ):
        return "osm:highway=traffic_signals"

    center_x, center_y = core.center_xy
    for node in patch.nodes.values():
        if node.tags.get("highway") != "traffic_signals" or node.x is None or node.y is None:
            continue
        if math.hypot(node.x - center_x, node.y - center_y) <= core.core_radius_m:
            return "osm:nearby_highway=traffic_signals"
    return ""
