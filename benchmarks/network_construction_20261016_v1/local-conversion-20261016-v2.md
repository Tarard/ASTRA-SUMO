# Spatial curve and permission follow-up

Status: diagnostic only, 2026-10-16. Construction commit `2251796`, native SUMO 1.27.1.
The [v2 record](local-conversion-20261016-v2.json) supplements [v1](local-conversion-20261016-v1.md), which remains unchanged.

## Changes and checks

Custom movement repair now samples the saved spatial curve at intervals no greater than 0.5 m before blending endpoint corrections.
It retains original vertices and the directions of its first and last nonzero segments.
The proposed curve can have different sample spacing after correction.
Reports separate endpoint direction changes, interior vertex turns, spatial lengths, and explicit logical lengths.
These quantities do not certify physical curvature, vehicle behavior, or field geometry.

Two permission corrections preserve old-network meaning when native import expands legacy restrictions.
Checks apply each file's version to lanes and connections.
Inherited lane-count patches specify explicit permitted classes, and every resulting target lane is checked against its source template.
A deliberately widened target lane now fails technical acceptance while explore still returns its measurements.

The ownership fixtures now distinguish an internal road's name from its physical parent references.
Renaming a connected path remains valid. Conflicting junction lane references remain unresolved and prevent an official movement claim.
This changes the test faults, not the reference-based ownership rule.

Separate focused runs passed 72 checks for dense curves and construction, 113 for ownership and movement support,
62 for target-lane inheritance, and 26 for geometry snapshot reuse.
The runs overlap and do not constitute a new local full-suite run.
Ruff and `git diff --check` pass for the affected changes.

## Latest real-network result

The public Hamburg width command still reports zero error against the nominal 3.25 m MAP default and zero changed facts outside scope.
The candidate has zero structural failures and zero remaining endpoint gaps.
Two repaired curves use 174 and 133 proposed points, compared with 88 and 68 saved points.
Their largest proposed vertex turns are 3.357 and 23.449 degrees.
Those values depend on sampling. They are not turning-radius or driving-quality certificates.

The original 100 routed requests retain the same paths, departures, and three seeds.
Arrival counts are again 61, 57, and 100 for the width candidate, versus 64, 100, and 60 for the parent.
Seeds 17 and 42 retain one collision and one teleport in each model.
All three pairs remain invalid for clean performance comparison. No invalid pair enters a performance average.
Identical arrival counts across the two candidate revisions do not establish identical trajectories.

The repeated MoST case converts 9 of 4404 ordinary roads and passes technical checks.
Its candidate hash equals the prior routable v2 candidate hash.
Whole-network checks still dominate this example. This follow-up makes no total-workflow speedup claim.

## Verified locations

- Curve implementation: [`junction_rebuild/geometry.py`](../../plugins/astra-sumo/src/astra_sumo/core/junction_rebuild/geometry.py) and [`native_path_repair.py`](../../plugins/astra-sumo/src/astra_sumo/core/junction_rebuild/native_path_repair.py).
- Permissions: [`network_edit_operations.py`](../../plugins/astra-sumo/src/astra_sumo/core/network_edit_operations.py) and [`network_permissions.py`](../../plugins/astra-sumo/src/astra_sumo/core/network_permissions.py).
- Driving comparison: [`paired_driving.py`](../../plugins/astra-sumo/src/astra_sumo/core/paired_driving.py).
- Regression tests: [`test_native_custom_path_repair.py`](../../tests/test_native_custom_path_repair.py), [`test_local_network_conversion.py`](../../tests/test_local_network_conversion.py), and [`test_official_movement_composition.py`](../../tests/test_official_movement_composition.py).
- Scenario: `outputs/local_conversion_20261016_v1/width-explore-request-v2.json` and `reference-width-paired-request-v3.json` in the same directory.
- Raw results: `width-explore-v5/`, `most-width-routable-v4/`, and `reference-width-paired-v3/` under that output root.

The v2 record retains exact artifact hashes. Generated networks and native outputs remain local.
