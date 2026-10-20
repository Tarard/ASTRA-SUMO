from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyproj import Transformer

from astra_sumo.core import hamburg_network_calibration as calibration
from astra_sumo.core.digital_twin import MapConnection, MapLane
from astra_sumo.core.digital_twin_mapping import MapLaneBinding


def _sources(tmp_path, monkeypatch):
    xml, kml = tmp_path / "map.xml", tmp_path / "map.kml"
    xml.write_text('''<MapData><IntersectionGeometry><laneWidth>325</laneWidth><laneSet>
        <GenericLane><laneID>1</laneID><nodeList><NodeXY><attributes><stopLine/></attributes></NodeXY></nodeList></GenericLane>
        <GenericLane><laneID>2</laneID><nodeList><NodeXY><attributes><dWidth>-125</dWidth><stopLine/></attributes></NodeXY></nodeList></GenericLane>
        </laneSet></IntersectionGeometry></MapData>''', encoding="utf-8")
    kml.write_text("independent KML source", encoding="utf-8")
    motor = MapLane("2349", "1", "vehicle", "1", "", 9.99, 53.54, ((0, 0), (10, 0)))
    cycle = MapLane("2349", "2", "bikeLane", "1", "", 9.99, 53.54, ((0, 2), (10, 2)))
    connection = MapConnection("2349", "movement", "1", "2", "7", "")
    rows = [{"lane_id": lane.lane_id, "coordinates": [[9.99, 53.54], [9.9901, 53.54]],
             "endpoint_a": [9.99, 53.54], "endpoint_b": [9.9901, 53.54]} for lane in (motor, cycle)]
    monkeypatch.setattr(calibration, "parse_mapem", lambda _: ([motor, cycle], [connection]))
    monkeypatch.setattr(calibration, "parse_hamburg_map_kml", lambda _: {})
    monkeypatch.setattr(calibration, "bind_hamburg_map_kml_to_mapem", lambda *_: {"node_id": "2349", "lanes": rows})
    binding = MapLaneBinding("2349", "1", "vehicle", "ingress", "road", "road_0", 10, 0, 0, "high", "active")
    return xml, kml, binding


def test_automatic_high_confidence_does_not_confirm_correspondence_or_omit_cycling(tmp_path, monkeypatch):
    xml, kml, binding = _sources(tmp_path, monkeypatch)
    table = calibration.write_hamburg_network_facts(map_xml_file=xml, map_kml_file=kml,
        output_file=tmp_path / "facts.json", target_date="2026-10-15", lane_bindings=[binding], sample_spacing_m=0.5)
    centerlines = [f for f in table["facts"] if f["kind"] == "lane_centerline"]
    assert len(centerlines) == 2
    assert all(f["mapping_status"] == "unresolved" for f in centerlines)
    assert centerlines[1]["expected"] and centerlines[1]["selector"] == {}
    assert all(s["source_date"] is None for s in table["sources"])
    assert all(not Path(s["path"]).is_absolute() for s in table["sources"])
    assert next(f for f in table["facts"] if f["kind"] == "signal_group")["expected"] is None
    widths = {f["id"]: f["expected"] for f in table["facts"] if f["kind"] == "lane_width"}
    assert widths == {"2349:lane:1:width_at_stop_line": 3.25, "2349:lane:2:width_at_stop_line": 2.0}


def test_reviewed_lane_uses_official_core_endpoint_for_stop_line(tmp_path, monkeypatch):
    xml, kml, binding = _sources(tmp_path, monkeypatch)
    table = calibration.write_hamburg_network_facts(map_xml_file=xml, map_kml_file=kml,
        output_file=tmp_path / "facts.json", target_date="2026-10-15", lane_bindings=[binding],
        confirmed_map_lane_ids=frozenset({"1"}), sample_spacing_m=0.5)
    stop = next(f for f in table["facts"] if f["id"] == "2349:lane:1:stop_line")
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
    assert stop["expected"] == list(transformer.transform(9.9901, 53.54))
    assert stop["mapping_status"] == "confirmed"
    assert stop["tolerance_m"] is None


def test_movement_sources_follow_explicit_connections_instead_of_approach_roles(tmp_path, monkeypatch):
    xml, kml, binding = _sources(tmp_path, monkeypatch)
    target = MapLaneBinding("2349", "2", "bikeLane", "egress", "cycle", "cycle_0", 10, 0, 0, "low", "active")
    table = calibration.write_hamburg_network_facts(map_xml_file=xml, map_kml_file=kml,
        output_file=tmp_path / "facts.json", target_date="2026-10-15", lane_bindings=[binding, target], sample_spacing_m=0.5)
    movement = next(f for f in table["facts"] if f["id"] == "2349:bicycle:movement_inventory")
    assert movement["selector"] == {"incoming_lane_ids": ["road_0"]}
    assert movement["expected"] == [["road_0", "cycle_0"]]
    assert movement["mapping_confidence"] == "low"


def test_empty_site_manifest_is_rejected_before_creating_a_run(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"paths": {}, "downloads": []}), encoding="utf-8")
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="at least one site"):
        calibration.evaluate_hamburg_calibration_snapshot(network=tmp_path / "absent.net.xml",
            official_sources=manifest, output_dir=output, target_date="2026-10-15", sample_spacing_m=0.5)
    assert not output.exists()


def test_snapshot_coverage_average_includes_zero_overlap(tmp_path, monkeypatch):
    xml, kml, _ = _sources(tmp_path, monkeypatch)
    ocit = tmp_path / "ocit.xml"
    ocit.write_text("OCIT fixture", encoding="utf-8")
    paths = {"map_xml": str(xml), "map_kml": str(kml), "ocit_xml": str(ocit)}
    source_manifest = tmp_path / "sources.json"
    source_manifest.write_text(json.dumps({"paths": {"2349": paths}, "downloads": [
        {"node_id": "2349", "kind": kind, "path": path, "sha256": calibration.file_sha256(Path(path))}
        for kind, path in paths.items()]}), encoding="utf-8")
    monkeypatch.setattr(calibration, "read_hamburg_map_lane_references", lambda *_, **__: ([], None))
    monkeypatch.setattr(calibration, "bind_map_lanes_to_network", lambda *_, **__: [])
    monkeypatch.setattr(calibration, "write_hamburg_network_facts", lambda **_: {"facts": [{}, {}]})
    facts = [{"kind": "lane_centerline", "traffic_mode": "motor", "mapping_confidence": "low",
        "metrics": {"mean_lateral_offset_m": offset, "reference_coverage": coverage}}
        for offset, coverage in [(2.0, 1.0), (None, 0.0)]]
    monkeypatch.setattr(calibration, "evaluate_network_calibration", lambda **_: {
        "status": "measured", "layers": {}, "output_file": "scorecard.json", "motor_centerlines": {}, "facts": facts})
    result = calibration.evaluate_hamburg_calibration_snapshot(network=xml, official_sources=source_manifest,
        output_dir=tmp_path / "run", target_date="2026-10-06", sample_spacing_m=.5)
    assert result["sites"]["2349"]["measured_centerlines"] == 1
    assert result["sites"]["2349"]["coverage_measured_centerlines"] == 2
    assert result["sites"]["2349"]["mean_reference_coverage"] == .5


def test_manifest_node_cannot_relabel_another_official_maps_contents(tmp_path, monkeypatch):
    xml, kml, _ = _sources(tmp_path, monkeypatch)
    ocit = tmp_path / "ocit.xml"
    ocit.write_text("OCIT source fixture", encoding="utf-8")
    paths = {"map_xml": str(xml), "map_kml": str(kml), "ocit_xml": str(ocit)}
    downloads = [{"node_id": "2394", "kind": kind, "path": path,
                  "sha256": calibration.file_sha256(Path(path))} for kind, path in paths.items()]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"paths": {"2394": paths}, "downloads": downloads}), encoding="utf-8")
    with pytest.raises(ValueError, match="requested node 2394"):
        calibration.evaluate_hamburg_calibration_snapshot(network=xml, official_sources=manifest,
            output_dir=tmp_path / "run", target_date="2026-10-15", sample_spacing_m=0.5)
    assert not (tmp_path / "run/run.json").exists()


def test_connected_parts_keep_missing_spans_separate_and_do_not_add_an_unconnected_parallel_road():
    import xml.etree.ElementTree as ET
    root = ET.fromstring('''<net><location netOffset="0,0" projParameter="EPSG:25832"/>
      <edge id="a"><lane id="a_0" shape="0,0 10,0"/></edge>
      <edge id="b"><lane id="b_0" shape="12,0 20,0"/></edge>
      <edge id="c"><lane id="c_0" shape="0,0 25,0"/></edge>
      <connection from="a" fromLane="0" to="b" toLane="0"/></net>''')
    parts = calibration._connected_lane_parts(root, "b_0", [[0, 0], [20, 0]], 0.25)
    assert parts == ["a_0", "b_0"]
    from astra_sumo.core.network_calibration import centerline_overlap_metrics
    from shapely.geometry import LineString
    metrics = centerline_overlap_metrics(LineString([[0, 0], [20, 0]]),
        [calibration._shape(root.find(f"edge/lane[@id='{key}']")) for key in parts], 0.25)
    assert metrics["reference_coverage"] == pytest.approx(0.9)


def test_tiny_seed_can_measure_a_complete_saved_internal_lane_chain():
    import xml.etree.ElementTree as ET
    root = ET.fromstring('''<net><location netOffset="0,0" projParameter="EPSG:25832"/>
      <edge id="a" from="w" to="j"><lane id="a_0" shape="0,0 0.1,0"/></edge>
      <edge id=":j_0" function="internal"><lane id=":j_0_0" shape="0.1,0 9,0"/></edge>
      <edge id="b" from="j" to="e"><lane id="b_0" shape="9,0 10,0"/></edge>
      <junction id="j"/>
      <connection from="a" fromLane="0" to="b" toLane="0" via=":j_0_0"/>
      <connection from=":j_0" fromLane="0" to="b" toLane="0"/></net>''')
    parts = calibration._connected_lane_parts(root, "a_0", [[0, 0], [10, 0]], 0.25, direction=1)
    assert parts == ["a_0", ":j_0_0", "b_0"]
