# Native proposal domains and the edit funnel

This extends the [native duplicate controls](native-duplicates-20261020-v1.md).
The [summary](generator-domains-20261020-v1.json) binds the captured native
networks, source-only timing observations, and construction outcomes.

## Native condition controls

Commit `7b4abec` adds native controls for the other default proposal generators.
These check detection of declared conditions, not field defects.

| Generator | Native condition | Proposals |
|---|---|---:|
| Isolated road | One detached ordinary road | 1 |
| Isolated road | Connected continuation | 0 |
| Isolated road | Annotated parking access | 0 |
| Micro geometry node | Matching straight corridor, no reference | 1 |
| Micro geometry node | Different road names | 0 |
| Micro geometry node | Reference retains the node | 0 |
| Micro geometry node | Reference omits the node | 1 |

Without a reference, node absence is now unknown instead of false. The proposal
retains that value and no longer claims reference absence in its rationale.
A supplied reference that retains the node still excludes the proposal.

The duplicate generator's protected-road rule excludes controlled roads before
comparison. Claude's signalized example therefore identifies an excluded domain,
not only a positional link-index mismatch. A native signalized control confirms
that no deletion is proposed despite coincident ordinary geometry. Discovery of
signalized duplicates would require a separate proposal contract and signal
behavior evidence. This change does not relax signal protection.

## Paired-model counterevidence and repeated source scans

All 31 original MoST duplicate pairs have different stored lane shapes and
different declared lane lengths. The published model also retains all 31 target
roads under exact endpoint and lane-role registration. These are useful
model-relative negative controls, without field-defect or human-edit labels.

The source-only generator previously recomputed the full controlled-road set for
each road. It now computes the protection set once per parsed source. Isolated,
duplicate, rail, structure, and parking exclusions retain their meaning.

The same frozen MoST input produces zero proposals before and after this change.
The retained observations are 29.78 seconds under `9142a2f` and 2.37 seconds
under clean `7b4abec`. Each version has one observation. Different runtime load
can affect these times. They do not establish a general speedup guarantee.

## A real construction blocker in the native micro case

The generated micro proposal targets all four roads of its small source.
The public materializer rejected an empty keep list before calling SUMO.
The existing native simplification function accepted that same input and built
the intended two-road candidate.

Commit `eb0df3e` removes the unsupported nonempty-keep-list condition. The native
regression now checks that the micro node disappears, two ordinary roads remain,
and source bytes remain unchanged. All later construction and candidate checks
remain active.

The public repeat now passes construction and native load. Final release remains
under review because the changed road identities leave no diagnostic OD pool.
Routing is explicitly unexecuted. This is progress from an artificial early
failure to measured construction. It does not establish route preservation or
authorize promotion.

## Retained shared-controller programs

A separate diagnostic repeats shared-controller signal demotion and compares
the four surviving controlled movements with the source through `_link_program`.
All four retain their program. The source remains unchanged. This is one
synthetic diagnostic case, not evidence about all shared-controller layouts.

## Verification and locations

Related generator/parking checks passed 39 tests. All-target construction
consumers passed 45 tests. The final combined group passed 62 tests. Counts
overlap. The first consumer command named a nonexistent test file and ran no
tests; that log remains separate. Ruff and `git diff --check` passed.

Both complete Windows suites for `7b4abec` passed 3,660 tests and skipped 24.
See the [PR run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39341813027)
and [push run](https://github.com/Tarard/ASTRA-SUMO/actions/runs/39341807881).
These runs precede the all-target guard repair and added signal exclusion test.
Those later changes have the focused checks above; their complete CI is pending.

- Native controls: `tests/test_native_proposal_generators.py` and `tests/test_duplicate_road_proposals.py`.
- Captured native inputs and references: `outputs/native_generator_controls_20261020_v1/`.
- Signal exclusion capture: `outputs/signal_duplicate_exclusion_20261020_v1/`.
- Source-only runner: `outputs/probe_source_only_proposals_20261020_v4.py`.
- Input identities, implementation hashes, timing, and measurements: `outputs/source_only_proposals_20261020_v4/inventory.json`.
- Original-pair geometry analysis: `outputs/source_only_proposals_20261020_v1/geometry-counterevidence.json`.
- Funnel runners: `outputs/probe_micro_edit_funnel_20261020_v1.py` and `outputs/probe_micro_edit_funnel_20261020_v2.py`.
- Raw candidates, commands, validation, and routing decisions: `outputs/micro_edit_funnel_20261020_v1/` and `outputs/micro_edit_funnel_20261020_v2/`.
- Shared-controller runner and measurements: `outputs/probe_shared_signal_program_20261020_v1.py` and `outputs/shared_signal_program_20261020_v1/`.
- Summary runner: `outputs/freeze_generator_domains_20261020_v1.py`.

Field false-alarm rates, useful real-network proposal recall, remaining legacy
whole-network conversion, and merged-road OD correspondence remain open.
