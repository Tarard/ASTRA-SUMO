# Public Repository Manifest

Use this manifest for the public ASTRA repository hosted at:

```text
Tarard/ASTRA-SUMO
```

The public project is ASTRA: Automated Simulation of TRAnsportation networks for SUMO.

## Include

```text
README.md
docs/readme/README.zh-CN.md
docs/readme/README.de.md
docs/architecture.md
docs/legal/NOTICE.md
LICENSE
CITATION.cff
.zenodo.json
pyproject.toml
uv.lock
AGENTS.md
.gitattributes
.gitignore
.agents/plugins/marketplace.json
.github/
plugins/
docs/
examples/
schemas/
tests/
```

The repository uses a single MIT License. `LICENSE` is the authoritative project license; `docs/legal/NOTICE.md` records third-party and attribution notices.

## Exclude

```text
runs/
outputs/
sumo/
*.pdf
__pycache__/
*.pyc
.pytest_cache/
local datasets
private experiment logs
unpublished research notes
machine-local absolute paths
untracked local research reports and captured run bundles
```

## Public Safety Checks

- No local machine paths.
- No private project acronyms, collaborator names, unpublished methods, or dataset identifiers.
- No vendored third-party source from OSMnx, OSMNet, pyrosm, SUMO, osm-to-xodr, SUMO Lights, or Google Maps.
- No claim that Google Maps is always the correct target; current versus historical map scope must be confirmed by the user.
- Full OSM cleanup is CLI-only. MCP does not expose it as a tool. Place resolution must produce a bbox before cleanup.
- Internal agent execution plans do not belong under public `docs/`.

## Release Checklist

Before tagging a release or publishing the plugin, check:

- `LICENSE`, `pyproject.toml`, `plugins/astra-sumo/.codex-plugin/plugin.json`, `CITATION.cff` and `.zenodo.json` all state the MIT License, and `CITATION.cff`, `.zenodo.json` and the release notes agree on the product name and version.
- `docs/legal/NOTICE.md` lists every vendored third-party file. The two writing skills in `plugins/astra-sumo/skills/astra-report/references/` keep their upstream bytes and their LICENSE files.
- The plugin bundles exactly four skills (`astra-build`, `astra-calibrate`, `astra-simulate`, `astra-report`). Each has `SKILL.md`, `agents/openai.yaml` and only the references it routes, and every reference named by a skill or by the workflow catalog exists (`tests/test_bundled_sumo_skill.py`, `tests/test_topic_skill_catalog.py`).
- README, install docs, `docs/mcp-tool-catalog.md` and examples describe the current CLI and MCP surface (`tests/test_repository_navigation.py`).
- No local machine paths, private datasets, unpublished collaborator material, raw private logs or generated run outputs are tracked.
- Public claims stay inside the evidence in the repository. Wording does not imply affiliation with or endorsement by Eclipse SUMO, the Eclipse Foundation or DLR.
- A green CI run shows packaging consistency only. It does not certify traffic-model correctness, field validity or controller superiority.

## GitHub Topics

After publishing, add the topics listed in `docs/release/github-topics.txt` through repository settings or GitHub CLI.
