# Benchmark and Evaluation Landscape (Snapshot 2026-11)

Status: research snapshot, 2026-11-16. This document does not change the product boundary in [`README.md`](../../README.md) or [Architecture](../architecture.md).

## Purpose

This snapshot reviews public benchmarks and evaluation tools that are close to ASTRA. It records what each project does well and which of these practices ASTRA can adopt. The main target is one capability that ASTRA does not have yet: a user can plug in an own signal-control method and compare it with a fixed set of baseline methods on ASTRA-built scenarios.

## What "Benchmark" Means

In machine-learning and LLM research, a benchmark is a combination of one or more datasets and a metric. The research community uses it as a shared framework to compare methods ([Raji et al. 2021, Sec. 2.2](https://arxiv.org/html/2111.15366v1)). Recent agent benchmarks add an executable evaluation harness. A benchmark in this sense has these parts:

| Part | Example |
|---|---|
| Fixed, versioned task set | SWE-bench: 2,294 task instances from GitHub issues |
| Reference result or goal state | SWE-bench reference patches; τ-bench annotated goal database state |
| Automatic scorer | SWE-bench FAIL_TO_PASS and PASS_TO_PASS tests; τ-bench end-state comparison |
| Metric over the task set | "% resolved", pass@k, pass^k |
| Comparison of different systems | Leaderboard or results table |
| Reproducibility | Docker images per instance, task versions, fixed prompts |

Sources: [SWE-bench](https://www.swebench.com/original.html), [τ-bench](https://arxiv.org/abs/2406.12045), [HELM](https://arxiv.org/abs/2211.09110), [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness).

## Reviewed Projects

### Signal-control benchmarks (plug-in controller, fixed baselines)

| Project | What it evaluates | Scenarios | Interface and baselines | Link |
|---|---|---|---|---|
| RESCO (NeurIPS 2021 D&B) | RL signal controllers in SUMO | Ingolstadt and Cologne scenarios with calibrated demand; single and multi-agent tasks | Gym interface. Baselines: Fixed Time, Greedy, Max Pressure. Learners: IDQN, IPPO, MPLight, MPLight*, FMA2C | [paper](https://people.engr.tamu.edu/guni/papers/NeurIPS-signals.pdf), [repo](https://github.com/Pi-Star-Lab/RESCO) |
| sumo-rl | SUMO signal-control environment | Includes the RESCO network and route files | Gymnasium (single agent) and PettingZoo (multi agent). Custom `ObservationFunction` class and `reward_fn` | [repo](https://github.com/LucasAlegre/sumo-rl), [PyPI](https://pypi.org/project/sumo-rl) |
| LibSignal (Machine Learning, 2024) | RL signal control across simulators | 9 datasets; SUMO and CityFlow | Modular pipeline, Gym interface, 3 baseline and 7 RL controllers | [paper](https://arxiv.org/abs/2211.10649), [site](https://darl-libsignal.github.io/) |
| PyTSC (2024) | Multi-agent RL signal control | 10 scenarios, including Cologne, Ingolstadt, Pasubio; SUMO and CityFlow | Unified MARL platform | [paper](https://arxiv.org/abs/2410.18202) |
| Sim2Signal (2026) | Sim-to-real gap of RL signal control | 10 calibrated networks from 5 locations | Train in CityFlow, test in SUMO, with perturbed model parts | [paper](https://arxiv.org/abs/2609.01676), [repo](https://github.com/DaRL-LibSignal/Sim2Signal) |

### LLM-agent evaluation for SUMO

| Project | What it evaluates | How success is judged | Link |
|---|---|---|---|
| SimSkill (2026) | An LLM agent that uses SUMO command-line tools and TraCI. Two frozen sets of 40 tasks in difficulty tiers: network and demand generation, signal control, TraCI control, output analysis; set V2 adds freeway, transit, and freight tasks | An LLM judge reruns the simulations and gives a binary verdict. No reference outputs or fixed checker are published | [paper](https://arxiv.org/abs/2609.03753), [repo](https://github.com/qiliuchn/SimSkill-V1) (Apache-2.0) |
| ChatSUMO, AgentSUMO, ChatSUMO-Agent | LLM agents that generate SUMO scenarios | Case studies chosen by the authors; no shared task set | [ChatSUMO](https://arxiv.org/abs/2409.09040), [AgentSUMO](https://arxiv.org/abs/2511.06804), [ChatSUMO-Agent](https://trid.trb.org/View/2704119) |

### Transport knowledge and transit data

| Project | Content | Link |
|---|---|---|
| ChatGPT for GTFS | GTFS Semantics and GTFS Retrieval question sets with reference answers | [arXiv 2308.02618](https://arxiv.org/abs/2308.02618) |
| TransitGPT | 100 GTFS query tasks solved by generated code | [arXiv 2412.06831](https://arxiv.org/abs/2412.06831) |
| TransportationGames | 10 transport knowledge tasks (Chinese) | [arXiv 2401.04471](https://arxiv.org/abs/2401.04471) |
| TransportBench | 140 undergraduate transport engineering problems | [arXiv 2408.08302](https://arxiv.org/abs/2408.08302) |
| CityBench (KDD 2025) | Urban tasks in 13 cities, including traffic control | [arXiv 2406.13945](https://arxiv.org/abs/2406.13945) |

### Benchmark quality checklists

- Agentic Benchmark Checklist (ABC): task validity, outcome validity, and reporting ([arXiv 2507.02825](https://arxiv.org/abs/2507.02825)).
- BetterBench: 46 best practices over the benchmark lifecycle; most benchmarks do not report statistical significance and are hard to replicate ([arXiv 2411.12990](https://arxiv.org/abs/2411.12990)).

## Practices Worth Adopting

| Practice | Seen in | Why it matters for ASTRA |
|---|---|---|
| A standard controller interface | RESCO, sumo-rl, LibSignal | Other researchers can run their method without changes to ASTRA code |
| A fixed set of baseline controllers | RESCO, LibSignal | A new method is always compared with the same references |
| Scenarios as frozen, versioned assets | RESCO, PyTSC, Sim2Signal, lm-evaluation-harness task versions | Results stay comparable across releases |
| Separate "fix" and "no-regression" checks | SWE-bench FAIL_TO_PASS / PASS_TO_PASS | Matches ASTRA's target-scope checks and outside-scope regression checks |
| Verify the reference result itself | SWE-bench `gold` predictions run | Golden outputs must pass their own checks before they are used as references |
| End-state comparison | τ-bench | Compare the final network and signal state, not the agent's step sequence |
| Repeated trials | τ-bench pass^k, BetterBench | Agent-built networks and stochastic simulations need more than one run |
| Robustness tests | Sim2Signal | Test controllers under changed demand or model parameters |
| Benchmark checklist per case | ABC, BetterBench | Makes task validity and claim limits explicit |

## Current ASTRA Position

This section describes `main` (release 1.3.1) on 2026-11-16. The development branches are ahead of it; the differences that matter here are noted.

ASTRA already has several benchmark parts:

- XS-1 and XS-2 (`examples/03_xs1_four_way_tls`, `examples/04_xs2_three_way_tls`) start from frozen OSM extracts, run named checks, and keep committed golden outputs (`golden/`) for rerun comparison.
- Project records bind inputs to SHA-256 hashes and keep stage status, rollback, and signal ownership evidence.
- `examples/01_signal_control_audit` defines fixed-time, max-pressure, and data-informed audit cases. It requires completion, unfinished vehicles, teleports, pairing, and controller identity before metric averages.
- `benchmarks/` is the declared place for frozen evaluation assets. The release omits these datasets. The development branches hold calibration and diagnosis packages for the Hamburg Ring1 corridor, but none is a scenario set for controller comparison.

ASTRA does not yet have:

1. a controller interface for user-supplied methods;
2. runnable baseline controllers;
3. a frozen scenario set built for controller comparison;
4. a fixed metric set with repeated seeds and a results table.

## Candidate Improvements

Each item names its capability group (Build, Calibrate, or Simulate). The order is a suggestion, not a release plan.

1. **Controller interface (Simulate).** Define a small controller contract on top of TraCI. Keep it compatible with the sumo-rl Gymnasium/PettingZoo interface, so that existing RL and rule-based controllers run without changes. Record controller identity, version, and parameters in the run record.
2. **Baseline controllers (Simulate).** Provide at least: the official or source signal plan from the signal ownership record, SUMO actuated control, Max Pressure, and a Webster-type fixed-time plan. Each baseline passes the existing signal-control audit before it is used as a reference.
3. **Frozen scenario set (Build, Calibrate).** A scenario is network + demand + signal plan + seeds, bound by hashes in a manifest under `benchmarks/`. Each scenario has a short card: data sources, calibration evidence, known limits, and claim tier. ASTRA's advantage over RESCO and PyTSC is that each scenario comes with a project record that shows how it was built and checked.
4. **Metric set and statistics (Simulate).** Report completion and teleports first, as the audit examples already require. Then report delay, stops, queue length, throughput, and bus person delay. Run several seeds and report mean and confidence interval.
5. **Build-task regression suite (Build).** Formalize XS-1 and XS-2 checks as target checks and outside-scope regression checks. Add a "gold" run that verifies each golden output against its own checks. For agent-built runs, report repeated-trial success (pass^k).
6. **Per-case checklist (all groups).** Add the ABC task-validity and outcome-validity questions to each frozen case.
7. **Results table.** Keep a versioned results file per scenario set, so that new methods can be compared with the baselines.

Practices not adopted as the only verdict: an LLM judge (SimSkill). It conflicts with ASTRA's evidence rules in `AGENTS.md`, because a verdict must bind to exact hashes and deterministic checks. An LLM judge can add a secondary review.

## Interpretation Rule

The projects and numbers above are as reported by their authors on the access date. This snapshot is not a claim that ASTRA scenarios are a benchmark. ASTRA reference cases become a benchmark only after items 1–4 exist and results for more than one method are published.
