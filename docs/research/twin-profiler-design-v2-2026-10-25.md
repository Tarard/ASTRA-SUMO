# ASTRA Twin Profiler v2: offline count profiler and known-answer gate first, kernel-style loops after

> Dated snapshot of a local working record of the ASTRA Twin Profiler effort, committed so the approved design (v2, after two Codex review rounds) can be read without the author's machine. Paths under `C:\Users\...\scratchpad`, `E:\twin-store` and `E:\astra-*` refer to that machine and are not part of the repository. Superseding decisions are recorded in later dated documents, not by editing this one.

## Executive summary

**What this is.** This design lets agents and humans improve ASTRA's SUMO models the way GPU engineers tune kernels. Each campaign has one frozen goal and a correctness test that every candidate must pass. It reports one benchmark number and a profiler that shows where the remaining error sits and which typed edit could move it.

**What "test" and "profile" mean here**

| | Test (pass or fail, never a score) | Benchmark number | Profile (where the error is) |
|---|---|---|---|
| Build | SUMO loads the network; only the declared scope changed; every scored fact stays measurable; on every routeability seed, every request that arrives on the source still arrives (no new collision, teleport, deadlock, unfinished or unroutable request); exact checks for lane counts, permissions, movements and signal bindings | Mismatched scored facts counted by layer (topology, control, geometry, attribute), then summed numeric excess | The existing `astra network calibrate` per-fact rows, grouped by SUMO element. Each row gets a class: topology, control, geometry or attribute, or not actionable (correspondence unresolved, source conflict, unknown, not expressible with the current ops). Each row is paired with a typed edit op |
| Calibrate | Every vehicle is inserted and arrives, with no teleport, collision or stall (`detector_demand_replay.py:209-214`). Detectors, counted attribute, run flags, hold nodes and request scope are unchanged | TAE over the 130 main 15-min rows, candidate minus parent on identical inputs | Per row: vehicles on a free-flow schedule, late arrivals from earlier bins, and vehicles delayed out of the bin. Per field: request totals against targets, and passages after the window. For delayed vehicles: where they lost time compared with on-time peers on the same route |

**What is measured today.** These numbers come read-only from tracked files, with zero SUMO runs, at commit 0173672. For this round I re-ran copies of the prototype scripts. Values that depend on the free-flow schedule a_v come from one implementation; M1 pins them only after a separately written routine agrees.
- Baseline: TAE 275, SSE 2833, exact 83/130 rows, init52 TAE 97, all182 TAE 372.
- Decomposition: 5770 vehicles on time, 902 late in, 866 delayed out, supply term 53. None of the 8589 passages is earlier than its free-flow schedule. This is an accounting of where vehicles were counted, not an explanation of why.
- Field balance: the 8589 requests are 51 short of the 8640 init+main targets (0119 Z12 15, Z13 15, 0535 Z4 12, 0119 Z5 6, Z11 2, Z4 1).
  - Any change that keeps the request totals therefore leaves TAE_init52 + TAE_main130 at 51 or more.
  - 29 passages fall after 08:00Z (Z5 12, Z4 9).
  - The v1 claims "quantity floor 51" and "224 headroom" are withdrawn.
- Main-only supply bound: it depends on the initialization counts. It is 12 if they stay at the parent's values and 52 if they reach their targets. It is a cumulative-bin bound, so for one field it can exceed that field's request deficit (0119 Z4: 2 against 1).
- Where delayed vehicles lost time, compared only with on-time peers on the same route:
  - 0119 Z13@3600: the 36 delayed vehicles and all 109 on-time peers enter on edge 44532531 by one route. Insertion delay averages 251 s against 66 s. LSA119 is not the anchor.
  - 0535 Z4@10800: there is no single anchor. The 22 delayed vehicles are compared with the 7 on-time peers on their route; the other 28 on-time vehicles enter elsewhere and are excluded. Three co-anchors stand out: +64 s on 551303424 (into 205069138, an actuated signal and a hold node), +55 s on −186713427 (into hold node 14398547) and +51 s on 551303409. Over 12 consecutive edges the delayed vehicles lose 293 s and recover 81 s downstream. All 22 arrived in the next bin. Round 1's single anchor at +114 s is withdrawn.

**What is built first**
1. M1 (about 860 lines, no SUMO):
   - an implementation clone, plus a pinned verifier clone with its own venv;
   - an offline Calibrate profiler that resolves its inputs through the tracked benchmark aliases;
   - golden tests that pin every number above, including an independent a_v routine.
2. M2 (about 920 lines plus at most 278 runs, est. 3.4-10 h wall at 4 workers): the known-answer tier K3 on the demand-timing family. Gate thresholds are fitted on a calibration set, then frozen and hashed. Only after that is the gate scored on held-out known-null cases, known-positive cases, placebos on the real counts, and a promotion pass on fresh keys.
3. M3 (about 890 lines; it does not depend on M2): the Build profiler with per-kind acceptance, a check that connection directions match the geometry, and Build known-answer faults.
4. M4 (about 390 lines): loops without an agent, recorded in an append-only trial log.
5. M5 (deferred, about 1.1k lines plus 4 schema files):
   - the fenced hmz agent loop with a Codex veto;
   - sealed holdouts with secret-committed draw keys;
   - ledger views, schemas and caches.

**Vocabulary.** Every twin record carries the explore status that ASTRA already uses: `measured`, `no_effect`, or `blocked` (as at `core/network_edit_operations.py:1149`). Gate and loop labels sit in separate fields (`screening`, `stop_reason`, `outcome`), and none of them carries promotion authority.

**What stays human**
- `--mode release`;
- correspondence locks;
- freezing a second count day, which needs online access;
- the perturbation family, K and the materiality floor g_min (M2 fits g ≥ g_min);
- any revision after a failed M2 exit;
- sandbox system settings;
- every promotion.

Every result is explore-only and claims nothing about field truth.

## Core idea

Each SUMO twin candidate is treated like a kernel variant:
- a hash-pinned goal fixes the inputs, the scope and the statistic;
- a test rejects broken candidates unconditionally;
- a benchmark compares the candidate with its parent on identical inputs;
- a profiler explains the parent's residual in units an edit can act on.

Three principles separate v2 from v1.

1. **Accounting before causation.** The Calibrate profile rests on two exact identities over simulated passages:
   - the per-row decomposition against a free-flow reference schedule;
   - the per-field balance: requests = init passages + main passages + post-window passages + unpassed vehicles.

   Both locate counts in time; neither says why a vehicle was late. Exit-time localization adds where the time was lost. It compares delayed vehicles only with route-matched on-time peers, reports co-anchors when no single edge dominates, and states what it cannot identify.
2. **Conditional, per-candidate bounds, reported only.** No fixed floor is carried from the parent to its candidates.
   - Each bound is recomputed for the candidate from its own request totals, initialization counts, post-window passages and free-flow times.
   - Each bound carries a conditions block that says which of these were verified.
   - In M1-M5 no rule consumes a bound: it neither refuses nor rejects a candidate, nor stops a loop. Bounds and SOL appear in the profile as context.
3. **Known answers before loops.**
   - Before any Calibrate loop runs, the screening gate is fitted on a calibration set of twin-of-twin cases (K3). It is then frozen and scored on held-out known-null and known-positive cases. A promotion pass on fresh keys measures the promotion rule's power.
   - Health and invariant failures reject unconditionally.
   - Statistics only decide whether a candidate improved. They never establish non-regression through a lack of significance.

**Order of work**
- M1: offline profiler plus an implementation clone and a pinned verifier clone, with no SUMO;
- then M2 (K3 and gate calibration) and M3 (Build profiler and Build known answers) in parallel, since M3 does not use the Calibrate gate;
- M4: loops without an agent, after both;
- M5: fenced agent loop (hmz, Claude actor, Codex veto).

**Placement.** The code goes in a new domain package, `plugins/astra-sumo/src/astra_sumo/twin/`, plus one reproducible script, `plugins/astra-sumo/scripts/astra_twin.py`, as AGENTS.md "Code Placement" asks. Reusable artifact logic goes in `core/`. `tools/` and `server.py` stay untouched until a public surface is intended. All edits happen in an implementation clone; judged runs import only a verifier clone pinned to a committed sha.

## Kernel analogy map

Paths are relative to `plugins/astra-sumo/src/astra_sumo/` unless they start with `benchmarks/`, `.github/` or `pyproject`. Line numbers are at commit 0173672.

| Kernel concept | Build | Calibrate | Change in round 2 |
|---|---|---|---|
| Kernel spec | `astra.twin-goal/v1`: scope (`mutable_edge_ids`, `mutable_junction_ids`) plus the 14 `SCOPED_EDIT_TYPES` (`core/network_edit_operations.py:22-27`); explore takes exactly one op (`:1227-1228`) | Inputs resolved by alias, plus the run config with all four hold nodes `14398547, 205069138, 18653534, LSA104_part0` (tracked `run.json.gz` and `prepared/scenario.json.gz`) and `timeout_s` 4800 from the executed driver | timeout_s provenance |
| Reference implementation | Frozen fact table and frozen scored set | 130 main rows plus 52 init rows; main rows equal `benchmarks/hamburg_ring1_count_window_20261020_v1/field-counts-15min.csv` (130/130 equal) | — |
| allclose(rtol, atol) | Per kind: exact categorical checks (lane_count, permissions, movements, signal binding); `tol_eff > 0` only for numeric kinds | Row worsening limit `m_row`: provisional 3, fitted in M2a | Provisional value declared |
| compute-sanitizer | G0 hard checks | H1 invariants, H2 health, H3 hard guards | — |
| Benchmark number | Lexicographic V | Nominal paired d0 plus a perturbation panel | — |
| Timer noise | Facts are deterministic; routeability varies by seed | Departure jitter is a robustness perturbation, not measurement noise; parent and candidate share draws (CRN) | — |
| Warmup | — | init52 is guarded and never scored | — |
| Stall reasons | Per-fact reason | Accounting identities (decomposition and field balance) | — |
| PC sampling | — | Exit-time localization against route-matched on-time peers, at (edge, downstream node, 60-s window); co-anchor sets; never the TLS link | Route-matched peers |
| Roofline | tol floor, coverage ceiling, op-vocabulary limit (OPX) | Conditional per-candidate bounds (B1 request-total floor on all182, B2 main supply bound), reported with SOL only | No roofline stop |
| Autotuner | — | MILP over observed assignment fractions = **surrogate** for ranking (`core/od_assignment_feedback.py:15-21, :45, :71`) | — |
| Counter validation | K1 X4 faults, K2 Ring1 faults (M3) | K3 twin-of-twin (M2), demand-timing family only; fitted on a calibration set, scored on a held-out set | Split; control family deferred |
| Hidden test set | Facts held out by approach (M4), sealed against the actor (M5) | HO-C3 day 2 tests the structure-plus-refitting procedure; fresh-key draws from M2; secret-committed keys in M5 | Ids renamed |
| Leaderboard | Append-only trial log (M4); ledger views (M5) | Append-only trial log (M4); ledger views (M5) | Log moved to M4 |

## Goal spec

**FORMAT**
- Model: pydantic, in `twin/goal.py`, with `extra=forbid`.
- Location: `E:\twin-store\goals\<goal_id>\goal.json`, outside every repository.
- Export to `schemas/product/` is deferred to M5.
- Changing any field yields a new goal_id. Diffs across goal_ids are refused, the same rule `compare_calibration_scorecards` applies to fact identities (`core/network_calibration.py:517-518`).
- Three things stay outside the goal, so that M2 and code changes do not fork it:
  - **The fitted gate values.** They live in a separate hashed artifact, `E:\twin-store\gates\<goal_id>\gate-<sha8>.json`, which names the protocol hash and the hashes of the calibration-set runs. Every verdict records its sha, and verdicts made under different gate artifacts are never compared. M1 profiles do not depend on the gate, so they stay diffable after M2.
  - **The verifier commit.** It is recorded in every run, profile and cache key. A profile delta across verifier commits is allowed only after the newer verifier reproduces the parent profile byte-identically, apart from timing fields.
  - **The trial log.**
- The goal must reproduce the recorded baseline. Its run block is copied from tracked machine fields, never from prose:
  - seed, step, end, backend and hold nodes from `evaluation/native/run.json.gz`;
  - `all_stopped_timeout_s` and the hold request from `evaluation/native/native-request.json.gz`;
  - `timeout_s` from the executed shared driver. That is `run_case.py:102`, reached through the alias `cases/h4_counter_z11_minus5_z5/measurement-implementation/run_case.py`, whose target is `benchmarks/hamburg_ring1_early_quantity_feedback_20261024_v1/cases/window0/measurement-implementation/run_case.py` (tracked as `.gz`, sha a64a03b8…).
  - Example of why prose is not used: `prepared/scenario.json.gz` `lane_intention_assumptions` names three hold nodes, while its `junction_body_hold_nodes` and the executed `stall_monitor` name four.

**CALIBRATE GOAL (M1-M2, real values)**
```json
{"schema":"astra.twin-goal/v1","goal_id":"calib-ring1-20261019-h4z11m5z5-v1","capability":"calibrate","mode":"explore",
 "verifier":{"python":"3.12.13","sync":"uv sync --python 3.12.13 --frozen --extra dev --extra native",
   "commit":"not a goal field: recorded per run, profile and cache key"},
 "inputs":{"packet":"benchmarks/hamburg_ring1_chronological_od_feedback_20261024_v1","case":"h4_counter_z11_minus5_z5",
   "resolver":"packet-root byte-references.json (keys cases/<case>/..., rule 'Exact raw SHA aliases only', this case's prepared aliases at lines 853-887); targets tracked as <target>.gz, read with a direct .gz fallback",
   "files":{"network.net.xml":"6c52382e7664eca33fceff6fd02ae700d93139a316a3f9f5139d0e139d7a18ce",
            "control.add.xml":"27220b430aa68c24f0ad360c57ee3bd0f4b8dcdc88aead5344bcff00e4f43e3b",
            "demand.rou.xml":"a9f236136ec5aeb32f4bf00ca9df0883b7b4c68615ea136391b025b5fdcf2e81",
            "field-loops.add.xml":"7393b0c6b1f890744621e16d4bb3784c2166c6211fdecd806c562efb07f08ebb",
            "expected-counts.csv":"a648d6ebc0d93920210662ced8f8e6ef9c46a04079a31a8faa1b7cea9232bb02",
            "initialization-counts.csv":"a6a2504c4e652bed33a6e330a5e62fa4c1a601be6f8106d4f5724d5fc933a147",
            "lane-intentions.json":"2b1debcd65c70335547f91f722025b0d1969214389d3ddffe87d55b1ebf32b78"},
   "baseline_run":["evaluation/native/{run.json,native-request.json,command.json,field-counts.xml,vehicle-routes.xml,tripinfo.xml,summary.xml,sumo.log}.gz",
                   "evaluation/contributing-passages.json.gz.gz"],
   "parent_passages":"archived evaluation/contributing-passages.json.gz.gz (reconciled true, inputs_unchanged true); the parent's instant-e1.xml (21,424,405 bytes) is local-only (local-only-large-traces.json)"},
 "run":{"backend":"libsumo","sumo":"1.27.1","seed":104,"step_s":0.1,"end_s":14400,
   "flags":"exactly the template at core/detector_demand_replay.py:154-162 (incl. --time-to-teleport -1, --precision 12, --collision.check-junctions true, --vehroute-output.exit-times true)",
   "lane_intentions":true,"junction_body_hold":true,
   "junction_body_hold_nodes":["14398547","205069138","18653534","LSA104_part0"],
   "all_stopped_timeout_s":300,"timeout_s":4800,
   "provenance":"run.json.gz (seed, step, end, backend, hold nodes); native-request.json.gz (all_stopped_timeout_s, hold); executed shared driver run_case.py:102 (timeout_s)"},
 "observation":{"attribute":"nVehContrib","e1_period_s":900,"e1_begin_s":0,
   "sim_zero_utc":"2026-10-19T04:30:00Z","init_utc":["04:30Z","05:30Z"],"main_utc":["05:30Z","08:00Z"],
   "sim_bin":"[begin,end) on the step-ceiling of the instant-E1 leave time (core/route_sensor_matrix.py:60-69)",
   "field_bin":"three SensorThings 5-min observations per 15-min row; phenomenonTime e.g. 05:30:00Z/05:34:59Z",
   "complete_rule":"score a row only if source_observation_count == expected_source_observation_count (3); otherwise a new goal, never imputation (today 130/130 and 52/52 complete)",
   "lineage":{"main":"benchmarks/hamburg_ring1_count_window_20261020_v1 (manifest.json; field-counts-15min.csv sha ae218727…)",
              "init":"initialization-counts.csv a6a2504c… (04:30-05:30Z; not part of the count-window package)",
              "per_row":"stream_id, source_begin_utc, source_end_utc, quality_status, aggregation_method in expected-counts.csv"},
   "e1_placement":"12 of 13 E1 sit within 0.11 m of the lane end (11 at 0.100 m, 0535 Z9 at 0.109 m); 0535 Z14 sits 1.398 m before it. Simulated counts are therefore stop-line discharge",
   "uncounted":"leave events without occupancy (early_departures, core/route_sensor_matrix.py:54-56) never count: 20 in the parent, 0 of them intended"},
 "baseline":{"tae_main":275,"sse":2833,"max":22,"exact":83,"init52_tae":97,"all182_tae":372},
 "variable_class":{"name":"demand_timing","ops":["cell_depart_shift"],
   "cells":"OD-period cells: 22 OD pairs x 14 departure periods = 308; 295 non-empty, 112 of them carry more than one field",
   "preserve":["request totals per field","routes","intentions","vehicle ids"],
   "frozen":["network","control","detectors","vtypes","hold nodes"]},
 "objective":{"primary":"TAE_main130, candidate minus parent on identical draws",
   "report_only":["SSE","max","exact rows","GEH raw and hourly","station40 (derived)","all182"]},
 "robustness":{"family":"per-vehicle departure jitter U(-0.5,0.5) s, keyed by HMAC(key_k, vehicle_id)","K":4,"K_p":8,"crn":true,
   "status":"declared by a human; calibrated in M2"},
 "gate_protocol":{"alpha":0.05,"g_min":3,
   "provisional":{"g_TAE":10,"m_row":3,"m_init":2,"k_post":5,"delta_NI":"g/2"},
   "provisional_use":"diagnostic gate evaluations before the freeze only; never a verdict that enters the trial log",
   "k3":{"family":"cell_depart_shift","magnitude_grid_s":{"moderate":300,"large":900},
         "positive_cells":"4 single-field cells with >= 10 vehicles (114 eligible), fixed sampler seed",
         "positives":8,"calibration_nulls":12,"validation_nulls":12,"truth":"own key per case, outside the panel"},
   "rules":"Noise and acceptance section 4; hashed with the goal",
   "calibrated":"separate artifact E:\\twin-store\\gates\\<goal_id>\\gate-<sha8>.json; every verdict records its sha"},
 "stop":{"stall_scored":5,"refuted":"3 refuted hypotheses since the last acceptance -> class_exhausted",
   "budget":{"sumo_runs":100,"duration":"8h",
     "basis":"2 promotions (2x16) + confirmatory comparison (16) + 10 scored hypotheses (10x5, parent cached) = 98 runs"}},
 "claim_boundary":"Explore only. Simulated counts under the supplied model and declared driver holds. No field SPaT, no OD identification (308 cells against 130 rows). Accounting identities locate counts, not causes."}
```

**BUILD GOAL (M3, truth-known)**
```json
{"schema":"astra.twin-goal/v1","goal_id":"build-ring1-k2-a1-v1","capability":"build","mode":"explore",
 "source":{"net":"benchmarks/hamburg_ring1_119_control_diagnosis_20261022_v1/nominal-inputs/network.net.xml (sha cc0453749470…, 2,219 lane elements)"},
 "reference":{"facts":"cas:sha256:<hex>","producer":"reference_net_facts@<sha> on the clean net","correspondence":"same ids, confirmed"},
 "frozen":{"scored_facts":[
    {"id":"lane:<id>:stop_line","kind":"stop_line","class":"CTL","accept":"|longitudinal| <= tol_eff","tol_eff_m":0.5},
    {"id":"edges:<id>:lane_count:passenger","kind":"lane_count","class":"TOP","accept":"exact integer"},
    {"id":"lanes:<in>:movement_inventory","kind":"movement_inventory","class":"TOP","accept":"exact lane-pair set"},
    {"id":"pair:<in>><out>:signal_group","kind":"signal_group","class":"CTL","accept":"exact [tls_id, link_index]"}],
   "excluded":{}},
 "scope":{"mutable_edge_ids":["<edge>"],"mutable_junction_ids":["<node>"],
   "ops":["move_stop_line","set_lane_count","add_lane_connection","delete_lane_connection"],"max_ops_per_round":1},
 "objective":{"order":["G0","n_TOP","n_CTL","n_GEO","n_ATTR","sum_numeric_excess"],"tie":"reject"},
 "g0_shape":{"max_vertex_delta":8,"max_heading_change_deg":35,"max_directed_hausdorff_m":3.0,"status":"declared, not calibrated"},
 "guards":{"movement_probes":"manifest_if_supported_else_all_turn_smoke",
   "routeability":{"seeds":[17,42,104,7,23,37,59,71,83,97],"frozen_trips":"per seed, generated from the source alone",
     "hard":"on every seed, the candidate's failing requests must be a subset of the source's: no request that arrives on the source may run, wait, be discarded, teleport, collide or be unroutable on the candidate (core/routeability_audit.py:315-334, :423)",
     "reported_only":["travel time","arrival time"]}},
 "claim_boundary":"Model-truth known-answer goal; no field claim."}
```

**GOAL LINT** (refused at goal-create)
- A numeric target without a tolerance, or with `tol_eff` below max(2σ, the 0.01 m or 0.01 m/s representation step).
- A `junction_surface` with `minimum_iou` ≥ 1.
- A categorical target given a numeric tolerance.
- A target whose frozen class is COR, SRC, OPX or UNK.
- A scope with no target.
- A Build goal without a `g0_shape` block.
- Calibrate goals only:
  - a hold-node list that differs from the baseline `run.json`;
  - an observation block missing any field above;
  - a class whose `preserve` list conflicts with its ops;
  - a `gate_protocol` without `alpha`, `g_min`, provisional values or the K3 magnitude grid.

**ACTOR OUTPUT (M5).** `work/op.json` for Build or `work/hypothesis.json` (`astra.scenario-ops/v1`) for Calibrate. In both, the class is derived from the diff. Lint refuses an out-of-class hypothesis before submission, and refuses explicit vehicle ids from an agent.

`move_stop_line` takes an absolute `end_offset_m` (`core/network_edit_operations.py:134`), so the suggested value is the current endOffset plus `longitudinal_offset_m`. In the recorded 2349 trial an absolute endOffset of 4.73 m moved the error from +4.73484 m to +0.00494 m (`benchmarks/network_calibration_followup_20261016_v1/README.md:51-53`).

## Test tiers

The tiers climb in order and stop at the first failure. Only a full-horizon run of the exact hashed candidate can lead to acceptance.

**CALIBRATE**

| Tier | Runtime | Content | Proves / cannot prove |
|---|---|---|---|
| C0 static | ≤5 s, no SUMO | Resolve and hash inputs (packet-root alias resolver); the static parts of the INV-* rules (M1 part 1, M2 part 2; see Holdout and anti-gaming); the class derived from the diff equals `goal.variable_class`; per-field request totals compared with the parent's (reported, with B1's condition) | Legality only |
| C1 judge | Recorded: 176.6 s for this baseline and 188.5 s for its static-history reproduction (`sumo.log.gz` Duration). Other tracked full 14400-s Ring1 runs (7,723-8,640 vehicles) took up to 519.3 s; older configurations took 546-1,052 s. Planning: 176.6 s expected, 519 s pessimistic | `run_point_detector_demand` (`core/detector_demand_replay.py:64-75`) with `lane_intentions_file`, `junction_body_hold=True` and the four hold nodes, on the nominal draw plus the goal's K perturbation draws. Parent runs are cached by content hash | Health and the paired statistic on visible data; not generalization |
| C2 offline profile | Target ≤10 s end-to-end CLI. Prototype in-process: 0.35 s (decomposition) and 0.20 s (localization), both including gzip and XML parsing, excluding interpreter start-up | Score, decomposition, field balance, conditional bounds, localization, integrity warnings | Accounting only |
| Gate | ms | `twin/gate.py` pure function (see Noise and acceptance) | — |
| C3 promotion (M5; the same rule serves as the M4 confirmatory comparison) | 16 runs, est. 12-35 min at 4 workers | 8 fresh keys for parent and candidate; in M5 the Codex veto, then a human | Robustness on unseen draws |
| C4 milestone (human) | — | Day-2 structure-plus-refitting protocol | Transfer of the procedure |

- Prefix, cordon, meso and checkpoint-resume shortcuts are deferred. When added, they may only reject, only on health, and only after a parity test.
- Recorded counterexample: a zero-error 900-s prefix still gave a full-run MAE of 5.469.

**BUILD**

| Tier | Content |
|---|---|
| L0 static, ≤2 s | `validate_scoped_operation` (`core/network_edit_operations.py:463`); op type in `goal.scope.ops`; hashes of facts, lock and frozen set |
| L1 materialize and static profile | `verify_network_edit(..., mode="explore")` (`core/network_edit_applicability.py:319`; explore takes one typed op, `core/network_edit_operations.py:1227-1228`), then `twin/build_profile.py` on the frozen scored set, plus the direction-consistency check; Hamburg est. 5-20 s |
| L2 deterministic probes | `run_candidate_movement_probes` (`core/movement_routeability.py:66`) when a supported manifest exists, otherwise `run_all_turn_movement_smoke` (`:246`) on every touched junction |
| L3 routeability guard | `run_routeability_audit` (`core/routeability_audit.py:350-366`, defaults 100 vehicles, seed 42) on 10 seeds with per-seed frozen trip files; hard rule only (see Noise and acceptance). Explore today always runs one pair at seed 42 (`core/network_edit_operations.py:1297-1311`), so a `routeability_seeds` kwarg is needed |
| L4 promotion (M5) | Held-out facts re-scored, Codex veto |
| L5 release | Human only: `astra network verify-edit --mode release` and `calibrate --mode release` with the locked correspondence |

**K - KNOWN-ANSWER TIER** (validates the profiler and the gate, before any loop)

- **K3 Calibrate twin-of-twin (M2, demand-timing family only).**
  - Cases: known-positive; known-null, split into a calibration set and a held-out validation set; placebo-on-real; plus a promotion pass. The protocol and run counts are in Noise and acceptance §4.
  - The control family (a cycle-preserving split shift on one of the 10 static tlLogics in the control file) is deferred. Under this demand_timing goal, C0 and INV-ONE-CLASS make every such candidate invalid, so those cases could never be accepted. That family needs a control-class goal with its own K3.
- **K1 X4 independent faults (M3).**
  - Sources: `build_synthetic_fixture` (`corridor/synthetic_networks.py:62`) and `apply_synthetic_mutation` (`:100`, registry `:1040-1064`). They are X4-only: W_in/E_out ids, never Hamburg nets.
  - Observable through movement facts (`core/network_calibration.py:394-420`): wrong-to-lane, crossed-lane-map, missing-connection, illegal-movement, missing-legal-movement.
  - Observable through signal-binding facts (`:421-431`; two bindings ≠ one expected): shared-link-index-conflict, out-of-range-link-index, duplicate-connection.
  - Not fact-observable: wrong-turn, which changes only `connection.dir` (`corridor/synthetic_networks.py:859-860`).
    - The new direction-consistency check must surface it as INT.
    - Inside its own scope, the NEMA binding gate already blocks such a mismatch (`core/standard_nema_binding.py:857-866`). M3 records which check fires.
  - Internal mutations (request-bit-order, asymmetric-foes, phase-state-length and others) must surface as INT through the audit.
- **K2 Ring1 nominal net (M3).** Sha cc0453749470…, 2,219 lane elements.
  - Ledger-injected faults: a mechanics test, labelled not independent.
  - Raw-XML perturbations. A raw per-lane shape change has no inverse typed op, so it must surface as OPX.

**CODE-CHANGE TIERS** (run only in the implementation clone `E:\astra-impl`; judged runs happen only in the verifier clone at its recorded commit)
- T0: ruff and `git diff --check` on changed files.
- T1: focused pytest per domain, with a `sumo --version` 1.27.1 preflight and `-rs`, so SUMO-gated tests cannot skip silently.
- T2: full suite per PR.
- No pytest ever runs in E:\ASTRA-SUMO. Another session edits it.

## Profile format

**SCHEMA.** `astra.twin-profile/v1`, built by `twin/count_profile.py`, `twin/bounds.py` and `twin/build_profile.py`, and rendered by `twin/render.py`. Each call writes a new directory, `E:\twin-store\profiles\<id>\`, containing:
- `profile.full.json`;
- `profile.compact.json` and `.txt`, capped at 6,000 characters (top 5 hotspots, at most 6 rows each, then "+N more");
- detail files referenced by path and sha256: `rows.csv`, `decomposition.csv`, `field-balance.csv`, `localization.csv`, scorecards.

Every profile records its verifier commit and carries the explore status `measured`. The actor view, which strips held-out and per-draw values, arrives with the actor in M5.

**STABLE IDS** (content-derived, so diffs line up across rounds)

| Prefix | Form |
|---|---|
| F | fact id from the table |
| element | `lane:<id>`, `edge:<id>`, `junction:<id>`, `conn:<from>_<fl>><to>_<tl>`, `tls:<id>#<linkIndex>` |
| R | `R:<detector alias>@<begin_s>`; e.g. 0119Z13 = hh_0119_Z_13_34018 |
| H | `H:<class>:<anchor>`, or `H:LOC:co:<hash8 of sorted anchor ids>` for a co-anchor set; a persisting hotspot keeps its id |
| c | `c-<12 hex of sha256(net, control, demand, additional)>` |

- Audit groups use `audit:<kind>:<hash8 of sorted node ids>`.
- Source lines come from a byte index of `id="..."` occurrences.

**CALIBRATE COMPACT (parent profile, M1 output).** Every value was re-derived read-only from tracked files.
```json
{"schema":"astra.twin-profile/v1","kind":"calibrate","view":"compact","status":"measured","goal":"calib-ring1-20261019-h4z11m5z5-v1@<8hex>","verifier":"<sha40>","cand":"c-<12hex>","parent":null,
 "inputs":{"resolved":"7/7 aliases, sha256 verified","hold_nodes":["14398547","205069138","18653534","LSA104_part0"],"passages":"archived report, reconciled"},
 "score":{"tae_main":275,"sse":2833,"max":22,"exact":"83/130","init52":97,"all182":372,"geh_raw_lt5":"100% (max 3.24)","geh_hourly_lt5":"99.23% (max 6.49; 2 x geh_value on 15-min counts)"},
 "integrity":{"healthy":true,"completion":"8589/8589 arrived; 0 teleport, collision, waiting","emergency_braking":5,
   "sumo_warnings":{"actuated_phase_without_controlling_detector":"20 warnings = 10 phases on 9 tlLogics, each reported for program '0' and 'readme_fixed_A'","missing_green_phase":2,"unused_states":5},"early_departures":"20 (0 intended)"},
 "decomp":{"kind":"accounting identity against the free-flow lower-bound schedule","ontime":5770,"late_in":902,"delayed_out":866,"supply_term_abs":53,
   "ff_violations":"0/8589","delay_vs_ff_s":{"p50":126,"p90":375,"p99":517}},
 "field_balance":{"identity":"R_init + R_main = -(deficit + post + unpassed)","deficit_total":51,"post_window":29,"unpassed":0,
   "fields":{"0119Z13":"init -32, main +13, deficit 15, post 4","0119Z12":"-23, +5, 15, 3","0119Z5":"-5, -13, 6, 12",
             "0119Z4":"-2, -8, 1, 9","0535Z4":"-1, -12, 12, 1","0119Z11":"-2, 0, 2, 0","7 fields":"all zero"}},
 "bounds":{"use":"reported only; no rule consumes a bound in M1-M5",
   "B1_all182_request_floor":{"value":51,"applies_if":["candidate's per-field request totals, read from its demand","INV-PASS on the candidate run"],"verified_on_parent":true,"parent_all182":372},
   "B2_main_supply":{"value":12,"at_init_targets":52,
     "applies_if":["a_v recomputed from the candidate's net and demand","the candidate's own measured init counts","INV-PASS on the candidate run"],"verified_on_parent":true,
     "note":"cumulative-bin bound; per field it can exceed the request deficit (0119 Z4: 2 at init targets against deficit 1)"},
   "surrogate":"MILP over observed assignment fractions: ranking only"},
 "hot":[
  {"id":"H:LOC:entry:44532531","rows":{"R:0119Z13@3600":"-4 (planned 145 = y; ontime 109, late_in 32 = the field's init shortfall, delayed_out 36)"},
   "peers":"route-matched: 36 delayed_out and 109 on-time, all entering on 44532531 by one route prefix",
   "where":"insertion delay 251.4 s vs 66.3 s (+185.1 s); next largest +8.9 s on 199500674#0 (into TLS 6390683114)",
   "cannot_tell":"insertion capacity vs spillback onto the entry edge","knob":"demand timing | network entry","conf":"accounting"},
  {"id":"H:LOC:co:<8hex>","rows":{"R:0535Z4@10800":"-22 (planned 57 = y; ontime 35, delayed_out 22, all measured in 11700)"},
   "peers":"route-matched: 22 delayed_out vs the 7 on-time peers entering on -983227468#1 by the same route prefix; 28 on-time peers enter on 771709146#0 and are excluded",
   "co_anchors":[
     {"edge":"551303424","node":"205069138","diff_s":64.3,"means_s":"126.7 vs 62.4","n":"22/7","flags":["junction-body hold node: declared driver assumption","actuated TLS 205069138: 4 candidate links to 189775480"]},
     {"edge":"-186713427","node":"14398547","diff_s":55.4,"means_s":"55.4 vs 0.0","n":"22/7","flags":["junction-body hold node: declared driver assumption"]},
     {"edge":"551303409","node":"265093112","diff_s":51.2,"means_s":"51.5 vs 0.3","n":"22/7","flags":[]}],
   "spread":"+293.1 s over 12 consecutive edges from -186713425 to 551303424; -81.0 s recovered downstream (189775480 -62.6 s, field edge -18.4 s)",
   "cannot_tell":"queue along the approach vs spillback from downstream vs hold side effects; which lane or link; red vs blocking","knob":"structural hypothesis only"},
  {"id":"H:BAL:field:0119Z5","rows":"main -13 = -(deficit 6 + post-window 12) + init shortfall 5","knob":"quantity (solver) or timing"}],
 "claim":"Accounting over simulated passages under the supplied model; no cause, no field SPaT, no OD identification."}
```

**TEXT RENDERING** (real)
```
== astra-twin calibrate calib-ring1-20261019-h4z11m5z5-v1 parent c-<12hex> (4 hold nodes) status measured
SCORE  TAE 275 | SSE 2833 | max 22 | exact 83/130 | init52 97 | all182 372 | GEH_h<5 99.23%
DECOMP ontime 5770 late_in 902 delayed_out 866 supply 53 | ff violations 0/8589 (accounting)
BAL    request deficit 51 (Z12 15, Z13 15, 535Z4 12, Z5 6, Z11 2, Z4 1) | post-window 29 | B1 all182 >= 51 if totals kept
BOUND  B2 main supply 12 @ parent init counts, 52 @ init targets (conditional, reported only)
HOT1   entry 44532531: Z13@3600 delayed_out 36 vs 109 same-route peers, insertion +185 s
HOT2   0535Z4@10800 delayed_out 22 vs 7 same-route peers, co-anchors: 551303424>205069138 +64 s (TLS, hold) | -186713427>14398547 +55 s (hold) | 551303409 +51 s; +293 s over 12 edges
INTEG  emergency braking 5 | actuated phases w/o detector 10 (20 warnings, 2 programs) | missing green 2
```

**CANDIDATE DELTA (M2 onward).** Keyed by stable ids. Refused across goal_ids and across verifier commits, unless the newer verifier has reproduced the parent profile. It contains:
- the explore status, the screening label with its first failing rule, and the gate artifact sha;
- d0, the panel mean and upper confidence bound, and the number of worse draws;
- every one of the 182 rows that worsened beyond `m_row`;
- per-field balance deltas (Δinit, Δmain, Δpost), which expose transfers between windows;
- the candidate's own bound conditions table;
- the prediction score if a hypothesis was submitted.

**BUILD COMPACT** (the recorded 2349 trial in this format; values from `benchmarks/network_calibration_followup_20261016_v1/README.md:42-66`)
```json
{"schema":"astra.twin-profile/v1","kind":"build","view":"compact","status":"measured","goal":"build-hh2349-a1-v1@<8hex>",
 "op":"move_stop_line lane:554713076#0_2 endOffset 0 -> 4.73",
 "gate":{"screening":"dynamic_hard","first_fail":"seed 42: 37 vehicles running and 1 waiting at 2400 s (source 0 running)","G0":"pass","outside_fact_changes":0,"measurability_lost":[]},
 "V":{"cand":[0,0,0,0,0.0],"parent":[0,1,0,0,8.47]},
 "fixed":["F:2349:lane:2:stop_line +4.73484 -> +0.00494 m (tol 0.5)"],
 "escalations":[
   {"id":"H:COR:lane:554713076#0_2","fact":"F:2349:lane:2:centerline","mean_lat_m":2.560,"reference_coverage":0.987,"status":"correspondence unresolved; geometry neither confirmed nor refuted"},
   {"id":"H:COR:lane:554713076#0_3","fact":"F:2349:lane:3:centerline","mean_lat_m":2.815,"reference_coverage":0.895,"status":"correspondence unresolved"}],
 "source_state":"dirty (1 collision, 1 teleport): not promotable without a human"}
```
In v1 the same trial passed as "measured_dynamic_unverified". The hard rule rejects it now, because requests that arrived on the source stayed unfinished on the candidate under the same seed.

**TOKEN BUDGET** (M5 actor): about 5k tokens injected per turn (TASK 800, STATE 600, compact 1,500, NOTES ≤1,000, last 5 trials 500). Mid-turn caps: drill ≤800 tokens, element ≤600.

## Build profiler

**1) DATA FORM.** No new contract; `astra.network-facts/v1` stays unchanged. Each row is an existing NetworkFact plus derived profile-only fields: class, `tol_eff`, excess, anchor element, suggested op, est_le.
- Producer data goes into the free-form `mapping_diagnostics`.
- A reverse index {SUMO object id → fact ids} is built once per net sha (`twin/ids.py`).

**2) PER-KIND ACCEPTANCE** (replaces v1's blanket threshold rule and excess/tol normalization)

| Kind | Check today in `core/network_calibration.py` | Acceptance | Contribution to V | Zero or missing tolerance |
|---|---|---|---|---|
| lane_count | `:370-383` integer equality per vClass | exact | n_TOP; reports \|obs−exp\| | not applicable |
| lane_permissions | `:314-323` set equality | exact | n_TOP; reports \|symmetric difference\| | not applicable |
| movement_inventory | `:394-420` lane-pair set equality | exact | n_TOP; reports FP+FN | not applicable |
| signal_group (binding) | `:421-431` list equals [{tls_id, link_index}] | exact | n_CTL | not applicable |
| stop_line | `:327-341` \|longitudinal\| ≤ tol_long, or distance ≤ tol | numeric | n_CTL; (\|long\| − tol_eff)⁺ / tol_eff | lint requires tol_eff ≥ max(declared, 2σ, 0.01 m) |
| lane_centerline | `:343-369` max lateral ≤ tol, coverage ≥ minimum, optional mean ≤ max | numeric | n_GEO; (max_lat − tol_eff)⁺ / tol_eff | same |
| lane_width | `:302-307` | numeric | n_ATTR | same |
| speed | `:308-313` | numeric | n_ATTR | ≥ 0.01 m/s |
| junction_surface | `:432-443` IoU ≥ minimum_iou | numeric (lower bound) | n_GEO; (min_iou − IoU)⁺ / (1 − min_iou) | lint requires min_iou < 1 |

- The categorical kinds always decide matched or mismatched; they never return "threshold not declared".
- In explore mode, numeric kinds without a threshold return `measured` (`:449-451`). Such facts never enter the frozen scored set.

**3) FROZEN SCORED SET.**
- At goal-create the verifier scores the source and freezes each scored fact with its kind, class and acceptance.
- Every scored fact must stay matched or mismatched on every candidate. Anything else is `measurability_lost` and fails G0.
- This closes three holes:
  - a deleted lane on an unresolved fact becomes unknown (`:270-283`);
  - an explore-mode ValueError becomes unknown (`:460-468`);
  - `compare_calibration_scorecards` flags only matched → not matched (`:523-524`).
- Change: an additive `measurability_lost` key in `compare_calibration_scorecards` (about 10 lines).

**4) CORRESPONDENCE, SOURCE, OP VOCABULARY** (frozen at goal-create, never recomputed on a candidate)

a) COR, correspondence unresolved.
- Applies when any of these holds:
  - `diagnose_lane_match` (`core/lane_correspondence.py:48-74`) gives confidence conflict, low or none;
  - the fact is unlocked and its margin to the best index-shift alternative is below max(1 m, 2σ);
  - the mapping is unresolved.
- A COR fact is never a task.
- Index-shift probe. This is a heuristic; it replaces v1's GEO_XSEC "proof".
  - For each approach, take the minimum of the maximum lane offset over the primary mapping (`:50-51`) and the alternative shifts −1 and +1 that `:54-56` generates.
  - If no mapping fits within tol_eff, the profile says "a geometry error is likely under every mapping tried". That is still no proof: other mappings (another edge, split or merged lanes, a missing lane) are not covered.
  - If some mapping fits, the fact stays COR. A fit under some mapping never proves the geometry correct.
- Applied to 2349: lanes 2 and 3 (2.560 m and 2.815 m mean lateral offset) are "correspondence unresolved; geometry neither confirmed nor refuted". The profiler escalates them with a review render that draws the alternatives.
- One human signature per site writes `correspondence.lock.json`, which feeds `confirmed_map_lane_ids` (`core/hamburg_network_calibration.py:104`).

b) SRC, source conflict.
- Two sources disagree by more than 2·sqrt(s1² + s2²), or the source date gap exceeds 365 days.
- Values are never averaged. Resolution order: authority rank, then the date nearest the target, then a human.

c) OPX, not expressible with the current op vocabulary.
- The 14 typed ops (`core/network_edit_operations.py:22-27`) set one width per lane and put the stop line at the lane end via endOffset. Lane shapes move only through `set_edge_shape`.
- These are **limits of the op vocabulary, not of SUMO**: PlainXML supports per-lane shapes.
- A fix that needs a lane-specific shape escalates as `op_not_expressible`. A `set_lane_shape` op would be a candidate code change through the implementation flow, never a site module.

**5) DIRECTION CONSISTENCY** (new INT check, about 40 lines; it generalizes an existing check)
- Existing coverage:
  - `core/standard_nema_binding.py` already compares SUMO `dir` with geometry, inside its own scope: one program controlling one 3- or 4-arm junction (`:1928-1933`). It computes `geometry_turn = _turn_from_delta(delta)` (`:857`), adds a mismatch blocker (`:863-866`) and marks the movement `blocked_turn_semantics` (`:909`).
  - `intersection/candidate_binding.py:44-56, :254-256` flags `sumo_direction_status` against the expected OSM-DAG direction.
  - Only `_connection_turn` (`core/connection_mode_audit.py:2909-2913`) trusts `dir` whenever it is present.
- New: the same comparison on every non-internal connection of the candidate.
  - It reuses `_normalize_signed_angle` and `_turn_from_delta` (`core/standard_nema_binding.py:1218-1230`: s ≤ 35°, l/r 45-135°, t ≥ 145°, otherwise ambiguous).
  - Input: the signed heading change from the from-lane's last segment to the to-lane's first segment, or along the via lane, with left positive as at `:854-857`.
  - It flags only a definite geometry turn that disagrees with `dir`. L and R are compared by side only. Ambiguous geometry is reported, never flagged.
- Acceptance: zero findings on the clean X4 fixture and on the clean Ring1 nominal net, and K1 wrong-turn is found.

**6) sg_partition** (derived CTL check, no new fact kind)
- MAP gives one `signalGroup` string per MAP connection (`MapConnection`, `core/digital_twin.py:137-144`; parse `:441-451`). It carries **no conflict matrix**, so v1's "MAP-conflicting groups must never be green together" rule is dropped.
- The check runs only on **evidenced bindings**: locked correspondence for both the MAP ingress and egress lanes, and a SUMO connection between them with `tl` and `linkIndex`.
- Rule: links bound to one signal group show the same colour class in every phase. SUMO's state alphabet is r, y, g, G, s, u, o, O; the tracked control file uses G, O, g, r, u and y.
  - {G, g} → green;
  - {y} → amber;
  - {r} → red;
  - {u} → red-amber;
  - {o, O, s} → a declared mapping, or the link is unscored.
- G against g is **not** a colour mismatch: it encodes SUMO priority (g must yield). Checking protected against permissive needs SPaT movement event states, which the repository does not ingest. The raw maneuver bits (`:450`) do not carry them, so priority stays unscored.
- Partition consistency is necessary, not sufficient, for correct signals.

**7) CLASSES AND SUGGESTED OPS**

| Class | Kinds |
|---|---|
| TOP | lane_count, movement_inventory, permissions |
| CTL | stop_line, signal binding, sg_partition |
| GEO | centerline (locked or high only), junction_surface |
| ATTR | speed, width |
| INT | load or integrity failure, direction inconsistency, new protected-green foes, in-scope audit findings |
| DYN | static facts fine but an L2/L3 guard regressed |
| Not tasks, escalations only | COR, SRC, OPX, UNK (expected=None) |

| Fact kind | Op |
|---|---|
| stop_line | `move_stop_line{lane_id, end_offset_m = current + longitudinal_offset_m}` |
| lane_count | `set_lane_count` (verify-edit remaps selectors, `_remap_lane_count_facts` at `core/network_edit_operations.py:406`) |
| movement missing | `add_lane_connection` |
| movement extra | `delete_lane_connection` or `suppress_turnaround` |
| permissions | `set_lane_permissions` |
| speed | `set_speed` |
| width | `set_lane_width` |
| centerline | `set_edge_shape` |
| junction surface | `set_junction_shape` |
| binding | `relocate_tls_control` or `demote_duplicate_tls` |

- A single-fact case is applied by the flow without an agent first.

**8) HOTSPOT RANKING.** Rows aggregate by anchor:
- edge: centerline, width, speed, lane_count;
- lane: stop line, permissions;
- junction: movements, surface, direction;
- tls#link: signal.

Sort order: class (INT > TOP > CTL > GEO > ATTR), then the sum of normalized numeric excess (categorical rows count 1), then traffic weight, then stable id. Lint rows (standard audit, `rank_traffic_effects` at `core/traffic_effect_review.py:26`) are shown apart from fact residuals.

**9) DYNAMICS AS GUARDS, NEVER A GRADIENT.** Deterministic movement probes (L2) come first. The seed-level routeability guard (L3) applies only the hard rule (see Noise and acceptance).

**10) PRODUCERS**
- P1 Hamburg MAP/KML via `write_hamburg_network_facts` (`core/hamburg_network_calibration.py:101`). It emits lane_count and movements as unresolved, and surface and signal_group with expected=None, so release pass is unreachable from this adapter alone.
- P2 `core/reference_net_facts.py` (new, about 180 lines; all 9 kinds from a clean net, same ids). It powers K1 and K2.
- P3 human aerial labels on `astra review render` output.
- P4 OSM tags, a build-fidelity lint only, never field targets (M5+).
- P5 truth by construction (tier K).

## Calibrate profiler

**1) OBJECTIVE**
- Gated statistic: TAE over the 130 main rows, paired against the parent on identical draws.
- Report-only:
  - SSE, max |r| and exact rows;
  - hourly GEH, computed locally in `twin/count_profile.py` as 2 × `geh_value` (`core/detector_demand.py:1026`) on the 15-min counts. GEH on hourly flows (4× the counts) equals twice the 15-min GEH, so no core edit is needed;
  - station40 (derived evidence, not independent) and all182.
- Exact 130/130 is no longer a tier. It is reported only, because sensor error is unknown and counts do not identify routes or OD.

**2) RESIDUAL DECOMPOSITION** (new `twin/count_profile.py`; an accounting identity)

Definitions:
- Free-flow earliest arrival: a_v = scheduled depart + (L_e0 − departPos)/v_e0 + Σ_edges up to the field [min internal-path time + L_e/v_e] + pos_E1/v_lane.
  - v is the lane speed. The tracked vType has speedFactor 1, speedDev 0 and sigma 0, no maxSpeed, and departSpeed max.
  - On each edge, use the shortest lane length and the highest lane speed.
  - Acceleration, yielding and insertion delay are ignored, so a_v is a lower bound on the passage time.
- planned_fb = {v : field(v) = f, a_v ∈ bin b}.
- M_fb is the set of measured contributing passages. In M1 they come from the archived parent report `evaluation/contributing-passages.json.gz.gz` (reconciled true, inputs_unchanged true), because the parent's instant-e1.xml is local-only. From M2 on, every judged run calls `read_contributing_e1_passages` (`core/route_sensor_matrix.py:15-79`).
- Identity: r_fb = (|planned_fb| − y_fb) + |M_fb \ planned_fb| − |planned_fb \ M_fb|, i.e. supply term + late_in − delayed_out.

The identity holds for **any** reference schedule: it is set algebra given INV-PASS. Its information comes from the choice of schedule and from the check that the schedule is a valid lower bound on this run. It locates counts in time and establishes delay relative to the chosen free-flow schedule. It does not show that any control or network change can recover the delayed vehicles.

Verified read-only on the tracked case (prototype 0.35 s in-process, including parsing):
- 0 of 8589 passages are earlier than a_v; 0 off-intention, 0 repeated and 0 unpassed;
- ontime 5770, late_in 902, delayed_out 866, supply term 53;
- the identity reproduces TAE 275;
- delay against free flow: p50 126 s, p90 375 s, p99 517 s.

Independent check before pinning:
- These values come from one implementation of a_v.
- M1's golden test computes a_v with a separately written routine. Both must agree on all 8589 vehicles before any value is pinned.
- M2(a) adds a toy-net SUMO check: single vehicles on an empty net pass no earlier than a_v, and within 1 s of it.

| Row | y | Planned | Measured | ontime / late_in / delayed_out |
|---|---|---|---|---|
| 0119 Z13@3600 | 145 | 145 | 141 | 109 / 32 / 36 |
| 0119 Z12@3600 | 119 | 119 | 114 | 91 / 23 / 28 |
| 0535 Z4@10800 | 57 | 57 | 35 | 35 / 0 / 22 (all 22 measured in 11700) |
| 0119 Z13@11700 | 118 | 103 | 138 | 99 / 39 / 4 |

**3) FIELD BALANCE IDENTITY** (new; window-aware)
- Under INV-PASS, every intended vehicle of field f passes at most once, so N_f = M_init,f + M_main,f + P_f + unpassed_f, where P_f is the number of passages after 12600 s.
- With deficit_f = E_init,f + E_main,f − N_f fixed by the request totals: R_init,f + R_main,f = −(deficit_f + P_f + unpassed_f).

On the parent, the 8589 requests against 8640 targets give a deficit of 51:

| Field | N_f | R_init | R_main | deficit | post-window | Reading |
|---|---|---|---|---|---|---|
| 0119 Z13 | 1885 | −32 | +13 | 15 | 4 | Main surplus = init shortfall 32 − deficit 15 − post 4 |
| 0119 Z12 | 1537 | −23 | +5 | 15 | 3 | Same pattern |
| 0119 Z5 | 618 | −5 | −13 | 6 | 12 | Post-window spill dominates |
| 0119 Z4 | 759 | −2 | −8 | 1 | 9 | Post-window spill dominates |
| 0535 Z4 | 483 | −1 | −12 | 12 | 1 | Request deficit dominates |
| 0119 Z11 | 256 | −2 | 0 | 2 | 0 | — |
| 7 other fields | — | 0 | 0 | 0 | 0 | Exact field totals |

Consequences:
- Fixed request totals do **not** freeze main-window totals. Vehicles move across 3600 s and 12600 s.
- The late_in of Z13@3600 and Z12@3600 (32 and 23) equals those fields' init shortfalls (32 and 23). Every one of these late vehicles was planned for the initialization period.
- Free-flow planned init arrivals equal the init targets in all 13 fields, U_f(3600) = E_init,f, and every vehicle could reach its field before 12600 s.
- Over all182, field totals account for 80 = 51 + 29 + 0 of the 372 TAE. The rest is within-field timing.

**4) DELAY LOCALIZATION FROM EXIT TIMES** (what the current output can and cannot identify)

The judge writes `--vehroute-output.exit-times true` but not internal edges or lanes (`core/detector_demand_replay.py:159`). The tracked `vehicle-routes.xml.gz` gives the actual insertion depart, the actual departLane, the edges and the exitTimes.

It identifies:
- the insertion delay, exactly: actual depart − scheduled depart;
- per ordinary edge e_i, the excess Δ_i = exit(e_i) − exit(e_{i−1}), minus [the minimum internal time from e_{i−1} + L_i/v_i].
  - Δ_i includes waiting at the stop line at the end of e_i and the time on the junction-internal lanes **before** e_i.
  - So the anchor is (e_i, its downstream node, 60-s window), and it is ambiguous with respect to the upstream junction's internals.
- on the field edge, the time truncated at the detector passage, because time after the E1 cannot affect the count.

It cannot identify:
- the lane used;
- the TLS link when an edge pair has more than one controlled connection. On the 22 delayed_out routes of 0535 Z4@10800, every controlled edge pair has 2-5 candidate links;
- red-light waiting as opposed to spillback from downstream;
- insertion capacity as opposed to spillback onto the entry edge;
- a queue moving along the approach as opposed to a downstream bottleneck;
- any causal effect.

**Method: a differential contrast against route-matched peers.** Delayed_out vehicles are compared with on-time vehicles of the same planned bin.
- Only on-time peers with the same entry edge, and the same route prefix up to the field edge where available, enter the contrast.
- Each anchor averages only over vehicles that traverse it, and n is reported per group.
- An anchor with no route-matched peer is `unmatched` and is not ranked.
- Anchors whose differential lies within max(15 s, 25% of the top value) of the top value are co-anchors. The hotspot is then the co-anchor set, not one element.

Verified (prototype 0.19 s parsing + 0.01 s computation):
- **Z13@3600.** The 36 delayed_out vehicles and all 109 on-time peers enter on 44532531 and share one route prefix.
  - Insertion delay: 251.4 s against 66.3 s (+185.1 s).
  - The next differential is +8.9 s on 199500674#0, into TLS 6390683114 with 3 candidate links.
  - The anchor is entry pressure, not LSA119.
- **0535 Z4@10800.** All 22 delayed_out vehicles enter on −983227468#1 with zero insertion delay. Of the 35 on-time peers, 28 enter on 771709146#0 and are excluded. That leaves 7 peers with the identical route prefix. Co-anchors:
  - 551303424 → node 205069138: +64.3 s (126.7 against 62.4). Node 205069138 is both an actuated TLS ('205069138', 5 phases in `readme_fixed_A`, 4 candidate links to 189775480) and a junction-body hold node, where lane changes on internal lanes are disabled (`core/lane_section_hold.py:53-72`).
  - −186713427 → node 14398547: +55.4 s (55.4 against 0.0). 14398547 is a priority junction and a hold node.
  - 551303409 → node 265093112: +51.2 s (51.5 against 0.3).
  - The excess spreads over 12 consecutive edges from −186713425 to 551303424, +293.1 s in all. The delayed group recovers 81.0 s downstream: −62.6 s on 189775480 and −18.4 s on the field edge.
  - Round 1's single anchor (+114 s) averaged over all 35 peers, and 28 of them never traverse 551303424. It is withdrawn.
  - Reading: the pattern is consistent with a queue along the approach. Exit times cannot separate that from downstream spillback or from side effects at the two hold nodes.

Minimal extra outputs. Before any rule uses one of them, a parity test must show that field-counts, contributing passages and summary are byte-identical with and without the option on the parent.

| Output | Resolves | Est. cost |
|---|---|---|
| `--vehroute-output.internal true` (`sumo --help` of 1.27.1, line 169); in the M2-M3 `trace` kwarg | Separates junction-internal time from edge time | Today the vehroute is 6.5 MB raw (0.70 MB gz); with internal edges, est. 13 MB raw or 1.4 MB gz. Runtime unmeasured; M2(b) records it |
| A judge-generated `<timedEvent type="SaveTLSSwitchStates">` additional, appended to the list at `core/detector_demand_replay.py:151-153`; in the `trace` kwarg. This is an additional-file element, not a command-line option, so `sumo --help` does not list it; the string is present in the 1.27.1 binary. The user-file allowlists at `:95-99` and `:116-121` stay intact | Exact signal state history at the anchor | One row per switch, small |
| Deferred, not in the `trace` kwarg: `--fcd-output` with `--fcd-output.filter-edges.input-file` (help line 112) on the anchor approach edges and `--device.fcd.period 1` (help line 887) | Lane, and therefore link, identity | About 10⁵ rows per anchor |

- The always-on period-1 queue output (`:161`, 63 MB local-only on this case) could be dropped for counts-only runs. That is deferred with the other output presets and needs the same parity test.

**5) ATTRIBUTION** (hypotheses with evidence and a falsifier, never verdicts)

| Class | Evidence | Falsified by | Routes to |
|---|---|---|---|
| DQ quantity | field deficit or surplus in the balance identity | the balance closing after the candidate's own init/post shift | inner solver with free OD totals (deferred) |
| DT timing | supply term ≠ 0 without a field deficit | the planned bins matching y | demand-timing goal (F2, F3) |
| ENTRY | insertion delay dominates the route-matched differential | low entry-edge occupancy at the insertion time (needs edgeData, deferred) | network entry or timing |
| EDGE/NODE | route-matched differential excess on (edge, node); co-anchor sets; hold-node and TLS flags | internal-edge exit times placing it upstream | structural hypothesis |
| TLS | the anchor edge pair has exactly one controlled link and the switch states show red waiting | no red in the window | a control-class goal (not in M1-M5) |
| OBS | observation-contract issue (incomplete source cells, early departures, placement) | — | human; the knob is forbidden |
| U | the knob's observable fraction is below 0.5 | — | — |

- v1's O1 flag ("E1 13.4 m on 13.5 m lanes") is withdrawn. 12 of the 13 E1s sit within 0.11 m of the lane end (11 at 0.100 m, 0535 Z9 at 0.109 m), so this is the scenario's stop-line convention, not a site anomaly. 0535 Z14 sits 1.398 m before the end.
- Whether the infrared field counters also count at the stop line is an open observation-contract question.

**6) IDENTIFIABILITY.**
- C[row, cell] is the parent's observed contributions. Report its rank, nullity, zero columns, identical column groups and condition number. Use `audit_route_sensor_incidence` (`core/route_sensor_matrix.py:335`) when a route manifest exists.
- Report the model-resolution diagonal R = (C'WC + λI)⁻¹C'WC. Cells with R_cc < 0.2 are prior-determined and never claimed.
- Report the observable fraction of each proposal and the DoF used.

**7) SURROGATE.**
- `propose_bounded_od_assignment` (`core/od_assignment_feedback.py:12`) works on OD-period cells (`:17`) and keeps each OD group total fixed (`:58-59`). It uses contributions divided by the prior, "an assignment fraction, not a derivative" (`:15-21, :45`).
- Its optimum ranks proposals only. It is not a lower bound, and it never accepts.
- M4 translates its per-cell changes into `cell_depart_shift` ops; see Loop integration.
- A quantity variant (a new `od_totals='free'` kwarg, about 10 lines) is deferred with the DQ class.
- Native gain against predicted gain (ρ) is tracked only on healthy runs from a minimal-change generator.

**8) INTEGRITY ROWS.**
- SUMO warnings are typed rows. From `evaluation/native/command.json.gz` stderr (32 lines), the parent shows:
  - 5 emergency brakings;
  - 20 "actuated phase has no controlling detector" warnings. These are 10 distinct phases on 9 tlLogics, each reported once for the network's program '0' and once for the loaded 'readme_fixed_A';
  - 2 "missing green phase" warnings (LSA104_part0 tl-index 14, LSA535_part0 tl-index 5);
  - 5 "unused states" warnings.
- None of these is in the health predicate (`:209-214`). They are reported, and the emergency-braking count is a hard guard (H3).

## Bounds / roofline

**RULES FOR EVERY BOUND**
- A bound is labelled unconditional, conditional (with its conditions listed) or surrogate.
- It is recomputed for each candidate from that candidate's own quantities. Nothing is inherited from the parent.
- A self-check on the parent (the parent must satisfy its own bound) is necessary but proves nothing for unseen candidates.
- **No rule consumes a bound in M1-M5.**
  - A bound never refuses a hypothesis before running. B1 needs INV-PASS on the candidate's run, and B2 needs the candidate's own measured init counts, so neither condition can be verified before the run.
  - A bound never rejects a measured candidate.
  - A bound never stops a loop. Neither B1 nor B2 bounds what another candidate of the class could reach: B1 is an all182 floor that does not split init from main, and B2 depends on the candidate's own departures and init counts. So no class-wide stop exists. The terminal state `roofline_reached` is withdrawn; see Open questions.
- Bounds are reported with their conditions table and SOL.

**CALIBRATE** (`twin/bounds.py`; seconds, offline)

**B1, request-total floor over all182.** TAE_init52 + TAE_main130 ≥ Σ_f max(0, deficit_f^c), with deficit_f^c = E_init,f + E_main,f − N_f^c.
- Conditions: the candidate's per-field request totals N_f^c, read from its demand; INV-PASS on the candidate's run.
- Valid for any class that keeps the request totals: control, network or demand timing.
- Parent: 51, met by 372.
- It does not split between init and main. A timing-capable class can move the shortfall between the windows.

**B2, main-only supply bound.** LB_main,f = max over main bins b of (E_f(≤b) − (U_f^c(b) − M_init,f^c))⁺.
- U_f^c(b) counts intended vehicles with a_v < end of bin b.
- M_init,f^c is the candidate's own measured init count.
- Conditions, the same list as the profile's `applies_if`:
  - a_v recomputed from the candidate's net and demand. Unchanged departures are simply a special case of this;
  - the candidate's own measured init counts;
  - INV-PASS on the candidate run.
- It is a cumulative-bin bound, so for one field it can exceed that field's request deficit.

| Init counts assumed | B2 |
|---|---|
| Parent's measured values | 12 (0119 Z5 1, 0535 Z4 11), equal to the archived `field-supply-bound.json` |
| Init targets | 52 (Z12 15, Z13 15, 535Z4 12, Z5 6, Z11 2, Z4 2; Z4's deficit is 1) |

- The archived 12 therefore assumes initialization counts identical to the parent's. The init guard allows them to change.
- After a run, B2 is an accounting statement: "given your departures and init counts, main could not have been below X".
- B2 must not be read as "control cannot fix these rows". Over-delivery has no quantity floor at all, because vehicles can be delayed past 08:00Z; the post-window guard blocks that route.

**B3, surrogate optimum.** The MILP solution under the parent's observed assignment ranks proposals and is reported next to the measured native gain. It is never a bound.

**B4, service envelope.** Deferred and diagnostic only.
- A short saturation probe does not prove an upper service curve.
- A valid envelope would have to cover admissible actuated histories, phase transitions, initial queues, shared lanes and downstream blocking.
- The parent run has 10 actuated phases on 9 tlLogics without a controlling detector (20 warnings, one per program) and 2 links with no green, so its own green history cannot stand in for the envelope.
- No rule may use B4 until a known-answer test validates it.

**B5, robustness floor.** From M2: the paired spread under the declared perturbation family, and the calibration-set distributions that set g, m_row, m_init and k_post. It is stored in the gate artifact.

**SOL** (reported only; each SOL uses its own statistic and only a bound whose conditions hold on that candidate)
- SOL_all182 = (TAE_all182,parent − TAE_all182,c) / (TAE_all182,parent − B1^c). Parent: 372 and 51.
- SOL_main = (TAE_main,parent − TAE_main,c) / (TAE_main,parent − B2^c). Parent: 275 and 12.
- g is a difference threshold, not a TAE floor, and does not enter SOL.

**BUILD** (deterministic; noise floor 0, with a determinism re-run after any environment change)
- **Source floor.** tol_eff = max(tol_declared, 2·σ_comb, representation step), with σ_comb = sqrt(σ_source² + σ_registration²).
  - Aerial labels: σ = sqrt(GSD² + check_rmse²), about 0.4 m at 0.2 m/px.
  - MAP: declared or unknown; unknown is explore only.
- **Correspondence margin.** Distance to the best index-shift alternative. The index-shift probe is a heuristic and bounds nothing beyond the mappings tried.
- **Op-vocabulary floor (OPX).** In M3 the profile escalates `op_not_expressible` when no typed op can express the fix, for example a per-lane shape. A fitted floor (the minimum residual reachable with one common edge shape, about 80 lines of shapely) is deferred to M5.
- **Coverage ceiling.** Per layer, attainable / known. With the Hamburg adapter, junction_surface and signal_group are unattainable by construction, and lane_count and movements stay COR until locked.
- **Dynamic guards** are constraints, never bounds.

## Noise and acceptance

**1) WHAT VARIES**
- **Seed dependence.** The tracked vType has sigma 0, speedFactor 1 and speedDev 0, with fixed departPos 5.1, departLane best and departSpeed max. These facts suggest seed dependence may be zero. The exact static-history replay (`benchmarks/hamburg_ring1_275_fixed_history_20261024_v1/README.md:3, :7-8`) used the same seed, 104, so it shows determinism, not invariance to the seed. M2(a) measures seed dependence with a seed-11 run.
- **Departure jitter is not measurement noise.** It changes the inputs, and it measures robustness: does an improvement survive small, predeclared perturbations?
- Declared family Π: u_v,k = HMAC(key_k, vehicle_id) → U(−0.5, 0.5) s, added to the scheduled depart. Keyed draws are implemented in M2 (`twin/perturb.py`); M5 adds secret-committed keys. The alternative family, permuting insertion order within equal-depart groups, is an open question.
- **Common random numbers.** Parent and candidate are evaluated on the same K draws. A vehicle added by an op gets its draw from its deterministic id.
- Under CRN, an A/A pair with identical inputs gives d = 0 exactly. Disjoint A/A replicas therefore cannot calibrate candidate-minus-parent differences; M2 uses known-answer and placebo cases instead.

**2) CALIBRATE GATE** (`twin/gate.py`; a pure function; every rule is evaluated and the label comes from the table)

| Step | Rule | `screening` label if it fails |
|---|---|---|
| H1 invariants | every INV-* rule passes, statically and on each run | invalid (status `blocked`; counts toward the two-violation stop) |
| H2 health | `core/detector_demand_replay.py:209-214` holds on **every** run: the nominal run and every draw | unhealthy |
| H3 hard guards | emergency brakings ≤ the parent's maximum on the same draws; Σ_f P_f ≤ parent + k_post; per-entry-edge insertion delay p95 ≤ parent + 10 s | guard |
| N1 rows, all 182 (not only incidence-affected) | max over rows of (\|r_c\| − \|r_p\|) ≤ m_row, on the nominal draw and on the panel mean | row_regress |
| N2 init | TAE_init52,c ≤ TAE_init52,p + m_init | init |
| I improvement | d0 ≤ −g, and with K ≥ 2: UCB_{1−α}(mean d) ≤ −g and at least ⌈0.8K⌉ draws with d_k ≤ 0 | → improved |
| S simplification | DoF_c < DoF_p and d0 ≤ +δ_NI, and with K ≥ 2: UCB_{1−α}(mean d) ≤ +δ_NI | → simplified |
| Otherwise | d0 ≤ −g: a nominal gain that fails I's robustness checks | not_robust |
| | d0 ≥ +g | worse |
| | \|d0\| < g | not_material |

- The explore status follows ASTRA's vocabulary:
  - `blocked` when H1 fails, as `core/network_edit_operations.py:1149` uses it;
  - `no_effect` when every counted row equals the parent's on every draw;
  - `measured` otherwise.
  The screening label is separate and carries no promotion authority.
- Branches I and S are evaluated independently after H and N. A simplification is therefore reachable even when the improvement test fails.
- Spillback and coupled rows are gated because N1 covers every row. The incidence-affected set is used only for attribution.
- N1 and N2 are non-inferiority rules with predeclared margins. Lack of significance never counts as evidence of no regression.
- α = 0.05, one-sided (declared in the goal). UCB = mean + t_{0.95, K−1}·sd/√K, with t_{0.95,3} = 2.353 for K = 4. When sd = 0 on a pair, UCB = mean. The t-formula is never used with fewer than 2 draws.
- Thresholds come from the frozen gate artifact. The goal's provisional values (g 10, m_row 3, m_init 2, k_post 5) serve only diagnostic runs before the freeze, and never a verdict that enters the trial log.
- **Deterministic branch (K = 0, nominal only):**
  - only d0 is used;
  - the verdict carries `robustness: not_assessed`;
  - a nominal-only candidate can never be promoted.

**3) PROMOTION** (M5; M4's confirmatory comparison applies the same rule against the original baseline)
- Fresh keys, K_p = 8, disjoint from the panel and from every key in the trial log.
- Both branches require N1 and N2 on the fresh mean. In addition:
  - for `improved`: UCB_{0.95}(mean d_fresh) ≤ −g/2;
  - for `simplified`: UCB_{0.95}(mean d_fresh) ≤ +δ_NI.
- Then the Codex veto, then a human (M5).

**4) GATE CALIBRATION PROTOCOL** (M2; demand-timing family only; the protocol is hashed before any run)

| Phase | Cases | Runs | Feeds |
|---|---|---|---|
| Setup | seed-104 rerun (determinism; it is also the parent nominal), seed-11 run, parent panel K = 4; toy-net a_v check (seconds, not counted) | 6 | m_row, m_init, k_post, parent spread |
| M2a calibration | 12 known-null placebos: a random `cell_depart_shift` from the declared grid, chosen without looking at residuals. Each has its own truth key: y* = parent on a truth draw outside the panel | 12 × (1 truth + 5) = 72 | g |
| Freeze | write `gate-<sha8>.json` (fitted values, protocol hash, calibration run hashes) | 0 | — |
| M2b validation, scored only after the freeze | 8 known-positive cases: op* = `cell_depart_shift` on 4 single-field cells with ≥ 10 vehicles (fixed sampler seed) × {moderate 300 s, large 900 s}. y* = run(parent ⊕ op*) on the case's own truth key. Candidates are parent ⊕ op* and parent ⊕ op*/2, scored against y* | 8 × (1 + 2 × 5) = 88 | false rejects by magnitude; attribution hit rate |
| | 12 fresh known-null placebos, built like the calibration set | 72 | held-out false accepts |
| | placebo-on-real: those same 12 validation runs, scored against the real counts | 0 | screening false-accept proxy under the real parent |
| M2b promotion pass | 8 fresh parent keys, plus 8 fresh keys for each large positive accepted at screening | ≤ 40 | promotion power |

Total: at most 278 runs. CPU time is 13.6 h at 176.6 s per run and 40.1 h at 519 s; wall time is est. 3.4-10 h at 4 workers. Of that, M2a is 78 runs (1-2.8 h wall) and M2b at most 200 (2.5-7.2 h). Parallel scaling and peak RSS on 15.7 GB are unmeasured and get recorded.

M2a rules, fixed in the protocol:
- g = max(g_min, 1 + max_k |TAE_main,p,k − TAE_main,p,0|, 1 + max over calibration nulls of max(−d0, −mean d)), rounded up. g_min = 3 is set by a human.
- m_row = max(2, 1 + max over k and all 182 rows of (|r_p,k| − |r_p,0|)).
- m_init = max(2, 1 + max_k (TAE_init52,p,k − TAE_init52,p,0)).
- k_post = max(5, 1 + max_k (P_p,k − P_p,0)).

What is measured:
- Validation false accepts, with a one-sided 95% Clopper-Pearson upper bound. 0 of 12 gives 22%; a ≤5% statement needs 0 of 59 or more (M2-ext: 47 more validation nulls, 282 runs overnight).
- False rejects by magnitude.
- The attribution hit rate: does the parent-versus-y* profile name a row of op*'s field among its top 3 hotspots, classed DT?
- Promotion power on the large positives. The promotion false-accept rate is not measured separately. It is bounded above by the screening false-accept rate, because promotion requires `improved` first.

**Exit criterion for the Calibrate loops** (M4 F2 and M5 F3; M3 does not depend on it):
- 0/12 validation-null accepts;
- placebo-on-real acceptance ≤ 1/12;
- 4/4 large positives `improved`;
- at least 3 of those 4 pass the promotion rule.

Otherwise M2 stops, and nothing is raised automatically. A human revises K, the family, g_min or the grid under a new protocol version. The spent validation set joins the calibration data, and a fresh validation set is drawn. No general error guarantee is claimed beyond the measured intervals.

**5) CAMPAIGN-LEVEL ERROR**
- In-loop decisions are **screening decisions**. Their per-decision error rate is the M2 held-out rate, and the chance of at least one false accept grows with the number of judged candidates.
- From M4 on, the append-only trial log (`twin/trials.py`) records the campaign size: every judged candidate and every draw key used.
- The claim-bearing test is a single confirmatory comparison of the final candidate against the original baseline. It applies the §3 rule (one-sided α = 0.05) on 8 keys absent from the log, plus day 2 when frozen. Because it runs once, it needs no multiplicity correction.
- Beyond the logged count of screening decisions and that single test, no campaign-level error rate is claimed.
- Held-out holdout replies (M5) are pass/fail with a query budget: 1 per candidate, 5 per goal. That limits adaptive overfitting but gives no Thresholdout guarantee, so none is claimed.

**6) BUILD ACCEPTANCE**
- **G0, hard; any failure rejects, with no statistics:**
  - verify-edit status measured or no_effect;
  - the candidate equals the ledger's materialization;
  - structural failures 0, SUMO load pass, source immutable;
  - facts, lock and frozen-set hashes unchanged;
  - outside fact changes 0 (`core/network_edit_operations.py:1336-1352`);
  - `regressed_facts` and `measurability_lost` both empty;
  - coverage non-decreasing for every improved centerline;
  - shape regularity within the goal's `g0_shape`: vertex delta, heading change and directed Hausdorff distance;
  - `<location>` unchanged;
  - lane-coordinate artifacts inside their lane length, replacing today's `not_checked` (`:1135-1137`);
  - no new protected-green foes, and no new in-scope audit or direction findings.
- **Then:** V_cand <lex V_parent. Ties are rejected. The only exception is equal counts, a sum-of-excess decrease ≥ 0.05 and no numeric metric worse by more than 0.1·tol_eff.
- **Guards:**
  - L2: no new probe or smoke failure.
  - L3, a hard rule only. On every seed, with the same frozen trips, the candidate's failing requests must be a subset of the source's. A request that arrives on the source and runs, waits, is discarded, teleports, collides or is unroutable on the candidate rejects. These are exactly the outcomes `run_routeability_audit` fails (`core/routeability_audit.py:315-334`; claim boundary `:423`).
  - Travel-time and arrival-time changes are reported, never gated.
  - Round 1's non-inferiority margin ("arrival loss ≤ max(2, 1%)") is dropped. Every lost arrival is a failing request, so the margin could never apply under the hard rule.
  - The v1 sign test is gone. The reviewer's case (4 worse seeds, 0 better, 6 ties) is rejected: a worse seed in this audit means a request that arrived on the source and fails on the candidate.
- **Dirty source.** If the source already fails requests on a seed, the candidate's failing requests on that seed must be a subset of the source's. The result cannot be promoted without a human.

**WHY THE HMZ BUILT-IN GATE IS NOT USED.** It extracts one integer and accepts a strict '<' with no margin. The flow reads the structured verdict instead.

## Holdout and anti-gaming

**DATA AS IT EXISTS (Calibrate)**
- Frozen in the tree: 2026-10-19, 05:30-08:00 UTC (07:30-10:00 local CEST), in `benchmarks/hamburg_ring1_count_window_20261020_v1`. It holds 13 fields × 30 five-minute observations = 390, summed into the 130 main rows. The scenario's 130 targets equal this package exactly.
- Initialization rows: 04:30-05:30Z, from `initialization-counts.csv`, outside that package (lineage recorded in the goal).
- The 4 stations are processed sums: derived evidence, not independent.
- Leakage: the demand was built from the counts. There are 8589 explicit vehicles, each with one intended field, so a field or station left out is not out-of-sample. Leave-one-station-out is never a gate.

**CALIBRATE HOLDOUTS** (ids HO-C*; distinct from gate steps H1-H3 and bounds B1-B5)

| Id | What it is | Status |
|---|---|---|
| HO-C0 init52 | visible warm-up rows, guarded by N2 | in use |
| HO-C1 shape390 | 5-min within-bin shape | **report-only diagnostic.** The 390 values have been tracked since commit 0735062 (2026-10-20), before the 275 case existed. Manual tuning could have read them, and code lineage searches cannot rule out that a human looked. It is never a promotion gate |
| HO-C2 fresh perturbation draws | robustness on unseen keys | keyed draws from M2 (promotion pass, M4 confirmatory comparison); M5 adds secret keys committed by sha256 and revealed at the end |
| HO-C3 day 2 (2026-10-12, a Monday like 10-19) | Refit demand with the same solver and budget, once for the parent structure and once for the candidate structure, then run both and compare. The claim is that the **structure-plus-refitting procedure** transfers better, not that the structure is field-correct | a human must freeze it first, with online access |
| HO-C4 TLD signal states | observed against simulated green windows (`materialize_hamburg_named_signal_observations`, `core/hamburg_named_signal_observations.py:242`) | conditional; coverage is partial |

**BUILD HOLDOUTS** (ids HO-B*)
- HO-B1: facts held out by approach or junction, never at random per fact, because one `set_edge_shape` moves every lane of an edge.
  - In M4 they are removed from the flow's scored set at goal-create. No seal is needed, because no agent reads the store.
  - In M5 they are sealed against the actor. Alternatively, an independent source is held out while the actor fits MAP.
- HO-B2: site holdouts for recipe changes: 2394, LuST, InTAS, the held-out corridor v2 corpus.
- HO-B3: routeability seeds held out from the guard.

**INVARIANTS AS CODE** (`twin/invariants.py`; each returns {id, pass, witness_ids})

M1, Calibrate part 1 (needed by the offline profile and by C0):
- INV-INPUTS: every alias resolves to its sha256.
- INV-OBS: attribute nVehContrib; E1 ids, lanes, pos and period unchanged; field-loops sha unchanged.
- INV-HOLD: the hold nodes equal the goal list and the `run.json` record (four nodes).
- INV-PASS:
  - reconciled true;
  - at most one passage per vehicle per field;
  - none off-intention.
  - In M1 it reads the archived parent report `evaluation/contributing-passages.json.gz.gz` (reconciled true, inputs_unchanged true), because the parent's instant-e1.xml (21,424,405 bytes) is local-only. From M2 on, every judged run calls `read_contributing_e1_passages` (`core/route_sensor_matrix.py:15-79`).

M2, Calibrate part 2 (needed by the judge, C0 and gate step H1):
- INV-FLAGS: the command equals the template at `core/detector_demand_replay.py:154-162`, paths excluded, plus only the parity-tested `trace` options.
- INV-ADDITIONAL: the signal file holds tlLogic only (`:95-99`) and the E1 file inductionLoop only (`:116-121`); the only other additionals are judge-generated.
- INV-DEMAND: the candidate's base demand equals apply(ops, parent demand) byte for byte. Each judged run's demand equals perturb_k(base) for its declared draw key k (k = none for the nominal run). Per-field request totals are reported.
- INV-ONE-CLASS: the class derived from the diff equals the goal's class.
- INV-NO-IDS: no agent-chosen vehicle ids.
- INV-LOOP: `junction_visit_audit` (`core/demand_route_pool.py:9`) on changed routes.
- INV-PERM: the vClass set per lane is ⊆ the source's unless the class declares it.
- INV-VERIFIER:
  - `astra_sumo.__file__` lies under the verifier clone, both in the parent process and in the `simulation_stall` child that `:191` spawns;
  - the clone's HEAD equals the recorded commit;
  - its `git status --porcelain` is empty.

M3, Build: INV-FACTS, INV-MATERIALIZE, INV-OPSCOPE, INV-MEASURABLE, INV-COVERAGE, INV-SHAPE, INV-FRAME, INV-ARTIFACTS, INV-E1-LANE, INV-COR, INV-GREEN, INV-AUDIT, INV-DIR.

M5: INV-SCOPE (workspace writes), INV-LEDGER, INV-REPLICAS.

**GAMING ROUTES AND THEIR BLOCKS**

| Route | Blocked by |
|---|---|
| Delay over-delivering vehicles past 08:00Z | gate step H3 (k_post) plus field-balance deltas in every delta |
| Push the init shortfall into main | N2 plus per-field Δinit/Δmain |
| Insertion holding | gate step H3 (entry-delay p95) |
| Change what is counted | INV-OBS, INV-ADDITIONAL |
| Per-vehicle tweaks | INV-NO-IDS, DoF accounting |
| Open bike or bus lanes | INV-PERM |
| Route loops or repeated passes | INV-LOOP, INV-PASS |
| Draw shopping | draw keys fixed by the goal and logged in the trial log; fresh keys only for promotion and the confirmatory comparison |
| Threshold shopping | gate artifact frozen and hashed before validation; verdicts under different artifacts are never compared |
| Build: delete lanes so facts turn unknown | frozen scored set and measurability_lost |
| Build: zigzag shapes, frame shifts, moving E1 lanes | G0 shape (goal `g0_shape`), frame and artifact rules; INV-E1-LANE |

**ENFORCEMENT**
- Two invariant violations by one actor stop the run with stop_reason `violations` (M5).
- The Codex reviewer checks for patterns code does not encode: DoF against n, null-space changes, draw-specific gains, scope leaks and claim creep.

## Scaling

Caching and speed-ups are deliberately not first priorities. Valid bounds, baseline reproduction and useful localization come first.

**IN M1-M3**
- Offline profile: the prototype takes 0.35 s for the decomposition and 0.20 s for the localization on 8589 vehicles. Both are in-process, include gzip and XML parsing, and exclude interpreter start-up. Target: ≤10 s for the end-to-end CLI.
- Parent runs are cached by content hash. The key is (net, demand, control, E1, intentions, draw key, flags plus the parity-tested `trace` option set, SUMO binary sha256, verifier commit).
- Parallel judge runs: each `run_point_detector_demand` already spawns its own SUMO child (`core/detector_demand_replay.py:191`), so a thread pool of workers suffices. The default is 4 workers on the i5-12400, and M2 records peak RSS against 15.7 GB and the measured parallel speed-up.
- Build index cache (M3, about 40 lines): `_measure` rebuilds its lane and edge dicts for every fact (`core/network_calibration.py:234-235`), and `_saved_lane_paths` calls `_index(root)` for every centerline (`:166-185`). Memoizing per parsed root targets ≤2 s for 800 internal-path facts, against 10.5 s recorded in v1.

**DEFERRED** (rough sizes; each needs a parity test before any rule uses it)

| Item | Size | Gate |
|---|---|---|
| Incremental Build re-score with an equality proof on element hashes | about 80 lines | full re-score every 5 accepts and always at promotion |
| Output presets beyond the M2-M3 `trace` kwarg: queue output off for counts-only runs, filtered FCD | about 40 lines | byte parity of counts and passages |
| Causal checkpoints (resume from the parent state; `state_times_s` exists, `:163-167`) | about 20 lines in `simulation_stall` plus hold re-attach | the parent resumed from its own state reproduces its post-t0 rows |
| Cordon replay (cut net plus `cutRoutes.py`) | about 120 lines | the parent's own cordon replay reproduces its in-cordon rows; reject-only |
| Meso screening | about 60 lines | Spearman ρ ≥ 0.8 on ≥10 recorded pairs; quantity classes only |
| Large networks with unknown routes | — | route_flows class through `run_route_sampler_ensemble` (`core/route_sampler.py:723`) after `audit_route_sensor_incidence` |

**DE-HAMBURGIZE** (later, through the implementation flow)
- `core/hamburg_od_spsa.py` asserts 21 pairs and 294 cells (`:90`, `:155`), so it cannot run on the 275 case, which has 22 pairs and 308 cells. It also hard-codes 130 rows (`:288`) and ThreadPoolExecutor(max_workers=2) (`:372`).
- `vehicle_field_passage_rows` hard-codes 3600, 900 and 13 (`core/hamburg_readme_results.py:67-69`).
- All of these constants move into the scenario manifest.

## Loop integration (hmz + Codex reviewer)

**STATUS AND GATING**
- The Calibrate loops (M4 F2, M5 F3) wait for M2's exit criterion.
- F1-lite waits for M3's K1/K2 acceptance.
- M3 itself does not use the Calibrate gate.
- What follows keeps the isolation facts that v1 established, in shorter form.

**VOCABULARY.** Every flow record carries the explore status (`measured`, `no_effect`, or `blocked` as at `core/network_edit_operations.py:1149`). Every flow ends with one `stop_reason` and one `outcome`:
- outcome `accepted_for_review` when a best candidate passes the confirmatory comparison;
- `not_confirmed` when it fails;
- `no_candidate` when nothing was accepted.

None of these has promotion authority; `accepted_for_review` is only an explore-to-review handoff.

**M4: LOOPS WITHOUT AN AGENT** (no fence needed; only the verifier writes, into the store)
- **Trial log** (`twin/trials.py`): append-only JSONL at `E:\twin-store\trials\<goal_id>.jsonl`. Each row holds:
  - the candidate hash, goal_id, gate artifact sha and verifier commit;
  - every draw key used;
  - the status and screening label;
  - the sha256 of the previous row.

  The confirmatory comparison draws keys absent from the log.
- **F2 demand timing:**
  1. Refresh the contributions from the current parent's passages, with vehicles re-binned by their scheduled period.
  2. Run the surrogate `propose_bounded_od_assignment` (`core/od_assignment_feedback.py:12`) with OD-group totals fixed (`:58-59`). It returns integer flow changes Δ_c per OD-period cell (`:17`).
  3. Translate to `cell_depart_shift{cell, shift_s}`. A donor cell (Δ < 0), paired with one adjacent recipient period of the same OD group (Δ > 0), becomes a whole-cell shift: the smallest |shift_s| that moves exactly |Δ_donor| scheduled departures across the shared period boundary.
     - The donor's other departures move by the same amount inside its period, and the run measures that side effect.
     - Not expressible, logged as `not_expressible` and never approximated: a non-adjacent recipient, a donor split over two recipients, or a shift beyond 900 s.
     - Multi-field cells (112 of the 295 non-empty cells) keep each vehicle's own field, so the realized field mix can differ from the surrogate's assignment fractions; the native run decides.
  4. One op per round: the expressible op with the largest predicted gain goes through C0, C1 and the gate. An `improved` or `simplified` candidate becomes the parent.
  - stop_reason: `stalled` (gain < g on two consecutive rounds), `solver_blocked`, `not_expressible` (nothing left to express) or `budget_exhausted`.
- **F1-lite Build:** single-fact suggested ops on K2 goals, applied by the flow. The approach-level held-out facts (HO-B1) are removed from the flow's scored set at goal-create.
- Each loop ends with the single confirmatory comparison (Noise and acceptance §3 rule, 8 fresh keys, against the original baseline).

**M5: ISOLATION TOPOLOGY**
- **Implementation clone.** `git clone --no-hardlinks E:\ASTRA-SUMO E:\astra-impl`, which only reads the source repository. All edits and the T0-T2 tests happen here. Code reaches E:\ASTRA-SUMO only through a reviewed PR, after the concurrent docs work merges.
- **Verifier.** A second clone, `git clone --no-hardlinks E:\astra-impl E:\astra-verifier\<sha>`, detached at a committed sha, then `uv sync --python 3.12.13 --frozen --extra dev --extra native`. The extras are declared in `pyproject.toml:33-43` and used in `.github/workflows/full-suite.yml:38`.
  - The verifier is never edited. INV-VERIFIER checks its HEAD, a clean status and the import path in both processes.
  - The shared `.venv`'s `__editable__.astra_sumo-1.3.1.pth` points at `E:\ASTRA-SUMO\plugins\astra-sumo\src` and would import live code.
  - Never use `git worktree add`: E:\ASTRA-SUMO already has five extra worktrees, and hmz snapshot refs are shared across worktrees.
- **Store.** `E:\twin-store\{goals,gates,profiles,runs,trials,sealed}`, with no git.
- **Actor workspace.** A standalone repo `~/twin/<goal_id>` on WSL ext4, containing TASK.md, goal.actor.json, work/, NOTES.md and .hmz/STATE.md. STATE.md is regenerated each turn from the trial log.
- **Fence.**
  - Actor: Permission(local=ALL, user=NONE, system=NONE, online=NONE).
  - Reviewer: local=READ.
  - `on_pre_tool_use` hooks are a second layer only, and they cannot gate Codex.
  - hmz is launched with a sanitized environment, `env -i HOME=$HOME USER=$USER PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin`, because the WSL PATH carries /mnt/c interop binaries.
  - Use Linux claude and Linux codex; the WSL `codex` currently resolves to a Windows npm shim.
  - Fallback if negative tests fail: `appendWindowsPath=false`, a persistent setting and the user's decision.
- **FakeCli `astra-twin`** (runs in the flow process, outside the fence; --flags only):
  - actor: lint (≤5 s; refuses out-of-class hypotheses and explicit vehicle ids), show, drill --id (≤800 tokens), element --id (≤600), try (Build L0+L1, ≤5 per turn), predict (surrogate, ≤5 per turn), status;
  - reviewer: show, drill, element;
  - the judge runs only between turns.

**FLOWS**

| Flow | Shape | stop_reason |
|---|---|---|
| F1 build | fresh Claude session per round, one typed op; single-fact cases applied without an agent first; L0→L3 between turns | `solved` (V = 0 with guards), `stalled` (stall_scored 5), `escalated` (COR/SRC/OPX/UNK), `violations`, `budget_exhausted` |
| F2 solve | no LLM (M4) | `stalled` (gain < g twice), `solver_blocked`, `not_expressible`, `budget_exhausted` |
| F3 calibrate | fresh Claude session per turn, one falsifiable hypothesis in the goal's class with predicted rows | `class_exhausted` (3 refuted hypotheses since the last acceptance), `stalled` (5 consecutive scored hypotheses without an accepted candidate), `escalated` (OBS or out-of-class attribution), `violations`, `budget_exhausted` (100 SUMO runs or 8 h) |
| F5 promotion | fresh Codex session that never acted on the goal, `output_schema` Verdict, veto only; then a human | — |

- **Refuted:** a scored hypothesis whose measured Δ|r| has the opposite sign to its prediction on more than half of its predicted rows.
- A refuted hypothesis also counts toward `stalled`. So `class_exhausted` can fire after only 3 scored hypotheses, while `stalled` needs 5 with at most 2 refuted.
- If both fire on the same hypothesis, `class_exhausted` wins. An accepted candidate resets both counters.
- One goal carries one class (INV-ONE-CLASS), so no class switch exists inside a goal. A different class needs its own goal and its own K3.
- Budget arithmetic: a scored hypothesis costs 5 runs (nominal plus K = 4; parent cached), and a promotion or the confirmatory comparison costs 16. So 100 runs allow 2 promotions, the confirmatory comparison and 10 scored hypotheses (98 runs), which keeps `stalled` reachable after two promotions.

**SKELETON** (about 150 lines in M5, outline)
```
goal-check -> gate-artifact check -> baseline (cached) -> loop:
  render STATE.md -> fresh actor session (25 min) -> eval (copy+hash work/, tiers, profile, gate, trial-log row)
  blocked|invalid -> violations += 1 (2 -> stop: violations)
  improved|simplified -> promote (fresh keys) -> Codex veto -> commit work/ -> new best; reset stall and refuted
  else -> rewind workspace; stall += 1 if scored (5 -> stalled); refuted += 1 if refuted (3 -> class_exhausted)
end -> confirmatory comparison on keys absent from the log -> outcome
```
Exit codes: 0 when the flow ends with a stop_reason, 3 when it ends without one (crash or refused input).

**M5 ACCEPTANCE FOR ISOLATION** (fixes v1's invalid M0 git-status check)
- The dry run gets a **disposable clone**, `E:\astra-m5-probe`, as the only repository path it may reference. That clone's `git status --porcelain`, `for-each-ref` and `worktree list` must be byte-identical before and after.
- E:\ASTRA-SUMO is edited concurrently; its HEAD moved c654174 → 0173672 during the round-1 review. For it, only two things are compared:
  - `git for-each-ref refs/hmz` must stay empty (0 refs today);
  - `git worktree list` must keep the same paths.
- A Landlock negative test must also show that a fenced actor cannot read `/mnt/e/ASTRA-SUMO/README.md` or the store, and cannot run cmd.exe, powershell.exe or `/mnt/e/.../python.exe`.
- A rewind in the workspace must leave the store and STATE.md untouched, and the trial-log chain check must pass.

## Reuse and new code

**REUSED IN-PROCESS** (paths under `plugins/astra-sumo/src/astra_sumo/`)

Calibrate:
- `core/detector_demand_replay.py`: `run_point_detector_demand` `:64-75`, command template `:154-162`, child spawn `:191`, health `:209-214`, exact fit `:226-227`.
- `core/detector_demand.py`: `audit_expected_to_e1_strict` `:1007`, `geh_value` `:1026` (hourly GEH = 2 × geh_value, computed in the profiler), `summarize_comparison` `:1033`.
- `core/route_sensor_matrix.py`: `read_contributing_e1_passages` `:15-79` (judged runs from M2), `solve_nonnegative_integer_route_flows` `:82`, `audit_route_sensor_incidence` `:335`.
- `core/od_assignment_feedback.py`: `propose_bounded_od_assignment` `:12`, OD-period cells `:17`, fixed group totals `:58-59`.
- `core/lane_section_hold.py`: hold semantics `:53-72`.
- `core/demand_route_pool.py`: `junction_visit_audit` `:9`.
- `core/candidate_contracts.file_sha256`, `core/artifact_io.write_json_atomic`.

Build:
- `core/network_calibration.py`: `_measure` `:233-453`, `_score` `:456`, `compare_calibration_scorecards` `:515`, `evaluate_network_calibration` `:527`.
- `core/network_edit_applicability.py`: `verify_network_edit` `:319`.
- `core/network_edit_operations.py`: `SCOPED_EDIT_TYPES` `:22-27`, `validate_scoped_operation` `:463`, explore status `:1149`, `explore_network_edit` `:1212`, outside facts `:1336-1352`.
- `core/routeability_audit.py`: `run_routeability_audit` `:350`, completion failures `:315-334`, claim boundary `:423`.
- `core/movement_routeability.py`: `:66`, `:246`.
- `core/lane_correspondence.py`: `:48-74` (primary mapping `:50-51`, shifts −1 and +1 at `:54-56`).
- `core/standard_nema_binding.py`: `_normalize_signed_angle` and `_turn_from_delta` `:1218-1230`, reused by the direction check.
- `core/digital_twin.py`: `:137-144`, `:441-451`.
- `corridor/synthetic_networks.py`: `:62`, `:100`, `:1040-1064`.
- `core/hamburg_network_calibration.py`: `:101`.

**Benchmark alias resolution** is ported from `benchmarks/hamburg_ring1_healthy_joint_od_feedback_20261024_v1/freeze_evidence.py:82-92`, an 11-line `read(case, name)`, with two changes:
- The base is the **packet root**. The chronological packet keeps `byte-references.json` there, with 325 aliases keyed `cases/<case>/...`, while the frozen `read()` loads `case/'byte-references.json'`.
- The `.gz` fallback is implemented directly. The frozen `raw()` reaches it through two other frozen modules, and every alias target here is tracked only as `<target>.gz` (the passage report as `.gz.gz`).

Frozen benchmark scripts are never imported.

**M1 NEW CODE** (about 860 lines, no SUMO)

| File | Lines | Content |
|---|---|---|
| `core/benchmark_references.py` | 50 | `resolve_packet_file(packet_root, relative) -> bytes`; packet and repository scopes; direct `.gz` fallback; raw sha256 assert |
| `twin/goal.py` (minimal) | 80 | Calibrate goal model, observation block, gate_protocol, goal-create from a packet case |
| `twin/count_profile.py` | 200 | score from tracked E1 output, hourly GEH, free-flow a_v, decomposition, field balance, route-matched exit-time localization with co-anchors, integrity warnings |
| `twin/bounds.py` | 60 | B1/B2 with conditions blocks, SOL per statistic |
| `twin/invariants.py` (Calibrate part 1) | 60 | INV-INPUTS, INV-OBS, INV-HOLD, INV-PASS on the archived report |
| `twin/ids.py` | 40 | stable ids, detector aliases, co-anchor ids |
| `twin/render.py` | 80 | compact JSON and text with the 6,000-character cap |
| `scripts/astra_twin.py` | 120 | `doctor`, `resolve`, `goal-create`, `profile calibrate` |
| `tests/test_twin_count_profile.py` | 170 | golden values from tracked files, plus a separately written a_v routine |

**M1 CLI** (run from the verifier clone with its own venv)
```
python -X utf8 plugins/astra-sumo/scripts/astra_twin.py doctor --json
python -X utf8 plugins/astra-sumo/scripts/astra_twin.py resolve --packet benchmarks/hamburg_ring1_chronological_od_feedback_20261024_v1 --case h4_counter_z11_minus5_z5 --json
python -X utf8 plugins/astra-sumo/scripts/astra_twin.py goal-create --capability calibrate --packet <packet> --case <case> --store E:\twin-store --json
python -X utf8 plugins/astra-sumo/scripts/astra_twin.py profile calibrate --goal E:\twin-store\goals\<id>\goal.json --run packet:evaluation/native --out E:\twin-store\profiles\<new> --json
```

**M2 NEW CODE** (about 920 lines)

| File | Lines | Content |
|---|---|---|
| `twin/scenario_ops.py` | 100 | `cell_depart_shift`, placebo sampler over the declared grid |
| `twin/perturb.py` | 60 | HMAC-keyed departure jitter with CRN by vehicle id |
| `twin/runner.py` | 140 | parallel judge, content-hash cache, determinism and parity checks, RSS capture |
| `twin/gate.py` | 180 | decision table, status mapping, UCB, max-row statistic, Clopper-Pearson |
| `twin/k3.py` | 170 | case plan, `calibrate` (M2a), freeze and hash, `validate` (M2b; refuses without a frozen gate sha), promotion pass, report |
| `twin/invariants.py` (Calibrate part 2) | 100 | INV-FLAGS, INV-ADDITIONAL, INV-DEMAND, INV-ONE-CLASS, INV-NO-IDS, INV-LOOP, INV-PERM, INV-VERIFIER |
| tests | 170 | — |

**M2 CLI**
```
astra_twin.py judge --goal <g> --candidate <dir> --draws nominal,k1,k2,k3,k4 --workers 4 --out <new> --json
astra_twin.py gate --goal <g> --gate-calibration <gate.json> --parent <eval> --candidate <eval> --json
astra_twin.py k3 calibrate --goal <g> --plan k3-plan.json --workers 4 --out E:\twin-store\k3\<new> --json
astra_twin.py k3 validate --goal <g> --plan k3-plan.json --gate-calibration E:\twin-store\gates\<goal_id>\gate-<sha8>.json --workers 4 --out E:\twin-store\k3\<new> --json
```

**SMALL CORE EDITS**, M2-M3 (additive, defaults unchanged; about 165 lines)

| Change | Lines |
|---|---|
| `detector_demand_replay.py`: `trace` kwarg (vehroute internal, judge-generated TLS switch states) | 30 |
| `network_calibration.py`: `measurability_lost` | 10 |
| `network_calibration.py`: per-root index memo | 40 |
| `network_edit_operations.py` / `network_edit_applicability.py`: `routeability_seeds` | 20 |
| `network_edit_operations.py`: lane-artifact positions | 60 |
| `simulation_stall.py`: record `astra_sumo.__file__` | 3 |

**M3 NEW CODE** (about 890 lines)
- `core/reference_net_facts.py` (180)
- `twin/build_profile.py` (300)
- `twin/direction_check.py` (40; reuses `_turn_from_delta`)
- K1/K2 harness (120)
- `twin/invariants.py` Build part (100)
- tests (150)

**M4 NEW CODE** (about 390 lines)
- `twin/trials.py` (60): append-only JSONL with a previous-row sha256.
- `twin/flow_f2.py` (150): surrogate translation, loop, stops, confirmatory comparison.
- `twin/flow_f1_lite.py` (80).
- tests (100).

**DEFERRED TO M5** (about 1.1k lines plus 4 schema files)
- hmz flow (150), fence and doctor checks (80), `twin/seal.py` with secret-committed keys (80), STATE render and actor view (60), Codex review pack (60), M5 invariants (60);
- schema exports for twin-goal, twin-profile, twin-trial and scenario-ops (4 files);
- caches and screens (about 320), OSM facts (250), fitted op-vocabulary floor (80).

**DEFERRED BEYOND M5**
- `od_assignment_feedback.py` `od_totals='free'` (about 10 lines), for the DQ class.
- A control-class goal with its own K3.
- A `cell_transfer` op.

A public `astra twin` subcommand or MCP tool comes only later, through the AGENTS.md public-surface steps (`server.py`, `docs/mcp-tool-catalog.md`, skill routing, contract tests, `tests/test_repository_navigation.py`).

## First milestones

**M1: Offline Calibrate profiler and minimal isolated verifier** (2-3 days of code, zero SUMO runs; runnable today)

Work:
- `git clone --no-hardlinks E:\ASTRA-SUMO E:\astra-impl`, which only reads the source. Write the M1 files there on a branch and run T0/T1 there.
- Commit. Then `git clone --no-hardlinks E:\astra-impl E:\astra-verifier\<sha>`, detach at that sha, and run `uv sync --python 3.12.13 --frozen --extra dev --extra native`.
- Run doctor, resolve, goal-create and profile from the verifier clone, with outputs under `E:\twin-store`.
- Write nothing in E:\ASTRA-SUMO.

Acceptance:
- (a) `doctor --json`:
  - `sumo --version` reports 1.27.1, and libsumo imports;
  - `astra_sumo.__file__` lies under the verifier clone, both in-process and in a child started with `sys.executable` from a new cwd, as `core/detector_demand_replay.py:191` starts it;
  - `E:\ASTRA-SUMO\plugins\astra-sumo\src` is not on the child's sys.path;
  - the clone's HEAD equals the recorded commit, and `git status --porcelain` is empty.
- (b) `resolve`, from the packet root (`byte-references.json` with `cases/<case>/...` keys):
  - the 7 prepared aliases (lines 853-887) resolve through their `.gz` targets, and their raw sha256 hashes match;
  - the baseline run files are present;
  - the archived passage report has reconciled true and inputs_unchanged true;
  - the goal's hold nodes equal `run.json` `junction_body_hold_nodes`: four nodes, including LSA104_part0;
  - `timeout_s` 4800 matches the shared driver's `run_case.py:102`, and `all_stopped_timeout_s` 300 matches `native-request.json`.
- (c) Score reproduced from the tracked E1 output through `audit_expected_to_e1_strict` and `summarize_comparison`:
  - TAE 275, SSE 2833, MAE 2.1153846, max 22, exact 83/130;
  - init52 TAE 97, all182 TAE 372;
  - GEH raw 100% (max 3.24), hourly 99.23% (max 6.49), computed in `twin/count_profile.py` as 2 × `geh_value` (`core/detector_demand.py:1026`) with no core edit.
- (d) Decomposition:
  - ontime 5770, late_in 902, delayed_out 866, supply term 53;
  - 0/8589 passages earlier than a_v, with INV-PASS on the archived report;
  - the four row splits in the Calibrate profiler table;
  - the separately written a_v routine agrees with the profiler's on all 8589 vehicles before any value is pinned.
- (e) Field balance:
  - deficits {Z11 2, Z12 15, Z13 15, Z4 1, Z5 6, 535Z4 12} = 51;
  - post-window {Z12 3, Z13 4, Z4 9, Z5 12, 535Z4 1} = 29, unpassed 0;
  - the identity closes on 13/13 fields.
- (f) Bounds:
  - B1 = 51;
  - B2 = 12 at the parent's init counts and 52 at the init targets (0119 Z4: 2 against deficit 1);
  - every bound has a conditions block, every condition is verified on the parent, and no rule consumes a bound.
- (g) Localization against route-matched peers:
  - Z13@3600: 36 delayed_out and 109 on-time vehicles share entry 44532531 and one route prefix. The insertion differential is +185.1 s (251.4 against 66.3); the next is +8.9 s on 199500674#0.
  - 0535 Z4@10800: peer counts n = 22/7, with the 28 peers on 771709146#0 excluded.
    - Co-anchors: 551303424 +64.3 s (TLS and hold-node flags), −186713427 +55.4 s (hold-node flag), 551303409 +51.2 s.
    - Spread +293.1 s over 12 edges, −81.0 s downstream.
    - All 22 delayed vehicles measured in 11700.
- (h) The compact profile is ≤6,000 characters. The end-to-end CLI, including parsing, runs in ≤10 s (measured). Output is byte-identical across two runs apart from timing fields.
- (i) Golden tests and ruff pass in the implementation clone.

**M2: K3 known-answer tier and gate calibration** (3 days of code; at most 278 runs, est. 3.4-10 h wall at 4 workers; demand-timing family only; may run in parallel with M3)

Acceptance:
- (a) Determinism and the free-flow check:
  - a seed-104 rerun reproduces the 208 tracked E1 rows (`evaluation/native208-comparison.csv.gz`) and the 8589 passages;
  - a seed-11 run is reported, so seed dependence is measured, not assumed;
  - the toy-net check passes: single vehicles on an empty net pass no earlier than a_v and within 1 s of it.
- (b) Parity: the `trace` kwarg options leave counts and passages byte-identical on the parent, and their runtime and output sizes are recorded.
- (c) A parent panel of K = 4 CRN draws, all healthy, with the TAE spread reported. An unhealthy draw is recorded as fragility and stops M2.
- (d) M2a: `gate-<sha8>.json` is written and hashed from the parent panel and the 12 calibration nulls only, with g ≥ g_min.
- (e) M2b is scored only after (d) and reports:
  - known-positive false rejects by magnitude;
  - validation-null and placebo-on-real false accepts, with one-sided 95% Clopper-Pearson bounds;
  - the attribution top-3 hit rate;
  - the promotion pass on fresh keys.
- (f) Exit criterion:
  - 0/12 validation-null accepts;
  - placebo-on-real ≤ 1/12;
  - 4/4 large positives `improved`;
  - at least 3/4 large positives pass the promotion rule.

  Otherwise M2 stops. A human revises it under a new protocol version, with a fresh validation set.
- M2-ext, optional overnight: 47 more validation nulls (282 runs), for a ≤5% statement at 0/59.

**M3: Build profiler and Build known answers** (2-3 days; independent of M2)

Acceptance:
- (a) K1 on X4:
  - top-1 names the mutated element in ≥90% of the fact-observable mutations, top-3 in ≥95%;
  - wrong-turn is found by the direction check, and the record notes whether the NEMA binding gate (`core/standard_nema_binding.py:857-866`) also blocks it;
  - the internal mutations surface as INT;
  - the clean fixture and the clean Ring1 nominal net give 0 direction findings.
- (b) K2 on Ring1 nominal (cc045374…), with 5 ledger-injected faults and 3 raw-XML perturbations:
  - the top hotspots are exactly the injected elements, with the right class;
  - the ledger faults, and any raw-XML fault a typed op can express, reach V = 0 through inverse ops with outside_fact_changes 0;
  - a raw per-lane shape change escalates as OPX (`op_not_expressible`) and names the lane.
- (c) G0 rejects 100% of these exploits: lane deletion turning a fact unknown, a selector-breaking ValueError, lane shortening, a zigzag shape beyond the goal's `g0_shape`, a netOffset shift, a coordinate change on an E1 lane.
- (d) The per-kind acceptance table is unit-tested. Lint refuses zero and missing tolerances, and refuses a Build goal without `g0_shape`.
- (e) Synthetic L3 cases, all rejected:
  - 4 worse seeds (one new collision), 0 better and 6 ties;
  - one seed with 1-2 lost arrivals and no collision;
  - one seed where the candidate fixes a failing source request but breaks another.
- (f) 800 internal-path facts profile in ≤2 s.

**M4: Loops without an agent** (2 days plus runtime; after M2's exit criterion and M3's acceptance)
- (a) F2 on the 275 case, demand timing only:
  - it ends with one stop_reason and one outcome;
  - every accepted step passed the frozen M2 gate artifact;
  - every judged candidate and draw key appears in the trial log, and its chain check passes;
  - the confirmatory comparison used keys absent from the log;
  - untranslatable surrogate proposals are logged as `not_expressible`;
  - no commit and no benchmark bundle exist for rejected trials.
- (b) F1-lite on 3 K2 goals:
  - ≥2/3 reach V = 0;
  - the approach-level held-out facts (removed from the flow's scored set at goal-create; no seal) show 0 new mismatches.
- (c) No campaign-level error rate is claimed beyond the logged count of screening decisions and the single confirmatory test.

**M5: Fenced agent loop** (deferred; 3-4 days)
- The isolation acceptance in Loop integration: disposable-clone status, refs/hmz, worktree paths, Landlock negative tests.
- F1 and F3 with a Claude actor and a Codex veto.
- F3 ends with one stop_reason (`class_exhausted`, `stalled`, `escalated`, `violations`, `budget_exhausted`) and one outcome.
  - For `stalled`, the trial log shows 5 consecutive scored hypotheses with predicted and measured values.
  - The stop function is unit-tested with 3 refutations before 5 scored hypotheses (→ `class_exhausted`) and with 5 non-refuted scored hypotheses (→ `stalled`).
- Within budget: 100 SUMO runs, 8 h wall.

## Risks

1. **The gate may be unusable.**
   - If departure jitter produces a large paired spread, single changes are unmeasurable and the 275 case is effectively at its robustness floor.
   - If the spread is zero, robustness protection rests on draws and holdouts only.
   - M2 decides which before any Calibrate loop runs. With 12 held-out nulls the false-accept bound is coarse (22%, one-sided 95%).
2. **Localization is not cause.** Exit-time anchors can point at the edges where delayed vehicles waited, not at what made them wait: entry capacity against spillback, red against blocking, a moving queue against a downstream bottleneck, or hold side effects. Route mixes confound naive contrasts: round 1's +114 s anchor was an artifact of averaging over peers that never used the edge.
   - Mitigations: route-matched peers with n reported; co-anchor sets instead of a single argmax; stated "cannot tell" fields; extra outputs gated by parity; K3 attribution hit rates.
3. **Model interventions inside the anchors.** Hold nodes 205069138 and 14398547, both co-anchors for 0535 Z4@10800, and the declared permissive-green adaptation are modelling assumptions. An agent may "fix" counts by tuning around them.
   - Mitigations: the hold list is an invariant, and a hold-node flag appears on every anchor.
4. **Observation contract.**
   - The stop-line placement of 12/13 E1s (within 0.11 m of the lane end) may not match the field detectors.
   - The init rows come from a different package than the main rows.
   - The sensor error of the infrared fields is unknown.
5. **Goodhart on profile metrics.** Sampled centerline metrics and explore-mode unknowns invite gaming.
   - Mitigations: the frozen scored set, per-kind acceptance, exploit tests, the Codex review.
6. **Correspondence is a human bottleneck.** The only field-measured Build target (2349 lanes 2 and 3) is COR, and its facts must be regenerated. Unattended Build loops therefore run on model truth until a human signs a lock.
7. **Sandbox escape (M5).** Interop and PATH-readable /mnt/c; Landlock activity on the 6.18 WSL kernel is unconfirmed; hooks cannot gate Codex.
   - Mitigation: mandatory negative tests.
8. **Verifier drift.** Without its own venv, the `simulation_stall` child imports live code, and an edited verifier would judge code other than the recorded commit.
   - Mitigation: a separate implementation clone, plus INV-VERIFIER on both processes with a HEAD and clean-status check.
9. **Compute and memory.**
   - Runs take about 3 to 9 min each: 176.6-519 s on tracked full runs of this family, and older configurations took up to 1,052 s.
   - M2 needs at most 278 runs (13.6-40 CPU-h).
   - Four parallel libsumo runs with the hold on 15.7 GB are untested.
10. **Concurrency.** Another session edits docs, skills and tests and commits to the shared worktree. All code lands from the implementation clone through a PR, after that work merges.
11. **Claim creep.** Control tuned to counts is simulated control, not field SPaT. Counts do not identify OD. `accepted_for_review` is only an explore-to-review handoff.
12. **Narrow K3 coverage.** M2 validates the gate only on the demand-timing family. Its rates say nothing about control or network candidates, which need their own goals and K3 runs.

## Open questions

**Reviewer items deferred rather than fixed, with reasons**
- **Service envelope** (the missing point on saturation probes): not built. A valid envelope must cover admissible actuated histories, phase transitions, initial queues, shared lanes and downstream blocking. The parent also has 10 actuated phases on 9 tlLogics without a controlling detector. It stays a deferred diagnostic. Is it worth building at all, or should capacity questions go through K3-style known-answer experiments?
- **Thresholdout guarantee:** not implemented. Holdout replies are budgeted and claim no formal guarantee. Implementing true Thresholdout (noise addition, threshold, budget) is possible later but gives little with ≤5 queries per goal.
- **Prior human exposure to shape390** cannot be ruled out, so it is demoted to a diagnostic rather than "sealed". Can the owner state whether the 5-min values were consulted? Even a "no" would not restore it as a gate.
- **Control-family K3** (round 2, D2/MP3): deferred. The only goal is demand_timing, so control candidates are invalid under it. A control hypothesis cannot be judged until a control-class goal and its own K3 pass. Which family should that goal declare: a cycle-preserving split shift on the 10 static tlLogics, offsets, or actuated parameters?
- **Class-wide bound and roofline stop** (round 2): none exists. B1 is an all182 floor, and B2 depends on the candidate's own departures and init counts, so `roofline_reached` is withdrawn and no loop stops on a bound. Is a B2 minimized over the init counts that N2 admits worth deriving?
- **Partial cell transfers** (round 2, F2 interface): F2 skips surrogate proposals that one whole-cell shift cannot express. A `cell_transfer{from, to, n}` op with deterministic vehicle selection would need its own K3 placebo family. Is it worth adding?
- **Promotion false-accept rate** (round 2, MP3): not measured separately. It is bounded above by the screening rate, because promotion requires `improved` first. Only promotion power is measured, on the large positives.

**Owner decisions**
- **Perturbation family:** departure jitter ±0.5 s, or insertion-order permutation within equal-depart groups? Also the choice of K, the materiality floor g_min = 3 TAE (about 1.1% of 275), the provisional m_row 3, and the K3 magnitude grid of 300 s and 900 s.
- **Day-2 freeze** (2026-10-12): it needs online access to the Hamburg SensorThings service, run by a human outside any fence. Is that approved?
- **Do the infrared field counters count at the stop line,** like the 12/13 simulated E1s placed within 0.11 m of the lane end? Who can confirm this from the detector metadata in `fields.raw.json`?
- **Emergency braking:** should new events be a hard health failure (the predicate at `core/detector_demand_replay.py:209-214` ignores them), or stay the H3 guard used here (≤ the parent's maximum)?
- **Protected against permissive green** (G against g): it needs SPaT movement event states, which the repository does not ingest. Is any Hamburg SPaT source available? Without one, priority stays unscored.
- **Compute:** approve the M2 budget of at most 278 runs (M2a 78, M2b ≤200) and the optional M2-ext overnight run of 282?
- **Build `g0_shape`:** 3.0 m directed Hausdorff is a declared choice, not calibrated. Is it acceptable for K2 goals?

**Open items carried from round 1**
- **Prose against machine fields:** `prepared/scenario.json.gz` prose names three hold nodes, while its machine field and the executed run name four. Benchmarks are frozen, so the goal pins the machine field. Should a future packet correct the prose?
- **Rollback evidence:** the concurrent session made the hand-written rollback note optional (commit c654174). Should the twin trial row always carry `restore_source_sha256`, to preserve the rollback evidence AGENTS.md asks for?
- **2349 correspondence lock:** who signs it, and from which evidence? The original facts table and scorecard are gone and must be regenerated before any field Build loop.
- **Sandbox (M5):** is Landlock active at runtime on the 6.18 WSL kernel, and may `/etc/wsl.conf` set `appendWindowsPath=false` if the negative tests fail?
- **Public surface:** when should `astra_twin.py` become an `astra twin` subcommand or MCP tool? That needs README, catalog and skill-routing changes owned by the docs session.

## Provenance

**BASE.** v1, the synthesis of designs A, B and C (kernel fidelity, statistical validity, implementability). v2 keeps:
- the goal → test → benchmark → profile → gate structure;
- the frozen scored set and measurability_lost;
- per-kind fact classes and typed ops with absolute end_offset_m;
- seed-level Build guards on a clean source;
- the own-venv verifier clone and the standalone WSL workspace;
- fence-first containment with a sanitized PATH;
- Codex only as a fresh veto reviewer;
- trial rows instead of benchmark bundles for rejected trials;
- the leakage audit (demand built from counts);
- the day-2 refit protocol;
- the frozen-control C3 idea, deferred.

**CONFIRMED BY THE REVIEWER AND KEPT:** TAE 275, SSE 2833, exact 83/130, init52 97, GEH raw and hourly, and the 5770/902/866/53 decomposition with 0 early-arrival violations.

**VERIFIED FOR ROUND 1** (read-only at commit 0173672, with `E:\ASTRA-SUMO\.venv\Scripts\python.exe -I`; no pytest, no SUMO simulation)
- The four hold nodes in `run.json.gz` (top level and stall_monitor) and in `prepared/scenario.json.gz`.
- All 7 prepared aliases resolve from tracked files, with equal sha256 (`byte-references.json:853-887`).
- `pyproject.toml:33-43` extras and `.github/workflows/full-suite.yml:38`.
- wrong-turn sets only `dir` (`corridor/synthetic_networks.py:859-860`); `_measure` compares lane pairs and tls/linkIndex only (`core/network_calibration.py:384-431`).
- Field balance, B2, the UTC alignment, the count-window equality on 130/130 rows, `core/digital_twin.py:137-144, :441-451` (no conflict matrix), and `core/od_assignment_feedback.py:15-21, :45, :71`.

**RE-VERIFIED OR CORRECTED FOR ROUND 2** (same rules; HEAD still 0173672fa6ac)
- Copies of the prototype scripts were re-run:
  - the decomposition reproduces 5770/902/866/53, TAE 275, 0/8589 and delay p50/p90/p99 126/375/517 s, in 0.35 s in-process including parsing;
  - B2 gives 12 and 52, with 0119 Z4 at 2;
  - the 0535 Z4@10800 planned group measured {10800: 35, 11700: 22}.
- Route-matched localization (new script, 0.19 s parsing + 0.01 s computation):
  - Z13@3600: entry 44532531 for 36/36 delayed and 109/109 on-time vehicles, one route prefix, insertion +185.1 s, next +8.9 s on 199500674#0.
  - 0535 Z4@10800: delayed vehicles enter on −983227468#1 (22); on-time on 771709146#0 (28) and −983227468#1 (7). Differentials: 551303424 +64.3 s (126.7 against 62.4), −186713427 +55.4 s, 551303409 +51.2 s; 12-edge spread +293.1 s; downstream −81.0 s.
  - Junction types: 205069138 traffic_light, 14398547 priority, 265093112 priority. Connection 551303424 → 189775480 has TLS 205069138 linkIndex 2-5.
  - The independent check by a separately written a_v routine is still pending; it is M1(d).
- `evaluation/native/command.json.gz` stderr, 32 lines:
  - 20 actuated-phase warnings, which are 10 distinct (tlLogic, phase) pairs on 9 tlLogics, each reported for program '0' and 'readme_fixed_A';
  - 2 missing-green warnings (LSA104_part0 index 14, LSA535_part0 index 5);
  - 5 unused-states warnings;
  - 5 emergency brakings.
- Control file: 50 tlLogic, all 'readme_fixed_A', 40 actuated and 10 static; state characters G, O, g, r, u, y. tlLogic 205069138 is actuated with 5 phases.
- E1 offsets from the lane end: 11 at 0.100 m (one at 0.1003 m), 0535 Z9 at 0.109 m, 0535 Z14 at 1.398 m.
- Vehroute: 701,132 bytes gz, 6,497,441 bytes raw.
- Runtimes from `sumo.log.gz` Duration:
  - baseline 176.611 s, static-history reproduction 188.525 s;
  - `benchmarks/hamburg_ring1_directed_backlog_20261023_v1/tail-evaluation` 519.311 s, with 7,723 inserted;
  - older configurations up to 1,052 s; SPSA evaluations up to 3,809 s.
- The vType has sigma 0, speedFactor 1, speedDev 0, departLane best, departPos 5.1 and departSpeed max.
- Cells: 295 non-empty of 308 (22 × 14); 112 multi-field; 114 single-field cells with ≥10 vehicles; the maximum within-cell departure span is 594 s.
- `timeout_s` 4800 and seed 104 come from the shared driver `run_case.py:102`, reached through the alias `cases/h4_counter_z11_minus5_z5/measurement-implementation/run_case.py` (sha a64a03b8…). `all_stopped_timeout_s` 300 is in `native-request.json.gz`.
- Alias resolution:
  - `byte-references.json` sits at the packet root, with 325 aliases and the rule "Exact raw SHA aliases only.";
  - the frozen `read()` loads `case/'byte-references.json'` (`freeze_evidence.py:82-92`);
  - the archived passage report has reconciled true and inputs_unchanged true;
  - the parent's instant-e1.xml (21,424,405 bytes) is listed in `local-only-large-traces.json`.
- `sumo --help` of 1.27.1 lists `--fcd-output.filter-edges.input-file` (line 112), `--vehroute-output.internal` (169), `--edgedata-output` (191), `--lanedata-output` (193) and `--device.fcd.period` (887). `SaveTLSSwitchStates` is an additional-file element with 2 matches in `sumo.exe`; it is not a command-line option.
- Code:
  - `core/standard_nema_binding.py:857-866, :909, :1218-1230, :1928-1933`;
  - `intersection/candidate_binding.py:44-56, :254-256`;
  - `core/connection_mode_audit.py:2909-2913`;
  - `core/lane_correspondence.py:50-51, :54-56`;
  - `core/routeability_audit.py:315-334, :350-366, :423`;
  - `core/network_edit_operations.py:1149, :1225-1228, :1297-1311`;
  - `core/od_assignment_feedback.py:12, :17, :58-59, :71`;
  - `core/detector_demand.py:1007, :1026, :1033`;
  - `core/detector_demand_replay.py:64, :154-162, :191, :209-214`.

Prototype scripts (scratch only, not deliverables):
- `C:\Users\Danial\AppData\Local\Temp\claude\E--ASTRA-SUMO\27f6b40f-b59c-47be-ba59-77efdb002a05\scratchpad\v3\decomp.py`
- `C:\Users\Danial\AppData\Local\Temp\claude\E--ASTRA-SUMO\27f6b40f-b59c-47be-ba59-77efdb002a05\scratchpad\v3\bounds.py`
- `C:\Users\Danial\AppData\Local\Temp\claude\E--ASTRA-SUMO\27f6b40f-b59c-47be-ba59-77efdb002a05\scratchpad\v3\delay2.py`

The round-1 originals are in `...\scratchpad\v2\`. Its `a_v.json` was found empty and was regenerated in v3.

**NOT VERIFIABLE READ-ONLY, KEPT AS M2 MEASUREMENTS:** seed dependence, the runtime and size cost of `--vehroute-output.internal`, parallel scaling, and peak RSS of 4 libsumo runs on 15.7 GB.

**REVIEWER.** Codex (gpt-6-astra, read-only), round 1: good=false. Round-2 checker: two passes of wrong claims, unresolved items and internal inconsistencies. Every item is recorded in the changelog.

## Changelog (reviewer items)

- **codex_item**: FE1: the 275 baseline uses three junction-body hold nodes | **resolution**: Fixed. The goal's run block now lists all four: 14398547, 205069138, 18653534 and LSA104_part0, as in run.json.gz (top level and stall_monitor) and prepared/scenario.json.gz junction_body_hold_nodes. The run block is copied from machine fields only, because scenario.json prose names three nodes. INV-HOLD and the M1(b) check enforce this. | **sections_changed**: goal_spec, kernel_analogy_map, holdout_and_anti_gaming, first_milestones, open_questions
- **codex_item**: FE2: prepared network, control and demand exist only in git-ignored outputs/ | **resolution**: Fixed. Inputs resolve through byte-references.json:853-887 (network to the 266197525 candidate-v2 net, control to the 209 candidate-v3 control, demand to the evaluation demand). I re-verified all 7 hashes and confirmed every target is git-tracked. A core resolver is ported from the healthy_joint freeze_evidence.py:82-92 read(). Amended in round 2: packet-root base and a direct .gz fallback. | **sections_changed**: goal_spec, reuse_and_new_code, first_milestones, provenance
- **codex_item**: FE3: 51 as a quantity floor and 224 as attainable timing/delay headroom | **resolution**: Withdrawn. They are replaced by (a) the field balance identity R_init + R_main = −(deficit + post + unpassed); (b) B1, an all182 floor of 51 that is conditional on unchanged request totals and INV-PASS; (c) B2, a main-only supply bound recomputed per candidate (12 at parent init counts, 52 at init targets). Post-window passages are 29 (Z5 12, Z4 9). No bound rejects a measured candidate. | **sections_changed**: executive_summary, calibrate_profiler, bounds_roofline, profile_format, first_milestones
- **codex_item**: FE4: uv sync --frozen prepares SUMO, libsumo and dev checks | **resolution**: Fixed to `uv sync --python 3.12.13 --frozen --extra dev --extra native` (pyproject.toml:33-43; .github/workflows/full-suite.yml:38). | **sections_changed**: goal_spec, loop_integration, first_milestones
- **codex_item**: FE5: wrong-turn is observable through the fact table | **resolution**: Fixed. wrong-turn changes only connection.dir (synthetic_networks.py:859-860), while facts compare lane pairs and tls/linkIndex (network_calibration.py:384-431). A new direction-consistency check (class INT) detects it. The K1 observable list is corrected. Superseded in part by the round-2 entry on direction consistency: existing code already compares dir with geometry inside its own scope. | **sections_changed**: test_tiers, build_profiler, first_milestones
- **codex_item**: FE6: vehroute exit times support exact edge-entry and TLS-link delay attribution | **resolution**: Fixed. The design states what exit times identify (exact insertion delay; excess per edge including the preceding junction internals, truncated at the detector) and what they cannot (lane, the link when a pair has 2-5 controlled connections, red against spillback, cause). It proposes extra outputs gated by parity. Superseded in part by the round-2 FE6 entry: route-matched peers, corrected values, and corrected option list. | **sections_changed**: calibrate_profiler, profile_format, reuse_and_new_code
- **codex_item**: FE7: SUMO represents lanes only as parallel offsets | **resolution**: Fixed. REP is renamed OPX (op_not_expressible), a limit of the 14-op vocabulary (network_edit_operations.py:22-27), not of SUMO; PlainXML supports per-lane shapes. A set_lane_shape op is a candidate code change. Completed in round 2 (goal lint). | **sections_changed**: build_profiler, bounds_roofline
- **codex_item**: MP1: supply-bound conditions must match the gate; recompute per candidate | **resolution**: Fixed. B2 depends on the candidate's own init counts and on a_v recomputed on the candidate net and demand. Every bound carries a conditions block. Sensitivity is shown: 12 against 52. Amended in round 2: one conditions list everywhere, and no rule consumes a bound. | **sections_changed**: bounds_roofline, calibrate_profiler, profile_format
- **codex_item**: MP2: the decomposition is an accounting identity, not causal identification | **resolution**: Fixed. It is stated as set algebra that holds for any reference schedule. Its information comes from the free-flow schedule and the 0/8589 validity check. Recoverability by control is not claimed. The attribution table lists hypotheses with falsifiers. | **sections_changed**: calibrate_profiler, core_idea, profile_format
- **codex_item**: MP3: departure jitter is a robustness perturbation; disjoint A/A does not calibrate CRN differences; 8 replicas cannot support a 5% guarantee | **resolution**: Fixed. Jitter is a predeclared robustness family with HMAC keys and CRN by vehicle id. Disjoint A/A is dropped (d=0 under CRN). Calibration uses K3 cases with Clopper-Pearson bounds. Superseded in part by the round-2 MP3 entry: calibration and validation split, promotion pass. | **sections_changed**: noise_and_acceptance, test_tiers, first_milestones
- **codex_item**: MP4: the Build sign test passes 4 worse / 0 better / 6 ties (p=0.0625), including new collisions | **resolution**: Fixed. The sign test is removed, and new failures on any seed reject unconditionally. The 2349 trial (37 vehicles running) now rejects. Superseded in part by the round-2 MP4 entry: the non-inferiority margin is dropped. | **sections_changed**: noise_and_acceptance, profile_format, goal_spec, first_milestones
- **codex_item**: MP5: Holm over incidence-affected rows misses spillback; ungated spillover; campaign-level error; Thresholdout | **resolution**: Fixed. N1 gates all 182 rows with a max-row non-inferiority margin, and incidence is used for attribution only. One confirmatory test runs on unused draws. The Thresholdout claim is removed. Superseded in part by the round-2 MP5 entry: the trial log moves to M4. | **sections_changed**: noise_and_acceptance, holdout_and_anti_gaming, open_questions
- **codex_item**: MP6: a short saturation probe does not prove a service envelope | **resolution**: Deferred as a diagnostic (B4). No rule may use it until a known-answer test validates it. A parent self-check is stated to be necessary, not sufficient. Amended in round 2: the parent has 10 actuated phases without a detector (20 warnings), not 20 phases. | **sections_changed**: bounds_roofline, open_questions
- **codex_item**: MP7: correspondence does not prove geometry correct; index shifts are a heuristic; digital_twin gives no conflict matrix; sg_partition needs evidenced bindings and G/g semantics | **resolution**: Fixed. GEO_XSEC is now an index-shift probe heuristic. 2349 lanes 2 and 3 are 'correspondence unresolved; geometry neither confirmed nor refuted'. sg_partition needs locked ingress and egress bindings plus tl/linkIndex. The conflict rule is dropped, and G against g priority is unscored without SPaT. Amended in round 2: shift set and amber class corrected. | **sections_changed**: build_profiler, profile_format, open_questions
- **codex_item**: MP8: acceptance per fact kind; no-threshold and excess/tol rules undefined for categorical kinds and zero tolerances | **resolution**: Fixed. A per-kind table with line references: exact checks for lane_count, permissions, movements and signal binding; numeric tolerances only for stop line, centerline, width, speed and surface. Lint requires tol_eff ≥ max(declared, 2σ, 0.01) and min_iou < 1, and refuses a numeric tolerance on a categorical kind. | **sections_changed**: build_profiler, goal_spec, first_milestones
- **codex_item**: MP9: gate reachability (A4 stops before A7; P1 rejects simplifications); deterministic n=1 branch | **resolution**: Fixed. A decision table: hard H steps, non-inferiority N steps, then independent improvement and simplification branches. An explicit K=0 nominal-only branch carries robustness not_assessed and is never promotable. Amended in round 2: the Otherwise rows and promotion symmetry. | **sections_changed**: noise_and_acceptance
- **codex_item**: MP10: seal the full observation contract; shape390 prior exposure; day-2 claim | **resolution**: Fixed. The observation block covers UTC alignment, pass notification and bin rule, field phenomenonTime, lineage, the completeness rule, early departures and E1 placement. shape390 is demoted to report-only. Day 2 is stated to evaluate the structure-plus-refitting procedure. | **sections_changed**: goal_spec, holdout_and_anti_gaming
- **codex_item**: D1: start with the offline profiler and a minimal isolated verifier; defer the hmz flow, sealing, ledger, schemas and the 2.6k-line package | **resolution**: Accepted. M1 is the offline profiler plus a verifier clone with its own venv, with zero SUMO runs. The hmz flow, fence, sealing, schemas and caches move to M5. Line counts were corrected in round 2: M1 about 860, M5 about 1.1k. | **sections_changed**: first_milestones, reuse_and_new_code, loop_integration, executive_summary
- **codex_item**: D2: bring K3 forward | **resolution**: Accepted. K3 is M2, before any loop. Superseded in part by the round-2 D2/MP3 entry: demand-timing family only. | **sections_changed**: test_tiers, noise_and_acceptance, first_milestones
- **codex_item**: D3: keep the linearized optimum as a surrogate, not a lower bound | **resolution**: Accepted. B3 surrogate is for ranking only (od_assignment_feedback.py:15-21, :45, :71). 'Linearized LP bound' and 'structural bound' are removed. | **sections_changed**: bounds_roofline, calibrate_profiler, kernel_analogy_map
- **codex_item**: D4: do not prioritize exact 130/130 or performance caching | **resolution**: Accepted. Exact 130 is report-only, and tier T4 is removed. Caches, checkpoints, cordon and meso are deferred with sizes. Only the Build index cache stays in M3, for K2 runtime. | **sections_changed**: calibrate_profiler, scaling, goal_spec
- **codex_item**: PF1: M1 is the right pilot after revision: remove 51/224, restore LSA104_part0, resolve inputs through aliases | **resolution**: Done. M1 acceptance is runnable today from tracked files and pins every reproduced number, including the balance, bounds and localization values. | **sections_changed**: first_milestones
- **codex_item**: PF2: the A/A acceptance check is not runnable as written; commands and trace levels do not exist; calibration protocol incomplete | **resolution**: Fixed. M1 needs no SUMO. All SUMO work, with new CLI signatures, moves to M2. Superseded in part by the round-2 PF2 entry: provisional values, α, magnitude grid, and no trace levels. | **sections_changed**: first_milestones, noise_and_acceptance, reuse_and_new_code
- **codex_item**: PF3: the M0 unchanged git-status check is invalid while another session edits the worktree | **resolution**: Fixed (M5). The dry run uses a disposable clone whose status, refs and worktree list must stay byte-identical. For E:\ASTRA-SUMO, only refs/hmz (empty) and the worktree paths are compared, plus Landlock negative tests. Evidence: HEAD moved c654174 to 0173672 during the round-1 review. | **sections_changed**: loop_integration, first_milestones
- **codex_item**: PF4: M3 allows stopping after three stalled rounds while requiring five scored hypotheses | **resolution**: Fixed in round 1 with stall_scored = 5. Superseded by the round-2 PF4 entry: the class-switch wording was wrong, because a goal has one class. | **sections_changed**: loop_integration, goal_spec, first_milestones
- **codex_item**: Self-found: v1 O1 flag (E1 13.4 m on 13.5 m lanes for Z12/Z13) is not distinctive | **resolution**: Withdrawn. The stop-line placement is scenario-wide and is recorded in the observation contract; whether the field counters match is an open question. Corrected in round 2: 12 of 13 E1s sit within 0.11 m of the lane end, 11 of them at 0.100 m. | **sections_changed**: calibrate_profiler, goal_spec, open_questions
- **codex_item**: Self-found: v1 anchor for Z12/Z13@3600 (vehicles held on red at 244203525) | **resolution**: Replaced with verified localization. Z13@3600 delayed_out (36/36) enter on 44532531, and the difference is insertion delay (251 s against 66 s). Their late_in 32 (23 for Z12) equals the init shortfall. The 0535 Z4@10800 anchor was superseded in round 2: there is no single anchor. | **sections_changed**: profile_format, calibrate_profiler, executive_summary
- **codex_item**: Self-found: health predicate ignores SUMO warnings | **resolution**: Added integrity rows and an H3 emergency-braking guard; whether it should be hard health is an open question. Corrected in round 2: 20 warnings are 10 phases reported for two programs. | **sections_changed**: calibrate_profiler, noise_and_acceptance, open_questions
- **codex_item**: Round 2, PF4: 3 refutations force a class switch, but a goal carries exactly one class | **resolution**: Fixed with option (b). Three refuted hypotheses since the last acceptance give a terminal stop_reason, class_exhausted. stall_scored = 5 consecutive scored hypotheses without acceptance gives stalled. A refuted hypothesis counts toward both; class_exhausted wins on a tie; an accepted candidate resets both. 'Refuted' is defined (measured sign opposite to the prediction on more than half of the predicted rows). Other classes need their own goal and K3. M5 adds a unit test with 3 refutations before 5 scored hypotheses. | **sections_changed**: goal_spec, loop_integration, first_milestones
- **codex_item**: Round 2, D2/MP3: half the K3 cases use a control family that the demand_timing goal makes invalid, so 4/4 large positives cannot be met | **resolution**: Fixed. M2 K3 is limited to cell_depart_shift under the demand_timing goal: 8 positives (4 single-field cells with ≥10 vehicles × 300/900 s) and null placebos from the same family. The control family (split shift on 1 of the 10 static tlLogics; 40 actuated and 10 static verified) is deferred to a control-class goal with its own K3. static_split_shift is removed from the M2 code. | **sections_changed**: test_tiers, noise_and_acceptance, reuse_and_new_code, first_milestones, open_questions, risks
- **codex_item**: Round 2, MP3: thresholds fitted and validated on the same 12 nulls; promotion rule never exercised | **resolution**: Fixed. M2a fits g, m_row, m_init and k_post from the parent panel and 12 calibration nulls by stated formulas. These are frozen and hashed into gate-<sha8>.json. M2b then scores 12 fresh validation nulls, placebo-on-real on those runs, and the 8 positives, and refuses to run without the frozen sha. A promotion pass on fresh keys (≤40 runs) measures promotion power. The promotion false-accept rate is stated as bounded by the screening rate, not measured. A failed exit needs a new protocol version and a fresh validation set. Runs: ≤278. | **sections_changed**: noise_and_acceptance, first_milestones, goal_spec, reuse_and_new_code, open_questions
- **codex_item**: Round 2, MP4: the Build arrival-loss margin can never apply under the hard rule | **resolution**: Fixed by dropping the margin. L3 is a hard rule only: per seed, with the same frozen trips, the candidate's failing requests must be a subset of the source's (routeability_audit.py:315-334, :423). Travel and arrival time are reported only. M3(e) adds a seed with 1-2 lost arrivals and no collision, and a fix-one-break-one seed, both rejected. | **sections_changed**: goal_spec, noise_and_acceptance, profile_format, first_milestones, test_tiers
- **codex_item**: Round 2, MP5: campaign accounting relies on a ledger and sealing deferred to M5, yet M4 runs adaptive loops | **resolution**: Fixed. A minimal append-only trial log (twin/trials.py, 60 lines, JSONL with the previous row's sha256) moves into M4 and records every judged candidate and draw key. The confirmatory comparison uses keys absent from the log. M4(b) no longer needs sealed facts: held-out approach facts are removed from the flow's scored set at goal-create. M4 claims nothing beyond the logged count of screening decisions and the single confirmatory test. | **sections_changed**: loop_integration, noise_and_acceptance, holdout_and_anti_gaming, reuse_and_new_code, first_milestones
- **codex_item**: Round 2, FE6 and wrong claim: the 0535 Z4@10800 +114 s anchor is route-confounded | **resolution**: Fixed. Localization now contrasts only route-matched peers and reports n per group. Anchors within max(15 s, 25% of the top value) are co-anchors. Recomputed: 22 delayed against 7 same-route peers (28 excluded). Co-anchors: 551303424 +64.3 s (126.7 against 62.4), −186713427 +55.4 s, 551303409 +51.2 s; the excess spreads +293.1 s over 12 edges, and 81.0 s is recovered downstream. Z13@3600 holds (+185.1 s; all 109 peers share entry 44532531). The golden value in M1(g), the profile hotspot, the executive summary and the risks are updated. | **sections_changed**: executive_summary, calibrate_profiler, profile_format, first_milestones, risks, provenance
- **codex_item**: Round 2, FE7: goal lint still lists REP | **resolution**: Fixed. Lint now refuses targets of class COR, SRC, OPX or UNK. | **sections_changed**: goal_spec
- **codex_item**: Round 2, PF2: no initial m_row, no α, undefined magnitudes, trace levels in cache key | **resolution**: Fixed. The goal's gate_protocol declares α 0.05 (one-sided), g_min 3, provisional g 10, m_row 3, m_init 2 and k_post 5 (for diagnostics only), and the magnitude grid (300 s, 900 s). 'Trace level' is removed from the cache key in favour of the parity-tested trace option set. Filtered FCD is deferred with the other output presets, not called a 'debug level'. | **sections_changed**: goal_spec, noise_and_acceptance, calibrate_profiler, scaling
- **codex_item**: Round 2, wrong claim: the exact static-history replay implies seed variance is likely zero | **resolution**: Corrected. The replay used seed 104 and shows determinism only. Only the vType facts suggest seed dependence may be zero, and M2(a) measures it with a seed-11 run. | **sections_changed**: noise_and_acceptance, first_milestones
- **codex_item**: Round 2, wrong claim: C1 runtime 177-361 s; M2 7.6-15.5 CPU-h; 3-6 min per run | **resolution**: Corrected. Baseline 176.6 s and static reproduction 188.5 s. Other tracked full 14400-s Ring1 runs took up to 519.3 s (directed_backlog tail-evaluation, verified), and older configurations 546-1,052 s. Planning uses 176.6 s expected and 519 s pessimistic. M2 is 13.6-40.1 CPU-h for ≤278 runs; risk 9 says 3-9 min per run. | **sections_changed**: test_tiers, noise_and_acceptance, risks, first_milestones
- **codex_item**: Round 2, wrong claim: vehroute.internal makes the vehroute about 2 → 4 MB, runtime negligible | **resolution**: Corrected. The vehroute is 6,497,441 bytes raw and 701,132 bytes gz; the estimate with internal edges is about 13 MB raw or 1.4 MB gz. Runtime is unmeasured and recorded in M2(b). | **sections_changed**: calibrate_profiler
- **codex_item**: Round 2, wrong claim: 20 actuated phases without a controlling detector | **resolution**: Corrected. 20 warnings are 10 distinct phases on 9 tlLogics, each reported for program '0' and 'readme_fixed_A'. The warning count key stays 20; the prose says 10 phases. | **sections_changed**: profile_format, calibrate_profiler, bounds_roofline, open_questions
- **codex_item**: Round 2, wrong claim: no existing audit compares dir with geometry | **resolution**: Corrected. standard_nema_binding.py already does so in its scope (:857, :863-866, :909, :1933), and candidate_binding.py:44-56, :254-256 checks the expected DAG direction. The new check reuses _turn_from_delta (:1218-1230) and its thresholds for every connection (about 40 lines instead of 60). M3(a) records whether the NEMA gate also blocks K1 wrong-turn. | **sections_changed**: build_profiler, test_tiers, reuse_and_new_code, first_milestones
- **codex_item**: Round 2, wrong claim: 12 of 13 E1 sit 0.1 m before the lane end | **resolution**: Corrected to: 12 of 13 within 0.11 m (11 at 0.100 m, 0535 Z9 at 0.109 m), 0535 Z14 at 1.398 m. | **sections_changed**: goal_spec, calibrate_profiler, risks, open_questions
- **codex_item**: Round 2, wrong claim: 205069138 carries only a hold-node flag | **resolution**: Corrected. 205069138 is also an actuated TLS (5 phases; 4 candidate links 551303424 → 189775480). The co-anchor carries both flags, as the EDGE/NODE row requires. | **sections_changed**: profile_format, calibrate_profiler
- **codex_item**: Round 2, wrong claim: index shifts {−1, 0, +1} generated at :55-56 | **resolution**: Corrected. lane_correspondence.py:54-56 generates −1 and +1; the primary mapping is measured at :50-51. | **sections_changed**: build_profiler, reuse_and_new_code
- **codex_item**: Round 2, wrong claim: {y, Y} → amber | **resolution**: Corrected to {y}. The SUMO alphabet is r, y, g, G, s, u, o, O; the control file uses G, O, g, r, u, y. | **sections_changed**: build_profiler
- **codex_item**: Round 2, wrong claim and inconsistency: all three option names verified in sumo --help; provenance lists --lanedata-output | **resolution**: Corrected. SaveTLSSwitchStates is an additional-file timedEvent type, present in the binary, not a CLI option. The profiler table cites help lines 169, 112 and 887. Provenance lists exactly the options used, and --lanedata-output is no longer claimed as used. | **sections_changed**: calibrate_profiler, provenance
- **codex_item**: Round 2, inconsistency: goal immutability against M2 writing gate values into the goal | **resolution**: Fixed. Fitted gate values live in a separate hashed artifact (gate-<sha8>.json) recorded by every verdict. The verifier commit moves out of the goal into each run, profile and cache key; a delta across commits needs a byte-identical parent re-profile. M1 profiles stay diffable after M2. | **sections_changed**: goal_spec, profile_format, scaling
- **codex_item**: Round 2, inconsistency: a non-robust nominal improvement is labelled worse | **resolution**: Fixed. The Otherwise rows are now: d0 ≤ −g → not_robust; d0 ≥ +g → worse; |d0| < g → not_material. | **sections_changed**: noise_and_acceptance
- **codex_item**: Round 2, inconsistency: Calibrate invariants and bounds.py not scheduled in M1/M2 | **resolution**: Fixed. twin/invariants.py Calibrate part 1 (INV-INPUTS, INV-OBS, INV-HOLD, INV-PASS) is in M1, and part 2 (the other eight) in M2. twin/bounds.py is an M1 file. The M5 invariants shrink to INV-SCOPE, INV-LEDGER and INV-REPLICAS. | **sections_changed**: reuse_and_new_code, holdout_and_anti_gaming, test_tiers
- **codex_item**: Round 2, inconsistency: line counts and op-vocabulary floor milestone | **resolution**: Fixed. Totals are recomputed from the tables: M1 about 860, M2 about 920, core edits about 165, M3 about 890, M4 about 390, M5 about 1.1k plus 4 schemas. The fitted op-vocabulary floor is M5 in both sections. | **sections_changed**: executive_summary, reuse_and_new_code, bounds_roofline
- **codex_item**: Round 2, inconsistency: bounds used beyond their conditions (SOL, refuse-before-run, roofline without a trigger) | **resolution**: Fixed. No rule consumes a bound in M1-M5. Refuse-before-run is removed, because B1 and B2 conditions need the candidate's run. roofline_reached and bound_limited are withdrawn, since no class-wide bound exists (an open question). SOL is split per statistic (all182 with B1, main with B2) and no longer uses g. | **sections_changed**: bounds_roofline, kernel_analogy_map, core_idea, loop_integration, open_questions
- **codex_item**: Round 2, inconsistency: B2 conditions differ between profile and bounds | **resolution**: Fixed. Both list one set: a_v recomputed from the candidate's net and demand, the candidate's own init counts, and INV-PASS on its run. 'Departures unchanged' is a special case, no longer a separate condition. | **sections_changed**: profile_format, bounds_roofline
- **codex_item**: Round 2, inconsistency: M1 hourly GEH depends on an M2-M3 core edit | **resolution**: Fixed. Hourly GEH is computed in twin/count_profile.py as 2 × geh_value on the 15-min counts (identical to GEH on hourly flows). The geh_value(interval_s) edit is removed. | **sections_changed**: calibrate_profiler, first_milestones, reuse_and_new_code
- **codex_item**: Round 2, inconsistency: verifier identity (M1 files written in the pinned verifier clone) | **resolution**: Fixed. Edits and T0-T2 run in an implementation clone, E:\astra-impl. The verifier is a second clone, detached at a committed sha and never edited. INV-VERIFIER and doctor check HEAD, a clean status and the import path. | **sections_changed**: first_milestones, loop_integration, test_tiers, holdout_and_anti_gaming, risks
- **codex_item**: Round 2, inconsistency: materiality threshold owned by a human but raised automatically | **resolution**: Fixed. A human sets g_min, K and the family. M2a fits g ≥ g_min by a predeclared formula. On a failed exit M2 stops, and only a human revises it, under a new protocol version. | **sections_changed**: executive_summary, noise_and_acceptance, first_milestones
- **codex_item**: Round 2, inconsistency: simplified promotion skips N1 and N2 on fresh draws | **resolution**: Fixed. Both promotion branches require N1 and N2 on the fresh mean. | **sections_changed**: noise_and_acceptance
- **codex_item**: Round 2, inconsistency: F3 terminal states (bound_limited undefined, missing escalation and budget) | **resolution**: Fixed. Every flow ends with one stop_reason from a per-flow list in the flows table, and one outcome (accepted_for_review, not_confirmed, no_candidate). M5 acceptance uses the same lists. | **sections_changed**: loop_integration, first_milestones, risks
- **codex_item**: Round 2, inconsistency: H1-H3 and B1-B3 name both gate steps or bounds and holdouts | **resolution**: Fixed. Holdouts are renamed HO-C0 to HO-C4 and HO-B1 to HO-B3. Gate steps (H1-H3, N1, N2, I, S) and bounds (B1-B5) keep their ids. | **sections_changed**: holdout_and_anti_gaming, kernel_analogy_map
- **codex_item**: Round 2, inconsistency: HMAC draws placed in M5 while M2 needs them | **resolution**: Fixed. HMAC-keyed draws are M2 (twin/perturb.py). M5 adds only secret-committed keys for HO-C2. | **sections_changed**: executive_summary, holdout_and_anti_gaming, noise_and_acceptance
- **codex_item**: Round 2, inconsistency: M3(b) asks inverse ops to reach V = 0 on out-of-vocabulary perturbations | **resolution**: Fixed. V = 0 is required only for ledger faults and raw-XML faults a typed op can express. A raw per-lane shape change must escalate as OPX and name the lane. | **sections_changed**: first_milestones, test_tiers
- **codex_item**: Round 2, inconsistency: budget unit makes stall_scored unreachable | **resolution**: Fixed. The budget is 100 SUMO runs and 8 h. The arithmetic is stated: 2 promotions × 16 + confirmatory 16 + 10 scored hypotheses × 5 = 98. | **sections_changed**: goal_spec, loop_integration, first_milestones
- **codex_item**: Round 2, inconsistency: surrogate per-cell flow changes have no translation to whole-cell ops | **resolution**: Fixed. A defined translation: a donor cell paired with one adjacent recipient period in the same OD group becomes a whole-cell cell_depart_shift by the smallest shift that moves exactly |Δ| scheduled departures across the boundary. Other cases are logged as not_expressible, never approximated. The multi-field side effect (112 of 295 cells) is stated. A cell_transfer op is an open question. | **sections_changed**: loop_integration, calibrate_profiler, open_questions
- **codex_item**: Round 2, inconsistency: verdict vocabulary against AGENTS.md explore statuses | **resolution**: Fixed. Records carry status measured, no_effect or blocked (as at network_edit_operations.py:1149). Gate and loop labels move to separate screening, stop_reason and outcome fields with no promotion authority. no_effect is no longer a screening label (now not_material). | **sections_changed**: executive_summary, noise_and_acceptance, loop_integration, profile_format
- **codex_item**: Round 2, inconsistency: INV-DEMAND byte equality ignores jittered draws | **resolution**: Fixed. The base demand equals apply(ops, parent), and each run's demand equals perturb_k(base) for its declared key. | **sections_changed**: holdout_and_anti_gaming
- **codex_item**: Round 2, inconsistency: timeout_s claimed to come from run.json | **resolution**: Fixed. Provenance is per field: run.json.gz, native-request.json.gz (all_stopped_timeout_s 300, added to the run block), and timeout_s 4800 from the shared driver run_case.py:102 through its byte-reference alias. | **sections_changed**: goal_spec, first_milestones, provenance
- **codex_item**: Round 2, inconsistency: G0 Hausdorff rule has no bound in the Build goal | **resolution**: Fixed. The Build goal declares g0_shape (vertex delta 8, heading 35°, directed Hausdorff 3.0 m; declared, not calibrated). Lint refuses a Build goal without it. | **sections_changed**: goal_spec, noise_and_acceptance, first_milestones, open_questions
- **codex_item**: Round 2, inconsistency: M2's exit criterion said to gate M3 | **resolution**: Fixed. It gates only the Calibrate loops (M4 F2, M5 F3). M3 runs in parallel with M2, and F1-lite depends on M3's K1/K2. | **sections_changed**: noise_and_acceptance, loop_integration, core_idea, first_milestones
- **codex_item**: Round 2, unresolved: a_v-dependent values reproduced only with the design's own prototype | **resolution**: Addressed. M1(d) requires a separately written a_v routine in the golden test to agree on all 8589 vehicles before any value is pinned. M2(a) adds a toy-net SUMO free-flow check. Provenance labels these values as single-implementation until then. | **sections_changed**: calibrate_profiler, first_milestones, executive_summary, provenance
- **codex_item**: Round 2, unresolved: prototype timings may exclude XML parsing | **resolution**: Clarified by re-measuring. 0.35 s (decomposition) and 0.20 s (localization: 0.19 s parsing, 0.01 s computation) are in-process, including gzip and XML parsing, and exclude interpreter start-up. M1(h) measures the end-to-end CLI against 10 s. | **sections_changed**: test_tiers, scaling, calibrate_profiler, first_milestones
- **codex_item**: Round 2, unresolved: seed variance, vehroute.internal runtime, 4 parallel runs on 15.7 GB | **resolution**: Kept as M2 measurements, as planned. These claims are stated as unmeasured. | **sections_changed**: noise_and_acceptance, calibrate_profiler, provenance
- **codex_item**: Round 2, unresolved: alias resolver port (packet-root base, chained .gz fallback) | **resolution**: Fixed. core/benchmark_references.py takes the packet root (byte-references.json with cases/<case>/... keys, 325 aliases) and implements the .gz fallback directly, including the .gz.gz passage report. It does not import frozen modules. | **sections_changed**: reuse_and_new_code, goal_spec, first_milestones
- **codex_item**: Round 2, unresolved: M1 cannot call read_contributing_e1_passages on the parent | **resolution**: Fixed. In M1, INV-PASS and M_fb read the archived evaluation/contributing-passages.json.gz.gz (reconciled true, inputs_unchanged true). The parent instant-e1.xml (21,424,405 bytes) is local-only. Only judged runs from M2 on call read_contributing_e1_passages. | **sections_changed**: calibrate_profiler, holdout_and_anti_gaming, goal_spec, first_milestones
