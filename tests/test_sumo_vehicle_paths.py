import math

import pytest

from torii_sumo.road_network.sumo_vehicle_paths import extract_sumo_vehicle_paths


NETWORK = '''<net>
  <location netOffset="0,0" projParameter="!"/>
  <edge id="in" from="before" to="J"><lane id="in_0" index="0" length="90" shape="-60,0 -20,0 0,0"/></edge>
  <edge id=":J_0" function="internal"><lane id=":J_0_0" index="0" length="8" shape="0,0 5,0"/></edge>
  <edge id=":J_1" function="internal"><lane id=":J_1_0" index="0" length="9" shape="5,0 5,5"/></edge>
  <edge id="out" from="J" to="after"><lane id="out_0" index="0" length="100" shape="5,5 5,55"/></edge>
  <junction id="J" type="traffic_light" incLanes="in_0" intLanes=":J_0_0 :J_1_0"/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":J_0_0" dir="l" tl="J" linkIndex="2"/>
  <connection from=":J_0" to="out" fromLane="0" toLane="0" via=":J_1_0" dir="l"/>
  <connection from=":J_1" to="out" fromLane="0" toLane="0" dir="l"/>
</net>'''


def test_multivia_path_clips_geometrically_and_preserves_source(tmp_path):
    source = tmp_path / 'source.net.xml'
    source.write_text(NETWORK, encoding='utf-8')
    before = source.read_bytes()
    report = extract_sumo_vehicle_paths(source, 'J', vehicle_length_m=12.135)
    row, = report['movements']
    assert row['status'] == 'pass'
    assert row['lane_ids'] == ['in_0', ':J_0_0', ':J_1_0', 'out_0']
    assert row['front_bumper_path'] == [[-20.0, 0.0], [0.0, 0.0], [5.0, 0.0], [5.0, 5.0], [5.0, 25.0]]
    assert row['initial_heading_rad'] == pytest.approx(0.0)
    assert row['initial_straight_backing_length_m'] == pytest.approx(40.0)
    assert row['initial_front_bumper_lane_position_m'] == pytest.approx(60.0)
    assert row['movement']['connection'] == ['in', 0, 'out', 0]
    assert row['movement']['link_index'] == 2
    assert row['junction_path_range_m'] == pytest.approx([20.0, 30.0])
    assert report['fcd_observed'] is False
    assert report['road_boundaries_assessed'] is False
    assert source.read_bytes() == before


def test_identical_external_and_internal_connections_are_deduplicated(tmp_path):
    source = tmp_path / 'duplicate.net.xml'
    extra = '\n'.join(line for line in NETWORK.splitlines() if '<connection' in line)
    source.write_text(NETWORK.replace('</net>', extra + '</net>'), encoding='utf-8')
    report = extract_sumo_vehicle_paths(source, 'J')
    assert len(report['movements']) == 1
    assert report['duplicate_connection_count'] == 3
    assert report['movements'][0]['initial_vehicle_space'] == 'not_assessed'


@pytest.mark.parametrize('old,new,reason', [
    ('shape="5,0 5,5"', 'shape="6,0 5,5"', 'lane_geometry_discontinuity'),
    ('via=":J_1_0" dir="l"', 'via=":J_0_0" dir="l"', 'internal_chain_unresolved'),
    ('via=":J_0_0" dir="l" tl="J"', 'dir="l" tl="J"', 'missing_internal_geometry'),
])
def test_no_path_is_synthesized_across_invalid_chains(tmp_path, old, new, reason):
    source = tmp_path / 'invalid.net.xml'
    source.write_text(NETWORK.replace(old, new), encoding='utf-8')
    row, = extract_sumo_vehicle_paths(source, 'J')['movements']
    assert row['status'] == 'review_required'
    assert reason in row['reasons']
    assert row['front_bumper_path'] == []


def test_short_or_curved_initial_backing_does_not_claim_vehicle_space(tmp_path):
    source = tmp_path / 'short.net.xml'
    source.write_text(NETWORK.replace('-60,0 -20,0 0,0', '-30,0 0,0'), encoding='utf-8')
    row, = extract_sumo_vehicle_paths(source, 'J', vehicle_length_m=12.135)['movements']
    assert row['initial_straight_backing_length_m'] == pytest.approx(10.0)
    assert row['status'] == 'review_required'
    assert row['initial_vehicle_space'] == 'insufficient_recorded_straight_extent'
    source.write_text(NETWORK.replace('-60,0 -20,0 0,0', '-20,-40 -20,0 0,0'), encoding='utf-8')
    row, = extract_sumo_vehicle_paths(source, 'J', vehicle_length_m=4.633)['movements']
    assert row['initial_heading_rad'] == pytest.approx(0.0)
    assert row['initial_straight_backing_length_m'] == pytest.approx(0.0)
    assert row['status'] == 'review_required'


def test_repeated_shape_points_and_invalid_inputs(tmp_path):
    source = tmp_path / 'input.net.xml'
    source.write_text(NETWORK.replace('-60,0 -20,0 0,0', '-60,0 -20,0 -20,0 0,0'), encoding='utf-8')
    row, = extract_sumo_vehicle_paths(source, 'J', approach_length_m=10, departure_length_m=15)['movements']
    assert row['front_bumper_path'][0] == [-10.0, 0.0]
    assert row['front_bumper_path'][-1] == [5.0, 20.0]
    assert row['path_length_m'] == pytest.approx(35.0)
    for value in [True, 0, -1, math.inf, math.nan]:
        with pytest.raises(ValueError):
            extract_sumo_vehicle_paths(source, 'J', approach_length_m=value)
    with pytest.raises(ValueError, match='junction'):
        extract_sumo_vehicle_paths(source, 'unknown')


def test_short_departure_extends_only_through_unique_recorded_connections(tmp_path):
    source = tmp_path / 'continued.net.xml'
    continuation = '''
      <edge id=":after_0" function="internal"><lane id=":after_0_0" index="0" shape="5,10 5,12"/></edge>
      <edge id="next" from="after" to="end"><lane id="next_0" index="0" length="40" shape="5,12 5,52"/></edge>
      <junction id="after" type="priority" incLanes="out_0" intLanes=":after_0_0"/>
      <connection from="out" to="next" fromLane="0" toLane="0" via=":after_0_0" dir="s"/>
      <connection from=":after_0" to="next" fromLane="0" toLane="0" dir="s"/>
    '''
    net = NETWORK.replace('shape="5,5 5,55"', 'shape="5,5 5,10"').replace('</net>', continuation + '</net>')
    source.write_text(net, encoding='utf-8')
    row, = extract_sumo_vehicle_paths(source, 'J', vehicle_length_m=12.135)['movements']
    assert row['status'] == 'pass'
    assert row['departure_lane_ids'] == ['out_0', ':after_0_0', 'next_0']
    assert row['movement']['to_lane_id'] == 'out_0'
    assert row['movement']['internal_lane_ids'] == [':J_0_0', ':J_1_0']
    assert row['front_bumper_path'][-1] == [5.0, 25.0]
    assert row['departure_length_m'] == pytest.approx(20.0)
    assert row['path_length_m'] == pytest.approx(50.0)
    branching = '<connection from="out" to="next" fromLane="0" toLane="0" via=":after_0_0" dir="r"/>'
    source.write_text(net.replace('</net>', branching + '</net>'), encoding='utf-8')
    row, = extract_sumo_vehicle_paths(source, 'J', vehicle_length_m=12.135)['movements']
    assert row['status'] == 'review_required'
    assert 'departure_extension_branching' in row['reasons']
    assert row['front_bumper_path'][-1] == [5.0, 10.0]
    source.write_text(net.replace('shape="5,10 5,12"', 'shape="6,10 5,12"'), encoding='utf-8')
    row, = extract_sumo_vehicle_paths(source, 'J')['movements']
    assert row['status'] == 'review_required'
    assert 'departure_extension_geometry_discontinuity' in row['reasons']
    assert row['front_bumper_path'][-1] == [5.0, 10.0]
