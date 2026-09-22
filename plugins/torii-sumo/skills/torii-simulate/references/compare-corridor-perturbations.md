# Compare Corridor Perturbations

Use this reference when a SUMO/TraCI experiment compares controller behavior under multiple perturbation mechanisms and the scientific question is whether a localization signal, mechanism channel, or information interface is useful.

The key idea is experimental control: keep the spatial target and all non-tested variables fixed, then change only the perturbation mechanism or controller information treatment.

## Core Experimental Logic

A fair mechanism comparison needs three layers:

1. **Fixed physical scope**: all perturbations act on the same selected corridor, zone, or core sub-area.
2. **Mechanism-specific intervention**: demand, speed, capacity, signal, incident, or policy perturbations differ in mechanism, not in location.
3. **Paired controller comparison**: every controller sees the same network, seed, background demand, horizon, output settings, and metric formulas.

If perturbation A is applied at one place and perturbation B at another, the comparison is confounded by location. Do not use that result to claim mechanism-specific behavior.

## Scope Before Mechanism

First define the spatial scope as an experimental object:

```text
scope_id:
core segment or zone:
affected nodes/intersections:
affected edges/lanes:
why this scope is scientifically meaningful:
what remains outside scope:
```

Only after the scope is fixed should perturbations be defined.

Conceptual mapping:

- **Demand change**: increase vehicles whose route or OD path traverses the selected scope.
- **Speed degradation**: reduce speed for vehicles while they traverse the same scope.
- **Capacity incident**: reduce usable capacity in the same scope, for example by closing one lane or restricting movement capacity.
- **Signal or control perturbation**: alter timing, phase availability, actuation, or controller information while preserving the same demand and network scope.

The exact SUMO implementation can vary. What matters for the claim is that the generated route, additional, config, and log files prove the intended scope.

## Controlled Variables

For each comparison, explicitly separate:

```text
fixed variables:
changed perturbation variable:
changed controller information variable:
changed controller actuation variable:
uncontrolled or diagnostic variables:
```

Typical fixed variables:

- network and signalization;
- corridor, zone, or core scope;
- random seed;
- background demand;
- simulation horizon and warmup;
- output interval and metric parser;
- vehicle type and route generation policy;
- controller update interval and yellow/all-red policy.

Typical changed variables:

- perturbation mechanism: demand vs speed vs capacity vs signal/control perturbation;
- perturbation intensity: low/medium/high or a documented numeric level;
- controller information: no extra information, localized impact signal only, full mechanism channels, shuffled or misaligned information;
- controller family: fixed-time, actuated, max-pressure, NEMA, information-informed pressure, MPC-style, or custom.

## Baseline and Negative-Control Logic

A useful controller-information experiment should include more than one baseline:

```text
primary baseline:
simple mechanism-informed treatment:
localization-only treatment:
negative information control:
operational baseline:
fixed or naive baseline:
```

Recommended interpretation:

- If localization-only improves over the primary baseline, the localization signal is informative.
- If full mechanism information improves over localization-only, the mechanism channels add value beyond location.
- If shuffled or mixed information performs worse than aligned information, the mechanism information is not just arbitrary regularization.
- If an operational baseline beats the proposed method, say so; this limits the controller-performance claim but does not automatically invalidate the information-signal claim.

Do not collapse these into one claim. Separate "information is useful" from "this controller is operationally best."

## Localized Impact-Signal Reasoning

Treat an impact signal as a localization or sensitivity hypothesis:

```text
perturbation source:
predicted affected region:
controller-action region:
mapping from model nodes to SUMO TLS/movements:
evidence that the controller acted in or near that region:
```

Before making an impact-signal claim, check:

- how the affected region was computed;
- whether graph semantics match the road network rather than an execution shortcut;
- whether intermediate road nodes are preserved or intentionally abstracted;
- whether predicted impact nodes map to controllable signals, movements, or detector groups;
- whether the same impact logic is used across perturbation mechanisms or clearly labeled as a proxy.

If the physical perturbation spans multiple edges but the impact proxy targets only one node or edge, state that limitation.

## Metrics

Report both local and global performance:

```text
local_or_scope_metric:
network_global_metric:
baseline for percentage reduction:
aggregation window:
completion and unfinished-vehicle policy:
```

Use paired reduction against the same baseline:

```text
local_reduction_pct = 100 * (baseline_local - method_local) / baseline_local
network_reduction_pct = 100 * (baseline_network - method_network) / baseline_network
```

For cross-mechanism comparison, prefer metrics that exist for all perturbation types. A trip-level metric may be valid for a demand pulse but not for speed or capacity perturbations. A scope-level edge/lane delay, time loss, throughput, queue, or completion-aware metric is often more comparable.

Always report completion, insertion failure, teleports, and unfinished vehicles before interpreting delay reductions.

## Manifest and Reuse

The reusable artifact is not a single hard-coded scenario. It is a manifest pattern:

```text
scenario_id:
scope_id:
perturbation_type:
perturbation_intensity:
controller_label:
controller_internal_config:
seed:
metric_scope:
output_dir:
claim_status:
```

A good runner can be reused by swapping:

- scope;
- seed set;
- perturbation levels;
- controller set;
- metric scope;
- network.

Before formal execution, create a dry-run manifest and audit that every perturbation row shares the intended scope.

## Result Interpretation

Use cautious wording:

```text
The experiment shows whether the information signal helps under paired fixed-scope perturbations.
It does not by itself prove universal robustness, global optimality, or operational dominance over all baselines.
```

Useful claim structure:

```text
Compared with the primary baseline, the localization-only treatment improves/degrades local and network delay under the same scope.
Compared with localization-only, the mechanism-channel treatment improves/degrades performance, indicating whether mechanism channels add information.
The negative information control improves/degrades performance, indicating whether aligned mechanism information matters.
Operational baselines define the controller-performance comparison boundary.
```

Demote the claim if:

- perturbation locations differ;
- perturbation scopes are not auditable in generated SUMO files;
- controller labels do not map to explicit internal configurations;
- a primary-baseline row is missing for the same seed and perturbation;
- completion differs and is not reported;
- only one seed or one scope is available but the wording implies robustness;
- negative information controls are missing when claiming mechanism information is useful.
