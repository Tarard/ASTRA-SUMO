import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from astra_sumo.core.hamburg_spillback_repair import remove_deleted_service_stage
from astra_sumo.core.hamburg_spillback_repair import prepare_hamburg_merge_coordination
from astra_sumo.core.hamburg_collision_trace import read_trace_source


def test_removed_service_keeps_every_clearance_and_retained_green():
    old = ET.fromstring('<tlLogic id="a" type="actuated"><phase duration="27" state="Gr" minDur="5" maxDur="50"/>'
        '<phase duration="3" state="yr"/><phase duration="27" state="rG" minDur="5" maxDur="50"/>'
        '<phase duration="3" state="ry"/><phase duration="2" state="rr"/></tlLogic>')
    new = ET.fromstring(ET.tostring(old))
    new.findall('phase')[2].set('state','rr')
    new.findall('phase')[3].set('state','rr')
    original = ET.tostring(new)
    result, removed = remove_deleted_service_stage(old,new)
    assert removed[0]['source_phase_index'] == 2
    assert [p.attrib for p in result] == [p.attrib for i,p in enumerate(new) if i != 2]
    assert ET.tostring(new) == original


def test_does_not_remove_original_clearance_or_alter_existing_service():
    program = ET.fromstring('<tlLogic id="a" type="actuated"><phase duration="3" state="rr"/>'
        '<phase duration="20" state="Gr"/></tlLogic>')
    with pytest.raises(ValueError,match='newly empty'):
        remove_deleted_service_stage(program,program)


def test_real_local_coordination_keeps_every_request_target_and_other_program(tmp_path):
    case=Path(__file__).parents[1]/'benchmarks/hamburg_ring1_munich_corridor_20261022_v1'
    result=prepare_hamburg_merge_coordination(case_dir=case,output_dir=tmp_path/'candidate')
    assert result['coordinated_green_windows_s']=={'A':(12,73),'B':(80,96)}
    assert result['other_programs_byte_identical']
    for name in ('network.net.xml','demand.rou.xml','lane-intentions.json','field-loops.add.xml','expected-counts.csv','initialization-counts.csv'):
        assert (tmp_path/'candidate'/name).read_bytes()==read_trace_source(case/'inputs',name)
    program=ET.parse(tmp_path/'candidate/control.add.xml').getroot().find("tlLogic[@id='GS_266199063']")
    phases=program.findall('phase')
    assert sum(float(p.get('duration')) for p in phases)==90
    assert all(set(p.get('state'))<=set('Gry') for p in phases)
    assert sum(float(p.get('duration')) for p in phases if 'y' in p.get('state'))==6
    assert all(not ('G' in p.get('state')[:2] and 'G' in p.get('state')[2:]) for p in phases)
