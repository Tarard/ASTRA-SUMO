---
name: astra-simulate
description: Use for planning, running, debugging, and diagnosing SUMO/TraCI experiments, controllers, code paths, paired comparisons, environment failures, and unclear mechanisms.
---

# ASTRA Simulate

Use this skill as a compact knowledge base for operating and reasoning about SUMO experiments. The agent may decide which tools, workflows, references, or direct inspections are useful for the task.

## Common Knowledge

- Separate simulator setup, controller behavior, generated outputs, and scientific interpretation. A run can succeed technically while still giving weak evidence for the intended claim.
- For controller comparisons, paired route, demand, seed, horizon, signal constraints, and metric definitions make differences easier to interpret.
- Completion, insertion backlog, teleports, warnings, and action traces often explain misleading performance metrics.
- When debugging, preserve as much of the scenario as practical and change the smallest useful thing first. This is a heuristic, not a mandatory execution order.
- TraCI is an interface to the simulator. What a controller can read through TraCI is not automatically equivalent to a deployable physical sensor system.
- Controller objectives, pressure, reward, prediction error, and traffic outcomes are different quantities and should not be conflated.
- Use the simplest evidence that answers the user's question. A full workflow is unnecessary for a narrow question.

## Reference Library

Read only the references that help with the current task.

| Topic | Reference |
|---|---|
| Environment and executable checks | `references/preflight-sumo-environment.md` |
| ASTRA CLI/MCP routing | `references/mcp-tool-routing.md` |
| Existing project or unclear next step | `references/route-project-workflow.md` |
| Experiment intake | `references/interactive-experiment-intake.md` |
| Experiment planning | `references/plan-experiment.md` |
| SUMO/TraCI debugging | `references/debug-sumo-traci.md` |
| Confusing experiment results | `references/experiment-problem-solving.md` |
| Controller, TLS, and TraCI boundaries | `references/audit-sumo-controllers.md` |
| Controller-family patterns | `references/sumolights-controller-patterns.md` |
| Mechanism-isolated comparison | `references/compare-corridor-perturbations.md` |
| Code implementation and repair | `references/develop-and-verify-code.md` |
| SUMO semantics and public-source lessons | `references/learn-sumo-knowledge.md` |

## Available ASTRA Support

The workflow catalog exposes reusable workflows, checks, stages, and guidance entries. It can be useful for discovering existing capabilities and their inputs. It is not a mandatory state machine. The agent may use a catalog entry, call a lower-level tool, inspect code or files directly, or combine several approaches when that better serves the user's request.

## Output

For substantial simulation work, state what was inspected or run, what changed, what the result means, and what uncertainty remains.
