"""Verify offline, or explicitly rerun the saved full native case in a new directory."""
import argparse
import csv
import gzip
import importlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import traceback

CASE=Path(__file__).resolve().parent
REPO=CASE.parents[1]


def new_output(path):
    out=Path(path).resolve()
    assert not out.exists() and not out.is_relative_to(REPO/'benchmarks') and not CASE.is_relative_to(out), 'Use a new output directory outside frozen benchmarks.'
    return out


def assert_project_module_paths(paths):
    assert set(paths)=={'libsumo','traci','sumolib'} and all(Path(p).resolve().is_relative_to((REPO/'.venv').resolve()) for p in paths.values()), 'Use project SUMO modules; set project PYTHONPATH and SUMO_HOME before starting Python.'


def native_full(path,label):
    out=new_output(path)
    f=runpy.run_path(str(CASE/'freeze_evidence.py'))
    f['verify'](CASE)
    prefix=f['case_prefix'](label)
    out.mkdir(parents=True)
    try:
        assert Path(sys.executable).resolve()==(REPO/'.venv/Scripts/python.exe').resolve(), 'Use this repository project.venv.'
        versions={n:version(n) for n in ('eclipse-sumo','libsumo','traci','sumolib')}
        modules={n:str(Path(importlib.import_module(n).__file__).resolve()) for n in ('libsumo','traci','sumolib')}
        runtime=dict(status='checking',python=sys.executable,versions=versions,module_paths=modules,
            environment={n:os.environ.get(n) for n in ('PYTHONPATH','SUMO_HOME')},native_runs=0)
        f['save'](out/'runtime-check.json',runtime)
        assert set(versions.values())=={'1.27.1'}
        assert_project_module_paths(modules)
        assert importlib.import_module('traci').__version__=='1.27.1'
        preflight=f['jread'](CASE,prefix/'preflight.json')
        parent_preflight=f['jread'](f['PARENT'],'full/preflight.json')
        assert all(f['sha'](Path(modules[n]))==row['sha256'] for n,row in preflight['runtime']['modules'].items())
        for name,expected in parent_preflight['implementation_sha256'].items():
            if name in {'audit_response.py','audit_scope.py','run_fixture.py','run_full.py'}:
                continue
            module=importlib.import_module('astra_sumo.core.'+name.removesuffix('.py'))
            assert f['sha'](Path(module.__file__))==expected, 'Use the recorded core implementation: '+name
        binaries=[REPO/'.venv/Scripts/python.exe',REPO/'.venv/Lib/site-packages/sumo/bin/sumo.exe']+list((REPO/'.venv/Lib/site-packages/libsumo').glob('*.pyd'))
        expected={p.replace('\\','/').split('/')[-1]:h for p,h in preflight['runtime']['binaries'].items()}
        assert len(binaries)>=3 and {p.name:f['sha'](p) for p in binaries}==expected
        runtime.update(status='pass',binaries_sha256={str(p):f['sha'](p) for p in binaries})
        f['save'](out/'runtime-check.json',runtime)
        os.environ['PYTHONPATH']=str(REPO/'.venv/Lib/site-packages')+';'+str(REPO/'plugins/astra-sumo/src')
        os.environ['SUMO_HOME']=str(REPO/'.venv/Lib/site-packages/sumo')
        runtime['child_pythonpath']=os.environ['PYTHONPATH']
        f['save'](out/'runtime-check.json',runtime)
        prepared,evaluation=out/'prepared',out/'evaluation'
        prepared.mkdir()
        evaluation.mkdir()
        for name in f['PREPARED']:
            (prepared/name).write_bytes(f['read'](CASE,prefix/'prepared'/name))
        for name in ('demand.rou.xml','lane-intentions.json'):
            shutil.copyfile(prepared/name,evaluation/name)
        from astra_sumo.core.detector_demand_replay import run_point_detector_demand
        from astra_sumo.core.hamburg_od_spsa import _body_hold_nodes,summarize_hamburg_od_measurement
        from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages
        result=run_point_detector_demand(net_file=prepared/'network.net.xml',route_file=prepared/'demand.rou.xml',
            e1_additional_file=prepared/'field-loops.add.xml',expected_counts_csv=prepared/'expected-counts.csv',
            output_dir=evaluation/'native',end_s=14400,step_length=.1,seed=104,backend='libsumo',
            sumo_binary=str(binaries[1]),timeout_seconds=4800,signal_program_file=prepared/'control.add.xml',
            lane_intentions_file=prepared/'lane-intentions.json',junction_body_hold=True,junction_body_hold_nodes=_body_hold_nodes(prepared))
        f['save'](out/'core-result.json',result)
        summarize_hamburg_od_measurement(prepared_dir=prepared,proposal_dir=evaluation,native=result)
        events=read_contributing_e1_passages(instant_file=evaluation/'native/instant-e1.xml',periodic_file=evaluation/'native/field-counts.xml',detector_ids=result['instant_to_periodic_ids'],step_length=.1)
        assert not result['errors'] and events['reconciled']
        (evaluation/'contributing-passages.json.gz').write_bytes(gzip.compress(json.dumps(events).encode(),mtime=0))
        # Existing native summarizer writes130/40; derive the other52/182 through the saved audit reader.
        detector=importlib.import_module('astra_sumo.core.detector_demand')
        initial=detector.read_csv_rows(prepared/'initialization-counts.csv')
        main=detector.read_csv_rows(prepared/'expected-counts.csv')
        full_rows=detector.audit_expected_to_e1_strict(initial+main,evaluation/'native/field-counts.xml')
        with (evaluation/'all182-comparison.csv').open('w',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(full_rows[0]))
            writer.writeheader()
            writer.writerows(full_rows)
        signatures={r['vehicle_id']:[r['field_id']] for r in json.loads((prepared/'lane-intentions.json').read_bytes())['vehicles']}
        measured,_,outside,repeated=f['audit_measurement'](prepared,evaluation,signatures)
        expected_report=f['load'](CASE/'results.json')['audit']['cases'][label]['measurement']
        checks={k:measured[k]==expected_report[k] for k in ('main130','initial52','all182','station40_MAE','completion','healthy','safe','event_count')}
        frozen_counts=f['intervals'](f['read'](CASE,prefix/'evaluation/native/field-counts.xml'))
        fresh_counts=f['intervals']((evaluation/'native/field-counts.xml').read_bytes())
        checks['all208_native_counts']={k:int(v['nVehContrib']) for k,v in fresh_counts.items()}=={k:int(v['nVehContrib']) for k,v in frozen_counts.items()} and len(fresh_counts)==208
        checks.update(no_extra=not outside,no_repeat=not repeated)
        report=dict(status='pass' if all(checks.values()) else 'blocked',native_runs=1,checks=checks,measured=measured,
            reproduced_case='late535 explicit-departure response: '+label+'',calibration_completed=False,claim_boundary='Fresh explicit native full reproduction; matching the measured result is not zero calibration or field certification.')
        f['save'](out/'reproduction.json',report)
        assert all(checks.values()), 'Retain this native mismatch for review.'
        print(json.dumps(report,ensure_ascii=False))
    except Exception as error:
        (out/'failure.log').write_text(traceback.format_exc(),encoding='utf-8')
        f['save'](out/'failure.json',dict(status='blocked',error=f'{type(error).__name__}: {error}',partial_outputs_preserved=True))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('verify','native-full'),default='verify')
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--case',choices=('compress29','minus30','plus30','minus60','minus90'))
    args=parser.parse_args()
    if args.mode=='verify':
        assert args.output_dir is None and args.case is None, 'Offline verification needs no case or output directory.'
        runpy.run_path(str(CASE/'freeze_evidence.py'))['verify'](CASE)
    else:
        assert args.output_dir is not None and args.case is not None, 'Native reproduction requires a case and fresh output directory.'
        native_full(args.output_dir,args.case)
