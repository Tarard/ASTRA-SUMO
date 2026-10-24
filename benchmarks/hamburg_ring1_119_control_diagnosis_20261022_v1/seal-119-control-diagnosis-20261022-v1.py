"""Seal control diagnosis, unchanged-traffic correspondence and the rejected prefix."""

import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_119_control_diagnosis_20261022_v1'
for suffix in ('xml', 'log'):
    shutil.copyfile(ROOT / f'outputs/control-diagnosis-integration-tests-20261022-v1.{suffix}', CASE / f'tests.{suffix}')
files = ['plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_control.py', 'tests/test_hamburg_nominal_control.py']
for name in files:
    target = CASE / 'implementation' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
shutil.copyfile(Path(__file__), CASE / Path(__file__).name)
decision = json.loads((CASE / 'decision.json').read_bytes())
decision['rejected_unrun_prototype'] = 'The retirement mechanism was never enabled in a native run. The prototype was reverted after checking waypoint upper-bound semantics.'
(CASE / 'decision.json').write_text(json.dumps(decision, indent=2) + '\n', encoding='utf-8', newline='\n')
(CASE / 'implementation-sha256.json').write_text(json.dumps({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    for name in files}, indent=2) + '\n', encoding='utf-8', newline='\n')
for path in CASE.glob('*.py'):
    path.write_text(path.read_text(encoding='utf-8').rstrip() + '\n', encoding='utf-8', newline='\n')
for md in CASE.glob('*.md'):
    for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', md.read_text(encoding='utf-8')):
        if '://' not in target and not target.startswith('#'):
            assert target == 'manifest.json' or (md.parent / target.split('#')[0]).exists(), (md.name, target)
report = json.loads((CASE / 'diagnostic-analysis/report.json').read_bytes())
for name, digest in report['source_sha256'].items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest
for name, digest in json.loads((CASE / 'nominal-inputs/executed-implementation-sha256.json').read_bytes()).items():
    snapshot = CASE / 'nominal-inputs/implementation-at-execution' / name
    source = snapshot if snapshot.is_file() else Path(name)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
proposal = json.loads((CASE / 'nominal-inputs/nominal-control-proposal.json').read_bytes())
for name, digest in proposal['input_sha256'].items():
    path = CASE / 'nominal-inputs' / name
    raw = path.read_bytes() if path.is_file() else gzip.decompress(path.with_name(path.name + '.gz').read_bytes())
    assert hashlib.sha256(raw).hexdigest() == digest, name
manifest = [{'path': p.relative_to(CASE).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    for p in sorted(CASE.rglob('*')) if p.is_file() and p.name != 'manifest.json']
assert all((CASE / row['path']).stat().st_size < 50_000_000 for row in manifest)
(CASE / 'manifest.json').write_text(json.dumps({'formal_result': False, 'files': manifest}, indent=2) + '\n', encoding='utf-8', newline='\n')
print(json.dumps({'files': len(manifest), 'bytes': sum(p.stat().st_size for p in CASE.rglob('*') if p.is_file())}))
