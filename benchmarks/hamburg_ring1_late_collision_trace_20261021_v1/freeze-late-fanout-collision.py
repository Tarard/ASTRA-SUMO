import gzip
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'benchmarks/hamburg_ring1_late_collision_trace_20261021_v1'
OUT.mkdir(exist_ok=False)
for label, source in [('native', 'ring1-late-fanout-collision-trace-20261021-v1'),
                      ('analysis', 'ring1-late-fanout-collision-analysis-20261021-v1')]:
    for path in (ROOT / 'outputs' / source).rglob('*'):
        if not path.is_file():
            continue
        target = OUT / label / path.relative_to(ROOT / 'outputs' / source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in {'.xml', '.json', '.csv'} and path.stat().st_size > 200000:
            target = target.with_name(target.name + '.gz')
            target.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
            assert gzip.decompress(target.read_bytes()) == path.read_bytes()
        else:
            shutil.copyfile(path, target)
for name in ('analyze-late-fanout-collision', 'freeze-late-fanout-collision'):
    shutil.copyfile(ROOT / 'outputs' / (name + '-20261021-v1.py'), OUT / (name + '.py'))
shutil.copyfile(ROOT / 'benchmarks/hamburg_ring1_localized_junction_body_full_20261021_v1/.gitattributes', OUT / '.gitattributes')
print(OUT)
