from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from torii_sumo.core.digital_twin import SignalObservation, SignalStream
from torii_sumo.core.digital_twin_mapping import (
    MapLaneBinding,
    TlsBinding,
    bind_signal_streams_to_tls,
    write_tls_link_events,
)


UTC = timezone.utc


def _stream(*, stream_id: int = 71221, signal_group: str = "K3") -> SignalStream:
    return SignalStream(
        stream_id=stream_id,
        thing_id=stream_id + 1,
        node_id="228",
        connection_id="2",
        ingress_lane_id="19",
        egress_lane_id="25",
        lane_type="KFZ",
        signal_group=signal_group,
        layer_name="primary_signal",
        name=f"stream-{stream_id}",
    )


def _lane_binding(map_lane_id: str, sumo_lane: str, role: str) -> MapLaneBinding:
    return MapLaneBinding(
        node_id="228",
        map_lane_id=map_lane_id,
        map_lane_type="vehicle",
        map_role=role,
        sumo_edge=sumo_lane.rsplit("_", 1)[0],
        sumo_lane=sumo_lane,
        lane_position=10.0,
        distance_m=1.0,
        heading_error_deg=1.0,
        mapping_confidence="high",
        mapping_status="active",
    )


def _write_serial_net(path: Path, controls: list[tuple[str, int]]) -> None:
    edge_ids = ["in", *[f"mid{index}" for index in range(len(controls) - 1)], "out"]
    edges = "".join(
        f'<edge id="{edge}"><lane id="{edge}_0" index="0" allow="passenger" length="20"/></edge>'
        for edge in edge_ids
    )
    connections = "".join(
        (
            f'<connection from="{source}" to="{target}" fromLane="0" toLane="0" '
            f'tl="{tls_id}" linkIndex="{link_index}"/>'
        )
        for (source, target), (tls_id, link_index) in zip(
            zip(edge_ids, edge_ids[1:]),
            controls,
        )
    )
    path.write_text(f"<net>{edges}{connections}</net>", encoding="utf-8")


def _write_parallel_net(
    path: Path,
    *,
    left_target: tuple[str, int],
    right_target: tuple[str, int],
) -> None:
    edges = "".join(
        f'<edge id="{edge}"><lane id="{edge}_0" index="0" allow="passenger" length="20"/></edge>'
        for edge in ("in", "left", "right", "out")
    )
    left_tls, left_index = left_target
    right_tls, right_index = right_target
    connections = (
        f'<connection from="in" to="left" fromLane="0" toLane="0" '
        f'tl="{left_tls}" linkIndex="{left_index}"/>'
        '<connection from="left" to="out" fromLane="0" toLane="0"/>'
        f'<connection from="in" to="right" fromLane="0" toLane="0" '
        f'tl="{right_tls}" linkIndex="{right_index}"/>'
        '<connection from="right" to="out" fromLane="0" toLane="0"/>'
    )
    path.write_text(f"<net>{edges}{connections}</net>", encoding="utf-8")


def _bind(net: Path) -> list[TlsBinding]:
    return bind_signal_streams_to_tls(
        net,
        [_stream()],
        [
            _lane_binding("19", "in_0", "ingress"),
            _lane_binding("25", "out_0", "egress"),
        ],
    )


def test_serial_physical_connections_with_one_control_key_collapse_to_one_binding(
    tmp_path: Path,
) -> None:
    net = tmp_path / "serial.net.xml"
    _write_serial_net(net, [("HH_228", 2), ("HH_228", 2)])

    bindings = _bind(net)

    assert len(bindings) == 1
    assert bindings[0].mapping_status == "active"
    assert (bindings[0].sumo_tls_id, bindings[0].sumo_link_index) == ("HH_228", 2)
    assert "collapsed_controlled_arcs=2" in bindings[0].mapping_reason


def test_serial_connections_with_different_control_keys_remain_fail_closed(tmp_path: Path) -> None:
    net = tmp_path / "serial.net.xml"
    _write_serial_net(net, [("HH_228", 2), ("HH_228", 3)])

    bindings = _bind(net)

    assert len(bindings) == 1
    assert bindings[0].mapping_status == "needs_review"
    assert bindings[0].sumo_link_index is None


def test_parallel_physical_connections_ignore_from_to_when_control_key_is_shared(
    tmp_path: Path,
) -> None:
    net = tmp_path / "parallel.net.xml"
    _write_parallel_net(
        net,
        left_target=("HH_228", 2),
        right_target=("HH_228", 2),
    )

    bindings = _bind(net)

    assert len(bindings) == 1
    assert bindings[0].mapping_status == "active"
    assert (bindings[0].sumo_tls_id, bindings[0].sumo_link_index) == ("HH_228", 2)


def test_parallel_paths_with_different_control_keys_remain_fail_closed(tmp_path: Path) -> None:
    net = tmp_path / "parallel.net.xml"
    _write_parallel_net(
        net,
        left_target=("HH_228", 2),
        right_target=("HH_228", 3),
    )

    bindings = _bind(net)

    assert len(bindings) == 1
    assert bindings[0].mapping_status == "needs_review"


def _target_binding(*, tls_id: str, link_index: int) -> TlsBinding:
    return TlsBinding(
        stream_id=71221,
        node_id="228",
        connection_id="2",
        signal_group="K3",
        official_ingress_lane="19",
        official_egress_lane="25",
        sumo_from_lane=f"in_{link_index}",
        sumo_to_lane=f"out_{link_index}",
        sumo_tls_id=tls_id,
        sumo_link_index=link_index,
        mapping_confidence="medium",
        mapping_status="active",
        mapping_reason="explicit atomic target row",
    )


def test_tls_event_writer_fans_one_stream_out_to_every_active_target(tmp_path: Path) -> None:
    begin = datetime(2026, 7, 4, 8, 0, tzinfo=UTC)
    end = datetime(2026, 7, 4, 10, 0, tzinfo=UTC)
    events_file = tmp_path / "tls_link_events.csv"
    observations = {
        71221: [
            SignalObservation(
                stream_id=71221,
                observation_id=1,
                phenomenon_time_utc=datetime(2026, 7, 4, 7, 59, tzinfo=UTC),
                result="1",
            ),
            SignalObservation(
                stream_id=71221,
                observation_id=2,
                phenomenon_time_utc=datetime(2026, 7, 4, 8, 5, tzinfo=UTC),
                result="3",
            ),
        ]
    }

    stats = write_tls_link_events(
        events_file,
        [_stream()],
        observations,
        [
            _target_binding(tls_id="HH_228", link_index=2),
            _target_binding(tls_id="HH_228", link_index=3),
        ],
        begin_utc=begin,
        end_utc=end,
    )

    with events_file.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert stats == {
        "active_binding_count": 2,
        "initialized_binding_count": 2,
        "event_count": 4,
        "collapsed_event_count": 0,
        "conflict_event_count": 0,
    }
    assert [
        (row["simulation_time"], row["sumo_link_index"], row["sumo_state"])
        for row in rows
    ] == [
        ("0.000", "2", "r"),
        ("0.000", "3", "r"),
        ("300.000", "2", "G"),
        ("300.000", "3", "G"),
    ]


def test_tls_event_writer_keeps_last_state_within_one_sumo_step(tmp_path: Path) -> None:
    begin = datetime(2026, 7, 4, 8, 0, tzinfo=UTC)
    end = datetime(2026, 7, 4, 10, 0, tzinfo=UTC)
    events_file = tmp_path / "tls_link_events.csv"
    observations = {
        71221: [
            SignalObservation(
                stream_id=71221,
                observation_id=1,
                phenomenon_time_utc=datetime(2026, 7, 4, 7, 59, tzinfo=UTC),
                result="1",
            ),
            SignalObservation(
                stream_id=71221,
                observation_id=2,
                phenomenon_time_utc=datetime(2026, 7, 4, 8, 0, 1, tzinfo=UTC),
                result="4",
                result_time="2026-07-04T08:00:01.100Z",
            ),
            SignalObservation(
                stream_id=71221,
                observation_id=3,
                phenomenon_time_utc=datetime(2026, 7, 4, 8, 0, 1, tzinfo=UTC),
                result="3",
                result_time="2026-07-04T08:00:01.900Z",
            ),
        ]
    }

    stats = write_tls_link_events(
        events_file,
        [_stream()],
        observations,
        [_target_binding(tls_id="HH_228", link_index=2)],
        begin_utc=begin,
        end_utc=end,
    )

    assert stats["collapsed_event_count"] == 1
    assert stats["conflict_event_count"] == 1
    with events_file.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [(row["simulation_time"], row["sumo_state"]) for row in rows] == [("0.000", "r"), ("1.000", "G")]
