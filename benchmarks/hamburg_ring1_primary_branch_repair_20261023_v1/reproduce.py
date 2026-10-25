"""Rebuild the frozen candidate and replay five vehicles, or explicitly the full demand."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path, PureWindowsPath
import xml.etree.ElementTree as ET

import sumolib

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.hamburg_primary_branch_repair import prepare_hamburg_primary_branch


def reproduce(output, full=False):
    case = Path(__file__).resolve().parent
    manifest = json.loads((case / 'manifest.json').read_bytes())
    for row in manifest['files']:
        data = (case / row['path']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == row['sha256'], row['path']
        if row['path'].endswith('.gz'):
            assert hashlib.sha256(gzip.decompress(data)).hexdigest() == row['raw_sha256'], row['path']
    out = Path(output).resolve()
    if out.exists() or case.is_relative_to(out) or out.is_relative_to(case):
        raise ValueError('Use a new output directory outside the frozen benchmark.')
    out.mkdir(parents=True)

    def extract(folder, target):
        target.mkdir(parents=True, exist_ok=False)
        for path in (case / folder).glob('*.gz'):
            (target / path.stem).write_bytes(gzip.decompress(path.read_bytes()))

    extract('parent-prepared', out / 'paired-prepared')
    extract('parent-evaluation', out / 'paired-evaluation')
    repository = out / 'source-repository'
    source = repository / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1/inputs'
    source.mkdir(parents=True)
    for name in ('network.net.xml', 'control.add.xml'):
        (source / name).write_bytes(gzip.decompress((case / 'source' / (name + '.gz')).read_bytes()))
    selection = repository / 'benchmarks/hamburg_ring1_munich_corridor_20261022_v1'
    selection.mkdir()
    (selection / 'selection.json').write_bytes(gzip.decompress((case / 'source/selection.json.gz').read_bytes()))
    result = prepare_hamburg_primary_branch(repository=repository, prepared_dir=out / 'paired-prepared',
        evaluation_dir=out / 'paired-evaluation', output_dir=out / 'candidate',
        netconvert_binary=sumolib.checkBinary('netconvert'), sumo_binary=sumolib.checkBinary('sumo'))
    assert result['status'] == 'prepared'
    expected = json.loads(gzip.decompress((case / 'repair-proposal.json.gz').read_bytes()))['candidate_hashes']
    assert result['candidate_hashes'] == expected, 'Rebuilt candidate bytes differ from the frozen candidate.'
    probe = out / 'sparse-probe'
    probe.mkdir()
    route = probe / 'five-vehicles.rou.xml'
    route.write_bytes(gzip.decompress((case / 'sparse-probe/five-vehicles.rou.xml.gz').read_bytes()))
    original = json.loads(gzip.decompress((case / 'sparse-probe/command.json.gz').read_bytes()))['command']
    mapping = {'network.net.xml': out/'candidate/prepared/network.net.xml',
               'control.add.xml': out/'candidate/prepared/control.add.xml', 'five-vehicles.rou.xml': route}
    command = [sumolib.checkBinary('sumo')]
    for value in original[1:]:
        if ':\\' in value:
            name = PureWindowsPath(value).name
            command.append(str(mapping.get(name, probe / name)))
        else:
            command.append(value)
    native = run_command(command, cwd=probe, timeout_seconds=60)
    write_json_atomic(probe / 'command.json', native.to_dict())
    assert native.status == 'pass' and native.returncode == 0
    trips = ET.parse(probe/'tripinfo.xml').getroot().findall('tripinfo')
    final = ET.parse(probe/'summary.xml').getroot()[-1]
    assert len(trips) == 5 and all(float(t.get('arrival')) >= 0 for t in trips)
    assert int(final.get('arrived')) == 5
    assert not any(int(final.get(k)) for k in ('running', 'waiting', 'collisions', 'teleports', 'discarded'))
    assert not ET.parse(probe/'collisions.xml').getroot().findall('collision')
    routes = ET.parse(probe/'vehicle-routes.xml').getroot().findall('vehicle')
    assert len(routes) == 5
    for vehicle in routes:
        record = vehicle.find('route')
        edges, times = record.get('edges').split(), list(map(float, record.get('exitTimes').split()))
        assert all(times[edges.index(e)] >= 0 for e in ('1108564798#0', '1108564798#1', '24483197'))
    seen = set()
    for _, vehicle in ET.iterparse(probe/'fcd.xml', events=('end',)):
        if vehicle.tag == 'vehicle' and vehicle.get('id', '').startswith('primary_lane_'):
            seen.add(vehicle.get('lane'))
        vehicle.clear()
    assert {'1108564798#0_1', '1108564798#0_2'} <= seen
    record = {'status': 'pass', 'candidate_hashes_identical': True, 'sparse_vehicles_arrived': 5,
              'full_demand_run': False, 'formal_result': False, 'summary': final.attrib}
    if full:
        from astra_sumo.core.detector_demand_replay import run_point_detector_demand
        from astra_sumo.core.hamburg_od_spsa import _body_hold_nodes, summarize_hamburg_od_measurement

        extract('prepared', out/'frozen-prepared')
        prepared = out/'frozen-prepared'
        evaluation = out/'evaluation'
        evaluation.mkdir()
        for name in ('demand.rou.xml', 'lane-intentions.json'):
            (evaluation/name).write_bytes((prepared/name).read_bytes())
        (evaluation/'od-vector.json').write_bytes(gzip.decompress((case/'evaluation/od-vector.json.gz').read_bytes()))
        native = run_point_detector_demand(net_file=prepared/'network.net.xml',
            route_file=prepared/'demand.rou.xml', e1_additional_file=prepared/'field-loops.add.xml',
            expected_counts_csv=prepared/'expected-counts.csv', output_dir=evaluation/'native',
            end_s=14400, timeout_seconds=4800, sumo_binary=sumolib.checkBinary('sumo'),
            signal_program_file=prepared/'control.add.xml', lane_intentions_file=prepared/'lane-intentions.json',
            junction_body_hold=True, junction_body_hold_nodes=_body_hold_nodes(prepared), seed=104, backend='libsumo')
        measured = summarize_hamburg_od_measurement(prepared_dir=prepared, proposal_dir=evaluation, native=native)
        counts = {(v.get('id'), float(v.get('begin')), float(v.get('end'))): int(v.get('nVehContrib'))
                  for v in ET.parse(evaluation/'native/field-counts.xml').getroot()}
        rows = []
        for name in ('initialization-counts.csv', 'expected-counts.csv'):
            for row in csv.DictReader((prepared/name).open(encoding='utf-8', newline='')):
                value = counts[row['detector_id'], float(row['begin']), float(row['end'])]
                rows.append({**row, 'measured_nVehContrib': value, 'residual': value-int(row['expected_total'])})
        with (evaluation/'all182-comparison.csv').open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        frozen = json.loads(gzip.decompress((case/'evaluation/evaluation.json.gz').read_bytes()))
        record.update(full_demand_run=True, measured=measured, frozen_result_identical=measured == frozen,
                      all182_TAE=sum(abs(row['residual']) for row in rows))
        assert measured == frozen, 'Full result differs. Inspect native outputs before accepting it.'
    write_json_atomic(out/'reproduction-result.json', record)
    print(json.dumps(record))
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output_dir', type=Path)
    parser.add_argument('--full', action='store_true', help='Run the full 8640-request, 14400-second measurement.')
    args = parser.parse_args()
    reproduce(args.output_dir, full=args.full)
