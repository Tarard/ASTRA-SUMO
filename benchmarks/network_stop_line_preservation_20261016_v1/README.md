# Stop-line geometry and junction preservation

This record compares two implementations of the same 4.73 m stop-line setback.
It retains the earlier failed trial and adds a direct geometric transfer.
[`evidence.json`](evidence.json) records source, candidate, fact-table, route, and result hashes.
Complete local results remain under `outputs/calibration_driving_20261016_v1/`.

## Change

The old `move_stop_line` implementation reimported the full network through netconvert.
It then restored the exterior of its declared edit scope.
At LSA2349, this conversion removed four internal waiting junctions and their corresponding path segments.
Vehicle trajectories also diverged from the source at 2 s, away from the edited road.
The old driving comparison therefore included changes beyond the intended stop-line position.
It did not isolate the geometric setback.

The new implementation transfers the saved terminal approach segment into its first internal path.
The approach becomes shorter and its internal continuation becomes longer by the same amount.
The reverse operation requires the same saved prefix for every outgoing movement.
It preserves elevation and all other source bytes, including signal programs, connections, request matrices, and waiting structures.
Shared or ambiguous continuations retain native reconstruction with a warning.

The real case changes only lane attributes on `554713076#0_2` and `:LSA2349_part0_0_1`.
It performs zero netconvert rebuilds.
The release evidence check reconstructs the candidate independently from the source and the declared offset.
A forged outside-lane speed change fails that check even after its candidate hash is updated.

## Geometry measurements

Both candidates use the same frozen official fact table for 2349 and 2394.
The source and direct candidate have no changed facts outside the declared scope.

| Stop-line component | Source | Direct candidate |
|---|---:|---:|
| Signed longitudinal error | +4.734841 m | +0.004841 m |
| Signed lateral error | -2.816698 m | -2.816698 m |

The lateral discrepancy remains unresolved.
Automatic reference correspondence and source validity dates retain their earlier limitations.
This is a geometric measurement, not field acceptance.

## Fixed-cohort driving comparison

The comparison fixes 100 source routes, vehicle IDs, and departures.
It uses seeds 17, 42, and 104 and a 2400 s horizon.
No request is dropped or resampled for a candidate.
SUMO's [randomness documentation](https://sumo.dlr.de/docs/Simulation/Randomness.html)
describes deterministic seeds and repeated runs with different seeds.
These three seeds support this diagnostic. They do not establish statistical reliability for every operating condition.

| Seed | Source arrivals | Old native candidate | Direct candidate | Source collision / teleport |
|---|---:|---:|---:|---:|
| 17 | 63/100 | 62/100 | 63/100 | 0 / 0 |
| 42 | 100/100 | 62/100 | 100/100 | 1 / 1 |
| 104 | 61/100 | 60/100 | 61/100 | 0 / 0 |

The direct candidate preserves the source collision and teleport counts in all three seeds.
Seeds 17 and 104 leave vehicles running or waiting in the source and both candidates.
All six source/candidate pairs remain invalid for a clean performance comparison.
The per-trip table retains every requested vehicle, including those without an arrival record.
No average combines these invalid units into a formal performance result.

The updated exploration warnings distinguish failed driving checks from missing routes.
They also identify an invalid source baseline.
A `measured` result reports the available evidence. It does not promote the network.

## Verification and reproduction locations

- Construction and checks: [`network_edit_operations.py`](../../plugins/astra-sumo/src/astra_sumo/core/network_edit_operations.py).
- Independent evidence validation: [`candidate_contracts.py`](../../plugins/astra-sumo/src/astra_sumo/core/candidate_contracts.py).
- Native and adversarial tests: [`test_network_edit_operations.py`](../../tests/test_network_edit_operations.py).
- Reused driving runner and output inspection: [`routeability_audit.py`](../../plugins/astra-sumo/src/astra_sumo/core/routeability_audit.py).
- Request and fixed facts: `outputs/claude_followup_20261015_v1/stop-line-request-v1.json` and `frozen-two-site-facts-v1.json`.
- Direct-edit result: `outputs/calibration_driving_20261016_v1/direct-stop-line-v2/`.
- Declared seeds and raw runs: `outputs/calibration_driving_20261016_v1/paired-seeds-v1/plan.json` and its neighboring run directories.
- Comparison runner: `outputs/calibration_driving_20261016_v1/compare_stop_line_seeds.py`.
- Final affected and navigation tests: 136 passed.
- Local full suite before the final lane-membership fix: 3,169 passed and 24 skipped.
- The [full Python CI suite](https://github.com/Tarard/ASTRA-SUMO/actions/runs/38820235601) passes on code revision `c0cf78b`, including that final fix.

The native checks use SUMO 1.27.1 from the project environment.
The source and candidates remain separate. The original native failure is retained.
Other geometry edits still use native conversion and exterior restoration.
The Hamburg baseline still needs a separate investigation of its mixed-demand failures.
