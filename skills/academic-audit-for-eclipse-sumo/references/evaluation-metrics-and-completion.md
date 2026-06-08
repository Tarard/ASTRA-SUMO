# Evaluation Metrics and Completion

Use this when selecting metrics, auditing reported performance, or comparing controllers in Eclipse SUMO experiments.

## Completion-First Rule

Prefer natural completion for performance evaluation. In TraCI runs, keep stepping until `traci.simulation.getMinExpectedNumber() == 0` unless the experiment is explicitly a fixed-horizon or online-control study.

Natural completion means:

- route files and planned departures for the evaluated demand are exhausted;
- no vehicles remain running in the network;
- no vehicles remain waiting for insertion;
- no unresolved teleports, collisions, vaporized vehicles, or discarded vehicles explain the outcome.

If a run stops at a fixed horizon, by timeout, by manual stop, or by controller failure, do not treat arrived-only trip averages as full-model performance. Compare completion and backlog first, then report censored traffic metrics separately.

## Completion Metrics

Always compute these before comparing travel-time metrics:

| Metric | Definition | Direction | Notes |
|---|---|---:|---|
| Planned demand | Vehicles expected to be evaluated | fixed | Count vehicles/flows within the evaluation window, not vehicles generated after the intended horizon. |
| Inserted vehicles | Vehicles that entered the network | higher is better when demand is fixed | From `summary.xml` final `inserted` or logs. |
| Arrived vehicles | Vehicles that reached destination | higher is better | From `summary.xml` final `arrived` or completed `tripinfo` records. |
| Completion rate | `arrived / planned_demand` | higher is better | Use the same denominator for every model in a comparison. |
| Insertion rate | `inserted / planned_demand` | higher is better | Separates demand admission failure from downstream congestion. |
| Unfinished vehicles | vehicles still running or waiting at stop time | lower is better | Report running and insertion backlog separately. |
| Teleport/discard/vaporize count | vehicles removed abnormally | lower is better | These are not successful completions unless the claim explicitly treats removal as failure. |

When completion rates differ, rank models by completion feasibility before ranking them by average trip metrics. A model with lower average travel time but many unfinished vehicles may only look good because hard trips were excluded.

## Common Paper Metrics

Use exact SUMO-backed definitions in the methods section. Do not mix reward definitions, online observations, and final evaluation metrics without naming the difference.

| Metric | SUMO-backed operational definition | Direction | Primary source |
|---|---|---:|---|
| Average travel time (ATT) | Mean `tripinfo.duration`: time from vehicle departure to route completion | lower | `tripinfo.xml` |
| Average waiting time (AWT) | Mean `tripinfo.waitingTime`: time with speed `<= 0.1 m/s`, excluding scheduled stops | lower | `tripinfo.xml` |
| Average time loss / delay | Mean `tripinfo.timeLoss`: time lost relative to ideal travel speed; includes waiting time | lower | `tripinfo.xml` |
| Depart delay | Mean `tripinfo.departDelay`: time a vehicle waited before entering the network | lower | `tripinfo.xml` |
| Throughput | Number of vehicles that finish in the evaluation window; optionally normalize to veh/h | higher | `summary.xml` final `arrived` or trip count |
| Average queue length (AQL) | Time-average count of halted/jammed vehicles on controlled approaches | lower | lane-area detectors, lane halting counts, or controller logs |
| Maximum or tail queue | Max or 95th percentile queue over time/approaches | lower | detectors or controller logs |
| Mean speed | Mean speed over vehicles in the network or trip distance divided by duration | higher | `summary.xml`, edge/lane outputs, or tripinfo |
| Stops / halting count | Stop events or vehicles below halting threshold | lower | `tripinfo.waitingCount`, `summary.halting`, lane-area `startedHalts` |
| Emissions and fuel | Per-trip totals such as `CO2_abs`, `NOx_abs`, `PMx_abs`, `fuel_abs` | lower | tripinfo emissions device |
| Runtime feasibility | Controller solve time and SUMO step computation time relative to the control interval | lower | controller logs and `summary.duration` |

## Queue Definition Discipline

Queue length is not a single universal SUMO field. State the sensor and threshold:

- lane-area detector jam metrics: use `meanMaxJamLengthInVehicles`, `jamLengthInVehiclesSum`, or related E2 fields;
- TraCI lane halting counts: count vehicles below the halting threshold on chosen lanes;
- controller-estimated queue: label as an estimate and report the detector/probe penetration assumptions.

Do not compare queue results across papers or controllers unless the lane set, detector length, speed threshold, aggregation period, and averaging formula match.

## Fixed-Horizon Studies

Fixed-horizon experiments are valid when the research question is online service within a time window. They require extra reporting:

- evaluation horizon and warm-up period;
- planned demand within that horizon;
- inserted, arrived, running, waiting, and discarded counts at horizon end;
- `--tripinfo-output.write-unfinished` when unfinished vehicles affect conclusions;
- cumulative delay, queue, vehicle-hours, or throughput over the full horizon, not only completed trips.

If a fixed-horizon paper-style metric is used, write: "within the fixed evaluation window" and report completion rate beside every travel-time or waiting-time table.

## Statistical Reporting

For publishable claims:

- run multiple seeds when demand, routing, model training, or controller decisions are stochastic;
- report mean and variation, such as standard deviation, standard error, or confidence interval;
- define improvement as `(baseline - candidate) / baseline` for lower-is-better metrics and `(candidate - baseline) / baseline` for higher-is-better metrics;
- keep the same demand, seeds, horizon, warm-up, outputs, and denominators across all compared models.

## Source Anchors

- SUMO TraCI shutdown and `getMinExpectedNumber`: https://sumo.dlr.de/docs/TraCI/index.html
- SUMO Python TraCI loop example: https://eclipse.dev/sumo/docs/TraCI/Interfacing_TraCI_from_Python.html
- SUMO Summary output: https://sumo.dlr.de/docs/Simulation/Output/Summary.html
- SUMO TripInfo output: https://eclipse.dev/sumo/docs/Simulation/Output/TripInfo.html
- SUMO lane-area detector output: https://eclipse.dev/sumo/docs/Simulation/Output/Lanearea_Detectors_%28E2%29.html
- SUMO-RL documentation: https://lucasalegre.github.io/sumo-rl/documentation/sumo_env/
- RESCO benchmark paper: https://openreview.net/forum?id=LqRSh6V0vR
- LibSignal project: https://darl-libsignal.github.io/
