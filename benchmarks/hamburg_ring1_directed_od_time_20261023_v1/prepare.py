"""Reconstruct the single-OD candidate from frozen native identities without rerunning SUMO."""

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

from astra_sumo.core.hamburg_od_spsa import prepare_hamburg_od_baseline, write_hamburg_od_demand
from astra_sumo.core.native_temporal_feedback import native_temporal_feedback
from astra_sumo.core.od_time_redistribution import propose_od_time_redistribution


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    out = parser.parse_args().output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    case = Path(__file__).resolve().parent
    repo = case.parents[1]
    reference = repo / 'benchmarks/hamburg_ring1_119_straight_order_20261023_v1'
    native = reference / 'full-native'
    def raw(name):
        return gzip.decompress((native / (name + '.gz')).read_bytes())
    intentions = {r['vehicle_id']: r for r in json.loads(raw('lane-intentions.json'))['vehicles']}
    vehicles = ET.fromstring(raw('demand.rou.xml')).findall('vehicle')
    requests = [{'vehicle_id': v.get('id'), 'plan_id': str(intentions[v.get('id')]['od_cell']),
                 'requested_depart_s': float(v.get('depart'))} for v in vehicles]
    trips = [{'vehicle_id': t.get('id'), 'actual_depart_s': float(t.get('depart'))}
             for t in ET.fromstring(raw('tripinfo.xml')).findall('tripinfo') if float(t.get('depart')) >= 0]
    identities = json.loads(gzip.decompress((case / 'native-identity-reconciliation.json.gz').read_bytes()))
    assert identities['reconciled'] and identities['measurement_attribute'] == 'nVehContrib'
    fields = list(csv.DictReader((native / 'field-comparison.csv').open(encoding='utf-8')))
    assert len(fields) == 130
    response = native_temporal_feedback(requests=requests, trips=trips, passages=identities['passages'], measurements=fields)
    frozen = np.load(case / 'response.npz')
    assert np.array_equal(response['numerators'], frozen['numerators'])
    assert np.array_equal(response['denominators'], frozen['denominators'])
    prepared = out / 'prepared'
    model = prepare_hamburg_od_baseline(case_dir=repo / 'benchmarks/hamburg_ring1_munich_corridor_20261022_v1',
        audit_dir=repo / 'benchmarks/hamburg_ring1_spsa_od_definition_20261022_v1', output_dir=prepared)
    network = gzip.decompress((reference / 'candidate.net.xml.gz').read_bytes())
    (prepared / 'network.net.xml').write_bytes(network)
    model['source_network_sha256'] = hashlib.sha256(network).hexdigest()
    (prepared / 'od-model.json').write_text(json.dumps(model, indent=2) + '\n', encoding='utf-8')
    (prepared / 'scenario.json').write_bytes(gzip.decompress((reference / 'scenario.json.gz').read_bytes()))
    indices = [int(c['plan_id']) for c in response['columns']]
    keys = [f"{model['cells'][i]['origin']}->{model['cells'][i]['destination']}" for i in indices]
    original = json.loads((case / 'proposal.json').read_bytes())
    result = propose_od_time_redistribution(response=response['fractions'], counts=response['denominators'],
        measured=response['measured_counts'], targets=[int(r['expected_total']) for r in fields], od_ids=keys,
        max_per_cell=original['max_per_cell'], max_moved=original['max_moved'])
    assert result['counts'] == original['counts'] and result['od_id'] == original['od_id']
    values = json.loads((native / 'od-vector.json').read_bytes())['integer_counts']
    for i, count in zip(indices, result['counts']):
        values[i] = count
    assert values == original['values'] and sum(values) == 8603
    result['values'] = values
    (out / 'proposal.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    generated = write_hamburg_od_demand(model_file=prepared / 'od-model.json', values=values, output_dir=out / 'preflight')
    print(json.dumps({'prepared_dir': str(prepared), 'proposal': str(out / 'proposal.json'),
        'request_count': 8603, 'demand_sha256': hashlib.sha256((generated / 'demand.rou.xml').read_bytes()).hexdigest()}, indent=2))


if __name__ == '__main__':
    main()
