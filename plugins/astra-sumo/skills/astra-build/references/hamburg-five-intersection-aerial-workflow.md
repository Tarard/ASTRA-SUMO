# Hamburg road reconstruction

Both modes use one command: `astra hamburg build-network <request.json> <new-dir> --json` with
`"schema": "torii.hamburg-topology-workflow-request/v1"`. It imports raw OSM into the new directory, builds the
candidate and runs the construction, movement and corridor-direction checks itself. In a repository checkout,
`examples/05_hamburg_topology/` holds request templates (`construction-plan.request.example.json`,
`request.example.json`). Neither mode needs detector counts or historical signal states.

## Read the result

- `workflow.manifest.json`: `stages` with links to their reports (in drawing mode including `road_continuity` and
  `corridor_lane_probes`), `topology_complete` (separate from SUMO load and vehicle passage) and
  `first_failed_stage`.
- `network-handoff.json`: the network file and hash. `construction_decision` keeps `review_required` or `blocked`;
  the file's presence does not mean every check passed.
- MAP/aerial candidate manifest: `official_connection_audit` with required, actual, missing, extra and unresolved
  lists. Keep apart whether the required connections exist, whether the geometry is supported, and whether vehicles
  finish their paths.

## Drawing-led mode (`construction_plan`)

- Bind the drawing PDF and a manually reviewed `topology.json` (`torii.engineering-topology/v1`, with
  `source_plan_sha256`) by hash. You write the topology after reading the drawing: nodes, roads, lane widths and
  uses, and lane-to-lane connections. The command does not interpret a PDF, and MAP/KML, LSA data and aerial images
  are not needed.
- The drawing's lane counts, uses and movements are authoritative. OSM may fill a missing coordinate, name or speed
  only when its `data_year` matches the scenario year or `osm_geometry_valid_for_target_year` declares that year
  with evidence.
- `scenario.target_year` is the user's year, else the drawing's `data_year`; keep `document_date` separate.
- `supporting_maps` lists supplied Google Maps references; a listed URL does not mean its imagery was inspected.
- Keep the topology's `assumptions` and `unresolved` entries. A missing lane use or turn needs more source work,
  not a map guess.
- Continuous lanes between intersections go in `road_runs` (ordered `source_way_ids`, a metric `axis_shape`, and
  `sections` with stable lane keys, widths and permissions; `examples/05_hamburg_topology/continuous-lanes.md` in a
  checkout). An introduced lane must show entry from a supported upstream lane. `source_attribute_overrides`
  remove only obsolete OSM attribute cuts; `tapers` need evidence for their positions.

## MAP/aerial mode

- Keys: `source_osm`, `lsa_identity`, `osm_build`, ordered `intersections` (each with `map_xml`, `map_kml`,
  `aerial_image`, `bbox_epsg25832`, `aerial_year`) and `road_names`; bind every file by SHA-256. The bounds must
  describe the actual, possibly cropped, image. A screenshot without geographic bounds cannot replace it.
- Authority: official MAP/KML for lane and movement identity and intersection parts; licensed aerial imagery to
  review and refine geometry; the fresh OSM import for the surrounding roads, keeping OSM side roads where MAP
  coverage is incomplete. Never derive lane connectivity, turn permissions or signal timing from imagery alone. The
  network gets diagnostic signals, not historical timing.
- Optional `road_reference.topology` and `road_reference.cross_sections` (hash-bound HH-SIB GeoJSON) add a soft
  road-area prior. The cross-sections come from a generalised 2016 survey, not current kerbs.
- Do not copy another area's `context_joins` or connection repairs without physical evidence.
  `construction.junction_contours: "guarded"` tightens junction outlines; read
  `movement_materialization.junction_contours` in the candidate manifest.

## Single steps for diagnosis

1. Through `astra workflow`: `sumo_osm_build_network` (`bbox`, `output_dir`, `source_osm_path`), `sumo_tls_audit`
   (`net_file`, `output_dir`, `osm_file`), then `hamburg_corridor_bind_tls_clusters` (`selection_file`,
   `lsa_identity_file`, `tls_clusters_file` = the audit's `clusters_file`, `net_file`, `output_file`, and an
   `expected_*_sha256` for each input).
2. `astra hamburg aerial-movements <request.json> <new-dir> --json` traces curves between official endpoints and
   keeps the official curve when a trace exceeds its error limit. The official curves feed acceptance and fallback,
   so the result is not an independent check against them. Review each intersection's movement image.
3. `astra hamburg combine-aerial-movements <request.json> <new-dir> --json` builds the candidate through
   `netconvert`.
4. `astra network movement-probes <candidate-manifest.json> <new-dir> --json` drives every declared movement.
5. A reviewed lane-connection patch: `astra hamburg repair-lane-connections <source.net.xml> <new-dir>
   --connection-patch <patch> --patch-sha256 <sha256> --json`. Without `--connection-patch` it applies the old
   LSA119 case repair; never use that on another corridor.

`astra hamburg inspect-road-uses <request.json> <new-dir> --json` compares road-use observations with MAP lane lines
without changing the network.

Drawing checks run as catalog scenarios through `astra workflow selected`: `road_design_review` (`request_file`,
`output_dir`) checks drawing widths against the implemented ReStra/RASt fragments, and `rigid_vehicle_sweep` checks a
rigid vehicle path against road and obstacle polygons. A missing boundary does not prove clearance.

## Stop conditions

- Missing or unresolved required movements, unexplained extra turns, broken internal paths or lost exterior branches
  block a topology-complete claim.
- Geometry uncertainty can keep a diagnostic candidate; it never justifies deleting a confirmed connection.
- Compare the same source and candidate trips before blaming a routeability failure. Do not raise distance
  thresholds to make a result pass.
- Keep this file-heavy work in the CLI; do not pass aerial rasters through MCP.
