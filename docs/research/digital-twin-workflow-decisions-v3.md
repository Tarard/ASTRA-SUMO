# Proposed defaults v3: parking observations and reusable studies

Status: open design proposal, 2026-10-16. This PR adds no operating capability.
The [v1 proposal](digital-twin-workflow-proposal-v1.md) and [v2 discussion](study-boundaries-and-parking-discussion-v2.md) remain as history.

The defaults below remain open to review. Each row states what evidence would change it.

## Proposed decisions

The owner column names the capability responsible for implementation, not an assigned person.

| Choice | Proposed default | Owner | Evidence that would change it |
|---|---|---|---|
| First parking site | Keep Speicherstadt. Check its observed facility record and portal correspondence first. | Build + Calibrate | Missing or inconsistent observations, or a different physical facility. |
| First observation work | Read existing observation windows and report gaps, overlaps, dates, and missing signal history. | Calibrate | Another documented source supplies an eligible independent period. |
| Observation collection | Search for existing dated archives first. This PR configures no recurring collection. One snapshot cannot support occupancy comparisons. | Calibrate | A documented archive, or a declared collection location, cadence, and source terms. |
| Scenario claims | Separate assumed scenarios, observation-initialized runs, fits, and independent-period checks. A refresh alone does not upgrade evidence. | Simulate | Matched independent observations support a claim about a specific measured quantity. |
| Parking detail | Keep mapped driving paths. Add native parking operations through separate operating files. | Build | A paired test shows that an abstraction preserves the required portal queues and demand accounting. |
| Unknown operating inputs | Permit declared capacity, initial occupancy, dwell, and flow assumptions in exploration. Keep their origin explicit. | Build + Calibrate | Dated observations replace those assumptions. Building size alone does not. |
| Reusable study record | Propose one JSON record for place, question, period, modes, crossings, sources, operating files, seeds, and hashes. | All three | Two working studies show that the same record cannot represent their requirements. |
| Physical correspondence | Retain source identities and candidate-specific associations with confidence. Recheck exact versions before publication. | Build | Reviewed split or merge evidence changes the correspondence. |
| Modes and curb activity | Inventory motor traffic, cycling, walking, scooter access, curb parking, and loading before adding behavior. | Build | The study question and sources justify a smaller or more detailed representation. |
| Refresh | Reuse existing dependency handling to report stale results after a new source snapshot. Rebuild affected stages. | All three | Implemented consumers show that an additional common component is needed. |
| Second place | Choose it from usable dated data and comparable questions. Münster remains a candidate. | Calibrate | Source checks establish better coverage or independent periods elsewhere. |

The proposed study record is a design target. This PR adds no product schema or command.
The first implementation should include a small validated example, rather than a new workflow framework.
Daily exploration still returns measurements. These defaults add no prerequisite to current road calibration.

## Parking source check

The [Hamburg official catalog](https://suche.transparenz.hamburg.de/dataset/parkhaeuser-hamburg17) describes partial, unchecked parking availability.
Its [WFS data service](https://geodienste.hamburg.de/wfs_parkhaeuser?REQUEST=GetFeature&SERVICE=WFS&VERSION=2.0.0&typename=de.hh.up%3Aparkhaeuser) returned Speicherstadt at Am Sandtorkai 6.
The [frozen check and raw response](../../benchmarks/parking_source_feasibility_20261016_v1/README.md) provide the response hash and one facility observation.
Source attribution: Freie und Hansestadt Hamburg, Behörde für Verkehr und Mobilitätswende. License: Datenlizenz Deutschland Namensnennung 2.0.

The response contains 127 feature members, although its envelope reports `numberReturned=0`.
Speicherstadt reports 553 total spaces and 452 free spaces, with `received` equal to `16.10.2026, 10:24`.
The catalog states five-minute updates. The [API description](https://api.hamburg.de/datasets/v1/parkhaeuser) states ten-minute updates.
Preserve those inconsistencies. The response timestamp is UTC. The facility timestamp has no stated zone in this response.
The point location supports a facility candidate. It does not locate its vehicle portals.

This single snapshot establishes source availability for the target facility.
It establishes neither a historical observation series nor a verified capacity or portal binding.
Changes in occupancy constrain net admissions minus departures. They do not identify both flows or dwell times separately.
An occupancy fit alone cannot validate entrance queues or parking-search behavior.

Münster's [official resource](https://opendata.stadt-muenster.de/dataset/parkleitsystem-parkhausbelegung-aktuell/resource/adbef66c-271b-4e25-a9aa-2c8e263e701f) advertises minute updates.
Its live response and historical coverage have not been checked in this work.
The earlier claim that Hamburg has no relevant public availability source is withdrawn.

A read-only observation audit precedes a data-informed parking comparison.
The proposed study record should list observation hashes, periods, measured cadence, coverage, and the claim supported by each source.
Check actual member counts, value ranges, timestamp reversals, repeated records, missing periods, and changes in reported capacity.
Keep source inconsistencies as findings so exploration still returns the available measurements.
Future collection should retain HTTP dates and modification headers. This first check did not save those headers.
The meaning of `received`, its time zone, and treatment of reserved spaces remain unknown.

Use separate descriptions for assumed scenarios, observation-initialized runs, occupancy fits, and checks on independent periods.
Initialize parking stock from availability only after the definitions of total and free spaces support that conversion.
Choose quality criteria on an exploratory period, then freeze them before the independent comparison.
No recurring collection is configured by this proposal.

## First usable steps and measures

1. Check observation eligibility and facility correspondence. Report usable periods and unresolved cases.
2. Represent one native parking scenario with explicit operating inputs and the existing detailed network.
3. Check prescribed inputs against execution. Report simulated admissions, parking stock, departures, insertion waiting, and portal queues as conditional outputs.
4. Compare the same requests and seeds. Preserve collisions, unfinished requests, and invalid comparisons.
5. Refresh from another dated observation and report affected bindings and stale results.

For a signal change, report corridor queues and variation across seeds.
Directly prescribed dwell times support input-conformance checks. They are not independent observations of parking behavior.
For a garage entrance change, report whether its queue reaches the public road and for how long.
For a loading zone, report the time and lane length unavailable to moving traffic.
Select study-specific numerical criteria before interpreting those results as successful interventions.
The current Hamburg collision and unfinished-demand findings remain unresolved.
