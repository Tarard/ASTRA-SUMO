# Direct edits, lane-removal correspondence, and duplicate proposals

This extends the [typed topology repeat](typed-topology-20261020-v1.md).
The [summary](maintenance-20261020-v1.json) binds inputs, reports, and failed runs.

## Direct edits and inherited topology

Commit `d4cf105` treats unchanged in-scope topology findings as informational
for verified direct speed, permission, and stop-line edits. The exact persisted
construction evidence must establish the direct method. New or changed topology
findings still require review. Native junction reconstruction keeps the existing
scope rule, including lane-width changes.

The clean committed-code Hamburg repeat uses the same frozen source and previous
speed and stop-line requests. Both pass every final technical check. Their whole
network reports still retain 13 inherited findings for review. This does not
establish field topology, calibrated demand, or traffic benefit.

A synthetic width control reconstructs the junction and changes its topology
finding. It remains under review. The first test incorrectly expected that
finding to remain unchanged. Its failed result is retained. The corrected test
checks the measured change and verifies that no informational exception applies.

## Declared lane removals

Commit `b73e6db` maps surviving lanes after removal. It lists the deleted lane's
connections separately from missing retained connections. Removed controlled
connections still count as missing and still fail the existing semantic gate.

The Hamburg request removes passenger lane index 2 from road `554713076#0`.
It records two declared removed connections, including one controlled straight
movement. The retained controlled missing count and extra count are both zero.
The candidate remains blocked. Later load and route checks are unexecuted for
this definite semantic failure. The report does not claim route preservation.

## Source-only proposal check against a paired model

The existing generator produced 31 duplicate-road deletion proposals from the
frozen MoST source, without receiving the published model. A later comparison
found all 31 roads retained in that model with registered endpoints and matching
lane roles. This is model-relative counterevidence to the deletion proposals.
It does not label field defects or establish human edit origin.

The duplicate signature compared endpoints, road type, name, permissions and
lane widths, but omitted geometry and incident movements. One pair contained
a 58.24 m walking path and a separate 0.20 m path. Their common endpoints did
not make them duplicate roads.

Commit `0eb5e5f` compares the complete road and lane state, including geometry,
lengths, parameters and driving rules. It also compares incident connection
records after substituting the compared road identity. Different internal path
identities remain different. Exact duplicate positive controls still produce
a proposal. Geometry, length, parameter, priority, and connection differences
prevent one. Exact numeric spellings can leave equivalent roads unmatched.

The same frozen MoST source now produces zero proposals. The initial inventory
was a diagnostic run without a predeclared implementation identity. The final
repeat uses clean committed code and retains all implementation hashes. Inputs
stay unchanged. The result does not measure missed duplicates or general repair
precision, and it does not show that the source network needs no cleaning.

## Shared controller and CI

A native negative control confirms that static relocation cannot move only one
part of a shared controller. Every source link must belong to the declared
source node and receive a complete mapping. This operation is rejected before
conversion. The earlier demotion control covers the full-network fallback.

Both complete Windows suites for `9e4f872` finished with 3,636 passes, 24 skips,
and one stale contour-test failure. That test still expected a reviewed candidate
to skip load and topology. The actual reports retain review, pass native load,
and save topology. Routing remains unexecuted because this extract has no shared
diagnostic endpoints. Commit `1f54dba` updates those assertions. Its local repeat
passes. No production behavior changed for that correction. The first selection
command matched no tests; that log remains separate from the passing run.

Local checks passed 47 lane-removal consumer tests, 29 direct-edit/service tests,
97 duplicate/navigation consumer tests, and the shared-controller negative and
contour CI correction. Counts overlap. Ruff and `git diff --check` passed.
Complete CI for `1f54dba` was running when this record was written.

## Locations

- Direct-edit runner: `outputs/probe_direct_topology_20261020_v1.py`.
- Configuration: `outputs/direct_topology_20261020_v1/declaration.json`.
- Raw candidates, native logs, routes, and reports: `outputs/direct_topology_20261020_v1/`.
- Proposal runners: `outputs/probe_source_only_proposals_20261020_v1.py` and `outputs/probe_source_only_proposals_20261020_v2.py`.
- Paired-model analysis: `outputs/assess_source_only_proposals_20261020_v1.py`.
- Proposal inventories and assessment: `outputs/source_only_proposals_20261020_v1/` and `outputs/source_only_proposals_20261020_v2/`.
- CI failure inputs and JUnit: `outputs/ci-9e4f872-pr-artifacts-20261020/`.
- Summary runner: `outputs/freeze_maintenance_20261020_v1.py`.
- Regressions: `tests/test_duplicate_road_proposals.py`, `tests/test_typed_topology_source.py`, `tests/test_gate_lane_correspondence.py`, and `tests/test_junction_contour_control.py`.

Joint lane correspondence, source-based useful automatic proposals, field
false-alarm rates, and operational parking remain open. The PR remains a draft.
