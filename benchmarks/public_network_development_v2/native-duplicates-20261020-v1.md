# Native duplicate-road controls

This corrects a limitation in the [first duplicate repair](maintenance-20261020-v1.md).
Commit `9142a2f` restores native positive detection while retaining the MoST
negative control. The [summary](native-duplicates-20261020-v1.json) binds the
compiled examples and real-source repeat.

## Why the intermediate repair missed a duplicate

The intermediate signature compared raw incident connection records. Two
coincident compiled roads have different internal lane identifiers. Their
native priority labels can also differ because the roads compete for the same
space. Internal continuation rows introduced further generated identifiers.
The source geometry and declared lane rules could match while those records
differed. A hand-written XML positive control did not expose that problem.

Claude independently reproduced the miss with a native four-road network.
A local native probe confirmed it. The original zero-proposal MoST result
therefore did not establish useful duplicate detection by itself.

## Final comparison and controls

The signature now compares ordinary movements. Internal paths use their actual
lane geometry, permissions, parameters and continuation rules instead of generated
identifiers or internal ordinal indices. Ordinary turn directions and declared
connection rules remain compared. Native priority labels remain outside the
proposal-equivalence claim. Signals and other protected roads retain their
existing exclusion rules.

Unknown, cyclic or branching internal paths do not establish equivalence.
This conservative rule can miss duplicates in more complex layouts. The command
does not approve deletion or establish equal native right-of-way.

| Captured netconvert example | Duplicate proposals |
|---|---:|
| Four roads, with two coincident middle roads | 1 |
| Same roads, different internal path shape | 0 |
| Same roads, different internal path permission | 0 |
| Same roads, unknown internal reference | 0 |

The same frozen MoST input still produces zero proposals under clean committed
code. All 31 earlier deletion proposals remain excluded. Their published-model
retention records remain in the preceding report. This does not estimate field
precision, missed duplicates, or useful automatic-cleaning coverage.

The native controls and related consumers passed 101 tests. A separate capture
of all 12 duplicate controls passed and retains the raw generated networks.
Counts overlap. An intermediate synthetic negative fixture referenced a missing
ordinary road. It now supplies the ordinary neighbors that the comparison uses.
Its failed result remains separate. Ruff and `git diff --check` passed.

## Locations

- Native regression and fixture generator: `tests/test_duplicate_road_proposals.py`.
- Captured PlainXML and compiled examples: `outputs/native_duplicate_controls_20261020_v1/`.
- Initial native diagnosis: `outputs/probe_native_duplicate_20261020_v1.py` and `outputs/native_duplicate_20261020_v1/`.
- MoST runner: `outputs/probe_source_only_proposals_20261020_v3.py`.
- Input identities, implementation hashes, proposal measurements: `outputs/source_only_proposals_20261020_v3/inventory.json`.
- Published-model assessment: `outputs/source_only_proposals_20261020_v1/reference-assessment.json`.
- Summary runner: `outputs/freeze_native_duplicate_20261020_v1.py`.
- Claude review: `outputs/claude-review-1f54dba-20261020.txt`.

Current complete CI remains pending. The PR remains a draft and is not merged.
