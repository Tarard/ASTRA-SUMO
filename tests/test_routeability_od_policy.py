import shutil
import os
import subprocess
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from torii_sumo.core import routeability_audit as audit
from torii_sumo.core.candidate_contracts import file_sha256


def _network(path, second_length=12):
    path.write_text(f'<net><location netOffset="0,0" projParameter="!"/><edge id="a" from="x" to="y"><lane id="a_0" index="0" length="12" shape="0,0 12,0" allow="passenger"/></edge><edge id="tiny" from="y" to="z"><lane id="tiny_0" index="0" length="0.2" shape="12,0 12.2,0" allow="passenger"/></edge><edge id="b" from="z" to="x"><lane id="b_0" index="0" length="{second_length}" shape="12.2,0 0,0" allow="passenger"/></edge><edge id="bike" from="y" to="x"><lane id="bike_0" index="0" length="20" allow="bicycle"/></edge><connection from="a" to="tiny" fromLane="0" toLane="0"/><connection from="tiny" to="b" fromLane="0" toLane="0"/><connection from="b" to="a" fromLane="0" toLane="0"/></net>', encoding='utf-8')


def test_common_od_pool_uses_vehicle_length_and_keeps_short_edges_in_network(tmp_path):
    source, candidate = tmp_path / 'source.xml', tmp_path / 'candidate.xml'
    _network(source)
    _network(candidate, second_length=4)
    before = file_sha256(source), file_sha256(candidate)
    policy = audit.build_routeability_od_policy([source, candidate])
    assert policy['source_edge_ids'] == ['a']
    assert policy['destination_edge_ids'] == ['a']
    assert policy['minimum_edge_length_m'] == 5
    assert policy['vehicle_class'] == 'passenger'
    assert before == (file_sha256(source), file_sha256(candidate))
    assert ET.parse(source).getroot().find("edge[@id='tiny']") is not None


def test_sampler_does_not_validate_route_or_resample_requests(tmp_path):
    command = audit._build_random_trips_command(random_trips='randomTrips.py', net_file=tmp_path/'net.xml', trip_file=tmp_path/'planned.trips.xml', route_file=tmp_path/'unused.rou.xml', cwd=tmp_path, vehicle_count=10, seed=104, weights_prefix=tmp_path/'weights')
    assert '--no-validate' in command
    assert '--validate' not in command
    assert '-r' not in command
    assert '--weights-prefix' in command


def test_od_weights_are_fully_read_by_sumos_native_fast_parser(tmp_path):
    import sumolib.xml

    policy = {'source_edge_ids': ['first', 'second', 'third'], 'destination_edge_ids': ['last', 'other']}
    paths = audit._write_od_weights(policy, tmp_path / 'weights')
    for path, role in zip(paths, ('source', 'destination')):
        # randomTrips.LoadedProps uses parse_fast, not a general XML parser.
        loaded = {row.id: float(row.value) for row in sumolib.xml.parse_fast(str(path), 'edge', ['id', 'value'])}
        assert loaded == dict.fromkeys(policy[f'{role}_edge_ids'], 1.0)


def test_shared_od_requires_same_geometry_not_only_same_edge_id(tmp_path):
    source, candidate = tmp_path/'source.xml', tmp_path/'candidate.xml'
    _network(source)
    _network(candidate)
    tree = ET.parse(candidate)
    lane = tree.getroot().find("edge[@id='a']/lane")
    lane.set('shape', '3,0 12,0')
    lane.set('length', '9')
    tree.write(candidate, encoding='utf-8')
    policy = audit.build_routeability_od_policy([source,candidate])
    assert 'a' not in policy['source_edge_ids']
    assert policy['common_geometry_exclusions']['a']=='geometry_or_lane_identity_changed'
    tree.getroot().find('location').set('netOffset','1,0')
    tree.write(candidate,encoding='utf-8')
    with pytest.raises(ValueError,match='coordinate'):
        audit.build_routeability_od_policy([source,candidate])


def test_candidate_lost_connection_cannot_shrink_reference_od_pool(tmp_path):
    source, candidate = tmp_path / 'source.xml', tmp_path / 'candidate.xml'
    _network(source)
    _network(candidate)
    tree = ET.parse(candidate)
    tree.getroot().remove(tree.getroot().find("connection[@from='a']"))
    tree.write(candidate, encoding='utf-8')
    policy = audit.build_routeability_od_policy([source, candidate])
    assert policy['source_edge_ids'] == ['a', 'b']
    assert policy['connection_eligibility_differences'][0]['source_edges_missing_connections'] == ['a']


def test_incomplete_source_network_does_not_escape_the_failure_report(tmp_path):
    source = tmp_path / 'incomplete.net.xml'
    _network(source)  # Valid XML, but lacks required SUMO network metadata.
    result = audit.run_routeability_audit(net_file=source, output_dir=tmp_path / 'out', vehicle_count=1,
        initial_end=10, max_end=10, binaries={'sumo': 'sumo', 'duarouter': 'duarouter'})
    assert result['status'] == 'fail'
    assert result['routeability_status'] == 'route-generation-failed'
    assert Path(result['report_file']).is_file()


@pytest.mark.skipif(not shutil.which('netconvert') or not shutil.which('duarouter') or not shutil.which('sumo'), reason='SUMO binaries required')
def test_source_reachable_requests_are_reproducible_and_candidate_cannot_filter_them(tmp_path):
    nodes, edges, source = (tmp_path / name for name in ('nodes.xml', 'edges.xml', 'source.net.xml'))
    nodes.write_text('<nodes><node id="w" x="0" y="0"/><node id="j1" x="40" y="0"/><node id="j2" x="80" y="0"/><node id="e" x="120" y="0"/><node id="p" x="0" y="100"/><node id="q" x="40" y="100"/><node id="r" x="44" y="100"/></nodes>', encoding='utf-8')
    edges.write_text('<edges><edge id="a" from="w" to="j1" allow="passenger"/><edge id="b" from="j1" to="j2" allow="passenger"/><edge id="c" from="j2" to="e" allow="passenger"/><edge id="x" from="p" to="q" allow="passenger"/><edge id="tiny" from="q" to="r" allow="passenger"/></edges>', encoding='utf-8')
    subprocess.run(['netconvert', '-n', str(nodes), '-e', str(edges), '-o', str(source)], check=True, capture_output=True, timeout=30)
    policy = audit.build_routeability_od_policy([source])
    outputs = [tmp_path / name for name in ('first.xml', 'same-seed.xml', 'other-seed.xml')]
    digest = file_sha256(source)
    reports = [audit._write_source_reachable_trips(source, policy, output, vehicle_count=100, seed=seed) for output, seed in zip(outputs, (104, 104, 105))]
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert outputs[0].read_bytes() != outputs[2].read_bytes()
    requests = audit._request_records(outputs[0])
    assert len(requests) == 100
    assert [row[2] for row in requests.values()] == list(range(100))
    assert {(row[0], row[1]) for row in requests.values()} == {('a', 'b'), ('a', 'c'), ('b', 'c')}
    assert reports[0]['sources_without_reachable_destinations'] == ['x']
    assert reports[0]['reachable_od_pair_count'] == 3
    candidate = tmp_path / 'candidate.net.xml'
    tree = ET.parse(source)
    tree.getroot().remove(tree.getroot().find("connection[@from='a'][@to='b']"))
    tree.write(candidate, encoding='utf-8')
    candidate_policy = audit.build_routeability_od_policy([source, candidate])
    unchanged_requests = tmp_path / 'candidate-policy.xml'
    audit._write_source_reachable_trips(source, candidate_policy, unchanged_requests, vehicle_count=100, seed=104)
    assert unchanged_requests.read_bytes() == outputs[0].read_bytes()
    result = audit.run_routeability_audit(net_file=candidate, output_dir=tmp_path / 'candidate-run', vehicle_count=100,
        initial_end=300, max_end=300, seed=104, frozen_trip_file=outputs[0], expected_frozen_trip_sha256=file_sha256(outputs[0]))
    assert result['status'] == 'fail'
    assert result['routeability_status'] == 'route-generation-failed'
    assert Path(result['trip_file']).read_bytes() == outputs[0].read_bytes()
    assert not result.get('attempts')
    assert file_sha256(source) == digest
    generated = audit.run_routeability_audit(net_file=candidate, od_reference_net_file=source,
        output_dir=tmp_path / 'public-candidate-run', vehicle_count=100, initial_end=300, max_end=300, seed=104)
    assert generated['status'] == 'fail'
    assert generated['routeability_status'] == 'route-generation-failed'
    assert audit._request_records(Path(generated['trip_file'])) == requests
    assert Path(generated['trip_file']).read_bytes() == outputs[0].read_bytes()
    assert generated['od_policy']['mode'] == 'source_reachable_shared_generated'

    # getReachable alone ignores connection-level vehicle restrictions in
    # SUMO 1.27.1. Endpoint lane permissions are not sufficient evidence.
    restricted = tmp_path / 'restricted.net.xml'
    tree = ET.parse(source)
    for lane in tree.getroot().iter('lane'):
        lane.set('allow', 'passenger bus')
    tree.getroot().find("connection[@from='a'][@to='b']").set('allow', 'bus')
    tree.write(restricted, encoding='utf-8')
    restricted_requests = tmp_path / 'restricted-trips.xml'
    audit._write_source_reachable_trips(restricted, audit.build_routeability_od_policy([restricted]), restricted_requests, vehicle_count=100, seed=104)
    assert {(row[0], row[1]) for row in audit._request_records(restricted_requests).values()} == {('b', 'c')}


def test_output_hardlink_cannot_overwrite_protected_network(tmp_path):
    network=tmp_path/'network.xml'
    _network(network)
    output=tmp_path/'audit'
    output.mkdir()
    os.link(network,output/'demo.od.src.xml')
    before=file_sha256(network)
    result=audit.run_routeability_audit(net_file=network,output_dir=output,prefix='demo',vehicle_count=1,initial_end=10,max_end=10,binaries={'randomTrips':'randomTrips.py','duarouter':'duarouter','sumo':'sumo'},command_runner=lambda *args,**kwargs:{'status':'pass','returncode':0})
    assert result['status']=='fail'
    assert file_sha256(network)==before


@pytest.mark.parametrize('changed', ['network','frozen','route'])
def test_inputs_and_approved_routes_are_hash_checked_after_simulation(tmp_path,changed):
    network,frozen=tmp_path/'network.xml',tmp_path/'frozen.xml'
    _network(network)
    frozen.write_text('<routes><trip id="0" depart="0" from="a" to="b"/></routes>',encoding='utf-8')
    def runner(command,*,cwd,**kwargs):
        if command[0]=='duarouter':
            Path(command[command.index('--output-file')+1]).write_text('<routes><vehicle id="0" depart="0"><route edges="a b"/></vehicle></routes>',encoding='utf-8')
        else:
            cfg=ET.parse(cwd/command[command.index('-c')+1]).getroot()
            (cwd/cfg.find('output/summary-output').get('value')).write_text('<summary><step time="10" loaded="1" inserted="1" arrived="1" running="0" waiting="0" collisions="0" teleports="0"/></summary>',encoding='utf-8')
            tripinfos, vehicle_routes = ET.Element('tripinfos'), ET.Element('routes')
            for row in ET.parse(cwd/cfg.find('input/route-files').get('value')).getroot().findall('vehicle'):
                depart = float(row.get('depart'))
                arrival = depart + 1
                ET.SubElement(tripinfos, 'tripinfo', id=row.get('id'), depart=str(depart), arrival=str(arrival),
                    duration='1', waitingTime='0', timeLoss='0', vaporized='')
                observed = ET.SubElement(vehicle_routes, 'vehicle', id=row.get('id'), depart=str(depart), arrival=str(arrival))
                edges = row.find('route').get('edges')
                ET.SubElement(observed, 'route', edges=edges, exitTimes=' '.join(str(arrival) for _ in edges.split()))
            ET.ElementTree(tripinfos).write(cwd/cfg.find('output/tripinfo-output').get('value'), encoding='utf-8')
            ET.ElementTree(vehicle_routes).write(cwd/cfg.find('output/vehroute-output').get('value'), encoding='utf-8')
            target={'network':network,'frozen':frozen,'route':cwd/cfg.find('input/route-files').get('value')}[changed]
            target.write_text(target.read_text(encoding='utf-8')+'\n',encoding='utf-8')
        return {'status':'pass','returncode':0,'command':command}
    result=audit.run_routeability_audit(net_file=network,output_dir=tmp_path/'out',vehicle_count=1,initial_end=10,max_end=10,frozen_trip_file=frozen,expected_frozen_trip_sha256=file_sha256(frozen),binaries={'duarouter':'duarouter','sumo':'sumo'},command_runner=runner)
    assert result['status']=='fail'
    assert result['input_changes']


def test_frozen_trip_output_collision_and_hash_mismatch_do_not_delete_input(tmp_path):
    network = tmp_path / 'network.xml'
    _network(network)
    output = tmp_path / 'audit'
    output.mkdir()
    frozen = output / 'demo.trips.xml'
    frozen.write_text('<routes><trip id="0" depart="0" from="a" to="b"/></routes>', encoding='utf-8')
    digest = file_sha256(frozen)
    calls = []
    options = dict(net_file=network, output_dir=output, prefix='demo', vehicle_count=1, initial_end=10, max_end=10, binaries={'randomTrips':'randomTrips.py','duarouter':'duarouter','sumo':'sumo'}, command_runner=lambda *a, **kw: calls.append(a))
    report = audit.run_routeability_audit(**options, frozen_trip_file=frozen, expected_frozen_trip_sha256=digest)
    assert report['status'] == 'fail'
    assert file_sha256(frozen) == digest
    assert not calls
    options['output_dir'] = tmp_path / 'elsewhere'
    report = audit.run_routeability_audit(**options, frozen_trip_file=frozen, expected_frozen_trip_sha256='0'*64)
    assert report['status'] == 'fail'
    assert file_sha256(frozen) == digest
    assert not calls


@pytest.mark.skipif(not shutil.which('netconvert') or not shutil.which('sumo') or not shutil.which('duarouter'), reason='SUMO binaries required')
def test_native_weights_and_fixed_requests_preserve_pairing_and_transit(tmp_path):
    nodes, edges = tmp_path/'nodes.xml', tmp_path/'edges.xml'
    nodes.write_text('<nodes><node id="w" x="0" y="0"/><node id="a" x="20" y="0"/><node id="b" x="20.2" y="0"/><node id="e" x="40.2" y="0"/></nodes>', encoding='utf-8')
    edges.write_text('<edges><edge id="entry" from="w" to="a" allow="passenger"/><edge id="tiny" from="a" to="b" allow="passenger"/><edge id="exit" from="b" to="e" allow="passenger"/></edges>', encoding='utf-8')
    source, candidate = tmp_path/'source.net.xml', tmp_path/'candidate.net.xml'
    subprocess.run(['netconvert','-n',str(nodes),'-e',str(edges),'-o',str(source)],check=True,capture_output=True,timeout=30)
    shutil.copy2(source,candidate)
    before = file_sha256(source),file_sha256(candidate)
    first = audit.run_routeability_audit(net_file=source, od_reference_net_file=candidate, output_dir=tmp_path/'first', vehicle_count=3, initial_end=60,max_end=60,seed=104)
    assert first['status']=='pass'
    trips = Path(first['trip_file'])
    request_hash = file_sha256(trips)
    second = audit.run_routeability_audit(net_file=candidate, od_reference_net_file=source, frozen_trip_file=trips, expected_frozen_trip_sha256=request_hash, output_dir=tmp_path/'second', vehicle_count=3, initial_end=60,max_end=60,seed=104)
    assert second['status']=='pass'
    assert second['od_policy']['mode']=='frozen_requests'
    assert file_sha256(Path(second['trip_file']))==request_hash
    assert all(trip.get('from')=='entry' and trip.get('to')=='exit' for trip in ET.parse(trips).getroot().findall('trip'))
    assert all('tiny' in vehicle.find('route').get('edges').split() for vehicle in ET.parse(first['route_file']).getroot().findall('vehicle'))
    assert before==(file_sha256(source),file_sha256(candidate))
