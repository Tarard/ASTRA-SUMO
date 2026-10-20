from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.routeability_audit import run_routeability_audit
from astra_sumo.core.routeability_audit import _inspect_vehicle_routes
from astra_sumo.core.routeability_od_mapping import _lane_endpoint


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


@pytest.mark.parametrize("suffix", [".source.trips.xml", ".od-map.json"])
def test_endpoint_mapping_outputs_cannot_replace_an_input(tmp_path, suffix):
    source = tmp_path / ("probe" + suffix)
    source.write_text('<net/>', encoding="utf-8")
    digest = file_sha256(source)
    result = run_routeability_audit(net_file=source, output_dir=tmp_path, prefix="probe")
    assert result["status"] == "fail"
    assert "overlap" in result["error"]
    assert file_sha256(source) == digest


def test_routing_must_retain_explicit_endpoint_positions(tmp_path):
    source, trips = tmp_path / "source.net.xml", tmp_path / "fixed.trips.xml"
    source.write_text('<net/>', encoding="utf-8")
    trips.write_text('<routes><trip id="fixed" depart="0" from="a" to="b" departPos="5"/></routes>', encoding="utf-8")
    def runner(command, **kwargs):
        assert command[0] == "duarouter", "Simulation must not run after routing changes an endpoint."
        Path(command[command.index("--output-file") + 1]).write_text(
            '<routes><vehicle id="fixed" depart="0" departPos="6"><route edges="a b"/></vehicle></routes>', encoding="utf-8")
        return {"status": "pass", "returncode": 0}
    result = run_routeability_audit(net_file=source, output_dir=tmp_path / "audit", vehicle_count=1,
        frozen_trip_file=trips, expected_frozen_trip_sha256=file_sha256(trips),
        binaries={"duarouter": "duarouter", "sumo": "sumo"}, command_runner=runner)
    assert result["status"] == "fail"
    assert "endpoint attribute" in result["route_generation"]["error"]


@pytest.mark.skipif(any(not shutil.which(name) for name in ("netconvert", "sumo", "duarouter")), reason="SUMO programs required")
@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("lane_count", [1, 2])
def test_native_merged_od_retains_original_requests_and_physical_positions(tmp_path, frozen, lane_count):
    nodes, edges = tmp_path / "nodes.xml", tmp_path / "edges.xml"
    nodes.write_text('<nodes><node id="w" x="0" y="0"/><node id="m" x="50" y="0"/>'
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
    tree = ET.parse(tripinfo)
    tree.getroot().find("tripinfo").set("departPos", "0")
    tree.write(tripinfo, encoding="utf-8")
    failed = _inspect_vehicle_routes(Path(result["route_file"]), Path(attempt["vehicle_route_file"]),
                                     len(mapped), 300, tripinfo)
    assert failed["status"] == "fail"
    assert any(row["reason"] == "vehicle_endpoint_position_changed" for row in failed["failures"])

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
        assert any("current arrival lane" in row["reason"] for row in unresolved["unresolved_requests"])
