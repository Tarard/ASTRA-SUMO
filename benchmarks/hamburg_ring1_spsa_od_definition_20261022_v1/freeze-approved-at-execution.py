"""Retain completed evidence and approved OD preparation; do not rerun SUMO."""
from pathlib import Path
import gzip
import hashlib
import json
import shutil

root = Path.cwd()
od = root / 'benchmarks/hamburg_ring1_spsa_od_definition_20261022_v1'
prepared = root / 'outputs/hamburg-od-baseline-20261022-v1'
(od/'approved-od-model.json.gz').write_bytes(gzip.compress((prepared/'od-model.json').read_bytes(), mtime=0))
shutil.copyfile(prepared/'initial-od-vector.json', od/'initial-od-vector.json')
for source, name in [('hamburg-spsa-od-method-tests-20261022-v1','method-tests'),
                     ('hamburg-od-spsa-native-tests-20261022-v1','tests')]:
    for suffix in ('.log', '.xml'):
        shutil.copyfile(root/'outputs'/(source+suffix), od/(name+suffix))
repair = root/'benchmarks/hamburg_ring1_spillback_repair_20261022_v1'
source = root/'outputs/ring1-native-merge-coordination-full-20261022-v1'
dest = repair/'merge-coordinated-full'; dest.mkdir(exist_ok=False)
for name in ('field-comparison.csv','station-comparison.csv','results.json'):
    shutil.copyfile(source/'report'/name, dest/name)
for name in ('run.json','command.json','field-counts.xml','summary.xml','tripinfo.xml','collisions.xml','sumo-errors.log','vehicle-routes.xml'):
    (dest/(name+'.gz')).write_bytes(gzip.compress((source/'native'/name).read_bytes(),mtime=0))
shutil.copyfile(source.with_name(source.name+'-execution')/'execution.json',dest/'execution.json')
shutil.copyfile(__file__, od/'freeze-approved-at-execution.py')
for directory in (od,repair):
    # Normalize only this task's new evidence before recording its byte hashes.
    for path in directory.rglob('*'):
        if path.is_file() and path.suffix in {'.py','.md','.json','.csv','.log','.xml'}:
            path.write_bytes(path.read_bytes().replace(b'\r\n',b'\n'))
    files=[{'path':p.relative_to(directory).as_posix(),'bytes':p.stat().st_size,
            'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
           for p in sorted(directory.rglob('*')) if p.is_file() and p.name!='manifest.json']
    (directory/'manifest.json').write_text(json.dumps({'files':files,'formal_result':False},indent=2)+'\n',encoding='utf-8')
print('Frozen completed control trial and approved OD preparation.')
