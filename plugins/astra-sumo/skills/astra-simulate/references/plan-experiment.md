# Plan Experiment

Use this reference when the user has a new or vague SUMO/TraCI experiment, asks to start coding or running, or confirms that they want to proceed from an intake record.

The goal is to make the user understand the experiment setup before code, SUMO files, simulation runs, controller comparisons, or result claims.

## Intake Questions

Ask only the missing questions needed to produce a usable `Experiment Readiness Record`:

1. Objective: What question or claim should the experiment answer?
2. Scenario and network: toy intersection, corridor, grid, OSM import, benchmark, or field-calibrated network?
3. Demand: fixed routes, generated trips, OD matrix, measured counts, scenario demand, and arrival type?
4. Controller: fixed-time, SUMO actuated, NEMA, max-pressure, data-informed, MPC-style, RL, or custom?
5. Baselines: which controllers are compared, and what must remain paired?
6. TLS and detectors: which TLS IDs, phase mappings, movements, detectors, and yellow/all-red policies are involved?
7. Completion policy: natural completion or fixed horizon, and how unfinished vehicles are handled?
8. Metrics: travel time, delay, waiting, queue, throughput, stops, emissions, fairness, or other metrics?
9. Reproducibility: seeds, run matrix, output naming, versions, and command records?
10. Claim boundary: what would count as formal evidence, diagnostic evidence, or unsupported overreach?

## Experiment Readiness Record

Before planning, summarize:

```text
Experiment Readiness Record
objective:
scenario:
network_artifacts:
demand_artifacts:
arrival_type:
arrival_evidence_or_generation_rule:
controller_family:
baseline_family:
tls_detector_evidence:
completion_policy:
metrics:
paired_variables:
open_design_decisions:
claim_boundary:
readiness: ready_to_plan / needs_answers / diagnostic_only / blocked
```

If readiness is not `ready_to_plan`, ask one focused follow-up instead of inventing missing design choices.

## Arrival Type

`arrival type` describes the temporal pattern of vehicles entering the simulated network, corridor, OD pair, or demand group. It is separate from demand volume, OD structure, route choice, vehicle type, and controller policy.

Use these generic categories when planning generated, calibrated, or sampled demand:

| Arrival type | Meaning |
|---|---|
| `smooth_even` | Vehicles are distributed nearly uniformly across the analysis window. Useful as a diagnostic simplification, but often less realistic for signalized corridors. |
| `poisson_random` | Vehicles are generated from an independent random process around a target rate. Useful when arrivals are assumed memoryless or weakly coordinated. |
| `platoon_burst` | Vehicles arrive in short clusters separated by gaps, such as from upstream signal releases, ramp metering, parking exits, or coordinated flows. |
| `scheduled_pulse` | Vehicles are concentrated around known external schedules, such as events, shift changes, school release, ferry/train arrivals, or facility opening/closing times. |
| `detector_matched_profile` | Arrival timing follows measured detector or count data, preserving the observed bin profile and any supported peak or burst structure. |
| `mixed_profile` | Demand combines multiple arrival patterns. State the mixture and which demand groups use each pattern. |

For generated demand, record the seed selection rule separately from the controller outcome. Seeds should be justified by ex-ante arrival features, calibration evidence, or baseline scenario definition, not by treatment performance.

## Planning Workflow

1. Check the confirmed `Experiment Readiness Record`.
2. List unresolved risks that affect code, simulation, metrics, or claims.
3. If a major design choice remains open, propose 2-3 options with tradeoffs and a recommendation.
4. Produce a `SUMO Experiment Plan`.
5. Ask the user to confirm the plan.
6. Only after confirmation, proceed to TDD/code, SUMO construction, execution, audit, or reporting.

If the user skips planning, mark the work `diagnostic-only` and do not present the outcome as formal experiment evidence.

## Required Plan Template

```markdown
## SUMO Experiment Plan

### 1. Objective and Claim Boundary
- Research question:
- Formal claim allowed if all gates pass:
- Claims explicitly not supported:

### 2. Scenario and Artifacts
- Network:
- Route/demand files:
- Arrival type and temporal pattern:
- Arrival evidence or generation rule:
- Additional files:
- SUMO config:
- Controller code:
- Output directory pattern:
- Metadata/hash/version records:

### 3. Controller and TLS Semantics
- Controller identity:
- TLS program or TraCI control mechanism:
- Phase/state mapping evidence:
- Yellow/all-red handling:
- Detector or state inputs:
- Control interval and fallback behavior:

### 4. Baselines, Ablations, and Pairing
- Controllers compared:
- Baselines:
- Ablations:
- Demand levels:
- Arrival type(s):
- Random seeds:
- Seed selection rule:
- Paired variables:
- Variables intentionally changed:

### 5. Run Matrix
| Run group | Controller | Demand | Seed(s) | Horizon/completion | Outputs | Purpose |
|---|---|---|---|---|---|---|

### 6. Completion Policy
- Natural completion or fixed horizon:
- Denominator for planned demand:
- Handling of unfinished vehicles:
- Handling of insertion failure, teleports, discarded routes, and collisions:

### 7. Metrics and Formulas
- Primary metrics:
- Secondary metrics:
- Exact formulas and denominators:
- Artifact source for each metric:
- Completion-aware reporting rule:

### 8. Code and TDD Tasks
- Code changes required:
- Failing tests or probes to write first:
- Minimal fixtures:
- Test commands:
- Integration checks:

### 9. Execution Steps
- SUMO/netconvert/duarouter commands:
- TraCI or controller entrypoint:
- Batch command:
- Output naming rule:
- Log collection:

### 10. Hard Gates
- Config/network/route gate:
- TLS/controller gate:
- Output gate:
- Warning/error/teleport gate:
- Baseline pairing gate:
- Arrival type and seed-selection gate:
- Metric validity gate:
- Reproducibility gate:

### 11. Verification and Review
- Commands to run before completion:
- Artifacts to inspect:
- Code review focus:
- Residual risks to report:

### 12. Result Interpretation
- What counts as formal evidence:
- What counts as diagnostic evidence:
- Demotion triggers:
- Required wording if the run is fixed-horizon or incomplete:

Plan status: ready / needs revision / diagnostic-only / blocked
Next allowed action:
```

## Plan Quality Rules

- Use concrete file names, commands, outputs, seeds, and horizons when they are known.
- If exact artifacts are unknown, ask a focused follow-up question instead of inventing details.
- Do not hide design choices inside implementation. If demand, seeds, controller policy, or metrics change, show the change in the plan.
- Record arrival type separately from demand volume and route choice. If arrivals are generated from seeds, state the seed selection rule before controller results are known.
- Do not bundle root-cause debugging fixes with formal comparison runs. Plan diagnostic runs separately.
- Do not let baseline and treatment runs share output paths.
- Do not approve a plan that compares controllers with different demand, seeds, horizons, output intervals, or metric denominators unless the claim is explicitly diagnostic.

## Confirmation Gate

End with:

```text
Plan Confirmation Needed
- ready to proceed:
- fields needing user decision:
- first allowed implementation or simulation action:
```

No SUMO run, controller implementation, metric report, or claim draft should begin before the user confirms the plan, unless the work is explicitly marked `diagnostic-only`.
