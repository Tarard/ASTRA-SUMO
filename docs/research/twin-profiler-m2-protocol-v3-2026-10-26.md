# M2 gate-calibration protocol v3: proposal for review

> Dated snapshot of a local working record of the ASTRA Twin Profiler effort, committed so the M2 gate-calibration protocol v3 can be read without the author's machine. Paths under `C:\Users\...\scratchpad`, `E:\twin-store` and `E:\astra-*` refer to that machine and are not part of the repository. Superseding decisions are recorded in later dated documents, not by editing this one.

Calibrate, demand-timing family, Hamburg Ring1 parent. This is iteration B0, which used 0 SUMO runs. The ledger
`E:\twin-store\m2-pilot\runs.jsonl` still holds 28 healthy runs of the workflow's cap of 30. Everything here is explore
evidence with no promotion authority. Script paths are relative to the scratchpad
`C:\Users\Danial\AppData\Local\Temp\claude\E--ASTRA-SUMO\27f6b40f-b59c-47be-ba59-77efdb002a05\scratchpad\`: `A\` means
`m2-b0-A\`, `B\` means `m2-b0-B\`, `C\` means `m2-b0-C\`, and `S\` means `m2-b0-S\`, which holds the synthesis checks
written for this document. Every number rests on 6 draws per arm.

## 1. Summary

**What B0 measured**
1. **Paired ensemble scores with a sign-flip permutation are valid.** They accepted 0 of 36 cached must-reject cases,
   and the Monte Carlo (MC) re-key size stayed within noise of 5% for K = 5..16. Only CRPS ignores dispersion-only
   gains: TAE-type tests accept an L-like sharpening 87-94% of the time under common random numbers (CRN).
2. **On 15-min rows the real counts sit at the parent's chaos floor** (PR/PP 0.96, oracle headroom −13.3). Rolling
   60-min per-field sums (ms4) show misfit beyond chaos: ratio 1.34, excess 127 = 12.7 g, z 3.1.
3. **M1's single-run profile is partly realization-specific.** Rank 1 and the field deficits are stable; co-anchor
   ids, weights beyond rank 1 and the delay tails are not.
4. **v2's fixed guards fired mostly on noise** (46 of 72 cases). Recalibrated margins fire only on M's own row and on
   L's genuine entry backlog.
5. **The test reduces to a sign-flip on per-key contributions.** The paired CRPS swap statistic is linear in the swap
   signs, ΔCRPS(s) = Σ s_k v_k (`S\ens_proto.py`), matching lens A's recomputation to 5·10⁻¹⁴.

**What v3 asks to adopt**
- **Primary objective:** ms4, the 91 rolling-hour values. 15-min main130 becomes a guard and is reported.
- **Rate-bearing test:** an exact one-sided paired sign-flip test on the fair-CRPS difference, with p ≤ α = 0.05 and
  ΔCRPS ≤ −g. Confirmation uses K = 8 fresh secret keys (size ≤ 12/256), or K = 16 when the panel shows no effective
  CRN.
- **Changed guarantee:** exactness under the sharp null, plus MC-bounded composite behaviour. This replaces v2's
  composite-median guarantee (v2's rule stays reported).
- **Guards:** the recalibrated margins, plus totals and main130 CRPS non-inferiority guards.
- **Positive qualification:** by paired energy distance (ED), with power measured against truth ensembles of at least
  8 draws.
- **M1:** an ensemble profile replaces the single-run profile.

## 2. Corrections to v2 (from the v2 review)

| v2 statement | Correction |
|---|---|
| "Exact re-key false-accept rate 1.0%" | A plug-in q̂⁵ with q̂ = 12/30 from 30 dependent ordered pairs; C13 held automatically by ordered-pair symmetry. Not a rate. |
| "P(d ≤ −g) < ½, more draws would not help" | Pooled dependent fold proportions (14/30, 14/30, 15/30). Finite-K power is not monotone (2.21% at K = 5, 2.28% at K = 8 for p = 14/30). The evidence shows low observed power, not impossibility. |
| "L has ED 0, N is a null" | These are unbiased estimates and can be negative. Leave-one-key-out (LOO) L ranges −1.8..2.8 and N −4.5..9.6; the null sd of a 6-key paired ED is 21 (`A\a5_dispersion.py`). Zero does not prove equality. A summed marginal CRPS is blind to changes in dependence alone. |
| "At K = 5 the sign-flip test equals the sign test"; "S needs sd(d) ≲ 2" | Both are false with ties: d = −10 ×5 passes the sign rule but has flip p = 1, and d = [−100, −50, −10, 0, 4] passes S with sd 44. |
| Entry-delay fire count | It used a network-pooled p95. v3 evaluates per entry edge (§5). |
| Code status, provenance | HEAD is a24b92e and clean. `result.json` has `plan_sha256: null` because `s3_analyse.py` reads a missing field. The external hash match stands, but the internal link is incomplete. |

## 3. What B0 measured

| Lens / script | Measurement | Result |
|---|---|---|
| A `a1_cases.py` | Flip-calibrated TAE, CRPS, ES-L2 and median TAE on the 36 null and wrong-revert cases | 0/36 accepted; M positive folds 1/6 (p = 1/32); against the real counts ΔCRPS L +14.47, M +4.67, N +25.33, none accepted |
| A `a1_cases.py` | Paired ED over 6 keys (main130) | M 24.2 (p = 1/32, the minimum); L 0.0 (p .375); N 4.33 (p .375). M's signal sits on 0535Z4@3600: parent = real = 34, M = 17 on every key |
| A `a2_rekey.py` | Re-key splits of the 6 parent draws | K ≤ 3 only: unpaired 1/20 by construction, paired cannot reject; point rules 35-45% false accepts |
| A `a4_mc.py`, `a9_mustreject.py` | Detector-block bootstrap MC, 252 configurations | Re-key size ≤ 0.072 over 42 cells; harmful M-class ≤ 0.023 (CRPS); TAE flip accepts an L-like sharpening at 0.87-0.94 under CRN, CRPS at 0.000 |
| A `a5_dispersion.py` | Identity | ΔTAE = ΔCRPS + ΔGMD/2. A twin-truth CRPS gain is ±ED; a pure scale change is second order in ED |
| B `b_objectives.py`, `b_structure.py` | 19 objectives: PR/PP ratio (excess z) | TAE15 0.96 (−0.48); ms4 1.34 (3.12); ms3 1.27 (2.69); totals 1.73 (1.98). 7 of 13 fields are exact or within 1 vehicle on every draw |
| B `b_oracle.py` | Structural headroom of an additive oracle fix | TAE15 −13.3; ms4 +127.2, of which 104.8 is on corridor Z11-Z13; totals +33.6 (level). Subsets were chosen post hoc |
| B `b_localize.py` | Where the misfit sits | Corridor lag on 0119Z11-Z13 plus the real 9000-9900 dip (Z12 about 7 sd); throughput deficit on 0119Z4/Z5/0535Z4; warm-up deficit (init, z 4-6) |
| C `c2_stability.py` | M1 profile on 6 draws | Entry 44532531 is rank 1 in 6/6. Top-5 id Jaccard 0.47 (0.37 against the nominal). The nominal's rank-3 co-anchor appears in 0/6 draws |
| C `c5_margins.py`, `c6_cases.py` | Guard margins from 6 draws, replayed on 72 cases | v2 rules fired on 46/72 cases; re-key t-margins fire on 12/72, all of them M's own row |
| S `s1_cached.py` | v3 statistics on the cached arms (ms4) | 0/36 must-reject; M paired ED 26.27 (p = 1/32, LOO 22.0-32.3); L −0.47; N −8.80 |
| S `s2_mc_ms4.py`, `s5_mh_coupled.py` | Lens A's generators mapped to ms4 | Size, power and noise in §4.4-4.5 |
| S `s4_mc_emp.py` | Bootstrap of each op's own draws on all detectors | **Rejected.** Two 6-atom samples differ by construction, which inflates the twin effects (M −76 against a cached ED of 24). Not used |

## 4. Gate v3

### 4.1 Hypothesis and claim

The test is exact for the **sharp null**: within each key pair, the candidate's and the reference's scored values are
exchangeable. This covers no-effect ops, re-key candidates, and ops whose only effect is a re-draw of the chaos.

Rejecting it in the direction of a lower CRPS, with ΔCRPS ≤ −g, supports one claim. On the frozen objective, the
candidate's K-draw ensemble is at least g closer in fair CRPS to the target, and that gain is not explained by
exchangeable chaos.

"Different but not better by g" is a composite null that this test does not control exactly. Its behaviour is bounded
only by MC (§4.5). The materiality g enters as a point check. v2's exact composite-median rule is kept as a reported
statistic: it is valid but had no power (P(d ≤ −g) = 0.40-0.63 under every objective, `B\b_objectives.py`).

### 4.2 Primary objective: ms4

ms4 is the L1 objective on per-field sums of 4 consecutive 15-min bins at a 15-min stride over 3600-12600 s, giving
13 × 7 = 91 values, each identified as `W:<field>@<begin>+3600`. Its parent GMD of 369.7 matches main130's 368.7, so
g = 10 keeps its scale.

| Property | main130 | ms4 | Source |
|---|---|---|---|
| PR/PP (excess z) | 0.96 (−0.48) | 1.34 (3.12) | B `b_structure.py` |
| Structural headroom; corridor part | −13.3; 21.0 | 127.2; 104.8 | B `b_oracle.py` |
| M paired ED (p); M ΔGMD/2 | 24.2 (1/32); 13.17 | 26.27 (1/32); 1.30 | S `s1_cached.py` |
| CRN ratio ρ, M / L | 1.52 / 0.39 | 0.60 / 0.05 | B `b_objectives.py` |
| ΔCRPS noise sd at K = 8, no CRN / measured M coupling | 21.2 / 16.3 | 35.6 / 20.9 | S `s5_mh_coupled.py` |
| MC power at K = 8 / 16 for M against an 8-draw truth, no CRN | .51 / .86 | .39 / .71 | A `a4_mc.py`, S `s2_mc_ms4.py` |

On power for the one 15-min-scale positive, M, main130 does better. ms4 is the only objective with headroom against
the real counts. A gate against the real counts, and the M4 loop it serves, needs headroom. Without it, any gain on
main130 is indistinguishable from fitting the real day's noise.

ms4 is a post-hoc pick from 19 objectives. Its width curve is smooth, peaking at 45-60 min, which reduces but does not
remove the winner's curse. The definition is **frozen now**, before any new run, and must be confirmed on fresh draws
(§9, C1). The choice of main130 instead is open question 1.

### 4.3 Rate-bearing test

Both arms run on the same K keys (CRN). The fair CRPS is CRPS(E, y) = mean_k |E_k − y|₁ − Σ_{k≠l} |E_k − E_l|₁ /
(2K(K − 1)). The statistic is T = CRPS(candidate, y) − CRPS(reference, y). For a swap pattern s ∈ {±1}^K,
T(s) = Σ_k s_k v_k, with

v_k = (|C_k − y| − |P_k − y|)/K − Σ_{l≠k} [D(C_k,C_l) + D(C_k,P_l) − D(P_k,P_l) − D(P_k,C_l)] / (2K(K − 1)).

The p-value is p = #{s : T(s) ≤ T} / 2^K, enumerated exactly with integer numerators.

**Accept** `improved` iff K ≥ 5, p ≤ 0.05 and T ≤ −g, with g = 10.

**Screening** uses K = 5 on panel keys and carries no rate.

**Confirmation** uses fresh secret keys drawn after the freeze. Before the keys are drawn, the following are fixed and
committed by SHA-256: the candidate, the reference, the target, the objective, g, K and α. There is no adaptive
stopping and no reuse of exposed keys.

K is set from the panel regime: K = 8 when ρ = sd(d)/re-key sd ≤ 0.35 on ms4, otherwise K = 16. Because the panel is
independent of the fresh keys, choosing K this way keeps the test exact. As in v2, the nominal run is excluded from
every rule and every margin.

### 4.4 Size

| Evidence | K = 5 | K = 8 | K = 16 |
|---|---|---|---|
| Exact bound (sharp null) | 1/32 | 12/256 = 4.7% | 3276/65536 = 5.0% |
| MC re-key, real target, forward / reverse: ms4 (`S\s2`, 1000 reps) | .027 / .035 | .040 / .054 | .044 / .046 |
| The same, main130 (`A\a4`, 600 reps) | .042 / .032 | .050 / .047 | .028 / .072 |
| MC re-key, 8-draw twin truth, ms4 | .028 / .030 | .046 / .048 | .039 / .045 |
| Perfect CRN (A-A) | 0 | 0 | 0 |
| Cached panel (enumeration) | K ≤ 3 only: unpaired 1/20 per objective, paired cannot reject (`A\a2`, `S\s1`) | | |

### 4.5 Power on M and behaviour on L and N

| Scenario (population effect ms4 / main130) | ms4 K = 5 / 8 / 16 | main130 K = 5 / 8 / 16 |
|---|---|---|
| M-hybrid against an 8-draw M truth, no CRN (ED 28.1 / 32.1) | .14 / .39 / .71 | .22 / .51 / .86 |
| The same, perfect CRN | .99 / 1.0 / 1.0 | .98 / 1.0 / 1.0 |
| M revert against the real counts, no CRN (gain 21.0 / 27.0) | .06 / .14 / .18 | .09 / .34 / .58 |
| The same, perfect CRN | .58 / .99 / 1.0 | 1.0 / 1.0 / 1.0 |
| Harmful M-class roles (null, wrongS, M against the real counts), maximum | ≤ .017 | ≤ .005 |
| Ld, dispersion-only (\|effect\| ≤ 3.6 < g), every role, maximum | ≤ .099 | ≤ .098 |
| Ld, TAE flip under CRN, against the real counts | 0 | .87-.97 |
| ΔCRPS noise sd, no CRN (`S\s5`) | 49.0 / 35.6 / 26.0 | 29.2 / 21.2 / 14.0 |
| ΔCRPS noise sd, M's measured coupling (`S\s5`) | 28.2 / 20.9 / 14.6 | 21.2 / 16.3 / 11.3 |

At 0.8 power the detectable effect is about 2.5 sd: for an early op on ms4, about 52 at K = 8 and 36 at K = 16.

**Cached behaviour** (`S\s1`, ms4):
- Against the real counts (K = 6): L +6.2 (p .75), M +8.53 (p .64), N +62.4 (p .95). No revert is accepted (minimum
  p .078).
- In the folds, M positives are accepted 1/6 (truth k1, T −74.8, p 1/32). L's positive and revert folds are accepted
  0/6. N's revert folds are accepted 0/6 (main130: 1/6 against a single truth draw).

### 4.6 Gate steps

| Step | Rule | Label if it fails |
|---|---|---|
| H1, H2 | invariants; every run healthy | `blocked`, `unhealthy` |
| H3 | entry, braking and post-window guards (§5) | `guard` |
| N1, N2 | row and init52 margins (§5) | `row_regress`, `init` |
| N3, N4 | ΔCRPS on totals13 and on main130 within their margins (§5) | `level_regress`, `fine_regress` |
| — | every counted row equals the reference's on every key | `no_effect` |
| I | K ≥ 5, p ≤ α and ΔCRPS_ms4 ≤ −g | → `improved` (rate α only in confirmation) |
| S | v2's exact sign rule on per-key ms4 TAE (DoF lower, c(K) of d < δ_NI) and ΔCRPS_ms4 < δ_NI | → `simplified` |
| otherwise | T ≤ −g → `not_significant`; T ≥ g → `worse`; else `not_material`. Note `tae_only` when ΔTAE ≤ −g but T > −g | |

### 4.7 Report-only statistics

These are reported and never gate:
- **Splits and alternative tests:** the split ΔTAE = ΔCRPS + ΔGMD/2; the ensemble-TAE flip p; v2's sign rule.
- **Distributional diagnostics:** the paired ED with its swap p (a "differs from parent" check, used for
  qualification); the main130 CRPS flip p; ms3 as a sensitivity check; the ensemble-median TAE.
- **Per-field checks:** the real total inside or outside the draw range for each field; the rank of the real counts
  among K + 1 members; the per-cell predictive z on the 6 informative fields; ρ and κ.
- **When pairing is impossible:** the unpaired label-permutation CRPS (4 + 4 minimum), never against a reference
  ensemble the proposer has seen.

## 5. Guards and margins

Margins are listed as K = 5 [K = 8] {K = 16, by formula}. Guards only reject, so they cost power and never validity.
All margins are provisional and are recomputed on a 14-draw panel before any standard tier.

| Guard | v3 rule | Basis | Fires on the 72 stage-3 cases |
|---|---|---|---|
| Entry, 7 quiet edges | K-mean p95 ≤ 10 s | p95 is 0.1 s in all 24 runs | 0 |
| Entry, 6 busy edges and network p95 | fires when mean Δ − t₀.₉₅·sd/√K > 10 s | L's paired sd is 8-16 s against a re-key sd of 170 s | 12, all L op-vs-parent |
| Entry backstop | network 245 [194] {137} s; 44532531 256 [203] {143} s | t₀.₉₉,₅ · s · √(2/K) | 0 |
| Brakings | K-mean Δ ≤ 5.3 [4.2] {3.0} | draw sd 2.48 | 0 (v2: 24) |
| Post-window passages | K-mean Δ ≤ 47 [37] {26} | draw sd 22.0 | 0 (v2: 2) |
| N2 init52 | K-mean Δ ≤ 29 [23] {16} | draw sd 13.62; the block bootstrap (9.17) understates it | 0 (v2: 3) |
| N1 max row growth | ≤ 15 [12], never on one draw | 6-draw bootstrap q99 | 12, all M on 0535Z4@3600 |
| N3 ΔCRPS totals13 (new) | ≤ max(owner floor, 21.0 [15.5] {10.4}) | MC re-key q99 (`S\s2`) | 2, both N against single truth draws (`S\s6`) |
| N4 ΔCRPS main130 (new) | ≤ 68 [50] {33} | MC re-key q99 (`S\s5`) | 0 (`S\s6`) |

Sources: `C\c5_margins.py`, `C\c6_cases.py` and `S\s6_new_guards.py`. Whether the L-type backlog guard should block a
positive whose twin truth shares the backlog is an owner decision.

## 6. Positive qualification

A twin-truth op counts as power evidence only if it passes four checks on at least 6 panel keys:
1. Paired ED on ms4 is at least g, with swap p ≤ 0.05.
2. Every leave-one-key-out ED is at least g/2.
3. The mechanistic check C8 holds on at least 5 of 6 keys.
4. Power is then measured against a truth ensemble of at least 8 fresh draws, on keys disjoint from the qualification
   panel. Single held-out truths give power ≤ 0.29 at K = 16.

The class follows ED/g, as in `k3.classify`.

On the cached arms:
- **M qualifies as moderate:** ED 26.27, p 1/32, LOO 22.0-32.3, C8 5/6.
- **L fails:** ED −0.47.
- **N fails:** ED −8.80, p .56.

## 7. What can and cannot be claimed

| Can claim | Cannot claim |
|---|---|
| **Confirmation:** P(`improved`) ≤ α under the sharp null, whatever produced the candidate, given §4.3; m·α over m logged confirmations; shared reference runs on the same secret keys are allowed | **Rates outside confirmation:** none for screening on visible keys, negative controls or plug-in re-key estimates |
| **Empirical, generator-based (not a theorem):** truly worse M-class candidates accepted ≤ 0.017; sub-g true gains ≤ 0.099 | **Composite and population claims:** no exact control of "different but not better by g", no exact mean claim, no population statement from 6 draws (an ED of 0 is not equality) |
| **Cached panel:** 0/36 must-reject accepts on ms4, main130, ms3 and totals13 (negative controls on reused runs, not rates) | **Generalisation:** no gain beyond the one real day; M4 can fit that day's noise and no holdout exists |
| **Judge:** determinism, causality before t0 and the cache key, as in v2 | **Elsewhere:** no changes confined to cross-field dependence; no other family (tie order) or parent; nothing on field truth, OD, causal attribution, or topology, demand or control correctness; no promotion |

## 8. Implications

**M1.** On this parent, the nominal run's profile is not the parent's profile. Its TAE (275), field misfit (51) and
init52 (97) all lie outside the 6-draw ranges (329-399, 75-89 and 54-92).

v3 adopts an ensemble profile:
- **Scope:** M1 runs on each panel draw, without the nominal.
- **Hotspots:** keyed by lead element, with top-5 frequency k/K and a Clopper-Pearson lower bound, the median weight
  with [min, max], and the median rank. Co-anchor ids go to a per-draw annex. A declared rule merges consecutive route
  edges into one corridor.
- **Field balance:** medians and ranges, a sign-stability flag, and a flag when the real total lies outside the draw
  range.

| M1 output | Status on 6 draws (`C\c2_stability.py`, `C\ensemble_profile.json`) |
|---|---|
| Entry 44532531 at rank 1 | trustworthy: 6/6 draws, weight 78 [53-140], every localized Z12/Z13 row |
| Z4/Z5 corridor 24483344#0/#1 | trustworthy when merged: weight 44-94 |
| BAL 0119Z4, 0119Z5, 0535Z4 | trustworthy: the real totals 600, 522 and 390 exceed every draw |
| Quiet fields and rows, supply term (53), identities | trustworthy |
| Co-anchor sets; weights and ranks beyond rank 1 | realization-specific |
| late_in, delayed_out (CV 27-28%); delay p90/p99 (CV 21-23%) | realization-specific |
| Per-row r on active fields (12/130 sign-stable); Z12/Z13 r_main sign | realization-specific |
| Top-three attribution evaluation | pending: re-run against the ensemble profile |

**M4.**
- **Objective and evaluation:** the loop optimises ms4 ensemble CRPS against the real counts. Each candidate is a
  K = 5 screen with no rate, and claims come only from logged confirmations.
- **Targets:** the 6 informative fields (0119Z11, Z12, Z13, Z4, Z5 and 0535Z4).
- **What can be reached:** the corridor lag and the 9000-9900 dip, the Z4/Z5 throughput deficit (8-30 vehicles per
  draw pass after 12600), and the 0535Z4 late spike.
- **What cannot be reached:** the warm-up deficit is outside the main-window timing family. From counts alone, these
  misfits are not identifiable as timing rather than supply or control.
- **Confirmable effects:** for early ops, only gains of about 5 g (K = 8) or 3.6 g (K = 16) are confirmable at 0.8
  power.

**The "130 residuals = 0" goal.**
- At 15-min resolution the parent is already at its chaos floor. Its distance to the real counts (PR 355) is below its
  draw-to-draw distance (PP 369), and its CRPS is 171 against 184 if the real counts were a draw. The real count lies
  outside the 6-draw range on only 16 of 182 rows.
- A zero-residual draw would be a selection effect, like the nominal 275. It would show neither a stable fit nor field
  correctness.
- The spread belongs to this parent; it is not an attainable floor for other twins.

Proposed restatement:
- (a) calibration on the 15-min rows (the rank of the real counts among the draws);
- (b) no hour-scale excess (ms4 PR/PP → 1; today 1.34);
- (c) the real field totals inside the twin range (today 3 fields lie above it);
- (d) any sharpening judged by CRPS, never by TAE.

## 9. Next run budget (deferred to the owner)

Costs assume about 100 s per run with 3 workers and 154 MB of disk per run. Every option needs a new budget beyond
the cap.

| Option | Runs | Content | Buys |
|---|---|---|---|
| C0 | 0 | the twin/ functions and pinned tests (§11); the M2(a) toy-net a_v check | executable v3 |
| C1 panel | 8 (13 min) | parent on k7..k14 | a 14-draw panel for the margins; a fresh check of the frozen ms4 excess (1.34) and of how often the k3-type spillback occurs |
| C2 qualification | 8 + 14 (37 min) | M on 8 fresh keys (a truth ensemble); 1 hour-scale corridor op on k1..k6 plus 8 truth keys | twin-truth power that fits ms4 |
| C3 confirmation | 16 or 32 (27-53 min) | fresh secret keys: the parent and one qualified positive, judged against its C2 truth ensemble | the confirmation end to end |
| C4 standard | about 100 (2.8 h, 15 GB) | 4 positives × 14, 2 negative controls × 6, 2 confirmations × 16 | power by class with wide CIs |

## 10. Open questions

1. **Primary objective.** ms4 (headroom; frozen post hoc) or main130 (more power for 15-min shifts; no headroom)?
2. **Guarantee.** Accept sharp-null exactness plus MC composite bounds in place of v2's composite-median exactness?
3. **Materiality.** Is g = 10 right on ms4? What vehicle floor applies to the totals guard (5)?
4. **K by regime.** Is K = 8 or 16 acceptable, given that early-op gains below about 5 g cannot be confirmed?
5. **S branch.** There is still no powered non-inferiority test. Keep v2's rule, or adopt an approximate rule with no
   exact rate?
6. **L backlog.** Should a guard block a positive whose twin truth shares the backlog?
7. **Family.** depart_jitter ±0.5 s only, or a separate tie-order family?
8. **Holdout.** Is a second count day, or a field or window holdout, available against overfitting the real day?
9. **Budget.** Which of C0-C4?

## 11. Artifacts and implementation

The synthesis scripts are in `m2-b0-S\`:

| Script | Content |
|---|---|
| `ens_proto.py` | pure-Python prototypes of the functions below |
| `s1_cached.py` | statistics on the cached arms (writes `s1_cached.json`) |
| `s2_mc_ms4.py` | ms4 MC, 48 configurations × 1000 reps |
| `s3_summary.py` | side-by-side summary of the MC results |
| `s5_mh_coupled.py` | the M-hybrid with measured coupling, 2000 reps |
| `s6_new_guards.py` | replay of the N3 and N4 guards |
| `s4_mc_emp.py` | rejected generator |

The lens scripts are listed in §3.

Proposed code, all pure, standard library, with no SUMO:

| Module | Functions |
|---|---|
| `twin/ensemble.py` | objective transforms, fair CRPS, `crps_contributions`, exact sign-flip p, paired CRPS and ED tests, unpaired test, regime and K, v3 case and verdict |
| `twin/guards.py` | p95 convention, re-key t-margin, K-mean and CRN-paired guards, entry guards |
| `twin/ensemble_profile.py` | lead-element hotspot aggregation, Clopper-Pearson bound, field-balance ensemble |
| `twin/panel.py` (I/O) | builds the committed cached-panel fixture from the store |

The tests pin values from `S\s1_cached.json` and the lens C outputs.
