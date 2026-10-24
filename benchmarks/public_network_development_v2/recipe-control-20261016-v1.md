# MoST published-configuration control

[`recipe-control-20261016-v1.json`](recipe-control-20261016-v1.json) records the new run and its code identity.
The earlier catalog, source cache, and results remain unchanged.
This experiment uses SUMO 1.27.1 and commit `1229f21`.

The control imports the same prepared OSM and five catalog-bound type maps.
It retains published joining and geometry-removal settings instead of the unjoined baseline overrides.
It ignores upstream output paths. The native control network loads successfully.
This replays supported published settings with current defaults. It does not reproduce the historical executable or all historical defaults.

The new run retains 10,969 original membership and connection records.
Its exact registration rules can judge 1,559 reference records. Another 9,410 remain unknown.
The control evaluates those same case IDs without rewriting the label table.

| Control outcome | All original records | Records that differed in the unjoined baseline |
|---|---:|---:|
| Recipe reproduced | 1,512 | 122 |
| Unexplained difference | 6 | 4 |
| Control correspondence unresolved | 41 | 14 |
| Reference correspondence unknown | 9,410 | Not judged |

Thus, published generator settings reproduce 122 of the 140 known baseline differences.
The remaining 18 baseline differences are unexplained or unresolved.
They do not receive manual-edit or field-defect labels.
The full record retains unknowns and has no human-cleaning evaluation or promotion authority.

The 25 focused corpus tests pass, including native elevation, exact-label attribution, and failed-control cases.

## Verified locations

- Implementation and exact registration: [`public_reference_corpus.py`](../../plugins/astra-sumo/src/astra_sumo/core/public_reference_corpus.py).
- Scenario and source identities: [`catalog.json`](catalog.json).
- Raw control, label provenance, and native command: `outputs/construction_trace_20261016_v1/most-recipe-control-v1/`.
- Regression tests: [`test_public_reference_corpus.py`](../../tests/test_public_reference_corpus.py).

The prepared source includes synthetic and manually prepared content. Its timestamps do not establish a dated field snapshot.
This is generator reproducibility evidence, not a new independent city or field validation.
