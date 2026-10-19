# Native contour identity controls

Implementation: `234545e07838194814ff90c0f9951c01a5b60a85`.
Runtime: repository Python environment and native SUMO 1.27.1.

## Reproduced false acceptance

The [frozen MoST extracts](../../tests/fixtures/most-contour-controls/README.md)
retain source lane coordinates and lengths from the prepared Monaco import.
Their terminal boundaries make them independently loadable.
The [provenance record](../../tests/fixtures/most-contour-controls/provenance.json)
binds both extracts to their parent source and upstream revision.

| Junction | Previous applicability | Native identity control |
|---|---|---|
| 132844 | Blocked by two pedestrian-preservation checks | Largest declared-length change: 1.00 m |
| 151337 | **Passed despite large vehicle-lane changes** | Largest declared-length change: 6.34 m |

The identity control declares the junction's existing polygon as a custom contour.
It does not request a new boundary. The observed changes therefore reveal native
reconstruction effects before the requested edit can be interpreted.
The 151337 regression failed under the previous implementation because it returned `pass`.
It now requires the source-contour control before automatic qualification.

These labels describe native model reconstruction. They do not identify field defects
or establish a historical survey date. The two extracts belong to the same MoST
development family. They are not independent city holdouts.

## Repair

`set_junction_shape` retains native reconstruction. Final candidate gates create
the identity control through the shared ledger and materializer. The requested
and control candidates use the same declared converter.

The applicability report separates:

- source-to-control reconstruction effects;
- source-to-candidate total changes;
- control-to-candidate requested changes.

Each comparison records XY and XYZ endpoint movement, XY shape displacement,
and saved logical-length changes. Missing lanes and unmeasured geometry remain
explicit. They are not zero-error observations.

The reconstruction screen uses 0.1 m, matching the existing connection-audit
endpoint tolerance. This is a technical stability screen, without field-accuracy
or traffic-performance authority. The source, control network, and materialization
record retain their hash bindings.

The final gate owns the applicability assessment. The verification wrapper uses
that result, so it does not run a second independent candidate assessment.
Explore mode retains native candidates and measured effects without this final
qualification. A real 151337 regression checks that explore still returns `measured`.

## Rejected boundary-only alternative

A diagnostic prototype changed only the saved polygon and `customShape` flag.
It passed all 14 earlier applicability checks. A native pedestrian probe also
retained positions and completion records. Those observations did not prove
that it implemented the original geometry-reconstruction operation.

SUMO stores [junction polygons and lane geometry separately](https://sumo.dlr.de/docs/Networks/SUMO_Road_Networks.html#plain-junctions).
Its [native editor](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/netedit/elements/network/GNEJunction.cpp#L1985)
marks connections deprecated after a junction-shape change.
Re-importing our boundary-only candidate moved the saved sidewalk by 2.045 m.
Its logical length changed from 14.28 to 16.055 m under that full-network recipe.
The candidate would defer the reconstruction effect until a later native pass.
The prototype was archived and removed from the working implementation before publication.

## Checks and retained records

All 125 related checks passed after the native-control repair. Ruff and
`git diff --check` passed. The checks include native control regressions,
ordinary edit profiles, ledger evidence, daily exploration, and repository navigation.
They also check that a requested candidate cannot substitute for the identity control.

- [Regression replay runner](../../outputs/contour_preservation_20261019_v1/replay-regressions.py).
- [Native replay records](../../outputs/contour_preservation_20261019_v1/regression-replay/results.json).
- [Pre-fix false-pass failure](../../outputs/contour_preservation_20261019_v1/native-control-pre-fix.log).
- [Related test log](../../outputs/contour_preservation_20261019_v1/native-identity-focused-v2.log).
- [Extract generator](../../outputs/contour_preservation_20261019_v1/extract-regressions.py).
- [Loadable extract records](../../outputs/contour_preservation_20261019_v1/regression-extracts-v2/results.json).
- [Boundary-only decision and native re-import](../../outputs/contour_preservation_20261019_v1/outline-only-decision.json).
- [Full MoST repeat runner](../../outputs/contour_preservation_20261019_v1/most-repeat/run.py).
- [Full MoST repeat artifacts](../../outputs/contour_preservation_20261019_v1/most-repeat/).

The earlier incomplete and instrumented runs remain intact. The full repeat uses
the original 42 proposals, lexicographic order, maximum ten inspections, and maximum
three materializations. No failed outcome is replaced with a more favorable case.

The repeat completed all three materializations under the original selection rule.
All returned `measured`. Source, reference, and implementation hashes stayed unchanged.

| Full-model junction | Identity control | Largest source-to-candidate length change | Largest control-to-candidate length change | Applicability at this revision |
|---|---|---:|---:|---|
| 132844 | Review: native source recut | 1.010 m | 0.037066 m | Blocked by pedestrian checks and source-control qualification |
| 132981 | Stable | 0.010 m | 0.000 m | Blocked by the existing source-relative pedestrian checks |
| 133544 | Stable | 0.010 m | 0.010 m | Blocked by the existing pedestrian checks |

At 132981, candidate and control pedestrian lanes and facilities are identical.
The source-relative checks still reject their small reconstruction differences.
This remaining false alarm needs a control-relative assessment.

Both complete Windows suites at this implementation passed **3,499 tests, with 24 skips each**:
[PR run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39258552948) and
[push run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39258545304).
The [machine-readable record](contour-identity-20261019-v1.json) retains the complete comparisons.

The current pedestrian-preservation checks remain in place. Automatic acceptance
of a genuinely changed pedestrian layout still needs further work. No contour
candidate in this record has promotion or field-correctness authority.
