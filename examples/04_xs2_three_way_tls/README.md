# XS-2: one real three-way TLS intersection

XS-2 reuses the XS-1 safety contract on a second, smaller topology. It starts from one frozen OpenStreetMap extract (about 400 m), builds a passenger-only source network, and creates one reversible candidate for the Schloßlände × Schutterstraße T-junction in Ingolstadt.

The candidate contains two declared PlainXML operations: join the six nodes inside one physical signal cell, then replace netconvert's unsafe default phase grouping with a conservative two-stage static program. The program is a canonical simulation plan, not a claim about field timing.

## Run

From the repository root, with SUMO 1.27.1 available:

```powershell
.\.venv\Scripts\python.exe plugins\torii-sumo\scripts\run_xs2_three_way.py
```

The result is written to `outputs/xs2-three-way/`. Open `review.html` for the compact result and load `xs2-candidate.net.xml` with `review.add.xml` in NetEdit for the optional visual check.

> **File roles matter:** `xs2-source.net.xml` is the immutable OSM baseline. Visually grade only `xs2-candidate.net.xml`; the `tls_ownership_rebuild` gate proves that the three target TLS nodes become one physical junction/controller and that the old `joinedS_*` controller does not survive.

### Background NetEdit evidence on Windows

The review can be captured without activating NetEdit or sending global keyboard or mouse input:

```powershell
.\.venv\Scripts\python.exe plugins\torii-sumo\scripts\netedit_background_review.py `
  --summary outputs\xs2-three-way\summary.json `
  --out-dir outputs\xs2-three-way\netedit-review\final-background `
  --view-center 284.2,280.7 --zoom 500
```

The script resolves the candidate, target junction, and review overlay only from the hash-bound `summary.json` and sibling `manifest.json`. It refuses a source/candidate swap, a hash mismatch, residual source TLS identities, or a candidate without exactly one target TLS owner. It then opens a fresh non-activating NetEdit process for each image, switches Inspect/Traffic Light/Connection mode by targeting that window only, selects the target junction through a selection file, captures the window off-screen, and records the complete identity check and foreground-window samples in `netedit-background-review.json`.

The current capture contract uses exactly three site-level images rather than one image per lane. Internal activation and mode messages are sent directly to NetEdit's FOX/OpenGL canvas while the user's actual foreground process remains unchanged; the script deliberately does not emulate a global click. These screenshots independently check target ownership, signal presentation, and connection geometry. The exact `fromLane → via → toLane`, request/foes, and TLS/linkIndex verdict remains the code-native audit result.

## OSM-only physical-cell proposal

`intersection-proposal.json` keeps two physical-cell hypotheses separate. The old fixed 20 m IR covers only four of the six reviewed source junctions and adds one unrelated node. The independent signal-anchor vehicle-path closure identifies all six reviewed junction members without taking that list as input; it also excludes three bounded single-way shape nodes. It exposes four raw boundary ports and groups the complementary Schutterstraße one-way pair into one physical approach, yielding three physical approaches and T3.

The remaining uncertainty is now stated more precisely. Strict OSM `turn:lanes` interpretation produces six lane movements, while geometry/lane continuity produces seven and exactly matches the materialized candidate. Six related OSM turn restrictions still require path-level resolution; one of them starts on a boundary approach but has its `via` just outside the proposed physical cell, so it is retained as unresolved evidence instead of being silently dropped. Both semantic classes are preserved in `candidate-dag.json`; the real candidate binds to the geometry-continuity merge node, but its semantic disposition remains `review`.

## Why NEMA abstains here

`tls-topology.json` runs the same evidence contract used by XS-1. Physical-cell, controller ownership, Connection Mode, independent conflict, and seven-turn runtime closure pass. NEMA generation nevertheless abstains because the two movement hypotheses disagree and the nested restrictions are unresolved. No NEMA `.net.xml` is written for XS-2. This is an intentional policy pass—Torii recognizes that a runnable static program is not enough evidence to reassign classic phases safely.

## What must pass

- The prompt, spec, OSM snapshot, join patch, TLS patch, and toolchain are hash-bound.
- The OSM-only proposal records exact physical-cell membership evidence and keeps XS-2 topology/movement inference review-required.
- Both lane-movement variants, their exact movement delta, all six related path-level restrictions, and the materialized candidate's DAG binding remain explicit.
- The source and candidate are rebuilt from the same frozen input.
- The three source TLS nodes are rebuilt as one target TLS junction/controller, with zero residual source junction or controller IDs.
- The candidate has three incoming approaches, three outgoing approaches, and seven lane-level movements: three straight, two left, and two right.
- All seven internal paths, request/foes rows, and TLS/linkIndex bindings pass the code-native Connection Mode audit.
- The independent conflict audit reports no protected conflict, potential protected conflict, or missing permissive yield relation.
- Exact semantic diff reports no target-external change or new finding.
- SUMO loads the candidate and all seven exact-lane movement probes arrive without collision or teleport.
- The NEMA stage fails closed before materialization because legal movement semantics are not yet unique.
- The display-only overlay, HTML, manifest, and rollback record are generated.

## Why the explicit TLS patch exists

The join-only probe was rejected. Netconvert grouped one main-road right turn with the side-road movements under protected green; Torii's independent lane-envelope audit found the potential conflict. XS-2 therefore records an explicit two-stage program with yellow and all-red clearance. This is a reviewed child operation of the same merge hypothesis and is completely removed by rebuilding the source without either patch. It proves executable safety for this candidate, not that OSM evidence has uniquely determined classic NEMA phase ownership.

## Frozen machine-review-ready result

The `golden/` directory contains the path-sanitized review bundle, the explicit TLS ownership proof, and both networks. The expected machine state is `review_ready`; automatic promotion remains blocked. A visual-review record is added only after a NetEdit check and is never treated as human validation.

## Claim boundary

Passing XS-2 proves only this one frozen, vehicle-only, standard three-way slice. It does not certify field signal timing, pedestrians, bicycles, rail, ramps, shared controllers, complex channelization, other junctions, or city-scale cleaning.

OSM data is © OpenStreetMap contributors and is distributed under ODbL 1.0.
