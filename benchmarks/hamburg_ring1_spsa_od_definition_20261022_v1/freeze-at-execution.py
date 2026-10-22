"""Freeze the OD definition audit and paper inventory, without another simulation."""
from pathlib import Path
import gzip
import hashlib
import json
import shutil

root = Path.cwd()
out = root / 'benchmarks/hamburg_ring1_spsa_od_definition_20261022_v1'
out.mkdir(exist_ok=False)
source = root / 'outputs/hamburg-od-definition-audit-20261022-v2'
for name in ('audit.json', 'od-support.json'):
    shutil.copyfile(source / name, out / name)
(out / 'candidate-routes.json.gz').write_bytes(gzip.compress((source / 'candidate-routes.json').read_bytes(), mtime=0))
papers = []
urls = {'spall1998-overview.pdf': 'https://www.jhuapl.edu/spsa/pdf-spsa/spall_an_overview.pdf',
        'antoniou2015-wspsa.pdf': 'https://web.mit.edu/cami/Public/CLA_WSPSAinPractice_ISTTT_2015_preprint.pdf',
        'mahajan2023.pdf': 'https://mediatum.ub.tum.de/doc/1701188/o6wl24dt4jm8erz53tllvu6nf.mahajan_2023.pdf'}
for name, url in urls.items():
    path = root / 'outputs/antoniou-spsa-literature-20261022-v1' / name
    papers.append({'file': name, 'url': url, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                   'bytes': path.stat().st_size, 'full_pdf_in_repository': False})
papers.append({'url': 'https://www.researchgate.net/publication/277402098_Calibration_of_Microscopic_Traffic_Simulation_Models_Methods_and_Application',
               'title': 'Calibration of Microscopic Traffic Simulation Models: Methods and Application',
               'doi': '10.3141/1999-21', 'access': 'Author-uploaded original full text read in browser',
               'local_pdf': None})
(out/'paper-inventory.json').write_text(json.dumps({'access_date': '2026-10-22', 'papers': papers}, indent=2)+'\n', encoding='utf-8')
shutil.copyfile(__file__, out / 'freeze-at-execution.py')
print(str(out))
