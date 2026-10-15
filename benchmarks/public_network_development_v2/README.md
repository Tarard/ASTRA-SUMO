# Historical MoST input supplement

This catalog supplements [the four-model v1 corpus](../public_network_development_v1/catalog.json).
It preserves that catalog and adds the prepared input from the published MoST network revision.
It is the same Monaco model family, not another independent city.

## Reproduce

Run from the repository root with the project environment and a new result directory:

```powershell
$native = (Resolve-Path .venv/Lib/site-packages/sumo/bin/netconvert.exe).Path
./.venv/Scripts/python.exe -m astra_sumo.cli network reference-corpus benchmarks/public_network_development_v2/catalog.json outputs/public-reference-cache-v2 outputs/public-reference-run-v2 --netconvert-binary $native --json
```

Use an absolute native binary path from the intended SUMO installation.
The command verifies each source file against its catalog size and SHA-256.
The upstream files retain GPLv3 licensing. Downloaded networks and source data remain in the cache.

## Source and recipe

The [pinned upstream revision](https://github.com/lcodeca/MoSTScenario/tree/d00e82945f5c6322a98ad2df4a8d5f48274fca53)
contains the matching prepared input under `tools/static/most.raw.osm`.
The v1 record had used its later replacement under `tools/most.raw.osm`.
The supplement pins the earlier network, input, configuration, five type maps, and license.

The builder reads supported import settings from the configuration.
OSM and type-map paths must match catalog-bound files.
It preserves elevation, crossings, ramp, and other declared import settings.
It overrides junction joining, signal joining, and geometry removal for the unjoined comparison.
It does not execute upstream output paths or report paths.
Unsupported settings require an explicit implementation change.

Regeneration uses SUMO 1.27.1. It does not reproduce the original historical binary.
The configuration, actual arguments, overrides, binaries, and source hashes remain in the result.

## Measured result

The retained local run is `outputs/calibration_driving_20261016_v1/most-corpus-v2/`.
The published network loads. The prepared import succeeds.
Under the existing exact correspondence rule, 10,969 comparison records contain:

| Record | Known targets |
|---|---:|
| Joined source-node pairs | 58 |
| Separate source-node pairs | 599 |
| Retained ordinary lane connections | 820 |
| Added ordinary lane connections | 80 |
| Removed ordinary lane connections | 2 |
| Total known targets | 1,559 |

The remaining 9,410 records retain unresolved correspondence.
These are model-relative development targets. Their manual-edit origin remains unestablished.
They do not establish a field defect or the precision of an automatic repair.

The prepared input includes edited and synthetic sources.
Its repository revision does not date the original OSM snapshot or the field state.
Both dates remain unknown. No current OSM substitutes for the missing original snapshot.
