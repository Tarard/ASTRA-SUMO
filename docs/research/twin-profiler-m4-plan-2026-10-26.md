# ASTRA Twin Profiler: M4 plan, revised after M2 and the Ring1 fragility check

Written 2026-10-26, after the M2 stages C1 to C3, the Ring1 fragility static gate and the candidate-F2 diagnostic run
([results](ring1-fragility-results-2026-10-26.md)). It proposes a replacement for the M4 milestone of the
twin-profiler design v2, "loops without an agent" (the demand-timing flow and the Build flow F1-lite). It does not
decide that replacement: design v2 gates its Calibrate loops on an M2 exit criterion that M2 did not meet, and leaves
the revision to a human (owner decision OD0, section 2.1). It is a milestone plan: it runs nothing, changes no dated
record and carries no promotion authority. Every result it schedules is explore evidence under the supplied SUMO
model, with no field claim and no traffic-safety claim.

Design v2, the M2 v3 protocol and decisions, and the M2 stage results are committed as dated snapshots:
[design v2](twin-profiler-design-v2-2026-10-25.md), [M2 protocol v3](twin-profiler-m2-protocol-v3-2026-10-26.md),
[M2 decisions](twin-profiler-m2-decisions-2026-10-26.md) and the M2 stage results
[C1](twin-profiler-m2-c1-result-2026-10-26.md), [C2](twin-profiler-m2-c2-result-2026-10-26.md) and
[C3](twin-profiler-m2-c3-result-2026-10-26.md). Section 12 quotes the rules of those documents that this plan relies
on and summarizes the M2 stages; section 13 defines the terms.

**Revised 2026-10-26 after the reviewer's verdict.** A read-only review of the first version of this plan and of the
fragility results (archive `codex_review_m4plan.json`, sha256 `a30f2021...9bd0`; good = false) asked for the changes
below. Nothing had relied on the first version.
- **Headroom and demand timing** (sections 2.1, 9, 10). The plan no longer says that main130 has no headroom or that
  M2 cannot be completed on the demand-timing family. Headroom was not demonstrated, and the evidence is weak either
  way. One failed confirmation at a plug-in power of about 0.47 does not show that the family cannot qualify, and
  withdrawing flow F2 is an owner choice (OD0 (a)).
- **Provenance** (section 12; results, section 6). The basis documents are committed snapshots; the first version of
  section 12 said that they were not in the repository. The selector fix is commit `01f91817`.
- **T5, new in M4.0** (sections 2.4 and 4). The exact evaluator refuses unequal arms above 20,000 relabelings and
  K = 17 or more. T5 fixes, before M4.0 freezes, the method for unequal arms and for K up to 24, its known answers,
  its analysis budget, the margins at those sizes and the outcome when too few healthy draws remain.
- **T2** (section 4). The routeability draw design and the independence of the arms are frozen, the binomial and
  Newcombe calculations are tied to that design, and resampling intervals are labelled conditional Monte Carlo
  uncertainty.
- **M4.3 persistence labels** (sections 7 and 11). The `effect_basis` threshold, which attributed a gain to fragility
  or calibration, is replaced by descriptive persistence labels with an explicit `unresolved` category. The plan now
  states that conditioning on health changes the comparison.
- **Known-positive check first** (sections 2.1, 6, 7, 8 and 9). The confirmation-scale twin-of-twin check at K = 16
  moves before the M4.2 loop as a recommended step for each admitted family: M qualified at C2 while none of its 28
  K = 6 primary screens was `improved`, so qualification alone did not show power at the confirmation gate. Budgets,
  effort and the owner decisions are updated.

**Names.** The design's flows are written *flow F2 (demand timing)* and *flow F1-lite*. The fragility candidates of
the [pre-registration](ring1-fragility-prereg-2026-10-26.md) and the
[owner-delegated decisions](ring1-fragility-owner-decisions-2026-10-26.md) are written *candidate F1* (S03, delete
LSA535 link 5), *candidate F2* (S01, add 603103444_1 -> 73474895_0) and *candidate F3* (F1 then F2). S01 to S04 are
the census sites. **ms4** is the rolling-hour objective of the M2 v3 gate: 91 values, 13 fields by 7 windows of four
15-min bins at a 15-min stride over 3600-12600 s. **main130** is the 15-min main-window objective. The *original
parent* is the Calibrate parent net `6c52382e...` with the executed control `readme_fixed_A` (`27220b43...`). Owner
decisions are numbered OD0 to OD9 (section 9).

## 1. Summary

The Ring1 fragility check was a pre-M4 step, run on the branch `twin/m4-fragility` before this plan; its
pre-registration carries "(M4)" in its title for that reason. It is not one of the parts below, which this plan
numbers M4.0 to M4.3.

| | design v2 M4 | this plan |
|---|---|---|
| precondition | M2 exit criterion and M3 acceptance | M3 acceptance (met, `ae21563`). M2's exit criterion was not met (C3 failed). M4.2 and M4.3 would run without it, which needs owner decision OD0 before M4.0 (section 2.1) |
| Calibrate loop | flow F2 (demand timing): surrogate `cell_depart_shift` ops, one per round | proposed for withdrawal, an owner choice that the M2 evidence does not force (section 2.1, OD0 (a)); if withdrawn, replaced by M4.2, hour-scale structural candidates judged on ms4 |
| Build loop | flow F1-lite: single-fact ops on 3 K2 goals | M4.1: the same no-agent loop on the fragility goals of the Calibrate parent; the K2 run stays as its machinery check |
| parent for calibration work | the original parent | the fixed parent if M4.1 adopts one, else the original parent |
| tools | as built in M1 to M3 | M4.0 first fixes five limits: four that the static gate and the runs exposed (T1 to T4) and the confirmation evaluator's scope (T5), which the review of this plan found; T2 and T3 change rules of design v2 and of the M2 decisions, so their principle is an owner decision (OD2, OD3) |
| accounting | trial log; one confirmatory comparison on 8 fresh keys | M4.2: a recommended confirmation-scale known-positive check per admitted family before its loop. M4.3: the trial log opens before the first M4.1 judgement; one calibration confirmation on disjoint fresh secret keys, 16 per arm unless OD9 raises K (M2 amendment A1.3), with a pre-registered sensitivity analysis that reports whether the gain persists on the draws without a health failure (descriptive labels, attributing nothing) |
| budget | 2 days plus runtime | about 10.5 days of code (estimate, with an owner stop point for M4.0); 330-346 Calibrate runs plus 19-20 spares with the recommended known-positive checks (266-282 plus 15-16 without them), about 9.2-9.6 h and 51-53 GB; static-gate work about 4.2 h at one worker (estimate); section 8 |

The parts run in order: M4.0, then M4.1, then M4.2, then M4.3, whose trial log covers M4.1 and M4.2. The agent loop
stays in M5 (section 10). **Firewall:** M4 adds no SUMO installation path, repository clone or Python environment.
Every SUMO run uses the existing SUMO 1.27.1 installation of the existing environments. Calibrate and static-gate
runs go through the existing judge (`astra_twin.py judge`) and profile-build (`astra_twin.py profile build`) entry
points from one committed code state, as the fragility check did.

## 2. The evidence that changes M4

### 2.1 Headroom on the 15-min objective was not demonstrated, and demand timing has not confirmed at hour scale

- On main130 the parent's distance to the real counts is not larger than its own draw-to-draw distance: PR/PP 0.96
  on k1..k6 (excess -13.3, jackknife SE 27.8, so weak evidence either way) and 0.99 on the 8 fresh draws k7..k14
  (z -0.39). M2 called this the parent's chaos floor; it is a statement about this parent and this day (M2
  amendment A1.8). Headroom at 15-min resolution was therefore not demonstrated. These panels do not show that there
  is none: a misfit smaller than their resolution would not appear in them.
- The only qualified demand-timing positive, `cell_depart_shift:107:900` (M; paired ms4 ED 26.27, swap p 1/32), was
  not confirmed at C3: T = -7.745, p = 0.352 on 16 + 16 disjoint secret keys, with H2 and the H3 `post_window` guard
  also failing. Its plug-in power for the primary test was about 0.47, so C3 is weak evidence about M's real effect
  in either direction. One failed confirmation at that power does not show that M, or the demand-timing family,
  cannot qualify or confirm.
- Qualification did not show power at the confirmation gate either: M qualified at C2, yet none of its 28 K = 6
  primary screens against its own 8-draw truth ensemble was `improved`, and test I failed in all 28 (C2, P1; mean
  T -28.4, smallest p 0.062).
- The one hour-scale corridor timing op screened at C2, `cell_depart_shift:101:2700` (X), failed qualification
  (ED 8.27, swap p 9/32, minimum leave-one-key-out ED -8.7, spread-driven). All 9 eligible corridor timing ops had a
  negative predicted ms4 gain, and the queue model over-predicted the effect (ED 25.21 predicted, 8.27 measured).
- The M2 sequence stopped at C3 as pre-registered; C4 did not run.

**What this means for the design's exit criterion.** Design v2 ties M2's exit criterion to the Calibrate loops, M4
flow F2 and M5 flow F3, not to demand timing alone (section 12.1). The criterion is the evidence that the gate's
screen decisions and its confirmation test behave as declared before any loop relies on them. M4.2 is still a
Calibrate loop on the M2 v3 gate, and M4.3 is still the claim-bearing confirmation on the A1.3 test. That test has
never confirmed a known positive: C3 failed at an approximate power of 0.47, so C3 shows neither that the test works
nor that it does not. When M2 fails, the design says that "a human revises K, the family, g_min or the grid under a
new protocol version". This plan is a proposal for that revision, and the owner decides it as OD0, before M4.0:

- (a) replace design v2's M4 by M4.0 to M4.3; and, as a separate choice, withdraw flow F2 (demand timing) or keep it.
  If it is kept, M4.2 does not replace it, and flow F2 needs its own revised protocol outside this plan (for example
  a confirmation of a demand-timing positive at a larger K);
- (b) accept M4.2's per-family qualification (section 6) in place of the exit criterion for the M4.2 loop. It is
  weaker: 2 positives and 2 negative controls per family at K = 6, against the criterion's 12 validation nulls, 12
  placebos and 4 large positives with a promotion pass. Qualification alone has no confirmation-scale check, and C2
  showed that it does not imply power at the confirmation gate;
- (c) run the confirmation-scale known-positive check of section 6 for each admitted family before the M4.2 loop
  (recommended; a twin-of-twin positive at K = 16, 32 runs per family), or skip it and run M4.2 and M4.3 on a
  confirmation test that has not confirmed a known positive; and whether a family whose check is not `improved` is
  looped at all.

This plan's reasons for going on without the exit criterion are that its validation set was built for the
demand-timing family, and that the M2 evidence on that family is weak: its only qualified positive did not confirm,
at a plug-in power of about 0.47, and its hour-scale op did not qualify. That evidence does not show that the family
cannot qualify or confirm, or that M2 cannot be completed on it, and it does not exclude that a larger confirmation K
or another hour-scale demand-timing op would. Withdrawing flow F2 is therefore the owner's choice (OD0 (a)), not a
consequence of the evidence, and these reasons are inputs to the owner's revision of the protocol, not a replacement
for it. If flow F2 is withdrawn, demand timing returns only with a family that qualifies at hour scale, on an
objective whose excess the parent panel has shown, as ms4's in C1 (section 10).

### 2.2 The hour-scale misfit replicates on fresh draws

- C1, decision row 1 as pre-registered: ms4 excess 105.5 on the fresh draws k7..k14, jackknife z 2.43, every
  leave-one-draw-out excess positive (90.9 to 139.8); 113.1 (z 3.56) on all 14 parent draws.
- **Corridor Z11-Z13.** 0119Z12 25.7, Z13 23.0 and Z11 21.4 of the fresh excess (70 of 105). The twin runs later
  than the real counts (+87 s pooled, +158 s on 0119Z11, from C2), the real counts dip at 9000-9900 s on 0119Z12
  (about 7 sd from the draws, B0), and the real 0119Z12 and Z13 totals (1160, 1346) lie below every fresh draw.
- **Throughput Z4/Z5 and 0535Z4.** 0119Z5 21.6, Z4 7.9 and 0535Z4 6.6 of the fresh excess. The real totals 0119Z4
  600, Z5 522 and 0535Z4 390 lie above every draw (fresh ranges 572-587, 483-498 and 372-379; 13 of 13 healthy panel
  draws), and 8 to 30 vehicles per draw on Z4/Z5 pass after 12600 s, outside the window.
- **Where.** On the 13 healthy panel draws, entry 44532531 is the rank-1 hotspot in 12 of 13 (one-sided
  Clopper-Pearson lower bound 0.684), and the merged corridor 24483344#0 + #1 is in the top 5 in 13 of 13.

These misfits are relative to the model, and the excess is a distance to one day's counts, not field truth. Counts
alone do not say whether timing, supply, capacity or control causes them (M2 protocol v3, section 8).

### 2.3 The parent is fragile, and candidate F2 was associated with no S01 precursor in 16 of 16 draws (exploratory)

- The census of the 106 M2 runs found 7 unhealthy runs at two sites: **S01**, 2 gridlocks rooted on 603103444_1 (a
  wrong-lane vehicle and its yielding follower wait on each other, and with teleport off the jam spreads ring-wide);
  **S02**, 5 side collisions with collision teleports, one signature at the -186821026_3 fork inside 315029332.
  **S03** is a latent trap at LSA535 link 5 that the lane-section hold masks (0 of 46 parent draws), and S04 is
  minor.
- In the 46 parent draws used as the reference arm, 4 are unhealthy (3 S02 collisions, 1 S01 gridlock), and 22 show
  the S01 wrong-lane standstill precursor.
- **Candidate F2:** the precursor was absent in 16 of 16 draws against 22 of 46 (exact one-sided Wilcoxon-Mann-Whitney
  p = 14430/62755763, about 0.00023). The 16 draws were all healthy, with no new failure site and emergency brakings
  at the parent's per-run level. N1-N4 and H3 held against both C3 parent arms. The ms4 fair CRPS was lower by 31.4
  and 49.6 than the two parent arms; that is a screen statistic on public keys, with no rate and no pre-registered
  test, and the 49.6 against the C3 reference is driven by that arm's gridlock draw c3-reference-03 (results,
  section 4.3).
- **Limits.** The label is `diagnostic_only`, because the static gate screened candidate F2 `dynamic_hard`. The p
  value is exploratory, not selection-adjusted: the census on the same historical draws selected the site and the fix.
  "No calibration regression detected" failed formally only through the C3 reference arm's own gridlock (T3 below).

Fragility work therefore comes before structural calibration work. S01 spill-back reaches the upstream detectors
0119Z4, Z5 and Z15, and the site diagnosis expects a fix at S01 to shift the downstream 0119Z11 to Z13 bins earlier,
where the twin now runs late. Part of the hour-scale misfit may therefore be fragility-driven. That is a hypothesis,
and M4 does not test it: the new-baseline panel in M4.1 reports a descriptive per-field comparison on those six
fields, with no rate, before M4.2 picks its targets.

### 2.4 Five tool limits

| id | limit | evidence | M4 needs |
|---|---|---|---|
| T1 | Junction-local re-derivation reshapes untouched internal paths, and findings are compared by link index and internal-lane id | Candidate F1: 5 retained paths move by 2.50-3.88 m, 3 wait points by 1.00, 1.97 and 3.47 m, 11 paths exceed 1 mm, 24 of 27 internal-lane chains are renamed. Its 10 INV-AUDIT witnesses are 4 new `path_width_transition_centerline_offset` findings, 5 parent findings renamed by the renumbering and 1 parent finding with a new hash identity | preservation of untouched internal paths, or an explicit declaration of every change; finding comparison by stable movement identity; genuine geometry regressions still blocking |
| T2 | The absolute L3 subset rule rejects a candidate for a remote stochastic collision under a chaotic parent | Candidate F2: one junction collision with a collision teleport on `:260305243_0_1`, about 0.6 km outside the edit scope, on routeability seeds 7 and 97; collisions carry no request identity, so L3 can never pass them | deterministic route failures stay hard; remote collision outcomes judged by a rate bound with an owner-set materiality margin, pre-registered and checked on fresh draws. This reverses a design v2 rule (OD3). The round-2 L3 rejection of candidate F2 stays as recorded |
| T3 | The H2 health rule fails every candidate when the reference arm holds the parent's own failure | C3 reference draw c3-reference-03 is a parent S01 gridlock, a class outside the panel's {collision, teleport}; `m2_stage.health_step` tests classes on both arms, so the C3-reference stage was `unhealthy` for candidate F2, which had 0 unhealthy draws | the reference arm's own failure classes no longer fail the candidate; the candidate side stays as the owner-delegated decision fixed it (OD2) |
| T4 | S02 has no expressible fix | Each single `set_lane_count` intermediate on -186821026 or -142944318 creates a new fork or merge at 13870881, which INV-AUDIT rejects under one op per round | a compound corridor lane-count op judged on its final state only |
| T5 | The exact unpaired evaluator covers equal arms of at most 16 draws and small unequal designs only | `ensemble.unpaired_crps_test` refuses unequal arms above 20,000 relabelings, with no random fallback (a 16/15 split has C(31, 16) = 300,540,195), and equal arms above `MAX_UNPAIRED_DRAWS` = 16, so K = 17 is refused; the frozen gates exist for K = 8 and 16 only, and H1 requires equal arms of the gate's K. Found by the review of this plan, not by a run | for M4.3's healthy-subset analysis and OD9's larger K: a stated method for unequal arms and for K up to 24, its known answers, an analysis budget, margins at those sizes and an outcome when too few healthy draws remain |

The fragility review also found a selector-validation gap in the declared-target lint (a signal target's selector
was not tied to its fact id). It was closed after the F2 run by commit `01f91817` (results, section 6) and is part
of the frozen M4.0 code state, not a separate part.

## 3. Structure and dependencies

| part | content | depends on | hands to the next part |
|---|---|---|---|
| M4.0 | tool fixes T1-T5, each with acceptance criteria and known-answer cases; one frozen, reviewed code state | M3 (`ae21563`), the fragility harness and its follow-up; OD0, OD1-OD3, OD8 and OD9 | the frozen code state and the pre-registered rule texts |
| M4.1 | no-agent structural Build loop on the fragility sites; adoption of a fixed parent; a C1-style panel on it | M4.0; owner budget (OD4); owner adoption sign-off (OD5) | the M4 baseline (fixed or original), its 14-draw panel, the gate artifact `m4-gate-<sha8>` |
| M4.2 | hour-scale structural candidates for the Z11-Z13 and Z4/Z5/0535Z4 corridors, qualified with hour-scale positive controls, checked at confirmation scale with a known positive per admitted family (recommended), screened with the M2 v3 ensemble gate on ms4 | M4.1's baseline and gate artifact; the ms4 re-check on that baseline; OD0 (c); OD6 | the known-positive check record of each admitted family; one frozen final candidate, or `no_candidate` |
| M4.3 | the append-only trial log (opened at the start of M4.1) and one confirmatory comparison on fresh secret keys | the frozen final candidate and its family's check record; K (OD9); optionally a second count day (OD7) | the M4 outcome: `accepted_for_review`, `not_confirmed` or `no_candidate`, with its persistence label |

A part starts only after its predecessor meets its acceptance criteria. A part that fails stops the milestone at
that point and hands the decision to the owner; nothing is relaxed automatically.

**M4 is complete** when M4.0 is accepted, M4.1 has ended with an adoption decision (a fixed baseline or none), M4.2
has ended with one stop_reason, and M4.3 has recorded exactly one outcome, all of it in the trial log.

## 4. M4.0: tool fixes

**Common acceptance for M4.0.**
- Each fix shows its required behaviour on the known-answer and synthetic cases listed below. The M3 known-answer
  harnesses still give their recorded results: K1 (46 cases, 9 of 9 checks) and K2 (8 cases, 43 of 43 checks), with
  any change in their saved hashes explained by identity fields only.
- One frozen code state, accepted by an independent read-only review, judges every M4.1 to M4.3 run.
- Every changed rule is written down before any M4.1 run. Each text states that it was written after C3 and the
  candidate-F2 run. Its replay on recorded cases checks the implementation and is not evidence. **No recorded label
  changes:** C3 stays failed, candidate F2 stays `diagnostic_only` and `dynamic_hard`, and candidate F1 stays
  `g0_rejected`.

**Budget.** 0 Calibrate runs. The SUMO work is static-gate work only: netconvert materializations (about 5 s each),
profile builds (about 47 s each with 10 routeability seeds, from the round-2 wall log), the T2 routeability panels
(about 1,000 audits at 200 draws per arm, estimated at about 2 s each, about 35 min at one worker; about 1,800 audits
and 1 h at 400 draws; measured and reported in M4.0), the T2 synthetic collision fixture and the cut-out probes
(under 2 s each). Disk use is small next to Calibrate runs and is measured. Engineering effort is in section 8.

### T1: untouched internal paths and stable movement identity

**Required behaviour.**
- After a junction-local re-derivation, every retained connection at a scope junction that is not an op pair keeps
  its parent geometry: internal-lane chain shape, length, speed and internal wait point, within 1 mm and 0.001 m/s.
  Where netconvert's re-derivation cannot be undone without breaking the junction, the change is declared instead: the
  goal lists each affected connection with its tolerance, the gate compares the materialized side effects
  (`materialized_side_effects`) with that list, and any undeclared side effect fails G0. Either way, every change
  inside the scope is reported, as every change outside it already is.
- Findings are compared by **movement identity**: junction, finding kind, from-lane, to-lane and endpoint side, with
  lanes mapped through the declared lane remap and link indices and internal-lane ids replaced by the movement they
  bind (signal findings keyed by controller and movement, not by index). The old-to-new link map is netconvert's own,
  as `control_reindex.crosscheck_declared` already reads it.
- A renamed or re-indexed parent finding is inherited, not new. A renamed key can never mask a new finding, and a
  measured finding that grows still blocks.

**Acceptance and known-answer cases.**
1. *Renumbering only.* A synthetic junction where one unused link is deleted and no geometry changes: 0 new findings,
   with every retained finding matched by movement identity across the renumbering.
2. *Genuine regression.* The same junction with an in-scope endpoint moved by 2 m: the finding stays new and G0
   rejects.
3. *Masking.* A new finding on a renamed lane is still reported new.
4. *Candidate F1 replay.* The 6 renamed and re-hashed witnesses resolve as inherited. The 4
   `path_width_transition_centerline_offset` findings either disappear because the paths are preserved (the 5
   inherited exit gaps then stay inside the 25 inherited structural failures, with no measure grown) or remain and
   block. Whatever F1's own G0 outcome, it is a result, not an acceptance requirement of T1.
5. *Candidate F2 replay.* G0 still passes, with the same 0 audit witnesses. The three +0.332 m length changes on
   603103444_2/3/4 are either restored or declared.
6. *Op pairs.* Restoration never touches an op pair, and the core release audit is not weakened.

**Owner decision (OD1).** Preservation changes the scoped-edit restoration in `core/`, which `astra network
verify-edit` uses for every user. The alternative is the twin-layer declaration only. Which of the two lands?

### T2: deterministic route failures against stochastic collision outcomes

**T2 reverses a design v2 rule.** Design v2 made L3 a hard rule only: on every seed, with the same frozen trips, the
candidate's failing requests must be a subset of the source's; "new failures on any seed reject unconditionally"; and
the round-1 non-inferiority margin was dropped (design v2 changelog, entries MP4; section 12.1). Its known-answer case
M3(e) rejects 4 worse seeds, one of them with a new collision. T2 brings a margin back for one outcome class,
collisions away from the changed lanes, because collisions carry no request identity and candidate F2 met one on
unchanged geometry 0.6 km from its edit. Whether to make that reversal at all is owner decision OD3, before any of
its parameters.

**Required behaviour.**
- Routeability audits record each collision and collision teleport with its identity: lane, junction, time and the
  request ids of both vehicles.
- **Hard, as today.** Deterministic route failures: a request that arrives on the source and is unroutable, discarded,
  still waiting or running at the end, or not arrived without a collision on the candidate. Any one rejects (the
  subset rule).
- **Hard, new.** Any collision or teleport on a lane that the materialization changed: the op pairs, every connection
  in the T1 side-effect list and the internal lanes of the scope junctions.
- **Rate-based, new.** Every other collision outcome (a *remote* collision). Each routeability draw is a pair
  (frozen-trip seed, second variation key, for example a keyed departure jitter). The variation key matters only for the
  paired or stratified alternatives below, where two draws can share a seed. The rule bounds the candidate's per-draw remote-collision rate against the parent's on fresh draws
  by non-inferiority: the one-sided 95% upper bound of the rate difference, by the Newcombe hybrid score interval,
  must not exceed `delta_coll`. Lack of significance never passes a candidate.
- **Draw design, frozen before the M4.0 code freeze (OD3).** By default every arm gets fresh, independent draws. For
  each draw of each arm, the frozen-trip seed is drawn uniformly from the declared range 0 to 2^31 - 1 (SUMO's seed
  range), excluding every seed in the trial log's prior exposure, and the variation key comes from `os.urandom`. No seed and no key is shared between
  arms or reused within an arm, and all of them are committed by SHA-256 before the first audit. Each arm's
  remote-collision indicators are then independent draws with that arm's rate, and the two arms are independent, up
  to a finite-population effect of sampling a few hundred seeds without replacement from 2^31 values, which is
  negligible.
  The Newcombe interval for two independent proportions and the exact binomial pass probabilities below assume this
  design. Two other designs need different treatment; the owner may choose one of them instead, but only before the
  freeze:
  - shared seeds or shared variation keys between arms (common random numbers) pair the draws, so the bound needs an
    interval for paired proportions (for example a score interval for matched pairs), with pass probabilities
    recomputed under a declared within-pair dependence;
  - a fixed set of seed strata used by both arms stratifies the draws, so the bound needs a stratified interval, and
    the rate it bounds is a rate over those strata, not over the seed distribution.
- **`delta_coll` is a materiality choice, not a noise quantile.** The proposed value is 0.03: at most 3 more remote
  collision outcomes per 100 frozen-trip draws than the parent, the largest increase this plan proposes to treat as
  immaterial for a Build fix. It is a value judgement for OD3. The parent showed no collision on the 10 frozen-trip
  seeds of the round-2 static gate, so its own rate gives no anchor. The parent-versus-parent panels of acceptance
  case 1 do not set `delta_coll`: they measure the parent's rate and how often a candidate that changes nothing
  passes, and so they set the number of draws. If such a candidate would pass with probability below 0.9 at the
  measured parent rate (a plug-in value), the draws per arm go from 200 to 400 (OD3); `delta_coll` is never widened
  to fit the noise. This replaces the earlier proposal (Wald bound, `delta_coll` from the parent-versus-parent q99
  with a floor of 0.05).
- **Pass probabilities**, computed exactly over the binomial distributions of both arms, under the default draw
  design (independent arms, independent draws at a common rate within each arm):

| candidate rate against parent rate | earlier proposal: Wald, 0.05, 200 draws | Newcombe, 0.05, 200 | proposed: Newcombe, 0.03, 200 | Newcombe, 0.03, 400 |
|---|---|---|---|---|
| 0 against 0 | 1 | 1 | 1 | 1 |
| 0.005 against 0.005 | 1.000 | 0.998 | 0.94 | 0.999 |
| 0.01 against 0.01 | 0.998 | 0.987 | 0.80 | 0.98 |
| 0.02 against 0.02 | 0.965 | 0.938 | 0.60 | 0.88 |
| 0.02 against 0 | 0.89 | 0.63 | 0.24 | 0.31 |
| 0.03 against 0 | 0.61 | 0.28 | 0.059 | 0.044 |
| 0.04 against 0 | 0.31 | 0.095 | 0.013 | 0.0035 |
| 0.05 against 0 | 0.12 | 0.026 | 0.0023 | 0.0002 |
| 0.20 against 0.02 (candidate F2's 2 of 10) | below 1e-9 | below 1e-9 | below 1e-11 | below 1e-19 |

The first four rows are passes of a candidate that changes nothing (higher is better); the other five are passes of
a candidate that adds remote collisions (lower is better). Under the earlier proposal, a candidate that adds 0.03
remote collisions per draw against a parent at 0 passes with probability 0.61, and one that adds 0.04 with 0.31. The
Wald bound is anti-conservative near 0: at its own margin (0.05 against 0) it passes with probability 0.12. The
Newcombe bound passes with probability 0.059 at the proposed margin and 200 draws, close to its nominal 0.05. The
proposed default costs power when the parent rate is not near 0 (0.80 at 0.01), which is what 400 draws are for.
These probabilities, and the Newcombe bound's nominal level, hold for the default draw design only; a paired or
stratified design needs its own interval and its own table.

**Acceptance and known-answer cases.**
1. *Parent against parent.* Two independent parent panels of 200 draws each, under the frozen draw design, measure
   the parent's per-draw remote-collision rate and its parent-versus-parent difference, and fix the number of draws
   as above. A third check, on fresh draws at that number per arm, runs the parent against a null-effect
   re-materialization through the same plan path; it must pass. That pass is one decision on fresh draws. Any
   false-reject frequency obtained by resampling recorded draws (with a frozen, recorded resampling seed) is reported
   with its Clopper-Pearson interval labelled *conditional Monte Carlo uncertainty*: it describes the resampling
   error given those recorded draws, not the rule's false-reject rate on new parent draws.
2. *In-scope collision.* A synthetic fixture verified to collide: a junction with two crossing internal paths that
   are both green and have no foe relation (a constructed right-of-way error), with frozen trips timed to reach the
   crossing together. It counts as a known answer only after a recorded routeability run shows a collision on a
   changed internal lane in at least one declared draw on the mutated fixture and none on the unmutated one; T2 must
   then reject it through the hard in-scope rule. The S02 fork is not used: three cut-out probes did not reproduce its
   collision.
3. *Remote rate.* Synthetic audit records with a remote collision rate above the bound reject; records at the
   parent's rate pass.
4. *Deterministic failure.* Deleting a connection that a frozen request needs rejects with that request's identity,
   whatever the collision rates.
5. *Design v2 cases.* The three synthetic L3 cases of M3(e) stay rejected: 4 worse seeds with one new collision, 0
   better and 6 ties; one seed with 1-2 lost arrivals and no collision; one seed where the candidate fixes a failing
   source request but breaks another. In the first, the worse seeds without a collision still reject through the
   hard deterministic rule. Its collision seed alone is re-labelled explicitly in the rule text: it rejects through
   the hard in-scope rule when the collision is on a changed lane, and is otherwise judged by rate. That re-labelling
   is the reversal OD3 accepts or refuses.
6. *Replay.* The recorded candidate-F2 audits on seeds 7 and 97 classify as remote collisions at junction
   260305243 with full identity. The round-2 static-gate record is not rewritten; candidate F2 is re-judged only under
   a new goal version in M4.1.

**Claim.** Unchanged remote geometry does not show that an edit cannot change collision exposure. That is why the
remote rule bounds a rate instead of exempting distance. A pass means that no increase larger than `delta_coll` was
detected on the declared draws. It is not a collision-rate claim and not a safety claim. T2 is less strict than
design v2's L3 for remote collisions: under the proposed default, a candidate that adds 0.02 remote collisions per
draw passes with probability 0.24 to 0.31. That is the cost of not rejecting every candidate for a remote collision
under a chaotic parent.

### T3: health compared between arms, reference side only

**Required behaviour.** H2 stays reject-only, and the frozen gates stay as they are for every run bound to them; the
revised rule ships only as a new gate version with its own SHA-256. The default changes only the reference side of
M2 amendment A2.1. The candidate side stays as A2.1 and the owner-delegated decision "Health rule for candidate
gridlocks" fixed it: keep the conservative A2 rule, with no outcome-informed relaxation (section 12.3).
1. The class clause applies to the **candidate arm only**. The admitted classes are the C1 panel's: collision and
   teleport. A candidate draw with any other class rejects: a gridlock (with the vehicles not inserted or not arrived
   that follow from it), a stall without gridlock, a discarded vehicle, a jam teleport or a native failure.
2. A class that only the reference arm shows, outside the admitted set, is reported separately as a finding that the
   parent panel is incomplete. It never fails the candidate.
3. The count clause of A2.1 (ii) is unchanged. As A2.1 states it, it counts only draws with an admitted class
   (collision or teleport), on both arms, so a reference draw with another class does not widen the candidate's
   allowance. The candidate arm's such draws may exceed the reference arm's by at most 2 at K = 16 (1 at K = 8). At
   the pooled parent rate of collision draws, 3/46, two equal-rate arms of 16 exceed that margin with probability
   0.035. It is a materiality margin, not a test, and because guards only reject, a false reject costs power, never
   validity.
4. Failure identities (class, site, draw) are recorded for both arms. A candidate class that the reference arm also
   shows is then compared by site, not by class name alone; a candidate failure at a site that no parent draw shows
   is already `new_failure_site` in the fragility decision.
5. The plausibility of each arm's count at the parent's rate stays reported (`unhealthy_plausibility`), and
   `rare_events` stays report-only.

**Cost of the default.** A candidate that does not change the parent's gridlock rate (1 in 46 draws) gridlocks at
least once in 16 draws with probability about 0.30, and then fails H2. That is power lost, never validity. For an
adopted S01 fix the rate is expected to be lower, but no rate is claimed.

**Alternative for the owner (OD2): admit gridlock on the candidate arm.** This would reverse the owner-delegated
decision for fresh runs, after C3 and the candidate-F2 run had been seen. If the owner chooses it, the admitted set
adds gridlock, with not_inserted and not_arrived admitted only as consequences of an all-stopped gridlock, and
per-family count margins replace the total margin as part of the rule:
- collision family (collision, teleport): at most 2 more draws than the reference at K = 16 (3/46; equal-rate arms
  exceed it with probability 0.035);
- gridlock family: at most 1 more at K = 16 (1/46; 0.034); for a candidate that targets S01, no more gridlock draws
  than P (margin 0; an equal-rate candidate exceeds it with probability 0.22).

**Acceptance and known-answer cases** (under the default; the expected output under the OD2 alternative in
brackets).
1. *Reference-only class.* When the reference arm shows a class outside the admitted set and the candidate shows none,
   H2 passes on the candidate, and the class appears in the parent-panel finding. In the candidate-F2 replay, with
   the C1 panel's classes as the admitted set, H2 against the C3 reference passes (0 collision draws against 2), and
   c3-reference-03's gridlock is reported as that finding [H2 passes: collision draws 0 against 2, gridlock draws 0
   against 1; no parent-panel finding].
2. *Unknown candidate class.* A candidate draw with a stall without gridlock fails H2 [the same].
3. *Frozen gates.* `m2-confirmation-K8` and `m2-confirmation-K16`, their hashes and the current `health_step`
   behaviour stay reproducible for every run bound to them.
4. *No relabelling.* Candidate F2's `unhealthy` stage label against `c3_reference` stays as recorded; the revised
   rule applies to fresh draws only.
5. *C3-positive replay.* H2 fails on the candidate's own gridlock, c3-candidate-16, and no longer on c3-reference-03,
   which becomes a parent-panel finding [collision draws 1 against 2 and gridlock draws 1 against 1, so H2 passes and
   the first failure becomes the H3 `post_window` guard (`guard`)]. The recorded C3 failure stands.
6. *Synthetic counts.* Candidate collision draws 3/16 against 0/16 reject (excess 3 > 2) [the same]. Candidate
   gridlocks 2/16 against 0/16 for P reject through the class clause [through the gridlock margin, 1 or 0].

**Owner decision (OD2).** The default amends A2.1 (i) on the reference side only. It was written after C3 and the
candidate-F2 run had shown the defect, and it applies only to fresh runs from M4.1 on.

### T4: compound corridor lane-count op

**Required behaviour.** One typed op sets the lane count of a chain of consecutive edges, here -142944318 and
-186821026, with declared 1:1 connections at every junction inside the chain, including their controller bindings.
It materializes once. G0, V, L2 and L3 judge only the final state, and no intermediate is ever materialized or judged.
For G-S02 this moves the 2-to-3 lane gain of the 0535 approach to the dead-end source 2003802536, so no lane forks
inside a junction. Lint refuses:
- a chain that is not a simple path;
- a junction of the chain outside the goal scope;
- a lane of the chain without a declared connection;
- any change to a lane that hosts an E1 detector.

**Acceptance and known-answer cases.**
1. *G-S02.* The INT diff at 13870881, 315029332 and 1975693623, compared by movement identity (T1), removes
   `ambiguous_target_lane_fanout:-186821026|3|-186821028:3,4` and adds no finding. Outside-scope fact changes are 0.
   The E1 lanes of 0535Z5 to Z8 are unchanged (INV-E1-LANE). All 8589 demand routes stay edge-continuous. The
   executed control of 315029332 is re-indexed and cross-checked against netconvert's link map.
2. *Synthetic chain.* A three-edge chain whose single-op intermediates each create a fork or merge, and whose
   compound final state creates none, passes.
3. *Still forked.* A chain whose final state still forks inside a junction is rejected by G0.
4. *Lint.* A non-consecutive or branching chain is refused.
5. *Inverse.* The inverse compound op restores V = 0 on a K2-style goal.
6. *Unchanged.* The single-op sequence S02-B1 then B2 is still rejected.

**Owner decision (OD1).** The op either enters `core/network_edit_operations.py` as a new scoped edit type, which
changes what `astra network verify-edit` accepts and needs its documentation in the same change, or stays a
twin-layer composite of the existing `set_lane_count` op applied in one materialization.

### T5: the permutation evaluator for unequal arms and larger K

**Why.** M4.3's healthy-subset analysis (section 7) and M4.1's guard sensitivity compare the draws without a health
failure, so their arms are in general unequal. OD9 may raise K above 16. The current evaluator supports neither (T5 in
section 2.4). T5 settles both before the M4.0 code freeze; until it does, neither analysis runs and K stays at 16.

**Required behaviour.**
- *Equal arms, the claim-bearing test.* The exact linear-form count (the meet-in-the-middle count over the integer
  coefficients of `unpaired_crps_contributions`) is extended to equal arms of up to K_max = 24 draws, that is
  2 x 2^24 subset sums. M4.3's primary test stays exact over all C(2K, K) relabelings, as A1.3 requires, and a Monte
  Carlo p never decides an outcome. OD9 chooses K from {16, 20, 24}. A K above 24 would need an amendment of A1.3,
  which this plan does not propose.
- *Unequal arms, report-only.* With unequal arm sizes the fair-CRPS difference is not linear in the labels: the
  within-arm normalizers differ, so the cross-arm distance sum stays in the statistic as a quadratic term. Neither the
  meet-in-the-middle count nor a dynamic programme over a linear statistic therefore applies to it. The healthy-subset
  analyses call the test and the guards directly, outside the stage evaluator, whose H1 keeps requiring equal arms of
  the gate's K.
  - When C(n1 + n2, n1) is at most 20,000, the existing direct enumeration gives the exact p.
  - Above that, the p is a Monte Carlo permutation p: B = 100,000 relabelings, each a uniformly random subset of n1 of
    the pooled draws, drawn independently by numpy's PCG64 generator from a 64-bit seed. The seed is frozen and hashed
    with the candidate before any M4.3 key is drawn. The p is (1 + hits) / (1 + B), a valid p value for the
    permutation null at any B.
  - Each record carries the seed, B, the hits and the 99% Clopper-Pearson interval of the exact permutation p given
    the hits (half-width about 0.0018 near p = 0.05). That interval is conditional Monte Carlo uncertainty about the
    permutation p of these draws, not sampling uncertainty about the arms.
- *Margins.* The M4.0 gate versions carry the original parent's margins at K = 20 and 24, by the formulas that built
  `m2-confirmation-K16` from the 14-draw panel k1..k14:
  - the t-margins t_{0.99,13} s sqrt(2 / K);
  - N1, the draw-level bootstrap q99 of the largest K-mean row growth;
  - N3 and N4, the larger of the block-bootstrap q99 at K and the exact K = 5 split q99 scaled by sqrt(5 / K), with
    N3's floor of 5 vehicles;
  The H2 count margin is not one of those formulas. At K = 8 and 16 it is the value M2 amendment A2.1 declared as a
  materiality margin from the parent's observed rate 1/14 (1 and 2; `HEALTH_MARGINS` in `twin/m2_stage.py`). For
  K = 20 and 24 this plan proposes a new declared rule, written after C3 and decided under OD9: the smallest margin
  that two equal-rate arms at the pooled parent rate 3/46 exceed with probability at most 0.05 (independent draws on
  disjoint fresh keys), which gives 3 at K = 20 (0.013; 2 would give 0.052) and 3 at K = 24 (0.020; 2 would give
  0.068). The rule matches A2.1's declared value only at K = 16 (2, with 0.035), by coincidence; at K = 8 it would
  give 2, not the declared 1.

  For unequal healthy subsets of sizes n1 and n2, the same formulas apply with sqrt(2 / K) replaced by
  sqrt(1/n1 + 1/n2), sqrt(5 / K) by sqrt(5 (1/n1 + 1/n2) / 2), and N1's bootstrap run at sizes (n1, n2). The formulas
  are frozen in M4.0; the values follow from the sizes, which are known only after the runs.
- *Too few healthy draws.* A healthy-subset analysis needs at least K/2 draws without a health failure in each arm
  (8 at K = 16). With fewer in either arm it is not computed, and its label is `unresolved` with the reason
  `too_few_healthy_draws`. In M4.1 the guard sensitivity then cannot support "no calibration regression detected".
- *Analysis budget.* At most 15 minutes and 4 GB of memory per test at one worker, measured in M4.0 on the largest
  allowed cases: equal arms at K = 24 and a 24/23 Monte Carlo test. A K whose exact count exceeds the budget is
  removed from OD9's choices before the freeze.

**Acceptance and known-answer cases.**
1. *Recorded values.* At K = 16 the extended count reproduces the recorded test I values exactly: C3's T = -7.745 and
   p = 0.352, and the fragility check's T = -31.383 with p 0.154 and T = -49.558 with p 0.041 (results, section 4.3).
2. *Linear form against enumeration.* For equal arms of 2 to 8 draws on synthetic counts, the linear-form count equals
   the direct enumeration of the fair-CRPS difference over every relabeling.
3. *Counts at large K.* For K = 17, 20 and 24, on synthetic draws with small integer counts, the meet-in-the-middle
   count equals an independent dynamic-programming count of the size-K subsets by coefficient sum.
4. *Monte Carlo against exact.* On unequal cases that direct enumeration can still check in a test (for example 6/5,
   9/7 and 10/8 arms), and on the recorded C3 arms at 16/16 run through the Monte Carlo path, the exact p lies
   inside the 99.9% interval for each of 10 seeds frozen in the test. A miss fails the case and is investigated; the
   seeds are never changed to pass. The same seed reproduces the same p.
5. *Refusals.* K above K_max, an arm of fewer than 2 draws, and a Monte Carlo p for the claim-bearing test are
   refused.
6. *Margins.* Rebuilding K = 16 by the frozen margin formulas reproduces every formula-built margin of
   `m2-confirmation-K16` (`cd7db25e...`), and its H2 count margin equals A2.1's declared value (2). The K = 20 and 24 gates are new gate versions with their own SHA-256, and `m2-confirmation-K8`
   and `m2-confirmation-K16` stay reproducible.
7. *Too few.* Synthetic arms with 7 healthy draws of 16 in one arm give `unresolved` with `too_few_healthy_draws`.

**Owner decision (OD9).** K for M4.3 from {16, 20, 24}. For an effect of M's plug-in size, K = 20 and 24 raise the
approximate power from 0.47 to about 0.55 and 0.61 (normal scaling of C2's P2 value), so even K = 24 leaves the
power near 0.6 for an effect of that size.

### Claim boundary of M4.0

M4.0 establishes rule behaviour on known-answer and synthetic cases only. It shows no edit to be correct, and no
dynamic claim follows from it.

## 5. M4.1: no-agent structural Build loop and a fixed baseline

**Machinery check.** Before it touches the Calibrate parent, the loop meets the design's F1-lite acceptance on 3 K2
goals: at least 2 of 3 reach V = 0, and the approach-level held-out facts (HO-B1, removed from the scored set at
goal-create) show 0 new mismatches. This is static only.

**Static stage.** Under the M4.0 rules (T1, T2 with fresh routeability draws per arm, T4), new goal versions are
created on the original parent:
- **A**, candidate F2 (S01) re-judged;
- **F1**, candidate F1 (S03) re-materialized under T1;
- **B**, G-S02 (S02) under T4, if T4 is accepted;
- **C**, the composite of every one of these that screens `improved`, as a verified-parent chain, formed only when
  it differs from A.

Each keeps the round-2 frame: declared fragility-fix targets (so V = 0 holds by construction), a re-indexed executed
control, a dirty source (`source_dirty`) with 25 inherited structural failures, and lane artifacts bound by hash. A
and C keep the model-justified S01 merge as a twin-layer exemption. All of these candidates are frozen and hashed
before the first dynamic run.

**Dynamic stage.** Arms run on disjoint fresh secret keys, K = 16 each, committed by SHA-256 before the first run as
in C3. The arms are P (the original parent), A, B and C. Each test is unpaired against P.

| comparison | primary metric (arm-neutral, judged by the parent's links on both arms) | test |
|---|---|---|
| A vs P | S01 wrong-lane standstill episodes on 603103444 | exact one-sided WMW, alpha 0.05 |
| B vs P | S02 collisions and the fork near-miss at 315029332, defined arm-neutrally and with its parent rate recomputed by the bound code | report-only: its power at 16 vs 16 is below 0.1 |
| C vs P | the S01 metric, with B's metric reported | exact one-sided WMW, Holm across the metrics that are not report-only (today only the S01 metric) |

- Candidate F1 has no dynamic test of its own: its target has 0 exposure in 46 parent draws. It enters only through
  C. Its structural evidence is the cut-out probe pair re-run on the T1 materialization (probe P09 traps 3 vehicles
  for 378 s or more on the parent; P14 traps none on the fix).
- **Power.** If A removes the precursor and the parent rate is 22/46, rejection needs 5 or more precursor draws among
  the 16 P draws (p 0.0217; 4 gives 0.0506). The probability of that is 0.945. For B, the fork near-miss occurs in
  about 0.12 of the census runs (13 of 106), which gives a power below 0.1 at 16 vs 16 even if B removes it. B's test
  is therefore report-only, B is never adopted on its own, and S02's case rests on the removed fork and on no-harm.
  B's arm only separates B's effect on health and the guards from A's inside C; OD4 may cut it.

**Acceptance of a step**, labelled `fragility_reduced`:
1. the static gate screens `improved` under T1 and T2;
2. the primary metric rejects (for C, Holm across its metrics that are not report-only). B, whose metric is
   report-only, cannot meet this;
3. H2 (T3) holds against P, and the new-site scan finds no site that P lacks;
4. H3 and N1-N4 detect no regression against P with the frozen K = 16 margins of the original parent (those of gate
   `m2-confirmation-K16`, `cd7db25e...`, carried into a gate artifact with T3 health). This means "within the
   original parent's noise", not non-inferiority.

**Sensitivity of the guards** (pre-registered, report-only). P's own failure draws inflate P's errors, so a guard
can hold against P only because of them. H3 and N1-N4 are therefore also recomputed on the draws without a health
failure in either arm, and the split of the ms4 fair-CRPS difference into its location and spread parts
(d_mean_tae = d_crps + d_half_gmd) is reported. The claim "no calibration regression detected" is made only when the
guards hold on all draws and on the draws without a health failure. The healthy subsets are in general unequal; their
margins and the rule for too few healthy draws are T5's. Conditioning on health changes the comparison: health is an
outcome of each arm, so a fix that changes the failure rate changes which draws are compared, and a guard result on
the subset is reported as it is, not attributed to fragility or calibration.

**Adoption.** The pre-registered order is C, then A: the first accepted candidate becomes the M4 baseline, and only
after a human signs it (OD5). On adoption, a new Calibrate goal binds:
- the new net and its re-indexed control;
- the unchanged demand, holds, E1 file, flags and targets;
- a recomputed baseline.

The original goal stays as it is. If no candidate is accepted, M4.2 runs on the original parent with its existing
14-draw panel, if the owner agrees (OD6).

**New-baseline panel.** The M2 C1 recipe runs on the adopted baseline: 14 fresh public keys. It gives:
- health rates with Clopper-Pearson intervals;
- the K = 5, 8 and 16 guard margins (N1-N4, H3) by the C1 formulas;
- the re-key sd and rho for the K rule;
- the ensemble profile and its corridor-merge rule;
- the **ms4 re-check**, by decision row 1 and A1.1 as pre-registered: excess above 0, jackknife z of at least 2, and
  every leave-one-draw-out excess above 0;
- a **descriptive per-field comparison** of the ms4 excess with the original parent's 14-draw panel on 0119Z4, Z5
  and Z15 (which S01 spill-back reaches) and 0119Z11 to Z13 (which the diagnosis expects a fix to shift earlier). It
  has no rate, tests nothing and only informs M4.2's targets (section 2.3).

If ms4 fails the re-check, main130 would become primary under A1.2, where the original parent's panels showed no
excess (headroom not demonstrated, section 2.1). M4.2 then stops for an owner decision instead. The values are
frozen into the gate artifact `m4-gate-<sha8>` before any M4.2 run.

**Stop and outcome.** The loop ends with one stop_reason:
- `solved`, when a candidate is adopted;
- `escalated`, when a step is not expressible or a required M4.0 rule is missing;
- `stalled`, when no candidate is accepted;
- `budget_exhausted`.

**Budget.**
- Static: about 4 profile builds with 200-draw L3 panels per arm, about 55 min estimated (about 1.8 h at 400 draws).
- Dynamic: 64 Calibrate runs plus 4 spares for crashed or timed-out runs only, about 1.8 h and 9.9 GB. It drops to
  48 runs without B, and to 32 when only A remains.
- Panel: 14 runs plus 1 spare, about 23 min and 2.2 GB.
- M4.1 total: 78 runs, about 2.2 h and 12.0 GB.
- Optional, only with OD4: an F1 exposure stress check, a diagnostic goal with the 0535Z8 lane-section hold removed
  so that the parent's trap occurs, 8 + 8 runs (27 min, 2.5 GB). It does not enter any decision.

**Claim boundary.** Under the supplied model, and on fresh secret keys, draws of the adopted baseline show fewer of
the pre-registered fragility events than draws of the original parent, by an exact test on an arm-neutral metric.
Within the original parent's noise, on all draws and on the draws without a health failure, no calibration
regression was detected. It does **not** show:
- that the edit is the real lane layout (the S01 merge has no field evidence);
- any traffic-safety property;
- a gridlock or collision rate;
- promotion, which needs a human (dirty source, `source_dirty`).

## 6. M4.2: hour-scale structural candidates

**Objective and gate.** The M2 v3 ensemble gate on ms4 against the real 2026-10-19 counts, with the margins of
`m4-gate-<sha8>` and T3 health:
- g = 10, alpha = 0.05;
- screening at K = 5 on panel keys k1..k5 with the baseline cached, which carries no rate;
- the S branch report-only;
- the L-type backlog a flag that never blocks.

**Families.** One class per goal (INV-ONE-CLASS). Each family needs its own known answers.
- **Capacity.** Typed Build ops from the existing scoped-edit types (lane count, lane connections) and T4, scoped to
  corridor elements. INV-PERM forbids opening bus or bike lanes, INV-E1-LANE forbids moving a counted lane, and the
  hold nodes stay fixed. Every enumerated op passes G0, V, L2 and the deterministic part of L3 on the 10 frozen-trip
  seeds; the T2 rate panel runs only for an op that goes on to a Calibrate run (a positive, a placebo or the op
  selected for a screen), before that run.
- **Control.** Changes to the executed programs with cycle, intergreens and phase order fixed:
  - split shifts at static programs;
  - durations of actuated phases that have no controlling detector. These 10 phases run at minDur = 5 s; 3 of them
    are at the sites (266197525 phases 0 and 2, 315029332 phase 3).

  This family brings forward the control-class goal that design v2 deferred beyond M5 (section 12.1). It gets its
  own goal and its own known answers, the positives and negative controls below, which play at M4.2's smaller scale
  the role of the control-class known-answer tier the design asked for. Whether it runs at all, and whether field
  static programs such as LSA119 or LSA535 may change, is OD6. A changed field program is a twin-fit hypothesis
  checked against its OCIT-C source with `core/ocit_c_programs.py`, never a field claim.

**Targets**, re-derived from the new-baseline ensemble profile. An element is eligible only when its top-5 frequency
has a one-sided Clopper-Pearson lower bound of at least 0.5 over the 14 draws.
- Corridor Z11-Z13: entry 44532531 and the S01 chain downstream of it.
- Corridor Z4/Z5: 24483344#0 + #1, the approach to LSA119.
- 0535Z4: the co-anchors 551303424 (into 205069138), -186713427 (into 14398547), both hold nodes with their holds
  fixed, and 551303409; and the 315029332 / 1975693623 pair that starves 0535Z4 through the S02 micro-edges.

**Hour-scale positive and negative controls** (twin of twin, per family). Under OD0 (b) this qualification takes the
place of design v2's exit criterion for the M4.2 loop. It is weaker (section 2.1): it shows only that the gate can
see an op of that size at screening scale, and the confirmation-scale check below adds what it lacks.
- For each positive, op* is drawn from the family's admissible ops at the target corridors by a fixed sampler seed,
  without looking at residuals: two distinct ops, where possible a moderate and a large one. The truth is
  y* = baseline + op* on 8 fresh keys, disjoint from the panel.
- **Qualification**, M2 v3 section 6 on ms4 against y* (section 12.2):
  - paired ED of at least g, with swap p of at most 0.05, on 6 panel keys;
  - every leave-one-key-out ED at least g/2;
  - a mechanistic check (the op's corridor flows move in the declared direction) on at least 5 of 6 keys;
  - plug-in power at K = 16, reported.
- **Attribution** (reported): does the baseline-versus-y* ensemble profile name op*'s element among its top-3 lead
  elements? This is the attribution check that M2 left pending.
- **Negative controls:** 2 placebo ops of the family at quiet fields (7 of 13 fields are exact or within 1 vehicle on
  every B0 draw). They must not screen `improved`.
- **A family is admitted** when at least one positive qualifies and no negative control screens `improved`. A family
  without a qualified positive is not looped, because its gate power at that scale would be unknown.

**Confirmation-scale known-positive check** (recommended, after qualification and before the loop; OD0 (c) may skip
it). Qualification did not show power at the confirmation gate: M qualified at C2 while none of its 28 K = 6 primary
screens was `improved` (section 2.1), and it then failed C3. For each admitted family, the qualified positive op* with
the larger qualification ED runs as a twin-of-twin positive at confirmation scale:
- the M4 baseline and baseline + op* on 16 + 16 fresh secret keys, committed by SHA-256 before the first run as in
  C3, judged by the A1.3 test at K = 16 against op*'s 8-draw truth y*, with the margins of `m4-gate-<sha8>` and T3
  health;
- its plug-in power, from the qualification draws, is recorded in the trial log before its keys are drawn;
- its keys enter the trial log and are excluded from every later confirmation.

Outcome per family:
- `improved`: the family is looped. The record shows that the A1.3 test at K = 16 confirmed one positive of that
  family at this scale: one demonstration, not a power estimate and not a rate.
- Not `improved`: the family's power at the confirmation gate is not demonstrated. It is looped only if the owner
  decides so (OD0 (c)), and a later `not_confirmed` for a candidate of that family says nothing about that
  candidate's effect.

**Loop** (no agent). It enumerates the admissible ops at the eligible elements, at most 6 per corridor and family. A
Build op passes the static gate first; a control op passes control lint and a load check. The ops are ranked by a
declared predictor, the queue model, used for ranking only: C2 measured it about three times optimistic. One op per
round goes through the screen.
- An `improved` screen becomes the parent of the next round, and its 5 screen draws become that round's cached
  parent draws.
- At most 2 rounds per corridor.
- stop_reason: `stalled` (no improved screen in a round), `not_expressible` or `budget_exhausted` (60 screen runs).

**Acceptance.**
- Each family's qualification record (positives, negatives, power, attribution) is in the trial log, with its
  known-positive check record or the owner's decision to skip it.
- The loop ends with one stop_reason, and every screened candidate is in the trial log with its keys.
- Every advanced step passed `m4-gate-<sha8>` with no H or N failure, and untranslatable ops are logged
  `not_expressible`.
- The final candidate, or `no_candidate`, is frozen by hash with its target, objective, g, K and gate artifact before
  any M4.3 key is drawn.
- No improvement is required.

**Budget.**
- Qualification: 2 families, each with 2 positives (6 + 8 runs) and 2 negatives (6 runs): 80 runs plus 4 spares,
  about 2.2 h and 12.3 GB.
- Known-positive checks (recommended): 32 runs plus 2 spares per admitted family, at most 64 runs plus 4 spares,
  about 1.8 h and 9.9 GB.
- Screens: at most 60 runs plus 3 spares, about 1.7 h and 9.2 GB.
- M4.2 total: 204 runs with two checks, about 5.7 h and 31.4 GB (172 runs with one check; 140 runs, about 3.9 h and
  21.6 GB, without checks).
- Static, capacity family only: at most 36 deterministic static gates (6 ops by 3 corridors by 2 rounds, about 47 s
  each, about 28 min) and at most 10 T2 rate panels (2 positives, 2 placebos and 6 screened ops, 200 draws per arm,
  about 2.2 h); about 2.7 h at one worker, estimated.

**Claim boundary.**
- Screens on panel keys and qualification on exposed keys are exploratory and carry no rate.
- A capacity or control change that improves ms4 is a hypothesis about the model fit, not evidence of the real
  layout or signal program.
- The counts do not identify the cause.
- Nothing extends beyond 2026-10-19, and M4.2 can fit that day's noise.

## 7. M4.3: trial log and the confirmatory comparison

**Trial log** (`twin/trials.py`, as the design specifies). Append-only JSONL at
`E:\twin-store\trials\<goal_id>.jsonl`, one row per judged candidate. Each row holds:
- the candidate hash, goal id, gate artifact SHA-256 and verifier commit;
- every draw-key fingerprint and routeability draw used;
- the status and screening label, and the stop_reason when a flow ends;
- the SHA-256 of the previous row.

Secret keys are never written, only their fingerprints. The log opens before the first M4.1 judgement. It imports
the prior exposure read-only, so that "fresh" excludes it: the M2 ledger (106 runs), the fragility ledger (16 runs)
and the round-2 routeability seeds. The chain check runs before each confirmation.

**Power before the keys.** Before any M4.3 key is drawn, the plug-in power of the M4.3 test at K = 16 for the frozen
candidate is computed from its existing draws (its M4.2 screen draws, or its M4.1 arm when the final twin is the
adopted baseline) and the reference panel, by the plug-in approximation used for C3, and recorded in the trial log.
Below 0.5, a `not_confirmed` outcome is reported as uninformative about the candidate's effect, and the owner may
raise K to 20 or 24 (OD9, T5) before the keys are drawn. K never changes after that.

**Known-positive check.** It runs before the M4.2 loop (section 6), not here. When the final candidate's family has no
`improved` check (it failed or was skipped), or when the final twin is the adopted M4.1 baseline, which no family
check covers, a `not_confirmed` outcome is reported as uninformative about the candidate's effect, and the owner
decides whether M4.3 still runs.

**Confirmatory comparison** (exactly one per M4 campaign).
- **Candidate:** the final M4 twin. That is the last accepted M4.2 candidate, or the adopted M4.1 baseline if M4.2
  accepts nothing.
- **Reference:** the original parent, which is the design's original baseline.
- **Arms:**
  - the reference parent, the final twin and a parent negative control, on disjoint fresh secret keys, K each
    (16 unless OD9 raises it): 48 runs plus 3 spares at K = 16;
  - the adopted M4.1 baseline as a fourth arm, required when M4.1 adopted a baseline and M4.2 produced the final
    candidate (K runs plus 1 spare). It separates the gain of the Build fix from that of the M4.2 candidate and does
    not enter the outcome.
- **Keys.** They come from `os.urandom` and are committed by SHA-256 before the first run, with the candidate, target,
  objective, g, K and gate frozen and hashed first, as in C3.
- **Test** (M2 v3 with A1.3): the exact one-sided permutation over all C(2K, K) relabelings (C(32, 16) at K = 16)
  of the fair-CRPS difference on ms4 against the real counts, by T5's exact count. `improved` holds when p is at
  most 0.05 and T is at most -g, with H1, H2 (T3), H3 and N1-N4 holding. The negative control must not be
  `improved`. The margins are the original parent's margins at K (T5 for K = 20 and 24), because the reference arm
  is the original parent.
- **Sensitivity analysis** (pre-registered, report-only; it never changes the outcome, because unhealthy draws stay
  in the decision under A2.2):
  - the split d_mean_tae = d_crps + d_half_gmd for the final twin against the reference, against the negative
    control and, when it runs, against the baseline arm;
  - the same permutation test on the draws without a health failure of any class in either arm, by T5: exact when
    the arms stay equal or C(n1 + n2, n1) is at most 20,000, else the Monte Carlo p with its frozen seed and its
    interval; not computed when either arm keeps fewer than K/2 such draws;
  - when the baseline arm runs, the final twin against it and it against the reference, by the same test.
- **Conditioning on health changes the comparison.** Health is an outcome of each arm, so the subset test compares
  the arms' healthy draws, which is a different comparison from the confirmation: a Build fix that changes the
  failure rate changes which draws are compared, and it can also shift the healthy draws themselves. The subset
  result therefore describes whether the observed gain persists without the failure draws. It does not attribute
  the gain to fragility or to calibration.
- **Outcome:** `accepted_for_review` (improved, and the negative control not improved), `not_confirmed`, or
  `no_candidate`. The confirmation record also carries one descriptive persistence label from the healthy-subset
  test of the final twin against the reference, with its T, p and draw counts:
  - `persists`: on the subset, T is at most -g and p is at most 0.05 (for a Monte Carlo p, the upper end of its 99%
    interval is at most 0.05);
  - `not_observed`: on the subset, T is 0 or above, so no gain is observed without the failure draws;
  - `unresolved`: every other case, including T at most -g with p above 0.05, T between -g and 0, a Monte Carlo
    interval that contains 0.05, and too few healthy draws (`too_few_healthy_draws`).

  No label names fragility or calibration as the source of the gain: `persists` does not make the gain a calibration
  gain, and `not_observed` does not make it a fragility effect.
- **Second count day** (HO-C3, 2026-10-12, a Monday like 2026-10-19). Only if the owner freezes it before any key is
  drawn (OD7): demand is refitted with the same solver and budget once for the original structure and once for the
  final structure, then both are run and compared. That needs its own pre-registration and budget. The claim would be
  that the structure-plus-refitting procedure transfers better, not that the structure is field-correct.

**Acceptance.**
- The chain check passes, and every M4 judged candidate and key is in the log.
- The confirmation keys are absent from the log and from both earlier ledgers.
- The plug-in power is recorded before the keys are drawn.
- Exactly one calibration confirmation runs, and its outcome, with its persistence label, is recorded.
- Rejected trials leave no commit and no benchmark bundle.

**Budget.** At K = 16, 48 runs plus 3 spares, about 1.3 h and 7.4 GB; with the required baseline arm, 64 runs plus 4
spares, about 1.8 h and 9.9 GB. If OD9 raises K, three arms take 60 runs at K = 20 (about 1.7 h, 9.2 GB) or 72 at
K = 24 (about 2.0 h, 11.1 GB), and four arms 80 (about 2.2 h, 12.3 GB) or 96 (about 2.7 h, 14.8 GB), plus one spare
per arm. The healthy-subset analysis stays within T5's analysis budget.

**Claim boundary.**
- Under the supplied model, the final twin's ensemble rejects equality with the original parent's, with an observed
  fair-CRPS gain of at least g on ms4 against the 2026-10-19 counts. That is not a population gain of g, and the
  composite null "different but not better" is not controlled.
- The reference arm is the original parent, which gridlocks or collides in about 4 of 46 draws (at least one
  gridlock in 16 draws with probability about 0.30). A Build fix that removes failure draws can gain on ms4 without a
  better count fit in the healthy draws, and a fix can also shift the healthy draws. The persistence label describes
  the healthy subset only; no label attributes the gain to fragility or to calibration.
- No campaign-level error rate is claimed beyond the logged count of screening decisions and the decisions below,
  and each claim is read with that count.

**Decisions with an error rate.**

| decision | part | number, at most | nominal rate | what an error does |
|---|---|---|---|---|
| exploratory S01 test of the fragility check | before M4 | 1, done | 0.05, not selection-adjusted | a false fragility signal behind candidate A |
| T2 null-effect check | M4.0 | 1 | false reject, reported | a sound rule is rejected (power) |
| T2 remote-collision bound, one per static gate with a rate panel | M4.1, M4.2 | 4 + 10 | false pass about 0.06 at the margin (Newcombe, 200 draws, independent draw design) | a candidate with a material remote-collision increase passes |
| M4.1 primary tests (A, C) | M4.1 | 2 | 0.05 each | a false `fragility_reduced` |
| M4.1 test for B | M4.1 | 1 | report-only | none: no decision rests on it |
| M4.2 qualification (swap p of each positive) | M4.2 | 4 | 0.05 each | a family admitted with overstated gate power (power, not a false claim) |
| known-positive checks (OD0 c, recommended) | M4.2 | 0 to 2 | not a rate claim; one demonstration per family | power evidence only |
| M4.3 confirmation | M4.3 | 1 | 0.05 | a false `accepted_for_review` |
| M4.3 negative control | M4.3 | 1 | 0.05 that the parent-against-parent control is `improved` | a sound pipeline fails (power) |
| M4.3 healthy-subset persistence label | M4.3 | 1 | report-only | none: the outcome does not rest on it |

Screens (K = 5 on panel keys, including the negative-control screens of M4.2) carry no rate. The claim-bearing
decisions are the 3 tests that can produce `fragility_reduced` or `accepted_for_review` (0.15 together at most under
their sharp nulls) and up to 14 static-gate bounds, each of which can pass a material remote-collision increase with
probability up to about 0.06. No bound on the chance of at least one error is claimed below the sum of these nominal
rates.

## 8. Run budget and effort

Basis:
- about 100 s effective per Calibrate run at 3 workers (candidate F2: 16 runs in 22 min; C3: 48 runs in 71 min;
  individual runs 185-460 s);
- 154 MB per run (the fragility store holds 2.4 GB for 16 runs, the M2 store 16 GB for 106);
- routeability audits estimated at about 2 s each at one worker, and profile builds at about 47 s (round-2 wall log);
  M4.0 measures both;
- E: had about 100 GB free on 2026-10-26.

Spares are for crashed or timed-out runs only. Unhealthy draws are scored as they are and never replaced (A2.2).

| part | Calibrate runs planned (+ spares) | Calibrate wall time | disk | static-gate work (one worker, estimated) |
|---|---|---|---|---|
| M4.0 | 0 | — | — | netconvert, profile builds, about 1,000 routeability audits, the synthetic fixture and probes: about 0.6 h |
| M4.1 | 64 (+4) dynamic, 14 (+1) panel | about 2.2 h | about 12.0 GB | about 4 profile builds with 200-draw L3 panels per arm: about 0.9 h |
| M4.2 | 80 (+4) qualification, 64 (+4) recommended known-positive checks (0 if OD0 (c) skips them), at most 60 (+3) screens | about 5.7 h (3.9 h without checks) | about 31.4 GB (21.6 GB) | at most 36 deterministic static gates and 10 T2 rate panels: about 2.7 h |
| M4.3 | 48 (+3), or 64 (+4) with the required baseline arm, at K = 16 | 1.3-1.8 h | 7.4-9.9 GB | — |
| total | 330-346 (+19-20); 266-282 (+15-16) without checks | about 9.2-9.6 h (7.4-7.8 h) | about 51-53 GB (41-44 GB) | about 4.2 h |

The total compute is therefore about 13.4-13.8 h, or 11.6-12.0 h without the known-positive checks. At 400
routeability draws per arm (OD3), the static-gate work roughly doubles to about 8 h. For comparison: M2 used 106 judge
runs and the fragility check 16. Not counted: a larger M4.3 K (OD9: 12 more runs at K = 20 and 24 more at K = 24 with
three arms, 16 and 32 more with four), the optional F1 stress check (16 runs) and a second-count-day refit. T5's
analyses run no SUMO and stay within its analysis budget.

**Engineering effort**, estimated from the size of the modules each change touches (for example
`core/routeability_audit.py` 997 lines, `core/network_edit_operations.py` 1554, `twin/m2_stage.py` 911,
`twin/declared_targets.py` 718), not measured:

| item | work | days | new or changed lines, tests included |
|---|---|---|---|
| T1 | restoration or declaration of retained internal paths; movement-identity comparison of findings | 2 | about 600 |
| T2 | collision identities in the routeability audit; the rate rule; the parent panels; the synthetic fixture | 1.5 | about 500 |
| T3 | the new gate version and the health step | 0.5 | about 200 |
| T4 | the compound lane-count op, its lint and its re-index | 1.5 | about 500 |
| T5 | the exact count for equal arms up to K = 24, the Monte Carlo permutation p for unequal arms, the margins at K = 20 and 24 and at unequal sizes, and their known answers | 1 | about 300 |
| M4.0 known answers and review | the K1/K2 re-run, the replays and an independent read-only review | 1 | about 300 |
| M4.1 to M4.3 | the loop driver, the trial log, control-family lint and the sensitivity reports | 3 | about 800 |
| total | | about 10.5 | about 3,200 |

**Stop point.** If M4.0 is not accepted after 11 working days, about 1.5 times its estimate of 7.5 days, it stops
for the owner, who may drop T4 (and with it candidate B), keep T1 to the twin-layer declaration, limit T5 to K = 16,
or end M4.

## 9. Owner decisions

| id | decision | needed before |
|---|---|---|
| OD0 | revise the protocol after M2's failed exit criterion: (a) replace design v2's M4 by M4.0 to M4.3, and, as a separate choice, withdraw flow F2 (demand timing) or keep it under its own revised protocol; the M2 evidence (one failed confirmation at a plug-in power of about 0.47, one unqualified hour-scale op) is weak and does not show that the family cannot qualify; (b) accept M4.2's per-family qualification in place of the exit criterion for the M4.2 loop; (c) run the confirmation-scale known-positive check for each admitted family before the M4.2 loop (recommended, section 6), or skip it and run M4.2 and M4.3 on the A1.3 test, which has not confirmed a known positive (C3, power about 0.47); and whether a family whose check is not `improved` is looped | M4.0 |
| OD1 | T1 restoration in `core/` (affects every `astra network verify-edit` user) or twin-layer declaration only; T4 as a core scoped edit type with its documentation, or as a twin-layer composite | M4.0 code freeze |
| OD2 | T3: adopt the default (reference-side fix only, the candidate class clause kept with the C1 classes) as an amendment to A2.1 written after C3 and the candidate-F2 run, for fresh runs only; or admit gridlock on the candidate arm with per-family margins (collision 2, gridlock 1, and 0 for an S01 candidate, at K = 16), which reverses the owner-delegated decision "Health rule for candidate gridlocks" | M4.0 code freeze |
| OD3 | T2: whether to reverse design v2's hard-only L3 (changelog MP4, case M3(e)) for remote collisions at all; then its parameters: the draw design (fresh independent draws per arm proposed; shared seeds or keys, or fixed seed strata, need a paired or stratified interval), the interval (Newcombe for independent arms proposed), `delta_coll` as a materiality choice (0.03 proposed), the draws per arm (200, or 400 when a null-effect candidate would pass with probability below 0.9) and the scope of the hard in-scope rule | M4.0 code freeze |
| OD4 | the M4.1 budget (78 runs plus 5 spares); whether candidate F1 and G-S02 enter the composite; whether B's report-only arm runs; the optional F1 stress check | M4.1 dynamic runs |
| OD5 | sign the adoption of the fixed parent as the M4 Calibrate parent despite its dirty source (`source_dirty`, 25 inherited structural failures) and the model-justified S01 merge; optionally supply official MAP or engineering-plan evidence for 266197536, the only kinds the core release audit accepts for that merge | after M4.1's dynamic stage |
| OD6 | M4.2 scope: the admitted families, including the control family that design v2 deferred; whether field static programs may change; the positive-control draw; whether M4.2 proceeds on the original parent when nothing is adopted; what happens if ms4 fails the re-check | M4.2 |
| OD7 | freeze a second count day (2026-10-12) online, with access to the Hamburg SensorThings service, by a human outside any fence; without it, every M4 claim stays on 2026-10-19 | before M4.3 keys are drawn |
| OD8 | effort and compute: about 10.5 days of code with the M4.0 stop point at 11 working days; about 9.2-9.6 h of Calibrate runs with the recommended known-positive checks (7.4-7.8 h without), about 4.2 h of static-gate work and 51-53 GB on E: (41-44 GB without) | M4.0 |
| OD9 | keep g = 10 on ms4, K = 16 for confirmations and the S branch report-only; the K values T5 supports (16, 20, 24) with their margins; K for M4.3 from those values if its plug-in power is below 0.5 | M4.0 code freeze; K for M4.3 before its keys are drawn |

Correspondence locks, `--mode release` and every promotion remain human, as in the design.

## 10. What stays for M5

- **The agent loop.** The fenced hmz flow with a Claude actor proposing one typed op or one falsifiable hypothesis per
  turn, a Codex veto and then a human; flows F1 (Build) and F3 (Calibrate) with their stop rules (`class_exhausted`,
  `stalled`, `escalated`, `violations`, `budget_exhausted`; 100 SUMO runs or 8 h).
- **Sealing and isolation.** Sealed holdouts with secret-committed keys, held-out approach facts sealed against the
  actor, the isolation acceptance (a disposable clone, refs and worktree checks, Landlock negative tests), ledger
  views and the four schema exports.
- **Demand timing**, if OD0 (a) withdraws flow F2. It returns in F3 only with a family that qualifies at hour scale,
  on an objective whose excess the parent panel has shown, as ms4's in C1.
- **Ops M4 does not add.** `extend_boundary_edge` or `move_node` (the S02 insertion braking, S04), `bind_signal_link`
  (266197525), `share_tls_controller` (the two S01 signals that control no crossing movement), and `cell_transfer`.
- **Later still.** The `od_totals='free'` class, control-class goals beyond the families M4.2 admits, and a public
  `astra twin` command or MCP tool, which goes through the public-surface steps.

## 11. Claim boundary of M4

Everything in M4 is explore evidence under the supplied SUMO model: the Calibrate parent or its fixed successor, the
declared holds, the re-indexed `readme_fixed_A`, the P1 demand with the depart jitter, seed 104 and libsumo 1.27.1,
on one count day, 2026-10-19 05:30-08:00 UTC.

| M4 can claim | M4 cannot claim |
|---|---|
| fewer pre-registered fragility events on fresh secret keys for an adopted fix, by an exact test on an arm-neutral metric | that any edit is the real lane layout or signal program; any traffic-safety property; a gridlock or collision rate |
| no calibration regression detected within the original parent's noise, on all draws and on the draws without a health failure (guards only reject) | non-inferiority, or neutrality of a Build edit for calibration |
| at most one confirmed observed ms4 gain of at least g against the real counts, with its descriptive persistence label on the draws without a health failure and the logged decisions counted | attribution of the gain to fragility or to calibration, whatever the persistence label; a population gain, field correctness, OD identification, causal attribution, or generalization beyond 2026-10-19 unless a second day is frozen |
| measured rule behaviour on known-answer cases (M4.0) | a general error guarantee beyond the measured cases; promotion of any kind without a human |

## 12. Rules this plan relies on

The documents quoted here are committed as the dated snapshots linked at the top of this plan. Each snapshot is the
local archive record of 2026-10-25 or 2026-10-26 with a provenance note inserted after its title (line endings
aside), so the sha256 values in the headings below, which are the archive records', differ from the snapshots' file
hashes. The quotes are exact where marked, and the rest is a close summary.

### 12.1 Design v2 (sha256 `a73d563b...9357`)

- **M4 as designed, "Loops without an agent"**, after M2's exit criterion and M3's acceptance: (a) flow F2 on the
  275 case, demand timing only: it ends with one stop_reason and one outcome, every accepted step passed the frozen
  M2 gate artifact, every judged candidate and draw key is in the trial log with a passing chain check, the
  confirmatory comparison used keys absent from the log, untranslatable surrogate proposals are logged
  `not_expressible`, and rejected trials leave no commit and no benchmark bundle; (b) flow F1-lite on 3 K2 goals: at
  least 2 of 3 reach V = 0, and the approach-level held-out facts show 0 new mismatches; (c) no campaign-level error
  rate is claimed beyond the logged count of screening decisions and the single confirmatory test.
- **Gating.** "The Calibrate loops (M4 F2, M5 F3) wait for M2's exit criterion." M3 and F1-lite do not.
- **Exit criterion for the Calibrate loops** (M4 F2 and M5 F3): 0 of 12 validation-null accepts; placebo-on-real
  acceptance of at most 1 of 12; 4 of 4 large positives `improved`; at least 3 of those 4 pass the promotion rule.
  "Otherwise M2 stops, and nothing is raised automatically. A human revises K, the family, g_min or the grid under a
  new protocol version."
- **Campaign-level error.** In-loop decisions are screening decisions. The claim-bearing test is a single
  confirmatory comparison of the final candidate against the original baseline, on keys absent from the trial log.
- **L3, a hard rule only.** On every seed, with the same frozen trips, the candidate's failing requests must be a
  subset of the source's; a request that arrives on the source and runs, waits, is discarded, teleports, collides or
  is unroutable on the candidate rejects. Changelog MP4: "new failures on any seed reject unconditionally", and in
  round 2 "the non-inferiority margin is dropped". M3(e) requires three synthetic cases to reject: 4 worse seeds (one
  new collision), 0 better and 6 ties; one seed with 1-2 lost arrivals and no collision; one seed where the candidate
  fixes a failing source request but breaks another.
- **Control family.** Deferred: under the demand-timing goal every control candidate is invalid, and "that family
  needs a control-class goal with its own K3". A control-class goal is listed as not in M1-M5.

### 12.2 M2 v3 protocol and its decisions (decisions sha256 `48106d95...e942`)

- **Decision row 1, primary objective.** ms4, on condition that the 8 fresh parent draws of C1 alone show hour-scale
  excess (excess above 0 with z of at least 2); otherwise main130 becomes primary.
- **Decisions 3 to 6.** g = 10 on ms4 (totals13 guard floor 5 vehicles); confirmation K = 8 when the panel's CRN ratio
  rho is at most 0.35, else K = 16; no simplification claims (the S branch is report-only); the L-type backlog is a
  flag that never blocks.
- **A1.1.** The z of row 1 is the excess over its leave-one-draw-out jackknife SE on the 8 fresh draws, and the
  excess must stay above 0 after every leave-one-draw-out deletion. It is an engineering screen, not an exact test.
- **A1.2.** If ms4 fails C1, main130 replaces it everywhere: qualification ED, rho and the K rule, g, and the guards.
- **A1.3.** The confirmation is unpaired: candidate and reference run on disjoint fresh secret keys, K each. Test: the
  exact one-sided permutation over all C(2K, K) relabelings of the fair-CRPS difference against the target; accept
  if and only if p is at most 0.05 and T is at most -g. The claim is limited to rejecting equal distributions with an
  observed gain of at least g against this target; "it is not a population gain of g, and it does not control the
  composite null 'different but not better'".
- **A1.8.** Qualification filters are exploratory screens on exposed keys; C3 is an end-to-end demonstration, not a
  validation of power or false-accept rates; "chaos floor" is a statement about this parent and this day.
- **A2.1** (written after C1). Health becomes a comparison with the reference arm: (i) no run may show a failure
  class the parent panel never showed (stall, gridlock, vehicles not inserted or not arrived at the end, discarded
  vehicles); (ii) the candidate arm's count of unhealthy draws (collision or teleport) may exceed the reference arm's
  by at most 1 (K = 8) or 2 (K = 16), a materiality margin, not a test. H2 failures reject outright.
- **A2.2.** Unhealthy draws stay in the ensemble and are scored as they are; spares are for crashed or timed-out runs
  only. **A2.3.** The parent's unhealthy frequency is reported with every confirmation.
- **Protocol section 6, positive qualification.** On at least 6 panel keys: paired ED on ms4 of at least g with swap
  p at most 0.05; every leave-one-key-out ED at least g/2; the mechanistic check on at least 5 of 6 keys; power then
  measured against a truth ensemble of at least 8 fresh draws on keys disjoint from the panel.

### 12.3 Owner-delegated decision of the fragility check (in the repository)

The [owner-delegated decisions](ring1-fragility-owner-decisions-2026-10-26.md) record, in the row "Health rule for
candidate gridlocks": keep the conservative A2 rule and report gridlocks against the reference rate (1 in 46) as
well, because of "no outcome-informed relaxation". T3's default keeps that rule on the candidate side; the OD2
alternative would reverse it for fresh runs.

### 12.4 The M2 stages in brief

- **C1** (8 fresh parent draws k7..k14): ms4 stayed primary under row 1 and A1.1 (excess 105.5, jackknife z 2.43,
  leave-one-draw-out excess 90.9-139.8); main130 stayed at the chaos floor (ratio 0.99). The gate "all runs healthy"
  failed on a parent side collision (k14), which led to amendment A2.
- **C2** (22 runs): M (`cell_depart_shift:107:900`) qualified as a moderate positive; the corridor op X
  (`cell_depart_shift:101:2700`) failed qualification.
- **C3** (48 runs on secret keys committed before the runs): the stage gate failed. M was `unhealthy` at H2 (its own
  gridlock c3-candidate-16 and the reference's c3-reference-03), with T = -7.745 and p = 0.352 for test I at an
  approximate power of 0.47; the parent-against-parent negative control was also `unhealthy` at H2, through
  c3-reference-03. Gridlock appeared in the parent for the first time. C4 did not run.

## 13. Terms

- **A1.x, A2.x**: amendments to the M2 v3 decisions (section 12.2).
- **arm, draw, K**: an arm is a set of judged Calibrate runs of one net; a draw is one run under a keyed perturbation
  of the demand (the depart jitter of plus or minus 0.5 s); K is the number of draws per arm.
- **B0**: the M2 v3 analysis of the existing 6-draw parent panel, which ran no SUMO.
- **C1 to C4**: the M2 stages (section 12.4).
- **Clopper-Pearson**: the exact binomial confidence bound.
- **ED, swap p**: the paired L1 energy distance between two ensembles on the same keys, and its exact within-pair
  swap permutation p.
- **excess, PR, PP**: PR is the parent draws' mean distance to the real counts, PP their mean distance to each other;
  the excess is PR - PP, and PR/PP is their ratio.
- **fair CRPS, half-GMD, d_mean_tae, d_half_gmd, d_crps**: the fair CRPS of an ensemble is its mean distance to the
  target (mean TAE) minus half its mean pairwise distance (half the Gini mean difference, half-GMD), summed over the
  objective's values; lower is better. A difference between two arms splits as d_mean_tae = d_crps + d_half_gmd, a
  location part and a spread part.
- **g**: the materiality threshold of the gate: 10 vehicles of fair CRPS on ms4.
- **G0**: the Build gate's hard checks; any failure rejects (verify-edit status, materialization, structural
  failures, frozen hashes, outside-scope fact changes, shape regularity, no new in-scope audit findings).
- **H1, H2, H3, N1-N4, I**: the steps of the M2 stage evaluator, in order. H1 invariants (equal arms, distinct keys,
  bindings; in a confirmation also disjoint secret keys and one measurement-code identity); H2 health (A2.1); H3
  guards (entry-delay p95, emergency brakings, post-window passages); N1 the largest per-row error growth over 182
  rows; N2 the init52 TAE; N3 the totals13 fair CRPS; N4 the main130 fair CRPS; I the primary test (A1.3). Guards
  only reject.
- **HO-B1, HO-C3**: held-out data: HO-B1 the approach-level facts removed from a Build flow's scored set at
  goal-create; HO-C3 a second count day as a Calibrate holdout.
- **INV-AUDIT, INV-E1-LANE, INV-ONE-CLASS, INV-PERM**: invariants: no new in-scope audit finding; no change to a lane
  that hosts an E1 detector; the class derived from the diff equals the goal's class; the vehicle-class permissions
  of each lane stay a subset of the source's.
- **init52, main130, ms4, totals13**: Calibrate objectives: the 52 initialization rows, the 130 main-window 15-min
  rows, the 91 rolling-hour values, and the 13 field totals.
- **K1, K2, K2 goals**: the M3 known-answer harnesses: K1, 46 mutation cases on the X4 fixture; K2, 8 cases on the
  Ring1 nominal net (5 injected ledger faults and 3 raw-XML perturbations), whose Build goals are the K2 goals.
- **L2, L3**: Build guards: L2 an all-turn smoke run; L3 frozen-trip routeability on 10 seeds.
- **op, op\*, y\***: a typed edit or Calibrate operation; in a twin-of-twin known positive, op\* is the hidden op and
  y\* = baseline + op\* run on fresh keys replaces the real counts as the target, so the true answer is known.
- **persistence label**: M4.3's descriptive record of the healthy-subset test (`persists`, `not_observed` or
  `unresolved`); it attributes the gain to nothing.
- **plug-in power**: power computed by a normal approximation with the measured effect and spread plugged in.
- **S branch**: the gate's simplification rule; report-only.
- **screen, confirmation**: a screen judges a candidate on panel or public keys and carries no rate; a confirmation
  runs on fresh secret keys committed before the runs.
- **TAE**: total absolute error between simulated and real counts over an objective's values.
- **V**: the Build violation vector (n_TOP, n_CTL, n_GEO, n_ATTR, E): the numbers of mismatched scored facts of the
  topology, control, geometry and attribute classes, and E the sum of their normalized numeric excess, compared
  lexicographically. V = 0 means every scored fact matches.
- **WMW**: the Wilcoxon-Mann-Whitney rank test.
