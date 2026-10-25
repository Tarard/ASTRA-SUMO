# Keep controller adoption inside the edit scope

The [walking-area repair](additive-walking-context-20261020-v1.md) expands native
computation to adjacent junctions. That expansion must not authorize changes
to an outside part of a shared signal controller.

Commit `cc54a92` retains both boundaries. A controller spanning the final edit
boundary still requires the original full-network fallback. A controller
spanning the larger native-context boundary also requires a fallback. A larger
input therefore cannot bypass the original controller-adoption check.

The regression uses a shared program at two junctions. Only one junction is
mutable, while both fit in the expanded native context. Extraction returns
the explicit `controller_spans_edit_boundary` fallback. Ninety-nine additive,
local-conversion and ledger checks pass. Counts overlap with earlier groups.
The test log is `outputs/additive-controller-context-regression-20261020-v1.log`.

Earlier code `ab5bfb4` passed complete CI but still contained the real Hamburg
walking-area regression. That result does not qualify later repairs. Full CI
for the current code is recorded separately when complete. The semantic lane
layout checks and the bus-only continuation controls also remain active.

Standalone simplification still uses global conversion. Guarded fallbacks,
merged-road OD correspondence, source-scope topology review and complete source
curation remain open. The real-road additions are construction controls without
field approval or an automatic-cleaning precision claim.
