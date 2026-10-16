# Existing Hamburg approaches and remaining merge offsets

Status: diagnostic source comparison, 2026-10-16. Construction code: `338b769`.
The [machine record](existing-approaches-20261016-v1.json) binds the source, complete rebuild, views, and checks by hash.

## Six construction findings

The previous builder treated all six approaches as unresolved construction cases.
It required a uniquely continuing official lane or a full reference on one source road.
That missed references which begin at a merge, and complete source paths split across ordinary roads.

The correction reads saved motor-lane connections and explicitly permitted adjacent changes on the same ordinary road.
It follows unchanged lane sections upstream through unrelated side entries.
It does not remove those entries or use a downstream detour to justify pocket access.
Several possible continuations remain review findings.
References without a merge require distinct saved paths, complete sampled directed coverage, and a margin over alternative lane indices.

| Site and approach | Source evidence | Construction result |
|---|---|---|
| 2349, approach 1 | Existing 1-to-2 transition at `5353176677`, followed by stable two-lane road sections | No additional lane construction |
| 2349, approach 3 | Two distinct connected paths cover both complete references. Mean lateral offsets are about 0.39 m and 0.28 m | No additional lane construction |
| 2394, approach 2 | Existing 1-to-2 transition at `86812401`. The separate pedestrian and cycle lanes remain unchanged | No additional lane construction |
| 2394, approach 4 | Existing 2-to-3 transition at `5353176676`, followed by stable three-lane sections | No additional lane construction |
| 2349, approach 2 | Existing 1-to-2 transition. Its node center lies +16.529 m along travel from the MAP merge | Review transition location |
| 2394, approach 3 | Existing 1-to-2 transition. Its node center lies -5.264 m along travel from the MAP merge | Review transition location |

Positive offset means that the source node center lies downstream of the MAP point.
These values compare a model node center with a MAP mark. They do not measure a surveyed taper start.
The new review records retain both lane counts, saved access paths, and longitudinal and lateral offsets.
They replace the two misleading lane-count-mismatch explanations.

The three transition matches establish source-model structure within the junction footprint.
They do not establish full reference coverage, actual lane geometry, historical validity, or driving capacity.
The four resolved construction findings are not a precision estimate for the separate network-wide audit.
Existing lateral discrepancies and uncertain field dates remain calibration work.

## Fixed aerial comparison

Six fixed-bound views compare the source and candidate against the unchanged 2026 Hamburg WMS image.
Two additional figures compare the aerial, official MAP centerlines, and saved model centerlines.
Their markers distinguish the MAP merge from the source node center.
The figures retain source attribution and label the geometry as references rather than field labels.
Aerial acquisition date within 2026 and MAP field-validity dates remain unknown.
No road or facility was removed because it was hidden or unclear in the image.

## Complete rebuild and checks

The complete Hamburg rebuild has four existing-approach outcomes and two remaining location findings.
It makes no approach edit and retains all 16 official vehicle movements.
Its final network remains byte-identical to `f09c24c7ef15950f52565a70eed698ef620c993a0188a027a666bc901c1c933a`.
All frozen inputs and generated inputs remain unchanged. The overall construction decision remains `review_required`.
The earlier collisions and unfinished trips therefore remain relevant. No additional cohort run was needed for an unchanged network.

Native controls cover fragmented references, explicit fanouts, ordinary lane-change access, a blocked pocket, and a shifted reference merge.
A side-entry control failed before the continuation correction. The affected run then passes 118 checks.
Another 107-check run verifies conversion callers and immutable inputs. These runs overlap.

Shared full-network fallbacks now compile an unchanged, hashed input copy.
Parsing and hashing use the same captured source bytes. External source drift still returns a warning and its record.
A controlled mutation failed before this correction and now leaves the candidate tied to the original snapshot.
A native full-source control and an input/output-alias check pass. The final focused snapshot run has three passing cases.
This change applies to the shared converter. Other documented conversion paths and release-chain work remain open.

## Verified locations

- Recognition and measurements: [`hamburg_aerial_approach.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_aerial_approach.py).
- Existing connected-path measurements: [`hamburg_network_calibration.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_network_calibration.py).
- Immutable conversion inputs: [`local_network_conversion.py`](../../plugins/astra-sumo/src/astra_sumo/core/local_network_conversion.py).
- Native controls: [`test_hamburg_aerial_approach.py`](../../tests/test_hamburg_aerial_approach.py) and [`test_local_network_conversion.py`](../../tests/test_local_network_conversion.py).
- Request: `outputs/claude_followup_20261015_v1/hamburg-request.json`.
- Raw rebuild and checks: `outputs/existing_approach_20261016_v1/`.
- Fixed aerial report and images: `outputs/local_approach_20261016_v1/aerial-review-v1/`.
- Reference-figure runner: `outputs/existing_approach_20261016_v1/plot_reference_approaches.py`.

No local full-suite rerun, independent field validation, or contour release-chain validation is claimed here.

The [portable fixture](../hamburg_approach_reference_20261016_v1/README.md) now includes the exact compressed model and original official XML/KML inputs.
Its read-only regression checks all six outcomes without local run folders, downloads, or native compilation.
The fixture records source licenses and retains the distinction between model-relative evidence and field truth.
