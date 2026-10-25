# Permission, routing, and runtime maintenance

## Windows CI failure

The [push run at acaf1dd](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39236972352)
had 3,471 passed tests, one failure, and 24 skips.
`test_real_bus_roads_preserve_source_access_and_reject_delivery_and_bicycle`
exceeded 30 seconds during the bicycle rejection process.
Its lane-permission assertions and legal bus run had passed.
The [parallel PR run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39236978819)
passed all 3,472 tests, with 24 skips. Both results remain part of the evidence.

The failed case used a launcher and captured pipes. The traceback does not
establish whether the native process or an inherited pipe caused this timeout.
The retained CI artifact contains the exact test inputs and generated network.
The old test did not write a separate native error log.

Commit `dca05f4` checks the same real OSM roads through running native SUMO.
It requires a specific departure-permission exception for delivery vehicles and
bicycles on both selected roads. A rejected vehicle must not exist afterward.
The two legal buses must complete their routes. SUMO must exit normally.
The test keeps a 30-second watchdog and writes a native log before cleanup.
It adds no skip or automatic retry of a failed test.
It does not test fatal XML-error shutdown.

## Source OD sampling

Commit `4908e1c` removes all-pair shortest-path searches from diagnostic request
preparation. It computes strong components and reachable destination bitsets.
It retains the original endpoint policy, uniform origin-then-destination rule,
seed, request order, and diagnostic counts. Native routing still checks every
selected request. A failed request is never replaced.

The frozen MoST source has hash
`8acab7d4767bdda9d1523da0a398735b495a66cb853cb9724654b5dbbfbbac6b`.
With seed 104 and 100 vehicles, the original implementation took 634.612 seconds.
The replacement took 3.018 seconds, including endpoint-policy preparation.
Both used the repository's Python modules and native SUMO 1.27.1 environment.
Their trip bytes and all report fields except the algorithm description match.
Both trip files have SHA-256
`63440c4fe6189c81b726106e068e123f47d5eebbe58c3b4150dc349f9bff7461`.
The source file did not change. This is one measured model and runtime comparison.

Independent review then identified an existing semantic error in v1.
sumolib uses compiled connection directives but does not trace internal lane
permissions. On contradictory edited networks, these rules differ from native
SUMO. Two native differential cases reproduced both directions of this error.

Commit `6cd640c` changes the graph to the shared loaded-turn permission reader.
Method v2 ignores dormant directives and traces complete internal lane chains.
The native router agrees with both corrected cases. The version changes because
requests can differ on contradictory networks. Frozen v1 records remain intact.
With each original seed and endpoint policy, the stored MoST and Hamburg source
requests remain byte-identical under v2. MoST preparation took 2.770 seconds.
Hamburg preparation took 0.098 seconds. All unchanged report values also match.
An earlier comparison script used Hamburg seed 104 instead of its recorded 17.
That comparison failed and remains stored. The repeat uses each recorded seed.

Fifteen native-generated cases compare exact requests and all-pair counts with
the original implementation. A separate work-count test checks that request
preparation does not search every possible pair.

## Python module origin

A global `PYTHONPATH` selected system SUMO 1.26 Python modules in earlier local
runs, although installed distribution metadata reported 1.27.1. Native programs
were 1.27.1. Those mixed-runtime records remain diagnostic evidence.
They are not locked-runtime replications. CI did not inherit this local setting.

Commit `590f282` records the Python executable and actual `sumolib` and `traci`
lookup paths. These fields add no acceptance condition. The install guide shows
how to remove `PYTHONPATH` in a dedicated process. No Windows setting changed.

## Local checks and records

All 146 related tests passed in 100.29 seconds. Ruff and `git diff --check` passed.
The checks cover OSM access, official approaches, sampling, routeability,
environment reports, runtime entry points, and install documentation.

After the loaded-permission correction, all 171 related tests passed in 231.35 seconds.
Ruff passed. Normalization and plain export now surface unavailable permission
tracing in top-level warnings. Native repair can continue without an added gate.
The documentation requires fresh prepared-output paths and explains the trace gap.

## Clean-runtime Hamburg repeat

The complete reconstruction used native programs and Python modules from SUMO 1.27.1.
It took 289.194 seconds and materialized all 16 official motor movements.
There were no missing movements or runtime fallbacks. Loaded-link comparison
passed at three native and 125 source junctions, with no differences.
The final network hash matches the prior candidate. Inputs and producer code stayed unchanged.

The result remains `review_required`. Its candidate has 45 surface findings,
compared with 51 in the source. Neither count estimates a false-alarm rate.
The four existing diagnostic edit proposals all applied their requested effect.
Their technical construction checks passed. Each had zero outside-fact changes.
All four source driving baselines remained incomplete, so no paired performance
average or traffic improvement follows from these checks.

The first preselected MoST contour edit completed measurement. Its automatic
applicability assessment was interrupted before the loaded-sampler correction.
No completed automatic-profile case is claimed from that interrupted run.
The candidate and all prior records remain intact. The same candidate has a
separate instrumented assessment record. No failed case was replaced.

Repeated stack samples identified a second preparation cost: canonicalization
scanned all roads again for each junction. Commit `eb3ba2d` builds the incident-road
index once. It preserves road roles and their ordering, including self-loops.
All 52 related canonicalization, conflict, and applicability checks passed.

The indexed diagnostic process exited with Windows status `0xC0000005` at its
first scheduled traceback dump. Its trace is partial. The cause remains unknown.
This process did not produce an applicability result. A separate repeat uses
the same immutable candidate without the diagnostic tools.

That plain repeat completed normally in 206.928 seconds. It passed 12 checks
and failed two pedestrian-preservation checks. The six changed local facilities
retain their IDs, but their geometry changed. One sidewalk lane moved by
2.043 m in XY Hausdorff distance and gained 1.01 m of declared length.
This is not a numeric-format difference. The candidate therefore remains
`blocked` for automatic applicability. Its exploratory measurements remain available.
The original process crash did not recur in this control. Its cause remains unconfirmed.

- [CI artifacts and native rejection probe](../../outputs/ci_acaf_failure_20261019_v1/).
- [Related test log](../../outputs/ci_acaf_failure_20261019_v1/focused-v2.log).
- [Frozen original sampler](../../outputs/source_od_performance_20261019_v1/frozen-v1-benchmark.py).
- [Replacement sampler measurement](../../outputs/source_od_performance_20261019_v1/optimized-benchmark.py).
- [Sampler comparison result](../../outputs/source_od_performance_20261019_v1/optimized-results.json).
- [Native sampler parity repeat](../../outputs/ci_acaf_failure_20261019_v1/native-sampler-v2-repeat/results.json).
- [Clean-runtime reconstruction and edit runs](../../outputs/clean_runtime_maintenance_20261019_v1/).
- [Same-candidate applicability assessment](../../outputs/ci_acaf_failure_20261019_v1/most-profile-resume/).
- [Plain same-candidate repeat](../../outputs/ci_acaf_failure_20261019_v1/most-profile-indexed-plain/).
- [Pedestrian geometry diagnosis](../../outputs/ci_acaf_failure_20261019_v1/pedestrian-changes.json).

The source and reference models do not establish field correctness or demand.
The two complete Windows suites at `590f282` each passed 3,489 tests, with 24 skips:
[PR run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39243883292) and
[push run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39243878248).
These runs precede the loaded-sampler correction. Later CI results are recorded separately.

## Final CI

Both full Windows suites at `eb3ba2d` passed **3,494 tests, with 24 skips each**:
[PR run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39247080048) and
[push run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39247072321).
These results include the v2 sampler, visible trace warnings, and incident-road index.
The earlier `6cd640c` suites were superseded by this follow-up. Their partial output is not full-suite evidence.
The [machine-readable record](maintenance-20261019-v2.json) binds the implementation and local artifacts.
