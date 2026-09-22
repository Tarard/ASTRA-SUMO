# Local road-use evidence review

Run this read-only check before deciding whether an observed road strip needs
a construction change:

```powershell
torii hamburg inspect-road-uses <request.json> <new-output-dir> --json
```

The output directory must be new. `road-use-review.json` records input hashes,
the settings, local comparisons, and unresolved evidence. A successful command
returns exit code 0 and `status: complete`. Its decision remains
`review_required`. Completion means that the comparison finished.

This is a separate inspection command. `build-network` does not call it or
apply its candidates. It does not extract image markings or reproduce a
trained image classifier.

## Request

Use `schema: torii.hamburg-road-use-review-request/v1`, `crs: EPSG:25832`,
a nonempty `sources` list, and a nonempty `samples` list.

Each source contains `path` and `sha256`. Paths can be relative to the request.
Include the observations, images, reference records, and MAP/OSM files used to
prepare the samples. Hashes verify file identity, not correct extraction.

Each sample contains:

| Field | Content |
|---|---|
| `id` | Unique sample name |
| `point_epsg25832` | Observed point `[easting, northing]`, in metres |
| `image_year` | Four-digit year, or null when unknown |
| `visual_function` | Original observation, such as `parking`, `cycling_indicated`, `bus_indicated`, or `undetermined` |
| `references` | Records with `id`, `year`, `category`, and `lines`, each line a coordinate list |
| `map_lanes` | Records with unique `id`, `line`, `lane_type`, and available `direction_role`, `revocable`, and `allowed_vehicle_classes` metadata |
| `road_axes` | Optional local road-axis records with `id` and `line` |

Empty reference geometry is retained as missing. Degenerate or nonfinite
coordinates are rejected. `revocable` accepts true, false, or null.
Keep different reference years as separate records. Include every relevant
nearby candidate instead of selecting the nearest lane in advance.

## Interpretation and settings

The command compares local line profiles around the observed point. Reversed
coordinate order does not change the line-orientation check. It does not
establish travel direction. A road-axis side comparison describes the supplied
axis, which may represent one carriageway rather than the whole street.

The optional `settings` object accepts these diagnostic defaults:

| Setting | Default |
|---|---:|
| `context_distance_m` | 3 |
| `profile_error_m` | 1.5 |
| `half_window_m` | 5 |
| `minimum_extent_m` | 6 |
| `orientation_error_deg` | 25 |
| `side_deadband_m` | 0.5 |
| `axis_distance_m` | 30 |
| `axis_margin_m` | 0.5 |

These values are screening choices, not surveyed accuracy or lane-width
standards. The profile check uses vertices and samples spaced at most 1 m.
Short MAP crossing sections can lack the length needed for a road-strip
comparison. That result does not prove that the crossing is wrong.

`single_geometric_candidate` means that one nearby MAP line meets the geometric
checks. It does not assign the observed strip to that line. Nearby parking and
cycling areas can produce compatible source lines while having different uses.
Source categories can also disagree despite close lines.

Every result retains `assigned_lane_id: null`. Access, travel direction, and
MAP field validity remain unverified. Same-year image and reference records
need not share a capture date. Different-year records can supply context but
cannot establish historical conditions. Revocable bus metadata does not prove
current operation or permanent exclusive access.
