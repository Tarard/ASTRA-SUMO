---
name: astra-report
description: Use to explain, compare, or write up completed SUMO and traffic-control results - which run is better, what the numbers mean, whether a baseline/variant pair is fair, how strong a claim the evidence allows, a full experiment report, or plain-English editing of the text. Names the ASTRA commands to run on finished outputs, in order.
---

# ASTRA Report

Work from completed outputs: check them with the commands below, then write the answer. Do not rerun or change an experiment unless the user asks.

## Calling ASTRA

- In a repository checkout: `.venv/Scripts/astra.exe <command> ... --json` (or `.venv/Scripts/python.exe -m astra_sumo.cli ...`). In an installed plugin: `uv run --isolated --frozen --script <plugin-dir>/scripts/run_astra_sumo.py --cli <command> ... --json`.
- `run compare`, `config inspect` and `demand audit` print `status`, `claim_status`, `next_actions` and the tool's own fields under `payload`; `astra workflow <tool>` prints the tool's fields at the top level. Exit code 0 means `pass`; 1 means `warn` or `review_required`; 3 means `fail`, `blocked` or `error`.
- The default MCP profile has the same tools with the same argument names: `astra.run.compare`, `astra.config.inspect`, `astra.demand.audit`.

## Choose the path

| Request | Steps | Read |
|---|---|---|
| "Which run is better?", "Explain these numbers" | 1. If the `.sumocfg` files exist: `astra config inspect <baseline.sumocfg> <variant.sumocfg> --json`. Without configs, skip to step 2. <br> 2. `astra run compare --baseline-summary <b>/summary.xml --baseline-tripinfo <b>/tripinfo.xml --variant-summary <v>/summary.xml --variant-tripinfo <v>/tripinfo.xml --json`, using the output paths from step 1 when you have them | Inspect: `payload.paired_warnings`; in `payload.baseline` and `payload.variant`, `missing_inputs` and the `resolved_path` of the `references` entries whose `option` is `summary-output` or `tripinfo-output` (the step 2 inputs). Compare: `payload.baseline` and `payload.variant`: `summary` (`loaded`, `arrived`, `running`, `waiting`, `teleports`, `collisions`, `completion_ratio`, `last_time`) and `tripinfo` (`trip_count`, `mean_duration`, `mean_waiting_time`, `mean_time_loss`); `payload.paired_warnings` |
| Several seeds or scenarios | Run the compare step once per paired seed (same seed, routes and horizon on both sides) | The same fields per pair; report `n`, the mean difference and its spread |
| Full report on a traffic-control experiment | Run the comparison steps, then follow `references/traffic-control-reporting.md`; check metrics and claim wording in `references/evaluate-and-report-results.md` | The method document, configs, raw outputs and figures the user names |
| Calibration or digital-twin fit | Read the existing audit or calibration JSON. Only if none exists: `astra demand audit <expected_counts.csv> <e1_output.xml> <new-output-dir> --json` | In the audit's `payload`: `measurement_attribute`, `expected_rows`, `matched_rows`, `missing_measurement_rows`, `metric_scope`, `GEH_lt5_percent`, `RMSE`, `signed_bias`, `comparison_file` |
| Keep a dated record of a result | `astra workflow sumo_collect_evidence request.json --json` with `{"output_dir": "<new-dir>", "label": "<name>", "payload": <the result's payload>}` | `json_path`, `markdown_path` |
| Edit the text | Controlled technical English: `references/asd-ste100-skill.md`. Remove AI-writing patterns and keep the author's voice: `references/humanizer-skill.md` | - |

Notes on the commands:

- `run compare` reads the last `<step>` of each `summary.xml`; `completion_ratio` is `arrived / loaded`. Without both summaries the `claim_status` is `construction-invalid`.
- `config inspect` checks that each side's net, route and additional files exist and that the two sides do not share a summary, tripinfo, fcd, emission or queue output path. Detector, edgeData and laneData outputs declared in additional files are not checked. It does not check that both sides use the same inputs: compare the two `references` lists yourself.
- `demand audit` needs `expected_counts.csv` with columns `detector_id,begin,end,expected_total`. Ids and interval bounds must match the E1 `<interval id begin end>` values; the audit counts `nVehEntered`.
- None of these commands returns a `claim_status` stronger than `diagnostic-demo`. Do not raise it in the prose on the strength of these commands alone.

## Facts that prevent mistakes

- `tripinfo.xml` holds only vehicles that arrived (unless SUMO ran with `--tripinfo-output.write-unfinished`), so every `mean_*` value is arrived-only. Report completion (`completion_ratio`, `running`, `waiting`, `teleports`, `last_time`) before any mean; when it differs between the runs, compare it first.
- Gridlock, spillback, insertion backlog and unfinished demand can be the controller's result. Do not drop such runs silently; if runs are excluded, give the rule and the excluded rate.
- Attribute a difference to the method only when the network, demand realization, routes, seeds, horizon, output interval, completion rule, signal constraints and evaluated vehicles all match. One seed pair is a diagnostic, not a finding.
- Reward, pressure, loss and prediction error are not traffic outcomes. Report delay, travel time, queues, throughput, stops or emissions.
- TraCI is an interface, not a sensor. All-vehicle, lane or edge state is simulator truth unless a detector or sampling model is implemented. When methods get different information, separate the benefit of the information from the benefit of the algorithm.
- Detector counts are not OD. A count fit is one plausible demand, not the true OD matrix. A missing detector interval is unknown, not zero, and error metrics cover matched intervals only.
- GEH thresholds are defined for hourly flows. `GEH_lt5_percent` is computed on the raw interval counts, so on 15-minute intervals it flatters the fit: state the interval length.
- A run that loads and finishes in SUMO is not proof that the model or the result is correct, and a SUMO result is not field validation.
- Before a report or a saved lesson leaves the user's machine, remove local paths, credentials, private datasets, private station tables and collaborator names. Save or publish only where the user chooses, and ask before writing to a shared or public repository.
- Respect source licences: credit OSM data as "© OpenStreetMap contributors" (ODbL), and do not publish map or aerial screenshots whose licence forbids it.
- Do not imply that Eclipse SUMO, the Eclipse Foundation or DLR endorse or are affiliated with the work. Call a workaround found in one case a heuristic, not SUMO behavior.

## Write the answer

Lead with the conclusion the evidence supports. Then give the key values or mechanism, the comparison basis, and the limit of the evidence that matters to the user's claim. Keep hashes, call traces, Git history, test counts and internal gates out of the main report unless the user asks for a software or reproducibility audit.

## References

- `references/evaluate-and-report-results.md`: what a formal comparison needs, metric definitions, baselines, claim classes and the wording each claim needs.
- `references/traffic-control-reporting.md`: the evidence chain, controller inputs, signal execution, and demand and evaluation window that a traffic-control report must state.
- `references/asd-ste100-skill.md` and `references/humanizer-skill.md`: unmodified upstream writing skills (MIT; licences beside them). The STE skill's rules file, lint script and examples are not bundled; skip the steps that need them. Neither skill overrides technical accuracy, quoted or cited source text, or the voice the user asked for.
