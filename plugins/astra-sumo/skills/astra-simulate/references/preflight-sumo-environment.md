# Environment Preflight and Smoke Test

Use this before planning, running, debugging, or reporting a SUMO/TraCI experiment when the executable environment has not been proven in the current project.

The goal is not to validate the scientific experiment. The goal is to prove that the local SUMO toolchain can run a minimal headless case and produce readable outputs before the agent spends effort on controller logic, metrics, or claims.

## When to Run

Run this preflight when:

- the repository has no recent SUMO execution evidence;
- `SUMO_HOME`, `PATH`, Python, `traci`, or `sumolib` may be wrong;
- the user is starting a new machine, container, branch, CI job, or fresh clone;
- SUMO/TraCI startup fails or the agent cannot tell whether the failure is environment, input, or controller logic;
- a formal experiment is about to begin and no environment/version record exists.

Skip it only when the project already has a recent, readable environment record for the same machine or CI image.

## Required Environment Record

Capture:

```text
sumo_version:
sumo_binary:
sumo_home:
python_version:
traci_location:
sumolib_location:
platform:
working_directory:
command_shell:
```

Useful commands:

```text
sumo --version
netgenerate --version
duarouter --version
python -c "import sys; print(sys.version)"
python -c "import traci, sumolib; print(traci.__file__); print(sumolib.__file__)"
```

If any command fails, keep the status `blocked` or `construction-invalid`, preserve stdout/stderr, and use `route-project-workflow.md` plus `develop-and-verify-code.md` for the next probe or fix path.

## Minimal Smoke Test

A smoke test should create disposable files in a temporary directory and prove the chain:

```text
netgenerate -> randomTrips.py -> duarouter -> sumo -> tripinfo/summary parsing
```

Minimum acceptance:

- `netgenerate` writes a valid `.net.xml`;
- `randomTrips.py` writes trips or routes with a fixed seed;
- `duarouter` writes a route file without unresolved route errors;
- `sumo -c scenario.sumocfg` exits cleanly in headless mode;
- `tripinfo.xml` and `summary.xml` exist and are readable;
- final `summary.xml` reports whether vehicles arrived, remained running, waited for insertion, or were teleported;
- the exact commands and versions are recorded.

This smoke test is a construction gate only. Passing it does not mean the target experiment, controller, metrics, or claims are valid.

## Failure Routing

Route failures by the first missing proof:

| Symptom | Likely class | Next action |
|---|---|---|
| `sumo` or tools not found | environment fault | fix `SUMO_HOME`, `PATH`, installation, or container image |
| Python imports fail | environment fault | fix Python environment and SUMO tools package visibility |
| network generation fails | input construction fault | inspect command syntax and output path |
| route computation fails | demand/routing fault | inspect trips, network connectivity, vehicle types, and route errors |
| SUMO runs but outputs are missing | output observability fault | inspect `.sumocfg`, output paths, and file overwrite rules |
| SUMO exits cleanly but vehicles remain unfinished | demand realization or horizon fault | report completion/backlog before any metric comparison |
| TraCI connection fails after shell command fails too | environment/input fault | debug SUMO startup before controller code |

## Output Shape

Use this record:

```text
Environment Preflight
status: pass / fail / skipped
reason:
sumo_version:
python_version:
traci_location:
smoke_commands:
generated_artifacts:
summary_final_counts:
warnings_or_errors:
next_route:
claim_status:
```

Only route to experiment planning, code changes, metrics, or claims after `status: pass` or after the user explicitly accepts a diagnostic-only path.
