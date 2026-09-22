"""Static access regressions from two real Hamburg bus-only OSM ways.

Fixture: OpenStreetMap ways 219999179 and 1217434982, frozen August 2026,
ODbL-1.0. It contains original node/way tags from the public corridor extract.
"""

import hashlib
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import sumolib

from torii_sumo.core.osm_network import build_osm_network
from torii_sumo.core.osm_access import resolve_static_osm_access


def test_static_restriction_hierarchy_and_unresolved_access_stay_explicit():
    current = {"allow": "passenger bus delivery bicycle pedestrian"}
    vehicle = resolve_static_osm_access({"vehicle": "no", "bus": "designated", "foot": "no"}, current)
    assert vehicle["attributes"] == {"allow": "bus"}
    motor_only = resolve_static_osm_access({"motor_vehicle": "no", "bus": "yes"}, current)
    assert set(motor_only["attributes"]["allow"].split()) == {"bus", "bicycle", "pedestrian"}
    specific = resolve_static_osm_access({"vehicle": "no", "bicycle": "yes", "motorcar": "yes"}, current)
    assert set(specific["attributes"]["allow"].split()) == {"private", "passenger", "bicycle", "pedestrian"}
    parking = resolve_static_osm_access({"highway": "service", "service": "parking_aisle"}, current)
    assert parking["status"] == "not_applicable"
    assert parking["attributes"] == current
    conditional = resolve_static_osm_access({"vehicle": "no", "bus:conditional": "yes @ (Mo-Fr)"}, current)
    assert conditional["status"] == "review_required"
    assert set(conditional["attributes"]["allow"].split()) == {"bus", "pedestrian"}
    assert any(row.get("key") == "bus:conditional" for row in conditional["review"])


@pytest.mark.parametrize("way_id,extra", [
    ("43860438", {"bicycle": "use_sidepath"}),
    ("1046693824", {"small_electric_vehicle": "yes"}),
    ("1046693825", {"bicycle": "use_sidepath"}),
    ("1358423849", {"small_electric_vehicle": "yes"}),
])
def test_real_delivery_only_conditions_do_not_grant_general_motor_access(way_id, extra):
    # Exact access-related tags from the four frozen Hamburg OSM ways.
    tags = {"motor_vehicle": "no", "motor_vehicle:conditional": "delivery @ (Mo-Su 00:00-11:00,21:00-24:00)", "psv": "yes", **extra}
    current = {"allow": "passenger private delivery bus taxi pedestrian bicycle scooter"}
    result = resolve_static_osm_access(tags, current)
    modes = set(result["attributes"]["allow"].split())
    assert not modes & {"passenger", "private", "delivery"}, way_id
    assert {"bus", "taxi", "pedestrian"} <= modes
    assert result["status"] == "review_required"
    assert any(row.get("key") == "motor_vehicle:conditional" for row in result["review"])


@pytest.mark.parametrize("conditional", ["yes @ (Mo-Fr)", "destination @ (Mo-Fr)", "delivery @ (Mo-Fr); yes @ (Sa)", "delivery @ (Mo-Fr"])
def test_conditions_that_can_change_general_access_stay_unresolved(conditional):
    current = {"allow": "passenger bus taxi pedestrian"}
    result = resolve_static_osm_access({"motor_vehicle": "no", "motor_vehicle:conditional": conditional, "psv": "yes"}, current)
    assert "passenger" in result["attributes"]["allow"].split()
    assert not any(row["mode"] == "passenger" for row in result["decisions"])
    assert any(row.get("mode") == "passenger" for row in result["review"])
    assert any(row["mode"] == "bus" and row["allowed"] and row["key"] == "psv" for row in result["decisions"])


@pytest.mark.parametrize("motorcar,allowed", [("yes", True), ("no", False)])
def test_specific_static_mode_overrules_broader_conditional(motorcar, allowed):
    result = resolve_static_osm_access({"motor_vehicle": "no", "motor_vehicle:conditional": "yes @ (Mo-Fr)", "motorcar": motorcar}, {"allow": "passenger pedestrian"})
    assert ("passenger" in result["attributes"].get("allow", "").split()) is allowed
    assert any(row["mode"] == "passenger" and row["key"] == "motorcar" and row["allowed"] is allowed for row in result["decisions"])


def test_directional_rule_stays_local_to_the_affected_mode():
    result = resolve_static_osm_access({"vehicle": "no", "bus:forward": "yes"}, {"allow": "passenger bus bicycle pedestrian"})
    assert set(result["attributes"]["allow"].split()) == {"bus", "pedestrian"}
    assert any(row.get("mode") == "bus" and row.get("key") == "bus:forward" for row in result["review"])


def test_real_bus_roads_preserve_source_access_and_reject_delivery_and_bicycle(tmp_path):
    binary = shutil.which("netconvert")
    sumo = shutil.which("sumo")
    if not binary or not sumo:
        pytest.skip("Installed SUMO is required for the real import regression")
    source = Path(__file__).parent / "fixtures/osm_bus_access_118.osm.xml"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = build_osm_network(
        bbox="9.985,53.554,9.993,53.560",
        output_dir=tmp_path / "build",
        source_osm_path=source,
        allowed_highways={"service"},
        clip_source_ways_to_bbox=False,
        netconvert_binary=binary,
    )
    assert result["status"] == "pass", result
    net = sumolib.net.readNet(result["net_file"], withInternal=True)
    relevant = [edge for edge in net.getEdges() if not edge.getID().startswith(":")]
    assert relevant
    for edge in relevant:
        for lane in edge.getLanes():
            assert lane.allows("bus")
            assert not lane.allows("delivery"), lane.getID()
            assert not lane.allows("bicycle"), lane.getID()
            assert not lane.allows("passenger"), lane.getID()
            if "219999179" in edge.getID():
                assert not lane.allows("pedestrian")
            else:
                assert lane.allows("pedestrian")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    report = result["osm_access_correction"]
    assert report["source_immutable"]
    assert report["runtime_activation_changes"] is False
    assert report["changed_lane_count"] == len(relevant)

    chosen = [next(edge for edge in relevant if key in edge.getID()).getID() for key in ("219999179", "1217434982")]
    for mode in ("bus", "delivery", "bicycle"):
        routes = ET.Element("routes")
        ET.SubElement(routes, "vType", id="test", vClass=mode)
        for i, edge in enumerate(chosen):
            vehicle = ET.SubElement(routes, "vehicle", id=f"v{i}", type="test", depart=str(i))
            ET.SubElement(vehicle, "route", edges=edge)
        route_file = tmp_path / f"{mode}.rou.xml"
        ET.ElementTree(routes).write(route_file, encoding="utf-8", xml_declaration=True)
        tripinfo = tmp_path / f"{mode}.tripinfo.xml"
        run = subprocess.run([sumo, "-n", result["net_file"], "-r", str(route_file), "--end", "300", "--tripinfo-output", str(tripinfo), "--no-step-log", "true"], text=True, capture_output=True, timeout=30)
        if mode == "bus":
            assert run.returncode == 0, run.stderr
            assert len(ET.parse(tripinfo).getroot().findall("tripinfo")) == 2
        else:
            assert run.returncode != 0, f"{mode} unexpectedly entered a bus-only road"
