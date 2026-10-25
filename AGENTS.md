# ASTRA Repository Instructions

ASTRA builds, calibrates and simulates Eclipse SUMO networks on Windows. The product is the plugin in
`plugins/astra-sumo/`: the Python package in `plugins/astra-sumo/src/astra_sumo/` (CLI `astra`, MCP server
`astra-sumo`) and four skills in `plugins/astra-sumo/skills/`.

## Precedence

1. The task text, then this file.
2. `README.md` and `docs/architecture.md` define product behaviour and published claims.
3. The skills describe how a user's agent runs ASTRA. Other pages in `docs/` describe features. Research
   plans, status notes and `docs/development-history/` are background, not requirements.

## Finding the code for a request

- `astra --help` lists the commands; `astra workflows --json` lists the catalog scenarios, and
  `plugins/astra-sumo/src/astra_sumo/core/workflow_catalog.py` maps each scenario to its entry point.
- The skill for the request (`astra-build`, `astra-calibrate`, `astra-simulate`, `astra-report`) gives the order
  in which to call them. `docs/mcp-tool-catalog.md` lists every MCP tool.
- Reuse an existing command or function before writing a new one.

## Where code goes

- Logic goes in `plugins/astra-sumo/src/astra_sumo/core/` or a domain package (`corridor`, `evidence`,
  `intersection`, `road_network`). Domain packages never import `astra_sumo.tools` or `astra_sumo.server`.
- `plugins/astra-sumo/src/astra_sumo/tools/` holds thin MCP adapters. `plugins/astra-sumo/src/astra_sumo/server.py`
  (default and netedit profiles) and `plugins/astra-sumo/src/astra_sumo/legacy_tools.py` (legacy profile) only
  register tools. `plugins/astra-sumo/src/astra_sumo/cli.py` is the `astra` command.
- `plugins/astra-sumo/scripts/` holds entry points that call package code.
- `plugins/astra-sumo/skills/` holds short workflow guides for a user's agent: no algorithms, no development rules.
- Do not add a top-level directory when an existing one fits.

## Domain rules

Code and tests enforce these. Do not weaken them.

1. Never modify or overwrite an input file. Write candidates and reports to a new path.
2. A network edit reports every change outside its declared scope.
3. `--mode explore` is the default for `astra network calibrate` and `astra network verify-edit`: it measures and
   warns. `--mode release` fails on missing evidence and checks the source and candidate SHA-256. Use release
   only when the task asks for it.
4. Review overlays contain only display elements (`additional`, `poi`, `poly`, `param`).
5. Status words are `measured`, `no_effect` and `blocked` (explore) and `pass`, `review_required`, `blocked`
   and `not_applicable` (release and audits). Put detail in reason fields; add no new status words.
6. SUMO load, route completion or a better KPI does not prove topology, demand, control or field correctness.
   Detector counts are not OD. A missing detector interval is unknown, not zero.
7. Do not hand-edit a generated `.net.xml`. Change the inputs and rebuild, or use `astra network verify-edit`.
8. Use NetEdit only for a manual edit or when the task asks. Open it with `astra-netedit` and take screenshots
   with `plugins/astra-sumo/scripts/netedit_background_review.py`. Never drive the user's desktop.
9. Do not commit proprietary detector feeds, private station tables or text from RiLSA, DIN/EN or OCIT
   documents. Keep the DL-DE-BY-2.0 attribution on Hamburg open data.

## Runs, data and docs

- Write runs to `outputs/<run-name>/` (gitignored). Put the command, input SHA-256 and key metrics in your
  handoff, not in the repository.
- `benchmarks/` packages hold evaluation inputs and evidence; a package manifest records the files and hashes it
  covers.
- Run results go in the handoff, not in doc indexes or `docs/hamburg-readme-case.md`. Plans, reviews and scratch
  notes go in `outputs/agent-notes/`.
- Historical and frozen research documents take link and typo fixes only.
- Tests check behaviour and structure, never Markdown wording.
- Keep unrelated uncommitted changes. Do not reformat files outside the task.

## Changing the public surface

When you add, rename or remove an MCP tool, CLI command or catalog scenario, update its registration, then
`docs/mcp-tool-catalog.md` and the skill that routes to it, in the same change.
`tests/test_repository_navigation.py` checks the catalog and every relative link.

## Owner-only actions

Only when the task text asks for them: rewriting git history or force-pushing; merging into `main`; tags, GitHub
releases or Zenodo records; deleting a `benchmarks/` package that `README.md`, `docs/`, `CITATION.cff`, code
or tests use.

## Checks and handoff

Setup: `uv sync --frozen --extra dev --extra native` (Python 3.12 and SUMO 1.27.1 in `.venv`).

- Focused, naming the test files of the area you changed: `.venv/Scripts/python.exe -m pytest tests/test_network_explore.py -q -p no:cacheprovider`
- Docs, skills, catalog or packaging: `.venv/Scripts/python.exe -m pytest tests/test_repository_navigation.py tests/test_docs_plugin_install.py tests/test_bundled_sumo_skill.py tests/test_topic_skill_catalog.py tests/test_plugin_packaging.py -q -p no:cacheprovider`
- Full suite, about 18 minutes (CI runs it): `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
- Lint: `.venv/Scripts/python.exe -m ruff check plugins/astra-sumo/src tests` and `git diff --check`

End with the files changed and why, each check you ran with its result line, and what is not done or not
verified. If an input is missing, stop and name it; never invent data.

A reviewer reruns the task's checks and confirms the diff stays inside the task. Style and wording are
advisory. Do not ask for extra records, plans, benchmark packages or index entries.
