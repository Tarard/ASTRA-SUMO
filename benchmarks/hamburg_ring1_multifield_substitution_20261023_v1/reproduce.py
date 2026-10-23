"""Explicitly run one saved candidate; never rebuild the OD model."""
import argparse
import gzip
from importlib.metadata import version
from importlib.util import find_spec
import json
from pathlib import Path
import runpy
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, choices=('substitute-1pair', 'substitute-20pair'))
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    case, out = Path(__file__).resolve().parent, args.output_dir.resolve()
    audit = runpy.run_path(str(case / 'freeze_evidence.py'))
    audit['verify'](case)
    repo, raw, load = case.parents[1], audit['raw'], audit['load']
    assert Path(sys.prefix).resolve() == (repo / '.venv').resolve()
    assert {version(n) for n in ('eclipse-sumo', 'libsumo', 'traci', 'sumolib')} == {'1.27.1'}
    assert not out.exists() and not out.is_relative_to(case) and not case.is_relative_to(out)
    source = case / args.case
    monitor = load(source / 'evaluation/native/stall-monitor.json')
    library = Path(find_spec('libsumo').origin).parent / '_libsumo.pyd'
    assert audit['sha'](library) == monitor['native_library']['sha256']
    for snapshot in (source / 'implementation').glob('*.py.gz'):
        assert audit['sha'](repo / 'plugins/astra-sumo/src/astra_sumo/core' / snapshot.stem) == audit['raw_sha'](snapshot.with_suffix(''))
    prepared, evaluation = out / 'prepared', out / 'evaluation'
    prepared.mkdir(parents=True)
    evaluation.mkdir()
    for p in (source / 'prepared').glob('*.gz'):
        (prepared / p.stem).write_bytes(gzip.decompress(p.read_bytes()))
    for n in ('demand.rou.xml', 'lane-intentions.json'):
        (evaluation / n).write_bytes((prepared / n).read_bytes())
    sys.path.insert(0, str(repo / 'plugins/astra-sumo/src'))
    import sumo
    from astra_sumo.core.detector_demand import audit_expected_to_e1_strict, read_csv_rows, write_csv
    from astra_sumo.core.detector_demand_replay import run_point_detector_demand
    from astra_sumo.core.hamburg_od_spsa import _body_hold_nodes, summarize_hamburg_od_measurement
    from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages

    print('Starting one complete explicit native libsumo replay, 14400 s.', flush=True)
    native = run_point_detector_demand(net_file=prepared / 'network.net.xml', route_file=prepared / 'demand.rou.xml',
        e1_additional_file=prepared / 'field-loops.add.xml', expected_counts_csv=prepared / 'expected-counts.csv',
        output_dir=evaluation / 'native', end_s=14400, timeout_seconds=4800, seed=104, backend='libsumo',
        sumo_binary=str(Path(sumo.__file__).parent / 'bin/sumo.exe'), signal_program_file=prepared / 'control.add.xml',
        lane_intentions_file=prepared / 'lane-intentions.json', junction_body_hold=True, junction_body_hold_nodes=_body_hold_nodes(prepared))
    measured = summarize_hamburg_od_measurement(prepared_dir=prepared, proposal_dir=evaluation, native=native)
    assert measured == load(source / 'evaluation/evaluation.json')
    full = audit_expected_to_e1_strict(read_csv_rows(prepared / 'initialization-counts.csv') + read_csv_rows(prepared / 'expected-counts.csv'), evaluation / 'native/field-counts.xml')
    assert len(full) == 182 and all(r['measured_nVehContrib'] is not None for r in full)
    write_csv(evaluation / 'all182-comparison.csv', full, list(full[0]))
    assert read_csv_rows(evaluation / 'all182-comparison.csv') == audit['rows'](source / 'evaluation/all182-comparison.csv')
    passages = read_contributing_e1_passages(instant_file=evaluation / 'native/instant-e1.xml', periodic_file=evaluation / 'native/field-counts.xml',
        detector_ids=native['instant_to_periodic_ids'], step_length=.1)
    original = json.loads(gzip.decompress(raw(source / 'evaluation/contributing-passages.json.gz')))
    assert passages['reconciled'] and passages['passages'] == original['passages']
    (evaluation / 'contributing-passages.json.gz').write_bytes(gzip.compress(json.dumps(passages).encode('utf-8'), mtime=0))
    report, _, _, _ = audit['audit_measurement'](prepared, evaluation, load(source / 'preflight.json')['signatures'])
    assert all(value == load(case / 'results.json')['evaluations'][args.case][key] for key, value in report.items())
    audit['save'](out / 'reproduction.json', dict(status='pass', new_native_run=True, formal_result=False, case=args.case, measurement=report, complete_passages_identical=True))
    print('Native 208/130/40/52/182, completion and complete passage equality: pass', flush=True)


if __name__ == '__main__':
    main()
