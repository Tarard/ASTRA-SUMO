# Native bus-stop rejection test repair

Implementation: `69c2e32f65b0d0ce599fb173f0b53cb11e80203e`. Native programs: SUMO 1.27.1.
Startup and evidence isolation follow-up: `e844d2490ec1f89bf62e4189038eecc15635293e`.

## Failure

[Run 39227787886](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39227787886) failed the full-length bus-lane negative case.
It had 3452 passed tests, one failure, and 24 skips.
The native SUMO process exceeded 30 seconds while loading an illegal passenger stop.
This timeout came from waiting for the native process, after the earlier launcher and pipe changes.
Those earlier changes did not resolve this failure.
[The parallel run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39227794902) passed 3453 tests, with 24 skips.
Both outcomes remain in the record. The earlier CI retained only JUnit output, so its native log cannot be recovered.

## Repair

The positive case still requires a bus to complete its legal stop and a passenger vehicle to complete its route.
The negative case now sends the forbidden stop to a running native SUMO instance through TraCI.
It requires the specific permission exception, an empty stop list, a successful simulation step, and normal process exit.
A 30-second watchdog terminates a stalled native process. Cleanup also handles failed assertions.
No test was skipped or automatically rerun. The native-process deadline remains 30 seconds.
Connection establishment can wait within that same watchdog.

The two paths retain the same permission predicate.
In [SUMO 1.27.1's MSBaseVehicle](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/microsim/MSBaseVehicle.cpp),
both `addStops` and `addTraciStop` call `addStop`, which checks the stopping lane's vehicle permissions.
[Vehicle::setStop](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/libsumo/Vehicle.cpp) forwards the request to `addTraciStop`.
The test no longer checks fatal XML-error shutdown behavior. The native timeout remains unresolved.

Failed pytest cases now retain their temporary inputs, outputs, and test identity before teardown.
Each pytest session uses a separate artifact directory, so later failures do not overwrite earlier evidence.
The existing CI artifact upload includes these retained files.
If evidence copying fails, pytest keeps the original test failure and adds a diagnostic section.

## Local verification

- Six focused checks passed: both native bus-lane cases and four failure-artifact checks.
- The broader approach, artifact, and navigation checks passed all 70 tests.
- A separate native probe rejected the passenger stop, applied no stop, and exited normally.
- An intentional failing-test control verified artifact capture. Its expected exit code was 1.
- Two further intentional failures retained separate evidence directories and preserved both copies.
- Ruff, workflow YAML parsing, and whitespace checks passed.

## Full Windows CI

Both full-suite runs for `e844d24` completed successfully, each with 3457 passed tests and 24 skips:
[pull-request run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39231007495)
and [push run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39230998194).
These are two executions of the same suite. Their test counts are not added together.
The earlier incomplete runs for `69c2e32` were superseded by this follow-up and are not full-suite evidence.

An independent Claude review confirmed the native rejection checks and recommended broader startup allowance within the same deadline.
It also identified local artifact-folder reuse. The follow-up addressed both points.
The complete review remains in the local result directory. It used the subscription allowance only.

Raw logs and runners remain under `outputs/ci_failure_20261019_v1/`.
The adjacent JSON records their paths and hashes.

## Limits

The intermittent native timeout has not been reproduced locally or repaired in SUMO itself.
The old CI did not retain SUMO's log, so its stack trace cannot locate the stall inside SUMO.
This change verifies vehicle-stop legality without depending on the fatal XML-input error path.
It does not establish field access rules or traffic performance.
