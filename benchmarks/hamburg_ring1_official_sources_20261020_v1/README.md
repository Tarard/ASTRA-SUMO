# Official inputs for the README Ring1 corridor

This package freezes MAP XML, MAP KML, and OCIT-C files for nodes 104, 118, 119, 200, and 535.
Each compressed file retains the exact downloaded bytes after decompression.
The manifest records URLs, source filenames, retrieval time, and both hashes.
The package also retains the official catalog and LSA identity snapshot.

The files were acquired on 2026-10-20 through ASTRA's existing Hamburg asset resolver.
All five XML/KML pairs join by exact official lane and movement IDs.
They contain 375 lane records and 275 connection records, including nonmotorized modes.
This checks source identity. It does not establish SUMO lane correspondence or validate a constructed junction.
The current source versions do not prove the field layout during the complete count observation window.

Data: Freie und Hansestadt Hamburg, Landesbetrieb Straßen, Brücken und Gewässer.
License: [Datenlizenz Deutschland – Namensnennung 2.0](https://www.govdata.de/dl-de/by-2-0).
Source: [Traffic Lights Data Hamburg](https://suche.transparenz.hamburg.de/dataset/traffic-lights-data-hamburg6).
The catalog records this license and attribution.

The geographic TLS clustering result is only a proposal for physical junction grouping.
For example, the 60 m clustering can chain several nearby junctions into one large group at LSA118.
Do not merge that complete group without checking the official movement footprint.
