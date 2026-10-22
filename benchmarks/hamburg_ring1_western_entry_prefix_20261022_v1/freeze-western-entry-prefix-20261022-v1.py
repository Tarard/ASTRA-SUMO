"""Retain the western-entry prefix without hiding other-field losses."""

import gzip
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_western_entry_prefix_20261022_v1'
assert (CASE / 'route-probe/route-response-check.json').is_file()
for label, source in (
    ('inputs', ROOT / 'outputs/ring1-western-primary-entry-inputs-20261022-v2'),
    ('native', ROOT / 'outputs/ring1-western-primary-entry-prefix-20261022-v1'),
    ('analysis', ROOT / 'outputs/ring1-western-primary-entry-analysis-20261022-v1'),
):
    (CASE / label).mkdir(exist_ok=False)
    for path in source.rglob('*'):
        if not path.is_file():
            continue
        target = CASE / label / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in {'.xml', '.json', '.csv'} and path.stat().st_size > 200000 and not (label == 'inputs' and path.name == 'network.net.xml'):
            target = target.with_name(target.name + '.gz')
            target.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
            assert gzip.decompress(target.read_bytes()) == path.read_bytes()
        else:
            shutil.copyfile(path, target)
for name in ('probe-western-entry-prefix-20261022-v1.py', 'analyze-western-entry-prefix-20261022-v1.py',
             'freeze-western-entry-prefix-20261022-v1.py', 'probe-western-entry-prefix-20261022-v1.log',
             'analyze-western-entry-prefix-20261022-v1.log'):
    shutil.copyfile(ROOT / 'outputs' / name, CASE / name)
(CASE / 'decision.json').write_text(json.dumps({'formal_result': False, 'decision': 'prefix_candidate_with_losses',
    'full_window_effect_established': False, 'complete_reference': '../hamburg_ring1_primary_entry_full_20261022_v1',
    'observed_losses': 'Initialization E1 contributions fall by 46; field MAE rises to 343/52 and maximum error rises to 59.',
    'full_window_evaluation': 'Keep this exact share, inputs and times for one complete-window test. The 427 changed main requests are not evaluated by the initialization prefix.',
    'no_new_time_or_signal_change': True}, indent=2) + '\n', encoding='utf-8', newline='\n')
print(CASE)
