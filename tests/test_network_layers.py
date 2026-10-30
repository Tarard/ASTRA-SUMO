import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core import network_layers
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.network_layers import (
    derive_network_layer,
    master_network_layer,
    measure_mode_components,
    measure_tls_cycles,
    select_layer_edges,
)
from astra_sumo.core.osm_network import build_osm_network


FIXTURE = Path(__file__).parent / "fixtures/osm_multimodal_layers.osm.xml"
FOOTWAY_AND_CYCLEWAYS = {"200", "201", "-201", "202", "-202"}


def _binaries() -> tuple[str, str]:
    sumo = pytest.importorskip("sumo")
    directory = Path(sumo.__file__).parent / "bin"
    suffix = ".exe" if os.name == "nt" else ""
    netconvert, simulator = directory / f"netconvert{suffix}", directory / f"sumo{suffix}"
    if not netconvert.is_file() or not simulator.is_file():
        pytest.skip("The SUMO package binaries are required.")
    return str(netconvert), str(simulator)


@pytest.fixture(scope="module")
def master(tmp_path_factory) -> Path:
    netconvert, _ = _binaries()
    build = build_osm_network(bbox="10.9985,47.9990,11.0025,48.0020", output_dir=tmp_path_factory.mktemp("master"),
        source_osm_path=FIXTURE, allowed_highways={"secondary", "tertiary", "residential", "footway", "cycleway"},
        clip_source_ways_to_bbox=False, netconvert_binary=netconvert, netconvert_profile="multimodal",
        multimodal_modes=["bicycle", "pedestrian"])
    assert build["status"] == "pass", build
    return Path(build["net_file"])


def _ordinary_edges(root: ET.Element) -> dict[str, ET.Element]:
    return {edge.get("id"): edge for edge in root.findall("edge") if not edge.get("function")}


def _connections(root: ET.Element, edges) -> dict[tuple, tuple]:
    return {(row.get("from"), row.get("fromLane"), row.get("to"), row.get("toLane")): (row.get("tl"), row.get("linkIndex"))
            for row in root.findall("connection") if row.get("from") in edges and row.get("to") in edges}


def test_vehicle_layer_removes_only_edges_and_keeps_master_identity(master, tmp_path):
    netconvert, simulator = _binaries()
    master_hash = file_sha256(master)
    selection = select_layer_edges(master, "vehicle")
    assert set(selection["removed_edge_ids"]) == FOOTWAY_AND_CYCLEWAYS
    assert selection["removed_by_permission_signature"] == {"bicycle": 4, "pedestrian": 1}

    report = derive_network_layer(master, tmp_path / "vehicle", "vehicle", netconvert_binary=netconvert,
                                  sumo_binary=simulator)

    assert report["status"] == "measured", report.get("identity")
    assert report["master_unchanged"] and file_sha256(master) == master_hash == report["derived_from_sha256"]
    assert all(report["identity"]["checks"].values())
    assert report["sumo_load"]["status"] == "pass"
    assert (report["edge_count"], report["removed_edge_count"]) == (10, 5)
    assert file_sha256(Path(report["net_file"])) == report["sha256"]
    assert json.loads(Path(report["manifest_file"]).read_text(encoding="utf-8"))["status"] == "measured"
    # The crossing over the removed cycleway and the links into it go with it; the controller stays.
    assert ["crossing", "1", "-202 202"] in report["identity"]["removed_controlled_links"]
    assert report["identity"]["removed_tls_ids"] == []

    before, after = ET.parse(master).getroot(), ET.parse(report["net_file"]).getroot()
    old_edges, new_edges = _ordinary_edges(before), _ordinary_edges(after)
    assert set(new_edges) == set(old_edges) - FOOTWAY_AND_CYCLEWAYS
    assert {row.get("id") for row in after.findall("junction")} - {row.get("id") for row in before.findall("junction")} <= {
        row.get("id") for row in after.findall("junction") if row.get("type") == "internal"}
    for edge_id, edge in new_edges.items():
        assert [(lane.get("id"), lane.get("allow"), lane.get("disallow")) for lane in edge.findall("lane")] == [
            (lane.get("id"), lane.get("allow"), lane.get("disallow")) for lane in old_edges[edge_id].findall("lane")]
    assert after.find("location").get("netOffset") == before.find("location").get("netOffset")
    old_connections = _connections(before, new_edges)
    assert _connections(after, new_edges) == old_connections
    assert any(tl for tl, _ in old_connections.values())
    programs = {(row.get("id"), row.get("type"), row.get("offset"),
                 sum(float(phase.get("duration")) for phase in row.findall("phase"))) for row in before.findall("tlLogic")}
    assert {(row.get("id"), row.get("type"), row.get("offset"),
             sum(float(phase.get("duration")) for phase in row.findall("phase"))) for row in after.findall("tlLogic")} <= programs


def test_layer_without_removed_edges_is_no_effect_and_output_directories_are_new(master, tmp_path):
    netconvert, simulator = _binaries()
    vehicle = derive_network_layer(master, tmp_path / "vehicle", "vehicle", netconvert_binary=netconvert,
                                   sumo_binary=simulator)
    again = derive_network_layer(vehicle["net_file"], tmp_path / "again", "vehicle", netconvert_binary=netconvert)
    assert (again["status"], again["reason"], again["removed_edge_count"]) == ("no_effect", "layer_keeps_every_master_edge", 0)
    assert again["net_file"] == str(Path(vehicle["net_file"]).resolve()) and again["native_conversion_count"] == 0
    assert derive_network_layer(master, tmp_path / "all", "multimodal")["status"] == "no_effect"
    with pytest.raises(ValueError, match="new output directory"):
        derive_network_layer(master, tmp_path / "vehicle", "vehicle", netconvert_binary=netconvert)
    with pytest.raises(ValueError, match="network layer"):
        derive_network_layer(master, tmp_path / "unknown", "bicycle")
    described = master_network_layer(master, "multimodal")
    assert (described["status"], described["reason"], described["sha256"]) == ("no_effect", "layer_is_master", file_sha256(master))


def test_layer_whose_signal_timing_changed_is_blocked(master, tmp_path, monkeypatch):
    netconvert, simulator = _binaries()
    native = network_layers.keep_selected_edges_natively

    def retimed(source, output, keep_file, candidate, **kwargs):
        result = native(source, output, keep_file, candidate, **kwargs)
        text = candidate.read_text(encoding="utf-8")
        candidate.write_text(text.replace('<phase duration="15"', '<phase duration="25"', 1), encoding="utf-8")
        return result

    monkeypatch.setattr(network_layers, "keep_selected_edges_natively", retimed)
    report = derive_network_layer(master, tmp_path / "vehicle", "vehicle", netconvert_binary=netconvert,
                                  sumo_binary=simulator)
    assert (report["status"], report["reason"]) == ("blocked", "layer_identity_check_failed")
    assert {"controlled_link_timelines_equal", "controller_cycle_offset_type_equal"} <= set(
        report["identity"]["failed_checks"])
    assert report["master_unchanged"]


def test_mode_components_are_measured_without_pruning(master, tmp_path):
    measured = measure_mode_components(master, ["bicycle", "pedestrian"])
    assert measured["gate"] == "pass" and measured["network_has_walkingareas"]
    assert {mode: row["largest_component_share"] for mode, row in measured["modes"].items()} == {
        "bicycle": 1.0, "pedestrian": 1.0}

    split = tmp_path / "split.net.xml"
    split.write_text(
        '<net version="1.20">'
        '<edge id="a" from="1" to="2"><lane id="a_0" allow="bicycle" length="10"/></edge>'
        '<edge id="b" from="2" to="3"><lane id="b_0" allow="bicycle" length="10"/></edge>'
        '<edge id="c" from="7" to="8"><lane id="c_0" allow="bicycle pedestrian" length="10"/></edge>'
        '<edge id="d" from="8" to="9"><lane id="d_0" allow="pedestrian" length="10"/></edge>'
        '<connection from="a" to="b" fromLane="0" toLane="0"/>'
        '</net>', encoding="utf-8")
    before = split.read_bytes()
    report = measure_mode_components(split, ["bicycle", "pedestrian"])
    bicycle, pedestrian = report["modes"]["bicycle"], report["modes"]["pedestrian"]
    assert (bicycle["edge_count"], bicycle["component_count"], bicycle["largest_component_share"]) == (3, 2, 0.666667)
    assert (bicycle["gate"], bicycle["reason"]) == ("review_required", "largest_component_share_below_threshold")
    # Without walking areas, pedestrians change edges at a shared junction.
    assert (pedestrian["component_count"], pedestrian["gate"]) == (1, "pass")
    assert (report["gate"], report["review_modes"]) == ("review_required", ["bicycle"])
    assert split.read_bytes() == before


def test_tls_cycles_list_programs_above_the_threshold(master, tmp_path):
    assert measure_tls_cycles(master)["long_cycle_controllers"] == []
    net = tmp_path / "cycles.net.xml"
    net.write_text('<net version="1.20">'
                   '<tlLogic id="short" type="static" programID="0" offset="0">'
                   '<phase duration="40" state="G"/><phase duration="50" state="r"/></tlLogic>'
                   '<tlLogic id="long" type="static" programID="0" offset="0">'
                   '<phase duration="100" state="G"/><phase duration="30" state="r"/></tlLogic>'
                   '</net>', encoding="utf-8")
    report = measure_tls_cycles(net)
    assert (report["controller_count"], report["max_cycle_s"]) == (2, 130.0)
    assert [row["tl_id"] for row in report["long_cycle_controllers"]] == ["long"]
