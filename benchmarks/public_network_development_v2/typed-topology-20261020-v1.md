# Typed edits, source topology, and scoped signal conversion

This follows the [informational diagnostics](informational-gates-20261020-v1.md).
Claude's read-only review confirmed the real Hamburg diagnostic path and found
two remaining problems. Commits `2c3cec7` and `9e4f872` address them.

## Source-relative topology

Typed scoped edits now use the existing source-relative topology comparison.
The checker validates the materialization plan's manifest hash and source hash.
It recomputes the operation's scope before using its node identities.
Changing a recorded scope cannot exclude a local finding.

Unchanged source findings outside the scope do not fail the local edit.
Findings inside the scope still require review. The whole-network report keeps
the inherited findings and their count. A native synthetic control verifies
both cases with the same source cluster and two different speed-edit locations.

## Real Hamburg repeat

Commit `9e4f872` rematerialized the same four frozen stress edits in explore
and release modes. It also reran the 2349 and 2394 baseline calibration tables.
Inputs and implementation stayed unchanged. The [summary](typed-topology-20261020-v1.json)
binds the exact runner, inputs, reports, and implementation declaration.

| Edit | Explore | Outside fact changes | Load and route checks | Release |
|---|---|---:|---|---|
| Road shape | Measured | 0 | Pass | Review required |
| Lane count | Measured | 0 | Pass | Review required |
| Stop line | Measured | 0 | Pass | Review required |
| Speed | Measured | 0 | Pass | Review required |

Each comparison identifies 13 inherited topology findings, zero new or changed
findings, and one inherited finding intersecting the declared scope. The local
finding is cluster `C001`. It remains unresolved. The lane-count candidate also
retains its separate new-lane service review. These results do not establish
field topology or an added-capacity benefit.

## Diagnostic failure handling

The loaded movement reader rejects unknown internal or endpoint lanes.
Previously that exception escaped the modal gate and prevented report output.
The gate now saves a blocked result with the affected input and cause.
Source and candidate negative cases verify both the comparison and persisted
candidate report. The addition checker reuses the same result instead of
parsing both modal graphs again.

## Scoped signal conversion

Commit `a7e8a95` removes the unconditional full-network conversion for duplicate
signal demotion and static controller relocation. Both use the existing local
conversion helper. Native controls verify retained remote signal programs,
road records, and loaded link semantics. When a shared controller spans the
edit boundary, the existing full-network fallback remains active.

These signal controls are synthetic. The current combined Hamburg source has
no controller shared by multiple ordinary-road junctions. No real signal
demotion or relocation is inferred from that source.

## Verification and locations

The topology controls passed 20 tests. Related consumers passed 116 tests.
The preceding native consumers passed 101 tests, and signal materialization
controls passed 17 tests. Counts overlap. Ruff and `git diff --check` passed.
Complete CI for the latest code was still running when this record was written.

- Real runner: `outputs/probe_typed_topology_20261020_v1.py`.
- Configuration and identities: `outputs/typed_topology_20261020_v1/declaration.json`.
- Metrics and report index: `outputs/typed_topology_20261020_v1/results.json` and `calibration.json` in that directory.
- Raw candidates, measurements, native logs, routes, and topology: `outputs/typed_topology_20261020_v1/`.
- Summary runner: `outputs/freeze_typed_topology_20261020_v1.py`.
- Claude review: `outputs/claude-review-0b1bb6a-20261020.txt`.
- Tests: `tests/test_typed_topology_source.py`, `tests/test_gate_lane_correspondence.py`, and `tests/test_network_edit_operations.py`.

Global automatic-cleaning precision, partial lane-removal correspondence, and
operational parking remain open. This record does not approve publication.
