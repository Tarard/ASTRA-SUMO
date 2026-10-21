import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
cases = [ROOT / 'benchmarks' / name for name in (
    'hamburg_ring1_localized_junction_body_full_20261021_v1',
    'hamburg_ring1_late_collision_trace_20261021_v1')]
implementation = [ROOT / name for name in (
    'plugins/astra-sumo/src/astra_sumo/core/hamburg_collision_trace.py',
    'plugins/astra-sumo/scripts/trace_hamburg_collisions.py',
    'plugins/astra-sumo/src/astra_sumo/core/lane_section_hold.py',
    'plugins/astra-sumo/src/astra_sumo/core/simulation_stall.py',
    'plugins/astra-sumo/src/astra_sumo/core/detector_demand_replay.py',
    'plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_pipeline.py',
    'plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_results.py',
    'tests/test_hamburg_collision_trace.py',
    'tests/test_hamburg_main_junction_hold.py')]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


for case in cases:
    for suffix in ('xml', 'log'):
        shutil.copyfile(ROOT / f'outputs/localized-full-and-late-trace-tests-20261021-v1.{suffix}', case / f'tests.{suffix}')
    for source in implementation:
        target = case / 'implementation' / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    shutil.copyfile(Path(__file__), case / 'seal-localized-full-and-late-trace.py')
    (case / 'implementation-sha256.json').write_text(json.dumps(
        {p.relative_to(ROOT).as_posix(): sha(p) for p in implementation}, indent=2) + '\n', encoding='utf-8', newline='\n')
    (case / 'manifest.json').write_text(json.dumps({'formal_result': False,
        'files': [{'path': p.relative_to(case).as_posix(), 'sha256': sha(p)}
                  for p in sorted(case.rglob('*')) if p.is_file() and p.name != 'manifest.json']}, indent=2) + '\n', encoding='utf-8', newline='\n')
    for md in case.glob('*.md'):
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', md.read_text(encoding='utf-8')):
            if '://' not in target and not target.startswith('#'):
                assert (md.parent / target.split('#')[0]).exists(), (md, target)
    manifest = json.loads((case / 'manifest.json').read_bytes())
    assert all(sha(case / row['path']) == row['sha256'] for row in manifest['files'])
    print(json.dumps({'case': case.name, 'files': len(manifest['files']), 'bytes': sum(p.stat().st_size for p in case.rglob('*') if p.is_file())}))
