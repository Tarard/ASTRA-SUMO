# Lane-count edits: retained turns and signal programs

## Finding and repair

Source-value controls exposed a lane-count construction defect. The original
Hamburg controller `LSA2349_part0` has 12 phases. Rebuilding the same four-lane
road through `set_lane_count` replaced it with a generated six-phase program.
The five-lane candidate also used that generated program.

The unchanged-count control changed 21 trip durations, with a mean difference
of -24.89 seconds. This nearly reproduced the earlier five-lane difference of
-24.90 seconds. Those runs used different timing programs, so their differences
cannot establish the effect of adding a lane. Earlier records remain saved.

Commit `a06076a` repairs two losses in lane-count construction:

- Surviving connections retain custom paths, driving lengths, speeds, signal
  bindings, and supported driving constraints through lane renumbering.
- Affected source signal programs are supplied explicitly to netconvert.
  Rebuilding incident connections must not silently replace their timing.

An explicit connection list inherits attributes for matching surviving pairs.
Explicit permission directives replace the inherited permission directives.
This avoids retaining an old `allow` when the request supplies `disallow`.

## Fixed-input diagnosis

All stages use the same source network, saved deterministic 150-second cohort,
seed 17, and 2400-second horizon. No vehicles, routes, departures, or thresholds
were changed between stages. Each selected run completed all 100 vehicles
without collisions or teleports.

| Construction stage | Saved custom turns | Phases | Same-count mean difference | Five-lane mean difference |
|---|---:|---:|---:|---:|
| Original implementation | 0 | 6 | -24.89 s | -24.90 s |
| Retain connection attributes | 2 | 6 | -24.90 s | -24.88 s |
| Also retain signal programs | 2 | 12 | 0.00 s | 0.00 s |

Differences are candidate duration minus source duration. In the final stage,
every recorded trip duration matches the source. The complete saved signal
program also matches the source. The retained-connection repair alone did not
remove the driving difference. The signal-program repair did so in this fixed
diagnosis. This result establishes neither field benefit nor general traffic
equivalence.

The other original source-value controls had zero trip-duration differences:
road shape, stop line, and speed. All eight original control/stress pairs were
valid runs. A valid run alone did not identify the signal-program defect.

## Calibration and verification

The final same-count and five-lane candidates were measured against the same
2349 and 2394 fact tables. The lane-index mappings remain the identity mappings.
At 2349, six facts change in the same-count control and seven in the five-lane
candidate. None change outside the declared scope. All 74 facts at 2394 retain
their measurements. Explore status remains `measured`.

The tables check signal-group bindings, but contain no full signal-program fact.
Earlier unchanged outside-fact results therefore did not establish timing
preservation. The new program checks use separate source and native evidence.

Local checks include:

- 109 related tests at the intermediate connection-attribute stage.
- 14 lane-count tests after the signal-program repair.
- Native Hamburg checks for three, four, and five lanes. All preserve the
  original 12-phase program and the surviving custom turns.
- Ruff and `git diff --check`.

These test sets overlap. An initial XML byte comparison failed on indentation;
the corrected assertion compares program elements, attributes, and meaningful
text. The failed assertion remains in the logs.

Both full Windows suites for the preceding production commit `f7b45f4` passed.
The [PR suite](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39312296573)
reported **3565 passed and 24 skipped** in 753.57 seconds. The
[push suite](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39312288192)
also succeeded. Complete CI for `a06076a` was still running when recorded.

## Reproduction artifacts

The [frozen summary](lane-count-preservation-20261019-v1.json) records source,
network, scorecard, and implementation hashes. The intermediate implementation
files were reconstructed afterward and verified against hashes recorded before
their runs. Their origin is recorded explicitly.

Paths below are relative to the repository:

- Initial controls: `outputs/run_driving_identity_20261019_v1.py` and
  `outputs/driving_identity_20261019_v1/`.
- Connection-only repair: `outputs/run_driving_identity_20261019_v2.py` and
  `outputs/driving_identity_20261019_v2/`.
- Final repair: `outputs/run_driving_identity_20261019_v3.py` and
  `outputs/driving_identity_20261019_v3/`.
- Configurations and input identities: `declaration.json` in each result folder.
- Vehicle comparisons: `driving/comparison.json` in each result folder.
- Reusable driving measurements: `plugins/astra-sumo/src/astra_sumo/core/paired_driving.py`.
- Calibration and summary runners: `freeze-evidence.py` and `compact-evidence.py`
  in the final result folder. Full raw scorecards remain there.
- Offline regression input: `benchmarks/hamburg_loaded_links_20261018_v1/candidate.net.xml.gz`.

Stochastic-driver validation, automatic-cleaning precision, and remaining
whole-network conversion scopes remain incomplete. This repair preserves source
timing. It does not establish the source timing's field accuracy.
