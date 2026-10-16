# Scoped movement conversion and native-output records

Status: diagnostic construction evidence, 2026-10-16. Code through `5befeeb`.
The [machine record](movement-local-20261016-v1.json) contains hashes, counts, and verification results.

## Construction changes

Movement passes now compile a local native input and retain one immutable connection patch per pass.
Contour trials use the same shared converter and retain the full requested junction inventory.
Their exterior comes from a saved baseline, while construction still uses the original source and accumulated node changes.

Native conversion now writes directly to its retained native-output path.
Only a successful conversion and merge creates the conventional candidate file.
A failed native command retains an incomplete-output record. A failed merge leaves its raw native output separate from the candidate.
Existing output files remain protected.

Context boundary restoration uses this shared converter. Contour selection runs after the immutable merge snapshot.
The boundary validator checks the reconstructed input and merge, and the command's exact native-output path.
Native tests exercise an accepted outline-only selection as well as the preserve policy.
Official-part joins also receive the shared check for occupied junction or controller names.

## Real Hamburg rebuild

Two movement passes each convert 12 of 196 ordinary roads.
Their saved inputs, native outputs, merged proposals, and connection patches retain their recorded hashes.
Independent reconstruction reproduces both inputs and both merged proposals.
The context boundary input and merge also pass reconstruction.

The complete rebuild materializes all 16 official vehicle movements without fallback parts.
Topology is complete. Construction coverage and the wider audit still require review.
The new candidate hash is `f09c24c7ef15950f52565a70eed698ef620c993a0188a027a666bc901c1c933a`.
It is byte-identical to the preceding scoped-movement rebuild.

Compared with the earlier `5af15e81...` candidate, all non-location elements and root attributes match.
Only the display-boundary spelling changes from `0.00` to `0.0`. The numeric bounds and coordinate frame match.
No additional fixed-cohort driving run was needed for that spelling change.
The earlier collisions and unfinished-demand results remain unresolved.

This real request uses the preserve contour policy.
Four separate native controls compare local and full-source contour selection, with guarded/fused modes and context inclusion on/off.
Their accepted and rejected target sets match. The fused controls explicitly accept all requested targets.
These controls do not establish acceptance of every real Hamburg contour.

## False-alarm correction and checks

The contour comparator now compares effective vehicle permissions, using each file's format version.
Equivalent legacy declarations retain a passing difference record. Actual permission, speed, geometry, conflict, and signal changes remain detectable.
The new permission regression failed before the fix. This is a technical false-alarm correction, not a field precision estimate.

Affected tests pass in overlapping runs of 100, 122, 49, and 72 checks. The native contour control has four cases.
The 49-check run includes the final command-output validation change. Ruff and whitespace checks pass.
This work did not repeat the local full test suite.

## Locations and remaining work

- Workflow runner: [`hamburg_execution_workflow.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_execution_workflow.py).
- Construction and comparison: [`movements.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_junctions/movements.py).
- Shared conversion and reconstruction: [`local_network_conversion.py`](../../plugins/astra-sumo/src/astra_sumo/core/local_network_conversion.py).
- Boundary restoration: [`junction_boundary_rebuild.py`](../../plugins/astra-sumo/src/astra_sumo/core/junction_boundary_rebuild.py).
- Request: `outputs/claude_followup_20261015_v1/hamburg-request.json`.
- Raw construction, comparison, and test records: `outputs/local_movements_20261016_v1/`.

Approach reconstruction, some controller changes, and documented context fallbacks still use full conversions.
The full-network reimport audit also remains. Local road counts are not workflow speed measurements.
The shared source-unchanged flags remain records, rather than a universal publication check.
Joint lane correspondence, lateral discrepancies, field audit precision, and independent dated labels remain open.
