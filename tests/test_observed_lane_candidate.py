import subprocess
import xml.etree.ElementTree as ET

import pytest

from torii_sumo.road_network import observed_lane_candidate as candidate_module
from torii_sumo.road_network.observed_lane_candidate import build_observed_lane_candidate


def _case(tmp_path, neighbor_tls=False, branch=False):
    nodes, edges, source = [tmp_path / name for name in ('nodes.xml', 'edges.xml', 'source.net.xml')]
    nodes.write_text('<nodes><node id="A" x="-100" y="0"/><node id="B" x="-50" y="0" '
                    f'type="{"traffic_light" if neighbor_tls else "priority"}"/>'
                    '<node id="J" x="0" y="0" type="traffic_light"/><node id="C" x="50" y="0"/>'
                    + ('<node id="D" x="30" y="50"/>' if branch else '') + '</nodes>')
    edges.write_text('<edges><edge id="ab" from="A" to="B" numLanes="2"/><edge id="bj" from="B" to="J" numLanes="2"/><edge id="jc" from="J" to="C" numLanes="2"/>'
                    + ('<edge id="jd" from="J" to="D" numLanes="1"/>' if branch else '') + '</edges>')
    subprocess.run(['netconvert', '-n', str(nodes), '-e', str(edges), '-o', str(source), '--offset.disable-normalization'], check=True, capture_output=True)
    original = ET.parse(source).getroot().find("edge[@id='bj']").findall('lane')
    def row(donor, y, allow):
        shape = [[float(v) for v in p.split(',')] for p in original[0].get('shape').split()]
        return dict(origin_lane_index=donor, shape=[[p[0], y] for p in shape], width_m=1.5 if donor is None else 3.2, allow=allow, evidence='test observation')
    layouts = {'bj': [row(0, -6.5, 'passenger bus'), row(None, -4.15, 'bicycle'), row(1, -1.8, 'passenger bus')]}
    movements = {'J': [dict(**{'from': 'bj', 'to': 'jc', 'fromLane': i, 'toLane': j}, evidence='test movement') for i, j in ((0, 0), (2, 1))]}
    return source, layouts, movements


def test_insert_middle_bike_lane_remaps_neighbor_connections_and_keeps_source(tmp_path):
    source, layouts, movements = _case(tmp_path)
    before = source.read_bytes()
    report = build_observed_lane_candidate(source_net=source, layouts=layouts, junction_movements=movements, output_dir=tmp_path / 'candidate')
    assert source.read_bytes() == before
    assert report['status'] == 'pass', report['gates']
    result = ET.parse(report['candidate_network']['path']).getroot()
    assert result.find("edge[@id='bj']/lane[@index='1']").get('allow') == 'bicycle'
    assert result.find("connection[@from='ab'][@fromLane='1']").get('toLane') == '2'
    assert len(result.find("tlLogic[@id='J'][@programID='torii-geometry']/phase").get('state')) == 2


def test_neighbor_signal_links_survive_remap_and_every_target_program_is_diagnostic(tmp_path):
    source, layouts, movements = _case(tmp_path, neighbor_tls=True)
    before = ET.parse(source).getroot()
    report = build_observed_lane_candidate(source_net=source, layouts=layouts, junction_movements=movements, output_dir=tmp_path / 'candidate')
    assert report['status'] == 'pass', report['gates']
    actual = ET.parse(report['candidate_network']['path']).getroot()
    for old in before.findall("connection[@from='ab']"):
        target = '0' if old.get('toLane') == '0' else '2'
        new = actual.find(f"connection[@from='ab'][@fromLane='{old.get('fromLane')}'][@toLane='{target}']")
        assert new is not None
        assert (new.get('tl'), new.get('linkIndex')) == (old.get('tl'), old.get('linkIndex'))
    expected = [(5.0, 'Gr'), (2.0, 'yr'), (1.0, 'rr'), (5.0, 'rG'), (2.0, 'ry'), (1.0, 'rr')]
    for logic in actual.findall("tlLogic[@id='J']"):
        assert [(float(p.get('duration')), p.get('state')) for p in logic.findall('phase')] == expected


@pytest.mark.parametrize('fault', ['bike_permissions', 'bike_width', 'neighbor_speed', 'diagnostic_phase', 'neighbor_signal_link'])
def test_compiled_result_must_match_declared_attributes_and_untouched_controls(tmp_path, monkeypatch, fault):
    source, layouts, movements = _case(tmp_path, neighbor_tls=True)
    original_run = candidate_module.run_command
    def corrupt_after_compile(command, **kwargs):
        outcome = original_run(command, **kwargs)
        output = command[command.index('--output-file') + 1]
        tree = ET.parse(output)
        root = tree.getroot()
        if fault == 'bike_permissions':
            root.find("edge[@id='bj']/lane[@index='1']").set('allow', 'passenger')
        elif fault == 'bike_width':
            root.find("edge[@id='bj']/lane[@index='1']").set('width', '8.0')
        elif fault == 'neighbor_speed':
            root.find("edge[@id='ab']/lane[@index='0']").set('speed', '1.0')
        elif fault == 'diagnostic_phase':
            root.find("tlLogic[@id='J']/phase").set('state', 'rr')
        else:
            root.find("connection[@from='ab'][@fromLane='0']").set('linkIndex', '1')
        tree.write(output, encoding='utf-8')
        return outcome
    monkeypatch.setattr(candidate_module, 'run_command', corrupt_after_compile)
    report = build_observed_lane_candidate(source_net=source, layouts=layouts, junction_movements=movements, output_dir=tmp_path / 'candidate')
    assert report['status'] == 'blocked', (fault, report['gates'])


def test_removed_branch_retires_its_connections_and_replaces_old_signal_program(tmp_path):
    source, layouts, movements = _case(tmp_path, branch=True)
    before = source.read_bytes()
    report = build_observed_lane_candidate(source_net=source, layouts=layouts, junction_movements=movements,
                                          removed_edges={'jd': 'explicit plan observation'}, output_dir=tmp_path / 'candidate')
    assert report['status'] == 'pass', report['gates']
    actual = ET.parse(report['candidate_network']['path']).getroot()
    assert actual.find("edge[@id='jd']") is None
    assert all(c.get('to') != 'jd' and c.get('from') != 'jd' for c in actual.findall('connection'))
    assert all(len(p.get('state')) == 2 for t in actual.findall("tlLogic[@id='J']") for p in t.findall('phase'))
    assert source.read_bytes() == before


def test_lane_numeric_comparison_accepts_floating_roundoff(tmp_path):
    source, layouts, movements = _case(tmp_path)
    layouts['bj'][0]['width_m'] += 1e-12
    report = build_observed_lane_candidate(source_net=source, layouts=layouts, junction_movements=movements, output_dir=tmp_path / 'candidate')
    assert report['status'] == 'pass', report['gates']
