"""Seal the western-entry route check and initialization evidence."""

import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_western_entry_prefix_20261022_v1'
for suffix in ('xml', 'log'):
    shutil.copyfile(ROOT / f'outputs/western-entry-integration-tests-20261022-v1.{suffix}', CASE / f'tests.{suffix}')
files = ['plugins/astra-sumo/src/astra_sumo/core/hamburg_entry_prior.py', 'tests/test_hamburg_entry_prior.py']
for name in files:
    target = CASE / 'implementation' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
shutil.copyfile(Path(__file__), CASE / Path(__file__).name)
(CASE / 'implementation-sha256.json').write_text(json.dumps({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    for name in files}, indent=2) + '\n', encoding='utf-8', newline='\n')
for p in CASE.glob('*.py'):
    p.write_text(p.read_text(encoding='utf-8').rstrip() + '\n', encoding='utf-8', newline='\n')
for md in CASE.glob('*.md'):
    for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', md.read_text(encoding='utf-8')):
        if '://' not in target and not target.startswith('#'):
            assert target == 'manifest.json' or (md.parent / target.split('#')[0]).exists(), target
for name, digest in json.loads((CASE / 'inputs/executed-implementation-sha256.json').read_bytes()).items():
    snapshot = CASE / 'inputs/implementation-at-execution' / name
    source = snapshot if snapshot.is_file() else Path(name)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
prior = json.loads((CASE / 'inputs/western-entry-prior.json').read_bytes())
for name, digest in prior['source_sha256'].items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest
for name, digest in prior['input_sha256'].items():
    p = CASE / 'inputs' / name
    raw = p.read_bytes() if p.is_file() else gzip.decompress(p.with_name(p.name + '.gz').read_bytes())
    assert hashlib.sha256(raw).hexdigest() == digest, name
manifest = [{'path': p.relative_to(CASE).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    for p in sorted(CASE.rglob('*')) if p.is_file() and p.name != 'manifest.json']
assert all((CASE / r['path']).stat().st_size < 50_000_000 for r in manifest)
(CASE / 'manifest.json').write_text(json.dumps({'formal_result': False, 'files': manifest}, indent=2) + '\n', encoding='utf-8', newline='\n')
print(json.dumps({'files': len(manifest), 'bytes': sum(p.stat().st_size for p in CASE.rglob('*') if p.is_file())}))
