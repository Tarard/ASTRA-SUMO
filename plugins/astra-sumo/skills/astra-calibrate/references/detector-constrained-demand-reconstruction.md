# Detector-Constrained Demand: Inputs, Mapping and Diagnosis

Load this when you prepare the input files for the generic fit in `SKILL.md`, map field detectors to SUMO lanes, or diagnose a poor fit.

## Input files

Detector mapping CSV (`detector_mapping_csv` of `sumo_detector_route_support`), one row per detector:

- `detector_id` (or `s_idx`, `id`), `edge_id` (or `sumo_edge`, `edge`), `lane_id` (or `sumo_lane`, `lane`), `lane_position` (or `lane_pos`, `pos`), `mapping_status` (or `status`); optional `real_direction`, `mapping_confidence`, `source_system`, `period`.
- Only `active` rows are routed, and a file without a status column counts every row as `active`. Write `needs_review`, `inactive`, `out_of_scope` or `ignored` to exclude a row.
- The lane must exist in the network and belong to the declared edge.

Expected counts CSV (`astra demand audit`, and `expected_counts_csv` of the replay tool): `detector_id`, `begin`, `end` in simulation seconds, and `expected_total` (or `entered`, `count`, `total`) as a non-negative integer; one row per detector and bin. The `detector_id` values must equal the E1 detector ids in the SUMO output.

routeSampler edgeData (`edge_data_file` of `sumo_detector_route_sampler_calibrate`):

```xml
<data>
    <interval id="bin_0" begin="0" end="900">
        <edge id="E12" count="41"/>
    </interval>
    <interval id="bin_1" begin="900" end="1800">
        <edge id="E12" count="38"/>
    </interval>
</data>
```

- Intervals must be exactly `interval` seconds wide and cover `begin` to `end` without gaps. The tool runs routeSampler with `--edgedata-attribute count`.
- Add an `<edge>` only when every passenger lane of that edge is observed at one cross-section; its count is the sum of those lanes in the bin. Keep partly observed edges as validation detectors only.
- `sumo_detector_count_constraints` writes this file from an expected counts CSV that has an `edge_id` column. It sums the rows of each edge and bin, so give it only complete cross-sections. Its `route_sampler_interval` is the `interval` to pass; `null` means a bin is missing or the widths differ, and calibration will reject the file.

## Detector-to-lane mapping

Field, paper and agency detector points are usually directional or lane-specific. Nearest-lane projection alone is not a mapping.

1. Keep the source detector table unchanged and write the mapping as a separate CSV.
2. Read direction, road side, lane side, movement and station grouping from the source text. Local-language direction phrases are evidence, not fixed rules.
3. Collect candidate passenger lanes within a bounded radius, with lane index, edge id, distance and lane position.
4. Opposite directions are separate detectors and separate constraints. Do not merge them unless the source data has no direction.
5. Within an active detector group, do not let two detectors share one `lane_id` unless that is intended. In one direction prefer distinct lane indices on the same directed edge; across directions do not share a directed edge unless the source says so.
6. On a same-lane conflict, re-solve the whole station or movement group, not only the colliding pair.
7. If the rules do not give one low-distance assignment, set `mapping_status=needs_review` instead of accepting a duplicate.
8. A detector on a road that the network does not contain is out of scope. Record it; do not move it to a nearby road.

Keep each count traceable through this chain: field sensor, time and count row, then mapping row, then SUMO detector id, lane and position, then the simulated vehicles, then the detector output, then the comparison row. Detector id, lane, position and period must agree between the mapping CSV and the detector additional file.

## Count semantics

- `astra demand audit` uses E1 `nVehEntered`, which also counts vehicles that only touch the loop. The replay tool uses `nVehContrib`, which counts complete passages. Name the attribute you report.
- Near the end of a very short edge, `nVehContrib` can miss vehicles that cannot leave the loop normally. Do not add vehicles to repair that residual.
- When a station sums several lane loops, `nVehEntered` can count one lane-changing vehicle twice. Deduplicate by station, vehicle and interval from instantaneous loop events.
- `route_detector_incidence` is edge-level and cannot tell parallel detector lanes apart. Check the physical E1 responses before you fit lane counts.
- When several field detectors project onto one SUMO lane at one node, use one virtual detector at the most downstream position and keep every source id for the expected-count sum.
- A departure lane controls only the first edge of a route. A detector edge later in the route is an internal observation; do not write `departLane` values to fit it.

## Time bins and scale

- Sampling interval, aggregation interval, comparison window, warm-up and replay horizon are separate settings; state each one.
- Use absolute local dates and keep bins fine enough to show the AM and PM peaks. A full-day total alone is not a target.
- Keep incident, holiday, roadwork, event and normal days apart.
- Scale up in steps: one hour, then the AM peak, the PM peak and one low-flow hour, then one day of hourly bins, then several days grouped by day type. Compute daily metrics from exact hourly sums, not from rounded hourly metrics.

## Diagnose a poor fit

| Symptom | Likely cause | Next action |
|---|---|---|
| route support fails for a detector | wrong direction in the mapping, missing connector, detector outside the network | fix the mapping, or return to `$astra-build` for a missing connector |
| routeSampler `partial` | too few candidate routes or conflicting constraints | rerun with `optimize: "full"` first (default sampling can miss an exact solution); then inspect `mismatch` and `route_detector_incidence` and raise `max_routes` or `max_hops` |
| underflow on one detector | missing route support or wrong direction | check the incidence table before adding vehicles |
| overflow on one detector | duplicate constraint, merged directions, too broad routes | audit the mapping before deleting routes |
| `unobserved_route_ids` not empty | routes that no counted edge sees | their flow is unconstrained; add a boundary count or a prior, or remove them |
| good fit, poor completion | insertion backlog, too few distinct routes, blocked movement | keep the fit unaccepted; restore route diversity or repair the cause, then rerun |
| good daily total, bad peak | bins too coarse | rebuild the counts in hourly or 15-minute bins |
| exact fit only with very few routes | route diversity collapse | keep the more diverse result as the reference and report the trade-off |
