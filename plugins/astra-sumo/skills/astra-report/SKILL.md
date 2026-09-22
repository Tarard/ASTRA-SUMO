---
name: astra-report
description: Use for explaining, comparing, or writing up completed SUMO and traffic-control experiment results, checking metrics and baselines, bounding claims, and reviewing release evidence.
---

# ASTRA Report

Use this skill as a compact knowledge base for interpreting and communicating completed SUMO evidence. The agent may decide which references are useful and whether a reporting task needs only explanation, a comparison, a formal report, or additional analysis.

## Common Knowledge

- Traffic outcomes such as delay, travel time, queue, throughput, stops, reliability, emissions, and priority-user effects are different from controller reward, pressure, objective value, estimator loss, or prediction error.
- Check whether compared methods used the same network, demand realization, routes, seeds, horizon, signal constraints, evaluation population, and metric definitions before attributing differences to the algorithm.
- Completion matters. Arrived-only averages can be misleading when one method leaves more vehicles unfinished or waiting for insertion.
- Keep simulator truth, modeled observations, oracle information, connected-vehicle samples, and physically deployable sensing separate.
- When methods receive different information, distinguish information advantage from controller or algorithm advantage.
- State exclusions, invalid runs, unfinished demand, and uncertainty when they materially affect interpretation.
- For traffic-control reporting, explain what information reached the controller, how it was obtained, what action the controller could take, and what traffic outcome followed.

## Reference Library

Read only the references that help with the current task. The two bundled writing skills below are optional editing tools. Use them when they improve the requested output. They do not override technical accuracy, source fidelity, or the user's requested voice.

| Topic | Reference |
|---|---|
| Metrics, baselines, completion, and claim wording | `references/evaluate-and-report-results.md` |
| Traffic-control experiment reporting | `references/traffic-control-reporting.md` |
| Clear controlled English for technical or agent-facing text | `references/asd-ste100-skill.md` |
| Remove AI-writing patterns while preserving the writer's voice | `references/humanizer-skill.md` |
| Capturing a reusable lesson from a resolved case | `references/capture-field-lesson.md` |
| Public repository or release review | `references/release-project.md` |

`asd-ste100-skill.md` is bundled from `danyuchn/asd-ste100-skill` and `humanizer-skill.md` is bundled from `blader/humanizer`. Both upstream skills are MIT licensed. Their license notices are stored beside the bundled copies.

## Available ASTRA Support

ASTRA can inspect paired SUMO configurations, compare completed outputs, and provide reporting-oriented references. These are optional aids. A narrow question may need only a small subset of the available evidence, while a full report may justify reading more artifacts or references.

## Output

Lead with the conclusion that the available evidence supports. Then give the key values or mechanisms, the comparison basis, and any uncertainty or evidence boundary that matters to the user's claim.
