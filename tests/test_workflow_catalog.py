import json
from pathlib import Path

import pytest

from torii_sumo.core.workflow_catalog import get_workflow_catalog, run_selected_workflow
from torii_sumo.core.workflow_router import run_auto_workflow


def decision(scenario='environment_preflight', arguments=None):
    return dict(user_request='请先检查运行环境，不要建网。', scenario_id=scenario,
                reason='The user requested environment checks only.', arguments=arguments or {})


def test_catalog_maps_scenarios_to_real_entries_and_separates_guidance():
    catalog = get_workflow_catalog()
    rows = {row['scenario_id']: row for row in catalog['scenarios']}
    assert len(rows) >= 18
    assert rows['hamburg_network']['required_arguments'] == ['request_file', 'output_dir']
    assert rows['experiment_planning']['kind'] == 'guidance'
    assert rows['experiment_planning']['entrypoint'] is None
    assert all(row['available'] for row in rows.values() if row['entrypoint'])
    json.dumps(catalog)


def test_plan_only_and_missing_inputs_do_not_execute(monkeypatch):
    from torii_sumo.tools import environment_tools
    calls = []
    monkeypatch.setattr(environment_tools, 'sumo_preflight', lambda: calls.append(True) or {'status':'pass'})
    assert run_selected_workflow(decision())['executed'] is False
    assert not calls
    missing = run_selected_workflow(decision('hamburg_network'), execute=True)
    assert missing['status'] == 'review_required'
    assert set(missing['missing_arguments']) == {'request_file', 'output_dir'}
    assert missing['executed'] is False
    assert run_selected_workflow(decision(), execute=True)['executed'] is True
    assert calls == [True]


def test_unknown_selection_or_arguments_never_falls_back_to_another_workflow():
    for value in [decision('__import__("os")'), decision(arguments={'shell_command':'anything'})]:
        report = run_selected_workflow(value, execute=True)
        assert report['status'] == 'blocked'
        assert report['executed'] is False
    guidance = run_selected_workflow(decision('experiment_planning'), execute=True)
    assert guidance['execution_status'] == 'guidance_only'
    assert guidance['executed'] is False
    invalid_guidance = run_selected_workflow(decision('experiment_planning', {'invented_option': True}), execute=True)
    assert invalid_guidance['status'] == 'blocked' and invalid_guidance['executed'] is False


def test_model_choice_bypasses_keyword_detector_and_preserves_inspect_only(tmp_path, monkeypatch):
    from torii_sumo.core import workflow_router
    from torii_sumo.tools import environment_tools
    calls = []
    def never_detect(text):
        raise AssertionError('The model choice must not be reclassified by keywords')
    monkeypatch.setattr(workflow_router, 'detect_workflow', never_detect)
    monkeypatch.setattr(environment_tools, 'sumo_preflight', lambda: calls.append(True) or {'status':'review_required', 'why':'test finding'})
    selected = decision()
    inspected = run_auto_workflow(user_request=selected['user_request'], output_dir=tmp_path,
        autonomy_mode='inspect-only', workflow_selection=selected)
    assert inspected['executed'] is False
    result = run_auto_workflow(user_request=selected['user_request'], output_dir=tmp_path,
        workflow_selection=selected)
    assert result['executed'] is True and result['status'] == 'review_required'
    assert calls == [True]


def test_comparison_requires_both_sides_even_though_function_arguments_are_optional():
    report = run_selected_workflow(decision('run_comparison', {'baseline_summary':'baseline.xml'}))
    assert report['status'] == 'review_required'
    assert report['executed'] is False
    assert report['missing_input_groups'] == [['variant_summary', 'variant_tripinfo']]


def test_cli_catalog_and_selected_route_share_the_same_entries(tmp_path, capsys):
    from torii_sumo import cli
    assert cli.main(['workflows', '--scenario', 'hamburg_network', '--json']) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [row['scenario_id'] for row in listed['scenarios']] == ['hamburg_network']
    request = tmp_path/'selection.json'
    request.write_text(json.dumps(decision()), encoding='utf-8')
    assert cli.main(['workflow', 'selected', str(request), '--json']) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned['executed'] is False
    assert planned['entrypoint'] == 'torii_sumo.tools.environment_tools:sumo_preflight'


def test_missing_source_file_prevents_even_a_valid_registered_call(tmp_path):
    report = run_selected_workflow(decision('hamburg_network', {
        'request_file': str(tmp_path/'absent.json'), 'output_dir': str(tmp_path/'out')}), execute=True)
    assert report['executed'] is False
    assert report['execution_status'] == 'needs_input'
    assert 'request_file' in report['missing_files']
    assert not (tmp_path/'out').exists()


def test_boolean_is_not_silently_converted_to_a_timeout(tmp_path):
    report = run_selected_workflow(decision('osm_network', {'output_dir':str(tmp_path/'out'),
        'bbox':'9.9,53.5,10.0,53.6', 'traffic_layers':'vehicle_core', 'timeout_seconds':True}))
    assert report['status'] == 'blocked'
    assert report['executed'] is False


@pytest.mark.parametrize('number', [float('nan'), float('inf')])
def test_nonfinite_numbers_are_rejected_before_dispatch(tmp_path, number):
    report = run_selected_workflow(decision('osm_network', {'output_dir':str(tmp_path/'out'),
        'bbox':'9.9,53.5,10.0,53.6', 'traffic_layers':'vehicle_core', 'timeout_seconds':number}))
    assert report['status'] == 'blocked' and report['executed'] is False


def test_custom_route_sampler_script_must_exist(tmp_path):
    candidates, counts = tmp_path/'candidates.csv', tmp_path/'counts.xml'
    candidates.write_text('header', encoding='utf-8')
    counts.write_text('<data/>', encoding='utf-8')
    report = run_selected_workflow(decision('detector_calibration', {'candidate_manifest_csv':str(candidates),
        'edge_data_file':str(counts), 'output_dir':str(tmp_path/'out'), 'route_sampler_script':str(tmp_path/'absent.py')}))
    assert report['execution_status'] == 'needs_input'
    assert 'route_sampler_script' in report['missing_files']


def test_checked_home_relative_file_is_the_file_passed_to_handler(tmp_path, monkeypatch):
    from torii_sumo.road_network import design_review
    path = tmp_path/'review.json'
    path.write_text('{}', encoding='utf-8')
    if not path.is_relative_to(Path.home()):
        pytest.skip('This check needs a temporary path inside the user home directory')
    home_relative = str(Path('~') / path.relative_to(Path.home()))
    def read(request_file, output_dir):
        return {'status':'pass', 'source_text':Path(request_file).read_text(encoding='utf-8')}
    monkeypatch.setattr(design_review, 'build_road_design_review', read)
    report = run_selected_workflow(decision('road_design_review',
        {'request_file':home_relative,'output_dir':str(tmp_path/'out')}), execute=True)
    assert report['status'] == 'pass'
    assert report['workflow_result']['source_text'] == '{}'
