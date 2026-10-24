# Lane-count service repair, 2026-10-20

## Result

Code commit `d398174` connects added lanes and transfers a removed lane's turns to a compatible surviving lane.
The source network, fixture, and implementation hashes stayed fixed during the real-input run.
The [evidence file](lane-count-service-20261020-v1.json) records hashes, measurements, and failed driving pairs.

On the Am Sandtorkai fixture, road `47854310#1` now supports both tested changes:

| Edit | Boundary result | Original request routing |
|---|---|---|
| Add a right lane, 3 to 4 | New lane has two incoming and two outgoing movements | 100 of 100 requests route |
| Remove original lane 0, 3 to 2 | Its unique right turn to `714078605` remains available | 100 of 100 requests route |

Both candidates retain the four original road-to-road movements and their vehicle classes.
Five fixed driving requests completed on the source and both candidates, with no collisions.
These requests include a car and bus starting on lane 0, the unique right turn, and both incoming roads.
The added-lane car actually departed on `47854310#1_0`.

## Failed corridor evidence

The paired 100-vehicle runs used seeds 6, 10, and 42 with a 600-second horizon.
All six source/candidate pairs remain invalid because collisions or incomplete arrivals occurred.
No performance average is reported.

The unchanged source collides at internal lane `:77777614_2_0` under seeds 6 and 10.
Both candidates collide there under seed 42, when the source does not collide.
This reproduces a source defect across seeds. It does not establish that candidate timing had no effect.
The passing fixed-turn tests do not make these failed corridor runs valid.

Other real signalized additions can create protected-green conflicts when cloned lanes merge into one destination lane.
The tests retain that release failure. A new lane's static service is not evidence of safe signal control.

## Verification and reproduction

The five focused suites passed all 120 tests. Both new native driving cases executed.
The Windows native policy now requires these two cases in the complete suite.
Ruff and the whitespace check passed.
Complete CI for `d398174` was still running when this record was written.

Runner: [run_lane_count_service.py](run_lane_count_service.py).
Scenario: [frozen Hamburg fixture](../hamburg_loaded_links_20261018_v1/fixture.json).
Analysis: `core/network_edit_operations.py` and `core/paired_driving.py` under `plugins/astra-sumo/src/astra_sumo/`.
Local raw results: `outputs/lane-service-20261020-v1/`.

Run from the repository root with its declared environment:

```powershell
.venv/Scripts/python.exe benchmarks/public_network_development_v2/run_lane_count_service.py outputs/lane-service-reproduction
```

Use a new output directory. The runner retains failed runs and checks its immutable inputs.
The recorded run started from parent commit `54f5218` with implementation bytes that match `d398174`.

This fixture covers Am Sandtorkai. It is separate from the README's Binnenalster/LSA118 paper case.
These results are engineering checks, not sensor calibration or traffic-improvement results.
