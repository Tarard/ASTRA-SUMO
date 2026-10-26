# Ring1 Calibrate parent: fragility check, results (pre-M4 step)

Written 2026-10-26, after the F2 runs and their analysis. It reports the check fixed by the
[pre-registration](ring1-fragility-prereg-2026-10-26.md) (sha256 `7fe443eb...2d64`) and the
[owner-delegated decisions](ring1-fragility-owner-decisions-2026-10-26.md) (sha256 `3ac167db...a93b`), both written
before any candidate run. Neither document is changed by this report; section 6 lists the corrections the reviewers
made to them.

Explore evidence only: simulated draws under the supplied SUMO model. There is no field claim, no traffic-safety
claim, no gridlock-rate or collision-rate claim and no promotion authority. Section 9 defines the terms. The rules of
the twin-profiler design v2 and of the M2 v3 decisions that this check relies on are not in the repository; the
[M4 plan](twin-profiler-m4-plan-2026-10-26.md) quotes them in its section 12, with a short summary of the M2 stages.

## Context: the twin-profiler effort to date

The ASTRA Twin Profiler applies a kernel-style test-and-profile discipline (one frozen goal, a pass/fail test, one
benchmark number and a profiler that locates the error) to Build and Calibrate work on the Hamburg Ring1 Calibrate
parent, in milestones M1 to M5. **M1** (`twin/m1-offline-profiler`, `6ccaac5`) is an offline Calibrate profiler that
runs no SUMO; it reproduces the tracked parent score (TAE 275 over the 130 main rows, 83/130 exact) and the supply bound
B1 = 51. Its round-2 independent review kept `17ca206`, and `6ccaac5` adds that review's one minor fix. **M2**
(`twin/m2-gate-calibration`, `503e119`) attempted to calibrate the Calibrate gate on the demand-timing family in 106
judge runs and stopped at C3. C1 kept the rolling-hour objective ms4 (excess 105.5, jackknife
z 2.43 on 8 fresh parent draws) but found a parent collision, which turned the health rule into a comparison with the
reference arm. C2 qualified one positive (`cell_depart_shift:107:900`). C3 (48 runs on secret keys committed before the
runs) failed its frozen gate: the positive was `unhealthy` at H2, and its primary test gave T = -7.745, p = 0.352 at an
approximate power of 0.47, so C4 was not run. C3 also showed the parent itself colliding and gridlocking. **M3**
(`twin/m3-build-profiler`, `ae21563`) is the Build profiler (G0 invariants, violation vector V, L2 all-turn smoke, L3
frozen-trip routeability) with known-answer harnesses K1 (46 cases) and K2 (8 cases) that pass all their checks; its
round-4 independent review kept it. The design's agent-free loops (M4) need M2's exit criterion, which C3 did not meet,
so the branch `twin/m4-fragility` (M2 and M3 merge `3ded083`, harness `fbb2fb4`, pre-registration `0276200`) first
localizes the parent's own failures and tests M3 Build fixes for them, as a step before M4. This report is that
result. The pre-registration's title says "(M4)" only because the check ran on that branch; the
[M4 plan](twin-profiler-m4-plan-2026-10-26.md) numbers M4's parts M4.0 to M4.3, and this check is not one of them.

## 1. Sources

Local stores, not in the repository: `E:\twin-store\m2-pilot` (the M2 runs, read only) and `E:\twin-store\fragility`
(this check). "Archive" is the session archive, the local working directory of the session that ran M1 to M3 and this
check; it is not in the repository and is not needed to read this report.

| item | file | sha256 |
|---|---|---|
| twin-profiler design v2, with milestones M1-M5 | archive `design_v2.md` | `a73d563b...9357` |
| M2 decisions and amendments A1, A2 | archive `m2-decisions.md` | `48106d95...e942` |
| M2 stage results | archive `m2-c1-result.json`, `m2-c2-result.json`, `m2-c3-result.json` | `4718d74c...171d`, `60bf6ea5...11c9`, `ce8b8779...a1fa` |
| M2 run ledger (106 runs) | `E:\twin-store\m2-pilot\runs.jsonl` | `4a55da59...00fe` |
| M3 known-answer reports K1, K2 | `E:\twin-store\m3-accept4\k1\k1-report.json`, `...\k2\k2-report.json` | `c10f4ec5...73f9`, `6edb171e...73fc` |
| site census | archive `fragility\sites.md`, `fragility\sites.json` | `6fb0a50c...7724`, `d6cf68cc...ae68` |
| site diagnosis and cut-out probes | archive `fragility\diagnosis.md` | `5590356c...e6f5` |
| static gate, round 2 | `E:\twin-store\fragility\gate\static-gate.json` | `74e2563f...0ecf` |
| design with the frozen analysis block | `E:\twin-store\fragility\prereg\design.json` | `4ac5eff8...769f` |
| F2 run ledger (16 runs) | `E:\twin-store\fragility\runs.jsonl` | `8dc0cfb9...d367` |
| F2 analysis (`astra_twin fragility-analyze`) | `E:\twin-store\fragility\analysis\fragility-analysis-F2-2026-10-26.json` | `f9fd49e7...bbb1` |
| F2 result note and data | archive `fragility\F2-result.md`, `fragility\F2-result.json` | `c7e61fe8...f888`, `a31d7709...136b` |
| independent recount | archive `fragility\recheck\s01-result.json`, `crps-result.json`, `sites-result.json` | `989793b3...3f5f`, `7d21e6bd...88f1`, `6e2dd7bb...6da8` |
| independent read-only reviews: fragility harness; M1 round 2; M3 round 4; M2 C2; M2 v3 | archive, named in the note below | `09e6c9be...530b`, `60c8392e...f668`, `0556b842...2b4d`, `e13d41c8...51e7`, `36e8acf9...584c` |

**Archive note.** The review records are, in the order of the last row, `codex_review_frag.json`,
`codex_review_m1_r2.json`, `codex_review_m3_r4.json`, `codex_review_m2c2.json` and `codex_review_m2v3.json`.

## 2. What was measured: the parent's fragility sites

The census read the native outputs of all 106 M2 runs; it ran no SUMO. 7 of the 106 runs are unhealthy: 5 collision
runs at S02 and 2 gridlock runs at S01. The runs use `--time-to-teleport -1`, so a jam becomes a standstill instead of a
jam teleport, and in those 2 runs a ring-wide gridlock. There are 1046 emergency brakings (EB), at least one in every
run.

| site | location | class | events in the 106 runs |
|---|---|---|---|
| S01 | Ring1 signal chain 1966837175 - 244203534 - 266197536 (edges 141050980#1 4.94 m, 603103444 0.158 m) | gridlock | 2 gridlocks, both rooted on 603103444_1; a wrong-lane standstill of 60 s or more on 603103444_1 in 42 of the 104 runs without a gridlock; 247 EB in 93 runs |
| S02 | 0535 approach 13870881 - 315029332 - 1975693623 | collision | 5 side collisions, each with a collision teleport, in 5 runs, all one signature; 631 EB in all 106 runs |
| S03 | LSA535_part0 (static program `readme_fixed_A`) | emergency braking | 165 EB in 83 runs, 163 of them on `:LSA535_part0_7_0`; link 5 never green |
| S04 | entry edges 189764301 and 61578529#1 | emergency braking | 3 EB in 3 runs, at insertion |

- **S01 (gridlock root).** At 266197536, lanes 0 and 1 of 603103444 connect only right to 381750336, which none of the
  8589 demand vehicles uses. All 2582 vehicles on 603103444 continue straight to 73474895, and 1964 of them (76 %;
  field 0119Z15 1205, 0119Z4 759) arrive on lanes 0/1. They must move one or two lanes left within about 55 m
  (22.5 + 27.3 + 4.9 m), past two signals that control no crossing movement. A vehicle that misses the weave stops on
  603103444_1 with no connection to its next edge, and its lane-2 follower yields to it. With teleport off, that
  two-vehicle circular wait spreads back until every running vehicle has stopped (395 and 396 stopped vehicles in the
  two gridlock runs). Every one of the 43 resolved wrong-lane standstills of 60 s or more on 603103444_1 in the runs
  without a gridlock is a vehicle bound for 73474895 and assigned to 0119Z4 (25) or 0119Z15 (18). In a cut-out probe
  with synthetic demand, adding 603103444_1 -> 73474895_0 turned the wrong-lane standstill (up to 75.4 s) into merge
  yields of at most 46.1 s; neither probe deadlocked within 700 s.
- **S02 (collision at the lane fork).** -186821026_3 forks inside junction 315029332 into two internal lanes
  (`:315029332_0_2` -> -186821028_3, `:315029332_0_3` -> -186821028_4) that start at the same point; their centerlines
  stay less than 1.8 m apart for 3.95 m. The queue spills back over the 8.58 m edge -186821028, so a stopped car's rear
  stands in the overlap. All 5 collisions are the same vehicle pair at t = 8481.1 s on `:315029332_0_2`, in stage
  `laneChange`. The lane-change trigger is inferred from the records; three probes did not reproduce it. The 631 EB are
  insertions at `departSpeed=max` 26.9 m before the 315029332 stop line.
- **S03 (LSA535 never-green link 5).** Link 5 (186821027#0_4 -> 186821036#1_1) is never green in the executed program,
  yet it gives lane 4 a valid left-turn path. In a cut-out probe, 3 left-turners changed to lane 4 and waited there for
  378 s or more, until the end. In the M2 runs the lane-section hold keeps 0535Z8 vehicles on lane 5, so the trap never
  occurs (0 of the 46 parent draws used later as the reference arm). Of the 163 EB on the first part of link 7, 138 fall
  at cycle second 6.0, the red onset; that trigger is unresolved.
- **S04 (minor).** 3 EB on the first route edge 0.3-3.9 s after departure.
- **Cross-cutting.** The parent carries 25 network-wide connection-mode structural failures (path endpoint gaps), so
  every Build goal on it is `source_dirty` and promotion needs a human. netconvert renumbers the link indices of an
  edited controller (266197536: 6 of 7 retained links; LSA535_part0: 22 of 27), so the executed control must be
  re-indexed for every candidate.

## 3. Candidates and the static gate

Each candidate is an M3 Build goal on the parent net with declared fragility-fix targets; V = 0 holds by construction
for those targets, so an improved V shows only that the materialization did what the declared op implies. V is written
as (n_TOP, n_CTL, n_GEO, n_ATTR, E) (section 9): (1,22,0,0,0) is 1 topology and 22 control mismatches.

| candidate | site | typed op | V source -> candidate | G0 | L2 | L3 (10 frozen-trip seeds) | screening |
|---|---|---|---|---|---|---|---|
| F1 | S03 | `delete_lane_connection` 186821027#0_4 -> 186821036#1_1 at LSA535_part0 (link 5) | (1,22,0,0,0) -> 0 | **fail**: INV-AUDIT, 10 witnesses | pass | `subset_holds` on 10/10 | `g0_rejected` |
| F2 | S01 | `add_lane_connection` 603103444_1 -> 73474895_0 at 266197536 | (1,7,0,0,0) -> 0 | pass (14/14) | pass | **fail** on seeds 7 and 97 | `dynamic_hard` |
| F3 | S03 + S01 | F1 then F2, verified-parent chain | step 1: (2,29,0,0,0) -> (1,7,0,0,0) | step 1 **fail** (the same 10) | step 1 pass | step 1 `subset_holds` | chain stops at step 1 |
| S02 | S02 | none | | | | | `op_not_expressible` |
| S04 | S04 | none | | | | | deferred |

All three goals have `source_dirty = true` (25 inherited structural failures); promotion is blocked without a human.

- **F1.** The 10 INV-AUDIT witnesses are 4 new `path_width_transition_centerline_offset` findings at the 43305966#1
  exits, 5 parent findings renamed by the renumbering, and 1 parent finding kind with a new hash identity. The
  materialization also changes retained connections at LSA535_part0 (27 retained, op pair excluded):
  - five paths move by more than 0.5 m (Hausdorff distance 2.50 to 3.88 m): the straight paths to 43305966#1 from
    186821027#0_1 (two), 186821034#0_1 (two) and 186821035#0_3;
  - three internal-junction wait points move, by 1.00, 1.97 and 3.47 m: the right turns 186821027#0_3 -> 142944337#1_1
    and 186821027#0_2 -> 142944337#1_0, and 186821035#0_3 -> 43305966#1_1, which is also one of the five paths;
  - eleven paths exceed the 1 mm shape tolerance (0.002 to 3.884 m);
  - path lengths change by more than 0.1 m on 5 links (-2.50 to +4.93 m), internal-lane speeds by more than 0.01 m/s
    on 2 links, and 24 of 27 internal-lane chains are renamed; right-of-way is unchanged apart from removed link 5.

  No dynamic result could be attributed to the removal of link 5 alone. The G0 rejection stands.
- **F2.** The model-justified merge `603103444|73474895|0:1,2` (site S01, "no field evidence") is taken out of
  INV-AUDIT by a twin-layer exemption; the core release audit does not accept it. L2 passes (at 266197536, 1 failing
  smoke movement on the parent and 0 on the candidate). L3 fails on seeds 7 and 97: on each, the candidate run has one
  junction collision with a collision teleport on `:260305243_0_1`, a priority fan-out about 0.6 km away, outside the
  edit scope and with unchanged geometry. Collisions carry no per-request identity, so L3 cannot pass them. Side
  effects: the three retained straight paths from 603103444_2/3/4 are 0.332 m longer, and no shape moves by more than
  1 mm.
- **F3.** Step 1 is F1, so it is G0-rejected and the chain does not form. The F2-on-F1 net was profiled only as a
  diagnostic.
- **S02** needs a compound corridor lane-count op: every single-op intermediate creates a new fork or merge at
  13870881, which G0 rejects under `max_ops_per_round 1`. **S04** was deferred.
- **Executed control.** The control re-index drops column 5 at LSA535_part0 for F1 (28 -> 27 states) and adds index 2
  at 266197536 for F2 (7 -> 8). The added link takes the declared `minor_where_yielding` column, `gyrr`, equal to
  netconvert's own program for that link. The declared link maps equal netconvert's, and every candidate loads with its
  control and the field loops with no error.

No candidate screened `improved`, so under the pre-registration every dynamic result is `diagnostic_only`. The
owner-delegated decisions ran F2 only: its precursor occurs in 22 of 46 parent draws, while F1's target (the link-5
trap) has 0 exposure and F3 has no chain.

## 4. The F2 diagnostic run

**Runs.** 16 judge runs on the public keys `fragility/F2/k1` ... `k16` (family `depart_jitter`, goal seed 104, libsumo,
SUMO 1.27.1), 3 in parallel, from 2026-10-26 11:12:22Z to 11:34:22Z, 184.7 to 235.5 s each. 16 of 16 `measured` and
healthy; no crash, no spare used, no extra run.

**Binding.** The hash manifest `PREREG.sha256` (sha256 `7b7144c6...9464`, the value in the decisions record) was
checked against all 74 bound files before the first run and after the last: 0 mismatches. `E:\astra-m4` was at HEAD
`0276200e` with a clean working tree before the first run, after the last run and after the analysis; the bound code
tree `2562999b` is the tree of `fbb2fb4f`. Before the runs, a plan probe found all 16 planned requests comparable with
all 46 reference requests, differing only in the six allowed fields. The analysis repeated that check on the real
requests (`comparable = true`, no file-binding problem).

**Reference arm.** The 46 parent draws that the frozen rule selects: P3 k1-k4, s3-parent k5-k6, C1 k7-k14, C3
reference 01-16 and C3 negative control 01-16. The analysis re-selected them, equal to the frozen list.

### 4.1 Primary test (S01, wrong lane judged by the parent's links on both arms)

| arm | draws with a wrong-lane standstill on 603103444 | sum |
|---|---|---|
| F2 | **0 of 16** | 0 |
| parent reference | 22 of 46 | 22 |

Exact one-sided Wilcoxon-Mann-Whitney, candidate lower, unpaired, midranks and conditional on ties, over all
C(62, 16) = 273342452889765 relabelings: rank sum 328.0, U = 192.0, **p = 14430/62755763 = 0.00023**. It rejects at
0.05; F2 has one primary metric, so no adjustment applies. The exact difference-of-means permutation (reported, not
decisive) gives the same p, with means 0.000 and 0.478.

An independent recount from the native outputs (archive `fragility\recheck\`; separate scripts that do not import
`astra_sumo` and use their own episode rule) gives the same 0/16 against 22/46 on the parent's links, on the run's own
links and under the covering-vehicle rule.

### 4.2 Secondary metrics (reported, never decisive)

- **S01 by the run's own links:** 0/16 against 22/46. The F2 arm's only standstill of 60 s or more on 603103444 is on
  k4, lane 603103444_2, 62.1 s, next edge 73474895: `correct_lane` on both bases.
- **Health.** F2: 0 gridlocks, 0 collisions and 0 teleports in 16 draws. Reference: 1 gridlock run (c3-reference-03,
  root on 603103444_1, site S01) and 3 collision runs with 3 teleport runs, all at S02. At `:260305243`, the L3
  collision site, there are 0 collisions, 0 teleports and 0 EB in all 16 F2 draws and in all 46 reference draws. The
  independent recount gives the same counts.
- **`rare_events`** (report only): the tail at the reference rate is 1.0 for gridlock (reference rate 1/46), collision
  and teleport (3/46 each).
- **New-site scan.** The F2 arm shows four standstill sites of 300 s or more outside S01-S04 (`new:14398547`,
  `new:1975693628`, `new:265093112`, `new:5324041442`); all four also occur in the reference arm, so no new failure
  site is found.
- **EB per run by site** (F2 / reference): S01 2.38 / 2.33, S02 6.19 / 6.24, S03 1.25 / 1.33, S04 0 / 0.02. Link-7 EB,
  the S03 metric component, has mean 1.25 against 1.30. S03 trap episodes: 0 in both arms.

### 4.3 M2 calibration guards (screen mode, frozen gate `m2-confirmation-K16`, sha256 `cd7db25e...e363a`)

| step | against the C3 negative control (16 parent draws) | against the C3 reference (16 parent draws) |
|---|---|---|
| H1 invariants | pass (public candidate keys: exchangeability `unverified`, reported) | pass (same note) |
| H2 health (A2) | pass: unhealthy 0 against 0 | **fail**: classes gridlock, not_arrived, not_inserted on c3-reference-03; unhealthy 0 against 3 |
| H3 guards | pass | pass |
| N1 182-row K-mean growth <= 8.31 | 2.375 (R:0119Z12@10800), pass | 2.875 (R:0119Z12@5400), pass |
| N2 init52 <= 13.284 | 0.000, pass | -5.625, pass |
| N3 totals13 fair CRPS <= 15.149 | -15.733, pass | -16.992, pass |
| N4 main130 fair CRPS <= 31.077 | -11.725, pass | -15.700, pass |
| I: ms4 fair CRPS difference T (g = 10) | -31.383, exact p 0.154 | -49.558, exact p 0.041 |
| stage label | `not_significant` | `unhealthy` (first failure H2) |

- **Why the label against the C3 reference is `unhealthy`.** The bound H2 rule (`m2_stage.health_step`) rejects a
  failure class that the parent panel never showed (it showed only collision and teleport), on **both** arms. The
  reference arm's draw c3-reference-03 is the parent's own S01 gridlock, with classes outside that panel. The F2 arm
  has no unhealthy draw. Because the frozen C3 reference arm contains that draw, H2 against it fails for every
  candidate, whatever the candidate's runs show; under the bound composition (both arms in conjunction) no candidate
  could reach `fragility_reduced`. The composition was fixed before any run; the pre-registration did not state this
  consequence (section 6).
- **Measurement-code identity.** H1 reports `identity consistent = false`: the F2 runs used measurement code
  `b6bd6660cfca...` and the reference runs `de7747aadc21...`, with one shared runtime identity (`3c7d6e80ca31...`).
  The pre-registration named the candidate judges' measurement code and stated that the stage only reports the
  identity mix, so it did not block, as pre-registered. A confirmation would require one identity.
- **ms4.** On both arms the F2 arm's ms4 fair CRPS is lower than the parent arm's by more than g (31.4 and 49.6). This
  is screen mode on public candidate keys, so no rate attaches, and no improvement test was pre-registered for this
  check: it is not a calibration-improvement claim. The independent recount reproduces both T values and both exact
  p values.
- **The 49.6 is driven by c3-reference-03.** In the split d_mean_tae = d_crps + d_half_gmd, the difference against the
  C3 reference is -349.31 = -49.56 + (-299.75), and against the negative control -61.31 = -31.38 + (-29.93). The C3
  reference arm's half-GMD is 435.3, against 135.5 for F2 and 165.5 for the negative control: its gridlock draw
  c3-reference-03 widens its spread and its distance to the counts. Of the summed ms4 TAE difference of -5589 over the
  16 key-ordered pairs, -4869 comes from the pair that holds c3-reference-03. The I step alone would have passed
  against the C3 reference (p 0.041, T at most -g) and not against the negative control (p 0.154); neither is a
  pre-registered test.

### 4.4 Decision label (bound `fragility.decide`)

- Not `blocked`: 16 scored draws, no extra run, comparable, both stages measured.
- `unhealthy`: the stage against the C3 reference is `unhealthy` (section 4.3), so rules 3 to 6 are not reached. For
  information only: no new failure site, S01 rejects, and the stage against the negative control detects no
  regression.
- Eligibility: the static gate screened `dynamic_hard`, not `improved`, so the label is **`diagnostic_only`**, with
  `label_if_static_gate_had_passed = unhealthy`. `calibration_no_regression_detected = false` and
  `calibration_regressed_against = [c3_reference]` both follow only from the reference arm's H2 effect.
- F1 and F3 are `blocked` ("k1: no run"), as decided. They were not run.

## 5. Reviewer verdicts

All reviews were independent read-only reviews; none ran SUMO. Their records are named in the archive note of
section 1.

| review | scope | verdict |
|---|---|---|
| fragility harness | `twin/fragility.py`, the control re-index, candidate binding in the runner, the pre-registration and decisions; written while the F2 runs were in progress | **good = true**: sound enough to interpret F2 as `diagnostic_only`; F1 stays G0-rejected, F2 `dynamic_hard`, and F3's chain stops. 23 read-only test cases passed, and all 46 reference request hashes and reconstructed requests matched. Not checked: the full test suite, the gate profile files and the F2 results. |
| M3, round 4 | the full M3 diff | **good = true**: keep `ae21563` as the M3 baseline; the round-3 finding is resolved; 65 read-only tests passed; the saved K1/K2 hashes match. |
| M2 C2, stage code and C3 freeze | `twin/m2_stage.py`, C2 qualification, the C3 plan | **good = true**: C3 can proceed as frozen. It is one conditional end-to-end demonstration, not a validation of power, false-acceptance rates or field correctness; the 0.47 is a plug-in approximation for test I alone. |
| M2 protocol v3 and decisions | the v3 protocol and its decisions | **good = false** for C2/C3 (C1 may continue): exchangeability was overextended, the code conflicted with the decisions, N1 was not enforced and C3 had no workable negative-control allocation. Amendment A1 answered it before any C1 result was read. |

The fragility review raised two corrections (section 6), two follow-ups (section 8) and one disagreement: the exact
permutation arithmetic is correct for the unequal 46/16 samples with ties, but distinct keys and matching requests do
not establish exchangeability after the historical census helped select the site and the fix. **The S01 p value is
therefore exploratory diagnostic evidence, not a selection-adjusted confirmation.** No reviewer has checked the F2
results themselves; they were checked only by the separate recount in section 4.

## 6. Corrections to the dated pre-registration and decisions

The dated documents stay as written; these corrections apply to them.

1. **F1 side effects.** The decisions record says netconvert "reshapes 7 untouched internal paths". Precisely: five
   paths move by more than 0.5 m and three wait points move, one of them on one of those five paths (seven distinct
   connections); eleven paths exceed the 1 mm shape tolerance (section 3). The G0 rejection is unchanged.
2. **Lint scope.** The pre-registration says lint refuses any declared entry the typed op does not imply. There was a
   selector-validation gap: changing F2's added signal target selector to another lane pair passes lint, the
   source-aware `implied_errors` and `apply_targets`. The frozen F2 selector is correct, so this diagnostic is not
   affected. The gap was closed after the F2 run, by a change to `twin/declared_targets.py` committed together with
   this report: lint, the source-aware check and apply now refuse a target whose selector is not its fact id's own
   lane or lane pair, with regression tests that include the re-pointed F2 case. The F2 run used the earlier code
   (bound tree `2562999b`). The frozen F2 block is unaffected: its base and applied fact hashes are unchanged, which
   a store-backed regression test checks.
3. **Exchangeability.** The pre-registration says the draws are exchangeable under the sharp null because the keys are
   distinct and the reference arm is selected by request fields. Because the census on the same historical draws
   selected the site and the fix, the p value is exploratory and not selection-adjusted (section 5).
4. **Health claim limit.** Section 6 of the pre-registration discusses only candidate gridlocks under H2. It does not
   state that H2 also tests the reference arm's classes, so the C3 reference arm's own gridlock fails H2 for every
   candidate (section 4.3). Re-reading the rule now would be outcome-informed; it is an owner decision.

## 7. What can and cannot be claimed

**Can**, under the supplied model (the Calibrate parent, its declared holds, `readme_fixed_A` re-indexed with
`minor_where_yielding`, P1 demand with the depart jitter, seed 104, libsumo 1.27.1) and as exploratory diagnostic
evidence:

- With F2's added lane-1 straight link (603103444_1 -> 73474895_0), the S01 wrong-lane standstill was absent in 16 of
  16 draws, against 22 of 46 parent draws; the exact one-sided unpaired test on the arm-neutral metric gives
  p = 14430/62755763.
- These 16 draws showed no gridlock, collision or teleport anywhere, none at `:260305243`, and no new failure site.
  EB at S01, S02 and S03 stayed at the parent's per-run level.
- The M2 guards N1-N4 and H3 held against both C3 parent arms, and H2 held against the negative control.

**Cannot:**

- **An accepted fix.** The label is `diagnostic_only`: F2 fails L3 (`dynamic_hard`). The 16 Calibrate draws use
  another demand than the routeability seeds, so their 0 collisions do not refute that finding; 0/16 bounds a per-draw
  rate only weakly (one-sided 95 % upper bound about 0.17).
- **Field correctness.** Nothing here shows that the edit is the real lane layout or signal plan. The F2 merge is a
  model-justified twin-layer exemption without field evidence, and the `minor_where_yielding` column is a declared
  resolution.
- **Traffic safety.** No safety claim of any kind.
- **A gridlock or collision rate.** 0/16 against 1/46 gridlocks is a count; the `rare_events` tail is 1.0. Under the
  parent's rate, P(at least one gridlock in 16 draws) is about 0.30.
- **A calibration improvement.** The ms4 difference is a screen-mode statistic on public keys with no rate, and it was
  not pre-registered as a test.
- **"No calibration regression detected"** as bound: it fails formally against the C3 reference because of that arm's
  own gridlock. Where the guards held, they only reject; holding is not non-inferiority.
- **Promotion or a new Calibrate parent.** The source is dirty (`source_dirty`, 25 inherited structural failures), so a
  human is required. A later parent built from a passing candidate needs its own goal, a re-indexed control and a new
  14-draw margin panel.
- **F1, F3, S02 and S04.** No dynamic result exists for them, and nothing is claimed about link-5 traps (0 of 46
  exposures).
- **Generalization** beyond this count day (2026-10-19), demand, holds and seed.

## 8. Open questions this result leaves

- **H2 against a failing reference.** When the reference arm is the parent and the parent has its own failure classes,
  H2 cannot separate a candidate effect from the reference's failure. Any revised rule would be outcome-informed here,
  so it needs a new pre-registration and fresh draws.
- **Untouched paths under junction-local re-derivation.** F1 shows netconvert reshaping retained paths at the edited
  junction. The reviewer's point is that such paths should be preserved or their changes declared, and that findings
  should be compared by stable movement identity so that renumbering does not create false new findings, while genuine
  geometry regressions keep blocking.
- **L3 and stochastic collisions.** L3 treats a deterministic route failure and a collision outcome alike. The
  reviewer's point is that collision identities and locations should be recorded and parent-against-parent variability
  measured, with any revised dynamic rule pre-registered on fresh draws; F2's L3 rejection stays. Unchanged remote
  geometry does not prove that an edit cannot change collision exposure.
- **Expressibility.** S02 needs a compound corridor lane-count op. Removing or coordinating the two S01 signals that
  control no crossing movement needs a controller op that Build does not have. The lane-0 case at S01 (a second
  `add_lane_connection`, which validates) was not tried.
- **Selector validation** (closed after the F2 run). Lint now ties a target's selector to its fact id (section 6,
  item 2).

## 9. Terms

- **arm, draw, K**: an arm is a set of judged Calibrate runs of one net; a draw is one run under a keyed perturbation
  of the demand (`depart_jitter`, plus or minus 0.5 s); K is the number of draws per arm.
- **B1**: M1's request-total floor: while the per-field request totals stay as they are, the TAE over all 182 rows
  cannot fall below the sum of the per-field request deficits, 51 vehicles on the parent.
- **C1 to C4**: the M2 stages: C1 a fresh parent panel, C2 the qualification of positives, C3 a confirmation on secret
  keys, and C4 (not run) the full known-answer calibration.
- **EB**: an emergency braking reported by SUMO.
- **excess, jackknife z**: the parent draws' mean distance to the real counts minus their mean distance to each other,
  and that excess over its leave-one-draw-out jackknife SE.
- **fair CRPS, half-GMD, d_mean_tae, d_half_gmd, d_crps**: the fair CRPS of an ensemble is its mean distance to the
  target (mean TAE) minus half its mean pairwise distance (half the Gini mean difference, half-GMD), summed over the
  objective's values; lower is better. A difference between two arms splits as d_mean_tae = d_crps + d_half_gmd, a
  location part and a spread part.
- **g**: the materiality threshold of the gate: 10 vehicles of fair CRPS on ms4.
- **G0**: the Build gate's hard checks; any failure rejects (verify-edit status, materialization, structural
  failures, frozen hashes, outside-scope fact changes, shape regularity, no new in-scope audit findings).
- **H1, H2, H3, N1-N4, I**: the steps of the M2 stage evaluator, in order. H1 invariants (equal arms, distinct keys,
  bindings, the measurement-code identity); H2 health (amendment A2.1); H3 guards (entry-delay p95, emergency
  brakings, post-window passages); N1 the largest per-row error growth over 182 rows; N2 the init52 TAE; N3 the
  totals13 fair CRPS; N4 the main130 fair CRPS; I the primary test on ms4. Guards only reject.
- **INV-AUDIT**: the Build invariant that no new in-scope audit finding appears.
- **K1, K2**: the M3 known-answer harnesses: K1, 46 mutation cases on the X4 fixture; K2, 8 cases on the Ring1
  nominal net.
- **L2, L3**: Build guards: L2 an all-turn smoke run; L3 frozen-trip routeability on 10 seeds, where a candidate's
  failing requests must be a subset of the source's.
- **main130, ms4, init52, totals13**: Calibrate objectives: the 130 main-window 15-min rows, the 91 rolling-hour
  values, the 52 initialization rows and the 13 field totals.
- **`source_dirty`**: the parent already fails structural checks (here 25 connection-mode failures), so a Build goal
  on it cannot be promoted without a human.
- **TAE**: total absolute error between simulated and real counts over an objective's values.
- **V**: the Build violation vector (n_TOP, n_CTL, n_GEO, n_ATTR, E): the numbers of mismatched scored facts of the
  topology, control, geometry and attribute classes, and E the sum of their normalized numeric excess, compared
  lexicographically. V = 0 means every scored fact matches.
- **WMW**: the Wilcoxon-Mann-Whitney rank test.
