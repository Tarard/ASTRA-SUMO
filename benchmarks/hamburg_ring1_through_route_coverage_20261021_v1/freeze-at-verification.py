"""Retain the completed route probe and the rejected larger-demand run."""

import gzip
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'benchmarks/hamburg_ring1_through_route_coverage_20261021_v1'
OUT.mkdir(exist_ok=False)
for name, source in {
    'inputs': 'ring1-through-prior-prepared-20261021-v2',
    'paired-probe': 'ring1-through-routes-paired-probe-20261021-v1',
    'native': 'ring1-through-prior-native-20261021-v1',
    'analysis': 'ring1-through-route-analysis-20261021-v1',
}.items():
    for p in (ROOT / 'outputs' / source).rglob('*'):
        if not p.is_file():
            continue
        relative = p.relative_to(ROOT / 'outputs' / source)
        target = OUT / name / relative
        target.parent.mkdir(exist_ok=True, parents=True)
        keep = name == 'inputs' and p.name == 'network.net.xml'
        if not keep and p.suffix in {'.xml', '.json', '.csv'} and p.stat().st_size > 200000:
            target = target.with_name(target.name + '.gz')
            target.write_bytes(gzip.compress(p.read_bytes(), mtime=0))
            assert gzip.decompress(target.read_bytes()) == p.read_bytes()
        else:
            shutil.copyfile(p, target)
for src, dst in {
    'verify-through-route-coverage-20261021-v1.py': 'analysis-at-verification.py',
    'freeze-through-route-coverage-20261021-v1.py': 'freeze-at-verification.py',
}.items():
    shutil.copyfile(ROOT / 'outputs' / src, OUT / dst)
shutil.copyfile(ROOT / 'benchmarks/hamburg_ring1_primary_exit_prior_20261021_v1/.gitattributes', OUT / '.gitattributes')
print(OUT)
