# ASTRA Documentation

Product behaviour and claims are defined by the top-level [`README.md`](../README.md) and
[Architecture](architecture.md). Contributor rules are in [`AGENTS.md`](../AGENTS.md).

| Goal | Start here |
|---|---|
| Install ASTRA | [Codex plugin installation](codex-plugin-install.md), [MCP host configuration](mcp-host-config.md), [ASTRA migration from Torii](astra-migration.md) |
| Understand the system and its claim boundary | [Architecture](architecture.md) |
| Find a tool or workflow | [MCP tool catalog](mcp-tool-catalog.md), [Workflow selection](workflow-selection.md), [Skill integration](skill-integration.md) |
| Build a network from OSM | [OSM source patterns](osm-source-patterns.md), [Road hierarchy and selection](road-hierarchy.md), [Parking facilities](parking-facilities.md), [Ingolstadt example](../examples/02_one_prompt_osm_network/README.md) |
| Edit a network | [Scoped road-network edits](network-edit-operations.md), [Local edit applicability](edit-specific-applicability.md) |
| Measure and review a network | [Road-network calibration](network-calibration.md), [Traffic-effect review](traffic-effect-review.md), [Network review over aerial imagery](network-aerial-review.md), [Public network references](public-reference-corpus.md) |
| Diagnose signal control | [Common SUMO signal-control failures](common-sumo-signal-control-failures.md) |
| Developer background on classification | [Intersection classification](intersection-classification.md), [Signal-device classification](signal-device-classification.md), [Road arms and connections](road-arm-classification.md), [OSM way fragments and road axes](osm-way-fragmentation.md) |
| Follow the Hamburg cases | [Hamburg README case](hamburg-readme-case.md), [Hamburg Sandtorkai case notes](hamburg-sandtorkai-case-notes.md) |
| Read research protocols and evidence | [Research index](research/README.md) |
| Read dated audits and development records | [Development history](development-history/README.md) |

Runnable examples live under [`examples/`](../examples/), command-line entry points under
[`plugins/astra-sumo/scripts/`](../plugins/astra-sumo/scripts/), and draft release material under
[`release/`](release/). Research and history documents describe a past state; they do not override the
README or the architecture document.
