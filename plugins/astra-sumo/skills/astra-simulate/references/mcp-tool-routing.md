# ASTRA Tool Map

Which MCP tool, CLI command or `astra workflow` function does a job. Every CLI command except `astra mcp serve` takes `--json`. MCP arguments use the CLI argument names in snake_case.

## MCP profiles

The server exposes one profile per process. The plugin's MCP launcher reads `ASTRA_MCP_PROFILE` (unset means `default`); `astra mcp serve --profile <name>` sets the profile directly and defaults to `default`.

- `default`: the ten tools in the table below.
- `netedit`: `astra.netedit.open`, `astra.netedit.observe`, `astra.netedit.act` and `astra.netedit.close`. They share one persistent session, so separate CLI processes cannot chain them. The editing loop is in `$astra-build`.
- `legacy`: 73 historical tools (`sumo_*` and `astra_auto_workflow`) for compatibility. Most `astra workflow` functions appear there under the same name, so their input schemas list the arguments.

## Default tools and CLI equivalents

| MCP tool | CLI command | Returns |
|---|---|---|
| `astra.preflight` | `astra preflight` | SUMO binaries with versions, `traci`, `sumolib`, `SUMO_HOME` |
| `astra.config.inspect` | `astra config inspect <baseline.sumocfg> <variant.sumocfg>` | missing input files and shared output paths |
| `astra.run.compare` | `astra run compare --baseline-summary <f> --baseline-tripinfo <f> --variant-summary <f> --variant-tripinfo <f>` | completion counts and trip means for each run |
| `astra.place.resolve` | `astra place resolve <place> --limit <n>` | OSM area candidates with bbox |
| `astra.intersection.classify` | `astra intersection classify <osm.xml> <seed-osm-node-id>` | physical junction structure; read-only |
| `astra.signal.classify` | `astra signal classify <ocit-c.xml>` | OCIT-C signal-device inventory; read-only |
| `astra.network.audit` | `astra network audit <net.xml> <out-dir> --profile quick` or `standard` | topology (`quick`), plus Connection Mode and overlapping junctions (`standard`); no SUMO run |
| `astra.network.compare` | `astra network compare <source.net.xml> <candidate.net.xml> <out-dir>` | Connection Mode changes in the candidate |
| `astra.demand.audit` | `astra demand audit <expected-counts.csv> <e1-output.xml> <out-dir>` | expected counts against a SUMO E1 output, counted as `nVehEntered` |
| `astra.review.create` | `astra review create <out-dir> --net-file <net.xml>` | review HTML over a network and its audit reports |

## CLI only

- `astra network routeability <net.xml> <out-dir>` runs random passenger trips in SUMO until every vehicle finishes or `--max-end` (default 2400 s) is reached. It is the check that a network is usable; `network audit` does not run it.
- `astra network select-roads`, `calibrate`, `review-effects`, `verify-edit`, `parking-facilities`, `movement-probes`, `reference-corpus` and `reference-check`, `astra review render` and `astra netedit review` build and review networks; see `$astra-build`.
- `astra hamburg <subcommand>` runs the Hamburg official-data workflows; see `$astra-build` and `$astra-calibrate`.
- `astra workflows` and `astra workflow selected` read and run the scenario catalog; the steps are in the astra-simulate `SKILL.md`.
- `astra workflow <tool> <request.json>` calls one allowlisted function. `sumo_osm_cleanup_workflow`, `hamburg_corridor_select`, `hamburg_corridor_bind_tls_clusters` and `sumo_hamburg_named_detector_bindings` exist only there, not as MCP tools.

## Finding a workflow function and its arguments

- Names follow the job: `sumo_run_*` and `sumo_collect_evidence` run and record; `sumo_osm_*` imports OSM; `sumo_network_*` audits networks and builds separate candidate variants; `sumo_tls_*` reviews traffic lights; `sumo_intersection_*` and `sumo_nema_four_way_reference_workflow` handle single junctions; `sumo_detector_*` and `sumo_digital_twin_replay_validate` handle detectors and demand; `sumo_hamburg_*` and `hamburg_*` serve the Hamburg case.
- An unknown tool name makes `astra workflow` print every allowed name on stderr.
- Full signature in a repository checkout: `.venv/Scripts/python.exe -c "import inspect; from astra_sumo.legacy_tools import WORKFLOW_TOOLS as W; print(inspect.signature(W['<tool>']))"`.

## Legacy router

`astra_auto_workflow` (legacy profile) takes a `workflow_selection` with the four fields of `astra workflow selected` and dispatches it first. With `autonomy_mode` set to `inspect-only` or `ask-first`, it does not execute the selection. Without a selection, it falls back to an older text classifier. Prefer `astra workflow selected`.
