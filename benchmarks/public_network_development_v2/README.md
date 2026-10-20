# Historical MoST input supplement

This catalog supplements [the four-model v1 corpus](../public_network_development_v1/catalog.json).
It preserves that catalog and adds the prepared input from the published MoST network revision.
It is the same Monaco model family, not another independent city.

## Reproduce

Run from the repository root with the project environment and a new result directory:

```powershell
$native = (Resolve-Path .venv/Lib/site-packages/sumo/bin/netconvert.exe).Path
./.venv/Scripts/python.exe -m astra_sumo.cli network reference-corpus benchmarks/public_network_development_v2/catalog.json outputs/public-reference-cache-v2 outputs/public-reference-run-v2 --netconvert-binary $native --json
```

Use an absolute native binary path from the intended SUMO installation.
The command verifies each source file against its catalog size and SHA-256.
The upstream files retain GPLv3 licensing. Downloaded networks and source data remain in the cache.

## Source and recipe

The [pinned upstream revision](https://github.com/lcodeca/MoSTScenario/tree/d00e82945f5c6322a98ad2df4a8d5f48274fca53)
contains the matching prepared input under `tools/static/most.raw.osm`.
The v1 record had used its later replacement under `tools/most.raw.osm`.
The supplement pins the earlier network, input, configuration, five type maps, and license.

The builder reads supported import settings from the configuration.
OSM and type-map paths must match catalog-bound files.
It preserves elevation, crossings, ramp, and other declared import settings.
It overrides junction joining, signal joining, and geometry removal for the unjoined comparison.
It does not execute upstream output paths or report paths.
Unsupported settings require an explicit implementation change.

Regeneration uses SUMO 1.27.1. It does not reproduce the original historical binary.
The configuration, actual arguments, overrides, binaries, and source hashes remain in the result.

## Original measured result

The retained local run is `outputs/calibration_driving_20261016_v1/most-corpus-v2/`.
The published network loads. The prepared import succeeds.
Under the existing exact correspondence rule, 10,969 comparison records contain:

| Record | Known targets |
|---|---:|
| Joined source-node pairs | 58 |
| Separate source-node pairs | 599 |
| Retained ordinary lane connections | 820 |
| Added ordinary lane connections | 80 |
| Removed ordinary lane connections | 2 |
| Total known targets | 1,559 |

The remaining 9,410 records retain unresolved correspondence.
These are model-relative development targets. Their manual-edit origin remains unestablished.
They do not establish a field defect or the precision of an automatic repair.

The pinned [build script](https://github.com/lcodeca/MoSTScenario/blob/d00e82945f5c6322a98ad2df4a8d5f48274fca53/tools/static/scenario.generator.sh)
invokes netconvert with the published configuration.
The target header records netconvert `v1_2_0+1020` and matching effective joining settings.
This comparison therefore belongs to recipe and version regression work.
Edits already present in the prepared OSM input are shared by both sides of this comparison.
The comparison does not isolate those human input edits.
The original frozen report marks `human_cleaning_evaluation: false` and the full-recipe control `not_run`.
The [later published-configuration control](recipe-control-20261016-v1.md) records that experiment and per-target attribution.
Its unexplained differences remain model differences, without cleaning or field attribution.

The published target matches all 1,559 known records in a self-check.
The unjoined comparison exposes 58 missing joins, 80 missing connections, and two extra connections relative to that model.
Both comparison reports retain `review_required` because unresolved records remain.
These outcomes test the comparison path. They do not establish the reference model's field correctness.

The prepared input includes edited and synthetic sources.
Its repository revision does not date the original OSM snapshot or the field state.
Both dates remain unknown. No current OSM substitutes for the missing original snapshot.

## Coordinate-frame repeat

The [2026-10-18 repeat](coordinate-frame-20261018-v1.md) corrects the unjoined import's normalization convention.
It retains 9,005 known model-relative records and 1,963 unknowns under the unchanged exact registration rules.
The previous input, label table, and results remain intact.
The repeat also records lost correspondence, one changed generated connection, and changed lane lengths.
It provides no field-defect labels or automatic-cleaning precision estimate.

The [source-sensitivity follow-up](source-sensitivity-20261018-v1.md) adds a controlled alternate import and per-selector records.
It keeps source-value changes, correspondence changes, and unscored native differences separate.

## Maintenance

The [2026-10-19 maintenance record](maintenance-20261019-v2.md) covers the later
native permission test, unchanged source OD sampling, and actual Python module paths.

The [native contour-control follow-up](contour-identity-20261019-v1.md) records
the real-model automatic-applicability false pass and the source-contour identity repair.
The [follow-up assessment](contour-identity-20261019-v2.md) retains review outcomes,
reports daily geometry changes, and removes the 132981 pedestrian false alarm.
The [source-topology qualification](topology-source-20261019-v1.md) records the
full-model release, its 170 inherited findings, and two native reporting checks.
The [scoped follow-up](topology-source-20261019-v2.md) records a fresh full-model
release, local source risks, and the final unchanged three-case assessment.
The [pedestrian follow-up](pedestrian-paths-20261019-v1.md) records native walking
completion, a real folded-area regression, and the facility-semantic correction.
The [source-validity correction](pedestrian-paths-20261019-v2.md) records the
corrected blocked/pass/review outcomes and a standalone native reproduction.
The [later complete release receipt](native-release-20261019-v1.json) reruns
132981 under code identical to `e1236f1`. All ten technical gates pass; the
170 inherited whole-network findings remain open.
The [native runtime follow-up](native-runtime-20261019-v1.md) records bounded
teacher and intersection checks, another unchanged Hamburg reconstruction,
and four valid pairs using the previously saved deterministic driving cohort.
The original burst-demand failures remain open.
The [lane-count follow-up](lane-count-preservation-20261019-v1.md) records an
unintended 12-to-6-phase signal rewrite exposed by unchanged-count controls.
It supersedes the earlier lane-count traffic-effect interpretation and records
the retained-turn and signal-program repair.
The [shifted-index follow-up](lane-count-preservation-20261019-v2.md) repairs
right-side additions and lower-index removals. It records independent source
signal checks, explore/release fault injection, and a clean committed-code repeat.
The [modal-lane follow-up](modal-lanes-20261019-v1.md) adds explicit traffic-class
selection, retained walking/cycling access checks, and walking/bicycle completion
probes. It resolves the recorded right-addition layout gap through class-selected
construction while retaining raw physical-index semantics.
The [new-lane service follow-up](new-lane-service-20261019-v1.md) reports compatible
boundary movements, preserves review through release/evidence validation, and
records a connected native example. It also repairs the tuple-versus-JSON-array
false alarm found in the real Hamburg evidence.
The [informational-gate follow-up](informational-gates-20261020-v1.md) runs the
remaining diagnostics without approving reviewed candidates. It repairs declared
lane-renumbering and internal-path segmentation false alarms and retains a
clean committed-code Hamburg repeat.
The [typed topology follow-up](typed-topology-20261020-v1.md) extends source
comparison to typed edits, retains malformed-path failure reports, and records
guarded local signal conversion. Its four-edit Hamburg repeat keeps the one
in-scope inherited finding under review.
The [direct-edit and proposal follow-up](maintenance-20261020-v1.md) removes
unchanged topology review from verified direct edits, distinguishes declared
lane removals, and fixes 31 duplicate-road proposals contradicted by the paired
published model. It retains the failed CI assertion and correction.
The [native duplicate follow-up](native-duplicates-20261020-v1.md) corrects the
intermediate repair's missed native positive. It compares actual internal paths
without generated identities and retains the zero-proposal MoST result.
The [generator-domain follow-up](generator-domains-20261020-v1.md) adds native
isolation and micro-node controls, preserves unknown reference absence, removes
repeated protection scans, and repairs the all-target merge construction path.
The [documented source-revision pair](source-curation-pair-20261020-v1.md)
recovers 17 prepared-road geometry changes and 1,417 unchanged registered roads
from a historical curation commit. Its labels remain separate from SUMO lanes
and field defects.
The [native import and calibration follow-up](source-curation-calibration-20261020-v1.md)
imports that pair with the same pinned recipe and creates 165 provisional lane
reference fragments through a reusable adapter. It records before-model
measurements and a separate reference self-control.
The [legacy funnel follow-up](legacy-funnel-20261020-v1.md) binds deletion and
merge scope to existing native plans. One planted isolated component is removed
on the 4,800-road MoST background while 171 inherited findings remain visible.
It also retains the mixed CI outcome and native-worker harness correction.
The [frozen source controls and spatial-edit check](source-curation-controls-20261020-v1.md)
adds 20 unchanged roads, repairs whole-road correspondence bias, and tests a real
XYZ road edit against its identity control. It retains incomplete junction
corrections, finding-overlap limits, and the failed native coordinate check.
The [neighbor-control follow-up](source-curation-neighbors-20261020-v1.md)
measures all 39 excluded neighbors and separates the one road inside the actual
edit scope. It traces three removed and two added finding memberships and
records both complete Windows suites with 3,699 tests passed in each run.
The [legacy local-conversion follow-up](legacy-local-conversion-20261020-v1.md)
uses local contexts for deletion and geometry merges, preserves absorbed turn
access and retains a bus-only interior counterexample.
The [merge-realization follow-up](legacy-merge-realization-20261020-v1.md)
rejects successful native commands that merge no declared roads, compares a
real Hamburg pair with full conversion, and requires critical native CI cases.
The [standalone-entry follow-up](legacy-standalone-realization-20261020-v1.md)
extends realization checks to the independent simplifier and records both
complete Windows suites for 1ad8919, including 38 executed critical native cases.
