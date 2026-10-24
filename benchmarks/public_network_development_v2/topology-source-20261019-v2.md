# Scoped topology and review outcomes, 2026-10-19

This follows the [first source comparison](topology-source-20261019-v1.md).
The [frozen record](topology-source-20261019-v2.json) distinguishes native
production, later assessment, tests, and observed CI states.

## Fresh full-model release

Commit `38c82ff` ran the full MoST `132981` contour release again. All ten
technical gates passed in 371.70 seconds. The outer report now states:

```json
{
  "status": "pass",
  "whole_network_review": {
    "status": "review_required",
    "inherited_finding_count": 170,
    "scope_finding_count": 0
  }
}
```

The source SHA-256 remains
`8acab7d4767bdda9d1523da0a398735b495a66cb853cb9724654b5dbbfbbac6b`.
The candidate remains
`5905bc2e541c5bfd95dfb7d38aadab2c365d73ddd98208ae24a2d767985cc685`.
Input and implementation hashes stayed unchanged during the run.
This is technical qualification of the declared edit. The source findings
remain unresolved.

## Independent review and repair

The source comparison now checks cluster structure as well as classifications
and risks. A source finding that contains an edited node requires review.
Source probes at `132844` and `151337` confirm this outcome. These probes are
read-only checks of the source findings, not new native edit trials.

A failed OSM corridor audit requires review even when no dense cluster exists.
It cannot qualify as a clean or inherited baseline. Unexecuted topology checks
expose `execution_status: not_run` in the outer review field.

Commit `b135e7a` separates unsupported pedestrian changes from confirmed defects.
A changed layout under a stable control requires review. A newly invalid
walking-area polygon remains blocked. Native tests retain both outcomes.
Independent speed, permission, structure, and safety checks still apply.

An empty shared diagnostic OD pool remains unchecked and requires review.
The report retains excluded roads, reasons, and source/destination counts.
This outcome cannot pass release. It does not establish a damaged network or
preserved routes. The repair does not add a scope-dependent routeability gate.

## Fixed three-case assessment

The final assessment uses `b135e7a` and the unchanged native artifacts from
the original three-case cohort. No failed case was replaced.

| Junction | Final applicability | Reason |
|---|---|---|
| `132844` | `review_required` | The native identity control recuts source geometry. |
| `132981` | `pass` | The stable control and candidate satisfy the technical profile. |
| `133544` | `review_required` | The pedestrian layout changes; automatic layout acceptance remains unsupported. |

All recorded ledger, source, candidate, and control hashes stayed unchanged.
The implementation also stayed unchanged during this read-only assessment.
Applicability alone does not replace the complete release checks.

## Verification and remaining work

The final related tests passed **77 tests**. Ruff and `git diff --check` passed.
An earlier test-code insertion error and its failed log remain recorded.
The corrected topology module passed all ten tests before publication.

The two `38c82ff` test-step logs each show **3517 passed, 24 skipped**.
Their observed workflow metadata still said `in_progress` when this record
was written. The logs are retained without claiming completed workflows.
The final `b135e7a` full suites remain pending in this snapshot:
[PR run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39279910178) and
[push run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39279904579).

Claude confirmed the preceding topology repair and is reviewing the final
follow-up under the subscription allowance. This record does not claim its
final review is complete.

## Local reproduction artifacts

Paths are relative to the repository. Generated runs remain local; the frozen
record retains their hashes.

- Full-model runner: `outputs/contour_release_20261019_v3/run-release.py`.
- Full-model ledger: `outputs/contour_preservation_20261019_v1/most-repeat/node-132981/ledger/corridor_edit_ledger.json`.
- Native release: `outputs/contour_release_20261019_v3/verification/edit-verification.json`.
- Final cohort runner: `outputs/contour_release_20261019_v3/rescore-three-cases.py`.
- Cohort results: `outputs/contour_release_20261019_v3/final-three-case-rescore/results.json`.
- Final related tests: `outputs/contour_release_20261019_v3/final-review-outcomes.log`.

The work does not establish field accuracy, dated field labels, automatic
cleaning precision, or traffic-performance improvement. Parking operations,
genuinely changed pedestrian layouts, and complete digital-twin operation
remain outside the verified capability recorded here.
