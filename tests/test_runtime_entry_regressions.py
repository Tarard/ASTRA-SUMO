"""Installed CLI and session lifecycle checks that do not depend on a live GUI."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from torii_sumo import cli, mcp_contract_tools as contracts
from torii_sumo.core.environment import collect_environment_report
from torii_sumo.core.workflow_router import run_auto_workflow


def test_ask_first_does_not_execute_a_recognized_scene(tmp_path):
    calls = []
    result = run_auto_workflow(user_request='Build a four-way TLS intersection', output_dir=tmp_path,
        autonomy_mode='ask-first', intersection_scene_func=lambda *a, **k: calls.append(a) or {'status':'pass'})
    assert not calls
    assert result['execution_status']=='plan-only'


@pytest.mark.parametrize('operation', ['open','observe','act'])
def test_blocked_netedit_operation_has_a_failure_summary(monkeypatch, operation):
    monkeypatch.setattr(contracts, 'sumo_netedit_session', lambda **k: {'status':'blocked','operation':operation,'reason':'No usable session.'})
    arguments = {'open':['source.net.xml','candidate.net.xml','out','0'*64],
                 'observe':['session'], 'act':['session','recompute','0'*64]}
    result = getattr(contracts, 'torii_netedit_'+operation)(*arguments[operation])
    assert result.status=='blocked'
    assert 'blocked' in result.summary.lower()


def test_failed_native_versions_cannot_pass_preflight():
    report = collect_environment_report(which_func=lambda name: name, package_finder=lambda name: object(),
        version_runner=lambda *a: {'status':'fail','returncode':1})
    assert report.status=='blocked'


def test_missing_netconvert_cannot_pass_preflight():
    report = collect_environment_report(which_func=lambda name: None if name=='netconvert' else name,
        package_finder=lambda name: object(), version_runner=lambda *a: {'status':'pass','returncode':0})
    assert report.status=='blocked'
    assert 'netconvert binary not found' in report.warnings


def test_cached_runner_executes_catalog_and_selected_check_without_repository_parents(tmp_path):
    source = Path(__file__).resolve().parents[1]/'plugins/torii-sumo'
    plugin = tmp_path/'cache/plugin'
    shutil.copytree(source, plugin, ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    runner = plugin/'scripts/run_torii_sumo.py'
    command = [sys.executable, str(runner), '--cli']
    catalog = subprocess.run([*command,'workflows','--json'], cwd=tmp_path, stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, encoding='utf-8', timeout=40)
    assert catalog.returncode==0,catalog.stderr
    assert 'torii.workflow-catalog/v1' in catalog.stdout
    assert all(row['available'] for row in json.loads(catalog.stdout)['scenarios'])
    selection = tmp_path/'selection.json'
    selection.write_text(json.dumps({'user_request':'Check the environment','scenario_id':'environment_preflight',
                                    'reason':'Use the existing environment check.','arguments':{}}),encoding='utf-8')
    result = subprocess.run([*command,'workflow','selected',str(selection),'--json'], cwd=tmp_path,
                            capture_output=True,text=True,encoding='utf-8',timeout=40)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['executed'] is False


@pytest.mark.parametrize('observed_status', ['pass','blocked'])
def test_one_shot_netedit_review_closes_in_the_same_process(monkeypatch, tmp_path, capsys, observed_status):
    calls = []
    def opened(*args, **kwargs):
        calls.append('open')
        return contracts.ToriiToolResult(status='pass',summary='Opened.',payload={'session_id':'owned'})
    def observed(*args, **kwargs):
        calls.append('observe')
        return contracts.ToriiToolResult(status=observed_status,summary='Observation result.')
    def closed(*args, **kwargs):
        calls.append('close')
        assert args[0]=='owned' and kwargs['mode']=='abort'
        return contracts.ToriiToolResult(status='pass',summary='Closed.')
    monkeypatch.setattr(cli,'torii_netedit_open',opened)
    monkeypatch.setattr(cli,'torii_netedit_observe',observed)
    monkeypatch.setattr(cli,'torii_netedit_close',closed)
    code = cli.main(['netedit','review','source.net.xml',str(tmp_path/'out'),'0'*64,'--json'])
    assert calls==['open','observe','close']
    assert code==(0 if observed_status=='pass' else 3)
    assert json.loads(capsys.readouterr().out)['status']==observed_status


def test_separate_cli_open_is_rejected_before_launch(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(cli,'torii_netedit_open',lambda *a,**k: calls.append(a) or contracts.ToriiToolResult(status='pass',summary='Opened.'))
    code = cli.main(['netedit','open','source.net.xml','candidate.net.xml',str(tmp_path),'0'*64,'--json'])
    assert not calls
    assert code==3
    assert 'persistent' in json.loads(capsys.readouterr().out)['error'].lower()
