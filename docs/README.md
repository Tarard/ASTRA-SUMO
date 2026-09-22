# Torii Documentation

This is the current documentation index for Torii. Product behavior and claims are defined by the top-level `README.md` and `docs/architecture.md`; research snapshots and historical development records are indexed separately below.

## Start by Goal

| Goal | Start here |
|---|---|
| Install Torii | [Codex Plugin Installation](codex-plugin-install.md) |
| Understand the system and claim boundary | [Architecture](architecture.md) |
| Find an MCP tool | [MCP Tool Catalog](mcp-tool-catalog.md) |
| Understand how skills are selected and invoked | [Skill Integration](skill-integration.md) |
| Select a workflow from a task and available sources | [Workflow Selection](workflow-selection.md) |
| Configure a direct MCP host | [MCP Host Configuration](mcp-host-config.md) |
| Review OSM/SUMO source patterns | [OSM Source Patterns](osm-source-patterns.md) |
| Diagnose recurring signal-control failures | [Common SUMO Signal-Control Failures](common-sumo-signal-control-failures.md) |
| Follow the Hamburg digital-twin execution loop | [Hamburg corridor workflow](hamburg-sandtorkai-codex-execution-workflow.md) |
| Run the one-prompt OSM demonstration | [Ingolstadt example](../examples/02_one_prompt_osm_network/README.md) |
| Inspect research protocols and evaluation evidence | [Research index](research/README.md) |
| Inspect dated audits and development handoffs | [Development history](development-history/README.md) |

## Current Product Documentation

- [Architecture](architecture.md) — system boundary, major layers, and allowed claims.
- [Codex Plugin Installation](codex-plugin-install.md) — installation and first-run setup.
- [MCP Tool Catalog](mcp-tool-catalog.md) — registered tool surface grouped by user intent.
- [Skill Integration](skill-integration.md) — skill discovery, reasoning, and execution handoff.
- [Workflow Selection](workflow-selection.md) — task-to-workflow routing.
- [MCP Host Configuration](mcp-host-config.md) — direct MCP host setup.
- [OSM Source Patterns](osm-source-patterns.md) — OSM/SUMO acquisition and conversion patterns.
- [Common SUMO Signal-Control Failures](common-sumo-signal-control-failures.md) — recurring controller and evidence failures.
- [Hamburg corridor workflow](hamburg-sandtorkai-codex-execution-workflow.md) — the plan, run, audit, and revision loop for the current digital-twin case.

Runnable examples live under [`examples/`](../examples/). Frozen evaluation assets and protocols live under [`benchmarks/`](../benchmarks/). Command-line experiment entry points live under [`plugins/torii-sumo/scripts/`](../plugins/torii-sumo/scripts/).

## Research and Evaluation

Research plans, frozen protocols, status snapshots, and manuscript planning are indexed in [Research](research/README.md). These documents preserve evidence and experiment history, but they do not override the current public product boundary.

## Development History

Dated architecture audits, handoff notes, development logs, and superseded repository guidance are indexed in [Development History](development-history/README.md). They are retained for traceability rather than treated as current instructions.

## Release Material

Draft public announcements and release-positioning notes live under [`release/`](release/). The current README and architecture document take precedence if a draft becomes stale.

## Documentation Rules

1. Put stable user navigation in this file and the top-level README.
2. Put system invariants and claim boundaries in `docs/architecture.md`.
3. Put current installation, tool, skill, and workflow guidance in focused documents under `docs/`.
4. Index research evidence through `docs/research/README.md`.
5. Index dated or superseded development records through `docs/development-history/README.md`.
6. Put runnable demonstrations in `examples/`, not in documentation prose alone.
7. Put frozen evaluation inputs and adjudication protocols in `benchmarks/` when they are executable evaluation assets.
8. Keep generated run artifacts out of `docs/`; place them under `outputs/` or an explicit artifact directory.
9. Do not store internal agent execution plans in public documentation.
