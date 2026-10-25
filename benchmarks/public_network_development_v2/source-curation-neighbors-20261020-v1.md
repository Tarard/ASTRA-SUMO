# Neighbor controls and complete source-control CI

This follows the [frozen source controls](source-curation-controls-20261020-v1.md).
The [receipt](source-curation-neighbors-20261020-v1.json) retains every selected
neighbor, model measurement, finding-membership change, and input identity.

## What the far controls establish

The earlier 20 roads lie outside the selected edit's declared scope. Scoped
restoration preserves their geometry. Their zero metric changes verify
restoration and measurement consistency. They do not independently estimate
cleaning quality. The edited road shape also comes from the generated reference.
Its near-zero target error tests the actuator, not an independently proposed
correction. Neither result establishes field precision or automatic recall.

## All unchanged source neighbors

The new declaration keeps all 39 complete-source-state roads excluded from
the original far cohort. It selects none by their measured near-control values.
This declaration follows the earlier far and revised-road results. It is not
a prospectively held-out evaluation. All inputs and implementation hashes stay
unchanged during measurement.

The cohort produces 434 reference fragments. Four retain unknown lateral
offsets, and 430 have measurements. Native scope inspection shows that this
single road edit touches only source road 1847 among the 39 neighbors. The
other 38 remain outside its declared scope. Keep that distinction when using
the aggregate results below.

| Model | Lateral measured / fragments | Largest fragment mean (mm) | Covered lane-length weighted mean (mm) | Lane-length weighted coverage |
|---|---:|---:|---:|---:|
| before | 430/434 | 0.031794 | 0.000073 | 0.999202637 |
| source-shape-identity-control | 430/434 | 3.339447 | 0.004830 | 0.999202637 |
| revised-shape-partial-correction | 430/434 | 2.255900 | 0.004312 | 0.999202637 |

The identity control changes 4 numeric fact records.
The partial edit changes 4.
Changed source way IDs are 1847.
Neither candidate changes reference coverage. The nonzero neighbor offsets
describe local reconstruction effects. They are not field errors. Compare
the partial edit with its identity control, not with an assumed zero floor.

## Trace the topology count change

The original model has 171 dense findings; the revised model has 170.
Exact node membership preserves 168 findings, removes three old node sets,
and adds two new node sets. All five changed sets intersect registered
source-revision roads. Their node IDs are positive, without negative-editor
renumbering in these changed findings.

Two old sets expand from six to seven nodes and from three to five nodes.
The third old set has seven nodes and shares no nodes with any new finding.
It touches source roads 1645, 1701, 1745 and 4201. That is a model-relative
finding resolved after the source revision. It does not establish a field
defect, prove causality, or provide a general precision estimate. The full
node memberships and overlap candidates remain in the receipt.

## Complete CI

Both complete Windows suites for `09673be` pass **3,699 tests and skip 24**.
The [CI receipt](source-curation-controls-ci-20261020-v1.json) records both runs,
their full logs and JUnit hashes. JUnit confirms that all 20 native fatal-exit
cases and the new native XYZ road-shape case executed without skips or failures.
Contract and export checks also pass. Later evidence commits contain documents
only; their skip-CI setting does not extend the code result to different code.

## Reproduction locations and remaining work

- Selection, scoring and analysis: `outputs/measure_prepared_near_controls_20261020_v1.py`.
- Source models and frozen edit configurations: `outputs/prepared_near_controls_20261020_v1/selection.json` and `outputs/prepared_source_shape_edits_20261020_v2/`.
- Raw neighbor facts, measurements and summaries: `outputs/prepared_near_controls_20261020_v1/`.
- Finding trace runner: `outputs/trace_prepared_topology_changes_20261020_v1.py`.
- Finding memberships and input hashes: `outputs/prepared_topology_change_trace_20261020_v1/trace.json`.
- CI logs and JUnit: paths and hashes in `source-curation-controls-ci-20261020-v1.json`.

A complete source correction still needs junction-position and resegmentation
handling. An independent shape proposal, field labels, useful automatic recall,
legacy local conversion and merged-road OD correspondence remain open.
