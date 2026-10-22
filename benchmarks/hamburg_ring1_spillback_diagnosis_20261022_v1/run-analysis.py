"""Reconstruct the recorded inputs and analyze queues without running SUMO."""

import argparse
import gzip
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import sys

from astra_sumo.core.hamburg_spillback import analyze_hamburg_spillback, plot_hamburg_spillback


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    assert Path(sys.executable).resolve() == (root / '.venv/Scripts/python.exe').resolve()
    assert all(version(name) == '1.27.1' for name in ('eclipse-sumo', 'traci', 'sumolib'))
    here = Path(__file__).parent
    reference = root / 'benchmarks/hamburg_ring1_munich_corridor_20261022_v1'
    source = args.output_dir.with_name(args.output_dir.name + '-source')
    source.mkdir(parents=True, exist_ok=False)
    for folder in ('inputs', 'native', 'report'):
        (source / folder).mkdir()
    copies = [(reference / 'inputs' / (name + '.gz'), source / 'inputs' / name)
        for name in ('network.net.xml', 'demand.rou.xml', 'field-loops.add.xml', 'control.add.xml', 'lane-intentions.json')]
    copies += [(reference / 'native' / (name + '.gz'), source / 'native' / name)
        for name in ('run.json', 'collisions.xml', 'field-counts.xml', 'instant-e1.xml')]
    copies += [(reference / 'seed/passage-reconciliation.json.gz', source / 'report/passage-reconciliation.json'),
               (here / 'source-queues.xml.gz', source / 'native/queues.xml')]
    for origin, destination in copies:
        with gzip.open(origin, 'rb') as src, destination.open('wb') as dest:
            shutil.copyfileobj(src, dest)
    shutil.copyfile(here / 'source-field-comparison.csv', source / 'native/field-comparison.csv')
    inventory = json.loads((reference / 'raw-native-inventory.json').read_bytes())['files']
    expected = next(row['sha256'] for row in inventory if row['path'] == 'queues.xml')
    assert hashlib.file_digest((source / 'native/queues.xml').open('rb'), 'sha256').hexdigest() == expected
    result = analyze_hamburg_spillback(case_dir=source, output_dir=args.output_dir)
    plot_hamburg_spillback(args.output_dir)
    print(json.dumps({'new_simulations': result['new_simulations'], 'field_rows': result['field_rows'],
                      'count_target_rows': result['count_target_rows'], 'sources_unchanged': result['sources_unchanged']}))
