from __future__ import annotations

import json
from pathlib import Path

import pytest

from torii_sumo.core import tls_replay
from torii_sumo.core.tls_replay import (
    _resolve_comparison_window,
    _select_expected_rows_for_comparison,
    run_tls_detector_replay,
)
from torii_sumo.tools import digital_twin_tools


def _row(begin: int, end: int, *, detector_id: str = "d0") -> dict[str, str]:
    return {
        "detector_id": detector_id,
        "edge_id": "edge0",
        "begin": str(begin),
        "end": str(end),
        "expected_total": "10",
    }


def test_warmup_bins_are_excluded_and_full_comparison_bins_are_retained() -> None:
    rows = [
        _row(0, 900),
        _row(900, 1800),
        _row(1800, 2700),
        _row(2700, 9000),
    ]

    selected = _select_expected_rows_for_comparison(
        rows,
        comparison_begin=1800,
        comparison_end=9000,
    )

    assert [(row["begin"], row["end"]) for row in selected] == [
        ("1800", "2700"),
        ("2700", "9000"),
    ]


@pytest.mark.parametrize(
    ("row", "comparison_begin", "comparison_end"),
    [
        (_row(900, 2100), 1800, 9000),
        (_row(8100, 9900), 1800, 9000),
        (_row(900, 9900), 1800, 9000),
    ],
)
def test_expected_bin_crossing_either_comparison_boundary_is_rejected(
    row: dict[str, str],
    comparison_begin: float,
    comparison_end: float,
) -> None:
    with pytest.raises(ValueError, match="crosses comparison window boundary"):
        _select_expected_rows_for_comparison(
            [row],
            comparison_begin=comparison_begin,
            comparison_end=comparison_end,
        )


def test_empty_comparison_selection_is_rejected() -> None:
    with pytest.raises(ValueError, match="no complete expected count bins"):
        _select_expected_rows_for_comparison(
            [_row(0, 900)],
            comparison_begin=1800,
            comparison_end=9000,
        )


def test_default_comparison_window_preserves_the_legacy_full_replay_window() -> None:
    comparison_begin, comparison_end = _resolve_comparison_window(
        comparison_begin=0,
        comparison_end=None,
        replay_end=7200,
        completion_end=10800,
    )

    assert (comparison_begin, comparison_end) == (0.0, 7200.0)
    rows = [_row(0, 900), _row(6300, 7200)]
    assert _select_expected_rows_for_comparison(
        rows,
        comparison_begin=comparison_begin,
        comparison_end=comparison_end,
    ) == rows


@pytest.mark.parametrize(
    ("comparison_begin", "comparison_end", "replay_end", "completion_end"),
    [
        (-1, 10, 10, 10),
        (10, 10, 10, 10),
        (0, 11, 10, 11),
        (0, 10, 11, 10),
    ],
)
def test_comparison_window_order_is_strict(
    comparison_begin: float,
    comparison_end: float,
    replay_end: float,
    completion_end: float,
) -> None:
    with pytest.raises(ValueError, match="0 <= comparison_begin"):
        _resolve_comparison_window(
            comparison_begin=comparison_begin,
            comparison_end=comparison_end,
            replay_end=replay_end,
            completion_end=completion_end,
        )


def test_replay_tool_wrapper_forwards_comparison_window(monkeypatch, tmp_path: Path) -> None:
    inputs = {}
    for field_name in (
        "net_file",
        "route_file",
        "e1_additional_file",
        "e2_additional_file",
        "tls_events_csv",
        "expected_counts_csv",
    ):
        path = tmp_path / field_name
        path.write_text("input", encoding="utf-8")
        inputs[field_name] = str(path)
    captured: dict[str, object] = {}

    def fake_replay(**kwargs):
        captured.update(kwargs)
        return {"status": "pass"}

    monkeypatch.setattr(digital_twin_tools, "run_tls_detector_replay", fake_replay)

    report = digital_twin_tools.sumo_digital_twin_replay_validate(
        **inputs,
        output_dir=str(tmp_path / "output"),
        replay_end=9000,
        completion_end=10800,
        comparison_begin=1800,
        comparison_end=9000,
    )

    assert report["status"] == "pass"
    assert captured["comparison_begin"] == 1800
    assert captured["comparison_end"] == 9000


class _FakeTrafficLight:
    def getProgram(self, _tls_id: str) -> str:
        return "original"

    def getRedYellowGreenState(self, _tls_id: str) -> str:
        return "r"

    def setRedYellowGreenState(self, _tls_id: str, _state: str) -> None:
        return None

    def setProgram(self, _tls_id: str, _program_id: str) -> None:
        return None


class _FakeSimulation:
    def __init__(self) -> None:
        self.time = 0.0

    def getTime(self) -> float:
        return self.time


class _FakeTraci:
    def __init__(self) -> None:
        self.trafficlight = _FakeTrafficLight()
        self.simulation = _FakeSimulation()

    def start(self, _command: list[str]) -> None:
        return None

    def simulationStep(self) -> None:
        self.simulation.time += 1.0

    def close(self) -> None:
        return None


class _Inspection:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return self.payload


def test_replay_completion_gate_requires_complete_clean_vehicle_accounting() -> None:
    complete = {
        "valid_xml": True,
        "loaded": 2,
        "inserted": 2,
        "arrived": 2,
        "running": 0,
        "waiting": 0,
        "teleports": 0,
        "collisions": 0,
        "discarded": 0,
    }

    assert tls_replay._summary_is_complete(complete) is True
    for field in complete:
        incomplete = dict(complete)
        incomplete.pop(field)
        assert tls_replay._summary_is_complete(incomplete) is False
    for field in ("running", "waiting", "teleports", "collisions", "discarded"):
        incomplete = {**complete, field: 1}
        assert tls_replay._summary_is_complete(incomplete) is False
    assert tls_replay._summary_is_complete({**complete, "inserted": 1}) is False
    assert tls_replay._summary_is_complete({**complete, "arrived": 1}) is False


def test_replay_audits_only_selected_bins_and_records_window_in_manifests(
    monkeypatch,
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "net.xml"
    net_file.write_text(
        """<net>
        <edge id="in"><lane id="in_0" index="0" allow="passenger" length="100"/></edge>
        <edge id="out"><lane id="out_0" index="0" allow="passenger" length="100"/></edge>
        <connection from="in" to="out" fromLane="0" toLane="0" tl="tls0" linkIndex="0"/>
        </net>""",
        encoding="utf-8",
    )
    route_file = tmp_path / "routes.xml"
    route_file.write_text("<routes/>", encoding="utf-8")
    e1_file = tmp_path / "e1.add.xml"
    e1_file.write_text(
        '<additional><inductionLoop id="d0" lane="in_0" pos="10" period="1" file="old.xml"/></additional>',
        encoding="utf-8",
    )
    e2_file = tmp_path / "e2.add.xml"
    e2_file.write_text(
        '<additional><laneAreaDetector id="q0" lane="in_0" pos="10" length="20" period="1" '
        'file="old.xml"/></additional>',
        encoding="utf-8",
    )
    events_file = tmp_path / "events.csv"
    events_file.write_text(
        "simulation_time,sumo_tls_id,sumo_link_index,sumo_state,source_state\n"
        "0,tls0,0,r,1\n",
        encoding="utf-8",
    )
    expected_file = tmp_path / "expected.csv"
    expected_file.write_text(
        "detector_id,edge_id,begin,end,expected_total\n"
        "d0,in,0,1,10\n"
        "d0,in,1,5,20\n"
        "d0,in,5,9,30\n",
        encoding="utf-8",
    )
    captured_rows: list[dict[str, str]] = []

    def fake_audit(rows, _detector_xml, *, count_attribute):
        assert count_attribute == "nVehContrib"
        captured_rows.extend(rows)
        return [
            {
                "detector_id": row["detector_id"],
                "edge_id": row["edge_id"],
                "begin": row["begin"],
                "end": row["end"],
                "expected_total": int(row["expected_total"]),
                "measurement_attribute": count_attribute,
                "measured_nVehContrib": int(row["expected_total"]),
                "diff_contrib_minus_expected": 0,
                "measurement_status": "matched",
            }
            for row in rows
        ]

    monkeypatch.setattr(tls_replay, "audit_expected_to_e1_strict", fake_audit)
    monkeypatch.setattr(
        tls_replay,
        "inspect_summary",
        lambda _path: _Inspection(
            {
                "valid_xml": True,
                "loaded": 0,
                "inserted": 0,
                "arrived": 0,
                "running": 0,
                "waiting": 0,
                "teleports": 0,
                "collisions": 0,
                "discarded": 0,
            }
        ),
    )
    monkeypatch.setattr(tls_replay, "inspect_tripinfo", lambda _path: _Inspection({"valid_xml": True}))

    report = run_tls_detector_replay(
        net_file=net_file,
        route_file=route_file,
        e1_additional_file=e1_file,
        e2_additional_file=e2_file,
        tls_events_csv=events_file,
        expected_counts_csv=expected_file,
        output_dir=tmp_path / "output",
        prefix="comparison_window",
        replay_end=9,
        completion_end=10,
        comparison_begin=1,
        comparison_end=9,
        traci_api=_FakeTraci(),
    )

    assert [(row["begin"], row["end"]) for row in captured_rows] == [("1", "5"), ("5", "9")]
    assert report["comparison_window"] == {
        "begin": 1.0,
        "end": 9.0,
        "source_expected_row_count": 3,
        "selected_expected_row_count": 2,
    }
    assert report["status"] == "pass"
    assert Path(report["artifacts"]["e1_output"]).name == "comparison_window_e1_15min.xml"
    assert Path(report["artifacts"]["e2_output"]).name == "comparison_window_e2_15min.xml"
    command_manifest = json.loads(Path(report["artifacts"]["command_manifest"]).read_text(encoding="utf-8"))
    assert command_manifest["comparison_window"] == report["comparison_window"]
    validation_manifest = json.loads(Path(report["report_file"]).read_text(encoding="utf-8"))
    assert validation_manifest["comparison_window"] == report["comparison_window"]
