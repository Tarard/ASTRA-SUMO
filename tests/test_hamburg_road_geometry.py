import xml.etree.ElementTree as ET

import pytest
from shapely.geometry import LineString

from torii_sumo.core import hamburg_road_geometry as geometry


def _network(dx=0, dy=0):
    root = ET.fromstring('''<net>
      <junction id="266199070" type="priority"/>
      <edge id="37693933#1" from="west" to="266199070">
        <lane index="0"/><lane index="1"/><lane index="2"/>
      </edge>
      <edge id="31106387" from="north" to="266199070"><lane index="0"/></edge>
      <edge id="234421319" from="266199070" to="south">
        <lane index="0" shape="960.07,792.82 982.69,783.23 1065.09,765.14"/>
      </edge>
      <edge id="24483344#0" from="266199070" to="east">
        <lane index="0" shape="959.70,791.90 985.37,788.04 1040.09,775.46"/>
        <lane index="1" shape="960.94,794.95 985.97,791.18 1040.81,778.58"/>
      </edge>
      <connection from="37693933#1" fromLane="0" to="234421319" toLane="0"/>
      <connection from="37693933#1" fromLane="1" to="24483344#0" toLane="0"/>
      <connection from="37693933#1" fromLane="2" to="24483344#0" toLane="1"/>
    </net>''')
    for lane in root.findall('edge/lane[@shape]'):
        lane.set('shape', ' '.join(f'{x+dx},{y+dy}' for x, y in geometry._points(lane.get('shape'))))
    return root


@pytest.mark.parametrize('translation', [(0, 0), (-350.29, -181.62), (50000, -70000)])
def test_reviewed_fork_repair_removes_crossing_in_each_network_frame(translation):
    root = _network(*translation)
    before = ET.tostring(root)
    plan = geometry.plan_hamburg_road_geometry(root)
    assert plan['status'] == 'pass'
    assert plan['before_overlap_m2'] > 30
    shape = plan['lane_shape']
    original = geometry._points(root.find("edge[@id='234421319']/lane").get('shape'))
    assert shape[1:] == original[1:]
    repaired = LineString(shape).buffer(1.6, cap_style=2)
    for lane in root.findall("edge[@id='24483344#0']/lane"):
        assert repaired.intersection(LineString(geometry._points(lane.get('shape'))).buffer(1.6, cap_style=2)).area == 0
    assert ET.tostring(root) == before


@pytest.mark.parametrize('change', ['shape', 'width', 'connection', 'signal'])
def test_changed_source_is_reported_without_replaying_old_coordinates(change):
    root = _network()
    if change == 'shape':
        root.find("edge[@id='234421319']/lane").set('shape', '960.07,792.82 989.69,783.23 1065.09,765.14')
    elif change == 'width':
        shape = geometry.plan_hamburg_road_geometry(root)['lane_shape']
        root.find("edge[@id='234421319']/lane").set('shape', geometry._shape(shape))
        root.find("edge[@id='234421319']/lane").set('width', '4.5')
    elif change == 'connection':
        root.find('connection').set('fromLane', '1')
    else:
        root.find('junction').set('type', 'traffic_light')
    assert geometry.plan_hamburg_road_geometry(root)['status'] == 'review_required'


def test_other_corridors_need_no_hamburg_repair():
    assert geometry.plan_hamburg_road_geometry(ET.fromstring('<net/>'))['status'] == 'not_applicable'


@pytest.mark.parametrize('translation', [(0, 0), (-350.29, -181.62), (50000, -70000)])
def test_san_francisco_clearance_preserves_cuts_and_remote_tangent(translation):
    root = ET.fromstring('''<net><junction id="3127482712" type="priority"/>
      <edge id="307556940#2" from="north" to="3127482712"><lane index="0" shape="746.12,238.06 750.98,229.03 760.54,209.82"/></edge>
      <edge id="9702439#0" from="3127482712" to="north"><lane index="0" shape="765.98,212.52 762.75,214.54 758.20,232.91"/><lane index="1" shape="764.28,209.80 759.95,212.52 755.10,232.15"/></edge>
      <edge id="307556942" from="south" to="3127482712"><lane/><lane/></edge>
      <edge id="-307556942" from="3127482712" to="south"><lane/></edge>
      <connection from="307556940#2" fromLane="0" to="-307556942" toLane="0"/>
      <connection from="307556942" fromLane="0" to="9702439#0" toLane="0"/>
      <connection from="307556942" fromLane="1" to="9702439#0" toLane="1"/>
    </net>''')
    for lane in root.findall('edge/lane[@shape]'):
        lane.set('shape', geometry._shape([(x + translation[0], y + translation[1]) for x, y in geometry._points(lane.get('shape'))]))
    before = ET.tostring(root)
    plan = geometry.plan_hamburg_road_geometry(root)
    assert plan['status'] == 'pass'
    assert plan['before_overlap_m2'] > 19
    assert plan['after_proposal_overlap_m2'] == 0
    for original, shape in zip(plan['before_lane_shapes'], plan['lane_shapes'].values()):
        assert (shape[0], shape[-1]) == (original[0], original[-1])
        assert LineString(original[1:]).distance(LineString(shape[1:])) < 0.01
    assert ET.tostring(root) == before
    lane = root.find("edge[@id='9702439#0']/lane")
    lane.set('width', '4')
    assert geometry.plan_hamburg_road_geometry(root)['status'] == 'review_required'
    lane.attrib.pop('width')
    for lane, shape in zip(root.findall("edge[@id='9702439#0']/lane"), plan['lane_shapes'].values()):
        lane.set('shape', geometry._shape(shape))
    assert geometry.plan_hamburg_road_geometry(root)['status'] == 'not_applicable'


def test_already_separated_exit_is_not_repaired_again():
    root = _network()
    plan = geometry.plan_hamburg_road_geometry(root)
    root.find("edge[@id='234421319']/lane").set('shape', geometry._shape(plan['lane_shape']))
    assert geometry.plan_hamburg_road_geometry(root)['status'] == 'not_applicable'


def test_added_road_requires_review_before_reusing_the_old_local_shape():
    root = _network()
    ET.SubElement(root, 'edge', id='new-road', attrib={'from': '266199070', 'to': 'new-place'})
    assert geometry.plan_hamburg_road_geometry(root)['status'] == 'review_required'
