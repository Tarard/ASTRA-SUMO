# Torii Release Review

Use this reference before publishing a Torii repository or plugin release. It reviews public exposure and packaging; selecting it does not authorize publication.

## Current Bundle

Torii is one plugin with four topic skills:

- `torii-build`
- `torii-calibrate`
- `torii-simulate`
- `torii-report`

The plugin manifest points to `./skills/` and the shared MCP server. Do not reintroduce the former `simulation-helper-skill-for-eclipse-sumo` bundle or standalone duplicate skill trees.

## Release Checks

- The repository license and plugin metadata use the MIT License.
- No local machine paths, private datasets, unpublished collaborator material, raw private logs, or copied third-party code are exposed.
- Every skill has `SKILL.md`, `agents/openai.yaml`, and only the references it owns or explicit handoff stubs.
- Every reference named by a skill or workflow-catalog entry exists.
- README, installation docs, MCP catalog, workflow-selection docs, and examples describe the current product surface.
- Public claims stay inside the evidence available in the repository.
- Generated runs and temporary outputs are not presented as maintained product documentation.
- Trademark wording does not imply affiliation with or endorsement by Eclipse SUMO, the Eclipse Foundation, or DLR.
- Zenodo, citation metadata, plugin metadata, and release notes remain consistent with the current repository license and product name.

## Skill Boundaries

- Network and infrastructure construction belongs to `$torii-build`.
- Demand reconstruction and observation calibration belong to `$torii-calibrate`.
- Experiment planning, execution, debugging, and controller diagnosis belong to `$torii-simulate`.
- Completed-result interpretation, traffic reports, reusable lesson capture, and release evidence belong to `$torii-report`.

Do not publish a second copy of the same skill under another repository path.

## Evidence Gate

Before a release, verify the relevant tests and public navigation. A green CI run supports packaging consistency but does not certify traffic-model correctness, field validity, controller superiority, or scientific reproducibility beyond the tested contracts.

End with:

```text
release_target:
public_surface_checked:
license_status:
skill_bundle_status:
private_content_status:
claim_boundary_status:
remaining_blockers:
```
