# M2 C1 result (2026-10-26)

> Dated snapshot of a local working record of the ASTRA Twin Profiler effort, committed so the M2 stage C1 result can be read without the author's machine. Paths under `C:\Users\...\scratchpad`, `E:\twin-store` and `E:\astra-*` refer to that machine and are not part of the repository. Superseding decisions are recorded in later dated documents, not by editing this one.

Explore evidence only: simulated counts, public keys, no rate, no promotion authority. Code E:\astra-m2 @ d11d284
(clean, unchanged). Store E:\twin-store\m2-pilot, ledger 28 -> 36 rows. Numbers: `m2-c1-result.json`. Scripts are
in `m2-c1\`, and the plan was frozen before the runs in `m2-c1\PREREG.md` (sha256 in `prereg.sha256`).

## Runs and gate
- 8 new judge runs `c1-parent-k7..k14` (depart_jitter, public keys m2-pilot/k7..k14, goal seed 104, 3 workers,
  214-247 s each). No spare was used.
- **Gate fails on "all runs healthy": 7/8.** k14 is `invalid`, with 1 side collision and 1 teleport on
  `:315029332_0_2` at 8481 s. The run did not crash: it completed and every bin was scored, and the same request is
  deterministic. So no spare was run. The teleported vehicle skipped its 0535Z6 loop, so 0535Z6 counts 310 on k14
  against 311 on every other draw.
- Unhealthy frequency: parent 1/14 (95% CP 0.002-0.339); all ledger runs 1/36 (0.001-0.145). Under v3 H2
  ("every run healthy"), a K = 16 confirmation of 32 runs would hold at least one unhealthy run with probability
  about 0.59-0.91. For K = 8 (16 runs) it is about 0.36-0.69. A pre-registered rule for invalid draws is needed
  before C3.

## Decision row 1: ms4 stays primary
| ms4 (91 values) | PR | PP | PR/PP | excess | z (jackknife) |
|---|---|---|---|---|---|
| **fresh k7..k14 (the pre-registered 8)** | 465.9 | 360.4 | 1.29 | **105.5** | **2.43** |
| fresh, healthy k7..k13 | 475.1 | 335.3 | 1.42 | 139.8 | 3.72 |
| all 14 | 479.1 | 366.0 | 1.31 | 113.1 | 3.56 |
| healthy 13 | 485.2 | 350.1 | 1.39 | 135.1 | 5.06 |
| k1..k6 (B0 parity) | 496.8 | 369.7 | 1.34 | 127.2 | 3.12 |

- excess > 0 and z ≥ 2 holds both with and without k14. The leave-one-out excess on the fresh 8 stays between 90.9
  and 139.8. The real counts are the most outlying member among the fresh draws (rank p 1/9).
- main130 on the fresh 8: PR 364.3, PP 368.6, ratio 0.99, z -0.39. It is still at the chaos floor.
- Sensitivity:
  - ms3 does not replicate on the 8 (ratio 1.16, z 1.28; healthy 7: 1.29, z 2.73).
  - totals13 does: ratio 3.31, z 11.5.
- **Per-field excess, fresh 8:**
  - 0119Z12 25.7, Z13 23.0 and Z11 21.4; the Z11-Z13 corridor carries 70 of 105.
  - 0119Z5 21.6, Z4 7.9 and 0535Z4 6.6.
  - Z5 now contributes, against -0.9 on k1..k6.
- **Real totals against the draws:** 0119Z4 600, Z5 522 and 0535Z4 390 are still above every draw (fresh ranges
  572-587, 483-498 and 372-379). The real 0119Z12 1160 and Z13 1346 lie below every fresh draw (1161-1175 and
  1355-1375); only k3 (1145 and 1330) is lower.

## Recalibration on the 13 healthy draws (k14 as sensitivity; v3 values in brackets)
| Guard | K = 5 | K = 8 | K = 16 | v3 (6 draws) |
|---|---|---|---|---|
| Entry network p95 (sd 85.4) | 144.9 | 114.5 | 81.0 | 245 / 194 / 137 |
| Entry 44532531 p95 (sd 89.3) | 151.5 | 119.8 | 84.7 | 256 / 203 / 143 |
| Brakings (sd 6.49, was 2.48) | 11.0 | 8.7 | 6.2 | 5.3 / 4.2 / 3.0 |
| Post-window (sd 16.6) | 28.2 | 22.3 | 15.8 | 47 / 37 / 26 |
| init52 (sd 14.8) | 25.0 | 19.8 | 14.0 | 29 / 23 / 16 |
| N1 max row growth (boot q99) | 14.8 | 11.9 | 8.4 | 15 / 12 |
| N3 totals13 dCRPS: MC / exact-split | 20.7 / **26.3** | 16.3 / 20.8* | 11.4 / 14.7* | 21.0 / 15.5 / 10.4 |
| N4 main130 dCRPS: MC / exact-split | **62.0** / 41.5 | 48.9 / 32.8* | 31.5 / 23.2* | 68 / 50 / 33 |

- The t-margins use t_{0.99,12} = 2.681.
- \* marks the exact K = 5 split quantile scaled by √(5/K).
- The block-bootstrap MC drops cross-field dependence. It understates the totals13 spread (sd 9.1 against 13.2 for
  the exact disjoint splits at K = 5) and overstates main130 (26.9 against 18.6). At K = 5, v3's N3 margin of 21.0
  is the exact-split q95, not its q99.
- Entry classes are unchanged: 7 quiet and 6 busy.

**CRN ratio ρ on ms4.**
- The re-key sd is 158.1 on the 13 healthy draws (155.2 with k14; 177.0 on B0's 6). So K = 8 needs sd(d) ≤ 55.3.
- No paired candidate exists on k7..k14. On the k1..k6 candidates:

| Candidate | ρ | K |
|---|---|---|
| L | 0.051 | 8 |
| M | 0.670 | 16 |
| N | 0.734 | 16 |

**k3-type spillback** (post-window passages on 0119Z12 + Z13 ≥ 20):
- 0/8 on the fresh draws (95% CP 0-0.369); the fresh maximum is 4 + 3 on k7.
- 1/14 overall (k3; 0.002-0.339).
- The sensitivity rule (both fields ≥ 10) gives the same counts.

**M1 ensemble profile, 13 healthy draws:**
- Entry 44532531 at hotspot rank 1 (B0's definition): 12/13, one-sided CP lower 0.684 (two-sided 0.640-0.998).
  It is rank 2 on k11. All 14: 13/14 (0.703).
- Entry 44532531 at lead-element rank 1: 10/13 (0.505). Top 5: 13/13 (0.794).
- Corridor 24483344#0 + #1, merged: top 5 in 13/13 (0.794; all 14: 14/14, 0.807), at rank 1 or 2 in every draw.
- Corridor edge #1 alone reaches the top 5 in only 8/13 draws, so the merge is needed.
- BAL 0119Z4, Z5 and 0535Z4: the real total lies above the draw range in 13/13.

## Caveats
- **Run cache.** The judge cache key includes the verifier commit. At d11d284 the k1..k6 requests are cache misses,
  although their content is otherwise identical (`m2-c1\request_diff.json`). None of the 8 C1 requests had run
  before.
- **What the decision rests on.** It rests on public keys and a post-hoc objective. z 2.43 on the pre-registered 8
  is close to the threshold. It confirms hour-scale excess on this day only, not its cause.
