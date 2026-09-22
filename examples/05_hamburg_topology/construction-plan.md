# Build from a reviewed construction drawing

Use a construction drawing to determine roads, lane counts, lane uses, and
connections. OSM and supplied Google Maps material support missing details.
Run the same CLI as the MAP/aerial mode:

```powershell
torii hamburg build-network <request.json> <new-output-dir> --json
```

The drawing must first be read and checked into a `topology.json` file.
This mode compiles those explicit decisions into a fresh SUMO network.
It does not automatically understand an arbitrary PDF. MAP/XML/KML, LSA
identities, aerial images, traffic counts, and historical signal states are
not prerequisites for this mode. The original [MAP/aerial mode](README.md)
remains available when `construction_plan` is absent.

## Request and years

Copy [construction-plan.request.example.json](construction-plan.request.example.json)
beside your inputs. Replace every placeholder with an actual path and hash.
The example intentionally has no runnable source files. Its 2013 dates show
how to declare a historical scenario; they do not relabel the project's 2022
Sandtorkai drawing or any current map as 2013 evidence.

| Field | Meaning |
|---|---|
| `construction_plan.path`, `sha256` | Original PDF and its exact SHA-256 |
| `construction_plan.data_year` | Year represented by the drawing |
| `construction_plan.document_date` | Optional actual document date, kept separately from the represented year |
| `construction_plan.document_kind` | `design`; a proposed design is not proof of what was built |
| `construction_plan.topology.path`, `sha256` | Manually reviewed road and connection description |
| `scenario.target_year` | Optional user-selected scenario year; otherwise use the drawing's represented year |
| `source_osm.path`, `sha256`, `data_year` | Supporting OSM snapshot and its represented year, when known |
| `supporting_maps` | Optional supplied references, such as entries with `provider: "google_maps"` |

Keep a source's represented year, document date, and download date separate.
Do not infer a source year from when it was downloaded. A current Google Maps
view does not update a historical scenario. If years differ, keep that
difference visible in the review.

The example leaves `supporting_maps` empty because it supplies no Google Maps
material. When a reference is supplied, record its provider and available
URL or local path, file hash, and data year. Do not invent a URL, capture date,
or claim that a reference was inspected merely because it was listed.

## Reviewed road description

The topology file uses `schema: "torii.engineering-topology/v1"` and
`source_plan_sha256` matching the original PDF. Declare its coordinate system,
for example `crs: "EPSG:25832"`.

| Records | Required reading and fields |
|---|---|
| `nodes` | Junction or road-end `id`, explicit `x` and `y`, and `evidence`; alternatively an explicit `osm_node_id` for a missing position |
| `edges` | Road `id`, `from`, `to`, `evidence`, and an explicit `lanes` list; optional name, speed, shape, or `osm_way_id` |
| `lanes` | `width_m` and explicit `allow`, such as `"passenger bus"` or `"bicycle"`; optional shape, speed, and evidence |
| `connections` | Explicit `from`, `to`, integer `fromLane` and `toLane`; optional shape and evidence |
| `assumptions`, `unresolved` | Keep declared assumptions and missing information in the resulting report |

List lanes in SUMO order: index 0 is the rightmost lane in the direction of
travel. Higher indices are farther left. Record drawing pages, details, or
reading notes in `evidence`. Check each turn against the drawing; do not add
every geometrically possible connection.

An explicit node position takes precedence over OSM. If its position is
missing, an `osm_node_id` may supply it only when the OSM year matches the
target year, or an explicit `osm_geometry_valid_for_target_year` equals that
target year. Put the latter declaration on the affected node or at the
topology root, and explain in `evidence` why the geometry remained valid.
A declaration without supporting reasoning does not establish historical truth.

Map tags cannot override drawing-based lane counts, permissions, or turns.
Missing names or speeds may use the explicitly referenced OSM way. Any
remaining diagnostic assumptions must stay visible. Unknown control or a
diagnostic speed is not recovered historical signal operation or a measured
speed limit.

## Checks and limits

For roads between reviewed intersections, add `road_runs` to the same topology
file. Read [continuous-lanes.md](continuous-lanes.md) for the lane-section fields
and the single-section-to-corridor checks. The same build command expands these
roads before compilation and checks every declared continuous lane path.

- Missing files, mismatched hashes, invalid references, or coordinates without a permitted source prevent a valid construction input.
- Missing lane uses or connections need another drawing review; do not silently replace them with map guesses.
- Keep assumptions and unresolved items even when netconvert and SUMO load succeed.
- Review the generated lane geometry and connections against the drawing. Check complete vehicle paths separately when claiming vehicle passage.

Use a new output directory and retain the source files. Read the workflow
manifest and linked construction reports before using the network. Successful
compilation or loading proves neither drawing completeness nor field accuracy.
Count fitting and historical traffic replay remain separate, requested tasks.
The workflow also runs isolated probes for every declared movement, including
bicycle-only connections. It reports native junction trimming of supplied lane
shapes separately from lane-count and connection reproduction. Neither a
completed probe nor a matching connection set proves exact drawing geometry.
