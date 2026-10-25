# Review images and NetEdit

## Before/after images over aerial imagery

`astra review render <request.json> <new-dir> --json` reads the saved networks directly; it needs no NetEdit,
desktop session or `netconvert`. Relative paths resolve from the request file.

```json
{
  "schema": "astra.network-aerial-review-request/v1",
  "source_net": {"path": "source.net.xml", "sha256": "<sha256>"},
  "candidate_net": {"path": "candidate.net.xml", "sha256": "<sha256>"},
  "aerial": {
    "path": "aerial.png", "sha256": "<sha256>", "crs": "EPSG:32632",
    "bbox": [500000, 5800000, 500800, 5800600], "north_up": true,
    "capture_date": null, "capture_year": 2026,
    "authority": "<image provider>", "license": "<licence>", "source_url": "<exact source or WMS request URL>"
  },
  "target_date": "2026-10-15",
  "views": [{"id": "junction", "bbox": [500300, 5800200, 500500, 5800400]}],
  "width_px": 1200
}
```

- `candidate_net`, `aerial` and `target_date` (or `null`) are required; `source_net` is optional. Replace every
  value above with your own; the numbers only show the shape.
- `bbox` is `[min easting, min northing, max easting, max northing]` of the outer image edges in a projected metre
  CRS. The image must be north-up and unrotated. Each of 1 to 24 views must lie inside the image and contain lanes
  of every supplied network. `width_px` is 128 to 4096.
- For a flat map screenshot without known bounds, replace `aerial.bbox` with `aerial.control_points`: at least three
  non-collinear `{"id": "nw", "pixel": [x, y], "lon_lat": [lon, lat], "role": "fit"}` points and extra points with
  `"role": "check"`. `crop_px` selects part of the screenshot. Omit `views` to use the whole image.
- `parking_facilities: {"path": "parking-facilities.json", "sha256": "<sha256>"}` adds building outlines, a P marker
  and entry/exit arrows. `source_label` and `candidate_label` set the panel captions.
- Set `PROJ_NETWORK=OFF` when PROJ network access is enabled; the renderer refuses network transforms.

The result keeps `status: review_required` (exit code 1) with `render_status: pass` when the images are ready.
Outputs: `review.html`, `report.json`, and per view an unmarked crop, overlays and a comparison image.

Colours: cyan lane centrelines and width ribbons; pink internal lanes; orange junction surfaces; yellow and green
crossings and walking areas; grey dashed covered passages, tunnels and bridges in the overview. Arrows follow lane
geometry, not signal phases or right of way.

## Schematic review page

Without georeferenced imagery: `astra review create <new-dir> --net-file <net.xml> --json`, optionally with
`--topology-audit-report-file`, `--routeability-audit-report-file`, `--tls-review-file` and
`--junction-aggregation-report-file`.

## NetEdit

- The user edits: `astra-netedit <net.xml or .sumocfg>` opens the GUI (checkout only; an installed plugin has only
  `run_astra_sumo.py --cli netedit review` and the `netedit` MCP profile).
- One-shot capture: `astra netedit review <source.net.xml> <new-dir> <source-sha256> --json` opens a copy, takes a
  screenshot and closes without saving.
- Agent-driven edit (MCP `netedit` profile, one session at a time): `astra.netedit.open` (`source_net_file`,
  `candidate_net_file`, `output_dir`, `expected_source_sha256`), then `astra.netedit.observe`, then
  `astra.netedit.act` with an `action` and the `expected_screenshot_sha256` from the latest observe. Observe again
  before every further act. Finish with `astra.netedit.close`: `mode=finalize` with the latest SHA saves and
  audits the candidate; `mode=abort` discards it. Actions: `click`, `drag`, `inspect_mode`, `connection_mode`,
  `delete_mode`, `edge_mode`, `move_mode`, `select_mode`, `tls_mode`, `recompute`, `join_selected_junctions`,
  `accept`, `cancel`, `undo`, `redo`.
- Scripted screenshots (checkout only):
  `.venv/Scripts/python.exe plugins/astra-sumo/scripts/netedit_background_review.py --net-file <net.xml> --out-dir <new-dir>`.
  For a before/after pair, use `--capture-modes inspect`, `--inspect-selection none` for the source,
  `--target-junction-id <id>`, `--expected-net-sha256 <sha256>`, the same `--view-center X,Y` and `--zoom` for
  both, and `--aerial-image`, `--aerial-bbox WEST SOUTH EAST NORTH` and `--aerial-crs` (default `EPSG:25832`) for
  a background.

A blue selected junction in a capture does not prove a merge; count the source and candidate junction IDs.
