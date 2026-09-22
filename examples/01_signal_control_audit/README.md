# Signal-Control Audit Examples

This folder groups the three signal-control audit examples under one entry point.

Use Torii when a SUMO/TraCI signal-control result is about to be reported, compared, or reused as a baseline. The audit goal is not to make a controller look good; it is to decide whether the experiment evidence supports the claimed result.

## Cases

| Case | Focus |
|---|---|
| [`fixed_time`](fixed_time/task.md) | Whether a fixed-time run can be used as a formal baseline. |
| [`max_pressure`](max_pressure/task.md) | Whether pressure calculation, detector mapping, and paired baselines support a max-pressure claim. |
| [`data_informed`](data_informed/task.md) | Whether external demand or state estimates are time-causal and isolated by ablations. |

## Workflow

1. Read the case task.
2. Compare `bad_case/` with `fixed_case/`.
3. Use the expected report as the minimum audit standard.
4. Report completion, unfinished vehicles, teleports, pairing, controller identity, and missing evidence before metric averages.

Claim status remains `diagnostic-demo` unless the required route, demand, TLS, output, controller-log, and completion evidence is present.
