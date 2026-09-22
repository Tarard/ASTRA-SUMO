# Torii MCP Tool Catalog

Torii provides a focused default MCP profile and an explicit legacy compatibility profile. Use the default profile for normal work. Use the CLI for long, batch, and specialized workflows.

## MCP Profiles

The server supports three profiles selected with `create_server(profile=...)` or `TORII_MCP_PROFILE`:

- `default` (default): the 10-tool core surface:
  `torii.preflight`, `torii.config.inspect`, `torii.run.compare`,
  `torii.place.resolve`, `torii.intersection.classify`, `torii.signal.classify`,
  `torii.network.audit`, `torii.network.compare`, `torii.demand.audit`,
  `torii.review.create`.
- `legacy`: all 73 historical tools listed below. Select this profile explicitly for compatibility.
- `netedit`: the four-tool observation loop:
  `torii.netedit.open`, `torii.netedit.observe`, `torii.netedit.act`,
  `torii.netedit.close`.

Default and NetEdit tools use stable names, titles, safety annotations, and a common structured result shape.

`torii.network.audit` accepts `profile=quick` or `profile=standard`. `quick`
runs the topology check. `standard` runs topology, Connection Mode, and
overlapping-junction checks. Neither profile runs SUMO routeability.

Run routeability through the CLI:

```powershell
torii network routeability <network.net.xml> <output-dir> --json
```

Run an allowlisted long, batch, Hamburg-specific, or candidate-generation
capability with a JSON request:

```powershell
torii workflow <tool> <request.json> --json
```

The implementation boundary is consistent across groups:

```text
server.py profile switch -> mcp_contract_tools.py or legacy_tools.py -> tools/* -> core/*
```

## Router

- `torii_auto_workflow` - accept a host-model `workflow_selection`, or retain the older request classifier when no selection is supplied.

Read the current scenario directory and selected function arguments with:

```powershell
torii workflows --json
torii workflows --scenario hamburg_network --json
torii workflow selected selection.json --json
torii workflow selected selection.json --execute --json
```

The host model selects from the user's meaning and supplied evidence. The
selection contains `user_request`, `scenario_id`, `reason`, and `arguments`.
The command without `--execute` checks readiness only. It does not read the
drawing's meaning, verify nested source evidence, or complete a workflow.

The catalog distinguishes `workflow`, `check`, `stage`, and `guidance`. As
checked on 2026-09-09, its 25 entries include 19 callable entries and six guides,
not 25 complete workflows. The executable mapping and live signatures belong
to [workflow_catalog.py](../plugins/torii-sumo/src/torii_sumo/core/workflow_catalog.py).
See [scenario selection](workflow-selection.md) for the four-field example and
result meanings.

`torii_auto_workflow` and `run_auto_workflow` dispatch `workflow_selection`
before the compatibility detector. `inspect-only` and `ask-first` keep it
unexecuted. Without a selection, the older regular-expression route remains.
The selected route calls no new model API and performs no second keyword
classification. Missing required values or input files return `needs_input`.
Unknown scenario IDs and extra arguments are blocked without choosing another
workflow. A child's `review_required` or `blocked` result remains visible;
the fact that a function ran is not a success certificate.

## Environment, Execution, and Evidence

- `sumo_get_environment`
- `sumo_preflight`
- `sumo_config_pair_preflight`
- `sumo_run_config`
- `sumo_run_minimal_smoke`
- `sumo_compare_outputs`
- `sumo_collect_evidence`

Use these for environment discovery, controlled execution, baseline/variant checks, and portable evidence bundles. They do not establish network or controller correctness by themselves.

## NetEdit GUI Candidate Session

The four NetEdit MCP operations share one persistent server process. They cannot
be chained as separate CLI processes. For a command-line capture, use
`torii netedit review <source.net.xml> <new-output-dir> <source-sha256>` to open,
observe, and close in one invocation. The installed plugin exposes this command
through its locked `run_torii_sumo.py --cli` runner.

- `sumo_netedit_session`

This is one grouped session tool, inspired by the recurring scene/document/
object/viewport/edit lifecycle in [Blender MCP](https://github.com/ahujasid/blender-mcp),
[FreeCAD MCP](https://github.com/neka-nat/freecad-mcp), and
[Unity MCP](https://github.com/CoplayDev/unity-mcp). These are design references,
not Torii runtime dependencies or identical APIs. Torii therefore keeps one
grouped tool rather than registering a separate MCP tool for every button. The
lifecycle maps to NetEdit as `open -> observe -> act -> observe -> finalize/abort`.
`open` creates one source-hash-bound candidate session per Torii server;
`observe` returns a local client-coordinate screenshot path/hash plus an explicitly
on-disk candidate summary; `act` checks the caller's exact last recorded screenshot
SHA, then captures the live viewport again. Bounded global editor animation is
allowed, while click/drag target pixels must remain exact. `finalize` saves,
closes, then runs existing SUMO-load, bounded surface-overlap, and Connection
Mode audits. F7 joins must also match the source/candidate junction identities
declared before the GUI edit, use the frozen selection containing exactly those
source junctions and be the first edit action. Every semantic shortcut and save
requires an exact live viewport match immediately before delivery. Unsaved GUI state is visible only in the screenshot, never
misreported as persisted XML. The source network is immutable and every result
keeps automatic promotion blocked. Torii deliberately omits arbitrary Python,
C#, shell, or caller-exposed raw Win32 execution surfaces. The current local Codex
integration renders the screenshot artifact with its local image viewer; the tool
does not yet embed pixels as generic MCP `ImageContent`.

`torii.netedit.observe` writes a screenshot and report. It is therefore marked
non-read-only and non-idempotent. `torii.netedit.close` accepts
`mode=finalize` or `mode=abort`. Finalize requires the latest screenshot
SHA-256. Abort does not require a screenshot hash and closes without saving.

## OSM Construction and User-Facing Review

- `sumo_osm_resolve_place`
- `sumo_osm_build_network`
- `sumo_tls_audit`
- `sumo_tls_multisource_review`
- `sumo_network_connected_core`
- `sumo_network_routeability_probe`
- `sumo_network_routeability_audit`
- `sumo_network_review_html`

`sumo_osm_cleanup_workflow` is CLI-only and is not registered in an MCP
profile. Resolve a place to a bbox before the call. Its JSON request has seven
fields: `output_dir`, `bbox`, `profile`, `source_osm_path`, `traffic_layers`,
`reference_net_file`, and `timeout_seconds`. The `reference_matched` profile is
audit-only and does not repair the network. `sumo_osm_build_network` remains a
low-level importer and is not a complete cleanup workflow.

## Structural, Reference, and Regression Audits

- `sumo_network_connection_mode_audit`
- `sumo_network_connection_mode_calibration`
- `sumo_network_connection_mode_regression_audit`
- `sumo_network_exact_semantic_regression_audit`
- `sumo_network_topology_audit`
- `sumo_network_overlapping_junction_audit`
- `sumo_network_reference_join_audit`
- `sumo_network_reference_hierarchy_audit`
- `sumo_network_reference_scope_audit`
- `sumo_network_tls_warning_parity`
- `sumo_network_surface_overlap_audit`
- `sumo_network_surface_overlap_comparison`

These tools distinguish structural failure from evidence ambiguity. A route completing successfully does not replace lane/via/request/TLS or exact source-to-candidate evidence.

## Candidate Construction and Promotion Gates

- `sumo_network_junction_aggregation_variant`
- `sumo_network_scope_pruning_variant`
- `sumo_network_corridor_geometry_simplification_variant`
- `sumo_network_corridor_edit_ledger`
- `sumo_network_corridor_materialize_variant`
- `sumo_network_corridor_candidate_gates`
- `sumo_network_teacher_corridor_comparison`
- `sumo_network_tls_reference_cleanup_variant`
- `sumo_network_standard_nema_phase_binding`
- `sumo_network_teacher_guided_junction_variant`
- `sumo_network_teacher_guided_repair_queue`
- `sumo_network_tls_aggregation_variant`

Candidate tools write separate review artifacts and retain rollback/provenance. They do not silently replace the source network.

## Intersection Workflows

- `sumo_intersection_archetype_classify`
- `sumo_intersection_model`
- `sumo_intersection_clean`
- `sumo_intersection_validate`
- `sumo_nema_four_way_reference_workflow`
- `sumo_intersection_scene_workflow`

The read-only archetype classifier hashes and parses one frozen OSM byte snapshot, then returns the finite composable profile together with complete physical-cell, topology, movement, and lower-level road-detail evidence bodies. Road detail resolves authoritative road-network categories first, keeps OSM `highway=*` as an explicit fallback, and only then emits arm/channelization/connection candidates. Its top-level generation status, type-recognition decision, disposition, and blocked promotion gate are separate fields. It does not authorize node joins, channelization reconstruction, or traffic-light binding. The remaining tools cover structured intersection modeling, compilation, validation, and the bounded synthetic/reference workflows used for small-network experiments.

## Road-Semantic Evidence Bridge

- `sumo_road_semantic_bridge`
- `sumo_intersection_road_sumo_bind`

This read-only tool parses caller-supplied frozen OSM, SUMO, and Hamburg HH-SIB snapshots at explicitly declared times. It reports official-to-OSM conflation candidates, OSM-to-SUMO lineage, and a `road_network_evidence` artifact containing only pass-reviewed identity/property assertions. OSM-to-SUMO lineage requires an explicit declared import provenance (`sumo_imported_from='osm'` and the exact OSM SHA-256); otherwise it stays blocked. An optional frozen HVS snapshot is retained as a distinct official category source; its feature membership is not automatically conflated to HH-SIB, OSM, or SUMO. An optional local reviewed-assignment JSON may select an already generated official-to-OSM candidate and attach scheme-qualified official road properties while retaining the HVS source reference. When an `output_dir` is supplied, it must be separate from input directories; the tool writes hash-listed bridge/evidence/manifest JSON artifacts there for the later intersection classifier and fails closed if its artifact filenames already exist. The tool does not download data, mutate a source or candidate, create SUMO files, infer lane connections, bind signals, or authorize reconstruction or digital-twin promotion.

`sumo_intersection_road_sumo_bind` composes the hash-bound local intersection classification with the bridge report's OSM-to-SUMO lineage. It requires the road-detail projection and bridge report to carry the same non-empty `bridge_id`, and checks the exact OSM source hash before binding. It returns road-arm-to-edge candidates and connection-intent review records only; optionally it writes that direct immutable binding payload as a new JSON artifact for a later materializer. It does not emit a SUMO `connection`, decide lane indices or stop lines, assign a junction owner or TLS `linkIndex`, alter a network, or authorize promotion.

## Signal Device Classification

- `sumo_signal_device_profile_classify`

This read-only specialist hashes and parses one frozen OCIT-C supply snapshot, then returns a Germany-first, Europe-compatible inventory that keeps logical signal groups, physical signal heads, visual displays, non-visual accessibility outputs, runtime state, and control methods separate. It preserves source-native traffic classes, channel labels, allowed signal-image codes, mounting metadata, and unresolved symbols or applicability. In particular, an OCIT `Gruen` interface slot carrying `Ton` or `Vibra` is not classified as a visual green lamp. The result never authorizes lane or movement binding, signal-group reassignment, phase generation, timing, or controller actions.

## Detector Demand

- `sumo_detector_route_support`
- `sumo_detector_count_constraints`
- `sumo_detector_count_audit`
- `sumo_detector_route_sampler_calibrate`

These tools construct and assess detector-constrained demand. Their output is a plausible count-matched route set, not a uniquely identified true OD matrix.

## Hamburg Corridor Digital Twin

Road construction and traffic-count calibration have separate entry points.
Use the CLI-only road construction command for a fresh build:

```powershell
torii hamburg build-network <request.json> <new-output-dir> --json
```

The same `torii.hamburg-topology-workflow-request/v1` request also accepts
`construction_plan` for construction-drawing-led work. It binds the original
PDF and a manually reviewed `torii.engineering-topology/v1` JSON by SHA-256.
This mode builds a fresh SUMO network from the declared roads, lane uses,
widths, and connections. It does not require MAP/KML, LSA identities, or aerial
images, and it does not interpret arbitrary PDFs automatically.

The drawing determines known lane counts, uses, and movements. OSM only fills
missing coordinates, names, or speeds. An OSM coordinate from a different or
unknown year needs `osm_geometry_valid_for_target_year` on the node or topology,
with evidence explaining its validity. Keep `construction_plan.data_year`,
optional `document_date`, and optional `scenario.target_year` distinct.
A newer map does not change a historical target year.

Optional `supporting_maps` records supplied references, including Google Maps.
If no Google Maps material was supplied, report that absence. A recorded URL
alone does not prove that imagery was inspected. See the
[drawing-led request and source policy](../examples/05_hamburg_topology/construction-plan.md).
This is a CLI mode, not an additional MCP tool.

Inspect normalized road-use observations before construction with the CLI-only
`torii hamburg inspect-road-uses <request.json> <new-output-dir> --json`.
This separate command compares local line orientation, extent, road-axis side,
and reference years. It preserves uncertain ownership and permissions and does
not modify the network. See the [request format](../examples/05_hamburg_topology/road-use-review.md).

Without `construction_plan`, the existing MAP/aerial mode uses the same request
schema. It supplies raw
`source_osm`, official `lsa_identity`, `osm_build`, ordered `intersections`,
and `road_names`. Each intersection supplies official MAP XML, matching KML,
an aerial image, its `bbox_epsg25832`, and `aerial_year`. Source files require
SHA-256 hashes. Paths may be relative to the request file. An arbitrary
screenshot without known geographic bounds is not a georeferenced input.

An intersection may also supply `road_reference.topology` and
`road_reference.cross_sections` as hash-bound Hamburg GeoJSON files. The
optional road-edge stage records image-contrast boundary candidates and supplies
a soft historical road-area prior to movement tracing. It does not assign
lane uses or promote the network. See the [input and interpretation guide](../examples/05_hamburg_topology/aerial-road-boundaries.md).

The command creates a new source network, physical-area bindings, movement
plan, and candidate before it runs the construction and vehicle checks.
Its request does not accept an earlier SUMO network or movement summary.
The output directory must be new. `workflow.manifest.json` retains the stage
results, input hashes, and first failed stage. `network-handoff.json` records
the resulting network and workflow manifest with their hashes for later use.
Its `construction_decision` stays visible for diagnostic candidates that
remain `review_required` or `blocked`; the file is not an acceptance certificate.
Connection structure, geometry findings, and vehicle passage remain separate
results. Missing counts or historical signal observations do not stop this
construction workflow. `calibration.status` remains `not_run`.

Set `construction.junction_contours` to `"guarded"` to try local outline
tightening. The default `"preserve"` keeps the constructed boundary. The
guarded method protects known lane surfaces and complete road mouths. It
does not select one support component and discard the others. Each proposed
node shape is compiled with the original construction network and the same
connection patch, before final movement records and test signals are built.
A failed geometry or traffic-data preservation check retains the previous
boundary. `movement_materialization.junction_contours` records each result.
`selected_netconvert_command` identifies the accepted construction command.
The comparison records the existing 0.1 m geometry precision. Adoption of
a contour also requires equivalent directed lane curves and lengths, allowing
only floating-point roundoff and equivalent resampling. This prevents lane
displacement from consuming the coverage tolerance a second time. Candidate
lane coverage is recorded separately. Speed, permissions, signal programs
and connection records must stay unchanged.
Geometry preservation does not establish field curbs or traffic islands.
These shape results retain their separate imagery review.

The existing commands remain available for individual steps and diagnosis:

```powershell
torii hamburg aerial-movements <request.json> <output-dir> --json
torii hamburg combine-aerial-movements <request.json> <output-dir> --json
torii hamburg repair-lane-connections <source.net.xml> <output-dir> --json
```

The aerial-movements command verifies source hashes, traces candidate curves, and
keeps the official MAP curve whenever tracing fails or exceeds the declared
error limit. It writes review images and selected movement geometry. It does
not write a SUMO network, classify physical conflict cores, or reconstruct
signal timing.

The combine-aerial-movements command builds a separate candidate for the declared node
list. It checks each referenced plan hash and transforms the official geometry
to the source network coordinates. It binds official ingress and egress lanes
before it supplies explicit connections and full curves to netconvert.
It checks complete internal paths and reports required, actual, missing, extra,
and unresolved official connections. A rejected curve does not remove a
confirmed connection. Unmapped side roads remain present for review.
The command does not reconstruct official signal timing. Detector data and
historical signal observations are not prerequisites for topology-only work.

The optional `context_joins` request field maps a new junction ID to an
explicit list of source nodes without signal or rail control. This stage runs
before the official junction reconstruction. It records the original boundary
paths and restores connections that native joining omitted. It does not infer
that the selected nodes form one physical intersection or that every source
turn is legal in the field. `context_geometry_neighbors` may name directly
adjacent junctions whose affected internal curves need adjustment; their
signal programs, connection identities, and unrelated geometry remain fixed.
All affected geometry and preservation checks are recorded separately. Do not
copy these optional, case-specific groups from an earlier run into a fresh
request without evidence for the selected physical intersection.

After construction, run `torii network movement-probes <manifest.json>
<output-dir> --json` to test official lane transitions with permitted vehicles.
The check observes the complete internal lane path. It keeps the number of
official movement records separate from the number of joined boundary
connections. A passed isolated vehicle test does not establish field geometry,
signal timing, or successful multi-vehicle operation.

Diagnostic routeability uses the standard 5 m passenger vehicle length to
select origin and destination roads. This is a sampling choice, not an access
rule. Short roads remain in the network and may be traversed. Shared endpoints
must have the same lane identity, geometry, length, and coordinate frame.

Only the original network determines connection eligibility and reachable
origin–destination pairs. The native SUMO router checks lane and connection
permissions. A seeded sample chooses an eligible origin, then one of its
reachable destinations. Same-road trips are excluded. The candidate network
cannot remove requests by losing a connection.

The combined workflow freezes the generated requests for all candidate
attempts and the original-source run. Every request is routed explicitly;
failed requests are not discarded or replaced. Frozen requests are replayed
without eligibility filtering. Reports list endpoints without a reachable
counterpart and eligible endpoints not sampled. This synthetic diagnostic
does not calibrate historical demand or replace complete movement checks.

Routeability requires natural completion within the declared `max_end`.
Each attempt uses the finite SUMO `time-to-teleport` value `max_end`, so waiting
recovery cannot move vehicles past a queue during that observation window.
The time limit, trips, and signals are not extended or changed to obtain a
pass. Unfinished vehicles, collisions, discarded vehicles, or teleports still
fail. Native vehicle-route output must contain every requested vehicle, its
approved complete road sequence, and a normal arrival within the attempt.
Long waits remain part of the result; this check is not a traffic-efficiency
or historical-signal validation.

The legacy lane-repair default was used after protected-signal construction. It removes
only the reviewed LSA119 fanouts, keeps one explicit target lane per source
lane, separates the reviewed 266199070 diverge, and preserves the signal
cycle. This default is a case-specific repair, not a general corridor rule.
`build-network` does not apply that default automatically.
Supply `--connection-patch <file.con.xml> --patch-sha256 <sha256>` to apply
explicit reviewed lane additions/deletions instead of that default. The command
checks patch scope and internal paths after netconvert. Other corridor fanouts
remain review items. See [the topology-only example](../examples/05_hamburg_topology/README.md).

Finish the local workflow with the hash-bound
`plugins/torii-sumo/scripts/netedit_background_review.py` review. Its Inspect
capture follows `Connection -> Inspect` so NetEdit computes and then displays
the final junction surfaces. Blue is the selected junction, red is an
unselected junction, and black is an external edge or lane. This visual stage
does not replace routeability, surface, movement, or signal-semantic gates.

After the road network has been checked and frozen, traffic reconstruction
can use the existing signal, count, and demand commands in a separate output
directory:

```powershell
torii hamburg bind-aerial-signals <request.json> <output-dir> --json
torii hamburg bind-aerial-counts <request.json> <output-dir> --json
torii hamburg generate-aerial-demand <request.json> <output-dir> --json
torii hamburg build-protected-signals <request.json> <output-dir> --json
```

These commands are not stages of `build-network`. Bind all later evidence to
the exact checked network hash and matching `candidate/manifest.json`.
They retain their existing request formats and do not accept the handoff file
directly. A road change requires new road checks and new affected bindings. See the
[count-calibration reference](../plugins/torii-sumo/skills/simulation-helper-skill-for-eclipse-sumo/references/hamburg-count-calibration-workflow.md).

The signal-binding command accepts only exact physical movement identities
and reports frozen MAP/TLD version mismatches. It does not infer timing. The
count-binding command reads Hamburg's published directional `Zählstelle`
compositions, then admits only their named `Zählfeld` members. Direction-0
stations are QA-only. Unlisted fields, missing station compositions, split-edge
groups, and nearest-lane fallbacks remain review-only. A `Z.*` number is never
treated as a MAP or SUMO lane number.

The demand command consumes only active count bindings and complete edge
cross-sections. It writes virtual detectors, edge constraints, routeSampler
evidence, and one plausible route file. A zero routeSampler mismatch still
requires a separate SUMO completion, teleport, collision, and backlog audit.

- `sumo_hamburg_sandtorkai_digital_twin`
- `sumo_hamburg_named_count_scope`
- `sumo_hamburg_sandtorkai_signal_observations`
- `sumo_hamburg_sandtorkai_named_replay`
- `sumo_hamburg_sandtorkai_execution_plan` (keeps network-independent W3a
  count acquisition separate from W1-bound W3b detector binding, verifies the
  W2/W3b/W4 network SHA-256 against W1, validates W4's W2/W3a/W3b manifest identities,
  derives the W5 capability summary automatically,
  and accepts optional hash-bound `stage_feedback` for diagnostic re-planning;
  feedback cannot promote a blocked stage)
- `sumo_hamburg_2394_archetype_classify`
- `sumo_hamburg_2394_compound_geometry_first_pass`
- `sumo_hamburg_2394_tls_topology_materialize`
- `sumo_hamburg_sandtorkai_corridor_geometry_materialize`
- `sumo_hamburg_sandtorkai_mainline_scope_materialize` (profiles include the
  compact mainline and the entry-flow scope with Großer Grasbrook and
  Singapurstraße)
- `sumo_hamburg_sandtorkai_geometry_safe_digital_twin`
- `sumo_hamburg_sandtorkai_corridor_tls_materialize`
- `sumo_hamburg_cached_detector_demand`
- `sumo_hamburg_corridor_candidate_detector_demand`
- `sumo_hamburg_corridor_candidate_map_bindings`
- `sumo_hamburg_corridor_candidate_signal_bindings`
- `sumo_hamburg_sandtorkai_corridor_candidate_package`
- `sumo_hamburg_official_tls_rebuild`
- `sumo_digital_twin_replay_validate`

The default detector audit retains the `nVehEntered` count definition and
reports it as `measurement_attribute`. Missing detector intervals remain empty
in the comparison and require review. Error metrics use matched intervals only;
an empty comparison has no error estimate.

Route sampling requires new output files for the selected directory and prefix.
Existing candidate, demand, mismatch, or command files are rejected before
execution. The run records input identities before execution and requires both
demand and mismatch outputs. Reuse a directory only with a new prefix, or choose
a new directory.

This group binds official Hamburg MAP, OCIT, count, detector, candidate, and replay evidence to a fixed corridor scope. Before the corridor TLS materializer may write a lane connection, it requires a hash-bound road-arm/SUMO binding for the exact candidate source network; every planned lane transition must be covered by exactly one ready intent and retain MAP geometry, official movement/control, and SUMO owner/link-index evidence. The geometry-safe package therefore stops after geometry when that candidate-specific binding is absent. Hamburg-specific orchestration stays separate from generic network and detector primitives.

`sumo_hamburg_sandtorkai_named_replay` is the reusable W4 materializer: it
consumes the hash-bound W2 signal/events, W3a count values, and W3b detector
mapping; shared-lane aggregation must already be approved. It then combines
same-location virtual sensors, routeSampler, SUMO, and the real-vs-virtual E1
audit under one automatic teleport/collision and completeness gate.

`sumo_hamburg_sandtorkai_signal_observations` is the preceding signal-history
stage: it consumes the hash-bound W2 primary-stream bindings, queries the
official v1.0 primary observation service for an explicit UTC window, writes
time-zero and in-window TLS link events, and blocks on partial/empty streams or
missing required nodes. The v1.1 endpoint may still be used for metadata
discovery, but is not silently substituted for v1.0 historical primary states.

## Maintenance Contract

The legacy catalog is checked against `plugins/torii-sumo/src/torii_sumo/legacy_tools.py`.
The default and NetEdit profiles are checked through `create_server` contract tests. When registering or removing a tool:

1. place reusable logic below the MCP adapter boundary;
2. update the appropriate group above;
3. update skill routing if the capability is user-visible;
4. add a contract or regression test;
5. state the tool's claim boundary in its description and output.
