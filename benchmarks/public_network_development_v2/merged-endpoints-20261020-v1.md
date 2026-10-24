# Original endpoints after a road merge

## Repair

Code `b2810d5` samples diagnostic requests from the original network alone.
Candidate geometry and missing road IDs cannot exclude source endpoints before sampling.
Frozen requests also remain unchanged in a separate source file.

The existing merge-path and lane-access checks establish the road correspondence.
Retained directed road and lane identities support small geometry differences too.
Lane positions use each lane's geometric-to-logical length ratio, including XYZ coordinates.
Projection and the encoded candidate position must each remain within 0.02 m of the source point.
This avoids treating a road's logical length as its shape length or omitting internal gaps.
The router must retain explicit positions and lane selectors.
Driving checks inspect nonnegative endpoint positions in native trip output, allowing 0.01 m for output rounding.

No request is replaced or removed.
An unresolved correspondence records every original request and returns review without running a reduced set.
Daily exploration retains its measurements and adds a warning.
Release checks retain the review outcome.

## Real Hamburg control

The [frozen record](merged-endpoints-20261020-v1.json) binds the inputs, implementation files, runner and raw reports.
The existing local and full candidates merge `83075269#0` and `83075269#1`.
Four synthetic requests cover both lanes and use the absorbed road as an origin or destination.
Their lane selectors and positions are declared before candidate routing.

| Network and handling | Original requests | Result |
|---|---:|---|
| Original Hamburg | 4 | Routes and normal completion pass |
| Local candidate without correspondence | 4 | Routing fails because an original road ID is absent |
| Local candidate with correspondence | 4 | Routes, positions and normal completion pass |
| Full-conversion candidate with correspondence | 4 | Routes, positions and normal completion pass |
| Local candidate, independently generated source requests | 100 | Review: one unresolved arrival request, all requests retained |

The unresolved request uses `arrivalLane="current"` on a curved source section.
Its possible lanes require different positions after merging.
The code cannot encode them with one scalar arrival position within the declared tolerance.
It does not choose a lane after observing the candidate or remove that request.
Explicit source lane selectors work in the separate four-request control, without resolving the original 100-request batch.

The source network, both candidates and original request file retain their hashes.
The persisted successful reports pass independent routeability-evidence validation.
The unresolved report validates as review, and the unmapped negative control remains blocked.
These are technical checks of synthetic trips on an actual city model.
They establish neither historical demand nor field correctness or traffic benefit.

## Regression checks and retained failures

- The final routeability, exploration and edit-profile group passes 106 tests.
- The default candidate-check and repository-navigation group passes 37 tests.
- Earlier routeability and paired-driving groups pass 59 and 84 tests. Counts overlap.
- Four required native cases cover generated and frozen requests on one- and two-lane roads.
- Projection controls cover unequal logical lengths, internal gaps, elevation, reversed shapes and ambiguous folded shapes.
- Fault controls reject changed router positions, changed trip-output positions and input/output path collisions.

The first two native probe failures lacked saved removed-node metadata in the test conversion.
Their inputs and logs remain in the failure archive.
The corrected probe uses the same removed-node output option as production materialization.

One earlier profile expectation also failed after restoring the full source request pool.
The reciprocal-fragment control absorbs source roads `A_B` and `B_A`, which are about 20 m long.
Its former common pool had omitted endpoints on those roads.
Construction and all-class boundary checks still pass, but original endpoint correspondence remains unverified.
The regression now checks that review result and the retained 20 original requests.
No ordinary-road correspondence is invented for endpoints absorbed into a junction.

Complete Windows CI for `b2810d5` was running when this record was written.
The [preceding completed runs](standalone-local-ci-20261020-v1.md) qualify earlier code only.

## Reproduction locations

- Runner: [Hamburg endpoint probe](../../outputs/probe_merged_od_hamburg_20261020_v3.py).
- Analysis and evidence validation: [record builder](../../outputs/freeze_merged_od_20261020_v1.py).
- Configuration and input hashes: [declaration](../../outputs/odmap26v3/declaration.json).
- Raw results: [result directory](../../outputs/odmap26v3/).
- Native regression: [endpoint tests](../../tests/test_routeability_od_mapping.py).

Generated local files are not distributed with the repository.
The public JSON retains the input identities, selected endpoint measurements, unresolved request and report hashes.
SUMO's [position definitions](https://sumo.dlr.de/docs/Definition_of_Vehicles%2C_Vehicle_Types%2C_and_Routes.html)
and [lane geometry conversion](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/microsim/MSLane.h)
define the position conventions used here.
