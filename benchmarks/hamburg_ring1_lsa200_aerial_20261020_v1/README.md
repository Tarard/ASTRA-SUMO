# LSA200 aerial reference

This snapshot covers the Ballindamm receiving road at LSA200.
It supports review of the disagreement between two short official MAP egress lanes and one OSM motor lane.
No receiving-lane composition or full-road lane addition is accepted by this package.

## Source

- Provider: Freie und Hansestadt Hamburg, Landesbetrieb Geoinformation und Vermessung (LGV).
- Dataset: [Luftbilder Hamburg - DOP Zeitreihe unbelaubt](https://suche.transparenz.hamburg.de/dataset/luftbilder-hamburg-dop-zeitreihe-unbelaubt7).
- WMS time selection: `2026`.
- CRS: EPSG:25832. Bounds: `[566200, 5934475, 566340, 5934615]`.
- The matching tile is `dop20rgbi_32_566_5934_1_hh_2026.tif`.
- Its published `Aktualitaet` is 2026-03-19, with 20 cm source pixels and an UltraCam sensor.
- The WMS display resamples the source. Its 1100 pixels over 140 m do not establish finer source resolution.

## Files

| File | Meaning |
|---|---|
| `aerial.png` | Unchanged WMS image response |
| `source.json` | Request URL, source hashes, retrieval time, bounds, and matching tile metadata |
| `capabilities.xml` | Saved WMS layer and time-domain description |
| `adv-metadata-2026.csv.gz` | Unchanged published 2026 tile metadata, compressed for storage |

The source dates of the MAP, OSM, aerial image, and traffic counts differ.
This snapshot does not establish that those sources describe the same road configuration.
It supplies no count-fit result, surveyed lane label, or historical signal replay.

Data license: [Datenlizenz Deutschland Namensnennung 2.0](https://www.govdata.de/dl-de/by-2-0).
Attribution: Freie und Hansestadt Hamburg, Landesbetrieb Geoinformation und Vermessung (LGV).
The service also lists a separate Maxar license for other content. This snapshot records the selected 2026 aerial tile metadata.
