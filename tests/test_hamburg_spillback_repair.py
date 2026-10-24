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


@pytest.mark.parametrize('preclear',[False,True])
def test_real_local_coordination_keeps_every_request_target_and_other_program(tmp_path,preclear):
    case=Path(__file__).parents[1]/'benchmarks/hamburg_ring1_munich_corridor_20261022_v1'
    result=prepare_hamburg_merge_coordination(case_dir=case,output_dir=tmp_path/'candidate',preclear_receivers=preclear)
    assert result['coordinated_green_windows_s']==({'A':(12,71),'B':(75,96)} if preclear else {'A':(12,73),'B':(80,96)})
    assert result['other_programs_byte_identical']
    for name in ('network.net.xml','demand.rou.xml','lane-intentions.json','field-loops.add.xml','expected-counts.csv','initialization-counts.csv'):
        assert (tmp_path/'candidate'/name).read_bytes()==read_trace_source(case/'inputs',name)
    program=ET.parse(tmp_path/'candidate/control.add.xml').getroot().find("tlLogic[@id='GS_266199063']")
    phases=program.findall('phase')
    assert sum(float(p.get('duration')) for p in phases)==90
    assert all(set(p.get('state'))<=set('Gry') for p in phases)
    assert sum(float(p.get('duration')) for p in phases if 'y' in p.get('state'))==6
    assert all(not ('G' in p.get('state')[:2] and 'G' in p.get('state')[2:]) for p in phases)
def test_replay_frozen_merge_changes_only_one_declared_program():
    import gzip
    from pathlib import Path
    import xml.etree.ElementTree as ET
    from astra_sumo.core.hamburg_spillback_repair import replay_frozen_merge_program

    repo = Path(__file__).resolve().parents[1]
    source = ET.fromstring(gzip.decompress((repo / "benchmarks/hamburg_ring1_119_straight_order_20261023_v1/full-native/signal-programs.add.xml.gz").read_bytes()))
    frozen = ET.parse(repo / "benchmarks/hamburg_ring1_spillback_repair_20261022_v1/proposals/merge_coordinated/control.add.xml").getroot()
    raw = ET.tostring(source)
    candidate = replay_frozen_merge_program(source, frozen)
    assert ET.tostring(source) == raw
    old, new = ({p.get("id"): ET.tostring(p) for p in root.findall("tlLogic")} for root in (source, candidate))
    assert {key for key in old if old[key] != new[key]} == {"GS_266199063"}
    program = candidate.find("tlLogic[@id='GS_266199063']")
    assert program.get("type") == "static"
    assert sum(float(p.get("duration")) for p in program.findall("phase")) == 90
    assert ET.tostring(program) == ET.tostring(frozen.find("tlLogic[@id='GS_266199063']"))


def test_merge_tail_extension_keeps_cycle_clearance_and_other_programs():
    from astra_sumo.core.hamburg_spillback_repair import extend_merge_tail

    control = ET.parse(Path(__file__).resolve().parents[1] / 'benchmarks/hamburg_ring1_spillback_repair_20261022_v1/proposals/merge_coordinated/control.add.xml').getroot()
    before = ET.tostring(control)
    result = extend_merge_tail(control, 18)
    assert ET.tostring(control) == before
    assert {a.get('id') for a, b in zip(control, result) if ET.tostring(a) != ET.tostring(b)} == {'GS_266199063'}
    phases = result.find("tlLogic[@id='GS_266199063']").findall('phase')
    assert [float(x.get('duration')) for x in phases] == [24, 3, 3, 43, 3, 4, 10]
    assert sum(float(x.get('duration')) for x in phases) == 90
    for value in (0, 61, float('nan')):
        with pytest.raises(ValueError):
            extend_merge_tail(control, value)


@pytest.mark.parametrize("change", ["missing", "timing", "states"])
def test_frozen_merge_replay_rejects_a_different_candidate(change):
    from astra_sumo.core.hamburg_spillback_repair import replay_frozen_merge_program

    old = ET.fromstring('<additional><tlLogic id="GS_266199063" type="actuated" programID="a">'
        '<phase duration="42" state="GGrrrr"/><phase duration="3" state="yyrrrr"/>'
        '<phase duration="42" state="rrGGGG"/><phase duration="3" state="rryyyy"/></tlLogic></additional>')
    frozen = ET.parse(Path(__file__).resolve().parents[1] / 'benchmarks/hamburg_ring1_spillback_repair_20261022_v1/proposals/merge_coordinated/control.add.xml').getroot()
    program = frozen.find("tlLogic[@id='GS_266199063']")
    program.set('programID', 'a')
    if change == 'missing':
        frozen.remove(program)
    else:
        program.findall('phase')[0].set('duration' if change == 'timing' else 'state', '7' if change == 'timing' else 'GGGGGG')
    with pytest.raises(ValueError):
        replay_frozen_merge_program(old, frozen)
