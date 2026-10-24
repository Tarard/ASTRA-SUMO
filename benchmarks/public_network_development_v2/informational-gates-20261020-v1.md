# Diagnostics for candidates that require review

Commit `0b1bb6a` runs load, route, and topology diagnostics for validated
materializations that require review. Their measurements do not approve the
candidate. A negative integration control retains a blocked semantic result
while collecting the later reports.

## Real Hamburg repeat

The public release command rematerialized the frozen passenger-selected lane
addition with clean committed code. Source inputs and implementation stayed
unchanged. The [summary](informational-gates-20261020-v1.json) binds the reports,
runner, source, candidate, and implementation declaration by hash.

| Check | Result |
|---|---|
| Artifact identity, XML, protected semantics, modal connectivity | Pass |
| SUMO load | Pass |
| Source-sampled route requests | 20 requested, 20 arrived |
| Materialization service qualification | Review required |
| Topology | Review required |
| Overall | Review required |

The new lane still has zero incoming and zero outgoing compatible ordinary
movements. Lane-change use remains unmeasured. Route completion does not show
that vehicles used this lane, or that the lane adds capacity.

## Two false alarms

The first diagnostic exposed three missing and three extra controlled
connections. These were retained movements with declared lane index changes.
Comparison now applies only a complete source-bound mapping from the hashed
materialization plan. Changed signal bindings still fail. Lane deletion does
not qualify for this mapping.

The bicycle comparison also counted generated internal path pieces as separate
movements. Reconstructing two pieces as one reduced that count without removing
an ordinary movement. Vehicle-mode comparison now uses the shared loaded
ordinary movement permissions. A negative internal-lane permission control
still fails. Pedestrian comparison retains explicit connections and does not
establish all implicit walking routes.

The earlier probe results remain in separate directories. An intermediate test
failed because its fake compiler omitted compiled lanes. The fixture now emits
those lanes with inherited permissions. The production reader was not relaxed.

## Verification and locations

The targeted suite passed 60 tests. The final service and negative controls
passed 12 tests. These counts overlap. Ruff and `git diff --check` passed.
The previous complete Windows CI for `b960e86` passed 3,623 tests and skipped 24.
Complete CI for `0b1bb6a` was running when this record was written.

- Runner: `outputs/probe_informational_gates_20261020_v5.py`.
- Configuration and input identities: `outputs/informational_gates_20261020_v5/declaration.json`.
- Measurements and report index: `outputs/informational_gates_20261020_v5/results.json`.
- Raw candidate, native logs, routes, trips, and topology: `outputs/informational_gates_20261020_v5/release/`.
- Summary runner: `outputs/freeze_informational_gates_20261020_v1.py`.
- Regressions: `tests/test_gate_lane_correspondence.py` and `tests/test_new_lane_service.py`.

General automatic-cleaning precision, remaining full-network conversion, and
operational parking remain open. This repeat grants no publication approval.
