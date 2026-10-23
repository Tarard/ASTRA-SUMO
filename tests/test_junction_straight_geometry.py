import gzip
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.junction_straight_geometry import materialize_ordered_straights, ordered_straight_vertices


REPO = Path(__file__).resolve().parents[1]
KEYS = [("199230909#0", 2, "486864294", 0), ("199230909#0", 3, "486864294", 1)]


def _source(tmp_path):
    source = tmp_path / 'source.net.xml'
    source.write_bytes(gzip.decompress((REPO / "benchmarks/hamburg_ring1_104_split_fanout_20261023_v1/candidate.net.xml.gz").read_bytes()))
    return source


def test_ordered_straight_vertices_retain_every_point_and_endpoints():
    points = [(0., 0.), (3., .1), (2., .2), (4., 0.)]
    assert ordered_straight_vertices(points) == [points[0], points[2], points[1], points[3]]
    assert ordered_straight_vertices([(0., 0.), (2., .1), (3., .2), (4., 0.)]) == [(0., 0.), (2., .1), (3., .2), (4., 0.)]


def test_ordering_is_explicitly_limited_to_a_straight_lane_width_envelope():
    with pytest.raises(ValueError, match="straight"):
        ordered_straight_vertices([(0., 0.), (2., 5.), (4., 0.)])
    with pytest.raises(ValueError, match="anchors"):
        ordered_straight_vertices([(0., 0.), (-1., 0.), (4., 0.)])
    with pytest.raises(ValueError, match="finite"):
        ordered_straight_vertices([(0., 0.), (float('nan'), 0.), (4., 0.)])


def test_hamburg_straight_ordering_removes_the_long_return_between_crossings():
    from shapely.geometry import LineString

    source = REPO / "benchmarks/hamburg_ring1_104_split_fanout_20261023_v1/candidate.net.xml.gz"
    root = ET.fromstring(gzip.decompress(source.read_bytes()))
    lanes = {lane.get('id'): lane for lane in root.findall('edge/lane')}
    def points(identity):
        return [tuple(map(float, p.split(','))) for p in lanes[identity].get('shape').split()]
    left = LineString(points(':LSA119_part0_0_0'))
    old = points(':LSA119_part0_10_1')
    assert len(left.intersection(LineString(old)).geoms) == 3
    ordered = LineString(ordered_straight_vertices(old))
    old_positions = [LineString(old).project(point) for point in left.intersection(LineString(old)).geoms]
    new_positions = [ordered.project(point) for point in left.intersection(ordered).geoms]
    assert max(old_positions) - min(old_positions) > 19
    assert max(new_positions) - min(new_positions) < 1.8


def test_native_hamburg_stopped_left_turn_reproduces_then_clears_with_forward_straights(tmp_path):
    sumo = pytest.importorskip('sumo')
    source = _source(tmp_path)
    original = source.read_bytes()
    result = materialize_ordered_straights(source_file=source, junction_id='LSA119_part0', connection_keys=KEYS,
        output_dir=tmp_path / 'candidate', netconvert_binary=Path(sumo.__file__).parent / 'bin/netconvert.exe')
    script = REPO / 'benchmarks/hamburg_ring1_119_straight_order_20261023_v1/stopped_probe.py'
    for label, network, count in [('before', source, 1), ('after', Path(result['candidate_file']), 0)]:
        out = tmp_path / label
        executed = subprocess.run([sys.executable, str(script), '--network', str(network), '--output-dir', str(out)],
            capture_output=True, text=True, timeout=30)
        assert executed.returncode == 0, executed.stderr
        observed = json.loads((out / 'result.json').read_bytes())
        assert len(observed['native_collisions']) == count
        assert int(observed['final_summary']['teleports']) == count
        assert int(observed['final_summary']['arrived']) == 2
        assert int(observed['final_summary']['running']) == int(observed['final_summary']['waiting']) == 0
    assert source.read_bytes() == original
    assert result['ordinary_roads_unchanged'] and result['turn_permissions_unchanged']
    assert result['signal_bindings_unchanged'] and result['signal_programs_unchanged']
    assert result['maximum_unselected_curve_offset_m'] < 1e-4
    before, after = ET.fromstring(original), ET.parse(result['candidate_file']).getroot()
    def inventory(root, tag):
        return {row.get('id'): ET.canonicalize(ET.tostring(row, encoding='unicode'), strip_text=True)
                for row in root.findall(tag) if tag != 'edge' or not row.get('function')}
    assert inventory(before, 'edge') == inventory(after, 'edge')
    assert inventory(before, 'tlLogic') == inventory(after, 'tlLogic')


def test_straight_materialization_rejects_other_junctions_turns_and_existing_outputs(tmp_path):
    source = _source(tmp_path)
    with pytest.raises(ValueError, match='straight movements at'):
        materialize_ordered_straights(source_file=source, junction_id='LSA104_part0', connection_keys=KEYS, output_dir=tmp_path / 'wrong-node')
    with pytest.raises(ValueError, match='straight movements at'):
        materialize_ordered_straights(source_file=source, junction_id='LSA119_part0',
            connection_keys=[('43866090#0', 0, '486864294', 0)], output_dir=tmp_path / 'turn')
    with pytest.raises(ValueError, match='distinct existing'):
        materialize_ordered_straights(source_file=source, junction_id='LSA119_part0', connection_keys=[], output_dir=tmp_path / 'empty')
    with pytest.raises(ValueError, match='new output'):
        materialize_ordered_straights(source_file=source, junction_id='LSA119_part0', connection_keys=KEYS, output_dir=tmp_path)


def test_frozen_lsa119_repair_retains_every_native_count_and_protected_input(tmp_path):
    import csv
    import hashlib

    from astra_sumo.core.detector_demand import audit_expected_to_e1_strict

    case = REPO / 'benchmarks/hamburg_ring1_119_straight_order_20261023_v1'
    for row in json.loads((case / 'manifest.json').read_bytes())['files']:
        data = (case / row['path']).read_bytes()
        assert len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256']
    native = case / 'full-native'
    old = REPO / 'benchmarks/hamburg_ring1_od_spsa_five_rounds_20261023_v1/evaluations/iteration-0001-plus'
    for name in ('demand.rou.xml', 'lane-intentions.json', 'source-e1.add.xml', 'signal-programs.add.xml', 'expected-counts.csv'):
        assert gzip.decompress((native / (name + '.gz')).read_bytes()) == gzip.decompress((old / (name + '.gz')).read_bytes())
    expected = list(csv.DictReader(gzip.decompress((native / 'expected-counts.csv.gz').read_bytes()).decode().splitlines()))
    path = tmp_path / 'e1.xml'
    path.write_bytes(gzip.decompress((native / 'field-counts.xml.gz').read_bytes()))
    measured = audit_expected_to_e1_strict(expected, path, count_attribute='nVehContrib')
    stored = list(csv.DictReader((native / 'field-comparison.csv').open(encoding='utf-8')))
    assert len(measured) == len(stored) == 130
    assert [r['measured_nVehContrib'] for r in measured] == [int(r['measured_nVehContrib']) for r in stored]
    scores = json.loads((native / 'evaluation.json').read_bytes())
    assert sum(abs(r['diff_nVehContrib_minus_expected']) for r in measured) / 130 == scores['MAE']
    assert sum(r['diff_nVehContrib_minus_expected'] ** 2 for r in measured) == scores['loss']
    assert scores['completion']['collisions'] == scores['completion']['teleports'] == 0
    assert not scores['healthy'] and not scores['exact_count_fit']
    assert len(list(csv.DictReader((native / 'station-comparison.csv').open(encoding='utf-8')))) == 40
