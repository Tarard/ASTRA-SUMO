# Merge realization and complete native-test execution

This follows the [legacy local conversion](legacy-local-conversion-20261020-v1.md).
Independent review of `6be6e20` found a merge that returned success without
merging any road. Commit `1ad8919` repairs that result and the stale CI test.
The [receipt](legacy-merge-realization-20261020-v1.json) binds the new controls,
real-background repeats, failed CI logs and current CI snapshot.

## Require the requested merge

On the saved Hamburg network, targets `83075269#1` and `83075269#2` meet at
a node with a third road. Native conversion succeeds but leaves both roads.
Re-rounded junctions had made the output bytes different, so an identity check
alone could not detect this missing effect.

Every declared merge target must now participate in a recovered composite
source path, including the surviving road. Both local and full-network paths
reject the Hamburg request when no merge occurs. Failed local subsets keep
their raw output with an explicit failure role. They are not registered as
complete native proposals. Saved merge commands must enable geometry removal
exactly once. A later false setting cannot qualify as an enabled merge.

## A genuine real-road positive control

The manually selected pair `83075269#0` and `83075269#1` does merge on the
current combined Hamburg source. Local and controlled full imports both pass
materialization, independent evidence validation and native load. The merged
road has identical endpoints, lane shapes, lengths, widths, speeds and permission
attributes in both results. The original source file remains unchanged.

This control uses original road IDs and geometry. It is a simulation copy,
not an approved field cleanup. It does not establish generator precision or
complete release qualification.

## Repeat the full real backgrounds

| Background | Operation | Converted / source roads | Release result | Original-source proposals |
|---|---|---:|---|---:|
| hamburg | delete_edge | 2/197 | pass | 0 |
| hamburg | merge_edges | 4/200 | review_required | 0 |
| most | delete_edge | 2/4801 | pass | 0 |
| most | merge_edges | 4/4804 | review_required | 0 |

The planted controls remain separate from original-road cleanup. All original
lane numeric states, loaded movement permissions and controlled programs stay
unchanged. Merge review remains visible because its scope touches a removed
source finding. Routeability covers saved requests, not every possible OD pair.
Candidate counts do not provide a field recall denominator.

## CI correction and native execution

Both full suites for `6be6e20` had 3,707 passes, one failure and 24 skips.
The same exterior-protection test failed in both runs. Its fault injector tried
to modify a remote road inside the local native result, where that road is
correctly absent. The test now covers two cases: local exclusion and restoration
of deliberately shifted geometry after a controlled full-network conversion.

The new focused group passes 82 cases. The Windows job now checks JUnit after
pytest. Missing or skipped critical native cases fail the job, as do native
failures or errors. The check covers the isolated and connected deletion,
micro-merge, source-protection, fatal-exit, XYZ-shape and Hamburg merge controls.
Unrelated expected skips remain separate. The receipt records current full CI
without treating earlier green commits as proof for new code.

- Current real-background runner and raw results: `outputs/probe_legacy_local_real_models_20261020_v2.py` and `outputs/legacy_local_real_20261020_v2/`.
- Actual Hamburg pair runner and configuration: `outputs/probe_real_hamburg_merge_pair_20261020_v1.py` and `outputs/real_hamburg_merge_pair_20261020_v1/declaration.json`.
- Pair metrics, evidence checks and raw networks: `outputs/real_hamburg_merge_pair_20261020_v1/`.
- Final local test log: `outputs/legacy-merge-realization-final-20261020-v1.log`.
- CI execution policy: `.github/scripts/check_windows_native_junit.py`.
- Independent review: `outputs/claude-review-6be6e20-20261020.txt`.

Legacy additive conversion, merged-road OD correspondence, removed-finding
qualification and complete source-curation correction remain open. Field
precision and independent automatic shape correction remain unverified.
