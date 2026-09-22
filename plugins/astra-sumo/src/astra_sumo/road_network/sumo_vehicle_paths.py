"""Read lane-centre reference paths for a declared SUMO junction.

The paths are proposed front-bumper routes, not observed FCD or vehicle sweeps.
No road boundary, predecessor lane, or missing internal geometry is invented.
"""

import gzip
import hashlib
import math
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

from ..core.official_movement_composition import _join_shapes, _slice_shape
from ..core.source_movement_support import _candidate_modes, _index, _internal
from ..corridor.netxml import _shape


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number.")
    return float(value)


def _length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _lane_shape(index, lane_id):
    return _join_shapes([_shape(index['lanes'][lane_id][1].get('shape', ''))])


def _departure_shapes(index, outgoing, core_lanes, requested):
    ids = [core_lanes[-1]]
    shapes = [_lane_shape(index, ids[0])]
    modes = set(index['lanes'][ids[0]][2])
    visited, total, reasons = set(core_lanes), _length(shapes[0]), []
    # ponytail: follow only unique continuations; branching needs a declared route.
    while total < requested - 1e-6:
        choices = outgoing.get(ids[-1], [])
        if len(choices) != 1:
            reasons.append('departure_extension_branching' if choices else 'departure_extension_ends')
            break
        _, source, target, connection = choices[0]
        next_modes, chain = _candidate_modes(index, source, target, connection)
        if not chain or any(lane_id in visited for lane_id in chain[1:]):
            reasons.append('departure_extension_chain_unresolved')
            break
        extra = [_lane_shape(index, lane_id) for lane_id in chain[1:]]
        if any(len(shape) < 2 or _length(shape) <= 1e-9 for shape in extra):
            reasons.append('departure_extension_geometry_missing')
            break
        pieces = [shapes[-1], *extra]
        if any(math.dist(a[-1], b[0]) > 1e-6 for a, b in zip(pieces, pieces[1:])):
            reasons.append('departure_extension_geometry_discontinuity')
            break
        modes &= next_modes
        for lane_id, shape in zip(chain[1:], extra):
            ids.append(lane_id)
            shapes.append(shape)
            visited.add(lane_id)
            total += _length(shape)
            if total >= requested - 1e-6:
                break
    return ids, shapes, modes, reasons


def _straight_backing(prefix, heading):
    """A conservative initial straight extent, not a road-surface clearance."""
    direction = (math.cos(heading), math.sin(heading))
    result = 0.0
    for first, last in reversed(list(zip(prefix, prefix[1:]))):
        length = math.dist(first, last)
        if length <= 1e-9:
            continue
        alignment = sum((b - a) * u for a, b, u in zip(first, last, direction)) / length
        if not math.isclose(alignment, 1.0, rel_tol=0, abs_tol=1e-8):
            break
        result += length
    return result


def extract_sumo_vehicle_paths(network_path, junction_id, approach_length_m=20.0, departure_length_m=20.0,
                               *, vehicle_length_m=None):
    """Extract each external connection through its complete unique via chain.

    Distances use metric XY shape length, not SUMO's possibly different lane
    length. Supply a vehicle length to check the recorded straight extent
    behind the initial front point. Short lanes remain explicit review cases.
    """
    approach = _positive(approach_length_m, 'approach_length_m')
    departure = _positive(departure_length_m, 'departure_length_m')
    vehicle_length = _positive(vehicle_length_m, 'vehicle_length_m') if vehicle_length_m is not None else None
    source = Path(network_path).resolve(strict=True)
    raw = source.read_bytes()
    root = ET.fromstring(gzip.decompress(raw) if raw.startswith(b'\x1f\x8b') else raw)
    if root.tag != 'net':
        raise ValueError("A SUMO net root is required.")
    if not isinstance(junction_id, str) or not junction_id or not any(
            node.get('id') == junction_id for node in root.findall('junction')):
        raise ValueError("The requested junction does not exist.")

    # Remove exact duplicate records in memory, preserving the source bytes.
    seen, duplicates = set(), 0
    for connection in root.findall('connection'):
        signature = (tuple(sorted(connection.attrib.items())), tuple(ET.tostring(child) for child in connection))
        if signature in seen:
            root.remove(connection)
            duplicates += 1
        else:
            seen.add(signature)
    index = _index(root)
    outgoing = defaultdict(list)
    for movement in index['movements']:
        outgoing[movement[1]].append(movement)
    rows = []
    for pair, first_id, last_id, connection in sorted(index['movements'], key=lambda item: item[0]):
        if index['edges'][pair[0]].get('to') != junction_id:
            continue
        row = dict(
            movement=dict(connection=list(pair), from_lane_id=first_id, to_lane_id=last_id,
                          direction=connection.get('dir', ''), controller_id=connection.get('tl'),
                          link_index=int(connection.get('linkIndex')) if connection.get('linkIndex') is not None else None),
            status='review_required', reasons=[], lane_ids=[], front_bumper_path=[],
            initial_heading_rad=None, initial_vehicle_space='not_assessed',
            initial_straight_backing_length_m=None, upstream_geometry_available_m=None,
        )
        rows.append(row)
        if _internal(index['edges'][pair[2]]) or index['edges'][pair[2]].get('from') != junction_id:
            row['reasons'].append('external_connection_wrong_junction')
            continue
        if not connection.get('via'):
            row['reasons'].append('missing_internal_geometry')
            continue
        motor_modes, lane_ids = _candidate_modes(index, first_id, last_id, connection)
        if not lane_ids:
            row['reasons'].append('internal_chain_unresolved')
            continue
        row.update(lane_ids=lane_ids, allowed_motor_vehicle_classes=sorted(motor_modes))
        row['movement']['internal_lane_ids'] = lane_ids[1:-1]
        shapes = [_lane_shape(index, lane_id) for lane_id in lane_ids]
        if any(len(shape) < 2 or _length(shape) <= 1e-9 for shape in shapes):
            row['reasons'].append('lane_geometry_missing_or_degenerate')
            continue
        gaps = [math.dist(left[-1], right[0]) for left, right in zip(shapes, shapes[1:])]
        row['endpoint_gaps_m'] = gaps
        # Only floating-point roundoff is joined. A visible gap needs evidence.
        if any(gap > 1e-6 for gap in gaps):
            row['reasons'].append('lane_geometry_discontinuity')
            continue
        departure_ids, departure_shapes, departure_modes, departure_reasons = _departure_shapes(index, outgoing, lane_ids, departure)
        row['reasons'].extend(departure_reasons)
        row['departure_lane_ids'] = departure_ids
        row['lane_ids'] = [*lane_ids[:-1], *departure_ids]
        row['allowed_motor_vehicle_classes'] = sorted(motor_modes & departure_modes)
        first_length, last_length = _length(shapes[0]), sum(_length(shape) for shape in departure_shapes)
        start = max(0.0, first_length - approach)
        end = min(departure, last_length)
        prefix = _slice_shape(shapes[0], 0.0, start)
        parts = [_slice_shape(shapes[0], start, first_length), *shapes[1:-1], _slice_shape(_join_shapes(departure_shapes), 0.0, end)]
        points = _join_shapes(parts)
        heading = math.atan2(points[1][1] - points[0][1], points[1][0] - points[0][0])
        backing = _straight_backing(prefix, heading)
        entry_end = _length(parts[0])
        junction_end = entry_end + sum(_length(shape) for shape in parts[1:-1])
        native_length = index['lanes'][first_id][1].get('length')
        native_length = _positive(float(native_length), 'source lane length') if native_length is not None else None
        row.update(
            front_bumper_path=[list(p) for p in points], initial_heading_rad=heading,
            approach_length_m=first_length - start, departure_length_m=end,
            junction_path_range_m=[entry_end, junction_end], path_length_m=_length(points),
            upstream_geometry_available_m=start, initial_straight_backing_length_m=backing,
            geometry_distance_basis='XY_polyline_arc_length',
            source_lane_length_m=native_length, source_lane_geometry_length_m=first_length,
            initial_front_bumper_lane_position_m=start * native_length / first_length if native_length is not None else None,
        )
        if first_length < approach - 1e-6:
            row['reasons'].append('approach_extent_shorter_than_requested')
        if last_length < departure - 1e-6:
            row['reasons'].append('departure_extent_shorter_than_requested')
        if vehicle_length is not None:
            fits = backing + 1e-6 >= vehicle_length
            row['initial_vehicle_space'] = 'fits_recorded_straight_extent' if fits else 'insufficient_recorded_straight_extent'
            if not fits:
                row['reasons'].append('initial_vehicle_space_insufficient')
            if end + 1e-6 < vehicle_length:
                row['reasons'].append('departure_extent_shorter_than_vehicle')
        row['status'] = 'review_required' if row['reasons'] else 'pass'

    return dict(
        schema='torii.sumo-vehicle-reference-paths/v1', source={'path': str(source), 'sha256': hashlib.sha256(raw).hexdigest()},
        junction_id=junction_id, approach_length_m=approach, departure_length_m=departure, vehicle_length_m=vehicle_length,
        status='pass' if rows and all(row['status'] == 'pass' for row in rows) else 'review_required',
        movements=rows, duplicate_connection_count=duplicates, fcd_observed=False,
        reference_point='front_bumper_center', reference_path_basis='declared_SUMO_lane_centre_shapes',
        road_boundaries_assessed=False,
        claim_boundary='Only recorded lane geometry and unique connection chains are extracted. The front-bumper route is a modelling assumption, not FCD. Initial straight extent does not prove road-surface clearance. No predecessor geometry, missing connection curve, vehicle trajectory, or road boundary is inferred.',
    )
