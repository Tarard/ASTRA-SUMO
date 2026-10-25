# Debug and Diagnose SUMO Runs

Use when SUMO, TraCI, netconvert or duarouter fails, a run hangs, or a completed run gives a result nobody can explain. Work from exact commands, logs, configs and output files, not from screenshots or descriptions. Keep the network, routes, seed, horizon and output paths fixed, and change one variable per rerun; read-only probes may run together. The same holds for a code fix, including its yellow and all-red handling.

Name the fault class in the answer: environment, input construction, demand realization, TraCI protocol, controller logic, output observability, stochasticity, or SUMO limitation or bug.

## Environment and toolchain

- Preflight and `sumo_run_config` look only on `PATH`; preflight only reports `SUMO_HOME`, and its `payload.python_module_origins` shows where `traci` and `sumolib` were imported from. The smoke test looks in `SUMO_HOME/bin` before `PATH` and needs `SUMO_HOME/tools/randomTrips.py`. If `SUMO_HOME` and `PATH` point to different installations, make them agree first. With the `eclipse-sumo` pip package, `SUMO_HOME` is the `sumo` folder in that environment's `site-packages`.
- `sumo_run_minimal_smoke` runs netgenerate (2x2 grid), `randomTrips.py` (seed 1, 60 s), duarouter and `sumo -c smoke.sumocfg` in `work_dir`. Each entry of `commands` has its `returncode` and `stderr`. A pass proves only the toolchain.

| Symptom | Fault class | First probe |
|---|---|---|
| binary not found, or `--version` fails | environment | `SUMO_HOME`, `PATH`, the installation |
| `traci` or `sumolib` import fails | environment | the Python that runs ASTRA; `python_module_origins` |
| netconvert or netgenerate fails | input construction | the exact command, the input file, the SUMO version |
| duarouter or routing fails | demand realization | disconnected or invalid edges, vehicle-class permissions; open the trips XML, because shell quoting can corrupt its attributes |
| SUMO runs but outputs are missing | output observability | output options in the `.sumocfg`; relative paths resolve from the config folder |
| TraCI cannot connect | environment or input | start the same config with `sumo -c` and no controller; then port reuse and command order |

## Runtime symptoms

| Symptom | Likely causes | First probes |
|---|---|---|
| empty `tripinfo` | no arrivals, end time too short, wrong output wiring | final `summary` arrived and running counts, `end`, output paths |
| low arrived count | short horizon, insertion backlog, disconnected routes, a controller holding red | `waiting` in `summary`; `astra network routeability` |
| vehicles never depart | insertion backlog, route capacity, depart attributes | `waiting`, `departLane` and `departSpeed`, capacity of the first edge |
| many teleports | gridlock, invalid demand, signal timing, missing connections | the lanes named in the teleport warnings, their connections and the controlling TLS |
| high waiting time | demand outside the intended scope, wrong phase-lane mapping, wrongly joined TLS, unsuitable controller | `tlLogic` cycle lengths and longest phases, phase strings against controlled links |
| TLS never changes | wrong TLS id, controller loop not running, program overridden | the TraCI command log, a TLS state output, the active program id |
| detector values look wrong | lane mapping, position, aggregation interval, network rebuilt after the mapping | detector lanes against the current `.net.xml` |
| results change between runs | seed, regenerated routes, overwritten outputs | seeds, route-file hashes, output paths |
| run hangs | TraCI loop condition, unfinished vehicles, insertion backlog | the loop's end condition, and `running` and `waiting` at the last step |

If a probe does not reduce the uncertainty, ask the user for a smaller artifact: the config, the command, the log or one output file.

## Smallest reproduction

Reproduce at the lowest level that shows the fault before rerunning a full experiment:

1. a pure function (parser, formula, phase map) with fixed input;
2. a tiny XML fixture (TLS states, detector map, route or config wiring);
3. mocked TraCI state with the expected command;
4. a short headless SUMO run with a fixed seed;
5. the paired experiment.

## Valid but puzzling results

1. Separate the observation from its explanation, and name the one uncertainty that blocks the claim. If you cannot name it, collect observations before running more.
2. If completion differs between the runs, repair completion before reading performance.
3. If the runs are not paired, rerun them paired or call the comparison unpaired.
4. If a controller did not log its actions, add logging or audit where it issues commands.
5. Compare against the strongest earlier result under the same scenario before inventing new logic. If the old and new methods differ in many ways, compare their action logs (time, signal, state, target, reason) under the same scenario instead of running a bigger matrix.
6. If an action helped in a fixture but the runtime controller fails, keep the action fixed and test only the trigger that decides when and where to act. A fixture or oracle trigger is weaker evidence than a trigger that uses observable runtime data.
7. If the trigger fires too often, check the action count before proposing a new architecture.
8. Broaden seeds or perturbations only after the mechanism survives one paired contrast.

Classify a worse result as over-action, under-action, wrong scope, wrong timing or an invalid metric. A negative result describes a failure mode; it does not by itself end the research direction. A useful action can still come from a wrong trigger.
