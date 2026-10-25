# Evaluate and Report Results

Use this reference to decide what completed SUMO outputs support before writing a comparison, table, figure caption or paper claim.

## What a formal comparison needs

For every run: the `.sumocfg`, the net, route and additional files, the exact command, the SUMO version, the seed, `summary.xml` and `tripinfo.xml`, edge or lane outputs when the claim is about them, the controller log, and the SUMO warnings and errors.

Demote a run to a diagnostic, or reject it, when:

- an output is missing, empty, overwritten, or from a different run;
- route errors, insertion failures or teleports are not reported;
- the run stopped before the declared horizon without an explanation;
- the controller log does not show that the intended method was active.

## Metric definitions

Define every metric you report:

- travel time: departure to arrival, and whether unfinished vehicles are excluded, penalized or reported separately;
- waiting time: SUMO `waitingTime` (time at speed of 0.1 m/s or less, scheduled stops excluded) and its aggregation;
- delay: SUMO `timeLoss` (time lost against the vehicle's ideal speed), or the reference travel time used for another delay formula;
- queue: detector, edge or lane; speed or jam threshold; aggregation interval;
- throughput: arrivals, crossings at a named point, or detector counts;
- stops: definition and speed threshold;
- emissions and fuel: emission model, vehicle types, output interval;
- distribution: percentile, maximum, or movement-level aggregation.

## Baselines and ablations

- Compare against an operational baseline (fixed-time or SUMO actuated), a baseline from the same controller family (for example max-pressure for a pressure-based method), and the proposed method without its new information module. Use a no-control baseline only when the claim needs one.
- Change one mechanism per ablation.
- Compute every metric with the same code and the same evaluation window for all methods.
- A full-state or oracle method is an upper bound, not a deployable baseline, unless its sensing model makes it deployable.

## Claim classes

The commands named in SKILL.md return `construction-invalid`, `construction-check` or `diagnostic-demo`. Other ASTRA tools may return other labels; read them as given. `formal-evidence` is a label you assign only when every condition above holds. The other classes describe diagnostic or failed evidence.

| Class | Allowed wording |
|---|---|
| `formal-evidence` | "In paired SUMO experiments with stated seeds, demand, completion and metrics, ..." |
| `diagnostic-demo` | "This run shows the behavior under a constructed scenario ..." |
| `stress-diagnostic` | "This stress case exposes sensitivity or a failure mode ..." |
| `construction-invalid` | "The run is not evidence about the controller because its construction failed ..." |
| `claim-overreach` | "The result does not support the stated claim without more evidence ..." |
| `blocked` | "The claim is blocked until the missing artifacts or reruns exist ..." |

## Words that need evidence

- "better": paired baselines and completion evidence;
- "optimal": a formal optimization problem and solver evidence;
- "realistic": calibration against field data or field validation;
- "robust": sensitivity or stress tests;
- "NEMA compliant": the NEMA parameters and the observed signal behavior.
