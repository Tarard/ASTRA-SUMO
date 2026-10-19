# Legacy repair permission preservation

Implementation: `f77e8eea052a4b45bd62e4fa06afc5c6654b2f71`. Native programs: SUMO 1.27.1.

## Confirmed defects and repair

Two native regression cases failed before the change:

- An explicit connection repair removed one branch but opened an untouched bus-only turn to passenger vehicles.
- The reviewed road-geometry conversion also opened that bus-only turn.

Both entry points now prepare separate conversion inputs with the existing loaded-permission calculation.
Explicit connection patches apply after preparation. The source files remain unchanged.
The road correction also prepares a separate reload input.
Reports retain the prepared input paths and hashes.

The existing exact-patch check compares loaded permissions on retained movements.
Road preservation and reload checks also compare loaded access.
No public command or new release gate was added.

## Verification

- 90 related tests passed, including connection repair, road geometry, the Hamburg workflow, and repository navigation.
- Two native repair cases preserve the bus-only turn and the source bytes.
- Two deliberately damaged native candidates widen internal-lane permissions. Existing checks reject both candidates.
- Ruff and whitespace checks passed.
- The earlier CI test follow-up `d16d4b8` completed successfully: [3449 passed, 24 skipped](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39226140078).

The road test supplies a fixed local plan to isolate the conversion path.
Existing tests separately check the historical source-matching profiles. Those profiles and tolerances did not change.
This run does not establish that either historical correction applies to the current Hamburg crop.

Raw logs, JUnit output, failed inputs, successful candidates, and deliberately damaged candidates remain under
`outputs/legacy_permission_maintenance_20261019_v1/`. The adjacent JSON records their hashes.

## Limits

This maintenance pass covers these two legacy repair entry points.
Other native re-import paths still need individual inspection. Raw diagnostic reloads retain their existing purpose.
No full Hamburg rebuild or traffic-performance experiment ran in this pass.
The 24 skipped full-suite tests still require unavailable exact artifacts.
