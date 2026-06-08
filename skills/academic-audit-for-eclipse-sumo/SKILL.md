---
name: academic-audit-for-eclipse-sumo
description: Use when designing, running, reviewing, debugging, or writing claims from Eclipse SUMO/TraCI traffic signal control experiments, including fixed-time, actuated, max-pressure, NEMA, MPC-style controllers, TLS phases, netconvert, routes, demand, detectors, outputs, baselines, ablations, reproducibility, or academic evidence boundaries.
---

# Academic Audit for Eclipse SUMO

## Overview

Use this skill to keep Eclipse SUMO/TraCI traffic signal control experiments academically defensible. Separate experiment design, simulator construction, controller identity, TLS semantics, runtime outputs, baseline fairness, sensitivity evidence, and paper/report claims.

## Control Loop

```text
claim target -> experiment intake -> source check -> construction audit -> run hard gates -> evidence classification -> claim boundary
```

## Source Ladder

Use sources in this order:

1. Installed Eclipse SUMO binary, command output, logs, and generated artifacts.
2. Official Eclipse SUMO documentation and official Eclipse SUMO source/tests.
3. Eclipse SUMO FAQ, issue tracker, mailing-list/forum discussions, and reproducible bug reports.
4. Public traffic-control codebases and benchmarks.
5. Academic papers and project reports.
6. Agent memory or prior notes.

If sources conflict, verify against the installed Eclipse SUMO version and official documentation before answering.

## First Gate: Experiment Intake

For new, vague, or partially specified experiment requests, read `references/experiment-intake-interview.md` first. Produce an `Experiment Readiness Record` and ask the user to confirm it before writing code, editing Eclipse SUMO files, running Eclipse SUMO, or drafting result claims.

If the user explicitly skips intake, state which fields are unknown and mark the run `diagnostic-only` until the missing fields are resolved.

## Reference Routing

- Source priority or conflicting sources: read `references/source-ladder.md`.
- New experiment design or unclear setup: read `references/experiment-intake-interview.md`.
- Eclipse SUMO network, routes, TLS, detectors, netconvert, signal states, or GUI/headless semantics: read `references/sumo-official-semantics.md`.
- Eclipse SUMO root-cause debugging or unexpected behavior: use `debugging-audit-for-eclipse-sumo` if available; otherwise read `references/sumo-official-operational-lessons.md` and `references/sumo-community-faq-lessons.md`.
- Eclipse SUMO operational pitfalls from official docs: read `references/sumo-official-operational-lessons.md`.
- Eclipse SUMO mailing-list/forum/GitHub issue failure patterns: read `references/sumo-community-faq-lessons.md`.
- NEMA ring/barrier, splits, offset, force-off, recall, detector, or `ignore-errors` claims: read `references/sumo-nema-controller-audit.md`.
- TraCI controller identity or API boundaries: read `references/sumo-traci-controller-boundaries.md`.
- Runtime outputs, logs, XML artifacts, and hard gates: read `references/sumo-output-hard-gates.md`.
- Completion-aware evaluation and paper-style metric definitions: read `references/evaluation-metrics-and-completion.md`.
- Lessons from public traffic-control codebases: read `references/public-code-lessons.md`.
- Experiment debugging, sweeps, ablations, sensitivity, and structural diagnosis: read `references/experiment-validation-ladder.md`.
- Baseline and ablation design: read `references/baseline-and-ablation-design.md`.
- Paper/report claim wording: read `references/claim-boundary-taxonomy.md`.
- Preparing a public release: read `references/public-release-checklist.md`.

## Non-Negotiable Separations

Always separate:

- what Eclipse SUMO loaded;
- what the controller actually did;
- what outputs were written;
- what warnings, errors, collisions, teleports, discarded vehicles, route failures, and unfinished vehicles occurred;
- what academic claim the run can support.

## Evidence Classes

- `formal-evidence`: hard gates pass and experiment semantics match the claim.
- `diagnostic-demo`: useful integration or debugging evidence, but not a formal traffic-control result.
- `stress-diagnostic`: intentionally extreme or boundary-probing run.
- `construction-invalid`: network, route, TLS, controller, or output construction is invalid.
- `claim-overreach`: run may be valid, but the claim exceeds the evidence.

## Common Red Lines

- Do not call a TraCI phase sequence a NEMA ring/barrier controller unless a loaded Eclipse SUMO NEMA program and timing evidence support that claim.
- Do not treat a GUI run as validation without a matching headless audited run.
- Do not treat TraCI, routing, insertion, teleport, or seed behavior as background details; they are experimental conditions.
- Do not accept `ignore-errors=true` in formal NEMA evidence.
- Do not claim controller superiority from a single scenario, one seed, weak baselines, missing ablations, or missing runtime audits.
- Do not equate clean Eclipse SUMO execution with academic evidence. Clean execution is only one hard gate.
- Do not compare average travel time, waiting time, or delay from arrived-only `tripinfo.xml` when models have different completion rates.
