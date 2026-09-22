from __future__ import annotations

import csv
from pathlib import Path

from torii_sumo.core.tls_replay import (
    _prepare_detector_additional,
    _read_tls_events,
    _validate_passenger_link_coverage,
)


def _write_net(path: Path) -> None:
    path.write_text(
        """<net>
        <edge id="in" from="n0" to="n1"><lane id="in_0" index="0" allow="passenger" length="100"/></edge>
        <edge id="out" from="n1" to="n2"><lane id="out_0" index="0" allow="passenger" length="100"/></edge>
        <connection from="in" to="out" fromLane="0" toLane="0" tl="tls0" linkIndex="0"/>
        </net>""",
        encoding="utf-8",
    )


def _write_events(path: Path, include_initial: bool) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["simulation_time", "sumo_tls_id", "sumo_link_index", "sumo_state", "source_state"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "simulation_time": "0" if include_initial else "10",
                "sumo_tls_id": "tls0",
                "sumo_link_index": "0",
                "sumo_state": "r",
                "source_state": "1",
            }
        )


def test_tls_replay_requires_initial_state_for_every_passenger_link(tmp_path: Path) -> None:
    net = tmp_path / "net.xml"
    events_file = tmp_path / "events.csv"
    _write_net(net)
    _write_events(events_file, include_initial=False)
    events = _read_tls_events(events_file, step_length=1, replay_end=100)
    report = _validate_passenger_link_coverage(net, events)
    assert report["status"] == "fail"
    assert report["missing_initial_states"] == {"tls0": [0]}

    _write_events(events_file, include_initial=True)
    events = _read_tls_events(events_file, step_length=1, replay_end=100)
    assert _validate_passenger_link_coverage(net, events)["status"] == "pass"


def test_detector_outputs_are_pinned_to_the_run_directory(tmp_path: Path) -> None:
    source = tmp_path / "source.add.xml"
    destination = tmp_path / "run" / "prepared.add.xml"
    output = tmp_path / "run" / "e1_15min.xml"
    source.write_text(
        '<additional><inductionLoop id="d0" lane="in_0" pos="10" file="elsewhere.xml"/></additional>',
        encoding="utf-8",
    )

    _prepare_detector_additional(
        source,
        destination,
        output_file=output,
        detector_tags={"inductionLoop"},
    )

    assert output.resolve().as_posix() in destination.read_text(encoding="utf-8").replace("\\", "/")


def test_tls_replay_rejects_an_unrepresented_link_on_a_target_controller(tmp_path: Path) -> None:
    net = tmp_path / "net.xml"
    events_file = tmp_path / "events.csv"
    net.write_text(
        """<net>
        <edge id="in0"><lane id="in0_0" index="0" allow="passenger" length="100"/></edge>
        <edge id="out0"><lane id="out0_0" index="0" allow="passenger" length="100"/></edge>
        <edge id="in1"><lane id="in1_0" index="0" allow="passenger" length="100"/></edge>
        <edge id="out1"><lane id="out1_0" index="0" allow="passenger" length="100"/></edge>
        <connection from="in0" to="out0" fromLane="0" toLane="0" tl="tls0" linkIndex="0"/>
        <connection from="in1" to="out1" fromLane="0" toLane="0" tl="tls0" linkIndex="1"/>
        </net>""",
        encoding="utf-8",
    )
    _write_events(events_file, include_initial=True)

    events = _read_tls_events(events_file, step_length=1, replay_end=100)
    report = _validate_passenger_link_coverage(net, events)

    assert report["status"] == "fail"
    assert report["missing_bindings"] == {"tls0": [1]}
    assert report["missing_initial_states"] == {"tls0": [1]}


def test_tls_replay_leaves_non_target_controllers_out_of_scope(tmp_path: Path) -> None:
    net = tmp_path / "net.xml"
    events_file = tmp_path / "events.csv"
    net.write_text(
        """<net>
        <edge id="in0"><lane id="in0_0" index="0" allow="passenger" length="100"/></edge>
        <edge id="out0"><lane id="out0_0" index="0" allow="passenger" length="100"/></edge>
        <edge id="in1"><lane id="in1_0" index="0" allow="passenger" length="100"/></edge>
        <edge id="out1"><lane id="out1_0" index="0" allow="passenger" length="100"/></edge>
        <connection from="in0" to="out0" fromLane="0" toLane="0" tl="tls0" linkIndex="0"/>
        <connection from="in1" to="out1" fromLane="0" toLane="0" tl="outside" linkIndex="0"/>
        </net>""",
        encoding="utf-8",
    )
    _write_events(events_file, include_initial=True)

    events = _read_tls_events(events_file, step_length=1, replay_end=100)
    report = _validate_passenger_link_coverage(net, events)

    assert report["status"] == "pass"
    assert report["non_target_tls_ids"] == ["outside"]


def test_tls_event_reader_rejects_conflicting_duplicate_link_states(tmp_path: Path) -> None:
    events_file = tmp_path / "events.csv"
    with events_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["simulation_time", "sumo_tls_id", "sumo_link_index", "sumo_state", "source_state"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "simulation_time": "0",
                "sumo_tls_id": "tls0",
                "sumo_link_index": "0",
                "sumo_state": "r",
                "source_state": "1",
            }
        )
        writer.writerow(
            {
                "simulation_time": "0",
                "sumo_tls_id": "tls0",
                "sumo_link_index": "0",
                "sumo_state": "G",
                "source_state": "3",
            }
        )

    try:
        _read_tls_events(events_file, step_length=1, replay_end=100)
    except ValueError as exc:
        assert "conflicting TLS events" in str(exc)
    else:
        raise AssertionError("conflicting states for one SUMO TLS link must fail closed")
