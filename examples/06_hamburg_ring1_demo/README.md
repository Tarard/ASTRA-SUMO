# Hamburg Ring1 main-road demonstration

**Implemented: stages 1 and 2. The six-stage demonstration is not complete.**

This example uses the Binnenalster / Ring1 case and LSA118 shown in the repository README.
It rebuilds a full vehicle import from a frozen OSM snapshot, then selects the main-road scope.
The source package includes the local streets, so the excluded roads remain available for comparison.
No internet access is needed for this run.

## Run

Use the repository's declared environment from the repository root:

```powershell
.venv/Scripts/python.exe examples/06_hamburg_ring1_demo/run_road_selection.py outputs/ring1-demo
```

Use a new output directory. The installed SUMO package supplies the native binaries.
The [fixture](../../benchmarks/hamburg_ring1_road_scope_20261020_v1/fixture.json) records the bbox, source date, classes, and study anchors.

## Outputs

| File | Meaning |
|---|---|
| `case.json` | Implemented stages, input identities, reproduction checks, geometry/signal findings, and remaining work |
| `case-input.json` | Immutable copy of the effective case settings used by this run |
| `01-osm-result.json` | Full OSM import and its recorded postprocessing |
| `01-osm/sumo/ring1-full.net.xml` | Original native import for the before view |
| `02-main-roads/selection.json` | Road tiers, retention reasons, exclusions, connector repairs, and detours |
| `02-main-roads/boundaries.json` | Potential traffic boundaries, without invented flow rates |
| `02-main-roads/selected.net.xml` | Selected scope candidate |
| `02-main-roads/report.json` | Source preservation, native attempts, load, permissions, reachability, geometry, and signal changes |

The current full example selects 367 of 1,156 ordinary road segments.
It applies the existing OSM access corrections before scope selection.
The direct raw-native regression also selects 367 segments. Its permissions differ from the prepared full-network input.
Earlier versions selected 540 or 533 segments because their boundary repairs cascaded into excluded residential routes.
The current rule retains main-road movements and local side-approach turns without those outside route obligations.
It retains Ernst-Merck-Straße even though its OSM class is tertiary, because the V1 study references it.
The imported source has stale pedestrian-internal signal references. Their narrow repair is recorded separately.
The raw before view remains unchanged. `selection_input` in `case.json` identifies the prepared full network used for pruning.

Use `normalized_full_network` from the selection report for a loadable full-network baseline.
Review signal and geometry changes before a later demand comparison.
The runner compares canonical XML content with the frozen native import, ignoring generator comments and platform line endings.
The scope is exploratory. Successful loading and static connectivity do not establish complete corridor driving validity.

## Remaining demonstration stages

1. Consolidate the physical junctions, including the LSA118 detail.
2. Place final-network virtual detectors at the real observation sections.
3. Calibrate against the frozen observations.
4. Compare observed and simulated counts in the same window.

The intended presentation uses actual matched NetEdit screenshots over one Google background.
Those figures and the count curves are not produced by this partial runner yet.
The [official source package](../../benchmarks/hamburg_ring1_official_sources_20261020_v1/README.md) supplies the next junction stage offline.
See the [case and six-stage design](../../docs/hamburg-readme-case.md) and [road-selection behavior](../../docs/road-hierarchy.md).
