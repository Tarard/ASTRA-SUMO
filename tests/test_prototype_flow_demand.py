import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.route_sensor_matrix import write_prototype_flow_demand


def test_prototype_schedule_preserves_boundary_inputs_and_places_all_passages_inside_bins(tmp_path):
    routes, trips, output = (tmp_path / name for name in ("source.xml", "trips.xml", "proposal.xml"))
    routes.write_text('''<routes><vType id="car" vClass="passenger"/><vehicle id="p" depart="0" type="car">
      <route edges="road_under_score exit"/></vehicle></routes>''', encoding="utf-8")
    trips.write_text('''<tripinfos><tripinfo id="p" arrival="100" departLane="road_under_score_1"
      departPos="5.1" speedFactor="0.95" vaporized=""/></tripinfos>''', encoding="utf-8")
    original = {path: path.read_bytes() for path in (routes, trips)}
    arguments = dict(source_routes=routes, source_tripinfo=trips,
        passages=[{"vehicle_id": "p", "notification_time_s": 10}, {"vehicle_id": "p", "notification_time_s": 50}],
        vehicle_counts=[[2], [1]], comparison_begin_s=900, bin_seconds=900, step_length=.1, output_file=output)
    result = write_prototype_flow_demand(**arguments)
    assert result["status"] == "proposal" and result["vehicle_count"] == 3 and result["inputs_unchanged"]
    root = ET.parse(output).getroot()
    assert root.find("vType").get("id") == "car"
    vehicles = root.findall("vehicle")
    assert [float(row.get("depart")) for row in vehicles] == sorted(float(row.get("depart")) for row in vehicles)
    for vehicle, assignment in zip(vehicles, sorted(result["assignments"], key=lambda row: row["planned_depart_s"])):
        assert vehicle.get("departLane") == "1" and vehicle.get("departPos") is None
        assert vehicle.get("speedFactor") == "0.95" and vehicle.find("route").get("edges") == "road_under_score exit"
        begin = 900 + assignment["target_bin"] * 900
        assert all(begin < float(vehicle.get("depart")) + delay < begin + 900 for delay in (10, 50))
    assert all(path.read_bytes() == data for path, data in original.items())
    with pytest.raises(ValueError, match="new file"):
        write_prototype_flow_demand(**arguments)


def test_static_prototype_cannot_hide_a_cross_bin_profile(tmp_path):
    routes, trips = tmp_path / "source.xml", tmp_path / "trip.xml"
    routes.write_text('<routes><vehicle id="p" depart="0"><route edges="a b"/></vehicle></routes>', encoding="utf-8")
    trips.write_text('<tripinfos><tripinfo id="p" arrival="1000" departLane="a_0"/></tripinfos>', encoding="utf-8")
    with pytest.raises(ValueError, match="time-expanded"):
        write_prototype_flow_demand(source_routes=routes, source_tripinfo=trips,
            passages=[{"vehicle_id": "p", "notification_time_s": 1}, {"vehicle_id": "p", "notification_time_s": 1000}],
            vehicle_counts=[[1]], comparison_begin_s=1800, bin_seconds=900, step_length=.1,
            output_file=tmp_path / "unused.xml")
    assert not (tmp_path / "unused.xml").exists()
