import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
cases = [ROOT / 'benchmarks' / name for name in (
    'hamburg_ring1_terminal_queue_diagnosis_20261022_v1', 'hamburg_ring1_shared_field_probe_20261022_v1')]
files = ['plugins/astra-sumo/src/astra_sumo/core/' + name for name in (
    'simulation_stall.py', 'lane_section_hold.py', 'detector_demand_replay.py', 'route_sensor_matrix.py')]
files += ['tests/test_hamburg_terminal_and_shared_probes.py']
for case in cases:
    for suffix in ('xml', 'log'):
        shutil.copyfile(ROOT / f'outputs/terminal-and-shared-probe-tests-20261022-v2.{suffix}', case / f'tests.{suffix}')
    for name in files:
        target = case / 'implementation' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    shutil.copyfile(Path(__file__), case / 'seal-terminal-and-shared-probes.py')
    for path in case.glob('*.py'):
        path.write_bytes(path.read_bytes().rstrip(b'\r\n \t') + b'\n')
    (case / 'implementation-sha256.json').write_text(json.dumps({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in files}, indent=2) + '\n', encoding='utf-8', newline='\n')
    (case / 'manifest.json').write_text(json.dumps({'formal_result': False, 'files': [
        {'path': p.relative_to(case).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(case.rglob('*')) if p.is_file() and p.name != 'manifest.json']}, indent=2) + '\n', encoding='utf-8', newline='\n')
    for md in case.glob('*.md'):
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', md.read_text(encoding='utf-8')):
            if '://' not in target and not target.startswith('#'):
                assert (md.parent / target.split('#')[0]).exists(), (md.name, target)
    manifest = json.loads((case / 'manifest.json').read_bytes())
    assert all(hashlib.sha256((case / row['path']).read_bytes()).hexdigest() == row['sha256'] for row in manifest['files'])
    print(json.dumps({'case': case.name, 'files': len(manifest['files']),
        'bytes': sum(p.stat().st_size for p in case.rglob('*') if p.is_file())}))
