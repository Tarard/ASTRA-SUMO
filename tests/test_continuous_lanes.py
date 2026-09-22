import json
from pathlib import Path

import pytest
from pyproj import Transformer

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.road_network.continuous_lanes import reconstruct_continuous_lanes
from torii_sumo.road_network.engineering_topology import build_engineering_topology


def case(tmp_path, speed_change=False):
    osm = tmp_path/'source.osm.xml'
    nodes = ''.join(f'<node id="{i+1}" lon="{9+i*.0003}" lat="53"/>' for i in range(4))
    ways = ''.join(f'<way id="{10+i}"><nd ref="{i+1}"/><nd ref="{i+2}"/><tag k="highway" v="secondary"/><tag k="oneway" v="yes"/><tag k="lanes" v="2"/><tag k="maxspeed" v="{50 if speed_change and i else 30}"/></way>' for i in range(3))
    osm.write_text('<osm version="0.6">'+nodes+ways+'</osm>', encoding='utf-8')
    transform=Transformer.from_crs('EPSG:4326','EPSG:25832',always_xy=True)
    coords=[transform.transform(9+i*.0003,53) for i in range(4)]
    lanes=[dict(key='right',width_m=3.2,allow='passenger bus',lateral_offset_m=-1.6),dict(key='left',width_m=3.2,allow='passenger bus',lateral_offset_m=1.6)]
    data=dict(schema='torii.engineering-topology/v1',crs='EPSG:25832',source_plan_sha256='1'*64,
        nodes=[dict(id=n,x=p[0],y=p[1],evidence='test plan') for n,p in [('A',coords[0]),('B',coords[-1])]],
        edges=[],connections=[],road_runs=[dict(id='road',**{'from':'A','to':'B'},source_way_ids=['10','11','12'],
            evidence='test plan',sections=[dict(start_m=0,end_m=None,lanes=lanes,evidence='test cross section')])])
    path=tmp_path/'topology.json'
    path.write_text(json.dumps(data),encoding='utf-8')
    return path,data,dict(path=str(osm),sha256=file_sha256(osm),data_year=2013)


def test_three_source_fragments_become_one_continuous_two_lane_road(tmp_path):
    path,data,osm=case(tmp_path)
    before=path.read_bytes()
    result=reconstruct_continuous_lanes(topology_file=path,source_osm=osm,target_year=2013,output_dir=tmp_path/'reconstruct')
    assert result['status']=='pass',result
    topology=json.loads(Path(result['topology']['path']).read_text(encoding='utf-8'))
    assert len(topology['edges'])==1
    assert [lane['key'] for lane in topology['edges'][0]['lanes']]==['right','left']
    assert result['runs'][0]['source_way_count']==3
    assert result['runs'][0]['generated_edge_count']==1
    assert path.read_bytes()==before
    compiled=build_engineering_topology(topology_file=result['topology']['path'],source_osm=osm,target_year=2013,output_dir=tmp_path/'compiled')
    assert compiled['declared_topology_reproduced'] is True


def test_real_speed_boundary_is_retained(tmp_path):
    path,data,osm=case(tmp_path,True)
    result=reconstruct_continuous_lanes(topology_file=path,source_osm=osm,target_year=2013,output_dir=tmp_path/'reconstruct')
    topology=json.loads(Path(result['topology']['path']).read_text(encoding='utf-8'))
    assert len(topology['edges'])==2
    assert len(topology['connections'])==2
    compiled=build_engineering_topology(topology_file=result['topology']['path'],source_osm=osm,target_year=2013,output_dir=tmp_path/'compiled')
    import xml.etree.ElementTree as ET
    network=ET.parse(compiled['artifacts']['network']['path']).getroot()
    speeds=[float(edge.find('lane').get('speed')) for edge in network.findall('edge') if edge.get('function')!='internal']
    assert speeds==pytest.approx([30/3.6,50/3.6])


def test_new_right_cycle_lane_preserves_the_two_motor_lane_identities(tmp_path):
    path,data,osm=case(tmp_path)
    first=data['road_runs'][0]['sections'][0]
    first['end_m']=25.0
    data['road_runs'][0]['sections'].append(dict(start_m=25.0,end_m=None,evidence='documented cycle-lane start',
        lanes=[dict(key='cycle',allow='bicycle',width_m=1.8,lateral_offset_m=-4.1),*first['lanes']]))
    path.write_text(json.dumps(data),encoding='utf-8')
    result=reconstruct_continuous_lanes(topology_file=path,source_osm=osm,target_year=2013,output_dir=tmp_path/'reconstruct')
    topology=json.loads(Path(result['topology']['path']).read_text(encoding='utf-8'))
    assert {(c['fromLane'],c['toLane']) for c in topology['connections']}=={(0,1),(1,2)}
    assert any(r['lane_key']=='cycle' for r in result['runs'][0]['introduced_lanes'])


def test_lane_order_cannot_cross_and_different_year_osm_cannot_fill_axis(tmp_path):
    path,data,osm=case(tmp_path)
    with pytest.raises(ValueError,match='year'):
        reconstruct_continuous_lanes(topology_file=path,source_osm={**osm,'data_year':2026},target_year=2013,output_dir=tmp_path/'wrongyear')
    a=data['road_runs'][0]['sections'][0]
    a['end_m']=25
    data['road_runs'][0]['sections'].append(dict(start_m=25,end_m=None,evidence='invalid crossing test',lanes=list(reversed(a['lanes']))))
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='order'):
        reconstruct_continuous_lanes(topology_file=path,source_osm=osm,target_year=2013,output_dir=tmp_path/'crossed')


def test_full_hamburg_entry_runs_continuity_before_compilation(tmp_path):
    from torii_sumo.core.hamburg_topology_workflow import build_hamburg_topology_workflow
    path,data,osm=case(tmp_path)
    pdf=tmp_path/'plan.pdf'
    pdf.write_bytes(b'%PDF-1.4\n% synthetic test plan\n')
    data['source_plan_sha256']=file_sha256(pdf)
    section=data['road_runs'][0]['sections'][0]
    section['end_m']=25
    data['road_runs'][0]['sections'].append({**section,'start_m':25,'end_m':None})
    path.write_text(json.dumps(data),encoding='utf-8')
    request=tmp_path/'request.json'
    request.write_text(json.dumps(dict(schema='torii.hamburg-topology-workflow-request/v1',source_osm=osm,
        construction_plan=dict(path=str(pdf),sha256=file_sha256(pdf),data_year=2013,topology=dict(path=str(path),sha256=file_sha256(path))))),encoding='utf-8')
    result=build_hamburg_topology_workflow(request_file=request,output_dir=tmp_path/'workflow')
    assert result['stages']['road_continuity']['status']=='pass'
    assert result['stages']['corridor_lane_probes']['status']=='pass'
    assert result['stages']['movement_probes']['status']=='not_applicable'
    assert result['network_handoff'] is not None


def test_explicit_ending_lane_merge_keeps_an_end_to_end_path(tmp_path):
    path,data,osm=case(tmp_path)
    first=data['road_runs'][0]['sections'][0]
    first['end_m']=25
    last=first['lanes'][-1]
    first['lanes'].append({**last,'key':'pocket','lateral_offset_m':4.8})
    data['road_runs'][0]['sections'].append(dict(start_m=25,end_m=None,evidence='declared ending-lane merge',
        lanes=first['lanes'][:2],merges={'pocket':'left'}))
    path.write_text(json.dumps(data),encoding='utf-8')
    result=reconstruct_continuous_lanes(topology_file=path,source_osm=osm,target_year=2013,output_dir=tmp_path/'merged')
    transitions=result['runs'][0]['lane_transitions']
    assert {(r['fromLane'],r['toLane']) for r in transitions}=={(0,0),(1,1),(2,1)}
    assert all(row['full_run'] for row in result['runs'][0]['lane_paths'])


def test_merge_cannot_silently_redirect_a_continuing_lane(tmp_path):
    path,data,osm=case(tmp_path)
    first=data['road_runs'][0]['sections'][0]
    first['end_m']=25
    data['road_runs'][0]['sections'].append(dict(start_m=25,end_m=None,evidence='invalid merge',
        lanes=first['lanes'],merges={'right':'left'}))
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='ending upstream key'):
        reconstruct_continuous_lanes(topology_file=path,source_osm=osm,target_year=2013,output_dir=tmp_path/'invalid')


def test_source_grade_doubt_and_invalid_year_declaration_cannot_report_pass(tmp_path):
    path,data,osm=case(tmp_path)
    source=Path(osm['path'])
    source.write_text(source.read_text(encoding='utf-8').replace('</osm>',
        '<node id="5" lon="9.0003" lat="53.0003"/><way id="20"><nd ref="2"/><nd ref="5"/>'
        '<tag k="highway" v="residential"/><tag k="layer" v="unknown"/></way></osm>'),encoding='utf-8')
    osm['sha256']=file_sha256(source)
    result=reconstruct_continuous_lanes(topology_file=path,source_osm=osm,target_year=2013,output_dir=tmp_path/'grade')
    assert result['status']=='review_required'
    assert any('side_road_grade_unresolved' in reason for reason in result['unresolved'])
    data['road_runs'][0]['osm_geometry_valid_for_target_year']=2013.0
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='integer year'):
        reconstruct_continuous_lanes(topology_file=path,source_osm={**osm,'data_year':2026},target_year=2013,output_dir=tmp_path/'year')


def test_documented_plan_override_removes_only_the_obsolete_attribute_cut(tmp_path):
    path,data,osm=case(tmp_path,True)
    run=data['road_runs'][0]
    run.update(speed_m_s=10,source_attribute_overrides={'maxspeed':'Plan fixes this entire road at 36 km/h.'})
    path.write_text(json.dumps(data),encoding='utf-8')
    result=reconstruct_continuous_lanes(topology_file=path,source_osm=osm,target_year=2013,output_dir=tmp_path/'override')
    assert result['runs'][0]['generated_edge_count']==1
    assert result['runs'][0]['superseded_boundaries'][0]['attribute_changes']['maxspeed']
    source=Path(osm['path'])
    source.write_text(source.read_text(encoding='utf-8').replace('<node id="2" lon="9.0003" lat="53"/>',
        '<node id="2" lon="9.0003" lat="53"><tag k="highway" v="crossing"/></node>'),encoding='utf-8')
    osm['sha256']=file_sha256(source)
    kept=reconstruct_continuous_lanes(topology_file=path,source_osm=osm,target_year=2013,output_dir=tmp_path/'crossing')
    assert kept['runs'][0]['generated_edge_count']==2
    assert 'node_control_or_crossing' in kept['runs'][0]['protected_boundaries'][0]['reasons']


def test_a_retained_boundary_at_a_bend_does_not_break_offset_lanes(tmp_path):
    path,data,osm=case(tmp_path,True)
    source=Path(osm['path'])
    source.write_text(source.read_text(encoding='utf-8').replace('id="2" lon="9.0003" lat="53"',
        'id="2" lon="9.0003" lat="53.0001"'),encoding='utf-8')
    osm['sha256']=file_sha256(source)
    result=reconstruct_continuous_lanes(topology_file=path,source_osm=osm,target_year=2013,output_dir=tmp_path/'bend')
    assert max(row['endpoint_gap_m'] for row in result['runs'][0]['lane_transitions'])<1e-6


def test_documented_widening_has_a_real_transition_surface_before_the_new_lane(tmp_path):
    path,data,osm=case(tmp_path)
    run=data['road_runs'][0]
    first=run['sections'][0]
    first['end_m']=25
    run['sections'].append(dict(start_m=25,end_m=None,evidence='test full-width pocket',
        lanes=[*first['lanes'],dict(key='pocket',width_m=3.2,allow='passenger bus',lateral_offset_m=4.8)]))
    run['tapers']=[dict(start_m=15,end_m=25,evidence='synthetic test widening bounds')]
    path.write_text(json.dumps(data),encoding='utf-8')
    result=reconstruct_continuous_lanes(topology_file=path,source_osm=osm,target_year=2013,output_dir=tmp_path/'widen')
    report=result['runs'][0]['tapers'][0]
    assert report['new_lane_keys']==['pocket']
    assert report['new_lane_available_from_m']==25
    assert report['variable_lane_width_modelled'] is False
    topology=json.loads(Path(result['topology']['path']).read_text(encoding='utf-8'))
    node=next(n for n in topology['nodes'] if n['id']==report['node_id'])
    assert max(p[0] for p in node['shape'])-min(p[0] for p in node['shape'])>9
    assert node['type']=='priority'
    compiled=build_engineering_topology(topology_file=result['topology']['path'],source_osm=osm,target_year=2013,output_dir=tmp_path/'candidate')
    assert compiled['declared_topology_reproduced'] is True
    import xml.etree.ElementTree as ET
    actual=ET.parse(compiled['artifacts']['network']['path']).getroot()
    actual_node=actual.find(f"junction[@id='{report['node_id']}']")
    actual_shape=[list(map(float,p.split(','))) for p in actual_node.get('shape').split()]
    assert max(p[0] for p in actual_shape)-min(p[0] for p in actual_shape)>9
    lanes={lane.get('id'):lane for edge in actual.findall('edge') for lane in edge.findall('lane')}
    links=[c for c in actual.findall('connection') if not c.get('from').startswith(':')]
    assert all(float(lanes[c.get('via')].get('length'))>9 for c in links)
    assert ET.parse(compiled['artifacts']['nodes']['path']).getroot().find(
        f"node[@id='{report['node_id']}']").get('keepClear')=='false'
    assert all(set(row.get('foes')) <= {'0'} for row in actual_node.findall('request'))
    from torii_sumo.road_network.continuous_lane_probes import run_continuous_lane_probes
    probes=run_continuous_lane_probes(network_file=compiled['artifacts']['network']['path'],
        continuity_file=result['report_file'],output_dir=tmp_path/'probes')
    assert probes['lane_paths_passed']==3
    assert probes['entry_probes'][0]['status']=='needs_entry_evidence'
    assert probes['entry_probes'][0]['compatible_carrier_count']==2


def test_empty_replacement_speed_cannot_erase_a_real_speed_change(tmp_path):
    path,data,osm=case(tmp_path,True)
    data['road_runs'][0].update(speed_m_s=None,source_attribute_overrides={'maxspeed':'Invalid empty replacement speed.'})
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='speed_m_s'):
        reconstruct_continuous_lanes(topology_file=path,source_osm=osm,target_year=2013,output_dir=tmp_path/'empty-speed')


def test_lane_cuts_use_the_axis_cross_section_at_a_right_angle_bend():
    from torii_sumo.road_network.continuous_lanes import _slice_offset
    for offset in (-1.6,1.6):
        shape=_slice_offset([[0,0],[10,0],[10,100]],offset,15,80)
        assert shape[0]==pytest.approx([10-offset,5])
        assert shape[-1]==pytest.approx([10-offset,70])


@pytest.mark.parametrize('fault',['custom_outline','crossing_at_end'])
def test_taper_cannot_swallow_an_unreviewed_area_or_a_crossing(tmp_path,fault):
    path,data,osm=case(tmp_path,True)
    source=Path(osm['path'])
    transform=Transformer.from_crs('EPSG:4326','EPSG:25832',always_xy=True)
    a,b=transform.transform(9,53),transform.transform(9.0003,53)
    import math
    boundary=math.dist(a,b) if fault=='crossing_at_end' else 25
    run=data['road_runs'][0]
    first=run['sections'][0]
    first['end_m']=boundary
    run['sections'].append(dict(start_m=boundary,end_m=None,evidence='synthetic widening',
        lanes=[*first['lanes'],dict(key='pocket',width_m=3.2,allow='passenger',lateral_offset_m=4.8)]))
    taper=dict(start_m=boundary-3,end_m=boundary,evidence='invalid overlap test')
    if fault=='custom_outline':
        taper['shape']=[[a[0]+5,a[1]-4],[a[0]+25,a[1]-4],[a[0]+25,a[1]+7],[a[0]+5,a[1]+4]]
    else:
        source.write_text(source.read_text(encoding='utf-8').replace('<node id="2" lon="9.0003" lat="53"/>',
            '<node id="2" lon="9.0003" lat="53"><tag k="highway" v="crossing"/></node>'),encoding='utf-8')
        osm['sha256']=file_sha256(source)
    run['tapers']=[taper]
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='outline|crossing'):
        reconstruct_continuous_lanes(topology_file=path,source_osm=osm,target_year=2013,output_dir=tmp_path/'invalid-taper')
