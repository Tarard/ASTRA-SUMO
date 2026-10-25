"""Seal native timing evidence after its regression checks pass."""

import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_native_departure_windows_20261022_v1'
for suffix in ('xml', 'log'):
    shutil.copyfile(ROOT / f'outputs/native-time-correction-integration-tests-20261022-v2.{suffix}', CASE / f'tests.{suffix}')
files = ['plugins/astra-sumo/src/astra_sumo/core/native_temporal_feedback.py', 'tests/test_native_temporal_feedback.py']
for name in files:
    target = CASE / 'implementation' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
shutil.copyfile(Path(__file__), CASE / Path(__file__).name)
(CASE / 'implementation-sha256.json').write_text(json.dumps({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    for name in files}, indent=2) + '\n', encoding='utf-8', newline='\n')
source_hashes = json.loads((CASE / 'timing-windows/source-sha256.json').read_bytes())
assert all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest for name, digest in source_hashes.items())
for p in CASE.glob('*.py'):
    p.write_text(p.read_text(encoding='utf-8').rstrip() + '\n', encoding='utf-8', newline='\n')
for md in CASE.glob('*.md'):
    for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', md.read_text(encoding='utf-8')):
        if '://' not in target and not target.startswith('#'):
            assert target == 'manifest.json' or (md.parent / target.split('#')[0]).exists(), target
provenance = CASE / 'inputs/executed-implementation-sha256.json'
for name, digest in json.loads(provenance.read_bytes()).items():
    snapshot = CASE / 'inputs/implementation-at-execution' / name
    if snapshot.is_file():
        assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == digest
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    else:
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest
proposal = gzip.decompress((CASE / 'inputs/time-correction-proposal.json.gz').read_bytes())
for name, digest in json.loads(proposal)['input_sha256'].items():
    p = CASE / 'inputs' / name
    if p.is_file():
        raw = p.read_bytes()
    else:
        raw = gzip.decompress(p.with_name(p.name + '.gz').read_bytes())
    assert hashlib.sha256(raw).hexdigest() == digest, name
manifest = [{'path': p.relative_to(CASE).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    for p in sorted(CASE.rglob('*')) if p.is_file() and p.name != 'manifest.json']
assert all((CASE / r['path']).stat().st_size < 50_000_000 for r in manifest)
(CASE / 'manifest.json').write_text(json.dumps({'formal_result': False, 'files': manifest}, indent=2) + '\n', encoding='utf-8', newline='\n')
print(json.dumps({'files': len(manifest), 'bytes': sum(p.stat().st_size for p in CASE.rglob('*') if p.is_file()),
    'source_files_unchanged': len(source_hashes)}))
