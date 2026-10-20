# Standalone local conversion and complete Windows checks

The [frozen CI receipt](standalone-local-ci-20261020-v1.json) records four completed runs.
Each JUnit file was checked against the native execution policy at its own tested commit.

| Code | Runs | Passed per run | Skipped per run | Required native cases executed |
|---|---|---:|---:|---:|
| `cc54a92` | PR and push | 3734 | 24 | 45 |
| `89aa27d` | PR and push | 3737 | 24 | 46 |

All four runs have zero failures and errors.
Contract and export checks also finish successfully in these workflows.
The later endpoint-correspondence work is outside these results.

At `89aa27d`, the independent corridor simplifier uses the shared scoped conversion runner.
It records the actual command, checks merged access, and restores exterior state.
An eight-road native control converts four roads and removes its declared intermediate node.
Remote road states remain exact, and SUMO loads the candidate.
The shared consumer group passes 100 tests. A later consumer/navigation group passes 26.
These groups overlap and must not be added.

That standalone control is synthetic. It does not establish field accuracy or a complete Hamburg reconstruction.
The separate real-junction walking-area comparison is recorded in the [additive follow-up](additive-walking-context-20261020-v1.md).
