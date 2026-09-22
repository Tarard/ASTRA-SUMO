# Debug SUMO/TraCI

Use this reference for SUMO, TraCI, netconvert, duarouter, route, demand, detector, TLS, output, teleport, insertion, seed, performance, or reproducibility failures.

```text
failure -> observe artifacts -> classify fault -> choose one probe -> compare feedback -> fix, rerun, or demote
```

## Observe

Prefer exact commands, logs, configs, output files, completion counts, controller traces, and minimal reproductions over screenshots or descriptions.

Check the environment first when the SUMO executable, Python environment, `SUMO_HOME`, `traci`, or `sumolib` identity is uncertain.

## Fault Classes

- `environment-fault`
- `input-construction-fault`
- `demand-realization-fault`
- `traci-protocol-fault`
- `controller-logic-fault`
- `output-observability-fault`
- `stochasticity-fault`
- `simulator-limitation-or-bug`

## Common First Probes

| Symptom | First probes |
|---|---|
| SUMO binary missing | `sumo --version`, `SUMO_HOME`, `PATH` |
| `traci` import fails | Python executable, environment, `traci.__file__` |
| netconvert or duarouter fails | exact command, input file, invalid edges, version |
| empty tripinfo | departures, arrivals, horizon, output path |
| vehicles never depart | insertion backlog, route capacity, depart settings |
| many teleports | gridlock, invalid demand, signal timing, network errors |
| TLS does not change | TLS id, controller loop, command timing, program override |
| detector values look wrong | lane mapping, placement, aggregation interval, changed network |
| results are unstable | seed, regenerated routes, overwritten outputs, floating state |
| run hangs | TraCI loop condition, unfinished vehicles, insertion backlog |

## Probe Rules

- Run one probe at a time.
- Keep route, demand, seed, horizon, and outputs unchanged unless that probe targets them.
- Do not hide faults with permissive flags or changed teleport behavior and then present the run as formal evidence.
- If TraCI fails, first prove SUMO can start from the same config without the controller.
- If the probe does not reduce uncertainty, stop and ask for a smaller artifact or reproduction.

## Outcome

```text
root_cause:
evidence:
fix_or_next_probe:
claim_status:
residual_risk:
```

If a resolved case produces a reusable lesson, hand the lesson to `$astra-report` for privacy-safe capture rather than keeping a second debugging skill.
