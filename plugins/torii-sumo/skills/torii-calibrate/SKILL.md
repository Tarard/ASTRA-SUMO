---
name: torii-calibrate
description: Use for traffic-demand reconstruction, detector or count observation binding, route-demand fitting, calibration on a fixed SUMO network, and sensor-based digital-twin validation.
---

# Torii Calibrate

Use this skill as a compact knowledge base for fitting traffic behavior and observations to a SUMO model. The agent may choose whichever Torii workflow, SUMO utility, code path, or reference best fits the task.

## Common Knowledge

- Detector counts are traffic observations, not direct OD observations. Different route or OD assignments can explain the same measurements.
- Keep the spatial meaning of every observation clear: lane, edge, movement, station, cross-section, or aggregate.
- Keep time semantics clear: sampling interval, aggregation interval, comparison window, warm-up, and replay horizon are different things.
- Calibration and validation answer different questions. Reusing the same observations for both should be stated explicitly.
- A good aggregate count fit can still coexist with poor route support, insertion backlog, teleports, unfinished vehicles, or weak detector coverage.
- When several traffic assignments fit equally well, report the non-identifiability rather than presenting one assignment as uniquely recovered.
- For traffic calibration, it is usually useful to know exactly which network and signal configuration generated the replay being compared.

## Reference Library

Read only the references that help with the current task.

| Topic | Reference |
|---|---|
| Cached detector-demand workflow | `references/cached-detector-demand.md` |
| Detector-constrained demand reconstruction | `references/detector-constrained-demand-reconstruction.md` |
| Hamburg count fitting | `references/hamburg-count-calibration-workflow.md` |
| Hamburg Sandtorkai digital-twin replay | `references/hamburg-sandtorkai-digital-twin.md` |

## Available Torii Support

Torii can audit detector counts, construct route-support evidence, run routeSampler-based fitting, generate Hamburg demand, and replay named digital-twin scenarios. These are available building blocks, not a required sequence. The agent may use a smaller or different path when the user's task does not need the full workflow.

## Output

For substantial calibration work, state the observations used, the variables being fitted, the comparison basis, the quality of the fit, and any remaining ambiguity or coverage limit.
