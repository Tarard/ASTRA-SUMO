import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_shared_population_prefix_20261022_v1'
files = ['plugins/astra-sumo/src/astra_sumo/core/' + name for name in (
    'hamburg_shared_population.py', 'hamburg_readme_results.py', 'lane_section_hold.py',
    'simulation_stall.py', 'detector_demand_replay.py', 'route_sensor_matrix.py')]
files += ['tests/test_hamburg_shared_population.py']
for name in files:
    target = CASE / 'implementation' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
for suffix in ('xml', 'log'):
    shutil.copyfile(ROOT / f'outputs/shared-population-prefix-tests-20261022-v1.{suffix}', CASE / f'tests.{suffix}')
shutil.copyfile(Path(__file__), CASE / 'seal-shared-population-prefix.py')
(CASE / 'implementation-sha256.json').write_text(json.dumps({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    for name in files}, indent=2) + '\n', encoding='utf-8', newline='\n')
(CASE / 'manifest.json').write_text(json.dumps({'formal_result': False, 'files': [
    {'path': p.relative_to(CASE).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    for p in sorted(CASE.rglob('*')) if p.is_file() and p.name != 'manifest.json']}, indent=2) + '\n', encoding='utf-8', newline='\n')
for md in CASE.glob('*.md'):
    for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', md.read_text(encoding='utf-8')):
        if '://' not in target and not target.startswith('#'):
            assert (md.parent / target.split('#')[0]).exists(), (md.name, target)
print(json.dumps({'files': len(json.loads((CASE / 'manifest.json').read_bytes())['files']),
    'bytes': sum(p.stat().st_size for p in CASE.rglob('*') if p.is_file())}))
