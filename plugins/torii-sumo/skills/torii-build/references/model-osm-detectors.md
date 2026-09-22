# Model Maps, TLS, Detectors, And Backgrounds

Use this reference for map-reality checks, TLS existence review, detector-to-lane mapping, field-data alignment, and diagnostic background visualization. Use `osm-to-sumo-workflow.md` for the OSM-to-SUMO construction workflow itself.

The goal is construction discipline. A network that looks plausible in `sumo-gui`, Netedit, or an HTML review page is not automatically ready for controller comparison or metric claims.

## Scope Discipline

- Keep each construction variant separate. Do not overwrite a validated baseline network when testing TLS cleanup, detector repair, denser road hierarchy, or background layers.
- Treat GUI inspection, map screenshots, and HTML review as diagnostic evidence only. Formal readiness still needs headless SUMO load, routeability, detector output, and completion checks.
- Keep map/TLS reality review separate from signal timing, phasing, recall, detector actuation, and controller evidence.

## Region-Aware Reality Baseline

Use the external map baseline that is appropriate for the modeled region. OSM can be a useful open construction source, but it is not automatically the ground truth for current road and TLS existence.

For mainland China, do not default to Google Maps as the current-road/TLS baseline. Prefer Amap/Gaode, Baidu Maps, Tencent Maps, official inventories, signal plans, or field photos. Record the WGS84/GCJ-02/BD-09 coordinate assumption before comparing review points with SUMO/OSM coordinates.

For regions where Google Maps is appropriate, unresolved TLS candidates require Google Maps review before a clean-network claim. Treat regional map/TLS reality evidence as a supplement for coordinate-system limits or source conflicts, but it does not remove the hard review status unless the workflow records the corresponding correction evidence.

Before using any map source as the standard, ask whether the user needs the current map or a historical target date. If the user is modeling a past network, the user's stated historical target controls the baseline. Use dated imagery, street-level imagery history, OSM history, or agency signal inventory when available.

Record:

```text
map_baseline_source: Amap/Gaode | Google Maps | official inventory | field evidence | other
regional_baseline_source:
coordinate_system: WGS84 | GCJ-02 | BD-09 | unknown
map_temporal_scope: current | historical | unspecified
map_target_date:
map_review_url:
audit_status:
action:
time_scope_residual_risk:
```

## TLS Reality Review

1. Convert every candidate junction, TLS, or detector point to a stable coordinate record such as `lat,lon`.
2. Cluster close SUMO TLS candidates before review. Many SUMO junction IDs may represent one physical intersection.
3. Verify vehicle signals visually or from public inventory:
   - Keep SUMO TLS where the external source shows a vehicle signal at that intersection.
   - Remove, downgrade, or mark for review SUMO TLS where the source shows no vehicle signal.
   - Be careful with pedestrian crossings, bicycle crossings, tram signals, ramp meters, and nearby separate intersections.
4. Record review fields such as `sumo_node_id`, `lat`, `lon`, road names, external-source URL or inventory ID, `map_temporal_scope`, `map_target_date`, `audit_status`, and action (`keep_tls`, `remove_tls`, `needs_review`).
5. Bound claims: external visual evidence supports a modeling decision. It does not prove signal timing, phasing, recall, detector actuation, or operational control.

When OSM/netconvert creates many traffic lights, force a TLS audit before using the network for signal-control claims:

```text
candidate_tls_count:
clustered_physical_intersections:
external_signal_source:
map_temporal_scope:
map_target_date:
keep_tls:
remove_tls:
downgrade_to_priority_or_uncontrolled:
needs_manual_review:
```

Rules:

- Remove or downgrade SUMO TLS that correspond only to geometry nodes, pedestrian-only crossings, ramp meters outside scope, or map artifacts.
- Keep TLS that correspond to real vehicle-signalized intersections in the modeled scope.
- If a cluster should become one signalized intersection, build a TLS aggregation review variant first; do not hand-edit the source network or compare raw SUMO TLS-node counts against a manual reference as if they were physical signals.
- After TLS removal or rebuilding, rerun routeability smoke, TLS phase audit, and controller mapping checks.
- Do not claim "realistic signal control" from OSM TLS alone. The claim requires source-bounded TLS existence, phase semantics, timing policy, and controller evidence.

## Detector-to-Lane Mapping

Field, paper, or agency detector points often represent directional or lane-specific measurements. Nearest-lane projection is not enough.

1. Preserve the original detector source table as an immutable reference.
2. Generate a separate mapping table with generic fields such as:
   `sensor_id`, `station_id`, `source_location`, `field_lane`, `lane_id`, `edge_id`, `lane_pos`, `match_distance_m`, `mapping_status`.
3. Parse source text for direction, road side, lane side, movement, or station grouping. Use local-language direction phrases only as evidence, not as hard-coded private project assumptions.
4. For same-lane conflicts, expand the repair group to the full detector station or movement group, not only the pair that first collided.
5. Candidate lanes should include nearby passenger lanes within a bounded radius, the current lane for traceability, lane index, edge ID, distance, and lane position.
6. Reject duplicate SUMO `lane_id` within the active detector group unless duplicates are explicitly intended.
7. Same direction: prefer distinct lane indices on the same directed edge.
8. Mixed directions: avoid sharing one directed edge across different directions unless the source justifies it.
9. If strict rules cannot select a unique low-distance assignment, stop with `manual_review` instead of silently accepting a duplicate lane.

## Field-Data Alignment Validation

For aggregate loop or sensor data, the strongest supported claim is usually sensor/time-bin count alignment, not real vehicle identity.

Required chain:

```text
field sensor/time/count record
-> selected mapping row
-> SUMO detector id/lane/position
-> validation route vehicles
-> detector output count
-> comparison row
```

Hard gates:

- Every active source sensor is present in the field data.
- Every active source sensor has exactly the intended SUMO detector.
- Detector ID, lane ID, lane position, and aggregation period match between the mapping table and additional XML.
- Every active sensor has every expected validation interval.
- For every sensor/time-bin, simulated detector count equals the expected validation count in the controlled validation case.
- Repaired same-lane conflict files have zero unresolved rows.
- SUMO summary reports loaded/inserted/arrived consistency and no unexplained running vehicles, collisions, or teleports.

Claim boundary:

- If field data includes sensors outside the modeled study area, report them as out of scope rather than failures.
- Detector entry counts prove count observability. They do not prove real per-vehicle identity unless a separate vehicle-ID audit is run.

## Background Visualization

SUMO road networks do not automatically include rivers, land-use polygons, map tiles, or static imagery.

Options:

1. Prefer a non-destructive polygon or background layer for diagnostic inspection. A road-only extract cannot show water or land-use features by SUMO GUI configuration alone.
2. Generate a polygon additional with `polyconvert` and a documented typemap, then load it alongside the road network in `sumo-gui`.
3. If using GUI background images, decals, or map tiles, save view settings separately from experiment configs.
4. Keep heavy building or land-use layers optional. They may make GUI inspection slow and are usually not needed for formal simulation evidence.
5. State clearly whether the background is static diagnostic visualization or part of the simulation network. Visual polygons are not routeable vehicle edges unless the experiment explicitly models that mode.

## Minimal Output Record

```text
target:
map_baseline_source:
coordinate_system:
map_temporal_scope:
tls_review:
detector_mapping:
field_data_alignment:
optional_background_output:
validation_commands:
key_results:
out_of_scope_items:
residual_risks:
claim_status:
```
