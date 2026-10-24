"""Freeze the completed older control trial and the standard SPSA preflight."""
from pathlib import Path
import gzip
import hashlib
import json
import re
import shutil

root=Path.cwd()
out=root/'benchmarks/hamburg_ring1_spillback_repair_complete_20261022_v2'
out.mkdir(exist_ok=True)
assert {p.name for p in out.iterdir()} <= {'README.md'}
source=root/'outputs/ring1-native-merge-preclear-full-20261022-v1'
for name in ('field-comparison.csv','station-comparison.csv','results.json'):
    shutil.copyfile(source/'report'/name,out/name)
for name in ('run.json','command.json','field-counts.xml','summary.xml','tripinfo.xml','collisions.xml','sumo-errors.log','vehicle-routes.xml','instant-e1.xml'):
    (out/(name+'.gz')).write_bytes(gzip.compress((source/'native'/name).read_bytes(),mtime=0))
shutil.copyfile(source.with_name(source.name+'-execution')/'execution.json',out/'execution.json')
inventory=[{'name':p.name,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
           for p in sorted((source/'native').iterdir()) if p.is_file()]
(out/'raw-native-inventory.json').write_text(json.dumps(inventory,indent=2)+'\n',encoding='utf-8')
study=root/'benchmarks/hamburg_ring1_od_spsa_standard_20261022_v1'
for suffix in ('.log','.xml'):
    shutil.copyfile(root/'outputs'/('hamburg-standard-spsa-integration-20261022-v2'+suffix),study/('preflight-tests'+suffix))
shutil.copyfile(__file__,study/'freeze-preflight-at-execution.py')
names=['plugins/astra-sumo/src/astra_sumo/core/'+n+'.py' for n in ('spsa','hamburg_od_spsa','hamburg_od_reuse')]
names+=['plugins/astra-sumo/scripts/run_hamburg_od_spsa.py','tests/test_spsa.py','tests/test_hamburg_od_spsa.py','tests/test_hamburg_od_reuse.py']
(study/'implementation.json').write_text(json.dumps({'files':[{'path':n,'sha256':hashlib.sha256((root/n).read_bytes()).hexdigest()} for n in names]},indent=2)+'\n',encoding='utf-8')
print('Frozen old full control evidence and standard SPSA preflight. No new simulations.')
