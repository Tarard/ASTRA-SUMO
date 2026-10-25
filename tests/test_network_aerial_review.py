from __future__ import annotations

import copy
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image
import pytest
from pyproj import Transformer

from astra_sumo.cli import main
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.network_aerial_review import (
    _draw, _network, build_network_aerial_review_request_schema, render_network_aerial_review,
)
from shapely.geometry import LineString
from pyproj import CRS


def _fixture(tmp_path: Path) -> tuple[Path, dict]:
    # Local coordinates require subtraction of a negative SUMO netOffset.
    network = tmp_path / "input.net.xml"
    network.write_text('''<net><location netOffset="-500000,-5800000" projParameter="EPSG:32632"/>
<edge id="road"><lane id="road_0" width="3" shape="0,50 100,50"/>
<lane id="road_1" width="3" shape="0,60 100,60"/></edge>
<edge id=":J_0" function="internal"><lane id=":J_0_0" width="3" shape="40,50 50,55 60,60"/></edge>
<junction id="J" shape="40,40 60,40 60,70 40,70"/>
</net>''', encoding="utf-8")
    aerial = tmp_path / "aerial.png"
    image = Image.new("RGB", (200, 200), (90, 100, 110))
    # This stripe tests orientation and exact geographic image cropping.
    for x in range(200):
        image.putpixel((x, 20), (200, 10, 20))
    image.save(aerial)
    def artifact(path):
        return {"path": path.name, "sha256": file_sha256(path)}
    request = {"schema": "astra.network-aerial-review-request/v1",
               "candidate_net": artifact(network), "source_net": artifact(network),
               "aerial": {**artifact(aerial), "bbox": [500000, 5800000, 500100, 5800100],
                          "crs": "EPSG:32632", "north_up": True, "capture_date": None,
                          "capture_year": 2026, "authority": "Synthetic fixture",
                          "license": "MIT", "source_url": "synthetic://test"},
               "target_date": "2026-10-15", "width_px": 200,
               "views": [{"id": "junction", "bbox": [500000, 5800000, 500100, 5800100]}]}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    return path, request


def test_repeated_output_is_identical_and_all_lanes_are_registered(tmp_path: Path) -> None:
    path, _request = _fixture(tmp_path)
    first = render_network_aerial_review(request_file=path, output_dir=tmp_path / "first")
    second = render_network_aerial_review(request_file=path, output_dir=tmp_path / "second")
    assert first["artifacts"] == second["artifacts"]
    assert (tmp_path / "first/report.json").read_bytes() == (tmp_path / "second/report.json").read_bytes()
    assert first["status"] == "review_required" and first["render_status"] == "pass"
    assert first["temporal_alignment"] == "unknown"
    assert first["inputs_unchanged"]
    visible = first["views"][0]["visible_objects"]["candidate_net"]
    assert visible == {"lanes": [":J_0_0", "road_0", "road_1"], "junctions": ["J"]}
    with Image.open(tmp_path / "first/junction-candidate_net.png") as image:
        # Header adds 24 pixels. North-up maps y=60 above y=50.
        assert image.getpixel((20, 104)) == (0, 230, 255)
        assert image.getpixel((20, 124)) == (0, 230, 255)
        assert image.getpixel((20, 164)) == (90, 100, 110)
    with Image.open(tmp_path / "first/junction-aerial.png") as image:
        assert image.getpixel((20, 20)) == (200, 10, 20)
    with pytest.raises(ValueError, match="new review"):
        render_network_aerial_review(request_file=path, output_dir=tmp_path / "first")


def test_independent_crs_and_offset_are_applied_per_network(tmp_path: Path) -> None:
    path, request = _fixture(tmp_path)
    original = ET.parse(tmp_path / "input.net.xml")
    converted = tmp_path / "different-frame.net.xml"
    transformer = Transformer.from_crs("EPSG:32632", "EPSG:3857", always_xy=True)
    offset = (-1000000, -6000000)
    location = original.getroot().find("location")
    location.set("netOffset", f"{offset[0]},{offset[1]}")
    location.set("projParameter", "EPSG:3857")
    for element in original.getroot().iter():
        if element.get("shape"):
            points = [transformer.transform(float(pair.split(',')[0]) + 500000,
                                            float(pair.split(',')[1]) + 5800000)
                      for pair in element.get("shape").split()]
            element.set("shape", " ".join(f"{x + offset[0]},{y + offset[1]}" for x, y in points))
    original.write(converted)
    request["candidate_net"] = {"path": converted.name, "sha256": file_sha256(converted)}
    path.write_text(json.dumps(request), encoding="utf-8")
    report = render_network_aerial_review(request_file=path, output_dir=tmp_path / "result")
    assert report["frames"]["candidate_net"]["projection"] == "EPSG:3857"
    assert report["views"][0]["visible_objects"]["candidate_net"]["lanes"] == [":J_0_0", "road_0", "road_1"]
    with Image.open(tmp_path / "result/junction-candidate_net.png") as image:
        assert image.getpixel((20, 124)) == (0, 230, 255)


@pytest.mark.parametrize("corruption", ["hash", "bounds", "geographic_crs", "unknown_projection", "nan",
                                        "path_traversal", "duplicate_views", "wrong_year", "extra_key", "rotation"])
def test_invalid_requests_produce_no_review(tmp_path: Path, corruption: str) -> None:
    path, request = _fixture(tmp_path)
    if corruption == "hash":
        request["candidate_net"]["sha256"] = "0" * 64
    elif corruption == "bounds":
        request["views"][0]["bbox"][0] -= 1
    elif corruption == "geographic_crs":
        request["aerial"]["crs"] = "EPSG:4326"
    elif corruption in {"unknown_projection", "nan"}:
        network = tmp_path / "input.net.xml"
        network.write_text(network.read_text().replace("EPSG:32632", "!") if corruption == "unknown_projection"
                           else network.read_text().replace("0,50", "nan,50"), encoding="utf-8")
        request["candidate_net"]["sha256"] = file_sha256(network)
        request["source_net"] = None
    elif corruption == "path_traversal":
        request["views"][0]["id"] = "../outside"
    elif corruption == "duplicate_views":
        request["views"].append(copy.deepcopy(request["views"][0]))
    elif corruption == "wrong_year":
        request["aerial"]["capture_date"] = "2025-10-01"
    elif corruption == "rotation":
        request["aerial"]["north_up"] = False
    else:
        request["aerial"]["unhandled_key"] = 42
    path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(ValueError):
        render_network_aerial_review(request_file=path, output_dir=tmp_path / "result")
    assert not (tmp_path / "result").exists()


def test_lane_surface_holes_preserve_the_aerial(tmp_path: Path) -> None:
    ring = LineString([(20, 20), (80, 20), (80, 80), (20, 80), (20, 20)])
    image, _visible = _draw(Image.new("RGB", (100, 100), (50, 60, 70)),
                           [{"id": "ring", "kind": "normal", "line": ring,
                             "surface": ring.buffer(3, cap_style=2, join_style=2)}], (0, 0, 100, 100), "ring")
    assert image.getpixel((50, 74)) == (50, 60, 70)


def test_invalid_junction_is_visible_without_repair(tmp_path: Path) -> None:
    path, _request = _fixture(tmp_path)
    network = tmp_path / "input.net.xml"
    network.write_text(network.read_text().replace("40,40 60,40 60,70 40,70", "40,40 60,70 40,70 60,40"))
    _objects, frame = _network(network, CRS.from_epsg(32632))
    assert frame["invalid_junction_surfaces_drawn_as_outlines"] == ["J"]
    assert path.exists()


def test_rounded_zero_length_internal_lane_is_retained(tmp_path: Path) -> None:
    path, request = _fixture(tmp_path)
    network = tmp_path / "input.net.xml"
    network.write_text(network.read_text().replace("40,50 50,55 60,60", "50,55 50,55"), encoding="utf-8")
    request["source_net"] = None
    request["candidate_net"]["sha256"] = file_sha256(network)
    path.write_text(json.dumps(request), encoding="utf-8")
    result = render_network_aerial_review(request_file=path, output_dir=tmp_path / "result")
    assert result["frames"]["candidate_net"]["zero_geometric_length_lanes_drawn_as_points"] == [":J_0_0"]
    assert ":J_0_0" in result["views"][0]["visible_objects"]["candidate_net"]["lanes"]


def test_cli_render_runs_the_real_domain_function(tmp_path: Path, capsys) -> None:
    request, _data = _fixture(tmp_path)
    code = main(["review", "render", str(request), str(tmp_path / "result"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1  # The image is ready. Field review remains required.
    assert payload["render_status"] == "pass"
    assert Path(payload["report_file"]).exists()


def test_request_schema_is_current() -> None:
    schema = Path(__file__).resolve().parents[1] / "schemas/product/astra.network-aerial-review-request.v1.schema.json"
    assert json.loads(schema.read_text(encoding="utf-8")) == build_network_aerial_review_request_schema()


def test_input_change_during_render_has_no_final_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, _request = _fixture(tmp_path)
    original_save = Image.Image.save

    def save(image, *args, **kwargs):
        network = tmp_path / "input.net.xml"
        network.write_bytes(network.read_bytes() + b"\n")
        return original_save(image, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", save)
    with pytest.raises(ValueError, match="input changed"):
        render_network_aerial_review(request_file=path, output_dir=tmp_path / "result")
    assert not (tmp_path / "result/report.json").exists()


def test_nonoverlapping_network_is_rejected_before_output(tmp_path: Path) -> None:
    path, request = _fixture(tmp_path)
    request["views"][0]["bbox"] = [500001, 5800001, 500010, 5800010]
    path.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(ValueError, match="contains no lanes"):
        render_network_aerial_review(request_file=path, output_dir=tmp_path / "result")
    assert not (tmp_path / "result").exists()


def test_tagged_passage_is_dashed_and_complete_geometry_is_retained(tmp_path: Path) -> None:
    path, request = _fixture(tmp_path)
    network = tmp_path / "input.net.xml"
    tree = ET.parse(network)
    ET.SubElement(tree.getroot().find("edge"), "param", key="tunnel", value="building_passage")
    tree.write(network)
    for role in ("source_net", "candidate_net"):
        request[role]["sha256"] = file_sha256(network)
    path.write_text(json.dumps(request), encoding="utf-8")
    before = file_sha256(network)
    report = render_network_aerial_review(request_file=path, output_dir=tmp_path / "result")
    assert file_sha256(network) == before
    structures = report["frames"]["candidate_net"]["structure_review"]
    assert structures["lane_structures"]["road_0"] == "building_passage"
    with Image.open(tmp_path / "result/junction-candidate_net.png") as overview:
        assert overview.getpixel((20, 124)) != (0, 230, 255)
    with Image.open(tmp_path / "result/junction-candidate_net-structures.png") as detail:
        assert detail.getpixel((20, 124)) == (0, 230, 255)
    assert "<details>" in (tmp_path / "result/review.html").read_text()
    assert report["views"][0]["files"]["structure_detail_comparison"] in report["artifacts"]
