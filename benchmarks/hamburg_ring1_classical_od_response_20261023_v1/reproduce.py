"""Explicitly run a full 14400 s native OD probe after verifying frozen hashes."""
import argparse
import csv
import gzip
import hashlib
from importlib.metadata import version
import io
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, help='A completed run listed in results.json.')
    parser.add_argument('--output-dir', required=True, type=Path, help='New directory outside the frozen package.')
    args = parser.parse_args()
    case, out = Path(__file__).resolve().parent, args.output_dir.resolve()
    manifest = json.loads((case / 'manifest.json').read_bytes())
    listed = {row['path'] for row in manifest['files']}
    assert len(listed) == len(manifest['files'])
    assert listed == {p.relative_to(case).as_posix() for p in case.rglob('*')
                      if p.is_file() and p.name != 'manifest.json' and '__pycache__' not in p.parts}
    for row in manifest['files']:
        path = (case / row['path']).resolve(strict=True)
        assert path.is_relative_to(case) and path.stat().st_size == row['bytes']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256'], row['path']
    metadata = json.loads((case / 'results.json').read_bytes())
    assert args.case in metadata['complete_runs']
    if out.exists() or out.is_relative_to(case) or case.is_relative_to(out):
        raise ValueError('Use a new output directory separate from the frozen evidence.')
    assert Path(sys.prefix).resolve() == case.parents[1] / '.venv'
    assert all(version(n) == '1.27.1' for n in ('eclipse-sumo', 'libsumo', 'traci', 'sumolib'))
    source = case / args.case
    prepared = out / 'prepared'
    for path in (case / 'prepared').glob('*.gz'):
        assert path.relative_to(case).as_posix() in listed
        prepared.mkdir(parents=True, exist_ok=True)
        (prepared / path.stem).write_bytes(gzip.decompress(path.read_bytes()))
    required = [source / 'evaluation' / name for name in
                ('od-vector.json.gz', 'evaluation.json.gz', 'all182-comparison.csv.gz', 'station-comparison.csv.gz')]
    assert all(path.relative_to(case).as_posix() in listed for path in required)
    vector = json.loads(gzip.decompress(required[0].read_bytes()))
    assert len(vector['values']) == 294 and sum(vector['integer_counts']) == vector['vehicle_count'] == 8640
    assert vector['source_model_sha256'] == hashlib.sha256((prepared / 'od-model.json').read_bytes()).hexdigest()
    import sumo
    from astra_sumo.core.detector_demand import audit_expected_to_e1_strict, read_csv_rows, write_csv
    from astra_sumo.core.hamburg_od_spsa import measure_hamburg_od

    print('Starting full native libsumo replay: 14400 s, 0.1 s steps; no cached measurement reuse.', flush=True)
    evaluation = out / 'evaluation'
    result = measure_hamburg_od(prepared_dir=prepared, values=vector['values'], output_dir=evaluation,
        sumo_binary=Path(sumo.__file__).parent / 'bin/sumo.exe', backend='libsumo')
    assert result == json.loads(gzip.decompress(required[1].read_bytes()))
    groups = [audit_expected_to_e1_strict(read_csv_rows(prepared / name), evaluation / 'native/field-counts.xml',
        count_attribute='nVehContrib') for name in ('expected-counts.csv', 'initialization-counts.csv')]
    assert [len(rows) for rows in groups] == [130, 52]
    rows = groups[0] + groups[1]
    assert all(row['measured_nVehContrib'] is not None for row in rows)
    write_csv(evaluation / 'all182-comparison.csv', rows, list(rows[0]))
    write_csv(evaluation / 'initialization52-comparison.csv', groups[1], list(groups[1][0]))
    for path, target, size in ((required[2], 'all182-comparison.csv', 182), (required[3], 'station-comparison.csv', 40)):
        expected = list(csv.DictReader(io.StringIO(gzip.decompress(path.read_bytes()).decode('utf-8'))))
        actual = read_csv_rows(evaluation / target)
        assert len(actual) == size and actual == expected, target
    native = json.loads((evaluation / 'native/run.json').read_bytes())
    assert native['native_status'] == 'pass' and native['stall_monitor']['status'] == 'completed'
    report = {'status': 'pass', 'case': args.case, 'formal_result': False, 'new_native_run': True,
        'manifest_sha256': hashlib.sha256((case / 'manifest.json').read_bytes()).hexdigest(),
        'frozen_measurement_equality': True, 'field_rows': 130, 'station_rows': 40, 'initialization_rows': 52,
        'completion': result['completion'], 'healthy': result['healthy'], 'measurement': result}
    (out / 'reproduction.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
