from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.network_edit_operations import write_scoped_edit_plan, check_scoped_edit
from astra_sumo.core.local_network_conversion import (
    loaded_turn_permissions, with_loaded_turn_permissions, validate_local_conversion, run_scoped_native_conversion,
    prepare_permission_preserving_input,
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


@pytest.mark.parametrize("step", ["normalize", "plain_export"])
def test_workflow_conversion_cannot_overwrite_its_source(tmp_path, step):
    from astra_sumo.core.osm_workflow_tail import _normalize_sumo_net, export_plain_net_for_teacher_guided_repair
    source = tmp_path / "export.nod.xml"
    original = b"<net/>"
    source.write_bytes(original)
    def cannot_run(*args, **kwargs):
        pytest.fail("A source collision must be detected before native conversion.")
    if step == "normalize":
        report = _normalize_sumo_net(net_file=source, output_file=source, output_dir=tmp_path,
            netconvert_binary="netconvert", timeout_seconds=30, command_runner=cannot_run)
    else:
        report = export_plain_net_for_teacher_guided_repair(net_file=source, output_dir=tmp_path,
            prefix="export", command_runner=cannot_run)
    assert report["status"] == "fail" and "source network" in report["error"]
    assert source.read_bytes() == original


@pytest.mark.parametrize("broken_reference", [False, True])
def test_permission_preparation_preserves_raw_source_and_reports_unavailable_references(tmp_path, broken_reference):
    source = tmp_path / "source.net.xml"
    original = (b'<net><edge id="in"><lane id="in_0"/></edge><edge id="out"><lane id="out_0"/></edge>'
        b'<connection from="in" fromLane="0" to="out" toLane="0" via=":missing"/></net>') if broken_reference else b"<net/>"
    source.write_bytes(original)
    prepared = tmp_path / "prepared.net.xml"
    report = prepare_permission_preserving_input(source, prepared)
    assert report["preparation"] == ("unavailable" if broken_reference else "unchanged")
    assert report["path"] == str(source) and report["sha256"] == file_sha256(source)
    assert ("warning" in report) is broken_reference
    assert source.read_bytes() == original and not prepared.exists()


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO required")
@pytest.mark.parametrize("step", ["normalize", "plain_export"])
def test_unavailable_permission_trace_is_visible_without_blocking_native_repair(tmp_path, monkeypatch, step):
    from astra_sumo.core import osm_workflow_tail as tail
    source = tmp_path / "source.net.xml"
    ET.ElementTree(_native_network(tmp_path)).write(source, encoding="utf-8", xml_declaration=True)
    def unavailable(source_file, prepared_file):
        return {"path": str(source_file), "sha256": file_sha256(source_file),
                "preparation": "unavailable", "warning": "Unknown internal lane."}
    monkeypatch.setattr(tail, "prepare_permission_preserving_input", unavailable)
    output = tmp_path / "conversion"
    if step == "normalize":
        report = tail._normalize_sumo_net(net_file=source, output_file=output / "candidate.net.xml", output_dir=output,
            netconvert_binary=shutil.which("netconvert"), timeout_seconds=30, command_runner=run_command)
    else:
        report = tail.export_plain_net_for_teacher_guided_repair(net_file=source, output_dir=output, prefix="export",
            netconvert_binary=shutil.which("netconvert"), timeout_seconds=30)
    assert report["status"] == "pass"
    assert report["warnings"] == ["Loaded turn permissions could not be traced. Unknown internal lane."]
    assert report["permission_input"]["preparation"] == "unavailable"


@pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="SUMO required")
@pytest.mark.parametrize("entrypoint", ["connection_repair", "road_geometry"])
@pytest.mark.parametrize("damage", [False, True])
def test_legacy_repair_entrypoints_keep_untouched_bus_only_turns(tmp_path, monkeypatch, entrypoint, damage):
    root = _native_network(tmp_path)
    turn = root.find("connection[@from='in'][@to='out']")
    root.find(f"edge/lane[@id='{turn.get('via')}']").set("allow", "bus")
    source = tmp_path / "source.net.xml"
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)
    digest = file_sha256(source)
    assert not _native_access(source, "passenger")["admitted"]
    output = tmp_path / "repair"
    def compile_case(command, **kwargs):
        result = run_command(command, **kwargs)
        if damage and "--output-file" in command and result.returncode == 0:
            artifact = Path(command[command.index("--output-file") + 1])
            changed = ET.parse(artifact)
            turn = changed.getroot().find("connection[@from='in'][@to='out']")
            lane = changed.getroot().find(f"edge/lane[@id='{turn.get('via')}']")
            lane.set("allow", "all")
            lane.attrib.pop("disallow", None)
            changed.write(artifact, encoding="utf-8", xml_declaration=True)
        return result
    if entrypoint == "connection_repair":
        from astra_sumo.core.hamburg_lane_connection_repair import build_hamburg_lane_connection_repair
        patch = tmp_path / "delete-branch.con.xml"
        patch.write_text('<connections><delete from="in" fromLane="0" to="branch" toLane="0"/></connections>', encoding="utf-8")
        report = build_hamburg_lane_connection_repair(source_net=source, output_dir=output,
            connection_patch_file=patch, expected_connection_patch_sha256=file_sha256(patch), timeout_seconds=30,
            command_runner=compile_case)
        candidate = Path(report["candidate_network"]["path"])
    else:
        from astra_sumo.core import hamburg_road_geometry as geometry
        monkeypatch.setattr(geometry, "run_command", compile_case)
        # The source-matching profile is tested separately. Exercise its native conversion with a fixed local shape.
        monkeypatch.setattr(geometry, "plan_hamburg_road_geometry", lambda network: {
            "status": "pass", "junction_id": "j", "edge_id": "out", "opposing_edge_id": "branch",
            "lane_shapes": {"0": geometry._points(network.find("edge[@id='out']/lane").get("shape"))}})
        report = geometry.build_hamburg_road_geometry_candidate(source_net=source, output_dir=output, timeout_seconds=30)
        candidate = output / "road-geometry.net.xml"
    assert _native_access(candidate, "passenger")["admitted"] is damage
    assert _native_access(candidate, "bus")["admitted"]
    assert file_sha256(source) == digest
    expected_statuses = {"blocked"} if damage else {"pass", "review_required"}
    assert report["status"] in expected_statuses, report
    assert file_sha256(Path(report["conversion_input"]["path"])) == report["conversion_input"]["sha256"]
    if entrypoint == "road_geometry":
        assert file_sha256(Path(report["reload_input"]["path"])) == report["reload_input"]["sha256"]


@pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="SUMO required")
@pytest.mark.parametrize("step", ["normalize", "plain_export"])
def test_workflow_normalization_and_plain_export_preserve_loaded_turn_access(tmp_path, step):
    from astra_sumo.core.osm_workflow_tail import _normalize_sumo_net, export_plain_net_for_teacher_guided_repair
    root = _native_network(tmp_path)
    turn = root.find("connection[@from='in'][@to='out']")
    root.find(f"edge/lane[@id='{turn.get('via')}']").set("allow", "bus")
    source = tmp_path / "source.net.xml"
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)
    digest = file_sha256(source)
    output = tmp_path / "conversion"
    candidate = output / "candidate.net.xml"
    if step == "normalize":
        report = _normalize_sumo_net(net_file=source, output_file=candidate, output_dir=output,
            netconvert_binary=shutil.which("netconvert"), timeout_seconds=30, command_runner=run_command)
    else:
        report = export_plain_net_for_teacher_guided_repair(net_file=source, output_dir=output, prefix="export",
            netconvert_binary=shutil.which("netconvert"), timeout_seconds=30)
        result = run_command([shutil.which("netconvert"), "-n", report["raw_node_file"],
            "-e", report["raw_edge_file"], "-x", report["raw_connection_file"],
            "--offset.disable-normalization", "true", "-o", str(candidate)], cwd=output, timeout_seconds=30)
        assert result.returncode == 0, result.stderr
    assert report["status"] == "pass", report
    assert not _native_access(candidate, "passenger")["admitted"]
    assert _native_access(candidate, "bus")["admitted"]
    assert file_sha256(source) == digest
    assert report["permission_input"]["source"]["sha256"] == digest
    changes = report["permission_input"]["reconciled_connections"]
    assert len(changes) == 1 and changes[0]["before"] == {} and changes[0]["after"] == {"allow": "bus"}
    assert changes[0]["loaded_classes"] == ["bus"]
    prepared = Path(report["permission_input"]["path"])
    prepared_digest = file_sha256(prepared)
    with pytest.raises(FileExistsError):
        prepare_permission_preserving_input(source, prepared)
    assert file_sha256(prepared) == prepared_digest and file_sha256(source) == digest
