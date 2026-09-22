from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
import re
from typing import Sequence

from .digital_twin_mapping import (
    LaneConnectionEvidence,
    MapLaneBinding,
    build_local_lane_graph,
    find_local_lane_paths,
)
from .hamburg_teacher_cell import HamburgOfficialMovementPath
from .ocit_c import OcitVehicleTopologyMovement


class HamburgMovementPathError(ValueError):
    """Raised when an official movement cannot be resolved without guessing."""


# These nine rows are the previously reviewed Sandtorkai MAP connection
# repairs, represented only as path-connectivity evidence.  In particular they
# contain no TLS id, linkIndex, or instruction about which physical arc should
# be controlled.
HAMBURG_SANDTORKAI_CONNECTION_EVIDENCE: tuple[LaneConnectionEvidence, ...] = (
    LaneConnectionEvidence(
        "22649708#1_0",
        "74547371#0_0",
        "hamburg-map:0228:41->38",
        "official MAP movement 41->38",
    ),
    LaneConnectionEvidence(
        "1231234769#1_0",
        "31274978_1",
        "hamburg-map:0228:9->7",
        "official MAP movement 9->7",
    ),
    LaneConnectionEvidence(
        "24732668#1_0",
        "158068424_0",
        "hamburg-map:0228:17->24",
        "official MAP movement 17->24 to Niederbaumbruecke",
    ),
    LaneConnectionEvidence(
        "30390250#2_1",
        "9718800_0",
        "hamburg-map:2421:3->11",
        "official MAP movement 3->11",
    ),
    LaneConnectionEvidence(
        "-647842957_0",
        "-9718800_1",
        "hamburg-map:2421:10->6",
        "official MAP movement 10->6",
    ),
    LaneConnectionEvidence(
        "381540198#2_0",
        "193847534#0_1",
        "hamburg-map:2394:6->5",
        "official MAP movement 6->5",
    ),
    LaneConnectionEvidence(
        "60578519_1",
        "193847534#0_0",
        "hamburg-map:2394:11->4",
        "official MAP movement 11->4",
    ),
    LaneConnectionEvidence(
        "60578519_2",
        "193847534#0_1",
        "hamburg-map:2394:12->5",
        "official MAP movement 12->5",
    ),
    LaneConnectionEvidence(
        "381540198#2_0",
        "-9702435_0",
        "hamburg-map:2394:7->14",
        "official MAP movement 7->14",
    ),
)


def derive_hamburg_official_movement_paths(
    *,
    candidate_net_file: Path,
    official_movements: Sequence[OcitVehicleTopologyMovement],
    lane_bindings: Sequence[MapLaneBinding],
    connection_evidence: Sequence[LaneConnectionEvidence] = (),
    max_path_hops: int = 6,
    max_path_span_m: float = 150.0,
    max_candidate_paths: int = 64,
) -> tuple[HamburgOfficialMovementPath, ...]:
    """Resolve every official OCIT-C movement to one bounded SUMO lane path.

    This adapter intentionally stops at connectivity.  It does not inspect,
    select, copy, or assign a controlled TLS arc anywhere along the path.
    Ambiguous paths, missing bindings, search overflow, and duplicate official
    movement ids all fail closed.
    """

    if max_path_hops <= 0 or max_candidate_paths <= 0:
        raise HamburgMovementPathError(
            "movement path hop and candidate limits must be positive"
        )
    if not math.isfinite(max_path_span_m) or max_path_span_m < 0:
        raise HamburgMovementPathError(
            "movement path span must be finite and non-negative"
        )
    movements = tuple(
        sorted(
            official_movements,
            key=lambda movement: (
                _normalize_node(movement.node_id),
                _natural_key(movement.connection_id),
                _natural_key(movement.ingress_lane_id),
                _natural_key(movement.egress_lane_id),
            ),
        )
    )
    if not movements:
        raise HamburgMovementPathError("at least one official movement is required")
    duplicate_movement_ids = sorted(
        (
            f"{node_id}/{connection_id}"
            for (node_id, connection_id), count in Counter(
                (_normalize_node(movement.node_id), movement.connection_id)
                for movement in movements
            ).items()
            if count > 1
        ),
        key=_natural_key,
    )
    if duplicate_movement_ids:
        raise HamburgMovementPathError(
            "official topology contains duplicate movement ids: "
            f"{duplicate_movement_ids}"
        )

    binding_index: dict[tuple[str, str, str], list[MapLaneBinding]] = {}
    for binding in lane_bindings:
        binding_index.setdefault(
            (
                _normalize_node(binding.node_id),
                binding.map_lane_id,
                binding.map_role.strip().lower(),
            ),
            [],
        ).append(binding)
    graph, network_lane_ids = build_local_lane_graph(
        candidate_net_file,
        connection_evidence=connection_evidence,
    )

    resolved: list[HamburgOfficialMovementPath] = []
    for movement in movements:
        node_id = _normalize_node(movement.node_id)
        ingress = _one_active_binding(
            binding_index,
            node_id=node_id,
            official_lane_id=movement.ingress_lane_id,
            role="ingress",
            connection_id=movement.connection_id,
        )
        egress = _one_active_binding(
            binding_index,
            node_id=node_id,
            official_lane_id=movement.egress_lane_id,
            role="egress",
            connection_id=movement.connection_id,
        )
        missing_network_lanes = sorted(
            {ingress.sumo_lane, egress.sumo_lane} - set(network_lane_ids)
        )
        if missing_network_lanes:
            raise HamburgMovementPathError(
                f"official movement {node_id}/{movement.connection_id} bindings reference "
                f"missing SUMO lanes: {missing_network_lanes}"
            )
        paths, overflow = find_local_lane_paths(
            graph,
            ingress.sumo_lane,
            egress.sumo_lane,
            max_hops=max_path_hops,
            max_span_m=max_path_span_m,
            max_paths=max_candidate_paths,
        )
        movement_label = (
            f"{node_id}/{movement.connection_id} "
            f"({movement.ingress_lane_id}->{movement.egress_lane_id})"
        )
        if overflow:
            raise HamburgMovementPathError(
                f"official movement {movement_label} path search exceeded "
                f"{max_candidate_paths} candidates"
            )
        if not paths:
            raise HamburgMovementPathError(
                f"official movement {movement_label} has no bounded SUMO lane path for "
                f"{ingress.sumo_lane}->{egress.sumo_lane}"
            )
        if len(paths) != 1:
            raise HamburgMovementPathError(
                f"official movement {movement_label} has {len(paths)} ambiguous bounded "
                f"SUMO lane paths for {ingress.sumo_lane}->{egress.sumo_lane}"
            )
        lane_ids = (ingress.sumo_lane, *(arc.to_lane for arc in paths[0]))
        if lane_ids[0] != ingress.sumo_lane or lane_ids[-1] != egress.sumo_lane:
            raise HamburgMovementPathError(
                f"official movement {movement_label} resolved to inconsistent endpoints"
            )
        resolved.append(
            HamburgOfficialMovementPath(
                node_id=node_id,
                connection_id=movement.connection_id,
                ingress_lane_id=movement.ingress_lane_id,
                egress_lane_id=movement.egress_lane_id,
                lane_ids=lane_ids,
            )
        )

    expected_ids = {
        (_normalize_node(movement.node_id), movement.connection_id)
        for movement in movements
    }
    resolved_ids = {(path.node_id, path.connection_id) for path in resolved}
    if resolved_ids != expected_ids or len(resolved) != len(movements):
        raise HamburgMovementPathError(
            "movement path result does not preserve the official movement inventory exactly"
        )
    return tuple(resolved)


def _one_active_binding(
    binding_index: dict[tuple[str, str, str], list[MapLaneBinding]],
    *,
    node_id: str,
    official_lane_id: str,
    role: str,
    connection_id: str,
) -> MapLaneBinding:
    candidates = binding_index.get((node_id, official_lane_id, role), [])
    if len(candidates) != 1:
        raise HamburgMovementPathError(
            f"official movement {node_id}/{connection_id} {role} MAP lane "
            f"{official_lane_id!r} has {len(candidates)} SUMO bindings"
        )
    binding = candidates[0]
    if binding.mapping_status != "active" or not binding.sumo_lane:
        raise HamburgMovementPathError(
            f"official movement {node_id}/{connection_id} {role} MAP lane "
            f"{official_lane_id!r} binding is not active"
        )
    return binding


def _normalize_node(value: str) -> str:
    text = str(value).strip()
    return str(int(text)) if text.isdigit() else text


def _natural_key(value: str) -> tuple[tuple[int, object], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", str(value))
        if part
    )
