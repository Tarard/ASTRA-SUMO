# Controller Interface Design Proposal (2026-11)

Status: design proposal, 2026-11-16. Nothing in this document is implemented. It does not change the product boundary in [`README.md`](../../README.md) or [Architecture](../architecture.md).

## Goal

Let a user run an own traffic-signal control method on ASTRA-built scenarios and compare it with a fixed set of baseline methods. The comparison must keep ASTRA's evidence rules: exact source hashes, structured decisions, and no silent change to signal safety.

The design takes the environment interface from [sumo-rl](https://github.com/LucasAlegre/sumo-rl) (MIT license) and extends it to more types of control method.

## Non-Goals

- Training algorithms. ASTRA runs and records controllers. Training stays in the user's own code or in RL libraries.
- Field-timing claims. A controller result does not prove that a signal plan is correct in the field.
- Replacing the existing signal-control audit. Every run still goes through the audit rules in [`examples/01_signal_control_audit`](../../examples/01_signal_control_audit/README.md).

## Lessons From sumo-rl

The notes below come from `sumo_rl/environment/env.py`, `traffic_signal.py`, `observations.py`, and `resco_envs.py` on the sumo-rl `main` branch (read 2026-11-16).

### Keep

| sumo-rl design | Where | Reason to keep |
|---|---|---|
| Gymnasium single-agent and PettingZoo multi-agent interface, one agent per traffic light | `env.py` | Existing RL code (for example Stable-Baselines3, RLlib) can run without changes |
| Observation as a replaceable class (`ObservationFunction`) | `observations.py` | Each method can define its own state input |
| Reward by name (`diff-waiting-time`, `average-speed`, `queue`, `pressure`) or by callable; a list of rewards with weights for multi-objective control | `traffic_signal.py` | Same reward definitions for all methods; multi-objective studies |
| Scenario registry: one function per scenario with fixed network, routes, begin time, and duration | `resco_envs.py` | Results stay comparable |
| `fixed_ts=True` mode that keeps the network's own program | `env.py` | Direct fixed-time baseline |
| Explicit `sumo_seed` and `reset(seed=...)` | `env.py` | Repeated runs with known seeds |
| System metrics: arrived, departed, teleported, waiting time, mean speed | `env.py` | Matches the audit rule to report completion and teleports first |

### Change

| sumo-rl behavior | Effect | ASTRA design |
|---|---|---|
| The only action is "select the next green phase" (`spaces.Discrete(num_green_phases)`) | No green extension, cycle, split, offset, coordination, or transit priority | Several action types (see [Action Types](#action-types)) |
| Green phases are taken from program 0. Yellow phases are generated with one fixed `yellow_time`. The program is replaced with `setProgramLogic` | All-red times, intergreen times, and pedestrian phases of the source plan are lost | Keep the source program. Clearance comes from declared timing constraints, and the guard applies it |
| `max_green` is ignored unless `enforce_max_green=True` | A controller can hold green without limit | Maximum green is always a constraint when it is declared |
| One fixed decision interval `delta_time` for all controllers | Event-based methods (for example actuated control or bus priority) do not fit | A controller states its next decision time |
| Observations read lane vehicles directly from TraCI | Methods with full information and methods with detector data only are mixed | Each observation provider declares an information tier |

## Requirements

1. A user controller runs through one Python contract. Existing sumo-rl agents run through an adapter.
2. A controller declares which action types it uses. The runtime rejects other action types.
3. Every action goes through an action guard before it reaches SUMO.
4. The guard never changes an action silently. Each changed or rejected action is recorded.
5. Baselines use the same contract as user controllers.
6. A run is bound to exact scenario hashes, controller identity, parameters, and seed.
7. Results report completion, unfinished vehicles, and teleports before averages.
8. The runtime can use a test TraCI object, as `core/tls_replay.py` already does with `traci_api`.

## Architecture

```text
project record ──> scenario (network + demand + signal program + timing constraints + seeds)
                         │
                         v
                 runtime loop (TraCI) ──> observation providers ──> controller
                         ^                                              │ action
                         │                                              v
                   SUMO state <────────── action guard <─────────────────┘
                         │                     │
                         v                     v
                  metrics + outputs       guard log
                         └──────> run record (hashes, controller identity, decisions)
```

## Controller Contract

```python
class Controller(Protocol):
    controller_id: str
    version: str
    action_types: frozenset[type[Action]]

    def reset(self, context: ControlContext, seed: int) -> None: ...
    def act(self, time_s: float, observation: Observation) -> Decision: ...
```

- `ControlContext` holds, for each controlled traffic light: the controller ID, the link-index-to-movement map, signal groups, source phases, conflict sets, timing constraints, and the information tier of each observation provider.
- `Decision` holds zero or more actions and the next decision time.
- Parameters are passed at construction time and are written to the run record.

## Action Types

| Action | Meaning | Example methods |
|---|---|---|
| `PhaseSelect(tls, phase)` | Select the next green phase from the source phase set | RL phase selection, Max Pressure, SOTL |
| `GreenHold(tls, until_s)` | Hold or extend the current green up to a time | Actuated control, gap-out logic |
| `PlanUpdate(tls, cycle_s, splits, offset_s)` | Set cycle, splits, and offset from the next cycle start | Webster, time-of-day plans, coordination, MPC with plan output |
| `ProgramSwitch(tls, program_id)` | Switch to another declared program | Plan selection by time period or by demand state |
| `PriorityRequest(tls, movement, eta_s)` | Request green extension or early green for a vehicle class | Transit signal priority |

A controller can use more than one action type. The adapter for sumo-rl maps the discrete action of a sumo-rl agent to `PhaseSelect`.

## Action Guard

The guard checks each action against the constraints of the scenario:

| Constraint | Source | Guard behavior |
|---|---|---|
| Conflicting movements never have green at the same time | Independent conflict audit and link-index map | Reject the action |
| Minimum green | Declared timing constraints | Delay the change until minimum green is reached |
| Maximum green | Declared timing constraints | Force the next phase when maximum green is reached |
| Clearance between phases (yellow, all-red, intergreen) | Declared timing constraints | Insert the clearance before the next green |
| Plan limits (cycle range, minimum split) | Declared timing constraints | Reject the plan |

Each intervention is written to the guard log with time, traffic light, requested action, applied action, and reason. The run record reports the number of interventions per controller.

**Source of timing constraints.** ASTRA does not yet hold an intergreen matrix. Official program rebuilds use an all-red placeholder, and the NEMA binding uses generic yellow values that are not reviewed. The guard therefore uses this order:

1. official intergreen or clearance data, when a reviewed source exists (for example OCIT supply data);
2. the clearance phases of the source program;
3. declared generic defaults.

Case 3 sets the run decision to `review_required`, and the run record names every default value used.

## Observation and Reward

- Observation providers follow the sumo-rl pattern (a class with a call method and an observation space). Each provider declares an information tier:
  - `detector`: only values that a field detector can measure;
  - `connected`: vehicle data from connected vehicles;
  - `full`: complete simulation state.
- A results table only compares controllers that use the same tier, unless the tier difference is reported.
- Reward functions follow the sumo-rl registry: a name or a callable, and a list with weights. ASTRA adds bus person delay to the reward registry.

## Baselines

| Baseline | Action type | Note |
|---|---|---|
| Source program | none (fixed) | Equivalent to sumo-rl `fixed_ts=True` |
| SUMO actuated | `ProgramSwitch` to an actuated program | Uses SUMO's own actuated logic |
| Max Pressure | `PhaseSelect` | Pressure computed from the declared detector or lane mapping |
| Webster fixed-time | `PlanUpdate` | Plan computed from the scenario demand |

Each baseline passes the existing signal-control audit before it is used as a reference.

## Scenarios

A scenario is created from a project record and is stored as a manifest under `benchmarks/`:

- network, route, additional, and signal program files with SHA-256 hashes;
- begin time, end time, and warm-up time;
- seed list;
- timing constraints and their source (official, source program, or default);
- a short scenario card: data sources, calibration evidence, known limits, and claim tier.

The registry gives each scenario a name and a version, as `resco_envs.py` does for RESCO networks.

## Metrics and Run Record

Per run, in this order:

1. completion: arrived, unfinished, teleported, and collisions;
2. guard interventions;
3. vehicle delay, stops, queue length, and throughput;
4. bus delay and person delay, when buses exist.

Each controller runs with all seeds in the scenario. Reports give the mean and the confidence interval. The run record contains scenario hashes, controller ID, version, parameters, seed, information tier, the guard log hash, and a decision (`pass`, `review_required`, or `blocked`).

## Proposed Code Placement

The placement follows [`AGENTS.md`](../../AGENTS.md):

| Part | Location |
|---|---|
| Contract, action types, guard, runtime loop, observation and reward registries, metrics | `plugins/astra-sumo/src/astra_sumo/core/control/` |
| Baseline controllers | `plugins/astra-sumo/src/astra_sumo/core/control/baselines/` |
| sumo-rl adapter (optional dependency) | `plugins/astra-sumo/src/astra_sumo/core/control/adapters/` |
| MCP tools (thin) | `plugins/astra-sumo/src/astra_sumo/tools/` and `docs/mcp-tool-catalog.md` |
| CLI entry point | `plugins/astra-sumo/scripts/` |
| Run record schema | `schemas/product/` |
| Scenario manifests | `benchmarks/` |
| Tests with a test TraCI object | `tests/` |

## Suggested Stages

1. Contract, runtime loop, guard for `PhaseSelect` and `GreenHold`, the source-program baseline, and Max Pressure on XS-1.
2. sumo-rl adapter, observation and reward registries, seed handling, and the metric report.
3. `PlanUpdate` and `ProgramSwitch`, the Webster and SUMO-actuated baselines, and a corridor scenario for coordination.
4. `PriorityRequest`, bus person delay, and a transit scenario.
5. Scenario registry under `benchmarks/` and a versioned results table.

## Open Questions

1. Which reviewed source can provide intergreen data first: OCIT supply data or the Hamburg official program data?
2. Should the sumo-rl adapter be an optional dependency, or should ASTRA copy only the interface types?
3. Does the guard insert clearance phases itself, or does it only reject unsafe actions and leave clearance to the controller?
4. Which pedestrian rules must the guard enforce in the first stage?

## Sources

- sumo-rl: <https://github.com/LucasAlegre/sumo-rl>
- RESCO: <https://github.com/Pi-Star-Lab/RESCO>
- Gymnasium: <https://gymnasium.farama.org/>
- PettingZoo: <https://pettingzoo.farama.org/>
- Related snapshot: [Benchmark and evaluation landscape (2026-11)](benchmark-landscape-2026-11.md) (open in PR #7)
