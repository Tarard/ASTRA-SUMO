# Coordinate precision during native construction

This fixture contains three prepared MoST ways and 40 nodes around junction `134549`.
The [manifest](fixture.json) records source hashes, native options, and the affected connection.
The effective type file retains the one road type used by these ways.
The [regression](../../tests/test_coordinate_precision_reference.py) needs no downloaded city or local run directory.

## Why construction precision matters

SUMO 1.27.1 calls `roundInputs()` after its optional normalization and before lane and junction geometry computation.
That method rounds node and road geometry using the selected precision.
`--precision` therefore affects construction, not just output formatting.
See the tagged native sources: [NBNetBuilder.cpp](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/netbuild/NBNetBuilder.cpp)
and [NBEdge.cpp](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/netbuild/NBEdge.cpp).

Under the retained type and import settings, SUMO 1.27.1 produced:

| Coordinate precision | Global target length | Normalized target length | Target connection |
|---|---:|---:|---|
| 2 | 195.76 m | 200.25 m | Present only in the global import |
| 6 | 200.246618 m | 200.246618 m | Absent in both |
| 12 | 200.246617684920 m | 200.246617685109 m | Absent in both |

These are model construction results. They do not identify the correct field movement or road length.
The regression checks the declared default for this input without requiring the coarse-precision failure to remain in future native versions.

## Application change

The shared scoped converter and typed geometry commands now supply precision 6 when the caller supplies no precision.
Explicit precision arguments remain unchanged.
Local extraction and full-input fallback use the same rule. Boundary round-trip verification uses it too.
The topology reload audit uses that default. Other legacy re-import paths still require migration and verification.
Public-source recipe comparisons retain their declared or native-default precision. Their earlier controls remain unchanged.
Declared contour restoration keeps its existing 12-place representation. Native construction precision and restored output representation are separate settings.

Native numeric conversion can also perturb inherited speeds at about one part in a billion.
The approach builder restores declared speeds only within `1e-9` m/s and records before/after values and network hashes.
Larger differences remain untouched and must pass the original preservation checks.
Raw native outputs remain available. The independent geometry check binds the final restored candidate.

## Real-network verification

The [evidence record](evidence.json) retains the native controls, implementation hashes, failures, and final Hamburg rebuild.
The first precision-12 test run passes 249 checks after correction. Its first attempt retains 27 failures and 178 passes.
Those failures exposed inherited speed roundoff and a string-format comparison of equal contour coordinates.
The speed correction retains exact declared values. The contour regression compares the complete numeric coordinates.

The fresh Hamburg reconstruction materializes all 16 official movements without fallback parts.
Movement and corridor-route checks pass. Loaded links match three adopted native junctions and 125 retained source junctions.
Source, generated inputs and candidate remain unchanged during verification.
Construction coverage and the wider audit still require review.
The candidate hash changes, so the older driving cohorts do not validate this candidate.

A complete MoST control retains these results under the same pinned input and recipe:

| Native precision | Maximum ordinary lane-length difference | Differences above 0.01 m | Ordinary connection differences |
|---|---:|---:|---:|
| 2 | About 21.7 m | Retained in earlier controls | 1 |
| 6 | About 0.000017 m | 0 | 0 |
| 12 | About 2.22 m | 12 | 0 |

Precision is not a monotonic robustness control. The default uses the tested micrometre construction grid.
The small fixture does not represent every junction in the full network.
These finite controls do not establish general frame invariance or field accuracy.
Centimetre-scale changes to source geometry can still select a different junction cut.
The exact native branch that amplifies the remaining geometric differences remains unresolved.

## Precision-6 and comparison follow-up

The updated shared default, typed geometry commands and topology reload audit use precision 6.
The fresh Hamburg reconstruction again retains all 16 official movements, zero fallback parts, and no loaded-link differences across 128 junctions.
All source and generated input hashes remain unchanged during validation.
Both sites remain measurable against the same frozen provisional fact tables.
Their motor-lane measurements do not establish confirmed field correspondence or traffic improvement.

The original precision-12 CI run exposed three failures caused by numeric formatting or roundoff in preservation comparisons.
The comparison now canonicalizes exact numeric spellings for physical attributes and coordinates.
Road IDs, state strings, permissions and actual numeric changes remain distinct.
Adjacent speed and width checks retain their `1e-9` numeric roundoff bound and preserve the original operating attributes in the final graph.
The native round-trip regression uses a consistent precision and compares all physical quantities and other attributes.

The local full suite passes 3,364 checks and skips 24. The retained CI failure log and skip reasons are in the evidence record.
This verification applies to the recorded implementation and experiments. Unmigrated legacy re-imports, source uncertainty and field validation remain open.

## Reproduce

Use the repository environment and put its SUMO binaries on `PATH`.
Run `python -m pytest tests/test_coordinate_precision_reference.py -q`.
Use new output directories for complete reconstruction or import controls.
The source configuration, runners, measurements and raw directories are recorded in the evidence file.

## Source notices

The source subset derives from [MoSTScenario revision d00e829](https://github.com/lcodeca/MoSTScenario/tree/d00e82945f5c6322a98ad2df4a8d5f48274fca53),
file `tools/static/most.raw.osm`. The upstream GPLv3 license is retained as [UPSTREAM_LICENSE.md](UPSTREAM_LICENSE.md).
The effective type combines the published type-map settings through SUMO 1.27.1 export.
This prepared input includes edited and synthetic data. Its timestamps do not establish an original OSM snapshot or field-validity date.
It remains part of the Monaco development family, without independent-city, field-defect, or human-cleaning labels.
