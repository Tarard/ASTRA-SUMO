# Contour qualification follow-up

Implementation: `44434c36e89ef3d37bf70f6d828fe0338dfe56cd`.
This extends the [frozen first repeat](contour-identity-20261019-v1.md).

## Reuse of native results

The requested candidates and identity controls were produced by `234545e`.
The new assessment reads those unchanged files. It does not rebuild the network.
The rescore records input hashes and checks the consumer implementation afterward.
All three rescored records retained their inputs and implementation.

| Junction | Previous assessment | New assessment | Evidence |
|---|---|---|---|
| 132844 | Blocked | Blocked | Identity reconstruction changes source geometry substantially |
| 132981 | Blocked | Pass | Stable native control. Candidate pedestrian paths and facilities exactly match that control |
| 133544 | Blocked | Blocked | Requested pedestrian geometry differs from the stable control |

The 132981 correction removes a source-relative false alarm. It does not approve
a changed pedestrian layout. Boundary paths, widths, speeds, permissions,
outside state, signal programs, and independent safety findings retain their checks.
The control also checks for newly invalid walking areas relative to the source.

## Additional repairs

- Release preserves `review_required` when no gate has a definite failure.
  Unexecuted checks retain `execution_status=not_run` and cannot satisfy publication requirements.
- Explore reports source-to-candidate endpoint movement, shape displacement, and
  logical-length changes. This adds no native run or final qualification requirement.
- Control-construction failures remain review outcomes with their diagnostic records.
- The requested effect has its own screen, separate from source reconstruction.
  Road endpoint movement follows the measured polygon-displacement budget.
  Logical-length budgets allow two moving ends and the observed spatial grade.
  Boundary checks account for declared stop-line offsets and existing control distances.
- Numeric `outlineShape` formatting uses the shared semantic signature.
  Actual coordinate changes remain visible.

The additional [frozen requests](../../tests/fixtures/most-contour-controls/provenance-v2.json)
cover 132981 and 133196. The latter has a stable identity control but a requested
reconstruction that moves a sidewalk by more than 10 m. The endpoint budget and
boundary-consistency check identify that large effect independently of the existing
pedestrian-preservation checks.

## Checks and locations

All 287 related tests passed. Ruff and `git diff --check` passed.
The checks include unchanged native-control qualification, daily measurements,
review propagation, unavailable controls, stop offsets, spatial grades,
source-bound evidence, geometry preservation, and repository navigation.

Both complete Windows suites passed **3,505 tests, with 24 skips each**:
[PR run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39266541395) and
[push run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39266532319).
The [machine-readable record](contour-identity-20261019-v2.json) binds the code,
native producers, unchanged assessment inputs, and resulting classifications.

- [Rescore runner](../../outputs/contour_preservation_20261019_v1/rescore-full-repeat.py).
- [Rescore records](../../outputs/contour_preservation_20261019_v1/full-repeat-rescore-v2/results.json).
- [Three pre-fix failures](../../outputs/contour_preservation_20261019_v1/followup-pre-fix.log).
- [Focused test log](../../outputs/contour_preservation_20261019_v1/followup-focused-v2.log).
- [Walking-area diagnosis](../../outputs/contour_preservation_20261019_v1/pedestrian-area-diagnosis.json).
- [Additional extract generator](../../outputs/contour_preservation_20261019_v1/extract-additional-regressions.py).
- [Native input, candidate, and control artifacts](../../outputs/contour_preservation_20261019_v1/most-repeat/).

These are technical development results from the same MoST family. Field validity,
independent city evaluation, and automatic acceptance of genuinely changed pedestrian
layouts remain incomplete. No candidate in this record was promoted or merged.
