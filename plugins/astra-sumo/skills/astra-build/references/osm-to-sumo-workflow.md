# OSM builds, checks and edits

## Cleanup request

`astra workflow sumo_osm_cleanup_workflow <request.json> --json` takes these keys:

| Key | Value |
|---|---|
| `output_dir` | a new directory |
| `bbox` | `west,south,east,north` in WGS84 degrees |
| `profile` | `standard` (default) or `reference_matched` |
| `traffic_layers` | required for `standard`, not allowed for `reference_matched`: a comma list of `passenger`, `bicycle`, `pedestrian`, `bus` |
| `network_layers` | optional for `standard`, not allowed for `reference_matched`: `vehicle`, `multimodal` or both; each needs its modes in `traffic_layers`; omitted means `multimodal` when `bicycle` or `pedestrian` is requested, plus `vehicle` when a vehicle mode such as `passenger` or `bus` is |
| `reference_net_file` | required for `reference_matched`, not allowed for `standard`: the reference `.net.xml` |
| `source_osm_path` | optional frozen OSM XML; without it the bbox is downloaded from Overpass |
| `timeout_seconds` | default 240; the limit for each netconvert and sumo call |

```json
{"output_dir": "outputs/area-v1", "bbox": "<west>,<south>,<east>,<north>", "profile": "standard", "traffic_layers": "passenger"}
```

- `passenger` or `bus` imports every drivable class, including residential, unclassified and service roads. For a
  one-line request use `passenger` and say so. Add `bicycle` or `pedestrian` only when the study needs them.
- `bicycle` or `pedestrian` adds cycleways, footways, paths and steps and builds one `multimodal` master:
  `--osm.bike-access` for bicycles; `--osm.sidewalks`, `--osm.crossings`, `--crossings.guess` and `--walkingareas` for
  pedestrians; no signal guessing, and the signal groups of a vehicle-road build of the same input. Sidewalks appear
  only where OSM tags them on the road; `sidewalk=separate` roads
  rely on the mapped footways. Bike lanes come only from `cycleway:left`, `cycleway:right` or `cycleway:both`; a plain
  `cycleway=lane` or `cycleway=track` gets none. With a vehicle mode in `traffic_layers`, the `vehicle` layer is cut
  from the master by removing edges, so both layers share edge, lane, junction and signal ids. Crossings at signals
  change the programs netconvert generates.
- To import only some road classes, build with `sumo_osm_build_network` and `highway_classes` (`arterial`, `drive`,
  `drive_plus_unclassified`, `full_vehicle` or a comma list of OSM `highway` values).
- A city-wide bbox from `astra place resolve` is usually far larger than the study area. Narrow it first.
- A whole city needs a far larger `timeout_seconds`: the Dresden city run used 7200 for a 1 GB master `.net.xml`.
  Its master SUMO load check peaked at 7.3 GB, and the result JSON was 855 MB (174 MB for central Dresden). On a
  16 GB machine, `OPENBLAS_NUM_THREADS=1` and a watchdog that stops the run when free commit memory runs low kept
  that run within memory.

## Reading the cleanup result

- `gate_status` holds one value per check: `network_build`, `connectivity`, `topology_audit`, `routeability_audit`,
  `connection_mode_audit`, `tls_reality_audit`, `tls_aggregation`, `network_layers`, `master_tls_cycles`, plus
  `reference_*` gates for `reference_matched`.
- `status: fail` means a broken build or structure. Open review items cap a sound run at `review_required` (exit code
  1); `review_required_gates` lists each such gate with its `reason`.
- `netedit` and `sumo_gui` read `blocked` when the run opened no GUI, and `tls_aggregation` reads `blocked` when it
  wrote a review variant; none of them fails the run. `topology_audit: blocked` (suspicious dense junction clusters)
  is a review item under `standard` and fails the structure under `reference_matched`.
- `network_layers` has one entry per layer with `net_file`, `sha256`, `derived_from_sha256`, `modes`, `edge_count`,
  `removed_edge_count`, `identity` and `gate`. `master_net_file` is the master. The traffic-light audit and
  aggregation run once on the master. The connectivity, topology, routeability, connection-mode and NEMA checks run
  on `passenger_gate_net_file`: the vehicle layer when one was cut and is usable, otherwise the master. `net_file` is
  that network or its connected core. `identity: measured` means the derived layer kept the master's ids,
  permissions, coordinate frame (netOffset and projection) and the signal timeline of every retained link; phase
  indices are per layer. netconvert recomputes junction shapes, lane geometry and lengths, junction types and
  connection right of way around removed edges: `identity.geometry` and `identity.right_of_way` count them without
  gating. A crossing that keeps some of its crossed edges stays the same signal link (`identity.reduced_crossings`).
  netconvert re-plans the program of a controller that lost a whole crossing; a second conversion loads the master
  program back with `--tllogic-files` (`identity.signal_restore`, `measured` when it replaced the first cut).
  Re-planned crossing timelines the restore could not keep set the layer gate to `review_required` with reason
  `crossing_signal_timelines_replanned`, controllers in `identity.replanned_crossing_controllers`. Sidewalk and bike
  lanes on road edges stay in the vehicle layer. The vehicle layer keeps the master's state strings, so its SUMO load
  warns `Unused states in tlLogic` for the indices of removed links; that is expected.
- `network_layers.multimodal.components` gives the largest-component share of the bicycle and pedestrian edges,
  measured without pruning; below 98 % sets the layer gate to `review_required`. `plain_cycleway_tags` counts the
  road ways with a plain `cycleway=lane|track` that got no bike lane. `master_tls_cycles` lists every program's cycle
  and, in `long_cycle_controllers`, those above 120 s; any such controller sets `gate_status.master_tls_cycles` to
  `review_required` (reason `long_signal_cycles`). The master's signal groups come from a vehicle-road build of the
  same input (`build.netconvert_signal_grouping`), so cycleways and paths do not join neighbouring signals; bicycle and
  crossing links can still add phases to a controller, and the vehicle layer inherits them. When the routeability
  audit does not pass,
  `routeability_long_signal_cycles` names the unfinished vehicles whose routes pass such a controller.
- An OSM turn restriction with `except=bicycle` next to a bike lane becomes a bicycle-only connection into a lane
  without bicycles; `connection_mode_audit` lists it as the review finding
  `non_motorized_connection_without_lane_permission`, not as a structural failure.
- The result JSON embeds `connection_mode_audit.junctions`, which grows with the network: 727 of the Dresden city
  run's 855 MB.
- The routeability audit routes with duarouter, which ignores lane-change bans (`changeLeft`/`changeRight` from OSM
  `change:lanes`). An unfinished vehicle can be stuck at a lane end its route cannot legally leave; check the marking
  before treating it as a network fault.
- `build.osm_access_correction` describes the access correction. `decisions` lists each settled way finding with its
  `rule` and `reason`; `review` keeps only open items. `added_class_movements` measures each class a lane gained:
  rows with `status: no_effect` were reverted (`new_class_has_no_loaded_movement`), because the correction creates no
  movement. `unevaluated_outside_gate` counts access tags on ways without `vehicle=no` or `motor_vehicle=no`, which
  the correction does not read. `network_build: review_required` carries reason `osm_access_findings_open` (open
  `review` items, such as time windows without a scenario date) or `newly_allowed_modes_require_connectivity_review`
  (an addition could not be reverted).
- When strict connectivity fails, the run extracts the largest passenger component and checks that instead:
  `connected_core_file`, with the dropped parts in `connected_core_discarded_components_review_file`. The core is a
  second netconvert cut: `--keep-edges.postload` first, then a plain keep-edges cut when that fails
  (`connected_core.fallback_used`, `connected_core.command_attempts`). The report does not say why the first attempt
  failed. On the Dresden city network the postload attempt ran until the 7200 s timeout. The core gets no signal
  identity check. `connectivity: partial` means no strictly connected core was found but the largest passenger
  component covers at least 98 % of passenger edges; treat that result as a diagnostic.
- `claim_tiers.highest_passed_tier` and each tier's `failed_gates` say which checks still block a stronger claim.
- `workflow_review_html_file` is the review page the run writes itself. `warnings` report that review variants were
  created; the TLS variant path is `tls_aggregation_variant_file`. No variant is adopted until reviewed.

## Traffic lights

1. `astra workflow sumo_tls_audit` with `net_file`, `output_dir`, `osm_file`, `google_maps_temporal_scope`
   (`current` or `historical`) and, for `historical`, `google_maps_target_date`. Save the printed JSON to a file.
   Its `clusters_file` CSV has one row per physical cluster with `regional_map_url`: Google Maps, or Amap with
   GCJ-02 coordinates in mainland China.
2. Review each cluster on that map. When official inventories, signal plans or field photos exist, run
   `sumo_tls_multisource_review` with `official_inventory_csv`, `signal_plan_csv` or `field_evidence_csv` (columns
   `tls_id`, `status`, optional `source_id`, `note`). Its `review_file` gives each light an `evidence_level`
   (`authoritative`, `field-observed`, `osm-only`, `sumo-guess-only`); every row stays `needs_manual_review`.
3. Decide each physical cluster: keep, remove or still needs review. Pedestrian-only crossings, ramp meters, tram
   signals and a separate nearby junction are not a vehicle signal at the intersection.
4. Several SUMO lights at one physical intersection: `sumo_network_tls_aggregation_variant` with `net_file`,
   `tls_audit_report_file` (the saved JSON from step 1) and `output_dir` writes `tls_aggregated.net.xml`. Two
   signal nodes that already share a controller: typed edit `demote_duplicate_tls`. A light that does not exist
   has no typed edit; rebuild a candidate with `netconvert --sumo-net-file <net.xml> --tls.unset <node-ids>
   --output-file <candidate.net.xml>` or keep the finding open.

A map review supports existence only. It says nothing about timing, phases, detector actuation or the controller.

## Fragmented junctions

1. `astra network audit <net.xml> <new-dir> --json`. Overlapping groups appear in `findings`; the topology and
   overlap JSON reports are listed in `artifacts`.
2. With a reference network: `sumo_network_reference_join_audit` (`reference_net_file`, `candidate_net_file`,
   `output_dir`). Source-node matches outweigh proximity.
3. `sumo_network_junction_aggregation_variant` with `net_file`, `output_dir` and `topology_audit_report_file`,
   `overlapping_junction_audit_report_file` or `reference_join_audit_report_file`.
4. For one known join, the typed edit `join_junctions` changes less.

Named-road continuity, modal paths and map evidence decide a join.

## Reference-matched builds

Give an explicit bbox and the reference `.net.xml`. The run builds `vehicle_core` (the passenger classes the
reference uses; for connectivity, routeability and topology) and, when it differs, `reference_visual_detail` (every
`highway` class seen in the reference; for visual comparison). Never compare a connected-core vehicle network with a
full-detail reference. The run only audits. To prune over-included detail, run `sumo_network_reference_scope_audit`
(`reference_net_file`, `candidate_net_file`, `output_dir`), then `sumo_network_scope_pruning_variant` (`net_file`,
`reference_scope_report_file` = the audit's `report_file`, `output_dir`).

## Typed edit ledger

`astra network verify-edit <ledger.json> <new-dir> --json` takes exactly one operation:

```json
{
  "schema": "torii.corridor_edit_ledger.v1",
  "source_net_file": "source.net.xml",
  "source_sha256": "<sha256 of source.net.xml>",
  "operations": [{
    "id": "edit-1", "operation": "set_lane_count", "status": "candidate",
    "params": {"edge_id": "<edge>", "num_lanes": 3, "vehicle_class": "passenger"},
    "rationale": "<why>", "evidence": [{"path": "<evidence file>", "sha256": "<sha256>"}],
    "rollback": {"action": "discard_candidate"}
  }]
}
```

| `operation` | required `params` |
|---|---|
| `join_junctions` | `node_ids`, `join_id` (optional `signal_policy`: `preserve` or `rebuild_diagnostic`) |
| `absorb_reciprocal_fragment` | `node_ids`, `join_id`, `maximum_length_m` |
| `set_junction_shape` | `node_id`, `shape` |
| `add_lane_connection`, `delete_lane_connection`, `suppress_turnaround` | `from`, `fromLane`, `to`, `toLane` |
| `relocate_tls_control` | `from_node`, `to_node`, `tls_id`, `link_mapping` |
| `demote_duplicate_tls` | `node_id`, `retained_node_id` |
| `set_lane_permissions` | `lane_id` with `allow` or `disallow` |
| `set_lane_width` | `lane_id`, `width_m` |
| `set_edge_shape` | `edge_id`, `shape` |
| `set_lane_count` | `edge_id`, `num_lanes` (optional `vehicle_class`, `add_side`, `remove_index`, `connections`) |
| `move_stop_line` | `lane_id`, `end_offset_m` |
| `set_speed` | `lane_id`, `speed_m_s` |

The default `--mode explore` builds `candidate_net_file`, reports `measured` or `no_effect`, and warns on a stale
`source_sha256` or a missing approval. Use `--mode release` only when the task asks for a final check: it needs
`status: "accepted"`, matching hashes and evidence.

Road deletion and the other corridor operations (`delete_edge`, `add_edge`, `add_sidewalk`, `add_crossing`,
`add_ramp`, `merge_edges`) go through `sumo_network_corridor_edit_ledger` with `operations` items such as
`{"operation": "delete_edge", "target_ids": ["<edge>"], "status": "accepted", "rationale": "...", "evidence": [...]}`
and `include_auto_proposals: false`. Only `accepted` operations are materialized.

## Synthetic intersections and local patches

Through `astra workflow`: `sumo_intersection_scene_workflow` (`prompt`, `output_dir`) builds an intersection from a
scene description, `sumo_nema_four_way_reference_workflow` (`output_dir`) a NEMA four-way reference, and
`sumo_intersection_clean` (`osm_file`, `output_dir`, optional `seed_osm_node_id`) cleans and compiles one local OSM
patch. None of them reconstructs a real road.

## Background layers

A road import contains no water, buildings or land use, and the cleanup's filtered OSM has no buildings. For a visual
backdrop, download a full OSM extract of the bbox. Write a stub `<net>` file that holds only the network's
`<location>` element, then run `polyconvert --osm-files <full.osm.xml> --net-file <location-stub.net.xml> --type-file
$SUMO_HOME/data/typemap/osmPolyconvert.typ.xml --prune.in-net true --output-file <poly.add.xml>`: the stub gives the
projection and boundary without loading a large network, and `--prune.in-net` drops polygons outside that boundary.
Load the result beside the network in `sumo-gui`. Polygons are display only; keep heavy layers and GUI view settings
out of experiment configurations.

## MATSim networks

ASTRA has no MATSim importer. `netconvert --matsim-files` is lossy: it drops self-loop links and rounds values.
