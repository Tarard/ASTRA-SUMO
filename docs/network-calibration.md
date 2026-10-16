# Road-network calibration

Compare each road fact with a dated, hash-bound source. Keep geometry, lanes,
movements and control as separate results. Unknown facts never count as matches.

## Explore daily, release at publication

Daily calibration uses `--mode explore` by default.
It measures tentative associations and retains their confidence and method.
It reports stale source hashes as warnings and records the actual source hashes.
Missing references remain unknown. They do not prevent other facts from being measured.
A malformed fact receives an unknown result with its reason in explore mode.
Invalid network identifiers or coordinate declarations still stop a comparison that cannot be interpreted.

Use `--mode release` for the final reference check.
Release mode requires matching source hashes, confirmed correspondence, and declared acceptance thresholds.
The construction workflow requests release mode explicitly when its scorecard contributes to promotion.
Neither mode grants promotion authority by itself.

Explore mode returns `measured` when the comparison completes.
Exit code zero means the command completed, not that the network matches the field.
Each layer includes `measured_facts` and `provisional_facts` alongside matches, mismatches, and unknown results.
Facts without acceptance thresholds and with a primary value have status `measured`.
`measured_facts` excludes centerlines without a lateral value.
Motor-vehicle summaries separate all measured lanes from high-confidence lanes and list the worst five.
Tentative matches retain `mapping_status: unresolved` and a confidence label.
The explore matched fraction uses only facts with an evaluated acceptance rule.
It is null when no acceptance rule was evaluated.

```powershell
astra network calibrate candidate.net.xml facts.json scorecard-v1.json --parent-net-file parent.net.xml --mode explore --json
```

The parent is optional. Both networks use the same fact table, source files and
coordinate declaration. The command writes a new report and preserves its inputs.
Use a new report name for each comparison.

In the Hamburg MAP/aerial construction request, supply `road_facts` with the
table's `path` and `sha256`. Construction then saves
`road-reference-scorecard.json`, compares the same source and candidate against
that table, and retains its review or blocked status in the construction
decision. Demand calibration remains a separate result. For drawing-led
construction, run the comparison command on the resulting network.

The reproducible acquisition and diagnostic runner is
[`rebuild_hamburg_calibration_baseline.py`](../plugins/astra-sumo/scripts/rebuild_hamburg_calibration_baseline.py).
It resolves the current 2349/2394 official file revisions and saves their exact
bytes before constructing the OSM baseline. Set the bounds, target snapshot
date and numerical sampling interval explicitly. Supply `--source-osm` to reuse
frozen OSM bytes. The runner keeps automatic lane associations unresolved.

## Fact table

Use `schema: astra.network-facts/v1`, a `site_id`, a `target_date`, a
`coordinate_system`, a `sources` list and a nonempty `facts` list.
The [input schema](../schemas/product/astra.network-facts.v1.schema.json)
defines the saved format. Kind-specific selector and geometry checks run during
evaluation.

Each source requires `id`, `path`, `source_date`, `authority` and `license`.
Supply `sha256` for release. Explore mode can compute it when absent.
Resolve source paths relative to the fact table. Set `source_date` to
null when the publication date is unknown. A download date does not establish
the field-validity date. Keep that uncertainty in the source record.

Each fact requires a unique `id`, `kind`, `selector`, `expected` and `source_ids`.
Set `expected` to null when the reference fact is unknown. Use
`mapping_status: unresolved` when its correspondence to network objects is
uncertain. Keep such facts in the denominator of known reference facts.
Confirmed mappings require independent source review; a nearest-lane guess is
insufficient.
Use `mapping_confidence` and `mapping_method` to describe an automatic association.
High-confidence automatic bindings remain usable for exploration without becoming confirmed field correspondence.
The Hamburg adapter checks whole-lane error and neighboring lane indices before assigning its calibration confidence.
`high` requires a mean lateral offset at most half the lane width and an alternative margin of at least 1 m.
A better neighbor produces `conflict`, with its measured alternative recorded. It does not change the frozen association.
These are diagnostic modeling choices, not calibrated confidence probabilities.
Crossings use crossing objects. Cycle lanes use dedicated bicycle lanes with distinct assigned identities.
Absent nearby compatible objects remain unmodeled, with the nearest candidate distance recorded when available.
Nonmotor association measures perpendicular offset over compatible longitudinal overlap.
Missing tails reduce coverage instead of increasing lateral distance.
Candidates within the existing 2 m lateral limit use cost `lateral_m + 2 * (1 - reference_coverage)`.
This favors greater coverage at equal offset and preserves the one-to-one assignment.
The report records this rule and each eligible candidate's coverage.
These associations remain low-confidence guesses. A short overlap does not establish whole-lane correspondence.
The [dated Hamburg follow-up](../benchmarks/network_construction_20261016_v1/length-and-correspondence-20261016-v1.md) records three recovered bicycle measurements and the remaining unknowns.

| Kind | Selector | Expected value | Measurement |
|---|---|---|---|
| `lane_centerline` | `lane_id` or `lane_ids` | Ordered reference points | Lateral offset over longitudinal overlap, plus reference coverage |
| `junction_surface` | `junction_id` | Reference polygon points | Intersection over union and symmetric difference, square metres |
| `lane_count` | `edge_ids`, `vehicle_class` | Nonnegative integer | Absolute count error |
| `lane_permissions` | `lane_id` | Allowed SUMO vehicle classes | Exact allowed-class comparison |
| `movement_inventory` | `incoming_lane_ids`, optionally a declared junction-path scope | List of `[from_lane, to_lane]` pairs | Precision, recall, extra movements and missing movements |
| `stop_line` | `lane_id` | Reference point | Signed longitudinal and lateral errors, plus Euclidean distance, metres |
| `signal_group` | `lane_pair` | `tls_id` and `link_index` | Exact comparison with an evidenced group-to-link binding |
| `speed` | `lane_id` | Positive speed in metres per second | Absolute speed error in metres per second |
| `lane_width` | `lane_id` | Positive width in metres | Absolute width error in metres |

Use one approach cross-section for lane counts. Adding consecutive road sections
would count the same physical lanes twice. An official signal-group identifier
is not automatically a SUMO link index.

For geometric facts, use `coordinate_system: {"type": "projected_crs", "crs":
"EPSG:25832"}` or another projected CRS with metre units. The evaluator removes
the SUMO network offset before projection. For local test coordinates, use
`{"type": "network_xy_m", "location": {...}}` with the exact network `location`
frame. The offset must match numerically and the projection must be equivalent.
Changes in recorded bounds do not change the frame. The report retains the actual location attributes.
Missing or inconsistent coordinate frames stop the comparison.

Centerline facts require `sample_spacing_m`.
Equal-length intervals supply midpoint samples for coverage and lateral offset.
A sample has longitudinal coverage when its perpendicular foot falls on a selected lane segment with compatible local alignment.
The alignment limit is 45 degrees, independent of the lateral acceptance tolerance.
Declare `reference_direction` as 1 or -1 for directed lane facts.
Hamburg ingress geometry uses -1 and egress geometry uses 1.
Opposite-direction segments do not contribute coverage when this direction is declared.
Missing tails and gaps reduce coverage. They do not increase the lateral offset.
No overlap yields zero coverage and null lateral offsets.
Coverage has finite sampling resolution. It is not an exact continuous overlap measure.
The result records the method, spacing, covered length, and uncovered length.
`coverage_within_m` reports coverage inside 1 m and 2 m bands, plus the declared `tolerance_m` band.
Acceptance uses coverage inside the declared tolerance band.
The old full-reference distance fields remain diagnostic values for comparison with earlier reports.
Those distances include missing tails and are not lateral errors.
One official lane may correspond to several compiled SUMO segments. Declare
all of them with `lane_ids`. The evaluator keeps their separate geometry and
does not draw an invented line across a missing segment.
The Hamburg adapter extends a supplied association along saved directed lane connections when those parts add reference coverage.
It keeps that association tentative.
For vehicle lanes, it saves ordinary lane IDs with `include_internal_paths: true`.
The evaluator resolves each connecting internal path in the network being measured.
This avoids reusing internal IDs that a geometry conversion has reassigned.
Each ordinary-lane pair must have one complete saved path. The report records the resolved IDs.
Nonmotor assignments require the correct object type and a distance within the recorded 2 m association policy.
Do not treat these guesses as independent validation of the network.

Set `tolerance_m` explicitly for centerlines and stop lines. Set `minimum_iou`
explicitly for surfaces. Without an acceptance threshold, the evaluator reports
measurements. Release mode leaves the fact unresolved. It supplies no field tolerance.
For centerlines, `minimum_coverage` defaults to full sampled coverage when an acceptance tolerance is supplied.
Set it explicitly for a different comparison extent.
For speeds, supply `tolerance_m_s`.
Lane widths use `tolerance_m`. The Hamburg adapter reuses the construction parser for MAP centimetre widths.
It applies cumulative `dWidth` changes through an explicit stop-line node.
Without such a node, width deltas leave the stop-line width unknown.
These facts describe width at the stop line. They do not establish a constant width over the reference lane.
Use `maximum_mean_lateral_offset_m` for a mean-offset target.
For stop lines, `tolerance_longitudinal_m` can test the longitudinal component separately.
A positive longitudinal error places the SUMO lane end downstream of the reference.
Increase its absolute `endOffset` to move it upstream.
A negative error requires extending the lane geometry or changing the junction shape.

The basic movement inventory covers direct external-lane connections from every declared incoming lane.
Add `junction_ids`, `outgoing_lane_ids`, and `vehicle_class` to compare paths inside an explicit compound-junction scope.
The scoped comparison follows saved connections and class permissions through internal lanes and roads between the declared junctions.
It cannot use an outside detour. Hamburg inventories separate motor, bicycle, and pedestrian movements.
An empty precision or recall denominator returns null. Stop-line geometry
does not establish vehicle-class stopping offsets or actual stopping behavior.
Pair presence does not establish mode permissions, complete internal paths or
protected-green safety. Keep the corresponding independent audits.

## Report and comparison

Each layer reports matched facts, known reference facts, unknown results and
mismatches. Layers with no known reference facts have a null matched fraction.
In release mode, the fraction measures established matches among known reference facts. A zero
with unresolved correspondence does not establish poor field accuracy.
The report binds the network, table and sources to SHA-256 hashes.
Data validation errors remain attached to individual facts in explore mode.
Unexpected programming errors propagate, so they cannot silently become unknown facts.

A parent comparison lists each fact's status transition and numeric metric
change. Review each metric's direction: smaller offsets are better, while higher
precision, recall and intersection over union are better. No weighted total
allows an improvement to hide a different fact's regression.

In release mode, `pass` means every supplied fact matched its declared reference and threshold.
`review_required` preserves unresolved facts and mismatches. An entirely unknown
reference table returns `blocked`. These results do not establish traffic
safety, full-site completeness or field correctness.

## Method basis and limits

The implementation adapts separate positional and connection comparisons to
the fact table requested for ASTRA. It does not reproduce an existing map
inference benchmark.

- Goodchild and Hunter, *A simple positional accuracy measure for linear
  features*, IJGIS 11(3), 299–306 (1997),
  [doi:10.1080/136588197242419](https://doi.org/10.1080/136588197242419).
  This paper compares line geometry against a more accurate reference using
  distance buffers. ASTRA retains explicit reference identity and metre units.
  Its sampled mean and maximum are a separate implementation choice, not that
  paper's buffer-coverage statistic.
- [SUMO intersections](https://sumo.dlr.de/docs/Simulation/Intersections.html)
  defines lane endings and stopping offsets. ASTRA compares the compiled lane
  end and keeps behavioral stopping offsets outside this metric.

Independent source correspondence and field-validity dates remain necessary.
Synthetic tests validate calculations and input checks, not real-site accuracy.

## Preserve geometry during a scoped edit

A compiled SUMO network is not a lossless authoring format for `netconvert`.
In SUMO 1.27.1, `NBEdge::computeEdgeShape` clips custom lane shapes at junctions.
The compiled file does not retain every per-edge junction cut border.
Setting `customShape` and pinning the stored length therefore requires an
independent comparison after conversion. See the versioned
[SUMO implementation](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/netbuild/NBEdge.cpp)
and [SUMO importer](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/netimport/NIImporter_SUMO.cpp).

For an edit to lane permissions alone, the OSM access correction changes only
`allow` and `disallow` in the selected lane opening tags. It preserves the other
bytes and checks the parsed network again. Both the original import and the
candidate must load in SUMO. Newly allowed vehicle classes require a separate
connection review. The correction does not create missing movements.

For geometry changes, declare mutable junctions and roads before conversion.
The native conversion produces a proposal. Restore the network outside that
scope from the immutable source after the last conversion. A road with one
mutable end must keep its other lane endpoint at the original coordinate.
The boundary restoration preserves proposed lane permissions and keeps the
source custom-shape markers. Unknown lane correspondence or missing geometry stops acceptance. A local lane
addition can split its original road. Declare `boundary_edge_map` from the old
road to its retained upstream section. The fixed cross-section keeps the source
lane indices and lane count. An additional lane at that fixed cross-section
still stops acceptance.

The Hamburg movement construction applies this final restoration after contour
conversion. It then compares external lane geometry, stored lengths, widths,
junction positions, junction shapes, road endpoints, and the coordinate frame.
It also checks the fixed lane endpoints on mutable boundary roads.
A later conversion requires another restoration and comparison.
Supported road-shape, width, lane-count, junction-shape, and connection edits now use a local native input.
They still restore remote road ends and verify the complete candidate.
Typed and context joins also use local inputs for joining and boundary-path restoration.
Controller reconstruction, shared controls across the edit boundary, and incomplete bidirectional context retain full conversion.
Other full-construction stages retain their existing conversion paths.
See [typed edit preservation](network-edit-operations.md#preservation-and-review) for retained inputs and current limits.
An explicit stop-line adjustment can avoid that conversion when the saved approach and internal paths support a unique prefix transfer.
Its byte comparison preserves unrelated junction waiting structures and signal definitions.
Other geometry operations retain native conversion and exterior restoration.

The reproducible comparison entry points are:

- [`probe_permission_geometry_preservation.py`](../plugins/astra-sumo/scripts/probe_permission_geometry_preservation.py)
  compares the direct edit, native conversion without an edit, native conversion
  with the same edit, and conversion with custom-shape hints.
- [`probe_scoped_lane_width.py`](../plugins/astra-sumo/scripts/probe_scoped_lane_width.py)
  tests a declared lane width with fixed remote endpoints.

Each command requires new output directories and preserves its source files.
The reports include input hashes, exact scope, conversion results, geometric
changes, and SUMO load results. Width probes are synthetic preservation tests.
Neither a load result nor unchanged outside geometry proves field accuracy,
complete internal connections, or traffic safety.

For each construction stage, protect its immediate immutable parent. Keep that
stage's declared road correspondence with its report. A later movement stage
must preserve the lane addition that an earlier stage already checked.
Junction joins retain the source boundary paths and require an explicit policy
for permitted lane changes inside absorbed roads. This policy cannot use an
outside detour or override a vehicle-class restriction.

A complete Hamburg diagnostic can declare `route_node_order`, for example
`["2349", "2394", "2403"]`. Each member must be a declared MAP or context node.
The route checks bind context construction to the source hash. They test both
directions across the complete order. Declare the actual road names across the
end junction, including `Brooktorkai` when the mainline continues there.
Generic signal programs apply only to rebuilt target junctions. Routing uses
SUMO and duarouter from the explicitly selected SUMO installation.

The no-edit reload audit retains the raw conversion and its changes. It checks
a separate restored copy against the complete source network. A geometry
change in the raw copy remains visible. An unexplained lane or road identity
change still blocks the protected result. This check does not promise a
lossless unprotected native reimport.
