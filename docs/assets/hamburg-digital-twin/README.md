# Hamburg digital-twin image provenance

These are curated, review-sized images for the repository README. They are
evidence illustrations, not substitutes for the hash-bound machine manifests.

| File | Provenance | Reproduction / license note |
|---|---|---|
| `official-aerial-2024.png` | Hamburg LGV DOP time-series WMS | `EPSG:25832`, bbox `565100,5933000,566350,5933625`, `TIME=2024`, 1600×800. Source: Freie und Hansestadt Hamburg, Landesbetrieb Geoinformation und Vermessung (LGV), Datenlizenz Deutschland – Namensnennung – Version 2.0. |
| `official-construction-plan-2022.png` | LSBG, *Am Sandtorkai / Brooktorkai – Verstetigung Pop-up-Bikelane*, coordinated plan, July 2022 | Page 1 rendered at 110 dpi. Keep the official PDF link with every public use. |
| `osm-import-overview.png` | Torii review rendering of the original OSM-derived Hamburg network | Generated artifact; red points are TLS/review anchors. |
| `torii-cleaned-corridor-connection.png` | Torii background NetEdit Connection Mode review of the compact OSM-derived corridor after micro-edge cleanup | Primary cleaned-network illustration. It exposes lane-to-lane movements and remaining junction complexity; diagnostic candidate, not automatic promotion evidence. |
| `torii-cleaned-corridor-overview.png` | Matching neutral overview of the same candidate | Auxiliary image only; the README uses Connection Mode as the primary cleaned-network evidence. |
| `torii-2403-single-core-inspect.png` | Torii background NetEdit Inspect capture of the bounded 2403 single-core probe | Candidate hash `2da03214eea32571669d5a0cbac80fd88a4bd9079a76be36290a8c180df5c559`; review-only because official 2403 MAP/OCIT is unpublished. |
| `torii-2403-junction-inspect.png` | Torii `NeteditTargetSession` capture after clicking the real 2403 network object | Primary repaired-junction screenshot; the left pane visibly identifies `Net: junction`. Candidate hash `2da03214eea32571669d5a0cbac80fd88a4bd9079a76be36290a8c180df5c559`. |
| `torii-2403-junction-connection.png` | Background NetEdit Connection Mode capture of the same 2403 candidate | Companion lane-to-lane movement evidence for the exact same network hash. |
| `official-tls-binding-2394.png` | Torii background NetEdit TLS capture for official node 2394 | Visual evidence for the MAP/OCIT/TLD-to-SUMO binding; machine manifest remains authoritative. |

Official sources:

- [Hamburg LGV DOP WMS metadata](https://metaver.de/trefferanzeige?docuuid=cc0eaed8-cb36-44a0-9bda-153f28d9e7ba)
- [Hamburg LGV DOP dataset metadata and attribution](https://metaver.de/trefferanzeige?docuuid=5DF0990B-9195-41E7-9960-9214BC85B4DA)
- [LSBG coordinated construction plan PDF](https://lsbg.hamburg.de/resource/blob/784084/6a06328b36b0de140d75baac9165f8f7/am-sandtorkai-brooktorkai-pop-up-bikelane-verstetigung-abgestimmte-planung-plan-data.pdf)
- [LSBG planning report PDF](https://lsbg.hamburg.de/resource/blob/784082/d82b462f3347d710b8f0cdee89a034af/am-sandtorkai-brooktorkai-pop-up-bikelane-verstetigung-abgestimmte-planung-bericht-data.pdf)
