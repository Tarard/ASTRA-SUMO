"""Track construction coverage and rebuild explicitly reviewed context junctions."""

import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image
from pyproj import Transformer

from .hamburg_junctions.groups import _prepare_context_joins


def _official_nodes(request):
    data = json.loads(Path(request['lsa_identity']['path']).read_text(encoding='utf-8'))
    rows = []
    for feature in data.get('features', []):
        geometry = feature.get('geometry') or {}
        points = geometry.get('coordinates', [])
        if geometry.get('type') == 'Point':
            points = [points]
        if geometry.get('type') not in {'Point', 'MultiPoint'} or not points:
            continue
        props = feature.get('properties') or {}
        node_id = str(props.get('knoten', ''))
        if not node_id.isdigit():
            continue
        lon, lat = points[0][:2]
        if (not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (lon, lat))
                or not -180 <= lon <= 180 or not -90 <= lat <= 90):
            raise ValueError('Official junction coordinates must be finite.')
        rows.append({'node_id': str(int(node_id)), 'name': str(props.get('LSA_Name', '')).strip(),
                     'longitude': lon, 'latitude': lat})
    return rows


def construction_coverage(request):
    west, south, east, north = map(float, request['osm_build']['bbox'].split(','))
    official = {str(int(row['node_id'])) for row in request['intersections']}
    context = {str(int(row['node_id'])) for row in request.get('context_intersections', [])}
    rows = []
    for node in _official_nodes(request):
        if west <= node['longitude'] <= east and south <= node['latitude'] <= north:
            treatment = ('official_MAP_reconstruction' if node['node_id'] in official else
                         'reviewed_context_reconstruction' if node['node_id'] in context else 'source_only')
            rows.append({**node, 'treatment': treatment})
    pending = sorted(row['node_id'] for row in rows if row['treatment'] == 'source_only')
    return {'schema': 'torii.hamburg-construction-coverage/v1', 'status': 'review_required' if pending else 'pass',
            'bbox': request['osm_build']['bbox'], 'intersections': rows, 'source_only_node_ids': pending,
            'claim_boundary': 'Inventory of requested reconstruction within the requested area. Source-only junctions have not been reconstructed; assigned treatment does not certify geometry.'}


def rebuild_context_intersections(*, source_net, request, output_dir, netconvert_binary, sumo_binary,
                                  timeout_seconds, seed):
    """Use declared members and diagnostic signals; never invent missing MAP data."""
    definitions = request.get('context_intersections', [])
    if not definitions:
        return {'status': 'not_applicable', 'candidate_network': None}
    root = ET.parse(source_net).getroot()
    nodes = {node.get('id'): node for node in root.findall('junction')}
    location = root.find('location')
    if location is None:
        raise ValueError('Context reconstruction requires a projected network.')
    offset = tuple(map(float, location.get('netOffset').split(',')))
    projection = location.get('projParameter')
    to_network = Transformer.from_crs('EPSG:4326', projection, always_xy=True)
    to_aerial = Transformer.from_crs(projection, 'EPSG:25832', always_xy=True)
    official = _official_nodes(request)
    positions = {row['node_id']: to_network.transform(row['longitude'], row['latitude']) for row in official}
    groups, evidence = {}, []
    for definition in definitions:
        identifier = str(int(definition['node_id']))
        if definition['signal_policy'] != 'rebuild_diagnostic' or identifier not in positions:
            raise ValueError('Context reconstruction requires official identity and an explicit diagnostic signal policy.')
        members = definition['source_node_ids']
        if any(node not in nodes or node.startswith(':') for node in members):
            raise ValueError('Context members must be existing road junctions.')
        with Image.open(definition['aerial_image']['path']) as image:
            image.verify()
        west, south, east, north = definition['bbox_epsg25832']
        points = [(float(nodes[node].get('x')) - offset[0], float(nodes[node].get('y')) - offset[1]) for node in members]
        if any(not (west <= x <= east and south <= y <= north) for x, y in map(lambda point: to_aerial.transform(*point), points)):
            raise ValueError('Context members extend beyond their reviewed aerial image.')
        center = tuple(sum(point[axis] for point in points) / len(points) for axis in (0, 1))
        if (min(positions, key=lambda key: math.dist(center, positions[key])) != identifier
                or math.dist(center, positions[identifier]) > request.get('max_binding_distance_m', 35.0)):
            raise ValueError('The selected context group does not match the nearest official junction identity.')
        # ponytail: one physical group per LSA; add part IDs when a reviewed case needs multiple groups.
        groups[f'LSA{identifier}_part0'] = members
        evidence.append({**definition, 'source_group_center_projected': center,
                         'official_center_projected': positions[identifier]})
    result = _prepare_context_joins(source_net=source_net, context_groups=groups, official_groups=[],
        output_dir=Path(output_dir), netconvert_binary=netconvert_binary, sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds, seed=seed, signal_policy='rebuild_diagnostic',
        junction_contours=request['construction'].get('junction_contours', 'fused'),
        junction_corner_radius_m=request['construction'].get('junction_corner_radius_m', 8.0),
        interior_lane_change_policy=request['construction'].get('context_lane_change_policy', 'fixed_paths'))
    return {**result, 'context_evidence': evidence, 'historical_signal_timing': 'not_reconstructed',
            'field_geometry_review': 'review_required'}
