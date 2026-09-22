# Torii Tool Routing

Use this reference when the installed `torii-sumo` plugin is available, when the user asks Codex to run SUMO checks through tools, or when SUMO outputs need interpretation before a code or modeling change.

## Role Split

- The skill is the reasoning layer: classify intent, choose the workflow, interpret feedback, and bound the claim.
- The MCP server is the execution layer: run bounded checks and return structured observations.
- MCP tool output is observation, not final interpretation.

## Host-Selected Scenarios

Read `torii workflows --json`, then inspect the selected entry with
`torii workflows --scenario ID --json`. The host model chooses from the
user's objective, source authority, target year, and available inputs.
Executable IDs, signatures, and references come from `core/workflow_catalog.py`.
Do not copy its mapping into a second tool table.

Write the four selection fields: `user_request`, `scenario_id`, `reason`,
and `arguments`. Use `torii workflow selected selection.json --json` to check
readiness. Add `--execute` to invoke the registered entry when execution is
within the user's task. `ready_not_executed` checks argument requirements and
declared input-file presence, not source contents or completed road work.

An entry may be a `workflow`, `check`, `stage`, or `guidance`. A check or stage
does not establish completion of its larger workflow. Guidance has no callable
entry; read its reference and perform the requested reasoning. The selection
does not call another model or pass through a second keyword classifier.

The legacy `torii_auto_workflow` tool and `run_auto_workflow` also accept
`workflow_selection`. They dispatch that selection before legacy detection.
`inspect-only` and `ask-first` do not execute it. Put the selected function's
inputs inside `workflow_selection.arguments`; do not rely on unrelated outer
legacy arguments. Its request must match the outer `user_request`.
Calls without a selection retain the older regular-expression route for
compatibility. Unknown IDs or extra arguments are blocked without fallback.
Missing required values or input files return `needs_input`.

Read the child's decision and evidence after execution. `executed=true`
does not turn `review_required`, `blocked`, or an incomplete result into success.

## Tool Profiles

Plugin-launched MCP sessions default to the 10-tool `default` profile. The
`legacy` profile is an explicit compatibility mode for the 73 historical tool
names. Use this mapping when the session exposes only the default tools:

| Default tool | Legacy name in this reference |
|---|---|
| `torii.preflight` | `sumo_preflight` |
| `torii.config.inspect` | `sumo_config_pair_preflight` |
| `torii.run.compare` | `sumo_compare_outputs` |
| `torii.place.resolve` | `sumo_osm_resolve_place` |
| `torii.intersection.classify` | `sumo_intersection_archetype_classify` |
| `torii.signal.classify` | `sumo_signal_device_profile_classify` |
| `torii.network.audit` | `sumo_network_topology_audit`, `sumo_network_connection_mode_audit`, and `sumo_network_overlapping_junction_audit` |
| `torii.network.compare` | `sumo_network_connection_mode_regression_audit` |
| `torii.demand.audit` | `sumo_detector_count_audit` |
| `torii.review.create` | `sumo_network_review_html` |
| `torii.netedit.open/observe/act/close` | `sumo_netedit_session` operations |

`torii.network.audit` has `quick` and `standard` profiles. `quick` runs only
the topology check. `standard` also runs Connection Mode and
overlapping-junction checks. Neither profile runs routeability.

Run routeability with:

```powershell
torii network routeability <network.net.xml> <output-dir> --json
```

This CLI command runs the legacy `sumo_network_routeability_audit` capability.

Run an allowlisted long, batch, or specialized legacy capability with:

```powershell
torii workflow <tool> <request.json> --json
```

## Tool Selection

| Situation | Tool | Required interpretation |
|---|---|---|
| One-sentence or ambiguous SUMO request | Read the scenario catalog first | Let the host model select from intent and evidence, then check required inputs before execution |
| Unknown machine, uncertain SUMO install, or missing runnable proof | `sumo_preflight` | Report environment pass/block status before any experiment claim |
| Need raw environment details for handoff | `sumo_get_environment` | Treat versions and missing binaries as construction evidence |
| Existing baseline and variant `.sumocfg` files | `sumo_config_pair_preflight` | Check missing inputs and shared outputs before running or comparing |
| Single `.sumocfg` execution | `sumo_run_config` | Inspect declared outputs before interpreting performance metrics |
| Need a minimal toolchain smoke test | `sumo_run_minimal_smoke` | Label as `diagnostic-demo`, not formal experiment evidence |
| Need to resolve a named OSM area before construction | `sumo_osm_resolve_place` | Return Nominatim/OSM candidate display name, relation/node/way id, bbox, OSM preview link, and confirmation boundary |
| Need full OSM cleanup | CLI: `torii workflow sumo_osm_cleanup_workflow <request.json> --json` | Resolve a place to a bbox first. Use the seven-field request. Reference-matched cleanup is audit-only. Do not present this command as an MCP tool. |
| Need bbox OSM download/reuse, road-class filtering, and SUMO network construction | `sumo_osm_build_network` | Treat tiled Overpass, retry, OSM deduplication, and netconvert output as construction evidence; inspect warnings before accepting the network |
| Need to identify a local OSM junction type before deciding joins, channelization, or signal ownership | `sumo_intersection_archetype_classify` | Use the hash-bound finite composable profile plus physical-cell, topology, and movement evidence; familiar T3/X4/roundabout names are derived aliases, and every network mutation remains blocked |
| Need to classify physical signal heads and non-visual accessibility outputs from a German OCIT-C supply file before binding or controller design | `sumo_signal_device_profile_classify` | Use the source-hash-bound device inventory; keep logical groups, physical heads, visual displays, audible/tactile outputs, runtime state, and control methods separate; preserve unknown symbols/placement and keep automatic binding/control blocked |
| Need an HTML human-review cockpit for a generated or partially edited SUMO network | `sumo_network_review_html` | Treat the HTML as a review/navigation artifact that points to gates, warnings, topology, junction aggregation, and routeability evidence; it does not by itself make the network clean |
| Need NetEdit screenshots or direct mouse/keyboard control | `torii.netedit.open/observe/act/close` | `observe` writes a screenshot and report. Observe again before each action. Use `close(mode=abort)` to discard changes. Use `close(mode=finalize)` only with the latest screenshot SHA-256. |
| Need OSM/netconvert traffic-light cleanup review | `sumo_tls_audit` | Extract TLS candidates, cluster nearby candidates, and create first-pass map review fields |
| Need traffic-light evidence from OSM, public map links, official inventory, signal plans, or field photos | `sumo_tls_multisource_review` | Use the correct regional current-network baseline; use Google Maps where appropriate, Amap/Gaode, Baidu Maps, Tencent Maps, official inventories, signal plans, and field photos for mainland China, and keep every row at `needs_manual_review` until a human confirms it |
| Need a non-destructive redundant-TLS cleanup artifact | `sumo_network_tls_aggregation_variant` | Use a TLS audit report to build a separate `netconvert --tls.discard-loaded --tls.set` review network with one real SUMO TLS junction per physical cluster; do not adopt it without Netedit and map review |
| Need to detect suspicious overlapping top-level junctions before editing | `sumo_network_overlapping_junction_audit` | Keep the network read-only, ignore valid SUMO internal crossing/walkingarea layers, and prioritize groups with TLS, pedestrian/bike interaction, or reference-join support |
| Need a reusable 100% passenger-connected core from an existing `.net.xml` | `sumo_network_connected_core` | Extract the largest passenger component into a `connected-core` network, keep the raw network as audit evidence, and report discarded fragments before routeability claims |
| Need routeability probes for named roads or bridges | `sumo_network_routeability_probe` | Check missing key edges, generated routes, and later SUMO completion before claim escalation |
| Need to prove random passenger routes finish before saying a network is usable | `torii network routeability <network.net.xml> <output-dir> --json` | Generate random passenger routes, run SUMO, parse `summary.xml`/`tripinfo.xml`, auto-extend the horizon when vehicles remain running, and fail rather than overclaim incomplete runs |
| Need to explain high-hierarchy road differences against a reference network | `sumo_network_reference_hierarchy_audit` | Separate over-split corridors, out-of-reference-scope high roads, hierarchy mismatches, protected link/slip-lane cases, and same-name corridor evidence before merge, prune, or downgrade decisions |
| Need to compare reference visual-detail scope before pruning candidate detail roads | `sumo_network_reference_scope_audit` | Compare reference and candidate `highway.*` type counts, flag absent or overrepresented short dead-end detail fragments, and treat the result as review evidence |
| Need a non-destructive physical-junction aggregation review artifact | `sumo_network_junction_aggregation_variant` | Use topology, reference-join, or overlapping-junction audit reports to create a SUMO plain-nodes `<join>` / `<joinExclude>` patch and separate review network; overlap groups are joined only when reference or human review confirms the core nodes |
| Need a non-destructive reference-scope pruning review artifact | `sumo_network_scope_pruning_variant` | Use a reference-scope audit report to create a separate `netconvert --remove-edges.input-file` review network; do not overwrite the source network or adopt it without map/Netedit review |
| Need to execute ready reference-supervised junction repair queue items | `sumo_network_teacher_guided_repair_queue` | Run only queue items against explicit plain node/edge/connection files, replay and normalize the teacher target internal subgraph by default, treat parity failures as failed construction evidence, and keep every output behind Netedit connection-mode review |
| Need a reference-supervised single-junction repair probe | `sumo_network_teacher_guided_junction_variant` | Replay a manual teacher network's lane permissions, movement graph, pedestrian ring, target internal subgraph, and target `tlLogic` onto candidate plain files by default; keep the result at `diagnostic-demo` until Netedit connection-mode review approves it |
| Need generic detector route support from a frozen network and mapping CSV | `sumo_detector_route_support` | Inspect source/sink, route-candidate, and sparse route-detector-incidence manifests; fail when an active detector lacks support |
| Need one generic count-window constraint artifact | `sumo_detector_count_constraints` | Treat the output as the legacy single-window edgeData path; use the Hamburg digital-twin tool for strict multi-interval 15-minute constraints |
| Need a legacy comparison against existing SUMO E1 output | `sumo_detector_count_audit` | Report its `nVehEntered` diagnostic semantics; use strict digital-twin replay for `nVehContrib` and missing-versus-zero handling |
| Need the fixed Am Sandtorkai three-intersection package from official Hamburg data | `sumo_hamburg_sandtorkai_digital_twin` | Enforce the fixed bbox/nodes, complete Saturday two-hour selection, API snapshots, MAP-confirmed mappings, one virtual E1/E2 per `(node, lane)`, `virtual_expected_counts_15min.csv`, audited complete-cross-section routeSampler edgeData, bounded unique-path TLS binding, and partial/block status when any gate is missing |
| Need only the official detector/time-window stage for the named corridor | `sumo_hamburg_named_count_scope` | Declare the LSA/count nodes, select a complete warm-up plus Saturday two-hour window, write normalized 15-minute counts, and keep missing nodes, unknown directions, and lane-binding/OD limits explicit |
| Need official signal history for a fixed UTC replay window | `sumo_hamburg_sandtorkai_signal_observations` | Consume the exact W2 primary bindings, query the official v1.0 primary observation service with bounded lookback/low concurrency, write t=0 and in-window TLS link events, and block on empty/partial streams or missing required nodes |
| Need the detector-constrained SUMO replay after signal history passes | `sumo_hamburg_sandtorkai_named_replay` | Supply execution-ready W2, W3a, and W3b manifests; W4 consumes their hash-bound signal events, count values, and detector mapping, and rejects unresolved shared-lane aggregation semantics |
| Need to resume or inspect the Codex execution plan for the named corridor | `sumo_hamburg_sandtorkai_execution_plan` | Hash-check W0/W1/W2/W3a/W3b/W4 manifests, require W2/W3b/W4 to match W1 and W4 to name the selected W2/W3a/W3b manifests, derive the W5 capability summary, and stop on stale evidence |
| Need to classify Hamburg 2394 before channelization, joins, or TLS binding | `sumo_hamburg_2394_archetype_classify` | Use the official MAP, official OCIT, and frozen SUMO network; preserve the finite composable type, five proposed owners, unresolved physical core, review-only join hints, and blocked automatic authorization |
| Need the accepted Hamburg 2394 geometry-only first pass | `sumo_hamburg_2394_compound_geometry_first_pass` | Require the frozen source file, classification file, both exact SHA-256 values, and the explicitly accepted `classification_id`; apply only the two bounded local joins, keep five owner components, do not restore official TLS, and keep automatic promotion blocked |
| Need to materialize the reviewed Hamburg 2394 channelization and shared controller candidate | `sumo_hamburg_2394_tls_topology_materialize` | Consume joined PlainXML plus hash-bound MAP/OCIT/classification evidence; apply exactly five removals and three additions, bind eight official vehicle movements to six link indices across three signal owners and one `HH_2394` controller, emit an all-red structural placeholder, run SUMO load and network audits, and keep historical timing/promotion blocked |
| Need the geometry-preserving three-node Am Sandtorkai TLS candidate | `sumo_hamburg_sandtorkai_corridor_tls_materialize` | First supply a hash-bound `sumo_intersection_road_sumo_bind` artifact for this exact SUMO source; every MAP/OCIT lane transition must match exactly one ready road-arm intent plus geometry/control/owner evidence. Then keep V10/V4 geometry, demote inherited OSM TLS bindings, bind 0228/2421 paths, retain HH_2394, add only gated transitions, and preserve bounded surface findings |
| Need to repair inherited 0228/2421 rendered lane-surface overlap first | `sumo_hamburg_sandtorkai_corridor_geometry_materialize` | Apply the hash-bound safe profile: join only confirmed sub-groups, protect the 0228 branch pair, trim three oversized junction faces, and run SUMO plus bounded surface audits without overwriting the source |
| Need the complete reusable Hamburg geometry-safe corridor package | `sumo_hamburg_sandtorkai_geometry_safe_digital_twin` | First run to the geometry-only binding hand-off; then rerun with the exact-candidate road-arm/SUMO artifact to permit TLS, MAP/signal, and detector-demand stages. Keep historical replay and promotion gates explicit |
| Need a MAP-to-SUMO contract after that candidate changes geometry | `sumo_hamburg_corridor_candidate_map_bindings` | Reproject all frozen official MAP lanes onto the candidate network, hash the new CSV, and fail if required vehicle lanes are not active; do not reuse pre-materialization nearest-edge assignments |
| Need candidate-compatible primary signal links before replay | `sumo_hamburg_corridor_candidate_signal_bindings` | Bind frozen primary-signal metadata through the reprojected MAP lanes and optional official movement-endpoint link indices to the candidate's three controllers; preserve 18 active + 9 redundant status and keep historical observations separate |
| Need sensors and demand for that review-pending corridor candidate | `sumo_hamburg_corridor_candidate_detector_demand` | Consume the candidate manifest/network plus frozen MAP lane bindings and cached Hamburg counts; emit same-location E1/E2, virtual counts, complete-section edgeData, route support, and routeSampler demand without promoting topology or signal timing |
| Need one reusable candidate handoff for all three review stages | `sumo_hamburg_sandtorkai_corridor_candidate_package` | Run candidate MAP reprojection, primary-signal binding, and detector-demand/routeSampler generation under one manifest while preserving each hash and the blocked topology/history gates |
| Need junction-polygon and external-lane-face overlap evidence beyond SUMO edge warnings | `sumo_network_surface_overlap_audit` | Keep the network read-only; check junction↔junction polygon area and external lane face↔non-owner junction area, exclude expected owner endpoint contact and internal lanes/junctions, preserve the source hash, and treat any finding as a global audit failure |
| Need to judge whether a bounded junction rebuild introduced or removed surface overlap | `sumo_network_surface_overlap_comparison` | Supply distinct baseline/candidate networks and explicit focus junction ids; require zero introduced and zero candidate focus findings, but keep inherited out-of-scope findings and each single-network global status visible |
| Need a generic routeSampler run from explicit candidate and multi-interval count files | `sumo_detector_route_sampler_calibrate` | Preserve script/input/output hashes, command and mismatch evidence; call the result plausible detector-constrained demand, never uniquely identified OD |
| Need final official TLS plus E1/E2 replay validation | `sumo_digital_twin_replay_validate` | Require link-complete routes/TLS bindings, compare exact E1 `nVehContrib` bins with missing distinct from zero, and report summary/tripinfo completion before fit metrics |
| Baseline and variant summary or tripinfo outputs | `sumo_compare_outputs` | Report completion, unfinished vehicles, and teleports before averages |
| Result handoff or reproducibility artifact | `sumo_collect_evidence` | Store raw observations, warnings, claim label, and residual risk |

## Feedback Diagnosis Loop

Use this loop whenever metrics look bad or outputs are incomplete:

```text
user intent
-> observed SUMO outputs, warnings, metrics, and logs
-> diagnose what the metric implies
-> identify likely network, demand, routing, controller, code, or experiment-design issue
-> choose the smallest next change
-> rerun the relevant check
-> report whether the user intent is better satisfied
```

Do not optimize a raw number without explaining the problem it indicates.

The NetEdit session is inspired by the scene/document/object/viewport/edit pattern
used by common Blender, FreeCAD, and Unity editor MCPs, but deliberately omits
caller-exposed arbitrary Python, C#, shell, and raw Win32 execution. Use `object_type` plus
`object_id` on `observe` for persisted SUMO object detail; a connection id is
`from|fromLane|to|toLane`. Declare the source/candidate junction scope during
`open` when a bounded join is planned. Use `undo` for a reversible GUI step or
`abort` after a failed visual hypothesis so the single service session is
released. `finalize` always leaves automatic promotion blocked even when all
machine audits pass.

Examples:

- Low arrived count can mean the horizon is too short, insertion failed, routes are disconnected, or a controller blocked movement. Run `torii network routeability <network.net.xml> <output-dir> --json` before treating a random-route smoke run as usable evidence.
- High waiting time can mean demand is outside the intended scope, phase-lane mapping is wrong, TLS were joined incorrectly, or the controller policy is unsuitable.
- Teleports indicate construction or control feedback requiring lane, route, capacity, conflict, and controller checks.
- `connected-core` means Torii preserved the raw network but routed downstream checks through a netconvert-built largest passenger component with strict connectivity passing.
- `partial-main-component` means strict connectivity still failed after cleanup or no connected core was available; treat it as diagnostic smoke-test material, not an experiment-ready network.
- A controller metric improvement with worse completion is not success; surface the completion regression first.
- A present E1 `nVehContrib="0"` interval is a measured zero; an absent detector/time interval is missing evidence. Never merge these states in a strict replay audit.

## MCP Tool Use Record

When using Torii, report:

```text
user_intent:
tool_called:
input_artifacts:
raw_observation:
feedback_diagnosis:
claim_status:
next_control_action:
residual_risk:
```

## Missing Tool Boundary

If the user asks for full OSM cleanup through MCP, explain that cleanup is
CLI-only. Resolve the place first, then provide the seven-field CLI request.
Do not claim that reference-matched cleanup applies repairs.

The MCP OSM tools resolve places and run bounded construction or audit steps.
The CLI cleanup workflow coordinates the long checks with fixed profiles. It
requires a bbox. Its reference-matched profile reports differences and leaves
all repairs to separate reviewed commands. These checks do not prove signal
timing, phasing, or controller readiness.

The detector tools now cover route support, generic constraints/audit, the fixed Hamburg official-data package, explicit routeSampler execution, and a TLS/E1/E2 replay validator. They still cannot identify a unique true OD matrix from cross-sectional counts, certify a manually accepted low-confidence mapping, classify incidents without an external event record, or validate places outside the fixed Hamburg preset without a separately declared workflow.
