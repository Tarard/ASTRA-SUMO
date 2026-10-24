"""Read native vehicle/link/signal states at selected green times without advancing time."""

import json
from pathlib import Path
import sys

import traci

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.simulation_stall import monitor_sumo, stopped_vehicle_report

request = json.loads(Path(sys.argv[1]).read_bytes())
output = Path(request['monitor_file']).parent / 'green-observations'
output.mkdir(exist_ok=False)
times = [3675, 3682, 3688, 3755, 3772]
original_connect = traci.connect


def observed_connect(*args, **kwargs):
    connection = original_connect(*args, **kwargs)
    original_step = connection.simulationStep

    def observed_step(*step_args, **step_kwargs):
        result = original_step(*step_args, **step_kwargs)
        now = connection.simulation.getTime()
        if times and now >= times[0] - 1e-8:
            target = times.pop(0)
            identities = connection.vehicle.getIDList()
            relations = stopped_vehicle_report(connection, identities)
            signals = {tls: {'program': connection.trafficlight.getProgram(tls),
                'phase': connection.trafficlight.getPhase(tls), 'state': connection.trafficlight.getRedYellowGreenState(tls),
                'next_switch_s': connection.trafficlight.getNextSwitch(tls)} for tls in connection.trafficlight.getIDList()}
            selected_lanes = ('24483344#1_0', '24483344#1_1', '79014037#1_0', '79014037#1_1',
                              '186713429_0', '186713429_1', '186713429_2', '186713429_3')
            lanes = {}
            for lane in selected_lanes:
                ids = connection.lane.getLastStepVehicleIDs(lane)
                lanes[lane] = {'length_m': connection.lane.getLength(lane), 'vehicles': list(ids),
                    'halting': connection.lane.getLastStepHaltingNumber(lane)}
                if ids:
                    head = max(ids, key=connection.vehicle.getLanePosition)
                    stops = connection.vehicle.getStops(head, 1)
                    lanes[lane]['head_stop_state'] = connection.vehicle.getStopState(head)
                    lanes[lane]['head_next_stop'] = vars(stops[0]) if stops else None
            assert connection.simulation.getTime() == now
            write_json_atomic(output / f'state-{target}.json', {'formal_result': False, 'time_s': now,
                'native_time_before_queries_s': now, 'native_time_after_queries_s': connection.simulation.getTime(),
                'sample_before_poststep_driver_update': True, 'traffic_lights': signals, 'lanes': lanes,
                'relations': relations, 'claim_boundary': 'Native traffic truth, not field sensing. Read-only queries. Relations identify candidate blockers and do not alone prove a causal deadlock.'})
        return result

    connection.simulationStep = observed_step
    return connection


traci.connect = observed_connect
monitor_sumo(request)
assert not times
