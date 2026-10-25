"""Retain unchanged-traffic diagnosis and the rejected nominal-control prefix."""

import gzip
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_119_control_diagnosis_20261022_v1'
CASE.mkdir(exist_ok=False)
for label, source in (
    ('diagnostic-inputs', ROOT / 'outputs/ring1-119-green-observation-inputs-20261022-v1'),
    ('diagnostic-native', ROOT / 'outputs/ring1-119-green-observation-native-20261022-v1'),
    ('diagnostic-analysis', ROOT / 'outputs/ring1-119-green-observation-analysis-20261022-v1'),
    ('nominal-inputs', ROOT / 'outputs/ring1-nominal-osm-control-inputs-20261022-v1'),
    ('nominal-native', ROOT / 'outputs/ring1-nominal-osm-control-prefix-native-20261022-v1'),
    ('nominal-analysis', ROOT / 'outputs/ring1-nominal-osm-control-prefix-analysis-20261022-v1'),
):
    for path in source.rglob('*'):
        if not path.is_file():
            continue
        target = CASE / label / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in {'.xml', '.json', '.csv'} and path.stat().st_size > 200000 and not (label.endswith('inputs') and path.name == 'network.net.xml'):
            target = target.with_name(target.name + '.gz')
            target.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
            assert gzip.decompress(target.read_bytes()) == path.read_bytes()
        else:
            shutil.copyfile(path, target)
for stem in ('observe-119-green-20261022-v1', 'probe-119-green-20261022-v1', 'analyze-119-green-20261022-v1',
             'observe-nominal-osm-installation-20261022-v1', 'probe-nominal-osm-prefix-20261022-v1',
             'analyze-nominal-osm-prefix-20261022-v1', 'freeze-119-control-diagnosis-20261022-v1'):
    for suffix in ('py', 'log'):
        path = ROOT / 'outputs' / f'{stem}.{suffix}'
        if path.is_file():
            shutil.copyfile(path, CASE / path.name)
rejected = CASE / 'rejected-unrun-prototype'
rejected.mkdir()
shutil.copyfile(ROOT / 'outputs/rejected-waypoint-retirement-unrun-20261022-v1.patch', rejected / 'retirement.patch')
shutil.copyfile(ROOT / 'outputs/waypoint-retirement-preflight-tests-20261022-v1.log', rejected / 'prototype-tests.log')
shutil.copyfile(ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1/.gitattributes', CASE / '.gitattributes')
(CASE / 'decision.json').write_text(json.dumps({'formal_result': False, 'decision': 'nominal_all_osm_not_promoted',
    'full_reference': '../hamburg_ring1_primary_entry_full_20261022_v1', 'new_full_window_runs': 0,
    'read_only_diagnosis_end_s': 3780, 'nominal_control_trial_end_s': 3600,
    'observation': '119 K2 can be green while serial downstream controls hold vehicles. The sampled field-lane head has no remaining waypoint.',
    'nominal_trial': 'Fixing all 58 nominal OSM sequences worsens the initialization result. No main-window fit is established.',
    'rejected_unrun_prototype': 'Positive waypoint speed is an upper bound, not a minimum. The prototype was reverted before any native run.',
    'claim_boundary': 'The observations do not prove physical infeasibility, a deadlock cycle, source-controller duplication or correct field timing.'}, indent=2) + '\n', encoding='utf-8', newline='\n')
print(CASE)
