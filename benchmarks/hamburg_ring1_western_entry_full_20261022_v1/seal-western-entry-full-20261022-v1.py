"""Seal the rejected complete western-entry experiment and its exact native inputs."""

import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_western_entry_full_20261022_v1'
for name in ('README.md', 'workflow-report.md', 'report.html'):
    path = CASE / name
    content = path.read_text(encoding='utf-8')
    for original in ('passage-reconciliation.json', 'vehicle-field-passages.csv'):
        content = re.sub(re.escape(original) + r'(?!\.gz)', original + '.gz', content)
    path.write_text(content, encoding='utf-8', newline='\n')
for suffix in ('xml', 'log'):
    shutil.copyfile(ROOT / f'outputs/western-entry-full-integration-tests-20261022-v1.{suffix}', CASE / f'tests.{suffix}')
files = ['plugins/astra-sumo/src/astra_sumo/core/hamburg_entry_prior.py', 'tests/test_hamburg_entry_prior.py']
for name in files:
    target = CASE / 'implementation' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
shutil.copyfile(Path(__file__), CASE / Path(__file__).name)
(CASE / 'implementation-sha256.json').write_text(json.dumps({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    for name in files}, indent=2) + '\n', encoding='utf-8', newline='\n')
(CASE / 'implementation-provenance-note.json').write_text(json.dumps({
    'formal_result': False,
    'native_execution': 'inputs/implementation-at-execution and inputs/executed-implementation-sha256.json',
    'current_preparation_and_verification': 'implementation and implementation-sha256.json',
    'difference': 'After native execution and analysis, future preparation metadata was corrected from the inherited 27 profiles to the actual 29. No executed native input or runtime driver changed.',
    'executed_scenario_retained': True
}, indent=2) + '\n', encoding='utf-8', newline='\n')
for path in CASE.glob('*.py'):
    path.write_text(path.read_text(encoding='utf-8').rstrip() + '\n', encoding='utf-8', newline='\n')
for md in CASE.glob('*.md'):
    for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', md.read_text(encoding='utf-8')):
        if '://' not in target and not target.startswith('#'):
            assert target == 'manifest.json' or (md.parent / target.split('#')[0]).exists(), (md.name, target)
for name, digest in json.loads((CASE / 'inputs/executed-implementation-sha256.json').read_bytes()).items():
    snapshot = CASE / 'inputs/implementation-at-execution' / name
    source = snapshot if snapshot.is_file() else Path(name)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest, name
    if not name.endswith('/hamburg_entry_prior.py'):
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
proposal = json.loads((CASE / 'inputs/full-window-proposal.json').read_bytes())
for name, digest in proposal['input_sha256'].items():
    path = CASE / 'inputs' / name
    raw = path.read_bytes() if path.is_file() else gzip.decompress(path.with_name(path.name + '.gz').read_bytes())
    assert hashlib.sha256(raw).hexdigest() == digest, name
prior = json.loads((CASE / 'inputs/western-entry-prior.json').read_bytes())
assert all(hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest for name, digest in prior['source_sha256'].items())
assert json.loads((CASE / 'results.json').read_bytes())['network_sha256'] == json.loads((CASE / 'figure-provenance.json').read_bytes())['current_network_sha256']
manifest = [{'path': path.relative_to(CASE).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    for path in sorted(CASE.rglob('*')) if path.is_file() and path.name != 'manifest.json']
assert all((CASE / row['path']).stat().st_size < 50_000_000 for row in manifest)
(CASE / 'manifest.json').write_text(json.dumps({'formal_result': False, 'files': manifest}, indent=2) + '\n', encoding='utf-8', newline='\n')
print(json.dumps({'files': len(manifest), 'bytes': sum(path.stat().st_size for path in CASE.rglob('*') if path.is_file())}))
