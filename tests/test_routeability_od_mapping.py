from copy import deepcopy
import math
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.routeability_audit import run_routeability_audit
from astra_sumo.core.routeability_audit import _inspect_vehicle_routes
from astra_sumo.core.routeability_od_mapping import _lane_endpoint
from astra_sumo.core.routeability_od_mapping import _geometry, _point_at


def test_endpoint_projection_uses_logical_lengths_and_keeps_internal_gap():
    source = ET.fromstring('<lane id="b_0" length="40" shape="12,0 22,0"/>')
    target = ET.fromstring('<lane id="a_0" length="88" shape="0,0 22,0"/>')
    result = _lane_endpoint(source, target, "10")
    assert result["source_point_xyz_m"] == (14.5, 0, 0)
    assert result["candidate_position_m"] == pytest.approx(58)
    assert result["projection_error_m"] == 0
    assert _lane_endpoint(source, target, "-10")["candidate_position_m"] == pytest.approx(78)


@pytest.mark.parametrize("shape", ["0,0 22,0 0,0", "22,0 0,0", "0,0,5 22,0,5"])
def test_endpoint_projection_rejects_ambiguity_reverse_direction_and_wrong_elevation(shape):
    source = ET.fromstring('<lane id="b_0" length="40" shape="12,0 22,0"/>')
    target = ET.Element("lane", id="a_0", length="88", shape=shape)
    with pytest.raises(ValueError, match="unique|direction"):
        _lane_endpoint(source, target, "10")


def test_endpoint_projection_retains_elevation_and_ignores_duplicate_vertices():
    source = ET.fromstring('<lane id="b_0" length="40" shape="10,0,10 10,0,10 20,0,20"/>')
    target = ET.fromstring('<lane id="a_0" length="80" shape="0,0,0 20,0,20 20,0,20"/>')
    result = _lane_endpoint(source, target, "20")
    assert result["source_point_xyz_m"] == pytest.approx((15, 0, 15))
    assert result["candidate_position_m"] == pytest.approx(60)


@pytest.mark.parametrize("suffix", [".source.trips.xml", ".routable.trips.xml", ".od-map.json"])
def test_endpoint_mapping_outputs_cannot_replace_an_input(tmp_path, suffix):
    source = tmp_path / ("probe" + suffix)
    source.write_text('<net/>', encoding="utf-8")
    digest = file_sha256(source)
    result = run_routeability_audit(net_file=source, output_dir=tmp_path, prefix="probe")
    assert result["status"] == "fail"
    assert "overlap" in result["error"]
    assert file_sha256(source) == digest


@pytest.mark.parametrize("attribute,value,output", [("departPos", "5", 'departPos="6"'), ("departLane", "first", "")])
def test_routing_must_retain_explicit_endpoint_positions(tmp_path, attribute, value, output):
    source, trips = tmp_path / "source.net.xml", tmp_path / "fixed.trips.xml"
    source.write_text('<net/>', encoding="utf-8")
    trips.write_text(f'<routes><trip id="fixed" depart="0" from="a" to="b" {attribute}="{value}"/></routes>', encoding="utf-8")
    def runner(command, **kwargs):
        assert command[0] == "duarouter", "Simulation must not run after routing changes an endpoint."
        Path(command[command.index("--output-file") + 1]).write_text(
            f'<routes><vehicle id="fixed" depart="0" {output}><route edges="a b"/></vehicle></routes>', encoding="utf-8")
        return {"status": "pass", "returncode": 0}
    result = run_routeability_audit(net_file=source, output_dir=tmp_path / "audit", vehicle_count=1,
        frozen_trip_file=trips, expected_frozen_trip_sha256=file_sha256(trips),
        binaries={"duarouter": "duarouter", "sumo": "sumo"}, command_runner=runner)
    assert result["status"] == "fail"
    assert "endpoint attribute" in result["route_generation"]["error"]


def _compiled_chain(tmp_path, lane_count=1, first_length=50):
    nodes, edges = tmp_path / "nodes.xml", tmp_path / "edges.xml"
    nodes.write_text(f'<nodes><node id="w" x="0" y="0"/><node id="m" x="{first_length}" y="0"/>'
                     '<node id="e" x="120" y="0"/><node id="s" x="180" y="0"/></nodes>', encoding="utf-8")
    edges.write_text(f'<edges><edge id="a" from="w" to="m" allow="passenger" numLanes="{lane_count}"/>'
                     f'<edge id="b" from="m" to="e" allow="passenger" numLanes="{lane_count}"/>'
                     f'<edge id="tail" from="e" to="s" allow="passenger" numLanes="{lane_count}"/></edges>', encoding="utf-8")
    source, candidate = tmp_path / "source.net.xml", tmp_path / "candidate.net.xml"
    subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "--offset.disable-normalization", "true",
                    "-o", str(source)], check=True, capture_output=True, timeout=30)
    subprocess.run(["netconvert", "-s", str(source), "--geometry.remove", "true",
                    "--output.removed-nodes", "true",
                    "--geometry.remove.keep-edges.explicit", "tail", "--offset.disable-normalization", "true",
                    "-o", str(candidate)], check=True, capture_output=True, timeout=30)
    return source, candidate


@pytest.mark.skipif(any(not shutil.which(name) for name in ("netconvert", "sumo", "duarouter")), reason="SUMO programs required")
@pytest.mark.parametrize("lane_count,frozen,first_length", [(1, False, 50), (1, True, 50), (2, False, 50), (2, True, 50), (1, False, 5.05)])
def test_native_merged_od_retains_original_requests_and_physical_positions(tmp_path, frozen, lane_count, first_length):
    source, candidate = _compiled_chain(tmp_path, lane_count, first_length)
    before = {path: file_sha256(path) for path in (source, candidate)}
    options = {}
    if frozen:
        trips = tmp_path / "original.trips.xml"
        trips.write_text('<routes><trip id="fixed" depart="0" from="a" to="b" '
                         'departLane="0" arrivalLane="0" departPos="10" arrivalPos="-10"/></routes>', encoding="utf-8")
        options = dict(frozen_trip_file=trips, expected_frozen_trip_sha256=file_sha256(trips))
        before[trips] = file_sha256(trips)
    result = run_routeability_audit(net_file=candidate, od_reference_net_file=source, output_dir=tmp_path / "audit",
                                   vehicle_count=1 if frozen else 30, seed=104, initial_end=300, max_end=300, **options)
    assert result["status"] == "pass", result
    mapping = result["od_policy"]["endpoint_mapping"]
    assert mapping["status"] == "pass" and mapping["changed"]
    assert not mapping["unresolved_requests"]
    original = ET.parse(result["source_trip_file"]).getroot().findall("trip")
    mapped = ET.parse(result["trip_file"]).getroot().findall("trip")
    assert len(mapped) == len(original) == (1 if frozen else 30)
    assert [(row.get("id"), row.get("depart")) for row in original] == [(row.get("id"), row.get("depart")) for row in mapped]
    if not frozen:
        assert {row.get("from") for row in original} == {"a", "b"}
        assert any(row.get("from") == row.get("to") for row in mapped)
    else:
        assert Path(result["source_trip_file"]).read_bytes() == trips.read_bytes()
    baseline = run_routeability_audit(net_file=source, output_dir=tmp_path / "source-audit",
        vehicle_count=len(original), seed=104, initial_end=300, max_end=300,
        frozen_trip_file=Path(result["source_trip_file"]),
        expected_frozen_trip_sha256=file_sha256(Path(result["source_trip_file"])))
    assert baseline["status"] == "pass", baseline
    observed, lane_sets = [], []
    for report, network in ((baseline, source), (result, candidate)):
        lanes = {lane.get("id"): lane for lane in ET.parse(network).getroot().iter("lane")}
        positions = {}
        for row in ET.parse(report["final_attempt"]["tripinfo_file"]).getroot().findall("tripinfo"):
            endpoints = {}
            for role in ("depart", "arrival"):
                points, offsets, length = _geometry(lanes[row.get(role + "Lane")])
                endpoints[role] = {"point": _point_at(points, offsets, float(row.get(role + "Pos")) / length * offsets[-1]),
                                   "position": float(row.get(role + "Pos")), "lane_index": lanes[row.get(role + "Lane")].get("index")}
            positions[row.get("id")] = endpoints
        observed.append(positions)
        lane_sets.append(lanes)
    assert observed[0].keys() == observed[1].keys()
    requests_by_id = {row.get("id"): row for row in original}
    for identity in observed[0]:
        for role, key in (("depart", "from"), ("arrival", "to")):
            reference = observed[0][identity][role]["point"]
            if requests_by_id[identity].get(role + "Lane") is None:
                # Default lane choice can change with queues. Check its original lane endpoint set.
                lane = lane_sets[0][f"{requests_by_id[identity].get(key)}_{observed[1][identity][role]['lane_index']}"]
                points, offsets, length = _geometry(lane)
                position = length if role == "arrival" else observed[0][identity][role]["position"]
                reference = _point_at(points, offsets, position / length * offsets[-1])
            assert math.dist(reference, observed[1][identity][role]["point"]) <= 0.02
    assert all(file_sha256(path) == digest for path, digest in before.items())
    for record in mapping["requests"]:
        for endpoint in record["endpoints"]:
            assert max(lane["projection_error_m"] for lane in endpoint["lanes"]) < 1e-6
    # duarouter must keep the mapped positions, not just the renamed edge IDs.
    routed = {row.get("id"): row for row in ET.parse(result["route_file"]).getroot().findall("vehicle")}
    for row in mapped:
        for name in ("departPos", "arrivalPos"):
            if row.get(name) is not None:
                assert float(routed[row.get("id")].get(name)) == pytest.approx(float(row.get(name)))
    attempt = result["final_attempt"]
    tripinfo = Path(attempt["tripinfo_file"])
    original_tripinfo = tripinfo.read_bytes()
    tree = ET.parse(tripinfo)
    tree.getroot().find("tripinfo").set("departPos", "0")
    tree.write(tripinfo, encoding="utf-8")
    failed = _inspect_vehicle_routes(Path(result["route_file"]), Path(attempt["vehicle_route_file"]),
                                     len(mapped), 300, tripinfo)
    assert failed["status"] == "fail"
    assert any(row["reason"] == "vehicle_endpoint_position_changed" for row in failed["failures"])
    if frozen:
        tree = ET.ElementTree(ET.fromstring(original_tripinfo))
        tree.getroot().find("tripinfo").set("departLane", "wrong-road_99")
        tree.write(tripinfo, encoding="utf-8")
        failed = _inspect_vehicle_routes(Path(result["route_file"]), Path(attempt["vehicle_route_file"]),
                                         len(mapped), 300, tripinfo)
        assert failed["status"] == "fail"
        assert any(row["reason"] == "vehicle_endpoint_lane_changed" for row in failed["failures"])

    from astra_sumo.core.routeability_od_mapping import map_merged_od_requests

    broken = deepcopy(ET.parse(candidate).getroot())
    merged = next(edge for edge in broken.findall("edge") if edge.find("param[@key='removedNodeIds']") is not None)
    broken.remove(merged)
    _, unresolved = map_merged_od_requests(ET.parse(source).getroot(), broken, ET.parse(result["source_trip_file"]).getroot())
    assert unresolved["status"] == "review_required"
    assert unresolved["request_count"] == len(original)
    assert unresolved["unresolved_requests"]
    if lane_count == 2 and not frozen:
        unequal = ET.parse(candidate).getroot()
        merged = next(edge for edge in unequal.findall("edge") if edge.find("param[@key='removedNodeIds']") is not None)
        lane = merged.find("lane[@index='1']")
        lane.set("length", str(float(lane.get("length")) + 3))
        _, unresolved = map_merged_od_requests(ET.parse(source).getroot(), unequal, ET.parse(result["source_trip_file"]).getroot())
        assert unresolved["status"] == "review_required"
        assert unresolved["request_count"] == len(original)
        assert any("lane set" in row["reason"] for row in unresolved["unresolved_requests"])


@pytest.mark.skipif(any(not shutil.which(name) for name in ("netconvert", "sumo", "duarouter")), reason="SUMO programs required")
@pytest.mark.parametrize("lost_connection", [False, True])
def test_native_unresolved_endpoint_does_not_hide_other_route_results(tmp_path, lost_connection):
    source, _ = _compiled_chain(tmp_path)
    root = ET.parse(source).getroot()
    lane = root.find("edge[@id='a']/lane")
    lane.set("shape", " ".join(f"{float(value.split(',')[0])},{float(value.split(',')[1]) + 1}" for value in lane.get("shape").split()))
    if lost_connection:
        root.remove(root.find("connection[@from='b'][@to='tail']"))
    candidate = tmp_path / "changed.net.xml"
    ET.ElementTree(root).write(candidate, encoding="utf-8")
    trips = tmp_path / "requests.xml"
    trips.write_text('<routes><trip id="uncertain" depart="0" from="a" to="b"/>'
                     '<trip id="other" depart="30" from="b" to="tail"/></routes>', encoding="utf-8")
    source_hash, request_hash = file_sha256(source), file_sha256(trips)
    baseline = run_routeability_audit(net_file=source, frozen_trip_file=trips,
        expected_frozen_trip_sha256=request_hash, output_dir=tmp_path / "baseline", vehicle_count=2, initial_end=300, max_end=300)
    assert baseline["status"] == "pass", baseline
    result = run_routeability_audit(net_file=candidate, od_reference_net_file=source, frozen_trip_file=trips,
        expected_frozen_trip_sha256=request_hash, output_dir=tmp_path / "candidate", vehicle_count=2, initial_end=300, max_end=300)
    assert len(ET.parse(result["trip_file"]).getroot().findall("trip")) == 2
    mapping = result["od_policy"]["endpoint_mapping"]
    assert mapping["physical_request_count"] == mapping["logical_only_request_count"] == 1
    assert [row["vehicle_id"] for row in mapping["unresolved_requests"]] == ["uncertain"]
    assert result["route_generation"]["command"], "Every request must reach the strict native router."
    from astra_sumo.core.candidate_contracts import validate_routeability_evidence
    checked = validate_routeability_evidence(result, candidate_net_file=candidate)
    if lost_connection:
        assert result["status"] == "fail"
        assert result["routeability_status"] == "route-generation-failed"
        assert checked["status"] == "blocked"
    else:
        assert result["status"] == "review_required"
        assert result["routeability_status"] == "unverified_endpoints"
        assert result["final_attempt"]["vehicle_routes"]["complete_count"] == 2
        assert checked["status"] == "review_required" and not checked["errors"]
    assert file_sha256(source) == source_hash and file_sha256(trips) == request_hash


@pytest.mark.skipif(any(not shutil.which(name) for name in ("netconvert", "sumo", "duarouter")), reason="SUMO programs required")
@pytest.mark.parametrize("case", ["remaining_ok", "remaining_disconnected", "all_removed"])
def test_native_removed_endpoints_do_not_abort_remaining_requests(tmp_path, case):
    source, _ = _compiled_chain(tmp_path)
    candidate = tmp_path / "deleted.net.xml"
    subprocess.run(["netconvert", "-s", str(source), "--remove-edges.explicit", "a",
                    "--offset.disable-normalization", "true", "-o", str(candidate)], check=True, capture_output=True, timeout=30)
    if case == "remaining_disconnected":
        tree = ET.parse(candidate)
        tree.getroot().remove(tree.getroot().find("connection[@from='b'][@to='tail']"))
        tree.write(candidate, encoding="utf-8")
    root = ET.fromstring('<routes><trip id="removed" depart="0" from="a" to="b"/></routes>')
    if case != "all_removed":
        ET.SubElement(root, "trip", id="retained", depart="30", attrib={"from": "b", "to": "tail"})
    trips = tmp_path / "original.xml"
    ET.ElementTree(root).write(trips, encoding="utf-8")
    count, digest = len(root.findall("trip")), file_sha256(trips)
    result = run_routeability_audit(net_file=candidate, od_reference_net_file=source, frozen_trip_file=trips,
        expected_frozen_trip_sha256=digest, output_dir=tmp_path / "audit", vehicle_count=count, initial_end=300, max_end=300)
    assert file_sha256(trips) == digest
    assert len(ET.parse(result["trip_file"]).getroot().findall("trip")) == count
    assert file_sha256(Path(result["source_trip_file"])) == digest
    executable = ET.parse(result["routing_trip_file"]).getroot().findall("trip")
    assert [row.get("id") for row in executable] == ([] if case == "all_removed" else ["retained"])
    coverage = result["cohort_coverage"]
    assert coverage["requested_count"] == count and coverage["removed_endpoint_count"] == 1
    assert coverage["routing_count"] == len(executable)
    assert coverage["removed_endpoint_vehicle_ids"] == ["removed"]
    assert coverage["physical_count"] + coverage["logical_only_count"] + coverage["removed_endpoint_count"] == count
    from astra_sumo.core.candidate_contracts import validate_routeability_evidence
    checked = validate_routeability_evidence(result, candidate_net_file=candidate)
    malformed = {**result, "cohort_coverage": {**coverage, "removed_endpoint_vehicle_ids": None}}
    rejected = validate_routeability_evidence(malformed, candidate_net_file=candidate)
    assert any(row["code"] == "routeability_invalid_cohort_counts" for row in rejected["errors"])
    if case == "remaining_disconnected":
        assert result["status"] == "fail" and result["routeability_status"] == "route-generation-failed"
        assert "not known" not in result["route_generation"].get("stderr", "")
        assert checked["status"] == "blocked"
    else:
        assert result["status"] == checked["status"] == "review_required"
        assert not checked["errors"]
        if case == "all_removed":
            assert result["execution_status"] == "not_run" and result["attempts"] == []
            assert result["route_generation"] == {"status": "not_run"}
        else:
            assert result["execution_status"] == "partial"
            assert result["final_attempt"]["vehicle_routes"]["complete_count"] == 1
            forged = {**result, "status": "pass", "routeability_status": "pass"}
            assert validate_routeability_evidence(forged, candidate_net_file=candidate)["status"] == "blocked"
