# Native runtime repair and fixed Hamburg diagnostic repeat

## Runtime repair

Commit `32118df` replaces captured pipes with temporary output files in the
shared command runner. A fatal SUMO line starts a one-second exit grace period.
The runner then stops its owned process tree if needed. Silent deadline expiry
remains `timeout`. The existing command-result fields stay unchanged.

The pre-fix native and Windows launcher baselines exited normally locally.
The controlled hanging-process test failed before the repair. Twenty real
Windows fatal-exit cases pass after it. These observations do not explain the
historical native shutdown fault.

Claude's review found three remaining direct native launches. Commit `18b400b`
routes teacher replay, intersection load, and intersection compilation through
the same runner. Their existing result fields retain native and timeout errors.
The shared polling loop now waits for process exit instead of sleeping through
an early exit. No new dependency or command-result field was added.

Both complete Windows CI suites for `32118df` passed **3550 tests with 24 skips**:
[PR](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39308886188) and
[push](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39308879200).
For `18b400b`, 114 focused tests and 353 workflow tests passed locally.
These sets overlap. Six real native consumer cases also passed.
The complete CI suites for that later commit were still running when recorded.

An initial native probe used `<net/>`. SUMO exited with Windows code
3221225477 and no captured text. Its input and failed assertion remain saved.
The subsequent six-case check uses a compiled network with an invalid numeric
lane attribute for its load-error cases. It confirms that the adapters preserve
native errors. It makes no claim to repair the SUMO engine crash.

A later diagnostic follow-up adds the observed nonzero return code to `error`,
including failures without output. Three focused assertions failed before this
change. All 111 related runner, consumer, intersection, and documentation tests
passed afterward. The logs are `empty-failure-before-fix.log` and
`empty-failure-after-fix.log` under `outputs/native_command_failure_20261019_v1/`.
This reports the observed code without assigning a native crash cause.

## Unchanged Hamburg reconstruction and edits

Under `32118df`, the complete reconstruction took 232.538 seconds. It produced
all 16 official motor movements without runtime fallbacks. Loaded-link checks
passed at three native and 125 source junctions. The candidate hash stayed
`445917878720723963506a087803ab5d69bd0805d91cbe936022ffea6f09264f`.
Source inputs, request bytes, and implementation stayed unchanged during the run.

All 21 motor centerlines received lateral measurements. Median offsets were
0.557126 m at 2349 and 0.808115 m at 2394. Fifteen associations have high
diagnostic confidence. The other six retain medium or conflicting associations.
Coverage and lateral offset remain separate measurements.

All four fixed stress edits applied their requested effect, with zero changed
outside facts. Their original burst-demand source runs still ended with
62 arrivals, 37 running vehicles, and one waiting vehicle. Those runs remain
incomplete and provide no valid paired performance average.

## Existing 150-second diagnostic transferred to the current networks

Under `18b400b`, the runner reused the exact saved route file from the earlier
150-second departure diagnostic. Its hash is
`d0e8393fd96f5ec284dbdea07aee0d502be3c20103684273b075d34685ed2dc4`.
The driver has `sigma=0` and `speedDev=0`. Seed 17, horizon 2400 seconds,
collision handling, and every vehicle and departure remain unchanged.
The new request and implementation hashes were recorded before execution.

The current source and all four candidates each inserted and completed all
100 vehicles. None recorded collisions, teleports, or discarded vehicles.
All four pairs are valid. Inputs and implementation remained unchanged.

| Candidate | Mean duration difference, candidate minus source | Vehicles with changed duration | Maximum absolute difference |
|---|---:|---:|---:|
| Road shape | -0.03 s | 3 | 1 s |
| Lane count | -24.90 s | 21 | 221 s |
| Stop line | +0.01 s | 1 | 1 s |
| Speed | 0.00 s | 0 | 0 s |

These are descriptive results for one fixed synthetic cohort. No equivalence
margin was selected. They do not establish traffic benefit, exposure to every
changed lane, field demand, or agreement across seeds. They do not replace the
incomplete burst-demand results.

### Frozen seed-set extension

A [separate receipt](native-driving-seeds-20261019-v1.json) extends that exact
deterministic cohort to seeds 17, 42, and 104. These are the complete seed set
already fixed in the checked-in 2026-10-16 Hamburg diagnosis fixture. The seed
source, request, and implementation were recorded before execution.

All 15 network runs completed 100/100 vehicles without collisions, teleports,
or discards. All 12 source/candidate pairs are valid. Trip records are identical
across these seeds for each network. This deterministic profile therefore adds
no observed driver variation across the three seeds. Stochastic-driver behavior
and the original burst-demand failures remain open.

The extension runner is `outputs/native_command_failure_20261019_v1/repeat-declared-driving-seeds-v1.py`.
Its configuration is `outputs/native_command_failure_20261019_v1/declared-driving-seeds-v1-identity.json`.
Raw results and vehicle-level comparisons are under
`outputs/native_command_failure_20261019_v1/declared-driving-seeds-v1/`.

An offline check of completed native vehicle routes found 14 vehicles traversing
the modified edge in each source and candidate run. Exact lane exposure remains
unknown because these route records identify edges. Nine of the 21 changed
lane-count trip durations belong to those 14 vehicles. Twelve belong to other
routes. No same-materialization identity driving control has been measured, so
these differences cannot yet be attributed entirely to the requested edit.
The analysis runner is `outputs/native_command_failure_20261019_v1/check-driving-exposure.py`.
Its saved input hashes and results are in the adjacent `declared-driving-exposure.json`.

## Reproduction and remaining work

The [frozen summary](native-runtime-20261019-v1.json) binds the raw records and
test logs to their hashes. Generated files are under the local result directory.
Paths below are relative to the repository.

- Runner: `outputs/native_command_failure_20261019_v1/repeat-declared-driving.py`.
- Reusable measurement code: `plugins/astra-sumo/src/astra_sumo/core/paired_driving.py`.
- Configuration and input identities: `outputs/native_command_failure_20261019_v1/declared-driving-repeat-identity.json` and `declared-driving-repeat/plan.json` in that directory.
- Raw runs and vehicle comparisons: `outputs/native_command_failure_20261019_v1/declared-driving-repeat/`.
- Native consumer runner: `outputs/native_command_failure_20261019_v1/check-native-consumers-v3.py`.
- Native consumer results: `outputs/native_command_failure_20261019_v1/native-consumers-v3/results.json`.
- Full reconstruction runner and results: `outputs/native_command_failure_20261019_v1/hamburg/run.py` and the adjacent result files.

Multiple driving seeds, automatic-cleaning precision, unsupported local
conversion scopes, and automatic acceptance of changed pedestrian layouts
remain incomplete. Published-model agreement is not a field-defect label.
