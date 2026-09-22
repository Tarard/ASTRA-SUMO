# Sumolights Controller Patterns

Use this reference when the user asks to add or compare traffic signal controllers such as max-pressure, Webster, SOTL, fixed-time, actuated control, or custom TraCI control.

This reference uses `docwza/sumolights` as public implementation-pattern evidence only. Do not copy GPL-3.0 source code, TensorFlow 1.14 training code, or project-specific files into this plugin.

## Controller Application Plan

Before generating or running a controller, record:

```text
target:
network_artifact:
controller_family:
controlled_tls_scope:
phase_mapping_source:
lane_mapping_source:
yellow_all_red_policy:
run_or_generate_only:
claim_boundary:
```

## Reusable Design Pattern

Use this lifecycle:

```text
observe current SUMO state
-> update controller internal state
-> choose next phase
-> choose duration or switch timing
-> issue TraCI command or generate TLS plan
-> log controller action and controlled TLS
-> inspect completion and warnings before performance metrics
```

## Controller Factory Pattern

Map user method names to controller families:

| User wording | Controller family | First implementation target |
|---|---|---|
| max-pressure, pressure, queue pressure | `max_pressure` | phase pressure from incoming/outgoing lane queues |
| Webster, classical timing | `webster` | generated timing plan from demand or measured flow assumptions |
| SOTL, self organizing | `sotl` | threshold-based phase switching with explicit detector assumptions |
| fixed-time | `fixed_time` | static timing plan with documented cycle, split, and offset |
| actuated | `actuated` | SUMO-native or TraCI-driven detector-based switching |

If the requested family is unsupported by existing MCP tools, create a code-development plan instead of pretending the controller was applied.

## Phase And Lane Mapping Requirements

For every controlled TLS, inspect or construct:

- TLS ID.
- phase list and green phases.
- controlled links.
- incoming lanes and outgoing lanes.
- movement-to-phase map.
- unsupported phases or ambiguous links.

Max-pressure needs lane queues or occupancy by incoming lane and a mapping from green movements to outgoing lanes. Webster needs demand or flow assumptions. SOTL needs detector or queue thresholds. Missing mappings demote the claim to `blocked` or `diagnostic-demo`.

## Controller Identity Record

For each controlled signal, report:

```text
tls_id:
controller_family:
controlled_links_count:
green_phase_indices:
phase_mapping_source:
lane_mapping_source:
unsupported_movements:
action_log_artifact:
claim_status:
```

## Evidence Boundary

- A generated controller file is not proof that the controller ran.
- A TraCI command log is not proof that the controller improved the user target.
- Completion, unfinished vehicles, route errors, teleports, and warnings must be reported before travel-time, waiting-time, delay, or queue averages.
- If controller logs are missing, do not claim controller coverage.
