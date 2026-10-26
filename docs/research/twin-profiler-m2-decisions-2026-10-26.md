# M2 v3 decisions (pre-registered 2026-10-26)

> Dated snapshot of a local working record of the ASTRA Twin Profiler effort, committed so the owner-delegated M2 decisions and amendments A1-A2 can be read without the author's machine. Paths under `C:\Users\...\scratchpad`, `E:\twin-store` and `E:\astra-*` refer to that machine and are not part of the repository. Superseding decisions are recorded in later dated documents, not by editing this one.

The owner delegated the open v3 decisions ("for those parameters, you decide") and approved running M2 now.
These decisions answer `m2-protocol-v3.md` section 10 and are frozen before any C1-C4 run.

| # | Question | Decision | Reason |
|---|---|---|---|
| 1 | Primary objective | **ms4** (91 rolling-hour values), conditional on C1: ms4 stays primary only if the 8 fresh parent draws alone show hour-scale excess, i.e. the same PR/PP estimator gives excess > 0 with z >= 2. Otherwise main130 becomes primary and ms4 is reported. | ms4 is the only objective with headroom against the real counts, but it was chosen post hoc on the spent panel, so it must replicate on fresh keys first. |
| 2 | Guarantee | Exactness under the sharp null for the paired sign-flip test, plus Monte Carlo bounds on the composite null. v2's exact composite-median sign rule stays report-only. | The v2 rule is valid but has no power under chaos. |
| 3 | Materiality | g = 10 on ms4; totals13 guard vehicle floor 5. | The parent's draw-to-draw ms4 distance (369.7) matches main130 (368.7), so g carries over unchanged. |
| 4 | K by regime | Confirmation K = 8 when the panel CRN ratio rho <= 0.35 on ms4, else K = 16. Screening K = 5, no rate. | Size <= 12/256 at K = 8; K = 16 restores power where CRN cancels nothing. |
| 5 | S branch | No simplification claims in M2. v2's exact S rule is reported only. | No powered non-inferiority test exists yet. |
| 6 | L-type backlog | Reported as a flag, never blocking. | A blocking guard would be an ad hoc rule fitted to one case. |
| 7 | Family | depart_jitter +-0.5 s is the only declared family. Tie order stays a diagnostic. | One declared family keeps the size claims interpretable. |
| 8 | Holdout | None in M2. Every claim is limited to 2026-10-19 05:30-08:00 UTC; freezing a second count day is a later, separate step. | Fetching a second day is outside M2's scope. |
| 9 | Budget | Run C1 (8) -> C2 (22) -> C3 (16-32) now, as separate stages with a stop after each failed gate. Run C4 (about 100) only if C3 passes. Hard caps: C1 8, C2 24, C3 36, C4 110 (all including spares for crashed runs only). | End-to-end confirmation first; power by class only once the pipeline is shown to work. |
| 10 | Fixture | Commit the derived fixture (tests/fixtures/twin_m2, about 112 KB). | Done in d11d284. |

## Amendment A1 (written 2026-10-26 after the Codex v3 review and BEFORE any C1 result was read)

Source: `codex_review_m2v3.json` (good = false for C2/C3; C1 may continue).

| # | Change |
|---|---|
| A1.1 | **C1 rule frozen precisely.** The z in row 1 is excess / SE, where SE is the leave-one-draw-out jackknife SE over the 8 fresh draws (PR and PP recomputed on each 7-draw subset). SE = 0 counts as a failed screen. Additional robustness condition: the excess must stay > 0 after every leave-one-draw-out deletion. ms4 stays primary only if both hold. This is an engineering screen, not an exact test. |
| A1.2 | **Fallback propagated.** If ms4 fails C1, main130 replaces it everywhere ms4 appears: qualification ED, rho and the K rule, materiality g = 10, and the guard set (ms4 then becomes report-only). |
| A1.3 | **Confirmation test changed to an unpaired design.** Candidate and reference run on DISJOINT fresh secret keys (K each). Exchangeability then holds by construction under the sharp null that both arms have the same distribution. Test: exact one-sided permutation over all C(2K, K) relabelings of the directional fair-CRPS difference against the target; accept iff p <= 0.05 and T <= -g. The paired CRN sign-flip test is reported only, labelled with its exchangeability assumption. Cost is unchanged (2K runs). The claim is limited to: rejection of equal distributions together with an observed score gain of at least g against this target. It is not a population gain of g, and it does not control the composite null "different but not better". |
| A1.4 | **Monte Carlo numbers relabelled** as observed acceptance frequencies on the stated generators (17/1000 and 99/1000), with one-sided 95% Clopper-Pearson upper bounds 0.025 and 0.116 before multiplicity. They are not bounds on the composite null. |
| A1.5 | **Code aligned with the decisions before C2.** S is report-only (no `simplified` verdict reaches a gate), the L-type backlog is a flag that never blocks, and N1 is enforced in one production stage evaluator together with every other guard. Tests pin each of these, including a 130-row case where one row worsens by 20 while the rest improve, which must be rejected. |
| A1.6 | **C3 allocation.** Reference parent on K fresh secret keys, the qualified positive on K other fresh secret keys, and the negative control (parent) on K further fresh secret keys: 3K runs. With K = 16 this is 48, so the C3 cap becomes 52 (4 spares for crashed runs only). The candidate that enters C3, its target and the K are frozen and hashed before any C3 run. |
| A1.7 | **Frozen before confirmation:** the K = 16 N1 margin, the init52 target used by N2, and the corridor-merge rule for the ensemble profile. |
| A1.8 | **Wording.** Qualification filters are exploratory screens on exposed keys. C3 is an end-to-end demonstration, not a validation of power or false-accept rates (a valid null passes its non-rejection gate about 95% of the time). Profile bounds are pointwise and "robust"/"trustworthy" are descriptive. "Chaos floor" and "selection effect" are statements about this parent and this day, not established population facts. |

## C1 outcome (read 2026-10-26)

- ms4 stays primary: fresh 8 draws excess 105.5, jackknife z 2.43, leave-one-draw-out excess 90.9-139.8 (all > 0), so
  both row 1 and A1.1 hold; healthy fresh 7: z 3.72. main130 stays at the floor (ratio 0.99).
- The gate "all runs healthy" FAILED: parent k14 had 1 side collision and 1 teleport (8481 s, :315029332_0_2). The
  parent is unhealthy in 1 of 14 draws (95% CP 0.002-0.339). The sequence stopped as pre-registered.

## Amendment A2 (written AFTER reading C1; it changes the health rule because C1 showed it cannot work)

| # | Change |
|---|---|
| A2.1 | **Health becomes a comparison with the reference arm.** "Every run healthy" assumed the parent is always healthy; C1 shows it is not. New H2: (i) no run may show a failure class the parent panel never showed (stall, gridlock, vehicles not inserted or not arrived at the end, discarded vehicles); (ii) the candidate arm's count of unhealthy draws (collision or teleport) may exceed the reference arm's count by at most 1 (K = 8) or 2 (K = 16), declared now from the parent's observed rate (1/14) as a materiality margin, not a test. H2 failures still reject outright. |
| A2.2 | **Unhealthy draws stay in the ensemble** and are scored as they are, with the teleport and collision counts reported per draw. Replacing them with new keys would condition on health and hide a candidate that makes failures more likely. Spares remain for runs that crash or time out only. |
| A2.3 | **The parent's unhealthy frequency is reported** with every confirmation (now 1/14), and the 13 healthy draws plus k14 together form the 14-draw parent panel for margins (C1 reports both; margins move by about 3% with k14). |
| A2.4 | This amendment is written after seeing C1 and is labelled as such. It changes no threshold that C1 evaluated: the ms4 decision is unchanged and is the same with and without k14. |

Stage gates (each stage stops the sequence when its gate fails, and the next stage is planned only after the previous
result and the Codex review of v3 are read):
- **C1** parent on 8 fresh public keys k7..k14 (seed 104, depart_jitter). Gate: all runs healthy; ms4 decision (row 1);
  report the 14-draw guard margins, rho, and the frequency of spillback events.
- **C2** qualification: M on 8 fresh truth keys; one hour-scale corridor op on 0119Z11-Z13, selected by a rule
  written down before its runs, on k1..k6 plus 8 truth keys. Gate: at least one op qualifies per v3 section 6.
- **C3** confirmation on fresh secret keys (os.urandom, sha256 committed before the runs) for the parent and the
  qualified positive, judged against its C2 truth ensemble. Gate: the qualified positive is `improved` and the
  guards hold; a parent-vs-parent re-key confirmation is not `improved`.
- **C4** only if C3 passes: 4 positives x 14, 2 negative controls x 6, 2 confirmations x 16.
