"""Explicitly rerun the fixed observer after relocating its saved input paths."""
import argparse
from importlib.metadata import version
from importlib.util import find_spec, module_from_spec, spec_from_file_location
import json
from pathlib import Path
import runpy
import sys


def relocate(value, source, destination):
    if isinstance(value, dict):
        return {k: relocate(v, source, destination) for k, v in value.items()}
    if isinstance(value, list):
        return [relocate(v, source, destination) for v in value]
    return value.replace(source, destination) if isinstance(value, str) else value


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
    monitor = load(case / 'trace-v2/baseline/stall-monitor.json')
    library = Path(find_spec('libsumo').origin).parent / '_libsumo.pyd'
    assert audit['sha'](library) == monitor['native_library']['sha256']
    for name, expected in load(case / 'trace-v2/baseline/preflight.json')['driver_hashes'].items():
        assert audit['sha'](repo / 'plugins/astra-sumo/src/astra_sumo/core' / name) == expected
    native = out / 'reference-input/evaluation/native'
    native.mkdir(parents=True)
    reference = audit['NATIVE']
    for path in reference.glob('*.gz'):
        (native / path.stem).write_bytes(raw(path.with_suffix('')))
    # The included observer instant file was checked attribute-for-attribute against this reference.
    (native / 'instant-e1.xml').write_bytes(raw(case / 'trace-v2/baseline/instant-e1.xml'))
    original_request = load(native / 'native-request.json')
    old_prefix = load(case / 'trace-v2/baseline/preflight.json')['source']
    request = relocate(original_request, old_prefix, str(native))
    request['command'][0] = str(repo / '.venv/Lib/site-packages/sumo/bin/sumo.exe')
    (native / 'native-request.json').write_text(json.dumps(request, indent=2)+'\n', encoding='utf-8')
    (out / 'observed-vehicles.csv').write_bytes(raw(case / 'trace-v2/observed-vehicles.csv'))
    script = out / 'record_substitution.py'
    script.write_bytes(raw(case / 'trace-v2/record_substitution.py'))
    sys.path.insert(0, str(repo / 'plugins/astra-sumo/src'))
    spec = spec_from_file_location('fixed_observer', script)
    observer = module_from_spec(spec)
    spec.loader.exec_module(observer)
    observer.ROOT, observer.REPO = out, repo
    observer.CASES = {'baseline': (str(native.parents[1]), 14400)}
    audit['save'](out / 'reproduction-plan.json', dict(fixed_input=True, changed_paths_only=True,
        new_demand_or_controller=False, reference_manifest_sha256=load(case / 'results.json')['reference_manifest_sha256']))
    print('Starting one complete fixed-input observation replay, 14400 s.', flush=True)
    observer.main('baseline')
    measured = audit['audit_trace'](out / 'baseline', historical_aux=False)
    assert measured == load(case / 'results.json')['observation']
    audit['save'](out / 'reproduction.json', dict(status='pass', new_native_observation_run=True,
        formal_result=False, new_calibration_candidate=False, measurement=measured))
    print('Observer parity, cohort identity and TLS/program export equality: pass', flush=True)


if __name__ == '__main__':
    main()
