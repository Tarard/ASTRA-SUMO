from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.network_edit_operations import write_scoped_edit_plan, check_scoped_edit
from astra_sumo.core.local_network_conversion import (
    loaded_turn_permissions, with_loaded_turn_permissions, validate_local_conversion, run_scoped_native_conversion,
)
from test_traffic_effect_review import _native_network, _native_access


@pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="SUMO required")
@pytest.mark.parametrize("full_input", [False, True])
@pytest.mark.parametrize("directive,restricted,damage", [(None, True, False), ("all", True, False), ("bus", False, False), (None, True, True)])
def test_width_edit_keeps_loaded_turn_access_and_survives_reimport(tmp_path, monkeypatch, full_input, directive, restricted, damage):
    root = _native_network(tmp_path)
    turn = root.find("connection[@from='in'][@to='out']")
    via = root.find(f"edge/lane[@id='{turn.get('via')}']")
    if restricted:
        via.set("allow", "public_transport")
    if directive:
        turn.set("allow", directive)
    source = tmp_path / "source.net.xml"
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)
    digest = file_sha256(source)
    before = _native_access(source, "passenger")
    assert before["admitted"] is not restricted
    if full_input:
        monkeypatch.setattr("astra_sumo.core.local_network_conversion.local_conversion_input",
                            lambda *args, **kwargs: (None, {"status": "full_network", "reason": "test_control"}))
    output = tmp_path / "edit"
    output.mkdir()
    candidate = output / "candidate.net.xml"
    operation = {"id": "width", "operation": "set_lane_width", "params": {"lane_id": "in_0", "width_m": 3.3}}
    plan = write_scoped_edit_plan(root=root, operation=operation, source_file=source, candidate_file=candidate,
        output_dir=output, prefix="width", netconvert_binary=shutil.which("netconvert"), mode="explore")
    result = run_command(plan["command"], cwd=output, timeout_seconds=30)
    assert result.returncode == 0, result.stderr
    if damage:
        changed = ET.parse(candidate)
        turn = changed.getroot().find("connection[@from='in'][@to='out']")
        lane = changed.getroot().find(f"edge/lane[@id='{turn.get('via')}']")
        lane.set("allow", "all")
        lane.attrib.pop("disallow", None)
        changed.write(candidate, encoding="utf-8", xml_declaration=True)
    checked = check_scoped_edit(plan=plan, candidate_file=candidate, output_dir=output,
        netconvert_binary=shutil.which("netconvert"), timeout_seconds=30, command_runner=run_command, mode="explore")
    assert _native_access(candidate, "passenger")["admitted"] == (before["admitted"] or damage)
    assert _native_access(candidate, "bus")["admitted"]
    assert checked["requested_effect_applied"] and checked["release_checks_passed"] is not damage, checked
    assert checked["other_connection_permissions_preserved"] is not damage
    assert file_sha256(source) == digest
    if damage:
        return
    assert validate_local_conversion(checked["local_conversion"], source) == Path(plan["local_conversion"]["input"]["path"])
    reimported = output / "reimported.net.xml"
    result = run_command([shutil.which("netconvert"), "-s", str(candidate), "-o", str(reimported)],
                         cwd=output, timeout_seconds=30)
    assert result.returncode == 0, result.stderr
    assert _native_access(reimported, "passenger")["admitted"] == before["admitted"]
    assert file_sha256(source) == digest


@pytest.mark.parametrize("version,attrs,expected", [
    ("1.20", {"allow": "public_transport", "disallow": "bus"}, {"bus"}),
    ("1.3", {"disallow": "rail_urban"}, {"bus", "passenger"}),
    ("1.20", {"disallow": "all"}, set()),
])
def test_preparation_follows_complete_internal_chains_without_changing_the_source(version, attrs, expected):
    root = ET.fromstring('''<net><edge id="in"><lane id="in_0" allow="bus passenger subway"/></edge>
      <edge id="out"><lane id="out_0"/></edge>
      <edge id=":first" function="internal"><lane id=":first_0"/></edge>
      <edge id=":second" function="internal"><lane id=":second_0"/></edge>
      <connection from="in" fromLane="0" to="out" toLane="0" via=":first_0"/>
      <connection from=":first" fromLane="0" to="out" toLane="0" via=":second_0" allow="truck"/>
      <connection from=":second" fromLane="0" to="out" toLane="0"/></net>''')
    root.set("version", version)
    root.find("edge[@id=':second']/lane").attrib.update(attrs)
    before = ET.tostring(root)
    prepared = with_loaded_turn_permissions(root)
    key = ("in", 0, "out", 0)
    assert loaded_turn_permissions(root)[key] == expected
    assert loaded_turn_permissions(prepared)[key] == expected
    assert ET.tostring(root) == before
    assert ET.tostring(with_loaded_turn_permissions(prepared)) == ET.tostring(prepared)


@pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="SUMO required")
@pytest.mark.parametrize("full_input", [False, True])
def test_shared_conversion_keeps_dormant_turn_restrictions(tmp_path, monkeypatch, full_input):
    root = _native_network(tmp_path)
    turn = root.find("connection[@from='in'][@to='out']")
    root.find(f"edge/lane[@id='{turn.get('via')}']").set("allow", "bus")
    source = tmp_path / "source.net.xml"
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)
    digest = file_sha256(source)
    if full_input:
        monkeypatch.setattr("astra_sumo.core.local_network_conversion.local_conversion_input",
                            lambda *args, **kwargs: (None, {"status": "full_network", "reason": "test_control"}))
    candidate = tmp_path / "candidate.net.xml"
    result, record = run_scoped_native_conversion(command=[shutil.which("netconvert"), "--sumo-net-file", str(source),
        "--offset.disable-normalization", "true", "--output-file", str(candidate)], mutable_nodes={"j"},
        mutable_edges={"in", "out", "branch"}, output_dir=tmp_path, prefix="native", timeout_seconds=30)
    assert result.returncode == 0, result.stderr
    assert record["preserve_loaded_turn_permissions"] is True
    assert not _native_access(candidate, "passenger")["admitted"]
    assert _native_access(candidate, "bus")["admitted"]
    assert file_sha256(source) == digest
