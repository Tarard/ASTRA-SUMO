# Torii Skill Integration

Torii bundles four topic knowledge bundles with one shared CLI/MCP execution layer and a workflow catalog. The agent may use, combine, or skip these resources according to the task. Skills, references, and workflows are supporting resources, not a required state machine.

## Skill Map

| Skill | Useful for |
|---|---|
| `torii-build` | OSM and drawing-based network construction, roads, lanes, junctions, signal-device structure, map/source review |
| `torii-calibrate` | detector/count binding, demand reconstruction, fixed-network calibration, digital-twin replay |
| `torii-simulate` | environment checks, experiment planning, workflow execution, controllers, debugging, code and mechanism diagnosis |
| `torii-report` | completed-result interpretation, traffic-control reports, claim boundaries, writing support, field lessons, release review |

The old `simulation-helper-skill-for-eclipse-sumo` bundle is retired. Do not create another top-level copy of these skills.

## Product and Skill Boundaries

The product still uses the three public capability groups **Build**, **Calibrate**, and **Simulate**. `torii-report` is a cross-cutting reasoning and reporting resource for completed outputs from those capabilities. It is not a fourth product capability.

The four skills are knowledge bundles rather than mandatory routing states. A task may use one skill, several skills, only a reference, only a workflow, or none of them. The agent decides what is useful from the user's request and available evidence.

## References

Each skill lists related references. These references contain deeper domain knowledge, examples, and workflow-specific guidance. The lists are discoverability aids. The agent decides which references, if any, are worth reading for the current task.

## Workflow Catalog

For executable work, Torii provides an optional workflow catalog:

```powershell
torii workflows --json
torii workflows --scenario <ID> --json
```

Each catalog entry exposes its `reference_bundle` and a plugin-relative reference path. These fields point to related knowledge. They do not require the agent to load that skill or reference before using the workflow.

The host model may select a registered workflow from user intent and supplied evidence when a catalog entry is useful. Missing inputs remain `needs_input`. Unknown scenarios or arguments remain `blocked`. The agent may also answer or reason directly when no workflow is needed.

MCP and CLI output is observation, not final interpretation. Preserve child decisions such as `review_required` and `blocked` when they matter to the result.

## Evidence Boundary

- Network load does not prove physical network correctness.
- Count fit does not uniquely identify OD demand.
- A successful simulation does not prove controller superiority.
- GUI or screenshot inspection is supporting review evidence, not performance evidence by itself.

Keep source authority, target year, comparison pairing, completion status, and non-identifiability visible when they materially affect the claim.