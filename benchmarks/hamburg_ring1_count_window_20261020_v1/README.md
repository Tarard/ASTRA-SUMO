# Frozen Ring1 source-count window

This package covers 2026-10-19 from 05:30 to 08:00 UTC.
It retains 390 five-minute observations from 13 official count fields and 40 fifteen-minute totals from four processed stations.
The published field compositions reproduce all 40 station totals exactly, with no missing source cells.
This checks source aggregation. No SUMO simulation or demand fitting produced these values.

`manifest.json` records file hashes, intervals, identities, and the source service.
`fields.raw.json` retains the selected field metadata. Field numbers are not SUMO lane indices.
`field-observations.json.gz` retains the parsed response pages, request URLs, and retrieval times for each field.
`field-counts-15min.csv` uses the existing canonical count writer, including completeness flags and original geographic points.
`stations.raw.json` retains the processed-station metadata and published membership lists.
`station-observations.json.gz` retains the published station observation records used for comparison.
`station-counts.json` records the published station totals and their source-field sums.

The window is a fixed aggregation check. A fitting/validation split has not been selected here.
The input package does not establish surveyed lane identity, field signal timing, calibrated demand, or independent prediction accuracy.

Data source and attribution: Freie und Hansestadt Hamburg, Verkehrsdaten Kfz (Infrarotdetektoren), [official SensorThings service](https://iot.hamburg.de/v1.1/).
