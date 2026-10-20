# Hamburg case shown in the README

## Site identity

The two `torii-v1-*` README figures depict the Binnenalster / Ring1 corridor, including LSA118.
The registered five-junction preset contains nodes `104`, `118`, `119`, `200`, and `535`.
Its definition is in [hamburg_corridor_candidate.py](../plugins/astra-sumo/src/astra_sumo/core/hamburg_corridor_candidate.py).
The four displayed directional count streams are `35822`, `33023`, `30666`, and `26585`.

This case is separate from the [Am Sandtorkai workflow](hamburg-sandtorkai-codex-execution-workflow.md).
That named corridor uses nodes `2349`, `2394`, and `2403`.
Its existing network fixtures do not establish results for the README case.

## Published count inputs

The official service still publishes the four displayed count streams.
Their metadata identifies processed directional station totals, with a 15-minute count interval.
The station composition lists constituent detector fields. Those field names are not SUMO lane numbers.

| Stream | Published detector composition |
|---|---|
| [35822](https://iot.hamburg.de/v1.1/Datastreams(35822)) | `0119-Z.13,0119-Z.11,0119-Z.12` |
| [33023](https://iot.hamburg.de/v1.1/Datastreams(33023)) | `0119-Z.5,0119-Z.4,0119-Z.15` |
| [30666](https://iot.hamburg.de/v1.1/Datastreams(30666)) | `0535-Z.6,0535-Z.7,0535-Z.8,0535-Z.5` |
| [26585](https://iot.hamburg.de/v1.1/Datastreams(26585)) | `0535-Z.14,0535-Z.4,0535-Z.9` |

A local snapshot retrieved on 2026-10-20 covers `2026-10-06T00:00:00Z` to `2026-10-20T00:00:00Z`, excluding the latter endpoint.
Each stream contains 1,344 distinct intervals, with no missing grid entries or invalid count values.
The service records each interval's final second as `hh:14:59`, `hh:29:59`, `hh:44:59`, or `hh:59:59`.
These input checks do not establish sensor accuracy, lane correspondence, or calibrated simulation accuracy.
The acquisition range is not a selected fitting or validation split.

The local raw responses, request URLs, retrieval times, and hashes are under
`outputs/hamburg-readme-case-20261020-v1/counts-discovery-v1/`.
The official service has a rolling observation range. A later query can return different coverage or revised records.

## Calibration evidence

The earlier README claimed 0.15 vehicles MAE per 15-minute bin.
No linked raw run or manifest currently supports that number in the repository.
It is therefore removed from the current README while the case is revalidated.

The [dated Ring1 record](development-history/ring1-road-continuity-and-calibration-2026-09-11.md)
reports different sample-window results: 3.125 vehicles per formal 15-minute bin at station 30666, and 2.9 across ten bins.
That record covers one station and four E1 loops. It also records incomplete turn construction and diagnostic signal timing.
Those historical numbers have not been reproduced from the current repository's input package.
They are not substitutes for a new measured before/after result.

A before/after comparison requires the same frozen observations, physical detector sections, vehicle-count definition, and time convention.
The demand fitting method and signal treatment must be recorded for both networks.
Fitting-window error and independent validation error are separate results.
A vehicle can pass several stations, so the sum of station counts is not the number of distinct corridor vehicles.

The existing figures remain illustrations of the earlier reconstruction.
Their source networks, calibration inputs, and dated aerial registration need a new reproducible case package before paper use.

## Proposed main-road demonstration

This section describes the agreed demonstration scope.
Road classification and scope selection are implemented in the [offline Ring1 example](../examples/06_hamburg_ring1_demo/README.md).
The complete six-stage workflow is not implemented yet.
The demonstration uses one case configuration, one frozen input package, and one report.
The configuration records the bbox, corridor, road tier, observation window, and signal treatment.

### Road importance and retained roads

The first version uses three coarse tiers: major roads, collector roads, and local roads.
OSM `highway` tags supply the initial classification.
They describe a road's role and importance in its network, as defined in the [OSM highway documentation](https://wiki.openstreetmap.org/wiki/Key:highway).
Missing classification remains visible in the report.
The tier is separate from SUMO's `priority` attribute, which affects [junction right-of-way rules](https://sumo.dlr.de/docs/Networks/SUMO_Road_Networks.html).

The corridor, observed road sections, necessary connectors, and required entry/exit approaches receive retention reasons before pruning.
Their source road class remains recorded even when the study needs a lower-class road.
For example, the V1 calibration scripts reference OSM way `186821027`, Ernst-Merck-Straße, currently tagged `tertiary`.
Way `186821036`, Glockengießerwall, is tagged `secondary` with `ref=Ring 1`.
These source identities were checked on 2026-10-20. Their classes alone do not decide their use in this study.

Pruning defines the simulation scope. It does not establish that an excluded street is absent in the field.
Cut branches that feed or drain the retained corridor require explicit traffic boundaries.
Required entry-to-exit movements are checked in their driving direction, with path-length changes recorded.
The first version does not impose a universal detour-ratio threshold.
Parking access can remain a declared boundary when its internal driving area is outside the chosen study.

### Six visible stages

| Stage | Demonstration output |
|---|---|
| 1. Original OSM | Full import from the frozen bbox, with the original road inventory |
| 2. Classify and prune | Coarse road tiers, retained connectors, excluded branches, and entry/exit boundaries |
| 3. Consolidate junctions | Actual joined junctions, including an LSA118 detail view and remaining unresolved groups |
| 4. Place virtual sensors | Final-network detectors at the recorded physical sections, with mapping status |
| 5. Calibrate | Demand fitting on one frozen network using the declared observations and signal treatment |
| 6. Compare counts | Observed and simulated 15-minute counts, station residuals, and run validity |

Detector sections are identified before pruning. The detector objects are placed after the lane layout is final.
The main corridor view and the LSA118 detail view each keep equal geographic bounds in their before/after pair.
The comparison uses actual NetEdit screenshots over one registered Google background.
The joined junction is selected in Inspect mode for the after detail.
These figures are part of the core demonstration.
The screenshots retain Google and imagery-provider attribution under the [Google geo guidelines](https://about.google/brand-resource-center/products-and-services/geo-guidelines/).
Declared OSM and official road sources supply the network geometry. The Google background supplies visual context.

The report shows retained road and junction counts, each station's count curve, total count error, and MAE per 15-minute bin.
It also records collisions, teleports, unfinished vehicles, insertion backlog, and the demand variables constrained by the observations.
Same-window count matching is labelled as calibration fit.
It does not establish independent prediction accuracy or geometric accuracy at LSA118.

The frozen package supports an offline presentation and a reproducible rerun.
The report retains original inputs, intermediate networks, selection reasons, and final count outputs.
Internet access is used for source acquisition, before the live demonstration.

Withheld-counter checks and comparisons across pruning levels are separate optional analyses.
They retain the same fitting method and prior and state which observations can affect demand.
They are not prerequisites for producing the core demonstration.

### Existing implementation and missing composition

The repository already contains road-class presets in `core/road_scope.py`,
road-arm classification in `intersection/road_detail.py`, and corridor extraction in `core/corridor_scope.py`.
The Hamburg topology, detector, demand, and NetEdit capture modules provide the later stages.
Road selection now fixes the original interfaces, preserves main-road movements and local turns, and avoids expanding residential repair obligations.
The offline example currently retains 468 of 1,156 ordinary road segments, including tentative official approach context.
The class-only variant retained 367 segments but excluded some official approach fragments.
The current result retains the source parts of 132 tentative motor-lane associations. Seventeen official motor lanes remain unmapped.
Seven associated lanes have zero directed overlap, so their lateral offset remains unavailable.
Its main report exposes geometry and signal changes beside the measured status.
The [official static source package](../benchmarks/hamburg_ring1_official_sources_20261020_v1/README.md) contains all five MAP/KML/OCIT triplets.
Exact XML/KML joins pass for all five nodes. This does not establish SUMO correspondence or a completed junction.
Nearest signal clusters remain grouping proposals. Chained nearby nodes must not become an automatic physical-junction merge.
The remaining work is junction construction, detector correspondence, count fitting, matched figures, and the demonstration report.
The Am Sandtorkai mainline profile supplies an implementation pattern. Its fixed road IDs do not belong to this case.
