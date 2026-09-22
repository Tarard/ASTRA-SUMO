# XS-1: one real four-way TLS intersection

This is Torii's deliberately small vertical slice. It starts from one frozen OpenStreetMap extract (about 400 m), builds a source network, applies exactly one reversible junction-join operation, and audits one standard vehicle-only four-way traffic-light intersection.

## Run

From the repository root, with SUMO 1.27.1 available:

```powershell
.\.venv\Scripts\python.exe plugins\torii-sumo\scripts\run_xs1_four_way.py
```

The result is written to `outputs/xs1-four-way/`. Open `review.html` for the compact result and `xs1-candidate.net.xml` with `review.add.xml` in NetEdit for the final visual check.

> **File roles matter:** `xs1-source.net.xml` intentionally retains the fragmented OSM baseline (10 nearby TLS junctions in this case). It is evidence, not the cleaned result. Visually grade only `xs1-candidate.net.xml`; the `tls_ownership_rebuild` gate proves that the target becomes one physical TLS junction/controller and that no old target controller ID survives.

The expected machine state is `review_ready`; after the NetEdit check it is `review_ready_visual_checked`. Automatic promotion remains blocked in both states.

### Background NetEdit evidence on Windows

The primary cleaned network and the secondary NEMA topology candidate can be reviewed without leaving NetEdit in the foreground:

```powershell
.\.venv\Scripts\python.exe plugins\torii-sumo\scripts\netedit_background_review.py `
  --summary outputs\xs1-four-way\summary.json `
  --candidate-role primary `
  --out-dir outputs\xs1-four-way\netedit-primary

.\.venv\Scripts\python.exe plugins\torii-sumo\scripts\netedit_background_review.py `
  --summary outputs\xs1-four-way\summary.json `
  --candidate-role nema-topology `
  --out-dir outputs\xs1-four-way\netedit-nema
```

Each run produces exactly three site-level images: Inspect, Traffic Light, and Connection mode. The script sends internal messages to NetEdit's FOX canvas, captures the window with `PrintWindow`, verifies that all three images differ, and records that the user's foreground process was restored. No global keyboard or mouse event is sent. The images are supporting review evidence; the code-native lane/path/request/TLS audits remain authoritative.

## OSM-only physical-cell proposal

`intersection-proposal.json` records two independently generated physical-cell hypotheses before looking at the reviewed join scope. The legacy fixed 20 m core finds 9 of the 11 reviewed source junctions. The signal-anchor vehicle-path closure finds all 11 and independently groups the boundary ports into four physical approaches.

Two lane-movement methods are then kept separate: strict OSM `turn:lanes` interpretation and geometry/lane-continuity interpretation. On XS-1 they produce the same 12 stable lane movements with no unresolved restriction. `candidate-dag.json` expands that semantic class across preserve-split, merge, and partial-repair topology hypotheses. The actual netconvert candidate is bound exactly to the reversible merge node in `candidate-dag-binding.json`; this makes the operation traceable but still does not authorize it automatically.

## Evidence-gated NEMA topology

`tls-topology.json` is generated only after the physical approaches, all 12 movements, controller ownership, Connection Mode, independent conflict graph, and all-turn runtime evidence agree. It reuses Torii's older strict NEMA builder instead of replacing it.

For XS-1, a separate `nema-topology/standard/nema-topology.candidate.net.xml` is created. It maps the 12 movements to eight classic NEMA phases, keeps the primary cleaned candidate immutable, and then passes a second netconvert round-trip, exact semantic diff, Connection Mode audit, independent conflict audit, SUMO load, and 12/12 all-turn smoke. Generic cycle and clearance values are executable placeholders for a canonical simulation plan; they are not a field-timing claim and still require review.

## What must pass

- The prompt, structured spec, OSM extract, join patch, and toolchain are hash-bound.
- The OSM-only intersection proposal is generated and preserves both hypotheses, their disagreement, and the automatic-promotion block.
- Both movement methods agree on the same 12 stable movements, and the materialized candidate binds exactly to one reversible DAG node.
- `netconvert` builds both source and candidate from the same frozen OSM input.
- TLS ownership is reduced from ten fragmented target junctions/controllers to one target junction/controller, with zero old target IDs left in the candidate.
- The candidate has 12 direct vehicle movements and 12 traceable internal paths.
- request/foes and TLS/linkIndex bindings pass the code-native Connection Mode audit.
- Independent movement-conflict safety finds no protected conflict or missing permissive yield relation.
- The exact semantic diff has no change outside the target plus its four-node guard ring.
- One vehicle for every right, straight, and left movement loads, departs, and arrives without collision or teleport.
- The optional NEMA topology is proposed only after upstream closure, preserves the movement/DAG binding, changes nothing outside the target, and independently passes all eight topology-validation gates.
- The display-only `additional.xml`, HTML report, manifest, and rollback record are generated.

## Candidate and rollback

The primary candidate operation joins the eleven fragmented OSM/SUMO nodes listed in `input/join.nod.xml`. The source file is never modified. Rollback means omitting that patch and rebuilding with the recorded source command. The NEMA network is a child review candidate, not a replacement: its rollback is simply to discard it and retain `xs1-candidate.net.xml`.

## Frozen machine-review-ready result

The small machine evidence bundle is committed in `golden/` so it can be opened before rerunning the workflow:

- [`golden/review.html`](golden/review.html): concise gate report.
- [`golden/xs1-candidate.net.xml`](golden/xs1-candidate.net.xml): reviewed candidate network.
- [`golden/review.add.xml`](golden/review.add.xml): display-only target and guard overlay.
- [`golden/summary.public.json`](golden/summary.public.json): path-sanitized evidence summary.
- [`golden/tls-ownership.public.json`](golden/tls-ownership.public.json): explicit source 10 → candidate 1 TLS ownership proof.
- [`golden/manifest.public.json`](golden/manifest.public.json): hashes, toolchain, inputs, and gate closure.
- [`golden/rollback.public.json`](golden/rollback.public.json): inverse operation.

The committed result records one target TLS junction/controller, zero residual old TLS IDs, 12/12 traceable movements, four right/straight/left movements each, 12/12 arrived vehicles, zero collision/teleport, zero outside-scope delta, and a passing independent safety audit. NetEdit visual review remains pending and is not counted as human validation.

## Claim boundary

Passing this example means only that this frozen, vehicle-only, standard four-way intersection and one canonical NEMA topology are machine-ready for review. Automatic promotion remains blocked. It does not establish field timing and does not cover pedestrians, bicycles, rail, ramps, shared TLS controllers, complex channelization, other intersections, or city-scale cleaning.

OSM data is © OpenStreetMap contributors and is distributed under ODbL 1.0.
