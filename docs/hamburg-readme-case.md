# Hamburg case shown in the README

## Site identity

The two `torii-v1-*` README figures depict the Binnenalster / Ring1 corridor, including LSA118.
The registered five-junction preset contains nodes `104`, `118`, `119`, `200`, and `535`.
Its definition is in [hamburg_corridor_candidate.py](../plugins/astra-sumo/src/astra_sumo/core/hamburg_corridor_candidate.py).
The four displayed directional count streams are `35822`, `33023`, `30666`, and `26585`.

This case is separate from the [Am Sandtorkai workflow](hamburg-sandtorkai-codex-execution-workflow.md).
That named corridor uses nodes `2349`, `2394`, and `2403`.
Its existing network fixtures do not establish results for the README case.

## Published count inputs

The official service still publishes the four displayed count streams.
Their metadata identifies processed directional station totals, with a 15-minute count interval.
The station composition lists constituent detector fields. Those field names are not SUMO lane numbers.

| Stream | Published detector composition |
|---|---|
| [35822](https://iot.hamburg.de/v1.1/Datastreams(35822)) | `0119-Z.13,0119-Z.11,0119-Z.12` |
| [33023](https://iot.hamburg.de/v1.1/Datastreams(33023)) | `0119-Z.5,0119-Z.4,0119-Z.15` |
| [30666](https://iot.hamburg.de/v1.1/Datastreams(30666)) | `0535-Z.6,0535-Z.7,0535-Z.8,0535-Z.5` |
| [26585](https://iot.hamburg.de/v1.1/Datastreams(26585)) | `0535-Z.14,0535-Z.4,0535-Z.9` |

A local snapshot retrieved on 2026-10-20 covers `2026-10-06T00:00:00Z` to `2026-10-20T00:00:00Z`, excluding the latter endpoint.
Each stream contains 1,344 distinct intervals, with no missing grid entries or invalid count values.
The service records each interval's final second as `hh:14:59`, `hh:29:59`, `hh:44:59`, or `hh:59:59`.
These input checks do not establish sensor accuracy, lane correspondence, or calibrated simulation accuracy.
The acquisition range is not a selected fitting or validation split.

The local raw responses, request URLs, retrieval times, and hashes are under
`outputs/hamburg-readme-case-20261020-v1/counts-discovery-v1/`.
The official service has a rolling observation range. A later query can return different coverage or revised records.

## Calibration evidence

The earlier README claimed 0.15 vehicles MAE per 15-minute bin.
No linked raw run or manifest currently supports that number in the repository.
It is therefore removed from the current README while the case is revalidated.

The [dated Ring1 record](development-history/ring1-road-continuity-and-calibration-2026-09-11.md)
reports different sample-window results: 3.125 vehicles per formal 15-minute bin at station 30666, and 2.9 across ten bins.
That record covers one station and four E1 loops. It also records incomplete turn construction and diagnostic signal timing.
Those historical numbers have not been reproduced from the current repository's input package.
They are not substitutes for a new measured before/after result.

A before/after comparison requires the same frozen observations, physical detector sections, vehicle-count definition, and time convention.
The demand fitting method and signal treatment must be recorded for both networks.
Fitting-window error and independent validation error are separate results.
A vehicle can pass several stations, so the sum of station counts is not the number of distinct corridor vehicles.

The existing figures remain illustrations of the earlier reconstruction.
Their source networks, calibration inputs, and dated aerial registration need a new reproducible case package before paper use.
