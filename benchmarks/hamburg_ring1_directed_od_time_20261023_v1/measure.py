"""Measure the frozen single-OD candidate with unchanged native assumptions."""

import argparse
import gzip
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys

import numpy as np
import sumo
import traci

from astra_sumo.core.hamburg_od_spsa import _body_hold_nodes, measure_hamburg_od


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-dir', type=Path, required=True)
    parser.add_argument('--proposal', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    options = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    reference = repo / 'benchmarks/hamburg_ring1_119_straight_order_20261023_v1'
    native = reference / 'full-native'
    prepared = options.prepared_dir.resolve(strict=True)
    assert Path(sys.executable).resolve() == (repo / '.venv/Scripts/python.exe').resolve()
    assert Path(traci.__file__).resolve().is_relative_to(repo / '.venv')
    assert all(version(name) == '1.27.1' for name in ('eclipse-sumo', 'traci', 'sumolib'))
    execution = json.loads(gzip.decompress((reference / 'full-execution.json.gz').read_bytes()))
    for name, sha in execution['native_driver_sha256'].items():
        assert hashlib.sha256((repo / 'plugins/astra-sumo/src/astra_sumo/core' / name).read_bytes()).hexdigest() == sha, 'Recheck a changed native driver before comparing against the frozen baseline.'
    for path, frozen in [('network.net.xml', reference / 'candidate.net.xml.gz'),
                         ('control.add.xml', native / 'signal-programs.add.xml.gz'),
                         ('field-loops.add.xml', native / 'source-e1.add.xml.gz'),
                         ('expected-counts.csv', native / 'expected-counts.csv.gz')]:
        assert (prepared / path).read_bytes() == gzip.decompress(frozen.read_bytes()), path
    assert _body_hold_nodes(prepared) == ('14398547', '205069138', '18653534', 'LSA104_part0')
    values = np.asarray(json.loads(options.proposal.read_bytes())['values'], dtype=float)
    prior = np.asarray(json.loads((native / 'od-vector.json').read_bytes())['integer_counts'])
    case = Path(__file__).parent
    frozen = [json.loads((case / name).read_bytes()) for name in ('proposal.json', 'unit-step/proposal.json')]
    assert values.shape == prior.shape and np.all(np.isfinite(values)) and np.all(values >= 0)
    assert np.all(values == np.rint(values)) and values.sum() == prior.sum() == 8603
    assert any(values.tolist() == item['values'] for item in frozen)
    result = measure_hamburg_od(prepared_dir=prepared, values=values, output_dir=options.output_dir,
        sumo_binary=Path(sumo.__file__).parent / 'bin/sumo.exe')
    before = json.loads((native / 'evaluation.json').read_bytes())
    state, old_state = result['completion'], before['completion']
    safe = all(state[key] == 0 for key in ('collisions', 'teleports', 'discarded'))
    improved = (safe and result['MAE'] < before['MAE'] and result['max_abs_error'] <= before['max_abs_error']
        and state['waiting'] + state['running'] <= old_state['waiting'] + old_state['running'])
    verdict = {'status': 'diagnostic_improvement' if improved else 'rejected', 'formal_result': False,
               'safe': safe, 'before': before, 'after': result}
    (options.output_dir / 'verdict.json').write_text(json.dumps(verdict, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(verdict, indent=2))


if __name__ == '__main__':
    main()
