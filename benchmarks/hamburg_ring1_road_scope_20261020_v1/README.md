# Frozen README-area road-scope input

This fixture covers the Binnenalster / Ring1 case shown in the repository README.
It is separate from the Am Sandtorkai fixtures.

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
Run the [offline example](../../examples/06_hamburg_ring1_demo/README.md) to rebuild stages 1 and 2.
[Separate junction diagnostics](junction-diagnostic-evidence-v2.json) record 35 passing isolated movements at 119 and 535.
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

`lane-count-input.net.xml.gz` freezes a 12-road conversion context from the unaccepted LSA119 prototype.
`lane-count-input.json` records its identity and the earlier conversion's adjacent geometry changes.
`lane-count-operation.json` adds a motor lane with explicit connections and retains bicycle access.
The native regression verifies source preservation, source-derived geometry hints, loading, and compatible service for the added lane.
It checks that neighboring geometry changes decrease relative to the recorded conversion without hints.
Remaining changes stay measured. This fixture does not establish field geometry, complete junction reconstruction, or count accuracy.

Data attribution: © OpenStreetMap contributors.
Data license: [Open Database License 1.0](https://www.openstreetmap.org/copyright).
ASTRA-generated files retain this source attribution. Code follows the repository license.

`detector-metadata.json.gz` retains two published adjacent count-field records at 119.
`detector-metadata-input.json` binds their model-relative regression expectations to the frozen source road context.
The KML regression checks separate field-to-reference and reference-to-SUMO associations without treating field numbers as lane indices.
The [published-point check](published-field-point-evidence-v1.json) records their proximity to the first official lane anchor.
This does not establish independently surveyed sensor cross-sections or discharge counting.
