# Traffic-selected lane edits and retained nonmotor access

Commit `36265b8` follows the [signal-binding repair](lane-count-preservation-20261019-v2.md).
It fixes the mixed-mode layout gap without changing saved physical-index requests.

## Interface and checks

`num_lanes` still counts all SUMO lanes. Optional `vehicle_class` selects the
source lanes that may be copied or removed. Right insertion uses the rightmost
compatible lane as its template and preserves the outer facilities' indices.
An explicit physical removal must match the selected class when one is supplied.

For the frozen Hamburg approach, selecting `passenger` adds a third passenger
lane between its cycle lane and existing passenger lanes. It retains one
sidewalk and one cycle lane. Their permissions and junction access remain.
Native reconstruction can still move lane geometry inside the declared scope.
This is a construction check, not a field-calibrated widening proposal.

The new source-relative check detects lost explicit junction access of retained
dedicated walking and cycling lanes. It follows declared lane mappings, accepts
walking-area renaming, and treats pedestrian access as bidirectional. Declared
movement and lane removals remain visible. It also detects newly duplicated
sidewalks or replacement of the pedestrian model's selected source sidewalk.

This uses the [SUMO pedestrian model's sidewalk selection](https://sumo.dlr.de/docs/Simulation/Pedestrians.html).
Class selection is an ASTRA interface choice. Its total-count meaning follows
the [SUMO lane descriptions](https://sumo.dlr.de/docs/Networks/PlainXML.html).

Explore keeps measurements and warnings. Release checks reject unrequested
loss or unavailable preservation evidence. This checks explicit dedicated-lane
access, not full implicit pedestrian routing or field legality.

New controlled connection records can now declare `tl`, `linkIndex`, and
optional `linkIndex2`. The controller must serve that junction and the indices
must fit its source programs. Missing bindings for new controlled movements
receive an input error before conversion.

## Real Hamburg repeat

The clean committed-code repeat preserves the source, fact tables, deterministic
route file, seed 17 and 2400-second horizon from the previous diagnosis.

| Candidate | Total lanes | Sidewalks | Cycle lanes | Passenger lanes | Nonmotor check | Technical release checks |
|---|---:|---:|---:|---:|---|---|
| Same-count control | 4 | 1 | 1 | 2 | pass | pass |
| Default left addition | 5 | 1 | 1 | 3 | pass | pass |
| Legacy raw right addition | 5 | 2 | 1 | 2 | fail | fail |
| Legacy removal of index 0 | 3 | 0 | 1 | 2 | fail | fail |
| Passenger-selected right addition | 5 | 1 | 1 | 3 | pass | pass |

Raw right insertion displaces the retained sidewalk as the model's selected
lane and loses its access. Removing index 0 explicitly deletes the sidewalk,
but also loses junction access on two other retained sidewalks. The new check
reports that collateral loss. It does not reinterpret the requested index.

All five candidates remain measured in explore. Each completes 100/100 cars
without collisions or teleports. Each has zero changed outside-scope facts.
These car outcomes do not excuse the two failed nonmotor constructions.

Four fixed walking requests and two separate bicycle requests then ran on both
source and the passenger-selected candidate. All completed. No teleport,
collision, jam or discard warnings were recorded. These are small mechanism
checks. They do not establish general mixed-traffic performance or safety.

The unchanged saved road-shape, stop-line and speed outputs were separately
reassessed by the new access check. All three pass. This was a read-only
reassessment, not fresh materialization of those three candidates.

## New lane operation and verification

A native regression gives the new passenger lane an explicit existing signal
binding. A vehicle departs from that new lane and completes its route. The
parallel merging movement also creates a protected-green conflict. Explore
reports it; release does not qualify that construction. Valid binding syntax
alone does not establish a suitable merge or signal design.

Local checks include 111 modal-access and consumer tests, 20 class/binding/native
tests, and 21 final pure comparison tests. Counts overlap. The earlier failed
class-selection and missing-binding checks remain saved. Ruff and
`git diff --check` passed.

Both full Windows suites for preceding commit `3439ae5` passed. The
[PR suite](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39318158384)
reported **3592 passed, 24 skipped** in 815.80 seconds. The
[push suite](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39318149594)
also succeeded. Complete CI for `36265b8` was still running when recorded.

## Reproduction and limits

The [frozen summary](modal-lanes-20261019-v1.json) retains source/candidate hashes,
lane modes, selector remapping, failed access checks, and native completion.
Paths below are relative to the repository:

- Construction runner: `outputs/run_modal_lane_hamburg_20261019_v1.py`.
- Construction configuration: `outputs/modal_lane_layout_20261019_v1/hamburg-v1/declaration.json`.
- Raw construction, scorecards and car comparisons: `outputs/modal_lane_layout_20261019_v1/hamburg-v1/`.
- Walking/bicycle runner: `outputs/probe_modal_access_20261019_v1.py`.
- Native access configuration and results: `outputs/modal_lane_layout_20261019_v1/native-access/`.
- Reusable measurements: `plugins/astra-sumo/src/astra_sumo/core/paired_driving.py` and `network_nonmotor_access.py` in that directory.
- Summary runner: `outputs/freeze_modal_lane_20261019_v1.py`.
- Offline native regression: `tests/test_modal_lane_layout.py` with the existing frozen Hamburg fixture.

Original burst-demand failures, stochastic-driver validation, automatic-cleaning
precision and remaining whole-network scopes remain open. Operational parking
and field-valid digital-twin claims are outside this construction evidence.

## Later service-coverage finding

Claude's follow-up confirms the modal protection repairs and identifies a
reporting gap. The default passenger-selected candidate has no ordinary
incoming or outgoing movement on its new lane. Vehicles may enter or leave
through lane changes. Its lane count describes geometry and permissions; it
does not establish a third boundary through lane or extra capacity.

The native one-car regression above supplies an explicit outgoing movement.
It therefore checks a different construction, with the recorded merge conflict.
Per-new-lane movement inventories and appropriate release qualification remain
to be added. The offline confirmation is
`outputs/modal_lane_layout_20261019_v1/new-lane-service-followup.json`.
