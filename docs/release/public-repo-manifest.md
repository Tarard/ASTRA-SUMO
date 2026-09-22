# Public Repository Manifest

Use this manifest for the public Torii repository hosted at:

```text
Tarard/Torii-SUMO
```

The public project is Torii: Task-Oriented Road Infrastructure Intelligence for Eclipse SUMO.

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
benchmarks/
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

## GitHub Topics

After publishing, add the topics listed in `docs/release/github-topics.txt` through repository settings or GitHub CLI.
