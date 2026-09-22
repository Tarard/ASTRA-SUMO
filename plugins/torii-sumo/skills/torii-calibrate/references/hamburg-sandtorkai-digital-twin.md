# Hamburg Am Sandtorkai Three-Intersection Digital Twin

Use this reference for the fixed Am Sandtorkai corridor workflow backed by Hamburg's official count and
traffic-light services. Use `detector-constrained-demand-reconstruction.md` for the generic calibration gates.
Do not generalize this preset to all of Hamburg or describe detector-matched routes as observed OD demand.

## Contents

1. Scope and claim boundary
2. Official sources and license
3. Official-first named corridor
4. Legacy OSM diagnostic preset
5. Tool route
6. Complete Saturday two-hour selection
7. Count aggregation and network mapping
8. RouteSampler demand
9. Signal and detector replay
10. Validation gates
11. Evidence package
12. Non-identifiability and public-release boundary
13. Failure feedback

## Scope And Claim Boundary

The workflow constructs a reproducible input and validation package for three physical nodes on the Am
Sandtorkai corridor. The current default is **official-first**: HH-SIB/HVS define the road corridor, Hamburg
MAP XML/KML define lane and movement geometry, OCIT-C defines signal-control semantics, and TLD supplies
runtime observations when available. OSM is optional corroboration or a bounded fallback; it is not the
geometry, lane, movement, or signal authority. The older OSM-first preset below remains only as a legacy
diagnostic path.

The strongest allowed result is a **detector-constrained plausible demand replay** for one declared network,
mapping, Saturday, two-hour window, and completion rule. It is not a unique OD estimate, an individual vehicle
trace, a causal model of every route choice, or a validated model of Hamburg outside the fixed bbox.

## Official Sources And License

Use the REST services and server-provided pagination links. Do not scrape Geoportal presentation pages.

| Source | Stable entry point | Use |
|---|---|---|
| Hamburg SensorThings v1.1 | `https://iot.hamburg.de/v1.1/` | infrared motor-vehicle count datastream metadata and observations |
| Hamburg Traffic Lights Data catalog | [Transparenzportal: Traffic Lights Data Hamburg, current version](https://suche.transparenz.hamburg.de/dataset/traffic-lights-data-hamburg6) | authoritative resource directory and version provenance; do not scrape the presentation page |
| Hamburg Traffic Lights Data v1.0 | `https://tld.iot.hamburg.de/v1.0/` | `primary_signal` datastream metadata and observations, as linked by the current catalog |
| Hamburg Traffic Lights Data v1.1 | `https://tld.iot.hamburg.de/v1.1/` | `signal_program` and `cycle_second` auxiliary datastream metadata and observations |
| Published TLD assets | `https://daten-hamburg.de/tlf_public/` | MAP XML/KML and OCIT-C files |
| Count metadata | [MetaVer: Verkehrsdaten Kfz (Infrarotdetektoren)](https://metaver.de/trefferanzeige?docuuid=2936465E-C045-4F5D-8614-24C3FBB522E2) | provenance, description, update status, and license |
| Signal metadata | [MetaVer: Traffic Lights Data Hamburg](https://metaver.de/trefferanzeige?docuuid=AB32CF78-389A-4579-9C5E-867EF31CA225) | signal semantics, access, assets, and license |

The metadata records declare `Datenlizenz Deutschland Namensnennung 2.0`. Preserve the dataset-specific source
attribution, request URLs, raw page snapshots, download URLs, and SHA-256 hashes. A continuously updated API is
not itself a frozen experiment input; the saved response pages and hashes define the replayable snapshot.

Bootstrap traffic-light downloads from the catalog's public CKAN/ODG API, not by scraping HTML and not by
copying links manually:

```text
https://suche.transparenz.hamburg.de/api/3/action/package_show?id=traffic-lights-data-hamburg6
```

Parse `result.resources` and require exactly one allowlisted HTTPS resource for each required role:
`primary_signal`, `signal_program`, `cycle_second`, `MAP-Dateien`, `OCIT-C Dateien`, and `TLD Usage Guide`.
Save the raw catalog JSON, retrieval time, selected resource ids/URLs, metadata modification time, and SHA-256
before downloading data. Reject missing, duplicate, non-HTTPS, unexpected-host, wrong-layer, or wrong-API-version
resources. A preset URL may be used only when the catalog request itself is unavailable; record that fallback in
the manifest and do not claim it as catalog-verified.

The count query contract is:

```text
entity: Datastreams
serviceName: HH_STA_Verkehrsdaten_Kfz_Infrarotdetektoren
layerName: Anzahl_Kfz_Zaehlfeld_5-Min
node field: properties/knotenName
expand: Thing and Locations
observations: Datastreams(<id>)/Observations ordered by phenomenonTime
```

The signal query contract is split by the endpoint published in the current Transparenzportal resource
directory. Do not silently substitute v1.1 for a failed primary-signal v1.0 request, and do not share cache
entries across API base URLs:

```text
primary entity: https://tld.iot.hamburg.de/v1.0/Datastreams
primary layer: primary_signal
auxiliary entity: https://tld.iot.hamburg.de/v1.1/Datastreams
auxiliary layers: signal_program, cycle_second
serviceName for both: HH_STA_traffic_lights
node field: Thing/properties/trafficLightsID
motor-vehicle filter: Thing/properties/laneType eq 'KFZ'
observations: Datastreams(<id>)/Observations ordered by phenomenonTime
cache identity: normalized API base URL plus stream/query identity
```

Follow only `@iot.nextLink` URLs on the same origin. Block on cross-origin pagination, malformed collections,
duplicate datastream IDs, missing locations, non-integer counts, negative counts, or record/page limits.

## Official-First Named Corridor

Use this preset for the requested Am Sandtorkai corridor:

| Official LSA node | Junction | Center longitude, latitude |
|---|---|---|
| `2349` | Am Sandtorkai / Großer Grasbrook | `9.9932270620, 53.5433207972` |
| `2394` | Am Sandtorkai / Am Sandtorpark | `9.9951328727, 53.5435027113` |
| `2403` | Am Sandtorkai / Osakaallee | `9.9978391600, 53.5440950084` |

Keep the main east-west Am Sandtorkai axis and only the entry arms required by the declared scope: Großer
Grasbrook, Am Sandtorpark, and Osakaallee. Brooktorkai, Singapurstraße, and Tokiostraße may be retained only
where the official link inventory or a declared boundary-entry profile requires them. Do not extend the
corridor merely because a source graph contains adjacent roads.

The source precedence is deterministic:

1. HH-SIB road link, stationing, lane count, speed, and carriageway attributes.
2. HVS membership/classification, joined to HH-SIB by exact official identifiers and geometry hash.
3. MAP XML/KML lane centerlines, stop-line endpoints, and Drive-line movements.
4. OCIT-C signal groups and structural signal semantics.
5. TLD observations for time-dependent signal state.
6. Other Hamburg official fine-mapping or engineering plans as geometry-only evidence when their semantics
   are insufficient for a lane/movement/signal binding.
7. OSM as optional topology corroboration or explicitly declared fallback. It must never override a stronger
   official source.

Generate SUMO PlainXML (`.nod.xml`, `.edg.xml`, `.con.xml`, `.tll.xml`, `.typ.xml`) and compile it with
`netconvert`; never hand-edit the compiled `.net.xml`. The direct single-intersection builder is permitted only
when a frozen composable profile proves exactly one physical conflict core, one SUMO owner, and one controller:

```text
uv run python plugins/torii-sumo/scripts/build_hamburg_official_intersection.py \
  --map-xml <official-MAP.xml> \
  --map-kml <official-MAP.kml> \
  --ocit-c <official-OCIT-C.xml> \
  --node-id <official-node-id> \
  --classification-file <single-core-profile.json> \
  --accepted-classification-id <classification-id> \
  --expected-classification-sha256 <sha256> \
  --output-dir <new-output-directory>
```

An unknown, candidate, compound, or multi-owner profile must fail before any output directory is created.
Always pass expected SHA-256 values in production. The core Python API is
`materialize_hamburg_official_intersection_plainxml(...)`; the CLI is only a stable adapter and must not
duplicate reconstruction logic.

`2349` and `2394` are compound/shared-controller sites and must instead use
`materialize_hamburg_compound_official_tls_candidate(...)` or its
`build_hamburg_compound_official_tls.py` CLI. That path preserves the classified local owners, verifies all
official movement paths, and binds one official controller across multiple SUMO junctions without constructing
a controller-domain hull.

Current evidence boundary for this preset:

- `2349` and `2394` have official MAP/KML/OCIT-C packages, but neither is authorized for the direct single-core
  builder. Their official control domains are bound to a preserved multi-owner corridor topology.
- The eastbound `2349 -> 2394` official lane transition has an HH-SIB-supported `2 -> 3` cut at station 698.
- The reverse transition has one proven continuation but no official `1 -> 2` lane-birth station; abstain from
  inventing the second transition.
- The current official published MAP/OCIT/TLD inventories contain no machine-bindable static package for
  `2403`; keep that component `blocked_missing_official_signal_assets` while still emitting the independently
  valid `2349` and `2394` components.
- All-red programs generated at this stage are structural placeholders, not historical Saturday timing.

## Legacy OSM Diagnostic Preset

The following preset is retained for regression and diagnostic comparison. It is **not** the requested
Großer-Grasbrook-to-Osakaallee corridor and must not be selected by the official-first workflow.

Treat these values as one versioned preset, not a geocoding suggestion:

```text
preset_id: hamburg_sandtorkai_3_intersections
bbox_west_south_east_north: 9.9780,53.5390,10.0005,53.5475
timezone: Europe/Berlin
count_node_ids: 0228, 2421, 2394
signal_node_ids: 228, 2421, 2394
```

| Count node | Official name | Center longitude, latitude |
|---|---|---|
| `0228` | Baumwall / Niederbaumbruecke / U-Bahnhof | `9.9820160581, 53.5442574511` |
| `2421` | Am Sandtorkai / Am Kaiserkai | `9.9850028399, 53.5427011625` |
| `2394` | Am Sandtorkai / Am Sandtorpark | `9.9951328727, 53.5435027113` |

Normalize the official signal node `228` to count/MAP node `0228` only through the declared preset logic. Do not
apply general leading-zero normalization to unrelated identifiers.

If no network is supplied, pass this bbox to the CLI-only
`sumo_osm_cleanup_workflow` standard profile. Do not call cleanup through MCP.
If a frozen `.net.xml` is supplied, preserve its path and hash. Do not silently
rebuild it.

Before joining nodes, rebuilding channelization, or binding official signals, load
`composable-intersection-classification.md`.  Run the 2394 profile through
`sumo_hamburg_2394_archetype_classify` with the official 2394 MAP XML, official
2394 OCIT XML, and frozen source `.net.xml`; one Hamburg controller domain must
not be flattened into one SUMO junction polygon. Keep the returned type and
execution hint separate. The classifier must record all three evidence hashes.
A materializer must then verify the frozen source-network hash, the complete
classification-file hash, and the accepted `classification_id` before applying
only the two listed local join groups; no classifier output authorizes a join by
itself.

After an explicit review accepts that classification, call
`sumo_hamburg_2394_compound_geometry_first_pass` with the frozen
`source_net_file`, the exact classifier `classification_file`, their two
expected SHA-256 values, and the accepted `classification_id`. The
classification artifact already carries the official MAP/OCIT evidence hashes;
the materializer binds that complete artifact byte-for-byte. Its output is a
geometry-only review candidate: official TLS restoration remains `not_run` and
automatic promotion remains `blocked`.

Once that geometry candidate has passed the bounded junction-surface audit,
export its already-joined PlainXML files from the candidate (the materializer
rejects the earlier pre-join staged files):

```text
netconvert --sumo-net-file <geometry-candidate.net.xml> \
  --plain-output-prefix <joined-plain-directory>/source
```

Then call
`sumo_hamburg_2394_tls_topology_materialize`. This second pass applies the
official `delete five / add three` channelization repair, binds the eight
official vehicle movements to six control expressions, and emits one all-red
`HH_2394` placeholder across exactly three signal-bearing owners. The two
passive priority owners remain unbound. The candidate is load-tested in SUMO
and remains review-only: historical Saturday timing is `not_run` and
operational timing is `blocked` until matching observations are available.

For the complete corridor, call
`sumo_hamburg_sandtorkai_corridor_geometry_materialize` first.  This is the
topology-aware geometry profile: it joins only the confirmed 0228 pair groups
and the 2421 signal cluster, preserves the official 0228 branch pair
`199166130`/`243175302`, and applies three frozen simple junction-face
overrides.  It is specifically designed to avoid the self-intersecting
polygon produced by joining all 0228 sub-nodes.  The source SHA, patch SHA,
SUMO load, and bounded surface audit are recorded; the source is never
overwritten.  Its output then becomes the source for
`sumo_hamburg_sandtorkai_corridor_tls_materialize` only after the
geometry-first candidate is accepted **and** a road-arm/SUMO binding has been
generated for that exact candidate source snapshot. First freeze the OSM,
candidate SUMO, HH-SIB, and (when available) HVS inputs with
`sumo_road_semantic_bridge`; then run `sumo_intersection_road_sumo_bind` with
`output_file=<new-binding.json>`. Pass that direct binding file and its exact
SHA-256 to the TLS materializer. It rejects a source-hash mismatch, an
unready/ambiguous arm intent, an uncovered directed edge pair, or a planned
lane connection lacking the four required evidence classes: MAP lane/stop-line,
MAP connector geometry, official movement/control, and SUMO owner/link-index.
It keeps the V10/V4 lane geometry, demotes inherited OSM TLS bindings, selects
the nearest existing source-network transition for each official 0228/2421 MAP
movement, retains the reviewed HH_2394 bindings, and adds only the explicitly
gated source transitions. It emits shared controllers `HH_0228`, `HH_2421`,
and `HH_2394` with all-red structural programs. A bounded surface-overlap
finding remains a real review gate.

When the requested experiment is the mainline corridor shown in the review
figure, call `sumo_hamburg_sandtorkai_mainline_scope_materialize` after the
three-controller TLS candidate exists. This profile is hash-bound to the
accepted V10 source and keeps the explicit 0228 -> 2421 -> 2394 backbone plus
the short `Am Kaiserkai`/`Am Sandtorpark` approaches. It removes the longer
0228 Baumwall branch, keeps only a 7 m boundary stub needed for the HH_0228
MAP link, and joins the two proven 2394 micro-junctions before `--keep-edges`
post-load pruning. The output is a separate review candidate; the source
network and previously generated MAP/sensor files are never overwritten.
Require the manifest gates for three controllers, ordered backbone
connectivity, one passenger component, zero surface-overlap findings, and a
successful SUMO load before opening it in NetEdit. Reproject MAP bindings and
regenerate same-location sensors against this exact candidate before running
the detector-demand stage.

For the narrowed entry-flow corridor, pass
`profile="hamburg_sandtorkai_entry_scope_v1"`. This variant keeps the same
Am Sandtorkai backbone and retains only the named entry approaches
`Am Sandtorpark`, `Großer Grasbrook`, and `Singapurstraße`; `Am Kaiserkai` and
the longer `Baumwall` branch remain outside scope. Its join patch keeps the
two Großer-Grasbrook carriageway conflict cores separate, rather than joining
all four OSM micro-nodes into one polygon.

Then call `sumo_hamburg_corridor_candidate_detector_demand` with the
candidate manifest/network, the three frozen MAP XML files, the hash-bound MAP
lane-binding CSV, and the cached Hamburg count snapshot plus canonical counts.
Generate that candidate binding CSV first with
`sumo_hamburg_corridor_candidate_map_bindings`; it deliberately reprojects all
official MAP lanes onto the candidate rather than reusing a pre-materialization
nearest-edge assignment.
This produces candidate-compatible same-location E1/E2 sensors, virtual
expected counts, complete-section edge constraints, route candidates, and a
routeSampler demand file. It is review evidence and does not promote a
blocked topology or infer historical signal timing. Use `0..7200` simulation
seconds and `1800..7200` as the two-hour comparison period (30-minute warm-up).
Finally, call `sumo_hamburg_corridor_candidate_signal_bindings` with the
reprojected MAP CSV, frozen `signal_streams.csv`, and
`official_movement_physical_endpoints.json`.  The endpoint artifact is used
only to disambiguate a shared-controller path that crosses several controlled
links; it does not invent a link or timing state.  The expected snapshot
currently resolves to 18 active plus 9 same-group redundant links, while its
historical observations are unavailable during the primary API outage.
These three review-stage calls can be run together with
`sumo_hamburg_sandtorkai_corridor_candidate_package` for the next Torii
invocation.

For an intermediate geometry candidate, use Torii's hash-bound background
Netedit reviewer instead of passing the `.net.xml` as a configuration file:

```text
python plugins/torii-sumo/scripts/netedit_background_review.py \
  --net-file <candidate.net.xml> \
  --expected-net-sha256 <sha256> \
  --target-junction-id <exact-owner-id> \
  --out-dir <review-directory> \
  --netedit-binary <netedit>
```

The reviewer loads the network through Netedit's `-s` network option, binds the
view and selection files explicitly, captures Inspect/TLS/Connection modes with
target-window messages plus `PrintWindow`, and records that global keyboard or
mouse input was not used. Long owner identifiers are shortened only in artifact
filenames with a stable hash; the exact identifier remains in the report. The
candidate SHA-256 is rechecked before and after capture, and this visual evidence
never opens the automatic-promotion gate.

## Tool Route

Use the narrowest executor in this order:

1. When the network topology is being reconstructed, call `sumo_hamburg_2394_archetype_classify` first and
   preserve its review-only owner layout and unresolved physical-core fields.
   The tool now requires `map_file`, `ocit_file`, and `source_net_file`.
2. Only after explicit classification acceptance, call
   `sumo_hamburg_2394_compound_geometry_first_pass` with the accepted
   `classification_id` and exact source/classification SHA-256 values. Treat its
   candidate as geometry-first-pass review evidence, not as an official TLS or
   promotion-ready network.
3. Export the joined PlainXML files from that geometry candidate, then call
   `sumo_hamburg_2394_tls_topology_materialize` with the four evidence hashes,
   the accepted classification id, and the joined `plain_source_dir`. Review
   its SUMO load, eight-link/three-owner/one-controller audit, and bounded
   surface comparison. Its all-red program is a structural placeholder only.
4. Call `sumo_hamburg_sandtorkai_corridor_geometry_materialize` on the accepted
   V10/V4 geometry and review its protected-branch/junction-face report.
5. Freeze and classify the accepted geometry candidate's road arms with
   `sumo_road_semantic_bridge` and `sumo_intersection_road_sumo_bind`, writing
   the latter's direct JSON `output_file`. Then call
   `sumo_hamburg_sandtorkai_corridor_tls_materialize` with that binding file
   and SHA-256. It may create the geometry-preserving three-controller corridor
   candidate only when every planned lane connection is covered by exactly one
   ready intent; otherwise stop at the binding review.
6. Call `sumo_hamburg_corridor_candidate_map_bindings` to recompute the
   official MAP-to-SUMO lane contract on that exact candidate.
7. Call `sumo_hamburg_corridor_candidate_detector_demand` to generate
   candidate-compatible E1/E2 sensors, virtual counts, edge constraints, and
   routeSampler demand from frozen official snapshots.
8. Call `sumo_hamburg_corridor_candidate_signal_bindings` after the MAP
   reprojection, passing the official movement-endpoint artifact to bind the
   frozen primary-signal metadata to the candidate's three controllers;
   historical observations remain a separate gate.
9. For a one-call reusable handoff, use
   `sumo_hamburg_sandtorkai_corridor_candidate_package`; after the geometry/TLS
   artifacts are supplied, it runs steps 6–8
   under one output manifest while preserving each stage's hashes and gates.
10. For a fresh, geometry-safe staged build, use
   `sumo_hamburg_sandtorkai_geometry_safe_digital_twin`. Without a binding for
   its exact geometry candidate it intentionally stops after protected geometry
   and emits the required candidate path/SHA; rerun it with the reviewed
   binding file and SHA only after step 5. It then runs the shared
   three-controller TLS candidate and endpoint-aware MAP/signal/detector-demand
   stages in one output tree. Its manifest exposes the 30-minute warm-up and
   keeps historical signal replay and automatic promotion blocked until those
   evidence gates are satisfied.
11. Call `sumo_hamburg_sandtorkai_digital_twin` to obtain or reuse the cleaned network, snapshot official data,
   select the window, build mappings, write E1/E2/TLS artifacts, construct edge constraints, and run
   routeSampler when available.
12. Call `sumo_detector_route_sampler_calibrate` only when rerunning the generic candidate-manifest plus edge-count
   step with explicit inputs or an explicit routeSampler script.
13. Call `sumo_digital_twin_replay_validate` with the generated route file, E1/E2 additions, TLS events, expected
   counts from `virtual_expected_counts_15min.csv`, and frozen network. Do not pass the per-source
   `canonical_counts_15min.csv` to the replay auditor. Treat the package as incomplete until this replay writes
   count and completion evidence.

Do not substitute the legacy single-window `sumo_detector_count_constraints` for the 15-minute multi-interval
edge-count artifact.

## Complete Saturday Two-Hour Selection

Use an explicit `saturday_date` when the experiment declares one. Otherwise inspect up to the eight most recent
completed Saturdays, newest first, and select the first Saturday that contains at least one strict complete
window. Then select the busiest complete two-hour window within that Saturday.

Apply these rules:

- Interpret the requested day in `Europe/Berlin`, then query the corresponding UTC interval.
- Use 300-second source cells and consider a candidate start every 300 seconds.
- Require 24 consecutive source cells per datastream for a 7200-second window.
- Require every declared count datastream to be present in every source cell.
- Score a candidate by summing all count-field values in its 24 cells.
- Block instead of treating a missing observation as zero.

The score ranks corridor activity. It is **not a unique-vehicle total**: one vehicle may cross several nodes, and
multiple lane fields may observe related traffic. Preserve the selected local date, UTC begin/end, score,
completeness ratio, expected/present cell counts, and all failed coverage attempts.

## Count Aggregation And Network Mapping

Aggregate three complete 5-minute observations into each 15-minute field count. For a two-hour window this gives
eight simulation bins per datastream, with simulation time normalized to `0..7200`. Preserve the absolute UTC
source interval beside every relative SUMO interval.

Map each count field in this order:

```text
Hamburg count-field location and node
-> nearest compatible official MAP ingress lane at the same node
-> MAP lane geometry/heading projected into the frozen SUMO network
-> SUMO lane, edge, and lane position
```

An official-MAP-confirmed lane binding may be `active`. A nearest-SUMO-lane fallback must remain `needs_review`
and must not feed calibration as though direction were confirmed. Emit distance, heading error, confidence,
status, and reason for every mapping.

Create one virtual E1/E2 detector for each `(physical node, SUMO lane)`. If several official fields collapse onto
that same lane, sum them within each 15-minute bin and retain their source ids in
`virtual_detector_mapping.csv`. The resulting `virtual_expected_counts_15min.csv` is the only expected-count
input for strict E1 replay comparison.

Keep routeSampler aggregation separate. Its edgeData is edge-level, while Hamburg source fields are lane-level.
Only sum a `(begin, end, physical node, SUMO edge)` bucket when every passenger lane on the edge has a virtual
detector and their longitudinal positions form one cross-section. Record included and excluded edges in
`route_sampler_edge_constraints.csv`; exclude partial-lane sections instead of treating a single lane as the
whole road. Fail if one edge would combine different physical nodes.

## RouteSampler Demand

Require route support for every active detector edge before sampling. Convert the route-candidate manifest to a
SUMO candidate route file and write eight edgeData intervals using `<edge count="...">`.

Invoke Eclipse SUMO `tools/routeSampler.py` with explicit reproducibility parameters:

```text
--edgedata-attribute count
--begin 0
--end 7200
--interval 900
--seed 42
--mismatch-output <file>
```

Resolve routeSampler from an explicit path or `SUMO_HOME`; block if it is unavailable. Preserve the script hash,
command, candidate-manifest hash, candidate-route hash, edgeData hash, output route hash, stdout/stderr status,
and mismatch deficit/overflow summary.

Only a zero mismatch deficit is a routeSampler `pass` and means `detector-constrained-plausible-demand` for the
eligible complete cross-sections. A readable route file with a nonzero deficit remains `partial`. Neither result
establishes that the sampled route volumes are the unique or true OD solution, and partial-lane fields remain
validation targets rather than routeSampler edge constraints.

## Signal And Detector Replay

Download and hash the declared MAP XML/KML and OCIT-C assets for all three nodes. Parse official MAP lanes and
connections, bind official ingress/egress lane pairs to SUMO controlled connections, and record the exact SUMO
TLS ID and `linkIndex`. A direct link may bind at high confidence. Across OSM segmentation, search only a bounded
local lane path and bind at medium confidence only when every candidate path contains the same single controlled
link. Zero-control, multi-control, ambiguous, duplicate, or non-active MAP paths stay `needs_review`; never pick
one arbitrary TLS from a multi-TLS path.

For each active primary-signal stream, fetch the last state before the selected begin time plus all states in the
window. The preceding state initializes simulation time zero. Block or demote the package when a motor-vehicle
connection is unbound or lacks an initial state. Treat flashing, dark, and unknown conversions as non-exact;
never invent a green/red state to fill a gap.

Place one virtual E1 detector per `(physical node, SUMO lane)` at the downstream-most contributing official
field position, with `period=900` and a shared output file. Validate lane existence, edge ownership, lane
position, source membership, and sanitized-ID uniqueness. Compare `virtual_expected_counts_15min.csv` to E1
`nVehContrib`, which counts vehicles that completely pass the loop during the interval. Do not silently replace
it with `nVehEntered`, which also includes vehicles that only touch the detector.

Use E2 lane-area detectors only as queue/occupancy diagnostics. They are not substitutes for official count
fields and do not add independent demand constraints.

## Validation Gates

Pass the final replay only when all applicable gates succeed:

1. The frozen network loads and every generated route is link-complete.
2. Every official count stream has a complete two-hour source window.
3. Every active count field has a MAP-confirmed SUMO lane/edge/position mapping.
4. Every active detector edge has route support.
5. routeSampler writes a readable demand file and mismatch evidence.
6. Every replayed primary signal binds to one SUMO TLS/linkIndex and has a time-zero state.
7. SUMO writes readable E1, E2, summary, and tripinfo outputs.
8. Every expected E1 bin is present. A measured zero is a valid matched value; a missing interval is a missing
   measurement, not zero.
9. Report loaded, inserted, arrived, running, waiting, teleports, collisions, and completion before count-fit or
   travel-time metrics.

Report per-bin and aggregate expected/measured totals, residuals, MAE, RMSE, maximum absolute error, bias, GEH,
routeSampler deficits, and completion. A good detector fit with unfinished vehicles, teleports, collisions, or
collapsed route diversity is not a successful digital-twin replay.

## Evidence Package

Preserve at least:

```text
frozen_network_path_and_sha256:
official_count_request_urls_and_raw_pages:
official_signal_catalog_raw_json_and_sha256:
official_signal_catalog_selected_resources:
official_signal_request_urls_and_raw_pages:
signal_asset_urls_and_sha256:
selected_window_and_coverage_attempts:
canonical_counts_15min:
official_map_lane_to_sumo_bindings:
detector_mapping:
virtual_detector_mapping:
virtual_expected_counts_15min_for_E1_replay:
route_candidate_manifest:
route_detector_incidence:
route_sampler_complete_edge_section_audit:
route_sampler_complete_edge_counts_15min:
route_sampler_command_and_mismatch:
generated_demand_route_file_and_sha256:
e1_additional_and_output:
e2_additional_and_output:
tls_bindings_and_link_events:
detector_comparison:
summary_and_tripinfo_completion:
digital_twin_manifest:
license_and_attribution:
gaps_and_claim_status:
```

Keep run directories immutable or content-addressed. Do not overwrite a prior Saturday snapshot with a newer API
response under the same evidence identity.

## Non-Identifiability And Public-Release Boundary

**Non-identifiability statement:** cross-sectional detector counts do not uniquely identify origins,
destinations, paths, departure micro-timing, or individual vehicles. Many route/OD assignments can produce the
same counts at the three nodes. routeSampler selects one plausible assignment from the supplied candidate route
set. Even an exact E1 match cannot prove the true OD matrix or recover individual trajectories.

Escalating to an OD claim requires independent information such as boundary counts, travel times, trajectories,
surveys, turning counts, route-choice priors, or assignment calibration. State which additional evidence changes
the identifiability boundary.

The official sources contain aggregate traffic observations and published infrastructure metadata, not a license
to infer or publish individual travel behavior. Preserve attribution and source terms. Keep proprietary joins,
private station tables, unpublished operational records, credentials, local absolute paths, and person-level data
out of public artifacts. Anonymize any non-public augmentation even when the official node IDs remain public.

## Failure Feedback

| Symptom | Likely cause | Next action |
|---|---|---|
| no complete Saturday window | missing 5-minute cells or a newly absent stream | inspect coverage attempts; choose a declared Saturday or block |
| mapping remains `needs_review` | MAP lane mismatch, wrong direction, or network geometry drift | review MAP geometry and split/repair the frozen network |
| multiple nodes collapse to one edge | corridor network is too coarse | split the edge or correct node/lane binding before aggregation |
| routeSampler deficit | insufficient route support or conflicting edge constraints | inspect incidence and mismatch; expand plausible routes without detector-entry patches |
| E1 zero but real count positive | route timing, mapping, TLS, or demand underflow | verify exact bin, lane, linkIndex, and completion before adding demand |
| E1 interval missing | detector output/configuration failure | keep status `missing`; repair replay rather than substituting zero |
| good count fit but low completion | insertion backlog, blocked TLS movement, or route collapse | report completion first; repair demand/control and rerun |
| signal state cannot initialize | no preceding observation or wrong stream binding | block exact replay; do not invent the initial state |
