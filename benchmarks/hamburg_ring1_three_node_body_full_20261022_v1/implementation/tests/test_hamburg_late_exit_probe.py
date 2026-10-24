import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.hamburg_collision_trace import read_trace_source


CASE = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_late_exit_body_probe_20261022_v1"


def test_paired_maneuver_keeps_traffic_fixed_and_delays_exit_changes_until_rear_clear():
    paired = json.loads(read_trace_source(CASE, "paired-check.json"))
    assert not paired["formal_result"]
    for label, result in paired["results"].items():
        assert result["healthy"] and result["native_version"] == [22, "SUMO 1.27.1"]
        assert result["completion"] == {"loaded": 40, "inserted": 40, "arrived": 40,
            "running": 0, "waiting": 0, "teleports": 0, "collisions": 0, "discarded": 0}
        assert result["observer_complete"] and result["exact_field_bins"] == 13 and not result["changes_during_body_hold"]
        assert len(result["exit_changes"]) == 20
        request = json.loads(read_trace_source(CASE / label, "native-request.json"))
        assert request["end_s"] == 900 and request["all_stopped_timeout_s"] == 300
        assert request["junction_body_hold_nodes"] == ["14398547", "205069138"] + (["18653534"] if label == "three_nodes" else [])
        routes = ET.fromstring(read_trace_source(CASE / label, "demand.rou.xml"))
        assert len(routes.findall("vehicle")) == 40
        for vehicle in routes.findall("vehicle"):
            roads = vehicle.find("route").get("edges").split()
            assert len(roads) == len(set(roads))
    baseline, candidate = (paired["results"][label] for label in ("two_nodes", "three_nodes"))
    assert sum(float(row["pos"]) < 5 for row in baseline["exit_changes"]) == 8
    assert all(float(row["pos"]) >= 5 for row in candidate["exit_changes"])
    assert candidate["target_node_holds"] == 40
    for name in ("network.net.xml", "demand.rou.xml", "signal-programs.add.xml", "source-e1.add.xml", "expected-counts.csv", "lane-intentions.json"):
        assert read_trace_source(CASE / "two_nodes", name) == read_trace_source(CASE / "three_nodes", name)


def test_passenger_permissions_and_identity_audit_do_not_promote_artificial_counts():
    audit = json.loads(read_trace_source(CASE, "passenger-route-audit.json"))
    assert not audit["formal_result"] and not audit["lane_permissions_changed"]
    assert audit["passenger_route_exit_lanes"] == ["2"]
    assert audit["bicycle_only_lane"] == "139083723#0_1"
    assert sum(row["model_requests"] for row in audit["movements"]) == 1082
    assert all(row["outlet_length_m"] == [38.38] for row in audit["movements"])
    identity = json.loads(read_trace_source(CASE, "identity-check.json"))
    assert identity["fixed_inputs_equal"] and not identity["formal_result"]
    assert all(row["unique_assigned_passages"] == 40 and row["repeated_or_other_fields"] == 0
        and row["sum_native_stop_time_s"] == 0 for row in identity["results"].values())
