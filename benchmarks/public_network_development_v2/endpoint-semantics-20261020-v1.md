# Default placement and retained lane correspondence

The [frozen record](endpoint-semantics-20261020-v1.json) binds the production code, source model, reports, request counts and CI receipts.
This follows the [removed-endpoint repair](removed-endpoint-coverage-20261020-v1.md).

## Repairs

On a retained directed road, default departure and arrival positions keep their road-relative meaning.
They do not become fixed world coordinates when a junction boundary changes.
The report records coordinate displacement separately.
Its request counts distinguish fixed physical positions, road-relative defaults, logical probes and removed-road endpoints.

Lane-count edits reuse the source lane correspondence already recorded in the edit plan.
Explicit source lane indices follow retained lanes after renumbering.
An explicit request on a removed lane remains unresolved.
Dynamic defaults can use the remaining lane set, with removed source lanes recorded.
An unspecified lane does not establish a fixed physical point when the candidate offers different lane choices.

A fixed position requires unique local projection and a matching local direction.
It does not require the entire source lane to fit the candidate.
For example, clipping a lane from 50 m to 46 m does not invalidate an unchanged point at 10 m.
Points beyond the candidate boundary, reversed directions and ambiguous projections still remain unresolved.

Two earlier reporting repairs are also retained.
Failed partial networks have the `failed_local_subset` role and no usable candidate path.
Loaded-link worker startup errors appear in report warnings as well as command logs.

## Real Hamburg checks

The source is the selected Hamburg corridor, SHA-256 `0eb22507926899f40dc1cd356dc52799d14da5cd3bff6fc75c27b17d2447f30a`.

| Case | Request coverage | Result |
|---|---|---|
| Delete spur `609417456`, seed 5 | 19 fixed physical, 1 road-relative | All 20 complete. The earlier default-position false alarm is removed. |
| Same deletion, seed 42 | 18 fixed physical, 2 removed-road endpoints | The 18 executable requests complete. Overall evidence retains review. |
| Add a right lane to `47854310#1` | 96 fixed physical, 4 road-relative, no unresolved positions | All 100 receive routes. Driving records one collision and remains incomplete. |
| Remove lane 0 from that road | 96 fixed physical, 4 road-relative, no unresolved positions | Strict routing fails on lost paths. Explore retains the blocked result. |

The new right lane has neither an incoming nor an outgoing ordinary connection.
The collision involves vehicles `90` and `95` at junction `77777614`, outside the two mutable junctions.
Its cause is not established.
Both 100-request source baselines are already incomplete.
These runs establish no traffic benefit, valid performance comparison or promotion.

Random sampling still does not guarantee that an edited movement is tested.
Deterministic edit-targeted checks remain open.

## Checks and CI

The default-placement group passes 85 tests. Its shared consumers pass 137 tests.
The later combined lane-correspondence group passes 230 tests. These counts overlap.
Repository navigation and install-document checks pass 11 tests.
Native controls cover right additions, low-index removals, explicit lane identity, clipped endpoints and preserved default placement.
They also retain removed requests, strict disconnection failures, input hashes and count validation.
Failed local test attempts remain identified in the JSON record.

Both complete Windows suites for preceding code `642b2f8` pass 3,758 tests, with 24 skipped and zero failures.
All 57 native cases required by that version execute successfully.
Those receipts do not qualify the later repairs.
The current execution policy requires 61 native cases. Current full CI remains separate.

## Verified locations

- Runners: [default placement](../../outputs/probe_relative_endpoint_hamburg_20261020_v2.py) and [lane edits](../../outputs/probe_lane_endpoint_hamburg_20261020_v1.py).
- Analysis: [receipt builder](../../outputs/freeze_endpoint_semantics_20261020_v1.py).
- Configuration: [default declaration](../../outputs/relativeep26v2/declaration.json) and [lane-edit declaration](../../outputs/laneep26v1/declaration.json).
- Raw results: [default runs](../../outputs/relativeep26v2/) and [lane-edit runs](../../outputs/laneep26v1/).
- Regressions: [native endpoint cases](../../tests/test_routeability_od_mapping.py) and [daily edit integration](../../tests/test_network_explore.py).

Generated local files are not distributed with the repository.
The public JSON retains their hashes, statuses and request counts.
