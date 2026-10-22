"""Read the current corridor's stopped heads without changing native stepping."""
import json
from pathlib import Path
import sys
import traci
from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.simulation_stall import monitor_sumo

request = json.loads(Path(sys.argv[1]).read_bytes())
out = Path(request['monitor_file']).parent / 'blocking-observations'
out.mkdir(exist_ok=False)
targets = [3618, 3630, 3639, 3675, 3682, 3708, 3720, 3739, 3765, 3770, 3778]
original_connect = traci.connect

def observed_connect(*args, **kwargs):
    c = original_connect(*args, **kwargs)
    original_step = c.simulationStep
    def observed_step(*a, **kw):
        result = original_step(*a, **kw)
        now = c.simulation.getTime()
        if targets and now >= targets[0] - 1e-8:
            target = targets.pop(0)
            signals = {tls: {'program': c.trafficlight.getProgram(tls), 'phase': c.trafficlight.getPhase(tls),
                'state': c.trafficlight.getRedYellowGreenState(tls), 'next_switch_s': c.trafficlight.getNextSwitch(tls)}
                for tls in c.trafficlight.getIDList()}
            lanes = {}
            for edge in ('603103445#0', '603103445#1', '603103445#2', '24483344#1', '79014037#1',
                         '186713429', '141050975', '141050980#0', '141050980#1', '603103444', '73474895'):
                for i in range(c.edge.getLaneNumber(edge)):
                    lane = edge + '_' + str(i)
                    ids = c.lane.getLastStepVehicleIDs(lane)
                    rows = []
                    for identity in ids:
                        rows.append({'id': identity, 'position_m': c.vehicle.getLanePosition(identity),
                            'speed_m_s': c.vehicle.getSpeed(identity), 'leader': c.vehicle.getLeader(identity, 200),
                            'next_tls': c.vehicle.getNextTLS(identity), 'stop_state': c.vehicle.getStopState(identity),
                            'next_stops': [vars(s) for s in c.vehicle.getStops(identity, 1)]})
                    lanes[lane] = {'length_m': c.lane.getLength(lane), 'vehicles': rows}
            assert c.simulation.getTime() == now
            write_json_atomic(out / f'state-{target}.json', {'time_s': now, 'read_only': True,
                'sample_before_driver_update': True, 'signals': signals, 'lanes': lanes})
        return result
    c.simulationStep = observed_step
    return c

traci.connect = observed_connect
monitor_sumo(request)
assert not targets
