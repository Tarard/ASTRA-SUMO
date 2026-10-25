# Road order and native request order

2026-10-16. Synthetic diagnostic controls. No field-performance claim.

The [measurement record](serialization-order-20261016-v1.json) binds counts, raw reports, and model hashes.

## Corrected interpretation

The frozen observations describe model `4ad06c`, not model `f09c24`.
Their non-location element multisets are equal. Their road, junction, and signal-program order differs.
This static equality does not establish equal seeded trajectories.
The earlier inference that another cohort replay was unnecessary is withdrawn.

All controls retain the same 100 routed requests, seed 17, SUMO 1.27.1, and 2400 s horizon.
They use one-second steps, junction collision checks, collision teleportation, and a 2400 s teleport threshold.

| Model or single change | Arrived | On road | Different final vehicle records | Different completed-trip records |
|---|---:|---:|---:|---:|
| Recorded `4ad06c` | 64 | 35 | 0 | 0 |
| `f09c24` | 60 | 39 | 26 | 64 |
| `f09c24`, restore road order only | 64 | 35 | 0 | 0 |
| `f09c24`, restore controller order only | 60 | 39 | 26 | 64 |
| `f09c24`, restore junction order only | 60 | 39 | 26 | 64 |
| `f09c24`, restore all three orders | 64 | 35 | 0 | 0 |
| `f09c24`, restore root header only | 60 | 39 | 26 | 64 |

The differences compare vehicle records by ID against the recorded control.
Disabling FCD output also leaves the `f09c24` count at 60 arrivals and 39 vehicles on road.
The discrepancy therefore predates the new passive observer.

## Shared merge repair

Existing roads, junctions, and signal programs retain their positions when their identities survive.
The merge adds new elements after their category's retained elements.
Duplicate native identities must fail explicitly rather than silently discard a row.

**Connections require different treatment.**
For each modified junction, the replacement connections retain native order.
That order defines movement indices in the replacement junction's conflict table.
The merge must keep the connections and that table together.
The [SUMO network specification](https://sumo.dlr.de/docs/Networks/SUMO_Road_Networks.html#indices_of_a_connection) describes this junction-index ordering.

The first repair, commit `2c245f0`, incorrectly retained old connection positions too.
Its complete Hamburg candidate loaded and passed individual movement probes.
All three fixed-cohort runs nevertheless crashed with native exit code `3221225477`.
The logs include conflict-table warnings and unsafe protected greens at `LSA2403_part0`.
These failed outputs remain available. They are not traffic performance results.

Commit `a2786ba` restores native connection order while retaining existing road order.
The real-cohort no-op regression checks completed-trip records, final vehicle records, and collisions.
The synthetic merge regression separately checks native connection ordering, new elements, and duplicate rejection.
A loaded-conflict regression permutes native connections without changing attributes, then compares TraCI approach and internal conflict sets.
The merge must reproduce that native reference. The source control must differ, so the regression checks a real semantic change.

Order preservation verifies a no-op property. It does not establish equivalence after a real road edit.
Actual construction can change road inventory, signal plans, interactions, and random-number consumption.
Completed synthetic trips also do not establish field correctness or plausible demand.

## Complete reconstruction and replay

The second complete reconstruction produces model `0eb22507926899f40dc1cd356dc52799d14da5cd3bff6fc75c27b17d2447f30a`.
It materializes all 16 official movements with zero fallback parts.
Outside geometry, movement probes, and corridor route checks pass.
Construction coverage and the broader construction audit remain `review_required`.

Its non-location element multiset equals `f09c24`, including the signal phases.
The different ordering still changes the simulated trajectories.

| Seed | Recorded `4ad06c`: arrived / collision count | Reordered `f09c24`: arrived / collision count | Repaired reconstruction: arrived / collision count |
|---|---:|---:|---:|
| 17 | 64 / 1 | 60 / 1 | 100 / 0 |
| 42 | 100 / 1 | 62 / 0 | 69 / 0 |
| 104 | 60 / 0 | 66 / 0 | 100 / 0 |

All nine native executions complete without a process failure.
The repaired runs contain no `ConflictInfo` or unsafe-green warnings.
The repaired seed-42 case still has 30 vehicles on road and one waiting for insertion.
All six comparisons retain invalid clean-performance status because at least one member has unfinished requests or recorded collisions.
The apparent changes in arrivals are not evidence of a traffic improvement.

## Passive observations and limits

The new passive observer reproduces the recorded seed-17 terminal state with no mismatched vehicle records.
Its continuous trace shows green opportunities while vehicles 89, 92, and 93 remain stopped.
During the final 600 s, their next movements show green for 230, 472, and 472 steps, respectively.
This rules out a permanently red next signal as the sole explanation for their persistent waiting.
It does not identify the blocking mechanism.

Claude independently proposed departure-spacing and driver-randomness controls.
A local check on seed 17 uses `sigma=0`, `speedDev=0`, and otherwise unchanged vehicle defaults.
It retains each vehicle's route and multiplies its scheduled departure by the declared spacing factor.

| Departure multiplier | First and last scheduled departure | `4ad06c` arrived | `f09c24` arrived | Collision count in either run |
|---|---|---:|---:|---:|
| 1.0 | 0–99 s | 63 | 63 | 0 |
| 1.5 | 0–148.5 s | 100 | 100 | 0 |

All four executions complete. The two original-spacing cases each retain 36 vehicles on road and one waiting for insertion.
These observations support interaction and demand sensitivity under the declared diagnostic driver settings.
They do not identify a field capacity threshold or establish equivalence for other vehicle behavior.
No field observations currently establish whether the synthetic departure intensity is realistic.
One-at-a-time route completion cannot exclude interacting-vehicle defects.

The independent output reader checks all 13 completed executions and reports zero technically invalid runs.
Five have complete trip records. Eight retain partial trip records.
This check does not remove their collisions, unfinished requests, or limits on traffic-performance claims.

The final focused checks pass 51 tests, including navigation. The affected construction callers and applicability checks pass 111 tests.
The three cohort and loaded-conflict tests then pass together. The final navigation repeat passes seven tests.
The earlier 97-test caller run preceded the connection-order correction and is retained separately.

## Reproduction and raw records

- Shared implementation: [local_network_conversion.py](../../plugins/astra-sumo/src/astra_sumo/core/local_network_conversion.py).
- Real inputs: [frozen cohort manifest](../hamburg_cohort_diagnosis_20261016_v1/fixture.json).
- Regression: [real-cohort test](../../tests/test_hamburg_cohort_diagnosis_fixture.py) and [merge tests](../../tests/test_local_network_conversion.py).
- Paired runner and analysis: [compare_driving_cohort.py](../../plugins/astra-sumo/scripts/compare_driving_cohort.py) and [paired_driving.py](../../plugins/astra-sumo/src/astra_sumo/core/paired_driving.py).
- Scenario: `outputs/claude_followup_20261015_v1/hamburg-request.json` and `outputs/interaction_diagnosis_20261016_v1/paired-order-request-v1.json`.
- Neutral-control runner: `outputs/interaction_diagnosis_20261016_v1/check_serialization_controls.py`.
- Neutral raw controls: `outputs/interaction_diagnosis_20261016_v1/command-controls-v1/` and `serialization-controls-v1/`.
- Failed complete reconstruction and native runs: `outputs/interaction_diagnosis_20261016_v1/hamburg-order-fixed-v1/` and `paired-order-v1/`.
- Corrected complete reconstruction: `outputs/interaction_diagnosis_20261016_v1/hamburg-order-fixed-v2/`.
- Corrected paired configuration and raw runs: `outputs/interaction_diagnosis_20261016_v1/paired-order-request-v2.json` and `paired-order-v2/`.
- Departure-control runner and raw results: `outputs/interaction_diagnosis_20261016_v1/probe_departure_sensitivity.py` and `departure-sensitivity-v1/`.
- Independent accounting: `E:/Code/SUMO-Research-Tools/sumo_evidence.py`, with `outputs/interaction_diagnosis_20261016_v1/reporting/sumo_research_manifest.json` and `reporting/sumo_evidence/report_bundle.json`.
- Passive observer: `outputs/interaction_diagnosis_20261016_v1/trace_recorded_baseline.py`.
- Passive trace, configuration, and states: `outputs/interaction_diagnosis_20261016_v1/recorded-baseline-17-v2/`.

The first observer mismatch remains in `baseline-17-v1/`. It used `f09c24` against expectations recorded on `4ad06c`.
