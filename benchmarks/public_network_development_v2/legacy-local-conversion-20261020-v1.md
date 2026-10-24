# Legacy local conversion and restricted-interior access

Commit `6be6e20` extends the shared native converter to legacy road deletion
and geometry merging. The [receipt](legacy-local-conversion-20261020-v1.json)
binds the real backgrounds, planted conditions, raw native outputs and checks.
Sources and implementation stay unchanged during the four runs.

## Reuse the source scope and correspondence

The saved native road list determines the targets, endpoint junctions, and
incident roads. Materialization, evidence replay and topology comparison now
use the same scope derivation. Existing `removedNodeIds` correspondence maps
each merged road to its original directed source path. The converter cannot
silently lose a road without a corresponding merged path.

An isolated deletion retains one extra native input road. This keeps the native
input nonempty. The final merge copies that road from the source outside the
edit scope. Native input, native output and the full merged proposal remain
separate hash-bound records. Full-network fallbacks retain their reasons.

## Real backgrounds with planted conditions

Each source receives a separate, disconnected test condition beyond its bounds:
one isolated road for deletion, or a four-road corridor with one micro node.
Coordinates are generated in the source NET XY frame. Native compilation uses
six decimals, with normalization disabled. The source projection declaration
then identifies that generated coordinate frame without changing coordinates.
No original road is selected for an edit.

| Background | Operation | Converted / source roads | Release result | Original saved lanes | Original loaded movements | Original controlled connections |
|---|---|---:|---|---:|---:|---:|
| hamburg | delete_edge | 2/197 | pass | 1096 | 419 | 206 |
| hamburg | merge_edges | 4/200 | review_required | 1096 | 419 | 206 |
| most | delete_edge | 2/4801 | pass | 25563 | 7568 | 128 |
| most | merge_edges | 4/4804 | review_required | 25563 | 7568 | 128 |

Every run uses one local native rebuild. Independent comparisons retain all
original lane shapes, lengths, widths, speeds and end offsets. Original loaded
movement permissions and controlled programs remain unchanged. These counts
include saved internal lanes. No numerical background change was observed.

Both deletion cases pass all nine technical checks. Both merge cases pass
load, routing, permission and other construction checks. They remain under
topology review because the declared scope intersects an inherited source
finding. There are no new or changed findings. The source-only planted finding
disappears, but current qualification still retains that review. The receipt
keeps the whole-network findings and the unresolved decision.

These are preservation and converter-context checks. They do not estimate
field defects, useful automatic recall, or a general runtime speedup. Routing
still covers sampled requests. It does not establish merged-road OD identity.

## A real permission counterexample

A compiled four-road example allows passenger cars and buses on ordinary
lanes. Its two interior continuations permit only buses. Native geometry
removal absorbs those continuations and leaves passenger access on each merged
road. The old diagnostic returned a successful materialization even though
the interior restriction disappeared.

The new candidate check excludes this geometry merge. Materialization also
checks every absorbed continuation and the lane permissions of every source
segment. It rejects the same forced edit through both local and full-network
paths. The shared alias-normalized connection audit records the access failure.
The raw pre-guard diagnostic remains available. Its exact implementation hash
was not captured, so it has no clean-commit reproduction claim.

A separate native spur-deletion test retains bus-only through access while
deleting another road at the junction. Another test places the source at the
planned native-output path and confirms that the source is not replaced.

## Verification and retained failures

The final focused group passes 70 tests. It covers the native local and full
permission negatives, genuine merges, connected and isolated deletions,
source protection, evidence replay, source scope and navigation. A preceding
consumer run passed 124 tests and failed one old fake-merge assertion.
Counts overlap. Full CI for the new code was running when this record was
written. The preceding complete 3,699-pass runs qualify `09673be` only.

The old fake merge deleted a road instead of merging it. The test now requires
rejection. Earlier failed logs also retain two indentation assertions and one
misplaced test variable. Those test faults do not establish native failures.
Ruff and `git diff --check` pass for the committed change.

- Real-model runner: `outputs/probe_legacy_local_real_models_20261020_v1.py`.
- Metrics, exact source/ledger configuration and raw results: `outputs/legacy_local_real_20261020_v1/`.
- Restricted-interior probe: `outputs/probe_restricted_micro_merge_20261020_v1.py`.
- Counterexample source, native command and result: `outputs/restricted_micro_merge_20261020_v1/`.
- Final test log: `outputs/legacy-local-final-regression-20261020-v2.log`.
- Shared implementation: `local_network_conversion.py`, `corridor_simplification.py`, and `corridor_edit_ledger.py` in `plugins/astra-sumo/src/astra_sumo/core/`.

Legacy additions still use whole-network conversion. Removed source findings,
merged-road OD correspondence and complete source-curation correction remain
open. Field precision and independent automatic correction remain unverified.
