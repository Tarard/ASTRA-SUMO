"""Rebuild and evaluate the fixed repaired Hamburg baseline with Libsumo."""
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
    assert all(version(name) == '1.27.1' for name in ('eclipse-sumo', 'traci', 'sumolib', 'libsumo'))
    source = repo / 'benchmarks/hamburg_ring1_119_straight_order_20261023_v1'
    model = prepare_hamburg_od_baseline(case_dir=repo / 'benchmarks/hamburg_ring1_munich_corridor_20261022_v1',
        audit_dir=repo / 'benchmarks/hamburg_ring1_spsa_od_definition_20261022_v1', output_dir=out / 'prepared')
    network = gzip.decompress((source / 'candidate.net.xml.gz').read_bytes())
    (out / 'prepared/network.net.xml').write_bytes(network)
    model['source_network_sha256'] = hashlib.sha256(network).hexdigest()
    (out / 'prepared/od-model.json').write_bytes((json.dumps(model, indent=2) + '\n').encode())
    (out / 'prepared/scenario.json').write_bytes(gzip.decompress((source / 'scenario.json.gz').read_bytes()))
    result = measure_hamburg_od(prepared_dir=out / 'prepared',
        values=json.loads((source / 'full-native/od-vector.json').read_bytes())['values'],
        output_dir=out / 'evaluation', sumo_binary=Path(sumo.__file__).parent / 'bin/sumo.exe', backend='libsumo')
    assert result == json.loads((source / 'full-native/evaluation.json').read_bytes())
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
