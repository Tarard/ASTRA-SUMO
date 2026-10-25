# Traffic-Control Reporting

Use this reference for a full report on a completed traffic-control experiment: signal control, Max-Pressure, MPC, RL, connected-vehicle control, transit priority, perimeter control, or an information or prediction module that feeds a controller.

Before writing, read the experiment design, the configurations, the raw outputs, the figures and the current method document. If the experiment is not complete, call the document a progress report.

## Evidence chain

Trace the experiment as:

```text
network and demand
-> raw observation
-> estimation or prediction
-> information supplied to the method
-> controller state and action
-> signal or vehicle execution
-> traffic outcome and uncertainty
```

For an information module, state what it adds to the base controller, how often it updates, where it applies, how the controller uses it, and whether removing it changes the actions or the outcomes.

## Controller inputs

For every important controller input, state:

- the variable, its traffic meaning and its unit;
- whether it is measured, estimated, predicted, oracle, or evaluation-only;
- how the simulation produces it, and the physical sensor or data source that would produce it in the field;
- its spatial and temporal resolution;
- the latency, noise, loss and coverage assumptions;
- which of the compared methods may use it.

## Signal execution

State the action space and its constraints: phase selection, hold or switch, green extension, split, cycle, offset, program or trajectory action; the decision interval; the phase order; minimum and maximum green; yellow and all-red; ring and barrier or NEMA rules when used; and the fallback behavior.

Keep these time scales apart: simulation step, vehicle action step, detector sampling and aggregation, estimator update, controller interval, prediction horizon and signal update.

## Demand and evaluation window

- Give demand as numbers with their spatial unit, the arrival process, the routes or OD and turning ratios, the vehicle mix and the traffic regime. Do not leave "low", "medium" or "high" undefined.
- State the demand injection, warm-up, evaluation, clearance and termination periods. SUMO has no standard duration.
- Report the repetitions or seeds, the valid `n`, the variability, and whether the design is paired. For learned controllers, keep training runs apart from held-out evaluation runs.

## Metrics and links

- Choose a small set of primary traffic outcomes that match the claim.
- Recompute summaries from the raw outputs when practical, without changing frozen scenarios, routes, demand, seeds or controller behavior.
- Link the method document, representative figures and the experiment-data directory with relative paths you have checked. If an artifact does not exist, say so; do not invent a path.
