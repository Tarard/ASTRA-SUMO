# Real Hamburg walking-area context regression

The [initial additive controls](local-additive-conversion-20261020-v1.md)
verified the converter on a synthetic component outside Hamburg. They could
not detect a real-junction pedestrian regression found by independent review.
The [receipt](additive-walking-context-20261020-v1.json) preserves that failure,
native comparisons, the corrected result and lane-layout checks.

## Reproduce the real junction

The source is the decompressed `candidate.net.xml.gz` in
`benchmarks/hamburg_loaded_links_20261018_v1/`. The gzip hash is
`48da2a9f750e4910866fa55573450a546851a077696d83b24bde623dcfd42684`.
The edit adds a residential road from 12865383036 to 5353176677 with native
input lane count one. The source type supplies a two-metre sidewalk.

The initial four-road local input produced no outgoing pedestrian connection.
Its new lane length was 15.294544 m. The full native control instead generated
walking area `:5353176677_w1` and lane length 8.387284 m. Removing only the frozen
shape attributes matched the length but did not restore the walking connection.
Both results remain available.

## Separate computation from adoption

Native walking-area generation needs complete adjacent junctions and the roads
beyond them. Additive conversion now includes their saved internal paths and
request tables. Those native-context junctions retain their original shape
settings instead of being forced to frozen shapes. The final edit scope still
contains only the requested junctions and roads. Outside controller programs
come from the source, not the larger native rebuild.

The corrected production path converts 14 of 196 source roads. Its two new lane
states and outgoing connections match the full-network control exactly. The
new sidewalk reaches `:5353176677_w1`. Both local and full construction pass.
The record lists native rebuild junctions separately from final mutable nodes.
Real Hamburg local/full regressions are required native CI cases.

## Lane-count semantics and checks

SUMO defines [sidewalk and bicycle widths as additional-lane attributes](https://sumo.dlr.de/docs/Netedit/attribute_help.html).
The [pedestrian documentation](https://sumo.dlr.de/docs/Simulation/Pedestrians.html)
also explains how a dedicated sidewalk prevents duplicate sidewalk generation.
Native `numLanes=1` plus the residential type therefore creates one driving lane
and one sidewalk. This result must not be treated as an unexplained two-lane
capacity increase.

The report now separates input lane count, type-added sidewalks and bicycle
lanes, expected physical total and built physical total. A count mismatch fails
construction. A native type control creates one base lane plus one sidewalk
and one bicycle lane, and reports three total lanes. Its separate bicycle
isolation check still blocks that candidate. Lane count cannot waive access
or connectivity checks.

The adjacent-context consumer group passes 155 tests. The lane-layout group
passes 40. Counts overlap. Earlier failed unit fixtures and the isolated-bicycle
control remain in the receipt. Full CI is recorded separately when complete.

- Reproduction and metrics: `outputs/probe_additive_walking_context_20261020_v1.py`, `_v2.py` and `_v3.py`.
- Original and experimental contexts: `outputs/awc26v1/` and `outputs/awc26v2/`.
- Corrected local/full sources, configuration, raw native outputs and results: `outputs/awc26v3/`.
- Source/hash checks and operation values: `result.json` under each run directory.
- Native and lane-count regressions: `tests/test_local_additive_conversion.py`.
- Independent review: `outputs/claude-review-ab5bfb4-complete-20261020.txt`.

The manually selected road is a construction control, not a field-approved
addition. Independent automatic correction, field precision, complete source
curation, merged-road OD correspondence and remaining global conversion stay
unverified or open.
