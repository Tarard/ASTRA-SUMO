# Torii-SUMO Architecture

## Main Claim

Torii-SUMO turns raw OSM-to-SUMO construction into a bounded,
evidence-aware, reference-comparable workflow. It does not certify that an
imported network is correct, and it does not claim arbitrary-city TUM-level
cleanup from one prompt.

The strongest current product claim is:

```text
Torii builds auditable SUMO network artifacts from OSM, records construction
and review evidence, compares against a cleaned reference when one is supplied,
and keeps promotion blocked until the relevant gates pass.
```

## Implemented Corridor Contract

The first acceptance boundary is corridor-scale human-modeling work, not a
city-scale automatic-cleanup claim.  An accepted edit is materialized into a
new candidate network and may be promoted only through this persisted chain:

```text
source network + accepted edit ledger
-> reversible plain-XML plan
-> netconvert materialization report
-> source/candidate path and SHA-256 identity
-> candidate-bound map-review JSON
-> display-only review additional.xml + review HTML
-> exact protected-semantic/TLS review when required
-> SUMO load + routeability + topology + modal-connectivity evidence
-> candidate-gate manifest
```

The public MCP gate accepts persisted materialization and review files.  It
does not accept caller-provided numeric allowances that can silently bypass a
protected semantic or TLS change.  Routeability and topology reports are bound
to the exact candidate path and hash, and stale command outputs are removed
before execution.

The three decision tiers are explicit:

1. **Automatic-safe**: the candidate has no protected semantic/TLS delta and
   all runtime gates pass.
2. **Review-required**: review locations are emitted as SUMO
   `additional.xml`; promotion additionally requires an exact, candidate-hash-
   bound decision with structured observed facts, reviewer, timestamp,
   evidence, rationale, and rollback. Map review is a hard requirement only
   when the edit explicitly declares it.
3. **Blocked**: missing/invalid evidence, an unreviewed protected delta, stale
   outputs, source/candidate identity, or a failed runtime gate prevents
promotion and is recorded in a manifest.

The review overlay is not an execution or truth layer. It is restricted to
`additional`, `poi`, `poly`, and `param`; rerouters, detectors, calibrators,
and other runtime-affecting elements fail the materialization contract. Deleted
or merged geometry is rendered from the source network, added geometry from
the candidate. The JSON evidence, decision, HTML rendering, overlay, and
candidate are bound by SHA-256 in the manifests.

The reproducible four-scenario acceptance runner is
`plugins/torii-sumo/scripts/run_corridor_contract_regression.py`.  It rebuilds
its source networks from tracked OSM input, the installed SUMO pedestrian
tutorial, and a sentence-generated five-way scene; it does not depend on old
ignored `outputs/` artifacts.

## Ingolstadt Teacher-Corridor Slice

The first same-bbox human-modeling experiment is intentionally one physical
intersection rather than the whole city. Its executable chain is:

```text
current same-bbox OSM
-> raw reference-visual-detail netconvert build
-> classify explicit, implicit railway, stale-safe, and unsafe TLS references
-> byte-minimal reversible candidate for stale-safe references only
-> source/candidate semantic and SHA-256 preservation checks
-> SUMO load + completion-aware routeability
-> current OSM facts + teacher/candidate physical-cell comparison
-> review additional.xml + HTML + decision template + manifest
```

`core/tls_reference_cleanup.py` treats `rail_signal` and `rail_crossing` as
SUMO implicit controller types rather than missing `tlLogic` errors. Automatic
cleanup is currently limited to a second-stage, pedestrian-only internal
connection at a `priority` junction whose stored state is already uncontrolled
(`M` or `m`). Any road TLS, controlled state, non-pedestrian movement, missing
owner, or mixed safe/unsafe set blocks the whole repair; partial cleanup is
forbidden.

The real junction `267517510` slice passes construction and runtime gates after
one such bounded structural repair. It does not pass teacher equivalence:
current OSM and the teacher agree on traffic-light control, while pedestrian
internal functions and movement signatures still differ. Those differences
remain review items and are not auto-replayed from the teacher.

## Code-Native Connection Mode Boundary

`core/connection_mode_audit.py` reconstructs the lane-level graph represented
by NetEdit Connection Mode. It is a default OSM-workflow gate and is also
exposed as `sumo_network_connection_mode_audit`:

```text
effective cleaned .net.xml
-> direct fromLane/toLane/via bindings
-> complete internal-lane path and endpoint continuity
-> request ordering, response/foes dimensions, lane order, rank, and coverage
-> current TLS controller/linkIndex/state-string binding checks
-> pass | review_required | fail
-> JSON + display-only additional.xml + source-hash-bound manifest
```

Candidate mutation uses a second boundary, exposed as
`sumo_network_connection_mode_regression_audit` and invoked automatically by
the teacher-guided repair queue:

```text
source .net.xml + candidate .net.xml + declared source/candidate edit IDs
-> independent Connection Mode reconstruction
-> coupled edit-scope closure over touched TLS-controller members
-> per-junction structural/review category deltas
-> block new target findings, every outside-scope regression, and unexplained
   outside-scope junction identity change
-> differential JSON + display-only additional.xml + two-hash manifest
```

The comparison uses category counts rather than internal connection indices,
which netconvert may legitimately renumber. New target-scope review findings
still block automatic promotion; they are not hidden merely because the target
was intentionally edited. A candidate file is diagnostically compared even if
an earlier semantic/parity gate has failed.

Structural XML/path/request/link-index failures are hard failures. Legal but
ambiguous fanouts, merges, lane drops/additions, and protected-green foe pairs
are review findings; they block automatic promotion but do not authorize a
repair. SUMO runtime `rail_signal` controllers are recorded separately because
they need not have ordinary road `tlLogic` programs. NetEdit is optional for
flagged visual review and is not part of the automatic proof. Routeability and
SUMO load remain separate gates because an edge-level route can succeed while
one lane has no connection or a TLS signal group is bound incorrectly.

The current Ingolstadt same-bbox run covers 2,274 junctions and 13,169 direct
movements. All 13,169 internal paths are structurally valid; 2,164 junctions
pass, 110 require evidence review, and zero fail structurally. The review queue
includes 27 incoming motor lanes without a junction connection, 19 outgoing
motor lanes without a source connection, and one protected-green foe pair.
Twelve implicit rail controllers are classified as SUMO runtime rail signals.

The Südliche Ringstraße experiment demonstrated why this boundary is required.
A plain-XML round trip re-applied `sidewalkWidth`/`bikeLaneWidth` from the type
file and produced 1,828 outside-scope review regressions by shifting lane
indices. Round-trip type sanitization eliminated that global class of error.
The current safe-default candidate has zero outside-scope regressions or
junction identity changes, but it remains blocked on 13 new target structural
findings and 27 target review findings. The replay now retains unmapped OSM
boundary roads, refuses to copy unmapped teacher roads, and preserves mapped
OSM remote endpoints. A shared TLS controller can be explored as a separate
bounded candidate, but controller membership alone is not accepted as proof
that physical junctions should be joined.

## Standard NEMA Phase-Binding Boundary

`core/standard_nema_binding.py` adds a fail-closed signal-group stage after an
OSM cleanup has selected its effective network:

```text
effective cleaned .net.xml
-> classify physical owner, controller scope, modes, arms, and movement geometry
-> Connection Mode audit: fromLane/toLane/via chain + lane order + request/foes
-> scan-only eligibility queue + connection JSON + display-only additional.xml
-> select one eligible three/four-way junction
-> canonical NEMA movement phases + phase-minus-one linkIndex groups
-> byte-minimal separate candidate + rollback plan
-> netconvert round trip -> SUMO load -> completion-aware routeability
   with junction-collision checking
-> pending human decision + HTML + artifact manifest
```

The stage never batch-promotes a controller. Four-way ownership is cross-ring:
one main approach uses left/through-right phases 5/2 and its opposite uses 1/6;
the minor approaches use 7/4 and 3/8. Three-way controllers use missing-phase
`0` placeholders and a repeated phase 4 across the empty ring side. The
Connection Mode gate traces every direct movement through all internal lanes,
checks right-hand lane ordering and lane-rank continuity, validates the complete
request/foes matrix, and rejects a canonical NEMA concurrent pair when SUMO
marks its movements as foes. Joined controllers, pedestrian/internal or rail
control, bicycle-only movements, turnarounds, `linkIndex2`, ambiguous geometry,
incomplete movement matrices, and shared protected-left lanes also block
automatic materialization. Generic NEMA timings remain review-only even when
all runtime gates pass; the system does not infer field timing from OSM.

The OSM workflow invokes this boundary in scan-only mode and exposes the queue,
review overlay, HTML, and counts in its final report. The public MCP tool can
then materialize one explicitly selected eligible candidate. This keeps the
legacy workflow from silently accumulating another mutation stage while still
making signal-phase review part of every cleanup run.

On the current Ingolstadt same-bbox effective TLS-cleaned network, all 249 TLS
owners remain outside automatic NEMA promotion. All 1,617 request-bound paths
are structurally valid: Connection Mode proof passes for 221 owners, marks 28
`review_required`, and reports zero structural failures. Among the 24 three-way
and 4 four-way geometric layouts it passes 15 and marks 13 for evidence review.
The unified overlay keeps those 28 connection-review locations separate from
221 other NEMA-scope blockers. These are review classifications, not assertions
that every flagged connection is objectively wrong.

## Layer 1: Router

The router classifies user intent, identifies missing input, chooses a workflow
recipe, and keeps autonomy mode and claim boundary visible.

Current anchors:

- `plugins/torii-sumo/src/torii_sumo/core/workflow_router.py`
- `plugins/torii-sumo/src/torii_sumo/tools/workflow_tools.py`

The router should not build networks, repair junctions, or decide whether a
repair is promoted.

## Layer 2: Planner

The planner chooses the network profile, traffic layers, road scope, reference
policy, and validation gates.

Current anchors:

- `plugins/torii-sumo/src/torii_sumo/core/network_plan.py`
- reference policy and hierarchy helpers under
  `plugins/torii-sumo/src/torii_sumo/core/`

For reference-matched workflows, the planner must keep `vehicle_core` and
`reference_visual_detail` separate. Passenger routeability claims belong to
`vehicle_core`; NetEdit and reference-topology comparisons belong to
`reference_visual_detail`.

## Layer 3: Executor

The executor runs bounded stages that transform or audit artifacts. Each stage
should eventually accept a `WorkflowState` and return a `StageResult`.

Current state: `WorkflowState` and most `StageResult` values are reporting
adapters built after the legacy OSM workflow has run.  They are not yet the
authoritative execution state.  New work must not add more orchestration to the
legacy monolith; it should enter through explicit request/service objects and
real stage boundaries while preserving the current public facade.

Target shape:

```text
WorkflowState:
  plan
  artifacts
  quality
  review_items
  claim_status
  warnings

StageResult:
  stage_name
  status
  input_artifacts
  output_artifacts
  before_quality
  after_quality
  delta_quality
  promotion_decision
  claim_status
  evidence_files
  warnings
```

Initial stage names:

- `BuildOSMStage`
- `ConnectivityStage`
- `TLSRealityStage`
- `ReferenceComparisonStage`
- `TeacherGuidedRepairStage`
- `RouteabilityStage`
- `ReviewHTMLStage`

The first implementation should group existing report fields into this model
without changing workflow behavior.

The Hamburg corridor ledger has one narrower persisted DAG:

```text
W0 -> W1
W1 -> W2
W0 -> W3a
(W1, W3a) -> W3b
(W1, W2, W3a, W3b) -> W4
W0-W4 ledger state -> derived W5 capability record
```

W3a acquires official counts without a SUMO network. W3b binds detectors to
the exact W1 network, and W4 consumes the W3a values and W3b mapping. W2, W3b,
and W4 must re-hash their declared network and match W1; W4 must also name the
selected W2, W3a, and W3b manifests and reject unresolved shared-lane
aggregation before it can feed a downstream diagnostic. W5 is a summary record
generated by the ledger and is never a caller-supplied prerequisite. Route incidence and demand
remain inside the current W4 producer until independent persisted producers
exist; the ledger must not invent empty stage boundaries.

## Layer 4: Reviewer

The reviewer renders existing evidence into human-inspectable artifacts:

- HTML review cockpit;
- JSON manifests;
- optional NetEdit selection and view files for code-flagged locations;
- map review links;
- review action queues;
- decision patch targets.

Current anchor:

- `plugins/torii-sumo/src/torii_sumo/core/workflow_review_html.py`

The reviewer should not make hidden workflow decisions. It should show which
gate a review item blocks and how a human decision feeds the next stage.

## Quality Vector

Major construction and repair stages should report a comparable quality vector:

```text
NetworkQualityVector:
  connectivity
  routeability
  topology_fragmentation
  tls_semantic_delta
  junction_pattern_delta
  reference_scope_delta
  manual_review_load
```

Each stage should expose:

```text
before_Q -> after_Q -> delta_Q -> promotion_decision
```

## Promotion Rule

A stage output may be promoted only when it improves at least one declared
target metric and does not regress a critical metric without an explicit,
recorded diagnostic tradeoff. The report must state which metric drove the
decision.

## Benchmark Order

The primary acceptance benchmark is now the corridor contract above.  It must
remain green while human-style add/delete/merge decisions and review overlays
are expanded.

The Ingolstadt single-corridor teacher slice above is now the first real
reference experiment. Full Ingolstadt reference-matched OSM-to-SUMO
construction remains the subsequent city-scale regression target, not a hard
gate already satisfied by the corridor result.

Target directory:

```text
benchmarks/ingolstadt_reference_matched/
```

Benchmark stages:

```text
raw netconvert
-> Torii scoped build
-> TLS aggregated
-> junction candidate review
-> teacher-guided repair
-> final review HTML
```

Each stage should report edge count, junction count, TLS count, `tlLogic`
count, routeability status, teleport count when available, manual review item
count, and reference delta score.

## Known Architecture Debt

- `run_osm_cleanup_workflow` still owns roughly 90 keyword arguments and most
  of an approximately 8,900-line module.  Split configuration from injected
  services first, then extract stage execution behind the unchanged facade.
- `junction_rebuild_candidate.py` is an approximately 12,800-line subsystem.
  Separate evidence extraction, replay planning, materialization, promotion,
  and rendering only after behavior is locked by characterization tests.
- Atomic artifact writes are enforced for the corridor contract and its
  routeability/topology evidence, but older report writers remain.  Migrate
  them incrementally through `core/artifact_io.py`.
- Quality vectors and promotion traces are partly post-hoc.  They must become
  executor inputs/outputs before they can be treated as a workflow state
  machine.
- Real SUMO regressions are local acceptance tests rather than the default
  unit-test suite.  CI needs an explicit SUMO job before these gates can be
  considered continuously enforced upstream.

The migration rule is deliberately conservative: preserve the working
corridor slice, add characterization tests, extract one boundary at a time,
and keep the legacy public API as a compatibility facade until callers have
moved.

## Non-Goals

- Do not claim fully automatic perfect network cleanup.
- Do not add more repair heuristics before benchmark quality deltas can show
  what they improve.
- Do not compare a Torii connected-core network against a full-detail manual
  reference as if they were the same scope.
- Do not use NetEdit screenshots, SUMO load, or routeability alone as proof of
  experiment-ready correctness.
