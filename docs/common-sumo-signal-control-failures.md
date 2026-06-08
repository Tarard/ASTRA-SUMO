# Common SUMO Signal-Control Experiment Failures

Use this checklist before reporting results from SUMO/TraCI traffic signal control experiments.

## 1. SUMO Runs but `tripinfo.xml` Is Empty

Risk: the simulation executed, but no vehicles arrived or the output was parsed before SUMO closed the file.

Check:

- Was `--tripinfo-output` enabled for every run?
- Did vehicles depart, insert, arrive, teleport, or remain unfinished?
- Was the output path unique per controller, seed, and demand level?
- Was the file parsed only after SUMO or TraCI closed cleanly?

Audit response: report completion rate and unfinished vehicles before using travel-time averages.

## 2. TLS Phase Index Does Not Match the Intended Movement

Risk: the controller sets a phase index that does not serve the movement assumed by the algorithm.

Check:

- Does the loaded `.net.xml` contain the expected TLS program?
- Are phase indices, state strings, lane links, and movement groups mapped explicitly?
- Are yellow and all-red phases handled consistently?
- Does TLS switch output confirm the actual sequence?

Audit response: reject controller-identity claims until the movement-green mapping is proven.

## 3. Max-Pressure Controller Compares Unpaired Seeds

Risk: the max-pressure controller appears better because the route realization changed.

Check:

- Are route files, demand levels, random seeds, and departure distributions identical across controllers?
- Are failed insertions, discarded routes, and teleports reported?
- Are all controllers run over the same horizon and output intervals?

Audit response: demote superiority claims to diagnostic-only until paired comparisons are restored.

## 4. `edgeData` Interval Differs Across Controllers

Risk: queue, speed, flow, or delay metrics are aggregated over incompatible time windows.

Check:

- Are `edgeData`, `laneData`, `summary`, `tripinfo`, and TLS outputs configured identically?
- Are output files overwritten between runs?
- Are aggregation intervals documented in the method section?

Audit response: require aligned output definitions before comparing controller metrics.

## 5. Data-Informed Weights Leak Future Outcome Information

Risk: a controller uses tuning, predictions, or weights that depend on full-run outcomes from the evaluation scenario.

Check:

- For every controller input, was the information available at decision time?
- Were weights tuned on separate training/calibration scenarios?
- Are ablations included for data source, prediction horizon, objective weights, and fallback policy?

Audit response: treat performance claims as invalid until information timing and ablations are documented.

## 6. Route File Silently Changes Demand Across Baselines

Risk: `randomTrips.py`, `duarouter`, route repair, or ignored route errors changes demand differently across methods.

Check:

- Are route generation command lines and seeds saved?
- Are route files committed or checksummed?
- Are invalid routes, discarded vehicles, and rerouting settings reported?
- Are all controllers using identical route inputs?

Audit response: separate demand-generation evidence from controller-performance evidence.

## 7. Yellow and All-Red Handling Differs Across Methods

Risk: one controller receives shorter lost time or unsafe transitions than another.

Check:

- Are yellow, all-red, min-green, max-green, and clearance constraints identical across controllers?
- Does TraCI command only green phases while SUMO handles yellow transitions, or does the controller command all phases directly?
- Are unsafe state transitions checked against the TLS state strings?

Audit response: do not compare delay or throughput until safety and lost-time policies are paired.

## 8. Simulation Stops Before Demand Completes

Risk: arrived-only metrics reward controllers that leave more vehicles unfinished.

Check:

- Was the run allowed to finish naturally, or stopped at a fixed horizon?
- Were unfinished vehicles written and counted?
- Are inserted, arrived, teleported, discarded, and pending vehicles reported?

Audit response: use fixed-horizon wording and completion-aware metrics unless all comparable runs complete naturally.

## 9. Controller Runtime or Fallback Behavior Is Missing

Risk: MPC-style or optimization-based controllers silently skip decisions or fall back under load.

Check:

- Are decision runtime, infeasible steps, fallback count, and skipped updates logged?
- Is the simulation step synchronized with controller updates?
- Are slow decisions included in the experimental condition?

Audit response: require runtime and fallback logs before claiming deployable or real-time performance.
