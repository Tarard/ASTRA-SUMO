import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from pyproj import Transformer

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.road_network import engineering_topology
from torii_sumo.road_network.engineering_topology import build_engineering_topology


def _case(tmp_path, osm_year=2013):
    osm = tmp_path / "source.osm.xml"
    osm.write_text('<osm version="0.6"><node id="1" lon="9.99" lat="53.54"/>'
                   '<node id="2" lon="9.991" lat="53.54"/><way id="99"><nd ref="1"/><nd ref="2"/>'
                   '<tag k="highway" v="residential"/><tag k="lanes" v="1"/>'
                   '<tag k="name" v="Auxiliary Street"/><tag k="maxspeed" v="30"/></way></osm>', encoding="utf-8")
    lanes = [{"width_m": 1.8, "allow": "bicycle", "evidence": {"measurement_basis_unknown": True}},
             {"width_m": 3.2, "allow": "passenger bus", "evidence": "plan lane annotation"}]
    topology = {
        "schema": "torii.engineering-topology/v1", "source_plan_sha256": "1" * 64, "crs": "EPSG:25832",
        "nodes": [{"id": "A", "x": 565000, "y": 5933000, "evidence": "plan"},
                  {"id": "J", "x": 565050, "y": 5933000, "type": "traffic_light", "evidence": "plan"},
                  {"id": "C", "x": 565100, "y": 5933000, "evidence": "plan"}],
        "edges": [{"id": "in", "from": "A", "to": "J", "lanes": lanes, "osm_way_id": "99", "evidence": "plan"},
                  {"id": "out", "from": "J", "to": "C", "lanes": lanes, "evidence": "plan"}],
        "connections": [{"from": "in", "to": "out", "fromLane": i, "toLane": i, "evidence": "plan arrow"} for i in (0, 1)],
        "assumptions": ["Widths are diagram estimates, not surveyed clear widths."],
        "unresolved": ["Actual field timing unavailable."],
    }
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(topology), encoding="utf-8")
    return path, topology, {"path": str(osm), "sha256": file_sha256(osm), "data_year": osm_year}


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_generated_turns_use_25_shape_points(tmp_path):
    path, data, osm = _case(tmp_path)
    data["nodes"][2].update(x=565050, y=5933050)
    _write(path, data)
    result = build_engineering_topology(topology_file=path, source_osm=osm,
                                        output_dir=tmp_path / "build", target_year=2013)
    assert result["status"] == "pass", result
    network = Path(result["artifacts"]["network"]["path"])
    root = ET.parse(network).getroot()
    turns = root.findall("connection[@from='in']")
    assert len(turns) == 2
    for turn in turns:
        lane = root.find(f"edge/lane[@id='{turn.get('via')}']")
        assert len(lane.get("shape").split()) == 25


def test_real_plan_two_lanes_including_bike_override_one_lane_osm(tmp_path):
    path, data, osm = _case(tmp_path)
    hashes = file_sha256(path), osm["sha256"]
    result = build_engineering_topology(topology_file=path, source_osm=osm, output_dir=tmp_path / "build", target_year=2013)
    assert result["status"] == "pass", result
    assert result["decision"] == "review_required"
    assert result["load_result"]["returncode"] == 0
    assert result["source_plan_sha256"] == data["source_plan_sha256"]
    assert result["topology_sha256"] == hashes[0]
    assert all(value == "pass" for value in result["construction_checks"].values())
    root = ET.parse(result["artifacts"]["network"]["path"]).getroot()
    for edge_id in ("in", "out"):
        lanes = root.find(f"edge[@id='{edge_id}']").findall("lane")
        assert len(lanes) == 2
        assert lanes[0].get("allow") == "bicycle"
        assert float(lanes[0].get("width")) == pytest.approx(1.8)
    assert root.find("edge[@id='in']").get("name") == "Auxiliary Street"
    assert len(root.findall("connection[@from='in']")) == 2
    assert len(root.find("tlLogic[@id='J']/phase").get("state")) == 2
    assert result["actual_signal_timing_verified"] is False
    assert any(row.get("source_field") == "topology.assumptions" for row in result["unknowns"])
    assert any(row.get("source_field") == "topology.unresolved" for row in result["unknowns"])
    node = root.find("junction[@id='J']")
    offset = [float(v) for v in root.find("location").get("netOffset").split(",")]
    assert float(node.get("x")) - offset[0] == pytest.approx(565050, abs=0.01)
    assert float(node.get("y")) - offset[1] == pytest.approx(5933000, abs=0.01)
    assert root.find("location").get("projParameter") not in (None, "!", "-")
    geographic_bounds = [float(v) for v in root.find("location").get("origBoundary").split(",")]
    assert all(-180 <= geographic_bounds[i] <= 180 for i in (0, 2))
    assert all(-90 <= geographic_bounds[i] <= 90 for i in (1, 3))
    assert (file_sha256(path), file_sha256(Path(osm["path"]))) == hashes


@pytest.mark.parametrize("osm_year, explicit", [(2013, False), (2012, True)])
def test_only_dated_or_explicitly_valid_osm_fills_missing_plan_node_position(tmp_path, osm_year, explicit):
    path, data, osm = _case(tmp_path, osm_year)
    data["nodes"][0] = {"id": "A", "osm_node_id": "1", "evidence": "plan-to-OSM node match"}
    if explicit:
        data["osm_geometry_valid_for_target_year"] = 2013
    _write(path, data)
    result = build_engineering_topology(topology_file=path, source_osm=osm, output_dir=tmp_path / "build", target_year=2013)
    assert result["status"] == "pass", result
    assert any(row["object_id"] == "A" and row["field"] == "position" for row in result["auxiliary_fills"])
    root = ET.parse(result["artifacts"]["network"]["path"]).getroot()
    node = root.find("junction[@id='A']")
    offset = [float(v) for v in root.find("location").get("netOffset").split(",")]
    expected = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True).transform(9.99, 53.54)
    assert [float(node.get("x")) - offset[0], float(node.get("y")) - offset[1]] == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("osm_year", [2024, None])
def test_other_year_osm_does_not_silently_supply_geometry(tmp_path, osm_year):
    path, data, osm = _case(tmp_path, osm_year)
    data["nodes"][0] = {"id": "A", "osm_node_id": "1", "evidence": "matching ID only"}
    _write(path, data)
    result = build_engineering_topology(topology_file=path, source_osm=osm, output_dir=tmp_path / "build", target_year=2013)
    assert result["status"] == "blocked"
    assert "network" not in result["artifacts"]
    assert any(row.get("reason") == "osm_geometry_not_valid_for_target_year" for row in result["unknowns"])


def test_newer_osm_does_not_override_explicit_plan_coordinates(tmp_path):
    path, data, osm = _case(tmp_path, 2024)
    data["nodes"][0]["osm_node_id"] = "1"
    _write(path, data)
    result = build_engineering_topology(topology_file=path, source_osm=osm, output_dir=tmp_path / "build", target_year=2013)
    assert result["status"] == "pass", result
    assert not any(row["field"] == "position" for row in result["auxiliary_fills"])


def test_explicit_connections_do_not_acquire_guessed_side_arm_movements(tmp_path):
    path, data, osm = _case(tmp_path)
    data["nodes"].append({"id": "D", "x": 565050, "y": 5932950, "evidence": "plan"})
    data["edges"].append({"id": "side", "from": "D", "to": "J", "lanes": [{"width_m": 3.2, "allow": "passenger"}], "evidence": "plan"})
    data["connections"] = data["connections"][1:]
    _write(path, data)
    result = build_engineering_topology(topology_file=path, source_osm=osm, output_dir=tmp_path / "build", target_year=2013)
    assert result["status"] == "pass", result
    root = ET.parse(result["artifacts"]["network"]["path"]).getroot()
    actual = [(c.get("from"), c.get("fromLane"), c.get("to"), c.get("toLane")) for c in root.findall("connection") if not c.get("from").startswith(":")]
    assert actual == [("in", "1", "out", "1")]


def test_bad_source_hash_is_rejected_before_output(tmp_path):
    path, _, osm = _case(tmp_path)
    osm["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        build_engineering_topology(topology_file=path, source_osm=osm, output_dir=tmp_path / "build", target_year=2013)
    assert not (tmp_path / "build").exists()


def test_no_year_or_osm_needed_when_plan_geometry_is_explicit(tmp_path):
    path, data, _ = _case(tmp_path)
    result = build_engineering_topology(topology_file=path, source_osm=None, output_dir=tmp_path / "build", target_year=None)
    assert result["status"] == "pass", result
    assert result["target_year"] is None
    assert result["declared_topology_reproduced"] is True
    assert result["field_readiness"] == "review_required"
    assert result["topology_complete"] is False
    assert any(row.get("reason") == "target_year_unknown" for row in result["unknowns"])
    data["nodes"][0] = {"id": "A", "osm_node_id": "1", "evidence": "matching identity"}
    _write(path, data)
    blocked = build_engineering_topology(topology_file=path, source_osm=None, output_dir=tmp_path / "missing", target_year=None)
    assert blocked["status"] == "blocked"


def test_explicit_lane_shapes_and_class_arrays_reach_native_net_in_the_correct_frame(tmp_path):
    path, data, osm = _case(tmp_path)
    for edge, start, end in zip(data["edges"], (565000, 565050), (565050, 565100)):
        edge["lanes"] = [dict(lane) for lane in edge["lanes"]]
        for index, lane in enumerate(edge["lanes"]):
            lane["allow"] = lane["allow"].split()
            lane["shape"] = [[start, 5933000 - index * 3], [end, 5933000 - index * 3]]
    _write(path, data)
    result = build_engineering_topology(topology_file=path, source_osm=osm, output_dir=tmp_path / "build", target_year=2013)
    assert result["status"] == "pass", result
    root = ET.parse(result["artifacts"]["network"]["path"]).getroot()
    offset = [float(v) for v in root.find("location").get("netOffset").split(",")]
    for edge in data["edges"]:
        for index in (0, 1):
            lane = root.find(f"edge[@id='{edge['id']}']/lane[@index='{index}']")
            points = [[float(v) for v in p.split(",")] for p in lane.get("shape").split()]
            assert all(abs(point[1] - offset[1] - (5933000 - index * 3)) < 0.01 for point in points)


def test_native_signal_output_and_post_load_candidate_identity_are_checked(tmp_path, monkeypatch):
    path, _, osm = _case(tmp_path)
    native = engineering_topology.run_command
    def altered(command, **kwargs):
        result = native(command, **kwargs)
        if "--node-files" in command:
            output = command[command.index("--output-file") + 1]
            tree = ET.parse(output)
            tree.getroot().find("tlLogic/phase").set("duration", "9")
            tree.write(output, encoding="utf-8")
        return result
    monkeypatch.setattr(engineering_topology, "run_command", altered)
    result = build_engineering_topology(topology_file=path, source_osm=osm, output_dir=tmp_path / "signal", target_year=2013)
    assert result["construction_checks"]["diagnostic_signal_programs"] == "blocked"
    def changed_during_load(command, **kwargs):
        result = native(command, **kwargs)
        if "--net-file" in command:
            file = Path(command[command.index("--net-file") + 1])
            file.write_bytes(file.read_bytes() + b"\n")
        return result
    monkeypatch.setattr(engineering_topology, "run_command", changed_during_load)
    result = build_engineering_topology(topology_file=path, source_osm=osm, output_dir=tmp_path / "changed", target_year=2013)
    assert result["status"] == "blocked"
    assert result["generated_artifacts_unchanged"] is False
def test_compiler_clipping_is_reported_separately_from_topology_reproduction(tmp_path):
    path, data, osm = _case(tmp_path)
    data['edges'][0]['lanes'][0]['shape'] = [[565000, 5933001], [565040, 5933001]]
    _write(path, data)
    result = build_engineering_topology(topology_file=path, source_osm=osm, output_dir=tmp_path/'build', target_year=2013)
    assert result['declared_topology_reproduced'] is True
    assert result['declared_geometry_reproduced'] is False
    assert any(row['end_displacement_m'] > 1 for row in result['lane_geometry_checks'])
    assert any(row.get('reason') == 'compiled_lane_geometry_differs_from_plan' for row in result['unknowns'])
