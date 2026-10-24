"""Explicitly rerun the frozen 54-ID observer with relocated fixed inputs."""
import argparse
from importlib.metadata import version
from importlib.util import find_spec, module_from_spec, spec_from_file_location
import json
from pathlib import Path
import runpy
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    case, out = Path(__file__).resolve().parent, args.output_dir.resolve()
    audit = runpy.run_path(str(case / 'freeze_evidence.py'))
    audit['verify'](case)
    repo, raw, load = case.parents[1], audit['raw'], audit['load']
    assert Path(sys.prefix).resolve() == (repo / '.venv').resolve()
    assert {version(n) for n in ('eclipse-sumo', 'libsumo', 'traci', 'sumolib')} == {'1.27.1'}
    assert not out.exists() and not out.is_relative_to(case) and not case.is_relative_to(out)
    original_trace = case / 'z5-observation/baseline'
    library = Path(find_spec('libsumo').origin).parent / '_libsumo.pyd'
    assert audit['sha'](library) == load(original_trace / 'stall-monitor.json')['native_library']['sha256']
    for name, expected in load(original_trace / 'preflight.json')['driver_hashes'].items():
        assert audit['sha'](repo / 'plugins/astra-sumo/src/astra_sumo/core' / name) == expected
    reference = out / 'reference-input/evaluation/native'
    reference.mkdir(parents=True)
    for p in audit['NATIVE'].glob('*.gz'):
        (reference / p.stem).write_bytes(raw(p.with_suffix('')))
    # Frozen observer instant attributes were checked against the original full before curation.
    (reference / 'instant-e1.xml').write_bytes(raw(original_trace / 'instant-e1.xml'))
    old_tools = runpy.run_path(str(audit['OLD_OBSERVER'] / 'reproduce.py'))
    request = old_tools['relocate'](load(reference / 'native-request.json'), load(original_trace / 'preflight.json')['source'], str(reference))
    request['command'][0] = str(repo / '.venv/Lib/site-packages/sumo/bin/sumo.exe')
    (reference / 'native-request.json').write_text(json.dumps(request, indent=2)+'\n', encoding='utf-8')
    cohort = out / 'next-observation-cohort'
    cohort.mkdir()
    (cohort / 'cohort.csv').write_bytes(raw(case / 'next-observation-cohort/cohort.csv'))
    trace = out / 'z5-observation'
    trace.mkdir()
    script = trace / 'record_z5.py'
    script.write_bytes(raw(case / 'z5-observation/record_z5.py'))
    sys.path.insert(0, str(repo / 'plugins/astra-sumo/src'))
    spec = spec_from_file_location('fixed_observer54', script)
    observer = module_from_spec(spec)
    spec.loader.exec_module(observer)
    observer.ROOT, observer.REPO = trace, repo
    observer.CASES = {'baseline': (str(reference.parents[1]), 14400)}
    audit['save'](out / 'reproduction-plan.json', dict(fixed_input=True, changed_paths_only=True, new_calibration_candidate=False,
        frozen_manifest_sha256=audit['sha'](case / 'manifest.json'), cohort54=True))
    print('Starting one complete unchanged-input 54-ID native observation, 14400 s.', flush=True)
    observer.main('baseline')
    parity = audit['audit_parity'](trace / 'baseline', historical_aux=False)
    assert parity == load(case / 'results.json')['parity']
    proof = load(case / 'large-trace-proof.json')
    for name, expected in proof['xml_semantics'].items():
        assert audit['xml_semantics'](trace / 'baseline' / name) == expected
    audit['save'](out / 'reproduction.json', dict(status='pass', formal_result=False, new_native_observation_run=True,
        new_calibration_candidate=False, parity=parity, large_FCD_TLS_LC_semantics_equal=True))
    print('12 parity checks, fixed input and FCD/TLS/program/LC semantic equality: pass', flush=True)


if __name__ == '__main__':
    main()
