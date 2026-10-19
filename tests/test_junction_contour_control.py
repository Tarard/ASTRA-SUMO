import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest
from shapely.geometry import Polygon
from sumolib import checkBinary

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.corridor_edit_ledger import build_corridor_edit_ledger, materialize_corridor_edit_variant
from astra_sumo.core.junction_contour_control import build_identity_control, assess_identity_control, geometry_delta
from astra_sumo.core.network_edit_applicability import assess_edit_applicability, verify_network_edit


FIXTURES = Path(__file__).parent / "fixtures/most-contour-controls"
native = pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="SUMO required")


def _request(tmp_path, node):
    source = (FIXTURES / f"{node}.net.xml").resolve()
    root = ET.parse(source).getroot()
    junction = next(row for row in root.findall("junction") if row.get("id") == node)
    polygon = Polygon([tuple(map(float, point.split(",")[:2])) for point in junction.get("shape").split()])
    target = polygon.buffer(-0.01, join_style="mitre")
    operation = {"id": "contour", "operation": "set_junction_shape", "status": "accepted",
        "params": {"node_id": node, "shape": [list(point) for point in target.exterior.coords[:-1]]},
        "rationale": "Published-model native conversion regression. No field claim.",
        "evidence": [{"path": str(source), "sha256": file_sha256(source)}], "rollback": {"action": "discard_candidate"}}
    ledger_report = build_corridor_edit_ledger(net_file=source, output_dir=tmp_path / "ledger",
        operations=[operation], include_auto_proposals=False)
    ledger_file = Path(ledger_report["ledger_file"])
    ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
    built = materialize_corridor_edit_variant(ledger_file=ledger_file, output_dir=tmp_path / "requested",
        netconvert_binary=checkBinary("netconvert"))
    assert built["status"] == "pass", built
    return source, root, ledger, Path(built["candidate_net_file"])


@native
def test_vehicle_only_recut_cannot_pass_without_identity_control(tmp_path):
    source, before, ledger, candidate = _request(tmp_path, "151337")
    delta = geometry_delta(before, ET.parse(candidate).getroot(), "151337")
    assert delta["max_absolute_declared_length_delta_m"] > 1
    result = assess_edit_applicability(ledger, candidate_net_file=candidate)
    assert result["status"] == "review_required"
    assert result["reason"] == "contour_identity_control_missing"
    assert file_sha256(source) == ledger["source_sha256"]


@native
@pytest.mark.parametrize("node", ["132844", "151337"])
def test_native_identity_control_measures_source_recut_before_acceptance(tmp_path, node):
    source, before, ledger, candidate = _request(tmp_path, node)
    control = build_identity_control(ledger, output_dir=tmp_path, netconvert_binary=checkBinary("netconvert"), command_runner=run_command)
    assert control["status"] == "pass", control
    observed = assess_identity_control(source, before, node, control)
    assert observed["materialization_evidence"]["status"] == "pass"
    assert observed["same_source_polygon"] and observed["custom_shape_declared"]
    assert observed["geometry"]["max_absolute_declared_length_delta_m"] > 0.5
    result = assess_edit_applicability(ledger, candidate_net_file=candidate, identity_control_report=control)
    assert result["status"] != "pass"
    assert result["reason"] == "custom_contour_recuts_source_geometry"
    assert result["contour_geometry_changes"]["control_to_candidate"] is not None
    wrong_control = {**control, "candidate_net_file": str(candidate)}
    rejected = assess_identity_control(source, before, node, wrong_control)
    assert rejected["status"] == "review_required"
    assert rejected["reason"] == "contour_identity_control_unverified"
    assert file_sha256(source) == ledger["source_sha256"]


def test_geometry_delta_keeps_spatial_and_declared_length_changes_separate():
    before = ET.fromstring('<net><edge id="road" from="a" to="j"><lane id="road_0" length="10" shape="0,0,2 10,0,2"/></edge>'
                          '<junction id="j" type="priority" x="10" y="0"/></net>')
    after = ET.fromstring(ET.tostring(before))
    after.find("edge/lane").set("shape", "0,0,2 10,0,3")
    after.find("edge/lane").set("length", "11")
    result = geometry_delta(before, after, "j")
    assert result["max_endpoint_shift_xy_m"] == result["max_shape_hausdorff_xy_m"] == 0
    assert result["max_endpoint_shift_xyz_m"] == 1
    assert result["changes"][0]["declared_length_delta_m"] == 1
    after.find("edge/lane").set("length", "nan")
    unknown = geometry_delta(before, after, "j")
    assert unknown["compared_lane_count"] == 0
    assert unknown["unmeasured_lane_ids"] == ["road_0"]


@native
def test_exploration_still_measures_a_recut_without_an_identity_gate(tmp_path):
    _request(tmp_path, "151337")
    result = verify_network_edit(ledger_file=tmp_path / "ledger/corridor_edit_ledger.json", output_dir=tmp_path / "daily",
        netconvert_binary=checkBinary("netconvert"), mode="explore")
    assert result["status"] == "measured", result
    assert result["release_gates"] == "not_run"
    assert "candidate_gates" not in result
    assert result["construction"]["requested_effect_applied"]
