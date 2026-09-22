import json
from types import SimpleNamespace
import xml.etree.ElementTree as ET

from astra_sumo.core.hamburg_context_junctions import construction_coverage


def test_contour_compilation_retains_existing_node_inputs_and_local_scope(tmp_path, monkeypatch):
    from astra_sumo.core import hamburg_junction_contour
    from astra_sumo.core.hamburg_junctions import movements

    files = [tmp_path / name for name in ['source.net.xml', 'connections.xml', 'nodes.xml', 'edges.xml']]
    for path in files:
        path.write_text('<net/>', encoding='utf-8')
    command = ['netconvert', '--sumo-net-file', str(files[0]), '--connection-files', str(files[1]),
               '--node-files', str(files[2]), '--edge-files', str(files[3]), '--output-file', str(tmp_path / 'out.net.xml')]
    commands = []

    def compile_candidate(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=1, stdout='', stderr='Compilation is not part of this command-shape test.')

    monkeypatch.setattr(movements, 'run_command', compile_candidate)
    monkeypatch.setattr(hamburg_junction_contour, 'propose_fused_junction_contour', lambda *args, **kwargs: {
        'status': 'pass', 'changed': True, 'proposed_shape': [(0, 0), (2, 0), (1, 1)]})
    root = ET.fromstring('<net><junction id="j"/><junction id="outside" type="priority"/></net>')
    for neighbor in ['west', 'east', 'north']:
        edge = ET.SubElement(root, 'edge', id=neighbor, attrib={'from': neighbor, 'to': 'outside'})
        ET.SubElement(edge, 'lane', allow='passenger')
    _, report = movements._compile_junction_contours(root, command=command,
        groups=[{'join_id': 'j'}], output_file=tmp_path / 'out.net.xml', timeout_seconds=10,
        mode='fused', include_context=False)
    assert report['target_junction_ids'] == ['j']
    assert report['context_junction_ids'] == []
    assert commands[0].count('--node-files') == 1
    assert commands[0][commands[0].index('--node-files') + 1].startswith(str(files[2]) + ',')
    assert {row['path'] for row in report['sources']} == {str(path) for path in files}


def test_coverage_identifies_the_visible_junction_omitted_from_the_request(tmp_path):
    path = tmp_path / 'lsa.json'
    path.write_text(json.dumps({'features': [
        {'properties': {'knoten': number, 'LSA_Name': f'Junction {number}'},
         'geometry': {'type': 'MultiPoint', 'coordinates': [[lon, 53.542]]}}
        for number, lon in [(2394, 9.995), (2396, 9.996), (9999, 10.5)]
    ]}), encoding='utf-8')
    request = {'lsa_identity': {'path': str(path)}, 'osm_build': {'bbox': '9.99,53.54,10.00,53.55'},
               'intersections': [{'node_id': '2394'}], 'context_intersections': []}
    result = construction_coverage(request)
    assert result['status'] == 'review_required'
    assert result['source_only_node_ids'] == ['2396']
    request['context_intersections'] = [{'node_id': '2396'}]
    result = construction_coverage(request)
    assert result['status'] == 'pass'
    assert result['source_only_node_ids'] == []
    assert {row['node_id']: row['treatment'] for row in result['intersections']} == {
        '2394': 'official_MAP_reconstruction', '2396': 'reviewed_context_reconstruction'}
