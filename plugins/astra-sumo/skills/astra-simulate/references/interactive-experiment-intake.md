# Interactive Experiment Intake

Use this reference before planning or executing a new SUMO/TraCI experiment when the request is vague, ambitious, under-specified, or likely to hide unresolved design choices.

The purpose is to align the user and the agent before SUMO files, controller code, route generation, metrics, or result claims are produced.

## Core Rule

Run a focused interrogation loop before experiment planning:

```text
inspect available evidence -> ask one missing question -> recommend an answer -> update the readiness record -> repeat until plan-ready or blocked
```

If a question can be answered from the repository, configuration files, logs, or existing outputs, inspect those artifacts instead of asking the user.

## When to Use

Use this intake when:

- the user says "start an experiment", "build the setup", "compare controllers", "run SUMO", or "report results" without a complete design;
- the network, demand, controller, baseline, detector, seed, horizon, output, or metric choices are unclear;
- the user wants to use OSM, public benchmarks, field detector counts, generated routes, routeSampler, or custom TraCI code;
- the claim boundary is not explicit;
- the next action would otherwise require the agent to invent experiment assumptions.

Do not use this as a long questionnaire when the needed information is already present in files. Read first, then ask only for missing decisions.

## Question Discipline

- Ask one question at a time.
- Resolve dependencies between decisions instead of asking a flat checklist.
- For each question, include the recommended answer and why it is safer.
- Prefer concrete options when possible.
- Stop and summarize when the next unresolved item changes the experiment design.
- Do not start coding, route generation, simulation runs, or result reporting until the user confirms the readiness record or explicitly marks the work diagnostic-only.

## Decision Tree

Walk the experiment tree in this order, skipping branches already proven by files:

1. Research target.
   - What claim is the experiment supposed to support?
   - Is the target formal comparison, diagnostic debugging, calibration, stress testing, or demonstration?
2. Network and scope.
   - Is the network toy, corridor, grid, benchmark, imported OSM, or field-calibrated?
   - Which spatial area and edge/TLS subset are in scope?
3. Demand and routes.
   - Are routes fixed, generated, sampled from OD, reconstructed from detector counts, or benchmark-provided?
   - What seed, time window, demand level, and route validity checks are required?
4. Controller and baselines.
   - Which controller family is being tested?
   - Which baseline must be paired, and what variables must remain identical?
5. TLS, detector, and state evidence.
   - Which TLS IDs, phases, movements, detectors, lanes, and aggregation periods define the control state?
   - What proves these mappings match the generated network?
6. Execution policy.
   - Natural completion or fixed horizon?
   - How will insertion failures, teleports, unfinished vehicles, and collisions be reported?
7. Metrics and artifacts.
   - Which output files define each metric?
   - What denominator is used, and is it completion-aware?
8. Reproducibility and output safety.
   - Which versions, commands, seeds, output paths, and metadata files will be recorded?
   - How will overwritten outputs be prevented?
9. Claim boundary.
   - What can be claimed if every gate passes?
   - What must be demoted to diagnostic evidence?

## Recommended Question Format

```text
Question:
Recommended answer:
Why this matters:
Evidence I can inspect instead:
If you choose differently:
```

Example:

```text
Question: Should this be a natural-completion run or a fixed-horizon run?
Recommended answer: Use natural completion for controller comparison unless the study explicitly measures fixed-horizon operations.
Why this matters: Arrived-only averages are biased when one controller leaves more vehicles unfinished.
Evidence I can inspect instead: .sumocfg end time, TraCI loop condition, summary output, tripinfo counts.
If you choose differently: I will mark the run fixed-horizon and require unfinished/backlog reporting before any performance claim.
```

## Readiness Record

Maintain this record during the intake:

```text
Interactive Experiment Intake
target_claim:
experiment_mode: formal_comparison / diagnostic_debugging / calibration / stress_test / demonstration
network_scope:
demand_route_source:
controller_family:
baselines:
tls_detector_state:
execution_policy:
metrics_and_outputs:
paired_variables:
reproducibility_record:
claim_boundary:
open_questions:
recommended_next_step:
readiness: ready_to_plan / needs_answers / inspect_repo_first / diagnostic_only / blocked
```

## Transition Rules

- If `readiness: inspect_repo_first`, inspect the repo and update the record before asking more.
- If `readiness: needs_answers`, ask exactly one next question.
- If `readiness: ready_to_plan`, load `plan-experiment.md` and produce the SUMO Experiment Plan.
- If `readiness: diagnostic_only`, allow narrow debugging or exploration, but do not present the output as formal evidence.
- If `readiness: blocked`, state the missing external information or artifact.

## External Inspiration Boundary

This workflow is inspired by public agent-skill patterns for pre-implementation interrogation, shared understanding, and red-green-refactor verification loops. Do not copy external skill text into this repository. Express the method as SUMO/TraCI-specific workflow guidance with independent wording, clear source credit in project documentation, and no implication of endorsement.
