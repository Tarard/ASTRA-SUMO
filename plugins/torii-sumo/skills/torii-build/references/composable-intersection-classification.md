# Composable intersection classification before SUMO reconstruction

Classify the intersection before joining nodes, rebuilding channelization, or
assigning traffic-light links. The canonical result is not a single name such
as `T3`, `X4`, `roundabout`, or `DDI`. It is an evidence-bound composition of a
small finite vocabulary and a core graph. Familiar names remain useful as
derived aliases for people, logs, and compatibility APIs.

An OSM node, a physical conflict core, a SUMO junction owner, and a logical
signal controller are different objects. Never infer one count directly from
another.

## Claim boundary

Classification is read-only. It may rank downstream hypotheses, but it never
authorizes a network mutation. Apply accepted changes through SUMO PlainXML
and `netconvert`; never hand-edit a generated `.net.xml`.

Keep these counts separate in every artifact:

```text
raw_node_count
approach_gate_count
entry_count
exit_count
physical_conflict_core_count
classification_join_group_count
owner_count_after_rebuild_candidate
controller_domain_count
```

Every asserted field carries its own state:

```text
value
status = observed | rule_derived | reviewed | contradicted | unknown
decision = pass | review_required | blocked | not_applicable
evidence_ids[]
alternatives[]
rationale
```

`candidate` belongs to the lifecycle of a core or reconstruction hypothesis;
it is not a geometry/control value and is not a field-level evidence grade.
Unknown is a valid result and must not be replaced with a convenient default
merely to let reconstruction continue.

## Canonical model: finite atoms plus a core graph

Represent an intersection as:

```text
IntersectionProfile = EvidenceEnvelope
                    + CoreGraph(AtomicCoreCandidates, InternalConnectors)
                    + OrthogonalDimensions
                    + DerivedAliases
```

The core graph explains how the pieces compose. Each
`atomic_core_candidates[]` record has a finite `interaction_kind`, source-node
evidence, and hypothesis status. `internal_connectors[]` retain endpoints,
measured graph length, and storage capability. Approach gates remain in the
structured `arm_model`, while channelization and movement transforms attach
finite semantics to cores and connectors. A complex or unconventional
intersection is therefore a larger graph made from the same atoms, not an
escape hatch named `complex`.

Core boundaries require more than distance. Useful independent evidence
includes movement-conflict envelopes, stop-line clusters, storage-capable
connectors, graph cuts, independent movement paths, grade separation, and
channelization boundaries.

## Orthogonal dimensions

Keep the following dimensions independent. Values are finite, while counts,
IDs, bearings, and movement edges are structured evidence attached to them.

| Dimension | Finite values or finite primitives | Meaning |
| --- | --- | --- |
| `grade_relation` | `at_grade`, `grade_separated`, `mixed`, `unknown` | Vertical relation of interacting facilities. |
| `interaction_kind` | `cross_and_turn`, `merge_diverge`, `access_on_continuous_mainline`, `crossing_only`, `mixed`, `unknown` | Whether paths cross and turn, only merge/split, retain a main-road continuation, or cross without road changes. These are Torii projections from ASAM OpenDRIVE interaction types. |
| `cell_structure` | `atomic`, `serial_compound`, `network_compound`, `ring_group`, `interchange_group`, `unknown` | Organization of physical interaction cells. `staggered` is a derived layout description of a serial graph, not an arm type. |
| `arm_model.arm_count_class` | `A0`, `A1`, `A2`, `A3`, `A4`, `A5_plus`, `unknown` | Coarse count bucket; always retain exact `arm_count`, `entry_count`, `exit_count`, `count_status`, stable gate IDs, bearings, and reviewed opposition pairs. |
| `angular_distribution` | `orthogonal_like`, `radial`, `irregular`, `unknown` | Bearing distribution independently of physical cell count and minimum-angle acceptability. |
| `minimum_angle_status` | `non_skew`, `small_angle_present`, `unknown` | Whether an adjacent arm gap falls below the emitted, configurable engineering threshold. An evenly spaced six-arm junction can therefore be both `radial` and `small_angle_present`. |
| `angular_form` | `orthogonal_like`, `skewed`, `radial`, `irregular`, `unknown` | Compatibility projection only. Canonical decisions must use `angular_distribution` and `minimum_angle_status`, so a small angle does not erase radial organization. |
| `circulation_form` | `none`, `nontraversable_ring`, `traversable_mini`, `unknown` | Presence and physical form of a circulating core. A roundabout retains its exact arm/entry count. |
| `carriageway_organization` | `bidirectional`, `divided_pair`, `one_way_pair`, `reversible`, `mixed`, `unknown` | Directional organization of the external roads. `one_way_pair` belongs here, independently of shape and control. |
| `movement_transforms[]` | `direct_left`, `left_via_uturn`, `left_via_auxiliary`, `minor_street_via_uturn`, `contraflow`, `contraflow_plus_uturn`, `circulation`, `through_crossover`, `left_crossover`, `dynamic_lane_sharing` | Set-valued primitives that reroute movements. Named alternative designs are projections of these primitives and the core graph. An empty list means no transform was evidenced; it is not proof that none exists. |
| `channelization[]` | `turn_bay`, `slip_bypass`, `splitter_island`, `median_refuge`, `flare_fanout`, `storage_connector`, `weave_segment`, `protected_corner` | Observed or rule-derived separation of movements into paths. `skewed`, `*_link`, lane-count change, and `traffic_calming=island` alone do not prove a canonical island, turn bay, or slip lane; retain them as review-gated evidence features. |
| `facility_modes[]` | `motor_vehicle`, `bicycle`, `pedestrian`, `rail`, `access` | Facilities represented in the classified cell; retain mode-specific restrictions separately. |
| `movement_graph_status` | `complete`, `partial`, `contradictory`, `unknown` | Provenance quality of ingress-lane to egress-lane legal reachability. The movement graph itself is a set of stable lane/gate edges, not an enum. |
| `control_rule` | `uncontrolled`, `priority`, `stop`, `all_way_stop`, `yield`, `signalized`, `rail`, `mixed`, `unknown` | Right-of-way rule. It is independent of geometry and physical cell count. |
| `controller_topology` | `single_core_single_controller`, `multi_core_shared_controller`, `linked_controllers`, `unknown` | Mapping between classified cores and logical controllers. OSM-only recognition normally leaves this `unknown`; one controller does not imply one physical core or one SUMO owner. |

This vocabulary may be extended only with a cited semantic definition, at
least one fixture, finite-enum validation, deterministic serialization, and a
documented projection from existing profiles.

## Derived aliases, not canonical types

Compute familiar labels only after the arm model and core graph are known:

- `T3`: exactly three reviewed arms, with one reviewed continuous/opposition
  pair and one remaining branch;
- `Y3`: exactly three reviewed arms with no defensible continuous pair and a
  reviewed three-way angular layout;
- `X4`: exactly four reviewed arms with two disjoint reviewed opposition
  pairs through one common conflict core;
- `irregular_3` or `irregular_4`: arm count is known but the required
  opposition/core evidence for `T3`, `Y3`, or `X4` is absent or contradicted;
- `roundabout`: the serialized compatibility alias for a circulation core;
  always render and compare it together with exact `arm_count`, for example
  `roundabout_A5`. Never discard the count merely because the alias is known.
  Claim the exact count only after the full connected tagged ring component is
  graph-closed; truncated or disconnected ring evidence must abstain. Rebuild
  protected mode/grade/access evidence over that expanded ring closure so
  facilities attached to a remote ring segment are not lost.

Node degree alone cannot establish these aliases. For example, three OSM ways
may represent one two-way road plus a one-way carriageway, and a four-gate
layout may be staggered, distributed, or lack two reviewed opposition pairs.

Names such as DDI, RCUT, MUT, DLT, split intersection, and quadrant roadway
remain useful `design_aliases`. They are composed from core cells, movement
transforms, grade relation, circulation, and carriageway organization. They do
not expand the canonical ontology with one unrelated top-level class per
design handbook.

## Evidence order

1. Hash-bind the source OSM, SUMO, MAP/MAPEM, controller, and review artifacts.
2. Extract stable approach gates and preserve lane direction, mode, access,
   grade, and carriageway evidence. Raw node degree is only a clue.
3. Build the lane/gate movement graph from legal reachability. When MAP/MAPEM
   is available, bind each selected `IntersectionGeometry` and its
   ingress-to-egress `connectsTo` relations rather than scanning unrelated
   geometries.
4. Partition the local network into core-graph atoms. Distance may nominate a
   quotient group, but cannot prove a join.
5. Classify each orthogonal dimension with field-level status, evidence,
   alternatives, and rationale.
6. Compute derived aliases. Never feed an alias back as stronger evidence than
   the dimensions from which it was derived.
7. Generate separate hypotheses for node aggregation, channelization repair,
   lane connections, SUMO owner layout, and controller binding. Hash-bind the
   profile to every hypothesis.
8. Require background Netedit render review, lane-surface and junction-polygon
   overlap checks, length/shape parity, movement closure, SUMO loading, and
   routeability before accepting any mutation.

## What the sources establish, and what Torii infers

The cited sources do not publish one universal ontology. They establish useful
pieces that Torii combines. Keep the boundary explicit:

| Source-backed observation | Direct support | Torii engineering inference |
| --- | --- | --- |
| Geometry has separable features. | CD 123 distinguishes T, cross, staggered, skew, one-way, and multi-arm forms. FHWA treats approaches, alignment, grade, channelization, and facilities separately. | Model arm count, angular form, carriageway organization, channelization, and grade as independent dimensions rather than one flat label. |
| Alternative forms are compositions. | FHWA 2024 describes alternative forms as sets of primary and satellite intersections that reroute movements to remove, reduce, or relocate conflicts. | Encode reusable core cells and movement-transform primitives; keep handbook names as derived design aliases. |
| Interaction semantics differ. | ASAM OpenDRIVE separates common, direct, virtual, and non-turning crossing junction interactions and supports junction groups such as roundabouts and complex junctions. | Project those semantics into `interaction_kind` and represent grouped junctions as a core graph. This does not make an OpenDRIVE group boundary a proven physical-core boundary. |
| Topology and signals are distinct. | ETSI MAPEM represents approaches, ingress/egress lanes, allowed connections, and their signal-group links. Lanelet2 separates lane primitives from regulatory elements. | Store a movement graph and a control mapping as separate evidence layers. A signal-group or regulatory-element relation is not itself a geometry class. |
| Logical and simulator ownership can differ. | SUMO permits one traffic-light controller to control an arbitrary number of junctions and warns that automatically generated programs may require correction. | Keep owner count, controller count, and physical-core count independent; treat joins and TLS binding as review-only hypotheses. |
| OSM is evidence, not ground truth. | OSM documents `junction=*`, `junction=roundabout`, `oneway=*`, `turn:lanes=*`, and traffic-control tagging, but completeness varies by mapper and region. | Use tags as provenance-bearing observations or candidates. Absence of a tag is not proof of absence, and proximity is not proof of identity. |
| Geometry and degree are insufficient. | Scheider and Possin define junction individuation through turn-compliant movement affordances and argue that geometry/topology alone are insufficient proxies. Yang et al. identify and classify complex junction subgraphs. | Make legal reachability and core-graph composition primary; use learned or geometric labels as candidates with alternatives, not mutation authority. |

Numerical thresholds require the same boundary. For example, CD 123's
stagger-distance and minimum-angle provisions are design guidance for a stated
road context. Torii may use them as cited candidate features, but must not claim
they are a universal definition of `staggered` or `skew` across all countries.

## Torii implementation and compatibility

The generic implementation is
`torii_sumo.intersection.archetype_profile`. It exposes
`registered_intersection_type_vocabulary()` and
`classify_osm_intersection_archetype(...)`. The read-only MCP adapter
`sumo_intersection_archetype_classify` binds the source OSM SHA-256, physical
cell, topology evidence, movement hypotheses, and resulting profile. The
teacher-free hypothesis path embeds the same profile and its classification ID
in every candidate DAG operation. The adapter reads and hashes one frozen byte
snapshot, returns all parent evidence bodies, and exposes generation status,
recognition disposition, type-recognition decision, and the blocked promotion
gate separately so `status=pass` cannot be mistaken for a reviewed type. The
candidate DAG recomputes the classification content ID and rejects stale or
forged profile bodies before claiming hash-bound lineage.

The recognizer remains classification-only. Downstream node aggregation,
channelization, lane connections, and TLS binding consume its profile ID and
evidence hashes but make their own reviewed decisions.

The existing `torii_sumo.intersection.composable_archetype` registry and
`sumo_hamburg_2394_archetype_classify` tool are a legacy/specialized
projection. They remain compatible for existing artifacts:

- `simple_T3_v1` and `simple_X4_v1` are derived-alias prototypes, not the
  complete canonical type system;
- `hamburg_2394_v1` is a rich, provenance-bound Hamburg projection;
- the old `BaseSkeleton × PhysicalArrangement` fields may be serialized for
  compatibility, but must be derived from the new dimensions and must not be
  used as primary evidence;
- `execution_hint.classification_only` remains `true`, and
  `automatic_authorization` remains `blocked`.

In particular, old `PhysicalArrangement` values mixed independent concepts:
`skewed` projects from `minimum_angle_status`, `one_way_pair` moves to
`carriageway_organization`, and `staggered` to a derived description of
`cell_structure` plus core-graph offsets. Candidate/confirmed wording moves
out of `ControlDomain` values and into field-level status.

## Hamburg 2394 compatibility projection

Official MAP evidence establishes:

- 25 lanes total, 11 motor-vehicle lanes;
- 16 connections total, 8 motor-vehicle movements;
- three reviewed vehicle-arm groups `2`, `3`, `4`; the generic parser leaves
  physical arm count unknown unless a provenance-bound arm grouping is passed;
- arm `2` and arm `4` are the reviewed continuous axis, arm `3` is the branch;
- all six non-U-turn arm-to-arm movement pairs exist;
- 20 `stopLine` and 6 `mergePoint` markers across all modes; the seven vehicle
  stop-line markers form three approach-projected coordinate bars with marker
  coverage `2, 2, 3`, not seven independent physical stop lines;
- one MAP `IntersectionGeometry`, official `intersectionPart={0}`, and one
  OCIT technical subnode/controller domain.

The legacy projection remains:

```text
prototype                 = hamburg_2394_v1
base_skeleton              = T3                 # derived alias
family                     = channelized_T3_family
physical_arrangement       = compound_candidate # legacy mixed field
control_domain             = multi_owner_single_controller_candidate
physical_conflict_core     = unknown_pending_conflict_analysis
automatic_promotion        = blocked
```

Its canonical migration preserves rather than flattens the evidence:

```text
arm_model.arm_count_class  = A3
arm_model.arm_count        = 3
arm_model.entry_count      = 3
arm_model.exit_count       = 3
arm_model.through_pairs    = {(2, 4)}
derived_alias.value        = T3
channelization             = legacy markers retained; canonical primitive mapping review_required
cell_structure.value       = unknown
cell_structure.alternatives= {atomic, serial_compound, network_compound}
controller_topology.value  = multi_core_shared_controller
controller_topology.status = rule_derived
controller_topology.decision = review_required
control_rule.value         = signalized
control_rule.status        = observed
```

The classification-only owner proposal remains:

```text
main_core       = {3847369287, 757036909, 76463166}
south_stopline  = {3847369288}
west_stopline   = {759714726}
east_ingress    = {2761334279, 757036795}
east_out_cross  = {3847369285}
logical_control = 2394
```

The official OCIT file is parsed and hash-bound. Its `Kopfdaten/Kurzbezeichnung`
must normalize to `2394`, phase `VerkehrstechnischerTeilknotenNr` and OMTC `Tk`
must agree, and this specialized projection requires exactly `TK 1`. The
five-owner layout remains a review candidate until V10 rendering and movement
gates pass.

Preserve the physical connectors between these owner candidates. In
particular, do not join `3847369288` into `757036909`: the former carries an
independent official approach-3 stop line and the intervening segment is about
6.25 m. The remaining review gates are the exact interpretation of that south
segment and whether the east-outbound owner carries a motor signal link or only
the crosswalk conflict.

## Adding evidence or vocabulary

For each new fixture or vocabulary value, prove:

1. arm and gate inference survives micro-node expansion and node-ID ordering;
2. the core graph preserves exact entry/exit counts and legal movement edges;
3. derived aliases are reproducible from reviewed dimensions;
4. owner count never fills `physical_conflict_core_count` implicitly;
5. classification and execution fields remain separate;
6. every automatic join, channelization edit, and controller binding remains
   blocked until geometric and movement review.

## Normative, technical, and research basis

Primary standards and government guidance:

- FHWA, *Synthesis of Alternative Intersection Forms*, FHWA-HRT-24-090
  (May 2024), inventories more than 50 forms and explicitly describes
  variations, combinations, primary/satellite compositions, and movement
  rerouting:
  <https://highways.dot.gov/sites/fhwa.dot.gov/files/FHWA-HRT-24-090.pdf>
- FHWA, *Signalized Intersections Informational Guide*, treats the number of
  approaches, channelization, alignment, grade, and multimodal facilities as
  separate geometric/design concerns:
  <https://highways.dot.gov/media/11406>
- National Highways, CD 123 v2.1.0, Appendix A, covers T, cross, staggered,
  skew, one-way, and more-than-four-arm at-grade forms:
  <https://www.standardsforhighways.co.uk/tses/attachments/962a81c1-abda-4424-96c9-fe4c2287308c>
- ASAM OpenDRIVE 1.9 distinguishes common, direct, virtual, and crossing
  junction interactions; its junction-group model covers roundabouts and
  complex groupings:
  <https://publications.pages.asam.net/standards/ASAM_OpenDRIVE/ASAM_OpenDRIVE_Specification/v1.9.0/specification/12_junctions/12_01_introduction.html>
  and
  <https://publications.pages.asam.net/standards/ASAM_OpenDRIVE/ASAM_OpenDRIVE_Specification/v1.8.1/specification/12_junctions/12_13_junction_groups.html>
- ETSI TS 103 301 V2.2.2 specifies MAPEM road/lane topology, approaches,
  ingress-to-egress connections, modes, and signal-group linkage:
  <https://www.etsi.org/deliver/etsi_ts/103300_103399/103301/02.02.02_60/ts_103301v020202p.pdf>
- SUMO documents multi-junction TLS control and the limits of automatic node
  joining:
  <https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html> and
  <https://sumo.dlr.de/docs/netconvert.html>
- OpenStreetMap tagging references provide raw evidence semantics for junction
  and movement-related tags:
  <https://wiki.openstreetmap.org/wiki/Key:junction>,
  <https://wiki.openstreetmap.org/wiki/Tag:junction%3Droundabout>, and
  <https://wiki.openstreetmap.org/wiki/Key:turn:lanes>.
- Lanelet2 keeps lane/area primitives and regulatory elements separate, and
  permits multiple lanelets to reference the same regulatory element:
  <https://docs.ros.org/en/rolling/p/lanelet2_core/doc/LaneletPrimitives.html>
  and
  <https://docs.ros.org/en/iron/p/lanelet2_core/user_docs/RegulatoryElementTagging.html>.

Research basis:

- Scheider and Possin, *Affordance-based individuation of junctions in Open
  Street Map*, defines junctions through turn-compliant movement affordances
  and explains why geometry or graph topology alone is insufficient:
  <https://doi.org/10.5311/JOSIS.2012.4.54>.
- Yang et al., *Identifying Complex Junctions in a Road Network*, uses
  primary/secondary road subgraphs to identify compound structures:
  <https://doi.org/10.3390/ijgi10010004>.
- Yang et al., *A Stacking Ensemble Learning Method to Classify the Patterns
  of Complex Road Junctions*, evaluates named complex-junction pattern
  recognition; its classes are useful projections, not a universal ontology:
  <https://doi.org/10.3390/ijgi11100523>.

These sources motivate the representation; the exact Torii vocabulary,
thresholds, evidence-status model, and mutation gates are Torii engineering
decisions and must be tested as such.
