# Task: Max-Pressure Controller Audit

Use `$simulation-helper-skill-for-eclipse-sumo` to audit this max-pressure SUMO/TraCI signal-control experiment before writing a performance claim.

## Claimed Result

The max-pressure controller outperforms fixed-time and actuated control on queue length, delay, and throughput.

## Available Artifacts

- `bad_case/`: current experiment description with detector and pairing risks.
- `fixed_case/`: corrected experiment description after applying the checklist.

## Audit Questions

1. Does the controller's pressure calculation match the SUMO network and detectors?
2. Are baselines paired fairly across seeds, demand, outputs, and horizon?
3. Which claims are still unsupported?
