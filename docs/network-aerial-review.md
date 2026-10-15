# Network review over aerial imagery

Use `astra review render` for routine road geometry review.
It reads the saved network directly and draws it over a fixed aerial image.
It does not require NetEdit, a desktop session, or netconvert.
For manual edits or a specific NetEdit display question, use NetEdit.
Without georeferenced imagery, use `astra review create` for a schematic view.

## Input

Prepare a JSON request that follows the
[request schema](../schemas/product/astra.network-aerial-review-request.v1.schema.json).
Relative input paths resolve from the request directory.
The candidate and aerial image are required. The source network is optional.
Record the actual SHA-256 of each file.

```json
{
  "schema": "astra.network-aerial-review-request/v1",
  "source_net": {"path": "source.net.xml", "sha256": "<64 lowercase hex characters>"},
  "candidate_net": {"path": "candidate.net.xml", "sha256": "<64 lowercase hex characters>"},
  "aerial": {
    "path": "aerial.png",
    "sha256": "<64 lowercase hex characters>",
    "crs": "EPSG:25832",
    "bbox": [565600, 5932900, 566300, 5933400],
    "north_up": true,
    "capture_date": null,
    "capture_year": 2026,
    "authority": "Hamburg LGV",
    "license": "Datenlizenz Deutschland - Namensnennung - 2.0",
    "source_url": "<exact source or frozen WMS request URL>"
  },
  "target_date": "2026-10-15",
  "views": [
    {"id": "junction", "bbox": [565735, 5933085, 565895, 5933245]}
  ],
  "width_px": 1200
}
```

Replace the placeholder paths, hashes, and source URL before use.
The example bounds describe one Hamburg view. Select bounds for your own source.
`bbox` means `[minimum easting, minimum northing, maximum easting, maximum northing]`.
The aerial bounds refer to the outer image edges. The upper-left image corner is the northwest corner.
The renderer samples pixel centers and preserves each view's aspect ratio, subject to integer image dimensions.
Every view must fit inside the aerial bounds and contain lanes from every supplied network.
Use one to 24 views. Image width must be 128–4096 pixels. Computed height must be 64–4096 pixels.

The image must be north-up. Rotated rasters and EXIF-rotated images are unsupported.
Use a projected CRS with metre units for both the image and network.
Missing or unknown network projections block rendering.
Set `PROJ_NETWORK=OFF` when the environment enables PROJ network access.
The renderer rejects network access and ballpark coordinate transforms.
It does not fetch imagery or infer coordinates from a screenshot.

## Run and outputs

```powershell
./.venv/Scripts/python.exe -m astra_sumo.cli review render request.json outputs/review-v1 --json
```

Use a new output directory. Existing directories are rejected.
The renderer verifies all input hashes and checks them again before it writes the final report.
It preserves the input files.

For each view, the output contains:

- An aerial crop without network marks.
- A candidate overlay and an optional source overlay.
- A comparison image with the same bounds, image, and scale in both panels.
- Complete geometry panels and a comparison for tagged structures and their transitions.

`review.html` displays the comparison images and links to the aerial crops.
Expand each structure detail section to inspect the complete geometry and turn curves.
`report.json` records inputs, coordinate frames, views, visible object IDs, image hashes, and renderer versions.
The CLI also returns the report hash.
Artifact filenames in the report resolve from the report directory.
No download or current timestamp enters the render output.
Identical inputs, settings, and rendering dependencies produce identical images and report bytes across output directories.
The report records Python, Pillow, pyproj, PROJ, and Shapely versions.
It also records the renderer source hash. Keep the HTML and source attribution with exported PNG files.
Byte equality across different dependency versions or operating systems has not been established.

The result retains `status=review_required`, `render_status=pass`, and `promotion_authority=false`.
The CLI therefore returns exit code 1 when the images are ready and field review remains required.
Read `render_status` to distinguish that result from an execution failure.
Exceptions can leave a partial directory without a final report. Use a new directory after correcting the input.

## Read the images

Cyan marks ordinary lane centerlines and lane width ribbons.
Pink marks internal lanes. Orange marks junction surfaces.
Yellow and green mark crossing and walking-area geometry.
Arrows follow lane geometry. They do not encode signal phases or right of way.

### Covered roads and structures

The overview draws annotated building passages, tunnels, bridges, covered ways, and layer transitions as gray dashed centerlines.
It omits their filled surfaces and direction arrows in this overview.
It also leaves their internal turn curves to the full detail panels.
The separate structure detail panels retain all saved geometry, including internal turn curves and junction surfaces.
Both views retain the original network and list the same intersecting objects in the report.

The report reads saved edge and lane parameters for `tunnel`, `bridge`, `covered`, `indoor`, `layer`, and `level`.
It follows explicit connection `via` chains to classify internal lanes. It does not use proximity to infer a tunnel.
Mixed endpoints remain transitions. Incomplete chains remain listed as unresolved.
Connections use lane sequence positions. The report lists inconsistent stored lane index attributes without changing them.
Unannotated roads retain their ordinary display. Missing annotations do not prove that a road lies on the ground.

`tunnel=building_passage` identifies a passage under or through a building, often at ground level.
It does not establish a basement road or a negative elevation.
See the [OSM building passage definition](https://wiki.openstreetmap.org/wiki/Tag:tunnel%3Dbuilding_passage).
These roads can be invisible beneath a roof in an aerial image.

The report retains `maxheight` and service-road annotations and lists turnarounds that touch declared structures.
Every such turnaround retains `review_required` until its space and traffic rules have supporting evidence.
The HTML page lists these movements below the images.
These checks do not enforce vehicle height restrictions or delete connections.
Use a dated passage plan, entrance images, or an explicit restriction source to decide those changes.

Every lane is eligible for display, including sidewalks and cycle lanes.
Objects outside a view are clipped to that view.
Lane ribbons use the stored width. Missing widths use the SUMO default of 3.2 m and are counted in the report.
The renderer buffers local lane geometry before coordinate transformation.
These ribbons are a display of the lane model, not surveyed road boundaries.
Polygon holes retain the aerial image underneath.
Invalid junction polygons appear as outlines and are listed in the report. The renderer does not repair them.
Rounded lane shapes can have identical points despite a positive declared travel length.
The renderer displays these lanes as point markers and lists them separately. It does not infer a direction or repair their geometry.
Road ends and internal waiting points can lack a polygon. The report lists these objects separately.

The renderer restores each network's projected coordinates by subtracting its own `netOffset`.
It then uses `always_xy=True` to transform easting and northing into the aerial CRS.
These rules follow the [SUMO network coordinate format](https://sumo.dlr.de/docs/Networks/SUMO_Road_Networks.html#coordinates-and-alignment)
and [pyproj Transformer API](https://pyproj4.github.io/pyproj/stable/api/transformer.html).
The view bounds come from the request. The renderer does not recenter either network to conceal an offset.

## Claim limits

A registered image does not establish positional accuracy of the aerial source.
No surveyed control points or independent registration residuals are available from bounds alone.
The report distinguishes equal exact dates, different exact dates, and unknown temporal alignment.
A capture year alone leaves temporal alignment unknown.
Equal dates do not establish road correctness.

Internal lane curves show saved geometry. They are not a complete connection audit.
Hidden or overlapping curves, elevation, bridge separation, lane permissions, conflicts, and signal logic need their own checks.
Images cannot replace source interpretation, connection checks, protected-state comparisons, or route evidence.
The existing candidate gates and frozen research protocols retain their requirements.

The [Speicherstadt garage example](../examples/05_hamburg_topology/speicherstadt-parking-scope.md)
shows an actual interior deletion after source review.
It records the real facility, retained entrances, exact removed roads, and the unresolved traffic batch.
An OSM tag alone does not establish a road's current existence. A roof can also hide a real access road.
