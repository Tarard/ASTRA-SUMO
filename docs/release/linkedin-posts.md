# LinkedIn Post Drafts

## Release Post

```text
Many SUMO/TraCI traffic signal control experiments fail silently: the simulation runs, but the comparison is invalid because seeds, routes, TLS phases, detector mappings, output files, or completion rates are inconsistent.

I made a lightweight Codex/Claude audit skill and checklist for researchers using Eclipse SUMO for signal-control experiments.

It helps check:
- TLS phase and movement-green consistency
- route/config/additional-file consistency
- paired baseline comparisons
- batch-run reproducibility
- tripinfo/summary/edgeData reporting
- fixed-time, actuated, max-pressure, data-informed, and MPC-style controllers

Repository: [GitHub link]

Independent academic resource; not affiliated with or endorsed by the Eclipse SUMO project, the Eclipse Foundation, or DLR.
```

## Specific Case Post

```text
Common SUMO/TraCI signal-control failure:

Controller A reports lower average travel time, but the simulation stopped at 3600s and only arrived vehicles were included in tripinfo.

If Controller A leaves more vehicles unfinished than Controller B, arrived-only averages are not a fair superiority claim.

At minimum, report:
- inserted vehicles
- arrived vehicles
- unfinished vehicles
- teleports
- discarded routes
- completion rate
- fixed-horizon metric wording

I collected these checks in a reusable audit skill/checklist for Eclipse SUMO signal-control experiments: [GitHub link]
```
