"""A service permission change must rebuild native connections without editing its source."""

from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.network_permissions import apply_service_passenger_permissions
from torii_sumo.core.osm_access import _permission_set


@pytest.mark.skipif(not shutil.which('sumo') or not shutil.which('netconvert'), reason='Native SUMO is required.')
def test_service_candidate_preserves_source_and_opens_the_entire_passenger_path(tmp_path):
    nodes, edges, source = (tmp_path/name for name in ('nodes.xml','edges.xml','source.net.xml'))
    nodes.write_text('<nodes><node id="a" x="0" y="0"/><node id="b" x="50" y="0" type="traffic_light"/>'
                     '<node id="c" x="100" y="0"/><node id="d" x="150" y="0"/></nodes>',encoding='utf-8')
    edges.write_text('<edges><edge id="a" from="a" to="b" type="highway.service" allow="bus"/>'
                     '<edge id="b" from="b" to="c" type="highway.service" allow="bus"/>'
                     '<edge id="c" from="c" to="d" type="highway.primary" allow="passenger bus"/></edges>',encoding='utf-8')
    built=subprocess.run(['netconvert','-n',str(nodes),'-e',str(edges),'-o',str(source)],capture_output=True,text=True)
    assert built.returncode==0,built.stderr
    before=source.read_bytes()
    result=apply_service_passenger_permissions(source,policy='allow_vehicle_service',output_dir=tmp_path/'permissions')
    assert source.read_bytes()==before
    assert result['status']=='pass',result
    candidate=Path(result['net_file'])
    assert candidate!=source
    assert result['changed_lane_count']==2
    assert result['candidate']['sha256']==file_sha256(candidate)
    original,new=ET.fromstring(before),ET.parse(candidate).getroot()
    for edge in new.findall('edge'):
        for lane in edge.findall('lane'):
            assert {'passenger','bus'} <= _permission_set(lane.attrib)
    assert [ET.tostring(t) for t in original.findall('tlLogic')]==[ET.tostring(t) for t in new.findall('tlLogic')]
    routes=tmp_path/'routes.xml'
    routes.write_text('<routes><vType id="p" vClass="passenger"/><vType id="b" vClass="bus"/>'
                      '<vehicle id="passenger" type="p" depart="0"><route edges="a b c"/></vehicle>'
                      '<vehicle id="bus" type="b" depart="30"><route edges="a b c"/></vehicle></routes>',encoding='utf-8')
    trips,summary=tmp_path/'trips.xml',tmp_path/'summary.xml'
    driven=subprocess.run(['sumo','-n',str(candidate),'-r',str(routes),'--end','600','--no-step-log','true',
        '--collision.check-junctions','true','--tripinfo-output',str(trips),'--summary-output',str(summary)],capture_output=True,text=True)
    assert driven.returncode==0,driven.stderr
    assert {row.get('id') for row in ET.parse(trips).getroot()}=={'passenger','bus'}
    last=ET.parse(summary).getroot()[-1]
    assert last.get('arrived')=='2' and last.get('collisions')==last.get('teleports')=='0'


def test_default_service_policy_does_not_create_or_modify_a_candidate(tmp_path):
    source=tmp_path/'source.net.xml'
    source.write_text('<net/>',encoding='utf-8')
    result=apply_service_passenger_permissions(source,policy='sumo_default',output_dir=tmp_path/'unused')
    assert result['status']=='pass'
    assert result['net_file']==str(source.resolve())
    assert not (tmp_path/'unused').exists()


@pytest.mark.parametrize('attributes', [{'allow':'all'}, {'disallow':'all'}, {'allow':'bus bicycle'}, {'disallow':'passenger'}])
def test_permission_patch_adds_only_passenger(attributes):
    from torii_sumo.core.network_permissions import _add_passenger_to_lane
    lane=ET.Element('lane',attributes)
    before=_permission_set(lane.attrib)
    changed=_add_passenger_to_lane(lane)
    assert _permission_set(lane.attrib)==before|{'passenger'}
    assert changed==('passenger' not in before)
