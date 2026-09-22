from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from torii_sumo.core.digital_twin_mapping import (
    LaneConnectionEvidence,
    MapLaneBinding,
    bind_map_lanes_to_network,
    build_local_lane_graph,
)
from torii_sumo.core.digital_twin import parse_mapem
from torii_sumo.core.hamburg_movement_path import (
    HAMBURG_SANDTORKAI_CONNECTION_EVIDENCE,
    HamburgMovementPathError,
    derive_hamburg_official_movement_paths,
)
from torii_sumo.core.ocit_c import (
    OcitVehicleTopologyMovement,
    build_vehicle_topology_inventory,
    parse_ocit_c,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SANDTORKAI_ROOT = REPOSITORY_ROOT / "artifacts" / "hamburg_sandtorkai_twin_20260711"
COMPACT_NET = (
    SANDTORKAI_ROOT
    / "network"
    / "official_osm_recovery_v1"
    / "compact_corridor"
    / "hamburg_sandtorkai_official_osm_compact.net.xml"
)
OFFICIAL_SIGNAL_ASSETS = SANDTORKAI_ROOT / "twin" / "official" / "signals" / "assets"


@pytest.mark.skipif(
    not COMPACT_NET.is_file() or not OFFICIAL_SIGNAL_ASSETS.is_dir(),
    reason="requires untracked Hamburg official signal and corridor artifacts",
)
def test_real_sandtorkai_inventory_resolves_exactly_once() -> None:
    map_lanes = []
    map_connections = []
    ocit_configs = []
    for node_id in ("0228", "2394", "2421"):
        node_lanes, node_connections = parse_mapem(
            OFFICIAL_SIGNAL_ASSETS / f"{node_id}_map_xml.xml"
        )
        map_lanes.extend(node_lanes)
        map_connections.extend(node_connections)
        ocit_configs.append(
            parse_ocit_c(OFFICIAL_SIGNAL_ASSETS / f"{node_id}_ocit_xml.xml")
        )
    inventory = build_vehicle_topology_inventory(
        ocit_configs,
        map_lanes,
        map_connections,
        observed_streams=(),
    )
    movements = inventory.movements
    bindings = tuple(bind_map_lanes_to_network(COMPACT_NET, map_lanes))

    paths = derive_hamburg_official_movement_paths(
        candidate_net_file=COMPACT_NET,
        official_movements=movements,
        lane_bindings=bindings,
        connection_evidence=HAMBURG_SANDTORKAI_CONNECTION_EVIDENCE,
    )

    assert len(paths) == 33
    assert Counter(path.node_id for path in paths) == {
        "228": 16,
        "2394": 8,
        "2421": 9,
    }
    assert len({(path.node_id, path.connection_id) for path in paths}) == 33
    binding_index = {
        (_normalize_node(binding.node_id), binding.map_lane_id, binding.map_role): binding
        for binding in bindings
    }
    for path in paths:
        assert path.lane_ids[0] == binding_index[
            (path.node_id, path.ingress_lane_id, "ingress")
        ].sumo_lane
        assert path.lane_ids[-1] == binding_index[
            (path.node_id, path.egress_lane_id, "egress")
        ].sumo_lane


def test_missing_path_fails_closed(tmp_path: Path) -> None:
    net_file = _write_net(
        tmp_path / "missing.net.xml",
        lane_ids=("in_0", "out_0"),
        connections=(),
    )

    with pytest.raises(HamburgMovementPathError, match="has no bounded SUMO lane path"):
        derive_hamburg_official_movement_paths(
            candidate_net_file=net_file,
            official_movements=(_movement(),),
            lane_bindings=_bindings(),
        )


def test_path_candidate_overflow_fails_closed(tmp_path: Path) -> None:
    net_file = _write_net(
        tmp_path / "overflow.net.xml",
        lane_ids=("in_0", "branch_a_0", "branch_b_0", "out_0"),
        connections=(
            ("in", 0, "branch_a", 0),
            ("in", 0, "branch_b", 0),
            ("branch_a", 0, "out", 0),
            ("branch_b", 0, "out", 0),
        ),
    )

    with pytest.raises(HamburgMovementPathError, match="exceeded 1 candidates"):
        derive_hamburg_official_movement_paths(
            candidate_net_file=net_file,
            official_movements=(_movement(),),
            lane_bindings=_bindings(),
            max_candidate_paths=1,
        )


def test_extra_connection_is_connectivity_evidence_not_tls_selection(
    tmp_path: Path,
) -> None:
    net_file = _write_net(
        tmp_path / "evidence.net.xml",
        lane_ids=("in_0", "out_0"),
        connections=(),
    )
    evidence = LaneConnectionEvidence(
        "in_0",
        "out_0",
        "official-map:228:1",
        "official movement connectivity",
    )

    graph, _lane_ids = build_local_lane_graph(
        net_file,
        connection_evidence=(evidence,),
    )
    assert len(graph["in_0"]) == 1
    assert graph["in_0"][0].evidence_id == "official-map:228:1"
    assert graph["in_0"][0].tls_id == ""
    assert graph["in_0"][0].link_index is None
    paths = derive_hamburg_official_movement_paths(
        candidate_net_file=net_file,
        official_movements=(_movement(),),
        lane_bindings=_bindings(),
        connection_evidence=(evidence,),
    )
    assert paths[0].lane_ids == ("in_0", "out_0")


def _movement() -> OcitVehicleTopologyMovement:
    return OcitVehicleTopologyMovement(
        node_id="228",
        connection_id="1",
        ingress_lane_id="1",
        egress_lane_id="2",
        map_signal_group="1",
        primary_motor_groups=("K1",),
        secondary_motor_groups=(),
        topology_control_key="P_K1__S_NONE",
        observed_stream_ids=(),
        observed_signal_groups=(),
    )


def _bindings() -> tuple[MapLaneBinding, MapLaneBinding]:
    return (
        _binding("1", "ingress", "in", "in_0"),
        _binding("2", "egress", "out", "out_0"),
    )


def _binding(
    official_lane_id: str,
    role: str,
    sumo_edge: str,
    sumo_lane: str,
) -> MapLaneBinding:
    return MapLaneBinding(
        node_id="228",
        map_lane_id=official_lane_id,
        map_lane_type="vehicle",
        map_role=role,
        sumo_edge=sumo_edge,
        sumo_lane=sumo_lane,
        lane_position=1.0,
        distance_m=0.0,
        heading_error_deg=0.0,
        mapping_confidence="high",
        mapping_status="active",
    )


def _write_net(
    path: Path,
    *,
    lane_ids: tuple[str, ...],
    connections: tuple[tuple[str, int, str, int], ...],
) -> Path:
    edge_ids = tuple(dict.fromkeys(lane_id.rsplit("_", 1)[0] for lane_id in lane_ids))
    edge_rows = "".join(
        f'<edge id="{edge_id}" from="n0" to="n1">'
        f'<lane id="{edge_id}_0" index="0" length="10" speed="13.9"/>'
        "</edge>"
        for edge_id in edge_ids
    )
    connection_rows = "".join(
        f'<connection from="{from_edge}" fromLane="{from_lane}" '
        f'to="{to_edge}" toLane="{to_lane}"/>'
        for from_edge, from_lane, to_edge, to_lane in connections
    )
    path.write_text(f"<net>{edge_rows}{connection_rows}</net>", encoding="utf-8")
    return path


def _normalize_node(value: str) -> str:
    text = str(value).strip()
    return str(int(text)) if text.isdigit() else text
