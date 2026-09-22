# OSM Source Patterns

Use this reference when the user asks to improve OSM import, intelligent cleanup, Overpass robustness, large-area import, historical map construction, OpenDRIVE conversion, or public OSM code reuse.

## Source Map

| Project | Use inside Torii | Borrow | Do not assume |
|---|---|---|---|
| OSMnx | Robust online Overpass acquisition | Overpass subdivision, network presets, rate-limit pause, cache, retry | That Codex should install heavy geospatial dependencies for every user |
| OSMNet | Lightweight OSM graph extraction architecture | road-class filtering, node/way deduplication, cleanup stages | That source code can be vendored |
| pyrosm | Large-region or historical PBF workflow | offline PBF import, bbox filtering, custom filters, city-scale parsing | That Windows dependency installation is trivial |
| SUMO osmGet/osmBuild | SUMO-native import behavior | tiles, Overpass fallback, `netconvert` option profiles, polygon/background flow | That generic OSM library behavior outranks SUMO semantics |
| osm-to-xodr | Staged conversion and metadata audit | multi-stage netconvert, OpenDRIVE export, post-processing records | That it is a SUMO traffic-signal controller |

## Integration Rule

Do not vendor external source code into this plugin. Treat these repositories as source-pattern evidence. Convert useful ideas into small MCP tools, explicit options, tests, and claim boundaries.

Preferred ladder:

1. Keep bbox/source-OSM network building lightweight in `sumo_osm_build_network`.
2. Add OSMnx/OSMNet-inspired Overpass subdivision, retry, deduplication, cache, and highway presets.
3. Add SUMO osmGet/osmBuild-inspired endpoint fallback, tile controls, polygon/background options, and `netconvert` profiles.
4. Add pyrosm-style offline PBF import for large-area, repeated, or historical builds.
5. Add osm-to-xodr-style staged conversion records for OpenDRIVE/export workflows.

## Observed OSM-to-SUMO Cleanup Profiles

These public scripts are useful workflow evidence, but they are not a substitute for TUM-style teacher replay. They mostly automate source preparation, filtering, and `netconvert` profiles; they do not guarantee TUM-equivalent connection, walkingarea, crossing, internal-edge, TLS phase, or `linkIndex` semantics.

| Source | Useful pattern | Torii implication | Boundary |
|---|---|---|---|
| SUMO `osmGet.py` / `osmBuild.py` | SUMO-native OSM download, tiling, polygon/background handling, and option-file driven `netconvert` calls | Treat SUMO's own import stack as the first baseline before inventing another downloader/profile layer | Official defaults are generic OSM import behavior, not Ingolstadt teacher parity |
| sumo-berlin | Geofabrik extract preparation, bbox cropping/filtering, and repeatable scenario build scripting | Add offline-source prep as a stage before the current XML-centric Torii path | Do not feed `.osm.pbf` directly into current `source_osm_path` until a PBF prep/conversion step exists |
| MoSTScenario | Scenario workflow with OSM-like inputs, cleaning stages, and manual/recorded overrides | Preserve a logged transformation chain for every source and cleanup action | A city scenario build recipe is not a reusable proof of junction movement correctness |
| actrys | Shell-level `osmfilter` before `netconvert`, plus explicit import flags for ramps, isolated edges, joined junctions, and guessed TLS | Use these flags as small probe variants for routeability and topology feedback | Do not copy options that remove internal-link semantics, such as `--no-internal-links`, into TUM-style junction repair |

## Google Maps Baseline

Use a region-aware external reality baseline for current OSM/SUMO road and TLS existence audit. Google Maps can support `keep_tls`, `remove_tls`, or `needs_review` decisions where it is reliable and appropriate. For mainland China, prefer Amap/Gaode, Baidu Maps, Tencent Maps, official inventories, signal plans, or field photos, and record WGS84/GCJ-02/BD-09 coordinate-system assumptions.

Always ask whether the audit should use the current map or a historical target date. If the user models a past network, the latest public map is not automatically decisive; use dated imagery, street-level imagery history, OSM history, or agency inventory when available.

Record:

```text
map_baseline_source: Google Maps
map_temporal_scope: current | historical | unspecified
map_target_date:
google_maps_url:
audit_status:
action:
time_scope_residual_risk:
```
