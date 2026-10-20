# Legacy edit scope through the full candidate checks

Commit `d4a4c8e` lets legacy deletion and geometry-merge operations use the
existing source-relative topology comparison. The [receipt](legacy-funnel-20261020-v1.json)
binds native controls, a planted-condition run on a real model, and CI failures.

## Scope from existing native inputs

The materializer already saves hash-bound road-ID lists. The gate now derives
scope from those lists and the recorded native command. It does not require
a new JSON plan or trust unbound report labels. A deletion uses the listed
roads and their end junctions. A geometry merge uses roads outside the keep
list, their end junctions, and incident roads.

The scope is recomputed against the exact source. Modified plans and ambiguous
compound commands do not grant scoped qualification. A numeric one-road list
is treated as a native input rather than accidentally parsed as JSON.
Both native controls start with generator proposals, then explicitly accept
them in the synthetic test and run the public verification command.

An isolated deletion passes while an unrelated dense cluster remains visible
in the whole-network report. The merge control still requires review for its
local inherited finding. Its unrelated finding is inherited, not a new defect.

## Planted condition on the MoST source component

The background is the 4,800-road original component from the
[source-curation import](source-curation-calibration-20261020-v1.md).
Its default generators produce zero proposals. The run adds a known isolated
component compiled by the pinned netconvert. Its Cartesian coordinates are
generated in the source XY frame, which is explicitly assigned before merging.
The shared local merge retains the original background. SUMO loads the result.

The augmented model produces one proposal for the planted component and no
other new proposal. That proposal is explicitly accepted for this controlled
test. After deletion, all candidate technical checks pass. The whole-network
report retains 171 inherited topology findings and zero findings in scope.
No real road was selected for deletion by this test.

Independent checks against the original background confirm:

- the same 4,800 ordinary roads;
- identical stored geometry, lengths, widths, and speeds for every lane,
  including internal lanes;
- the same loaded vehicle-class access on 7,568 ordinary movements;
- unchanged programs on all 90 controlled ordinary movements;
- 20 requested routeability vehicles with a passing native routeability report.

This demonstrates one complete technical workflow. It does not estimate general
detection recall or field false-alarm rates. The remaining whole-network review
does not disappear because this local edit passes.

## CI outcome and native-worker diagnostics

The complete PR suite for `ce71e87` passed 3,674 tests and skipped 24. The push
suite for the same code passed 3,671 tests and skipped 24, with three native-worker
timeouts. Its 8-second external guard expired before the runner's 10-second
command deadline. Worker logs and native error files were empty. Those records
cannot identify whether startup, execution, or cleanup consumed the time.

Test commit `f3c3f23` gives the external guard 30 seconds for worker startup,
the existing command deadline, and bounded Windows cleanup. It records import
completion, copies each native input into the test directory, and places worker
temporary output there for failure capture. The negative fixture also selects
a road whose lanes actually forbid bicycle departure.

The production runner is unchanged. Native failure status, nonzero return code,
and both expected error messages remain required. The separate deterministic
fatal-response test keeps its 2.5-second limit. Local runner tests pass all
35 cases, including 20 native/launcher error exits. Their largest command time
is 1.282 seconds and largest worker import time is 0.453 seconds. These local
times do not establish the cause of the earlier CI delay. Complete CI for the
updated harness remains pending.

## Verification and locations

The legacy consumers passed 55 tests, and the final two native scope controls
passed again. Counts overlap. Ruff and `git diff --check` passed.

- Scope implementation: `plugins/astra-sumo/src/astra_sumo/core/corridor_edit_ledger.py`.
- Scope regressions: `tests/test_legacy_edit_topology_scope.py`.
- Real-model runner: `outputs/probe_real_model_legacy_funnel_20261020_v1.py`.
- Source identities, planted fragment, native build, proposal counts and command: `outputs/real_model_legacy_funnel_20261020_v1/declaration.json` and adjacent native input files.
- Raw materialization, routeability, topology and final reports: `outputs/real_model_legacy_funnel_20261020_v1/verification/`.
- Original-model comparison: `outputs/check_real_model_restoration_20261020_v2.py` and `restored-model-checks-v2.json` in the run directory.
- Failed CI inputs and JUnit: `outputs/ci-ce71e87-push-artifacts-20261020/`.
- Updated native success captures: `outputs/windows_native_watchdog_capture_20261020_v1/`.
- Capture runner: `outputs/capture_native_watchdog_20261020_v1.py`.
- Summary runner: `outputs/freeze_legacy_funnel_20261020_v1.py`.

General useful proposal recall, field truth, remaining legacy whole-network
conversion, and merged-road OD correspondence remain open.
