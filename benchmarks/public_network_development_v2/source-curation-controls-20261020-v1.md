# Frozen source controls and a spatial road-edit check

This follows the [source-revision calibration](source-curation-calibration-20261020-v1.md).
The [receipt](source-curation-controls-20261020-v1.json) binds the source selection,
measurements, native edits, failed checks, and implementation versions.
The source pair remains one MoST family and a generated-model reference.
Field precision, field recall, and a complete source correction remain unknown.

## Frozen source controls

Registration found 1,434 source roads with unique, unchanged external provenance.
Seventeen coordinate sequences changed. Of 1,417 coordinate-unchanged roads,
1,416 also retained node references and tags. Way 3472 changed only node references.
Twenty-three source correspondences remain unresolved.

The selection excluded 39 unchanged roads because they shared source nodes or
native junctions with revised roads, or lacked a native representation.
It sampled 20 of the remaining 1,377 roads with seed 17 before scoring.
All selected roads stayed in the cohort. They produced 134 lane fragments.
Their original coverage was 100%. Their largest fragment mean lateral offset
was about 4.6e-10 m. That is measurement roundoff, not field accuracy.

The report retains control roles. A label never forces a measured offset to
zero. Transit-only lanes now contribute to motor-vehicle summaries.

## Correspondence repair

The original whole-road selection could compare a reference fragment with a
different interval of the same road. A local diagnostic changed the largest
fragment mean from 14.647098 m to 6.074611 m. Its coverage also changed, from
0.877358 to 0.839623. These are different associations, not a physical improvement.

Stable native edge and endpoint IDs now bind each eligible source interval.
Unbound intervals retain their whole-road measurement with low confidence.
Missing roles remain unknown. The revised evaluator uses the same 17 revisions,
20 frozen controls, ordered fact IDs, and expected reference geometry.
It changes 17 before-model fact measurements.

| Generated model | Lateral measured / fragments | Largest fragment mean (m) | Covered lane-length weighted mean (m) | Lane-length weighted coverage |
|---|---:|---:|---:|---:|
| Original model | 161/165 | 7.204911 | 0.356810 | 0.989213 |
| Original-shape identity control | 161/165 | 7.204911 | 0.356869 | 0.989213 |
| Revised-shape partial edit | 161/165 | 7.204911 | 0.346034 | 0.989598 |
| Reference self-control | 165/165 | 0.000000 | 0.000000 | 1.000000 |

Lateral weighting uses covered reference lane length. Coverage uses total
reference lane length. Uncovered intervals contribute zero coverage and no
invented lateral value. Fragments and reference lane length can count several
lanes on one road. They are not independent road-defect observations.

## Actual spatial road edit

The source pair contains 71 native sections on its 17 revised roads.
Thirty-five sections move native junctions. Seven also change endpoint IDs.
Only 36 meet the shape-only preconditions, and their anchored source intervals
did not move. A complete correction therefore needs a junction-position edit
and, for resegmented intervals, source-path handling. An outline edit cannot
move a junction.

Both probe variants use road centerlines from permission-preserving PlainXML
exports. They do not use an offset lane shape as a road centerline.
The target, `3795#6`, had the largest original fragment offset. This is a
selected diagnostic case, not a representative improvement sample.

The first native trial rejected its XYZ coordinates. The road-shape contract
now accepts consistently dimensioned finite XY or XYZ points. A native test
then exposed SUMO's omission of zero heights. The final check treats those
heights as zero and still rejects lost nonzero elevation. Calibration distances
remain planar. The failed trial and native test artifacts remain available.

Both final edits applied their requested XYZ shape, passed native load and
source/candidate route checks, and preserved geometry outside the declared
scope. All recorded inputs stayed unchanged. These are technical checks.
The revised road shape leaves the junction nodes at their original positions.
It is a partial correction, not the known answer for the complete revision.

For target fragment `source-revision:3795:forward:3795#6_0`, the interval-bound mean lateral offset was
6.074611 m before editing, 6.074611 m after the identity control, and about
4.4e-9 m after the partial edit. Coverage changed from 0.839623 to 1.0.
The largest mean across all revised fragments stayed about 7.205 m because
other source intervals and the reverse direction remain uncorrected.
The new baseline retains four fragments without a lateral measurement.

The identity conversion changed 16 per-fact numeric records inside its scope.
The partial edit also changed 16 records. Exact numeric changes include small
reconstruction and rounding effects, so compare the partial edit with its
identity control as well as the original source.

Control metric changes were 0
for the identity edit and 0
for the partial edit. Full per-fact differences remain in the receipt.

## Finding coverage does not establish precision

The original model produced 171 topology findings. Their endpoint membership
overlapped 12 of 17 revised roads and 9 of 20 unchanged controls. The revised
model produced 170 findings, overlapping 10 revised roads and the same 9 controls.
Both models produced zero conservative automatic-edit proposals.

An unchanged source road is not a confirmed clean road. A curator geometry
revision is not a confirmed topology defect. These counts show detector reach
and inherited review burden. They do not establish true positives, false
positives, field recall, or automatic-cleaning precision.

## Verification and locations

Source registration, facts, calibration consumers, and navigation passed 55
tests. The first spatial consumer run had 117 passes and one native failure.
After its correction, nine focused cases passed, including native elevation
and existing planar edits. The interval repair passed 57 cases. Counts overlap.
The receipt retains the current CI snapshot for `09673be` separately from local
test results. A running check does not establish success.

- Source registration and facts: `plugins/astra-sumo/src/astra_sumo/core/prepared_source_registration.py` and `prepared_source_facts.py`.
- Frozen selection runner: `outputs/measure_prepared_source_controls_20261020_v1.py`.
- Source motion audit: `outputs/audit_prepared_source_correspondence_20261020_v1.py`.
- Native edit runner: `outputs/probe_prepared_source_shape_edits_20261020_v2.py`.
- Re-score and analysis: `outputs/score_prepared_source_shape_edits_20261020_v1.py`.
- Finding audit: `outputs/check_prepared_revision_finding_coverage_20261020_v1.py`.
- Configurations, metrics, and raw results: `outputs/prepared_source_controls_20261020_v1/`, `outputs/prepared_source_shape_edits_20261020_v2/`, `outputs/prepared_source_shape_scores_20261020_v1/`, and `outputs/prepared_revision_finding_coverage_20261020_v1/`.
- Failed native test artifacts: `outputs/spatial_road_edit_failure_20261020_v1/`.
- Independent subscription-only review: `outputs/claude-review-53478d6-20261020.txt`.

Near controls, complete junction movement, legacy local conversion, merged-road
OD correspondence, independent-city validation, and field labels remain open.
