import gzip
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'benchmarks/hamburg_ring1_localized_junction_body_20261021_v1'
OUT.mkdir(exist_ok=False)
for label,name in {'inputs':'ring1-localized-junction-body-inputs-20261021-v1','native':'ring1-localized-junction-body-prefix-20261021-v1','analysis':'ring1-localized-junction-body-analysis-20261021-v1'}.items():
    folder=ROOT/'outputs'/name
    for p in folder.rglob('*'):
        if not p.is_file():
            continue
        target=OUT/label/p.relative_to(folder)
        target.parent.mkdir(parents=True,exist_ok=True)
        if p.suffix in {'.xml','.json','.csv'} and p.stat().st_size>200000 and not (label=='inputs' and p.name=='network.net.xml'):
            target=target.with_name(target.name+'.gz')
            target.write_bytes(gzip.compress(p.read_bytes(),mtime=0))
            assert gzip.decompress(target.read_bytes())==p.read_bytes()
        else:
            shutil.copyfile(p,target)
for name in ('probe-localized-junction-body','analyze-localized-junction-body-prefix','freeze-localized-body-prefix'):
    shutil.copyfile(ROOT/'outputs'/(name+'-20261021-v1.py'),OUT/(name+'.py'))
shutil.copyfile(ROOT/'benchmarks/hamburg_ring1_junction_body_full_20261021_v1/.gitattributes',OUT/'.gitattributes')
print(OUT)
