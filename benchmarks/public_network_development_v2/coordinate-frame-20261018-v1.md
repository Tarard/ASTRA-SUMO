# MoST coordinate-frame repeat

The [evidence record](coordinate-frame-20261018-v1.json) retains source identities, implementation hashes, and comparison results.
The original catalog, cache, and earlier runs remain unchanged.

## Cause and change

The earlier unjoined import forced global coordinates. The published network uses normalized local coordinates.
Both writers retain two decimal places. Subtracting their saved offsets does not undo the earlier rounding.
The old import has 1,038 same-ID nodes with a 0.01 m coordinate difference from the published model.

The importer now follows the reference's normalization convention.
A zero declared reference offset disables normalization. A nonzero offset enables native normalization.
The report records that choice. It does not infer an arbitrary manual translation or force unmatched coordinates to agree.
The exact node, lane, and complete-join registration rules remain unchanged. No proximity tolerance was added.

The regression retains both global and normalized reference cases, including a native source-to-reference comparison.
All 26 focused corpus checks pass.

## Correspondence result

The repeat uses the same historical prepared input, published model, configuration, and SUMO 1.27.1.
It changes only the declared normalization option in the unjoined import command.
Native conversion can change more than the saved coordinate text, as the next section records.

| Record | Earlier global import | New normalized import |
|---|---:|---:|
| Registered source nodes | 1,019 | 2,054 |
| Joined source-node pairs | 58 | 58 |
| Separate source-node pairs | 599 | 2,475 |
| Retained lane connections | 820 | 6,171 |
| Added lane connections | 80 | 291 |
| Removed lane connections | 2 | 10 |
| Known model-relative records | 1,559 | 9,005 |
| Unknown records | 9,410 | 1,963 |
| Total records | 10,969 | 10,968 |

Across shared logical selectors, 7,456 records gain correspondence and ten lose it.
Another 3,502 remain unchanged. One previously unknown connection record leaves the newly generated source universe.
No shared known target changes its expected value. The machine record lists the nonmonotone cases.
These are separate versioned label tables. Their case IDs also bind their different generated source hashes.

The published-model self-check scores all 9,005 known records without a mismatch.
It retains the 1,963 unknown records and reports `review_required`.
Self-agreement verifies extraction consistency. It does not establish cleaning performance.

## Native conversion changes

Ordinary road IDs and lane IDs are unchanged between the generated imports.
The normalized import omits connection `152526:2 -> 152527:2`. The earlier label for this connection was unknown.
Logical lengths change on 5,410 ordinary lanes. The largest absolute change is 21.7 m.
The machine record retains these counts alongside the improved registration.
The new import is a separate development baseline. It is not an equivalent rewrite or an adopted road repair.
No driving-equivalence or traffic-improvement claim follows.

## Published-configuration control

The control retains published joining and geometry settings under current SUMO.
It loads successfully and evaluates the same new label table.

| Control outcome | All records | Known differences in the unjoined baseline |
|---|---:|---:|
| Recipe reproduced | 8,897 | 295 |
| Unexplained difference | 69 | 64 |
| Control correspondence unresolved | 39 | 0 |
| Reference correspondence unknown | 1,963 | Not judged |

Published settings explain 295 of the 359 known differences in the unjoined baseline.
The other 64 differences remain unexplained. None receives a manual-edit or field-defect label.
The historical executable, original OSM date, and field-validity date remain unestablished.

## Verified locations

- Implementation: [`public_reference_corpus.py`](../../plugins/astra-sumo/src/astra_sumo/core/public_reference_corpus.py).
- Source configuration and identities: [`catalog.json`](catalog.json).
- Native regression: [`test_public_reference_corpus.py`](../../tests/test_public_reference_corpus.py).
- New corpus and generator control: `outputs/source_pairing_20261018_v1/most-normalized-v1/`.
- Model self-check: `outputs/source_pairing_20261018_v1/self-comparison-v1.json`.
- Baseline comparison: `outputs/source_pairing_20261018_v1/baseline-comparison-v1.json`.
- Raw analysis: `outputs/source_pairing_20261018_v1/analyze-normalization-v1.py`.

Use the existing corpus command with this catalog, intact cache, and a new output directory to reproduce the run.
This improves available development correspondence. Field precision, audit false-alarm rates, and human-cleaning accuracy remain unknown.

## Frame-reporting follow-up

The importer now records its actual offset and projection alongside the reference values.
Separate equality fields expose a manual-offset mismatch without hiding it among unknown labels.
Missing metadata stays unknown. A different saved offset can still describe compatible world coordinates after translation.
These fields do not add an acceptance condition.
The new native regression deliberately changes a reference offset and retains both the diagnostic difference and the unknown records.

A fresh MoST repeat retains all 9,005 known records and 1,963 unknowns.
Its actual offset and projection match the reference. The evidence record binds this run to the updated implementation.
All 49 corpus, observation-error, and navigation checks pass.

An independent exact-recipe replay also reproduces the 5,410 changed lane lengths and the omitted vehicle connection.
Decimal comparisons of the saved lengths find 123 differences above 0.1 m and 32 above 1 m.
These are diagnostic bins. They do not define an acceptable length error or a noise threshold for other networks.
The native output depends on the coordinate convention. The internal cause and per-target sensitivity remain unresolved.
Incomplete coordinate correspondence and a changed generated connection are different observations. Neither supplies a field-defect label.
