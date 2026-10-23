"""Explicitly rerun the whole frozen study with the existing CLI; never reuse historical paths."""
import argparse
import csv
import hashlib
from importlib.metadata import version
from importlib.util import find_spec
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True, help='New directory outside this frozen package.')
    parser.add_argument('--initial-cache', type=Path, help='Optional actual current cache; omission adds one fresh initial native run.')
    args = parser.parse_args()
    case, out = Path(__file__).resolve().parent, args.output_dir.resolve()
    audit = runpy.run_path(str(case / 'freeze_evidence.py'))
    audit['verify_manifest'](case)
    if out.exists() or out.is_relative_to(case) or case.is_relative_to(out):
        raise ValueError('Use a new output directory separate from the frozen evidence.')
    repo, raw, load = case.parents[1], audit['raw'], audit['load']
    assert Path(sys.prefix).resolve() == repo / '.venv'
    metadata = load(case / 'run-execution/execution.json')
    assert all(version(name) == '1.27.1' for name in ('eclipse-sumo', 'libsumo', 'traci', 'sumolib'))
    library = Path(find_spec('libsumo').origin).parent / '_libsumo.pyd'
    assert hashlib.sha256(library.read_bytes()).hexdigest() == metadata['libsumo_sha256']
    cli = repo / 'plugins/astra-sumo/scripts/run_hamburg_od_spsa.py'
    assert cli.read_bytes() == raw(case / 'run-execution/run.py'), 'Use the frozen source commit; do not silently restore old code.'
    for relative, expected in metadata['implementation_sha256'].items():
        source = repo / 'plugins/astra-sumo/src' / Path(relative.replace('\\', '/'))
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected, relative
    cache = args.initial_cache.resolve(strict=True) if args.initial_cache else None
    prepared = out / 'prepared'
    prepared.mkdir(parents=True)
    for path in (case / 'prepared').glob('*.gz'):
        (prepared / path.stem).write_bytes(raw(path))
    for name in ('settings.json', 'preparation.json'):
        (out / name).write_bytes(raw(case / name))
    settings = load(out / 'settings.json')
    fresh = cache is None
    command = [sys.executable, str(cli), '--prepared-dir', str(prepared), '--output-dir', str(out / 'run'),
        '--settings', str(out / 'settings.json'), '--backend', 'libsumo', '--parallel-pair', '--stop-on-unsafe']
    if cache:
        command += ['--initial-evaluation-dir', str(cache)]
    plan = {'command': command, 'fresh_initial': fresh, 'extra_initial_native_run': int(fresh),
        'normal_path_native_budget': 2 * settings['iterations'] + 1 + int(fresh),
        'source_manifest_sha256': hashlib.sha256((case / 'manifest.json').read_bytes()).hexdigest(),
        'historical_absolute_paths_used': False}
    (out / 'reproduction-plan.json').write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
    print(f"Starting the complete native study; fresh initial={fresh}, normal-path budget={plan['normal_path_native_budget']}. Unsafe/failure may stop earlier.", flush=True)
    with (out / 'stdout.log').open('w', encoding='utf-8') as stdout, (out / 'stderr.log').open('w', encoding='utf-8') as stderr:
        process = subprocess.run(command, cwd=repo, stdout=stdout, stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, check=False)
    actual, tables = audit['audit_study'](out, initial_source=cache, require_reviews=False)
    expected = load(case / 'results.json')
    for key in ('completed_updates', 'update_endpoint', 'actual_final_measurement', 'selected_evaluation',
                'best_safe_by_SSE', 'best_safe_by_MAE', 'any_healthy_native_zero', 'evaluations'):
        assert actual[key] == expected[key], key
    assert actual['new_native_evaluations'] == expected['new_native_evaluations'] + int(fresh and expected['initial_reused'])
    if process.returncode and not expected['declared_failure']:
        raise ValueError('Unexpected native workflow failure. Preserve stdout.log, stderr.log and run/failure.json.')
    for name, data in tables.items():
        for label, rows in data.items():
            target = out / 'count-checks' / name / (label + '.csv')
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    audit['save'](out / 'reproduction.json', dict(status='pass', fresh_initial=fresh,
        extra_initial_native_run=int(fresh), formal_result=False, frozen_logical_result_equality=True, actual=actual))
    print('Whole-study measurements, 130/40/52/182 and math equality: pass', flush=True)


if __name__ == '__main__':
    main()
