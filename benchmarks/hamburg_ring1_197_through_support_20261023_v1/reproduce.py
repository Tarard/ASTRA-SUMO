"""Explicitly materialize v5 with existing core and measure its fixed selected demand."""
import argparse
import gzip
from importlib.metadata import version
from importlib.util import find_spec
import json
from pathlib import Path
import runpy
import shutil
import sys
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    case, out = Path(__file__).resolve().parent, args.output_dir.resolve()
    audit = runpy.run_path(str(case / 'freeze_evidence.py'))
    audit['verify'](case)
    repo, load, raw = case.parents[1], audit['load'], audit['raw']
    assert Path(sys.prefix).resolve() == (repo / '.venv').resolve()
    assert not out.exists() and not out.is_relative_to(case) and not case.is_relative_to(out)
    runtime = load(case / 'runtime.json')
    assert all(version(n) == v for n, v in runtime['versions'].items())
    package = repo / 'plugins/astra-sumo/src/astra_sumo'
    for name, expected in runtime['implementation_sha256'].items():
        assert audit['sha'](package / name) == expected, 'Use the frozen source implementation: '+name
    library = Path(find_spec('libsumo').origin).parent / '_libsumo.pyd'
    assert audit['sha'](library) == load(case / 'full/evaluation/native/stall-monitor.json')['native_library']['sha256']
    import sumo
    binary = Path(sumo.__file__).parent / 'bin'
    assert audit['sha'](binary / 'netconvert.exe') == runtime['netconvert_sha256']
    source = out / 'source'
    source.mkdir(parents=True)
    for name in ('network.net.xml', 'control.add.xml'):
        (source / name).write_bytes(raw(case / 'source-input' / name))
    sys.path.insert(0, str(repo / 'plugins/astra-sumo/src'))
    from astra_sumo.core.hamburg_197_through_support import materialize_hamburg_197_through_support
    print('Starting two native scoped network conversions, followed by one complete fixed-demand measurement.', flush=True)
    candidate = out / 'materialization'
    proof = materialize_hamburg_197_through_support(source_file=source / 'network.net.xml', control_file=source / 'control.add.xml',
        output_dir=candidate, netconvert_binary=str(binary / 'netconvert.exe'))
    assert proof['status'] == 'review_required' and all(proof['checks'].values())
    frozen = load(case / 'candidate-v5/manifest.json')
    assert proof['candidate_sha256'] == frozen['candidate_sha256'] and proof['control_sha256'] == frozen['control_sha256']
    prepared, evaluation = out / 'prepared', out / 'evaluation'
    prepared.mkdir()
    evaluation.mkdir()
    for p in (case / 'full/prepared').glob('*.gz'):
        (prepared / p.stem).write_bytes(gzip.decompress(p.read_bytes()))
    for name in ('network.net.xml', 'control.add.xml'):
        shutil.copyfile(candidate / name, prepared / name)
    for name in ('demand.rou.xml', 'lane-intentions.json'):
        shutil.copyfile(prepared / name, evaluation / name)
    from astra_sumo.core.detector_demand_replay import run_point_detector_demand
    from astra_sumo.core.detector_demand import audit_expected_to_e1_strict, read_csv_rows, write_csv
    from astra_sumo.core.hamburg_od_spsa import _body_hold_nodes, summarize_hamburg_od_measurement
    from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages

    native = run_point_detector_demand(net_file=prepared / 'network.net.xml', route_file=prepared / 'demand.rou.xml',
        e1_additional_file=prepared / 'field-loops.add.xml', expected_counts_csv=prepared / 'expected-counts.csv', output_dir=evaluation / 'native',
        end_s=14400, timeout_seconds=4800, step_length=.1, seed=104, backend='libsumo', sumo_binary=str(binary / 'sumo.exe'),
        signal_program_file=prepared / 'control.add.xml', lane_intentions_file=prepared / 'lane-intentions.json', junction_body_hold=True,
        junction_body_hold_nodes=_body_hold_nodes(prepared))
    measured = summarize_hamburg_od_measurement(prepared_dir=prepared, proposal_dir=evaluation, native=native)
    assert measured == load(case / 'full/evaluation/evaluation.json')
    comparison = audit_expected_to_e1_strict(read_csv_rows(prepared / 'initialization-counts.csv') + read_csv_rows(prepared / 'expected-counts.csv'), evaluation / 'native/field-counts.xml')
    write_csv(evaluation / 'all182-comparison.csv', comparison, list(comparison[0]))
    assert read_csv_rows(evaluation / 'all182-comparison.csv') == audit['rows'](case / 'full/evaluation/all182-comparison.csv')
    passages = read_contributing_e1_passages(instant_file=evaluation / 'native/instant-e1.xml', periodic_file=evaluation / 'native/field-counts.xml',
        detector_ids=native['instant_to_periodic_ids'], step_length=.1)
    original = json.loads(gzip.decompress(raw(case / 'full/evaluation/contributing-passages.json.gz')))
    assert passages['reconciled'] and passages['passages'] == original['passages']
    (evaluation / 'contributing-passages.json.gz').write_bytes(gzip.compress(json.dumps(passages).encode('utf-8'), mtime=0))
    signatures = {r['vehicle_id']: [r['field_id']] for r in load(prepared / 'lane-intentions.json')['vehicles']}
    actual, _, _, _ = audit['audit_measurement'](prepared, evaluation, signatures)
    assert all(actual[k] == v for k, v in load(case / 'results.json')['full'].items() if k in actual)
    def intervals(path):
        return [r.attrib for r in ET.fromstring(raw(path)).findall('interval')]
    assert intervals(evaluation / 'native/field-counts.xml') == intervals(case / 'full/evaluation/native/field-counts.xml')
    audit['save'](out / 'reproduction.json', dict(status='pass', formal_result=False, new_native_measurement=True,
        materialization_candidate_hash_equal=True, all208_attributes_equal=True, full_passages_equal=True, measurement=actual))
    print('Core materialization and raw208/130/40/52/182, completion and full passage equality: pass', flush=True)


if __name__ == '__main__':
    main()
