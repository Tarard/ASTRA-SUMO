# Public Release Checklist

Use this before publishing the skill to GitHub.

## Content Safety

- No local machine paths.
- No non-public notes.
- No non-public datasets or corpora.
- No private project acronyms or collaborator-specific method names.
- No named-person provenance from unpublished collaboration notes.
- No copied third-party code.
- No claims that all SUMO source code or all forum posts have been exhaustively reviewed.

## Source Hygiene

- Official Eclipse SUMO URLs are listed for NEMA, traffic lights, TraCI, TLS outputs, Summary, TripInfo, FAQ, and the official repository.
- Public codebase lessons cite repository or project URLs.
- Verbatim quotation is avoided or kept short.
- Source-derived facts are paraphrased and bounded.

## Trademark Hygiene

- The public display title is `Academic Audit for Eclipse SUMO`.
- The repository slug is `academic-audit-for-eclipse-sumo`.
- Skill slugs use the `<product name>-for-eclipse-sumo` pattern, not `sumo-*` or `eclipse-sumo-*`.
- README includes a trademark notice stating that the project is independent and not affiliated with, endorsed by, sponsored by, or maintained by the Eclipse Foundation, the Eclipse SUMO project, or DLR.
- README does not use official, certified, endorsed, sponsored, maintained, or similar wording for this project except inside a negative non-affiliation disclaimer.
- No official Eclipse or Eclipse SUMO logos are included.
- Publication titles should treat Eclipse SUMO as the compatible research platform, not as this project's brand.

## Skill Hygiene

- `SKILL.md` frontmatter has `name` and `description`.
- `agents/openai.yaml` has display name, short description, default prompt, and implicit invocation policy.
- Every reference named in `SKILL.md` exists.
- The skill has no README or extra auxiliary docs inside the skill directory.
- The main package name is `academic-audit-for-eclipse-sumo`.
- The companion debugging package name is `debugging-audit-for-eclipse-sumo`.

## Exposure Hygiene

- README first screen reads like a tool, not a personal note.
- README states what it is, who it is for, and what failures it prevents.
- README includes the repository slug `academic-audit-for-eclipse-sumo`.
- README quick start uses concrete repository-scoped install paths for Codex (`.agents/skills/`) and Claude Code (`.claude/skills/`).
- README includes `## Project Status`, clarifying that the current release is instruction-only and does not include executable SUMO validators or Python audit scripts.
- README includes `## Limitations`, clarifying that the audit does not certify experiment correctness.
- README includes `## No Warranty or Certification`.
- README includes suggested GitHub topics:
  `sumo`, `eclipse-sumo`, `traci`, `traffic-simulation`, `traffic-signal-control`, `transportation`, `intelligent-transportation-systems`, `max-pressure-control`, `reproducibility`, `research-software`, `agent-skills`, `codex`, `claude`.
- `examples/01_fixed_time_audit/` contains `task.md`, `expected_audit_report.md`, `bad_case/`, and `fixed_case/`.
- `examples/02_max_pressure_audit/` contains `task.md`, `expected_audit_report.md`, `bad_case/`, and `fixed_case/`.
- `examples/03_data_informed_signal_control_audit/` contains `task.md`, `expected_audit_report.md`, `bad_case/`, and `fixed_case/`.
- `docs/common-sumo-signal-control-failures.md` exists and emphasizes practical failure modes.
- `docs/release/` contains GitHub topics, mailing-list announcement draft, LinkedIn post drafts, conference/demo positioning, and a public repository manifest.

## Smoke Prompts

Run these after installation:

1. "Help me run an Eclipse SUMO NEMA experiment."
   - Expected: asks intake questions before commands.
2. "Audit this toy TraCI setPhase controller as a NEMA result."
   - Expected: demotes NEMA claim unless NEMA program evidence is supplied.
3. "Can I use an Eclipse SUMO GUI run screenshot as validation?"
   - Expected: requires headless outputs and classifies GUI as inspection.
4. "My run has summary and tripinfo but no TLS output."
   - Expected: identifies missing TLS evidence for traffic-light claims.
5. "Controller A has lower average travel time, but the simulation stopped at 3600s with vehicles unfinished."
   - Expected: requires completion rate, backlog, and fixed-horizon wording before any superiority claim.
6. "My TraCI co-simulation has two clients and sometimes hangs or changes order."
   - Expected: asks for `--num-clients`, `setOrder`, every-client `simulationStep`, logs, and shutdown evidence.
7. "randomTrips.py produced demand and I used --ignore-route-errors to keep running."
   - Expected: treats invalid/discarded routes as a construction or demand-transformation issue, not formal evidence.
8. "My Eclipse SUMO GUI run works, but TraCI says connection closed or could not connect."
   - Expected: asks for exact command, SUMO logs, environment/version check, port/run-protocol evidence, and classifies as diagnostic until startup is proven.
9. "My Eclipse SUMO experiment improved travel time after I changed three settings at once."
   - Expected: invokes the debugging loop, separates root cause from workaround, and rejects formal claims until one-variable evidence exists.

## Publication Gate

Before publishing:

- add `LICENSE-DOCS` with Creative Commons Attribution 4.0 International (`CC BY 4.0`) for skill files, documentation, checklists, and protocol text;
- add `LICENSE-CODE` with a code license such as MIT before publishing Python audit scripts or other source code;
- decide whether to publish the skill alone or as part of a skills collection;
- include `## Trademark Notice` and `## Citing Eclipse SUMO` in the repository README;
- include `## References and Related Resources` without implying endorsement by Eclipse SUMO, Eclipse Foundation, DLR, Anthropic, or any agent-skill project;
- add the suggested GitHub topics in the repository settings after creating the GitHub repository;
- publish only the files listed in `docs/release/public-repo-manifest.md`, not unrelated local project files or private PDFs;
- run a non-public-content scan;
- test installation with Codex skill installer or manual copy;
- note that users should restart Codex after installing.
