# Intersection and signal-device classification

Both commands are read-only. They hash the input, change no network, and keep automatic joins, lane connections
and signal binding blocked. Use them to choose the next build step, not as approval for it.

## Intersections

`astra intersection classify <osm.xml> <center-node-id> --traffic-side right --json` (`left` for left-hand
traffic). In `payload`, read `status` (generation), `disposition` (`suggest` or `review`), `type_recognition`,
`archetype_profile` and `evidence_summary` (approach count, movement status, unknown road arms).

- Each field carries `value`, `status` (`observed`, `rule_derived`, `reviewed`, `contradicted`, `unknown`),
  `decision`, `evidence_ids` and `alternatives`. `unknown` is a valid answer; do not replace it with a default.
- The profile keeps independent dimensions: grade relation, interaction kind, cell structure, arm model (exact
  `arm_count`, `entry_count`, `exit_count`), angular distribution and minimum angle, circulation, carriageway
  organisation, movement transforms, channelisation, control rule and controller topology.
- `T3`, `Y3`, `X4`, `irregular_*` and `roundabout` are derived aliases. Never use an alias as evidence. Node degree
  alone cannot give one: three OSM ways can be one two-way road plus a one-way carriageway.

## Road arms and OSM way fragments

- An arm's category comes from the official inventory first, then a supplied RIN/RASt category, then a network role.
  The raw OSM `highway` class is kept but never promoted to an official category. Member ways that disagree make
  the arm `contradicted`.
- Resolve arm identity before the connection relation (`through_axis`, `branch_to_axis`, `link_to_network`,
  `access_to_network`, `arm_to_arm`). A relation is a hypothesis; a SUMO connection still needs lane, legal-turn
  and control evidence.
- An OSM way boundary is neither necessary nor sufficient for a road boundary. Keep four levels apart: OSM way
  fragment, carriageway segment (what binds to a SUMO edge), road axis (identity), official link interval.
- Matching a road axis never permits concatenating SUMO edges. Keep the splits at sensors, stop lines, signals,
  restrictions and lane changes. Opposite carriageways stay separate directional edges; `*_link` and slip roads
  are connectors, not continuations; bridges and tunnels stay grade-separated.
- A bbox-clipped extract can create false road ends. Check the complete way before treating an end as real.

## Hamburg official road categories (HH-SIB)

1. `astra workflow sumo_road_semantic_bridge` with `osm_file`, `sumo_net_file`, `hh_sib_snapshot_file`,
   `hh_sib_request_url`, `hh_sib_bbox`, `target_time`, `retrieved_at` and a new `output_dir` outside the input
   folders. Add `sumo_imported_from: "osm"` and `sumo_imported_source_sha256`, or the OSM-to-SUMO lineage stays
   blocked. Its `artifacts` list the bridge report and a road-network-evidence projection.
2. Pass the projection as `--road-network-evidence-file` to `astra intersection classify`, or run
   `sumo_intersection_road_sumo_bind` with `osm_file`, `seed_osm_node_id`, `road_network_evidence_file`,
   `bridge_report_file` and `output_file`. An intent marked `ready_for_lane_connection_review` is not an approval.

## Signal devices (OCIT-C)

`astra signal classify <ocit-c.xml> --expected-node-id <id> --json` returns `review_required` (exit code 1) when it
succeeds. In `payload`, read `logical_signal_groups`, `profiles`, `unassigned_signal_generators`,
`unresolved_dimensions` and `automatic_binding_gate`.

- Keep `Signalgruppe` (logical group), `Signalgeber` (physical head), `Signalbild` (displayed pattern) and the
  semantic state (`Frei`, `Gesperrt`) apart. One group can drive several heads.
- Never infer a signal group from head shape or a shared pole; it needs controller or supply data, or observed
  identical state traces.
- Tram (BOStrab) signals use white bars, points and triangles, not red/yellow/green.
- Bind a device to lanes or movements only from stop-line, lane, MAP or connection evidence; proximity is a
  candidate feature. Timing, phases and controller behaviour are separate reviewed steps.
