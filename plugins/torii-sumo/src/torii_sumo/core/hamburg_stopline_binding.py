from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from .candidate_contracts import file_sha256
from .digital_twin_mapping import MapLaneBinding


@dataclass(frozen=True)
class StoplineBindingEvidence:
    source_net_sha256: str
    node_id: str
    map_lane_id: str
    status: str
    reason: str
    original_sumo_edge: str
    original_sumo_lane: str
    adjusted_sumo_edge: str
    adjusted_sumo_lane: str
    anchor_to_lane_start_m: float
    connection_repair_key: tuple[str, int, str, int] | None
    connection_repair_evidence: str


@dataclass(frozen=True)
class StoplineBindingEstimate:
    status: str
    source_net_sha256: str
    bindings: tuple[MapLaneBinding, ...]
    evidence: tuple[StoplineBindingEvidence, ...]


@dataclass(frozen=True)
class _Lane:
    edge_id: str
    lane_id: str
    index: int
    length: float


def estimate_ingress_stopline_bindings(
    net_file: Path,
    lane_bindings: Sequence[MapLaneBinding],
    *,
    start_tolerance_m: float = 5.0,
) -> StoplineBindingEstimate:
    """Move start-side ingress bindings to one proven external predecessor lane.

    ``MapLaneBinding.lane_position`` is the official anchor projection along
    the mapped SUMO lane.  A projection near that lane's start may therefore
    describe the physical stop line at the end of its predecessor.  This
    estimator changes only that identity and never edits the network.
    """

    if not math.isfinite(start_tolerance_m) or start_tolerance_m < 0:
        raise ValueError("start_tolerance_m must be finite and non-negative")

    source = net_file.resolve()
    source_sha256 = file_sha256(source)
    root = ET.parse(source).getroot()
    lanes, lanes_by_edge_index = _external_lanes(root)
    predecessors = _external_predecessors(root, lanes_by_edge_index)
    adjusted = list(lane_bindings)
    evidence: list[StoplineBindingEvidence] = []

    for index, binding in enumerate(lane_bindings):
        if not _is_active_vehicle_ingress(binding):
            continue
        lane = lanes.get(binding.sumo_lane)
        if lane is None or lane.edge_id != binding.sumo_edge:
            evidence.append(
                _blocked(source_sha256, binding, "mapped SUMO lane identity is invalid")
            )
            continue
        if (
            not math.isfinite(binding.lane_position)
            or binding.lane_position < 0
            or binding.lane_position > lane.length
        ):
            evidence.append(
                _blocked(source_sha256, binding, "mapped lane position is outside lane bounds")
            )
            continue

        start_distance = binding.lane_position
        if start_distance >= lane.length - start_distance:
            continue
        if start_distance > start_tolerance_m:
            evidence.append(
                _review(
                    binding,
                    start_distance,
                    "official anchor is start-side but outside the stop-line tolerance",
                    source_net_sha256=source_sha256,
                )
            )
            continue

        candidates = predecessors.get(binding.sumo_lane, ())
        if len(candidates) != 1:
            evidence.append(
                _review(
                    binding,
                    start_distance,
                    f"expected one external predecessor connection, found {len(candidates)}",
                    source_net_sha256=source_sha256,
                )
            )
            continue

        predecessor, key = candidates[0]
        if not predecessor.lane_id:
            evidence.append(
                _blocked(
                    source_sha256,
                    binding,
                    "predecessor connection references an invalid lane",
                )
            )
            continue
        adjusted_binding = replace(
            binding,
            sumo_edge=predecessor.edge_id,
            sumo_lane=predecessor.lane_id,
            lane_position=predecessor.length,
        )
        adjusted[index] = adjusted_binding
        evidence.append(
            StoplineBindingEvidence(
                source_net_sha256=source_sha256,
                node_id=binding.node_id,
                map_lane_id=binding.map_lane_id,
                status="pass",
                reason="unique external predecessor proves the physical stop-line boundary",
                original_sumo_edge=binding.sumo_edge,
                original_sumo_lane=binding.sumo_lane,
                adjusted_sumo_edge=predecessor.edge_id,
                adjusted_sumo_lane=predecessor.lane_id,
                anchor_to_lane_start_m=start_distance,
                connection_repair_key=key,
                connection_repair_evidence="official_map_stopline",
            )
        )

    statuses = {row.status for row in evidence}
    status = (
        "blocked"
        if "blocked" in statuses
        else "review_required"
        if "review_required" in statuses
        else "pass"
    )
    return StoplineBindingEstimate(
        status=status,
        source_net_sha256=source_sha256,
        bindings=tuple(adjusted),
        evidence=tuple(evidence),
    )


def _external_lanes(
    root: ET.Element,
) -> tuple[dict[str, _Lane], dict[tuple[str, int], _Lane]]:
    by_id: dict[str, _Lane] = {}
    by_edge_index: dict[tuple[str, int], _Lane] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function"):
            continue
        for element in edge.findall("lane"):
            try:
                lane = _Lane(
                    edge_id=edge_id,
                    lane_id=element.attrib["id"],
                    index=int(element.attrib["index"]),
                    length=float(element.attrib["length"]),
                )
            except (KeyError, ValueError):
                continue
            if not lane.lane_id or not math.isfinite(lane.length) or lane.length < 0:
                continue
            if lane.lane_id in by_id or (edge_id, lane.index) in by_edge_index:
                raise ValueError(f"duplicate external SUMO lane identity: {lane.lane_id}")
            by_id[lane.lane_id] = lane
            by_edge_index[(edge_id, lane.index)] = lane
    return by_id, by_edge_index


def _external_predecessors(
    root: ET.Element,
    lanes_by_edge_index: dict[tuple[str, int], _Lane],
) -> dict[str, tuple[tuple[_Lane, tuple[str, int, str, int]], ...]]:
    rows: dict[str, dict[tuple[str, int, str, int], _Lane]] = {}
    for connection in root.findall("connection"):
        from_edge = connection.attrib.get("from", "")
        to_edge = connection.attrib.get("to", "")
        if from_edge.startswith(":") or to_edge.startswith(":"):
            continue
        try:
            from_lane_index = int(connection.attrib["fromLane"])
            to_lane_index = int(connection.attrib["toLane"])
        except (KeyError, ValueError):
            continue
        predecessor = lanes_by_edge_index.get((from_edge, from_lane_index))
        target = lanes_by_edge_index.get((to_edge, to_lane_index))
        if target is None:
            continue
        key = (from_edge, from_lane_index, to_edge, to_lane_index)
        if predecessor is None:
            rows.setdefault(target.lane_id, {})[key] = _Lane(from_edge, "", from_lane_index, 0)
            continue
        rows.setdefault(target.lane_id, {})[key] = predecessor
    return {
        lane_id: tuple((lane, key) for key, lane in sorted(candidates.items()))
        for lane_id, candidates in rows.items()
    }


def _is_active_vehicle_ingress(binding: MapLaneBinding) -> bool:
    return (
        binding.mapping_status == "active"
        and binding.map_lane_type.lower() == "vehicle"
        and binding.map_role == "ingress"
    )


def _blocked(
    source_net_sha256: str,
    binding: MapLaneBinding,
    reason: str,
) -> StoplineBindingEvidence:
    return StoplineBindingEvidence(
        source_net_sha256,
        binding.node_id,
        binding.map_lane_id,
        "blocked",
        reason,
        binding.sumo_edge,
        binding.sumo_lane,
        "",
        "",
        math.nan,
        None,
        "",
    )


def _review(
    binding: MapLaneBinding,
    start_distance: float,
    reason: str,
    *,
    source_net_sha256: str,
) -> StoplineBindingEvidence:
    return StoplineBindingEvidence(
        source_net_sha256,
        binding.node_id,
        binding.map_lane_id,
        "review_required",
        reason,
        binding.sumo_edge,
        binding.sumo_lane,
        "",
        "",
        start_distance,
        None,
        "",
    )
