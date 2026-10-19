# A documented prepared-source revision pair

The existing MoST comparison separates published-model structure from field
truth. This supplement recovers a different kind of pair: prepared source
geometry before and after a documented curation commit.

## Source and acquisition

MoST commit [`e9585c4`](https://github.com/lcodeca/MoSTScenario/commit/e9585c408ebcb8c987b4573f0426487851685a8b)
is titled “2 intersections fixed” and is dated 2018-09-19. It modifies
`tools/static/data/network.osm` and the aggregated `tools/static/most.raw.osm`.
The parent is `f551c4a25db8695591ae80cb5618645dd8e75f2b`.

The acquisition saves both versions of the prepared `network.osm` component.
It verifies byte sizes and Git blob SHA-1 against GitHub metadata, then records
SHA-256. The files contain about 3.1 MB each. The raw geometry remains local.
The [frozen receipt](source-curation-pair-20261020-v1.json) records their identities.

This prepared OSM-like input includes external road provenance identifiers.
It is not an original dated OSM snapshot. The commit date does not establish
when any field observation was valid. Its title states curation intent, not
correctness of every changed object.

## Automatic comparison

XML state comparison finds 67 shared positive node identifiers with changed
coordinates and five shared positive way records with changed source state.
There are also 20 added and 21 removed identifiers. Those counts do not label
added or removed roads. Negative editor identifiers can be renumbered.

Road geometry pairing requires the same positive file-local way identifier and
a unique, unchanged external source ID in both snapshots. It compares the
resolved coordinate sequences, rather than treating changed node references as
changed geometry automatically.

| Registered prepared-road geometry | Count |
|---|---:|
| Changed coordinate sequence and geometric path | 17 |
| Unchanged geometry | 1,417 |
| Unresolved source provenance or geometry | 23 |

The 17 records retain before/after longitude-latitude coordinates and source
provenance. They have no field-defect label. One raw reference change can merely
rename an editor-assigned node, which is why the geometry comparison is separate.

These records can support source-revision development without waiting for a new
manual labeling round. They do not establish correct human-drawn lane geometry.
Prepared road centerlines are not SUMO lane centerlines. SUMO imports, generated
network comparisons, and calibration-fact integration have not run for this pair.
The pair also belongs to the existing Monaco family, not an independent city.

## Locations and remaining checks

- Acquisition runner: `outputs/acquire_source_revision_pair_20261020_v1.py`.
- Geometry-label runner: `outputs/label_source_way_changes_20261020_v1.py`.
- Metadata, exact source bytes, XML-state comparison, labels, and unresolved records: `outputs/source_revision_pairs_20261020_v1/`.
- Geometry measurements and counts: `source-way-label-summary.json` in that directory.
- Summary runner: `outputs/freeze_source_revision_pair_20261020_v1.py`.

The historical source license has not been separately reviewed. No raw source
geometry is added to the repository. File-local identity is not confirmed field
correspondence. Native import controls and downstream use remain open.
