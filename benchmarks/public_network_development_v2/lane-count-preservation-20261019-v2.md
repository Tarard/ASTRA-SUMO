# Shifted lane indices and independent signal checks

This follows the [original lane-count repair](lane-count-preservation-20261019-v1.md).
Commit `3439ae5` fixes shifted signal bindings and adds an independent source
program comparison to scoped checks.

## Two verified gaps

Claude found that right-side lane additions and lower-index removals still
failed. Local native Hamburg regressions reproduced both failures. Each
candidate contained three controlled connections with `tl="dummy"`. SUMO
rejected them because that controller does not exist.

The repair writes the remapped connections beside their `tlLogic` definitions
in the plain signal file. Existing custom-path endpoint repair remains active.
The retained custom curves need not be discarded.

Separate fault injection changed a signal phase duration during a lane-width
edit. Before the comparison repair, release materialization still returned
`pass`. The new comparison detects this unrequested change.

It checks source controller type, offset, phase timing, parameters, and bound
signal states through the existing numeric program comparison. Declared lane
mappings and removed connections define which movements remain. Explicit
signal operations retain their dedicated checks.

Explore keeps physically valid measurements and reports a warning. The same
unexpected program change fails release checks. Missing or unreadable signal
programs cannot satisfy preservation.

## Final committed-code Hamburg repeat

The repeat used clean commit `3439ae5`, the exact previous source and route
bytes, seed 17, deterministic drivers, and the 2400-second horizon. The code
snapshot was saved before execution. Inputs and implementation stayed unchanged.

| Candidate | Lanes | Signal comparison | Arrivals | Maximum absolute trip-duration difference |
|---|---:|---|---:|---:|
| Source-value control | 4 | pass, 13 retained connections | 100/100 | 0 s |
| Default left addition | 5 | pass, 13 retained connections | 100/100 | 0 s |
| Right addition | 5 | pass, 13 retained connections | 100/100 | 1 s |
| Remove index 0 | 3 | pass, 13 retained connections | 100/100 | 0 s |

Every candidate retains the original 12 phases and two custom turns. None has a
dummy binding. All have zero collisions and teleports. The right-addition case
changes two durations by opposite one-second amounts. No equivalence margin
or traffic-benefit claim follows from their zero mean difference.

The 2349 fact table was evaluated through the declared lane selector remapping.
Each case has zero changed outside-scope facts. Technical release checks pass
for these four constructions. This does not grant promotion authority to their
explore results.

The driving comparison also retains the two earlier six-phase networks. All
six source/candidate pairs are valid executions. Their old timing-related
differences remain recorded.

## Verification and artifacts

Twenty lane-count checks passed after remapped bindings were added. The later
signal-comparison, typed-operation and explore suite passed 97 tests. After
explicit removed-connection accounting was finalized, all 14 signal tests
passed, including native explore/release fault injection. These sets overlap.
Ruff and `git diff --check` passed.

Both complete Windows suites for preceding commit `a06076a` passed. The
[PR suite](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39314961741) reported
**3576 passed, 24 skipped** in 667.01 seconds. The
[push suite](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39314951187)
also succeeded. Complete CI for `3439ae5` was still running when recorded.

The [frozen summary](lane-count-preservation-20261019-v2.json) records hashes,
failed dummy-binding candidates, remapped selectors, and final measurements.
Paths below are relative to the repository:

- Final runner: `outputs/run_driving_identity_20261019_v5.py`.
- Reusable measurements: `plugins/astra-sumo/src/astra_sumo/core/paired_driving.py` and `network_calibration.py` in the same directory.
- Configuration and identities: `outputs/driving_identity_20261019_v5/declaration.json`.
- Raw construction reports, scorecards and vehicle comparisons: `outputs/driving_identity_20261019_v5/`.
- Retained failing candidates: `outputs/pytest-failures/80284f1a85ad4e599f6e3e4a00aacf96/`.
- Final signal tests: `outputs/driving_identity_20261019_v4/final-signal-comparison-tests.log`.
- Summary runner: `outputs/freeze_lane_count_followup_20261019_v2.py`.

Original burst-demand failures, stochastic-driver validation, cleaning precision,
and remaining whole-network conversion scopes remain open. Source timing
preservation does not establish its field accuracy.

## Later open modal-layout finding

Claude's subsequent review identified a retained-sidewalk problem. An offline
check of the saved final networks confirms it. The source has one pedestrian
lane, one bicycle lane, and two passenger lanes. A raw right-side addition
copies the outer sidewalk. The candidate has two pedestrian lanes and still
only two passenger lanes. The retained sidewalk moves from index 0 to index 1,
but has no outgoing walking-area connection.

The current contract counts all SUMO lanes and treats `remove_index` as a raw
physical index. Removing index 0 therefore explicitly removes this source
sidewalk. Traffic-aware selection is not available in that interface yet.
The source-program and vehicle checks above do not validate retained walking
access. These mixed-mode constructions remain diagnostic work pending repair.

The offline record is `outputs/driving_identity_20261019_v5/lane-mode-followup.json`.
The original candidates and measurements remain unchanged.
