from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.command_runner import CommandResult, run_command
from astra_sumo.core.junction_rebuild.geometry import reanchor_spatial_shape
from astra_sumo.core.junction_rebuild.native_path_repair import rebuild_custom_path_endpoints
from astra_sumo.core.network_edit_operations import write_scoped_edit_plan, check_scoped_edit


def test_spatial_reanchor_keeps_curvature_and_mixed_zero_elevation():
    assert reanchor_spatial_shape([(0, 0), (3, 1, 4), (6, 0, 8)], start=(0, 2), end=(6, 2, 8)) == [
        (0, 2, 0), (3, 3, 4), (6, 2, 8)]


def _case(tmp_path, elevation):
    if not shutil.which("netconvert") or not shutil.which("sumo"):
        pytest.skip("SUMO required")
    nodes, edges, base, source, patch = [tmp_path / name for name in ("nodes.xml", "edges.xml", "base.net.xml", "source.net.xml", "custom.con.xml")]
    nodes.write_text(f'''<nodes><node id="w" x="0" y="0" z="0"/><node id="j" x="50" y="0" z="{elevation}"/>
        <node id="e" x="100" y="0" z="{2*elevation}"/><node id="n" x="50" y="50" z="{elevation}"/></nodes>''', encoding="utf-8")
    edges.write_text('''<edges><edge id="in" from="w" to="j" numLanes="2"><lane index="0" allow="bicycle" width="1.25"/>
        <lane index="1" allow="passenger" width="3.2"/></edge>
        <edge id="out" from="j" to="e" numLanes="2"><lane index="0" allow="bicycle" width="1.25"/>
        <lane index="1" allow="passenger" width="3.2"/></edge><edge id="north" from="n" to="j"/>
        <edge id="south" from="j" to="n"/></edges>''', encoding="utf-8")
    executable = shutil.which("netconvert")
    result = subprocess.run([executable, "-n", str(nodes), "-e", str(edges), "--offset.disable-normalization", "true", "-o", str(base)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    root = ET.parse(base).getroot()
    def point(edge, at):
        lane = root.find(f"edge[@id='{edge}']/lane[@index='1']")
        value = tuple(map(float, lane.get("shape").split()[at].split(",")))
        return value + (0.0,) * (3 - len(value))
    a, b = point("in", -1), point("out", 0)
    curve = [a, tuple((a[i] + b[i]) / 2 + (0.5 if i == 1 else 0) for i in range(3)), b]
    text = " ".join(",".join(map(str, value)) for value in curve)
    patch.write_text(f'<connections><connection from="in" fromLane="1" to="out" toLane="1" shape="{text}"/></connections>', encoding="utf-8")
    result = subprocess.run([executable, "--sumo-net-file", str(base), "--connection-files", str(patch),
        "--offset.disable-normalization", "true", "-o", str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    output = tmp_path / "candidate"
    output.mkdir()
    candidate = output / "candidate.net.xml"
    plan = write_scoped_edit_plan(root=ET.parse(source).getroot(), operation={"id": "width", "operation": "set_lane_width",
        "params": {"lane_id": "in_0", "width_m": 2}}, source_file=source, candidate_file=candidate,
        output_dir=output, prefix="test", netconvert_binary=executable, mode="explore")
    result = run_command(plan["command"], cwd=output, timeout_seconds=30)
    assert result.returncode == 0, result.stderr
    # Seed the endpoint defect observed in Hamburg without depending on a native-version bug.
    tree = ET.parse(candidate)
    root = tree.getroot()
    connection = root.find("connection[@from='in'][@fromLane='1'][@to='out'][@toLane='1']")
    lane = root.find(f"edge/lane[@id='{connection.get('via')}']")
    values = [tuple(map(float, token.split(","))) for token in lane.get("shape").split()]
    start = (values[0][0], values[0][1] + .5, *values[0][2:])
    adjusted = reanchor_spatial_shape(values, start=start, end=values[-1])
    lane.set("shape", " ".join(",".join(map(str, value)) for value in adjusted))
    tree.write(candidate, encoding="utf-8", xml_declaration=True)
    return source, candidate, output, plan


@pytest.mark.parametrize("elevation", [0, 10])
def test_native_rebuild_repairs_a_custom_curve_endpoint_gap(tmp_path, elevation):
    source, candidate, output, plan = _case(tmp_path, elevation)
    digest = file_sha256(source)
    result = check_scoped_edit(plan=plan, candidate_file=candidate, output_dir=output,
        netconvert_binary=shutil.which("netconvert"), timeout_seconds=30, command_runner=run_command, mode="explore")
    assert result["status"] == "measured", result
    assert result["structural_failure_count"] == 0
    repair = result["native_path_repair"]
    assert repair["attempts"] and repair["remaining_endpoint_gap_count"] == 0
    assert all(row["result"]["returncode"] == 0 for row in repair["attempts"])
    assert result["local_conversion"]["native_rebuild_count"] == 1 + len(repair["attempts"])
    assert file_sha256(source) == digest
    changed = repair["attempts"][0]["changes"][0]
    assert len(changed["before_shape"]) >= 3
    assert len(changed["after_shape"]) == len(changed["before_shape"])
    if elevation:
        assert max(point[2] for point in changed["after_shape"]) > 5
    assert Path(repair["attempts"][0]["before"]["path"]).is_file()


def test_failed_native_path_rebuild_retains_the_original_bad_proposal_and_source(tmp_path):
    source, candidate, output, plan = _case(tmp_path, 0)
    original, candidate_digest = file_sha256(source), file_sha256(candidate)
    def fail(command, **kwargs):
        assert not candidate.exists()
        return CommandResult(command=command, cwd=str(kwargs["cwd"]), status="fail", returncode=1, error="test failure")
    result = rebuild_custom_path_endpoints(plan=plan, candidate_file=candidate, output_dir=output,
        command_runner=fail, timeout_seconds=30)
    assert result["attempts"][0]["result"]["returncode"] == 1
    assert result["remaining_endpoint_gap_count"] > 0
    assert file_sha256(source) == original
    assert file_sha256(candidate) == candidate_digest
