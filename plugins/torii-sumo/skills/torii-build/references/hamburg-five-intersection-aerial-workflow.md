# Hamburg Road-Network Workflow

Use this reference for Hamburg road construction led by a reviewed construction
drawing or by official MAP/KML and aerial evidence. Both use the same CLI:

```powershell
torii hamburg build-network <request.json> <new-output-dir> --json
```

## Construction-Drawing Mode

When the user provides a drawing as the primary source, use
`construction_plan` in `torii.hamburg-topology-workflow-request/v1`. Bind the
PDF and the manually reviewed `topology.json` by hash. The topology file uses
`torii.engineering-topology/v1` and binds `source_plan_sha256`. It supplies
explicit nodes, roads, lane widths and uses, and lane-to-lane connections.
Read the drawing before preparing that description. This mode is not an
automatic arbitrary-PDF interpreter and does not require MAP/KML, LSA data,
or an aerial image.

Preserve drawing-based lane counts, uses, and allowed movements. OSM may fill
missing coordinates, road names, or speeds. Explicit drawing coordinates
remain primary. For an OSM position, its `data_year` must match the scenario
year, or `osm_geometry_valid_for_target_year` on the node or topology must
declare that year and provide supporting evidence. Do not treat download
time as data year.

Use `scenario.target_year` when the user supplies a year, otherwise the
drawing's `data_year`. Keep optional `document_date` separate. For example,
a requested 2013 road scenario stays a 2013 scenario even when a newer map
is available. Do not relabel a 2022 drawing as 2013 evidence.

`supporting_maps` may contain supplied Google Maps references. If none was
provided, report that fact. A listed URL does not establish that its imagery
was inspected. Later or undated material remains context unless its relevance
to the target year has an explicit basis.

Retain `assumptions` and `unresolved` from the reviewed topology. Missing lane
uses, unclear turns, or unavailable positions require further source work;
do not silently substitute map guesses. A fresh network and successful SUMO
load do not prove historical field accuracy. Keep geometry, vehicle passage,
and signal-operation conclusions separate.

The following sections describe the existing MAP/aerial mode when
`construction_plan` is absent.

### Continuous Corridor Lanes

Verify local lane connections before extending the reviewed drawing to roads
between intersections. Use `road_runs` in the same topology file and the same
build command. Each run supplies original ordered `source_way_ids`, a metric
`axis_shape`, and complete `sections` with stable lane keys, widths, permissions,
and geometry. Preserve keys when a new lane changes numerical lane indices.
The full input reference is `examples/05_hamburg_topology/continuous-lanes.md`.

The workflow expands continuous roads before compilation. It retains meaningful
OSM boundaries and tests complete external lane sequences afterward. For an
introduced lane, require observed entry from a supported upstream lane. A
vehicle inserted directly on that lane does not establish entry.

Inspect `road_continuity` and `corridor_lane_probes` in the workflow manifest.
Do not equate retained boundary nodes with reconstructed side roads or signals.
Keep missing source interpretation and partial coverage as review items.
Neither fewer SUMO edges nor successful passage proves drawing accuracy.

For natural road geometry, use documented `source_attribute_overrides` to
remove only obsolete attribute cuts. Keep real side roads, controls, and
crossings. Optional `tapers` describe widening before a new full-width section.
Their positions require evidence or an explicit diagnostic assumption. The
new lane opens at the taper end; this does not represent variable lane width.
Inspect native internal paths through the taper and preserve the added-lane
entry check. Do not infer field accuracy from the four synthetic reuse layouts.

## MAP and Aerial Mode

This mode uses raw OSM, official MAP/KML lane and movement data, matching MAP
XML, Hamburg aerial imagery, official LSA identities, and selected road names.
It is CLI-only because it reads large local files, runs SUMO, and writes review
artifacts.

Start with `torii hamburg build-network`. Do not require count observations
or historical signal states. The network receives diagnostic signals for
vehicle checks. This does not reconstruct historical timing.

For image-edge experiments, an intersection can supply hash-bound
`road_reference.topology` and `road_reference.cross_sections` GeoJSON files
from Hamburg HH-SIB. The resulting road-edge stage records contrast candidates
and supplies a soft road-area prior to the movement tracer. Cross-section
polygons are generalized from a 2016 survey. Do not treat their edges as
current surveyed curbs, and do not interpret image contrast as lane permission.
This optional stage retains a review decision. Inspect both the image evidence
and the resulting NetEdit network before judging whether geometry improved.

When the user requests count fitting or demand reconstruction, use
[hamburg-count-calibration-workflow.md](hamburg-count-calibration-workflow.md)
after the road network has been checked and frozen. Do not run calibration
to decide whether road construction passed. Keep extra OSM side roads when
official MAP coverage is incomplete.

## MAP/Aerial Authority Order

1. Use official MAP/KML for lane identity, movement identity, and physical
   intersection parts.
2. Use licensed Hamburg aerial imagery to review and refine geometry.
3. Use the freshly imported OSM network for surrounding roads and preserve
   their documented connections.
4. Do not derive lane connectivity, turn permissions, or signal timing from
   imagery alone.

## Workflow

### 1. Build from Frozen Raw Inputs

```powershell
torii hamburg build-network <request.json> <new-output-dir> --json
```

Use `torii.hamburg-topology-workflow-request/v1`. Supply `source_osm`,
`lsa_identity`, `osm_build`, ordered `intersections`, and `road_names`.
Each intersection needs `map_xml`, `map_kml`, `aerial_image`,
`bbox_epsg25832`, and `aerial_year`. Bind source files by SHA-256.
The aerial bounds must describe the actual image, including its crop.
An arbitrary screenshot without geographic bounds cannot replace this input.

The command imports raw OSM and creates all intermediate files in a new
directory. It does not accept an old source network, binding, or movement
summary. Read `workflow.manifest.json` for each stage and the first failure.
`network-handoff.json` records the resulting network, construction decision,
and workflow manifest with their hashes. Keep its `review_required` or `blocked`
decision visible when a diagnostic candidate needs more work.

Record absolute paths, SHA-256 hashes, coordinate reference systems, aerial
year, SUMO version, seed, vehicle count, and time horizon. Never overwrite a
source file or an earlier candidate directory. Do not copy case-specific
`context_joins` or connection repairs into a new area without physical evidence.

The following commands remain available for diagnosis of individual steps.
For source import, use `torii workflow sumo_osm_build_network`; then use
`sumo_tls_audit` and `hamburg_corridor_bind_tls_clusters` through the workflow
command. Cluster binding accepts `selection_mode=topology_only` and
`ordered_node_ids`; the retained selection name does not limit the node count.

### 2. Select Movement Geometry

```powershell
torii hamburg aerial-movements <request.json> <output-dir> --json
```

The request binds the selected official MAP/KML files and aerial crops by hash.
The command traces a candidate curve between official endpoints. It retains
the official curve when the trace fails its declared error limit. Review the
per-intersection movement image before combination.

The summary must include each `plan_sha256`. Plans retain official ingress
and egress lane identities, lane geometry, and the coordinate reference system.
Full official curves participate in acceptance and fallback. Do not describe
the selected result as independent validation against unused official curves.

### 3. Build a Separate Candidate

```powershell
torii hamburg combine-aerial-movements <request.json> <output-dir> --json
```

The command joins supported physical parts and first binds official lanes to
SUMO lanes. It compares shared geometric coverage instead of penalizing long
SUMO segments outside the official MAP extent. It supplies explicit connections
and complete curves to netconvert so SUMO rebuilds internal paths and conflicts.
Never replace only the first internal lane of a multi-part connection with a
whole movement curve. If curve fitting fails for a confirmed lane pair, keep
the connection with SUMO-generated geometry and record the fallback.

Read `official_connection_audit` as separate required, actual, missing, extra,
and unresolved lists. Check `topology_complete` separately from load and
vehicle-passage results. Preserve unmapped side roads for review. Permit zero
surface findings to remain zero rather than requiring an artificial reduction.

Separate three decisions: whether required lane connections were built,
whether geometry is supported by the source evidence, and whether vehicles
complete the specified paths. Compare the final internal curves with the
selected geometry. Retain MAP, aerial, and SUMO-generated fallback counts.
Successful single-vehicle passage does not verify the chosen curve shape.

Read each approach's rebuilt sections, `not_applicable` records, and reviews.
No documented addition plus matching existing lanes can make an addition
unnecessary. Missing or ambiguous correspondence remains a review item.
Do not count every skipped approach as missing road geometry.

Case-specific reviewed connection patches can run through
`repair-lane-connections --connection-patch <file> --patch-sha256 <sha256>`.
This path validates lane identities and rebuilds through netconvert; it does
not apply the default LSA119 repair. Do not impose a V1 patch on another corridor.

Require the manifest to report the SUMO load result, routeability completion,
collisions, teleports, unfinished vehicles, surface findings, materialized
official movements, rejected movements, parameters, source hashes, and the
candidate hash. A runnable candidate remains `diagnostic-demo`. Keep geometric
uncertainty and missing required movements as `review_required`; neither counts
nor historical signal timing are road-construction acceptance requirements.

### 4. Review the Final Network

```powershell
python plugins/torii-sumo/scripts/netedit_background_review.py `
  --net-file <candidate.net.xml> `
  --expected-net-sha256 <sha256> `
  --target-junction-id <junction-id> `
  --out-dir <review-dir> `
  --neutral-overview
```

The background review uses no global mouse or keyboard input. For Inspect
captures it performs `Connection -> Inspect`: entering Connection mode makes
NetEdit compute junction shapes, then Inspect mode shows the final surface.

Interpret the standard review colors as follows:

- blue: selected junction surface;
- red: unselected junction surface after computation;
- black: external SUMO edge or lane surface;
- white: background or median, not proof of a disconnected route.

The review must preserve the candidate hash. Screenshots support visual
inspection only. They do not replace lane-to-lane, internal-path, vehicle-passage,
collision, teleport, unfinished-vehicle, or surface checks.

## Stop Conditions

Keep incomplete official lane bindings and movement coverage as review items.
Block a topology-complete claim for missing or unresolved required movements,
unexplained extra turns, broken internal paths, or lost exterior branches.
Geometry uncertainty can retain a diagnostic candidate; it does not justify
deleting a confirmed connection. Missing signal timing or count data limits
later replay claims, not topology construction. Keep routeability failures
visible and compare the same source/candidate trips before assigning the cause.
Do not increase distance thresholds simply to make the result pass.

## CLI And MCP Boundary

Keep this file-heavy workflow in the CLI. MCP may discover sources, inspect
small manifests, and explain results. Do not add one MCP tool for every batch
stage or transfer aerial rasters through MCP.
