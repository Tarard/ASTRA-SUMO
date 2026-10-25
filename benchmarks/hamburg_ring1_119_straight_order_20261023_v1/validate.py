"""Repeat the complete paired-demand check on the frozen LSA119 candidate."""

import argparse
import gzip
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.hamburg_od_spsa import measure_hamburg_od, prepare_hamburg_od_baseline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    out = parser.parse_args().output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[2]
    assert Path(sys.executable).resolve() == (repo / '.venv/Scripts/python.exe').resolve()
    assert all(version(name) == '1.27.1' for name in ('eclipse-sumo', 'traci', 'sumolib'))
    prepared = out / 'prepared'
    model = prepare_hamburg_od_baseline(case_dir=repo / 'benchmarks/hamburg_ring1_munich_corridor_20261022_v1',
        audit_dir=repo / 'benchmarks/hamburg_ring1_spsa_od_definition_20261022_v1', output_dir=prepared)
    case = Path(__file__).parent
    network = gzip.decompress((case / 'candidate.net.xml.gz').read_bytes())
    (prepared / 'network.net.xml').write_bytes(network)
    model['source_network_sha256'] = hashlib.sha256(network).hexdigest()
    (prepared / 'od-model.json').write_text(json.dumps(model, indent=2) + '\n', encoding='utf-8')
    (prepared / 'scenario.json').write_bytes(gzip.decompress((case / 'scenario.json.gz').read_bytes()))
    vector = json.loads((case / 'full-native/od-vector.json').read_bytes())['values']
    result = measure_hamburg_od(prepared_dir=prepared, values=vector, output_dir=out / 'evaluation',
        sumo_binary=Path(sumo.__file__).parent / 'bin/sumo.exe')
    reference = json.loads((case / 'full-native/evaluation.json').read_bytes())
    assert result == reference, (result, reference)
    for name in ('demand.rou.xml', 'lane-intentions.json', 'source-e1.add.xml', 'signal-programs.add.xml', 'expected-counts.csv'):
        assert (out / 'evaluation/native' / name).read_bytes() == gzip.decompress((case / 'full-native' / (name + '.gz')).read_bytes()), name
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
