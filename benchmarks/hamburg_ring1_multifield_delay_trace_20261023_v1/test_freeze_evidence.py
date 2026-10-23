"""Offline parity refusal and path-only relocation checks; no native run."""
from pathlib import Path
import runpy

import pytest


HERE = Path(__file__).resolve().parent
audit = runpy.run_path(str(HERE / 'freeze_evidence.py'))
reproduce = runpy.run_path(str(HERE / 'reproduce.py'))


def test_incomplete_observer_parity_is_rejected():
    result = dict(status='pass', formal_result=False, checks={str(i): True for i in range(12)},
        closed_e1_rows=208, summary_steps_checked=144000, instant_e1_events_checked=235746, arrived_routes_checked=8596)
    audit['check_parity_gate'](result)
    result['checks']['3'] = False
    with pytest.raises(AssertionError, match='twelve'):
        audit['check_parity_gate'](result)


def test_relocation_preserves_driver_options_and_does_not_mutate_source():
    original = dict(command=['sumo', '--seed', '104', '--step-length', '0.1', '--route-files', 'OLD/demand.rou.xml'],
        backend='libsumo', junction_body_hold=True, monitor_interval_s=None, route_file='OLD/demand.rou.xml')
    result = reproduce['relocate'](original, 'OLD', 'NEW')
    assert result == {**original, 'route_file': 'NEW/demand.rou.xml', 'command': original['command'][:-1] + ['NEW/demand.rou.xml']}
    assert original['route_file'] == 'OLD/demand.rou.xml' and result['junction_body_hold'] is True
