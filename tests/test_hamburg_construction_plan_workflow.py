import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from torii_sumo.core.hamburg_topology_workflow import build_hamburg_topology_workflow


def identity(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def request_file(tmp_path):
    plan = tmp_path / '2013-plan.pdf'
    plan.write_bytes(b'%PDF-1.4\n% synthetic drawing identity for an integration test\n')
    osm = tmp_path / '2026-context.osm.xml'
    osm.write_text('<osm version="0.6"><node id="1" lat="53.54" lon="9.99"/><node id="2" lat="53.541" lon="9.991"/><way id="1"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/><tag k="lanes" v="99"/></way></osm>', encoding='utf-8')
    topology = tmp_path / 'interpreted-plan.json'
    lanes = [{'width_m': 3.1, 'allow': 'passenger bus'}, {'width_m': 1.6, 'allow': 'bicycle'}]
    topology.write_text(json.dumps(dict(schema='torii.engineering-topology/v1', source_plan_sha256=identity(plan)['sha256'], crs='EPSG:25832',
        nodes=[dict(id=n, x=565000+x, y=5933000, type='traffic_light' if n=='J' else 'priority', evidence='test drawing') for n,x in [('A',0),('J',50),('B',100)]],
        edges=[dict(id='in', **{'from':'A','to':'J'}, lanes=lanes, evidence='test drawing'), dict(id='out', **{'from':'J','to':'B'},lanes=lanes,evidence='test drawing')],
        connections=[dict(**{'from':'in','to':'out','fromLane':i,'toLane':i},evidence='test drawing') for i in range(2)])), encoding='utf-8')
    request = tmp_path / 'request.json'
    request.write_text(json.dumps(dict(schema='torii.hamburg-topology-workflow-request/v1',
        construction_plan={**identity(plan), 'data_year':2013, 'document_kind':'design', 'topology':identity(topology)},
        source_osm={**identity(osm), 'data_year':2026},
        supporting_maps=[dict(provider='google_maps',url='https://maps.google.com/',data_year=2026)])), encoding='utf-8')
    return request


def test_same_complete_entry_uses_2013_drawing_without_MAP_or_current_aerial(tmp_path):
    request = request_file(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    result = build_hamburg_topology_workflow(request_file=request, output_dir=tmp_path / 'run')
    assert result['source_policy']['target_year'] == 2013
    assert result['source_policy']['topology_authority'] == 'construction_plan'
    assert result['source_policy']['supporting_maps'][0]['role'] == 'context_only'
    assert result['network_handoff'] is not None
    root = ET.parse(result['network_handoff']['network']['path']).getroot()
    assert len(root.find("edge[@id='in']").findall('lane')) == 2
    assert root.find("edge[@id='in']/lane[@index='1']").get('allow') == 'bicycle'
    assert len([c for c in root.findall('connection') if c.get('from') == 'in']) == 2
    assert result['stages']['sumo_load']['status'] == 'pass'
    assert result['stages']['movement_probes']['status'] == 'pass'
    probes = json.loads(Path(result['stages']['movement_probes']['artifact']['path']).read_text(encoding='utf-8'))
    assert probes['declared_total'] == 2
    assert probes['vehicle_class_counts'] == {'passenger': 1, 'bicycle': 1}
    assert result['comparison_target'] == 'provided_construction_plan'
    assert all(p.read_bytes() == content for p, content in before.items())
    assert result['calibration']['status'] == 'not_run'


def test_plan_identity_mismatch_cannot_relabel_other_geometry_as_authoritative(tmp_path):
    request = request_file(tmp_path)
    data = json.loads(request.read_text(encoding='utf-8'))
    topology = Path(data['construction_plan']['topology']['path'])
    values = json.loads(topology.read_text(encoding='utf-8'))
    values['source_plan_sha256'] = '0'*64
    topology.write_text(json.dumps(values), encoding='utf-8')
    data['construction_plan']['topology'] = identity(topology)
    request.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='source_plan_sha256'):
        build_hamburg_topology_workflow(request_file=request, output_dir=tmp_path / 'run')
    assert not (tmp_path / 'run').exists()
