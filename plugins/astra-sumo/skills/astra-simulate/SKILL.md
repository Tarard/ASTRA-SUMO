---
name: astra-simulate
description: Use when a user wants to check the SUMO/TraCI environment, run a SUMO configuration, compare a baseline with a variant, debug a failing, hanging or puzzling run, audit or build a traffic-signal controller, plan a paired or perturbation experiment, pick a registered ASTRA workflow for a vague request, or learn how SUMO behaves. Names the ASTRA commands to run, in order, and the outputs to read.
---

# ASTRA Simulate

Run and diagnose SUMO experiments with code that already exists: the `astra` CLI, the functions behind `astra workflow <tool>`, and the ASTRA MCP tools. Find the request in the table, run its steps in order, and read the named outputs. Open a reference only when the table names it. Network construction and edits belong to `$astra-build`, detector counts and demand fitting to `$astra-calibrate`, and the write-up of finished results to `$astra-report`.

## Calling ASTRA

- Repository checkout: `.venv/Scripts/astra.exe <command> ... --json`. Installed plugin: `uv run --isolated --frozen --script <plugin-dir>/scripts/run_astra_sumo.py --cli <command> ... --json`. Below, `astra` stands for either.
- The ten commands that mirror the default MCP tools print `status`, `summary`, `claim_status`, `findings`, `artifacts`, `next_actions` and `payload`; warnings sit inside `payload`. Other commands, `astra workflow` included, print the called function's own result. Exit code 0 means `pass` (also `ok`, `measured`, `no_effect`); 1 means review the result (`warn`, `review_required`); 2 is a usage error (usage text on stderr, no JSON); 3 means `blocked`, `fail` or `error`.
- `astra workflow <tool> <request.json> --json` calls one allowlisted Python function with the keyword arguments in the JSON file. A request of `{}` returns an error that names the required arguments.
- MCP tools, profiles and CLI equivalents: `references/mcp-tool-routing.md`.

## Choose the path

| Request | Steps | Read |
|---|---|---|
| Is SUMO usable here? A run will not start. | 1. `astra preflight --json`. 2. `astra workflow sumo_run_minimal_smoke <req.json> --json` with `{"work_dir": "<new-dir>"}`. 3. On failure, `references/debug-sumo-traci.md`. | 1: `payload.sumo_binary` and the other `*_binary` paths, `versions`, `traci_available`, `sumolib_available`, `sumo_home`. 2: `status`, `warnings`, `commands[].returncode`, `summary_path`. |
| Run one `.sumocfg` | 1. `astra workflow sumo_run_config <req.json> --json` with `{"config_path": "<scenario.sumocfg>", "timeout_seconds": 600}`. 2. `astra run compare --baseline-summary <summary.xml> --baseline-tripinfo <tripinfo.xml> --json`, with the `summary-output` and `tripinfo-output` paths from the `.sumocfg` (relative to its folder; add either option if it is missing). | 1: `status`, `returncode`, `stderr`. 2: `payload.baseline.status`, `warnings`, `summary` (`loaded`, `inserted`, `arrived`, `running`, `waiting`, `teleports`, `collisions`, `completion_ratio`) and `tripinfo`. Without a variant the top level is at best `warn` with `construction-invalid`; judge the run by `payload.baseline`. |
| Compare a baseline and a variant | 1. `astra config inspect <baseline.sumocfg> <variant.sumocfg> --json`. 2. Run each config as above. 3. `astra run compare` with `--baseline-summary`, `--baseline-tripinfo`, `--variant-summary` and `--variant-tripinfo`. 4. For the write-up, `$astra-report`. | 1: `payload.paired_warnings` (shared output paths, checked only for the `.sumocfg` summary, tripinfo, fcd, emission and queue outputs; check detector and `edgeData` `file=` in the additional files yourself), and each role's `missing_inputs` and `missing_output_parents`. Step 3 takes the `resolved_path` of each role's `summary-output` and `tripinfo-output` entry in `payload.<role>.references`. 3: both roles' `summary` and `tripinfo`, then `paired_warnings` (completion or loaded counts differ). |
| A run fails, hangs, or gives a result nobody can explain | `references/debug-sumo-traci.md` | its symptom tables |
| Audit, apply or build a signal controller | `references/audit-sumo-controllers.md` | |
| Plan an experiment, or compare perturbation mechanisms | `references/plan-experiment.md` | |
| A vague request that may match a registered ASTRA workflow | "Pick a registered workflow" below | `execution_status` |
| How does SUMO or TraCI behave? | `references/learn-sumo-knowledge.md`; answer without claiming a run | |

`sumo_run_config` runs `sumo -c <config>` in the config's folder, with `sumo` from `PATH` unless `sumo_binary` names another binary (default timeout 120 s). Outputs land where the config declares them and are not parsed; read them with `astra run compare`. Its `pass` means only that SUMO exited with code 0.

## Pick a registered workflow

1. `astra workflows --json` lists every scenario with its `kind` (`workflow`, `check`, `stage` or `guidance`), `required_arguments`, `optional_arguments`, `input_file_arguments` and `reference`. `--scenario <id>` shows one.
2. Choose the scenario from the user's intent and the files they supplied. Keep their target year and source authority.
3. Write `selection.json` with only `user_request`, `scenario_id`, `reason` and `arguments` (the function's keyword arguments; `{}` for guidance).
4. `astra workflow selected selection.json --json` checks the arguments, their types and that the input files exist. Add `--execute` to run the function when running it is part of the user's request.

| `execution_status` | Meaning |
|---|---|
| `ready_not_executed` | The selection is valid. Nothing ran. |
| `needs_input` | Supply the listed `missing_arguments`, `missing_input_groups` or `missing_files`. |
| `selection_invalid` | `status: blocked`: unknown scenario, extra field or argument, or a wrong type; read `error`. Nothing ran, and no other workflow was tried. |
| `guidance_only` | There is no function. Read the named `reference` and do the reasoning. |
| `executed` | The function ran. Its verdict is `workflow_result.status`, not `executed`. |
| `execution_failed` | The function raised an exception; read `error`. |

A `check` or `stage` does not complete the larger workflow it belongs to.

## Facts that prevent mistakes

- Completion comes first. Report loaded, inserted, arrived, running, waiting and teleported vehicles before any travel-time, delay or waiting average. Arrived-only averages are biased when completion differs, and a better average with worse completion is not an improvement.
- SUMO loading the files, exiting with code 0 or passing a smoke test proves that it ran. It does not prove that the network, demand, controller or result is correct.
- Paired runs share the network, routes or demand, seed, begin and end times, output intervals and metric formulas, and change only the studied variable. Each run writes its own output files; a shared path overwrites the other run.
- `sumo --ignore-route-errors`, `duarouter --ignore-errors`, disabled teleporting (`--time-to-teleport -1`) and changed demand can help locate a fault. A run that uses them is diagnostic, not evidence.
- TraCI reads simulator ground truth. A controller that reads it does not use deployable sensors unless an observation model is implemented.
- A controller file does not prove that the controller ran. A command log or a TLS state output must show its actions.
- End a TraCI loop when `traci.simulation.getMinExpectedNumber() == 0`, or at a declared end time with the unfinished vehicles reported.
