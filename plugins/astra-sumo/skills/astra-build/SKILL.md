---
name: astra-build
description: Use when a request builds, cleans, audits, edits, compares or reviews a SUMO road network or its traffic infrastructure - an OSM area, an existing .net.xml, a Hamburg drawing or MAP/KML with aerial images, traffic lights, intersection or OCIT-C signal-device classification, parking buildings, aerial review images or NetEdit edits. Names the ASTRA commands to run, in order.
---

# ASTRA Build

This skill maps a network request to the ASTRA code that already does the work. Find the request in the table, run
its steps in order and read the named outputs. Open a reference only when a step needs its detail.

## Calling ASTRA

- In a checkout: `.venv/Scripts/astra.exe <command> ... --json`. In an installed plugin:
  `uv run --isolated --frozen --script <plugin-dir>/scripts/run_astra_sumo.py --cli <command> ... --json`.
- Each command prints one JSON object. Exit code 0 means `pass`, `measured` or `no_effect`; 1 means review is
  needed (`review_required` and similar); 3 means `blocked`, `fail` or `error`. Read `status`, `warnings`,
  `findings` and the file paths in the result.
- `astra workflow <tool> <request.json> --json` calls one allowlisted `sumo_*` or `hamburg_*` function with the
  request file's keys as keyword arguments. `astra workflows --json` lists the catalog scenarios with their real
  arguments; `astra workflow selected <selection.json> --execute --json` runs one from `user_request`,
  `scenario_id`, `reason` and `arguments` (without `--execute` it only checks readiness).
- MCP: the default profile serves `astra.place.resolve`, `astra.network.audit`, `astra.network.compare`,
  `astra.intersection.classify`, `astra.signal.classify` and `astra.review.create`, which match the CLI commands
  of the same name. Those commands print the MCP envelope: `status`, `findings` and `artifacts` at the top, every
  other field (such as `warnings`) under `payload`. `ASTRA_MCP_PROFILE=netedit` serves the four `astra.netedit.*`
  tools. Everything else here is CLI-only.
- Give every run a new output directory outside the input folders. Several commands reject an existing one.

## Choose the path

| Request | Steps, in order | Read |
|---|---|---|
| Network for a place or bbox from OSM | `astra place resolve "<place>" --json` when only a name is given (check `payload.candidate_display_name`, take `payload.candidate_bbox`); write the cleanup request; `astra workflow sumo_osm_cleanup_workflow <request.json> --json` | `status`, `gate_status`, `claim_tiers`, `warnings`, `workflow_review_html_file`; fields and gates in `references/osm-to-sumo-workflow.md` |
| Network as it was at a past date | `astra workflow sumo_osm_build_network` with `bbox`, `output_dir`, `historical_date` (Overpass form, such as `2013-06-01T00:00:00Z`); then the cleanup with `source_osm_path` set to the returned `source_osm_file` | as above |
| Check an existing network | `astra network audit <net.xml> <new-dir> --json` (`--profile quick` checks topology only); `astra network routeability <net.xml> <new-dir> --json`; optionally `astra network review-effects <net.xml> <new-dir> --parent-net-file <parent.net.xml> --json` to order findings by traffic effect | `findings`, report paths in `artifacts`, routeability `status` |
| Compare a source and a candidate | `astra network compare <source.net.xml> <candidate.net.xml> <new-dir> --json`; `astra network routeability` on the candidate | Connection Mode changes the candidate introduces |
| One typed edit: junction join or shape, lane connection, lane count, width, permissions, speed, stop line, duplicate signal | ledger with one operation; `astra network verify-edit <ledger.json> <new-dir> --json` (`--facts-file <facts.json>` also measures road facts) | `status`, `warnings`, `candidate_net_file`, `new_lane_service`; ledger format in `references/osm-to-sumo-workflow.md` |
| Delete or add roads, several reviewed edits | `sumo_network_corridor_edit_ledger` (`net_file`, `output_dir`, `operations`), `sumo_network_corridor_materialize_variant` (`ledger_file`, `output_dir`), `sumo_network_corridor_candidate_gates` (`source_net_file`, `candidate_net_file`, `output_dir`, `materialization_report_file`) through `astra workflow` | gate `status` per check; operation items in `references/osm-to-sumo-workflow.md` |
| Main roads only | `astra network select-roads <net.xml> <new-dir> --keep-tier major --json`; repeat `--protect-edge-id` or `--protect-way-id` for study and detector roads | kept tiers, added approaches and boundaries (no flow rates); compare with `normalized_full_network`, the loadable full baseline, not the raw import |
| Traffic lights: do they exist, one controller per intersection | `sumo_tls_audit`, `sumo_tls_multisource_review`, `sumo_network_tls_aggregation_variant` through `astra workflow` | cluster CSV with map links; order in `references/osm-to-sumo-workflow.md` |
| Fragmented or overlapping junctions | `astra network audit`, then `sumo_network_junction_aggregation_variant` through `astra workflow` | order in `references/osm-to-sumo-workflow.md` |
| Parking building or underground garage | `astra network parking-facilities <net.xml> <full-osm.xml> <new-dir> --expected-net-sha256 <sha256> --expected-osm-sha256 <sha256> --json` | `parking-aware.net.xml`, `parking-facilities.json`; give the report to `astra review render`; spaces, occupancy and dwell times stay unconfigured |
| Measure against dated road facts | `astra network calibrate <net.xml> <facts.json> <out.json> --json` (`--parent-net-file` to compare a parent); facts table `astra.network-facts/v1`, schema `schemas/product/astra.network-facts.v1.schema.json` in a checkout | matches, mismatches and unknowns per layer |
| Review images | `astra review render <request.json> <new-dir> --json` with a georeferenced image; `astra review create <new-dir> --net-file <net.xml> --json` without one | `render_status`, `review.html`; request in `references/review-images.md` |
| Manual NetEdit edit or a NetEdit display question | `astra-netedit`, `astra netedit review`, the `astra.netedit.*` loop or `netedit_background_review.py` | `references/review-images.md` |
| Classify an intersection (optionally with Hamburg HH-SIB road categories) or OCIT-C signal devices (read-only) | `astra intersection classify <osm.xml> <center-node-id> --traffic-side right --json`; `astra signal classify <ocit-c.xml> --expected-node-id <id> --json` | `references/classification.md` |
| Hamburg road from a drawing, or from MAP/KML and aerial images | `astra hamburg build-network <request.json> <new-dir> --json` | `workflow.manifest.json`, `network-handoff.json`; `references/hamburg-five-intersection-aerial-workflow.md` |
| Synthetic test intersection or one local OSM patch | `sumo_intersection_scene_workflow`, `sumo_nema_four_way_reference_workflow` or `sumo_intersection_clean` through `astra workflow` | arguments in `references/osm-to-sumo-workflow.md`; not for reconstructing a real road |

Every builder and variant writes a separate candidate. After a change, run `astra network compare`,
`astra network audit` and `astra network routeability` on the candidate before calling it better. Count fitting on
a finished network belongs to `$astra-calibrate`. Report the source basis, the candidate paths, each command's
`status` and what remains unreviewed.

## Facts that prevent mistakes

- Never edit or overwrite an input. Change PlainXML and rebuild with `netconvert`, or use `astra network verify-edit`.
  Never hand-edit a generated `.net.xml`, and save GUI edits only to a candidate.
- SUMO load, `netconvert` success, routeability and images are construction evidence. They do not prove that
  topology, lanes, signals or geometry match the field.
- An OSM node, a physical conflict core, a SUMO junction and a signal controller are different objects. Distance can
  nominate a junction join; it never proves one.
- The cleanup cannot record light reviews, so over an area with traffic lights a structurally sound run ends
  `review_required` (exit 1) with `gate_status.tls_reality_audit` `review_required`. Any other failing gate makes it
  `fail`. Review the lights with the traffic-light steps in `references/osm-to-sumo-workflow.md`.
- Check that a traffic light exists on a map that suits the region: Google Maps where it is reliable; Amap, Baidu or
  Tencent in mainland China, stating the WGS84/GCJ-02/BD-09 conversion. Use the current map unless the user names
  a past date.
- Keep the source year and authority. A newer map does not replace a dated drawing, and a download date is not a
  data year.
- Use Google Maps only for manual visual checks: no scraping, tracing or derived datasets. Do not reproduce RiLSA,
  DIN/EN or OCIT text, and do not claim conformance from a classification.
- An OCIT-C `Gruen` lamp-output slot is an interface slot, not proof of a green lamp; it can carry `Ton` or `Vibra`
  outputs.
- For lane-count edits on shared roads, set `vehicle_class`; `num_lanes` still counts every SUMO lane. Read
  `new_lane_service` before calling a new lane a through lane. Widen a sidewalk with `set_lane_width`. A new
  movement at a signal in `connections` needs `tl` and `linkIndex` of that junction's existing controller.
- An OSM tag does not prove that a road exists, and a roof can hide a real one. Before deleting a suspected false
  road, trace its SUMO objects and compare dated imagery with access evidence; use `parking-facilities` where a
  parking building is involved.
- Never drive NetEdit with generic mouse or keyboard input on the user's desktop.

## References

- `references/osm-to-sumo-workflow.md`: cleanup request and gates, traffic-light review, junction joins,
  reference-matched builds, the typed edit ledger, synthetic intersections, background layers.
- `references/review-images.md`: the `astra review render` request, image colours, schematic review page, NetEdit
  options.
- `references/classification.md`: intersection and signal-device classification, road arms, OSM way fragments,
  Hamburg HH-SIB road categories.
- `references/hamburg-five-intersection-aerial-workflow.md`: Hamburg drawing-led and MAP/aerial reconstruction.
