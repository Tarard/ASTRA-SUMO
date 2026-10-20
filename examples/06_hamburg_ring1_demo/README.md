# Hamburg Ring1 main-road demonstration

**Offline commands cover stages 1–4. Junction correspondence remains partial. The calibration demonstration is not complete.**

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

## Continue through junctions and detectors

Regenerate the official inputs, then continue the road-scope run above:

```powershell
.venv/Scripts/python.exe examples/06_hamburg_ring1_demo/prepare_approach_evidence.py outputs/ring1-official-inputs
.venv/Scripts/python.exe examples/06_hamburg_ring1_demo/run_junction_construction.py outputs/ring1-demo/case.json outputs/ring1-official-inputs/official-sources.json outputs/ring1-junctions
```

All inputs come from the frozen repository packages. Use a new output directory for each run.
The command calls shared source readers, grouping, conversion, typed edits, movement probes, and field-loop writers.
The first five-node run can take several minutes because it tests source lane changes for the retained vehicle classes.

`junction-edits.json` supplies the explicit LSA119 lane-count correction and its lane connections.
The added motor lane inherits its source donor's permissions, including bus access. Bicycle access remains separate.
The workflow removes only source-model turns whose compiled permission intersection is empty before applying that correction.
It records every retained movement's effective permissions after each such removal.

| Output under `outputs/ring1-junctions` | Meaning |
|---|---|
| `run.json` | Current stage, source identities, measurements, warnings, and remaining coverage |
| `plans/` | Official MAP/KML movement geometry and permissions, without aerial tracing |
| `physical-groups.json` | Physical parts derived from official boundaries and the source network |
| `official.net.xml` | Separate constructed candidate |
| `movement-candidate.net.xml` | Constructed network before declared supplementary-connection exclusions |
| `connection-exclusions.json` | Declared exclusions, retained permissions, geometry comparison, and refreshed movement audit |
| `movement-probes/summary.json` | Official movement denominator and exact lane-chain driving results |
| `field-bindings/` | Published count-field points and tentative lane associations |
| `field-loops.add.xml` | E1 definitions for active field associations |
| `field-layout.json` | Native layout load, published station membership, and missing fields |
| `count-influence.json` | Static potential count influence of unresolved movements, with unknown outlet associations retained |
| `lane-edit-*/verification.json` | Lane-count effect, access, geometry changes, and service checks |

The earlier five-node candidate verified 92 of 114 official movements and loaded 13 field loops.
Twenty-two movements and four supplementary motor connections remain unresolved.
The current case configuration declares four connection exclusions after official movement construction.
They restrict this demonstration to official movements and source-verified supplements.
Each exclusion retains all other loaded movement permissions. Official boundary pairs and nonmotor access remain protected.
The runner retains the pre-exclusion network and compares ordinary road geometry, logical lengths, and junction contours after exclusions.
Missing verified support does not establish a real-world turn prohibition.
The [new complete replay](../../benchmarks/hamburg_ring1_road_scope_20261020_v1/public-replay-evidence-v3.json) retains all 92 passing official movements and 13 field loops after these exclusions.
It has zero missing required boundary pairs and zero unsupported supplementary pairs. Twenty-two official movements remain unresolved.
The full model differs from the prior version by the four exclusions and restored source precision on 13 lane lengths and two lane shapes.
The maximum restored shape difference is 0.0057 m. The restored shapes match the pre-cleanup source.
Its network hash differs from the saved v1 reference. The runner reports that difference and retains the older reference.
The new model has one complete Windows replay. The earlier model's Windows/Linux match does not establish the same result for this version.
The lane-count stage also has a neighboring geometry change under review.
Every declared official movement remains in the denominator.
Field points are published reference locations. Their projections do not establish surveyed detector cross-sections.
The one-second layout load has no traffic demand or fitted counts.
The [fresh public replay record](../../benchmarks/hamburg_ring1_road_scope_20261020_v1/public-replay-evidence-v2.json) binds the construction run to unchanged input and implementation hashes.
Fresh Windows and independent Linux runs have identical canonical network and loop XML contents.
The earlier saved-input record retains the mismatch caused by reusing a road-scope result from before the source-cut repair.
The count-influence diagnostic found 19 potentially relevant movements and two unknown outlets.
`run.json` now compares canonical network and loop XML hashes with `replay-reference-v1.json`, along with movement and loop counts.
Canonicalization removes comments and presentation whitespace while retaining model values and element order.
A reference mismatch remains a warning and does not suppress measurements.
The road-scope case also records its selected-network content hash and shared geometry dependencies.
Rebuild a saved road-scope result after a relevant shared geometry change before comparing complete construction outputs.

`load_warning_records` names each intermediate edit stage and records whether its exact SUMO warning line also appears in the final layout load.
An absent identical line is not a safety verdict. Final layout warnings remain recorded separately.
Count influence uses compiled permissions and allowed ordinary lane changes as an upper bound.
It ignores lane-change space, time, signals, and queues. Saved outlet hypotheses are not exhaustive.
It does not establish native driving or justify dropping a movement or fitting target.
Hash mismatches in this read-only diagnostic remain warnings. It records actual and declared hashes and treats correspondence as unverified supplied hypotheses.
Invalid graph data and attempts to replace existing output files still stop the diagnostic.

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

The current full example selects 467 of 1,156 ordinary road segments.
It applies the existing OSM access corrections before scope selection.
It also retains the saved tentative official approach associations in `official-approach-anchors-v3.json`.
Those associations protect 130 source edges. The case records their confidence, source compatibility, and unresolved references.
The direct raw-native regression uses only the class rule and four V1 road anchors. It selects 367 segments.
Its permissions and study-retention reasons differ from the full example.
Earlier versions selected 540 or 533 segments because their boundary repairs cascaded into excluded residential routes.
The current rule retains main-road movements and local side-approach turns without those outside route obligations.
The earlier association replay retained 468 roads. The v3 replay uses explicit KML geometry and local anchor headings.
It excludes one old source association and retains the updated source parts.
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
The source has 149 official motor-lane references. Of these, 135 have tentative ordinary-lane parts and 14 remain unmapped.
Seven associated lanes have zero directed overlap and no lateral measurement. They remain explicit uncertainties.
All 135 saved associations retain their ordinary-lane parts. Their measured coverage does not regress against the full source network.
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
The current frozen inputs produce the same 130 protected source edges.
`source-reproduction.json` records whether the rebuilt raw network matches the frozen input content.
The road-selection `case.json` reports `reproduction.status` separately from its measured construction status.
An input mismatch remains visible while exploratory measurements continue.
The required visual-profile type maps are located from the selected SUMO installation even when `SUMO_HOME` is absent.

To extract retention evidence from an existing measured snapshot, use:

```powershell
.venv/Scripts/python.exe plugins/astra-sumo/scripts/generate_hamburg_approach_retention.py outputs/ring1-approach-evidence/calibration/run.json outputs/ring1-regenerated-anchors.json
```

Use a new output file. Neither command replaces the frozen fixture.
Low-confidence associations remain visible and retained. Their confidence does not prevent measurement or retention.
Each anchor separates whole-reference coverage from the coverage and lateral offset supplied by its individual source edge.
Each retained road records its basis: a declared study way, a declared edge, or tentative official approach correspondence.
Eighteen protected edges have no supporting fact marked high or confirmed. They remain tentative.
These measurements do not establish independent field accuracy or final detector correspondence.

## Remaining demonstration work

1. Resolve the remaining official lane relations, supplementary turns, and neighboring geometry change.
2. Declare signal timing and the comparison window, then freeze the final network and detector positions.
3. Fit against the frozen observations and retain all field-level residuals.
4. Compare observed and simulated counts in the same window, with separate independent validation.

The intended presentation uses actual matched NetEdit screenshots over one Google background.
Those figures and the count curves are not produced by this partial runner yet.
The [official source package](../../benchmarks/hamburg_ring1_official_sources_20261020_v1/README.md) supplies the next junction stage offline.
The [source-count package](../../benchmarks/hamburg_ring1_count_window_20261020_v1/README.md) checks official field-to-station aggregation offline.
It contains no simulated or fitted counts.
The [earlier five-node driving evidence](../../benchmarks/hamburg_ring1_road_scope_20261020_v1/full-movement-driving-evidence-v1.json) retains the diagnostic construction before this shared replay command.
Its local runner references remain historical. The commands above replace those scripts for the construction and detector steps.
See the [case and six-stage design](../../docs/hamburg-readme-case.md) and [road-selection behavior](../../docs/road-hierarchy.md).
