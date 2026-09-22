from __future__ import annotations

from pathlib import Path

from torii_sumo.core import digital_twin_mapping as mapping
from torii_sumo.core.digital_twin import MapLane
from torii_sumo.core.digital_twin_mapping import (
    NetworkLane,
    bind_map_lanes_to_explicit_network_lanes,
    bind_map_lanes_to_network,
)


class _LocalMetricNet:
    def convertLonLat2XY(self, longitude: float, latitude: float) -> tuple[float, float]:
        return longitude * 111_320.0, latitude * 110_540.0


def _map_lane(lane_id: str, *, anchor_x: float = 0.0, anchor_y: float) -> MapLane:
    return MapLane(
        node_id="2394",
        lane_id=lane_id,
        lane_type="vehicle",
        ingress_approach="3",
        egress_approach="",
        ref_longitude=0.0,
        ref_latitude=0.0,
        points_m=((anchor_x, anchor_y), (anchor_x + 100.0, anchor_y)),
    )


def _network_lane(
    lane_id: str,
    *,
    edge_id: str = "381540198#1",
    begin_x: float = 100.0,
    end_x: float = 0.0,
    y: float,
) -> NetworkLane:
    return NetworkLane(
        lane_id=lane_id,
        edge_id=edge_id,
        length=abs(begin_x - end_x),
        shape=((begin_x, y), (end_x, y)),
    )


def _install_network(monkeypatch, lanes: list[NetworkLane]) -> None:
    monkeypatch.setattr(
        mapping,
        "read_network_lanes",
        lambda _path: (_LocalMetricNet(), lanes),
    )


def test_parallel_map_lanes_use_minimum_cost_unique_assignment(monkeypatch) -> None:
    # Both official anchors independently prefer lane 0.  Because the SUMO edge has
    # two parallel lanes, the group assignment must instead preserve two lanes.
    _install_network(
        monkeypatch,
        [
            _network_lane("381540198#1_0", y=0.0),
            _network_lane("381540198#1_1", y=3.0),
        ],
    )

    bindings = bind_map_lanes_to_network(
        Path("2394.net.xml"),
        [_map_lane("6", anchor_y=0.0), _map_lane("7", anchor_y=1.4)],
    )

    assert [(item.map_lane_id, item.sumo_lane) for item in bindings] == [
        ("6", "381540198#1_0"),
        ("7", "381540198#1_1"),
    ]
    assert {item.mapping_status for item in bindings} == {"active"}


def test_capacity_shortage_preserves_auditable_many_to_one_fallback(monkeypatch) -> None:
    # A compact OSM network may genuinely have fewer physical lanes than the official
    # MAP.  With no second lane available, retain the established independent-nearest
    # representation so downstream detector aggregation can audit the collapse.
    _install_network(monkeypatch, [_network_lane("compact_0", edge_id="compact", y=0.0)])

    bindings = bind_map_lanes_to_network(
        Path("compact.net.xml"),
        [_map_lane("6", anchor_y=0.0), _map_lane("7", anchor_y=1.4)],
    )

    assert [item.sumo_lane for item in bindings] == ["compact_0", "compact_0"]
    assert {item.mapping_status for item in bindings} == {"active"}


def test_explicit_map_lane_binding_uses_contract_lane_and_rechecks_geometry(monkeypatch) -> None:
    _install_network(
        monkeypatch,
        [
            _network_lane("edge_0", edge_id="edge", y=0.0),
            _network_lane("edge_1", edge_id="edge", y=3.0),
        ],
    )

    bindings = bind_map_lanes_to_explicit_network_lanes(
        Path("contract.net.xml"),
        [_map_lane("6", anchor_y=0.0), _map_lane("7", anchor_y=1.4)],
        {("2394", "6"): "edge_1", ("2394", "7"): "edge_0"},
    )

    assert [(item.map_lane_id, item.sumo_lane) for item in bindings] == [
        ("6", "edge_1"),
        ("7", "edge_0"),
    ]
    assert all(item.mapping_status == "active" for item in bindings)


def test_explicit_map_lane_binding_does_not_waive_heading_gate(monkeypatch) -> None:
    _install_network(
        monkeypatch,
        [_network_lane("wrong_way_0", edge_id="wrong_way", begin_x=0.0, end_x=100.0, y=0.0)],
    )

    [binding] = bind_map_lanes_to_explicit_network_lanes(
        Path("contract.net.xml"),
        [_map_lane("6", anchor_y=0.0)],
        {("2394", "6"): "wrong_way_0"},
    )

    assert binding.sumo_lane == "wrong_way_0"
    assert binding.mapping_status == "needs_review"
    assert binding.mapping_confidence == "none"
    assert binding.heading_error_deg == 180.0


def test_parallel_assignment_searches_beyond_nearest_micro_stub(monkeypatch) -> None:
    # Two official lanes independently snap to the same lane on a short two-lane
    # stub.  The immediately aligned three-lane cross-section can represent the
    # complete official approach injectively and must be selected as one edge.
    _install_network(
        monkeypatch,
        [
            _network_lane("stub_0", edge_id="stub", y=0.0),
            _network_lane("stub_1", edge_id="stub", y=3.0),
            _network_lane("continuation_0", edge_id="continuation", y=0.2),
            _network_lane("continuation_1", edge_id="continuation", y=3.2),
            _network_lane("continuation_2", edge_id="continuation", y=6.2),
        ],
    )

    bindings = bind_map_lanes_to_network(
        Path("lane-count-transition.net.xml"),
        [
            _map_lane("25", anchor_y=5.9),
            _map_lane("26", anchor_y=3.0),
            _map_lane("27", anchor_y=0.0),
        ],
    )

    assert [(item.map_lane_id, item.sumo_lane) for item in bindings] == [
        ("25", "continuation_2"),
        ("26", "continuation_1"),
        ("27", "continuation_0"),
    ]


def test_parallel_assignment_rejects_a_distant_full_capacity_edge(monkeypatch) -> None:
    _install_network(
        monkeypatch,
        [
            _network_lane("compact_0", edge_id="compact", y=0.0),
            _network_lane("distant_0", edge_id="distant", y=20.0),
            _network_lane("distant_1", edge_id="distant", y=23.0),
        ],
    )

    bindings = bind_map_lanes_to_network(
        Path("distant-capacity.net.xml"),
        [_map_lane("6", anchor_y=0.0), _map_lane("7", anchor_y=1.0)],
    )

    assert [item.sumo_lane for item in bindings] == ["compact_0", "compact_0"]


def test_equal_cost_unique_assignments_fail_closed(monkeypatch) -> None:
    _install_network(
        monkeypatch,
        [
            _network_lane("parallel_0", edge_id="parallel", y=0.0),
            _network_lane("parallel_1", edge_id="parallel", y=3.0),
        ],
    )

    bindings = bind_map_lanes_to_network(
        Path("ambiguous.net.xml"),
        [_map_lane("6", anchor_y=1.5), _map_lane("7", anchor_y=1.5)],
    )

    assert len({item.sumo_lane for item in bindings}) == 2
    assert {item.mapping_status for item in bindings} == {"needs_review"}


def test_same_approach_consecutive_segments_are_not_forced_into_one_cross_section(
    monkeypatch,
) -> None:
    _install_network(
        monkeypatch,
        [
            _network_lane("first_0", edge_id="first", begin_x=10.0, end_x=0.0, y=0.0),
            _network_lane("first_1", edge_id="first", begin_x=10.0, end_x=0.0, y=3.0),
            _network_lane("second_0", edge_id="second", begin_x=30.0, end_x=20.0, y=0.0),
            _network_lane("second_1", edge_id="second", begin_x=30.0, end_x=20.0, y=3.0),
        ],
    )

    bindings = bind_map_lanes_to_network(
        Path("split.net.xml"),
        [_map_lane("46", anchor_x=0.0, anchor_y=0.0), _map_lane("47", anchor_x=20.0, anchor_y=0.0)],
    )

    assert [(item.map_lane_id, item.sumo_lane) for item in bindings] == [
        ("46", "first_0"),
        ("47", "second_0"),
    ]
