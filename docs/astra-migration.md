# ASTRA migration

ASTRA means **Automated Simulation of TRAnsportation networks for SUMO**.
Version 1.3.0 continues Torii 1.2.0 in the new
[ASTRA-SUMO repository](https://github.com/Tarard/ASTRA-SUMO).
The source is [Torii commit f761f8f](https://github.com/Tarard/Torii-SUMO/commit/f761f8f96b65bb888565b856d620223fb3229f2a).
The original repository and its published releases remain available.

## Install ASTRA

```powershell
codex plugin marketplace add Tarard/ASTRA-SUMO --ref main
codex plugin add astra-sumo@astra-sumo
```

Disable the old Torii plugin in the host if both installations expose duplicate
tools. Start a new task after installation.

| Interface | ASTRA name |
|---|---|
| Python distribution | `astra-sumo` |
| Python imports | `astra_sumo` |
| CLI | `astra` |
| MCP server command | `astra-sumo` |
| NetEdit command | `astra-netedit` |
| Topic skills | `astra-build`, `astra-calibrate`, `astra-simulate`, `astra-report` |
| Default MCP tools | `astra.preflight`, `astra.config.inspect`, and the other names in the [tool catalog](mcp-tool-catalog.md) |
| MCP profile variable | `ASTRA_MCP_PROFILE` |

Update direct host configurations to the new plugin path and
`scripts/run_astra_sumo.py`. Its adjacent lock file travels with the script.
The new marketplace is a separate installation. Existing Torii installations do
not switch repositories automatically.

## Compatibility in 1.3.x

The installed Python distribution also provides `torii`, `torii-sumo`, and
`torii-netedit` commands. The `torii-sumo` command retains the old `torii.*` MCP
tool names. Its legacy profile retains `torii_auto_workflow`.
The new server uses `astra.*` and `astra_auto_workflow`.
Both servers retain the unchanged `sumo_*` tools in the legacy profile.

`TORII_MCP_PROFILE` remains a fallback. `ASTRA_MCP_PROFILE` takes precedence.
Update Python imports to `astra_sumo`; the old Python import package is not
installed by ASTRA. Existing absolute script paths still point to the original
Torii checkout until you update them.

## Data and provenance

Existing `torii.*` schema identifiers remain valid and continue to be written.
They identify data formats, independently of the project name.
Schema files, example outputs, source hashes, historical reports, and published
DOI records retain their original identity.
No traffic model, acceptance rule, or experimental result changes in this release.

The upstream project deleted `benchmarks/`. ASTRA removes tests that depend on
those deleted datasets and removes the corresponding CI freeze steps.
Self-contained product tests and checks for retained data formats remain.

The README links to the original Torii Zenodo archive as a historical source.
ASTRA does not yet have a separate published DOI.
The Git history and MIT attribution are preserved.

## Visual assets

The project owner supplied the ASTRA logo, banner, and framework diagram on
September 22, 2026. The original PNG bytes are used in the plugin and README files.
Historical traffic screenshots retain their original filenames and contents.

The project uses English, Chinese, and German README files as its public entry
points. It does not publish a separate GitHub Pages website.
