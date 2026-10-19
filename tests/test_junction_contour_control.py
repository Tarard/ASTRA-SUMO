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
from astra_sumo.core.junction_contour_control import build_identity_control, assess_identity_control, assess_requested_geometry, geometry_delta
from astra_sumo.core.network_edit_applicability import assess_edit_applicability, verify_network_edit, _pedestrian_facility_inventory


FIXTURES = Path(__file__).parent / "fixtures/most-contour-controls"
native = pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="SUMO required")


def _request(tmp_path, node, requested_shape=None):
    source = (FIXTURES / f"{node}.net.xml").resolve()
    root = ET.parse(source).getroot()
    junction = next(row for row in root.findall("junction") if row.get("id") == node)
    polygon = Polygon([tuple(map(float, point.split(",")[:2])) for point in junction.get("shape").split()])
    target = polygon.buffer(-0.01, join_style="mitre")
    operation = {"id": "contour", "operation": "set_junction_shape", "status": "accepted",
        "params": {"node_id": node, "shape": requested_shape or [list(point) for point in target.exterior.coords[:-1]]},
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
    assert result["contour_identity_control"]["reason"] == "custom_contour_recuts_source_geometry"
    if node == "132844":
        assert result["status"] == "blocked"
        assert result["reason"] == "candidate_newly_invalid_walking_areas"
        assert result["checks"]["walking_area_validity"] == "blocked"
        assert result["requested_walking_area_quality"]["newly_invalid_area_ids"] == [":132844_w1_0"]
        missing_control = assess_edit_applicability(ledger, candidate_net_file=candidate)
        assert missing_control["status"] == "blocked"
        assert missing_control["contour_identity_control"]["reason"] == "contour_identity_control_missing"
        assert missing_control["checks"]["walking_area_validity"] == "blocked"
        assert not any(missing_control["pedestrian_facility_changes"].values())
    else:
        assert result["status"] == "review_required"
        assert result["reason"] == "custom_contour_recuts_source_geometry"
        assert not any(status == "blocked" for status in result["checks"].values())
    assert result["contour_geometry_changes"]["control_to_candidate"] is not None
    assert result["unqualified_source_pedestrian_checks"]["status"] == "not_applicable"
    wrong_control = {**control, "candidate_net_file": str(candidate)}
    rejected = assess_identity_control(source, before, node, wrong_control)
    assert rejected["status"] == "review_required"
    assert rejected["reason"] == "contour_identity_control_unverified"
    assert file_sha256(source) == ledger["source_sha256"]
    if node == "132844":
        changed = ET.parse(candidate)
        edge = next(edge for edge in changed.getroot().findall("edge") if edge.get("function") == "walkingarea")
        old_id, new_id = edge.get("id"), ":132844_w999999"
        edge.set("id", new_id)
        for connection in changed.getroot().findall("connection"):
            for key in ("from", "to"):
                if connection.get(key) == old_id:
                    connection.set(key, new_id)
        renamed = candidate.with_name("unqualified-control-facility-change.net.xml")
        changed.write(renamed, encoding="utf-8")
        for reference in (control, None):
            structural = assess_edit_applicability(ledger, candidate_net_file=renamed, identity_control_report=reference)
            assert structural["status"] == "blocked"
            assert structural["pedestrian_structure_reference"] == "immutable_source"
            assert structural["checks"]["all_class_explicit_boundary_paths"] == "pass"
            assert structural["checks"]["pedestrian_facilities"] == "blocked"
            assert structural["pedestrian_facility_changes"]["missing_ids"] == [old_id]
            assert structural["pedestrian_facility_changes"]["added_ids"] == [new_id]
    changed = ET.parse(candidate)
    lane = next(edge.find("lane") for edge in changed.getroot().findall("edge") if not edge.get("function"))
    lane.set("speed", str(float(lane.get("speed")) + 1))
    changed.write(candidate, encoding="utf-8")
    unsafe = assess_edit_applicability(ledger, candidate_net_file=candidate, identity_control_report=control)
    assert unsafe["status"] == "blocked"
    assert unsafe["checks"]["boundary_lane_permissions_width_speed"] == "blocked"


def test_crossed_road_order_does_not_change_facility_structure():
    root = ET.parse(FIXTURES / "132981.net.xml").getroot()
    before = _pedestrian_facility_inventory(root)
    crossing = next(edge for edge in root.findall("edge") if edge.get("function") == "crossing")
    crossing.set("crossingEdges", " ".join(reversed(crossing.get("crossingEdges").split())))
    assert _pedestrian_facility_inventory(root) == before


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
    assert result["construction"]["contour_geometry_changes"]["source_to_candidate"]["max_absolute_declared_length_delta_m"] > 1


@native
def test_release_preserves_review_and_marks_unexecuted_checks(tmp_path):
    _request(tmp_path, "151337")
    result = verify_network_edit(ledger_file=tmp_path / "ledger/corridor_edit_ledger.json", output_dir=tmp_path / "release",
        netconvert_binary=checkBinary("netconvert"), mode="release")
    assert result["applicability"]["status"] == "review_required"
    assert result["status"] == "review_required"
    assert result["candidate_gates"]["gates"]["sumo_load"]["execution_status"] == "not_run"
    assert result["whole_network_review"]["status"] == "review_required"
    assert result["whole_network_review"]["execution_status"] == "not_run"
    assert result["candidate_gates"]["gates"]["sumo_load"]["status"] != "blocked"


@native
def test_stable_native_pedestrian_control_is_the_geometry_reference(tmp_path):
    records = json.loads((FIXTURES / "provenance-v2.json").read_text(encoding="utf-8"))["fixtures"]
    shape = next(row["requested_shape"] for row in records if row["node_id"] == "132981")
    source, before, ledger, candidate = _request(tmp_path, "132981", shape)
    control = build_identity_control(ledger, output_dir=tmp_path, netconvert_binary=checkBinary("netconvert"), command_runner=run_command)
    assert assess_identity_control(source, before, "132981", control)["status"] == "pass"
    result = assess_edit_applicability(ledger, candidate_net_file=candidate, identity_control_report=control)
    assert result["status"] == "pass", result
    assert result["pedestrian_geometry_reference"] == "validated_native_identity_control"
    assert result["walking_area_validity_reference"] == "immutable_source"
    changed = ET.parse(candidate)
    lane = next(edge.find("lane") for edge in changed.getroot().findall("edge")
                if not edge.get("function") and edge.find("lane").get("allow") == "pedestrian")
    points = lane.get("shape").split()
    a, b = [tuple(map(float, point.split(","))) for point in points[:2]]
    points.insert(1, f"{(a[0]+b[0])/2+.001},{(a[1]+b[1])/2+.001}")
    lane.set("shape", " ".join(points))
    modified = candidate.with_name("pedestrian-change.net.xml")
    changed.write(modified, encoding="utf-8")
    unsupported = assess_edit_applicability(ledger, candidate_net_file=modified, identity_control_report=control)
    assert unsupported["status"] == "review_required", unsupported
    assert unsupported["reason"] == "pedestrian_layout_change_requires_review"
    changed = ET.parse(candidate)
    lane = next(edge.find("lane") for edge in changed.getroot().findall("edge") if edge.get("function") == "walkingarea"
                and Polygon([tuple(map(float, point.split(",")[:2])) for point in edge.find("lane").get("shape").split()]).is_valid)
    x, y = map(float, lane.get("shape").split()[0].split(",")[:2])
    lane.set("shape", f"{x},{y} {x+1},{y+1} {x+1},{y} {x},{y+1}")
    modified = candidate.with_name("invalid-walking-area.net.xml")
    changed.write(modified, encoding="utf-8")
    invalid = assess_edit_applicability(ledger, candidate_net_file=modified, identity_control_report=control)
    assert invalid["status"] == "blocked"
    assert invalid["checks"]["walking_area_validity"] == "blocked"
    changed = ET.parse(candidate)
    edge = next(edge for edge in changed.getroot().findall("edge") if edge.get("function") == "walkingarea")
    old_id, new_id = edge.get("id"), ":132981_w999999"
    assert changed.getroot().find(f"edge[@id='{new_id}']") is None
    edge.set("id", new_id)
    for connection in changed.getroot().findall("connection"):
        for key in ("from", "to"):
            if connection.get(key) == old_id:
                connection.set(key, new_id)
    modified = candidate.with_name("renamed-walking-area.net.xml")
    changed.write(modified, encoding="utf-8")
    structural = assess_edit_applicability(ledger, candidate_net_file=modified, identity_control_report=control)
    assert structural["status"] == "blocked"
    assert structural["checks"]["protected_counts"] == structural["checks"]["all_class_explicit_boundary_paths"] == "pass"
    assert structural["checks"]["pedestrian_facilities"] == "blocked"
    assert structural["pedestrian_facility_changes"]["missing_ids"] == [old_id]
    assert structural["pedestrian_facility_changes"]["added_ids"] == [new_id]
    changed = ET.parse(candidate)
    crossings = [edge for edge in changed.getroot().findall("edge") if edge.get("function") == "crossing"]
    first, second = crossings[:2]
    first_roads, second_roads = first.get("crossingEdges"), second.get("crossingEdges")
    first.set("crossingEdges", second_roads)
    second.set("crossingEdges", first_roads)
    modified = candidate.with_name("reassigned-crossings.net.xml")
    changed.write(modified, encoding="utf-8")
    reassigned = assess_edit_applicability(ledger, candidate_net_file=modified, identity_control_report=control)
    assert reassigned["checks"]["protected_counts"] == reassigned["checks"]["all_class_explicit_boundary_paths"] == "pass"
    assert reassigned["checks"]["pedestrian_facilities"] == "blocked"
    assert reassigned["pedestrian_facility_changes"]["changed_structure_ids"] == sorted([first.get("id"), second.get("id")])
    changed = ET.parse(candidate)
    walking = {edge.get("id") for edge in changed.getroot().findall("edge") if edge.get("function") == "walkingarea"}
    arrivals = [row for row in changed.getroot().findall("connection") if row.get("to") in walking
                and not row.get("from", "").startswith(":")]
    first, second = arrivals[:2]
    first_to, second_to = first.get("to"), second.get("to")
    first.set("to", second_to)
    second.set("to", first_to)
    modified = candidate.with_name("reassigned-walking-neighbors.net.xml")
    changed.write(modified, encoding="utf-8")
    reassigned = assess_edit_applicability(ledger, candidate_net_file=modified, identity_control_report=control)
    assert reassigned["checks"]["protected_counts"] == reassigned["checks"]["all_class_explicit_boundary_paths"] == "pass"
    assert reassigned["checks"]["pedestrian_facilities"] == "blocked"
    assert reassigned["pedestrian_facility_changes"]["changed_structure_ids"] == sorted([first_to, second_to])


@native
def test_requested_contour_effect_is_checked_after_a_stable_control(tmp_path):
    records = json.loads((FIXTURES / "provenance-v2.json").read_text(encoding="utf-8"))["fixtures"]
    shape = next(row["requested_shape"] for row in records if row["node_id"] == "133196")
    source, before, ledger, candidate = _request(tmp_path, "133196", shape)
    control = build_identity_control(ledger, output_dir=tmp_path, netconvert_binary=checkBinary("netconvert"), command_runner=run_command)
    assert assess_identity_control(source, before, "133196", control)["status"] == "pass"
    result = assess_edit_applicability(ledger, candidate_net_file=candidate, identity_control_report=control)
    observed = result["requested_contour_geometry"]
    assert observed["geometry"]["max_endpoint_shift_xy_m"] > 10
    assert observed["status"] == "review_required"
    assert observed["oversized_lane_changes"] and observed["boundary_failures"]


@native
def test_unavailable_identity_build_remains_review_required(tmp_path, monkeypatch):
    from astra_sumo.core import junction_contour_control
    _request(tmp_path, "151337")
    def unavailable(*args, **kwargs):
        raise OSError("control storage unavailable")
    monkeypatch.setattr(junction_contour_control, "build_identity_control", unavailable)
    result = verify_network_edit(ledger_file=tmp_path / "ledger/corridor_edit_ledger.json", output_dir=tmp_path / "release",
        netconvert_binary=checkBinary("netconvert"), mode="release")
    assert result["status"] == "review_required"
    assert result["applicability"]["reason"] == "contour_identity_control_unavailable"
    assert "control storage unavailable" in result["applicability"]["contour_identity_control"]["build_error"]


@native
def test_real_native_walking_area_fold_is_blocked_after_geometry_screen_passes(tmp_path):
    record = json.loads((FIXTURES / "provenance-v3.json").read_text(encoding="utf-8"))["fixtures"][0]
    source, before, ledger, candidate = _request(tmp_path, "140489", record["requested_shape"])
    control = build_identity_control(ledger, output_dir=tmp_path, netconvert_binary=checkBinary("netconvert"), command_runner=run_command)
    assert assess_identity_control(source, before, "140489", control)["status"] == "pass"
    result = assess_edit_applicability(ledger, candidate_net_file=candidate, identity_control_report=control)
    assert result["checks"]["requested_contour_geometry"] == "pass"
    assert result["checks"]["walking_area_validity"] == "blocked"
    assert result["requested_walking_area_quality"]["newly_invalid_area_ids"] == [":140489_w0_0"]
    assert result["status"] == "blocked"
    assert result["reason"] == "candidate_newly_invalid_walking_areas"


def test_requested_length_budget_respects_spatial_grade_and_stop_offset():
    control = ET.fromstring('<net><edge id="road" from="a" to="j"><lane id="road_0" length="12" endOffset="2" shape="0,0,0 8,0,8"/></edge>'
        '<junction id="j" type="priority" x="10" y="0" shape="10,-2 12,-2 12,2 10,2"/></net>')
    candidate = ET.fromstring(ET.tostring(control))
    candidate.find("junction").set("shape", "10.5,-2 12,-2 12,2 10.5,2")
    candidate.find("edge/lane").set("shape", "0,0,0 8.5,0,8.5")
    candidate.find("edge/lane").set("length", "12.7")
    result = assess_requested_geometry(control, candidate, "j")
    assert result["status"] == "pass"
    assert result["logical_length_budgets_m"]["road"] > 1.4
    assert not result["boundary_failures"]
    candidate.find("edge/lane").set("endOffset", "nan")
    unknown = assess_requested_geometry(control, candidate, "j")
    assert unknown["status"] == "review_required"
    assert unknown["unmeasured_input_lane_ids"] == ["road_0"]
