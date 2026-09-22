from __future__ import annotations

from pathlib import Path

from torii_sumo.core.digital_twin import SignalStream
from torii_sumo.core.digital_twin_mapping import (
    MapLaneBinding,
    bind_signal_streams_to_tls,
    select_active_signal_streams,
)


def _write_net(path: Path) -> None:
    path.write_text(
        """<net>
        <edge id="in"><lane id="in_0" index="0" allow="passenger" length="100"/></edge>
        <edge id="out"><lane id="out_0" index="0" allow="passenger" length="100"/></edge>
        <connection from="in" to="out" fromLane="0" toLane="0" tl="tls0" linkIndex="0"/>
        </net>""",
        encoding="utf-8",
    )


def _lane_binding(
    map_lane_id: str,
    sumo_lane: str,
    role: str,
    *,
    status: str = "active",
) -> MapLaneBinding:
    return MapLaneBinding(
        node_id="228",
        map_lane_id=map_lane_id,
        map_lane_type="vehicle",
        map_role=role,
        sumo_edge=sumo_lane.rsplit("_", 1)[0],
        sumo_lane=sumo_lane,
        lane_position=90,
        distance_m=1,
        heading_error_deg=1,
        mapping_confidence="high",
        mapping_status=status,
    )


def _stream(stream_id: int, signal_group: str) -> SignalStream:
    return SignalStream(
        stream_id=stream_id,
        thing_id=stream_id + 100,
        node_id="228",
        connection_id=str(stream_id),
        ingress_lane_id="1",
        egress_lane_id="2",
        lane_type="KFZ",
        signal_group=signal_group,
        layer_name="primary_signal",
        name=f"stream-{stream_id}",
    )


def test_same_signal_group_uses_one_deterministic_stream_per_sumo_link(tmp_path: Path) -> None:
    net = tmp_path / "net.xml"
    _write_net(net)
    streams = [_stream(20, "SG1"), _stream(10, "SG1")]

    bindings = bind_signal_streams_to_tls(
        net,
        streams,
        [_lane_binding("1", "in_0", "ingress"), _lane_binding("2", "out_0", "egress")],
    )

    assert [(item.stream_id, item.mapping_status) for item in bindings] == [(10, "active"), (20, "redundant")]
    assert [item.stream_id for item in select_active_signal_streams(streams, bindings)] == [10]


def test_conflicting_signal_groups_fail_closed_when_osm_collapses_links(tmp_path: Path) -> None:
    net = tmp_path / "net.xml"
    _write_net(net)
    streams = [_stream(10, "SG1"), _stream(20, "SG2")]

    bindings = bind_signal_streams_to_tls(
        net,
        streams,
        [_lane_binding("1", "in_0", "ingress"), _lane_binding("2", "out_0", "egress")],
    )

    assert {item.mapping_status for item in bindings} == {"needs_review"}
    assert select_active_signal_streams(streams, bindings) == []


def test_unique_signal_link_remains_active(tmp_path: Path) -> None:
    net = tmp_path / "net.xml"
    _write_net(net)
    stream = _stream(10, "")

    bindings = bind_signal_streams_to_tls(
        net,
        [stream],
        [_lane_binding("1", "in_0", "ingress"), _lane_binding("2", "out_0", "egress")],
    )

    assert bindings[0].mapping_status == "active"
    assert select_active_signal_streams([stream], bindings) == [stream]


def _write_path_net(path: Path, controlled_connections: set[int]) -> None:
    connections = []
    edge_ids = ["in", "mid0", "mid1", "out"]
    for index, (source, target) in enumerate(zip(edge_ids, edge_ids[1:])):
        control = f' tl="tls{index}" linkIndex="{index}"' if index in controlled_connections else ""
        connections.append(
            f'<connection from="{source}" to="{target}" fromLane="0" toLane="0"{control}/>'
        )
    edges = "".join(
        f'<edge id="{edge}"><lane id="{edge}_0" index="0" allow="passenger" length="20"/></edge>'
        for edge in edge_ids
    )
    path.write_text(f"<net>{edges}{''.join(connections)}</net>", encoding="utf-8")


def test_path_with_one_controlled_connection_binds_at_controlled_arc(tmp_path: Path) -> None:
    net = tmp_path / "net.xml"
    _write_path_net(net, {1})

    binding = bind_signal_streams_to_tls(
        net,
        [_stream(10, "SG1")],
        [_lane_binding("1", "in_0", "ingress"), _lane_binding("2", "out_0", "egress")],
    )[0]

    assert binding.mapping_status == "active"
    assert (binding.sumo_tls_id, binding.sumo_link_index) == ("tls1", 1)
    assert (binding.sumo_from_lane, binding.sumo_to_lane) == ("mid0_0", "mid1_0")
    assert binding.mapping_confidence == "medium"


def test_path_with_multiple_controlled_connections_needs_review(tmp_path: Path) -> None:
    net = tmp_path / "net.xml"
    _write_path_net(net, {0, 1})

    binding = bind_signal_streams_to_tls(
        net,
        [_stream(10, "SG1")],
        [_lane_binding("1", "in_0", "ingress"), _lane_binding("2", "out_0", "egress")],
    )[0]

    assert binding.mapping_status == "needs_review"
    assert "non-unique controlled-link counts" in binding.mapping_reason


def test_official_endpoint_link_index_disambiguates_joined_shared_controller_path(tmp_path: Path) -> None:
    net = tmp_path / "net.xml"
    _write_path_net(net, {0, 1})

    binding = bind_signal_streams_to_tls(
        net,
        [_stream(10, "SG1")],
        [_lane_binding("1", "in_0", "ingress"), _lane_binding("2", "out_0", "egress")],
        official_link_indices={("228", "10"): (1,)},
    )[0]

    assert binding.mapping_status == "active"
    assert (binding.sumo_tls_id, binding.sumo_link_index) == ("tls1", 1)


def test_path_without_controlled_connection_needs_review(tmp_path: Path) -> None:
    net = tmp_path / "net.xml"
    _write_path_net(net, set())

    binding = bind_signal_streams_to_tls(
        net,
        [_stream(10, "SG1")],
        [_lane_binding("1", "in_0", "ingress"), _lane_binding("2", "out_0", "egress")],
    )[0]

    assert binding.mapping_status == "needs_review"


def test_non_active_map_lane_binding_is_not_promoted(tmp_path: Path) -> None:
    net = tmp_path / "net.xml"
    _write_net(net)

    binding = bind_signal_streams_to_tls(
        net,
        [_stream(10, "SG1")],
        [
            _lane_binding("1", "in_0", "ingress", status="needs_review"),
            _lane_binding("2", "out_0", "egress"),
        ],
    )[0]

    assert binding.mapping_status == "needs_review"
    assert "not active" in binding.mapping_reason
