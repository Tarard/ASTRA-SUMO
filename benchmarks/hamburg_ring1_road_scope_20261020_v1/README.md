# Frozen README-area road-scope input

This fixture covers the Binnenalster / Ring1 case shown in the repository README.
It is separate from the Am Sandtorkai fixtures.

## Current status

The current scope has 467 of 1,156 source road segments.
The [fresh public replay](public-replay-evidence-v2.json) verifies 92 of 114 official motor movements and loads 13 field loops.
The observed Windows and Linux runs have identical canonical network and detector XML contents.
The earlier [saved-input replay](public-replay-evidence-v1.json) reused a road-scope network generated before the source-cut repair.
The fresh replay rebuilds that input. Its difference report retains the changed geometry and matching movement permissions and programs.
The added motor lane inherits bus access. Input and implementation hashes remain unchanged during that run.
Twenty-two movements and four supplementary motor connections remain unresolved. No demand fitting or count-accuracy result is available.
The separate static influence diagnostic marks 19 unresolved movements as potentially count-relevant and two outlets as unknown.
It does not justify excluding the remaining movement, establish driving feasibility, or change fitting targets.
Earlier records below keep their original 367-, 468-, and 533-road scopes. They do not replace the current record.

## Source and replay details

- OSM query date: `2026-10-06T00:00:00Z`.
- Retrieval date: 2026-10-20.
- Requested bbox: `9.9815,53.5525,10.0065,53.5598`.
- Native import: SUMO 1.27.1, visual-detail profile, precision 6, TLS join distance 0.
- Whole intersecting OSM ways remain complete. The imported geometry can extend outside the requested bbox.

`source.osm.xml.gz` retains the downloaded historical source.
`source-query.ql` retains the exact query.
`native-import.net.xml.gz` retains the original native import before ASTRA cleanup or scope selection.
`fixture.json` records hashes, build settings, and the declared study anchors.

The import has 1,156 ordinary road segments and nine narrowly repairable stale pedestrian-internal signal references.
SUMO 1.27.1 crashes or stalls during postload pruning on this input. The current implementation uses checked preload extraction.
The raw-native class regression retains 367 segments, including four V1 calibration-road references supplied as study anchors.
The full offline example applies OSM access corrections and protects tentative official approach parts. It retains 467 segments.
`official-approach-anchors-v3.json` records the source associations, their confidence, input identity, and unresolved references.
These data protect 130 source edges. They do not constitute confirmed SUMO correspondence or field geometry acceptance.
These anchors do not establish an exact sensor-to-lane mapping.
The v3 file separates whole-lane coverage from individual-edge support and records the retention basis for each edge.
It preserves low-confidence associations. Eighteen retained edges lack high or confirmed fact support.
The earlier `official-approach-anchors.json` and v2 data remain available.
V3 uses the same explicit KML coordinates for association and measurement, with headings taken at the bound anchor.
The previous v2 association replay retained 468 roads. That version remains recorded separately.
The [regeneration instructions](../../examples/06_hamburg_ring1_demo/README.md#regenerate-the-approach-evidence) start from the frozen source files.
[Regeneration evidence](approach-regeneration-evidence-v2.json) records the current measured input identities and byte comparison.

[Current execution evidence](execution-evidence-v4.json) records the 467-road scope and remaining stages.
[Official approach comparison](official-approach-comparison-v2.json) records source-bound lane-part and coverage preservation.
Earlier evidence files retain their original scope and measurements.
The prior [367-road execution](execution-evidence-v2.json) and [geometry check](main-road-geometry-evidence-v2.json) remain available.
[Earlier signal-scope evidence](signal-scope-evidence-v1.json) records the reporting repair on the 367-road scope.
The earlier [533-road run](execution-evidence.json) and [geometry check](main-road-geometry-evidence.json) remain as superseded evidence.
Other retained approach geometry can change during extraction. Controller removals remain recorded separately.

The fixture tests road selection and input handling. It contains no new traffic-count calibration result.
Run the [offline example](../../examples/06_hamburg_ring1_demo/README.md) to rebuild the source and selected scope, then continue through partial junction reconstruction and detector layout.
[Older two-node diagnostics](junction-diagnostic-evidence-v2.json) record 35 passing isolated movements at 119 and 535.
This prototype comes from the earlier 468-road scope. It has not been replayed on the current 467-road scope.
Its movement-only conversion preserves all ordinary lane shapes, lengths, and widths from its lane-count input.
The earlier lane-count stage still changes three neighboring lane shapes. These changes remain unaccepted.
Thirteen geometric count-field associations are active, with no shared lane assignments.
Two extra 535 connections, the complete junction-stage replay, and final detector placement remain unresolved.
The earlier 34-of-35 diagnostic record remains available.

The [five-node approach replay](approach-replay-evidence-v1.json) uses the current 467-road scope and nine physical parts.
Its first complete-constructor attempt exposed an inherited sidewalk default that shifted a split road's lane indices.
Source-derived import hints remove that extra lane. Exact source cuts also retain their curved vertices instead of a warped replacement.
The repaired approach stage passes its boundary, geometry, attribute, and connection-set checks.
Two pre-existing movements have no traversable vehicle class. Explicit deletions retain all other loaded movement permissions and leave zero structural failures.
Complete official movement construction and driving on this scope remain unfinished. These diagnostic runners still depend on local outputs.

The [full-scope driving record](full-movement-driving-evidence-v1.json) verifies 92 of 114 official motor movements after the 119 lane-count correction.
The 22 unresolved movements remain in the denominator. Bus-only and revocable lanes require their own correspondence and use assumptions.
All 13 count fields have distinct active lane associations. Their E1 loops load, with the four published station compositions retained.
There is no demand fitting or count accuracy result in that layout check.

The [post-exclusion full replay](public-replay-evidence-v3.json) verifies the current four declared connection exclusions.
It retains all 92 passing official movements, 13 field loops, and four station groups.
Every other loaded movement keeps its vehicle permissions. The exclusions preserve ordinary geometry, logical lengths, and junction contours exactly.
The shared restoration repair also retains pre-cleanup precision on 13 lane lengths and two shapes that earlier cleanup rounded.
The maximum restored shape difference is 0.0057 m. All 22 unresolved official movements remain recorded.
These exclusions define the candidate scope. They do not establish field turn prohibitions or calibrated counts.
The [independent replay and control review](public-replay-evidence-v4.json) records matching Linux content hashes for the changed model.
It separates ten inherited protected-green foe findings from new findings, without relaxing release checks.
It also identifies two terminal motor lanes after exclusions, including official ingress lane 1 at 200.
The reporting repair's isolated native replay retains the same network content hash and exact ordinary geometry.

`movement-contour-input.net.xml.gz` freezes four ordinary roads at LSA118 part 1.
`movement-contour-input.json` records the source and the two official movements whose paths failed after automatic contour recomputation.
The native regression now preserves source contours and ordinary geometry, checks internal continuity, and drives both movements.
This verifies the movement-only preservation repair. It does not establish field geometry or signal timing.

`lane-count-input.net.xml.gz` freezes a 12-road conversion context from the unaccepted LSA119 prototype.
`lane-count-input.json` records its identity and the earlier conversion's adjacent geometry changes.
`lane-count-operation.json` adds a motor lane with explicit connections and retains bicycle access.
The native regression verifies source preservation, source-derived geometry hints, loading, and compatible service for the added lane.
It checks that neighboring geometry changes decrease relative to the recorded conversion without hints.
Remaining changes stay measured. This fixture does not establish field geometry, complete junction reconstruction, or count accuracy.

`lane-count-short-context-input.net.xml.gz` retains the later real context with a 0.2 m inlet road.
Its metadata and operation are in `lane-count-short-context-input.json` and `lane-count-short-context-operation.json`.
Native import shifts that short lane before exterior restoration. Restoring the road without recompiling its connections left three entry gaps.
The regression requires native curves anchored to retained road endpoints, zero final structural failures, and exact exterior geometry.
It also checks the widened road mouth and the contained connector's complete native shape and forward direction.
Temporary native gaps remain recorded before restoration. They do not replace the final endpoint-continuity check.
The required Windows native inventory includes both lane-count contexts.
The [repaired complete replay](public-replay-evidence-v5.json) retains the failed full case and verifies the repaired result with 92 passing movements and 13 loops.

The [current scope repair](public-replay-evidence-v6.json) also fixes the preceding model's incomplete automatic junction surface.
It includes the flat connector and both endpoint junctions in the lane-count edit.
Local road mouths can adapt. Exterior roads and remote boundary endpoints retain their exact source geometry.
The complete Windows replay preserves 92 passing movements and 13 loops, with zero structural failures.
Its local passenger, bus, and bicycle transition probes complete without collision or teleport.
All declared native detector coordinates remain within 0.00236 m of the preceding model.
Count fitting remains unrun. The latest record retains the previous surface defect and all unresolved movements.

The [complete marker and precision replay](public-replay-evidence-v7.json) removes the temporary lane hints and retains exact upstream shapes and logical lengths.
It preserves 92 passing movements and 13 loops, with unchanged native field coordinates and successful local transition probes.
The record includes the passing b09ece9 CI result. Its independent Linux replay and count fitting remain unfinished.
Its local passenger, bus, and bicycle checks preserve complete requested lane chains without collision or teleport.
Native sensor-coordinate checks retain all thirteen declared fields, with maximum model-to-model displacement of 0.00236 m.

The [OCIT group check](ocit-vehicle-group-evidence-v1.json) retains the declared Kfz group `R6` at 200 and its VT correspondence.
Traffic class, rather than a K-prefixed name, determines this source classification. Current timing and road availability remain unverified.

Data attribution: © OpenStreetMap contributors.
Data license: [Open Database License 1.0](https://www.openstreetmap.org/copyright).
ASTRA-generated files retain this source attribution. Code follows the repository license.

`detector-metadata.json.gz` retains two published adjacent count-field records at 119.
`detector-metadata-input.json` binds their model-relative regression expectations to the frozen source road context.
The KML regression checks separate field-to-reference and reference-to-SUMO associations without treating field numbers as lane indices.
The [published-point check](published-field-point-evidence-v1.json) records their proximity to the first official lane anchor.
This does not establish independently surveyed sensor cross-sections or discharge counting.

The [clearance diagnostic](signal-clearance-evidence-v1.json) retains the source intergreen checks and the 6,000 s control comparison.
The [longer replay record](clearance-timeout-evidence-v1.json) retains the subsequent timeout and partial detector recovery.
Its demand fixture is the failed static-response proposal. It does not represent calibrated field demand.
The truncated E1 fixture contains 137 complete records, including 85 requested field bins. The remaining 45 requested bins stay missing.
Its seven exact field pairs supply no accepted fit metric because the native run remains invalid.
