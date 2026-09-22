# OSM Source Patterns

This project treats public OSM repositories as source-pattern evidence for Torii. Do not vendor external source code into the plugin. Borrow architecture, option names, and validation ideas only after checking license, dependencies, and Windows install risk.

## Source Map

The legacy `clip_source_ways_to_bbox` option selects complete ways whose
original segments intersect the requested box. It keeps their original node
order, including portions outside the box. It does not cut their geometry or
connect the two sides of a removed excursion. Filter reports record
`bbox_selection_mode=whole_ways_intersecting_bbox` and `geometry_clipped=false`.

An explicit service-road permission policy now creates a separate netconvert
candidate. The workflow uses that candidate for later checks and keeps the
original build file and its recorded identity. External and internal vehicle
permissions, existing connections, external geometry, and signals are checked
before the candidate is accepted.

| Project | Torii role | Pattern to borrow | Boundary |
|---|---|---|---|
| OSMnx | Online Overpass network acquisition | Overpass subdivision, network-type presets, rate-limit pause, cache, retry on API pressure | Keep a lightweight local implementation; do not require heavy geospatial dependencies for the MVP |
| OSMNet | OSM graph extraction design | Road-class filtering, node/way deduplication, graph-oriented cleanup stages | Architecture reference only; do not vendor source code |
| pyrosm | Large-area and historical extract path | offline PBF reading, bbox filtering, custom tag filters, city/country-scale imports | Optional future dependency because geospatial wheels can be fragile on Windows |
| SUMO osmGet/osmBuild | SUMO-native import path | Overpass server fallback, tiles, `netconvert` option sets, polygon/background import flow | Prefer SUMO-native semantics when they differ from generic OSM libraries |
| osm-to-xodr | Conversion and metadata post-processing pattern | multi-stage netconvert, OpenDRIVE export, traffic-sign/signal metadata post-process | Reference for staged conversion/audit; not a controller implementation |

## Integration Ladder

1. Keep the current `sumo_osm_build_network` path lightweight: bbox, tiled Overpass, retry, XML deduplication, highway presets, and `netconvert`.
2. Add OSMnx-inspired Overpass controls next: endpoint fallback, rate-limit/status pause, local query cache, and richer network presets.
3. Add SUMO osmGet/osmBuild-inspired defaults: tile strategy, optional polygon/background generation, and documented `netconvert` option profiles.
4. Add a pyrosm-style offline PBF path for large regions, historical extracts, and repeated city-scale builds.
5. Add osm-to-xodr-style staged conversion records when the user needs OpenDRIVE, CARLA-style maps, or metadata post-processing.

## Observed OSM-to-SUMO Cleanup Profiles

These public scripts are useful workflow evidence, but they are not a substitute for TUM-style teacher replay. They mostly automate source preparation, filtering, and `netconvert` profiles; they do not guarantee TUM-equivalent connection, walkingarea, crossing, internal-edge, TLS phase, or `linkIndex` semantics.

| Source | Useful pattern | Torii implication | Boundary |
|---|---|---|---|
| SUMO `osmGet.py` / `osmBuild.py` | SUMO-native OSM download, tiling, polygon/background handling, and option-file driven `netconvert` calls | Treat SUMO's own import stack as the first baseline before inventing another downloader/profile layer | Official defaults are generic OSM import behavior, not Ingolstadt teacher parity |
| sumo-berlin | Geofabrik extract preparation, bbox cropping/filtering, and repeatable scenario build scripting | Add offline-source prep as a stage before the current XML-centric Torii path | Do not feed `.osm.pbf` directly into current `source_osm_path` until a PBF prep/conversion step exists |
| MoSTScenario | Scenario workflow with OSM-like inputs, cleaning stages, and manual/recorded overrides | Preserve a logged transformation chain for every source and cleanup action | A city scenario build recipe is not a reusable proof of junction movement correctness |
| actrys | Shell-level `osmfilter` before `netconvert`, plus explicit import flags for ramps, isolated edges, joined junctions, and guessed TLS | Use these flags as small probe variants for routeability and topology feedback | Do not copy options that remove internal-link semantics, such as `--no-internal-links`, into TUM-style junction repair |

## Immediate Torii Probe

Keep `vehicle_core` and `reference_visual_detail` as separate profiles. For a candidate bbox, compare only one small junction at a time:

1. Build the current Torii OSM net.
2. Rebuild with one borrowed profile variant.
3. Compare approach-edge preservation, routeability, TLS/linkIndex delta, crossing/walkingarea delta, and NetEdit connection screenshots.
4. Promote an option only if it improves the same target junction without deleting teacher-required approaches or internal semantics.

## Region-Aware Baseline Rule

Use a region-aware external reality baseline for OSM/SUMO network and TLS existence audit. Google Maps can be the baseline where it is reliable and appropriate. For mainland China, use Amap/Gaode, Baidu Maps, Tencent Maps, official inventories, signal plans, or field photos, and record WGS84/GCJ-02/BD-09 coordinate-system assumptions.

Before using any map source to decide `keep_tls`, `remove_tls`, or `needs_review`, record whether the user wants the current map or a historical target date. If the user is modeling a historical network, do not treat the latest public map view as decisive unless imagery, street-level imagery, or inventory evidence matches the study period.

Minimum record:

```text
map_baseline_source:
regional_baseline_source:
coordinate_system: WGS84 | GCJ-02 | BD-09 | unknown
map_temporal_scope: current | historical | unspecified
map_target_date:
map_review_url:
audit_status:
action:
time_scope_residual_risk:
```

## References

- OSMnx: https://github.com/gboeing/osmnx
- OSMNet: https://github.com/UDST/osmnet
- pyrosm: https://github.com/pyrosm/pyrosm
- SUMO osmGet/osmBuild: https://github.com/eclipse-sumo/sumo/tree/main/tools
- sumo-berlin: https://github.com/DLR-TS/sumo-berlin
- MoSTScenario: https://github.com/lcodeca/MoSTScenario
- actrys: https://github.com/vishalmhjn/actrys
- osm-to-xodr: https://github.com/das-rise/osm-to-xodr
