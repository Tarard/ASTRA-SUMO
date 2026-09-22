# Hamburg topology-only construction

Use `torii hamburg build-network` for either construction-drawing-led work or
the existing MAP/aerial corridor mode. Detector counts and historical signal
states are not required.

For a reviewed construction drawing with OSM or Google Maps as supporting
sources, start with [construction-plan.md](construction-plan.md) and
[construction-plan.request.example.json](construction-plan.request.example.json).
The drawing's lane counts, uses, and connections remain authoritative. The
user's target year is preserved, including historical scenarios such as 2013.

The remaining instructions on this page describe MAP/aerial mode: raw OSM,
official MAP XML and matching KML, georeferenced aerial images, official LSA
identities, and selected road names. Select at least two ordered intersections
for that corridor mode. Its inputs are not prerequisites for the drawing-led mode.

For a local comparison of road-use observations, cycling records, and MAP
lane lines, see the separate [read-only road-use review](road-use-review.md).
To add image-boundary candidates and a public road reference to construction,
see [aerial boundaries with Hamburg road references](aerial-road-boundaries.md).
For source-bound RASt/ReStra checks and manual engineering-plan dimensions,
see the independent [road-design rule study](design-rule-study.md).

## Build and check

1. Copy [request.example.json](request.example.json) beside your frozen source files.
2. Replace the source paths and each `REPLACE_WITH_SHA256` value with the actual file hash.
3. Set the OSM bounds, ordered intersection IDs, road names, aerial year, and exact image bounds.
4. Run the command with a new output directory:

```powershell
torii hamburg build-network <request.json> <new-output-dir> --json
```

The example coordinates describe the named Sandtorkai crops. Use those values
only for images with those exact bounds. An arbitrary aerial screenshot needs
geographic bounds before it can be used here. Keep each image's full extent;
do not assign its bounds to a differently cropped image.

The command builds the source network, binds official physical areas, selects
movement geometry, and builds the candidate from fresh intermediate files.
It then checks construction, each official movement, and both corridor
directions. It also tests the same diagnostic trips on the source and candidate.
It does not take an old `source_net`, `cluster_binding`, or `movement_summary`
as construction input. Existing source files remain unchanged.

To try local junction outline tightening, set
`construction.junction_contours` to `"guarded"`. The default is `"preserve"`.
The guarded method removes only areas proved clear of known lane surfaces
and road mouths. It compiles each proposal with the same construction inputs
and connection patch. If geometry, permissions, speeds, signal programs or
connection records change beyond the permitted outline, that part retains
its previous boundary. Read `movement_materialization.junction_contours` in
the candidate manifest for accepted, retained and rejected parts. The actual
compiled shape still requires aerial-image review.

Read `workflow.manifest.json` first. Its stage records link to the detailed
reports. `first_failed_stage` identifies a stopped stage. When a candidate
exists, `network-handoff.json` records its file and hash plus the workflow
manifest hash. Its `construction_decision` preserves `review_required` or
`blocked` when checks are incomplete. Its presence does not mean the network
passed all checks.

## Individual steps for diagnosis

The earlier commands remain available when a particular step needs investigation.

1. Build a source network with `torii workflow sumo_osm_build_network <request.json> --json`.
2. Run `sumo_tls_audit` through the same workflow command.
3. Bind the selected official nodes with `hamburg_corridor_bind_tls_clusters`.
4. Run `torii hamburg aerial-movements <request.json> <new-plan-dir> --json`.
5. Run `torii hamburg combine-aerial-movements <request.json> <new-network-dir> --json`.
6. Check `official_connection_audit`, internal-path continuity, and the selected corridor's bidirectional paths.
7. Run `torii network movement-probes <manifest.json> <new-probe-dir> --json`.

The cluster-binding request uses the existing function arguments in
`core/hamburg_corridor_candidate.py`. Its `selection_file` can use this content:

```json
{
  "schema": "torii.hamburg-five-corridor-selection/v1",
  "selection_mode": "topology_only",
  "ordered_node_ids": ["2349", "2394"]
}
```

The schema name is retained for compatibility. The selected list is not fixed
to five nodes. Cluster binding identifies physical areas, not individual lanes.

The movement request supplies each selected node's `map_xml`, `map_kml`, and
`aerial_image` with paths and SHA-256 hashes, plus `bbox_epsg25832` and
`aerial_year`. The new plans include official lane identities, geometry, and
their coordinate reference system. The summary freezes each `plan_sha256`.
Rebuild old plans rather than adding hashes to plans that lack lane evidence.

The combination request uses schema
`torii.hamburg-aerial-corridor-candidate-request/v1` and three hash-bound
artifacts: `source_net`, `cluster_binding`, and `movement_summary`.
Paths can be relative to the request file. SUMO binaries must be available.

Torii matches official lanes before it assigns movement curves. It gives
explicit lane connections to netconvert, which generates internal paths and
conflict records. A rejected curve does not remove an otherwise confirmed
connection. Unmapped side roads remain present for review.

An official stop section need not coincide with an existing OSM road endpoint.
Torii checks its position on the source lane and connected internal lanes of
the same junction. It retains direction and vehicle permissions during this
match. Joining several source nodes must preserve the complete inlet-to-outlet
paths, including official movements that describe only part of a joined path.
An unchanged, uniquely identified boundary lane remains identifiable after
trimming. A later distance tie does not erase it. Conflicting identities,
changed permissions, and newly added lanes still require separate checks.

A fixed junction polygon must remain fixed when the network is read again.
The fallback surface drawing must not replace a custom shape established by
the boundary construction. Check the final network with netconvert, not only
an intermediate network or a screenshot.

## Read the result

- Read connection structure, geometry review, and vehicle passage separately. A pass in one does not replace the others.
- `connection_structure=pass` means the generated internal paths passed the structural checks.
- `official_connection_audit` separates required, actual, missing, extra, and unresolved connections.
- `topology_complete` is false when official connections remain unresolved. Loading SUMO does not override that result.
- A routeability failure remains visible even when it occurs outside the selected intersections. Use paired source/candidate checks to locate it.
- Inspect the actual network at a useful scale. A review image does not replace the connection checks.

`topology_complete` describes the required official connections. It does not
certify the chosen curve shape, every surrounding road, or field conditions.
Keep MAP curves, accepted aerial traces, and SUMO-generated fallback curves
separate in the report. An inlet that needs no documented lane addition can
be `not_applicable`; unresolved additions remain `review_required`.

Generated diagnostic trips use reachable pairs from the original network,
including connection permissions. The candidate does not choose which pairs
are tested. Both networks receive the same requests. Reports distinguish
unreachable or unsampled endpoints from roads that were actually traversed.
Do not interpret a vehicle count alone as coverage of the whole corridor.

The routeability check requires normal completion within the same declared
`max_end`. Waiting-time teleport recovery is postponed to that finite limit.
This prevents a long red light from causing an artificial jump along the
route. A vehicle that remains stuck still makes the check fail. Native
vehicle-route output verifies every complete road sequence and normal
arrival. Collisions and other abnormal recoveries remain failures.

The full official geometry participates in curve selection and fallback.
The method combines official data and imagery. It is not an independent
image-only reconstruction or a claim of superiority over the official MAP.

## Continue with traffic counts only when requested

Finish the road checks and freeze the chosen network hash before binding
counts, fitting demand, or reconstructing signal operation. Those tasks use
the existing signal/count/demand commands in a separate directory. Follow the
[count-calibration workflow](../../plugins/torii-sumo/skills/simulation-helper-skill-for-eclipse-sumo/references/hamburg-count-calibration-workflow.md)
for that work. Road construction never requires a calibration result.

## Replay the reviewed V1 connection correction

`lsa535-reviewed.con.xml` records the two explicit LSA535 connections added
during the V1 investigation. It is a case-specific correction, not a rule for
all intersections. Its reference input network SHA-256 is
`779de51512b5c18bb21a9f85496cb5da7eed76bb8d63f04811bcc43c34af8fea`.
This patch does not restore the historical V1 signal program.

From the repository directory, after confirming the source network:

```powershell
$patch = (Resolve-Path examples/05_hamburg_topology/lsa535-reviewed.con.xml).Path
$digest = (Get-FileHash -LiteralPath $patch -Algorithm SHA256).Hash.ToLowerInvariant()
torii hamburg repair-lane-connections <source.net.xml> <new-output-dir> `
  --connection-patch $patch --patch-sha256 $digest --json
```

The command validates edge and lane identities, rebuilds through netconvert,
and checks the exact connection changes and all internal paths. It does not
run the older LSA119/diverge repair when an explicit patch is supplied.
Without `--connection-patch`, the command still selects that older, case-specific repair.
The existing CLI returns exit code 1 for `review_required`. Read the manifest
to distinguish a successful diagnostic construction from failed checks.

An explicit patch uses only lane-level `connection` and `delete` records.
Do not supply guessed deletions or apply this example to unrelated edge IDs.

## Sources and limits

- Official MAP/XML/KML: [Hamburg public traffic files](https://daten-hamburg.de/tlf_public/).
- Official aerial images: [Hamburg DOP WMS](https://geodienste.hamburg.de/wms_dop_zeitreihe_belaubt?SERVICE=WMS&REQUEST=GetCapabilities).
- Am Sandtorkai source OSM: `benchmarks/hamburg_sandtorkai_w1_v1/hamburg_sandtorkai_source_bbox.osm.xml.gz`.
- SUMO construction: [PlainXML and connection patches](https://eclipse.dev/sumo/docs/Networks/PlainXML.html).

Keep source versions, download links, licenses, and hashes with each run.
The Hamburg source adapter still expects Hamburg KML conventions. Other cities
may need a different source adapter even when their coordinates can be transformed.

As checked on 2026-09-07, the official directory has MAP data for 2349 and
2394, but not 2403. Keep 2403 and its OSM roads if they are inside the chosen
area. Do not call that road portion an official-MAP reconstruction.
