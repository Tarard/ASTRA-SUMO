# Prepared source revisions through native import and calibration

This extends the [registered source pair](source-curation-pair-20261020-v1.md).
Commit `e9e1957` supports its historical import options. Commit `ce71e87` adds
a reusable model reference fact adapter. The [receipt](source-curation-calibration-20261020-v1.json)
binds the exact inputs, generated networks, fact tables, selectors, and results.

## Controlled native imports

Both prepared road components use one frozen recipe from MoST revision
`e9585c408ebcb8c987b4573f0426487851685a8b`. The acquisition retrieves its
configuration, five declared type maps, and license notice. Sizes and Git blob
hashes are verified. SHA-256 identifies each saved artifact.

The original configuration expects the aggregated scenario input. Derived
configuration files explicitly replace that path with each prepared road
component. The shared parser ignores upstream output paths. Joining, signal
joining, and geometry removal are disabled to preserve source identities.
Coordinate normalization is disabled. Other declared settings remain active.
This is a current-engine source-component comparison, not historical scenario
or demand reproduction.

The first attempt stopped before native conversion because the parser did not
support `no-turnarounds`, `no-turnarounds.tls`, and `default.lanewidth`. The
installed converter supports all three. The shared allowlist now preserves
their literal values, including `no-turnarounds=false` and lane width 2.7 m.
Tests cover both full and unjoined recipe modes.

The corrected imports both pass, and both networks load in SUMO 1.27.1.
The original component produces 4,800 ordinary roads; the revised component
produces 4,795. Their declared coordinate frames match. Sources, historical
assets and implementation remain unchanged during the repeat. Loading does
not establish lane geometry, signal safety, or field correctness.

## Reusable provisional facts

`build_prepared_source_lane_facts` creates the two tables used by the existing
explore calibrator. It groups lane sections by source road, direction, allowed
vehicle classes, and lane order. Unique saved internal connections between
selected ordinary lanes contribute geometry. Input hashes and an explicit
selector correspondence bind the before table and reference self-control.

All mappings remain unresolved with confidence labels. Missing lane roles and
unmodeled reference roads remain unknown records. Invalid reference shapes also
remain unknown. Lane additions and reordering can invalidate permission-rank
association. The adapter does not infer confirmed field lane identities.

The 17 registered source road changes produce 165 reference lane fragments.
A changed source road contains many unchanged fragments. These are reference
measurements, not 165 defects. The nominal date is the source model revision
date. The field validity date remains unknown.

## Measured result

| Quantity | Original generated model | Reference self-control |
|---|---:|---:|
| Reference fragments | 165 | 165 |
| Fragments with lateral measurements | 163 | 165 |
| Fragments without longitudinal overlap | 2 | 0 |
| Largest fragment mean lateral offset | 14.647 m | About 0 m |
| Minimum reference coverage | 0 | 1 |

The two original fragments without longitudinal overlap retain zero coverage
and unknown lateral offsets. They do not receive a fabricated zero offset.
The reference self-control's largest fragment mean is about `2.59e-10` m,
which reflects numerical roundoff. Most original fragments also have near-zero
offsets because much of each source road stayed unchanged.

The self-control repeats the reference geometry into the evaluator. It checks
calculations and coordinate conversion. It does not measure reference quality,
automatic-cleaning precision, or traffic benefit.

The clean `ce71e87` repeat matches every numeric metric from the preceding
diagnostic prototype. All input and implementation checks pass. No network
edit or automatic source repair was applied by this fact-generation run.

## Verification and locations

The historical reference tests passed 40 cases. Calibration consumers passed
51 cases. The final factory/reference/calibration group passed 81 cases.
Counts overlap. A new negative test exposed a degenerate reference line, which
the factory now retains as unknown. Its failed test log remains available.
Ruff, repository navigation, and `git diff --check` passed.

Both preceding complete Windows suites for `cae92d7` passed 3,661 tests and
skipped 24. Full CI for the new adapter was running when this record was written.

- Native import runner: `outputs/import_source_revision_pair_20261020_v2.py`.
- Historical assets, derived configurations, input identities, native commands, networks and load reports: `outputs/source_revision_import_20261020_v2/`.
- Reusable adapter: `plugins/astra-sumo/src/astra_sumo/core/prepared_source_facts.py`.
- Clean repeat runner: `outputs/repeat_prepared_source_facts_20261020_v1.py`.
- Configuration and implementation identities: `outputs/prepared_source_facts_repeat_20261020_v1/declaration.json`.
- Fact tables and selector correspondence: `outputs/prepared_source_facts_repeat_20261020_v1/facts/`.
- Metrics, raw scorecards and comparison: `outputs/prepared_source_facts_repeat_20261020_v1/`.
- Initial prototype and results: `outputs/probe_source_revision_calibration_20261020_v1.py` and `outputs/source_revision_calibration_20261020_v1/`.
- Summary runner: `outputs/freeze_source_revision_calibration_20261020_v1.py`.
- Tests: `tests/test_prepared_source_facts.py` and `tests/test_public_reference_corpus.py`.

Field truth, independent-city generalization, useful automatic-cleaning recall,
legacy edit release scoping, and merged-road OD correspondence remain open.
