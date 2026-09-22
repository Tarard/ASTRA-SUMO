# Road-arm and connection classification

This reference defines the second layer below Torii's physical-cell
intersection archetype. It is classification evidence only; it cannot merge
SUMO junctions, create lane connections, or bind a signal controller.

## Why the layers are separate

The Hamburg road inventory intentionally uses two network-responsibility
categories, `Hauptverkehrsstraße` (HVS) and `Bezirksstraße`. The city describes
HVS as the network carrying the main traffic work and notes that Bezirksstraßen
can still provide local access and, in some cases, larger-scale connections.
Therefore an OSM `highway=*` value must not be treated as Hamburg's official
functional class. Use the official road network as the authoritative source
when it is available, and retain the OSM tag as geometry/importance evidence.

Germany's RIN distinguishes network categories such as `AS`, `LS`, `VS`, `HS`,
and `ES`; RASt applies to urban main roads and access roads and supplies typical
cross-section/design situations. These are network and design categories, not
SUMO lane-to-lane movements.

## Resolution order

For each semantic boundary arm, resolve the following independently:

1. `authority_category`: the jurisdiction's official inventory (`hvs`,
   `bezirksstrasse`, `bundesfernstrasse`, etc.).
2. `functional_category`: an explicitly supplied RIN/RASt category (for
   example `HS III` or `ES IV`).
3. `network_role`: a jurisdiction-neutral role (`arterial`, `collector`,
   `local`, `access`, `link`). Prefer a caller-supplied official role. If it is
   absent, emit an OSM-only fallback with `rule_derived` status and an explicit
   `osm_fallback` resolution.
4. `osm_highway_class`: preserve the raw OSM-derived class (`primary`,
   `secondary`, `service`, `*_link`, ...); it is never silently promoted to an
   official category.
5. Operational form: directionality, directional carriageway pairing, lane
   counts, turn-lane marking, access restrictions, and grade-separation tags.

If member ways disagree, the dimension becomes `unknown`/`contradicted` and the
arm is sent to review. Its overall source resolution is recorded as
`contradicted`, rather than silently falling back to OSM or choosing the
highest-class way as an override.

## Connection order

Only after arm identity is resolved do we classify a connection candidate:

1. `through_axis`: two arms in the same continuous axis;
2. `branch_to_axis`: a branch arm meets the main/continuous axis;
3. `link_to_network`: a link/ramp candidate meets the network;
4. `access_to_network`: a service/access arm meets the network;
5. `arm_to_arm`/`unknown`: evidence is insufficient for a more specific
   relation.

The relation is a routing/topology hypothesis. A legal movement still needs
independent lane connectivity, turn restrictions, signal/MAP evidence, and
rendered geometry review before a SUMO connection can be emitted.

## OSM-to-SUMO binding boundary

`road_sumo_binding` is the hand-off from this semantic layer to a future
network rebuild. It only consumes the explicit OSM-way → SUMO-edge lineage
report, whose OSM source hash and actual SUMO network hash must both match the
snapshots being reviewed. For each semantic road arm it records:

- candidate and pass-status SUMO road edges;
- unmapped or review-only OSM ways; and
- a connection intent between already classified arms.

An intent becomes `ready_for_lane_connection_review` only when both arms have
pass-status road-edge lineage. That is intentionally not a materialization
approval: before emitting a SUMO `<connection>`, the workflow still needs the
junction-facing lane/stop-line binding, connector geometry or MAP evidence,
legal-turn/control evidence, and an explicit SUMO owner/link-index decision.
The artifact keeps `automatic_promotion_gate=blocked` even in that ready state.

## Finite output vocabulary

The implementation stores the following finite dimensions:

- road-arm form: `bidirectional`, `one_way`, `directional_pair`, `link`,
  `unknown`;
- network role: `arterial`, `collector`, `local`, `access`, `link`, `unknown`;
- channelization candidates: turn bay, slip bypass, splitter/island, median
  refuge, flare/fanout, storage connector, merge/diverge, and protected
  corner;
- connector class: storage, short internal, movement, or unknown.

Every record carries source way/port/connector IDs, a provenance status, and a
review decision. The road-detail artifact is therefore reusable for a simple
T/X intersection and for a compound case such as 2394 without creating an
unbounded list of named intersection types.

## Source boundary

- [Hamburg HVS inventory and two-category policy](https://www.hamburg.de/resource/blob/193364/ba2cd41bbfc74502e0f78e75f679578e/kriterien-hauptverkehrsstrassen-pdf-data.pdf)
- [Hamburg current HVS dataset](https://suche.transparenz.hamburg.de/dataset/hauptverkehrsstrassen-hamburg18)
- [FGSV RIN category table](https://www.fgsv-verlag.de/pub/media/pdf/121.k.pdf)
- [FGSV RASt scope and typical design situations](https://www.fgsv-verlag.de/rast)
- [OSM highway classification caveat](https://wiki.openstreetmap.org/wiki/Highway_classification)
- [FHWA roadway/intersection data elements](https://highways.dot.gov/safety/data-analysis-tools/market-analysis-collecting-fundamental-roadway-data-elements-support-5)
- [SUMO road-network and connection model](https://sumo.dlr.de/docs/Networks/SUMO_Road_Networks.html), [SUMO connection descriptions](https://sumo.dlr.de/docs/Networks/PlainXML.html)
