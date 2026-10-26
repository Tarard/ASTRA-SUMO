# Ring1 Calibrate parent: fragility-fix check, pre-registration (M4), round 2

Written 2026-10-26, after the review of round 1 and the static-gate re-run, and before any candidate SUMO run of the
Calibrate scenario. Explore only: no field claim, no traffic-safety claim, no promotion authority. This round
supersedes round 1 (kept unchanged in `round1\PREREG.md`, sha256 `467ff01b...cbf5a`, and `round1\PREREG.sha256`,
sha256 `2aaf9387...92f6`, beside this file). No dynamic run has happened under either round: the fragility store has
no `runs.jsonl` and no `runs\` folder. Every file named here is bound by its SHA-256 in `PREREG.sha256` beside this
file. The code is bound by the git tree hash of the working tree of `E:\astra-m4` (branch `twin/m4-fragility`, HEAD
`3ded083`, not committed): `2562999b19ce09fc612190e5e0b43bce1e331e41`.

## R. What round 2 changed, and what the owner must decide before any run

| review finding | change | owner decision needed |
|---|---|---|
| major: the S01 metric is biased between arms | Every primary metric now judges "wrong lane" by the **parent's** usable links on both arms (`run_metrics(parent=)`). The round-1 own-links count is kept as a secondary metric. | **Yes.** The owner named this metric, so confirm the arm-neutral reading before any F2 or F3 run (section 4). |
| major: the S03 trap does not follow the owner's definition | Trap episodes now require every covering vehicle's next edge to be reachable from 186821027#0_4 only through a never-green parent link (that is, 186821036#1 via link 5). The one round-1 "trap" (c3-negative-control-12) has next edge 142944337#1, so it is not a trap. | **Yes.** No reference draw shows a link-5 trap (0/46), so F1's 16 runs and F3's S03 test can only test the link-7 EB component (section 4). Decide whether to spend them on that. |
| minor: declared targets can be gamed | Lint and goal-create refuse any entry the declared typed op does not imply: movement changes, op pairs, controller bijection and merges are all tied to the op. The control re-index cross-checks the declared signal entries against netconvert's link map. The gate claim now says V = 0 holds by construction. | no |
| minor: side effects under-described | `declared_targets.materialized_side_effects` records them in `static-gate.json` (section 2). | no |
| minor: the NI arm was chosen after the census | The stage runs against **both** 16-draw parent arms (conjunction). The criterion is renamed to "no calibration regression detected (M2 guards, within parent noise)". | no |
| minor: the decision rule is not bound in code | `decide(comparable=, stages=)` and `fragility.analyze`, CLI `astra_twin fragility-analyze --design prereg\design.json`. A dry run on the empty candidate ledger is bound (`analysis-dryrun.json`). | no |
| minor: gridlock health is asymmetric | Kept conservative. Gridlocks are also reported against the reference rate (`rare_events`); see section 6. | optional |
| minor: candidate parent unchecked | `candidate_scenario` requires the manifest's `parent`, and `plan_judge` refuses one that differs from the goal's prepared net and control. | no |
| minor: stray load output; absolute output path | Load checks copy every additional file into the load directory and refuse an output path that is absolute or leaves its directory. The stray `field-counts.xml` was **moved**, not deleted, to `gate\superseded\round1\stray-load-output\`. The M2 P1 field counts are untouched (mtime 2026-10-25 11:51). No step SUMO-loads `e1.add.xml`: the gate only parses lane artifacts. | no |
| minor: nothing anchors the PREREG | The SHA-256 of `PREREG.sha256` is reported to the owner with this round. | **Yes.** Before the first run, commit the bound tree with PREREG.md and PREREG.sha256, or record that hash. |
| minor: the merge mechanism deviates | Stated in section 2: a twin-layer exemption, not accepted by the core release audit. | no |

Still open from round 1: the `minor_where_yielding` copy rule for F2's added link (section 2), and whether to spend
runs on candidates that failed the static gate (section 0).

## 0. Result of the static gate (re-run in round 2), and what it means for the dynamic check

| candidate | op (typed, M3) | V source -> candidate | G0 | L2 | L3 (10 frozen seeds) | screening | source_dirty |
|---|---|---|---|---|---|---|---|
| F1 | `delete_lane_connection` 186821027#0_4 -> 186821036#1_1 at LSA535_part0 (link 5) | (1,22,0,0,0) -> 0 (improved, n_TOP) | **fail: INV-AUDIT** (10 witnesses) | pass | subset_holds (10/10) | `g0_rejected` | true (25) |
| F2 | `add_lane_connection` 603103444_1 -> 73474895_0 at 266197536 | (1,7,0,0,0) -> 0 (improved, n_TOP) | pass (14/14) | pass | **fail: seeds 7 and 97** | `dynamic_hard` | true (25) |
| F3 | F1 then F2, verified-parent chain | step 1 (2,29,0,0,0) -> (1,7,0,0,0) | step 1 **fail: INV-AUDIT** (same 10) | step 1 pass | step 1 subset_holds | chain stops at step 1 | true (25) |

Round 2 re-ran every step whose code or record changed:

- goal-create ×3: all three goals came back `unchanged`, and they pass the stricter lint;
- the three profile builds: the results are identical; only the gate claim changed;
- the F3 diagnostic materialization;
- control-reindex with the contained load check: candidate nets and controls are byte-equal to round 1;
- the F3 raw profile.

Round-1 outputs were moved to `gate\superseded\round1\` (README there).

- **F1.** G0 rejects correctly. There are 10 INV-AUDIT witnesses:
  - 4 are genuinely new `path_width_transition_centerline_offset` review findings at the 43305966#1 exits.
  - 5 are parent findings renamed by the renumbering.
  - 1 has a parent finding's kind with a new hash identity.

  As materialized, F1 is "link 5 removed **plus** the in-scope side effects of section 2". No dynamic result can be
  attributed to the removal of link 5 alone. Link 7 (27.20 m) keeps its shape; only its ids change.
- **F2.** G0 passes. The merge `603103444|73474895|0:1,2` is taken out of INV-AUDIT by the goal's declared
  model-justified merge and reported under `gate.justified_merges` (section 2). L3 fails on seeds 7 and 97: on each, the
  candidate run has one junction collision with a collision teleport on `:260305243_0_1`. That is a priority fan-out
  about 0.6 km away, outside the edit scope, with unchanged geometry (site `new:1561539019`). Collisions carry no
  per-request identity, so L3 never passes them (`dynamic_hard`).
- **F3.** Step 1 is G0-rejected, so the chain does not form. The diagnostic F2-on-F1 net is profiled raw only: V = 0,
  with the same 10 INT rows as F1.

**Eligibility (frozen).** A `fragility_reduced` label requires a static-gate screening of `improved`. No candidate meets
it, so under this pre-registration every dynamic result is `diagnostic_only` (with `label_if_static_gate_had_passed`).
Whether to spend the authorized runs (16 per candidate, 48 plus 3 spares) on candidates that failed the static gate is
the owner's decision. None was started.

## 1. Inputs (all hash-bound; paths under `E:\twin-store\fragility` unless absolute)

| item | file | sha256 |
|---|---|---|
| parent net (byte-equal to `E:\twin-store\m2-pilot\inputs\6c52382e7664eca33fceff6f\network.net.xml`) | `inputs\6c52382e7664eca33fceff6f\network.net.xml` | `6c52382e7664eca33fceff6fd02ae700d93139a316a3f9f5139d0e139d7a18ce` |
| parent control (staged `readme_fixed_A`, 50 tlLogic) | `inputs\27220b430aa68c24f0ad360c\control.add.xml` | `27220b430aa68c24f0ad360c57ee3bd0f4b8dcdc88aead5344bcff00e4f43e3b` |
| lane artifacts (hash-bound in each Build goal) | `inputs\b041850bcb7936f8f37c6079\e1.add.xml`, `inputs\7393b0c6b1f890744621e16d\source-e1.add.xml` | `b041850b...1010`, `7393b0c6...8ebb` |
| Calibrate goal (byte-equal copy of the M2 goal) | `goals\calib-ring1-20261019-h4z11m5z5-v1\goal.json` | `2ba4c172278fc59286548c557cf1a82e22e685232068f58ac0f88fc4beec33e5` |
| F1 / F2 / F3 Build goals (unchanged from round 1) | `goals\fragility-f1-lsa535-link5\goal.json`, `...f2-266197536-lane1\...`, `...f3-f1-then-f2\...` | `9e8f2c4a...8cc3d` / `fbad5349...86c6` / `8cb5fe41...4c18` |
| F1 candidate net / control (unchanged) / manifest | `candidates\F1\{network.net.xml, control.add.xml, candidate.json}` | `d26de9dc...598b1c` / `6af990f0...46ba7b` / `6f154e62...afe0f` |
| F2 candidate net / control (unchanged) / manifest | `candidates\F2\...` | `235f6477...fe036b` / `b62b02eb...d1f5fb` / `965f9f09...c43f` |
| F3 candidate net / control (unchanged) / manifest (diagnostic) | `candidates\F3\...` | `8b18f75d...39cf57` / `d3876c11...a6c17b` / `21657f1b...d430` |
| static-gate record (round 2) | `gate\static-gate.json` | `74e2563f495bd15e531beb06c3e915a78b9ad822eef4241e8d3c974327310ecf` |
| reference arm with its metrics (schema v2) | `reference\reference-arm.json` | `a05e6c5af5d973223e5c84329b01d1b230ef56adbc328e4c5bc896918227a04d` |
| design, with the frozen `analysis` block | `prereg\design.json` | `4ac5eff8a7548205fc4ebba065a851a22a237a2c1ceaa36224bd527087a6769f` |
| analysis dry run (no candidate run) | `prereg\analysis-dryrun.json` | `172c5f7c968a1a589d57d200dc8e390c028d2ba71ff1ce3a17638d2580b5e55d` |
| deferred sites (unchanged) | `prereg\deferred.json` | `cf596d0d325a220f41067b64dd9d33efcde96920e383f957d9e1a02d56ec2f11` |
| code: working tree of `E:\astra-m4` | git tree (temporary index, `git add -A` + `write-tree`) | `2562999b19ce09fc612190e5e0b43bce1e331e41` |

The candidate judges' measurement code will be `b6bd6660cfca8f9206f81444e231e528ef04ea76617bc4d7555d711deaebd1da`; it
changed from round 1 because `twin/runner.py` gained the parent check. M2's stage only *reports* the identity mix, so
this does not block it.

## 2. The candidates

Each candidate is an M3 Build goal on the parent net (`twin/declared_targets.py`, `goal.create_build_goal(declared=)`):

- **Reference facts** are `core.reference_net_facts` of the parent itself (4626 facts, all matched) plus a **declared
  target delta**: only the in-scope `movement_inventory` and `signal_group` facts the op changes, each tagged
  `fragility_fix`, explore only. The delta is derived from a verify-edit materialization of the same op
  (`derive_targets`). **So V = 0 holds by construction:** an improved V shows only that the gate's materialization did
  what the declared op implies, never that the edit is right. The gate claim says so.
- **Lint now ties every entry to the declared typed op** (`derived_from.steps[].op`). It refuses:
  - a movement change other than on the op's from-lane, equal to the source set plus or minus the op's pair;
  - a removed or added signal link other than the op's own pair (control declarations likewise);
  - a renumbered link that changes its controller, and any `binding_changed` entry;
  - a merge that no declared add op creates.

  goal-create also checks, against the source's facts, that the declared links of each touched controller remain a
  bijection onto 0..n-1, and that a deleted signal link has its `link_removed` entry. The control re-index checks
  that the goal's renumbered, removed and added links equal netconvert's link map (`declared_crosscheck`: F1
  LSA535_part0 22 renumbered, index 5 removed; F2 266197536 6 renumbered, index 2 added; F3 both). All three goals
  pass with no error. Their table differs from the parent's in exactly the 24 / 8 / 32 declared facts, and every
  other fact is frozen and protected by G0-REGRESSED.
  - F1: 24 entries (1 movement change, 1 link removed, 22 renumbered); scope LSA535_part0 and its 8 incident edges.
  - F2: 8 entries (1 movement change, 1 link added at index 2, 6 renumbered); scope 266197536 and its 4 edges.
  - F3: the union of both (32 entries), with both scopes and both ops.
- **The F2 merge** `603103444|73474895|0:1,2` (basis `model_justified_simulation_fragility`, site S01, label
  "model-justified (simulation fragility S01), no field evidence") is a **twin-layer exemption**. It does not go through
  the core `evidence_justified_destination_merges` mechanism the owner named:
  - `twin/declared_targets.justify_merges` reuses the core key and exact-lane rule and takes the finding out of the
    explore gate's INT rows;
  - the core release audit (`core/connection_mode_audit.py`) accepts only official MAP or engineering-plan evidence,
    so it still reports the merge.
- **In-scope side effects beyond the declared targets** (`materialized_side_effects`, `static-gate.json` →
  `side_effects`). This covers every retained road connection at the scope junctions, op pairs excluded.
  - **F1** (27 retained links):
    - 5 untouched paths are reshaped by 2.50 to 3.88 m (Hausdorff distance between the internal-lane chains): the
      straight paths to 43305966#1 from 186821027#0_1, 186821034#0_1 and 186821035#0_3, the last one with a 4.93 m
      longer path.
    - 3 internal-junction wait points move by 1.00, 1.97 and 3.47 m: the two right turns to 142944337#1 with unchanged
      shape, and 186821035#0_3 -> 43305966#1_1.
    - Path lengths change by -2.50 to +4.93 m on 5 links.
    - Internal-lane speeds change on 2 untouched links: 186821034#0_2 -> 43305966#1_0 10.860 -> 11.344 m/s and
      186821034#0_1 -> 43305966#1_0 9.073 -> 8.942 m/s. A further 6 links change by less than 0.01 m/s.
    - 24 of 27 internal-lane chains are renamed.
    - Right-of-way (response, foes, cont) of every retained link is unchanged apart from removed link 5.
  - **F2** (7 retained links):
    - The three retained straight paths 603103444_2/3/4 -> 73474895_0/1/2 are 0.332 m longer: netconvert equalizes
      lane lengths within an internal edge.
    - No shape moves by more than 1 mm.
    - The shape, speed and length attributes of 3 custom connections are rounded from 12 to 6 decimals (largest
      change 2.3e-10).
    - Right-of-way is unchanged apart from the added link, which is a new foe of the 141050972 links and of the
      603103444_2 sibling.

  A dynamic result of F1 or F3 includes the moved paths, wait points and speeds, and one of F2 includes the +0.33 m.
- INT baseline basis `source`; D1 applies (`source_dirty = true`, 25 inherited structural failures, so promotion is
  blocked without a human); lane artifacts hash-bound; routeability on the default 10 seeds with frozen trips;
  `max_ops_per_round 1`.
- **Executed control** (`twin/control_reindex.py`): every tlLogic of the staged control is re-indexed to the
  candidate's linkIndex layout. Retained links keep their column; F1 drops column 5 at LSA535_part0 (28 -> 27 states);
  F2 adds index 2 at 266197536 (7 -> 8). The map is cross-checked against netconvert's embedded program `0`. **Declared
  resolution, still flagged for owner review:** a verbatim copy of the sibling's column (603103444_2 -> 73474895_0)
  would give the added link `G` against a green foe. The F2/F3 goals therefore declare `copy_rule:
  minor_where_yielding`, which gives `gyrr`, equal to netconvert's own program for the link.
- **Load check** (one SUMO step, never a Calibrate run): the parent and each candidate load with their control and the
  relative-output field loops `source-e1.add.xml`. Both files are copied into the load directory first, where the
  field-counts output now lands. All six loads pass with no error. F1 and F3 drop exactly the "Missing green phase ...
  tl-index 5" warning, and no candidate adds one. All 8589 demand routes stay edge-continuous.
- **Deferred.** S02 is `op_not_expressible` (it needs a compound corridor lane-count op). S04 is deferred (3 insertion
  EB in 106 runs).

## 3. Arms, keys and caps

- **Candidate arm** per candidate F in {F1, F2, F3}:
  - 16 fresh **public** draw keys `fragility/<F>/k1` ... `k16` (fingerprints in `design.json`, none equal to any M2
    key);
  - family `depart_jitter` (U(-0.5, 0.5) s per vehicle, keyed by HMAC of the key and the vehicle id), goal seed 104,
    libsumo;
  - the Calibrate goal above, store `E:\twin-store\fragility`, `--candidate candidates\<F>\candidate.json`.

  The planned request differs from all 46 reference requests only in `candidate`, `inputs_sha256.{net_file,
  signal_program_file, route_file}`, `perturbation.key_sha256` and `runtime.verifier_commit` (plan probe in
  `design.json`). `analyze` repeats this check on every real request. It also checks that each used run is bound to
  the candidate's files and its parent: the request's candidate block, the native net and control, and the ledger's
  candidate id. Any other difference blocks the candidate.
- **Reference arm** (rate-bearing test): every m2-pilot ledger run that `fragility.is_parent_draw` accepts. That means
  no scenario op, no candidate, `depart_jitter`, seed 104, and the parent net and control, with any key. These are
  46 draws:
  - P3 k1-k4, s3-parent k5-k6 and C1 k7-k14;
  - C3 reference 01-16 and C3 negative control 01-16.

  The draws are re-selected by the rule at analysis time and must equal the frozen list. Their native outputs are only
  read. Exposure:
  - S01 episodes are on 603103444_1 only, under the parent's links: 22 of 46 draws have one and 24 have none. On F2's
    own links, all 24 standstills of 60 s or more there (22 wrong-lane, 2 correct-lane on lanes 2/3) would be
    `correct_lane`, which is the round-1 bias.
  - S03: trap episodes 0, link-7 EB 60 (S03 mean 1.30).
  - Unhealthy: 4 (3 S02 collisions, 1 S01 gridlock).
- **Calibration stage arms** (the M2 stage needs K = 16 per arm): both 16-draw secret-key parent arms of C3, fixed by
  their design role rather than by their outcomes:
  - the C3 negative control (0 unhealthy, mean TAE_main 350.2);
  - the C3 reference (3 unhealthy, mean 432.9).
- **Caps**: 16 runs per candidate (48), plus 3 spares in one shared pool, used only for crashed or timed-out runs (no
  complete score).
  - A spare uses the next key of the same candidate (`k17`, `k18`, `k19`, in order), drawn from the pool in the order
    F2, F1, F3.
  - An unhealthy run is scored as it is and never replaced (A2.2). A key without a run, or with an unscored run, ends
    the walk.
  - A run on any other key is an extra run and blocks the candidate (`select_runs`).
  - Planned order: F2, F1, F3, with at most 3 runs in parallel.

## 4. Metrics (exact definitions, `twin/fragility.py`, read from each run's `native` directory)

- **Standstill episode**: in `queues.xml` (period 1 s), the per-lane `queueing_time`. Each local peak of 60 s or more
  is one episode. A drop of more than 1.5 s, or the lane leaving the output, ends it. One still present at the last
  step is `open_at_end`.
- **Usable connection**: a road connection whose controller's executed program shows its link `G`, `g`, `s`, `O` or
  `o` in some phase; uncontrolled connections are usable. On the parent, LSA535_part0 link 5 and LSA104_part0 link 14
  are never usable.
- **Covering vehicles** of an episode on road lane L: the vehicles on L's edge from at most 1.5 s after the start
  until at least 1.5 s before the peak (`vehicle-routes.xml` exit times). An `open_at_end` episode of a stalled run
  reads the vehicles on L from `all-stopped-state.xml.gz`. The status is `wrong_lane` when every covering vehicle's
  next edge is unreachable from L through a usable connection; a mix is `ambiguous` and never counted.
- **Arm-neutral basis (new).** The primary metrics evaluate that predicate with the **parent's** net and executed
  control on both arms (`primary_basis` in each record; `primary_tests` refuses arms with different bases). Lane and
  edge ids at S01 and S03 are the same on every candidate. Under the sharp null of unchanged vehicle behaviour, both
  arms therefore have the same metric distribution.
  - Residual asymmetry, which works against F2: a lane-1 vehicle bound for 73474895 that halts 60 s or more at a red
    light on F2 still counts.
  - In the 46 reference draws, correct-lane standstills of 60 s or more on lanes 2-4 occurred twice.
- **Primary metrics** per run:
  - `S01_wrong_lane_episodes` (F2, F3): wrong-lane standstills by the parent's links (open_at_end included) on the
    lanes of edge 603103444. A vehicle on 603103444_0/_1 bound for 73474895 counts on every net.
  - `S03_metric = S03_trap_episodes + S03_link7_eb` (F1, F3).
    - `S03_trap_episodes` (the owner's definition, new): wrong-lane standstills by the parent's links on
      186821027#0_4. Every covering vehicle's next edge must lie in the parent's `closed_only` set for that lane, the
      edges it connects to only through never-green links. That set is {186821036#1}, via link 5.
    - `S03_link7_eb`: emergency brakings on the internal lanes of 186821027#0_5 -> 186821036#1_0 (parent link 7,
      found by its lanes on every net).
  - **Exposure warning.** No reference draw shows a link-5 trap (0/46): the lane-section hold keeps 0535Z8 cars on
    lane 5.
    - Lane 4 has 2346 standstills of 60 s or more in the reference arm, with a maximum of 69.9 s, about the red time.
      2211 are ambiguous, because lane-5 left-turners cover them; 134 are correct-lane; and 1 is wrong-lane (next edge
      142944337#1, not a trap).
    - The S03 test of F1 and F3 is therefore in practice a test of link-7 EB. Link 7's geometry is unchanged by F1,
      but its wait points and neighbours are not (section 2).
    - The trap rule is conservative: an ambiguous trap is never counted.
- **Secondary, reported**:
  - the same episodes by the run's own links (`S01_wrong_lane_episodes_own_links`, `S03_wrong_lane_episodes_own_links`);
  - all standstills of 60 s or more on 603103444 by status;
  - the lane-4 episodes by status and their maximum halt;
  - gridlock (stall monitor `all_stopped`; root = the stopped vehicle with the longest wait, with its lane, site and
    own-links status);
  - collisions, teleports and EB by site;
  - standstills of 60 s or more and of 300 s or more.

  Site = the census cluster S01..S04 containing the anchor junction, else `new:<smallest junction id of its
  cluster>`.
- **New-site scan**: collisions, teleports, gridlock roots and resolved standstills of 300 s or more outside S01..S04.
- The module reproduces the census on cached M2 runs (store-backed test).

## 5. The rate-bearing test

Per candidate and primary metric, the test is the exact one-sided Wilcoxon-Mann-Whitney, candidate lower than
reference, unpaired, 16 candidate draws against the 46 reference draws.

- Midranks are used; p = the share of all C(62, 16) relabelings whose candidate midrank sum is at most the observed
  one, counted exactly by dynamic programming (conditional on the ties).
- Reject at p <= 0.05. F3 uses Holm's step-down across its two metrics; F1 and F2 have one each.
- The exact difference-of-means permutation is reported, never decisive.
- The draws are exchangeable under the sharp null: the keys are distinct and the reference arm is selected by request
  fields. No adjustment is made across candidates.

Illustrations (`design.json`):

- **S01**: 0, 2, 4 or 6 candidate draws with an episode give p = 0.00023, 0.011, 0.096 or 0.34.
- **S03** (reference link-7 EB counts 0:11, 1:20, 2:9, 3:3, 4:2, 5:1): 16 zeros give p < 1e-5; 12 zeros and 4 ones
  give p = 8e-5; 8 zeros and 8 ones give p = 0.0039; 6 zeros and 10 ones give p = 0.016.

Rare events (gridlock 1/46, collision 3/46) are reported as counts with the binomial upper tail at the reference rate
(`rare_events`). They are never tested.

## 6. Calibration ("no calibration regression detected") and health

`fragility.ni_stage` runs `m2_stage.evaluate_stage(candidate, arm, real=..., gate=frozen_gate(16), mode="screen")`
against **each** of the two stage arms, reading both ledgers and requiring the same goal and real counts.

The frozen gate is `m2-confirmation-K16`, sha256 `cd7db25e...e363a`:

- N1 182-row K-mean worsening <= 8.31;
- N2 init52 <= 13.284;
- N3 totals13 fair-CRPS <= 15.149;
- N4 main130 fair-CRPS <= 31.077;
- H3 margins as in M2;
- primary objective ms4 with g = 10.

**No calibration regression detected** holds when neither stage's label is in `unhealthy`, `guard`, `row_regress`,
`init`, `level_regress`, `fine_regress`, `worse`. The M2 guards only reject (M2 decision 5: no powered non-inferiority
test exists), so this means no regression was detected within parent noise, not non-inferiority. It does not mean the
edit is neutral for calibration.

**Health** is A2 relative, the stage's H2 against each arm:

- no failure class outside the C1 panel's collision and teleport;
- candidate unhealthy draws minus arm unhealthy draws <= 2.

**Claim limit (asymmetric, conservative).** H2 fails on any candidate gridlock, but the parent itself gridlocks at S01
(1 in 46 draws). With p = 1/46, P(at least one gridlock in 16) = 0.30. F2 and F3 can therefore be labelled
`unhealthy` for the parent's own failure. `rare_events` reports the candidate's gridlock count against that rate; it
is not a rule. Public candidate keys label the exchangeability `unverified` in screen mode; this is reported, not a
failure.

## 7. Decision rule per candidate (bound in code: `fragility.decide`, composed by `fragility.analyze`)

1. `blocked` in any of these cases:
   - fewer than 16 scored candidate draws after spares, or an extra run;
   - a request that fails the comparability or file-binding check;
   - a stage against either arm that is missing or blocked (H1).
2. `unhealthy`: H2 fails against either arm.
3. `new_failure_site`: the candidate arm shows a collision, teleport or gridlock root at a site, or a new-site-scan
   site, that no reference draw shows.
4. `not_reduced`: a primary metric does not reject (Holm for F3).
5. `fragility_reduced_calibration_regressed`: every primary metric rejects, but a stage against some arm labels a
   regression.
6. `fragility_reduced`: every primary metric rejects and no calibration regression is detected against either arm.
7. Eligibility: if the static gate did not screen `improved`, any label but `blocked` becomes `diagnostic_only`, and
   the rule's label is kept as `label_if_static_gate_had_passed`. Today this applies to F1, F2 and F3.

The analysis is `astra_twin fragility-analyze --design E:\twin-store\fragility\prereg\design.json --out <new file>`.
It reads both ledgers, recomputes both arms' metrics with the same code, applies keys, spares and the pool, checks
comparability and file binding, and runs the tests, the new-site scan, both stages, `rare_events` and `decide`. The
dry run on the empty candidate ledger (`analysis-dryrun.json`) re-selected the 46-draw reference arm, equal to the
frozen list, and labels every candidate `blocked` ("k1: no run").

## 8. What can and cannot be claimed

- **Can** (only with `fragility_reduced` and a passing static gate): under the supplied model, draws of the candidate
  show fewer of the pre-registered fragility events than draws of the parent, by an exact unpaired test on an
  arm-neutral metric. No calibration regression against the 2026-10-19 counts was detected by the M2 guards within
  the parent's C1 noise. The supplied model means the Calibrate parent, the declared holds, `readme_fixed_A`
  re-indexed, P1 demand with the depart jitter, seed 104 and libsumo 1.27.1.
- **Cannot**:
  - that the edit is the real lane layout or signal plan (the F2 merge is a model-justified twin-layer exemption with
    no field evidence);
  - any traffic-safety claim;
  - promotion, or a new Calibrate parent, without a human (D1, 25 inherited structural failures);
  - a rate for gridlocks or collisions (counts only); a population effect size;
  - generalization beyond this day, demand, holds and seed;
  - for F1 and F3, attribution to the removal of link 5 alone (the side effects in section 2), or any statement about
    link-5 traps (0/46 exposure: the test covers link-7 EB);
  - for F3, a verified chain;
  - that V = 0 is evidence for the edit (it holds by construction).
- A later Calibrate parent built from a passing candidate needs its own goal, re-indexed control and a new 14-draw
  margin panel.

## 9. This round's runs and code

No Calibrate run and no judge run. SUMO tools ran only for the static gate, each logged with wall time in
`gate\wall.jsonl` (round-1 rows kept):

- archive moves: 0.007 s;
- goal-create ×3: 3.3, 3.2 and 3.7 s (no SUMO);
- profile build (verify-edit explore, L3 on the frozen trips, L2 smoke): F1 46.5 s, F2 47.6 s, F3 step 1 45.8 s;
- F3 diagnostic materialization: 5.0 s;
- control-reindex with the contained load check: 1.83, 1.91 and 1.94 s;
- F3 raw profile: 2.1 s (offline).

Offline reads of the m2-pilot store took 74 s in `prereg_data.py` (including the 35.8 s analysis dry run), plus one
36 s scratch probe of the reference arm. The store was only read, through its `runs.jsonl`, `runs\<id>\request.json`
and `runs\<id>\native\`, and its secret folder was never opened.

Code (in `E:\astra-m4`, uncommitted; no core module changed):

- `twin/fragility.py`: arm-neutral primary metrics, the link-5 trap rule, `decide(comparable=, stages=)`,
  `select_runs`, `rare_events`, `ni_stage`, `analyze`.
- `twin/declared_targets.py`: op-implied lint, `implied_errors`, `materialized_side_effects`.
- `twin/control_reindex.py`: `crosscheck_declared` and the contained load check.
- `twin/goal.py`: goal-create checks the delta against the ops.
- `twin/build_profile.py`: the gate claim.
- `twin/runner.py`: the parent check.
- `scripts/astra_twin.py`: `fragility-analyze`, `fragility --parent-net/--parent-control`, and `control-reindex`
  passing a goal's entries.

Tests: 381 passed (every `tests/test_twin_*.py` suite plus `tests/test_repository_navigation.py`), with new regression
tests for each finding. Ruff and `git diff --check` are clean.

**Anchoring.** Before the first run, commit the bound tree (`2562999b...`) together with PREREG.md and PREREG.sha256,
or record the SHA-256 of `PREREG.sha256` (reported with this round) with the owner. A judge's `runtime.verifier_commit`
names HEAD, so run only from that commit; it is an allowed difference from the M2 requests.
