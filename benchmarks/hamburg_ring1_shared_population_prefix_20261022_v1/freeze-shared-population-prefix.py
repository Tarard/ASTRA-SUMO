import gzip
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'benchmarks/hamburg_ring1_shared_population_prefix_20261022_v1'
CASE.mkdir(exist_ok=False)
for label, name in [('inputs', 'ring1-shared-population-inputs-20261022-v2'),
                    ('native', 'ring1-shared-population-prefix-20261022-v1'),
                    ('analysis', 'ring1-shared-population-prefix-analysis-20261022-v1')]:
    source = ROOT / 'outputs' / name
    for path in source.rglob('*'):
        if not path.is_file():
            continue
        target = CASE / label / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in {'.xml', '.json', '.csv'} and path.stat().st_size > 200000 and not (label == 'inputs' and path.name == 'network.net.xml'):
            target = target.with_name(target.name + '.gz')
            target.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
            assert gzip.decompress(target.read_bytes()) == path.read_bytes()
        else:
            shutil.copyfile(path, target)
for name in ('probe-shared-population-prefix', 'analyze-shared-population-prefix', 'freeze-shared-population-prefix'):
    shutil.copyfile(ROOT / 'outputs' / (name + '-20261022-v1.py'), CASE / (name + '.py'))
shutil.copyfile(ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1/.gitattributes', CASE / '.gitattributes')
print(CASE)
