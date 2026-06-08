# Expected Audit Report: Max-Pressure Controller

## Classification

`claim-overreach` for `bad_case/`; possible `formal-evidence` only after detector mapping, paired seeds, output gates, and ablations are verified.

## Required Findings

- Max-pressure claims require a movement map: incoming lanes, outgoing lanes, permitted turns, saturation assumptions, and queue/detector definitions.
- Detectors or queue proxies must be aligned to the actual SUMO lane IDs used after `netconvert`.
- Baseline comparisons must be paired by identical network, routes, demand seeds, simulation horizon, and output configuration.
- Throughput claims must report inserted, arrived, unfinished, teleported, and discarded vehicles.
- Controller logs should show selected phases, pressures, min-green, yellow/all-red handling, and skipped phase decisions.

## Expected Fixes

- Add a detector-to-movement table and verify it against the loaded `.net.xml`.
- Use a shared seed list for every controller.
- Save one output directory per controller/seed/demand level.
- Include ablations for pressure definition, phase constraints, detector availability, and demand level.
