# Tracked length measurements and nonmotor correspondence

[`length-and-correspondence-20261016-v1.json`](length-and-correspondence-20261016-v1.json) binds this follow-up to its code, inputs, and results.
The [earlier construction experiment](README.md) remains a separate record.

## Construction lengths

The reusable length audit now runs inside Hamburg construction.
The real rebuild at `0878011` records all 15 retained `.net.xml` outputs.
Every file has zero ordinary roads with unequal lane lengths. The audit does not change the construction decision.
Construction coverage and the broader construction audit still require review.

The same tracked audit reads 45 retained files from the original, failed intermediate, and corrected builds.
It reproduces the original 8 unequal-length roads after context restoration and 18 after the final physical join.
It retains measured shape lengths separately from required logical-length expectations.

A further native check found that custom road lengths must account for lane end offsets.
The [native calculation](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/netbuild/NBEdge.cpp)
subtracts their average from the loaded road length.
Native regression cases verify a 500 m road with zero, 5 m, and 10 m offsets on one of two lanes.
The expected common lengths are 500 m, 497.5 m, and 495 m.
Coupled bidirectional roads retain native lengths. Their independent custom-length expectation remains unverified.

## Collision-affected arrivals

The same 100 requests run under seeds 17, 42, and 104 with runner `6eebd64`.
All six executions finish. All three driving comparison pairs remain invalid.
Arrival, collision, and unfinished counts reproduce the earlier diagnostic result.

The comparison identifies four collision-affected vehicle/seed pairs.
It retains their raw records but excludes their duration differences from ordinary comparisons.
The collider receives a teleport marker under the runner's explicit collision action.
The victim receives a collision marker without an inferred teleport.
An eventual arrival does not remove either marker.

## Recovered bicycle measurements

The old nonmotor association used distances from the entire official lane to one short SUMO segment.
Missing longitudinal tails could therefore look like large lateral errors.
The revised association measures perpendicular offset over directed overlap and records coverage separately.
It keeps the existing mode restrictions, 2 m lateral limit, and one-to-one assignment.

The real two-site snapshot at `3c5ae28` retains all 21 measured motor centerlines.
It also measures three of the 38 nonmotor reference centerlines:

| Reference | Mean lateral offset | Reference coverage | Confidence |
|---|---:|---:|---|
| 2349 bicycle lane 1 | 1.748883 m | 91.22% | Low |
| 2394 bicycle lane 1 | 0.279789 m | 91.86% | Low |
| 2394 bicycle lane 13 | 0.020977 m | 52.50% | Low |

These are tentative associations. They do not establish field accuracy or a complete bicycle network.
The remaining 35 nonmotor references stay unassociated or unmeasured. They do not become road-absence labels.
The motor approach's joint correspondence and lateral discrepancies still require investigation.

## Verified locations

- Length measures: [`network_length_audit.py`](../../plugins/astra-sumo/src/astra_sumo/core/network_length_audit.py) and [`audit_network_lengths.py`](../../plugins/astra-sumo/scripts/audit_network_lengths.py).
- Driving observations: [`paired_driving.py`](../../plugins/astra-sumo/src/astra_sumo/core/paired_driving.py).
- Nonmotor association: [`lane_correspondence.py`](../../plugins/astra-sumo/src/astra_sumo/core/lane_correspondence.py).
- Scenario and raw results: `outputs/length_conformance_20261016_v1/`.
- Stage measurements: `hamburg-rebuild-v1/construction-stage-lane-lengths.json` under that result directory.
- Driving requests and runs: `paired-driving-request-v1.json` and `paired-rebuild-v1/`.
- Official-source scorecards: `nonmotor-calibration-v1/`.

The affected construction domains pass 531 tests. The calibration domains pass 42 tests.
The collision-observation tests pass 14 tests, including a native driving case.
These focused results do not claim a new local full-suite run or field validation.
