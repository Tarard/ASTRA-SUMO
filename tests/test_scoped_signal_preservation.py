from copy import deepcopy
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core import command_runner
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.corridor_edit_ledger import materialize_corridor_edit_variant
from astra_sumo.core.network_edit_operations import explore_network_edit
from astra_sumo.core.network_edit_operations import _retained_signal_programs
from test_network_edit_operations import _source


def _signals():
    return ET.fromstring('<net><edge id="in"><lane id="in_0"/><lane id="in_1"/></edge>'
        '<edge id="out"><lane id="out_0"/></edge>'
        '<tlLogic id="shared" programID="0" type="static" offset="0">'
        '<phase duration="10" state="Gr"/><phase duration="5" state="rG"/></tlLogic>'
        '<connection from="in" fromLane="0" to="out" toLane="0" tl="shared" linkIndex="0"/>'
        '<connection from="in" fromLane="1" to="out" toLane="0" tl="shared" linkIndex="1"/></net>')


@pytest.mark.parametrize("change", ["type", "offset", "duration", "state", "phase_count", "missing_program", "missing_connection"])
def test_retained_signal_comparison_detects_timing_and_binding_changes(change):
    before = _signals()
    after = deepcopy(before)
    logic = after.find('tlLogic')
    if change in {"type", "offset"}:
        logic.set(change, "actuated" if change == "type" else "3")
    elif change in {"duration", "state"}:
        logic.find('phase').set(change, "11" if change == "duration" else "rG")
    elif change == "phase_count":
        logic.remove(logic.findall('phase')[-1])
    elif change == "missing_program":
        after.remove(logic)
    else:
        after.remove(after.find('connection'))
    result = _retained_signal_programs(before, after, {"operation": "set_lane_width", "params": {}}, {"shared"})
    assert result["status"] == "fail" and result["differences"]


def test_retained_signal_comparison_uses_numeric_time_values():
    before = _signals()
    after = deepcopy(before)
    after.find('tlLogic').set('offset', '0.00')
    after.find('tlLogic/phase').set('duration', '10.000')
    result = _retained_signal_programs(before, after, {"operation": "set_lane_width", "params": {}}, {"shared"})
    assert result["status"] == "pass" and result["checked_connection_count"] == 2


@pytest.mark.parametrize("layout", ["add_right", "remove_right"])
def test_retained_signal_comparison_follows_declared_lane_remapping(layout):
    before = _signals()
    after = deepcopy(before)
    if layout == "add_right":
        params = {"edge_id": "in", "num_lanes": 3, "add_side": "right"}
        for row in after.findall('connection'):
            row.set('fromLane', str(int(row.get('fromLane'))+1))
    else:
        params = {"edge_id": "in", "num_lanes": 1, "remove_index": 0}
        after.remove(after.find('connection'))
        after.find('connection').set('fromLane', '0')
        after.find('tlLogic/phase').set('state', 'yr')
    result = _retained_signal_programs(before, after, {"operation": "set_lane_count", "params": params}, {"shared"})
    assert result["status"] == "pass"
    assert result["declared_removed_connection_count"] == (1 if layout == "remove_right" else 0)


def test_retained_signal_comparison_defers_explicit_control_changes():
    result = _retained_signal_programs(_signals(), _signals(),
        {"operation": "join_junctions", "params": {"signal_policy": "rebuild_diagnostic"}}, {"shared"})
    assert result["status"] == "not_applicable"


def test_retained_signal_comparison_respects_explicit_connection_removal():
    before = _signals()
    after = deepcopy(before)
    after.remove(after.find('connection'))
    after.find('tlLogic/phase').set('state', 'yr')
    operation = {"operation": "set_lane_count", "params": {"edge_id": "in", "num_lanes": 2,
        "connections": [{"from": "in", "fromLane": 1, "to": "out", "toLane": 0}]}}
    result = _retained_signal_programs(before, after, operation, {"shared"})
    assert result["status"] == "pass"
    assert result["declared_removed_connection_count"] == 1
    assert result["checked_connection_count"] == 1


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
@pytest.mark.parametrize("mode", ["explore", "release"])
def test_unrequested_signal_timing_change_warns_in_explore_and_fails_release(tmp_path, monkeypatch, mode):
    source = _source(tmp_path, "relocate_tls_control")
    digest = file_sha256(source)
    original_run = command_runner.run_command

    def corrupt_program(command, **kwargs):
        result = original_run(command, **kwargs)
        if Path(command[0]).stem.lower() == "netconvert" and result.status == "pass":
            output = Path(command[command.index("--output-file")+1])
            tree = ET.parse(output)
            phase = tree.getroot().find("tlLogic/phase")
            assert phase is not None
            phase.set("duration", str(float(phase.get("duration"))+1))
            tree.write(output, encoding="utf-8", xml_declaration=True)
        return result

    operation = {"id": "width", "operation": "set_lane_width", "status": "accepted",
        "params": {"lane_id": "middle_0", "width_m": 3.4}, "rationale": "Signal timing preservation regression.",
        "evidence": [{"path": str(source), "sha256": digest}], "rollback": {"action": "discard_candidate"}}
    ledger = {"source_net_file": str(source), "source_sha256": digest, "operations": [operation]}
    if mode == "explore":
        import json
        path = tmp_path / "ledger.json"
        path.write_text(json.dumps(ledger), encoding="utf-8")
        monkeypatch.setattr(command_runner, "run_command", corrupt_program)
        report = explore_network_edit(ledger_file=path, output_dir=tmp_path / "edit", netconvert_binary=shutil.which("netconvert"))
        assert report["status"] == "measured", report
        checks = report["construction"]
    else:
        report = materialize_corridor_edit_variant(ledger=ledger, output_dir=tmp_path / "edit",
            netconvert_binary=shutil.which("netconvert"), command_runner=corrupt_program)
        assert report["status"] == "blocked", report
        checks = report["semantic_validation"]
    assert checks["release_checks_passed"] is False
    assert checks["signal_program_preservation"]["status"] == "fail"
    assert any("signal" in warning.lower() for warning in checks["warnings"])
    assert file_sha256(source) == digest
