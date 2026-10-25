import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_three_node_body_full_20261022_v1'
for p in (CASE / 'README.md', CASE / 'workflow-report.md', CASE / 'report.html'):
    text = p.read_text(encoding='utf-8')
    for old in ('passage-reconciliation.json', 'vehicle-field-passages.csv'):
        assert (CASE / (old + '.gz')).exists()
        text = text.replace('](' + old + ')', '](' + old + '.gz)').replace('href="' + old + '"', 'href="' + old + '.gz"')
    p.write_text(text, encoding='utf-8', newline='\n')
for source in CASE.glob('*.py'):
    source.write_bytes(source.read_bytes().rstrip(b'\r\n \t') + b'\n')
for suffix in ('xml', 'log'):
    shutil.copyfile(ROOT / f'outputs/three-node-completion-tests-20261022-v1.{suffix}', CASE / f'tests.{suffix}')
logdir = CASE / 'analysis-execution'
logdir.mkdir(exist_ok=True)
for name in ('analyze-three-node-body-full-20261022-v1.log', 'finish-three-node-body-full-analysis-20261022-v1.log', 'finish-three-node-body-full-analysis-20261022-v2.log'):
    shutil.copyfile(ROOT / 'outputs' / name, logdir / name)
files = ['plugins/astra-sumo/src/astra_sumo/core/' + name for name in (
    'lane_section_hold.py', 'simulation_stall.py', 'detector_demand_replay.py', 'hamburg_readme_pipeline.py',
    'hamburg_readme_results.py', 'hamburg_collision_trace.py')]
files += ['tests/' + name for name in ('test_hamburg_collision_trace.py', 'test_hamburg_main_junction_hold.py', 'test_hamburg_late_exit_probe.py')]
for name in files:
    target = CASE / 'implementation' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
shutil.copyfile(Path(__file__), CASE / 'seal-three-node-body-full.py')
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
