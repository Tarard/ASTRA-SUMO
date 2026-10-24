"""Rebuild the explicit LSA119 candidate and run the paired two-vehicle diagnostic."""

import argparse
import gzip
import json
from pathlib import Path
import subprocess
import sys

import sumo

from astra_sumo.core.junction_straight_geometry import materialize_ordered_straights


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    out = parser.parse_args().output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[2]
    source = out / 'source.net.xml'
    source.write_bytes(gzip.decompress((repo / 'benchmarks/hamburg_ring1_104_split_fanout_20261023_v1/candidate.net.xml.gz').read_bytes()))
    candidate = materialize_ordered_straights(source_file=source, junction_id='LSA119_part0',
        connection_keys=[('199230909#0', 2, '486864294', 0), ('199230909#0', 3, '486864294', 1)],
        output_dir=out / 'candidate', netconvert_binary=Path(sumo.__file__).parent / 'bin/netconvert.exe')
    for label, network, collisions in [('before', source, 1), ('after', Path(candidate['candidate_file']), 0)]:
        subprocess.run([sys.executable, str(Path(__file__).with_name('stopped_probe.py')), '--network', str(network),
                        '--output-dir', str(out / label)], check=True, timeout=30)
        result = json.loads((out / label / 'result.json').read_bytes())
        assert len(result['native_collisions']) == collisions
        assert int(result['final_summary']['teleports']) == collisions
        assert int(result['final_summary']['arrived']) == 2
    print('Matched two-vehicle failure and repair. Full-population validity remains a separate check.')


if __name__ == '__main__':
    main()
