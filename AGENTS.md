# ASTRA Repository Instructions

These instructions apply to the whole repository.
Read this file before starting work on ASTRA.

## Product Boundary

ASTRA's public product is organized around three capability groups:

- **Build** — construct SUMO networks and traffic infrastructure from sources such as OpenStreetMap, trajectory data, and road or signal records.
- **Calibrate** — reconstruct demand, bind observations, and calibrate simulation inputs against measured traffic data.
- **Simulate** — run, inspect, compare, and audit SUMO experiments from structured or natural-language tasks.

New public capabilities should fit one of these groups unless the product architecture itself is intentionally changed.

## NetEdit Default

- Use ASTRA's CLI by default to open NetEdit and capture screenshots. Open networks with `astra-netedit`. Capture screenshots with `plugins/astra-sumo/scripts/netedit_background_review.py`, using the repository's `.venv` Python. Do not default to desktop clicking, typing, or menu navigation. If the CLI cannot perform the task, explain the limitation before using another method.

## Start Here

Before changing structure or adding a public capability, read:

1. `README.md` for the user-facing promise;
2. `docs/architecture.md` for claim and promotion boundaries;
3. `docs/README.md` for the current documentation map;
4. `docs/mcp-tool-catalog.md` for the registered public tool surface.

## Repository Layout

- `README.md` — product entry point and current public promise.
- `docs/architecture.md` — system architecture, boundaries, and promotion logic.
- `plugins/astra-sumo/src/` — implementation.
- `plugins/astra-sumo/skills/` — reasoning, domain knowledge, and workflow-selection guidance.
- `plugins/astra-sumo/scripts/` — reproducible CLI entry points.
- `examples/` — curated user-facing demonstrations.
- `benchmarks/` — frozen evaluation datasets and benchmark evidence.
- `schemas/product/` — current product-facing JSON Schemas.
- `schemas/research/corridor/` — frozen corridor research and benchmark schemas.
- `tests/` — unit, contract, integration, and regression verification.
- `docs/` — current public documentation.
- `docs/research/` — research plans, protocols, and manuscript-oriented material.
- `docs/development-history/` — dated or superseded development records.
- `outputs/` — generated local run results.

## Code Placement

- Put reusable SUMO, OSM, audit, candidate, and artifact logic under `plugins/astra-sumo/src/astra_sumo/core/` or a focused domain package.
- Keep `plugins/astra-sumo/src/astra_sumo/tools/` thin: validate/resolve inputs, call domain logic, and serialize structured outputs.
- Use `plugins/astra-sumo/src/astra_sumo/server.py` only to register MCP tools and their public descriptions.
- Put reasoning and workflow-selection guidance under `plugins/astra-sumo/skills/`; do not implement subprocess or XML algorithms in skill prose.
- Put reusable workflow composition in workflow modules, not in one-off CLI scripts.
- Put reproducible CLI entry points under `plugins/astra-sumo/scripts/`; scripts must call reusable implementation rather than duplicate it.

## Evidence Invariants

- Keep source artifacts immutable and write candidates separately.
- Do not treat SUMO load, route completion, or KPI improvement as proof of topology, demand, control, or field correctness.
- Use structured decisions: `pass`, `review_required`, `blocked`, or `not_applicable`.
- Bind promotion-relevant evidence to exact source/candidate hashes and declared edit scope.
- Preserve rollback and outside-scope regression evidence for materialized candidates.
- State non-identifiability and claim boundaries in tool output instead of hiding them in documentation alone.

## Repository Hygiene

- Put curated demonstrations in `examples/` and frozen evaluation assets in `benchmarks/`.
- Put generated runs in `outputs/` or an explicit artifact directory; do not place generated data in `docs/`.
- Do not create a new top-level directory when an existing ownership category fits.
- Add dated research snapshots without silently rewriting historical protocols.
- Index research material through `docs/research/README.md` and dated or superseded development records through `docs/development-history/README.md`.
- Do not store internal agent execution plans, scratch plans, or coding checklists under public `docs/`.
- Preserve unrelated dirty-worktree changes and avoid broad formatting of files outside the active task.

## Public Surface Changes

When adding, renaming, or removing an MCP tool:

1. update `plugins/astra-sumo/src/astra_sumo/server.py`;
2. update `docs/mcp-tool-catalog.md`;
3. update skill routing if normal users should discover it;
4. add or update contract/regression tests;
5. update the README capability table only when the product-level promise changes.

The catalog coverage check is in `tests/test_repository_navigation.py`.

## Verification

- Run focused tests for every changed domain.
- Run `tests/test_repository_navigation.py` for repository navigation or MCP surface changes.
- Run `tests/test_docs_plugin_install.py` for public README/install changes.
- Run Ruff on changed Python files.
- Run `git diff --check` before handoff.
