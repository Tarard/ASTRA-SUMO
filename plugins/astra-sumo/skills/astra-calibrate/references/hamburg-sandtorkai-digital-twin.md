# Hamburg Am Sandtorkai Digital Twin

Two corridors share the name. Never let one supply geometry, signal identities or counts to the other.

| Corridor | Official nodes | Use |
|---|---|---|
| Named official-first corridor | `2349` Am Sandtorkai / Großer Grasbrook, `2394` Am Sandtorkai / Am Sandtorpark, `2403` Am Sandtorkai / Osakaallee | the requested twin: stages W0-W5 |
| OSM preset `hamburg_sandtorkai_3_intersections` | `0228` Baumwall, `2421` Am Kaiserkai, `2394` | regression and diagnostic baseline only |

The strongest allowed result is a detector-constrained plausible replay for one declared network, detector mapping, Saturday two-hour window and completion rule. It is not an OD estimate, not individual trajectories and not a model of Hamburg outside the corridor.

## Named corridor: stages W0-W5

Every stage writes a hash-bound manifest into a new, empty directory, and later stages check those hashes. Run `sumo_*` tools as `astra workflow <tool> <request.json> --json` and scripts with `.venv/Scripts/python.exe plugins/astra-sumo/scripts/<script>`.

| Stage | Entry point and key inputs | Output to pass on |
|---|---|---|
| W0 scope | `freeze_hamburg_lsa_nodes.py`, then `freeze_hamburg_named_scope.py` (commands below the table) | `lsa-node-identity.<hash>.json`, named-scope manifest |
| W1 road network | `$astra-build` (official geometry: `build_hamburg_official_corridor_geometry.py`) | frozen `.net.xml` |
| W3a counts | `sumo_hamburg_named_count_scope`: `lsa_identity_file`, `count_node_ids`, `scope_id`, `output_dir`, optional `saturday_date` (`YYYY-MM-DD`) | `sensor-count-scope.manifest.json`; its `artifacts.count_streams_raw` and `artifacts.counts_simulation_15min` |
| W2 signal binding | `build_hamburg_named_signal_binding.py --net-file N --intersection-manifest NODE=PATH ... --signal-stream-file F ... --output-dir D` (optional `--compound-tls-manifest`) | `official-primary-signal-binding.manifest.json` |
| W2 window screen | `screen_hamburg_signal_windows.py --binding-manifest F --candidate-window LABEL=BEGIN_UTC,END_UTC ... --output-dir D` before a full history fetch | screening hint only |
| W2 signal history | `sumo_hamburg_sandtorkai_signal_observations`: `binding_manifest` (W2), `begin_utc`, `end_utc` (ISO-8601 UTC), `output_dir`, optional `cache_dir` | `signal-observations.manifest.json` and its TLS link events |
| W3b detector binding | `sumo_hamburg_named_detector_bindings`: `net_file` (W1), `count_stream_file` (W3a `count_streams_raw`), `output_dir`, `map_files`, `map_kml_files` (one per MAP file, same order) | `detector-binding.manifest.json` |
| W4 demand and replay | `sumo_hamburg_sandtorkai_named_replay`: `net_file`, `signal_binding_manifest`, `detector_binding_manifest`, `count_scope_manifest`, `count_stream_snapshot` (W3a `count_streams_raw`), `canonical_count_file` (W3a `counts_simulation_15min`), `output_dir`, optional `signal_observation_manifest` and `route_sampler_script` | `hamburg_named_replay.manifest.json` |
| W5 ledger | `sumo_hamburg_sandtorkai_execution_plan`: `output_dir`, `stage_manifests` (`["W0=<path>", "W1=<path>", ...]`), optional `stage_feedback` | first invalid stage, next action, invalidated dependants |

- W0: `freeze_hamburg_lsa_nodes.py --output-dir D --bbox W,S,E,N --node "2349=ROAD1|ROAD2" ...` (one `--node` per node; quote the `|`), then `freeze_hamburg_named_scope.py --lsa-identity F --corridor-scope F --signal-discovery F --output F`. No ASTRA tool writes the two inputs. `--corridor-scope` is a hand-written HH-SIB link list (`schema` `torii.hamburg-hh-sib-corridor-scope/v1`, `scope_id` `hamburg_sandtorkai_2349_2394_2403_named_entries_v1`, `links`), the same file that W1's `build_hamburg_named_plainxml.py --corridor-scope` reads. For `--signal-discovery`, call `discover_hamburg_signal_assets(catalog, ["2349", "2394", "2403"], output_dir=D)` from `astra_sumo.core.hamburg_official`, with `catalog` = `download_hamburg_traffic_light_catalog(D)[0]`.
- W2 inputs: `--intersection-manifest NODE=PATH` takes each node's local MAP cell manifest with top-level `node_id` and `movements` (the `<prefix>.manifest.json` of `build_hamburg_official_intersection.py`). `--compound-tls-manifest` takes `hamburg_compound_official_tls.manifest.json` of `build_hamburg_compound_official_tls.py`. `--signal-stream-file` takes a frozen JSON list of official `primary_signal` stream records (`stream_id`, `node_id`, `connection_id`, `ingress_lane_id`, `egress_lane_id`, `signal_group`) as `fetch_hamburg_signal_streams` returns them; no CLI writes it.
- W4 builds virtual detectors, route support and routeSampler demand, runs SUMO and compares E1 counts. Its defaults simulate 0-9000 s and compare 1800-9000 s in 900 s bins (30-minute warm-up).
- Without `signal_observation_manifest`, W4 runs the network's structural all-red programs; that run is diagnostic and normally ends with teleports. A supplied manifest that is not execution-ready blocks the SUMO run.
- A W2 window screen is a preflight hint, not proof that a two-hour history is complete.

## Corridor facts

- `2349` and `2394` are compound sites with shared controllers. Bind each controller across its SUMO junctions with `build_hamburg_compound_official_tls.py`; `build_hamburg_official_intersection.py` builds one local MAP cell and refuses any profile without exactly one conflict core, one SUMO owner and one controller.
- The published Hamburg inventories have no machine-bindable MAP/OCIT-C package for `2403`. Keep it blocked; do not borrow assets from another node.
- The eastbound `2349 -> 2394` lane transition has an official HH-SIB `2 -> 3` cut. The reverse direction has no official lane-birth station; do not invent one.

## Window, counts and signals

- With `saturday_date` the window comes from that day; otherwise the tool tries up to eight recent completed Saturdays (Europe/Berlin), takes the first with a complete window, and picks its busiest complete two-hour window from 300 s cells. A window with a missing 300 s cell is rejected. The ranking score is corridor activity, not unique vehicles.
- Counts are aggregated to 15-minute bins per official field. One virtual E1/E2 detector stands for each (physical node, SUMO lane). Only a MAP-confirmed lane binding can be `active`; a nearest-lane fallback stays `needs_review`. An edge constraint never sums fields from different physical nodes; if a tool reports that one SUMO edge receives detectors from several nodes, split the edge in `$astra-build` or fix the binding.
- E2 lane-area detectors are queue and occupancy diagnostics, not count constraints.
- A signal is bound to one SUMO TLS and `linkIndex` only when every candidate path contains the same single controlled link; a path with zero or several controlled links stays `needs_review`. The last state before the window start initializes time 0. Flashing, dark and unknown states are not exact; a missing state blocks the replay.

## Official sources and licence

Use the REST services and their `@iot.nextLink` pagination; do not scrape portal pages.

| Source | Entry point |
|---|---|
| Count observations (SensorThings v1.1) | `https://iot.hamburg.de/v1.1/` |
| Traffic Lights Data catalog | `https://suche.transparenz.hamburg.de/api/3/action/package_show?id=traffic-lights-data-hamburg6` |
| Primary signal history (v1.0) | `https://tld.iot.hamburg.de/v1.0/` |
| Signal program and cycle second (v1.1) | `https://tld.iot.hamburg.de/v1.1/` |
| MAP XML/KML and OCIT-C assets | `https://daten-hamburg.de/tlf_public/` |
| Count metadata and licence | `https://metaver.de/trefferanzeige?docuuid=2936465E-C045-4F5D-8614-24C3FBB522E2` |
| Signal metadata and licence | `https://metaver.de/trefferanzeige?docuuid=AB32CF78-389A-4579-9C5E-867EF31CA225` |

Keep the licence attribution named in both metadata records, the request URLs, raw response pages and SHA-256 hashes; the saved pages, not the live API, define the replayable snapshot. Never take historical primary-signal state from the v1.1 service when the v1.0 request fails.

## OSM preset (0228, 2421, 2394)

| Purpose | Tool |
|---|---|
| Rebuild the preset network, in this order | `sumo_hamburg_2394_archetype_classify` (`map_file`, `ocit_file`, `source_net_file`), `sumo_hamburg_2394_compound_geometry_first_pass` (`accepted_classification_id`), `sumo_hamburg_2394_tls_topology_materialize` (`plain_source_dir` from `netconvert --plain-output-prefix`), `sumo_hamburg_sandtorkai_corridor_geometry_materialize`, `sumo_road_semantic_bridge` and `sumo_intersection_road_sumo_bind`, `sumo_hamburg_sandtorkai_corridor_tls_materialize` (`road_sumo_binding_file`), then optionally `sumo_hamburg_sandtorkai_mainline_scope_materialize` (`profile` `hamburg_sandtorkai_entry_scope_v1` for the entry arms); what each step does is in the repository's `docs/hamburg-sandtorkai-case-notes.md` |
| Discovery package: OSM cleanup (when `net_file` is omitted), official snapshots, Saturday window, mappings, E1/E2, TLS events, routeSampler | `sumo_hamburg_sandtorkai_digital_twin`: `output_dir`, optional `net_file`, `saturday_date` |
| Official TLS rebuild on a frozen network | `sumo_hamburg_official_tls_rebuild`: `source_net_file`, `signal_asset_dir`, `output_dir` |
| Staged geometry-safe corridor twin | `sumo_hamburg_sandtorkai_geometry_safe_digital_twin`; without `road_sumo_binding_file` it stops after the geometry candidate, so bind road arms with `sumo_road_semantic_bridge` and `sumo_intersection_road_sumo_bind` (`output_file`), then rerun with that file and its SHA-256 |
| MAP, detector-demand and signal stages on an existing candidate | `sumo_hamburg_sandtorkai_corridor_candidate_package`, or separately `sumo_hamburg_corridor_candidate_map_bindings`, `sumo_hamburg_corridor_candidate_detector_demand`, `sumo_hamburg_corridor_candidate_signal_bindings` |
| Demand on a frozen, accepted official-TLS network | `sumo_hamburg_cached_detector_demand` or `scripts/build_cached_detector_demand.py` (below) |
| Signal and detector replay | `sumo_digital_twin_replay_validate` with `expected_counts_csv` = `virtual_expected_counts_15min.csv`, never the per-field `canonical_counts_15min.csv` |

Demand resume on the frozen network:

```powershell
.venv/Scripts/python.exe plugins/astra-sumo/scripts/build_cached_detector_demand.py `
  --official-tls-manifest <hamburg_official_tls_rebuild.manifest.json> `
  --count-stream-snapshot <count_streams.raw.json> `
  --canonical-count-file <canonical_counts_15min.csv> `
  --output-dir <new-dir> --simulation-begin 0 --simulation-end 9000 `
  --comparison-begin 1800 --comparison-end 9000 --interval 900 `
  --exclude-route-edge <dated-closed-edge> --route-sampler-optimize full `
  --route-sampler-script <SUMO_HOME>/tools/routeSampler.py
```

It does not download data, rebuild the network or redo nearest-lane binding; the official-TLS manifest is the authority for network, MAP/OCIT assets and signal bindings, and every recorded hash must match. A dated closed edge must be absent from candidate and generated routes. The result, `cached_detector_demand.manifest.json`, remains review-pending while the official-TLS promotion gate is blocked or historical signal states have not been replayed.
