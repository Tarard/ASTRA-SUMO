# M2 C3 result (2026-10-26)

> Dated snapshot of a local working record of the ASTRA Twin Profiler effort, committed so the M2 stage C3 result can be read without the author's machine. Paths under `C:\Users\...\scratchpad`, `E:\twin-store` and `E:\astra-*` refer to that machine and are not part of the repository. Superseding decisions are recorded in later dated documents, not by editing this one.

This is explore evidence only: simulated counts under the supplied model, with no field claim and no promotion
authority. No secret key appears in this file or in `m2-c3-result.json`.

## Verdict: the C3 stage gate FAILS
The frozen gate was applied literally.

| Evaluation | Label | First failure | Gate requirement | Met |
|---|---|---|---|---|
| Positive: candidate M against the reference parent | `unhealthy` | H2 | `improved` | **no** |
| Negative control: negative-control parent against the reference parent | `unhealthy` | H2 | not `improved` | yes |

- **Sequence.** The pre-registered sequence stops here (m2-decisions.md stage gates). C4 is not run.
- **The positive fails three separate steps.**
  - H2: a failure class the parent panel never showed.
  - H3: the `post_window` guard.
  - I: the primary test, with T = -7.745 and p = 0.352.
- **What the label would be at each later step.** If H2 had held, the label would be `guard` from H3. The I step on
  its own reads `not_material`, because |T| < g.

## Identity and commitment
- **Code.** E:\astra-m2 matched FREEZE.json both before the first run and after the last run. Nothing in E:\astra-m2
  was changed.
  - HEAD d11d284.
  - Working-tree hash 11951a03....
  - `git diff HEAD` hash 8fb059c7....
  - All 12 per-file sha256s.
- **Frozen hashes**, each recomputed from M's 8 C2 truth runs or from the code:

| Item | sha256 |
|---|---|
| FREEZE | de9d73c0... |
| Gate `m2-confirmation-K16` | cd7db25e... |
| Target | 06261d25... |
| Real counts | 32ab5283... |

  The truth runs' key and request fingerprints match, and so does their identity: measurement code de7747aa,
  runtime 3c7d6e80.
- **Ledger.** Before the runs it equalled the C2 ledger: 58 rows, 8f3e2de5.... After the runs it has 106 rows,
  4a55da59....
- **Commitment.** `m2-c3/COMMIT.json`, sha256 f013bcd8... (in `m2-c3/COMMIT.sha256`).
  - Committed at 02:11:20.217Z. The batch started at 02:12:20Z. The first run started at 02:12:33Z and the last at
    03:18:39Z, so every run started after the commitment.
  - 51 keys (16 + 1 spare per arm), each from os.urandom(32).
  - Every key is distinct. None equals any of the 32 ledger fingerprints, the 36 public labels of the earlier stages
    or the 8 target fingerprints.
- **Secret files.** These are the only places the keys exist:
  - `E:\twin-store\m2-pilot\secret\c3-keys.json` (sha256 a32dc25d...);
  - `c3-jobs.json` and `c3-spare-jobs.json` in the same directory.
- **Recorded keys.** All 48 recorded run keys match the commitment in order, and every one has `public: false`.

## Runs
- **Batch.** 48 judge runs with 3 workers, from 02:12:20Z to 03:23:45Z, through `astra_twin.py judge --batch`. Each run
  took 223.6-460.4 s.
- **No replacements.** No run crashed or timed out, so no spare was used (0 of 3).
- **Unhealthy runs.** Five runs completed unhealthy. All are kept and scored under A2.2:

| Run | Classes | Detail |
|---|---|---|
| c3-reference-02 | collision, teleport | 1 / 1 |
| c3-reference-03 | **gridlock**, not_inserted, not_arrived | All stopped at 12864.8 s: 1187 waiting, 396 running. TAE 1741 |
| c3-reference-06 | collision, teleport | 1 / 1 |
| c3-candidate-13 | collision, teleport | 1 / 1 |
| c3-candidate-16 | **gridlock**, not_inserted, not_arrived | All stopped at 12955.2 s: 872 waiting, 395 running. TAE 1565 |

**Unhealthy frequency** (A2.3):

| Group | Unhealthy | Gridlock |
|---|---|---|
| Reference arm | 3/16 | 1 |
| Candidate arm | 2/16 | 1 |
| Negative-control arm | 0/16 | 0 |
| Parent, pooled C1 + C3 | 4/46 | 1 |
| M, pooled | 3/30 | 1 |

Gridlock had never been observed before C3. It now appears in the parent itself.

## Gate steps
Margins are the frozen K = 16 values. N1, N3 and N4 are applied against M's truth ensemble; N2 is applied against the
real init52 counts.

| Step | Positive (M vs reference) | Negative control (parent vs reference) |
|---|---|---|
| H1 | **pass**: disjoint secret keys, 16/16 admitted per arm, 0 spares, one identity | **pass** (same checks) |
| H2 | **FAIL**: new classes on c16 and r03; unhealthy 2 vs 3, excess -1 (margin 2) | **FAIL**: new classes on r03; unhealthy 0 vs 3, excess -3 |
| H3 | **FAIL**: post_window +19.25 vs 15.002 (61.56 vs 42.31) | **pass**: post_window +8.81 |
| N1 | 35/8 = 4.375 <= 8.31 (worst R:0119Z13@11700): pass | 39/16 = 2.4375 (R:0119Z13@6300): pass |
| N2 | -7/8 = -0.875 <= 13.284: pass | -45/8 = -5.625: pass |
| N3 (totals13) | -239/480 = -0.498 <= 15.149: pass | 757/960 = 0.789: pass |
| N4 (main130) | -4299/160 = -26.869 <= 31.077: pass | -91/10 = -9.1: pass |
| I (ms4, g 10, alpha 0.05) | T = -1487/192 = **-7.745**; p = 211616587/601080390 = **0.35206**: fail | T = -533/160 = **-3.331**; p = 27379598/100180065 = **0.27330**: fail |
| Label / note | `unhealthy` / `tae_only` | `unhealthy` / `tae_only` |

The other H3 guards in the positive, all of which hold:

| Guard | Change | Margin |
|---|---|---|
| Network p95 | +51.26 | 79.529 |
| 44532531 | +51.93 | 84.693 |
| Brakings | +1.875 | 5.999 |
| Quiet entries | <= 0.097 | floor 10 |

Test I is the exact count over all C(32, 16) = 601080390 relabelings. Its minimum attainable p is 1/601080390.

## Report only (never a gate)

| | Positive | Negative control |
|---|---|---|
| Split d_mean_tae / d_crps / d_half_gmd | -56.33 / -7.745 / -48.58 | -273.16 / -3.331 / -269.83 |
| Unpaired main130 / ms3 / totals13: T (p) | -26.87 (0.0045) / -16.81 (0.226) / -0.50 (0.426) | -9.10 (0.0425) / -7.66 (0.121) / 0.79 (0.645) |
| Unpaired energy distance | -1099/120 = -9.16 | -1111/320 = -3.47 |
| Index-paired sign flip, no CRN: p | 0.350 | 0.239 |
| Index-paired ensemble-TAE flip: p | 115/256 = 0.449 | 28205/65536 = 0.430 |
| Index-paired v2 sign rule (needs 12) | 8/16, p 0.598 | 7/16, p 0.773 |
| Index-paired v2 S rule | 11/16, p 0.105 | 8/16, p 0.598 |
| Index-paired energy distance (p) | -4.675 (0.500) | -2.49 (0.654) |
| rho / kappa | not applicable (disjoint keys) | not applicable |
| L-type backlog flags | none (44532531 lcb 3.75, network lcb 6.08, both < 10) | none |
| Busy-entry report-only margins | none fired | none fired |
| Real totals above both arms' ranges | 0119Z4, 0119Z5, 0535Z4 | same |
| Unhealthy plausibility (tail at parent rate) | candidate 0.318, reference 0.102: neither implausible | reference 0.102 |
| Truth health | t1 unhealthy (kept) | same |

- **The gridlocked draws dominate the split.** The reference arm holds r03 (TAE 1741). In the negative control,
  d_half_gmd -269.8 offsets d_mean_tae -273.2, so the CRPS difference is only -3.3.
- **main130 sensitivity.** The negative control's report-only main130 test gives p 0.0425 on a parent-vs-parent
  comparison. It is one of three report-only objectives, so a value this low is expected occasionally.

## What C3 does and does not show
- **Shows.** C3 is one conditional end-to-end demonstration of the confirmation pipeline. It covers:
  - keys committed before the runs;
  - 48 runs on secret keys;
  - ledger key-use, binding and identity checks, all passed;
  - the frozen gate evaluated literally.

  The positive M was **not** confirmed, and the stage gate fails.
- **Does not show anything about M's real effect, in either direction.**
  - The approximate power of test I at K = 16 was about 0.47.
  - That figure is a plug-in normal approximation for test I alone. It is not the complete C3 success probability,
    which also depends on health, the guards and the negative control.
  - So even a correct pipeline fails the positive about half the time.
- **Negative control.** Its non-improvement is what a valid null gives in at least 95% of cases (non-rejection).
  - Here it was decided at H2, not at I. Its I step alone gives p 0.273.
- **Health rule.** A2.1 (i) rejects any class the 14-draw parent panel never showed, and it now rejects the parent
  against itself: the parent gridlocks on 1 of 32 fresh draws.
  - Whether to amend this rule is an owner decision.
  - Any amendment would be written after seeing C3, and nothing was amended here.
- **Guards only reject.** N1, N3 and N4 held, but their margins were calibrated against the real counts. That does
  not establish 99% guard behaviour against M's truth ensemble, and it does not certify non-regression.
- **No field claim.** The evidence covers one day, 2026-10-19 05:30-08:00 UTC, and one family, depart_jitter. There is
  no holdout.

## Files

**Plan and commitment** (`m2-c3\`):
- FREEZE.json
- COMMIT.json and COMMIT.sha256

**Evaluations** (`m2-c3\`):
- spec_positive.json and spec_negative_control.json
- verdict_positive.json and verdict_negative_control.json: the full canonical stage verdicts

**Scratch** (`m2-c3\work\`):
- identity_pre.json and identity_post.json
- plan_only.json
- c3_run.out.json and c3_run.started
- admission_walk.json
- the scripts: verify_identity.py, commit_keys.py, make_jobs.py, build_specs.py, summarise.py and add_notes.py

**Result:** `m2-c3-result.json`.
