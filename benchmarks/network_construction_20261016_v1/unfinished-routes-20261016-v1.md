# Unfinished native vehicle-route observations

Status: diagnostic only, 2026-10-16. Commit `362a534`, native SUMO 1.27.1.
The [measurement record](unfinished-routes-20261016-v1.json) retains exact input and output hashes.

Earlier diagnostic runs wrote edge exit times only for vehicles whose routes ended.
New diagnostic runs also request native vehicle routes for unfinished vehicles.
The original request cohort still determines the denominator and arrival validity.
The output setting changes no requested departure, route, seed, network, or simulation horizon.

| Seed | Model | Arrivals | Native route records | Unfinished route records | Waiting for insertion |
|---|---|---:|---:|---:|---:|
| 17 | Parent | 64 | 99 | 35 | 1 |
| 17 | Width candidate | 61 | 99 | 38 | 1 |
| 42 | Parent | 100 | 100 | 0 | 0 |
| 42 | Width candidate | 57 | 99 | 42 | 1 |
| 104 | Parent | 60 | 99 | 39 | 1 |
| 104 | Width candidate | 100 | 100 | 0 | 0 |

Each run keeps the same 100 requested vehicles and 2400 s horizon as the [previous comparison](local-conversion-20261016-v2.md).
All six terminal arrival, running, insertion-waiting, collision, and teleport counts match that comparison.
All six last-step vehicle position records also match.
These checks establish agreement at the recorded terminal step, not universal trajectory equivalence.
All three pairs remain invalid for clean performance comparison.

An unfinished native route can include planned edges with no recorded exit.
Those edges are not evidence that a vehicle crossed a study boundary.
The vehicle waiting for insertion has no native vehicle-route record and remains in the original request cohort.
Future extraction must retain that observation gap. It must not silently omit the request or treat its crossing as zero.
Historical route files remain unchanged.

## Verified locations

- Output writer: [`routeability_audit.py`](../../plugins/astra-sumo/src/astra_sumo/core/routeability_audit.py).
- Comparison and request accounting: [`paired_driving.py`](../../plugins/astra-sumo/src/astra_sumo/core/paired_driving.py).
- Focused checks: 41 pass across [`test_paired_driving.py`](../../tests/test_paired_driving.py) and [`test_routeability_audit.py`](../../tests/test_routeability_audit.py).
- Scenario: `outputs/local_conversion_20261016_v1/reference-width-paired-request-v3.json`.
- Raw results: `outputs/local_conversion_20261016_v1/reference-width-paired-v4/`.

Generated native outputs remain local. The measurement record freezes their hashes and selected observations.
