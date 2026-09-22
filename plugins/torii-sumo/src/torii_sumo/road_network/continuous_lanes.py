"""Reconstruct declared continuous carriageways from inspected OSM fragment chains."""

from copy import deepcopy
import json
import math
from pathlib import Path

from pyproj import Transformer

from ..core.artifact_io import write_json_atomic
from ..core.candidate_contracts import file_sha256
from ..core.hamburg_aerial_approach import _project_polyline
from ..core.hamburg_aerial_movement import _endpoint_guide
from ..core.hamburg_junction_contour import _polygon
from ..core.official_movement_composition import _slice_shape
from ..core.osm_access import _permission_set
from .engineering_topology import _evidence, _id, _number, _shape, _year
from .osm_road_chain import inspect_osm_road_chain


def _length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _offset(points, distance):
    directions = []
    for a, b in zip(points, points[1:]):
        size = math.dist(a, b)
        directions.append(((b[0]-a[0])/size, (b[1]-a[1])/size))
    result = []
    for i, point in enumerate(points):
        first, last = directions[max(0, i-1)], directions[min(i, len(directions)-1)]
        denominator = 1 + first[0]*last[0] + first[1]*last[1]
        if denominator < 0.01:
            raise ValueError('A reversing axis requires explicit lane shapes.')
        result.append([point[0]-distance*(first[1]+last[1])/denominator,
                       point[1]+distance*(first[0]+last[0])/denominator])
    return result


def _cross_section_position(axis, shape, station):
    """Locate the unique lane crossing of the normal section at an axis station."""
    stations = [0.0]
    directions = []
    for a, b in zip(axis, axis[1:]):
        length = math.dist(a, b)
        stations.append(stations[-1] + length)
        directions.append([(b[j]-a[j])/length for j in (0, 1)])
    index = next(i for i in range(len(directions)) if station <= stations[i+1]+1e-7)
    tangent = directions[index]
    fraction = min(1.0, max(0.0, (station-stations[index])/(stations[index+1]-stations[index])))
    origin = [axis[index][j]+fraction*(axis[index+1][j]-axis[index][j]) for j in (0, 1)]
    if abs(station-stations[index+1]) < 1e-7 and index+1 < len(directions):
        tangent = [tangent[j]+directions[index+1][j] for j in (0, 1)]
    matches, travelled = [], 0.0
    for a, b in zip(shape, shape[1:]):
        first, last = (sum((p[j]-origin[j])*tangent[j] for j in (0, 1)) for p in (a, b))
        length = math.dist(a, b)
        if length < 1e-9:
            continue
        if abs(first-last) < 1e-9:
            if abs(first) < 1e-7:
                raise ValueError('A lane follows the cross section instead of crossing it.')
        else:
            fraction = first/(first-last)
            if -1e-7 <= fraction <= 1+1e-7:
                position = travelled+min(1.0,max(0.0,fraction))*length
                if not matches or abs(position-matches[-1]) > 1e-6:
                    matches.append(position)
        travelled += length
    if len(matches) != 1:
        raise ValueError('The axis cross section does not uniquely intersect the lane; review its geometry.')
    return matches[0]


def _slice_offset(axis, distance, start, end):
    """Cut one whole-road offset using common normal cross sections."""
    shape = _offset(axis, distance)
    return _slice_shape(shape, _cross_section_position(axis, shape, start), _cross_section_position(axis, shape, end))


def _apply_tapers(run, axis, segments, nodes, protected):
    """Use a bounded road surface; the added lane opens after its full-width station."""
    tapers = run.get('tapers', [])
    if not isinstance(tapers, list):
        raise ValueError('tapers must be a list of documented widening intervals.')
    records, used = [], set()
    for taper in tapers:
        if not isinstance(taper, dict):
            raise ValueError('Each taper must be a documented interval.')
        if 'shape' in taper:
            raise ValueError('Custom taper outlines need a separate spatial review; use the bounded generated outline here.')
        _evidence(taper.get('evidence'))
        start = _number(taper.get('start_m'), 'taper start_m')
        end = _number(taper.get('end_m'), 'taper end_m')
        boundary = next((i for i, s in enumerate(segments) if i and abs(s['start_m']-end)<1e-5), None)
        if boundary is None or boundary in used or end <= start:
            raise ValueError('A taper must end at one unique lane-section boundary.')
        old, new = segments[boundary-1], segments[boundary]
        if start <= old['start_m'] + 1e-5:
            raise ValueError('A taper must leave an upstream approach and cannot cross a retained boundary.')
        if any(start < row['run_station_m'] <= end+1e-5 and
               set(row['effective_reasons'])-{'physical_attribute_change'} for row in protected):
            raise ValueError('A taper cannot absorb a source control, restriction, crossing, or side-road boundary.')
        added = [key for key in new['lane_keys'] if key not in old['lane_keys']]
        if not added or any(key not in new['lane_keys'] for key in old['lane_keys']):
            raise ValueError('A widening taper requires added lanes and continuing upstream lanes.')
        used.add(boundary)
        def boundary_point(segment, lane, station, side):
            shape = lane['shape']
            position = 0 if abs(station-segment['start_m'])<1e-7 else _cross_section_position(axis, shape, station)
            tail = _slice_shape(shape, position, _length(shape))
            a, b = tail[0], next(p for p in tail[1:] if math.dist(p, tail[0])>1e-8)
            heading = math.atan2(b[1]-a[1], b[0]-a[0])
            width = side*lane['width_m']/2
            return [a[0]-math.sin(heading)*width, a[1]+math.cos(heading)*width], math.degrees(heading)
        sides = []
        for index, side in ((0, -1), (-1, 1)):
            a, first_heading = boundary_point(old, old['lanes'][index], start, side)
            b, last_heading = boundary_point(new, new['lanes'][index], end, side)
            guide = _endpoint_guide(a, b, first_heading, last_heading, control_fraction=1/3)
            # ponytail: 20 boundary pieces describe a local guide; surveyed outlines need a separate containment check.
            sides.append(guide[::6] + [guide[-1]])
        shape = _polygon(sides[0] + list(reversed(sides[1])))
        for cap, opposite in ((0, -1), (-1, 0)):
            a, b = sides[0][cap], sides[1][cap]
            direction = [b[j]-a[j] for j in (0, 1)]
            def across(point):
                return direction[0]*(point[1]-a[1])-direction[1]*(point[0]-a[0])
            sign = math.copysign(1, across(sides[0][opposite]))
            if any(sign*across(point) < -1e-6*math.hypot(*direction) for point in shape):
                raise ValueError('The generated taper extends past its end cross sections; review the bend geometry.')
        node = next(row for row in nodes if row['id'] == f"{run['id']}.at{boundary}")
        node.update(shape=shape, keepClear=False)
        records.append(dict(start_m=start, end_m=end, node_id=node['id'], shape=shape, evidence=taper['evidence'],
            new_lane_keys=added, new_lane_available_from_m=end, variable_lane_width_modelled=False,
            representation='priority_junction_surface_before_full_width_lane',
            claim_boundary='Existing lanes traverse the taper as internal paths. Entry into new lanes is checked after the full-width station. '
                           'This does not model continuously variable lane width or prove usable clearance inside the taper.'))
    return records


def reconstruct_continuous_lanes(*, topology_file, source_osm, output_dir, target_year):
    """Expand road_runs to explicit lanes and connections, retaining protected cuts.

    Each section lists lanes right-to-left with stable keys and either a signed
    leftward lateral_offset_m or an explicit lane shape. Same keys continue even
    when numeric lane indices change. Ending keys require explicit merges.
    """
    target_year = _year(target_year, 'target_year')
    path = Path(topology_file).resolve(strict=True)
    digest = file_sha256(path)
    topology = json.loads(path.read_text(encoding='utf-8'))
    if topology.get('schema') != 'torii.engineering-topology/v1':
        raise ValueError('Use engineering-topology/v1 input.')
    runs = topology.get('road_runs')
    if not isinstance(runs, list) or not runs:
        raise ValueError('road_runs must contain at least one carriageway.')
    destination = Path(output_dir).resolve()
    if destination.exists() or path.is_relative_to(destination):
        raise ValueError('Choose a new output directory separate from inputs.')
    result = deepcopy(topology)
    result.pop('road_runs')
    result.setdefault('edges', [])
    result.setdefault('connections', [])
    result.setdefault('unresolved', [])
    node_ids = {row['id'] for row in result['nodes']}
    edge_ids = {row['id'] for row in result['edges']}
    transform = Transformer.from_crs('EPSG:4326', topology['crs'], always_xy=True)
    records, bindings, errors = [], {}, []
    for run in runs:
        identifier = _id(run.get('id'))
        if identifier in bindings or identifier in edge_ids:
            raise ValueError('Road run IDs must be unique and separate from existing edges.')
        _evidence(run.get('evidence'))
        source_tolerance=_number(run.get('source_axis_tolerance_m',15.0),'source_axis_tolerance_m',positive=True)
        if run.get('from') not in node_ids or run.get('to') not in node_ids or run['from'] == run['to']:
            raise ValueError('A road run must reference its two declared end nodes.')
        chain = inspect_osm_road_chain(source_osm, run['source_way_ids'], start_node_id=run.get('start_node_id'))
        if not chain.get('axis_lonlat'):
            raise ValueError(f'Cannot reconstruct a disconnected or unresolved OSM chain: {identifier}')
        errors.extend(f'{identifier}: source chain requires review: {json.dumps(reason, ensure_ascii=False)}'
                      for reason in chain.get('reasons', []))
        valid = _year(run.get('osm_geometry_valid_for_target_year', topology.get('osm_geometry_valid_for_target_year')),
                      'osm_geometry_valid_for_target_year')
        if run.get('axis_shape') is not None:
            axis = [list(p) for p in run['axis_shape']]
            geometry_basis = 'supplied_plan_axis'
        else:
            if target_year is None or not (source_osm.get('data_year') == target_year or valid == target_year):
                raise ValueError('OSM axis geometry is not established for the target year.')
            axis = [list(transform.transform(*p, errcheck=True)) for p in chain['axis_lonlat']]
            geometry_basis = 'dated_OSM_chain' if source_osm.get('data_year') == target_year else 'OSM_chain_with_declared_year_validity'
        _shape(axis)
        axis = [p for i, p in enumerate(axis) if i == 0 or math.dist(axis[i-1], p) > 1e-9]
        total = _length(axis)
        source_intervals=[]
        for way in chain['ways']:
            locations=[chain['axis_node_ids'].index(node) for node in way['oriented_node_ids']]
            stations=[_project_polyline(transform.transform(*chain['axis_lonlat'][i]),axis)['fraction']*total for i in locations]
            source_intervals.append(dict(way_id=way['way_id'],start_m=min(stations),end_m=max(stations)))
        sections = deepcopy(run.get('sections', []))
        if not sections:
            raise ValueError('Declare a lane section covering the road run.')
        if sections[0].get('merges'):
            raise ValueError('The first section has no upstream section for an explicit merge.')
        previous = 0.0
        for section in sections:
            start = _number(section.get('start_m'), 'section start_m')
            end = total if section.get('end_m') is None else _number(section['end_m'], 'section end_m')
            if abs(start-previous) > 1e-5 or end <= start or end > total+1e-5:
                raise ValueError('Lane sections must cover the axis in order without gaps or overlap.')
            section.update(start_m=start, end_m=min(end,total))
            _evidence(section.get('evidence'))
            lanes = section.get('lanes')
            if not isinstance(lanes, list) or not lanes:
                raise ValueError('Each section needs explicit ordered lanes.')
            keys = [_id(lane.get('key')) for lane in lanes]
            if len(set(keys)) != len(keys):
                raise ValueError('Lane keys must be unique within a section.')
            for lane in lanes:
                _number(lane.get('width_m'), 'lane width_m', positive=True)
                if 'shape' in lane:
                    _shape(lane['shape'])
                else:
                    _number(lane.get('lateral_offset_m'), 'lateral_offset_m')
            previous = end
        if abs(previous-total) > 1e-5:
            raise ValueError('Lane sections must reach the end of the road run.')
        overrides = run.get('source_attribute_overrides', {})
        if not isinstance(overrides, dict):
            raise ValueError('source_attribute_overrides must map OSM attributes to plan evidence.')
        for attribute, evidence in overrides.items():
            if attribute.split(':')[0] not in {'lanes', 'turn', 'width', 'maxspeed'}:
                raise ValueError('Only lane, turn, width, or speed attributes can be superseded here.')
            _evidence(evidence)
            if attribute.split(':')[0] == 'maxspeed':
                for section in sections:
                    _number(section.get('speed_m_s', run.get('speed_m_s')), 'replacement speed_m_s', positive=True)
        cuts = {0.0, total, *(s['start_m'] for s in sections), *(s['end_m'] for s in sections)}
        plan_cuts=sorted(cuts)
        station_tolerance=_number(run.get('boundary_station_tolerance_m',0.5),'boundary_station_tolerance_m',positive=True)
        protected, superseded = [], []
        for boundary in chain.get('boundaries', []):
            if not boundary.get('mandatory_boundary'):
                continue
            point = chain['axis_lonlat'][boundary['axis_index']]
            projected = _project_polyline(transform.transform(*point), axis)
            station = projected['fraction']*total
            if 0.01 < station < total-0.01 and projected['distance_m'] <= source_tolerance:
                reasons = list(boundary['reasons'])
                changes = boundary.get('attribute_changes', {})
                resolved = {key: overrides[key] for key in changes if key in overrides}
                if resolved:
                    superseded.append({**boundary, 'run_station_m': station, 'override_evidence': resolved})
                if changes and set(changes) <= overrides.keys():
                    reasons = [reason for reason in reasons if reason != 'physical_attribute_change']
                if not reasons:
                    continue
                nearest=min(plan_cuts,key=lambda value:abs(value-station))
                snapped=nearest if abs(nearest-station)<=station_tolerance and set(reasons)=={'physical_attribute_change'} else station
                cuts.add(snapped)
                protected.append({**boundary, 'effective_reasons': reasons, 'run_station_m': snapped, 'source_projected_station_m':station,
                                  'aligned_to_plan_boundary':snapped!=station, 'plan_axis_distance_m': projected['distance_m']})
                if any(reason in boundary['reasons'] for reason in ('node_control_or_crossing','node_barrier','protected_relation','same_plane_side_road')):
                    errors.append(f"{identifier}: retained source boundary {boundary['node_id']} needs its control, restriction or side-access interpretation")
        cuts = sorted(cuts)
        cuts = [s for i,s in enumerate(cuts) if not i or s-cuts[i-1] > 1e-5]
        segments = []
        introduced, ended = [], []
        for i, (start,end) in enumerate(zip(cuts,cuts[1:])):
            section = next(s for s in sections if s['start_m'] <= (start+end)/2 < s['end_m'])
            shape = _slice_shape(axis, start, end)
            edge_id = identifier if len(cuts)==2 else f'{identifier}.s{i}'
            if edge_id in edge_ids:
                raise ValueError('Generated road edge conflicts with a declared edge.')
            start_id = run['from'] if i==0 else f'{identifier}.at{i}'
            end_id = run['to'] if i==len(cuts)-2 else f'{identifier}.at{i+1}'
            if i<len(cuts)-2:
                if end_id in node_ids:
                    raise ValueError('Generated road boundary conflicts with a declared node.')
                x,y=shape[-1]
                # This is a numerical boundary for retained attributes, not a new physical road junction.
                result['nodes'].append(dict(id=end_id,x=x,y=y,type='priority',
                    shape=[[x-.01,y-.01],[x+.01,y-.01],[x+.01,y+.01],[x-.01,y+.01]],evidence=run['evidence']))
                node_ids.add(end_id)
            lanes=[]
            for lane in section['lanes']:
                item=deepcopy(lane)
                if 'shape' in lane:
                    full=lane['shape']
                    lane_start = 0 if abs(start-section['start_m'])<1e-7 else _cross_section_position(axis, full, start)
                    lane_end = _length(full) if abs(end-section['end_m'])<1e-7 else _cross_section_position(axis, full, end)
                    item['shape']=_slice_shape(full, lane_start, lane_end)
                else:
                    item['shape']=_slice_offset(axis,lane['lateral_offset_m'],start,end)
                item.setdefault('evidence',section['evidence'])
                lanes.append(item)
            edge=dict(id=edge_id,**{'from':start_id,'to':end_id},shape=shape,lanes=lanes,evidence=run['evidence'])
            active=[item for item in source_intervals if item['end_m']>start+1e-5 and item['start_m']<end-1e-5]
            carrier=next((item for item in active if item['start_m'] <= (start+end)/2 <= item['end_m']),None)
            if carrier is not None:
                edge['osm_way_id']=carrier['way_id']
            edge['source_way_ids']=[item['way_id'] for item in active]
            for key in ('name','speed_m_s'):
                if key in section or key in run:
                    edge[key]=section.get(key,run.get(key))
            result['edges'].append(edge)
            edge_ids.add(edge_id)
            segments.append(dict(edge_id=edge_id,start_m=start,end_m=end,lane_keys=[lane['key'] for lane in lanes],lanes=lanes))
        taper_records = _apply_tapers(run, axis, segments, result['nodes'], protected)
        transitions=[]
        for old,new in zip(segments,segments[1:]):
            a,b=old['lane_keys'],new['lane_keys']
            if [k for k in a if k in b] != [k for k in b if k in a]:
                raise ValueError('Continuing lane order changes without a declared junction.')
            section=next(s for s in sections if s['start_m'] <= new['start_m']+1e-5 < s['end_m'])
            merges=section.get('merges',{}) if abs(section['start_m']-new['start_m'])<1e-5 else {}
            if not isinstance(merges,dict) or set(merges)-(set(a)-set(b)) or any(value not in b for value in merges.values()):
                raise ValueError('Explicit merges must name an ending upstream key and a downstream key.')
            for index,key in enumerate(a):
                target=key if key in b else merges.get(key)
                if target not in b:
                    ended.append(dict(lane_key=key,station_m=old['end_m'],reason='no_declared_successor'))
                    errors.append(f'{identifier}: lane {key} ends without a declared merge or outlet')
                    continue
                target_index=b.index(target)
                def permissions(lane):
                    allow=lane['allow']
                    return _permission_set({'allow':' '.join(allow) if isinstance(allow,list) else allow})
                if not permissions(old['lanes'][index]) & permissions(new['lanes'][target_index]):
                    raise ValueError('A continued lane has no compatible vehicle class.')
                connection=dict(**{'from':old['edge_id'],'to':new['edge_id'],'fromLane':index,'toLane':target_index},
                                evidence=section['evidence'], role='continuous_road_boundary')
                gap=math.dist(old['lanes'][index]['shape'][-1],new['lanes'][target_index]['shape'][0])
                if key==target and gap>0.1:
                    errors.append(f'{identifier}: continuing lane {key} has a {gap:.3f} m endpoint gap')
                result['connections'].append(connection)
                transitions.append({**connection,'from_key':key,'to_key':target,'endpoint_gap_m':gap})
            introduced.extend(dict(lane_key=k,station_m=new['start_m']) for k in b if k not in a)
        following={(row['from'],row['fromLane']):(row['to'],row['toLane']) for row in transitions}
        targets=set(following.values())
        lane_paths=[]
        for segment in segments:
            for index,key in enumerate(segment['lane_keys']):
                current=(segment['edge_id'],index)
                if current in targets:
                    continue
                groups=[current]
                while current in following:
                    current=following[current]
                    groups.append(current)
                lane_paths.append(dict(lane_key=key,edge_lanes=[list(value) for value in groups],
                                       full_run=len(groups)==len(segments)))
        bindings[identifier]=dict(first=segments[0],last=segments[-1])
        records.append(dict(id=identifier,axis_length_m=total,geometry_basis=geometry_basis,source_way_count=len(run['source_way_ids']),
            target_year=target_year,osm_geometry_valid_for_target_year=valid,
            source_way_ids=run['source_way_ids'],source_chain=chain,protected_boundaries=protected,superseded_boundaries=superseded,
            generated_edge_count=len(segments),segments=segments,lane_transitions=transitions,
            introduced_lanes=introduced,ended_lanes=ended,lane_paths=lane_paths,tapers=taper_records))
        records[-1]['source_sections']=sections
        records[-1]['source_way_intervals']=source_intervals
    for connection in result['connections']:
        for side,end in (('from','last'),('to','first')):
            if connection[side] in bindings:
                section=bindings[connection[side]][end]
                key=connection.pop(side+'LaneKey',None)
                if key is not None:
                    connection[side+'Lane']=section['lane_keys'].index(key)
                connection[side]=section['edge_id']
    result['unresolved'].extend(errors)
    for run in runs:
        result['unresolved'].extend(run.get('unresolved',[]))
    result['continuous_lane_source']=dict(path=str(path),sha256=digest)
    destination.mkdir(parents=True)
    normalized=destination/'topology.json'
    write_json_atomic(normalized,result,ensure_ascii=False)
    unchanged=file_sha256(path)==digest and file_sha256(Path(source_osm['path']))==source_osm['sha256']
    report=dict(schema='torii.continuous-lanes/v1',status='blocked' if not unchanged else 'review_required' if errors else 'pass',runs=records,
        source_topology=dict(path=str(path),sha256=digest),topology=dict(path=str(normalized),sha256=file_sha256(normalized)),
        inputs_unchanged=unchanged,unresolved=errors,
        claim_boundary='Continuous lanes follow declared plan keys. OSM supplies inspected fragment lineage and retained boundaries. '
                       'Lane geometry, merge design, side-road coverage and real control remain separate checks.')
    report['report_file']=str(destination/'continuity.json')
    write_json_atomic(Path(report['report_file']),report,ensure_ascii=False)
    return report
