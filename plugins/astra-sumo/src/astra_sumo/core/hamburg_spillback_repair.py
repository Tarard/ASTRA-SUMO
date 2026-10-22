"""Remove a deleted approach's service stage from one pruned Ring1 controller."""

from copy import deepcopy
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .hamburg_collision_trace import read_trace_source


def remove_deleted_service_stage(source_program, projected_program):
    """Retain yellow and all-red clearance while dropping newly empty green service."""
    if source_program.get('id') != projected_program.get('id') or projected_program.get('type') != 'actuated':
        raise ValueError('Match the source identity of the projected actuated controller.')
    old, current = source_program.findall('phase'), projected_program.findall('phase')
    if len(old) != len(current) or any(p.get('next') for p in current):
        raise ValueError('Use the original ordered sequence without conditional jumps.')
    candidate = deepcopy(projected_program)
    removed = []
    for index, (before, after) in enumerate(zip(old, current)):
        if {k:v for k,v in before.attrib.items() if k != 'state'} != {k:v for k,v in after.attrib.items() if k != 'state'}:
            raise ValueError('The projected phase timing no longer matches its source.')
        if any(color in 'Gg' for color in before.get('state')) and set(after.get('state')) == {'r'}:
            removed.append({'source_phase_index': index, 'source_state': before.get('state'),
                'projected_state': after.get('state'), 'duration_s': float(after.get('duration'))})
            candidate.remove(candidate.findall('phase')[index - len(removed) + 1])
    if not removed or not any('G' in p.get('state') or 'g' in p.get('state') for p in candidate.findall('phase')):
        raise ValueError('Require a newly empty service stage and retained vehicle service.')
    return candidate, removed


def prepare_hamburg_pruned_phase_repair(*, case_dir, source_control, output_dir):
    """Change only controller 266197525; keep every request, target and other program."""
    case, out = Path(case_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or case.is_relative_to(out) or out.is_relative_to(case):
        raise ValueError('Use a new repair directory separate from the source case.')
    names = ('network.net.xml','demand.rou.xml','field-loops.add.xml','control.add.xml',
             'lane-intentions.json','initialization-counts.csv','expected-counts.csv','scenario.json')
    raw = {name:read_trace_source(case / 'inputs',name) for name in names}
    control = ET.fromstring(raw['control.add.xml'])
    original = ET.parse(source_control).getroot()
    identity = '266197525'
    source = original.find(f"tlLogic[@id='{identity}']")
    current = control.find(f"tlLogic[@id='{identity}']")
    if source is None or current is None:
        raise ValueError('Retain the identified downstream controller in both sources.')
    candidate, removed = remove_deleted_service_stage(source, current)
    if [row['source_phase_index'] for row in removed] != [2]:
        raise ValueError('Reassess the local repair when the source projection changes.')
    before = {p.get('id'):ET.tostring(p) for p in control if p.get('id') != identity}
    index = list(control).index(current)
    control.remove(current)
    control.insert(index,candidate)
    assert before == {p.get('id'):ET.tostring(p) for p in control if p.get('id') != identity}
    out.mkdir(parents=True)
    for name,data in raw.items():
        (out/name).write_bytes(data)
    ET.ElementTree(control).write(out/'control.add.xml',encoding='utf-8',xml_declaration=True)
    scenario = json.loads(raw['scenario.json'])
    scenario.update(control_sha256=file_sha256(out/'control.add.xml'),
        control_variant='one_deleted_service_stage_removed',
        control_assumptions=scenario['control_assumptions'] + ' At 266197525 only, remove the original phase-2 green-service stage whose controlled links were all excluded by corridor pruning. Retain every other phase, including all yellow and all-red clearance, and all other controllers. This corrects the declared pruned program, not measured field SPaT.')
    write_json_atomic(out/'scenario.json',scenario)
    report = {'formal_result':False,'native_verified':False,'changed_controller':identity,'removed_stages':removed,
        'other_programs_byte_identical':True,'all_yellow_and_all_red_clearance_retained':True,
        'network_demand_targets_and_driver_unchanged':True,
        'source_control_sha256':file_sha256(Path(source_control)),
        'candidate_control_sha256':file_sha256(out/'control.add.xml'),
        'claim_boundary':'Local correction of a newly empty service stage after pruning. Native spillback, all-field effects and safety require a new paired measurement. No source-supported field binding is inferred.'}
    write_json_atomic(out/'spillback-repair-proposal.json',report)
    return report


def prepare_hamburg_merge_coordination(*, case_dir, output_dir):
    """Coordinate the one merge with upstream releases and measured short transit times."""
    case, out = Path(case_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or case.is_relative_to(out) or out.is_relative_to(case):
        raise ValueError('Use a new coordination directory separate from its source.')
    names = ('network.net.xml','demand.rou.xml','field-loops.add.xml','control.add.xml',
             'lane-intentions.json','initialization-counts.csv','expected-counts.csv','scenario.json')
    raw = {name:read_trace_source(case / 'inputs',name) for name in names}
    control = ET.fromstring(raw['control.add.xml'])
    collisions = ET.fromstring(read_trace_source(case/'native','collisions.xml')).findall('collision')
    first_collision = min(float(row.get('time')) for row in collisions)
    intent = {r['vehicle_id']:r['field_id'] for r in json.loads(raw['lane-intentions.json'])['vehicles']}
    road_pairs = {'hh_0119_Z_15_29375':('603103445#0','603103445#2'),
        'hh_0119_Z_4_29437':('24483344#1','79014037#1'),
        'hh_0119_Z_5_30494':('24483344#1','79014037#1')}
    fastest = {}
    for vehicle in ET.fromstring(read_trace_source(case/'native','vehicle-routes.xml')).findall('vehicle'):
        field = intent[vehicle.get('id')]
        if field not in road_pairs:
            continue
        route = vehicle.find('route')
        edges = route.get('edges').split()
        exits = list(map(float,route.get('exitTimes').split()))
        first,last = [exits[edges.index(edge)] for edge in road_pairs[field]]
        if last >= first_collision or last <= first:
            continue
        transit = last-first
        if field not in fastest or transit < fastest[field]['transit_s']:
            fastest[field] = {'vehicle_id':vehicle.get('id'),'upstream_exit_s':first,
                'downstream_exit_s':last,'transit_s':transit}
    if fastest.keys() != road_pairs.keys():
        raise ValueError('Require pre-collision native transit records for all three affected fields.')
    def green_window(identity,link):
        program = control.find(f"tlLogic[@id='{identity}']")
        assert program.get('type')=='static' and float(program.get('offset','0'))==0
        clock,spans = 0.,[]
        for phase in program.findall('phase'):
            end = clock+float(phase.get('duration'))
            if phase.get('state')[link] in 'Gg':
                spans.append((clock,end))
            clock=end
        assert clock==90
        return min(a for a,b in spans),max(b for a,b in spans)
    a = green_window('6390683126',0)
    b = green_window('LSA119_part0',12)
    assert a==(11.,71.) and b==(75.,89.) and green_window('LSA119_part0',13)==b
    d_a = fastest['hh_0119_Z_15_29375']['transit_s']
    d_b = [fastest[field]['transit_s'] for field in ('hh_0119_Z_4_29437','hh_0119_Z_5_30494')]
    start_a,end_a = math.floor(a[0]+d_a),math.ceil(a[1]+d_a)
    start_b,end_b = math.floor(b[0]+min(d_b)),math.ceil(b[1]+max(d_b))
    assert 0<start_a<end_a and end_a+3<=start_b<90<end_b and end_b-90+3<=start_a
    identity = 'GS_266199063'
    current = control.find(f"tlLogic[@id='{identity}']")
    if [p.get('state') for p in current.findall('phase')] != ['GGrrrr','yyrrrr','rrGGGG','rryyyy']:
        raise ValueError('Reassess coordination after any merge movement or phase-state change.')
    candidate = ET.Element('tlLogic',id=identity,type='static',programID=current.get('programID'),offset='0')
    spans = [(end_b-90,'rrGGGG'),(3,'rryyyy'),(start_a-(end_b-90+3),'rrrrrr'),
        (end_a-start_a,'GGrrrr'),(3,'yyrrrr'),(start_b-end_a-3,'rrrrrr'),(90-start_b,'rrGGGG')]
    assert sum(duration for duration,state in spans)==90 and all(duration>0 for duration,state in spans)
    for duration,state in spans:
        ET.SubElement(candidate,'phase',duration=str(duration),state=state)
    before = {p.get('id'):ET.tostring(p) for p in control if p.get('id')!=identity}
    index=list(control).index(current)
    control.remove(current)
    control.insert(index,candidate)
    assert before=={p.get('id'):ET.tostring(p) for p in control if p.get('id')!=identity}
    out.mkdir(parents=True)
    for name,data in raw.items():
        (out/name).write_bytes(data)
    ET.ElementTree(control).write(out/'control.add.xml',encoding='utf-8',xml_declaration=True)
    scenario=json.loads(raw['scenario.json'])
    scenario.update(control_sha256=file_sha256(out/'control.add.xml'),control_variant='native_transit_merge_coordination',
        control_assumptions=scenario['control_assumptions']+' GS_266199063 alone uses a declared 90 s coordinated placeholder: the same two exclusive vehicle service states and 3 s yellows, with additional all-red clearance. Green windows follow the existing upstream K9-A and K2 releases shifted by pre-collision native minimum transit. No real control-domain binding or field SPaT is asserted.')
    write_json_atomic(out/'scenario.json',scenario)
    report={'formal_result':False,'native_verified':False,'changed_controller':identity,'first_source_collision_s':first_collision,
        'native_fastest_transit':fastest,'upstream_windows_s':{'A':a,'B':b},
        'coordinated_green_windows_s':{'A':(start_a,end_a),'B':(start_b,end_b)},
        'phases':spans,'other_programs_byte_identical':True,'network_demand_targets_and_driver_unchanged':True,
        'claim_boundary':'A local coordinated placeholder candidate, not source-confirmed real signal timing or binding. The native minimum transit is a proposal input, not a prediction under changed control. A new native comparison is required.'}
    write_json_atomic(out/'merge-coordination-proposal.json',report)
    return report
