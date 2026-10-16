# Local native conversion and real-network controls

Status: diagnostic only, 2026-10-16. No promotion authority.
The [measurement record](local-conversion-20261016-v1.json) identifies every retained input and raw result by hash.
Construction used `6791bd6` and native SUMO 1.27.1 on Windows.
Later regression checks also cover explicit right-of-way prohibitions and spatial diagnostics.

## Scope and measurements

The same edit code runs on the rebuilt Hamburg network and the public MoST 2019 model.
Each candidate retains the source exterior, restores remote road ends, and checks the complete network.
The source files remain unchanged.

| Case | Source roads | Converted roads | Initial local native time | Initial full native time |
|---|---:|---:|---:|---:|
| Hamburg bicycle width 1.25 to 2 m | 196 | 8 | 0.251 s | 0.434 s |
| Hamburg road midpoint +0.5 m | 196 | 8 | 0.252 s | 0.436 s |
| Hamburg road 4 to 5 lanes | 196 | 8 | 0.247 s | 0.432 s |
| MoST passenger-lane width +0.1 m | 4404 | 9 | 0.255 s | Not run locally |

The first three edits are technical perturbations. They are not field corrections.
The 2 m width is an assumed partial change. It is not the official nominal target for this particular lane.
MoST selection uses the first eligible passenger road at least 5 m long, by lexical ID, with unsignalized endpoints.
This rule selected lane `-152134_1` before reading the outcome.

All four candidates apply the requested edit, pass exterior geometry checks, and have zero structural failures and endpoint gaps.
The Hamburg width edit requires one further native conversion to reanchor a retained custom movement curve.
Other cases require one native conversion.
The table reports initial native calls only. It excludes restoration and complete-network validation.
MoST validation alone took 33.986 s. These measurements do not establish total workflow acceleration.

The full-conversion controls use the same edits and restoration code.
The local and full Hamburg outputs are not bit-identical.
The width case has maximum XY lane Hausdorff distance 0.000778 m and maximum logical length difference 0.000284 m.
The other cases differ geometrically by less than 1e-12 m and have zero logical length difference.
Canonical records also differ through numeric serialization. Driving equivalence has not been established.

Joins, controller reconstruction, controls shared across the edit boundary, and incomplete bidirectional contexts still use full conversion.
The source graph, copying, and complete-network audits remain part of each edit.

## Reference-width calibration

A separate public `network verify-edit --mode explore --facts-file` run sets the same bicycle lane to 3.25 m.
The frozen MAP fact uses the intersection's nominal default. This lane has no node-specific width override.
The absolute width error changes from 2 m to zero. No fact outside the edit changes.
Field dates and bicycle association remain tentative. A uniform SUMO lane width is an approximation.
Fitting this nominal value does not establish the actual road width.

The source OD driving check fails with a collision. The candidate OD check passes.
Those checks can reroute requests. The following separate comparison retains the original 100 routed paths and departures.

| Seed | Parent arrivals | Width candidate arrivals | Parent collisions / teleports | Candidate collisions / teleports |
|---|---:|---:|---:|---:|
| 17 | 64/100 | 61/100 | 1 / 1 | 1 / 1 |
| 42 | 100/100 | 57/100 | 1 / 1 | 1 / 1 |
| 104 | 60/100 | 100/100 | 0 / 0 | 0 / 0 |

All six executions finish. All three pairs remain invalid for clean performance comparison.
No invalid pair enters a performance average.
Each run uses 2400 s, one-second steps, junction collision checks, and a 2400 s teleport delay.
The source and candidate retain the same routes, departures, seeds, and operating assumptions.
Changed event order can still change which random draws affect a trip.
These results do not establish a traffic improvement, field correctness, or a failure frequency.

## Regression checks and retained failures

- 229 focused checks passed before the last preservation additions.
- A later 68-check run covered source movement support, ownership, local conversion, custom paths, and repository navigation.
- A 17-check run then covered local conversion, active native right-of-way changes, and navigation.
- Spatial and curve diagnostics passed 40 checks. Later legacy-permission changes passed separate runs of 69 and 58 checks.
- Ruff and `git diff --check` passed for the changes at their respective commits.

The native fixtures retain signal programs, reciprocal opposite-lane references, right-side lane insertion, and explicit custom logical lengths.
A seeded 25 m remote recut verifies that restoration precedes candidate acceptance.
Separate real Hamburg cases reproduce the original endpoint gap without a seeded defect.
The prohibition test changes native junction responses and matches the full conversion after restoration.

Earlier failed attempts remain under the raw output root: mixed zero-Z comparison failure, permission false mismatches, and a precision-based no-effect result.
The mistaken `width-explore-v3` facts path also remains recorded. The successful public run uses `width-explore-v4`.
The later fixes retain native permission aliases, network-version rules, and the command's numeric precision.
Passing focused checks does not substitute for the full suite or resolve field correspondence.

## Verified locations

- Local input and merge: [`local_network_conversion.py`](../../plugins/astra-sumo/src/astra_sumo/core/local_network_conversion.py).
- Construction and restoration: [`network_edit_operations.py`](../../plugins/astra-sumo/src/astra_sumo/core/network_edit_operations.py) and [`restoration.py`](../../plugins/astra-sumo/src/astra_sumo/core/junction_rebuild/restoration.py).
- Curve repair: [`native_path_repair.py`](../../plugins/astra-sumo/src/astra_sumo/core/junction_rebuild/native_path_repair.py).
- Calibration and comparison: [`network_calibration.py`](../../plugins/astra-sumo/src/astra_sumo/core/network_calibration.py) and [`paired_driving.py`](../../plugins/astra-sumo/src/astra_sumo/core/paired_driving.py).
- Tests: [`test_local_network_conversion.py`](../../tests/test_local_network_conversion.py), [`test_native_custom_path_repair.py`](../../tests/test_native_custom_path_repair.py), and [`test_legacy_lane_permissions.py`](../../tests/test_legacy_lane_permissions.py).
- Public command: `astra network verify-edit`. The fixed-cohort runner is [`compare_driving_cohort.py`](../../plugins/astra-sumo/scripts/compare_driving_cohort.py).
- Scenario: `outputs/local_conversion_20261016_v1/width-explore-request-v2.json` and `reference-width-paired-request-v2.json` in the same directory.
- Raw results: `outputs/local_conversion_20261016_v1/`. Selected result hashes appear in the measurement record.

The generated networks and upstream MoST model remain local. This record does not redistribute them.
