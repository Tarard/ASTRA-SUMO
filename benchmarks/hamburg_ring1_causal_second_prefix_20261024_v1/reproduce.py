"""Verify offline or explicitly rerun one saved1800s prefix in a new directory."""
import argparse
import gzip
import importlib
from importlib.metadata import version
import json
from pathlib import Path
import runpy
import sys
import traceback

CASE=Path(__file__).resolve().parent
REPO=CASE.parents[1]
CASES={'reference':'reference','120232':'advance-second900-120232','126232':'advance-second900-126232'}


def new_output(path):
    out=Path(path).resolve()
    assert not out.exists() and not out.is_relative_to(REPO/'benchmarks') and not CASE.is_relative_to(out), 'Use a new output directory outside frozen benchmarks.'
    return out


def native_prefix(label,path):
    out=new_output(path)
    f=runpy.run_path(str(CASE/'freeze_evidence.py'))
    f['verify'](CASE)
    out.mkdir(parents=True)
    try:
        run=CASES[label]
        p=f['jread'](CASE,Path(run)/'preflight.json')
        assert Path(sys.executable).resolve()==(REPO/'.venv/Scripts/python.exe').resolve()
        assert {version(n) for n in ('eclipse-sumo','libsumo','traci','sumolib')}=={'1.27.1'}
        for name,expected in p['implementation_sha256'].items():
            module=importlib.import_module('astra_sumo.core.'+name.removesuffix('.py'))
            assert f['sha'](Path(module.__file__))==expected
        binaries=[REPO/'.venv/Scripts/python.exe',REPO/'.venv/Lib/site-packages/sumo/bin/sumo.exe']+list((REPO/'.venv/Lib/site-packages/libsumo').glob('*.pyd'))
        expected={name.replace('\\','/').split('/')[-1]:h for name,h in p['binaries'].items()}
        assert len(binaries)>=3 and {path.name:f['sha'](path) for path in binaries}==expected
        prepared=out/'prepared'
        prepared.mkdir()
        for name in f['PREPARED']:
            (prepared/name).write_bytes(f['read'](CASE,Path(run)/'prepared'/name))
        (out/'prefix26.csv').write_bytes(f['read'](CASE,'study-source/prefix26.csv'))
        from astra_sumo.core.detector_demand_replay import run_point_detector_demand
        from astra_sumo.core.hamburg_od_spsa import _body_hold_nodes
        from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages
        result=run_point_detector_demand(net_file=prepared/'network.net.xml',route_file=prepared/'demand.rou.xml',
            e1_additional_file=prepared/'field-loops.add.xml',expected_counts_csv=out/'prefix26.csv',
            output_dir=out/'native',end_s=1800,step_length=.1,seed=104,backend='libsumo',sumo_binary=str(binaries[1]),timeout_seconds=4800,
            signal_program_file=prepared/'control.add.xml',lane_intentions_file=prepared/'lane-intentions.json',
            junction_body_hold=True,junction_body_hold_nodes=_body_hold_nodes(prepared))
        f['save'](out/'core-result.json',result)
        events=read_contributing_e1_passages(instant_file=out/'native/instant-e1.xml',periodic_file=out/'native/field-counts.xml',detector_ids=result['instant_to_periodic_ids'],step_length=.1)
        (out/'contributing-passages.json.gz').write_bytes(gzip.compress(json.dumps(events).encode(),mtime=0))
        native=f['intervals']((out/'native/field-counts.xml').read_bytes())
        old=f['intervals'](f['read'](CASE,Path(run)/'native/field-counts.xml'))
        old_events=json.loads(gzip.decompress(f['read'](CASE,Path(run)/'contributing-passages.json.gz')))
        checks=dict(native_pass=result['native_status']=='pass',errors_empty=not result['errors'],observer_complete=result['observer_complete'] and result['missing_field_bins']==0,
            all26_attributes=len(native)==26 and native==old,
            all18000_summary_except_CPU=f['summary_prefix']((out/'native/summary.xml').read_bytes())==f['summary_prefix'](f['read'](CASE,Path(run)/'native/summary.xml')),
            complete_events=events['passages']==old_events['passages'] and events['outside_periodic_intervals']==old_events['outside_periodic_intervals'],reconciled=events['reconciled'])
        target=f['rows'](CASE,Path(run)/'prepared/initialization-counts.csv')+f['rows'](CASE,Path(run)/'prepared/expected-counts.csv')
        coverage=[dict(detector_id=r['detector_id'],begin=r['begin'],end=r['end'],expected_total=r['expected_total'],measured_nVehContrib=native.get(f['prior'].key(r),{}).get('nVehContrib'),status='measured_prefix' if f['prior'].key(r) in native else 'unmeasured_after_prefix_horizon') for r in target]
        f['check_coverage'](coverage,native,target)
        f['save'](out/'all182-coverage.json',coverage)
        report=dict(status='pass' if all(checks.values()) else 'blocked',native_runs=1,case=run,checks=checks,
            measured_real_rows=26,unmeasured_real_rows=156,main130_metrics=None,full_health_not_claimed=True,full130_calibration_acceptance=False,
            claim_boundary='Fresh1800s native prefix reproduction only. Future26initial+130main rows stay unknown; no full validation or field truth claim.')
        f['save'](out/'reproduction.json',report)
        assert all(checks.values()), 'Retain any native mismatch for review.'
        print(json.dumps(report,ensure_ascii=False))
    except Exception as error:
        (out/'failure.log').write_text(traceback.format_exc(),encoding='utf-8')
        f['save'](out/'failure.json',dict(status='blocked',error=f'{type(error).__name__}: {error}',partial_outputs_preserved=True))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('verify','native-prefix'),default='verify')
    parser.add_argument('--case',choices=tuple(CASES),default='126232')
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args()
    if args.mode=='verify':
        assert args.output_dir is None
        runpy.run_path(str(CASE/'freeze_evidence.py'))['verify'](CASE)
    else:
        assert args.output_dir is not None
        native_prefix(args.case,args.output_dir)
