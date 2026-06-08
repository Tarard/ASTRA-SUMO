# Eclipse SUMO Official Operational Lessons

Use this when converting Eclipse SUMO official documentation, FAQ patterns, and reproducibility guidance into audit rules.

## High-Value Rules

| Rule | Audit action | Demote when |
|---|---|---|
| TraCI owns the run horizon | In TraCI server mode, verify the client loop, close behavior, and natural completion criterion. | A run relies on `--end` as if SUMO will stop itself under TraCI. |
| GUI needs explicit start semantics | Record `sumo` vs `sumo-gui`, `--start`, and whether manual play/stepping occurred. | GUI interaction changes the run or replaces headless evidence. |
| Multi-client TraCI is a synchronization protocol | Record `--num-clients`, each client role, `setOrder`, and whether every client calls `simulationStep`. | Missing client order or a client can stall/alter the step sequence. |
| Output files are only valid after shutdown | Call `traci.close()`, wait for SUMO to exit, then parse output files. | Results are read while SUMO is still closing or before all clients close. |
| Connection failure is often a SUMO-side input failure | Add SUMO `--log`, inspect the SUMO log, and fix input errors before Python-side debugging. | `FatalTraCIError` is treated as a controller bug without checking SUMO logs. |
| Randomness is an experimental variable | Record SUMO seed, route-generation seed, `--random`, random flows, speed distributions, driver `sigma`, departure offsets, and RL/model seeds. | A result cannot be reproduced or uses a single favorable random realization. |
| Planned demand is not served demand | Compare planned, loaded, inserted, waiting-for-insertion, running, arrived, discarded, and vaporized vehicles. | Only inserted or arrived vehicles are reported while insertion backlog exists. |
| Step length affects insertion and control | Record `--step-length` and controller decision interval. Check insertion spacing artifacts when demand is dense. | Controllers are compared with different step lengths or unreported insertion artifacts. |
| Teleports are failure evidence unless intentionally studied | Count teleports by reason and inspect whether they come from wrong lanes, disconnections, yield conflicts, jams, collisions, or rail/deadlock constraints. | Teleported vehicles are silently counted as normal successful traffic. |
| `--time-to-teleport -1` is diagnostic, not a fix | If disabling teleports, also report gridlock/backlog and explain why the setting matches the research question. | Teleports are hidden without proving the network/demand/control is valid. |
| Random trips need route validation | Validate `randomTrips.py` output with routing and route checks; record discarded trips. | A route file was generated from random trips but invalid/disconnected trips were ignored. |
| Route repair changes the experiment | If `duarouter --repair` or route fixing is used, record what changed and keep pre/post route artifacts. | Repaired routes are treated as the original demand without disclosure. |
| `--ignore-errors` and `--ignore-route-errors` are demotion flags | Use only for diagnostic exploration unless the paper claim explicitly justifies the discarded/invalid demand. | Formal evidence depends on ignored errors. |
| Fixed end-time comparisons require missing-vehicle accounting | Use `--tripinfo-output.write-unfinished`, `--duration-log.statistics`, and total travel-time plus depart-delay accounting. | Average travel time ignores vehicles that did not depart or arrive. |
| Statistics output has separate vehicle/person/ride scopes | Match metrics to the entity being studied: vehicles, pedestrians, rides, transports, or persons. | Person-trip or public-transport claims use only vehicle-trip statistics. |

## TraCI Run Discipline

For formal TraCI experiments:

1. Start SUMO with explicit logs and output paths.
2. If multiple clients are used, set `--num-clients` and call `traci.setOrder(...)` in every client before stepping.
3. Step until the chosen horizon:
   - natural completion: `traci.simulation.getMinExpectedNumber() == 0`;
   - fixed horizon: documented simulation time with unfinished-vehicle accounting;
   - online/HIL: documented external stop condition and backlog accounting.
4. Call `traci.close()` from every client.
5. Wait for the SUMO process to exit before parsing output files.
6. Treat any SUMO warning/error/log anomaly as part of the evidence record.

## Demand and Route Validation

Before performance analysis:

- verify network loading without fatal errors;
- verify route connectivity for the relevant vehicle classes;
- record invalid, discarded, repaired, or unreachable trips;
- separate planned demand from realized inserted demand;
- avoid `--ignore-route-errors` in formal evidence;
- treat `duarouter --repair`, `routecheck.py --fix`, or automatic route repair as a demand-transformation step.

If demand generation uses `randomTrips.py`, record:

- generation command and seed;
- begin/end/period or insertion-rate settings;
- whether the router discarded disconnected trips;
- the final planned-demand denominator used in metrics.

## Teleport and Insertion Triage

When teleports, insertion backlog, or low arrival ratios occur, diagnose in this order:

1. route and vehicle-class connectivity;
2. wrong-lane insertion near turn pockets or motorway weaving;
3. junction priority and signal timing;
4. demand exceeding network capacity;
5. collision or unsafe insertion settings;
6. step-length and depart-position artifacts.

Do not tune a controller to hide these symptoms until the construction cause is known.

## Output and Statistics Discipline

For repeated runs, set a unique output prefix or run directory per seed/scenario. Never overwrite outputs from another model or seed.

Use `--statistic-output` and `--duration-log.statistics` when comparing fixed end-time runs. For fair travel-time comparison with missing vehicles, include:

```text
vehicles.inserted * (vehicleTripStatistics.duration + vehicleTripStatistics.departDelay)
+ vehicles.waiting * vehicleTripStatistics.departDelayWaiting
```

This formula is appropriate only when the statistics fields are generated and the compared simulations use the same planned demand denominator.

## Source Anchors

- TraCI startup, shutdown, multi-client behavior, `--end`, and `getMinExpectedNumber`: https://eclipse.dev/sumo/docs/TraCI/index.html
- TraCI protocol `setOrder` and client synchronization: https://eclipse.dev/sumo/docs/TraCI/Protocol.html
- Python TraCI troubleshooting and natural-completion loop: https://eclipse.dev/sumo/docs/TraCI/Interfacing_TraCI_from_Python.html
- Output and output-prefix behavior: https://sumo.dlr.de/docs/Simulation/Output/index.html
- Summary output counters: https://sumo.dlr.de/docs/Simulation/Output/Summary.html
- Statistic output and fair fixed-end comparison: https://sumo.dlr.de/docs/Simulation/Output/StatisticOutput.html
- TripInfo unfinished vehicles and metrics: https://eclipse.dev/sumo/docs/Simulation/Output/TripInfo.html
- Vehicle insertion and depart delay: https://eclipse.dev/sumo/docs/Simulation/VehicleInsertion.html
- Teleport causes and meaning: https://sumo.dlr.de/docs/Simulation/Why_Vehicles_are_teleporting.html
- Randomness and reproducibility: https://eclipse.dev/sumo/docs/Simulation/Randomness.html
- Shortest/optimal routing and route repair: https://sumo.dlr.de/docs/Demand/Shortest_or_Optimal_Path_Routing.html
- Route checking tools: https://sumo.dlr.de/docs/Tools/Routes.html
- Random trip generation: https://eclipse.dev/sumo/docs/Tools/Trip.html
