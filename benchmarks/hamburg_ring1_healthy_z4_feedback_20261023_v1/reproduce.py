"""Explicitly replay the frozen explicit Z4 case without generating demand or changing control."""
import argparse
import gzip
from importlib.metadata import version
from importlib.util import find_spec
import json
from pathlib import Path
import runpy
import sys
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=('batch-1','batch-4'), required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    case, out = Path(__file__).resolve().parent, args.output_dir.resolve()
    frozen = runpy.run_path(str(case / 'freeze_evidence.py'))
    frozen['verify'](case)
    repo, raw, load = case.parents[1], frozen['raw'], frozen['load']
    assert Path(sys.prefix).resolve() == (repo / '.venv').resolve()
    assert all(version(n) == '1.27.1' for n in ('eclipse-sumo', 'libsumo', 'traci', 'sumolib'))
    assert not out.exists() and not out.is_relative_to(case) and not case.is_relative_to(out)
    source = case / args.case
    assert 'measurement' in load(case / 'results.json')['audit']['cases'][args.case], 'The frozen invalid case has no complete expected measurement.'
    for name in load(case / 'freeze-whitelist.json')['implementation_names']:
        assert frozen['sha'](repo / 'plugins/astra-sumo/src/astra_sumo/core' / name) == frozen['raw_sha'](source / 'implementation' / name)
    library = Path(find_spec('libsumo').origin).parent / '_libsumo.pyd'
    assert frozen['sha'](library) == load(source / 'evaluation/native/stall-monitor.json')['native_library']['sha256']
    prepared, evaluation = out / 'prepared', out / 'evaluation'
    prepared.mkdir(parents=True)
    evaluation.mkdir()
    for name in load(case / 'freeze-whitelist.json')['prepared_names']:
        (prepared / name).write_bytes(raw(source / 'prepared' / name))
    for name in ('demand.rou.xml', 'lane-intentions.json'):
        (evaluation / name).write_bytes(raw(prepared / name))
    sys.path.insert(0, str(repo / 'plugins/astra-sumo/src'))
    from astra_sumo.core.detector_demand_replay import run_point_detector_demand
    from astra_sumo.core.detector_demand import audit_expected_to_e1_strict, read_csv_rows, write_csv
    from astra_sumo.core.hamburg_od_spsa import _body_hold_nodes, summarize_hamburg_od_measurement
    from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages

    print('Starting one fixed8640 priority-native measurement; no OD or candidate change.', flush=True)
    native = run_point_detector_demand(net_file=prepared / 'network.net.xml', route_file=prepared / 'demand.rou.xml',
        e1_additional_file=prepared / 'field-loops.add.xml', expected_counts_csv=prepared / 'expected-counts.csv',
        output_dir=evaluation / 'native', end_s=14400, timeout_seconds=4800, step_length=.1, seed=104, backend='libsumo',
        sumo_binary=str(repo / '.venv/Lib/site-packages/sumo/bin/sumo.exe'), signal_program_file=prepared / 'control.add.xml',
        lane_intentions_file=prepared / 'lane-intentions.json', junction_body_hold=True, junction_body_hold_nodes=_body_hold_nodes(prepared))
    measured = summarize_hamburg_od_measurement(prepared_dir=prepared, proposal_dir=evaluation, native=native)
    assert measured == load(source / 'evaluation/evaluation.json')
    comparison = audit_expected_to_e1_strict(read_csv_rows(prepared / 'initialization-counts.csv') + read_csv_rows(prepared / 'expected-counts.csv'), evaluation / 'native/field-counts.xml')
    write_csv(evaluation / 'all182-comparison.csv', comparison, list(comparison[0]))
    assert read_csv_rows(evaluation / 'all182-comparison.csv') == frozen['rows'](source / 'evaluation/all182-comparison.csv')
    passages = read_contributing_e1_passages(instant_file=evaluation / 'native/instant-e1.xml', periodic_file=evaluation / 'native/field-counts.xml', detector_ids=native['instant_to_periodic_ids'], step_length=.1)
    old = json.loads(gzip.decompress(raw(source / 'evaluation/contributing-passages.json.gz')))
    assert passages['reconciled'] and passages['passages'] == old['passages']
    (evaluation / 'contributing-passages.json.gz').write_bytes(gzip.compress(json.dumps(passages).encode(), mtime=0))
    signatures = {r['vehicle_id']: [r['field_id']] for r in load(prepared / 'lane-intentions.json')['vehicles']}
    report, _, _, _ = frozen['audit_measurement'](prepared, evaluation, signatures)
    assert report == load(case / 'results.json')['audit']['cases'][args.case]['measurement']
    def intervals(path):
        return [dict(r.attrib) for r in ET.fromstring(raw(path)).findall('interval')]
    assert intervals(evaluation / 'native/field-counts.xml') == intervals(source / 'evaluation/native/field-counts.xml')
    frozen['save'](out / 'reproduction.json', {'status': 'pass', 'formal_result': False, 'fixed_input': True, 'native_runs': 1,
        'all208_attributes_equal': True, 'all_full_passages_equal': True, 'measurement': report})
    print('Fixed input, raw208/130/40/52/182, completion and full passage equality: pass', flush=True)


if __name__ == '__main__':
    main()
