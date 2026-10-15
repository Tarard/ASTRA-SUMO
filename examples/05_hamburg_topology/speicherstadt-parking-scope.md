# Speicherstadt garage interior: surface model scope

## Source decision

The reviewed location is Parkhaus Speicherstadt at Am Sandtorkai 6.
The [facility operator](https://www.contipark.de/de/parken/hamburg/parkhaus-speicherstadt/)
confirms vehicle access, 1.95 m height clearance, and 2.52 m width clearance.
The source OSM building is `60260768`. Its tags identify a parking building.
Covered driveway ways `174284968`, `882966132`, and `882966133` form the disputed interior component.

The 2026 Hamburg aerial image shows the roof above that component.
It does not establish that the covered driveways are absent.
The reviewed decision excludes these indoor routes from this surface corridor model.
It retains the exterior approaches and does not change OSM.
No street entrance or indoor turn geometry is certified by this decision.

The [frozen ledger](speicherstadt-parking-scope.ledger.json) removes eight directed SUMO roads.
It applies only to its exact source SHA-256.
The tunnel-preservation exception applies to these declared targets.
The materializer separately checks the declared exterior.
Other sites need their own source evidence, scope, and review.

## Reproduce the candidate

The generated source network is not shipped in this repository.
Supply the source with SHA-256
`5bf18df7329c7eddf3dd2c394baadc24c9e023f1712a7336c6e4341551eb509a`.
Change `source_net_file` in a local ledger copy to its absolute path.
Keep `source_sha256` unchanged. Use a new output directory.

Run this from the repository directory with its own Python environment:

```python
import json
from pathlib import Path
from astra_sumo.core.corridor_edit_ledger import materialize_corridor_edit_variant

repo = Path.cwd()
ledger = json.loads((repo / "examples/05_hamburg_topology/speicherstadt-parking-scope.ledger.json").read_text())
ledger["source_net_file"] = str((repo / ledger["source_net_file"]).resolve(strict=True))
result = materialize_corridor_edit_variant(
    ledger=ledger,
    output_dir=repo / "outputs/speicherstadt-parking-reproduction-v1",
    netconvert_binary=str(repo / ".venv/Lib/site-packages/sumo/bin/netconvert.exe"),
    prefix="surface-corridor",
)
print(result["status"], result.get("candidate_net_file"))
```

This uses the existing deletion materializer. It preserves the source network.
The candidate remains a review artifact. Materialization does not approve the final network.

## Verified local construction

- Eight ordinary roads and their dependent internal paths were removed.
- Two interior junctions were removed. Four entrance boundary nodes remain as road ends.
- All eight exterior driveway segments remain.
- Four adjacent driveway lane geometries changed within the declared scope.
- Exterior ordinary-road attributes, signal programs, crossings, and walking areas matched the parent.
- Both named mainline directions passed native checks in the parent and candidate.
- Four parking approaches passed native entry or exit checks in both networks.

The mainline checks used fixed endpoint lanes and traversed LSA 2349, 2394, and 2403.
The twelve individual checks used passenger vehicles, seed 104, and a 600 s horizon in SUMO 1.27.1.
They establish these individual paths only.

## Retained batch failure

The original frozen set contains 100 synthetic requests.
Eleven requests have an endpoint inside the removed component.
They remain recorded separately. They were not replaced or counted as successful trips.
The same remaining 89 requests and their planned edge routes were used in both networks.

With the existing default random-number configuration and seed 104, the parent completed 89/89 requests.
The candidate completed 44/89 by 2400 s, with 43 vehicles running and two awaiting insertion.
Neither run had a collision or teleport.
This batch does not pass the complete traffic gate.

SUMO 1.27.1 assigns a lane RNG index as `numericalID % myRNGs.size()`.
Removing lanes can change that assignment despite an unchanged seed.
The source is [`MSLane.cpp`, tag v1_27_1](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/microsim/MSLane.cpp#L295).

A separate check fixed `threads=1` and `thread-rngs=1` in both networks.
Both completed 44/89 and exhibited the same remote gridlock.
All completed trip records, completed route records, and per-step summary values matched, excluding runtime duration.
These results show sensitivity to the random-number configuration and an unresolved traffic condition in the parent network.
They do not establish successful traffic operation or justify changing the random configuration to obtain a pass.

## Evidence locations

Generated evidence remains local and is not part of this example:

- Runner and native commands: `outputs/claude_parking_scope_20261015_v1/build.py`, `check.py`, `check_retained.py`, and `rng_probe.py`.
- Configuration and scope: `scope-decision.json`, `ledger/`, and `frozen-request-disposition.json` in that directory.
- Native candidate and rollback: `materialized/` and `ledger/`.
- Mainline and entry/exit checks: `mainline/` and `portal-*/`.
- Batch results: `fixed-source-v2/`, `fixed-candidate-v2/`, and `single-rng-*/`.
- Review images: `review/`. Use the complete structure comparison to inspect the actual removal.

The first batch attempt expected 100 requests despite the declared 89-request subset.
Its setup failures remain under `fixed-source/` and `fixed-candidate/`.
The corrected count did not remove the candidate's traffic failure.

Parking arrival and departure demand needs an explicit boundary mapping before a full-demand study.
The eleven interior requests cannot silently disappear from a future traffic comparison.
