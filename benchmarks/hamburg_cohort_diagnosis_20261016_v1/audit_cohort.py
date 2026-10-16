"""Read frozen cohort observations without running SUMO or changing controls."""

from collections import Counter, deque
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from astra_sumo.core.source_movement_support import _index, _internal, _modes


def audit_cohort():
    folder = Path(__file__).resolve().parent
    manifest = json.loads((folder / "fixture.json").read_text(encoding="utf-8"))

    def read(record):
        compressed = (folder / record["path"]).read_bytes()
        assert hashlib.sha256(compressed).hexdigest() == record["sha256"]
        raw = gzip.decompress(compressed)
        assert len(raw) == record["uncompressed_bytes"]
        assert hashlib.sha256(raw).hexdigest() == record["uncompressed_sha256"]
        return raw

    model, current = [ET.fromstring(read(manifest[key])) for key in ("recorded_model", "current_model")]
    def elements(root):
        return Counter(ET.canonicalize(ET.tostring(row, encoding="unicode"), strip_text=True) for row in root if row.tag != "location")
    assert elements(model) == elements(current)
    cohort = ET.fromstring(read(manifest["cohort"]))
    assert not cohort.findall("vType") and all(row.get("type") is None for row in cohort.findall("vehicle"))
    expected = {row.get("id") for row in cohort.findall("vehicle")}
    index = _index(model)

    def access(lane_id, edge_id):
        queue, seen = deque([(lane_id, [lane_id])]), {lane_id}
        while queue:
            lane, path = queue.popleft()
            for nxt, connection in index["outgoing"].get(lane, []):
                target_edge, _, allowed = index["lanes"][nxt]
                if "passenger" not in allowed or "passenger" not in _modes(connection):
                    continue
                if target_edge == edge_id:
                    return path + [nxt]
                if nxt not in seen and _internal(index["edges"][target_edge]):
                    seen.add(nxt)
                    queue.append((nxt, path + [nxt]))
        return None

    result = {"kind": "frozen_cohort_lane_front_audit", "promotion_authority": False,
        "information": "All-vehicle simulator truth for offline diagnosis.",
        "front_definition": "Most advanced vehicle per lane, speed <= 0.1 m/s and contiguous waiting >= 60 s.",
        "limits": "Immediate saved connections only. This does not exclude strategic lane changes for later turns or identify causal blocking vehicles. Junction foes are not treated as blocking dependencies.", "cases": {}}
    for seed, case in manifest["cases"].items():
        snapshots = {time: json.loads(read(record)) for time, record in case["snapshots"].items()}
        snapshot = snapshots["2400"]
        trips = ET.fromstring(read(case["tripinfo"])).findall("tripinfo")
        assert all(float(row.get("arrival", "-1")) >= 0 and row.get("vaporized") in {None, ""} for row in trips)
        arrived = {row.get("id") for row in trips}
        collisions = ET.fromstring(read(case["collisions"])).findall("collision")
        summary = ET.fromstring(read(case["summary"])).findall("step")[-1]
        vehicles = {row["id"]: row for row in snapshot["vehicles"]}
        pending = set(snapshot["waiting_for_insertion"])
        assert arrived.isdisjoint(vehicles) and arrived.isdisjoint(pending) and set(vehicles).isdisjoint(pending)
        assert arrived | set(vehicles) | pending == expected
        assert int(summary.get("arrived")) == len(arrived) and int(summary.get("running")) == len(vehicles)
        assert int(summary.get("waiting")) == len(pending) and int(summary.get("collisions")) == len(collisions)
        assert float(summary.get("time")) + manifest["simulation_parameters"]["step_length_s"] == snapshot["simulation_time_s"]
        fronts = {}
        for row in vehicles.values():
            if row["lane"] not in fronts or row["pos"] > fronts[row["lane"]]["pos"]:
                fronts[row["lane"]] = row
        observations = []
        for row in sorted(fronts.values(), key=lambda item: item["id"]):
            if row["speed"] > 0.1 or row["waiting_s"] < 60:
                continue
            edge_id = index["lanes"][row["lane"]][0]
            assert _internal(index["edges"][edge_id]) or row["route"][row["route_index"]] == edge_id
            position = row["route_index"] + 1
            target = row["route"][position] if position < len(row["route"]) else None
            observations.append({"id": row["id"], "lane": row["lane"], "next_route_edge": target,
                "waiting_s": row["waiting_s"], "saved_path": access(row["lane"], target) if target else None,
                "lookahead_leader": row["leader"], "next_tls": row["next_tls"][0] if row["next_tls"] else None})
        result["cases"][seed] = {"requested": len(expected), "arrived": len(arrived), "on_road": len(vehicles),
            "pending_ids": sorted(pending), "collision_count": len(collisions), "lane_front_candidates": observations,
            "teleport_count": int(summary.get("teleports")),
            "fronts_without_immediate_path": [row["id"] for row in observations if row["next_route_edge"] and not row["saved_path"]]}
    return result


if __name__ == "__main__":
    print(json.dumps(audit_cohort(), indent=2))
