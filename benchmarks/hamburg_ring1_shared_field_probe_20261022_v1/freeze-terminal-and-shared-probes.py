import gzip
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
DIAG = ROOT / 'benchmarks/hamburg_ring1_terminal_queue_diagnosis_20261022_v1'
SHARED = ROOT / 'benchmarks/hamburg_ring1_shared_field_probe_20261022_v1'


def copy_folder(source, target):
    target.mkdir(exist_ok=False, parents=True)
    for path in source.rglob('*'):
        if not path.is_file():
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in {'.xml', '.json', '.csv'} and path.stat().st_size > 200000:
            destination = destination.with_name(destination.name + '.gz')
            destination.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
            assert gzip.decompress(destination.read_bytes()) == path.read_bytes()
        else:
            shutil.copyfile(path, destination)


DIAG.mkdir(exist_ok=False)
for name, source in [('failed-original-load', 'ring1-primary-entry-terminal-state-20261022-v1'),
                     ('inspection', 'ring1-primary-entry-terminal-state-20261022-v2'),
                     ('failed-observer-probe', 'ring1-z15-buffer-lane-probe-20261022-v1'),
                     ('lane-probe', 'ring1-z15-buffer-lane-probe-20261022-v2')]:
    copy_folder(ROOT / 'outputs' / source, DIAG / name)
(DIAG / 'analysis').mkdir()
checks = ROOT / 'outputs/ring1-terminal-and-route-probe-checks-20261022-v1'
for name in ('snapshot-correspondence.json', 'buffer-probe-check.json'):
    shutil.copyfile(checks / name, DIAG / 'analysis' / name)
copy_folder(ROOT / 'outputs/ring1-shared-field-route-probe-20261022-v1', SHARED)
(SHARED / 'analysis').mkdir()
for name in ('shared-route-check.json', 'shared-vehicle-passages.csv'):
    shutil.copyfile(checks / name, SHARED / 'analysis' / name)
base = ROOT / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1'
control = ET.parse(base / 'inputs/control.add.xml').getroot()
green = {}
for identity, index in [('LSA200_part1', 0), ('LSA200_part0', 28)]:
    logic = control.find(f"tlLogic[@id='{identity}']")
    start = 0
    windows = []
    for phase in logic.findall('phase'):
        end = start + float(phase.get('duration'))
        state = phase.get('state')[index]
        if state in 'gGyY':
            windows.append({'begin_s': start, 'end_s': end, 'state': state})
        start = end
    green[identity] = {'link_index': index, 'cycle_s': start, 'windows': windows}
positive, peak = {}, {}
with gzip.open(base / 'native/queues.xml.gz', 'rb') as stream:
    for _, row in ET.iterparse(stream):
        if row.tag == 'lane' and row.get('id', '').startswith(('142944325#0_', '480873120#0_')):
            identity = row.get('id')
            length = float(row.get('queueing_length', '0'))
            positive[identity] = positive.get(identity, 0) + (length > 0)
            peak[identity] = max(peak.get(identity, 0), length)
        row.clear()
write_json_atomic(DIAG / 'analysis/queue-and-signal-context.json', {'formal_result': False,
    'source_case': base.name, 'queue_sample_period_s': 1, 'positive_queue_samples': positive,
    'maximum_native_queue_length_m': peak, 'installed_signal_windows': green,
    'claim_boundary': 'Original native queue samples and installed input programs only. A free lane or one green snapshot does not establish spare service capacity. Overlapping green windows do not prove a capacity bottleneck or its absence.'})
for case, names in [(DIAG, ('query-primary-entry-terminal-state-20261022-v1', 'query-primary-entry-terminal-state-20261022-v2',
                            'probe-z15-buffer-lanes-20261022-v1', 'probe-z15-buffer-lanes-20261022-v2')),
                    (SHARED, ('probe-shared-field-routes-20261022-v1',))]:
    for name in names:
        shutil.copyfile(ROOT / 'outputs' / (name + '.py'), case / (name + '.py'))
        shutil.copyfile(ROOT / 'outputs' / (name + '.log'), case / (name + '.log'))
    shutil.copyfile(ROOT / 'outputs/check-terminal-and-route-probes-20261022-v1.py', case / 'check-terminal-and-route-probes.py')
    shutil.copyfile(Path(__file__), case / 'freeze-terminal-and-shared-probes.py')
    shutil.copyfile(base / '.gitattributes', case / '.gitattributes')
    source_paths = [base / 'manifest.json', ROOT / 'benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base/ideal-lane-profiles.json.gz',
                    ROOT / 'benchmarks/hamburg_ring1_m2_control_20261021_v1/link-bindings.json.gz']
    write_json_atomic(case / 'source-sha256.json', {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths})
print(json.dumps({'diagnosis': str(DIAG), 'shared_probe': str(SHARED)}))
