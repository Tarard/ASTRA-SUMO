# Audit SUMO Controllers

Use this reference when auditing NEMA/TLS/TraCI traffic-signal controllers, fixed-time plans, actuated baselines, max-pressure, data-informed, MPC-style, or custom controller implementations.

The goal is to prove what controller actually controlled which phases under which SUMO semantics.

## Controller Identity

Capture:

```text
controller_name:
controller_type: fixed_time / actuated / NEMA / max_pressure / data_informed / MPC_style / RL / custom
controlled_tls_ids:
phase_index_source:
movement_mapping_source:
detector_mapping_source:
TraCI_commands_used:
yellow_all_red_policy:
fallback_policy:
```

Do not claim a method was evaluated unless logs or code prove that method issued the signal commands used in the run.

## TraCI Boundary Table

| Pattern | Evidence required |
|---|---|
| read-only observation | exact TraCI getters, timing within step loop |
| direct phase control | `setPhase`, `setRedYellowGreenState`, or equivalent command log |
| timing/program control | program IDs, phase durations, offsets, and update timing |
| detector-based control | detector IDs, lanes, aggregation window, missing-data behavior |
| multi-intersection coordination | clock, offset, communication timing, and conflict handling |

Separate SUMO built-in TLS behavior from external TraCI controller behavior. If SUMO's internal actuated/NEMA logic is active, prove the external controller is not silently overriding or being overridden.

## NEMA/TLS Checks

For NEMA claims, verify:

- ring, barrier, phase, split, offset, and recall parameters match the intended design;
- `setNemaSplits`, `setNemaMaxGreens`, `setNemaCycleLength`, and `setNemaOffset` timing calls apply to the correct controller and cycle;
- max recall and force-off behavior are compatible with the claimed experimental comparison;
- detector mapping matches the controlled movements and lane IDs after network generation;
- yellow/all-red durations are consistent across methods or explicitly part of the experiment.

For generic TLS claims, verify:

- phase strings match controlled links and movement permissions;
- phase index assumptions survive generated-network changes;
- protected/permissive movements are represented as intended;
- no unsafe conflicting greens are introduced by direct state commands.

## Baseline Fairness

For controller comparison:

- use paired routes, demand, seeds, horizon, output intervals, and completion criteria;
- keep yellow/all-red, lost time, detector placement, and phase availability comparable unless explicitly studied;
- report whether the baseline is fixed-time, SUMO actuated, NEMA, max-pressure, RL, or another controller family;
- avoid comparing an optimized controller against a broken, uncalibrated, or unobserved baseline.

## Debug Boundaries

If the controller appears ineffective, check in this order:

1. SUMO loaded the intended TLS program and additional files.
2. The controller loop actually ran for the expected steps.
3. TraCI commands targeted the intended TLS IDs.
4. Phase/movement/detector maps match the generated network.
5. Output logs prove controller actions affected the simulated signal states.

## Demotion Rules

Demote claims when:

- phase mapping is inferred only from visual inspection;
- detector mapping is missing or generated after the controller assumptions;
- controller logs do not prove command execution;
- NEMA/actuated/fixed-time behavior is mixed without a clear boundary;
- yellow/all-red handling differs across methods without disclosure.

## Applying Controller Families

When the user asks to add or apply a controller family, also load `sumolights-controller-patterns.md`. Use it for controller lifecycle, method-family selection, phase-lane mapping, and Controller Identity Record requirements. Use this audit reference for the SUMO/TraCI evidence boundary.
