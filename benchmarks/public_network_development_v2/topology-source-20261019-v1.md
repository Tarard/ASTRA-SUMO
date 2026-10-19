# Source-relative topology qualification, 2026-10-19

## Result

The full MoST contour edit at junction `132981` passes its technical release
checks. The source and candidate each retain 170 unresolved topology findings.
No finding was added, removed, or changed. The separate whole-network status
remains `review_required`.

The previous run took 411.85 seconds and stopped at topology. SUMO load,
routeability, edit applicability, and all other gates passed. The repeated run
took 405.82 seconds and passed all ten gates. Its candidate SHA-256 is
`5905bc2e541c5bfd95dfb7d38aadab2c365d73ddd98208ae24a2d767985cc685`,
identical to the previous candidate. This repair changes qualification, not
the candidate geometry.

Both runs use source SHA-256
`8acab7d4767bdda9d1523da0a398735b495a66cb853cb9724654b5dbbfbbac6b`.
The previous source audit also confirms zero added, removed, or changed
canonical cells. Input and implementation hashes remained unchanged during
each run.

## Change and checks

Commit `9924f6c` validates unresolved topology evidence as `review_required`.
Malformed or changed evidence remains `blocked`. Only a candidate that passes
its edit-specific applicability profile can use the source comparison.
Unscoped candidates still require whole-network review.

The comparison uses node membership, classifications, recorded risks, and
explicit absorption aliases. Equal finding counts alone do not qualify an edit.
The raw source and candidate audits remain in the final report.

The full release ran from a modified worktree based on `9beb019`. Its exact
producer code hashes are in the [record](topology-source-20261019-v1.json).
After stricter classification checks were added, a read-only comparison under
`9924f6c` confirmed all 170 findings as inherited. The native release artifacts
were unchanged. The final focused topology checks passed 110 tests.

## Review-outcome follow-up

Two source-derived native extracts check later reporting repairs:

| Extract | Outcome | Interpretation |
|---|---|---|
| `132981` | `review_required` | Applicability and SUMO load pass. No shared diagnostic OD pool remains, so routeability is `not_run`. |
| `132844` | `review_required` | The native identity control recuts the source. Dependent pedestrian geometry results remain recorded but cannot establish an edit defect. |

Neither extract receives automatic acceptance. Independent permission, speed,
structure, safety, and requested-geometry failures still block qualification.
The tests also change a lane speed under an unstable control and require a
blocked result.
The reporting repair is commit `6cdc45d`. Its final related checks passed
88 tests. Ruff and `git diff --check` passed for both repairs. Full CI for
the reporting repair was still running when this record was written.

## Reproduction artifacts

All paths below are relative to the repository. These generated runs remain
local. The frozen JSON retains their hashes and producer identities.

- Full-model runner: `outputs/contour_release_20261019_v2/run-release.py`.
- Final topology comparison: `outputs/contour_release_20261019_v2/confirm-topology.py`.
- Requested edit: `outputs/contour_preservation_20261019_v1/most-repeat/node-132981/ledger/corridor_edit_ledger.json`.
- Full release: `outputs/contour_release_20261019_v2/verification/edit-verification.json`.
- Native extract runner: `outputs/contour_release_20261019_v2/check-review-outcomes.py`.
- Extract results: `outputs/contour_release_20261019_v2/native-review-outcomes/results.json`.
- Tests: `tests/test_topology_source_regression.py`, `tests/test_corridor_edit_ledger.py`, `tests/test_junction_contour_control.py`, and `tests/test_routeability_audit.py`.

## Limits

This is one full-model case from the unchanged three-case cohort. The extracts
check reporting behavior. They are not independent field validation. Source
findings, unstable controls, and genuinely changed pedestrian layouts remain
unresolved. These results provide no automatic-cleaning precision estimate,
dated field labels, or traffic-performance claim.
