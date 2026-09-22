import hashlib
import json
from pathlib import Path

import pytest

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.road_network.design_review import build_road_design_review


def _request(tmp_path, monkeypatch):
    from torii_sumo.road_network import design_rules
    pdf = tmp_path / 'plan.pdf'
    pdf.write_bytes(b'%PDF-1.7\nmanual record test\n')
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps(dict(schema='torii.engineering-plan-observations-request/v1',
        source=dict(path=str(pdf), sha256=file_sha256(pdf), title='Plan', document_date='2021-03-16', document_kind='design'),
        observations=[dict(id='cycle', page=67, location='north side', property='cycle_width',
                           value=256, unit='cm', width_basis='unknown')])), encoding='utf-8')
    standard = tmp_path / 'standard.pdf'
    standard.write_bytes(b'%PDF-1.7\nstandard fixture\n')
    monkeypatch.setitem(design_rules._BOOKS[design_rules.RESTRA], 'source_sha256', file_sha256(standard))
    request = tmp_path / 'request.json'
    request.write_text(json.dumps(dict(schema='torii.road-design-review-request/v1',
        standard_files={design_rules.RESTRA: dict(path=str(standard), sha256=file_sha256(standard))},
        plan_requests=[dict(id='p', path=str(plan), sha256=file_sha256(plan))],
        checks=[dict(id='unknown_width_basis', rule='cycle_lane_width',
            observation=dict(plan_id='p', observation_id='cycle'),
            parameters=dict(context=dict(purpose='design_review', jurisdiction='DE-HH', assessment_date='2026-09-08',
                                          standard_id=design_rules.RESTRA), facility_type='radfahrstreifen'))])), encoding='utf-8')
    return request, plan, pdf, standard


def test_review_preserves_unknown_plan_basis_and_both_source_identities(tmp_path, monkeypatch):
    request, _, pdf, standard = _request(tmp_path, monkeypatch)
    output = tmp_path / 'review'
    result = build_road_design_review(request_file=request, output_dir=output)
    assert result['status'] == 'pass'
    saved = json.loads((output / 'design-review.json').read_text(encoding='utf-8'))
    check = saved['checks'][0]
    assert check['parameters']['width_m'] == 2.56
    assert check['parameters']['width_basis'] == 'unknown'
    assert check['observation']['field_status'] == 'not_verified'
    assert check['observation_source']['sha256'] == file_sha256(pdf)
    assert check['result']['sources'][0]['ref']['source_sha256'] == file_sha256(standard)
    assert check['result']['numeric_evaluation_performed'] is False
    assert saved['decision'] == 'review_required'
    assert saved['network_changed'] is False
    with pytest.raises(ValueError, match='new output'):
        build_road_design_review(request_file=request, output_dir=output)
    data = json.loads(request.read_text(encoding='utf-8'))
    data['checks'][0]['parameters']['width_basis'] = 'including_markings'
    request.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='override'):
        build_road_design_review(request_file=request, output_dir=tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()


def test_shared_pdf_cannot_change_between_two_plan_reads(tmp_path, monkeypatch):
    from torii_sumo.road_network import design_review
    request, plan, pdf, _ = _request(tmp_path, monkeypatch)
    replacement = b'%PDF-1.7\nchanged between observations\n'
    plan2 = tmp_path / 'plan2.json'
    value = json.loads(plan.read_text(encoding='utf-8'))
    value['source']['sha256'] = hashlib.sha256(replacement).hexdigest()
    plan2.write_text(json.dumps(value), encoding='utf-8')
    payload = json.loads(request.read_text(encoding='utf-8'))
    payload['plan_requests'].append(dict(id='p2', path=str(plan2), sha256=file_sha256(plan2)))
    request.write_text(json.dumps(payload), encoding='utf-8')
    read = design_review.read_engineering_plan_observations
    def changing_source(path, target_date):
        result = read(path, target_date)
        if Path(path) == plan:
            pdf.write_bytes(replacement)
        return result
    monkeypatch.setattr(design_review, 'read_engineering_plan_observations', changing_source)
    with pytest.raises(ValueError, match='changed|identity'):
        build_road_design_review(request_file=request, output_dir=tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()


@pytest.mark.parametrize('change', ['plan_requests_type', 'plan_id_type', 'observation_id_empty', 'not_a_width'])
def test_invalid_linked_observations_are_rejected_before_output(tmp_path, monkeypatch, change):
    request, plan, _, _ = _request(tmp_path, monkeypatch)
    payload = json.loads(request.read_text(encoding='utf-8'))
    if change == 'plan_requests_type':
        payload['plan_requests'] = {'not': 'a list'}
    elif change == 'plan_id_type':
        payload['checks'][0]['observation']['plan_id'] = []
    elif change == 'observation_id_empty':
        payload['checks'][0]['observation']['observation_id'] = ''
    else:
        data = json.loads(plan.read_text(encoding='utf-8'))
        data['observations'][0]['property'] = 'curb_height'
        plan.write_text(json.dumps(data), encoding='utf-8')
        payload['plan_requests'][0]['sha256'] = file_sha256(plan)
    request.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(ValueError):
        build_road_design_review(request_file=request, output_dir=tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()


def test_linked_plan_measurements_do_not_default_to_zero_uncertainty(tmp_path, monkeypatch):
    request, plan, _, _ = _request(tmp_path, monkeypatch)
    data = json.loads(plan.read_text(encoding='utf-8'))
    data['observations'][0].update(value=275, width_basis='including_markings')
    plan.write_text(json.dumps(data), encoding='utf-8')
    payload = json.loads(request.read_text(encoding='utf-8'))
    payload['plan_requests'][0]['sha256'] = file_sha256(plan)
    request.write_text(json.dumps(payload), encoding='utf-8')
    result = build_road_design_review(request_file=request, output_dir=tmp_path / 'review')
    saved = json.loads(Path(result['report_file']).read_text(encoding='utf-8'))
    assert saved['checks'][0]['result']['numeric_evaluation_performed'] is False
