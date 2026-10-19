# Geometry edits preserve loaded turn access

Implementation: `2de375dab8d39e3cbb5135e6d71088139a6d2541`. Native programs: SUMO 1.27.1.
CI test follow-up: `d16d4b8feb0ee57fa2f4e08ce7c143ae4348418f`.
This resolves the conversion defect recorded in [the earlier permission review](permission-review-20261019-v1.md).

## Change

Conversion input copies reconcile connection directives with loaded access through incoming, internal, and outgoing lanes.
The calculation follows complete internal chains and network-version permission rules.
Explicit connection patches apply after this preparation. Original sources remain unchanged.
Local conversion and full-network fallback both retain their prepared inputs and hashes.
Historical conversion records retain their earlier reconstruction rules.

The existing connection-preservation check now compares loaded access for surviving movements.
It still excludes explicitly changed lanes and declared lane-count changes.
Explore mode still reports measurements. This change adds no promotion gate.

## Verification

- Permission and conversion tests: 71 passed. These include 13 new regression cases.
- Calling code, edit evidence, Hamburg approaches, and repository navigation: 144 passed.
- The six undamaged native width-edit cases retain access after another native re-import.
- Two damaged candidates deliberately widen internal-lane access. The preservation check detects both changes.
- Complete internal chains cover aliases, legacy exclusions, and an empty permission intersection.
- Shared conversion tests cover both local input and full-network fallback.
- Ruff and whitespace checks passed.
- [Full Windows CI for the production fix](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39224598993): 3449 passed, 24 skipped.

The frozen source has SHA-256 `4032e5ca5ff09f0551d5f56fb7ced33101e72660faec3af815eace8bee137747`.
Before and after the fixed width edit, SUMO excludes passenger vehicles and permits buses.
The requested width change and existing technical checks pass.
The resulting candidate has SHA-256 `b980f5114504eda94b3fb455ae3a32ad638abcd94f56daae1160b5ae25995a3b`.
The source remains unchanged.

Raw results, runners, and retained initial failures are in `outputs/turn_permission_fix_20261019_v1/`.
The adjacent JSON records their paths and hashes.

## Earlier CI failure

[Run 39220361116](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39220361116) had one failed test, 3435 passed tests, and 24 skips.
`test_official_bus_lane_permissions_and_real_vehicle_legality[True]` exceeded 30 seconds during its illegal-vehicle subprocess.
The legal-vehicle subprocess and its trip assertions had already passed.
[The other full run at the same commit](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39220366044) passed all 3436 tests, with 24 skips.

The original two-case test passed locally through the wheel entry point in 9.95 seconds.
Separate native and wheel-entry probes rejected the illegal stop with exit code 1 in 0.98 and 1.38 seconds.
Both reported that the vehicle could not stop on the bus-only lane.
The earlier timeout did not reproduce. Its cause remains unconfirmed.

The traceback raises the timeout while waiting for the captured stdout reader. Its process representation shows return code 1.
The wheel entry point starts another process that calls the native program.
A controlled Windows case reproduced a completed parent process whose captured output remained open in a descendant.
File output returned exit code 1 in 0.18 seconds without waiting for the descendant's output handle to close.
This reproduces a failure mechanism. It does not identify the original CI handle owner.

The final test follow-up uses `sumolib.checkBinary` and writes stdout and stderr to separate per-run log files.
It avoids the wheel launcher and captured output pipes under the locked CI environment.
The test requires an actual nonzero exit and the specific illegal-stop error.
A timeout cannot count as successful rejection. The 30-second limit remains unchanged.
Both test cases passed with the locked SUMO 1.27.1 distribution in 7.21 seconds.
An earlier probe selected the system SUMO 1.26.0 installation. Its separate log remains available but does not establish locked-runtime verification.
The full-suite result above covers the production fix before this test-only follow-up.

## Limits

The checks establish model access preservation for these cases. They do not establish field access rules or traffic improvement.
The earlier CI failure remains part of the record. Local success does not prove that it cannot recur.

An independent Claude review confirmed the dormant-case fix and idempotent input preparation.
It identified other native re-import paths that do not yet use this preparation.
This result covers typed scoped edits and callers of the shared scoped conversion function.
It does not establish protection for every native re-import in the repository.
Original directives remain in the immutable source. Prepared directives remain in the hashed conversion input.
A per-connection reconciliation summary remains a possible follow-up. No additional release gate was added.
The full review and screenshot remain in the local result directory.
