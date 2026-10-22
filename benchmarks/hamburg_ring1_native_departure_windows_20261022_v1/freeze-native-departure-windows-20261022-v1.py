"""Retain a failed bounded timing trial and its native-data checks."""

import gzip
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_native_departure_windows_20261022_v1'
CASE.mkdir(exist_ok=False)
for label, source in (
    ('timing-windows', ROOT / 'outputs/ring1-native-departure-windows-20261022-v2'),
    ('inputs', ROOT / 'outputs/ring1-native-time-correction-inputs-20261022-v2'),
    ('native', ROOT / 'outputs/ring1-native-time-correction-prefix-20261022-v1'),
    ('analysis', ROOT / 'outputs/ring1-native-time-correction-prefix-analysis-20261022-v1'),
):
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
for name in ('analyze-native-departure-windows-20261022-v2.py', 'probe-native-time-correction-20261022-v2.py',
             'analyze-native-time-correction-prefix-20261022-v1.py', 'freeze-native-departure-windows-20261022-v1.py',
             'analyze-native-departure-windows-20261022-v2.log', 'probe-native-time-correction-20261022-v2.log',
             'analyze-native-time-correction-prefix-20261022-v1.log'):
    shutil.copyfile(ROOT / 'outputs' / name, CASE / name)
preflight = CASE / 'preflight-rejected'
preflight.mkdir()
for suffix in ('py', 'log'):
    shutil.copyfile(ROOT / f'outputs/probe-native-time-correction-20261022-v1.{suffix}', preflight / f'probe-native-time-correction-20261022-v1.{suffix}')
shutil.copyfile(ROOT / 'outputs/ring1-native-time-correction-inputs-20261022-v1/time-correction-proposal.json', preflight / 'unrun-proposal.json')
shutil.copyfile(ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1/.gitattributes', CASE / '.gitattributes')
(CASE / 'decision.json').write_text(json.dumps({'formal_result': False, 'decision': 'not_promoted',
    'new_initialization_trials': 1, 'new_full_window_runs': 0,
    'reference_case': '../hamburg_ring1_primary_entry_full_20261022_v1',
    'reason': 'The bounded frozen-lag request advances reduce native initialization E1 contributions and worsen mean absolute error.',
    'stop_for_this_rule': True,
    'claim_boundary': 'Reject this timing rule, not every possible schedule. Unknown passages and joint target conflicts do not prove physical infeasibility.'}, indent=2) + '\n', encoding='utf-8', newline='\n')
print(CASE)
