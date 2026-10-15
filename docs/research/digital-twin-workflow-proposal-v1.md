# Proposal: traffic workflows for real places and operational parking

Status: open design proposal, 2026-10-16. This document proposes future work.
The draft PR adds no operating capability or public command.

The proposal follows an independent Claude review and a discussion with Codex.
Claude inspected the development branch without changing it.
Its cloud environment had no SUMO installation. Native results below come from the local project environment.

## Purpose

ASTRA should represent what happens at a place: arrivals, departures, dwell times, queues, and changes over time.
The study question should determine the necessary road and facility detail.
Keep the existing Build, Calibrate, and Simulate capability groups.

The [Digital Twin Consortium definition](https://www.digitaltwinconsortium.org/2020/12/digital-twin-consortium-defines-digital-twin/)
emphasizes synchronization with a real place at a declared frequency and fidelity.
For ASTRA, this suggests a dated observation, update, simulation, and independent validation cycle.
That cycle can start with historical replay. Each result must state its time window and update frequency.
An aerial overlay or one fitted count window does not establish that cycle.

## Current evidence and gaps

- [Parking recognition](../parking-facilities.md) preserves garage access and adds metadata. It creates no parking behavior.
- [Road calibration](../network-calibration.md) now measures tentative associations and candidate effects during exploration.
- The [Hamburg record](../../benchmarks/network_calibration_followup_20261016_v1/README.md) retains 21 motor-lane measurements and a failed driving trial.
- The [earlier demand record](../hamburg-digital-twin-evidence-summary.json) reports a 6 by 58 route-sensor matrix, rank 6, and nullity 52.
  That historical configuration cannot identify a unique route-demand vector from detector counts alone.
- [Stage dependencies](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_execution_workflow.py) already track network bindings and downstream invalidation.
  Reuse this behavior when another workflow needs it.

## Proposed working cycle

1. Declare the place, study question, observation period, represented modes, and required detail.
2. Save source evidence and assumptions. Keep publication, observation, validity, and download times distinct.
3. Build the roads and relevant facilities. Keep source references separate from compiled SUMO identifiers.
4. Measure discrepancies. Construct bounded candidates and compare the same physical facts and trip requests.
5. Fit demand and operating parameters on declared calibration windows.
6. Evaluate independent windows and paired simulation runs. Retain invalid runs and unresolved attribution.
7. When evidence changes, refresh affected bindings and dependent results. Record what remains current or stale.

Daily exploration must still return useful measurements with confidence and warnings.
Final publication applies release checks to the selected candidate.
Changing road supply can require a new demand assignment. Preserve that dependency in the same cycle.

## Reuse existing components

| Need | Existing component | Proposed extension |
|---|---|---|
| Physical road correspondence | [`lane_correspondence.py`](../../plugins/astra-sumo/src/astra_sumo/core/lane_correspondence.py) | Keep source identity and explicit split or merge history when binding candidates |
| Facility sources and access | [`parking_facilities.py`](../../plugins/astra-sumo/src/astra_sumo/core/parking_facilities.py) | Supply native parking operations from explicit facility and scenario records |
| Reference measurements | [`network_calibration.py`](../../plugins/astra-sumo/src/astra_sumo/core/network_calibration.py) | Compare frozen physical facts after a declared binding change |
| Candidate construction | [`network_edit_operations.py`](../../plugins/astra-sumo/src/astra_sumo/core/network_edit_operations.py) | Reuse scoped construction and outside-change measurements |
| Observation time | [`digital_twin_timeline.py`](../../plugins/astra-sumo/src/astra_sumo/core/digital_twin_timeline.py) | Use the same time and warm-up rules for facility observations |
| Demand ambiguity | [`route_sensor_matrix.py`](../../plugins/astra-sumo/src/astra_sumo/core/route_sensor_matrix.py) | Reuse incidence, rank, unseen-route, and indistinguishable-route checks |
| Stage results and updates | [`workflow_state.py`](../../plugins/astra-sumo/src/astra_sumo/core/workflow_state.py) and Hamburg execution workflow | Reuse result records and dependency handling before introducing another runner |

A physical identity can locate an approach, facility, or portal across network versions.
It must not imply a one-to-one lane correspondence after a split or merge.
Record candidate-specific mappings, confidence, and unresolved cases.
Release evidence still binds to exact source and candidate hashes.

Use the current CLI and workflow selection path. Add a new public surface only when a working use case needs it.
Extract shared code when two implemented workflows need the same behavior.
The first facility use case does not require a generic plugin hierarchy or another workflow engine.

## First implementation choices

Run three development tracks in parallel: paired driving comparison, native parking operations, and held-out validation.
The smallest first merge can wrap existing frozen-request replay in a paired comparison report.
Scoped edit verification and Hamburg corridor checks already need this comparison.
This addition needs no new field data or general facility framework.

The first new facility behavior can then use the same cohort and result records.
Held-out work can start with a read-only scan for eligible independent observation windows.
It need not wait for parking recognition or occupancy inference.

### Native parking operations

Proposed PR title: **Parking operations on existing detailed networks**.
The current discussion PR leaves its exact interface open.

Start with an explicitly selected facility and usable access lanes on an existing detailed network.
Reuse recognition results when available. Also accept an explicit facility declaration for an imperfect source.
The declaration records identity, portals, access restrictions, and an optional footprint.
A portal-only underground garage can have an unknown footprint.

Write separate SUMO additional and vehicle-stop files.
Keep the source network unchanged.
Use SUMO's [parking areas](https://sumo.dlr.de/docs/Simulation/ParkingArea.html) for capacity and parking stops.
Optional full-capacity alternatives use its [parking rerouting](https://sumo.dlr.de/docs/Simulation/Rerouter.html#rerouting_to_an_alternative_parking_area).
Placement, arrival, dwell, departure, admission, and queue behavior need native SUMO 1.27.1 checks.

Each operating value must be observed, assumed, or unknown.
An explicit assumed capacity or dwell time can support an explore scenario.
It cannot become evidence of actual facility capacity or parking behavior.
Unknown observations must not become zero demand or zero occupancy.
Publication checks must test the stated claim and its evidence.
A scenario study can retain declared assumptions. Assumed values must not appear as observed field data.
Keep observation basis and conditional inputs with each reported metric.
One global label can hide mixed evidence, such as measured street counts and assumed garage dwell times.
Report a sensitivity comparison when uncertain operating values affect the study question.
Declare its parameter ranges and basis before interpreting the result.

The first demonstration should measure arrival, the requested stop, departure, occupancy, and the full-capacity case.
It should also measure whether an entrance queue reaches the public road.
Reusing a street lane as off-road parking requires an explicit placement decision.
Building floors and roof area must not determine capacity automatically.

Wider automatic recognition remains a separate choice: surface lots, relation footprints, portal-only garages, and curb parking need different evidence.
The first operational example should expose which of these additions is actually needed.

## Representation detail is a study choice

| Representation | Appropriate use | Required comparison |
|---|---|---|
| Detailed internal network | Indoor routes or circulation affect the study | Preserve valid internal trip endpoints and access movements |
| Facility with access storage | Street queues and parking arrivals matter | Map internal trips to portals and preserve entrance storage |
| Arrival and departure boundary | Only exchange with the street matters | Declare the boundary flows and their uncertainty |

Changing representation creates a new candidate.
Every original physical origin and destination needs an explicit mapping or an unresolved record.
Keep real access roads when the roof image hides them.
For multilevel facilities, record floor and connection evidence before inferring ramps or conflicts.
Visual simplification alone changes no parking operation.

## Driving comparisons and demand evidence

Use the same physical trip cohort, operating assumptions, simulation horizon, and paired seeds.
The same seed does not guarantee the same per-trip random draws after an edit changes event order.
Use a declared seed ensemble before claiming that an outcome difference exceeds simulation variability.
Separate missing routes, unfinished trips, collisions, teleports, queue changes, and travel-time changes.
Compare complete trip records when the networks share valid endpoint correspondence.

An invalid baseline still produces diagnostic measurements.
Report that causal attribution is unresolved. Do not call a candidate correct because its baseline was worse.
Keep invalid pairs separate from performance summaries of valid pairs.
Spatial and temporal incident subsets can help diagnosis. Keep the full frozen cohort as the primary comparison.
Declare subset rules before reading candidate outcomes. A local absence of incidents does not establish an absence of indirect effects.
Use isolated movements to investigate failures before interpreting the mixed-demand result.
The Hamburg stop-line trial is an immediate case for this distinction.

Parking observations can add demand information when they exist and add independent constraints.
Occupancy differences constrain net arrivals minus departures only with a defined facility boundary and initial occupancy.
They do not reveal unique routes or dwell times by themselves.
Report matrix dimensions, numerical rank, tolerance, and remaining ambiguity before and after adding observations.
Do not promise that more rows increase rank.
Redundant observations can still test consistency with the fitted model.
Record which candidate routes each new observation touches.
Hypothetical observation layouts support measurement design. They are not acquired field evidence.
The current static incidence audit cannot establish the value of occupancy across time bins.
Keep any time-expanded occupancy model as a separate, justified extension.

Keep calibration and held-out periods separate.
Freeze the fitted parameters before evaluating the held-out period.
Report missing observations, initial-state uncertainty, and comparison validity alongside fit errors.
First inspect whether a nonoverlapping period has adequate counts, warm-up data, and signal history for the intended claim.
Missing validation data becomes an explicit coverage result.
Declare which held-out inputs can update demand and which observations remain evaluation targets.
Refitting to the held-out target counts creates another fitted window.
Street-count validation does not validate garage occupancy or dwell behavior without their own observations.
Develop paired comparisons and held-out validation alongside the parking example.
Neither must wait for broader facility recognition or a new city.

## Broader directions to compare

| Direction | Useful traffic question | Evidence needed |
|---|---|---|
| Facility arrival and occupancy observations | Which trip endpoints explain observed flows? | Dated entrance counts, exits, occupancy, and coverage |
| Surface and curb parking, loading | When does access or stopping reduce road capacity? | Curb allocation, restrictions, stopping demand, and durations |
| Transit stops and scheduled services | How do stops and arrivals affect queues? | Stop positions, service dates, routes, and dwell observations |
| Time-varying signals and access | Does replay represent the observed operating period? | Dated signal schedules, restrictions, and observations |
| Temporary works and closures | How should a dated road change update the scenario? | Effective dates, changed movements, and affected demand |
| Pedestrian and bicycle behavior | Which conflicts and clearance times matter? | Compatible geometry, mode demand, and control evidence |

Each direction should use the same source, correspondence, scenario, measurement, and update records where practical.
Demonstrate reuse on a second place before claiming city independence.
One Hamburg case and one independently sourced case can expose assumptions hidden in the current adapters.

## Open choices

- Should the first example use an existing garage or a surface lot with better operating observations?
- How much access storage must the facility representation preserve for the chosen study?
- Should the first observation input be a small dated table or a specific public feed adapter?
- Which workflow should demonstrate reuse after parking: transit stops, loading, or a second parking site?
- Which seed list and comparison rule are adequate for the first simulation-variability claim?
- Which update period and held-out comparison would support the first limited digital-twin claim?
- Should result records use one claim class or separate observation basis, validation scope, and conditional inputs?

These questions are discussion points. They are not extra prerequisites for daily calibration.
No new operating result, city-scale capability, or field accuracy follows from this proposal alone.

## Review record

Claude first proposed a generic facility layer, wider parking recognition, physical identities, and operational parking.
The discussion narrowed the first implementation to native operations on an existing detailed network.
Wider recognition and occupancy inference remain separate directions.
The proposal retains explicit assumptions and diagnostic comparisons when a baseline is invalid.
It does not impose a blanket publication ban on scenario assumptions.
Claude then proposed paired comparison as the smallest first implementation, with parking and validation as parallel tracks.
The proposal adopts that sequencing option. It leaves the exact claim-record format open.

The review used branch `codex/claude-network-cleaning-v1` at `e0fe240`.
This document preserves the recommendations and debated limits from that English discussion.
