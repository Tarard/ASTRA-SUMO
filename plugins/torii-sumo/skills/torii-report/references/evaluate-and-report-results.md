# Evaluate and Report Results

Use this reference when reviewing SUMO outputs, metrics, baselines, ablations, validation evidence, plots, tables, or paper/report claims.

The goal is not to make results look good. The goal is to decide what the evidence actually supports.

## Output Hard Gates

Formal evidence requires readable runtime artifacts:

```text
sumocfg:
net_file:
route_file:
additional_files:
command:
sumo_version:
run_seed:
summary_output:
tripinfo_output:
edge_or_lane_output:
tls_or_controller_log:
warnings_errors:
```

Reject or demote a run if:

- required output files are missing, empty, overwritten, or from mismatched runs;
- route errors, insertion failures, or teleports are unreported;
- the simulation stopped before the declared horizon without explanation;
- controller logs do not prove the intended method was active;
- different methods use different route, demand, seed, horizon, output interval, or completion criteria.

## Completion-First Rule

Before travel time, delay, waiting time, queue, throughput, emissions, or speed averages, report:

```text
departed:
arrived:
running_at_end:
waiting_for_insertion:
teleported:
min_expected_final:
horizon:
completion_ratio:
```

If completion differs across controllers, compare completion/backlog/teleport status first. Arrived-only averages can be biased when one controller leaves more vehicles unfinished.

## Metric Definitions

Define every reported metric:

- travel time: depart-to-arrival time, and whether unfinished vehicles are excluded, penalized, or reported separately;
- waiting time: SUMO `waitingTime` meaning and aggregation window;
- delay: reference travel time formula or baseline;
- queue: detector/edge/lane definition, speed threshold, jam threshold, and aggregation interval;
- throughput: arrival count, crossing count, or detector count;
- stops: SUMO stop definition and threshold;
- emissions/fuel: model, vehicle types, and output interval;
- fairness or distributional metrics: percentile, Gini, max delay, or movement-level aggregation.

## Baselines, Ablations, and Validation

Baseline classes:

- fixed-time plan;
- SUMO actuated controller;
- NEMA controller;
- max-pressure or pressure-style controller;
- data-informed or MPC-style controller;
- no-control or diagnostic baseline only when scientifically justified.

Fair comparison requires:

- paired seeds, routes, demand, horizon, outputs, and completion policy;
- identical network and TLS movement availability;
- identical metric formulas and aggregation scripts;
- enough repetitions or a clear diagnostic-only label;
- ablations that isolate one mechanism at a time.

Validation ladder:

1. Construction validity: files load, outputs exist, no hidden route/config failure.
2. Semantic validity: controller, TLS, detector, and metric definitions match the claim.
3. Diagnostic validity: the run explains a failure or behavior pattern.
4. Comparative validity: paired baselines and statistical reporting support comparison.
5. Claim validity: wording stays inside the evidence boundary.

## Claim Boundary

Use these evidence classes:

| Class | Allowed wording |
|---|---|
| `formal-evidence` | "In paired SUMO/TraCI experiments with stated seeds, demand, completion, and metrics..." |
| `diagnostic-demo` | "This run demonstrates behavior under a constructed scenario..." |
| `stress-diagnostic` | "This stress case exposes sensitivity or failure behavior..." |
| `construction-invalid` | "The run is not evidence for controller performance because construction failed..." |
| `claim-overreach` | "The result does not support the stated claim without additional evidence..." |
| `blocked` | "The claim is blocked pending missing artifacts or reruns..." |

Avoid unsupported wording such as:

- "the controller is better" without paired baselines and completion evidence;
- "optimal" without a formal optimality problem and solver evidence;
- "realistic" without calibration or field validation;
- "robust" without sensitivity or stress testing;
- "NEMA compliant" without NEMA parameter and behavior evidence.

## Report Record

End with:

```text
outputs_checked:
completion_status:
metric_definitions:
baseline_pairing:
validation_level:
evidence_class:
allowed_claim:
prohibited_claim:
required_next_evidence:
```
