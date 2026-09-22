import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.command_runner import CommandResult
from torii_sumo.core import hamburg_topology_audit as audit


def _case(tmp_path, *, outside_bowtie=False):
    body = '''<net><location netOffset="0,0" projParameter="!"/>
    <edge id="in" from="a" to="j"><lane id="in_0" index="0" length="10" shape="0,0 10,0"/></edge>
    <edge id="out" from="j" to="b"><lane id="out_0" index="0" length="10" shape="12,0 22,0"/></edge>
    <edge id="far" from="c" to="d"><lane id="far_0" index="0" length="10" shape="100,0 110,0"/></edge>
    <junction id="a" type="dead_end" shape="0,0"/>
    <junction id="j" type="priority" customShape="true" shape="10,-2 12,-2 12,2 10,2"/>
    <junction id="b" type="dead_end" shape="22,0"/>
    <junction id="c" type="dead_end" shape="100,0"/>
    <junction id="d" type="dead_end" shape="110,0"/>
    <connection from="in" fromLane="0" to="out" toLane="0"/>
    </net>'''
    if outside_bowtie:
        body = body.replace('</net>', '<junction id="remote" type="priority" shape="200,0 202,2 200,2 202,0"/></net>')
    source, candidate = tmp_path / 'source.net.xml', tmp_path / 'candidate.net.xml'
    source.write_text(body, encoding='utf-8')
    candidate.write_text(body, encoding='utf-8')
    row = {'node_id': '1', 'movement_id': '1', 'sumo_connection': ['in', 0, 'out', 0]}
    payload = {
        'schema': 'torii.hamburg-aerial-corridor-candidate/v1', 'status': 'pass', 'topology_complete': True,
        'inputs': {'source_net': {'path': str(source), 'sha256': file_sha256(source)}},
        'artifacts': {'network': {'path': str(candidate), 'sha256': file_sha256(candidate)}},
        'physical_parts': [{'node_id': '1', 'join_id': 'j', 'source_node_ids': ['j']}],
        'counts': {'official_vehicle_movements': 1},
        'official_connection_audit': {'status': 'pass', 'required': [row], 'missing': [], 'ambiguous': [],
                                      'unexplained_extra': [], 'geometry_rejected': []},
        'movement_materialization': {'movements': [{**row, 'geometry_source': 'selected_curve'}]},
        'approach_rebuild': {'status': 'pass', 'approaches': [], 'reviews': []},
    }
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    return manifest, payload, source, candidate


def _runner(monkeypatch, change=None):
    def run(command, *, cwd, timeout_seconds):
        assert timeout_seconds == 240.0
        source = Path(command[command.index('--sumo-net-file') + 1])
        target = Path(command[command.index('--output-file') + 1])
        shutil.copy2(source, target)
        if change:
            tree = ET.parse(target)
            change(tree.getroot())
            tree.write(target, encoding='utf-8')
        return CommandResult(command=command, cwd=str(cwd), status='pass', returncode=0)
    monkeypatch.setattr(audit, 'run_command', run)
    monkeypatch.setattr(audit, 'audit_network_connection_mode', lambda root, **kw: {
        'status': 'pass', 'structural_failure_count': 0, 'review_finding_count': 0, 'junctions': []})


def test_geometry_reviews_survive_pass_and_remote_polygon_is_not_target(tmp_path, monkeypatch):
    manifest, payload, source, candidate = _case(tmp_path, outside_bowtie=True)
    payload['approach_rebuild']['reviews'] = [{'node_id': '1', 'reason': 'lane_count_mismatch'}]
    payload['official_connection_audit']['geometry_rejected'] = [{'movement_id': '1', 'reason': 'anchor_fit'}]
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    _runner(monkeypatch)
    report = audit.audit_hamburg_topology_candidate(manifest, tmp_path / 'audit')
    assert report['structural']['status'] == report['official_connectivity']['status'] == 'pass'
    assert report['geometry_review']['status'] == report['status'] == 'review_required'
    assert report['geometry_review']['target_surface_findings'] == []
    assert report['geometry_review']['outside_surface_findings'][0]['junction_id'] == 'remote'
    assert report['geometry_review']['approach_reviews'] == payload['approach_rebuild']['reviews']
    assert report['geometry_review']['curve_fallbacks'][0]['reason'] == 'anchor_fit'
    assert report['reload_stability']['status'] == 'pass'
    assert report['inputs_unchanged'] is True
    assert file_sha256(candidate) == file_sha256(source)
    assert json.loads(Path(report['report_file']).read_text(encoding='utf-8'))['status'] == 'review_required'


def test_a_geometrically_preserved_contour_keeps_its_imagery_review(tmp_path, monkeypatch):
    manifest, payload, _, _ = _case(tmp_path)
    contours = {'mode': 'guarded', 'status': 'review_required', 'accepted_junction_ids': ['j'],
                'rejected_junction_ids': [], 'parts': [{'junction_id': 'j', 'status': 'pass'}],
                'claim_boundary': 'Vehicle-surface preservation does not establish a field curb.'}
    payload['movement_materialization']['junction_contours'] = contours
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    _runner(monkeypatch)
    report = audit.audit_hamburg_topology_candidate(manifest, tmp_path / 'audit')
    assert report['geometry_review']['status'] == report['status'] == 'review_required'
    assert report['geometry_review']['junction_contours'] == contours
    assert report['official_connectivity']['status'] == 'pass'


@pytest.mark.parametrize('official_error, expected', [(2.0, 'pass'), (12.0, 'review_required'), (None, 'review_required')])
def test_official_curve_fallback_requires_same_limit_evidence_and_keeps_actual_source(
    tmp_path, monkeypatch, official_error, expected,
):
    manifest, payload, _, _ = _case(tmp_path)
    payload['parameters'] = {'maximum_anchor_projection_error_m': 10.0}
    row = payload['movement_materialization']['movements'][0]
    row.update(selected_source='aerial_trace', geometry_source='official_map_curve',
               selected_curve_anchor_projection_error_sum_m=12.0,
               official_curve_anchor_projection_error_sum_m=official_error,
               anchor_projection_error_sum_m=official_error)
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    _runner(monkeypatch)
    report = audit.audit_hamburg_topology_candidate(manifest, tmp_path / 'audit')
    assert report['geometry_review']['status'] == expected
    if expected == 'pass':
        assert report['geometry_review']['curve_fallbacks'] == []
        used = report['geometry_review']['official_curve_fallbacks'][0]
        assert used['selected_source'] == 'aerial_trace'
        assert used['geometry_source'] == 'official_map_curve'
        assert used['official_curve_anchor_projection_error_sum_m'] == 2.0
    else:
        assert report['geometry_review']['curve_fallbacks'][0]['geometry_source'] == 'official_map_curve'


def test_inherited_outside_geometry_and_connection_reviews_do_not_fail_clean_target(tmp_path, monkeypatch):
    manifest, _, _, _ = _case(tmp_path, outside_bowtie=True)
    _runner(monkeypatch)
    monkeypatch.setattr(audit, 'audit_network_connection_mode', lambda root, **kw: {
        'status': 'review_required', 'traffic_side': 'right', 'structural_failure_count': 0,
        'review_finding_count': 1, 'junctions': [{'junction_id': 'remote',
            'connection_mode_audit': {'review_findings': ['lane_order:existing'], 'structural_failures': []}}]})
    report = audit.audit_hamburg_topology_candidate(manifest, tmp_path / 'audit')
    assert report['geometry_review']['status'] == report['status'] == 'pass'
    background = report['geometry_review']['outside_surface_review']
    assert background['status'] == 'review_required'
    assert background['regression_status'] == 'pass'
    assert len(background['inherited_findings']) == 1
    assert report['structural']['review_finding_count'] == 1
    assert report['connection_comparison']['outside_scope_new_review_finding_count'] == 0


@pytest.mark.parametrize('source_shape, expected_list', [
    ('200,0 202,0 202,2 200,2', 'introduced_findings'),
    ('200,0 203,2 200,2 203,0', 'changed_findings'),
])
def test_new_outside_self_intersection_is_not_treated_as_inherited(
    tmp_path, monkeypatch, source_shape, expected_list,
):
    manifest, payload, source, _ = _case(tmp_path, outside_bowtie=True)
    tree = ET.parse(source)
    tree.getroot().find("junction[@id='remote']").set('shape', source_shape)
    tree.write(source, encoding='utf-8')
    payload['inputs']['source_net']['sha256'] = file_sha256(source)
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    _runner(monkeypatch)
    report = audit.audit_hamburg_topology_candidate(manifest, tmp_path / 'audit')
    assert report['geometry_review']['status'] == report['status'] == 'review_required'
    background = report['geometry_review']['outside_surface_review']
    assert background['regression_status'] == 'review_required'
    assert len(background[expected_list]) == 1
    assert background['inherited_findings'] == []


def test_changed_outside_connection_witness_is_not_hidden_by_same_category_count(tmp_path, monkeypatch):
    manifest, payload, _, candidate = _case(tmp_path)
    tree = ET.parse(candidate)
    tree.getroot().set('candidate_marker', 'true')
    tree.write(candidate, encoding='utf-8')
    payload['artifacts']['network']['sha256'] = file_sha256(candidate)
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    _runner(monkeypatch)
    def connection_report(root, **kw):
        witness = 'lane_order:new' if root.get('candidate_marker') else 'lane_order:existing'
        return {'status': 'review_required', 'traffic_side': 'right', 'structural_failure_count': 0,
                'review_finding_count': 1, 'junctions': [{'junction_id': 'c',
                    'connection_mode_audit': {'review_findings': [witness], 'structural_failures': []}}]}
    monkeypatch.setattr(audit, 'audit_network_connection_mode', connection_report)
    report = audit.audit_hamburg_topology_candidate(manifest, tmp_path / 'audit')
    assert report['status'] == 'review_required'
    assert report['connection_comparison']['outside_scope_new_review_finding_count'] == 1


@pytest.mark.parametrize('difference, expected', [(0.01, 'pass'), (0.2, 'review_required')])
def test_exact_outside_geometry_uses_existing_precision_and_reports_actual_difference(
    tmp_path, monkeypatch, difference, expected,
):
    manifest, payload, _, candidate = _case(tmp_path)
    tree = ET.parse(candidate)
    lane = tree.getroot().find("edge[@id='far']/lane")
    lane.set('length', str(10 + difference))
    lane.set('shape', f'100,0 {110 + difference},0')
    tree.write(candidate, encoding='utf-8')
    payload['artifacts']['network']['sha256'] = file_sha256(candidate)
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    _runner(monkeypatch)
    report = audit.audit_hamburg_topology_candidate(manifest, tmp_path / 'audit')
    assert report['outside_scope_preservation']['status'] == report['status'] == expected
    row = report['outside_scope_preservation']['changed_outside_edges'][0]
    assert row['maximum_vertex_displacement_m'] == pytest.approx(difference)
    assert row['maximum_length_difference_m'] == pytest.approx(difference)
    assert row['geometry_precision_m'] == 0.1
    assert row['within_geometry_precision'] is (expected == 'pass')


def test_declared_context_polygon_is_checked_without_changing_it(tmp_path, monkeypatch):
    manifest, payload, _, candidate = _case(tmp_path, outside_bowtie=True)
    payload['context_rebuild'] = {'plan': {'groups': [{'join_id': 'remote', 'source_node_ids': ['remote']}]}}
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    _runner(monkeypatch)
    report = audit.audit_hamburg_topology_candidate(manifest, tmp_path / 'audit')
    assert report['geometry_review']['target_surface_findings'][0]['junction_id'] == 'remote'
    assert report['geometry_review']['outside_surface_findings'] == []
    assert file_sha256(candidate) == payload['artifacts']['network']['sha256']


@pytest.mark.parametrize('change', ['external', 'fixed_boundary'])
def test_reload_change_blocks_even_when_constructor_passes(tmp_path, monkeypatch, change):
    manifest, _, _, _ = _case(tmp_path)
    def mutate(root):
        if change == 'external':
            root.find("edge[@id='far']/lane").set('length', '10.01')
        else:
            root.find("junction[@id='j']").set('shape', '10,-2 12,-2 12,2 10,2.01')
    _runner(monkeypatch, mutate)
    report = audit.audit_hamburg_topology_candidate(manifest, tmp_path / 'audit')
    assert report['reload_stability']['status'] == report['status'] == 'blocked'
    assert report['reload_stability']['changed_external_edges'] == (['far'] if change == 'external' else [])
    assert report['reload_stability']['changed_target_boundaries'] == (['j'] if change == 'fixed_boundary' else [])


def test_outside_permission_loss_is_not_hidden_by_declared_topology_pass(tmp_path, monkeypatch):
    manifest, payload, _, candidate = _case(tmp_path)
    tree = ET.parse(candidate)
    tree.getroot().find("edge[@id='far']/lane").set('disallow', 'passenger')
    tree.write(candidate, encoding='utf-8')
    payload['artifacts']['network']['sha256'] = file_sha256(candidate)
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    _runner(monkeypatch)
    report = audit.audit_hamburg_topology_candidate(manifest, tmp_path / 'audit')
    assert report['outside_scope_preservation']['status'] == report['status'] == 'blocked'
    assert report['outside_scope_preservation']['changed_outside_edges'][0]['edge_id'] == 'far'


def test_missing_required_connection_cannot_inherit_manifest_pass(tmp_path, monkeypatch):
    manifest, payload, _, _ = _case(tmp_path)
    payload['official_connection_audit']['required'][0]['sumo_connection'][2] = 'missing'
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    _runner(monkeypatch)
    report = audit.audit_hamburg_topology_candidate(manifest, tmp_path / 'audit')
    assert report['official_connectivity']['status'] == report['status'] == 'blocked'
    assert report['official_connectivity']['missing_actual_connections'] == [['in', 0, 'missing', 0]]


def test_hash_and_new_destination_are_checked_before_any_command(tmp_path, monkeypatch):
    manifest, _, _, candidate = _case(tmp_path)
    monkeypatch.setattr(audit, 'run_command', lambda *a, **kw: pytest.fail('must not run'))
    with pytest.raises(ValueError, match='must not already exist'):
        audit.audit_hamburg_topology_candidate(manifest, tmp_path)
    candidate.write_text('<net/>', encoding='utf-8')
    with pytest.raises(ValueError, match='SHA-256'):
        audit.audit_hamburg_topology_candidate(manifest, tmp_path / 'audit')
    assert not (tmp_path / 'audit').exists()


@pytest.mark.skipif(shutil.which('netconvert') is None, reason='netconvert required')
def test_real_native_reload_and_static_checks_on_one_small_junction(tmp_path):
    manifest, payload, source, candidate = _case(tmp_path)
    nodes, edges = tmp_path / 'nodes.xml', tmp_path / 'edges.xml'
    nodes.write_text('<nodes><node id="a" x="0" y="0"/><node id="j" x="50" y="0"/>'
                     '<node id="b" x="50" y="50"/></nodes>', encoding='utf-8')
    edges.write_text('<edges><edge id="in" from="a" to="j"/><edge id="out" from="j" to="b"/></edges>',
                     encoding='utf-8')
    subprocess.run(['netconvert', '--node-files', str(nodes), '--edge-files', str(edges),
                    '--output-file', str(source)], check=True, capture_output=True, timeout=30)
    shutil.copy2(source, candidate)
    for record in (payload['inputs']['source_net'], payload['artifacts']['network']):
        record['sha256'] = file_sha256(Path(record['path']))
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    report = audit.audit_hamburg_topology_candidate(manifest, tmp_path / 'audit')
    assert report['structural']['status'] == report['official_connectivity']['status'] == 'pass'
    assert report['reload_stability']['status'] == report['outside_scope_preservation']['status'] == 'pass'
    assert report['inputs_unchanged'] is True
