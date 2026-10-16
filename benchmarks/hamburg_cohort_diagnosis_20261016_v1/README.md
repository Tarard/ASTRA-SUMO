# Frozen Hamburg cohort diagnosis

This package retains the actual 100 synthetic routed requests and recorded observations used in the driving diagnosis.
The [manifest](fixture.json) records input hashes, SUMO version, departure times, signal-program types, and simulation settings.
It includes the recorded model, current model, three snapshots per seed, completed-trip records, summaries, and collisions.
Gzip preserves each original file's bytes.

The observations below belong to `recorded_model` (`4ad06c`), not `current_model` (`f09c24`).
The models have equal non-location element multisets but different road, junction, and signal-program order.
A later native replay found different trajectories. Static equality does not establish runtime equivalence.
See the [serialization control](../network_construction_20261016_v1/serialization-order-20261016-v1.md).

The requests use the default passenger type and scheduled departures from 0 to 99 s, across the whole study network.
The recorded model has 21 actuated and three static signal programs. Their correspondence with actual control remains unverified.
The observations are simulator truth for offline diagnosis. They are not a physical sensor model or information supplied by a new controller.

## Reproduce the offline audit

Run `python benchmarks/hamburg_cohort_diagnosis_20261016_v1/audit_cohort.py` from the project environment.
The verifier checks compressed and decompressed hashes, complete request accounting, and source-model elements.
It reads the recorded states and makes no simulation or network request.

| Seed | Arrived | On road at 2400 s | Waiting for insertion | Recorded collisions |
|---|---:|---:|---:|---:|
| 17 | 64 | 35 | 1 | 1 |
| 42 | 100 | 0 | 0 | 1 |
| 104 | 60 | 39 | 1 | 0 |

The 12 and 13 lane-front candidates under seeds 17 and 104 all have a saved path to their immediately next route road.
This differs from the generic-demand hypothesis about missing immediate connections.
It does not rule out strategic lane changes for later turns or identify the actual blocking mechanism.
The raw states remain available for that analysis. Failed outcomes and insertion waiting remain in the population.
None of these runs supports a clean performance improvement claim.

## Source notices

The edited source databases use [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Attribution: © OpenStreetMap contributors. See [OpenStreetMap copyright](https://www.openstreetmap.org/copyright).
Official MAP geometry: Freie und Hansestadt Hamburg, Landesbetrieb Straßen, Brücken und Gewässer.
Aerial references: Freie und Hansestadt Hamburg, Landesbetrieb Geoinformation und Vermessung.
The official data use [Datenlizenz Deutschland Namensnennung 2.0](https://www.govdata.de/dl-de/by-2-0).
Source URLs and the original official snapshots are indexed in the
[approach fixture](../hamburg_approach_reference_20261016_v1/fixture.json) and
[aerial comparison record](../network_construction_20261016_v1/existing-approaches-20261016-v1.json).
The model joins junctions, restores source geometry, and includes derived official and aerial reference curves.
The demand and traffic observations are synthetic simulation artifacts. They establish no field demand or traffic performance.
