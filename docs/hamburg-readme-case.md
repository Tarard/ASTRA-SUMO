# Hamburg case shown in the README

## Site identity

The two `torii-v1-*` README figures depict the Binnenalster / Ring1 corridor, including LSA118.
The registered five-junction preset contains nodes `104`, `118`, `119`, `200`, and `535`.
Its definition is in [hamburg_corridor_candidate.py](../plugins/astra-sumo/src/astra_sumo/core/hamburg_corridor_candidate.py).
The four displayed station count streams are `35822`, `33023`, `30666`, and `26585`.

This case is separate from the [Am Sandtorkai workflow](hamburg-sandtorkai-codex-execution-workflow.md).
That named corridor uses nodes `2349`, `2394`, and `2403`.
Its existing network fixtures do not establish results for the README case.

## Published count inputs

The official service still publishes the four displayed count streams.
Their metadata identifies processed station totals, with a 15-minute count interval.
The station composition lists constituent detector fields. Those field names are not SUMO lane numbers.
Stations 33023 and 26585 include fields with different published direction labels.
Their observation operator follows the explicit field membership. It does not assume one corridor direction per station.

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

The [frozen source-count package](../benchmarks/hamburg_ring1_count_window_20261020_v1/README.md) covers 2026-10-19 from 05:30 to 08:00 UTC.
It retains 390 five-minute field observations and 40 fifteen-minute station totals.
An independent aggregation check reproduces all 40 published totals from their constituent fields.
This is a source-data check. It is not a SUMO calibration result or a selected fitting/validation split.

### Detector positions and source geometry

The binding tool accepts `map_kml_files` paired with `map_files` in the same order.
Its CLI uses repeated `--map-kml-file` and `--map-file` arguments.
Road-fact snapshots and count bindings use the same explicit KML geometry when available.
The MAP files supply lane identities and direction semantics. The KML files supply geographic polylines.
The association uses the reference heading at the bound endpoint, rather than the chord across a curved lane.
Parallel lanes are assigned within their nearest road arms when a channelized approach spans several roads.

The detector point determines its position on the selected SUMO lane.
Its XML position uses SUMO's logical lane length, which can differ from the saved XY polyline length.
It does not reuse the MAP anchor's position. A native coordinate round-trip regression checks this conversion.
The binding report keeps field-to-reference and reference-to-network distances separate.
These are geometric associations with confidence labels. They do not establish independently surveyed field identity or historical validity.
The [published-point check](../benchmarks/hamburg_ring1_road_scope_20261020_v1/published-field-point-evidence-v1.json) places all 13 points within 0.80 m along the reference from its first anchor.
Their lateral distances are below 2.16 m.
This proximity does not establish surveyed sensor cross-sections. The simulation uses projections of the published points as an explicit placement assumption.
It does not establish that the observations are measured at the stop line or represent discharge counts.

## Signal observation availability

The [2026-10-20 signal screen](../benchmarks/hamburg_ring1_road_scope_20261020_v1/signal-history-screen-v1.json) records the current metadata and five bounded observation queries.
One primary stream at each of 104, 118, and 200 returned an event between 06:00 and 08:00 UTC on 2026-10-19.
The sampled streams at 119 and 535 returned no event in that interval.
Their published motor-stream metadata ends in July 2024 and February 2024 respectively.
Their lane pairs also differ from the frozen current MAP inputs. They are not current signal bindings.
The screen does not establish complete histories, initial states, or availability across every possible stream.
It does not select a fitting window.
The frozen OCIT files contain static signal programs. Their actual use during the count window still needs evidence.
Any use of static programs must remain distinct from same-window signal replay in the report.

The post-exclusion candidate also retains an OSM-derived controller at `6390683126`.
Field `0119-Z.15` binds to lane `603103445#0_0`, immediately upstream of that controller's one controlled link.
Its generated actuated program is separate from `LSA119_part0`. Thus, importing only the named LSA119 program would leave this field under placeholder control.
The official group correspondence for this section remains unresolved. An assumed discharge headway cannot establish its signal group.
Other retained OSM controllers also need an explicit treatment before interpreting a traffic-count fit.

### Source intergreens and the LSA119 collision diagnostic

The program reader now reports `safety_intergreens`: required gaps, measured gaps, release overlap, slack, and unresolved source rows.
The safety matrix is directional. Its reverse entry can have a different requirement, as defined in [OCIT-C section 3.4.18](https://www.ocit.org/media/ocit-c_lsa_versorgungsdaten_v2.0_a01.pdf#page=56).
Seven selected source programs contain 850 matrix entries. Their expanded source timelines satisfy all 850 entries.
These checks do not establish which program operated during the count window.

The [clearance diagnostic](../benchmarks/hamburg_ring1_road_scope_20261020_v1/signal-clearance-evidence-v1.json) identifies two short gaps in the generated LSA119 program.
K2→K6 has 3 s against a required 7 s. K6→K3 has 3 s against a required 4 s.
Only K2, K3, and K6 are assessed in this comparison. Other control correspondences remain unresolved.

All three native trials use the same road geometry, routes, departures, seed, and 0.1 s step.
They stop at 6,000 s. Only the LSA119 program changes.

| Temporary control | Cycle | Collisions / teleports | Arrived by 6,000 s |
|---|---:|---:|---:|
| Original generated program | 90 s | 4 / 4 | 583 |
| Protected diagnostic stages | 155 s | 0 / 0 | 530 |
| Two clearance extensions, with continuing greens retained | 95 s | 0 / 0 | 601 |

The shared clearance function adds 4 s and 1 s after the two existing yellow transitions.
It turns closing yellow links red and preserves greens that continue across each transition.
All original phase records, other programs, geometry, and connections remain unchanged.
The six declared K2/K3/K6 intergreen constraints then hold. This is still a temporary control candidate, not imported field timing.
The scheduled demand contains 4,494 vehicles. These partial trials do not establish their complete arrival or an exact count fit.
Existing protected-green findings and crossings between paths from the same approach still need separate assessment.

The [longer replay](../benchmarks/hamburg_ring1_road_scope_20261020_v1/clearance-timeout-evidence-v1.json) reaches its 900 s wall-clock limit before the requested 14,400 s simulation end.
Its last complete snapshot is at 10,607.0 s: 671 arrivals and 628 running vehicles, all halting, with zero recorded collisions or teleports.
These are partial snapshot counts, not final trip totals. The run remains invalid.

The shared recovery function reads completed E1 records from the truncated output and retains the original file.
It keeps strict checks on count values, identifiers, and duplicate intervals.
This run supplies 85 of 130 field comparisons and 26 of 40 station comparisons. The other entries remain missing.
Seven available field pairs happen to match exactly. They do not establish an accepted fit, and no aggregate fit metric is reported.
The record also freezes the failed [4,494-vehicle demand proposal](../benchmarks/hamburg_ring1_road_scope_20261020_v1/lsa119-stress-demand-v1.rou.xml.gz) for independent checks.
That demand is a diagnostic input, not calibrated traffic.

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

### Exact count-fit target

The requested calibration target is exact integer agreement at every declared field, station, and 15-minute bin in the fitting window.
Both MAE and maximum absolute count error must equal zero. An average error of 0.15 does not meet this target.
The comparison must use actual SUMO detector output with fixed observations, declared detector sections, count definitions, and time boundaries.
The comparison will also report all 130 field-level fifteen-minute residuals before their aggregation into the 40 station totals.
This prevents errors in different fields or directions from cancelling within a station total.
A station total cannot pass while a declared constituent field has a nonzero residual.
Any initialization period precedes the declared comparison window. Declared first-bin observations remain in the result.
The report must retain every declared station and bin, including residuals that remain nonzero.
It must record demand variables, signal treatment, vehicle insertion, unfinished traffic, collisions, and teleports.
Exact agreement is not achieved or established as feasible for the current reconstructed corridor yet.
If constraints prevent exact agreement, the report must identify the remaining residuals and their causes.
An exact fitting-window result does not establish independent prediction accuracy or exact field geometry.

### Current reconstruction and fitting status

The current construction has independent Windows and Linux replays with identical canonical network and detector XML contents.
The [v7 record](../benchmarks/hamburg_ring1_road_scope_20261020_v1/public-replay-evidence-v7.json) records the repaired model recipe.
This reproducibility result does not reproduce the historical README images or establish count calibration.

The [native count probe](../benchmarks/hamburg_ring1_count_probe_20261021_v1/README.md) runs 500 unfitted vehicles to completion without collision or teleport.
It supplies all 130 field and 40 station comparisons. None of the field intervals matches the source exactly.
Its measured responses admit exact integer source-count vectors, but the first larger native replay is invalid.
An exact response equation does not establish an exact SUMO count fit under changed traffic conditions.

The [entry-context repair](../benchmarks/hamburg_ring1_road_scope_20261020_v1/entry-context-evidence-v1.json) retains real upstream roads before vehicle injection.
The ten-vehicle short-entry reproduction then completes without collision or teleport. All 13 native detector coordinates remain unchanged.
The larger replay still records collisions inside LSA119. Thus, the zero-error target remains unachieved.
The later clearance diagnostic above removes those four collisions within its 6,000 s horizon. Full-window count calibration remains unfinished.
Signal treatment, unresolved movements, and demand plausibility still need work before a valid full-demand fit.

The [entry checks](../benchmarks/hamburg_ring1_entry_probe_20261021_v1/README.md) compare two short entries with declared main-road alternatives.
Each of the four runs inserts and completes 20 passenger vehicles without collision or teleport.
For the residential entry, the main-road alternative reduces mean insertion delay from 153.035 s to 3.800 s.
The service-road alternative extends travel time and remains a candidate.
A separate 5,498-vehicle demand proposal changes 3,628 upstream route sections without changing departure times or measured-road sequences.
It has not completed a full-window replay. Measured-road agreement does not establish lane-level count agreement.
These checks use temporary generated control programs and do not establish an exact count fit or true OD.

The [535 lane-binding check](../benchmarks/hamburg_ring1_lsa535_binding_probe_20261021_v1/README.md) retains a failed bounded candidate.
Its reference completes 1,270 vehicles with one collision and one teleport.
The candidate retains the turnaround but moves it to lane 5 and removes the supplemental lane-4 left turn.
It stops at 6,709.9 s after all 97 running vehicles remain stationary for 300 s. Only 136 vehicles arrive.
All 130 field and 40 station rows remain in its diagnostic tables, including 91 missing field rows and 28 missing station rows.
Formal error metrics remain unavailable for both invalid runs. The candidate is not adopted.
The shared demand contains 667 routes that revisit the same 535 core without repeating a directed road.
This identifies remaining tour candidates, not a proven sole cause of the standstill or a zero-error infeasibility result.

The existing figures remain illustrations of the earlier reconstruction.
Their source networks, calibration inputs, and dated aerial registration need a new reproducible case package before paper use.

## Proposed main-road demonstration

This section describes the agreed demonstration scope.
Road classification and scope selection are implemented in the [offline Ring1 example](../examples/06_hamburg_ring1_demo/README.md).
The complete six-stage workflow is not implemented yet.
The demonstration uses one case configuration, one frozen input package, and one report.
The configuration records the bbox, corridor, road tier, observation window, and signal treatment.

### Current construction and driving

The [latest five-node driving check](../benchmarks/hamburg_ring1_road_scope_20261020_v1/full-movement-driving-evidence-v1.json) also applies the explicit missing-lane correction at 119.
It verifies 92 of the 114 official motor movements on the new scope. All 92 matched movements pass their inlet/internal/outlet probes.

| Node | Verified movements | Official total |
|---|---:|---:|
| 104 | 13 | 14 |
| 118 | 17 | 26 |
| 119 | 16 | 16 |
| 200 | 27 | 39 |
| 535 | 19 | 19 |

The other 22 movements remain unresolved. The report retains their lane reviews, unexplained extra connections, and composed-boundary reviews.
Some unresolved references are bus-only or revocable lanes. Adding ordinary motor lanes is not a general correction for them.
An earlier movement-only conversion left four endpoint gaps after it recalculated automatic contours and restored the ordinary roads.
Contour-preservation mode now fixes existing contours as well as ordinary roads. A four-road native regression reproduces the failure and checks both repaired movements by driving.
The earlier lane-count edit changed one neighboring lane shape by up to 0.91 m. That historical change was unaccepted.
A normal passenger vehicle completes a requested stop on the added lane and arrives without collision or teleport.

All 13 count-field associations are active on this five-node candidate, with no shared lane assignments.
Thirteen E1 loops load in SUMO, and their membership reproduces the four published station groups.
The one-second layout load has no traffic demand. It supplies no count-fit result and does not select the experiment window.
The [shared example command](../examples/06_hamburg_ring1_demo/README.md#continue-through-junctions-and-detectors) now rebuilds construction and detector placement from saved inputs.
The [fresh public replay](../benchmarks/hamburg_ring1_road_scope_20261020_v1/public-replay-evidence-v2.json) reproduces 92 passing movements and 13 loops with the implementation recorded at `e8ea94b`.
Its canonical network and detector XML contents match the independent Linux replay of the same model recipe at `91254d6`.
The earlier [saved-input replay](../benchmarks/hamburg_ring1_road_scope_20261020_v1/public-replay-evidence-v1.json) reused a road-scope input from before the shared source-cut repair.
That input had different centerline precision. The fresh rebuild removes the observed content mismatch without changing the comparison recipe.
Inputs and implementation files stay unchanged during the fresh run. The added motor lane retains passenger and bus access.
The separate static influence check finds possible count influence for 19 unresolved movements and unknown outlets for two more.
The remaining movement has no path in its saved outlet hypotheses. That finding does not establish absence of count influence.
Signal treatment and fitting remain unfinished. The complete six-stage demonstration is not available yet.

The [subsequent complete replay](../benchmarks/hamburg_ring1_road_scope_20261020_v1/public-replay-evidence-v3.json) applies four explicit supplementary-connection exclusions from the case configuration.
It retains 92 passing official movements, 13 field loops, and four station groups. No required boundary pair disappears.
The four exclusions preserve all other loaded movement permissions, ordinary geometry, logical lengths, and junction contours.
They define this model candidate. Missing verified source support does not establish real-world turn prohibitions.
The shared connection-edit repair also restores pre-cleanup precision on 13 lane lengths and two shapes.
The maximum restored shape difference is 0.0057 m. This is separate from the remaining neighboring lane change of up to 0.91 m.
The changed network has one complete Windows replay and differs from the retained v1 content reference.
Its detector XML content is unchanged. The earlier model's cross-platform agreement does not establish it for this new version.

Subsequent independent Linux review reproduced the new network and loop hashes as well.
The pre-exclusion and post-exclusion networks each contain ten protected-green foe findings in the temporary programs.
Those physical conflict relations persist across renumbered link indices. They remain unresolved control findings.
The verifier now compares physical connection identities and separates inherited from new relations without relaxing release checks.
Two source lanes lose their direct motor exit: `368989705_4` and `626677498_0`.
The first binds official ingress lane 1 at 200, whose required movements remain unresolved. Neither lane carries a count loop.
The workflow records terminal lanes and their official identities. It does not establish service by lane changes.

The next 200 diagnostic concerns official egress lanes 16 and 17, each about 14 m long and separated by about 3.2 m.
Both currently fit one SUMO motor lane on `551284796#0`, a roughly 49 m road with a separate bicycle lane.
The [dated aerial reference](../benchmarks/hamburg_ring1_lsa200_aerial_20261020_v1/README.md) supports visual review of this receiving section.
Its tile metadata dates the image to 2026-03-19. The MAP validity date remains unresolved.
A full-road lane addition is a diagnostic candidate. Its source agreement and a shorter merge interpretation still need review.

That diagnostic exposed neighboring road-end displacement during lane-count conversion.
Source-derived contour hints and a narrower geometric edit scope now preserve neighboring roads at junctions without pedestrian permission.
Automatic pedestrian junctions retain their walking-area rebuilding behavior, with remaining neighboring changes reported.
The repair has a failing source regression and a passing repaired regression. A complete current-profile replay is still required.

The first complete replay with that narrower scope failed at junction `9810392490`.
It retained a 0.2 m inlet road while the compiled internal entries followed a temporary native shift, leaving three gaps after restoration.
The failed run remains in `outputs/ring1-public-junction-demo-20261020-v5/`.
Native curve repair now uses the final retained road endpoints. The [short-context regression input](../benchmarks/hamburg_ring1_road_scope_20261020_v1/lane-count-short-context-input.json) preserves this failure case.
The repaired real-input check has zero structural failures and no neighboring shape, length, or width changes.
This result still requires complete corridor driving and detector verification on the new recipe.

Official signal-group names also require their declared traffic class.
At 200, `R6` is explicitly `Kfz`, despite its name. The parser now retains its VT reference for movements 1→16 and 1→17.
This corrects source classification. It does not establish current signal timing or that these movements were available during the count window.

The [repaired complete replay](../benchmarks/hamburg_ring1_road_scope_20261020_v1/public-replay-evidence-v5.json) retains the failed predecessor and its successful repair.
The new run verifies all 92 mapped movements out of 114 and loads 13 field loops in four published station groups.
It has zero structural failures. The lane-count stage preserves neighboring shapes, logical lengths, and widths exactly.
Three local transition checks also complete their requested lane chains for passenger, bus, and bicycle classes without collision or teleport.
The bicycle starts on the local inlet. These checks do not establish upstream bicycle access or capacity.

A native coordinate comparison covers all 13 declared field loops between the previous and repaired models.
Only Z.11 changes its logical position, from 10.763 m to 13.936 m as the saved lane length changes.
Its native XY displacement is 0.00236 m. The other twelve positions are unchanged.
The comparison records 207 automatic controller loops separately. They are outside the declared count-field inventory.
These are model-coordinate checks, not surveyed field correspondence or a count-fit result.
The 22 unresolved movements and ten inherited protected-green foe findings remain explicit.

### Earlier construction diagnostics

A separate prototype constructs LSA535 and LSA119.
It materializes all 19 official motor movements at 535 and all 16 at 119.
All 35 mapped movements pass isolated SUMO probes on the combined prototype.
Each probe verifies its inlet lane, complete internal lane sequence, and first outlet lane.
The earlier missing movement required a second motor lane alongside a source bicycle lane.
The typed lane-count correction retains bicycle access and supplies the official motor connection.
A normal passenger vehicle completes an explicit stop on the new lane and then arrives without collision or teleport.
This requested stop does not establish natural lane allocation or capacity.
The lane-count stage still changes three neighboring lane shapes. Those changes remain measured and unaccepted.
The subsequent movement-only conversion preserves ordinary lane shapes, lengths, and widths exactly in contour-preservation mode.

This prototype comes from the earlier 468-road scope. It is separate from the current 467-road replay.
Its 13 count-field associations are active and have no shared lane assignments.
They use physical field points, explicit paired KML geometry, and SUMO logical positions.
These model-relative associations do not establish independently surveyed identity or final detector placement.

Channelized official approaches can span several source roads.
Lane-order matching now handles each unambiguously selected source road separately, with equal counts and consistent shared-section order.
It does not select a more distant road to obtain a desired lane count.
Two extra 535 connections remain unexplained. The earlier source diagnostic also records nine failures at four outside junctions.
The probes disable autonomous lane changes and do not establish multi-vehicle capacity, field signal timing, or count accuracy.
The [diagnostic record](../benchmarks/hamburg_ring1_road_scope_20261020_v1/junction-diagnostic-evidence-v2.json) records the prototype lineage, results, and remaining limits.
At that stage, the complete junction replay and final sensor mapping were unfinished.

The [new-scope approach replay](../benchmarks/hamburg_ring1_road_scope_20261020_v1/approach-replay-evidence-v1.json) constructs nine physical parts across all five nodes on the 467-road scope.
It records the failed initial approach conversion and its source-derived geometry repairs.
The repaired stage preserves its declared outside geometry, attributes, and connection set.
Two pre-existing movements have no effective permitted vehicle class. Their explicit deletion preserves every remaining movement's loaded permissions.
The resulting intermediate network has zero structural path failures. Its full official movement inventory and driving still need verification.
Those diagnostic runners depend on local outputs. The shared example command now replaces their construction and detector steps.

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
The offline example currently retains 467 of 1,156 ordinary road segments, including tentative official approach context.
The class-only variant retained 367 segments but excluded some official approach fragments.
The current result retains the source parts of 135 tentative motor-lane associations. Fourteen official motor lanes remain unmapped.
Seven associated lanes have zero directed overlap, so their lateral offset remains unavailable.
Its main report exposes geometry and signal changes beside the measured status.
The [official static source package](../benchmarks/hamburg_ring1_official_sources_20261020_v1/README.md) contains all five MAP/KML/OCIT triplets.
Exact XML/KML joins pass for all five nodes. This does not establish SUMO correspondence or a completed junction.
Nearest signal clusters remain grouping proposals. Chained nearby nodes must not become an automatic physical-junction merge.
The remaining work is junction construction, detector correspondence, count fitting, matched figures, and the demonstration report.
The Am Sandtorkai mainline profile supplies an implementation pattern. Its fixed road IDs do not belong to this case.
