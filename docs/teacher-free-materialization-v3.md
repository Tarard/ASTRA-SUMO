# Teacher-free experimental materialization v3

## Outcome

The v3 small-network path closes one end-to-end experimental loop without a
teacher network, a caller-provided OSM seed, reviewed scope, expected topology,
expected approach count, or expected movement count:

```text
frozen OSM bbox
  -> signal/physical-cell discovery
  -> independent movement variants
  -> mutually exclusive candidate DAG
  -> preregistered merge experiment contract
  -> generated PlainXML join patch
  -> independent source/candidate netconvert builds
  -> post-hoc DAG and TLS binding
  -> Connection Mode + independent conflict audit + exact outside diff
  -> SUMO load + every-movement routeability
  -> review overlay + rollback + hash-closed manifest
```

The preregistered `merge_physical_cell` arm is an experiment chosen independently
of controller membership. It is not a machine claim that merge is the real-world
topology. `field timing`, automatic topology selection, and automatic promotion
remain blocked in every output.

## Write-before-proof boundary

`intersection/materialization_experiment.py` evaluates every automatically
discovered vehicle cell before looking up SUMO binaries or writing any candidate
artifact. Candidate writing requires all of the following:

- exactly one discovered vehicle cell passes the frozen profile;
- the physical cell is standard T3/X4 and has no unresolved cell risk;
- the two independent movement methods occupy one semantic equivalence class;
- no unresolved restriction or lane-movement finding remains;
- exactly one `merge_physical_cell` node for that semantic class already exists
  in the candidate DAG;
- the DAG node itself is not blocked.

Pedestrian source records are inventoried as protected review dimensions. They
do not prevent an isolated falsification experiment, but they permanently block
promotion until pedestrian model, phase, and runtime closure exists. This matters
for XS1, whose frozen extract contains seven signalized-crossing records without
complete two-sided support paths.

Only the ready contract may generate `candidate-join.nod.xml`. A blocked or
not-applicable contract produces discovery, contract, summary, and manifest
evidence but no join patch, candidate network, or rollback claim.

## XS1 result

The frozen XS1 input remains SHA-256
`f46505aaaf2ae184420a782fb0e8d0a4f12c9434a788d9da836f3d0ab14325da`.
The machine selected OSM node `89129156` as the vehicle-graph medoid and inferred
the eleven-member physical cell before materialization.

| Gate | Verified result |
| --- | --- |
| netconvert source/candidate | pass/pass with SUMO 1.27.1 |
| TLS ownership rebuild | target changes from 10 TLS junctions/controllers and 32 controlled connections to one TLS junction/controller and 12 controlled movements; no old target TLS identity remains |
| post-hoc DAG binding | exact binding to `candidate-be5706e43609f5c0fb9d`; both OSM-turn-lane and geometry-continuity variants match all 12 movements |
| Connection Mode | 12 direct movements, 12 verified internal paths, 0 structural failures, 0 review findings; request/foes and TLS binding pass |
| independent conflict audit | 32 independently reconstructed conflict witnesses, 0 safety findings |
| outside exact diff | 0 outside-scope entity deltas and 0 outside-scope added findings |
| SUMO load | pass |
| every-movement routeability | 12/12 vehicles arrive; 4 right, 4 straight, 4 left; 0 collision, 0 teleport |
| source immutability | pass |
| rollback/manifest | present and hash-bound |

The resulting machine state is `REVIEW_PENDING` / `review_ready`, not accepted or
auto-certified. The generated controller is a netconvert experimental program,
not a NEMA or field-timing reconstruction.

netconvert reports the same OSM compound-type warnings for source and candidate
(`psv` and `cycleway.lane`). They are retained as tool evidence and are not
silently interpreted as candidate-specific regressions.

## XS2 negative control

XS2 is discovered from the bbox without the old caller seed. The machine selects
node `7009179663`, infers the six-member three-way cell, and preserves two
non-equivalent movement variants with 6 and 7 atomic movements. Nested turn
restrictions also remain unresolved.

The materialization contract therefore has no candidate plan and terminates at
`pre_materialization` with `movement_semantic_variants_disagree`. It writes no
join patch, source/candidate build, or rollback artifact. This is the intended
fail-closed result; the old hand-authored seven-movement TLS patch is not used.

## Held-out no-signal control

The frozen X4 fixture is replayed after removing its signal tag before discovery.
It yields zero anchors and zero cells. The workflow returns `not_applicable`
before binary lookup and writes no candidate artifact. This distinguishes
“nothing in the certified discovery domain” from a failed network build.

## Reproduction

Run the complete evidence and regression harness:

```powershell
.\.venv\Scripts\python.exe `
  plugins\torii-sumo\scripts\verify_teacher_free_materialization_v3.py
```

The harness runs focused teacher-free tests, materializes and verifies XS1,
proves the XS2 and held-out negative controls, then runs the complete pytest suite
and Ruff. It writes ignored runtime evidence under:

```text
outputs/teacher-free-materialization-v3/
  verification.json
  xs1/
    source.net.xml
    candidate.net.xml
    materialization-contract.json
    candidate-dag.json
    candidate-dag-binding.json
    netconvert-build.json
    exact-audit/
    sumo-load/
    all-movement-routeability/
    tls-ownership.json
    tls-policy.json
    review.add.xml
    review.html
    rollback.json
    manifest.json
  xs2/
    teacher-free-discovery.json
    materialization-contract.json
    summary.json
    manifest.json
```

For one arbitrary frozen bbox, use the production entry point:

```powershell
.\.venv\Scripts\python.exe `
  plugins\torii-sumo\scripts\run_teacher_free_materialization.py `
  --osm path\to\bbox.osm.xml `
  --output-dir outputs\one-v3-run `
  --traffic-side right
```

## Claim boundary

This milestone proves one teacher-free, preregistered small-network
materialization and its negative controls. It does not prove that merge is the
right topology at an unseen junction, that pedestrian semantics are complete,
that the generated TLS program matches reality, or that arbitrary OSM networks
can yet be cleaned automatically to expert NetEdit quality.
