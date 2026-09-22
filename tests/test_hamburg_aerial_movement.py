from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from pyproj import Transformer

from torii_sumo import cli
from torii_sumo.core import hamburg_aerial_movement as movement
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.hamburg_aerial_movement import (
    build_aerial_road_likelihood,
    select_movement_geometry,
)


def test_movement_selection_uses_trace_only_inside_error_limit() -> None:
    official = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]

    accepted = select_movement_geometry(
        official,
        [(0.0, 0.0), (1.0, 0.2), (2.0, 0.0)],
        max_error_m=0.5,
    )
    rejected = select_movement_geometry(
        official,
        [(0.0, 0.0), (1.0, 2.0), (2.0, 0.0)],
        max_error_m=0.5,
    )
    failed = select_movement_geometry(official, None, max_error_m=0.5)

    assert accepted["selected_source"] == "aerial_trace"
    assert accepted["selected_shape"] != official
    assert rejected["selected_source"] == "official_map"
    assert rejected["selected_shape"] == official
    assert rejected["reason"] == "trace_error_exceeds_limit"
    assert failed["selected_source"] == "official_map"
    assert failed["reason"] == "trace_failed"


def test_aerial_road_likelihood_prefers_gray_road_inside_declared_area() -> None:
    pixels = np.zeros((30, 30, 3), dtype=np.uint8)
    pixels[:, :] = (40, 150, 40)
    pixels[8:22, 8:22] = (175, 175, 175)
    image = Image.fromarray(pixels, mode="RGB")

    likelihood = build_aerial_road_likelihood(
        image,
        allowed_polygon=[(7.0, 7.0), (23.0, 7.0), (23.0, 23.0), (7.0, 23.0)],
        bbox=(0.0, 0.0, 30.0, 30.0),
    )

    assert likelihood[15, 15] > 0.8
    assert likelihood[2, 2] == 0.0


def test_road_prior_cannot_erase_road_space_or_ignore_image_identity(tmp_path):
    image = Image.new('RGB', (20, 20), (175, 175, 175))
    with pytest.raises(ValueError, match='road prior'):
        build_aerial_road_likelihood(image, allowed_polygon=[(0, 0), (20, 0), (20, 20)],
                                    bbox=(0, 0, 20, 20), road_prior=np.zeros((20, 20)))
    sources = {}
    for role in ('map_xml', 'map_kml', 'aerial_image'):
        path = tmp_path / role
        path.write_text('frozen source', encoding='utf-8')
        sources[role] = path
    expected = {role: file_sha256(path) for role, path in sources.items()}
    with pytest.raises(ValueError, match='exact aerial image and bounds'):
        movement.build_hamburg_aerial_movement_plan(node_id='1', map_xml_file=sources['map_xml'],
            map_kml_file=sources['map_kml'], aerial_image_file=sources['aerial_image'],
            bbox=(0, 0, 20, 20), aerial_year=2024, max_error_m=3, output_dir=tmp_path / 'bad',
            expected_sha256=expected, road_prior={'image_sha256': expected['aerial_image'],
                                                'bbox_epsg25832': [1, 0, 20, 20]})
    assert not (tmp_path / 'bad').exists()


def test_cli_routes_hamburg_aerial_movement_plan(monkeypatch, tmp_path, capsys) -> None:
    request = tmp_path / "request.json"
    request.write_text("{}", encoding="utf-8")
    output = tmp_path / "out"

    def fake_build(*, request_file, output_dir):
        assert request_file == str(request)
        assert output_dir == str(output)
        return {"status": "pass", "decision": "review_required"}

    monkeypatch.setattr(cli, "build_hamburg_aerial_corridor_plan", fake_build, raising=False)

    exit_code = cli.main(
        ["hamburg", "aerial-movements", str(request), str(output), "--json"]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "review_required"


@pytest.mark.parametrize("relative_sources", [False, True])
def test_corridor_plan_preserves_official_lanes_and_binds_plan_hash(
    tmp_path, monkeypatch, relative_sources
) -> None:
    source_dir = tmp_path / "inputs"
    source_dir.mkdir()
    map_xml = source_dir / "map.xml"
    map_xml.write_text(
        """<MAPEM><IntersectionGeometry><id><id>77</id></id>
        <refPoint><lat>530000000</lat><long>90000000</long></refPoint><laneSet>
        <GenericLane><laneID>1</laneID><ingressApproach>4</ingressApproach>
        <laneAttributes><sharedWith>0000100000</sharedWith><laneType><vehicle>10010000</vehicle></laneType></laneAttributes>
        <connectsTo><Connection><connectingLane><lane>2</lane></connectingLane>
        <connectionID>1</connectionID><signalGroup>3</signalGroup></Connection></connectsTo></GenericLane>
        <GenericLane><laneID>2</laneID><egressApproach>5</egressApproach>
        <laneAttributes><laneType><vehicle/></laneType></laneAttributes></GenericLane>
        </laneSet></IntersectionGeometry></MAPEM>""",
        encoding="utf-8",
    )
    lane_coordinates = {"1": [(9.0, 53.0), (9.0001, 53.0)], "2": [(9.0002, 53.0), (9.0003, 53.0)]}
    kml_root = ET.Element("kml")
    folder = ET.SubElement(kml_root, "Folder")
    ET.SubElement(folder, "name").text = "MAP"
    folders = {}
    for name in ("Base Points", "Lanes", "Crosswalks", "Connections", "Drive lines", "Points", "Merge points"):
        folders[name] = ET.SubElement(folder, "Folder")
        ET.SubElement(folders[name], "name").text = name

    def placemark(folder_name, name, coordinates, *, point=False, style=""):
        item = ET.SubElement(folders[folder_name], "Placemark")
        ET.SubElement(item, "name").text = name
        if style:
            ET.SubElement(item, "styleUrl").text = style
        geometry = ET.SubElement(item, "Point" if point else "LineString")
        ET.SubElement(geometry, "coordinates").text = " ".join(f"{x},{y},0" for x, y in coordinates)

    placemark("Base Points", "Base Point", [(9.0, 53.0)], point=True)
    for lane_id, coordinates in lane_coordinates.items():
        placemark("Lanes", f"Lane {lane_id}", coordinates, style="#laneStyleIn" if lane_id == "1" else "#laneStyleOut")
        for endpoint, coordinate in zip(("A", "B"), coordinates):
            placemark("Points", f"Lane {lane_id} {endpoint}", [coordinate], point=True)
    for name, label in (("Connections", "Con."), ("Drive lines", "DrvLn.")):
        placemark(name, f"{label} 1: 1 → 2", [lane_coordinates["1"][-1], lane_coordinates["2"][0]])
    map_kml = source_dir / "map.kml"
    ET.ElementTree(kml_root).write(map_kml, encoding="utf-8")
    aerial = source_dir / "aerial.png"
    Image.new("RGB", (64, 64), (175, 175, 175)).save(aerial)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
    x, y = transformer.transform(9.0, 53.0)
    request = {
        "schema": movement.REQUEST_SCHEMA,
        "max_error_m": 2.0,
        "intersections": [{
            "node_id": "77", "aerial_year": 2024,
            "bbox_epsg25832": [x - 10, y - 20, x + 40, y + 20],
            **{
                role: {"path": path.name if relative_sources else str(path), "sha256": file_sha256(path)}
                for role, path in {"map_xml": map_xml, "map_kml": map_kml, "aerial_image": aerial}.items()
            },
        }],
    }
    request_file = source_dir / "request.json"
    request_file.write_text(json.dumps(request), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    def fail_trace(*args, **kwargs):
        raise ValueError("test trace failure")

    monkeypatch.setattr(movement, "trace_probability_curve", fail_trace)

    report = movement.build_hamburg_aerial_corridor_plan(request_file=request_file, output_dir=tmp_path / "out")

    entry = report["intersections"][0]
    assert entry["plan_sha256"] == file_sha256(Path(entry["plan_file"]))
    plan = json.loads(Path(entry["plan_file"]).read_text(encoding="utf-8"))
    assert plan["crs"] == "EPSG:25832"
    assert plan["selection_reference_basis"] == "official_geometry_used_for_acceptance_and_fallback"
    assert plan["independent_geometry_validation"] is False
    lanes = {lane["lane_id"]: lane for lane in plan["lanes"]}
    assert set(lanes) == {"1", "2"}
    assert lanes["1"]["direction_role"] == "ingress"
    assert lanes["2"]["direction_role"] == "egress"
    assert lanes["1"]["lane_type"] == "vehicle"
    assert lanes["1"]["vehicle_attribute_bits"] == "10010000"
    assert lanes["1"]["shared_with_bits"] == "0000100000"
    assert lanes["1"]["allowed_vehicle_classes"] == ["bus"]
    assert lanes["1"]["revocable"] is True
    assert lanes["1"]["ingress_approach"] == "4"
    assert lanes["2"]["egress_approach"] == "5"
    for lane_id, coordinates in lane_coordinates.items():
        assert np.asarray(lanes[lane_id]["shape_epsg25832"]) == pytest.approx(
            np.asarray([transformer.transform(*point) for point in coordinates])
        )
    assert plan["inputs"]["map_kml"]["sha256"] == file_sha256(map_kml)


@pytest.mark.parametrize("node_id,max_error", [("77", float("nan")), ("77", float("inf")), ("77/../../escape", 2.0)])
def test_movement_request_rejects_invalid_limits_and_node_paths(tmp_path, node_id, max_error) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps({
        "schema": movement.REQUEST_SCHEMA,
        "max_error_m": max_error,
        "intersections": [{
            "node_id": node_id, "bbox_epsg25832": [0, 0, 10, 10], "aerial_year": 2024,
            **{role: {"path": "source", "sha256": "0" * 64} for role in ("map_xml", "map_kml", "aerial_image")},
        }],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="node_id|max_error_m"):
        movement._read_request(request_file)
def test_newer_aerial_cannot_replace_a_historical_target_even_if_curve_is_close():
    official = [(0, 0), (10, 0)]
    traced = [(0, 0.1), (10, 0.1)]
    result = select_movement_geometry(official, traced, max_error_m=3.0, target_year=2013, aerial_year=2026)
    assert result['selected_source'] == 'official_map'
    assert result['selected_shape'] == official
    assert result['reason'] == 'aerial_not_from_target_year'
    same_year = select_movement_geometry(official, traced, max_error_m=3.0, target_year=2013, aerial_year=2013)
    assert same_year['selected_source'] == 'aerial_trace'
