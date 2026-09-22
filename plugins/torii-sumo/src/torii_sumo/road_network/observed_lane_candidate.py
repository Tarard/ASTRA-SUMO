"""Compile explicit observed lane layouts without changing the frozen source."""

import json
import math
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

from ..core.candidate_contracts import file_sha256
from ..core.command_runner import run_command
from ..core.connection_mode_audit import audit_network_connection_mode
from ..core.hamburg_aerial_approach import (
    _CONNECTION_ATTRIBUTES, _LANE_ATTRIBUTES, _connection_key, _preserved_edge, _set_lane_shape,
)
from ..core.osm_access import _permission_set


def _edge_attributes_preserved(expected, actual):
    """Allow adjacent endpoint reshaping, while retaining all assessed lane attributes."""
    if actual is None or any(expected.get(k) != actual.get(k) for k in ('from', 'to', 'priority', 'type', 'spreadType')):
        return False
    before, after = expected.findall('lane'), actual.findall('lane')
    if len(before) != len(after):
        return False
    for old, new in zip(before, after):
        if _permission_set(old.attrib) != _permission_set(new.attrib):
            return False
        for key in _LANE_ATTRIBUTES:
            if key in ('allow', 'disallow'):
                continue
            left, right = old.get(key), new.get(key)
            if key in ('width', 'speed'):
                left = float(left) if left is not None else 3.2 if key == 'width' else None
                right = float(right) if right is not None else 3.2 if key == 'width' else None
                if left is not None and right is not None and math.isclose(left, right, rel_tol=0, abs_tol=1e-6):
                    continue
            if left != right:
                return False
        if not {(p.get('key'), p.get('value')) for p in old.findall('param')} <= {
                (p.get('key'), p.get('value')) for p in new.findall('param')}:
            return False
    return True


def _signal_signature(logic):
    times = {'duration', 'minDur', 'maxDur', 'earliestEnd', 'latestEnd'}
    return dict(type=logic.get('type', 'static'), offset=float(logic.get('offset', '0')),
                phases=[{k: float(v) if k in times else v for k, v in p.attrib.items()}
                        for p in logic.findall('phase')],
                parameters=sorted((p.get('key'), p.get('value')) for p in logic.findall('param')))


def _control_binding(attributes):
    return attributes.get('tl'), attributes.get('linkIndex'), attributes.get('linkIndex2')


def build_observed_lane_candidate(*, source_net, layouts, junction_movements, output_dir,
                                  node_shapes=None, removed_edges=None, netconvert_binary='netconvert'):
    """Layouts list every lane right-to-left with its old index or None for additions.

    Each lane declares shape, width_m, allow and evidence. Junction movements
    replace the complete local connection set and use NEW lane indices. Signals
    are independent diagnostic phases, one movement at a time, never field timing.
    """
    source = Path(source_net).resolve(strict=True)
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError('output_dir must not already exist')
    source_hash = file_sha256(source)
    root = ET.parse(source).getroot()
    edges = {e.get('id'): e for e in root.findall('edge') if e.get('function') != 'internal'}
    junctions = {j.get('id'): j for j in root.findall('junction')}
    if not layouts or not junction_movements:
        raise ValueError('Declare lane layouts and complete junction movements')
    if set(layouts) - edges.keys() or set(junction_movements) - junctions.keys():
        raise ValueError('Unknown edge or junction')
    removed_edges = removed_edges or {}
    if set(removed_edges) - edges.keys() or set(removed_edges) & set(layouts) or any(not v for v in removed_edges.values()):
        raise ValueError('Removed edges must exist, have evidence, and not have a layout')
    affected_nodes = set(junction_movements)
    edge_patch, con_patch, tls_patch, node_patch, type_patch = [ET.Element(t) for t in ('edges', 'connections', 'tlLogics', 'nodes', 'types')]
    for edge_id in removed_edges:
        ET.SubElement(edge_patch, 'delete', {'id': edge_id})
        affected_nodes.update((edges[edge_id].get('from'), edges[edge_id].get('to')))
    remaps = {}
    for edge_id, rows in layouts.items():
        original = edges[edge_id]
        affected_nodes.update((original.get('from'), original.get('to')))
        old = original.findall('lane')
        if not rows:
            raise ValueError('Each layout needs at least one lane')
        remaps[edge_id] = {}
        patch = deepcopy(original)
        patch.set('numLanes', str(len(rows)))
        for child in patch.findall('lane'):
            patch.remove(child)
        for i, row in enumerate(rows):
            donor = row.get('origin_lane_index')
            if donor is not None and (type(donor) is not int or donor < 0 or donor >= len(old) or donor in remaps[edge_id]):
                raise ValueError('Old lane indices must exist and be unique')
            width = row.get('width_m')
            shape = row.get('shape', [])
            if (isinstance(width, bool) or not isinstance(width, (int, float)) or not math.isfinite(width) or width <= 0
                    or len(shape) < 2 or any(len(p) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in p) for p in shape)):
                raise ValueError('Each lane needs finite geometry and positive width')
            if not row.get('evidence') or not isinstance(row.get('allow'), str) or not row['allow'].strip():
                raise ValueError('Each lane needs declared permissions and evidence')
            lane = deepcopy(old[donor]) if donor is not None else ET.Element('lane', {'speed': '5.56'})
            _set_lane_shape(lane, i, shape)
            lane.attrib.pop('disallow', None)
            lane.set('allow', row['allow'])
            lane.set('width', str(width))
            patch.append(lane)
            if donor is not None:
                remaps[edge_id][donor] = i
        # An omitted old lane needs an explicit network-wide removal policy.
        if set(remaps[edge_id]) != set(range(len(old))):
            raise ValueError('Retain each old lane identity exactly once')
        edge_patch.append(patch)
    for node_id in affected_nodes:
        node = junctions[node_id]
        attrs = {k: node.get(k) for k in ('id', 'x', 'y', 'z', 'type', 'shape') if node.get(k) is not None}
        if node_id in (node_shapes or {}):
            attrs['shape'] = ' '.join(f'{x},{y}' for x, y in node_shapes[node_id])
        ET.SubElement(node_patch, 'node', attrs)
    expected = {}
    replacements = {}
    for junction_id, movements in junction_movements.items():
        if not movements:
            raise ValueError('A junction movement set cannot be empty')
        tls = ET.SubElement(tls_patch, 'tlLogic', {'id': junction_id, 'type': 'static', 'programID': 'torii-geometry', 'offset': '0'})
        for i, movement in enumerate(movements):
            attrs = {k: str(movement[k]) for k in ('from', 'to', 'fromLane', 'toLane')}
            if attrs['from'] in removed_edges or attrs['to'] in removed_edges:
                raise ValueError('A movement cannot use a removed edge')
            if not movement.get('evidence'):
                raise ValueError('Each new movement needs evidence')
            for side, endpoint in (('from', 'to'), ('to', 'from')):
                edge = edges.get(attrs[side])
                count = len(layouts[attrs[side]]) if attrs[side] in layouts else len(edge.findall('lane')) if edge is not None else 0
                index = int(attrs[side + 'Lane'])
                if edge is None or edge.get(endpoint) != junction_id or index < 0 or index >= count:
                    raise ValueError('Movement endpoints or new lane indices do not match the junction')
            attrs.update(tl=junction_id, linkIndex=str(i))
            if movement.get('shape'):
                attrs['shape'] = ' '.join(f'{x},{y}' for x, y in movement['shape'])
            key = _connection_key(attrs)
            if key in replacements:
                raise ValueError('Duplicate movement')
            replacements[key] = attrs
            for duration, signal in (('5', 'G'), ('2', 'y'), ('1', 'r')):
                ET.SubElement(tls, 'phase', {'duration': duration, 'state': 'r' * i + signal + 'r' * (len(movements) - i - 1)})
        for old_logic in root.findall('tlLogic'):
            if old_logic.get('id') == junction_id and old_logic.get('programID') != 'torii-geometry':
                diagnostic = deepcopy(tls)
                diagnostic.set('programID', old_logic.get('programID', '0'))
                tls_patch.append(diagnostic)
    for connection in root.findall('connection'):
        if connection.get('from', '').startswith(':'):
            continue
        attrs = {k: connection.get(k) for k in _CONNECTION_ATTRIBUTES if connection.get(k) is not None}
        if attrs['from'] in removed_edges or attrs['to'] in removed_edges:
            continue
        junction_id = edges[attrs['from']].get('to')
        changed = junction_id in junction_movements or attrs['from'] in layouts or attrs['to'] in layouts
        if changed:
            ET.SubElement(con_patch, 'delete', {k: attrs[k] for k in ('from', 'to', 'fromLane', 'toLane')})
        if junction_id in junction_movements:
            continue
        for side in ('from', 'to'):
            if attrs[side] in remaps:
                attrs[side + 'Lane'] = str(remaps[attrs[side]][int(attrs[side + 'Lane'])])
        expected[_connection_key(attrs)] = attrs
        if changed:
            ET.SubElement(con_patch, 'connection', attrs)
    expected.update(replacements)
    for attrs in replacements.values():
        ET.SubElement(con_patch, 'connection', attrs)
        ET.SubElement(tls_patch, 'connection', {k: attrs[k] for k in ('from', 'to', 'fromLane', 'toLane', 'tl', 'linkIndex')})
    con_patch[:] = sorted(con_patch, key=lambda row: row.tag != 'delete')
    for original in root.findall('type'):
        item = deepcopy(original)
        # Imported SUMO lanes already contain these facilities.
        item.attrib.pop('bikeLaneWidth', None)
        item.attrib.pop('sidewalkWidth', None)
        type_patch.append(item)
    destination.mkdir(parents=True)
    paths = {}
    for name, tree in (('edges', edge_patch), ('connections', con_patch), ('nodes', node_patch), ('tllogic', tls_patch), ('types', type_patch)):
        paths[name] = destination / f'layout.{name}.xml'
        ET.indent(tree)
        ET.ElementTree(tree).write(paths[name], encoding='utf-8', xml_declaration=True)
    candidate = destination / 'observed-lanes.net.xml'
    command = [str(netconvert_binary), '--sumo-net-file', str(source), '--edge-files', str(paths['edges']), '--node-files', str(paths['nodes']),
               '--connection-files', str(paths['connections']), '--tllogic-files', str(paths['tllogic']), '--type-files', str(paths['types']),
               '--offset.disable-normalization', 'true', '--output-file', str(candidate)]
    result = run_command(command, cwd=destination, timeout_seconds=60)
    (destination / 'netconvert.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode or not candidate.exists():
        raise ValueError('netconvert failed: ' + result.stderr)
    actual = ET.parse(candidate).getroot()
    actual_edges = {e.get('id'): e for e in actual.findall('edge') if e.get('function') != 'internal'}
    actual_connection_rows = {_connection_key(c.attrib): c.attrib for c in actual.findall('connection')
                              if not c.get('from', '').startswith(':')}
    actual_connections = set(actual_connection_rows)
    binding_failures = [key for key, attrs in expected.items() if key not in actual_connection_rows
                        or _control_binding(attrs) != _control_binding(actual_connection_rows[key])]
    expected_logics = {(t.get('id'), t.get('programID', '0')): _signal_signature(t) for t in root.findall('tlLogic')}
    expected_logics.update({(t.get('id'), t.get('programID', '0')): _signal_signature(t) for t in tls_patch.findall('tlLogic')})
    actual_logics = {(t.get('id'), t.get('programID', '0')): _signal_signature(t) for t in actual.findall('tlLogic')}
    signal_failures = sorted(key for key in expected_logics.keys() | actual_logics.keys()
                             if expected_logics.get(key) != actual_logics.get(key))
    requested_edges = {e.get('id'): e for e in edge_patch.findall('edge')}
    attribute_failures = [key for key, edge in edges.items() if key not in removed_edges
                          and not _edge_attributes_preserved(requested_edges.get(key, edge), actual_edges.get(key))]
    outside = [key for key, edge in edges.items() if edge.get('from') not in affected_nodes and edge.get('to') not in affected_nodes]
    failures = [key for key in outside if key not in actual_edges or not _preserved_edge(edges[key], actual_edges[key])]
    audit = audit_network_connection_mode(actual, endpoint_tolerance_m=0.1)
    gates = {'source_immutable': file_sha256(source) == source_hash, 'edge_set': set(edges) - set(removed_edges) == set(actual_edges),
             'connection_set': set(expected) == actual_connections, 'outside_geometry': not failures,
             'lane_attributes': not attribute_failures, 'signal_bindings': not binding_failures,
             'path_continuity': audit['structural_failure_count'] == 0, 'diagnostic_signal_programs': not signal_failures}
    report = dict(schema='torii.observed-lane-candidate/v1', status='pass' if all(gates.values()) else 'blocked',
                  source_network=dict(path=str(source), sha256=source_hash), candidate_network=dict(path=str(candidate), sha256=file_sha256(candidate)),
                  gates={k: 'pass' if v else 'blocked' for k, v in gates.items()}, layouts=layouts, junction_movements=junction_movements, removed_edges=removed_edges,
                  affected_nodes=sorted(affected_nodes), outside_geometry_failures=failures, lane_index_remaps=remaps,
                  lane_attribute_failures=attribute_failures, signal_binding_failures=binding_failures,
                  signal_program_failures=signal_failures,
                  missing_connections=sorted(set(expected) - actual_connections), extra_connections=sorted(actual_connections - set(expected)),
                  connection_audit=audit, command=result.to_dict(),
                  timing_claim='Independent diagnostic phases only. No observed signal timing or traffic performance claim.',
                  claim_boundary='Explicit observed layouts and declared movements. Adjacent edge-end geometry may be rebuilt. Physical vehicle passage requires a separate swept-body check.')
    (destination / 'candidate-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
