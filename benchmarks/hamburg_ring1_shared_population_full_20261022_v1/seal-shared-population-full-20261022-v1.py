"""Freeze the unsuccessful complete shared-population measurement."""

import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_shared_population_full_20261022_v1'
for name in ('README.md', 'workflow-report.md', 'report.html'):
    path = CASE / name
    content = path.read_text(encoding='utf-8')
    for original in ('passage-reconciliation.json', 'vehicle-field-passages.csv'):
        content = re.sub(re.escape(original) + r'(?!\.gz)', original + '.gz', content)
    path.write_text(content, encoding='utf-8', newline='\n')
files = ['plugins/astra-sumo/src/astra_sumo/core/' + name for name in (
    'hamburg_shared_population.py', 'hamburg_readme_results.py', 'lane_section_hold.py',
    'simulation_stall.py', 'detector_demand_replay.py', 'route_sensor_matrix.py')]
files += ['tests/test_hamburg_shared_population.py']
for name in files:
    target = CASE / 'implementation' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
for suffix in ('xml', 'log'):
    shutil.copyfile(ROOT / f'outputs/shared-population-full-tests-20261022-v1.{suffix}', CASE / f'tests.{suffix}')
shutil.copyfile(ROOT / 'outputs/ring1-shared-population-full-prefix-check-20261022-v1.json', CASE / 'analysis/live-prefix-check.json')
for name in ('run-shared-population-full-20261022-v1.log', 'analyze-shared-population-full-20261022-v1.log'):
    shutil.copyfile(ROOT / 'outputs' / name, CASE / name)
shutil.copyfile(Path(__file__), CASE / Path(__file__).name)
for path in CASE.glob('*.py'):
    path.write_text(path.read_text(encoding='utf-8').rstrip() + '\n', encoding='utf-8', newline='\n')
(CASE / 'implementation-sha256.json').write_text(json.dumps({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    for name in files}, indent=2) + '\n', encoding='utf-8', newline='\n')
(CASE / 'decision.json').write_text(json.dumps({
    'decision': 'not_promoted', 'formal_result': False,
    'reference_case': '../hamburg_ring1_primary_entry_full_20261022_v1',
    'reason': 'Field MAE and uninserted demand worsen despite all 455 shared vehicles realizing both fields.',
    'next_single_issue': 'Joint native passage timing; the frozen-time proxy is not invariant under changed demand.'
}, indent=2) + '\n', encoding='utf-8', newline='\n')
for md in CASE.glob('*.md'):
    for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', md.read_text(encoding='utf-8')):
        if '://' not in target and not target.startswith('#'):
            assert target == 'manifest.json' or (md.parent / target.split('#')[0]).exists(), (md.name, target)
manifest = [{'path': p.relative_to(CASE).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    for p in sorted(CASE.rglob('*')) if p.is_file() and p.name != 'manifest.json']
assert all((CASE / r['path']).stat().st_size < 50_000_000 for r in manifest)
(CASE / 'manifest.json').write_text(json.dumps({'formal_result': False, 'files': manifest}, indent=2) + '\n', encoding='utf-8', newline='\n')
print(json.dumps({'files': len(manifest), 'bytes': sum(p.stat().st_size for p in CASE.rglob('*') if p.is_file())}))
