# Task: Signal-Control Experiment Audit

Use `$simulation-helper-skill-for-eclipse-sumo` or Torii to audit one of the bundled SUMO/TraCI signal-control cases before reporting a performance claim.

## Available Cases

- [`fixed_time/`](fixed_time/task.md): fixed-time baseline readiness.
- [`max_pressure/`](max_pressure/task.md): pressure calculation and paired comparison readiness.
- [`data_informed/`](data_informed/task.md): causal information use, ablations, and metric evidence.

## Audit Questions

1. What is the exact controller identity and what evidence proves it ran?
2. Are routes, demand, seeds, horizon, outputs, TLS mapping, and completion criteria paired fairly?
3. Are unfinished vehicles, teleports, collisions, and output mismatches reported before averages?
4. Which claim label is supported: `formal-evidence`, `diagnostic-demo`, `construction-invalid`, or `claim-overreach`?
