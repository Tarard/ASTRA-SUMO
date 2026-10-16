# Frozen Hamburg approach references

This fixture retains the exact diagnostic source model and official MAP/KML inputs for six approach comparisons.
It supports a portable, read-only regression. It contains no aerial raster or complete construction replay package.
The [manifest](fixture.json) records compressed and uncompressed hashes, source URLs, parameters, and expected outcomes.
Gzip preserves the input bytes. A decompressed file must match its recorded SHA-256.

## Scope

Four approaches already have the required source-model structure.
Two remain review cases because the existing transition node lies outside the MAP merge footprint.
The reference coordinates and source graph establish these model-relative comparisons.
They do not establish human defect labels, field-validity dates, historical signals, or actual traffic performance.

Run `python -m pytest tests/test_hamburg_existing_approach_reference.py -q` to check the fixture.
The test verifies every input hash and calls the approach builder with native conversion disabled.
It checks the four existing cases, both remaining offsets, and source immutability.
No download or SUMO executable is required for this comparison.

## Source licenses and changes

The generated source database, `joined-base.net.xml.gz`, is distributed under the
[Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Attribution: © OpenStreetMap contributors. See the [OpenStreetMap copyright notice](https://www.openstreetmap.org/copyright).
The OSM input was retrieved on 2026-10-15. Its exact query URL and hash are in the manifest.
The model applies diagnostic junction joins and retains Hamburg official geometry. It is an edited diagnostic database.

The unchanged official MAP/XML and KML snapshots use
[Datenlizenz Deutschland Namensnennung 2.0](https://www.govdata.de/dl-de/by-2-0).
Attribution: Freie und Hansestadt Hamburg, Landesbetrieb Straßen, Brücken und Gewässer.
Dataset: [Traffic Lights Data Hamburg](https://suche.transparenz.hamburg.de/dataset/traffic-lights-data-hamburg6).
The manifest links each exact source file. These notices also apply to official geometry retained in the edited model.
The plan metadata selects the lane and intersection identities required by this regression.
Field-validity dates remain unknown.
