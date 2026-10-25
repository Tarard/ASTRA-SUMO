"""Restore one frozen Alsterglacis branch without rebuilding the current corridor."""

from copy import deepcopy
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .hamburg_corridor_pruning import map_retained_control
from .local_network_conversion import loaded_turn_permissions
from .network_geometry_freeze import prepare_geometry_frozen_input
from .road_hierarchy import _internal_transition_lengths, _network, materialize_road_selection
from .sumo_commands import run_sumo_load_audit


ADDED_ROADS = frozenset({'1108564798#0', '1108564798#1'})
LOCAL_NODES = frozenset({'266197525', '6390683113', '1562406613'})
OLD_TAIL = ('73474895', '27064186#0', '1073054122#1', '1345098540', '24483197', '199500676')
NEW_TAIL = ('73474895', '1108564798#0', '1108564798#1', '24483197', '199500676')


def branch_cost_evidence(root):
    """Expose the ordinary-road shortcut and the complete native path lengths."""
    roads, graph, lengths = _network(root, 'passenger')
    transitions = _internal_transition_lengths(root, 'passenger')
    rows = {}
    for name, path in [('primary', NEW_TAIL[:-1]), ('residential', OLD_TAIL[:-1])]:
        if any(last not in graph.get(first, ()) for first, last in zip(path, path[1:])):
            raise ValueError('Both source branch paths must retain passenger permission.')
        ordinary = sum(lengths[edge] for edge in path[1:-1])
        internal = sum(transitions[first, last] for first, last in zip(path, path[1:]))
        rows[name] = {'path': list(path), 'ordinary_intermediate_m': ordinary,
                      'internal_m': internal, 'complete_intermediate_m': ordinary + internal}
    rows['ordinary_only_choice'] = min(('primary', 'residential'), key=lambda k: rows[k]['ordinary_intermediate_m'])
    rows['complete_path_choice'] = min(('primary', 'residential'), key=lambda k: rows[k]['complete_intermediate_m'])
    return rows


def replace_primary_tails(demand):
    """Keep each request and observation approach, changing only the declared tail."""
    result, changed = deepcopy(demand), []
    for vehicle in result.findall('vehicle'):
        route = vehicle.find('route')
        edges = route.get('edges').split()
        if OLD_TAIL[0] not in edges:
            continue
        index = edges.index(OLD_TAIL[0])
        if edges.count(OLD_TAIL[0]) != 1 or tuple(edges[index:]) != OLD_TAIL:
            raise ValueError('Recheck the exact reviewed residential route suffix.')
        route.set('edges', ' '.join(edges[:index] + list(NEW_TAIL)))
        changed.append(vehicle.get('id'))
    return result, changed


def _owned(row, roads):
    def internal(identity):
        return any((identity or '').startswith(f':{node}_') for node in LOCAL_NODES)
    if row.tag == 'edge':
        return internal(row.get('id'))
    if row.tag == 'junction':
        return row.get('id') in LOCAL_NODES or internal(row.get('id'))
    if row.tag == 'tlLogic':
        return row.get('id') in LOCAL_NODES
    if row.tag == 'connection':
        first = row.get('from')
        return internal(first) or first in roads and roads[first].get('to') in LOCAL_NODES
    return False


def _transplant(current, projected, source):
    """Copy only the three projected junction domains and two exact source roads."""
    old_roads = {r.get('id'): r for r in current.findall('edge') if not r.get('function')}
    donor_roads = {r.get('id'): r for r in projected.findall('edge') if not r.get('function')}
    source_roads = {r.get('id'): r for r in source.findall('edge') if not r.get('function')}
    if ADDED_ROADS & old_roads.keys() or donor_roads.keys() != old_roads.keys() | ADDED_ROADS:
        raise ValueError('Restore exactly the two absent primary roads.')
    result = deepcopy(current)
    for tag in ('edge', 'tlLogic', 'junction', 'connection'):
        removed = [r for r in result.findall(tag) if _owned(r, old_roads)]
        replacements = [deepcopy(r) for r in projected.findall(tag) if _owned(r, donor_roads)]
        if tag == 'edge':
            replacements += [deepcopy(source_roads[identity]) for identity in sorted(ADDED_ROADS)]
        position = min((list(result).index(r) for r in removed), default=
            max((list(result).index(r) for r in result.findall(tag)), default=len(result) - 1) + 1)
        for row in removed:
            result.remove(row)
        for index, row in enumerate(replacements):
            result.insert(position + index, row)
    return result


def _preservation(current, candidate):
    old_roads = {r.get('id'): r for r in current.findall('edge') if not r.get('function')}
    new_roads = {r.get('id'): r for r in candidate.findall('edge') if not r.get('function')}
    old = loaded_turn_permissions(current)
    new = loaded_turn_permissions(candidate)
    lost = [list(key) for key, modes in old.items() if modes != new.get(key)]
    added_old = [list(key) for key in new.keys() - old.keys() if key[0] in old_roads and key[2] in old_roads]
    protected_old = [ET.tostring(r) for r in current if not _owned(r, old_roads)]
    protected_new = [ET.tostring(r) for r in candidate
                     if not _owned(r, new_roads) and not (r.tag == 'edge' and r.get('id') in ADDED_ROADS)]
    checks = {
        'old_ordinary_roads_identical': all(ET.tostring(row) == ET.tostring(new_roads.get(key))
                                           for key, row in old_roads.items()),
        'exact_two_added_roads': new_roads.keys() - old_roads.keys() == ADDED_ROADS,
        'all_old_lane_turn_permissions_identical': not lost,
        'no_added_turn_between_old_roads': not added_old,
        'all_outside_scope_elements_identical': protected_old == protected_new,
    }
    return {'checks': checks, 'old_road_count': len(old_roads), 'candidate_road_count': len(new_roads),
            'old_lane_turn_count': len(old), 'candidate_lane_turn_count': len(new),
            'lost_or_changed_lane_turns': lost, 'added_turns_between_old_roads': added_old,
            'new_lane_turns': [list(key) for key in sorted(new.keys() - old.keys())]}


def prepare_hamburg_primary_branch(*, repository, prepared_dir, evaluation_dir, output_dir,
                                   netconvert_binary, sumo_binary):
    """Build one paired 8640-request candidate. Do not execute its full demand."""
    repo, prepared, evaluated = [Path(p).resolve(strict=True) for p in (repository, prepared_dir, evaluation_dir)]
    out = Path(output_dir).resolve()
    if out.exists() or any(p.is_relative_to(out) or out.is_relative_to(p) for p in (prepared, evaluated)):
        raise ValueError('Use a new candidate directory separate from both paired inputs.')
    source = repo / 'benchmarks/hamburg_ring1_primary_entry_full_20261022_v1/inputs'
    files = [prepared / name for name in ('network.net.xml', 'control.add.xml', 'field-loops.add.xml',
              'expected-counts.csv', 'initialization-counts.csv', 'scenario.json')]
    files += [evaluated / name for name in ('demand.rou.xml', 'lane-intentions.json')]
    files += [source / name for name in ('network.net.xml', 'control.add.xml')]
    hashes = {str(path): file_sha256(path) for path in files}
    current, full = [ET.parse(path).getroot() for path in (prepared / 'network.net.xml', source / 'network.net.xml')]
    old_roads, _, _ = _network(current, 'passenger')
    source_roads, _, _ = _network(full, 'passenger')
    if len(old_roads) != 236 or not old_roads.keys() <= source_roads.keys() or ADDED_ROADS & old_roads.keys():
        raise ValueError('Use the current 236-road corridor without the two source primary roads.')
    out.mkdir(parents=True)
    evidence = branch_cost_evidence(full)
    write_json_atomic(out / 'branch-cost-evidence.json', evidence)
    plan = json.loads((repo / 'benchmarks/hamburg_ring1_munich_corridor_20261022_v1/selection.json').read_bytes())
    selected = old_roads.keys() | ADDED_ROADS
    plan.update(selected_edge_ids=sorted(selected), excluded_edge_ids=sorted(source_roads.keys() - selected),
                selected_road_count=len(selected), source_road_count=len(source_roads))
    for row in plan['roads']:
        if row['edge_id'] in ADDED_ROADS:
            row.update(selected=True, reasons=['source_primary_branch_restoration'])
    frozen = out / 'frozen-source.net.xml'
    prepare_geometry_frozen_input(source_file=source / 'network.net.xml', output_file=frozen)
    projection = materialize_road_selection(net_file=frozen, output_dir=out / 'source-projection', selection=plan,
        netconvert_binary=netconvert_binary, sumo_binary=sumo_binary)
    if projection['status'] != 'measured':
        raise ValueError('Source projection failed. Inspect the retained source-projection report.')
    donor_path = Path(projection['candidate']['path'])
    donor = ET.parse(donor_path).getroot()
    candidate = _transplant(current, donor, full)
    preservation = _preservation(current, candidate)
    write_json_atomic(out / 'preservation.json', preservation)
    if not all(preservation['checks'].values()):
        raise ValueError('The local transplant changed protected roads, turns or outside-scope elements.')
    candidate_dir = out / 'prepared'
    candidate_dir.mkdir()
    for name in ('field-loops.add.xml', 'expected-counts.csv', 'initialization-counts.csv'):
        (candidate_dir / name).write_bytes((prepared / name).read_bytes())
    ET.ElementTree(candidate).write(candidate_dir / 'network.net.xml', encoding='utf-8', xml_declaration=True)
    projected_control = out / 'source-projection-control.add.xml'
    map_retained_control(source_net=source / 'network.net.xml', candidate_net=donor_path,
        source_control=source / 'control.add.xml', output_file=projected_control)
    control = ET.parse(prepared / 'control.add.xml').getroot()
    mapped = ET.parse(projected_control).getroot()
    for identity in sorted(LOCAL_NODES):
        old = control.find(f"tlLogic[@id='{identity}']")
        new = mapped.find(f"tlLogic[@id='{identity}']")
        if new is None:
            raise ValueError('Every restored junction requires its projected source program.')
        position = list(control).index(old) if old is not None else len(control)
        if old is not None:
            control.remove(old)
        control.insert(position, deepcopy(new))
    old_control = ET.parse(prepared / 'control.add.xml').getroot()
    assert [ET.tostring(r) for r in old_control if r.get('id') not in LOCAL_NODES] == [
        ET.tostring(r) for r in control if r.get('id') not in LOCAL_NODES]
    ET.ElementTree(control).write(candidate_dir / 'control.add.xml', encoding='utf-8', xml_declaration=True)
    demand = ET.parse(evaluated / 'demand.rou.xml').getroot()
    intentions = json.loads((evaluated / 'lane-intentions.json').read_bytes())
    ids = {v.get('id') for v in demand.findall('vehicle')}
    if len(ids) != len(demand.findall('vehicle')) or len(ids) != 8640 or ids != {r['vehicle_id'] for r in intentions['vehicles']}:
        raise ValueError('Retain the original 8640 distinct requests and every field intention.')
    replacement, changed = replace_primary_tails(demand)
    fields = {r['vehicle_id']: r['field_id'] for r in intentions['vehicles']}
    if len(changed) != 2589 or {fields[key] for key in changed} != {
            'hh_0119_Z_15_29375', 'hh_0119_Z_4_29437', 'hh_0119_Z_5_30494'}:
        raise ValueError('Recheck the original three-field 2589-request tail population.')
    roads, graph, _ = _network(candidate, 'passenger')
    for vehicle in replacement.findall('vehicle'):
        edges = vehicle.find('route').get('edges').split()
        if not set(edges) <= roads.keys() or any(b not in graph[a] for a, b in zip(edges, edges[1:])):
            raise ValueError(f"A candidate vehicle has a disconnected route: {vehicle.get('id')}")
    ET.ElementTree(replacement).write(candidate_dir / 'demand.rou.xml', encoding='utf-8', xml_declaration=True)
    (candidate_dir / 'lane-intentions.json').write_bytes((evaluated / 'lane-intentions.json').read_bytes())
    scenario = json.loads((prepared / 'scenario.json').read_bytes())
    scenario.update(network_sha256=file_sha256(candidate_dir / 'network.net.xml'),
        control_sha256=file_sha256(candidate_dir / 'control.add.xml'),
        demand_sha256=file_sha256(candidate_dir / 'demand.rou.xml'),
        branch_treatment='Restore two frozen Alsterglacis primary roads and their source control projection. '
        'Move all 2589 existing residential tails to this source-supported primary path. '
        'This is a route sensitivity, not an observed field split or field SPaT.')
    write_json_atomic(candidate_dir / 'scenario.json', scenario)
    load = run_sumo_load_audit(net_file=candidate_dir / 'network.net.xml', output_dir=out / 'sumo-load',
                               sumo_binary=str(sumo_binary))
    report = {'schema': 'astra.hamburg-primary-branch-repair/v1', 'status': 'prepared', 'formal_result': False,
        'source_hashes': hashes, 'source_unchanged': all(file_sha256(Path(p)) == h for p, h in hashes.items()),
        'prepared_dir': str(candidate_dir), 'local_junctions': sorted(LOCAL_NODES), 'added_roads': sorted(ADDED_ROADS),
        'preservation': preservation, 'other_programs_identical': True, 'field_intentions_identical': True,
        'vehicle_count': len(ids), 'changed_route_count': len(changed), 'changed_vehicle_ids': changed,
        'old_tail': list(OLD_TAIL), 'new_tail': list(NEW_TAIL), 'sumo_load': load,
        'candidate_hashes': {p.name: file_sha256(p) for p in candidate_dir.iterdir()},
        'claim_boundary': scenario['branch_treatment'] + ' Full-demand counts, spillback and safety remain unmeasured.'}
    if load['status'] != 'pass' or not report['source_unchanged']:
        report['status'] = 'blocked'
    write_json_atomic(out / 'repair-proposal.json', report)
    return report
