# Traffic-Control Reporting

Use this reference for completed SUMO and traffic-control experiments, including signal control, Max-Pressure, MPC, RL, information or prediction modules feeding a controller, connected-vehicle control, transit priority, perimeter control, ablations, demand sweeps, and sensor-robustness studies.

Do not rerun experiments unless the user explicitly requests it. Use Chinese when the user requests a Chinese report; otherwise preserve the requested language.

## Two Modes

- **Brief explanation:** answer the requested number, comparison, or interpretation directly. Check only the evidence needed for that answer.
- **Full report:** read the completed design, configuration, report draft, raw outputs, figures, and current method document before writing. If the experiment is incomplete, label it as a progress report.

## Evidence Chain

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

For an information module, state exactly what it adds to the base controller, how often it updates, where it applies, how the controller consumes it, and whether removing it changes actions or outcomes.

## Controller Information Contract

For every important controller input, state:

- variable, traffic meaning, and unit;
- measured, estimated, predicted, oracle, or evaluation-only status;
- how it is obtained in the simulation;
- physical sensor or data equivalent when one exists;
- spatial and temporal resolution;
- latency, noise, loss, and coverage assumptions;
- which compared methods are allowed to use it.

`TraCI` is an interface, not a sensor model. All-vehicle, lane, or edge state is simulator truth unless a detector, connected-vehicle sampling process, or another observation model is explicitly implemented.

## Controller and Signal Execution

Report the applicable action space and constraints: phase selection, hold or switch, green extension, split, cycle, offset, program, or trajectory action; decision interval; phase order; minimum and maximum green; yellow and all-red; ring/barrier or NEMA rules when used; and fallback behavior.

Distinguish simulation step, vehicle action step, detector sampling and aggregation, estimator update, controller interval, prediction horizon, and signal update.

## Demand and Evaluation Window

Report numeric demand with its spatial unit, arrival process, routes or OD/turning ratios, vehicle mix, and traffic regime. Do not leave low, medium, or high undefined.

State demand injection, warm-up, evaluation, cooling or clearance, termination, and unfinished-vehicle treatment. There is no universal SUMO duration.

Report repetitions or seeds, valid `n`, variability, and whether the design is paired. For learned controllers, separate training from held-out evaluation.

Distinguish technical failure from a controller-induced traffic outcome. Gridlock, spillback, instability, insertion backlog, or unfinished demand may be the result and must not be silently discarded.

## Metrics and Comparison

Choose a small set of primary traffic outcomes tied to the claim. Keep reward, pressure, loss, estimator error, and internal scores separate from delay, travel time, queues, throughput, stops, reliability, safety, emissions, fairness, and priority-user outcomes.

Use the same network, demand realization, routes, seeds, signal constraints, and evaluation population across compared methods. Include an operational baseline, a relevant controller-family baseline, and the same controller without the proposed information module when applicable.

If information differs across methods, separate algorithm benefit from information benefit. Treat full-state or oracle methods as ideal upper bounds unless the sensing model makes them deployable.

If unstable runs, unfinished vehicles, warm-up vehicles, or other samples are excluded, report both the exclusion rule and the corresponding instability, residual-demand, or excluded-sample rate.

## Report Discipline

Lead with the traffic conclusion and its evidence boundary. Recompute summaries from completed raw outputs when practical without changing frozen scenarios, routes, demand, seeds, or controller behavior.

Use verified repository-relative links to the current method document, representative figures, visualization directory when useful, and experiment-data directory. If an artifact does not exist, say so instead of inventing a path.

Keep hashes, call traces, Git history, test counts, internal gates, and exhaustive file inventories out of the main traffic report unless the user explicitly requests a software or reproducibility audit.
