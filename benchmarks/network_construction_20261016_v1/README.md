# Hamburg construction trace and fixed-cohort diagnosis

[`evidence.json`](evidence.json) records the inputs, candidates, measurements, tests, and raw result hashes.
The construction uses commit `c3f3773`. The reusable driving runner uses `69915c3`.
Later custom-length and binding-provenance corrections have separate regression tests.

The [local conversion follow-up](local-conversion-20261016-v1.md) records Hamburg and MoST controls, nominal-width calibration, and invalid fixed-cohort driving pairs.
The [spatial curve follow-up](local-conversion-20261016-v2.md) retains the subsequent repair, permission, and real-network checks.
The [unfinished-route follow-up](unfinished-routes-20261016-v1.md) preserves route observations for vehicles still on the road.
The [local join follow-up](local-joins-20261016-v1.md) records reduced join scopes, the corrected MoST format control, and complete Hamburg reconstruction.
The [official-part follow-up](official-local-20261016-v1.md) records local official joining and boundary reconstruction with immutable snapshots.
The [movement follow-up](movement-local-20261016-v1.md) records scoped movement passes, contour controls, effective-permission comparison, and corrected native-output records.
The [approach follow-up](approach-local-20261016-v1.md) records local road splits, logical-length allocation, durable failures, and unresolved real Hamburg approaches.
The [existing-approach comparison](existing-approaches-20261016-v1.md) resolves four construction findings and measures the two remaining merge-location differences.
The [fixed-cohort diagnosis](traffic-diagnosis-20261016-v1.md) distinguishes taper observations from edit decisions and checks immediate route access for recorded lane-front candidates.
The [serialization control](serialization-order-20261016-v1.md) corrects an unsupported runtime-equivalence inference and verifies order-preserving local merges.

## Construction findings and repair

The retained stages identify three interacting causes:

1. Nearest-lane matching assigned official ingress lanes 2 and 3 to the same source lane.
2. Boundary restoration computed a separate logical length for each ordinary lane.
3. A corrected lane binding exposed an incomplete road mouth when source lanes were laterally displaced from official positions.

The first corrected-binding rebuild extended lane 2 past the official stop, producing a +46.01 m longitudinal error into the junction.
That failed intermediate candidate remains in the evidence record. It has no promotion authority.

The final repair reuses equal-count, common-section lane ordering.
Road mouths cover both the official section and the displaced source lane strip.
The contour keeps one lateral order across overlapping lane envelopes.
Ordinary roads receive a common mean spatial length. Turning internal paths retain separate geometry lengths.
An independent review also found explicit custom road lengths. A native SUMO regression now checks that a declared 500 m length survives.
Ordered bindings retain their earlier binding, candidates, and review record.

## Same-input Hamburg rebuild

The number of ordinary roads with unequal logical lane lengths falls from 18 to zero.
The rebuild materializes all 16 official vehicle movements, with zero runtime fallback parts.
Official connectivity, exterior geometry, movement probes, and corridor route checks pass.
Construction coverage and the broader construction audit still require review.

Lane 2's longitudinal stop error changes from +4.734841 m to +0.060829 m without a stop-line edit.
Its lateral error remains -2.815163 m. Lane 3 has longitudinal error -0.087682 m and lateral error -3.029343 m.
The lane correspondence remains tentative. The road is not fully calibrated.
The remaining lateral errors also show why a longitudinal repair cannot establish lane accuracy.

## Fixed-cohort driving result

The original 100 routed requests retain their vehicle IDs, routes, and departures.
Each run uses a 2400 s horizon, one-second steps, junction collision checks, and a 2400 s teleport delay.

| Seed | Source arrivals | Rebuilt arrivals | Source collisions | Rebuilt collisions |
|---|---:|---:|---:|---:|
| 17 | 63/100 | 64/100 | 0 | 1 |
| 42 | 100/100 | 100/100 | 1 | 1 |
| 104 | 61/100 | 60/100 | 0 | 0 |

All six executions finish, but all three comparison pairs remain invalid for clean performance evidence.
The seed-17 candidate has an additional simulated junction collision. This result does not establish an improvement in traffic performance.
Collision records locate the observed events on `:77777614_2_0` and retain times, vehicle IDs, speeds, and positions.
Their interpretation still requires junction diagnosis. They are not field collision-risk estimates.

At 2399 s, the source retains 36 and 38 stationary road vehicles under seeds 17 and 104.
The rebuilt model retains 35 and 39. Another request waits for insertion in each of those runs.
The last-step output records each road vehicle's lane, position, and speed.
The comparison retains unfinished requests as right-censored observations of time since scheduled departure.
That time includes insertion waiting. It is not a completed travel duration.
No invalid pair contributes a performance average. Three seeds do not establish a failure frequency.

## Verified locations

- Construction: [`boundaries.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_junctions/boundaries.py), [`geometry.py`](../../plugins/astra-sumo/src/astra_sumo/core/junction_rebuild/geometry.py), and [`official_movement_composition.py`](../../plugins/astra-sumo/src/astra_sumo/core/official_movement_composition.py).
- Runner: [`compare_driving_cohort.py`](../../plugins/astra-sumo/scripts/compare_driving_cohort.py).
- Comparison and censoring: [`paired_driving.py`](../../plugins/astra-sumo/src/astra_sumo/core/paired_driving.py).
- Regression checks: [`test_normal_lane_construction.py`](../../tests/test_normal_lane_construction.py) and [`test_paired_driving.py`](../../tests/test_paired_driving.py).
- Scenario: `outputs/claude_followup_20261015_v1/hamburg-request.json` and `outputs/construction_trace_20261016_v1/paired-driving-request-v1.json`.
- Rebuild and scorecard: `outputs/construction_trace_20261016_v1/hamburg-rebuild-v3/` and `rebuilt-scorecard-v3.json`.
- Raw runs, collisions, and last-step positions: `outputs/construction_trace_20261016_v1/paired-rebuild-diagnostics-v2/`.

The construction-domain run passed 467 tests before the follow-up review corrections.
The affected native-diagnostic, routeability, lane-construction, and movement run then passed 111 tests.
The source inputs and failed experiments remain available. No branch has merged into `main`.
