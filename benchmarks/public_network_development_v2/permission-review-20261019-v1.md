# Loaded permission review: 2026-10-19

Implementation: `cd7943e1c223e4705d8b902ddd6b94e2f999bd5b`.
Native programs: SUMO 1.27.1. All results below completed before the user requested a pause.

## Completed checks

- The review now uses vehicle-class aliases, network-version rules, and native allow/disallow precedence.
- It measures loaded movement access from lanes. Connection directives can affect a later conversion.
- Focused tests: 79 passed, including nine native differential cases.
- Full Windows CI: 3436 passed, 24 skipped. [Successful run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39220366044).
- A duplicate full-suite job failed. Its log remains available. [Failed run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39220361116). The cause remains unassessed.
- Six small native observations agree with the corrected review.
- MoST and InTAS controls made implicit legacy lane exclusions explicit without changing the network version or geometry.
- SUMO loaded all 38,961 changed lane permission sets without a difference between each source and its equivalent candidate.
- For each of `subway` and `cable_car`, MoST review findings fell from 9,451 to zero. InTAS findings fell from 22,102 to zero.
- These class-specific counts overlap. They are not independent field labels or a field false-alarm estimate.
- The Hamburg passenger comparison retained the same 248 findings.
- Source files remained unchanged. Existing InTAS load warnings remain in the logs.

Raw results, scripts, native snapshots, failed checks, and CI logs remain under `outputs/permission_review_20261019_v1/`.
The adjacent JSON records their paths and hashes. Generated model files remain local.

## Confirmed unresolved conversion defect

An independent review identified a difference between loaded permissions and conversion directives.
The completed `dormant-control.py` reproduction confirmed it through the actual scoped-edit path:

1. A source internal lane permits buses and excludes passenger vehicles.
2. The compiled connection has no explicit permission directive. SUMO still excludes passenger vehicles.
3. A native re-import admits passenger vehicles.
4. An ASTRA `set_lane_width` candidate also admits passenger vehicles.
5. Existing release checks report success despite this unintended change.

This defect remains unfixed. The successful test suite does not establish permission preservation for this case.
The reproduction, source, candidate, commands, and loaded observations remain in `dormant-control/report.json`.
No new construction change or release decision followed this finding.

## Limits

These controls establish agreement with native loaded behavior for the stated cases.
They do not establish field accuracy, cleaning precision, demand validity, or traffic improvement.
The 24 skipped tests still require unavailable exact artifacts.
Work paused at the user's request. No new experiment or review started during cleanup.
