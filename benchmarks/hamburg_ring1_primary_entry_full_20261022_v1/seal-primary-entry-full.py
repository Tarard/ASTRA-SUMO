import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
for path in (CASE / 'README.md', CASE / 'workflow-report.md', CASE / 'report.html'):
    text = path.read_text(encoding='utf-8')
    for old in ('passage-reconciliation.json', 'vehicle-field-passages.csv'):
        assert (CASE / (old + '.gz')).exists()
        text = text.replace('](' + old + ')', '](' + old + '.gz)').replace('href="' + old + '"', 'href="' + old + '.gz"')
    path.write_text(text, encoding='utf-8', newline='\n')
for path in CASE.glob('*.py'):
    path.write_bytes(path.read_bytes().rstrip(b'\r\n \t') + b'\n')
for suffix in ('xml', 'log'):
    shutil.copyfile(ROOT / f'outputs/primary-entry-full-tests-20261022-v1.{suffix}', CASE / f'tests.{suffix}')
for name in ('run-primary-entry-full-20261022-v1.log', 'analyze-primary-entry-full-20261022-v1.log'):
    shutil.copyfile(ROOT / 'outputs' / name, CASE / name)
shutil.copyfile(ROOT / 'outputs/ring1-primary-entry-prior-full-prefix-check-20261022-v1.json', CASE / 'live-prefix-check.json')
files = ['plugins/astra-sumo/src/astra_sumo/core/' + name for name in (
    'hamburg_entry_prior.py', 'lane_section_hold.py', 'simulation_stall.py', 'detector_demand_replay.py', 'hamburg_readme_results.py')]
files += ['tests/test_hamburg_entry_prior.py']
for name in files:
    target = CASE / 'implementation' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
shutil.copyfile(Path(__file__), CASE / 'seal-primary-entry-full.py')
(CASE / 'implementation-sha256.json').write_text(json.dumps({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    for name in files}, indent=2) + '\n', encoding='utf-8', newline='\n')
(CASE / 'manifest.json').write_text(json.dumps({'formal_result': False, 'files': [
    {'path': p.relative_to(CASE).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    for p in sorted(CASE.rglob('*')) if p.is_file() and p.name != 'manifest.json']}, indent=2) + '\n', encoding='utf-8', newline='\n')
for md in CASE.glob('*.md'):
    for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', md.read_text(encoding='utf-8')):
        if '://' not in target and not target.startswith('#'):
            assert (md.parent / target.split('#')[0]).exists(), (md.name, target)
manifest = json.loads((CASE / 'manifest.json').read_bytes())
assert all(hashlib.sha256((CASE / row['path']).read_bytes()).hexdigest() == row['sha256'] for row in manifest['files'])
print(json.dumps({'files': len(manifest['files']), 'bytes': sum(p.stat().st_size for p in CASE.rglob('*') if p.is_file())}))
