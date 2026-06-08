# Expected Audit Report: Data-Informed Signal Control

## Classification

`claim-overreach` for `bad_case/`; possible `formal-evidence` only after information timing, ablations, baselines, completion, and metric gates are verified.

## Required Findings

- The audit must check whether any feature, weight, prediction, or controller input leaks future outcomes from the same evaluation run.
- MPC-style claims require horizon length, update interval, objective terms, constraints, fallback behavior, and solver/runtime budget.
- Data-informed claims require ablations that remove or freeze the data component while keeping all other controller code and demand conditions paired.
- Baselines should include fixed-time and at least one relevant adaptive comparator when the claim is about control value.
- If SUMO stops before all demand is completed, report completion rate, unfinished vehicles, backlog, teleports, and fixed-horizon metric boundaries.

## Expected Fixes

- Add a decision-time information table for every controller input.
- Add ablations for data source, prediction horizon, objective weights, and fallback policy.
- Use paired seeds and identical routes across all controllers.
- Save controller runtime, infeasible-step count, fallback count, and output metrics per seed.
