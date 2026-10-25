# Hamburg Am Sandtorkai Case Notes

Background for the Am Sandtorkai digital twin, moved out of the `astra-calibrate` skill. The tool order an agent follows is in the skill's [Sandtorkai reference](../plugins/astra-sumo/skills/astra-calibrate/references/hamburg-sandtorkai-digital-twin.md). These notes record how the tools read official data, the source precedence, the fixed presets and the corridor-specific gate values.

## Named corridor nodes

Junction names are in the skill reference; these are the official centres.

| Official LSA node | Centre longitude, latitude |
|---|---|
| `2349` | `9.9932270620, 53.5433207972` |
| `2394` | `9.9951328727, 53.5435027113` |
| `2403` | `9.9978391600, 53.5440950084` |

The scope keeps the east-west Am Sandtorkai axis and the entry arms Großer Grasbrook, Am Sandtorpark and Osakaallee. Brooktorkai, Singapurstraße and Tokiostraße are kept only where the official link inventory or a declared boundary-entry profile needs them.

## Source precedence

1. HH-SIB road link, stationing, lane count, speed and carriageway attributes.
2. HVS membership and classification, joined to HH-SIB by exact identifiers and geometry hash.
3. MAP XML/KML lane centre lines, stop-line endpoints and movements.
4. OCIT-C signal groups and structural signal semantics.
5. TLD observations for time-dependent signal state.
6. Other official fine-mapping or engineering plans, as geometry-only evidence.
7. OSM, as optional topology corroboration or a declared fallback; it never overrides a stronger official source.

The network is generated as SUMO PlainXML and compiled with `netconvert`.

## Official data contracts

- The Traffic Lights Data catalog is read from the CKAN `package_show` API. `result.resources` must contain exactly one allowlisted HTTPS resource for each role: `primary_signal`, `signal_program`, `cycle_second`, `MAP-Dateien`, `OCIT-C Dateien` and `TLD Usage Guide`. Missing, duplicate, non-HTTPS, unexpected-host, wrong-layer or wrong-version resources are rejected. A preset URL is used only when the catalog request fails, and that fallback is recorded.
- Count streams: SensorThings `Datastreams` with `serviceName` `HH_STA_Verkehrsdaten_Kfz_Infrarotdetektoren`, `layerName` `Anzahl_Kfz_Zaehlfeld_5-Min`, node field `properties/knotenName`, expanded `Thing` and `Locations`; observations ordered by `phenomenonTime`.
- Signal streams: `serviceName` `HH_STA_traffic_lights`, node field `Thing/properties/trafficLightsID`, motor-vehicle filter `Thing/properties/laneType eq 'KFZ'`. The service version per layer and the v1.0/v1.1 rule are in the skill reference. Cache entries are keyed by API base URL plus query, and are never shared across versions.
- Only same-origin `@iot.nextLink` pages are followed. Cross-origin pagination, malformed collections, duplicate datastream ids, missing locations, non-integer or negative counts and record or page limits block the fetch.

## OSM preset

`hamburg_sandtorkai_3_intersections`: bbox `9.9780,53.5390,10.0005,53.5475`, time zone `Europe/Berlin`, count nodes `0228`, `2421`, `2394`, signal nodes `228`, `2421`, `2394`. Signal node `228` maps to count and MAP node `0228` only inside this preset; no general leading-zero normalization is applied.

| Count node | Centre longitude, latitude |
|---|---|
| `0228` | `9.9820160581, 53.5442574511` |
| `2421` | `9.9850028399, 53.5427011625` |
| `2394` | `9.9951328727, 53.5435027113` |

What each preset network tool does (the call order is in the skill reference's OSM preset table):

- `sumo_hamburg_2394_archetype_classify` records the hashes of the 2394 MAP XML, the OCIT XML and the frozen source network, and keeps one Hamburg controller domain from being flattened into one SUMO junction polygon.
- `sumo_hamburg_2394_compound_geometry_first_pass` runs only after that classification is reviewed and checks the expected source and classification hashes. Its geometry-only candidate keeps official TLS restoration `not_run`.
- `sumo_hamburg_2394_tls_topology_materialize` applies the official delete-five/add-three channelization repair, binds eight official vehicle movements to six control expressions and emits one all-red `HH_2394` placeholder across three signal-bearing owners; two passive priority owners stay unbound.
- `sumo_hamburg_sandtorkai_corridor_geometry_materialize` joins only the confirmed 0228 pair groups and the 2421 signal cluster, keeps the 0228 branch pair `199166130`/`243175302`, and applies three fixed junction-face overrides, which avoids the self-intersecting polygon that joining all 0228 sub-nodes produces.
- `sumo_hamburg_sandtorkai_corridor_tls_materialize` needs the road-arm binding of that exact candidate and requires, for every planned lane connection, MAP lane and stop-line evidence, MAP connector geometry, official movement and control evidence, and the SUMO owner and link index. It emits shared controllers `HH_0228`, `HH_2421` and `HH_2394` with all-red structural programs.
- `sumo_hamburg_sandtorkai_mainline_scope_materialize` keeps the 0228 -> 2421 -> 2394 backbone with the short Am Kaiserkai and Am Sandtorpark approaches, removes the longer Baumwall branch except a 7 m stub for the `HH_0228` MAP link, and joins the two 2394 micro-junctions. The entry-scope profile keeps the Am Sandtorpark, Großer Grasbrook and Singapurstraße entries and keeps the two Großer Grasbrook carriageway conflict cores separate.
- MAP bindings and same-location sensors are regenerated against each new candidate before detector demand runs. The candidate signal binding uses `official_movement_physical_endpoints.json` only to separate a shared-controller path that crosses several controlled links.

## Frozen-network demand gates

For the audited Saturday package, the official-TLS manifest carried six MAP/OCIT assets, a 44/44 effective MAP-lane contract and 27 official signal bindings. The cached-demand gate also required 18 active plus 9 redundant TLD bindings and 33/33 unique physical movement endpoints. Every artifact role had to be unique and every recorded hash had to match.

## Evidence kept per run

A replayable package keeps the frozen network and its hash; official request URLs, raw pages, catalog JSON, selected resources and asset hashes; the selected window with all coverage attempts; 15-minute counts; MAP-to-SUMO lane bindings, the detector and virtual-detector mappings and the virtual expected counts; route candidates and incidence; the complete-section audit, edge counts, routeSampler command and mismatch; the demand file and hash; E1/E2 additionals and outputs; TLS bindings and link events; the detector comparison; summary and tripinfo completion; the twin manifest; the licence attribution; and the open gaps. Run directories are immutable, and a newer API response never replaces an earlier Saturday snapshot under the same identity.

## NetEdit review

Routine geometry images use `astra review render`. For a NetEdit display or mode inspection, `plugins/astra-sumo/scripts/netedit_background_review.py --net-file <candidate.net.xml> --expected-net-sha256 <sha256> --target-junction-id <id> --out-dir <dir> --netedit-binary <netedit>` loads the network with `-s`, captures the target window with `PrintWindow` without global keyboard or mouse input, and rechecks the network hash before and after capture. The captures never open the automatic promotion gate.
