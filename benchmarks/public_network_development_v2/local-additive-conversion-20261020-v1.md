# Local legacy additions over the complete Hamburg background

Commit `ab5bfb4` extends the shared local converter to `add_edge`,
`add_sidewalk`, `add_ramp` and `add_crossing`. The
[receipt](local-additive-conversion-20261020-v1.json) retains the inputs,
native outputs, source comparisons and blocked release results.

## Bind the actual additive inputs

The saved edge and crossing XML files determine new road identities, existing
endpoints, crossing nodes and incident source roads. A road patch cannot modify
an existing road. Crossing roads must meet their declared junction. The same
source-derived scope serves materialization, evidence replay and topology
comparison. Every referenced plain input must match its manifest hash.

The existing converter already supports new road endpoints. The change reuses
that mechanism instead of adding another native runner. Saved native output
and the full merged proposal remain separate. Existing permission preparation
and reasoned full-network fallbacks remain active.

## Native and full-background results

Four native controls pass construction, independent evidence validation and
native load. Each converts four of five source roads. A bus-only interior
continuation stays bus-only, and the remote road remains unchanged. A forged
operation header cannot change the source scope derived from the plain files.

The complete Hamburg comparison appends the same controlled component beyond
the original bounds in its declared NET XY frame. No original Hamburg road
is selected for an addition or geometry change.

| Operation | Converted / source roads | Construction | Independent load | Release | Unapproved facility changes |
|---|---:|---|---|---|---|
| add_edge | 4/201 | pass | pass | blocked | `{'walkingarea_edge_count': 2}` |
| add_sidewalk | 4/201 | pass | pass | blocked | `{'walkingarea_edge_count': 3}` |
| add_ramp | 4/201 | pass | pass | blocked | `{'walkingarea_edge_count': 2}` |
| add_crossing | 4/201 | pass | pass | blocked | `{'crossing_edge_count': 1, 'walkingarea_edge_count': 2}` |

All original lane attributes and loaded movement permissions remain unchanged.
The original signal programs also match. The controlled bus restriction stays
in place. These checks verify construction and outside-state restoration.
They do not estimate field cleanup precision or independent correction quality.

Release remains blocked because no protected-facility increase was approved.
The existing release gate records the new walking areas or crossing. Separate
native load succeeds for all four candidates. Routing and publication authority
must not be inferred from construction or load alone.

## Verification and failure records

The consumer group passes 153 tests. The strengthened native permission group
passes six tests, including all four addition kinds. Counts overlap. The Windows
JUnit policy now requires those four native cases, bringing the critical group
to 42 cases. Full CI is recorded separately when complete.

The first native fixture lacked required edge types and shared pedestrian access
on every vehicle lane, so it did not define a valid crossing. Revised fixtures
declare types and dedicated sidewalks. One old fake conversion also returned
the full network after receiving a local input. It now uses the actual input.
These failed logs remain available.

The first full-background crossing run failed when a Windows extended path
prefix was passed to SUMO. A repeat with a shorter, normal path passes native
construction and load. This record does not establish general extended-path
support. The original failure is retained.

- Runner and metrics analysis: `outputs/probe_local_additive_hamburg_20261020_v2.py`.
- Exact source, ledger and implementation configuration: `outputs/aah26v2/<operation>/declaration.json` and `ledger.json`.
- Raw native inputs, outputs, full proposals and independent load logs: `outputs/aah26v2/`.
- Earlier failure records: `outputs/local_additive_hamburg_20261020_v1/` and the log hashes in the receipt.
- Native regressions: `tests/test_local_additive_conversion.py`.
- Shared implementation: `local_network_conversion.py` and `corridor_edit_ledger.py` under `plugins/astra-sumo/src/astra_sumo/core/`.

The standalone simplification entry still uses full-network conversion.
Guarded fallback conversion, merged-road OD mapping, source-scope topology
qualification and complete source-curation correction remain open.
