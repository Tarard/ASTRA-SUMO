# Academic Audit for Eclipse SUMO

**Agent Skill for Traffic Signal Control Experiments**

Reusable Codex/Claude skills and checklists for auditing SUMO/TraCI traffic signal control experiments before results are reported.

```text
What it is:     A reusable agent skill for auditing SUMO/TraCI signal-control workflows.
Who it is for:  Researchers using Eclipse SUMO for fixed-time, actuated, max-pressure, data-informed, or MPC-style controllers.
What it catches: Broken routes, unsafe TLS phases, unpaired baselines, overwritten outputs, invalid metrics, and unreproducible batches.
```

This repository is built as a practical research tool, not as a paper wrapper. Copy the skill into Codex or Claude, point it at a SUMO experiment, and use the audit report to decide what can be claimed.

## Quick Start

For Codex, copy the skill folders to a repository-scoped skill directory:

```text
.agents/skills/
  academic-audit-for-eclipse-sumo/
  debugging-audit-for-eclipse-sumo/
```

For Claude Code, copy the same folders to:

```text
.claude/skills/
  academic-audit-for-eclipse-sumo/
  debugging-audit-for-eclipse-sumo/
```

Then call the skill from the agent:

```text
Use $academic-audit-for-eclipse-sumo to audit this SUMO/TraCI traffic signal control experiment before I report results.
```

For failures:

```text
Use $debugging-audit-for-eclipse-sumo to diagnose why this SUMO/TraCI run is invalid or unreproducible.
```

The skill should ask for missing experiment details first. It should not endorse results just because SUMO ran without crashing.

## Project Status

Current version: instruction-only skill and checklist package.

This repository currently contains Markdown-based agent skills, audit checklists, examples, and release materials. It does not yet provide executable SUMO validators or Python audit scripts.

## Skill Catalog

| Skill | Use it for | Main outputs |
|---|---|---|
| `academic-audit-for-eclipse-sumo` | Planning, reviewing, comparing, or writing claims from SUMO/TraCI signal-control experiments. | Experiment Readiness Record, hard-gate audit, evidence class, claim boundary. |
| `debugging-audit-for-eclipse-sumo` | Debugging route, TraCI, TLS, demand, detector, output, seed, completion, and reproducibility failures. | Fault class, next diagnostic probe, evidence, fix or demotion rule. |

Both skills are plain `SKILL.md` packages with YAML frontmatter and Markdown references. The `agents/openai.yaml` files provide optional Codex UI metadata; the core skill instructions remain readable by Claude-style skill loaders that use `SKILL.md`.

## What It Audits

- TLS phase and movement-green consistency.
- Route, config, additional-file, detector, and network consistency.
- Fixed-time, actuated, max-pressure, NEMA, data-informed, and MPC-style controller comparisons.
- Paired seeds, paired demand, paired output intervals, and paired simulation horizons.
- `tripinfo`, `summary`, `edgeData`, TLS switch output, controller logs, warnings, teleports, and unfinished vehicles.
- Completion-aware metric reporting when simulations stop before all vehicles leave the network.
- Baselines, ablations, sensitivity runs, and claim wording.

## Examples

Each example is designed to be copied into an agent prompt:

```text
examples/
  01_fixed_time_audit/
    task.md
    expected_audit_report.md
    bad_case/
    fixed_case/
  02_max_pressure_audit/
    task.md
    expected_audit_report.md
    bad_case/
    fixed_case/
  03_data_informed_signal_control_audit/
    task.md
    expected_audit_report.md
    bad_case/
    fixed_case/
```

Start with `task.md`, then compare the agent's output against `expected_audit_report.md`.

## Common Failure Checklist

See `docs/common-sumo-signal-control-failures.md` for a longer checklist. It covers failures such as:

1. SUMO runs but `tripinfo.xml` is empty.
2. TLS phase index does not match the intended movement.
3. Max-pressure controller compares unpaired seeds.
4. `edgeData` interval differs across controllers.
5. Data-informed weights leak future outcome information.
6. Route files silently change demand across baselines.
7. Yellow and all-red handling differs across methods.
8. Simulation stops before demand completes.
9. Controller runtime or fallback behavior is missing.

## How the Skills Are Designed

The design follows four principles:

**Progressive disclosure.** `SKILL.md` stays compact and routes the agent to focused reference files only when needed.

**Socratic intake before execution.** For underspecified experiments, the skill asks targeted questions and builds an Experiment Readiness Record before code, SUMO runs, or claims.

**Hard gates before claims.** The audit separates what SUMO loaded, what the controller did, what outputs were written, what warnings occurred, and what claim the evidence can support.

**Debugging as a closed loop.** The debugging skill uses observe -> classify -> probe -> compare -> update, so fixes are based on artifacts rather than trial-and-error parameter changes.

## Limitations

This repository provides agent instructions, checklists, and audit procedures. It does not certify that a SUMO experiment is correct.

The skills do not replace manual review, SUMO documentation, controller-specific validation, or independent reproduction. They are intended to reduce common workflow errors and to make claim boundaries explicit.

The audit output should be treated as review support, not as a formal verification result.

## Design Influences

This repository borrows patterns from the broader agent-skill ecosystem:

- Agent Skills convention: self-contained folders with a required `SKILL.md`, YAML frontmatter, and optional resources.
- Public skill repositories such as `anthropics/skills`: repository-level README, skill catalog, examples, and clear disclaimers.
- Skill-authoring patterns from `skill-creator` and `writing-skills`: lean frontmatter, compact `SKILL.md`, one-level references, and validation before release.
- Academic workflow skills such as `academic-paper`, `academic-paper-reviewer`, and `deep-research`: intake records, source hierarchy, evidence boundaries, and claim calibration.
- Debugging and control-loop skills such as `systematic-debugging` and `control-theory`: explicit target, observed state, deviation, next probe, feedback, and residual risk.

No external skill is required at runtime. These influences shaped the structure; the usable package is contained in this repository.

## Repository Layout

```text
README.md
LICENSE-DOCS
skills/
  academic-audit-for-eclipse-sumo/
  debugging-audit-for-eclipse-sumo/
docs/
  common-sumo-signal-control-failures.md
  release/
examples/
  01_fixed_time_audit/
  02_max_pressure_audit/
  03_data_informed_signal_control_audit/
```

Use `docs/release/public-repo-manifest.md` when preparing a clean public repository from a larger local worktree.

## GitHub Topics

Suggested topics:

```text
sumo eclipse-sumo traci traffic-simulation traffic-signal-control transportation intelligent-transportation-systems max-pressure-control reproducibility research-software agent-skills codex claude
```

## Outreach Materials

Draft release materials are included under `docs/release/`:

- `github-topics.txt`
- `mailing-list-announcement.md`
- `linkedin-posts.md`
- `conference-positioning.md`
- `public-repo-manifest.md`

The recommended positioning is "reproducibility and error auditing for SUMO/TraCI signal-control experiments", not "a new academic result".

## Trademark Notice

Eclipse SUMO is a trademark of the Eclipse Foundation. This project is an independent academic resource and is not affiliated with, endorsed by, sponsored by, or maintained by the Eclipse Foundation, the Eclipse SUMO project, or DLR.

This project is intended for academic and research workflow support for experiments using Eclipse SUMO. It does not use official Eclipse or Eclipse SUMO logos.

## No Warranty or Certification

This resource is provided as-is, without warranty of correctness, completeness, or fitness for a particular purpose. Passing an audit checklist does not imply that a SUMO experiment is valid, publishable, or officially certified.

## Citing Eclipse SUMO

This project supports experiments using Eclipse SUMO. If your research uses SUMO, cite the official SUMO reference recommended by the SUMO project:

- Pablo Alvarez Lopez, Michael Behrisch, Laura Bieker-Walz, Jakob Erdmann, Yun-Pang Floetteroed, Robert Hilbrich, Leonhard Luecken, Johannes Rummel, Peter Wagner, and Evamarie Wiessner. "Microscopic Traffic Simulation using SUMO." IEEE Intelligent Transportation Systems Conference, 2018. DOI: `10.1109/ITSC.2018.8569938`.

The Eclipse SUMO about page also notes that release-specific DOIs are available for SUMO releases starting with version 1.2.0.

## License

The skill files, documentation, checklists, and protocol text in this repository are licensed under Creative Commons Attribution 4.0 International (`CC BY 4.0`). See `LICENSE-DOCS`.

If future releases add Python audit scripts or other source code, add a separate code license such as MIT and document the split clearly, for example `LICENSE-CODE` for source code and `LICENSE-DOCS` for text.

## References and Related Resources

These links are provided for context and do not imply endorsement of this repository.

- Eclipse SUMO documentation and licensing pages: [About Eclipse SUMO](https://eclipse.dev/sumo/about/), [SUMO FAQ](https://sumo.dlr.de/docs/FAQ.html), and [SUMO Downloads and Licensing Note](https://sumo.dlr.de/docs/Downloads.html).
- Eclipse Foundation trademark usage policy: [Eclipse Foundation Trademark Usage Policy](https://www.eclipse.org/legal/logo-guidelines/).
- Public agent-skill examples and conventions: [Anthropic public skills repository](https://github.com/anthropics/skills).
