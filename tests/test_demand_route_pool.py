from collections import defaultdict
import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
import gzip
import xml.etree.ElementTree as ET

import pytest
import numpy as np

from astra_sumo.core.demand_route_pool import boundary_route_pool, ideal_lane_profiles, junction_visit_audit, lane_change_reachability
from astra_sumo.core.detector_demand import EdgeInfo
from astra_sumo.core.hamburg_readme_demand import _capture_lanes, _internal_paths, profile_free_flow, timed_columns, vehicle_lower_bound_certificate


def test_tour_filter_keeps_single_pass_turnarounds_and_flags_required_indirect_turns():
    edges = {name: EdgeInfo(name, a, b, True, 20) for name, a, b in
             [('in', 'boundary', 'J'), ('around', 'J', 'K'), ('back', 'K', 'J'), ('out', 'J', 'exit')]}
    connections = {'in': {'around', 'out'}, 'around': {'back'}, 'back': {'out'}}
    loop = ['in', 'around', 'back', 'out']
    assert not junction_visit_audit(loop, edges, connections, {})['allowed']
    assert junction_visit_audit(['in', 'out'], edges, connections, {})['allowed']
    connections['in'].remove('out')
    flagged = junction_visit_audit(loop, edges, connections, {})
    assert flagged['allowed'] and flagged['repeats'][0]['classification'] == 'flagged_indirect_turn'


def test_boundary_pool_filters_anchored_tours_before_reporting_their_missing_coverage():
    edges = {name: EdgeInfo(name, a, b, True, 20) for name, a, b in
             [('in', 'boundary', 'J'), ('around', 'J', 'K'), ('back', 'K', 'J'), ('out', 'J', 'exit')]}
    pool, excluded = boundary_route_pool(edges, {'in': {'around', 'out'}, 'around': {'back'}, 'back': {'out'}, 'out': set()},
        ['in'], ['out'], ['around'], {})
    assert [row['edges'] for row in pool] == [('in', 'out')]
    assert excluded[0]['junction_audit']['reason'] == 'avoidable_tour'
    assert len({r['route_id'] for r in pool + excluded}) == len(pool) + len(excluded)


def test_different_multipart_sections_are_one_pass_but_returning_to_a_part_is_not():
    edges = {name: EdgeInfo(name, a, b, True, 20) for name, a, b in
             [('a', 'start', 'p0'), ('b', 'p0', 'p3'), ('c', 'p3', 'q'), ('d', 'q', 'p0'), ('e', 'p0', 'end')]}
    connections = {'a': {'b', 'e'}, 'b': {'c'}, 'c': {'d'}, 'd': {'e'}}
    groups = {'p0': 'LSA200', 'p3': 'LSA200'}
    assert junction_visit_audit(['a', 'b', 'c'], edges, connections, groups)['allowed']
    result = junction_visit_audit(['a', 'b', 'c', 'd', 'e'], edges, connections, groups)
    assert not result['allowed'] and result['repeats'][0]['junction'] == 'LSA200'


def _lanes(edge, count):
    return [{'id': f'{edge}_{i}', 'index': i, 'length': 100, 'speed': 10,
             'change_left': ['passenger'], 'change_right': ['passenger']} for i in range(count)]


def test_ideal_lane_intention_respects_restricted_changes_and_unique_field_passages():
    lanes = {'in': _lanes('in', 1), 'observed': _lanes('observed', 2), 'out': _lanes('out', 1)}
    links = defaultdict(set, {('in', 0, 'observed'): {0}, ('observed', 0, 'out'): {0}, ('observed', 1, 'out'): {0}})
    fields = [{'lane_id': 'observed_0', 'field_index': 0}, {'lane_id': 'observed_1', 'field_index': 1}]
    route = ['in', 'observed', 'out']
    profiles = ideal_lane_profiles(route, lanes, links, fields)
    assert {p['field_mask'] for p in profiles} == {1, 2}
    assert {p['field_mask'] for p in ideal_lane_profiles(route, lanes, links, fields, allow_changes=False)} == {1}
    lanes['observed'][0]['change_left'] = ['authority']
    assert lane_change_reachability(lanes['observed'])[0] == {0}
    assert {p['field_mask'] for p in ideal_lane_profiles(route, lanes, links, fields)} == {1}


def test_free_flow_rear_clearance_uses_internal_speed_and_can_cross_a_bin_boundary():
    lanes = {'a_0': {'id': 'a_0', 'length': 100, 'speed': 10},
             ':j_0': {'length': 20, 'speed': 5}, 'b_0': {'id': 'b_0', 'length': 100, 'speed': 10}}
    fields = [{'edge_id': 'a', 'lane_id': 'a_0', 'field_index': 0, 'pos': 99},
              {'edge_id': 'b', 'lane_id': 'b_0', 'field_index': 1, 'pos': 20}]
    offsets, times, distance = profile_free_flow(['a', 'b'], {'entry_lanes': [0, 0], 'exit_lanes': [0, 0]},
        lanes, fields, {('a', 0, 'b', 0): [':j_0']}, 5, 0)
    assert offsets == {0: 10.8, 1: 16.5} and times == [10.0, 24.0] and distance == 220
    shifted, times, distance = profile_free_flow(['a', 'b'], {'entry_lanes': [0, 0], 'exit_lanes': [0, 0]},
        lanes, fields, {('a', 0, 'b', 0): [':j_0']}, 5, 5.1)
    assert shifted[0] == pytest.approx(10.29) and distance == pytest.approx(214.9)
    columns = timed_columns([{'source_edge': 'a', 'field_offsets_s': {0: 440, 1: 470}, 'distance_m': 220}],
        2, begin=3600, interval=900, bins=2, end=6000, step=.1)
    assert any(column['field_rows'] == [0, 3] for column in columns)
    for column in columns:
        departure = (column['lower_s'] + column['upper_s']) / 2
        assert int(departure // 900) == column['departure_bin']


@pytest.mark.skipif(not shutil.which('sumo') or not shutil.which('netconvert'), reason='SUMO required')
def test_native_offline_lane_inventory_reads_directional_change_restrictions_without_traffic(tmp_path):
    nodes, edges, network = [tmp_path / name for name in ('nodes.xml', 'edges.xml', 'net.xml')]
    nodes.write_text('<nodes><node id="a" x="0" y="0"/><node id="b" x="100" y="0"/></nodes>', encoding='utf-8')
    edges.write_text('<edges><edge id="road" from="a" to="b" numLanes="2">'
                     '<lane index="0" changeLeft="authority"/><lane index="1" changeRight="passenger"/>'
                     '</edge></edges>', encoding='utf-8')
    subprocess.run([shutil.which('netconvert'), '-n', str(nodes), '-e', str(edges), '-o', str(network)],
                   check=True, capture_output=True, timeout=30)
    output = tmp_path / 'lanes.json'
    _capture_lanes({'network': str(network), 'sumo_binary': shutil.which('sumo')}, output)
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['simulation_steps'] == report['simulation_time_s'] == 0
    assert 'passenger' not in report['lanes']['road_0']['change_left']
    assert 'passenger' in report['lanes']['road_1']['change_right']


def test_frozen_readme_network_has_internal_paths_for_all_passenger_connections():
    source = Path(__file__).parents[1] / 'benchmarks/hamburg_ring1_entry_probe_20261021_v1/network.net.xml.gz'
    paths = _internal_paths(ET.fromstring(gzip.decompress(source.read_bytes())))
    assert paths[('186821027#0', 5, '186821036#1', 0)]


def test_vehicle_lower_bound_remains_valid_after_rationalizing_and_scaling_duals():
    response = np.array([[1, 1, 0], [1, 0, 1], [0, 1, 1]])
    cert = vehicle_lower_bound_certificate(response, np.ones(3, dtype=int), np.ones((1, 3), dtype=int),
        np.array([10]), [.6] * 3, [0])
    field = [Fraction(*v) for v in cert['field_dual']]
    assert all(sum(int(response[r, c]) * field[r] for r in range(3)) <= 1 for c in range(3))
    assert cert['integer_vehicle_lower_bound'] == 2
