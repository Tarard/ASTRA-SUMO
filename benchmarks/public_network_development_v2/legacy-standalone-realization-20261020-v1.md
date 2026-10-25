# Standalone realization check and complete legacy-edit CI

The [merge-realization follow-up](legacy-merge-realization-20261020-v1.md)
passed both complete Windows suites at `1ad8919`: 3,719 tests passed and 24
were skipped in each run. The [CI receipt](legacy-merge-realization-ci-20261020-v1.json)
binds the full logs and JUnit files. All 38 critical native cases executed
without skips or failures. The explicit native-execution job step also passed.

## Follow the second caller

The independent corridor-simplification entry still returned success when its
planned nodes survived. It also read the connection audit's controlled counts
without checking its merged-road access result. Commit `84c7e6a` requires both
actual planned-node removal and complete source-path aliases, and honors the
full connection-audit result.

Three focused controls cover unchanged output, a genuine merged source path,
and changed vehicle access. The unchanged output and permission drift fail.
The valid merge passes. Nine simplification tests and 26 simplification,
correspondence and navigation consumers pass. Counts overlap.

A separate native four-road control removes the planned micro node, reports
the requested merge as realized, and loads in SUMO 1.27.1. Its source file
remains unchanged. The standalone entry still uses whole-network conversion.
It does not yet provide the localized ledger's exterior-preservation guarantee.
The two original source and candidate location records remain in the raw result.

- Native runner: `outputs/probe_standalone_merge_realization_20261020_v1.py`.
- Native configuration, metrics and raw output: `outputs/standalone_merge_realization_20261020_v1/`.
- Focused unit log: `outputs/standalone-merge-realization-20261020-v1.log`.
- Consumer log: `outputs/standalone-merge-consumers-20261020-v1.log`.

The complete 1ad8919 result does not qualify different code. Full CI for
84c7e6a is recorded separately when complete. Legacy additive and standalone
full-network conversion, merged-road OD correspondence, inherited source-scope
qualification and complete source-curation correction remain open. Field
precision and independent automatic shape correction remain unverified.
