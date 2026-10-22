from copy import deepcopy
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_collision_trace import read_trace_source
from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages


ROOT = Path(__file__).parents[1]
DIAG = ROOT / "benchmarks/hamburg_ring1_terminal_queue_diagnosis_20261022_v1"
SHARED = ROOT / "benchmarks/hamburg_ring1_shared_field_probe_20261022_v1"


def test_snapshot_query_keeps_original_state_and_discloses_incomplete_control_restoration():
    base = ROOT / "benchmarks/hamburg_ring1_primary_entry_full_20261022_v1"
    original = ET.fromstring(read_trace_source(base / "native/states", "state_14399.900.xml"))
    private = ET.fromstring(read_trace_source(DIAG / "inspection", "inspection-state.xml"))
    expected = deepcopy(original)
    empty = []
    for row in expected.findall("tlLogic"):
        if row.get("state") == "":
            empty.append(row.get("id"))
            del row.attrib["state"]
    assert empty == ["7709833318", "7709833318"] and ET.tostring(private) == ET.tostring(expected)
    report = json.loads(read_trace_source(DIAG / "analysis", "snapshot-correspondence.json"))
    native = json.loads(read_trace_source(DIAG / "inspection", "native-state-query.json"))
    assert native["native_version"] == [22, "SUMO 1.27.1"]
    assert report["simulation_steps"] == 0 and report["time_unchanged"] and not report["formal_result"]
    assert report["active_vehicles_verified"] == 306 and report["original_unentered_vehicle_count"] == 644
    assert report["loaded_pending_api_count"] == 0 and report["loaded_pending_api_is_not_the_original_insertion_queue"]
    assert report["mode_mismatches"] == 24 and report["mode_mismatches_by_recorded_hold"] == {"field": 19, "junction": 5}


def test_buffer_lane_probe_retains_no_count_gain_and_the_native_stop_tradeoff():
    result = json.loads(read_trace_source(DIAG / "lane-probe", "paired-check.json"))["results"]
    for row in result.values():
        assert row["native_version"] == [22, "SUMO 1.27.1"]
        assert row["healthy"] and row["observer_complete"] and row["exact_field_bins"] == 13
        assert row["completion"]["arrived"] == 40 and row["completion"]["collisions"] == row["completion"]["teleports"] == 0
        assert row["field_passages"] == 40 and row["every_vehicle_once_at_assigned_field"]
    assert result["baseline"]["sum_native_stop_time_s"] == 0
    assert result["two_lane_intent"]["sum_native_stop_time_s"] == pytest.approx(.1)
    assert result["baseline"]["last_native_arrival_s"] == result["two_lane_intent"]["last_native_arrival_s"] == 680.2
    checked = json.loads(read_trace_source(DIAG / "analysis", "buffer-probe-check.json"))
    assert not checked["promoted_as_capacity_fix"] and not checked["formal_result"]
    assert checked["baseline_last_E1_time_s"] == checked["candidate_last_E1_time_s"]
    assert all(r["same_every_FCD_vehicle_sample"] and r["same_tripinfo_attributes"] for r in checked["observation_neutrality"].values())


def test_shared_routes_have_eighty_distinct_native_contributions_from_forty_vehicles(tmp_path):
    run = json.loads(read_trace_source(SHARED / "native", "run.json"))
    for name in ("instant-e1.xml", "field-counts.xml"):
        (tmp_path / name).write_bytes(read_trace_source(SHARED / "native", name))
    measured = read_contributing_e1_passages(instant_file=tmp_path / "instant-e1.xml", periodic_file=tmp_path / "field-counts.xml",
        detector_ids=run["instant_to_periodic_ids"], step_length=.1)
    assert measured["reconciled"] and len(measured["passages"]) == 80
    proposal = json.loads(read_trace_source(SHARED, "proposal.json"))
    expected = proposal["expected_fields_by_vehicle"]
    actual = {}
    for row in measured["passages"]:
        key = row["vehicle_id"], row["detector_id"]
        assert key not in actual
        actual[key] = row["time_s"]
    assert len(expected) == 40 and len(actual) == 80
    for identity, fields in expected.items():
        assert len(fields) == len(set(fields)) == 2
        assert actual[identity, fields[0]] < actual[identity, fields[1]]
    assert all(audit["allowed"] for audit in proposal["route_audits"].values())
    result = json.loads(read_trace_source(SHARED, "joint-response-check.json"))
    assert result["native_version"] == [22, "SUMO 1.27.1"]
    assert result["completion"]["arrived"] == 40 and result["completion"]["collisions"] == result["completion"]["teleports"] == 0
    assert result["sum_native_stop_time_s"] == 0 and result["observer_complete"] and not result["formal_result"]
    checked = json.loads(read_trace_source(SHARED / "analysis", "shared-route-check.json"))
    assert not checked["driver_schema_changed"] and not checked["larger_shared_population_validated"]
