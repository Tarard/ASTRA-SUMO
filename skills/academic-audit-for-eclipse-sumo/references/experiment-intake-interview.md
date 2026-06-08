# Experiment Intake Interview

Use this before designing, coding, running, comparing, or writing claims for an Eclipse SUMO experiment when the setup is not already explicit.

Ask one focused question at a time. Prefer concrete choices, but allow free-form answers. Use defaults only after stating the assumption.

## Intake Questions

1. **Claim**: What exact research claim or engineering question should the experiment test? What should it not claim?
2. **Scenario and network**: Is the network a toy intersection, corridor, grid, OSM import, benchmark scenario, or field-calibrated network?
3. **Controller identity**: What controller is actually used: static, actuated, delay-based, NEMA, TraCI phase sequence, online state override, Max Pressure, RL, MPC, ramp metering, or another mechanism?
4. **Demand design**: Are demand inputs routes, OD matrices, flows, turn ratios, or benchmark traces? Which demand levels and random seeds will be used?
5. **Observability and sensors**: What information does the controller observe: loop detectors, lane-area detectors, vehicle probes, queue estimates, CV penetration, full SUMO state, or offline outputs?
6. **Baselines**: Which baselines are required for a fair claim: no-control/current timing, fixed-time, actuated/Webster-like, Max Pressure/Max Wave, public benchmark controller, ablation, or another controller?
7. **Completion policy**: Should the simulation run to natural completion, or is it intentionally fixed-horizon? If fixed-horizon, how will completion rate, unfinished vehicles, and backlog be compared?
8. **Metrics**: Which primary and secondary metrics will be reported: completion rate, throughput, average travel time, waiting time, time loss, queue length, stops, mean speed, emissions, fuel, runtime, or tail metrics?
9. **Outputs and hard gates**: Which files will be written: `summary.xml`, `tripinfo.xml`, SUMO log, TLS states/switches/programs, detector outputs, controller event log, metadata, hashes, and runtime?
10. **Sensitivity and ablation**: Which axes will be varied: demand, seeds, penetration rate, noise, detector failure, route uncertainty, control interval, horizon length, or module removal?
11. **Success and demotion criteria**: What result counts as formal evidence, diagnostic evidence, stress evidence, construction failure, or claim overreach?
12. **Publication boundary**: What exact wording is allowed if all gates pass? What wording remains prohibited?

## Experiment Readiness Record

Produce this record and ask the user to confirm it before implementation or execution.

```markdown
## Experiment Readiness Record

| Field | Confirmed Value | Open Risk |
|---|---|---|
| Claim | `{specific supported claim}` | `{unsupported or ambiguous claim wording}` |
| Network/scenario | `{network source, topology, scale, calibration state}` | `{missing calibration or topology risk}` |
| Controller identity | `{actual SUMO/TraCI/controller mechanism}` | `{identity or API-boundary risk}` |
| Demand design | `{routes, OD/flows, demand regimes, seeds}` | `{unserved demand, stochastic, or calibration risk}` |
| Observability/sensors | `{available detector/probe/state information}` | `{unrealistic observability or missing-data risk}` |
| Baselines | `{selected baselines and reasons}` | `{weak, missing, or unfair baseline risk}` |
| Completion policy | `{natural completion or fixed horizon, denominator, unfinished handling}` | `{censoring, backlog, or biased comparison risk}` |
| Metrics | `{primary/secondary metrics and exact formulas}` | `{undefined metric or reward/evaluation mismatch risk}` |
| Outputs/hard gates | `{required outputs and pass/fail gates}` | `{missing output or validation risk}` |
| Sensitivity/ablation | `{planned axes and removed modules}` | `{robustness or mechanism-identification risk}` |
| Runtime/reproducibility | `{SUMO version, command, hashes, runtime criterion}` | `{repeatability or real-time feasibility risk}` |
| Claim boundary | `{allowed wording if gates pass}` | `{prohibited wording until more evidence exists}` |

Decision: ready / needs design revision / diagnostic-only / blocked
Next allowed action: `{specific next action allowed by the decision}`
```

## Confirmation Gate

The user must confirm the record before the agent writes experiment code, edits SUMO files, runs SUMO, or drafts result claims.

If the user explicitly skips intake:

1. list the unknown fields;
2. mark the run `diagnostic-only`;
3. proceed only with actions that cannot be mistaken for formal evidence.
