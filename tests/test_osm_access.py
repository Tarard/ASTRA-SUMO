"""Static access regressions from two real Hamburg bus-only OSM ways.

Fixture: OpenStreetMap ways 219999179 and 1217434982, frozen August 2026,
ODbL-1.0. It contains original node/way tags from the public corridor extract.
"""

import hashlib
import os
import shutil
import subprocess
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import sumolib
import traci
from sumolib import checkBinary
from sumolib.miscutils import getFreeSocketPort

from astra_sumo.core.osm_network import build_osm_network
from astra_sumo.core.osm_access import resolve_static_osm_access
from astra_sumo.core.command_runner import _terminate_process_tree


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
    assert result["status"] == "pass" and result["review"] == []
    assert any(row.get("key") == "motor_vehicle:conditional" and row["rule"] == "delivery_only_conditional"
               and row["reason"] == "static_no_preserved_without_delivery_purpose_grant" for row in result["decisions"])
    if extra.get("bicycle") == "use_sidepath":
        assert "bicycle" in modes
        assert any(row["mode"] == "bicycle" and row["reason"] == "use_sidepath_kept_native" for row in result["decisions"])


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
    assert any(row.get("mode") == "bus" and row.get("key") == "bus:forward"
               and row["reason"] == "directional_access_edge_direction_unknown" for row in result["review"])


def _rule_rows(result, reason):
    return [row for row in result["decisions"] if row.get("reason") == reason]


@pytest.mark.parametrize("direction,bus_allowed", [("forward", True), ("backward", False)])
def test_directional_access_applies_to_the_matching_edge_direction(direction, bus_allowed):
    current = {"allow": "passenger bus bicycle pedestrian"}
    result = resolve_static_osm_access({"vehicle": "no", "bus:forward": "yes"}, current, edge_direction=direction)
    assert result["status"] == "pass" and result["review"] == []
    assert ("bus" in result["attributes"]["allow"].split()) is bus_allowed
    assert "passenger" not in result["attributes"]["allow"].split()
    [row] = _rule_rows(result, "directional_access_applied")
    assert row["mode"] == "bus" and row["applies_to_edge"] is bus_allowed and row["edge_direction"] == direction


@pytest.mark.parametrize("tags", [{"vehicle": "no", "vehicle:backward": "no"}, {"vehicle": "no", "bicycle": "no", "bicycle:forward": "no"},
                                  {"vehicle": "no", "foot": "designated", "foot:forward": "designated"}])
def test_directional_value_equal_to_the_static_value_is_a_no_op(tags):
    current = {"allow": "passenger bicycle pedestrian"}
    unknown = resolve_static_osm_access(tags, current)
    assert unknown["status"] == "pass" and _rule_rows(unknown, "directional_value_equals_static")
    assert unknown["attributes"] == resolve_static_osm_access(tags, current, edge_direction="backward")["attributes"]


@pytest.mark.parametrize("tags,reason,modes", [
    ({"vehicle": "no", "bicycle": "yes", "bicycle:signed": "no"}, "signage_key_ignored", {"bicycle", "pedestrian"}),
    ({"vehicle": "no", "vehicle:disabled": "yes"}, "user_group_without_sumo_class", {"pedestrian"}),
    ({"motor_vehicle": "no", "motor_vehicle:conditional": "delivery"}, "malformed_conditional_delivery_only", {"bicycle", "pedestrian"}),
])
def test_suffix_keys_without_access_meaning_are_settled_by_rule(tags, reason, modes):
    result = resolve_static_osm_access(tags, {"allow": "passenger delivery bicycle pedestrian"})
    assert result["status"] == "pass" and result["review"] == []
    assert _rule_rows(result, reason)
    assert set(result["attributes"]["allow"].split()) == modes


def test_unknown_value_falls_back_to_the_less_specific_key():
    result = resolve_static_osm_access({"vehicle": "no", "bicycle": "unknown"}, {"allow": "passenger bicycle pedestrian"})
    assert result["attributes"] == {"allow": "pedestrian"}
    assert _rule_rows(result, "unknown_value_falls_back")[0]["key"] == "bicycle"
    assert any(row["mode"] == "bicycle" and row["key"] == "vehicle" and row["allowed"] is False for row in result["decisions"])


@pytest.mark.parametrize("value,reason", [
    *[(value, "purpose_restriction_kept_native") for value in ("private", "permit", "destination", "customers", "delivery",
                                                                 "agricultural;forestry", "residents")],
    ("dismount", "dismount_kept_native"), ("use_sidepath", "use_sidepath_kept_native"), ("bus", "category_value_kept_native"),
])
@pytest.mark.parametrize("native", [{"allow": "passenger bicycle pedestrian"}, {"allow": "passenger"}])
def test_purpose_and_category_values_never_widen_or_narrow(value, reason, native):
    tags = {"motor_vehicle": "no", "foot": value, "bicycle": value}
    result = resolve_static_osm_access(tags, native)
    assert result["status"] == "pass" and result["review"] == []
    before, after = set(native["allow"].split()), set(result["attributes"].get("allow", "").split())
    for mode in ("bicycle", "pedestrian"):
        assert (mode in after) is (mode in before)
        [row] = [row for row in result["decisions"] if row["mode"] == mode]
        assert row["reason"] == reason and row["value"] == value and row["allowed"] is (mode in before)


@pytest.mark.parametrize("conditional,reason", [
    ("yes @ (Mo-Fr)", "conditional_time_window_without_scenario_time"),
    ("delivery @ (Mo-Fr); yes @ (Sa)", "conditional_time_window_without_scenario_time"),
    ("delivery @ (Mo-Fr", "conditional_directional_or_lane_access_not_evaluated"),
    ("yes", "conditional_directional_or_lane_access_not_evaluated"),
])
def test_time_windows_without_scenario_time_stay_open(conditional, reason):
    result = resolve_static_osm_access({"motor_vehicle": "no", "motor_vehicle:conditional": conditional}, {"allow": "passenger bus"})
    assert result["status"] == "review_required"
    assert {row["reason"] for row in result["review"]} == {reason}
    assert result["attributes"] == {"allow": "bus passenger"}


def test_dedicated_lane_modes_stay_on_their_lane():
    result = resolve_static_osm_access({"motor_vehicle": "no", "foot": "yes", "bicycle": "yes"}, {"disallow": "pedestrian bicycle"},
                                       keep_modes={"pedestrian", "bicycle"})
    assert set(result["attributes"]["disallow"].split()) >= {"pedestrian", "bicycle", "passenger"}
    assert {row["mode"] for row in _rule_rows(result, "mode_kept_for_dedicated_lane")} == {"pedestrian", "bicycle"}


@pytest.mark.parametrize("edge_id,direction", [("123", "forward"), ("123#2", "forward"), ("-123#0", "backward"),
                                                ("-123", "backward"), ("1234#0", None), ("cluster_1_2", None)])
def test_edge_direction_is_read_from_the_netconvert_edge_id(edge_id, direction):
    from astra_sumo.core.osm_access import _edge_direction
    assert _edge_direction(edge_id, "123") == direction


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
    routes = ET.Element("routes")
    for mode in ("bus", "delivery", "bicycle"):
        ET.SubElement(routes, "vType", id=mode, vClass=mode)
    for i, edge in enumerate(chosen):
        vehicle = ET.SubElement(routes, "vehicle", id=f"v{i}", type="bus", depart=str(i))
        ET.SubElement(vehicle, "route", edges=edge)
    route_file, tripinfo = tmp_path / "vehicles.rou.xml", tmp_path / "tripinfo.xml"
    ET.ElementTree(routes).write(route_file, encoding="utf-8", xml_declaration=True)
    port, native_log = getFreeSocketPort(), tmp_path / "native.log"
    expired = threading.Event()
    with native_log.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([checkBinary("sumo"), "-n", result["net_file"], "-r", str(route_file),
            "--remote-port", str(port), "--tripinfo-output", str(tripinfo), "--no-step-log", "true",
            "--error-log", str(tmp_path / "native-errors.log")],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        def stop_at_deadline():
            if process.poll() is not None:
                return
            expired.set()
            (tmp_path / "native-timeout.txt").write_text("The native permission probe exceeded 30 seconds.\n", encoding="utf-8")
            _terminate_process_tree(process)
        deadline = threading.Timer(30, stop_at_deadline)
        deadline.start()
        try:
            connection = traci.connect(port, numRetries=30, proc=process)
            for i, edge in enumerate(chosen):
                route_id = f"restricted-{i}"
                connection.route.add(route_id, [edge])
                for mode in ("delivery", "bicycle"):
                    vehicle_id = f"{mode}-{i}"
                    # Check native departure rejection without the fatal XML-error shutdown path.
                    with pytest.raises(traci.TraCIException, match="not allowed to depart on any lane of edge"):
                        connection.vehicle.add(vehicle_id, route_id, typeID=mode)
                    with pytest.raises(traci.TraCIException, match="not known"):
                        connection.vehicle.getVehicleClass(vehicle_id)
            connection.simulationStep(300)
            connection.close(wait=False)
            assert process.wait(timeout=15) == 0, native_log.read_text(encoding="utf-8")
        except traci.FatalTraCIError as error:
            raise AssertionError(f"Native permission connection closed. Deadline expired: {expired.is_set()}. Process exit: {process.poll()}. See native logs.") from error
        finally:
            deadline.cancel()
            if process.poll() is None:
                _terminate_process_tree(process)
            process.wait(timeout=5)
    assert {row.get("id") for row in ET.parse(tripinfo).getroot().findall("tripinfo")} == {"v0", "v1"}
