# Closed-Loop Debugging

Use this when an Eclipse SUMO failure needs disciplined root-cause work instead of trial-and-error fixes.

## Observer

Collect only facts first:

- exact SUMO command or `traci.start(...)` command list;
- `sumo --version`, `sumo-gui --version`, Python version, and `traci.__file__`;
- working directory and whether paths are relative or absolute;
- `.sumocfg`, network, route/demand, additional files, and controller entrypoint;
- SUMO `--log`, `--error-log`, stdout/stderr, and Python traceback;
- generated outputs and their modification times;
- whether the same scenario runs with `sumo`, `sumo-gui`, TraCI, and headless mode.

Do not infer from screenshots when logs or XML artifacts can be inspected.

## Controller

Choose the smallest next diagnostic action. Good probes:

- run the printed SUMO command directly in a shell;
- add `--log` and `--error-log`;
- run `sumo --check-route` only if supported by the installed version, otherwise use `duarouter`, `routecheck.py`, or a minimal route reproduction;
- reduce to one vehicle, one route, one TLS, or one controller action;
- print controller commands and observe the corresponding TraCI/SUMO state in the next step;
- use unique output directories or `--output-prefix` for repeated runs;
- rerun with a fixed seed and then with a seed sweep.

Bad probes:

- changing multiple variables at once;
- hiding route errors or teleports before classifying them;
- switching to GUI as the only validation;
- reporting only the successful seed or successful episode.

## Executor

Run one probe, then stop and inspect feedback. Record:

```text
probe:
expected_signal:
observed_signal:
changed_files:
stdout_stderr:
log_findings:
next_fault_model:
```

## Critic

Accept a root cause only when the evidence distinguishes it from plausible alternatives.

Examples:

- If `traci.start` fails but the exact SUMO command fails in shell too, debug SUMO input/environment before controller code.
- If shell SUMO succeeds but TraCI fails, inspect port, `--remote-port`, Python package path, working directory, and run lifecycle.
- If travel-time metrics improve while completion falls, the problem is evaluation censoring, not controller improvement.
- If disabling teleport makes the network freeze, the underlying problem is gridlock/construction/demand, not teleport itself.

## Memory Update

Only add a recurring lesson to a skill when it is:

- general across SUMO projects;
- supported by official docs, local evidence, or reproducible community reports;
- expressible as an audit rule or diagnostic gate.

Do not store private logs, paths, unpublished project details, or one-off workaround narratives.
