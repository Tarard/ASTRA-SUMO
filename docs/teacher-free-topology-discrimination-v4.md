# Teacher-free topology discrimination v4

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: plan + run
- Origin Date: 2026-07-16
- Verification Status: VERIFIED (machine-side, review-only)
- Version Label: topology_discrimination_v4

## Experiment overview

- **Title:** TTD-1 — Teacher-free small-cell topology discrimination
- **Objective:** determine whether the same automatically discovered OSM signal cell can be represented by a preserved split/shared controller (H_S), a merged physical cell (H_M), or a topology-preserving partial TLS/connection rebuild (H_P), without using a teacher network, reviewed scope, expected answer, or field timing.
- **Research question:** can independent topology evidence plus identical structural, semantic, safety, differential, and runtime gates falsify unsafe topology hypotheses before human review?
- **Type:** deterministic SUMO/netconvert simulation experiment.

This experiment does not attempt to infer a real-world winner automatically. It compares immutable hypotheses. A machine recommendation is review guidance, never topology truth or promotion authority.

## Hypotheses

### H_S — Preserve split physical nodes and share one controller

Operation:

- retain every discovered physical node and edge;
- assign all target vehicle signal nodes one deterministic shared `tl` identity;
- let netconvert regenerate the shared program and link indices;
- do not join junctions.

Positive evidence includes multiple conflict centers, multiple stop lines, an intermediate storage segment, or a paired/offset layout. Absence of those facts is not proof against H_S; it leaves the hypothesis review-only.

Hard falsifiers include fewer than two target signal owners, incomplete controller-owner closure, a target signal node outside the declared physical cell, or any outside-scope semantic delta.

### H_M — Merge the discovered physical cell

Operation:

- write one PlainXML `<join>` over the automatically discovered source junction members;
- give the joined cell one deterministic junction and controller identity;
- let netconvert regenerate internal lanes, request/foes, connections, and TLS bindings.

Minimum applicability evidence:

- exactly one vehicle-graph branch/conflict-center candidate;
- signal anchors are inline degree-two signal-head candidates rather than independent branch junctions;
- no independently evidenced connector storage between multiple conflict centers;
- no rail, bridge/tunnel/layer separation, or protected access that would be destroyed by the join.

Hard falsifiers include multiple branch/conflict centers, a storage-bearing inter-junction connector, grade separation, rail ownership, boundary-port movement, or any need to enlarge the discovered scope.

### H_P — Preserve nodes and rebuild only local control ownership

Operation:

- retain all discovered junction and edge identities;
- demote inline target signal-head artifacts to non-TLS junctions;
- promote the unique machine-derived branch/conflict center to one deterministic TLS owner;
- let netconvert regenerate only the resulting local connection/TLS semantics.

Minimum applicability evidence:

- exactly one branch/conflict-center candidate;
- at least two inline signal anchors;
- the conflict center is not already the sole correct TLS owner;
- no unresolved movement semantics or nested restriction.

Hard falsifiers include an ambiguous/multiple conflict center, a signalized branch that must remain independently controlled, or any loss of declared boundary-to-boundary movements.

## Variables and controls

- **Independent variable:** topology hypothesis (`H_S`, `H_M`, `H_P`).
- **Controlled inputs:** frozen OSM bytes, automatically discovered cell, boundary ports, movement semantic class, traffic side, SUMO binaries, netconvert options, toolchain lock, tolerances, and runtime schedule.
- **Primary dependent variables:** hard-gate vector, exact stable movement binding, outside-scope exact semantic delta, independent conflict findings, and all-movement route completion.
- **Secondary dependent variables:** physical TLS-junction count, controller count, controlled local-link count, candidate edit size, and review burden.
- **Potential confounds:** global netconvert regeneration, unstable internal IDs, controller membership mistaken for physical topology, routeability hiding lane errors, and request/foes sharing assumptions with the generator.

Controls against confounds:

- stable semantic IDs rather than internal edge IDs;
- one source build and the same OSM import options for every variant;
- target/guard scope derived from the source network, never supplied manually;
- exact outside-scope entity diff;
- code-native Connection Mode and an independently constructed conflict graph;
- boundary-to-boundary lane-path binding before SUMO runtime;
- source SHA-256 checked before and after every variant.

## Frozen applicability contract

The experiment may write candidates only when exactly one automatically discovered vehicle cell satisfies all base gates:

1. right-hand traffic in v4's initial certified domain;
2. standard three- or four-approach movement domain;
3. exactly one movement semantic equivalence class;
4. exact agreement between the independent OSM-turn-lane and geometry-continuity movement variants;
5. no unresolved movement semantics or nested turn restriction;
6. one matching candidate-DAG node for each topology hypothesis;
7. all source signal anchors and boundary ports are inside the hash-bound discovery artifact.

Each topology arm is then independently marked `ready`, `blocked`, or `not_applicable`. One blocked arm must not prevent another ready arm from being materialized. XS2 remains globally blocked before any candidate write because its 6/7 movement variants disagree.

## Common build and audit pipeline

Each ready arm executes:

1. write an immutable, topology-specific PlainXML node patch;
2. run netconvert from the same frozen OSM and common options;
3. inventory expected retained/removed node and controller identities;
4. bind every expected stable movement to a concrete boundary-to-boundary lane path;
5. reject missing, duplicate, unexpected, ambiguous, or wrong-controller paths;
6. run full target Connection Mode audit;
7. run independent movement-conflict audit;
8. run stable exact source/candidate diff with zero outside-scope tolerance;
9. run SUMO load;
10. run one collision-aware vehicle for every bound movement path;
11. emit rollback, review overlay, blind comparison payload, and artifact manifest.

All hard gates are conjunctive. No score, teacher similarity, or routeability result may compensate for a failed gate.

## Decision rule

- **No feasible arm:** reject the current physical-cell hypothesis; do not enlarge scope.
- **One feasible arm:** output `suggest` for human review; automatic selection remains false.
- **Two or three feasible arms:** output `blind_review_required`; hide arm names in the reviewer-facing package.
- **Any unresolved protected pedestrian, bicycle, rail, grade, or field-control fact:** promotion remains blocked even when the machine comparison is otherwise review-ready.

## Negative control

A frozen offset/paired-intersection OSM fixture must contain multiple nearby signal anchors and at least two branch/conflict centers separated by a storage-capable connector. Required outcome:

- H_M is blocked before its join patch is written;
- the blocker names the stable multiple-center/storage evidence;
- the fixture cannot pass by increasing a distance threshold or widening scope;
- signal count alone never appears as merge authorization.

## Verified execution result

The authoritative local verification completed on 2026-07-16 with SUMO
1.27.1. It ran the same hash-bound workflow twice for XS1, checked every
manifest hash, executed 1,341 repository tests, and ran Ruff over source,
scripts, and tests.

- **XS1:** all three preregistered arms were independently materialized. H_M
  and H_P passed the common ten-gate vector, including exact 12-movement
  binding, Connection Mode, independent conflict safety, zero outside-scope
  exact delta, SUMO load, and 12/12 collision-free route completion. H_S also
  loaded and completed 12/12 routes, but its independent conflict result was
  `review`; it was therefore blocked. H_M and H_P remain two anonymous
  alternatives in the blind-review package—Torii did not choose between them.
- **XS2:** the 6-versus-7 movement-semantic disagreement stopped the workflow
  at `pre_materialization`; no source network or candidate variant was written.
- **Paired/offset negative:** four signal anchors surround two conflict centers
  separated by a storage-capable connector. H_M and H_P were falsified before
  candidate writing. H_S alone was materialized and passed every common gate,
  including 12/12 route completion. This is direct executable evidence that
  signal-head count is not merge authority.
- **Determinism:** the clean XS1 rerun reproduced discovery, contract, topology
  evidence, DAG, candidate-plan IDs, normalized candidate hashes, bindings,
  gate vectors, and machine decision exactly.

The machine result is recorded in
`outputs/teacher-free-topology-v4/verification.json`. Reproduce it with:

```powershell
python plugins/torii-sumo/scripts/verify_teacher_free_topology_v4.py
```

The exact semantic-diff component may report that its own regression gate
passes. That component status is only one input to v4; the enclosing variant,
workflow, manifest, blind package, and verification result all keep automatic
topology selection and promotion explicitly blocked.

## Expected outputs

For the experiment root:

- discovery and topology-evidence JSON;
- v4 preregistered contract;
- candidate DAG and comparison ledger;
- one subdirectory per ready hypothesis;
- blind review JSON/HTML and display-only `additional.xml`;
- comparison summary, rollback index, and manifest closure;
- verification report covering XS1, XS2, and the paired/offset negative control.

For each materialized arm:

- node patch;
- candidate `.net.xml`;
- netconvert command/result;
- TLS ownership report;
- stable cell-movement binding;
- Connection Mode report;
- independent conflict report;
- exact semantic diff;
- SUMO load and all-movement routeability reports;
- rollback and manifest.

## Success criteria

Machine-side v4 succeeds only if:

1. XS1 writes all three independently declared arms or records a specific implementation-independent falsifier for any omitted arm;
2. every materialized arm is hash-bound to one DAG node and one topology operation;
3. every feasible arm has zero outside-scope structural/semantic delta, zero safety failure, complete stable movement coverage, SUMO load pass, and all vehicles arrived without collisions or teleports;
4. XS2 writes no candidate artifacts and preserves the 6/7 disagreement witness;
5. the paired/offset negative blocks H_M before candidate writing for multiple-center/storage evidence;
6. reruns produce identical contract, evidence, candidate semantic hashes, and verdicts;
7. automatic topology selection, field timing, and promotion remain blocked.

## Failure and stop conditions

Stop the current method rather than add a site-specific branch if any of the following occurs:

- a variant changes an outside-scope stable entity;
- H_M needs a larger scope or a moved boundary port;
- H_P cannot identify one conflict center without a benchmark answer;
- H_S requires copying a controller program from a teacher;
- movement binding relies on raw internal IDs or connection indices;
- a small coordinate perturbation changes the topology verdict;
- request/foes passes while the independent conflict graph fails;
- the paired/offset negative can be merged merely by changing a distance constant;
- code needs an Ingolstadt junction ID or site-specific constant.

## Claim boundary

Passing TTD-1 means Torii can construct and compare three small-cell topology hypotheses under identical machine gates. It does not prove which hypothesis matches the real road, certify signal timing, complete Stage 1 human validation, or establish arbitrary-city expert-quality cleaning.
