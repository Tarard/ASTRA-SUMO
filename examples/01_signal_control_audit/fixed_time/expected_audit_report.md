# Expected Audit Report: Fixed-Time Baseline

## Classification

`construction-invalid` for `bad_case/`; eligible for `diagnostic-demo` or `formal-evidence` only after the `fixed_case/` gates are satisfied by real SUMO outputs.

## Required Findings

- The bad case does not prove that the fixed-time controller and adaptive controllers used identical demand, seeds, simulation horizon, and output settings.
- `tripinfo.xml`, `summary.xml`, TLS switch output, and route completion evidence are required before travel-time or waiting-time claims.
- If vehicles remain unfinished at the stop time, arrived-only averages cannot support a controller-superiority claim without completion-rate and backlog reporting.
- The fixed-time baseline must record cycle length, phase sequence, yellow/all-red handling, and movement-green mapping.

## Expected Fixes

- Pin the network, routes, random seed list, additional files, output paths, and SUMO version.
- Use unique output directories per controller and seed.
- Write unfinished vehicles or run until natural completion when the claim depends on per-trip metrics.
- Report fixed-time timing parameters as an experimental condition, not as an implementation detail.
