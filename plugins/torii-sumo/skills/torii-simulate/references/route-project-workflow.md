# Route Project Workflow

Use this reference when the user points Torii at an existing SUMO/TraCI project, asks what to do next, mixes several requests, or provides partial code, logs, outputs, or results.

First identify the current task boundary. Do not load every reference by default.

```text
user request -> current state -> deviation -> topic skill -> smallest next action
```

## Topic Handoff

- road, lane, junction, signal-device, OSM, or construction problem -> `$torii-build`;
- detector, count, route-demand, calibration, or fixed-network replay problem -> `$torii-calibrate`;
- environment, runtime, controller, code, debugging, or experiment-design problem -> stay in `$torii-simulate`;
- completed metrics, comparison, report, claim, or release problem -> `$torii-report`.

For an executable Torii operation, read `torii workflows --json`, inspect the chosen scenario, and preserve the user's original target, source authority, and requested action. Missing inputs remain missing. Do not fall back to a different task because one scenario is blocked.

## Project Screen

```text
target:
current_state:
deviation:
available_artifacts:
missing_artifacts:
environment_state:
network_state:
demand_state:
controller_state:
output_state:
claim_state:
next_step:
```

## Local Simulation References

- environment missing or stale -> `references/preflight-sumo-environment.md`
- new or vague experiment -> `references/interactive-experiment-intake.md`
- confirmed experiment plan -> `references/plan-experiment.md`
- SUMO/TraCI fault -> `references/debug-sumo-traci.md`
- confusing result that needs a new diagnostic contrast -> `references/experiment-problem-solving.md`
- controller boundary -> `references/audit-sumo-controllers.md`
- controller-family design -> `references/sumolights-controller-patterns.md`
- code change -> `references/develop-and-verify-code.md`
- SUMO semantics -> `references/learn-sumo-knowledge.md`

If the current state is not observable, ask for the smallest artifact that would expose it: a config, command, log, output file, result table, or network/demand manifest.

End with the selected topic, missing evidence, next action, stopping condition, and claim status.
