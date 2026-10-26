# M2 C2 result (2026-10-26)

> Dated snapshot of a local working record of the ASTRA Twin Profiler effort, committed so the M2 stage C2 result can be read without the author's machine. Paths under `C:\Users\...\scratchpad`, `E:\twin-store` and `E:\astra-*` refer to that machine and are not part of the repository. Superseding decisions are recorded in later dated documents, not by editing this one.

This is explore evidence only: simulated counts on public keys, with no rate and no promotion authority.

- **Code.** E:\astra-m2 at d11d284, plus the uncommitted stage-0 code and the C2 modules. Nothing is committed. The
  working-tree sha256 is 11951a03..., and the `git diff HEAD` sha256 is 8fb059c7....
- **Store.** E:\twin-store\m2-pilot. The ledger went from 36 to 58 rows.
- **Numbers.** They are in `m2-c2-result.json`: the frozen analysis output plus an `addenda` key that holds the runs
  and the selection.
- **Pre-registration.** The plan was frozen in `m2-c2/PREREG.md` before any C2 run. Its sha256 f53eb7cd... is in
  `m2-c2/PREREG.sha256`, stamped 01:20:53Z. The runs started at 01:21:08Z.

## Runs
- **Batch.** There were 22 new judge runs with 3 workers, from 01:21:08Z to 01:54:33Z. Each took 191-282 s. No run
  crashed and **no spare was used**.
  - `c2-M-t1..t8`: M on m2-c2/t1..t8.
  - `c2-X-k1..k6`: X on m2-pilot/k1..k6.
  - `c2-X-u1..u8`: X on m2-c2/u1..u8.
- **One unhealthy run.** `c2-M-t1` completed `invalid`, with 1 collision and 1 teleport. These are the same classes
  as the parent's k14. Under A2.2 it is kept and scored. It is the only reason the batch printed `blocked`.
- **Unhealthy frequency.**

| Group | Unhealthy | Note |
|---|---|---|
| Parent | 1/14 | unchanged |
| M draws | 1/14 | k1..k6 cached, t1..t8 |
| X draws | 0/14 | |
| All ledger runs | 2/58 | |

## Corridor-op selection (rule fixed in code before it ran once, as in PREREG b)
**The chosen op.**
- **X = `cell_depart_shift:101:2700`**: pair 13, period 4, 12 vehicles, field 0119Z11, entry 44532531.
- 9 of the 510 screened ops were eligible.
- **Predicted ms4 real-count gain: -21.18.** Every eligible op had a negative predicted gain, so the rule picked the
  least harmful one.
- **Direction.** X shifts later. The corridor lag runs the other way: the twin is later than the real counts by
  +87 s pooled and +158 s on Z11.

**Predicted against measured.**

| Quantity | Predicted | Measured |
|---|---|---|
| Paired ms4 ED | 25.21 | 8.27 |
| C8 | 14/14 draws | 5/6 keys (24, 10, 21, 19, 19, 16 against 12) |

The queue model was optimistic about the size of the effect, as the B0 model check already suggested. For L it
predicted 120 where 48 was measured on k1, and 84 against -1 on k4.

## Qualification per v3 section 6 (ms4, g = 10, alpha = 0.05; panel k1..k6 against the cached parent)
| Check | M `cell_depart_shift:107:900` | X `cell_depart_shift:101:2700` |
|---|---|---|
| Q1 paired ED (swap p) | 394/15 = 26.27 (p 1/32): **pass** | 124/15 = 8.27 (p 9/32): **fail** |
| Q2 leave-one-out ED >= 5 | 22.0, 24.9, 32.3, 26.2, 23.3, 28.9: **pass** | 10.6, 23.4, 23.8, -8.7, 1.7, -1.2: **fail** |
| Q3 C8 (needs 5 of 6) | 25, 27, 12, 18, 23, 28 against 17: 5/6, **pass** | 24, 10, 21, 19, 19, 16 against 12: 5/6, **pass** |
| Q4 truth ensemble | t1..t8, 8 scored (t1 unhealthy, kept), disjoint: **pass** | u1..u8, 8 scored, disjoint: **pass** |
| Qualified (class ED/g) | **yes** (moderate) | **no** (immaterial) |

**Power against each op's own 8-draw truth ensemble** (reported, with no threshold):

| | M | X |
|---|---|---|
| P1, K = 5 (336 subset pairs): `improved` / test I / guards hold | 7 (0.021) / 7 / 336 | 13 (0.039) / 13 / 336 |
| P1, K = 5: T median [min, max]; p median [min] | -28.05 [-81.6, 15.7]; 0.238 [0.008] | -40.78 [-92.8, 7.95]; 0.153 [0.016] |
| P1, K = 6 (28 pairs): `improved` / guards hold | 0 / 28 | 0 / 28 |
| P1, K = 6: T mean [range]; p median [min] | -28.43 [-53.9, -11.75]; 0.199 [0.062] | -40.09 [-61.84, -26.2]; 0.152 [0.060] |
| P2 T over the pools | -21.49 | -29.59 |
| P2 approximate power at K = 5 / 6 / 8 / 16 | 0.255 / 0.279 / 0.323 / **0.472** | 0.422 / 0.453 / 0.510 / 0.678 |

- **Pools.** P1 pairs the op's k1..k6 draws with the independent parent draws k7..k14 under the screen gate (the
  frozen K = 8 margins). It gives observed frequencies over overlapping subsets, not rates. P2 is a normal
  approximation with delete-one jackknife variances; it is approximate and plug-in.
- **X's higher power.** X's P2 power is higher than M's, but its paired ED fails Q1, and its shared-key screen carries
  the note `spread_driven`: d_half_gmd +14.9 and d_mean_tae -2.58. X mostly widens the ensemble. It is excluded
  because it does not qualify.

**Report-only shared-key screen** (op against the cached parent on k1..k6, K = 6 screen gate, truth target):
- **M:** `not_significant`, T -212/15 = -14.13, p 0.254. H2 to N4 all hold: N1 5.08 against 11.62, N3 0.62, N4 -32.25.
  The entry delay on 44532531 fell by 35.7 s.
- **X:** `not_significant` with the note `spread_driven`, T -17.48, p 0.201. Every guard holds. No backlog flag and no
  report-only busy-entry margin fired for either op.

**K rule** (rho = sd(d) / 158.074; d = per-key ms4 TAE difference against the real counts):

| Op | d | sd | rho | K |
|---|---|---|---|---|
| M | 69, -64, 18, -146, 160, 22 | 105.83 | 0.6695 | 16 |
| X | 38, 239, -243, -183, -30, 44 | 173.73 | 1.099 | 16 |

## Stage gate: PASSES (applied literally: at least one op qualifies; M does, X does not)

## Frozen C3 plan: `m2-c3/FREEZE.json`, sha256 de9d73c0822b0cda916398e2aea77bc9675f185ecefda822ab99603e4df3a8b7 (`m2-c3/FREEZE.sha256`)
- **Candidate.** M (`cell_depart_shift:107:900`). It was the only qualified op, and it holds every guard step in
  28/28 of its K = 6 screens.
- **Target.** M's C2 truth ensemble: `c2-M-t1..t8` on m2-c2/t1..t8, with t1 unhealthy and kept.
  - target_sha256: 06261d25...
  - real counts sha256: 32ab5283...
- **K = 16** (rho 0.6695).
- **Arms.** Reference (parent), candidate (M) and negative control (parent), with 16 fresh secret keys each and 1 spare
  key each.
  - That is 48 planned runs, 51 with spares, against a cap of 52.
  - The keys come from os.urandom(32) at C3 time. Their sha256 fingerprints are committed in order before the first
    C3 run.
- **Gate.** `m2-confirmation-K16`, sha256 cd7db25e....
- **Evaluations.** The positive must be `improved`. The negative control, against the same reference, must not be
  `improved`.
- **Code identity.** HEAD d11d284 plus the working-tree hash and the per-file sha256s. The truth runs' identity is
  measurement code de7747aa and runtime 3c7d6e80.
- **No C3 run was made.**

## Caveats
- **C3 positive.** The approximate power of C3's positive at K = 16 is about 0.47. Even a correct pipeline fails the
  positive about half the time. A null negative control passes non-rejection about 95% of the time (A1.8).
- **Measurement-code identity.**
  - M's panel draws s3-M-k1..k6 and the parent k1..k6 were measured by B0's code (22d28e93, verifier 6ccaac5). The
    truth runs and the X runs used de7747aa.
  - The C3 identity check covers both C3 arms and the truth. Until C3 runs, nothing may change in `runner.py`,
    `count_profile.py`, `ids.py`, `core/route_sensor_matrix.py` or `core/detector_demand.py`.
- **Verifier state.** Every C2 run records `verifier_clean: false`, because the working tree is dirty.
- **Scope of the evidence.** Everything rests on public keys, one day and post-hoc objectives. Qualification is an
  exploratory screen, not a population ED of g.
- **Files.**
  - New modules: `twin/corridor_screen.py` and `twin/m2_qualify.py`.
  - `twin/panel.py` gained `read_queue_draws` and `read_demand_schedule`.
  - New test: `tests/test_twin_m2_qualify.py` (17 tests).
  - The 254 twin and digital-twin tests pass (199 + 55), ruff is clean, files use LF, and `git diff --check` is clean.
