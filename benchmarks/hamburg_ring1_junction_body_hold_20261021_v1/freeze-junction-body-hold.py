import gzip
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'benchmarks/hamburg_ring1_junction_body_hold_20261021_v1'
OUT.mkdir(exist_ok=False)
for label, name in {'native':'ring1-junction-body-hold-native-20261021-v1','analysis':'ring1-junction-body-hold-analysis-20261021-v1'}.items():
    source=ROOT/'outputs'/name
    for p in source.rglob('*'):
        if not p.is_file():
            continue
        target=OUT/label/p.relative_to(source)
        target.parent.mkdir(parents=True,exist_ok=True)
        if p.suffix in {'.xml','.json','.csv'} and p.stat().st_size>200000:
            target=target.with_name(target.name+'.gz')
            target.write_bytes(gzip.compress(p.read_bytes(),mtime=0))
            assert gzip.decompress(target.read_bytes())==p.read_bytes()
        else:
            shutil.copyfile(p,target)
for name in ('analyze-junction-body-hold','check-junction-body-population','check-junction-body-terminal-holds','freeze-junction-body-hold'):
    shutil.copyfile(ROOT/'outputs'/(name+'-20261021-v1.py'),OUT/(name+'.py'))
shutil.copyfile(ROOT/'benchmarks/hamburg_ring1_through_route_coverage_20261021_v1/.gitattributes',OUT/'.gitattributes')
print(OUT)
