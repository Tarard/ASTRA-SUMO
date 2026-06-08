# Symptom to Evidence Map

Use this to choose the first probe for common Eclipse SUMO failures.

| Symptom | First evidence | Next probe | Do not claim |
|---|---|---|---|
| `FatalTraCIError: Could not connect` | printed command, SUMO log, port, binary path, Python `traci.__file__` | run exact command in shell; add `--log` | controller failure |
| `connection closed by SUMO` | SUMO error log, Python traceback, last simulation time | inspect SUMO-side fatal/warning before Python code | model performance result |
| GUI works but headless/TraCI fails | command diff, working directory, `--start`, output paths | run same binary and config headless with logs | GUI validation |
| repeated episodes fail later | lifecycle log, `traci.close`, labels, ports, output paths | one episode per fresh process or documented `load` flow | missing episodes are random noise |
| run never finishes | `getMinExpectedNumber`, summary running/waiting, teleports, route/demand | classify natural completion vs fixed horizon vs deadlock | completed experiment |
| many vehicles waiting for insertion | summary waiting/running, departDelay, max vehicles, step length | reduce demand; inspect insertion settings and capacity | served demand equals planned demand |
| teleports occur | teleport reason counts, warning lines, route/network/TLS state | classify jam/yield/wrongLane/disconnected/collision | normal travel-time evidence |
| route/reroute fails | edge connectivity, vehicle class permissions, stops/via, closed edges | validate with duarouter/routecheck/minimal route | controller failure |
| vehicle command has no effect | active vehicle IDs, command log, object domain, next-step state | verify command timing and later overrides | command executed successfully |
| traffic-light control looks wrong | loaded program, phase index, state string length, TLS outputs | compare TraCI command log with SUMO TLS outputs | NEMA/TLS claim |
| detector output empty | detector XML, lane ID, position, period, file path, passed vehicles | visualize/check detector coverage and lane permissions | detector-based observability |
| metric looks too good | completion rate, unfinished vehicles, seed count, output scope | include unfinished and backlog; run seed sweep | controller superiority |
| result changes across seeds | SUMO seed, route seed, randomTrips seed, RL seed | separate training randomness from evaluation randomness | robust result |
| emissions/EV output missing | device activation, emission class, battery params, units/version | run one vehicle with expected device output | energy/emission claim |
| collision/safety anomaly | collision options, minGap, step length, insertion checks, SSM/safety output | minimal collision reproduction | safe operation |

## Probe Ordering

Default order:

1. Environment and command.
2. SUMO input construction.
3. Run protocol and TraCI lifecycle.
4. Controller action and state response.
5. Output completeness and metric scope.
6. Stochasticity and sensitivity.
7. Simulator limitation or bug.

Only skip earlier layers when current artifacts already prove they passed.
