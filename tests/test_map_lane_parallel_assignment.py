from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import shutil

import pytest

from astra_sumo.core import digital_twin_mapping as mapping
from astra_sumo.core.digital_twin import MapLane, CountStream
from astra_sumo.core.digital_twin_mapping import (
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


def test_channelized_approach_assigns_distinct_lanes_within_each_nearest_road(monkeypatch):
    _install_network(monkeypatch, [_network_lane(f"{edge}_{i}", edge_id=edge, y=y)
        for edge, values in (("first", (0, 3)), ("second", (6, 9))) for i,y in enumerate(values)])
    lanes = [_map_lane(str(i), anchor_y=y) for i,y in enumerate((0, 1.4, 6, 7.4))]
    bindings = bind_map_lanes_to_network(Path("split.net.xml"), lanes)
    assert [b.sumo_lane for b in bindings] == ["first_0", "first_1", "second_0", "second_1"]


def test_curved_lane_uses_its_heading_at_the_bound_anchor(monkeypatch):
    _install_network(monkeypatch, [_network_lane("main_0", edge_id="main", y=.3),
        NetworkLane("slip_0", "slip", 14.1421356, ((10,10),(0,0)))])
    lane = replace(_map_lane("curve", anchor_y=0), points_m=((0,0),(10,10),(100,0)))
    [binding] = bind_map_lanes_to_network(Path("curve.net.xml"), [lane])
    assert binding.sumo_lane == "slip_0" and binding.heading_error_deg == pytest.approx(0)


@pytest.mark.parametrize("missing_offsets", [False,True])
def test_explicit_reference_geometry_replaces_approximate_map_offsets(monkeypatch,missing_offsets):
    _install_network(monkeypatch, [_network_lane("road_0", edge_id="road", y=0),
        _network_lane("road_1", edge_id="road", y=3)])
    lane = _map_lane("6", anchor_y=0)
    if missing_offsets:
        lane = replace(lane, points_m=())
    [binding] = bind_map_lanes_to_network(Path("reference.net.xml"), [lane],
        lane_shapes={("2394","6"):((0,3),(100,3))})
    assert binding.sumo_lane == "road_1"


def test_detector_uses_its_physical_position_and_the_logical_lane_scale(monkeypatch):
    _install_network(monkeypatch, [NetworkLane("road_0", "road", 200, ((100,0),(0,0)))])
    lane = _map_lane("6", anchor_y=0)
    stream = CountStream(1,None,"2394","Z.1","","",75/111320,0)
    bindings = bind_map_lanes_to_network(Path("scaled.net.xml"), [lane])
    [detector] = mapping.bind_count_streams_to_network(Path("scaled.net.xml"), [stream], [lane], bindings)
    assert bindings[0].lane_position == pytest.approx(199.9)
    assert detector.lane_position == pytest.approx(50) and detector.distance_m == pytest.approx(0)
    [fallback] = mapping.bind_count_streams_to_network(Path("scaled.net.xml"), [stream], [])
    assert fallback.lane_position == pytest.approx(50) and fallback.mapping_status == "needs_review"


@pytest.mark.skipif(not shutil.which("sumo"), reason="SUMO required")
def test_native_detector_position_roundtrip_uses_logical_scale(tmp_path):
    import traci
    from pyproj import Transformer
    network = tmp_path / "scaled.net.xml"
    network.write_bytes(b'''<net version="1.20"><location netOffset="0,0" convBoundary="565950,5933204,566050,5933206" origBoundary="0,0,1,1" projParameter="!"/>
    <edge id="road" from="a" to="b" priority="1"><lane id="road_0" index="0" speed="10" length="200" shape="565950,5933205 566050,5933205"/></edge>
    <junction id="a" type="dead_end" x="565950" y="5933205" incLanes="" intLanes=""/>
    <junction id="b" type="dead_end" x="566050" y="5933205" incLanes="road_0" intLanes=""/></net>''')
    transform = Transformer.from_crs("EPSG:25832","EPSG:4326",always_xy=True)
    lon,lat = transform.transform(565975,5933205)
    reference = MapLane("1","1","vehicle","1","",lon,lat,((0,0),(-100,0)))
    shapes = {("1","1"):((566050,5933205),(565950,5933205))}
    [detector] = mapping.bind_count_streams_to_network(network,
        [CountStream(1,None,"1","Z.1","","",lon,lat)],[reference],
        network_projection="EPSG:25832",lane_shapes=shapes)
    assert detector.mapping_status == "active" and detector.lane_position == pytest.approx(50)
    label = "detector-coordinate-scale"
    traci.start([shutil.which("sumo"),"-n",str(network),"--end","1","--no-step-log","true"],label=label,numRetries=3)
    connection = traci.getConnection(label)
    try:
        assert connection.simulation.convert2D("road",detector.lane_position,0) == pytest.approx((565975,5933205),abs=1e-6)
    finally:
        connection.close()


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
