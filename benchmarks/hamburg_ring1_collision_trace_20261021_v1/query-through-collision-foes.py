import json
from pathlib import Path
import xml.etree.ElementTree as ET

import sumo
import traci

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256

ROOT = Path(__file__).resolve().parents[1]
NET = ROOT / 'benchmarks/hamburg_ring1_through_route_coverage_20261021_v1/inputs/network.net.xml'
OUT = ROOT / 'outputs/ring1-through-collision-foes-20261021-v1'
OUT.mkdir(exist_ok=False)
network = ET.parse(NET).getroot()
lanes = [r for edge in network.findall('edge') for r in edge.findall('lane') if edge.get('id').startswith((':14398547_', ':205069138_'))]
binary = Path(sumo.__file__).parent / 'bin/sumo.exe'
traci.start([str(binary), '--net-file', str(NET), '--no-step-log', 'true', '--log', str(OUT / 'sumo.log'), '--error-log', str(OUT / 'sumo-errors.log')])
try:
    version = traci.getVersion()
    assert version == (22, 'SUMO 1.27.1')
    result = {r.get('id'): {'runtime_internal_foes': traci.lane.getInternalFoes(r.get('id')), 'lane': r.attrib} for r in lanes}
    report = {'native_version': version, 'traci_module': traci.__file__, 'network_sha256': file_sha256(NET), 'lanes': result,
              'claim_boundary': 'Static runtime lane conflicts only. No traffic or causal collision test. Runtime sibling conflicts can exist even when XML request foes bits are zero.'}
    write_json_atomic(OUT / 'runtime-foes.json', report)
    print(json.dumps({k: v['runtime_internal_foes'] for k, v in result.items()}))
finally:
    traci.close()
for identity in ('189775480', '186713428'):
    edge = network.find(f"edge[@id='{identity}']")
    print(identity, [{k: r.get(k) for k in ('id', 'length', 'width', 'changeLeft', 'changeRight')} for r in edge.findall('lane')])
