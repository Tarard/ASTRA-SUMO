# Task: Data-Informed Signal-Control Audit

Use `$academic-audit-for-eclipse-sumo` to audit this data-informed or MPC-style SUMO/TraCI signal-control experiment before reporting improvements.

## Claimed Result

The controller uses external demand or state estimates to improve signal timing compared with fixed-time, actuated, and max-pressure baselines.

## Available Artifacts

- `bad_case/`: current experiment description with leakage and ablation risks.
- `fixed_case/`: corrected experiment description after applying the checklist.

## Audit Questions

1. Does the controller use only information available at decision time?
2. Are the baselines and ablations sufficient to isolate the value of the data-informed component?
3. Are completion and metric definitions strong enough for a performance claim?
