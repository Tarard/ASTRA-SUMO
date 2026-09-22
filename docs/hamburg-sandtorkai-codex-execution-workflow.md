# Am Sandtorkai Digital-Twin Execution Workflow

This is the long-running execution contract for the Torii work on the named
Am Sandtorkai corridor. It is deliberately a workflow for Codex/Torii, not a
promise that one generated network is correct.

## Frozen scope

The production scope is the named corridor requested by the user:

- `2349`: Am Sandtorkai / Großer Grasbrook
- `2394`: Am Sandtorkai / Am Sandtorpark
- `2403`: Am Sandtorkai / Osakaallee
- the Am Sandtorkai east-west axis and only the declared entry arms, including
  Singapurstraße where it is needed to inject traffic into the corridor

The older `0228/2421/2394` preset remains a diagnostic/regression baseline. It
must not silently supply geometry, signal identity, or counts to the named
corridor.

## The repeatable Codex loop

Every change follows the same loop:

1. **Plan**: state the stage, inputs, expected artifacts, and automatic gates.
2. **Inspect**: search existing Torii modules, tests, cached official artifacts,
   and the relevant SUMO documentation before writing new code.
3. **Implement minimally**: reuse an existing core function or CLI; add a new
   abstraction only when the existing contract cannot express the stage.
4. **Unit-test**: add or update deterministic tests for parsing, hashes,
   failure states, and XML invariants.
5. **Run against real inputs**: use the official catalog/API or a frozen cache,
   produce a versioned output directory, and never overwrite a candidate.
6. **Audit**: run SUMO load, topology, lane-surface overlap, routeability,
   signal-link coverage, and detector-fit checks. Netedit screenshots are
   evidence only; they do not replace code-native gates.
7. **Revise**: use failures to update the plan and the smallest necessary code
   surface, then repeat from step 4. A `review_required` or missing official
   asset is a recorded stop, not permission to invent data.
8. **Publish the state**: update the stage manifest, hashes, artifact paths,
   claim boundary, and this plan's status.

Each Codex turn must leave one of two machine-readable outcomes: a new
versioned artifact that passes its execution gate, or a fail-closed artifact
that names the first invalid stage and the next evidence/code action.  A GUI
capture can explain a failure, but it cannot change a gate.  This keeps the
long-running task resumable after a context switch instead of depending on
conversation memory.

## Stage contracts

| Stage | Main output | Must pass before the next stage |
|---|---|---|
| W0 scope/evidence | scope manifest, source catalog snapshots, coordinate and hash ledger | named nodes and road arms are unambiguous; legacy inputs are segregated |
| W1 road reconstruction | PlainXML + compiled `.net.xml`, road-arm/archetype evidence | every retained movement has one bounded lane path; no introduced focus overlap; routes load |
| W2 signals | W2a window screen, W2b coverage census, MAP/OCIT bindings, TLS owners/link indices, historical event CSV | census endpoint coverage, screen a candidate Saturday, then require every replayed passenger link to have a complete history and t=0 state; missing 2403 material stays blocked |
| W3a count acquisition | frozen official stream snapshot and 15-minute aggregates | count window, stream identities, completeness, and source hashes are explicit; no SUMO network is required |
| W3b detector binding | hash-bound detector-to-lane mapping and candidate evidence | exact W1 network and W3a count snapshot are recorded; ambiguous mappings stay visible |
| W4 route/demand/replay | W3b mapping, same-location E1/E2, routeSampler routes, SUMO config, summary/tripinfo, real-vs-virtual comparison | W1/W2/W3a/W3b identities match; shared-lane aggregation is approved; every expected detector bin is measured; zero SUMO teleports/collisions |
| W5 workflow summary | derived capability and invalidation record inside the execution plan | hashes, provenance, failure states, and the next rerun action are self-contained |

## Evidence and claim rules

- Hamburg catalog/API responses, MAP XML/KML, OCIT-C, TLD observations, HH-SIB,
  HVS, and map references are cached with URLs, retrieval time, and SHA-256.
- OSM may provide topology hints or a declared fallback only. It cannot
  override official lane, movement, road-class, or signal evidence.
- A routeSampler solution is a detector-constrained plausible realization, not
  a uniquely identified OD matrix or observed trajectory.
- Structural all-red programs are topology placeholders and cannot be called
  historical signal replay.
- Missing official data produces `blocked`/`review_required` with a reason and
  next action; it never gets replaced by a guessed value.
- Background Netedit capture must verify a non-Chinese foreground keyboard
  layout, enable Per-Monitor-V2 DPI awareness, maximize the window, preserve
  the user's foreground context, and record all of those facts in the report.

## Current starting state (2026-07-20)

- W0: scope decision is recorded here and in the active task plan.
- W1: the hash-bound v19 official-first candidate retains the three-node road
  boundary and the declared entry arms. Its compiled surface, SUMO-load, and
  connection-mode gates pass; it remains non-promoting only because W2 has not
  established the complete official signal package for `2403`.
- W2: official static MAP/OCIT triplets are available for `2349` and `2394`;
  the published directory and TLD probe currently provide no machine-bindable
  static package for `2403`, so the third controller is blocked. The new W2a
  screen tested five two-hour Saturday candidates (`2026-06-20`, `06-27`,
  `07-04`, `07-11`, and `07-18`) against all 16 active bindings: no candidate
  had complete stream presence. The best candidates (`07-11` and `07-18`)
  returned 8/16; the other three returned 0/16. This is an official-data
  coverage blocker, not a reason to copy a state from another date or signal
  group. W2b now adds a bounded endpoint census at
  `workflow/w2b_signal_coverage_census_v3/signal-coverage-census.manifest.json`:
  all 16 active streams answer the endpoint queries, but eight streams have
  latest records at 2026-06-24 while the other eight continue to 2026-07-20.
  This explains the weekend split without claiming continuity; the census is
  a coverage hint only. It is joined to the frozen official LSA identity
  snapshot, so the gate now classifies 2403 as
  `confirmed_official_node_without_published_tld_binding` rather than treating
  it as an unknown or unsignalized junction. The official OGC API source is
  `https://api.hamburg.de/datasets/v1/lichtsignalanlagen/collections/lsa_knotengrunddaten/items`.
  An [LSBG planning report](https://lsbg.hamburg.de/resource/blob/784082/d82b462f3347d710b8f0cdee89a034af/am-sandtorkai-brooktorkai-pop-up-bikelane-verstetigung-abgestimmte-planung-bericht-data.pdf) independently lists node `2403` as the signalized,
  traffic-dependent Am Sandtorkai/Osakaallee junction, so the missing TLD/MAP/
  OCIT package is treated as an official publication-coverage gap rather than
  as evidence that the junction is unsignalized.
- W3: the named scope has a real official count snapshot for `2026-07-18`:
  19 detector streams at `2394` and `2403`, a complete 30-minute warm-up plus
  two-hour window, and 15-minute simulation/formal comparison files. Node
  `2349` has no official infrared count stream; the artifacts are therefore
  useful for calibration but remain `partial`, not full-corridor demand.
  The new `core/hamburg_named_detector_bindings.py` stage then converts the
  WGS84 detector points with explicit EPSG:25832 metadata and records every
  nearby passenger lane. Eight of 19 fields pass the strict unique-lane gate;
  11 are automatically withheld because of distance or parallel-lane
  ambiguity. No E1/E2 file is emitted from a partial mapping.
- W4: the latest real detector-constrained replay (`w4_named_replay_v12`)
  generated 20 candidate routes, 11 same-location virtual E1/E2 detector
  groups, and 104/104 E1 comparison bins. The E2 writer now permits two
  non-overlapping queue sections on one long corridor lane (but still rejects
  overlapping sections). SUMO itself completed, but the quality gate remains
  blocked by 160 teleports; the structural all-red programs are only
  placeholders. The comparison remains diagnostic: expected total 5,266 versus
  measured `nVehContrib` total 90, with MAE 49.77.
- Netedit evidence: fullscreen captures are automated and now include the
  keyboard-layout check; they remain non-promoting review evidence.

The W2b endpoint census is now implemented in
`core/hamburg_named_signal_observations.py` and
`core/hamburg_official.py`, with CLI
`scripts/census_hamburg_signal_coverage.py`. It performs only bounded first
and latest-record queries and writes a screening-only, hash-bound manifest.
The real run is
`workflow/w2b_signal_coverage_census_v3/signal-coverage-census.manifest.json`.
The reusable CLI `scripts/freeze_hamburg_lsa_nodes.py` freezes the official
OGC node identity, and `scripts/census_hamburg_signal_coverage.py` accepts
`--lsa-identity-manifest` and records the identity hash together with the
coverage result. Its next action is now explicitly
`resolve_official_signal_publication_gap_or_change_scope`; the planner will
not spend another full-history fetch on a node with no published binding.
A positive endpoint census is not permission to replay a guessed or
interpolated weekend signal.
Only after W2b is complete will the workflow return to the W4 teleport gate.
The W2 `2403` signal asset blocker remains explicit; no guessed historical
phase is allowed. The W0 implementation is now
available in `core/hamburg_named_scope.py` and the reproducible CLI is
`scripts/freeze_hamburg_named_scope.py`. Its current real-input output is
`artifacts/hamburg_sandtorkai_twin_20260719/official_first_named_corridor_v1/workflow/w0_named_scope_v2/named-corridor-scope.manifest.json`.
That manifest proves the three official node identities and seven HH-SIB link
selectors, but remains `partial/blocked` because the official catalog has no
static MAP/OCIT triplet for 2403. The CLI exit code records that stop rather
than silently promoting a two-node candidate.

W1 consumes the manifest through `core/road_network/official_plainxml.py`.
The reproducible entry point is `scripts/build_hamburg_named_plainxml.py`;
it writes a new PlainXML directory and records the W0 manifest hash in the W1
manifest. The current real run is
`artifacts/hamburg_sandtorkai_twin_20260719/official_first_named_corridor_v1/workflow/w1_official_plainxml_v4/`.
It has seven selected official links and 26 directed geometry edges. Its
`named_scope_contract` gate passes; the overall W1 candidate remains blocked
because legal lane connections and signal bindings have not yet been produced.

The new official-first geometry entry point is
`core/hamburg_official_corridor_geometry.py` with CLI
`scripts/build_hamburg_official_corridor_geometry.py`. It consumes the frozen
HH-SIB axis plus the official local MAP PlainXML cells for `2349` and `2394`,
keeps `2403` as a road/control boundary, and emits only versioned candidates.
The latest real run is
`workflow/w1_official_corridor_geometry_v19/`. It has 57 compiled edges,
zero junction/lane-surface overlap findings, a passing SUMO load audit, and
zero structural connection failures. The 2349↔2394 mainline lane-count
difference is represented by an explicit derived lane-transition node rather
than a single mismatched edge. The compiler preserves the official MAP
movement curves, anchors malformed local-MAP lane endpoints to their signal
node while preserving the remote boundary, and synthesizes a simple convex
hull for each rebuilt signal-node boundary. It then performs deterministic
`netconvert` endpoint re-anchoring; the final candidate has no custom-shape
distance warnings. The two semantic one-to-many fan-outs are accepted
automatically only when frozen official MAP evidence is exact; an unrelated or
inexact fan-out remains `review_required`. W1 reports
`connection_mode=pass`, `review_finding_count=0`, and
`evidence_justified_target_fanout_count=2` without a permanent manual gate.
The candidate's execution gate is usable for W2 diagnostics, but automatic
promotion remains blocked by the missing official 2403 signal package. The
remaining large-coordinate message is retained as a non-promoting GUI
diagnostic, not silently waived. Fullscreen 1920×1080 NetEdit captures for
all three owners are stored under
`workflow/w1_official_corridor_geometry_v19/netedit_review_2349/`,
`netedit_review_2394/`, and `netedit_review_2403/`; each records a
non-Chinese keyboard layout, DPI-aware capture, and an unchanged candidate
hash.

The follow-up geometry run is
`workflow/w1_official_corridor_geometry_v20_lsa_boundary_evidence/`. It passes
the same surface, SUMO-load, and structural Connection Mode gates, and adds a
hash-bound comparison with the official `lsa_knotengrunddaten` identity
snapshot. The 2403 LSA point projects to `(566119.50, 5933262.50)` in
EPSG:25832, while the HH-SIB road boundary node
`hh_sib.n.242500071` is `(566117.41, 5933264.86)`, a 3.152 m offset. Torii
records this as evidence and keeps the road node in place; it does not snap a
signal point into the road graph or invent 2403 movements. The reusable CLI
accepts this evidence with `--lsa-identity-manifest`, and the signal-asset
promotion gate remains blocked until a machine-bindable 2403 MAP/OCIT/TLD
package is published.

The existing composable intersection classifier was also rerun read-only on
the frozen 2403 OSM cell (seed `5241629952`) into
`workflow/w1_road_detail_2403_diagnostic_v1/`. It recognizes four physical
approaches and `paired_or_offset_conflict_centers`, but all four road-arm
identities are explicitly `osm_fallback`; this is topology evidence, not
permission to copy OSM lane connections over the official HH-SIB geometry.

The reusable OSM/HH-SIB semantic bridge was then rerun with the current
adapter (rather than reusing the older review output) into
`workflow/road_semantic_bridge_2403_v1/`. The run is `review_required` with
53 official-link candidate sets and 167 bounded OSM-segment candidates. This
is an important W1 diagnosis: the official `A326` Am Sandtorkai record is a
986 m link, while the strongest OSM match is only a corridor fragment and is
therefore rejected by the full-link gate. `O219` Osakaallee and the relevant
`B623` Brooktorkai candidates cover their official links, but the bridge still
keeps them out of the canonical projection until their hash-bound lineage is
established. The current adapter has no `strassenschluessel` parsing blocker;
the remaining reviews are candidate confirmation, stationing direction, and
geometry-derived SUMO lineage. Thus OSM remains a topology/fragmentation
hint, while the HH-SIB link is the authoritative road object for the next
official-first materialization step.

The W1 compiled network now declares its already-metric coordinates as
ETRS89 / UTM 32N (`EPSG:25832`) in `<location projParameter>`. This metadata-only
step keeps WGS84 detector and MAP binding reproducible; it does not move the
geometry. A diagnostic compilation then bound 21 official vehicle MAP lanes on
the geometry-only network: 18 active/high, 2 active/medium, and 1
needs_review/low. That result is recorded as a blocked preflight, not as
permission to guess lateral lane order or write connections.

The current W2 partial evidence package is
`artifacts/hamburg_sandtorkai_twin_20260719/official_first_named_corridor_v1/workflow/w2_official_map_partial_v2/`.
The existing deterministic official lane-axis and lane-transition code was
rerun against W1 v4: it reports 16/21 matched MAP lanes, one authorized
eastbound profile transition, and one abstained direction. The next code slice
is now implemented by
`road_network/official_connection_plainxml.py` and
`scripts/build_hamburg_official_connections.py`. The real connection-stage
run is in
`artifacts/hamburg_sandtorkai_twin_20260719/official_first_named_corridor_v1/workflow/w2_official_connection_partial_v3/`:
it emits exactly two official continuation connections (`0→1`, `1→2`) and
four explicit deletes for the other lane pairs. The newly added right-side
lane is intentionally not fed from upstream. The stage remains `blocked`
because the reverse direction has no unique official HH-SIB profile cut; its
compiled network is diagnostic only. SUMO 1.27.1 compiled and loaded that
diagnostic network successfully, with only the expected empty-pocket warning.
Three-node signal promotion remains blocked until 2403 has a machine-bindable
MAP/KML/OCIT-C asset triplet.

The reusable TLD-to-MAP binding slice is now implemented in
`core/hamburg_named_signal_binding.py` with the CLI
`scripts/build_hamburg_named_signal_binding.py`. It reuses the existing
SensorThings stream shape and never fabricates a phase or rewrites the
candidate network. The real run is
`workflow/w2_official_signal_binding_v1/`: all 8/8 vehicle movements at both
2349 and 2394 bind to their official controller `linkIndex`; 2403 is explicit
and missing. Its execution gate passes for downstream diagnostics, while its
automatic-promotion and historical-replay gates remain blocked. Both the live
2349 v1.1 source and the cached official 2394 v1.0 snapshot are recorded with
URLs and SHA-256 hashes.

After the W1 v19 projection fix, the same binding was rerun without changing
the network in `workflow/w2_official_signal_binding_v3/`. It still binds 8/8
vehicle movements at each available node and records `2403` as
`missing_official_map_cell`; this is diagnostic evidence attached to the
resumable plan, not a substitute for the missing official asset.

W2a is now a separate, reusable preflight in
`core/hamburg_named_signal_observations.py` and
`core/hamburg_official.py`, with CLI
`scripts/screen_hamburg_signal_windows.py`. It performs one bounded official
query per candidate-window/stream pair and records `present`, `empty`, or
`error` without treating a positive hint as a two-hour completeness proof.
The real screen is
`workflow/w2a_signal_window_screen_v1/signal-window-screen.manifest.json`.
It found no complete candidate among the five tested Saturdays; `07-11` and
`07-18` each have 8/16 present streams and no query errors. This artifact is
the reason the workflow now stops before another full history fetch.
An expanded scan over all 29 Saturdays from `2026-01-03` through `2026-07-18`
is recorded in
`workflow/w2a_signal_window_screen_saturday_range_v1/`; it also found zero
complete candidates, with only `07-11` and `07-18` returning any streams
(8/16 each).

The count-acquisition stage now called W3a was first implemented in
`core/hamburg_named_count_scope.py` with the
reproducible entry point `scripts/build_hamburg_named_counts.py`. It reuses the
existing `SensorThingsClient`, count parsers, Saturday-window ranking, warm-up
timeline, and canonical-count writer. The real run is
`artifacts/hamburg_sandtorkai_twin_20260719/official_first_named_corridor_v1/workflow/w3_official_counts_partial_v3/`:
the busiest complete formal interval is 16:30–18:30 local time on Saturday
2026-07-18 (14:30–16:30 UTC), with warm-up beginning at 16:00 local. It writes
19 streams, 190 ten-bin simulation rows, and 152 formal comparison rows. The
scope manifest explicitly records missing `2349` coverage. Stream `32693` was
assigned to `Richtung 1` only by a deterministic nearest-declared-direction
geometry rule (3.116 m to stream 28466 versus 23.806 m to the next candidate);
the inference and provenance remain in `sensor-scope.evidence.json`. The
single compact hand-off is `counts.corridor.aggregate.15min.csv`, grouped by
official node and direction. The W3a execution gate passes for diagnostics,
while full node coverage keeps automatic promotion blocked.

W4 is implemented in `core/hamburg_named_replay.py` with the reproducible
entry point `scripts/build_hamburg_named_replay.py`. It reuses Torii's existing
count parser, virtual-detector aggregation, edge constraints, route support,
`routeSampler.py`, and E1/E2 writers. The real run is
`workflow/w4_named_replay_v5/`: it produces 20 candidate routes, 11 virtual
same-location detectors, and a complete 88-bin E1 comparison for the warmed
window. The process itself exits successfully, but the automatic quality gate
blocks the stage on 152 SUMO teleports (collisions remain zero). The manifest
therefore points back to signal timing/topology repair instead of promoting a
replay with artificial vehicle teleportation. SUMO detector output is written
with an absolute path because relative output paths resolve beside the
additional file, not beside the process working directory.
The same stage is exposed as the `sumo_hamburg_sandtorkai_named_replay` MCP
tool, so the CLI and Torii calls share one materializer and one gate contract.
After W1 v10, the replay was rerun as `workflow/w4_named_replay_v6/` with the
new network and W2 v2 binding; its 152-teleport quality gate remains blocked,
while the 88-bin detector comparison is still produced. A separate
`--tls.all-off` run with the same demand and a 24,000-second horizon is recorded
under `workflow/w4_routeability_tls_off_v4/`: all 3,228 vehicles arrived and
there were no collisions, but five vehicles still teleported while yielding.
This is `diagnostic-demo` evidence only. It isolates signal control from most
base route connectivity, but it is not a zero-teleport proof and does not
validate official signal timing.
When an execution-ready signal-observation manifest is supplied, the same W4
materializer switches to Torii's existing TraCI TLS replay and uses the
official `tls-link-events.csv`; when it is absent or blocked, W4 stays on the
diagnostic all-red construction path and cannot pass the teleport gate.
The latest W4 run also proves that a detector geometry fix alone does not make
the replay promotable: low-confidence lane snaps and missing historical signal
observations remain separate automatic gates.

The signal-history hand-off is implemented in
`core/hamburg_named_signal_observations.py` with the reproducible entry point
`scripts/build_hamburg_named_signal_observations.py` and MCP tool
`sumo_hamburg_sandtorkai_signal_observations`. It consumes the exact W2
binding artifact, uses the official v1.0 primary observation service for the
declared UTC window, writes normalized observations plus `tls-link-events.csv`,
and requires a valid preceding state for every active SUMO link. A v1.1
metadata/cache artifact is never promoted as v1.0 historical state. The first
real observation package is
`workflow/w2_official_signal_observations_v3/`; it correctly remains blocked:
the reused v1.1 cache response is partial for multiple streams and the required
2403 node is missing. Direct v1.0 probes are retained separately and confirm
that the v1.0 primary endpoint is the historical source to use on the next
low-concurrency refresh. The event file is still retained as diagnostic
evidence, but it is not eligible to drive W4.
An independent focused navigation to the same v1.0 Datastream endpoint in the
Codex in-app browser also timed out, matching the CLI probes; this is an
upstream availability condition, not a guessed alternate URL.
The retry policy is now explicit: `fetch_hamburg_signal_observations` and the
named-signal CLI accept `--retry-incomplete-cache`. With that flag, a cached
`partial`/`error` response, or a primary stream without a preceding t=0 state,
is not authoritative and is fetched again. The default remains compatible with
the older cache-reuse contract, while the long-running W2 workflow uses the
explicit retry mode and records the policy in its manifest. The new policy is
covered by the focused signal-history tests (18 passed). A v10 refresh using
this policy was bounded to two workers and an eight-second request timeout;
the Windows transport still failed to return within the outer six-minute
runner limit. Its child processes were reaped and no observation manifest was
promoted, so this remains an upstream/transport blocker rather than historical
signal evidence.
The MCP entry point uses the same policy by default, so Codex and CLI reruns
cannot silently diverge in cache behavior.
The next v1.0 low-concurrency refresh was deliberately bounded; it reached
9/16 stream caches before the outer timeout and therefore wrote no final
manifest. Those partial cache files remain diagnostic evidence only and are
not supplied to W4 or the execution ledger.

The latest bounded real fetch is
`workflow/w2_official_signal_observations_v11/`. It completed 8/16 streams
for the requested `2026-07-18` window and proved that the endpoint is
reachable; the other eight streams returned valid empty collections (their
last available records are on a different date range). Its manifest remains
`execution_gate=blocked`, so it cannot drive W4. The next run must use a
screened complete window, not simply retry the same date.

As a code-path diagnostic only, the screened weekday window
`workflow/w2_official_signal_observations_weekday_diagnostic_v1/` fetched all
16/16 active streams, initialized all 16 t=0 states, and emitted 4,925
official events. Its overall status is still `partial` because 2403 is
missing, and its weekday timestamp is not eligible for the requested Saturday
experiment.

The Codex loop is now executable through
`core/hamburg_execution_workflow.py`, the CLI
`scripts/run_hamburg_execution_plan.py`, and MCP tool
`sumo_hamburg_sandtorkai_execution_plan`. The v2 contract accepts dated W0,
W1, W3a, W2, W3b, and W4 manifests. W3a is official count acquisition and
depends only on W0; W3b is detector binding and depends on both W1 and W3a.
The existing W4 producer still owns route incidence, demand, and replay, but
now consumes the selected W3a count values and W3b mapping instead of accepting
arbitrary count bytes or remapping count points.
W5 is the derived capability-summary record and cannot be supplied by the caller.
Optional diagnostic artifacts (for example the W2b endpoint census) can be
attached with `--stage-feedback W2=...`; repeat the option to attach several
feedback manifests to the same stage.
The ledger records each primary and feedback-manifest SHA-256, stage
dependencies, readiness, first invalid stage, machine feedback, next action,
and downstream invalidation set. W1, W2, W3b, and W4 network files are
re-hashed; W2/W3b/W4 must carry the exact W1 network SHA-256, and W4 must name
the selected W2, W3a, and W3b manifest identities and exact count values.
Execution-ready signal history must also bind the selected W2 manifest and the
exact TLS-event bytes. A changed
upstream or feedback manifest keeps already-materialized dependants invalid
until each downstream manifest identity changes; the newly supplied changed
stage itself is not invalidated. Feedback manifests are merged in command-line
order; they can explain a stop, but they cannot change a stage's execution or
automatic-promotion gate.

The W0 scope manifest has two separate meanings in this ledger: its overall
promotion gate remains blocked when 2403 signal assets are missing, while its
scope execution gate passes because the three official node identities and
road-scope links are complete. This allows W1 geometry and W3a count work to
continue without allowing W2 signal promotion. The latest real ledger run is
`workflow/w5_execution_plan_v32/execution-plan.manifest.json`; W0, W1, W2, and
legacy W3 have execution gates that allow downstream diagnostics, while W2
remains promotion-blocked by signal publication coverage. That v1 artifact is
historical evidence and must be regenerated under the v2 split before reuse.
W4 is explicitly blocked by its teleport quality gate and missing historical
signal observations; overall promotion remains blocked.
Historical v1 detail: that same v32 ledger used the blocked W2 observation
manifest, official W2 feedback audits, a legacy W3 detector-binding audit, and
the W4 replay audit. It kept explicit `execution_gate` and
`automatic_promotion_gate` decisions separate and attached the W2b census,
asset history, identity refresh, and replay audit as hash-bound feedback. It
reported `missing_required_node_ids=["2403"]` and
`publication_gap.decision=confirmed_official_node_without_published_tld_binding`
without guessing a signal plan. Those details document the old run only; they
do not satisfy the current W3a/W3b split, W4 v2 dependency identities, or W5
summary contract.

The same official catalog and directory resolver was rerun on 2026-07-20 into
`workflow/w2b_signal_asset_directory_v4/`. It resolved exactly one MAP/KML/
OCIT-C triplet for `2349` and one for `2394`; `2403` had no MAP or OCIT-C
directory entry. The report is bound to the catalog package
`247b868e-b947-488f-8bc5-ac902b00976f`, MAP directory SHA-256
`75af40f1bd0a9ba158700208401a3938eaddf6be7a26c71e13c657e27bae1d2c`, and
OCIT-C directory SHA-256
`a6e8905a392536d76d051ff67f78010fdc16a98930e5dc6f07123e38a4721a95`.
This also resolves an identity trap seen in the browser: TLD
`trafficLightsID=228` belongs to the legacy `0228`
`Baumwall/Niederbaumbrücke/U-Bahnhof` node, not to the named `2403`
`Am Sandtorkai/Osakaallee` node. It cannot substitute for the missing asset.
The execution planner now extracts `resolved_node_ids` and
`unresolved_node_ids` from such auxiliary reports. The rerun is
`workflow/w5_execution_plan_v32/`; it keeps the promotion gate blocked, but
the feedback now records `resolved_node_ids=["2349","2394"]` and
`unresolved_node_ids=["2403"]` without changing the gate.
The reusable `audit_hamburg_signal_asset_history.py` step then checked the
current `traffic-lights-data-hamburg6` directory plus archived package 5 and
package 4 MAP/OCIT directory snapshots. All three snapshots resolve the same
triplets for `2349` and `2394`; none contains a complete triplet for `2403`.
Historical-only matches are explicitly labeled
`abstain_do_not_substitute_historical_asset`, so an archive cannot silently
replace a missing current publication. The latest report is
`workflow/w2b_signal_asset_history_v1/hamburg-signal-asset-history-audit.629426836cfe.json`.
The plan was then re-executed from the versioned W0-W4 inputs into
`w5_execution_plan_v32/`; its first invalid stage is W4 and its next action is
`resolve_stage_gate`. This is the intended revise-and-rerun behavior: W4 is
retried once W2 is execution-ready, but its quality failure is retained rather
than hidden. The plan itself remains blocked until the official publication
gap, detector-lane ambiguity, and W4 quality gate are resolved.

An independent current OGC identity refresh was also run into
`workflow/w2b_lsa_identity_current_v1/`. It resolves all three official
points (`2349`, `2394`, `2403`) uniquely by node number and normalized road
name, with 2403 explicitly named `Am Sandtorkai/Osakaallee`. This passes the
identity gate but deliberately does not count as MAP, OCIT-C, TLD stream, or
SUMO signal binding; those are separate evidence requirements. The refresh
therefore confirms that the W2 blocker is a control-asset/publication gap,
not an unknown or misidentified junction.

The first W2 gate is now wired into the existing compound TLS entry point. When
`--named-scope-manifest` is supplied, the entry point requires a signal-complete
named-scope manifest before checking hashes or creating an output directory. The
real run
`scripts/build_hamburg_compound_official_tls.py` against the W0 v2 manifest
returned `blocked` with `named scope is not signal-complete for this stage` and
created no candidate directory. The live Hamburg catalog, OCIT-C directory, and
Transparency Portal package response were each checked on 2026-07-20; none
contained a machine-bindable 2403 asset. The existing 2349/2394 compound
adapter is therefore retained only as a bounded diagnostic path until a
three-node signal materializer exists; it cannot claim the named corridor.

The first loops have been executed and audited: the initial Windows console
encoding failure was fixed, the same real-input command was rerun, and the
W0/W1/W2/W3 gate tests pass in their focused slices. The full repository
regression and changed-file checks are rerun after each stage change; the
latest result is recorded with the next test run rather than copied from an
earlier revision.

The 2026-07-20 fullscreen NetEdit audit adds a bounded visual-evidence rule:
2349 and 2394 each pass their local physical-cell/junction-owner and lane
surface checks, with no cross-owner controlled connection outside its owner.
The audit also records that the source network is unchanged and that the
captures are DPI-aware, maximized, and made only after the keyboard-layout
check. Remaining overlap findings are outside the declared local junction
cells and therefore cannot be used to reject the corridor or to authorize
merging owners. This evidence is diagnostic unless the code-native gates and
the hash-bound stage manifest agree. The latest full regression is
`1884 passed in 92.43s`; Ruff and `git diff --check` pass as well. The active
execution ledger remains `first_invalid_stage=W2`, with
`resolved_node_ids=[2349,2394]` and `unresolved_node_ids=[2403]`.
