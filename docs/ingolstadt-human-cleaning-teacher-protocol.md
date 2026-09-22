# Ingolstadt Human-Cleaning Teacher Protocol

## Purpose and claim boundary

This protocol turns a same-bbox pair—raw OSM-derived SUMO network and the
human-cleaned Ingolstadt reference—into reusable topology-cleaning lessons for
Torii.

Ingolstadt is a structural teacher and regression fixture, not authority for
Hamburg. A teacher case may propose a reversible edit, but Hamburg official
road, MAP/KML, OCIT-C, TLD, detector and imagery evidence must independently
approve the corresponding Hamburg edit.

The exact SUMO/NetEdit payload remains `.net.xml`. Higher-level state records
describe evidence, actions and audit results; they do not replace the SUMO
network format.

## Dataset unit

Each case is one hash-bound A/B pair:

- **A — raw:** the unmodified OSM-derived network for the same bbox;
- **B — human:** the matching human-cleaned Ingolstadt network clip;
- source OSM, A and B SHA-256 values;
- controller IDs and physical owner junction IDs;
- original OSM node and way lineage;
- exact internal edges proposed for absorption;
- exact boundary edges that must remain;
- vehicle, bicycle and pedestrian movement signatures;
- road class, lane order, access and geometry attributes;
- typed action, applicability evidence, counterevidence and audit result.

Source and teacher files are immutable. Every materialized candidate is a new
artifact with rollback and outside-scope differential evidence.

## Classification dimensions

Cases are classified by independent, composable dimensions. No single
dimension—especially distance or controller identity—decides a merge.

| Layer | Required dimensions |
|---|---|
| Physical skeleton | T3, X4, offset/dog-leg, or other arm arrangement; number and location of conflict centers |
| OSM realization | source nodes, micro-edges, divided carriageways, storage segments, slip/channelization links and boundary ports |
| Movement graph | lane-to-lane turns, turn restrictions, merges, splits, turnarounds, internal `via` paths and conflicts |
| Control | uncontrolled/priority/right-before-left/TLS, controller ID, physical owner set, signal groups and link indices |
| Geometry | stop-line locations, approach tangents, lane order, junction polygons, internal curves, overlaps and declared-versus-rendered length |
| Modal layer | motor vehicle, bicycle, pedestrian, public transport, crossing and walking-area structures |
| Road hierarchy | motorway/trunk/primary/secondary/tertiary/residential/service/path family, access rules, speed and lane-count transitions |
| Evidence quality | source identity completeness, retained-boundary proof, official/reference match, ambiguity and known blockers |

## Typed actions

Every case has one primary decision and zero or more bounded operations.

### Primary decision

- `JOIN_CONFLICT_CORE`: absorb one proven physical conflict core.
- `KEEP_SEPARATE`: preserve distinct physical conflict centers.
- `KEEP_SEPARATE_SHARED_TLS`: preserve distinct physical owners while allowing
  one controller to govern them.
- `ABSTAIN`: evidence is incomplete, unmatched, ambiguous or out of scope.

### Optional bounded operations

- `ABSORB_MICRO_EDGES`
- `ABSORB_RECIPROCAL_MICRO_FRAGMENT`
- `SUPPRESS_ARTIFICIAL_TURNAROUNDS`
- `PRESERVE_BOUNDARY_PORTS`
- `REBUILD_LANE_MOVEMENTS`
- `REBUILD_PEDESTRIAN_INTERNALS`
- `REASSIGN_CONTROLLER_OWNERSHIP`
- `PRESERVE_STORAGE_OR_CHANNELIZATION`
- `PRESERVE_ROAD_HIERARCHY`
- `GEOMETRY_ONLY_REPAIR`
- `NO_OP`

An action record must enumerate affected IDs, preconditions, expected
invariants and counterexamples. It must never express an unbounded instruction
such as “clean this intersection”.

## Positive and negative labels

A positive `JOIN_CONFLICT_CORE` label is valid only when all conditions hold:

1. the same-bbox reference case is matched;
2. teacher source-node identity is complete;
3. at least one same-source internal OSM edge proves the absorbable core;
4. at least two retained boundary edges are proven;
5. no candidate node lies outside the teacher core;
6. the action contract has no blocker;
7. the human reference contains one physical conflict core for that scope.

Do not trust the legacy `action_family` string alone. Only the 29 exact,
blocker-free cases qualify as positive joins; broader historical counts include
review cases whose candidate cluster extends beyond the teacher core.

Negative cases are first-class training data:

- incomplete source identity;
- no same-source internal-edge proof;
- unmatched reference case;
- candidate scope larger than the teacher core;
- independent stop lines, storage or conflict centers;
- grade/layer/access separation;
- shared controller with more than one physical owner.

The correct response to insufficient evidence is `ABSTAIN`, not the most
plausible-looking merge.

### Reciprocal micro-fragment rule

Two opposite SUMO edges between the same near-coincident OSM nodes can form a
false two-edge cycle.  If netconvert also builds a turnaround at each end, the
cycle is rendered in NetEdit as a small ring even though OSM describes no
roundabout.  Length alone never authorizes contraction.  A reciprocal pair may
be proposed as `ABSORB_RECIPROCAL_MICRO_FRAGMENT` only when all of the following
are machine-readable:

1. both directions share the same OSM way lineage and compatible lane, access,
   speed and road-class semantics;
2. declared lane length and rendered shape length agree and are both below the
   configured micro threshold;
3. neither endpoint owns an independently evidenced stop line, controller,
   crossing, walking area, public-transport stop, storage bay or channelized
   conflict point;
4. the pair is not part of an explicit SUMO/OSM roundabout;
5. retained boundary ports and non-turnaround lane movements can be reproduced
   exactly after contraction; and
6. a same-bbox human reference, or independent target-city evidence, supports
   one physical conflict core.

Generated `dir="t"` connections around the pair are reported separately as
`SUPPRESS_ARTIFICIAL_TURNAROUNDS`; their presence is evidence of the symptom,
not by itself proof that the nodes may be joined.  Protected or ambiguous pairs
remain `KEEP_SEPARATE`/`ABSTAIN`.

## Eight required A/B categories

The smallest teaching queue must contain at least one case from each category.
The listed IDs are current anchors; replacements must preserve the category and
evidence strength.

| # | Category | Expected lesson | Current anchor |
|---|---|---|---|
| 1 | T3 | Preserve three physical arms while rebuilding the exact movement and pedestrian layers | `1433119620`, `267380207`, or an exact-core T3 contract |
| 2 | X4 | Preserve four arm identities and lane order; do not infer validity from SUMO load alone | `267517510` or `cluster_281967823_305519232_7009179649_7626856596_7626856598_7626856599` |
| 3 | Offset/dog-leg | Closely spaced OSM nodes may form one core only when both external-arm sets and the connecting micro-edge are proven | `cluster_1448519097_7254701196` |
| 4 | Channelized/compound | Absorb only the bounded central core; keep storage, slip and channelization links outside it | `cluster_307632375_3300416891_3300416896_335543933` |
| 5 | Road hierarchy and modal mix | Preserve higher-class through roads, lower-class branches, lane transitions, bicycle and pedestrian access | `cluster_271273941_59743835` or `cluster_267395411_270697844_915689881_915690365` |
| 6 | Multi-owner shared TLS | One controller may control separated physical junctions; controller equality never authorizes a physical merge | controllers `cluster_1875873944_1875873956_273472399` and `7623669975` |
| 7 | Ambiguous/incomplete negative | Choose `ABSTAIN` when identity or scope is incomplete, even if the geometry looks mergeable | `cluster_1242949438_2230504053_2230685419_2230685443_271787024_321573214_321573215_359022505_4622372941_7622194980_7622194981_7622194982_7622194983_89129156` |
| 8 | Unmatched/out-of-scope negative | Refuse a join when no same-bbox candidate or exact boundary contract exists | `cluster_26288887_365515590` or `cluster_1443568616_310280199_365519573` |

The existing frozen T3/X4 probe signatures remain unit-test fixtures. They do
not substitute for raw-versus-human A/B cases.

## Train and held-out isolation

Splitting screenshots randomly is prohibited. All cases connected by any of
the following keys must remain in the same fold:

- human controller ID;
- absorbed source-node connected component;
- original internal or boundary OSM way family;
- nearby spatial cell containing the same physical conflict core.

This prevents near-duplicate roads, opposite directions of one way, or several
views of one controller from leaking into held-out evaluation. In particular,
the related `cluster_703495348...`, `cluster_703495349...` and
`cluster_703495350...` cases must not be split across folds.

The held-out set must include, at minimum:

- one unseen T3 positive;
- one unseen X4 positive;
- one unseen offset/channelized positive;
- one unmatched or incomplete negative;
- one multi-owner shared-TLS negative.

For held-out inference, B, its action contract and its reference ID are hidden.
The estimator sees A and its derived state, predicts the primary decision and
exact edit scope, and may materialize a candidate. B is revealed only for
scoring.

## Machine scoring and promotion gates

Score each case in this order:

1. exact primary decision: `JOIN_CONFLICT_CORE`, `KEEP_SEPARATE`,
   `KEEP_SEPARATE_SHARED_TLS`, or `ABSTAIN`;
2. exact absorbed-node, absorbed-internal-edge and retained-boundary-edge sets;
3. zero edits outside the declared scope;
4. lane-to-lane movement parity, including direction, access, `via`, conflicts
   and turnaround semantics;
5. controller-to-owner and signal-link parity;
6. road hierarchy, lane order, modal and speed/access parity;
7. zero new lane/junction surface overlap, self-intersection, endpoint gap or
   implausible declared-versus-rendered length;
8. successful netconvert round trip, SUMO load and required route smoke tests.

Any false physical merge, outside-scope edit, lost movement, changed physical
owner, or new overlap is a hard failure. Route completion and SUMO load alone
cannot promote a case.

## NetEdit A/B evidence

For each side, capture four deterministic views:

- overview;
- junction inspect;
- Connection Mode;
- TLS Mode.

A and B must use the same world bbox, zoom, view settings and selected owner.
Record image hashes and the network hash before and after capture. Screenshots
are review aids for geometry, stop lines and lane surfaces; machine-readable
topology and semantic evidence remains authoritative for scoring.

## Hamburg transfer and final review

An Ingolstadt action is only a prior for the Hamburg estimator. Before a
Hamburg edit is materialized, it requires independent support from the relevant
combination of:

- Hamburg OSM topology and lineage;
- official road geometry and classification;
- official MAP/KML lane and movement geometry;
- OCIT-C controller and signal-group evidence;
- TLD signal observations;
- official detector identity, type, direction and location;
- current imagery as review evidence;
- SUMO/NetEdit structural, geometry, movement and runtime audits.

Official Hamburg evidence overrides an Ingolstadt analogy. Missing or
conflicting official evidence yields `review_required` or `blocked`; it must not
be filled by a teacher guess.

Most importantly:

> A shared TLS controller does not imply one physical junction. Distinct stop
> lines, storage segments or conflict centers must remain distinct physical
> owners unless independent Hamburg evidence proves a bounded single core.
