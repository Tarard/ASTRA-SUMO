# Pedestrian facility regression and native walking checks

The [record](pedestrian-paths-20261019-v1.json) retains the inputs, commands,
outcomes, and limits. These checks extend the
[scoped topology follow-up](topology-source-20261019-v2.md).

## Facility structure

Commit `af18eab` blocks missing or added pedestrian facilities and changed
facility types or lane identities. A native fixture test renames a walking area
while keeping its lane references and connections consistent. Protected counts
and boundary-path sets remain unchanged, but the facility check blocks it.

Independent review then found another case. Generated IDs can stay the same
when a crossing changes which roads it spans. Commit `1974a6d` adds crossed-road
membership and walking-area lane neighbours to the same structural check.
Swapped crossing assignments and swapped walking-area neighbours now block,
even when the overall boundary-path sets are unchanged. Crossed-road list order
does not change the structural inventory.

The additional semantic check finds no changed facilities in the prior native
pairs for `132844`, `132981`, and `133544`. Their input hashes remain unchanged.
This is a read-only inventory check, not a new complete release run.

## Native walking at 133544

The source has five incident pedestrian roads. The probe uses one fixed
position near the edited junction on each road. It checks all 20 ordered pairs
of distinct roads. The existing directed lane-path check represents five of
these pairs. Native pedestrian routing finds all 20 on the source, identity
control, and candidate. The additional pairs include reverse walking.

The source's returned routes form one frozen 20-person request file. Each
network receives the same bytes. No failed request is replaced. Departures
occur at seconds 0 through 19. The scenario uses one seed, a maximum walking
speed of 1 m/s, the striping model without dawdling, no cars, and a 600 s end.

| Network | Inserted | Arrived | Teleports | Discarded | Jammed |
|---|---:|---:|---:|---:|---:|
| Source | 20 | 20 | 0 | 0 | 0 |
| Identity control | 20 | 20 | 0 | 0 | 0 |
| Candidate | 20 | 20 | 0 | 0 | 0 |

FCD records retain all 20 people in each run. Every person traverses local
internal facilities. No observed edge lies outside the declared incident roads
and this junction's internal edges. Collapsed edge sequences match between the
three networks. Position output has two decimal places, so these records do not
establish equal trajectories or sub-centimetre geometry.

The five explicit lane pairs are not a complete pedestrian-route check. These
native probes provide additional finite-case evidence. They do not enable
automatic acceptance of changed pedestrian layouts. Mixed vehicle interactions,
field safety, other requests, and other seeds remain unverified.

## Native fold at 140489

Independent review selected this converter regression after observing its
outcome. It is separate from the original fixed three-case cohort.

The new [fixture](../../tests/fixtures/most-contour-controls/140489.net.xml)
retains the original saved lane geometry. Its native load check passes.
The identity control is stable. A 0.5 m contour tightening passes the requested
geometry screen but folds walking area `:140489_w0` into a self-intersection.
Its valid control polygon has XY area 3.088746 m². The candidate has XY area
1.606688 m² and is invalid. `walking_area_validity` correctly blocks it.

The [provenance](../../tests/fixtures/most-contour-controls/provenance-v3.json)
records the parent source hash, extraction scope, exact requested polygon,
licence context, and selection limit. This is a model regression, not a field
defect label or an independent estimate of cleaning precision.

## Verification and provenance limits

The first related checks passed 24 tests. The real fold and navigation checks
passed eight tests. After the semantic correction, the final related set passed
33 tests. These sets overlap and must not be added together. Ruff and
`git diff --check` passed.

Claude confirmed the native fold and the first identity correction. Its review
found the semantic gap, which the final correction addresses. Review of
`1974a6d` is still running in this snapshot under the subscription allowance.

The earlier `b135e7a` test-step logs each show 3517 passed and 24 skipped.
The final correction's complete CI is not claimed complete in this snapshot.

The exploratory walking runners did not record their producer commit. Their
network hashes, native versions, request bytes, commands, and output hashes are
retained. The initial fold runner also had a Path-argument error before native
conversion. Its failed log remains beside the corrected second run.

## Local reproduction artifacts

Paths are relative to the repository. Generated runs remain local.

- Native route probe: `outputs/pedestrian_paths_20261019_v1/probe.py`.
- Frozen-person runner: `outputs/pedestrian_paths_20261019_v1/complete-walks.py`.
- Raw person runs: `outputs/pedestrian_paths_20261019_v1/walking-completion/`.
- Request file: `outputs/pedestrian_paths_20261019_v1/walking-completion/source-frozen.people.rou.xml`.
- Analysis: `outputs/pedestrian_paths_20261019_v1/analyze.py` and `analysis.json` in the same directory.
- Fold runner: `outputs/pedestrian_paths_20261019_v1/reproduce-folded-area-v2.py`.
- Fold results: `outputs/pedestrian_paths_20261019_v1/folded-area-reproduction-v2/results.json`.
- Semantic inventory check: `outputs/pedestrian_paths_20261019_v1/check-cohort-semantics.py`.
- Final related tests: `outputs/pedestrian_paths_20261019_v1/facility-semantics-regression.log`.
