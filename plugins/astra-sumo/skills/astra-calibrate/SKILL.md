---
name: astra-calibrate
description: Use when traffic demand must be fitted to, or checked against, detector or count observations on a fixed SUMO network - detector count audits, detector-to-lane mapping, route support, routeSampler demand fitting, signal and detector replay, and the Hamburg count and Am Sandtorkai digital-twin workflows.
---

# ASTRA Calibrate

Fit or check traffic demand on a frozen SUMO network from detector or count observations. This skill names the ASTRA command, workflow tool or script for each kind of request, the order to run them in, the key inputs and the result fields to read. If the road network must change, stop and use `$astra-build`, then redo every binding that depends on the network.

## Calling ASTRA

- Repository checkout: `.venv/Scripts/astra.exe <command> ... --json`. Installed plugin: `uv run --isolated --frozen --script <plugin-dir>/scripts/run_astra_sumo.py --cli <command> ... --json`.
- `astra workflow <tool> <request.json> --json` calls an allowlisted `sumo_*` function; the JSON object holds its keyword arguments. `astra workflows --scenario <id> --json` prints the real arguments of the cataloged scenarios `detector_audit`, `detector_calibration`, `hamburg_demand` and `hamburg_replay`.
- MCP: the default profile has `astra.demand.audit`; `ASTRA_MCP_PROFILE=legacy` exposes the `sumo_*` tools except `sumo_hamburg_named_detector_bindings`, which only `astra workflow` runs.
- Plugin scripts (`plugins/astra-sumo/scripts/*.py`) import `astra_sumo`; run them with `.venv/Scripts/python.exe` from the repository root. They print their own JSON and set their own exit codes (the Hamburg W0/W2 scripts: 2 on error, 3 when the gate is not `pass`).
- Each `astra` call prints one JSON result. Read `status` first (exit 0: `pass`; exit 1: `partial`, `review_required` and other review states; exit 3: `blocked`, `fail`, `error`), then `warnings`, then the output paths it names. `astra demand audit` nests the tool fields under `payload`.
- Give each run a new output directory outside every input directory. The Hamburg stages and routeSampler refuse existing outputs.

## Choose the path

| Request | Run, in order | Read |
|---|---|---|
| Compare a finished run's E1 output with observed counts | `astra demand audit <expected_counts.csv> <e1-output.xml> <new-dir> --json` | under `payload`: `matched_rows`, `missing_measurement_rows`, `metric_scope`, `MAE`, `RMSE`, `GEH_lt5_percent`, `signed_bias`, `comparison_file` |
| Fit demand to detector counts on any network | the five steps below | each step's fields |
| Replay demand against observed signal states | `sumo_digital_twin_replay_validate` (below) | `count_metrics`, `missing_measurement_rows`, `summary`, `tripinfo` |
| Hamburg five-intersection corridor built from aerial or official MAP data | `astra hamburg bind-aerial-signals`, `sumo_hamburg_named_count_scope`, `sumo_hamburg_count_station_inventory`, `astra hamburg bind-aerial-counts`, `sumo_detector_route_support`, `astra hamburg generate-aerial-demand`, SUMO, `astra demand audit` | `references/hamburg-count-calibration-workflow.md` |
| Hamburg Am Sandtorkai corridor (2349, 2394, 2403) | W3a `sumo_hamburg_named_count_scope`, W2 signal binding and `sumo_hamburg_sandtorkai_signal_observations`, W3b `sumo_hamburg_named_detector_bindings`, W4 `sumo_hamburg_sandtorkai_named_replay`, W5 `sumo_hamburg_sandtorkai_execution_plan` | `references/hamburg-sandtorkai-digital-twin.md` |

## Fit demand to detector counts (any network)

Run the tools as `astra workflow <tool> <request.json> --json`. File formats and mapping rules are in `references/detector-constrained-demand-reconstruction.md`.

1. `sumo_detector_route_support`: `net_file`, `detector_mapping_csv`, `output_dir`; for a pruned network add `boundary_zones_file` (the `boundaries.json` that `astra network select-roads` writes). It passes only when every `active` detector lies on a candidate route. Read `covered_detector_count`, `active_detector_count`, `route_candidate_manifest`, `route_detector_incidence`.
2. `sumo_detector_count_constraints`: `expected_counts_csv` (rows with `edge_id`, `begin`, `end`, `expected_total`, only for completely observed edge cross-sections), `output_dir`, `begin`, `end`. It keeps each source bin and writes `edge_data_file` with one `<interval>` per bin and `<edge id=... count=...>`. Read `route_sampler_interval`: it is the `interval` for step 3, and `null` (with a warning) when the bins do not cover `begin` to `end` at one width without gaps.
3. `sumo_detector_route_sampler_calibrate`: `candidate_manifest_csv` (step 1 `route_candidate_manifest`), `edge_data_file` (step 2), `output_dir`, `begin`, `end`, `interval`, `seed`, `optimize: "full"` (SciPy), optional `route_sampler_script` (otherwise `SUMO_HOME`, `PATH` or the installed `sumo` package's `tools` directory). `pass` means zero mismatch deficit; `partial` means unmatched counts; `blocked` means routeSampler was not found. Read `demand_route_file`, `mismatch`, `solution.active_route_count` and `identifiability` (`rank`, `nullity`, `equivalent_route_classes`, `unobserved_route_ids`).
4. Run SUMO on the frozen network with the demand file, E1 detectors whose `period` equals the bin width, and `--summary-output`, for example `astra workflow sumo_run_config` with `config_path`. Take completion from the last summary step.
5. `astra demand audit` with the expected counts and the E1 output.

## Replay against observed signal states

`sumo_digital_twin_replay_validate`: `net_file`, `route_file`, `e1_additional_file`, `e2_additional_file`, `tls_events_csv`, `expected_counts_csv`, `output_dir`, `replay_end`, `completion_end`, `comparison_begin`, `comparison_end`. The events CSV has `simulation_time`, `sumo_tls_id`, `sumo_link_index`, `sumo_state`, `source_state`, and every passenger TLS link needs a state at time 0, otherwise the result is `blocked` at `tls_passenger_link_coverage`. It drives the signals through TraCI, compares E1 `nVehContrib` per bin and returns `pass` only when no bin is missing and all vehicles complete. Without observed signal states, use steps 4-5 above instead.

## Facts that prevent mistakes

- Detector counts constrain routes; they do not observe origins and destinations. Many route sets fit the same counts, so a routeSampler result is one plausible demand, not the true OD matrix. Vehicles spawned on detector edges only test detector placement.
- A missing detector interval is unknown, never zero. A measured zero is a valid value.
- Fit demand on a frozen network. Do not improve a fit by changing road geometry, vehicle permissions or the observations, and never hand-edit a generated `.net.xml`.
- A count on some lanes of a multi-lane edge is not the flow of that edge. Constrain complete cross-sections only.
- A SUMO load or a zero routeSampler mismatch is not proof of a correct replay. Report loaded, inserted, arrived, running, waiting, teleports and collisions before any fit metric; backlog or teleports invalidate a perfect count match.
- Never invent a signal state or a count to fill a gap; leave the stage `blocked`.
- Say so when calibration and validation use the same observations.
- Keep the data licence attribution (Hamburg: Datenlizenz Deutschland Namensnennung 2.0). Do not publish raw proprietary feeds, private station tables, unmasked sensor locations or individual travel behaviour. Tool outputs keep the caller's detector and edge ids, so anonymize the inputs before you publish outputs.

## Report

State the observations and time window, what was fitted, the detector attribute and metric, the completion counts, the routeSampler deficit and identifiability, any manual step, and the remaining coverage limits.

## References

- `references/detector-constrained-demand-reconstruction.md`: input file formats, detector-to-lane mapping rules, count semantics, time bins and diagnosis of a poor fit.
- `references/hamburg-count-calibration-workflow.md`: Hamburg five-intersection request files, stage order, SUMO check, and the README Ring1 case with departure retiming.
- `references/hamburg-sandtorkai-digital-twin.md`: Am Sandtorkai stages W0-W5, official sources, the older OSM preset and frozen-network demand resume.
