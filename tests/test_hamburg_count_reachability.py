import csv
import json

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.hamburg_count_reachability import audit_hamburg_count_reachability


def test_count_influence_respects_vehicle_permissions_and_keeps_unknown_outlets(tmp_path):
    network = tmp_path / "network.xml"
    network.write_text('''<net version="1.20">
      <edge id="out" from="junction" to="next"><lane id="out_0" index="0" allow="passenger bus" changeLeft="bus"/>
        <lane id="out_1" index="1" allow="bus"/></edge>
      <edge id="car" from="next" to="car-exit"><lane id="car_0" index="0" allow="passenger"/></edge>
      <edge id="bus" from="next" to="bus-exit"><lane id="bus_0" index="0" allow="bus"/></edge>
      <connection from="out" fromLane="0" to="car" toLane="0"/>
      <connection from="out" fromLane="1" to="bus" toLane="0"/>
    </net>''', encoding="utf-8")
    def movement(identity, egress, restriction=None):
        return {"node_id": "1", "intersection_part": "0", "movement_id": identity,
                "egress_lane_id": egress, "allowed_vehicle_classes": restriction}
    manifest = tmp_path / "candidate.json"
    manifest.write_text(json.dumps({"artifacts": {"network": {"path": str(network), "sha256": file_sha256(network)}},
        "official_connection_audit": {
            "ambiguous": [movement("all", "eg"), movement("bus-only", "eg", ["bus"]),
                          movement("candidate", "uncertain"), movement("unknown", "absent")],
            "lane_bindings": [{"node_id": "1", "intersection_part": "0", "egress": {
                "bindings": {"eg": ["out", 0]}, "records": [{"lane_id": "uncertain", "candidates": [
                    {"edge": "out", "lane": 0}, {"edge": "missing", "lane": 99}]}]}}]}}), encoding="utf-8")
    mapping = tmp_path / "fields.csv"
    with mapping.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["stream_id", "sumo_lane", "mapping_status", "mapping_confidence"])
        writer.writeheader()
        writer.writerows([{"stream_id": 101, "sumo_lane": "car_0", "mapping_status": "active", "mapping_confidence": "high"},
                          {"stream_id": 102, "sumo_lane": "bus_0", "mapping_status": "needs_review", "mapping_confidence": "low"},
                          {"stream_id": 103, "sumo_lane": "missing", "mapping_status": "needs_review", "mapping_confidence": "none"}])
    binding = tmp_path / "binding.json"
    payload = {"source": {"candidate_net": {"sha256": file_sha256(network)}},
               "artifacts": {"detector_mapping": {"path": str(mapping), "sha256": file_sha256(mapping)}}}
    binding.write_text(json.dumps(payload), encoding="utf-8")
    result = audit_hamburg_count_reachability(candidate_manifest=manifest, field_binding_manifest=binding,
                                             output_file=tmp_path / "reachability.json")
    rows = {row["movement_id"]: row for row in result["rows"]}
    assert rows["all"]["reachable_fields_by_vehicle_class"]["passenger"] == [101]
    assert rows["all"]["reachable_fields_by_vehicle_class"]["bus"] == [102]
    assert rows["bus-only"]["reachable_fields_by_vehicle_class"] == {"bus": [102]}
    assert rows["unknown"]["status"] == "outlet_unknown"
    assert len(rows["candidate"]["outlet_hypotheses"]) == 2
    assert result["unknown_outlets"] == 1 and result["potential_count_influence"] == 3
    assert result["unavailable_fields"][0]["stream_id"] == 103
    assert next(row for row in result["field_correspondence"] if row["stream_id"] == 102)["mapping_confidence"] == "low"
    payload["source"]["candidate_net"]["sha256"] = "0" * 64
    binding.write_text(json.dumps(payload), encoding="utf-8")
    stale = audit_hamburg_count_reachability(candidate_manifest=manifest, field_binding_manifest=binding,
                                            output_file=tmp_path / "stale-network.json")
    assert stale["status"] == "measured" and stale["warnings"]
    assert stale["correspondence_basis"] == "unverified_supplied_hypotheses"
    assert stale["hash_records"]["field_network_declared"] == "0" * 64
    assert stale["rows"] == result["rows"]
