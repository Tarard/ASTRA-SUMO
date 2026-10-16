# Discussion v2: study boundaries and parking portals

Status: open proposal, 2026-10-16. This document adds no operating behavior.
It supplements the [first workflow proposal](digital-twin-workflow-proposal-v1.md), which remains unchanged.

Claude independently reviewed construction commit `6791bd6` and later checked the revised ownership tests at `4f47194`.
The English discussion then reconsidered the earlier parking-first priority.
Claude proposed a bounded study with declared vehicle entry and exit points.
Codex challenged any claim that removing a simulated jam proves the removed context was wrong.
Both agreed that the existing OSM extract already has spatial limits.
The useful addition is an explicit study boundary and measured dependence on its assumptions.

## Question and research basis

Can ASTRA reproduce traffic inside a declared study area while exposing the influence of demand, surrounding roads, and facility operations?

Established microsimulation guidance places the study limits around the traffic influence area, including relevant congestion and diversions.
Boundary queues can require a larger area. Adopt that scoping principle, rather than choosing an area to remove an inconvenient result.
Source: [FHWA, Traffic Analysis Toolbox Volume III, 2019, Chapter 1](https://ops.fhwa.dot.gov/publications/fhwahop18036/chapter1.htm).
This is authoritative practice guidance, not proof that any particular Hamburg boundary is adequate.

Knorr and Schreckenberg compare three microscopic models against one German motorway day's detector series.
Their open-boundary methods and downstream-boundary comparison show that boundary choices can change breakdown occurrence and duration.
Adopt their sensitivity question, without copying their motorway parameters or calibration choices into an urban corridor.
Source: [Journal of Statistical Mechanics, 2012, P10018](https://doi.org/10.1088/1742-5468/2012/10/P10018),
with [author full text, sections 4.2 and 5.3](https://arxiv.org/html/1209.5189v2).
Assessment: T2 method support and E2 limited experiment. It does not identify the cause of Hamburg's failure.

| Decision | Classification | Basis and remaining evidence |
|---|---|---|
| Declare the study question, area, period, and represented modes | Adopt | FHWA scoping practice. Hamburg's actual influence area still needs evidence. |
| Compare different amounts of surrounding road context | Adapt | Boundary sensitivity has published support. Ring counts and tolerances remain study choices. |
| Connect parking portals to the same physical crossing records | New proposal | A reuse opportunity from the discussion. No implemented consumer or field validation establishes it yet. |
| Treat a disappearing jam as proof of a context defect | Reject | The comparison also changes inflow, downstream influence, and possibly routes. |

## Proposed study declaration

A study would record its area, question, observation period, modes, demand source, operating assumptions, and simulation seeds.
It would bind the network and operating files to the exact versions used.
Existing construction evidence remains useful within that larger declaration.
Reuse the current workflow records and dependency handling before adding another public command or common abstraction.

Each physical crossing would record a direction, position, source reference, and a network-specific lane association.
Claude calls these crossings "gates." They are vehicle entry or exit locations, not approval checks.
Candidate-specific associations must retain uncertainty after a road split, merge, or lane-count change.

Proposed crossing kinds are road entry, road exit, and facility portal.
Flow inputs may come from an observation, a parent simulation, or an explicit scenario assumption.
Keep those bases distinct. Missing observations must not become zero flow.
Observed crossings can constrain admitted flow without identifying demand held upstream.

The amount of surrounding road context is another declared input.
Distance, travel time, and successive road connections are alternatives for describing it.
Keep relevant downstream storage and alternative routes when the study question depends on them.
An unrestricted exit can remove downstream queue effects.

## Extraction and trip correspondence

SUMO supplies [edge-retention options](https://sumo.dlr.de/docs/netconvert.html#edge_removal) and
[`cutRoutes.py`](https://sumo.dlr.de/docs/Tools/Routes.html#cutroutespy).
The latter can derive reduced-route departure times from recorded edge exit times.
These are available building blocks. Their use does not establish unchanged junction geometry or equivalent traffic.

The proposed extraction would retain an immutable parent and identify every removed road and boundary crossing.
Verify interior lane shapes, logical lengths, permissions, connections, junction requests, and signal programs.
Keep extraction artifacts separate from networks accepted for a different study.

The current comparison runner expects the same explicit routed vehicles on each network.
It cannot compare a reduced network unchanged when the old routes contain removed roads.
A new comparison needs correspondence between the original trip and each retained visit to the study area.
One trip can enter repeatedly. Visits need separate identities and a link to the original trip.
Never-entering and unobserved visits remain explicit outcomes.

The current routeability writer requests edge exit times, but does not explicitly request unfinished vehicle-route output.
A jammed parent can therefore leave incomplete boundary timing evidence.
Audit the actual crossing coverage before cutting routes. Missing crossings cannot silently disappear from the denominator.
Parent-derived timings remain simulated inputs, including the parent's delays and errors.
Observed boundary counts support a different conditional replay.

## Accounting and comparison

Use separate quantities for requested entry, admitted entry, vehicles inside the area, and vehicles waiting outside.
For one interval, a proposed accounting definition is:

$$
N_{end}=N_{start}+A+S-D-F+R.
$$

$N$ includes vehicles on roads and parked vehicles within the declared area.
$A$ and $D$ count actual boundary entries and exits.
$S$ and $F$ count internal trip starts and ends.
$R$ records documented exceptional net additions or removals, including any relevant teleport or removal event.

For the insertion backlog:

$$
B_{end}=B_{start}+Q-A-C.
$$

$Q$ counts requested entries and $C$ counts explicitly canceled requests.
Facility occupancy needs its own initial stock, parking admissions, and parking departures.
These definitions are proposed accounting rules. They are not implemented measurements.

Report unfinished visits as censored observations and retain requested-entry waiting.
Distinguish queue discharge, insertion backlog, unresolved blocking, collisions, and an insufficient observation horizon.
Do not change the existing full-cohort construction diagnostic to make a reduced model pass.

Claude proposed locating blocking vehicles in the parent, then comparing surrounding-road extents and flow sources.
Its examples of zero to three road rings and ten seeds remain suggestions.
No selected tolerance, statistical power claim, or approved field-validation rule follows from those numbers.
An observed blocking group is a mechanism clue. It does not alone prove a construction error or actual field gridlock.

The current detailed blocking probes remain local diagnostic scripts.
A reusable diagnosis entry point and direct measurement of true blocking relations still need implementation.
Reported future conflicts must not all be treated as current blocking dependencies.

## Parking and other real facilities

A parking portal can supply arrivals and departures while the facility supplies storage, admission rules, and dwell times.
Accept an explicit facility declaration when recognition is incomplete.
OSM, an operator record, and a manual declaration can each propose a facility, with their own evidence.
Building footprint and floor count do not establish capacity.

Keep the detailed driving network for the first operating example.
Use native parking areas in separate operating files, then measure occupancy, dwell, departures, full-capacity behavior, and entrance queues.
The current network-only comparison does not load those operating files.
Its extension must bind them to each scenario and retain their observation or assumption basis.

Preserve street queue storage when a full garage affects the corridor.
If parking search leaves the study area, state that omitted behavior and its possible influence.
Surface parking, loading, transit stops, and time-dependent access remain further uses to compare.
Two proposed consumers are not yet evidence that a shared facility representation works.
Extract common behavior when actual implementations need it.

## Open choices

- Which traffic question and observations justify the first Hamburg study boundary?
- How should downstream queue influence and trips that leave and re-enter be retained?
- Which crossing times are observed, parent-derived, assumed, or missing?
- Which surrounding-road extents and simulation seeds answer the chosen sensitivity question?
- Which measures and tolerances would justify a claim of low boundary sensitivity?
- Should the first implementation demonstrate extraction, parking operations, or crossing-observation coverage?
- Which independent period and second site can test reuse without Hamburg-specific assumptions?

Daily exploration should continue to return measurements with uncertainty.
This proposal does not add a new prerequisite for road calibration or authorize a field-performance claim.
