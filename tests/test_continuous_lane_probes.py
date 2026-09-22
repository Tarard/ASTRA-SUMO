import json
from pathlib import Path

import pytest

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.road_network.engineering_topology import build_engineering_topology
from torii_sumo.road_network.continuous_lane_probes import run_continuous_lane_probes


def _case(tmp_path, kind="constant", *, upstream=False, ambiguous_prefix=False):
    first_keys = ["bike", "through"] if kind == "constant" else ["through"]
    last_keys = first_keys if kind == "constant" else ["new", "through"]
    def lanes(keys, start, end):
        return [dict(key=key, width_m=1.5 if key == "bike" or key == "new" and kind == "bike" else 3.2,
                     allow="bicycle" if key == "bike" or key == "new" and kind == "bike" else "passenger bus",
                     shape=[[start, 0 if key == "through" else -3.2], [end, 0 if key == "through" else -3.2]],
                     evidence="synthetic test") for key in keys]
    first, last = lanes(first_keys, 0, 30), lanes(last_keys, 30, 70)
    topology = dict(schema="torii.engineering-topology/v1", source_plan_sha256="1" * 64, crs="EPSG:3857",
        nodes=[dict(id=key, x=x, y=0, type="priority" if key == "B" else "dead_end", evidence="synthetic test") for key, x in [("A", 0), ("B", 30), ("C", 70)]],
        edges=[dict(id="e0", **{"from": "A", "to": "B"}, lanes=first, evidence="synthetic test"),
               dict(id="e1", **{"from": "B", "to": "C"}, lanes=last, evidence="synthetic test")],
        connections=[dict(**{"from": "e0", "to": "e1", "fromLane": i, "toLane": last_keys.index(key)}, evidence="same lane key") for i, key in enumerate(first_keys)])
    if upstream:
        topology['nodes'][0]['type'] = 'priority'
        topology['nodes'].append(dict(id='P', x=-30, y=0, type='dead_end', evidence='synthetic test'))
        prefix_keys = ['through', 'other'] if ambiguous_prefix else first_keys
        prefix_lanes = lanes(prefix_keys, -30, 0)
        topology['edges'].insert(0, dict(id='pre', **{'from':'P', 'to':'A'}, lanes=prefix_lanes, evidence='synthetic test'))
        topology['connections'].extend(dict(**{'from':'pre', 'to':'e0', 'fromLane':i, 'toLane':0 if ambiguous_prefix else i}, evidence='declared continuation') for i in range(len(prefix_keys)))
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(topology), encoding="utf-8")
    built = build_engineering_topology(topology_file=path, source_osm=None, output_dir=tmp_path / "net", target_year=None)
    assert built["status"] == "pass", built["construction_checks"]
    records = [dict(lane_key=key, edge_lanes=[["e0", i], ["e1", last_keys.index(key)]], full_run=True) for i, key in enumerate(first_keys)]
    if kind != "constant":
        records.append(dict(lane_key="new", edge_lanes=[["e1", 0]], full_run=False))
    segments = [dict(edge_id="e0", start_m=0, end_m=30, lane_keys=first_keys, lanes=first),
                dict(edge_id="e1", start_m=30, end_m=70, lane_keys=last_keys, lanes=last)]
    if upstream:
        for row in records:
            if row['edge_lanes'][0][0] == 'e0':
                row['edge_lanes'].insert(0, ['pre', row['edge_lanes'][0][1]])
        if ambiguous_prefix:
            records.append(dict(lane_key='other', edge_lanes=[['pre',1], ['e0',0], ['e1',1]], full_run=True))
        for row in segments:
            row['start_m'] += 30
            row['end_m'] += 30
        segments.insert(0, dict(edge_id='pre', start_m=0, end_m=30, lane_keys=prefix_keys, lanes=prefix_lanes))
    continuity = tmp_path / "continuity.json"
    continuity.write_text(json.dumps(dict(schema="torii.continuous-lanes/v1", status="pass", unresolved=[],
        topology=dict(path=str(path), sha256=file_sha256(path)), source_topology=dict(path=str(path), sha256=file_sha256(path)),
        runs=[dict(id="run", segments=segments,
                   lane_paths=records, introduced_lanes=[] if kind == "constant" else [dict(lane_key="new", station_m=60 if upstream else 30)])])), encoding="utf-8")
    return Path(built["artifacts"]["network"]["path"]), continuity


def test_constant_motor_and_bicycle_paths_run_with_external_lane_sequence_proof(tmp_path):
    network, continuity = _case(tmp_path)
    before = file_sha256(network), file_sha256(continuity)
    result = run_continuous_lane_probes(network_file=network, continuity_file=continuity, output_dir=tmp_path / "probes")
    assert result["status"] == "pass", result
    assert result["lane_paths_passed"] == result["lane_paths_total"] == 2
    assert {r["vehicle_class"] for r in result["path_probes"]} == {"passenger", "bicycle"}
    for row in result["path_probes"]:
        assert row["external_lane_sequence"] == row["expected_external_lanes"]
        assert row["structural_connections"]["status"] == "pass"
        assert row["summary"]["arrived"] == "1"
        assert row["summary"]["collisions"] == row["summary"]["teleports"] == "0"
    assert result["producer_manifest"]["sha256"]
    assert (file_sha256(network), file_sha256(continuity)) == before
    manifest = json.loads(Path(result['manifest_file']).read_text())
    assert all(not Path(row['path']).is_relative_to(tmp_path / 'probes') for row in manifest['inputs'])


@pytest.mark.parametrize("kind,entry_status", [("pocket", "pass"), ("bike", "needs_entry_evidence")])
def test_index_shift_single_edge_new_lane_and_native_entry_evidence(tmp_path, kind, entry_status):
    network, continuity = _case(tmp_path, kind)
    result = run_continuous_lane_probes(network_file=network, continuity_file=continuity, output_dir=tmp_path / "probes")
    assert result["lane_paths_passed"] == 2, result["path_probes"]
    through = next(r for r in result["path_probes"] if r["lane_key"] == "through")
    assert through["external_lane_sequence"] == ["e0_0", "e1_1"]
    added = next(r for r in result["path_probes"] if r["lane_key"] == "new")
    assert added["expected_external_lanes"] == ["e1_0"]
    assert added["structural_connections"]["transitions"] == []
    entry = result["entry_probes"][0]
    assert entry["status"] == entry_status, entry
    if kind == "pocket":
        assert entry["observed_lane_changes"]
        assert entry["observed_lane_changes"][0]["to"] == "e1_0"
        assert entry["direct_carrier_to_new_lane_connection"] is False
    else:
        assert entry["executed"] is False


@pytest.mark.parametrize('ambiguous', [False, True])
def test_new_pocket_entry_starts_at_unique_road_entry_not_just_previous_section(tmp_path, ambiguous):
    network, continuity = _case(tmp_path, 'pocket', upstream=True, ambiguous_prefix=ambiguous)
    result = run_continuous_lane_probes(network_file=network, continuity_file=continuity, output_dir=tmp_path / 'probes')
    entry = result['entry_probes'][0]
    if ambiguous:
        assert entry['status'] == 'needs_entry_evidence'
        assert entry['executed'] is False
        assert entry['compatible_road_entry_prefix_count'] == 2
    else:
        assert entry['status'] == 'pass', entry
        assert entry['requested_edge_lanes'][0] == ['pre', 0]
        assert entry['external_lane_sequence'][0] == 'pre_0'
        assert entry['tripinfo']['departLane'] == 'pre_0'
        assert entry['native_entry_lane_changes']
