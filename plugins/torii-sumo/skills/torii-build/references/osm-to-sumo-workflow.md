# OSM-to-SUMO Workflow

Use this reference when the user asks Torii to build, clean, audit, compare, or open a SUMO network from OSM or another imported network. This is the fixed OSM-to-SUMO workflow layer. Use `model-osm-detectors.md` only for map/TLS reality baselines, detector alignment, field-data matching, and background visualization details.

The workflow goal is a bounded, inspectable SUMO artifact with explicit evidence. Do not treat `netconvert` success, a clean GUI load, or a nice screenshot as proof that the network is experiment-ready.

## One-Sentence Autopilot Contract

Torii should turn a short user prompt into a bounded diagnostic network build without hiding uncertainty.

Default behavior:

1. Resolve a place to a confirmed bbox before cleanup.
2. Run `sumo_osm_cleanup_workflow` through `torii workflow`, not MCP.
3. Pass only `output_dir`, `bbox`, `profile`, `source_osm_path`, `traffic_layers`, `reference_net_file`, and `timeout_seconds`.
4. Use `standard` with traffic layers. Use `reference_matched` with a reference `.net.xml` and no traffic layers.
5. Treat reference-matched output as audit evidence. Do not apply a repair in the cleanup call.

## Network Planning Gate

Before construction, resolve the traffic layers and road detail. For one-sentence diagnostic builds, use a conservative passenger vehicle core first and report the assumption. For formal experiment candidates or user-requested variants, ask for road detail before building.

Record:

```text
network_plan_status:
traffic_layers: passenger | bicycle | pedestrian | bus
network_detail_target: arterial_core | passenger_vehicle | passenger_plus_service | bicycle | pedestrian | multimodal | reference_matched
reference_target:
reference_artifact: reference_net_file | locate_reference_artifact
primary_network_layer:
auxiliary_modal_layers:
selected_highway_classes:
vehicle_core_highway_classes:
reference_visual_detail_highway_classes:
reference_visual_detail_net_file:
default_routeability_layer: vehicle_core
default_netedit_comparison_layer: reference_visual_detail
service_passenger_policy: sumo_default | allow_vehicle_service | reference_match
validation_gates:
```

Use a small option set rather than silently adding every OSM class:

| Option | Typical use | Risk |
|---|---|---|
| arterial/core roads only | signal-control corridor or clean controller comparison | may omit local access and visual context |
| arterial/core plus next lower road class | finer visual model or routeability test | more junctions, more TLS candidates, harder review |
| residential/service/access roads | high-detail local access study | fragmented topology, noisy TLS candidates, heavier GUI |
| bicycle/pedestrian layers | explicit multimodal study | requires modal review and claim boundary |
| background-only layers | water, buildings, land use, visual context | not routeable network evidence |

## Reference-Matched Construction

If the user asks to match, mimic, compare against, or learn from a reference network or dataset, first locate or request the reference artifact. Do not hardcode a city, repository, or named reference into the plugin.

Require an explicit bbox and a reference `.net.xml`. Do not derive the bbox
inside cleanup. If the user gives a place name, resolve and confirm it first.

Infer two scopes:

- `vehicle_core`: passenger-drivable road classes from the reference. Use this for connectivity, connected-core extraction, routeability, topology audit, and simulation-readiness claims.
- `reference_visual_detail`: all visible OSM `highway.*` classes observed in the reference. Build this as a separate Netedit comparison network when it differs from `vehicle_core`.

Apply `highway.service` passenger permissions only when the reference policy uses them. Never compare a Torii `connected-core` vehicle network against a full-detail manual reference network.

After construction, run reference join, hierarchy, and scope audits on the
candidate `reference_visual_detail` network. The cleanup call must not create,
select, or apply a repair variant.

## OSM Cleanup Hard Gates

1. If the user gives only a place name, resolve it before cleanup. Confirm the returned bbox when the place is ambiguous.
2. Resolve or infer the network plan before construction. If no user intent or reference target identifies traffic layers, block on the network-plan question instead of silently choosing all road types.
3. Build into a fresh output directory. Keep raw, filtered, connected-core, visual-detail, route, report, and review artifacts separate.
4. Run passenger connectivity checks. If small disconnected fragments cause failure, extract the largest passenger `connected-core`, keep the discarded-component report, and rerun strict connectivity on the core.
5. Run routeability audit on the routeability layer with fixed parameters derived from network scale.
6. Run TLS candidate extraction and region-aware map review-link generation by default where supported.
7. For current-network modeling, treat Google Maps TLS review as a hard gate where Google is the appropriate regional baseline. Any unresolved TLS candidate keeps the workflow claim at `construction-invalid` even if construction, routeability, SUMO-GUI, and Netedit artifacts were produced.
8. When TLS audit reports multiple SUMO TLS nodes for one physical cluster, report the finding. Use a separate reviewed command to create a variant.
9. Run topology fragmentation, overlapping junction, modal aggregation, and junction aggregation audits. These are review gates, not automatic proof of a clean network.
10. Generate `workflow_review_html` after construction reaches a final workflow report. The HTML must summarize artifacts, gates, warnings, topology audit, modal review actions, junction aggregation review, TLS review, and routeability evidence.
11. If visual review is needed, open the selected network in a separate NetEdit session.

If any gate is incomplete, keep the claim at `diagnostic-demo`, `construction-invalid`, or `blocked`.

## Road-Class And Modal Detail

Start sparse and add detail only when the target requires it.

1. Keep arterial or study-relevant road classes first.
2. Add lower road classes only when they affect the experimental target, route realism, detector coverage, or requested reference matching.
3. Add residential, service, foot, bicycle, or access roads only with an explicit purpose. They can fragment topology, create low-value junctions, and make traffic-light review noisy.
4. When modal layers are included, require modal audit fields such as `modal_aggregation_decision`, `modal_review_action`, and `junction_aggregation_blocked_by_modal_count` before aggregation claims.
5. Record the exact keep/remove rule, source extract, netconvert command, warnings, and output network path for every variant.

When producing a denser variant, quantify the change:

```text
kept_osm_classes:
added_osm_classes:
osm_way_count_before_after:
sumo_edge_count_before_after:
sumo_lane_count_before_after:
lane_length_before_after:
junction_count_before_after:
routeability_status:
```

## Junction Aggregation Review

When OSM/netconvert creates many close junctions or short edges around one physical intersection, do not immediately edit or join the network. First run dense-junction, overlapping-junction, corridor, modal, and map/reference review audits.

Required summary:

```text
junction_aggregation_candidate_count:
physical_intersection_candidate_count:
physical_intersection_shape_counts:
junction_aggregation_join_candidate_count:
junction_aggregation_needs_map_review_count:
junction_aggregation_do_not_join_count:
junction_aggregation_blocked_by_corridor_count:
junction_aggregation_blocked_by_modal_count:
modal_review_action_counts:
junction_aggregation_candidates_file:
```

Use local topology, source-node matches, named-road/corridor context, modal policy, and map/reference evidence. Do not join by radius alone.

For reference-matched cleanup, prefer reference case mining over manual parameter sweeps. If the reference has `cluster_*` joined junctions, use `sumo_network_reference_join_audit` after building the candidate `reference_visual_detail` network. Treat encoded source-node matches as stronger evidence than spatial proximity.

Torii may produce `*_junction_aggregated.net.xml` or `*_tls_aggregated.net.xml` review variants, but these must not overwrite the source network or be treated as adopted before review. After any review variant, rerun connectivity, routeability, TLS audit, and topology audit before stronger claims.

## HTML Review And Partial-Network Review

HTML review is mandatory for generated OSM-to-SUMO networks because every realistic import leaves some human-review boundary. The review page should expose only actionable items: uncertain junctions, TLS review, routeability evidence, warnings, and artifacts.

When the user wants to audit a partially edited network instead of generating a new OSM network, route to `sumo_network_review_html` rather than rebuilding from OSM.

## Minimal Output Record

```text
target:
study_area_scope:
source_extract:
network_plan_status:
road_class_rule:
network_output:
connected_core_output:
reference_visual_detail_output:
external_signal_audit:
topology_audit:
junction_aggregation_review:
workflow_review_html:
sumo_gui_launch:
netedit_launch:
routeability_audit:
key_results:
out_of_scope_items:
residual_risks:
claim_status:
```
