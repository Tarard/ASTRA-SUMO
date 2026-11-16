# AI and SUMO Project Survey (Snapshot 2026-11)

Status: research snapshot, 2026-11-16. This document does not change the product boundary in [`README.md`](../../README.md) or [Architecture](../architecture.md).

## Purpose

This snapshot lists public projects that combine AI methods (LLM agents or reinforcement learning) with SUMO. For each project it records:

- what the project does;
- which idea ASTRA can learn from it;
- whether ASTRA may reuse its code, based on its license.

Repository data (stars, license, last push) was read from the GitHub API on 2026-11-16. These values change over time.

## Reuse Rule

ASTRA is released under the MIT license. The default rule for all projects below is:

1. **Learn the idea and write ASTRA's own code.** Read papers, documentation, and public interfaces. Then design and write the ASTRA version from ASTRA's own requirements. Do not copy code, and do not translate code line by line.
2. **Copy code only after an explicit decision**, and only when the license allows it. Record each copied part in a third-party notice with the source repository, commit, file, and license.
3. **Data and scenario files have their own licenses.** Check the data license separately before you put any network, route, or detector file into `examples/` or `benchmarks/`.

| License | Projects | What ASTRA may do |
|---|---|---|
| MIT | sumo-rl, Flow, AgentSUMO | Learn the idea. Code may be copied after an explicit decision; keep the copyright line and the MIT notice |
| Apache-2.0 | LA-Light, iLLM-TSC, SimSkill | Learn the idea. Code may be copied after an explicit decision; keep the license and any NOTICE file, and state the changes |
| GPL-3.0 | RESCO | Learn the idea only. Do not copy code into ASTRA, because the copy would put ASTRA under GPL terms. A separate, unmodified RESCO install may be called as an external tool |
| No license | LibSignal, LLMLight (LLMTSCS), Open-TI | Learn the idea only. Without a license, no right to reuse the code is granted |
| Repository not available | SUMO-MCP, ChatSUMO, ChatSUMO-Agent, TrafficSimAgent, SimTIO | Learn the idea from the paper only |

## Projects

### LLM agents that build or run SUMO scenarios

| Project | What it does | Repository (stars, license, last push) |
|---|---|---|
| AgentSUMO | Turns a user intent into an executable SUMO plan. Asks clarifying questions before it runs. Provides an MCP server ([arXiv 2511.06804](https://arxiv.org/abs/2511.06804)) | [urban-ai-institute/kra35-prismx-agentsumo](https://github.com/urban-ai-institute/kra35-prismx-agentsumo) (11, MIT, 2026-06) |
| SimSkill | A SUMO agent inside Claude Code with curriculum, action, and critic agents; a library of about 150 procedural skills and 153 knowledge pages; two frozen sets of 40 tasks; results checked by an independent rerun ([arXiv 2609.03753](https://arxiv.org/abs/2609.03753)) | [qiliuchn/SimSkill-V1](https://github.com/qiliuchn/SimSkill-V1) (1, Apache-2.0, 2026-10) |
| Open-TI | One central agent sends tasks to separate executors (signal control, demand optimization). Supports SUMO, CityFlow, and VISSIM ([arXiv 2401.00211](https://arxiv.org/abs/2401.00211)) | [DaRL-LibSignal/OpenTI](https://github.com/DaRL-LibSignal/OpenTI) (25, no license, 2025-07) |
| SUMO-MCP | Wraps SUMO utilities as MCP tools, so that one request can run and compare signal-control methods ([arXiv 2506.03548](https://arxiv.org/abs/2506.03548)) | The repository named in the paper, `ycycycl/SUMO-MCP`, returns 404 |
| ChatSUMO | Generates abstract and real-world SUMO scenarios from text ([arXiv 2409.09040](https://arxiv.org/abs/2409.09040)) | Not found |
| ChatSUMO-Agent | Closed-loop agent for network extraction, scenario setup, and runs ([TRID record](https://trid.trb.org/View/2704119)) | Not found |
| TrafficSimAgent | Hierarchical LLM agent for experiment design and decision optimization in SUMO and MATSim ([arXiv 2512.20996](https://arxiv.org/abs/2512.20996)) | Not found |
| SimTIO | Multi-agent LLM framework; each intervention candidate is scored by a full SUMO run ([arXiv 2609.05740](https://arxiv.org/abs/2609.05740)) | Not found |

### LLM-based signal control

| Project | What it does | Repository (stars, license, last push) |
|---|---|---|
| LLMLight (KDD '25) | An LLM selects signal phases with stated reasons; LightGPT is a fine-tuned model for this task. Evaluated on ten traffic datasets and with human experts ([arXiv 2312.16044](https://arxiv.org/abs/2312.16044)) | [usail-hkust/LLMTSCS](https://github.com/usail-hkust/LLMTSCS) (294, no license, 2025-08) |
| LA-Light | An LLM selects perception and decision tools and explains its decision. Test cases include a failed sensor and a blocked road ([arXiv 2403.08337](https://arxiv.org/abs/2403.08337)) | [Traffic-Alpha/LLM-Assisted-Light](https://github.com/Traffic-Alpha/LLM-Assisted-Light) (111, Apache-2.0, 2026-10) |
| iLLM-TSC | An RL policy proposes an action; an LLM reviews and can correct it ([arXiv 2407.06025](https://arxiv.org/abs/2407.06025)) | [Traffic-Alpha/iLLM-TSC](https://github.com/Traffic-Alpha/iLLM-TSC) (82, Apache-2.0, 2026-09) |
| Traffic-R1, ProcessLight, CoLLMLight | LLM signal controllers trained with RL or process supervision ([Traffic-R1](https://arxiv.org/abs/2508.02344), [ProcessLight](https://arxiv.org/abs/2609.22746), [CoLLMLight](https://arxiv.org/abs/2503.11739)) | Not checked |

### RL frameworks and benchmarks for SUMO

| Project | What it does | Repository (stars, license, last push) |
|---|---|---|
| sumo-rl | Gymnasium/PettingZoo environment for signal control; replaceable observation and reward | [LucasAlegre/sumo-rl](https://github.com/LucasAlegre/sumo-rl) (1104, MIT, 2026-03) |
| RESCO | Benchmark scenarios (Ingolstadt, Cologne) with fixed baselines and RL learners | [Pi-Star-Lab/RESCO](https://github.com/Pi-Star-Lab/RESCO) (190, GPL-3.0, 2025-12) |
| LibSignal | Modular signal-control library for SUMO and CityFlow with baselines and datasets | [DaRL-LibSignal/LibSignal](https://github.com/DaRL-LibSignal/LibSignal) (205, no license, 2025-09) |
| Flow | RL framework for traffic control and mixed autonomy, mainly on SUMO; little activity since 2024 | [flow-project/flow](https://github.com/flow-project/flow) (1194, MIT, 2024-07) |

## Ideas for ASTRA

All items below follow the reuse rule: ASTRA writes its own implementation.

| Idea | From | ASTRA area | Note |
|---|---|---|---|
| Ask clarifying questions before a run when the request is incomplete | AgentSUMO | Router, workflow selection | Fits the existing `blocked` and `review_required` decisions |
| Frozen task sets in difficulty tiers for agent regression | SimSkill | Tests, `benchmarks/` | Use deterministic checks and golden outputs as the verdict, not an LLM judge |
| Independent rerun of a finished result before it is reported | SimSkill | Evidence | ASTRA already binds results to hashes; add a rerun check to the run record |
| Separate executors behind one router | Open-TI | Router, MCP profiles | ASTRA already separates Build, Calibrate, and Simulate |
| One request that runs and compares several control methods | SUMO-MCP | Simulate | Depends on the controller contract in the [controller interface proposal](control-interface-design-2026-11.md) |
| LLM controller with a stated reason for each decision | LLMLight, LA-Light | Simulate | Run it as one more controller through the controller contract and the action guard; store the reason in the decision log |
| Test cases with a failed detector or a blocked road | LA-Light | Scenarios | Add fault variants to the scenario registry to test robustness |
| Proposal-then-review control (RL proposes, a second stage checks) | iLLM-TSC | Simulate | The action guard is a deterministic version of the review stage |
| Gymnasium/PettingZoo interface, observation and reward registries | sumo-rl | Simulate | See the controller interface proposal |
| Fixed scenarios with fixed baselines | RESCO, LibSignal | `benchmarks/` | See the [benchmark landscape snapshot](benchmark-landscape-2026-11.md) |
| Network and scenario generators kept separate from the control environment | Flow | Build, Simulate | ASTRA project records already produce the scenario; the control runtime only reads it |

## Position of ASTRA

The projects above do one of two things:

- an LLM builds or runs a scenario (AgentSUMO, SimSkill, Open-TI);
- an LLM or an RL policy controls signals (LLMLight, LA-Light, sumo-rl).

None of them builds networks from real data with deterministic checks and a provenance record for each stage. ASTRA's work on control methods should keep that evidence chain from network construction to controller results.

## Related Documents

- [Benchmark and evaluation landscape (2026-11)](benchmark-landscape-2026-11.md), proposed in PR #7
- [Controller interface design proposal (2026-11)](control-interface-design-2026-11.md), proposed in PR #8
