# MoST source-value and correspondence sensitivity

The [evidence record](source-sensitivity-20261018-v1.json) binds the implementation, native commands, inputs, labels, and comparisons.
The earlier label tables and native runs remain unchanged.

## Controlled imports

The corpus builder imports the same catalog-bound prepared OSM and type maps twice.
The alternate command changes normalization and its output path. All other import arguments stay identical.
Both imports retain disabled node joining, signal joining, and geometry removal.
The alternate network loads successfully under SUMO 1.27.1.

The control measures two different properties for each logical selector:

- Generated source value: node membership or direct lane-connection presence, traced through explicit identities.
- Reference registration: whether the existing exact-coordinate and complete-membership rules can judge the published-model target.

ID tracing applies only within this controlled same-input experiment.
Candidate-to-reference scoring keeps its existing exact rules. It receives no proximity tolerance or ID-only fallback.
The primary label table is not rewritten by the control.

## Measured result

The primary table retains 10,968 records, including 9,005 known model-relative targets and 1,963 unknowns.
The union with the alternate import contains 10,969 logical selectors.

| Generated source value | Selector count |
|---|---:|
| Unchanged | 10,968 |
| Changed | 1 |
| Unresolved | 0 |

The changed selector is connection `152526:2 -> 152527:2`.
It is absent from the normalized primary import and present in the global-coordinate alternate import.
It has no reference label and lies outside the primary table. The control retains it explicitly.
No known primary target has a changed generated node-membership or direct-connection value in this experiment.

| Exact reference registration | Selector count |
|---|---:|
| Both imports | 1,549 |
| Primary import only | 7,456 |
| Alternate import only | 10 |
| Neither import | 1,954 |

The 7,466 one-sided registrations are correspondence sensitivity. They are not generated source-value changes.
Their unknown outcomes remain unknown. The control does not replace them with guessed field labels.

The full native connection inventory also has 34 primary-only and 34 alternate-only ID tuples.
These include internal and pedestrian objects. Generated names and internal paths can differ.
The raw tuples remain available without interpreting them as 68 independent physical defects.

## Conditional comparison

The published-model self-check still scores 9,005 known records without a mismatch.
Both the self-check and unjoined-baseline comparison retain all 1,963 unknown records and report `review_required`.
Every primary case now carries its source-coordinate sensitivity result.
The comparison separately reports the changed selector outside its original table.

The regression includes a known reference case whose generated connection flips.
Its conditional model score remains available, and its sensitivity is `changed`.
Missing or damaged diagnostic evidence produces `not_measured` case values and an unavailable diagnostic result.
It does not create a passing stability claim or block the primary measurements.

All 42 focused corpus and navigation checks pass.
They include identity changes, registration-only changes, extra selectors, failed and malformed alternate imports, and damaged diagnostic evidence.

## Scope

This control covers generated node membership and direct connection presence.
An unchanged Boolean value does not establish unchanged geometry, permissions, signal programs, conflicts, demand, or driving behavior.
The earlier [21.7 m length difference](coordinate-frame-20261018-v1.md) remains relevant and unresolved.
The control adds no acceptance threshold or field-validity claim.
Historical source dates, field-defect labels, audit precision, and automatic-cleaning accuracy remain unestablished.

## Verified locations

- Builder, sensitivity and comparison: [`public_reference_corpus.py`](../../plugins/astra-sumo/src/astra_sumo/core/public_reference_corpus.py).
- Source configuration and identities: [`catalog.json`](catalog.json).
- Native and fault regressions: [`test_public_reference_corpus.py`](../../tests/test_public_reference_corpus.py).
- Raw corpus, alternate import and per-selector records: `outputs/source_sensitivity_20261018_v1/most-v1/`.
- Comparisons: `outputs/source_sensitivity_20261018_v1/self-comparison-v1.json` and `baseline-comparison-v1.json`.
- Measurement runner: `outputs/source_sensitivity_20261018_v1/measure-sensitivity-v1.py`.

Reproduce with the existing corpus command, the v2 catalog, an intact cache, and a new output directory.

## Diagnostic-evidence follow-up

The comparison also checks the alternate network's retained bytes and the declared reference identity before using sensitivity records.
Deleting that network leaves primary measurements available and marks sensitivity unavailable.
Nine focused evidence checks pass, overlapping the earlier run.
Fresh self and baseline comparisons retain the same 9,005 scored records and the separately retained unscored change.

## Record-kind and failure-scope follow-up

The report now separates source-state counts by record kind:

| Kind | Unchanged | Changed |
|---|---:|---:|
| Junction membership | 2,794 | 0 |
| Direct lane connection | 8,174 | 1 |

Both commands disable joining. Unchanged memberships mainly check retained identities and do not test joining sensitivity.
The connection result is unchanged only under this normalization toggle. It does not establish stability under other perturbations.

The diagnostic now retains unexpected tracing or label-format exceptions as unavailable observations.
Empty malformed dictionaries and lists are included in the failure regressions. They are not silently skipped.
Primary label measurements remain separate. Native global and normalized regressions explicitly require a completed diagnostic.
All 45 focused corpus and navigation checks pass. A fresh MoST run and two comparisons retain the measured counts above.
