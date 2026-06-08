---
name: debugging-audit-for-eclipse-sumo
description: Use when debugging Eclipse SUMO, TraCI, netconvert, duarouter, route, demand, detector, traffic-light, output, teleport, insertion, seed, performance, or reproducibility failures in traffic-simulation workflows.
---

# Debugging Audit for Eclipse SUMO

## Overview

Use this skill to debug experiments using Eclipse SUMO as a closed-loop control problem. The goal is not to make a run continue at any cost; the goal is to identify the root cause, verify it with evidence, and decide whether the experiment can be repaired, rerun, or must be demoted.

## Closed Loop

```text
failure target -> observe artifacts -> classify deviation -> choose one diagnostic probe -> run probe -> compare feedback -> update fault model -> fix or demote
```

## First Response

When the failure is not already diagnosed, start with this compact record:

```text
debug_target:
observed_state:
known_artifacts:
deviation:
constraints:
first_probe:
expected_feedback:
stop_condition:
```

Ask for only the missing artifact needed for the next probe. Prefer exact command lines, Eclipse SUMO logs, output files, and minimal examples over screenshots or descriptions.

## Root-Cause Rule

Do not propose fixes until the fault class is supported by evidence. Do not mask symptoms with `--ignore-route-errors`, `--ignore-errors`, larger retry counts, `--time-to-teleport -1`, GUI-only inspection, or changed demand unless the run is explicitly marked diagnostic.

## Reference Routing

- Closed-loop SUMO debugging workflow: read `references/closed-loop-debugging.md`.
- Symptom-to-evidence triage: read `references/symptom-to-evidence-map.md`.
- Diagnostic gates and demotion rules: read `references/debugging-gates-and-claim-boundaries.md`.

## Fault Classes

Classify every issue into one or more:

- `environment-fault`: SUMO binary, `SUMO_HOME`, Python `traci`, port, working directory, container, OS, or version mismatch.
- `input-construction-fault`: invalid XML, missing files, bad paths, bad network, route infeasibility, wrong vehicle class, missing stops/stations/detectors.
- `demand-realization-fault`: planned demand differs from loaded, inserted, arrived, or completed demand.
- `traci-protocol-fault`: connection, `--remote-port`, `--num-clients`, `setOrder`, `simulationStep`, `traci.close`, stale labels, repeated-run lifecycle.
- `controller-logic-fault`: command issued to wrong domain/object, command overridden, wrong timing, invalid phase/program/state, missing controller log.
- `output-observability-fault`: output disabled, overwritten, parsed before close, missing unfinished vehicles, wrong metric scope.
- `stochasticity-fault`: seed, random trips, stochastic car-following, random departures, RL training, or thread/run nondeterminism.
- `simulator-limitation-or-bug`: behavior is reproducible after construction and protocol are proven; check official docs, issues, and minimal reproduction.

## Debug Outcome

End every debugging pass with:

```text
root_cause:
evidence:
fix_or_next_probe:
claim_status: formal-evidence / diagnostic-demo / construction-invalid / claim-overreach / blocked
residual_risk:
```

If the root cause is unknown, say so and keep the run out of formal evidence.
