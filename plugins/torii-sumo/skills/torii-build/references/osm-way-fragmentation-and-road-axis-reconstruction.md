# OSM way fragmentation and road-axis reconstruction

This reference defines the evidence boundary between an OSM `way` fragment and
the higher-level physical road axis used for authoritative-road matching. It is
classification and candidate-generation guidance only. It cannot merge SUMO
edges, create a SUMO `<connection>`, assign a traffic-light link index, or
promote a candidate network.

## Fundamental distinction

An OSM way is an ordered list of nodes, not a persistent identifier for one
real-world named road. A road may be represented by many ways, and OSM
guidance says that when this happens its component ways should retain the
road's `name` and/or `ref`. Consequently, a way boundary is neither necessary
nor sufficient evidence of a physical road boundary.

At-grade intersecting ways must share a node, but a through road does not have
to be split into a new way at that node. Conversely, grade-separated crossings
must not share a node. The topology and the tag context, not a way ID alone,
determine whether two fragments can belong to the same road axis.

The reusable data model therefore has four non-interchangeable levels:

```text
osm_way_fragment
  -> operational_carriageway_segment
  -> road_axis / bidirectional_corridor
  -> official_link_interval
```

`road_axis` is an identity object. `operational_carriageway_segment` is the
object that may later be bound to a SUMO edge. A successful identity match
must never be interpreted as permission to flatten the operational segments.

## Finite fragment-cause taxonomy

Classify each boundary with one primary value and source evidence. Do not use
`editor_artifact` as an automatic fallback: unexplained boundaries remain
`unknown` and require review.

| `fragment_cause` | Typical evidence | Meaning for a road axis |
| --- | --- | --- |
| `topology_junction` | shared same-layer node; incident roads | May cross a named-road identity boundary, but is an operational junction boundary. |
| `attribute_transition` | change in `highway`, `lanes*`, `turn:*`, `change:*`, `placement`, `maxspeed*`, `access*`, surface, width, or mode facility | May share a parent road axis; must retain the transition for simulation. |
| `directionality_transition` | `oneway`, directional lane tags, reversible/conditional direction evidence | May share a corridor parent; never silently fuse directional operation. |
| `carriageway_split` | physically separated parallel ways, opposing directions, median/barrier evidence | Pair as opposite carriageways under one corridor only. |
| `channelized_connector` | `*_link`, slip road, protected bypass, physically separated turn lane, merge/diverge | Relate as `connector_of`; not a serial continuation of the through axis. |
| `grade_separation` | `bridge`, `tunnel`, `layer`, `level`, non-shared crossing | Retain separate topology and elevation; identity grouping is at most parent-level. |
| `traffic_control_boundary` | official stop-line, MAP/OCIT, TLS/controller evidence | May require a synthetic approach boundary, but never follows from an OSM way split alone. |
| `roundabout_or_internal_cell` | `junction=roundabout`, compound-junction evidence | Preserve as a junction interaction cell, not a through-road segment. |
| `official_or_administrative_boundary` | official link endpoint, network inventory or boundary relation | Keep the source interval separate even if it shares a corridor parent. |
| `relation_scope` | route, restriction, or connectivity membership | Preserve relation semantics; membership alone is not road identity evidence. |
| `extraction_boundary` | declared bbox/crop operation or incomplete source snapshot | Verify against the complete source before treating it as a physical endpoint. |
| `editor_artifact` | reviewed degree-two split with no semantic/tag/geometry boundary | Eligible only for reviewed identity grouping; not automatic edge contraction. |
| `unknown` | insufficient or contradictory evidence | Block automatic grouping/promotion. |

## Finite aggregation decision table

“Aggregate” below means create an identity relation or an official-link
coverage relation. It never means concatenate geometry into one SUMO edge.

| Situation | Road-axis aggregation | SUMO operation | Required Torii action |
| --- | --- | --- | --- |
| Same-direction, degree-two continuation with no semantic change | Candidate `same_carriageway_continuation` | Preserve by default | Review before any optional contraction; forbid contraction near sensors, stop lines, TLS, restrictions, or lane changes. |
| Same named road on opposite sides of an at-grade junction | Candidate shared `road_axis` | Keep approach, junction, and exit distinct | Add `crosses_junction`; derive movements only from lane/control evidence. |
| Lanes or lane markings begin/end | Candidate parent identity only | Must remain separate | Record `attribute_transition`; use it as a channelization boundary. |
| Physically divided opposing carriageways | Candidate `opposite_carriageway_of` pair | Must remain separate directional edges | Never form a serial chain or bidirectional SUMO edge. |
| Link/slip/turn bypass | No serial aggregation | Must remain connector edge | Record `connector_of`; require legal-turn and geometry evidence. |
| Bridge/tunnel/grade separation | Optional common corridor parent | Must remain topologically/elevation separate | Reject a same-level join unless independent evidence proves it. |
| Roundabout or compound junction interior | No through-axis aggregation through the cell | Must remain junction structure | Preserve movement graph and controller ownership separately. |
| Official link/inventory boundary | Parent corridor allowed | Preserve source interval | Use `official_link_interval`, not a many-to-one overwrite. |
| Bbox/cropped source | Candidate only after full-way verification | No inferred geometry | Re-download complete ways or retain `review_required`. |
| Disconnected or branching fragments that merely share a name/ref/route | Blocked by default | Must remain separate | Require a reviewed authoritative relation; route membership is only supplementary evidence. |

## Segment-aware matching

An authoritative link may be longer than the corridor being reconstructed and
may represent both directions while OSM stores directional carriageways. A
strict all-length, one-to-one matcher therefore creates false negatives.

For each authoritative link, parameterize its geometry as an interval
`s in [0, L]`. Search each OSM direction separately, then emit one or more
immutable relations such as:

```text
official_link_interval A326@[s0, s1]
  covers_directional_fragments
    -> [way/..., way/...] (eastbound)
    -> [way/..., way/...] (westbound)
```

The status may be `partial_valid_coverage` when the source scope explicitly
explains why only `[s0, s1]` is present. It must remain `review_required` when
the scope, full-way completeness, or competing candidate is unknown.

Hard gates for a *directional* fragment sequence are:

1. graph continuity at the same usable level, or an independently evidenced
   grade transition;
2. compatible directionality and vehicle access;
3. no unaccounted `*_link`, roundabout/internal-cell, or branch transition;
4. every relevant tag/relation boundary is preserved; and
5. no competing official candidate that explains the geometry equally well.

Normalized name/ref, spatial overlap, bearing continuity, and route-relation
membership are useful ranking evidence, but are never sufficient by themselves.
The natural-road literature supports using continuity to generate candidate
strokes; it does not authorize collapsing junction, lane, or legal-movement
semantics.

## A326 / Am Sandtorkai counterexample

This example prevents an overly aggressive implementation of the matcher. In
the current OSM API objects inspected on 2026-07-19:

- [way 395272650](https://www.openstreetmap.org/way/395272650) is
  `Am Sandtorkai`, `secondary`, `oneway=yes`, `lanes=1`, and runs east to west
  in its OSM direction;
- [way 83075275](https://www.openstreetmap.org/way/83075275) is also
  `Am Sandtorkai`, `secondary`, `oneway=yes`, but has `lanes=3` and
  `turn:lanes=left|through|through;right`, running west to east.

If an official A326 record is a 986 m bidirectional link while a local OSM
snapshot contains these ways, the correct hypothesis is **not** one official
link to a serial two-way chain. It is one official bidirectional link covered
partially by multiple direction-specific OSM carriageway intervals. The
different lane and turn-lane attributes are an operational boundary even if
the ways share a named-corridor parent. Any such live observation must be
revalidated against the frozen OSM snapshot used by a candidate run.

## Extraction and provenance boundary

Record the extraction method and source hash alongside every fragment:

```text
source_export_mode = complete_ways | simple | geometry_clipped | unknown
full_way_verified = true | false
partial_coverage_reason = scope_cut | semantic_boundary | unknown
```

`osmium extract`'s default `complete_ways` strategy keeps all nodes referenced
by included ways; its `simple` strategy can leave crossing ways
reference-incomplete. Therefore an endpoint inside a local file must not be
treated as a real road endpoint until the extraction mode or the original full
way has been verified.

Retain source way IDs, versions, relation memberships, snapshot SHA-256 values,
and the exact official-link interval. When SUMO lineage is later used, bind it
to the actual SUMO-network SHA-256 and an explicit OSM import declaration.

## Non-promotion boundary

This classifier may emit `pass`, `review_required`, `blocked`, or
`not_applicable` identity and coverage decisions. It must always retain:

```text
classification_only = true
automatic_promotion_gate = blocked
```

Before Torii may materialize a SUMO lane connection it still needs separately
verified junction-facing lane and stop-line binding, connector geometry or MAP
evidence, legal-turn/restriction evidence, signal-control evidence, and a
SUMO-controller owner/link-index decision. A routeable SUMO network or a
successful visual load is not proof that these conditions are met.

## Sources

- [OSM Way data model](https://wiki.openstreetmap.org/wiki/Way)
- [OSM nodes, at-grade versus grade-separated topology](https://wiki.openstreetmap.org/wiki/Node#Nodes_on_ways)
- [OSM editing standards: divided highways, junctions, bridges, and repeated road names](https://wiki.openstreetmap.org/wiki/Editing_Standards_and_Conventions)
- [OSM lanes and exact lane-availability splits](https://wiki.openstreetmap.org/wiki/Lanes)
- [OSM one-way guidance](https://wiki.openstreetmap.org/wiki/Key:oneway)
- [OSM bridge guidance](https://wiki.openstreetmap.org/wiki/Key:bridge) and [tunnel guidance](https://wiki.openstreetmap.org/wiki/Key:tunnel)
- [OSM link-road semantics](https://wiki.openstreetmap.org/wiki/Tag:highway%3Dprimary_link)
- [OSM connectivity relation](https://wiki.openstreetmap.org/wiki/Relation:connectivity) and [turn restrictions](https://wiki.openstreetmap.org/wiki/Relation:restriction)
- [OSM route relation](https://wiki.openstreetmap.org/wiki/Relation:route)
- [Osmium extract strategies](https://docs.osmcode.org/osmium/latest/osmium-extract.html)
- [Jiang and Liu (2011), natural-road continuity](https://doi.org/10.1080/13658816.2010.510799)
- [Pourabdollah et al. (2013), authoritative-road conflation](https://www.mdpi.com/2220-9964/2/3/704)
- [Google Maps additional terms](https://maps.google.com/help/terms_maps/): use Google Maps only for manual visual cross-checks; do not scrape, trace, or create a derived map dataset from it.
