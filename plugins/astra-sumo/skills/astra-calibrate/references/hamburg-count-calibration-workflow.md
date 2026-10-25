# Hamburg Count Calibration on a Fixed Road Network

Use this for the Hamburg five-intersection corridor whose road network was built from aerial images or official MAP data (`astra hamburg aerial-movements`, `astra hamburg combine-aerial-movements`, `astra hamburg build-network`; see `$astra-build`). Building the network does not run these stages, and counts or signal histories are not needed to build it.

Freeze the network first. Check `network-handoff.json`: a `review_required` or `blocked` `construction_decision` is not an accepted network. The stages below do not read the handoff file; put the same network and candidate manifest into their requests.

## Request files

Each `astra hamburg <stage> <request.json> <new-output-dir> --json` call needs a JSON object with a `schema` field. Every file in it is written as `{"path": "...", "sha256": "..."}` and must match its hash. The output directory must not exist; the stage writes `manifest.json` there.

| Stage | Command and request `schema` | Request keys |
|---|---|---|
| 1 Signal identities | `astra hamburg bind-aerial-signals`, `torii.hamburg-aerial-signal-binding-request/v1` | `candidate_manifest` (the `manifest.json` of `combine-aerial-movements`), `movement_summary` (`corridor-summary.json` of `aerial-movements`), `signal_stream_snapshot` (frozen JSON list of official primary-signal stream records) |
| 2 Count scope | `astra workflow sumo_hamburg_named_count_scope` (keyword arguments, no `schema`) | the corridor's count nodes, called as in stage W3a of `hamburg-sandtorkai-digital-twin.md`; writes `sensor-count-scope.manifest.json` |
| 3 Station inventory | Python only: `materialize_hamburg_count_station_inventory(output_dir=Path(D), client=SensorThingsClient("https://iot.hamburg.de/v1.1/"), requested_node_ids=[...])` from `astra_sumo.core.hamburg_named_count_scope` and `astra_sumo.core.hamburg_official` | writes `count-station-inventory.manifest.json` into an empty directory |
| 4 Count binding | `astra hamburg bind-aerial-counts`, `torii.hamburg-aerial-count-binding-request/v1` | `candidate_manifest`, `count_scope_manifest` (stage 2), `count_station_inventory_manifest` (stage 3), `map_xml_files` (list), optional `map_kml_files` |
| 5 Route support | `astra workflow sumo_detector_route_support` | `net_file` (candidate network), `detector_mapping_csv` (stage 4 `artifacts.detector_mapping`), `output_dir` |
| 6 Demand | `astra hamburg generate-aerial-demand`, `torii.hamburg-aerial-demand-request/v1` | `count_binding_manifest` (stage 4), `route_candidate_manifest` (stage 5), optional `scenario` (`conservative`, `medium`, `high`), `minimize_vehicles`, `diversify_routes` |
| 7 Diagnostic signal plan, optional | `astra hamburg build-protected-signals`, `torii.hamburg-protected-signal-candidate-request/v1` | `net_file`, `signal_binding_csv` (stage 1 `artifacts.bindings`), `route_file` (stage 6 demand), `tls_ids`, optional `green_seconds`, `yellow_seconds`, `cycle_seconds`, `green_overrides`, `offset_overrides` |

## Reading the results

- Signal binding accepts a signal only when node, official connection, ingress lane and egress lane match exactly and the internal lane owns one SUMO controlled link. MAP or TLD version mismatches are reported, not repaired by distance. It binds identities only, not timing.
- Count binding uses Hamburg's published `Zählstelle` compositions. Direction 1 and 2 stations are directional totals; direction 0 is a QA total only. The station's `zusammensetzung` lists the member fields; never replace it by summing nearby fields. A `Z.*` asset number is not a MAP, TLD or SUMO lane number. Use the station's physical direction; do not guess a turn when `fahrspur` is empty. A station whose members map to more than one directed SUMO edge stays validation-only, and a node without a published composition stays unconstrained.
- Demand uses only active station bindings and complete passenger-lane cross-sections, always sums the published members (the `scenario` label never changes an official count), and runs routeSampler over 0-9000 s in 900 s bins. Its manifest names `virtual_expected_counts`, `e1_additional`, `station_group_e1_additional`, `station_detector_bank`, `e2_additional`, `edge_counts` and the routeSampler `demand_route_file`. Require zero deficit and overflow. It has no routeSampler path key: set `SUMO_HOME` (repository venv: `.venv/Lib/site-packages/sumo`) or put `routeSampler.py` on `PATH`, otherwise it ends with `status` `error`.
- The protected signal plan uses protected green only, keeps Hamburg signal groups intact and can share one cycle across controllers. It is a diagnostic plan, not historical operation, and green or offset overrides must keep the declared cycle. It writes a separate network: read `candidate_network.path` (`protected-signals.net.xml`) and inspect `tls_plans`.

## Run and check SUMO

- Write a `.sumocfg` with the network (the frozen network, or `candidate_network.path` from stage 7 when you use the protected signal plan), `route_sampler.demand_route_file`, the manifest's `e1_additional`, `station_group_e1_additional` and `e2_additional`, and a summary output, and run it with `astra workflow sumo_run_config` (`config_path`) over the full horizon including warm-up. If departures continue up to the end of the comparison window, add a later flush horizon before you judge completion.
- Take the warm-up and comparison window from the count-scope manifest (`parameters.warmup_seconds`, `selected_window`). Drop the warm-up bins from `virtual_expected_counts`, then run `astra demand audit <expected.csv> <e1-15min.xml> <new-dir> --json`.
- Compare counts only when every row of `station-detector-bank.json` has `placement_basis=physical_field_projections`. A conserved or movement proxy is calibration-only evidence, not a same-location twin. Rows with `exact_physical_field_positions=false` (low-confidence or collapsed projections) are diagnostic, not exact physical matches.
- To isolate a backlog cause, keep network, routes, seed, departure window and flush horizon fixed, and compare the current signals, `--tls.all-off` and a declared scaled-demand run. Signals off is diagnostic only. If scaled demand clears and full demand does not, look at boundary storage and demand before signal timing.
- For time profiles, a departure interval is not a detector-passage interval. Include the traffic already in the network before the first comparison bin instead of forcing first-bin counts to start at the boundary at time 0.

## README Ring1 case (repository checkout only)

The first paragraph of `docs/hamburg-readme-case.md` links the current best result; its `benchmarks/hamburg_ring1_*` package README gives the inputs and the reproduce command. Start new work from that package, not from an older one. Entry scripts in `plugins/astra-sumo/scripts/`:

- `run_hamburg_readme_pipeline.py prepare <repo-root> <new-dir>`, then `local-lanes <prepared-dir> <new-dir>` or `run <prepared-dir> <new-dir>` (`run` accepts `--hold-observed-lanes`, and with it `--hold-junction-body`).
- `run_hamburg_od_spsa.py --prepared-dir <dir> --output-dir <new-dir>` (optional `--settings`, `--backend traci|libsumo`, `--parallel-pair`, `--stop-on-unsafe`).
- `trace_hamburg_collisions.py <case-dir> <new-dir> [--end <s>]` reproduces a recorded collision.
- `summarize_hamburg_readme.py <repository> <prepared_dir> <native_dir> <figure_dir> <output_dir>` writes the field and station comparisons.

Only a healthy run (all vehicles arrive, no collisions, teleports or discards) can be accepted; a backlogged run is a diagnostic loss.

### Departure retiming

`.venv/Scripts/python.exe plugins/astra-sumo/scripts/hamburg_event_retiming.py --source-run <run-dir> --canonical-counts <counts.csv> --output-dir <new-dir>` (optional `--sumo-binary`, `--seed`, `--max-trials`, `--max-shift`).

- `<run-dir>` is a Ring1 V1 run directory, not the output of `generate-aerial-demand`: it holds `count-audit.json` (window and artifact hashes), `network.net.xml`, `e1.add.xml`, `instant.add.xml`, `station-detector-bank.json` and `demand.rou.xml`. A benchmark package's `inputs/` holds these gzipped; its `reproduce.py` unpacks them and passes `counts.simulation.15min.csv` as `--canonical-counts`. The script stops if a hash differs or the reproduction of that run differs.
- It fixes each vehicle's speed factor and the seed, tests single departure shifts in full SUMO runs, and keeps a shift only when total error falls, no station gets worse, and every vehicle completes without warnings, collisions or teleports. Read `status` (`exact_fit` or `bounded_search_complete`) and `best` in `result.json`.
- An exact fit holds for the calibration window only; it is not held-out validation.
