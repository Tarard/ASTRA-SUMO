# Endpoint coverage and retained route failures

Production code: `ddedffb`.
The [frozen record](endpoint-routing-coverage-20261020-v1.json) binds the implementation, inputs, reports, paired traces and preceding failed CI.

## Independent review and repair

The independent review found that one unresolved endpoint prevented all candidate routing at `b2810d5`.
Local repetitions confirmed the two reported Hamburg cases.
The shared audit now sends every original request to the strict router.
Unresolved physical selectors receive separate road-level probes with their original vehicle IDs, departure times and corresponding road OD.
The original request file retains all physical selectors.
The correspondence report identifies every logical-only probe and its unresolved or removed endpoints.

No request is filtered, replaced or resampled. The router does not ignore errors.
Successful logical probes leave physical endpoint qualification incomplete and retain review.
Routing and driving failures remain failures when other endpoints are unresolved.
Daily exploration retains measurements and the separate diagnostic outcomes.

## Native position correction

The pinned SUMO 1.27.1 base insertion position is `min(5.1, original_edge_length)` for the default passenger type.
The previous mapper used 5 m. A native source/candidate control exposed its 0.1 m departure displacement.
The repaired control also covers a 5.05 m source edge, which clamps the base position.
Five native cases compare generated and fixed requests on one- and two-lane roads.
Explicit numeric lane selectors are checked against native trip output.

SUMO 1.27.1 uses `best_prob` when the departure lane is omitted.
The mapper now checks all possible passenger lanes for that selector.
It retains the lane-selection rule and qualifies each source lane's endpoint set within 0.02 m.
Realized lane choices can change with queues and route context.
An intermediate assertion requiring identical realized lanes failed on a two-lane control.
The final control uses the declared lane endpoint set while retaining exact checks for explicit numeric selectors.
The earlier failed inputs and logs remain available.

These conventions follow the pinned [base-position implementation](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/microsim/MSBaseVehicle.cpp)
and [departure defaults](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/microsim/MSFrame.cpp).

## Hamburg repetitions

The review fixture is the decompressed `hamburg_loaded_links_20261018_v1/candidate.net.xml.gz`.
Its SHA-256 is `0eb22507926899f40dc1cd356dc52799d14da5cd3bff6fc75c27b17d2447f30a`.
This is the selected Hamburg corridor model.

| Case | Original requests | Physical qualification | Road-level routing | Driving |
|---|---:|---|---|---|
| Delete `609417456` and `60578487#0` | 100 | 98 qualified, 2 removed endpoints recorded | Fails strictly | Not run after routing failure |
| Original lane-count baseline | 100 | Original requests | Passes | 63 arrive, 36 run, 1 waits at 2400 s |
| `47854310#1`, 3 to 2 lanes | 100 | 97 qualified, 3 unresolved lane selections | Passes for all 100 | 63 arrive, 36 run, 1 waits at 2400 s |

The lane-count explore command returns `measured` and records the dirty source baseline.
Both underlying driving checks remain failed/incomplete.
Their equal arrival counts do not establish unchanged behavior.

The existing paired-driving tool repeats the unchanged original routed vehicles with seed 42 and horizon 2400 s.
It retains every requested vehicle and reports:

| Source observation | Candidate observation | Vehicles |
|---|---|---:|
| Arrived | Arrived | 62 |
| Unfinished | Unfinished | 36 |
| Arrived | Unfinished | 1, ID `68` |
| Unfinished | Arrived | 1, ID `83` |

The pair is invalid for a performance comparison. `performance_mean` remains null.
The diagnostic repeat records collision action and unfinished-route output in its own plan.
The three unresolved physical endpoints remain unqualified in this road-level trace.

The earlier four-request real merge control also repeats on committed code.
Original, local candidate and full-conversion control all complete normally with explicit source lane selectors.
The unmapped negative control still fails routing.
Its separate 100-request batch now runs instead of stopping at the unresolved endpoint.
It retains 99 physically qualified requests and one logical-only request.
At 1200 s, 98 arrive and two remain running, matching the original model's incomplete count for that cohort.
The two source models, cohorts and horizons remain separate.

## CI and focused checks

The previous `b2810d5` complete Windows runs failed:

| Run | Passed | Failed | Skipped | Required native cases passed |
|---|---:|---:|---:|---:|
| PR | 3748 | 2 | 24 | 50 |
| Push | 3749 | 1 | 24 | 50 |

Both runs hit a stale contour-test expectation that routing remained unexecuted.
Its saved report actually records completed, passing routing.
The updated test checks all 20 original requests and their normal completion while retaining the contour review outcome.

The PR run also lost the native bus-permission connection after 32.051 s.
Its 30-second deadline and empty native log leave the underlying cause unestablished.
The test now uses the production Windows no-window policy and process-tree cleanup.
It retains a separate timeout marker and native error log.
All permission rejection and bus-arrival assertions remain active.
The local checks pass. Repeated CI qualification is still required.

The routing group passes 79 tests. The review-consumer group passes 113.
The later CI-regression, paired-driving and navigation group passes 27. Counts overlap.
Ruff and whitespace checks pass.
The current execution policy requires 54 native cases, including the new masking controls and the real bus-permission case.
Full Windows CI for `ddedffb` was running when this record was written.

## Verified locations and limits

- Review-case runner: [Hamburg repetitions](../../outputs/probe_endpoint_review_hamburg_20261020_v2.py).
- Merge runner: [existing merge controls](../../outputs/probe_merged_od_hamburg_20261020_v5.py).
- Per-vehicle runner: [paired diagnostic](../../outputs/probe_endpoint_review_pair_20261020_v1.py).
- Analysis: [frozen-record builder](../../outputs/freeze_endpoint_routing_coverage_20261020_v1.py).
- Settings and hashes: [review declaration](../../outputs/epreview26v2/declaration.json), [paired plan](../../outputs/epreviewpair26v1/plan.json).
- Raw results: [review cases](../../outputs/epreview26v2/), [merge cases](../../outputs/odmap26v5/), [paired traces](../../outputs/epreviewpair26v1/).
- Regression cases: [native endpoint tests](../../tests/test_routeability_od_mapping.py).

Generated local files are not distributed with the repository.
The public JSON retains hashes, counts, unresolved identities and the failed CI receipts.
These controls establish neither field correctness nor automatic-cleaning precision or recall.
Junction absorption still lacks physical correspondence for original endpoints on removed ordinary roads.
Source driving faults, native-process intermittence and remaining independent-review findings remain open.
