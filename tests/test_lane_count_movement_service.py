import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.corridor_edit_ledger import materialize_corridor_edit_variant
from astra_sumo.core.network_edit_operations import _lane_count_connections, _lane_count_road_movements


def test_removed_bus_lane_does_not_transfer_bus_access_to_a_car_lane():
    root = ET.fromstring('''<net version="1.20">
      <edge id="in"><lane id="in_0" allow="passenger bus"/></edge>
      <edge id="road"><lane id="road_0" allow="bus"/><lane id="road_1" allow="passenger"/></edge>
      <edge id="right"><lane id="right_0" allow="passenger bus"/></edge>
      <connection from="in" to="road" fromLane="0" toLane="0" allow="bus"/>
      <connection from="in" to="road" fromLane="0" toLane="1" allow="passenger"/>
      <connection from="road" to="right" fromLane="0" toLane="0" allow="bus"/>
      <connection from="road" to="right" fromLane="1" toLane="0" allow="passenger"/>
    </net>''')
    params = {"edge_id": "road", "num_lanes": 1, "remove_index": 0}
    records = _lane_count_connections(params, root)
    assert len(records) == 2
    assert all(row["allow"] == "passenger" for row in records)
    before = {("in", 0, "road", 0): {"bus"}, ("in", 0, "road", 1): {"passenger"},
              ("road", 0, "right", 0): {"bus"}, ("road", 1, "right", 0): {"passenger"}}
    after = {("in", 0, "road", 0): {"passenger"}, ("road", 0, "right", 0): {"passenger"}}
    report = _lane_count_road_movements(before, after, params)
    assert report["status"] == "fail"
    assert report["lost"] == [{"from": "in", "to": "road", "vehicle_classes": ["bus"]},
                              {"from": "road", "to": "right", "vehicle_classes": ["bus"]}]
    assert _lane_count_road_movements(before, after, {**params, "connections": []})["status"] == "review_required"


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
@pytest.mark.parametrize("layout", ["add_right", "remove_right"])
def test_native_lane_count_keeps_unique_turn_and_serves_new_lane(tmp_path, layout):
    nodes, edges, turns = [tmp_path / name for name in ("nodes.xml", "edges.xml", "turns.xml")]
    nodes.write_text('''<nodes><node id="W" x="-100" y="0"/><node id="A" x="0" y="0"/>
      <node id="B" x="100" y="0"/>
      <node id="E" x="200" y="0"/><node id="S" x="100" y="-100"/></nodes>''', encoding="utf-8")
    edges.write_text('''<edges><edge id="in" from="W" to="A" allow="passenger bus"/>
      <edge id="road" from="A" to="B" numLanes="2" allow="passenger bus"/>
      <edge id="out" from="B" to="E" allow="passenger bus"/>
      <edge id="right" from="B" to="S" allow="passenger bus"/></edges>''', encoding="utf-8")
    turns.write_text('''<connections><connection from="in" to="road" fromLane="0" toLane="0"/>
      <connection from="in" to="road" fromLane="0" toLane="1"/>
      <connection from="road" to="right" fromLane="0" toLane="0"/>
      <connection from="road" to="out" fromLane="1" toLane="0"/></connections>''', encoding="utf-8")
    source = tmp_path / "source.net.xml"
    subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-x", str(turns),
                    "--offset.disable-normalization", "true", "-o", str(source)],
                   check=True, capture_output=True, timeout=30)
    digest = file_sha256(source)
    params = {"edge_id": "road", "num_lanes": 3, "add_side": "right"} if layout == "add_right" else {
        "edge_id": "road", "num_lanes": 1, "remove_index": 0}
    operation = {"id": layout, "operation": "set_lane_count", "params": params, "status": "accepted",
        "rationale": "Retain the unique source right turn and serve the added lane.",
        "evidence": [{"path": str(source), "sha256": digest}], "rollback": {"action": "discard_candidate"}}
    result = materialize_corridor_edit_variant(output_dir=tmp_path / "edit", ledger={
        "source_net_file": str(source), "source_sha256": digest, "operations": [operation]},
        netconvert_binary=shutil.which("netconvert"))
    assert result["status"] == "pass", result
    checks = result["semantic_validation"]
    assert checks["road_movement_preservation"]["status"] == "pass"
    assert checks["road_movement_preservation"]["lost"] == []
    if layout == "add_right":
        assert checks["new_lane_service"]["status"] == "pass"
        assert checks["new_lane_service"]["lanes"][0]["vehicle_classes"] == ["bus", "passenger"]
        assert checks["new_lane_service"]["lanes"][0]["incoming_movement_count"] == 1
        assert checks["new_lane_service"]["lanes"][0]["outgoing_movement_count"] == 1
    assert checks["signal_program_preservation"]["status"] == "pass"
    routes = tmp_path / "probe.rou.xml"
    routes.write_text('''<routes><vType id="car" vClass="passenger"/><vType id="bus" vClass="bus"/>
      <vehicle id="new-or-reassigned-car" type="car" depart="0" departLane="0"><route edges="road right"/></vehicle>
      <vehicle id="source-turn-bus" type="bus" depart="5" departLane="0"><route edges="road right"/></vehicle>
      <vehicle id="through" type="car" depart="10"><route edges="in road out"/></vehicle></routes>''', encoding="utf-8")
    tripinfo = tmp_path / "tripinfo.xml"
    driven = subprocess.run(["sumo", "-n", result["candidate_net_file"], "-r", str(routes),
        "--end", "300", "--collision.action", "warn", "--collision.check-junctions", "true",
        "--tripinfo-output", str(tripinfo), "--no-step-log", "true"],
        capture_output=True, text=True, timeout=30)
    assert driven.returncode == 0, driven.stderr
    assert "collision" not in driven.stderr.lower()
    trips = {row.get("id"): row for row in ET.parse(tripinfo).getroot()}
    assert set(trips) == {"new-or-reassigned-car", "source-turn-bus", "through"}
    assert all(float(row.get("arrival")) >= 0 for row in trips.values())
    assert trips["new-or-reassigned-car"].get("departLane") == "road_0"
    assert trips["source-turn-bus"].get("arrivalLane") == "right_0"
    assert file_sha256(source) == digest
