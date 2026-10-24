import gzip
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'benchmarks/hamburg_ring1_junction_body_full_20261021_v1'
OUT.mkdir(exist_ok=False)
sources={
    'inputs':'ring1-junction-body-full-inputs-20261021-v1',
    'native':'ring1-junction-body-full-native-20261021-v1',
    '':'ring1-junction-body-full-report-20261021-v1',
    'data-analysis':'ring1-junction-body-full-data-analysis-20261021-v1',
    'short-connector-probe':'ring1-short-connector-body-trap-20261021-v1',
    'prefix-data-analysis':'ring1-data-led-prefix-diagnosis-20261021-v1',
    'signal-inspection':'ring1-data-led-green-inspection-20261021-v1',
    'live-observations':'ring1-junction-body-full-live-notes-20261021-v1'}
for label,name in sources.items():
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
shutil.copyfile(ROOT/'outputs/ring1-junction-body-full-prefix-check-20261021-v1.json',OUT/'prefix-check.json')
shutil.copyfile(OUT/'README.md',OUT/'workflow-report.md')
for name in ('prepare-junction-body-full-inputs','decompose-junction-body-main-residuals','decompose-junction-body-prefix-residuals','inspect-main-field-green-times','probe-short-connector-body-trap','freeze-junction-body-full'):
    shutil.copyfile(ROOT/'outputs'/(name+'-20261021-v1.py'),OUT/(name+'.py'))
shutil.copyfile(ROOT/'benchmarks/hamburg_ring1_junction_body_hold_20261021_v1/.gitattributes',OUT/'.gitattributes')
print(OUT)
