import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
net = ET.parse(ROOT / 'benchmarks/hamburg_ring1_through_route_coverage_20261021_v1/inputs/network.net.xml').getroot()
for identity in ('14398547', '205069138'):
    junction = net.find(f"junction[@id='{identity}']")
    print('JUNCTION', junction.attrib)
    lanes = junction.get('intLanes').split()
    for i, lane in enumerate(lanes):
        connections = [c.attrib for c in net.findall('connection') if c.get('via') == lane and not c.get('from').startswith(':')]
        request = junction.find(f"request[@index='{i}']")
        print(json.dumps({'index': i, 'lane': lane, 'connections': connections, 'request': request.attrib if request is not None else None}))
