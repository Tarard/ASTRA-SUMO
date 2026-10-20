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

The current full example selects 468 of 1,156 ordinary road segments.
It applies the existing OSM access corrections before scope selection.
It also retains the saved tentative official approach associations in `official-approach-anchors-v2.json`.
Those associations protect 126 source edges. The case records their confidence, source compatibility, and unresolved references.
The direct raw-native regression uses only the class rule and four V1 road anchors. It selects 367 segments.
Its permissions and study-retention reasons differ from the full example.
Earlier versions selected 540 or 533 segments because their boundary repairs cascaded into excluded residential routes.
The current rule retains main-road movements and local side-approach turns without those outside route obligations.
The first 367-road example still excluded some official approach fragments. The added approach evidence restores that context.
It retains Ernst-Merck-Straße even though its OSM class is tertiary, because the V1 study references it.
The imported source has stale pedestrian-internal signal references. Their narrow repair is recorded separately.
The raw before view remains unchanged. `selection_input` in `case.json` identifies the prepared full network used for pruning.

Use `normalized_full_network` from the selection report for a loadable full-network baseline.
Review signal and geometry changes before a later demand comparison.
The current signal summary separates 61 controllers removed with their junctions and six removed at dead ends.
No retained controller program changes in this scope.
The detailed report retains the raw strict comparison and controller IDs grouped by scope.
The runner compares canonical XML content with the frozen native import, ignoring generator comments and platform line endings.
The scope is exploratory. Successful loading and static connectivity do not establish complete corridor driving validity.
The source has 149 official motor-lane references. Of these, 132 have tentative ordinary-lane parts and 17 remain unmapped.
Seven associated lanes have zero directed overlap and no lateral measurement. They remain explicit uncertainties.
All 132 saved associations retain their ordinary-lane parts. Their measured coverage does not regress against the full source network.
This is preservation of tentative source correspondence, not field geometry acceptance.
The grouped official movement inventory still has incomplete correspondence. Approach preservation does not establish complete official turns.

## Regenerate the approach evidence

The frozen approach list can be regenerated offline from the frozen OSM, MAP, KML, and OCIT files:

```powershell
.venv/Scripts/python.exe examples/06_hamburg_ring1_demo/prepare_approach_evidence.py outputs/ring1-approach-evidence
```

This command rebuilds the full source network and measures all five official sites.
It writes `calibration/run.json` and `official-approach-anchors.json` in the new output directory.
It does not read the saved protection list to derive its result.
The current frozen inputs produce the same 126 protected source edges.

To extract retention evidence from an existing measured snapshot, use:

```powershell
.venv/Scripts/python.exe plugins/astra-sumo/scripts/generate_hamburg_approach_retention.py outputs/ring1-approach-evidence/calibration/run.json outputs/ring1-regenerated-anchors.json
```

Use a new output file. Neither command replaces the frozen fixture.
Low-confidence associations remain visible and retained. Their confidence does not prevent measurement or retention.
Each anchor separates whole-reference coverage from the coverage and lateral offset supplied by its individual source edge.
Each retained road records its basis: a declared study way, a declared edge, or tentative official approach correspondence.
Thirteen protected edges have no supporting fact marked high or confirmed. They remain tentative.
These measurements do not establish independent field accuracy or final detector correspondence.

## Remaining demonstration stages

1. Consolidate the physical junctions, including the LSA118 detail.
2. Place final-network virtual detectors at the real observation sections.
3. Calibrate against the frozen observations.
4. Compare observed and simulated counts in the same window.

The intended presentation uses actual matched NetEdit screenshots over one Google background.
Those figures and the count curves are not produced by this partial runner yet.
The [official source package](../../benchmarks/hamburg_ring1_official_sources_20261020_v1/README.md) supplies the next junction stage offline.
See the [case and six-stage design](../../docs/hamburg-readme-case.md) and [road-selection behavior](../../docs/road-hierarchy.md).
