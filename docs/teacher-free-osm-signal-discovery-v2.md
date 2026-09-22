# Teacher-free OSM signal discovery v2

Status: implemented research milestone, review-only. Automatic promotion remains blocked.

## Why this path exists

The earlier small-intersection workflows could validate a supplied OSM seed and a reviewed scope, but that did not prove that Torii could find the same modeling unit by itself. This milestone moves the first decision boundary in front of all teacher, reviewed-scope, expected-topology, expected-count, and materialized-network inputs.

The machine path is now:

```text
frozen OSM bbox
  -> enumerate signal and signalized-crossing anchors
  -> conservative exact-anchor-set grouping
  -> vehicle-graph one-center medoid
  -> physical-cell and boundary-port hypothesis
  -> OSM restriction/lane-movement variants
  -> reversible split/merge/partial-repair candidate DAG
  -> optional materialized SUMO network as post-hoc evidence only
  -> hash-bound JSON, GeoJSON, HTML, and manifest
```

Generation explicitly forbids:

- a teacher network;
- a reviewed junction scope;
- expected topology, approach count, or movement count;
- a materialized candidate network.

These inputs may be used only after generation to evaluate a frozen hypothesis. No generated DAG selects a topology, writes a network, reconstructs field timing, or authorizes automatic promotion.

## What was implemented

`intersection/autodiscovery.py` scans every OSM signal anchor, deduplicates anchors that produce the same local closure, keeps overlapping non-identical closures separate, and selects a canonical graph medoid. It does not require a user-selected seed.

`intersection/hypothesis.py` generates the physical cell, boundary ports, competing movement semantics, and the reversible candidate DAG before looking at a SUMO candidate. The three topology alternatives remain explicit: preserve split/shared controller, merge one physical cell, and partial internal repair.

Boundary-port identity no longer includes bearings or coordinates. Geometry remains evidence, while the stable identity uses inside/outside OSM nodes, way identity, and flow role. This prevents centimetre-scale coordinate noise from changing movement identities.

`intersection/pedestrian_facility.py` adds a source-only crossing audit. It separates two claims:

1. OSM may explicitly identify a signalized crossing.
2. OSM may or may not contain a complete pedestrian support path across a passenger road.

The first claim feeds the existing independent ROW source oracle. The second is a separate topology gate. Geometry never infers right-of-way, and `request/foes`, generated internal lanes, TLS programs, and runtime behavior are not read during source classification.

Passenger-road classification is now an explicit OSM whitelist plus access semantics. A feature such as `highway=platform` can no longer enter the vehicle graph merely because it lacks a restrictive access tag.

The artifact workflow refuses to clear an unknown directory, binds ownership to the exact output root, refuses an output directory containing the frozen source OSM, verifies the source hash after generation, and writes a manifest over every generated review artifact.

## Falsifiable results

| Case | Automatic discovery result | Post-hoc result |
|---|---|---|
| XS1 real four-way TLS | 13 anchors -> 2 conservative cells; canonical main cell has 4 approaches; both independent movement methods produce the same 12 movements | Automatically generated DAG binds the existing materialized candidate: 12/12 mapped, semantic disposition `suggest` |
| XS2 real three-way TLS | 12 anchors -> 7 conservative cells; canonical target seed is `7009179663`, not the old caller seed; cell has 3 approaches | Materialized candidate binds 7 movements; geometry-continuity is exact while strict OSM turn-lane evidence permits only 6, so disposition remains `review` |
| Held-out synthetic X4 OSM | 1 anchor -> 1 four-way vehicle cell; both movement methods produce 12 movements | Discovery is invariant to parsed dictionary order |
| Complete signalized crossing | 2 opposed pedestrian arms, 2 opposed vehicle arms, 90-degree crossing | Source ROW class is `signalized`; facility reaches `review_ready`, but model/phase/runtime gates and automatic promotion remain blocked |
| Incomplete signalized crossing | Signalized OSM tag exists, but only one pedestrian support arm | Source ROW class remains `signalized`; facility is blocked on `pedestrian_support_arm_count_not_two` rather than being mislabeled as uncontrolled |

The real XS pedestrian source census is intentionally not converted into a correctness claim:

- XS1: 7 signalized crossing records, all blocked because the OSM extract contains no explicit two-sided support path at those nodes.
- XS2: 5 signalized crossing records, 2 source-topology `review_ready`, 3 blocked on incomplete or unsupported incidence.

These are evidence-coverage results, not seven or three asserted network defects.

The existing ROW-1 independent experiment was rerun with SUMO/netconvert 1.27.1: 15 cases, 12 runtime probes, 0 failed cases, 0 unsafe false passes, and automatic promotion blocked. Its expected answer still comes only from frozen source evidence; request/foes is treated as a model claim.

Full repository validation after this milestone: 1,329 tests passed and the full Ruff check passed.

## Reproduce

```powershell
python plugins/torii-sumo/scripts/run_teacher_free_discovery.py `
  --osm examples/03_xs1_four_way_tls/input/xs1-89129156.osm.xml.gz `
  --output-dir outputs/teacher-free-xs1 `
  --traffic-side right
```

The output contains:

- `teacher-free-discovery.json`: complete evidence, hypotheses, candidate DAGs, and pedestrian source audits;
- `teacher-free-discovery.geojson`: exact review locations;
- `teacher-free-discovery.html`: compact human review index;
- `manifest.json`: frozen input and generated-artifact SHA-256 identities.

The independent pedestrian ROW experiment is reproducible with:

```powershell
python plugins/torii-sumo/scripts/run_pedestrian_row_experiment.py `
  --output-dir outputs/row-1 `
  --netconvert-binary "C:/Program Files (x86)/Eclipse/Sumo/bin/netconvert.exe" `
  --sumo-binary "C:/Program Files (x86)/Eclipse/Sumo/bin/sumo.exe"
```

## Remaining limits and next experiment

This milestone proves automatic discovery and post-hoc semantic binding for small signal cells. It does not prove that the merge hypothesis is physically correct, that a candidate's independent conflict graph is safe, that pedestrian signal phases are complete, or that the method generalizes to arbitrary OSM morphology.

The next smallest experiment should materialize exactly one auto-discovered candidate without reintroducing a caller seed or reviewed scope. The materializer must consume only the frozen candidate DAG and boundary ports, then run netconvert, SUMO load, complete movement routeability, Connection Mode, exact outside-scope semantic diff, independent conflicts, and rollback closure. XS2 remains the required negative control: its 6-versus-7 movement disagreement must continue to block automatic selection.
