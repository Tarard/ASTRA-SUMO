# Codex Plugin Installation

`ASTRA` is distributed as a Codex plugin. Installing it provides both:

- four topic skills: `astra-build`, `astra-calibrate`, `astra-simulate`, and `astra-report`;
- the `astra-sumo` local stdio MCP server.

ASTRA supports 64-bit Windows only. Linux and macOS are not supported targets.

The skill is the reasoning layer. The MCP server is the execution layer.

The bundled `.mcp.json` starts `scripts/run_astra_sumo.py` through
`uv run --isolated --frozen --script`. The script declares its dependencies with
PEP 723 metadata and uses the adjacent `run_astra_sumo.py.lock` file. It can run
from an installed plugin cache without the repository root or a `python` command
on `PATH`. The retained `scripts/bootstrap_mcp.py` entry point checks the same
plugin-local runner and lock file. Plugin-launched MCP sessions default to the
10-tool `default` profile. Set `ASTRA_MCP_PROFILE=legacy` to expose all 73 legacy
tools, or `ASTRA_MCP_PROFILE=netedit` for only the NetEdit loop.

## CLI from an installed plugin

The plugin runner also provides the CLI. From the installed plugin root, use:

```powershell
uv run --isolated --frozen --script ./scripts/run_astra_sumo.py --cli workflows --json
uv run --isolated --frozen --script ./scripts/run_astra_sumo.py --cli workflow selected selection.json --json
```

Use an absolute runner path when another directory is current. Commands written
as `astra ...` in this guide and the bundled skill are shorthand for this runner
with `--cli`. A separate global `astra` installation is not required.

For a Windows NetEdit capture, `netedit review <source.net.xml> <new-output-dir>
<source-sha256>` opens a diagnostic copy, observes it, and closes it in one process.
The copied network and review artifacts remain in the output directory. Use the
persistent NetEdit MCP profile for multi-step edits. Separate CLI `open`, `observe`,
`act`, and `close` calls do not preserve a session and are rejected before action.

The Windows runtime explicitly includes pywin32 for the target-window session.
SUMO programs must still be available on `PATH`. Developers can install the tested
native binaries with `uv sync --extra dev --extra native`; CI uses SUMO 1.27.1 on
Windows and verifies its binaries before running tests.

Environment reports include `python_executable` and `python_module_origins` for
`sumolib` and `traci`. Check these paths when a local run differs from CI.
A global `PYTHONPATH` can select system SUMO modules before the project environment.
For a dedicated test process, remove that variable before starting Python:

```powershell
Remove-Item -LiteralPath Env:PYTHONPATH -ErrorAction SilentlyContinue
```

This changes the current process environment. It does not change Windows user or
system settings. The recorded paths do not add a new acceptance check.

## Repository Layout

The installable plugin root is:

```text
plugins/astra-sumo
```

The plugin manifest is:

```text
plugins/astra-sumo/.codex-plugin/plugin.json
```

The repo marketplace entry is:

```text
.agents/plugins/marketplace.json
```

## Native command completion

The shared runner captures UTF-8 output through temporary files. This avoids
waiting for captured-pipe EOF during Windows error shutdown. Reports retain
stdout, stderr, return codes, and timeout status in the existing format.

For known SUMO executables, `Quitting (on error)` starts a one-second exit grace
period. A process that remains active is stopped with its owned process tree.
The result is `fail` with the native error text. A silent deadline expiry remains
`timeout`. Text from unrelated commands does not trigger this SUMO rule.
After a forced stop, the return code describes termination, not a normal SUMO
exit. Output files from failed runs can be incomplete. Keep their failure status
when inspecting those files.

The runner handles finite foreground commands. Detached service launchers need
their own lifecycle handling. Native observation helpers also own and reap
their SUMO process when a TraCI connection fails.
Teacher replay and intersection load and compilation checks use the same runner.

The underlying native shutdown fault remains unexplained. The current local
native and launcher baselines exited normally; a controlled hanging process
tests the error path. Windows CI also exercises twenty native rejection runs.

## Local Development Install

From a local checkout, add the repository as a local marketplace:

```powershell
codex plugin marketplace add <path-to-this-repo>
codex plugin add astra-sumo@astra-sumo
```

Start a new Codex thread after installing or reinstalling so the skill and MCP tools are discovered in the new session.

## GitHub Install Shape

For a GitHub source, the expected marketplace command is:

```powershell
codex plugin marketplace add Tarard/ASTRA-SUMO --ref main
codex plugin add astra-sumo@astra-sumo
```

If the Codex CLI reports a marketplace-source mismatch, verify that `.agents/plugins/marketplace.json` points to `./plugins/astra-sumo`.

## Usage Boundary

The plugin can run bounded environment checks, config preflight, smoke runs, output comparison, evidence bundle writing, and OSM/network construction helpers through MCP tools.

For one-sentence requests, start with the workflow router:

- `astra_auto_workflow`: classify the user's natural-language SUMO request, choose the workflow recipe, ask only blocking questions, and run safe MCP steps when enough evidence is available.

Implemented OSM/network interfaces:

- `sumo_osm_cleanup_workflow`: run this CLI-only workflow with `astra workflow`; it is not an MCP tool. Resolve a place and complete area confirmation to obtain a bbox first. Pass only `output_dir`, `bbox`, `profile`, `source_osm_path`, `traffic_layers`, `reference_net_file`, and `timeout_seconds`. The `standard` profile requires traffic layers. The `reference_matched` profile requires a reference `.net.xml`, audits differences, and does not apply repairs.
- `sumo_osm_build_network`: download or reuse an OSM extract, use tiled Overpass requests with retry, deduplicate merged OSM XML by object id, apply road-class presets or explicit highway classes, run `netconvert`, and return artifact/log paths.
- `sumo_tls_audit`: extract SUMO TLS audit candidates, cluster nearby candidates into physical-intersection review groups, and attach map-review baseline fields.
- `sumo_tls_multisource_review`: create a human-review CSV that combines SUMO TLS candidates with OSM traffic-signal matches, region-aware map links such as Amap/Gaode for mainland China or Google Maps where appropriate, Mapillary, KartaView, optional official signal inventory rows, optional signal-plan rows, and optional field-photo evidence rows.
- `sumo_network_tls_aggregation_variant`: create a separate TLS cleanup review network by selecting one real SUMO junction per physical TLS audit cluster, rebuilding TLS with `netconvert --tls.discard-loaded --tls.set`, and reporting raw physical cluster count versus aggregated `tlLogic`/traffic-light junction counts.
- `sumo_network_connected_core`: extract the largest passenger component from an existing SUMO `.net.xml`, write a reusable connected-core network, and report discarded fragments.
- `sumo_network_routeability_probe`: generate named-road probe routes and a bounded `.sumocfg` for routeability checks.
- `sumo_network_routeability_audit`: generate random passenger trips, run SUMO, parse `summary.xml` and `tripinfo.xml`, and extend the horizon until all generated vehicles finish or `max_end` is reached.
- `sumo_network_topology_audit`: audit dense junction clusters and short internal edges that indicate over-fragmented OSM/netconvert topology, including a reference-free `join` / `needs_map_review` / `do_not_join` aggregation scorer.
- `sumo_network_overlapping_junction_audit`: audit close overlapping top-level junctions without modifying the network, while ignoring valid SUMO internal crossing and walkingarea layers.
- `sumo_network_reference_join_audit`: mine joined-junction cases from a reference `.net.xml` and match them against the candidate network by encoded source nodes first, with spatial topology clusters as a fallback.
- `sumo_network_reference_hierarchy_audit`: compare high-hierarchy candidate roads against a reference `.net.xml`, separating over-split corridors, out-of-reference-scope high roads, hierarchy mismatches, and protected link/slip-lane cases.
- `sumo_network_reference_scope_audit`: compare a candidate reference-visual-detail network against a reference `.net.xml` by OSM `highway.*` type counts, then flag over-included or absent-in-reference short dead-end detail fragments for review.
- `sumo_network_junction_aggregation_variant`: create a separate junction-join review variant from topology, reference-join, or overlapping-junction audit reports without overwriting the source network; overlapping groups are joined only when reference or human review confirms the physical-intersection core.
- `sumo_network_scope_pruning_variant`: create a separate reference-scope pruning review variant from a scope audit without overwriting the source network.
- `sumo_network_teacher_guided_repair_queue`: execute ready teacher-guided repair queue items against explicit plain node/edge/connection files, replay and normalize the teacher target internal subgraph by default, aggregate construction and parity gates, and keep non-ready or parity-failing candidates out of adoption.
- `sumo_network_teacher_guided_junction_variant`: build a diagnostic single-junction variant that replays a manual reference network's lane permissions, allowed movements, pedestrian ring, target internal subgraph, and target `tlLogic` onto candidate plain network files by default. Set `replay_target_internal_subgraph=false` only for a legacy lane/connection-attribute probe. Keep it at `diagnostic-demo` until Netedit connection-mode review approves the result.

Use a region-aware reality baseline as supporting evidence for current-network OSM
cleanup. Record whether each source uses WGS84/GCJ-02/BD-09 when coordinate
systems differ. Unresolved TLS
candidates keep the result at `construction-invalid`. The cleanup command does
not open a GUI or repair these findings. For a historical target, supply a
frozen source OSM file and matching dated evidence.

External OSM source patterns are tracked from OSMnx, OSMNet, pyrosm, SUMO osmGet/osmBuild, and osm-to-xodr. ASTRA borrows architecture and validation ideas from these projects without vendoring their source code.

The plugin does not certify full OSM cleanup, an authoritative TLS inventory,
controller generation, or controller-log inspection through MCP. Resolve a
place and confirm its bbox before the CLI cleanup call. A reference-matched run
requires both that bbox and a reference `.net.xml`. It reports differences and
keeps all network changes for a separate reviewed step.
