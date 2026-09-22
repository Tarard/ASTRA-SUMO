# Continuous lanes between intersections

Use the existing `hamburg_network` workflow and construction-plan request.
First verify a single road section. Then extend the same lane description
across the corridor. Keep the chosen drawing and year authoritative.

Add `road_runs` to `torii.engineering-topology/v1`. Each run represents one
direction of travel between two declared nodes. Supply frozen raw OSM through
the request's `source_osm` record so the workflow can inspect the original ways.

| Field | Meaning |
|---|---|
| `id`, `from`, `to`, `evidence` | Road identity, distinct end nodes, and drawing evidence |
| `source_way_ids` | Original OSM way IDs in travel order, without SUMO suffixes |
| `start_node_id` | Optional original OSM node that fixes chain orientation |
| `axis_shape` | Drawing-based metric points in the topology coordinate system |
| `sections` | Ordered cross sections covering the entire axis without gaps |
| `start_m`, `end_m` | Distance along that axis; final `end_m: null` reaches its end |
| `sections[].evidence` | Source for the cross section and its transition position |
| `sections[].lanes` | Lanes ordered from right to left in travel direction |
| `lanes[].key` | Stable physical lane identity across sections |
| `lanes[].width_m`, `allow` | Positive width and explicit SUMO vehicle permissions |
| `lanes[].shape` | Explicit metric lane centerline for that whole section |
| `lanes[].lateral_offset_m` | Alternative to `shape`; positive offsets are left of the axis |
| `sections[].merges` | Map an ending upstream lane key to its documented downstream key |
| `source_attribute_overrides` | Map superseded OSM lane, turn, width, or speed tags to specific drawing evidence |
| `tapers` | Documented widening intervals with `start_m`, `end_m`, and `evidence` |

If `axis_shape` is absent, OSM geometry needs a matching `data_year` or an
evidence-backed `osm_geometry_valid_for_target_year` declaration. Undated or
newer OSM cannot supply historical coordinates by default.

Keep a continuing lane's key when a new lane changes its numerical index.
For example, adding a right-hand bicycle lane changes two motor lanes from
indices `0, 1` to `1, 2`. Their keys and physical continuity remain unchanged.
Give the new bicycle lane a separate key. Do not create a motor-to-bicycle
connection merely to make all lanes reachable.

The workflow inspects original way points, tags, side connections, controls,
and restrictions. It removes harmless way boundaries and retains meaningful
changes. Retained cuts can make the SUMO edge count exceed the original way
count. A smaller edge count is not the acceptance criterion.

The output includes `road-continuity/continuity.json`, normalized `topology.json`,
the compiled candidate, and `corridor-lane-probes/summary.json`. Source hashes
bind the interpretation, normalized topology, candidate, and probe results.
Each lane path gets an isolated native SUMO vehicle. The check requires the
expected external lane sequence, arrival, and zero collisions or teleports.
Internal lane connections receive a separate structural check.

A new lane also needs entry evidence. If one compatible upstream lane and
one complete approach are available, an additional probe must observe a
native lane change into the new lane and normal arrival. Otherwise the report
keeps `needs_entry_evidence`. Direct insertion on the new lane does not prove entry.

Retained side-road or control locations require interpretation. The numerical
boundary alone does not rebuild their access or signals. Declare supported
side roads and junction movements explicitly. Keep missing coverage visible.
Use the existing junction-movement checks before extending the road description.

Successful probes establish diagnostic passage for native default vehicle
models. They do not establish surveyed geometry, full vehicle clearance,
historical signals, or field implementation of a design drawing.

## Road geometry and widening

Generate offset lanes along the whole axis before cutting the road. Cuts use
normal cross sections at axis stations. At an axis vertex, they use the angle
bisector. This keeps shared lane endpoints together on curved and rotated roads.
An ambiguous or missing intersection with the lane requires geometry review.

For example, `source_attribute_overrides: {"maxspeed": "Drawing sheet 2 sets
the entire road to 30 km/h"}` can remove an obsolete speed boundary when every
section also declares a valid `speed_m_s`. An override removes only the named
attribute reason. It never removes a coincident crossing, side road, restriction,
or structural boundary. Uncertain lane uses do not justify an override.

Declare a taper only when its location has a stated basis:

```json
"tapers": [
  {"start_m": 45, "end_m": 60, "evidence": "Synthetic test widening; not a field measurement"}
]
```

The end must coincide with a section that adds lanes. The interval must leave
an upstream approach and cannot cross or absorb a protected source boundary.
The workflow builds a smooth surface between the upstream and downstream road
boundaries. It keeps normal junction collision semantics and requests
`keepClear=false` for this isolated road transition. New lanes become available
only at the full-width end. Existing lanes pass through native internal paths.

This is a bounded geometric approximation. SUMO lane width remains a scalar,
and new-lane use inside the taper is not represented. Custom taper outlines are
not accepted here because they need a separate spatial check. The report states
`variable_lane_width_modelled: false` and retains the source evidence.

Native compilation moves the approach portion inside the taper to internal
paths. Compare that complete path, not just the shortened external lane.
The existing geometry report still exposes external-lane trimming differences.

`tests/test_continuous_lane_junction_reuse.py` exercises four synthetic layouts:
an orthogonal four-way junction, an oblique T junction, a five-arm junction,
and a staggered pair. Each includes the taper, bicycle and passenger paths,
entry to an added lane, and explicit junction turns. Separate checks rotate the
taper and inspect its actual SUMO surface. These tests establish reuse for the
tested layouts; they do not establish surveyed accuracy for arbitrary junctions.

The approach follows the [LuST revision record](https://github.com/lcodeca/LuSTScenario/releases/tag/v2.0)
on continuous road sections and the [Munich manual-editing example](https://www.tib-op.org/ojs/index.php/scp/article/download/153-163/427/4608)
on road shapes. SUMO's [lane definitions](https://sumo.dlr.de/docs/Networks/PlainXML.html#lane-specific_definitions)
and [junction blocking rules](https://sumo.dlr.de/docs/Simulation/Intersections.html#junction_blocking)
bound the model interpretation.
