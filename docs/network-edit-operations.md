# Scoped road-network edits

The corridor edit ledger supports the existing six road operations and fourteen
scoped operations. `review_marker` records a review location without changing
the network. A proposed or accepted operation does not establish field accuracy.

Use `build_corridor_edit_ledger` to record the source path, source SHA-256,
operation, rationale, evidence, scope, and rollback. Use
`materialize_corridor_edit_variant` to create a separate candidate and its
review artifacts. The existing CLI workflow and candidate gates use these
same functions. Do not replace the final candidate gates with a successful
materialization result.
Set `include_auto_proposals=False` when building the single-operation scoped
request described by the input schema.

## Operation contract

| Operation | Required parameters | Preconditions and checks |
|---|---|---|
| `join_junctions` | `node_ids`, `join_id` | Existing disjoint nodes, explicit signal policy, preserved source boundary paths |
| `absorb_reciprocal_fragment` | Join parameters and `maximum_length_m` | Exactly two nodes, reciprocal source roads within the stated length limit |
| `suppress_turnaround` | `from`, `fromLane`, `to`, `toLane` | Exact existing connection with a source turnaround direction |
| `set_junction_shape` | `node_id`, `shape` | Valid finite polygon, unchanged outside geometry and fixed remote lane ends |
| `add_lane_connection` | Connection parameters | Existing lanes at one junction, absent exact pair, explicit optional vehicle permissions |
| `delete_lane_connection` | Connection parameters | Existing exact pair, unchanged other ordinary connection pairs and permissions |
| `relocate_tls_control` | `from_node`, `to_node`, `tls_id`, `link_mapping` | Static source controller, uncontrolled target, complete distinct link mapping, preserved program and indices |
| `demote_duplicate_tls` | `node_id`, `retained_node_id` | Existing signal nodes sharing a controller, retained signal node remains controlled |
| `set_lane_permissions` | `lane_id`, `allow` or `disallow` | Only the declared permission attributes may change. No native geometry conversion |
| `set_lane_width` | `lane_id`, `width_m` | Finite positive width, unchanged other lane identities and permissions, fixed outside geometry |
| `set_edge_shape` | `edge_id`, `shape` | At least two finite network XY or XYZ points with consistent dimensions and positive planar length. SUMO rebuilds lane offsets and local connections |
| `set_lane_count` | `edge_id`, `num_lanes` | Positive integer. SUMO rebuilds the changed road and its local connections. Record resulting movement changes |
| `move_stop_line` | `lane_id`, `end_offset_m` | Nonnegative absolute setback. Transfer the saved lane prefix to its internal paths when correspondence is unique. Otherwise retain native reconstruction |
| `set_speed` | `lane_id`, `speed_m_s` | Positive speed in metres per second. Change only that lane attribute without native conversion |

The existing operations remain `add_edge`, `add_sidewalk`, `add_crossing`,
`add_ramp`, `delete_edge`, and `merge_edges`.

Connection additions, deletions, and turnaround suppression freeze ordinary road geometry and existing junction contours before native conversion.
Their source-derived import hints also remove inherited sidewalk and bicycle synthesis defaults.
The evidence validator reconstructs these hints from the source and edit, rather than trusting the saved input.
The restoration step also preserves ordinary lane lengths on roads adjacent to the edited connection.
Those roads belong to the conversion scope, but their geometry is not an intended edit effect.
Connection and control changes retain their separate checks. A geometry-preserving connection edit does not establish field legality.
Protected-green conflict comparisons use directed lane-pair identities, rather than native connection or link indices.
Declared lane remapping preserves those identities across lane-count edits.
New conflict relations and inherited relations have separate lists. Signal timing changes retain their separate preservation check.
Inherited conflicts remain warnings and release findings. Reclassification does not remove a conflict or relax the release checks.

Junction shape edits use native reconstruction. Final automatic qualification
compares that reconstruction with a source-contour identity control. See
[local edit applicability](edit-specific-applicability.md) for the measured
geometry changes and the control's limits.
Explore output also records source-to-candidate lane movement and logical-length
changes under `construction.contour_geometry_changes`. This adds no native run.

## Daily exploration and release

Road shape edits retain supplied elevation. Use XYZ coordinates when the source
road has elevation. The applied-shape check compares ordered XYZ vertices within
2 cm, so a planar match cannot hide lost elevation. Omitted native heights mean
zero. Native vertex resampling may require review. XY-only geometry and
calibration distances remain planar.

Use `astra network verify-edit <ledger.json> <new-output-dir> --mode explore --json` for daily calibration.
Explore is the default for this command and supports one typed operation per candidate.
The ledger needs `source_net_file` and one operation with its `id`, `operation`, and `params`.
Approval, evidence, and declared source hashes are optional during exploration.
Their absence or mismatch produces warnings. The report records actual file hashes.
The complete ledger schema below remains the release input contract.

Explore mode creates a separate candidate and retains the native proposal and construction measurements.
It skips promotion gates and the automatic-profile approval step.
It checks SUMO load, internal driving paths, and 100 original-network route requests.
Requests come from the source alone. Changed candidate endpoints cannot remove them from the test.
Lost routes block the result. Unfinished driving runs remain visible with a warning when routes still exist.
If no original route requests can be validated, exploration retains a warning and makes no route-preservation claim.
Candidate endpoint changes cannot shrink the original request pool.
Retained directed road identities and verified geometry merges can map lane positions to the candidate.
The mapping uses each lane's logical-to-geometric length ratio and checks XYZ positions within 0.02 m.
It does not add the lengths of absorbed roads, which would omit internal gaps and shape-length differences.
The source request file remains unchanged. A separate correspondence record identifies every mapped or unresolved request.
The router must retain explicit endpoint attributes. Driving checks verify mapped nonnegative positions and explicit numeric lane selectors against trip output.
The pinned SUMO 1.27.1 default base position is the smaller of 5.1 m and the original edge length.
An omitted departure-lane selector uses `best_prob`. Mapping checks every possible source passenger lane rather than assuming the first lane.
Default lane selection qualifies the source lane endpoint set. It does not require identical realized lane choices after an edit.
Ambiguous lane sets, unsupported vehicle definitions, and absorbed endpoints without an ordinary-road correspondence remain unverified.
Requests whose endpoint roads still exist receive strict road-level routing probes.
Removed endpoints remain in the complete source and candidate request files and in the correspondence record.
A separate `.routable.trips.xml` file excludes only requests with recorded removed endpoint roads.
It retains the other IDs, departure times and corresponding road OD. It does not establish route existence before the strict native check.
No original request is silently lost, replaced or resampled.
`cohort_coverage` states the original, native-input, physical, logical-only and removed-endpoint counts.
The three qualification counts sum to the full original cohort. `vehicle_count` remains the original count, while `routing_vehicle_count` states the native subset size.
Completed subset probes retain `review_required` and `unverified_endpoints` when endpoints remain unqualified.
Their execution status is `partial` when removed endpoints lie outside the native subset.
If all original endpoints are removed, the audit reports review and `not_run` with the full retained cohort.
Remaining routing or driving failures stay failures. A removed endpoint cannot hide a disconnection between retained roads.
Exploration retains available measurements with warnings. Release qualification requires complete physical endpoint evidence.
`source_baseline` separates checked `clean` or `dirty` diagnostics from `not_run` cases.
These labels describe the synthetic passenger probe, not field quality.
The sampled passenger requests do not establish all-class or all-pair route preservation.
Invalid edit parameters and attempts to overwrite inputs still stop construction.
Other preservation and review findings remain in the report for inspection.

Use `--mode release` when preparing a candidate for publication or promotion.
This runs the existing approval, materialization, and candidate checks.
The three existing automatic profiles retain their narrower eligibility rules.
An expanded junction surface can be explored without qualifying as automatic contour tightening.
Other typed edits use explicit release materialization and the general candidate checks.
An explore result has no promotion authority.
Successful exploration returns `measured`. An unapplied requested effect returns `no_effect`.
Both describe a completed command. Neither status accepts the candidate for publication.

Scoped checks compare source signal programs on retained movements. They check
controller type, offset, phase timing, and the bound signal states with numeric
time normalization. Declared lane mappings and removed movements define the
comparison. Explicit signal edits retain their existing dedicated checks.
`signal_program_preservation` records the result and each changed or unavailable
binding. Explore reports a warning while preserving otherwise valid measurements.
Release checks require preservation for edits that retain source signal control.

Supply `--facts-file <facts.json>` to measure the source and candidate against one frozen fact table.
The output includes both scorecards, `fact_deltas`, and `outside_fact_changes`.
It records numerical differences inside nested coverage metrics too.
The report retains outside geometry drift before restoration and its residual after restoration.
Those records include the count of outside lanes displaced by more than 0.01 m and their maximum shape displacement.
Native command time remains separate from the measurements.

For lane-count edits, `remove_index` removes one selected lane and remaps surviving indices.
`add_side` adds one lane on the left or right. Existing lanes retain their permissions, widths, and speeds.
Without these fields, a reduction removes the highest indices and an increase adds lanes on the left.
`num_lanes` is the total SUMO lane count, including walking and cycling lanes.
Optional `vehicle_class` selects the source lanes that may be copied or removed.
It selects a template. It does not restrict the new lane to that class.
The copied lane retains the template's complete vehicle permissions.
Right insertion precedes the rightmost compatible lane. Left insertion follows
the leftmost compatible lane. Reductions remove the highest compatible indices.
An explicit `remove_index` must serve the selected class when that class is given.
Omitting `vehicle_class` retains the existing physical-index interpretation.

For example, on a road with one sidewalk, one cycle lane, and two passenger
lanes, these parameters add a passenger lane beside the cycle lane:

```json
{"edge_id": "road", "num_lanes": 5, "add_side": "right", "vehicle_class": "passenger"}
```

The default connection policy preserves existing movements of surviving lanes, with their remapped indices.
New lanes copy the template lane's source-supported incoming and outgoing movements.
A removed lane's movements transfer to the nearest surviving lane that allows all their source vehicle classes.
The tool retains existing matching connections when transferred movements share a resulting lane pair.
It does not expand a surviving lane's permissions to accommodate a removed lane.
Copied and transferred paths use newly computed geometry instead of the source lane's custom curve.
Their source driving constraints and signal bindings remain recorded.

Supply an explicit complete `connections` list to replace the default result.
Matching pairs inherit their source or template bindings.
For a controlled movement without such a binding, include `tl` and `linkIndex`, with optional
`linkIndex2`, in its connection record. The controller must serve that junction.
Indices must fit every source program. Missing or incompatible bindings are invalid inputs.
This reuses existing program states. It does not design a new signal program.
Parallel copied turns can introduce protected-green conflicts when they merge into the same destination lane.
Release checks retain these conflicts. A served lane does not establish safe signal control.
`new_lane_service` lists each added lane's compatible ordinary incoming and
outgoing movements. Counts use loaded vehicle-class permissions, including
internal paths. A boundary is unverified when compatible source lanes have
movements there but the added lane has none. Source departure and arrival
boundaries do not acquire requirements that were absent from the source.
Lane-change use, intended auxiliary use, and discharge capacity remain separate.

`road_movement_preservation` compares the original and candidate road-to-road turns by vehicle class.
This comparison does not use the native proposal as its reference.
It lists every lost and added turn, even when the total turn count stays equal.
An undeclared lost turn blocks construction and explore driving because the original fixed route no longer exists.
An explicit connection override declares its omitted turns and retains a release review outcome.
The report still lists these losses. Original traffic requests require separate routing and driving checks.
The [Hamburg lane-service reproduction](../benchmarks/public_network_development_v2/lane-count-service-20261020-v1.md)
retains the successful fixed-turn checks and failed paired corridor runs.

Explore retains measurements and warns about unverified new-lane service.
Release returns `review_required` when construction checks pass but those
boundaries remain unverified. `construction_checks_passed` distinguishes that
case from a definite failure. Hard failures retain `blocked`.
The materialization report, evidence validator, and release command preserve
the review outcome. Gates skipped for that reason remain `not_run`.
An explicit compatible connection list can qualify the static boundary service.
It does not by itself establish traffic benefit or a safe new signal design.
Surviving connections retain their saved custom paths, lengths, speeds, signal
bindings, and supported driving constraints when lane indices change.
An explicit connection list also inherits these values for matching surviving
pairs. Explicit `allow` or `disallow` replaces the inherited permission directives.
Lane-count patches supply existing affected signal programs explicitly. Adding
or removing lanes must not silently replace those programs with generated timing.
The signal file also binds surviving movements to their remapped lane indices.
New controlled movements still need compatible signal bindings and a valid load.
With `--facts-file`, surviving lanes use the declared index mapping in a separate candidate fact table.
Reference geometry and expected physical values stay fixed. Removed lanes retain a missing-lane result.
The report records both table hashes and every changed selector.

`nonmotor_access_preservation` compares dedicated walking and cycling lanes
against their source junction access. It follows lane and junction remapping,
permits declared lane or movement removals, and reports collateral access loss.
Pedestrian access is bidirectional. Walking-area renaming alone is not a failure.
The check also detects a retained sidewalk displaced as the pedestrian model's
selected lane, or a newly duplicated sidewalk. SUMO assumes at most one sidewalk
per edge; see [pedestrian network definitions](https://sumo.dlr.de/docs/Simulation/Pedestrians.html).
Explore retains otherwise valid measurements with warnings. Release checks
require preservation of the retained facilities. This checks explicit access,
not complete implicit pedestrian routing or field legality.

The road shape is the SUMO edge reference line, not one individual lane centerline.
Road-shape and lane-count edits clear custom shapes on the retained target lanes so SUMO can recompute their geometry.

Lane-count edits add source geometry hints for all other ordinary lanes and their saved logical lengths.
They retain existing custom junction contours. Automatically generated contours can adapt, including their walking connections.
The plan records both the original conversion input and the input with geometry hints, with separate hashes.
The materialization manifest includes both files. Verification reconstructs the hints from the source rather than trusting their saved contents.

Native SUMO can still clip hinted lane shapes near junctions.
`neighboring_road_geometry` reports changes on every ordinary road except the edited target.
Its `source_geometry_preserved` flag states whether that comparison is exact. Its status remains `measured` during exploration.
Changes produce a warning for sensor placement. They do not establish accepted field geometry or stop otherwise valid exploratory measurements.
Use the [SUMO PlainXML definitions](https://sumo.dlr.de/docs/Networks/PlainXML.html) for shape, lane count, and lane speed.
Stop-line movement uses a geometric lane `endOffset`, as described in
[SUMO intersections](https://sumo.dlr.de/docs/Simulation/Intersections.html).
It does not configure a separate vehicle-class `stopOffset`.
When saved geometry supports a unique adjustment, this edit avoids netconvert.
It shortens the approach shape and extends its first internal paths by the same spatial distance, or reverses that transfer.
Distance includes elevation, including shapes with omitted zero-Z vertices.
It preserves junction waiting structures, request matrices, connections, and signal programs.
Affected normal roads and straight internal edges use SUMO's common logical length for all their lanes.
The tool averages the transferred saved logical lengths across each affected edge, with SUMO's 0.1 m minimum.
Sibling lanes can therefore change logical length while their shapes remain fixed.
The report records the convention and each affected lane's before and after logical length.
This follows the [SUMO lane-length convention](https://sumo.dlr.de/docs/Networks/SUMO_Road_Networks.html#lanes).
Moving back downstream requires a common saved prefix for every outgoing movement.
Shared paths, missing continuations, divergent prefixes, custom lengths, bidirectional coupling, and multi-lane turning paths use native conversion with an explore warning.
Multi-lane turns can have separate logical lengths in native SUMO. The direct method does not guess their grouping convention.
Release evidence independently reconstructs the direct edit from its source before accepting the materialization record.
The method is `stop_line_edge_mean_3d`. Earlier per-lane `stop_line_geometry_only` records remain diagnostic history and require rebuilding for release.
The construction report lists changed lane coordinates in `lane_coordinate_artifacts`.
Existing detector, stop, and parking positions require a separate check before reuse.
These files are not inputs to this network-only edit. Changed lane bindings receive a `not_checked` result for those files.
A successful network load therefore does not establish that an old simulation configuration still works.

For example, a daily speed edit can use:

```json
{
  "source_net_file": "C:/project/source.net.xml",
  "operations": [{
    "id": "adjust-lane-speed",
    "operation": "set_speed",
    "params": {"lane_id": "road_0", "speed_m_s": 8.33}
  }]
}
```

One scoped operation is materialized per variant. Apply a dependent sequence
through separate source-bound stages and check its complete final network.
An intermediate network does not receive standalone acceptance from the
success of a later stage. Keep every failed intermediate result.

Lane indices are integers. Shapes use network XY coordinates. Geometry edits
declare their junctions and incident roads before conversion. Reciprocal
absorption has no built-in length threshold. Supply the limit for the actual
case. Joining rail-control nodes requires a separate rail operation.

Signal joins use `signal_policy: preserve` by default. Use
`rebuild_diagnostic` explicitly when the construction needs test signals.
`interior_lane_change_policy` defaults to `fixed_paths`. The optional
`permitted_interior` policy checks source permissions inside absorbed roads.
It cannot use an exterior detour or override a source vehicle-class restriction.
Actuated control relocation also needs detector correspondence. The static
relocation operation rejects that case.

Each scoped edit requires a matching ledger `source_sha256`. File evidence
requires an absolute `path` and matching `sha256`. The same files must remain
unchanged through materialization. The
[scoped input schema](../schemas/product/astra.network-edit-ledger.v1.schema.json)
describes the parameters. Runtime checks also verify the source network and
the actual compiled result.

## Example

```json
{
  "schema": "torii.corridor_edit_ledger.v1",
  "source_net_file": "C:/project/source.net.xml",
  "source_sha256": "REPLACE_WITH_SOURCE_SHA256",
  "operations": [{
    "id": "remove-source-turnaround-1",
    "operation": "suppress_turnaround",
    "status": "candidate",
    "target_ids": ["incoming-road", "return-road"],
    "params": {
      "from": "incoming-road",
      "fromLane": 0,
      "to": "return-road",
      "toLane": 0
    },
    "rationale": "The dated reference excludes this exact turnaround.",
    "evidence": [{
      "path": "C:/project/turn-reference.json",
      "sha256": "REPLACE_WITH_REFERENCE_SHA256"
    }],
    "rollback": {"action": "discard_candidate_and_use_immutable_source"}
  }]
}
```

The candidate state records a proposal. Materialization requires an explicitly
accepted operation. Both states remain distinct from final field acceptance.
Replace the identifiers and hashes with the actual evidence. Source node
proximity alone does not establish one physical junction. A source turnaround
direction alone does not establish a field prohibition.

## Preservation and review

Legacy `delete_edge` and `merge_edges` variants now use the shared local native
converter. Their saved native road list determines the source junctions and
incident roads in scope. A merge must preserve source paths through the native
`removedNodeIds` correspondence. Losing a road without a corresponding merged
path fails the inventory check.
Every declared merge target must belong to an actual merged source path.
A native command that merges nothing does not produce a successful variant.
Failed local subsets retain their raw output and an explicit failure role.

Geometry removal must also preserve every absorbed lane continuation and its
loaded vehicle access. A bus-only interior connection cannot disappear into
an unrestricted road. Candidate generation excludes that case. Both local
and full-network materialization reject it, and the shared connection audit
records the access failure. A merged lane must retain the permission set and
lane count of each original road segment.

An isolated deletion retains one extra road in the converter input because
`netconvert` requires a nonempty road network. The merge copies that road from
the source, outside the edit scope. Native input, raw output, and the full
merged proposal remain separate records. Evidence validation reconstructs the
input and merge from the source and command, including the saved road list.

Shared controllers, unsupported prohibitions, missing coordinate frames, or
absence of a retained context road keep a recorded full-network fallback.
Permission reconciliation remains separate from the immutable source.
Legacy additive roads and crossings also use local conversion. The saved plain
inputs determine their new road IDs, source endpoints, crossing junctions and
incident roads. Evidence validation reconstructs that scope and checks every
plain input against its manifest binding. Existing roads cannot be modified
through an additive patch. Guarded full-network fallbacks remain explicit.

Additive conversion rebuilds the edited junctions and their adjacent junctions
in the native input, with complete incident roads and saved internal paths.
This larger computation context supports walking-area generation. Only the
original edit junctions, roads and controllers enter the final candidate.
The record separates `native_rebuild_junction_ids` from the mutable scope.
An outer shared controller can still require a recorded full-network fallback.

For legacy additions, `lanes` maps to native `numLanes` before type-derived
sidewalk and bicycle lanes. SUMO defines `sidewalkWidth` and `bikeLaneWidth`
as [additional-lane attributes](https://sumo.dlr.de/docs/Netedit/attribute_help.html).
An existing dedicated sidewalk prevents another automatic sidewalk, as the
[pedestrian documentation](https://sumo.dlr.de/docs/Simulation/Pedestrians.html) explains.
The construction report separates input lane count, type-derived lanes,
expected total lanes and built total lanes in `added_lane_layouts`.
An unexpected total fails construction. Matching the count does not waive
pedestrian, bicycle, permission or connectivity checks.

The independent corridor-geometry simplifier also uses the shared scoped
converter. It retains its existing candidate and semantic checks, records the
actual native command and local artifacts, and verifies exterior geometry
after source-derived restoration. Explicit full-network fallbacks remain.

Duplicate-road proposals require matching geometry, lane attributes, parameters,
and ordinary movement neighbors after replacing the compared road identity.
Internal paths compare their lane state and geometry instead of generated IDs.
Generated internal indices and native priority states are not identity keys.
Turn directions and declared connection rules remain part of the comparison.
Unknown or branching internal paths do not establish equivalence.
Shared endpoints and lane classes alone do not establish duplicate roads.
The comparison is exact and can leave numerically equivalent spellings unmatched.
Proposals remain review candidates and do not prove a field defect.
They also do not establish equal native right-of-way or authorize deletion.

Signal demotion and static controller relocation use the shared local conversion
path when the complete affected controller stays inside the declared scope.
A controller that spans the edit boundary still requires full-network
conversion. Unrelated signal programs and road records remain source-owned.

Individual road-geometry and connection edits now supply a local network to netconvert.
The input includes every incident road of the edited junctions. It closes any affected roundabout ring.
It keeps explicit [right-of-way prohibitions](https://sumo.dlr.de/docs/Networks/PlainXML.html#right-of-way) whose movement references lie within that input.
Unresolved prohibition references retain full conversion and report the reason.
The candidate copies the exterior from the immutable source and uses native elements inside the declared edit.
Existing roads, junctions, and signal programs retain their source order when their identities survive.
Modified-junction connections retain native order because it defines the replacement conflict table's movement indices.
New elements follow their category's retained elements.
SUMO can produce different seeded trajectories after road reordering, despite equal road attributes and saved connections.
The [real-cohort control](../benchmarks/network_construction_20261016_v1/serialization-order-20261016-v1.md) records this difference.
Typed edits also measure loaded link relations after restoration.
Shared scoped native conversions use coordinate precision 6 unless a caller explicitly supplies a precision.
Boundary round-trip checks and typed geometry commands use the same default.
SUMO rounds input geometry before constructing lane and junction geometry, so this choice affects the candidate.
It does not imply matching physical accuracy. The [precision fixture](../benchmarks/coordinate_precision_20261018_v1/README.md) records the controls and limits.
Other legacy re-import entry points retain their independent settings until migrated and verified.
The worker reads successor and via lanes, turn directions, SUMO controller and link-index assignments, yielding incoming lanes, and conflicting internal lanes through TraCI.
It also reads priority and state for links without signal assignments.
Controlled link colors depend on the active phase and are not compared as road-priority rules.
Edited junctions are compared with their last native conversion. All other junctions are compared with the source.
For a joined junction, the reference comes from boundary reconstruction rather than the initial join.
Direct attribute edits use the source as their reference.
Measurements run without vehicles or simulation steps. They do not verify signal timing, geometry, demand, or field legality.
Only the active program loaded from the network is observed. Other programs, switching plans, and runtime additional files remain outside this check.
The report retains network hashes, observations, errors, and the worker command under a process timeout.
The worker loads frozen network copies and checks its loaded junction and lane inventories against the explicit XML.
Input drift and damaged observation files remain recorded. They cannot produce a passing preservation result.
An unavailable observation never counts as preserved. Explore mode retains the measurements and release warnings.
Release mode requires this comparison to pass. Runtime driving checks remain separate.

The worker also saves each network's complete load warning and error log, with its hash.
`load_warning_comparison` reports exact warning-line counts inherited from the source, added by the candidate, or absent from it.
Repeated lines retain their counts. Failed loads retain their logs and have no complete warning comparison.
Each log record lists native `Error:` lines as well as warnings, so a failed load retains SUMO's actual diagnosis.
The observation-error summary also retains the first native error line alongside the worker exception.
Native subnetworks retain separate logs because their warning totals are not comparable with a complete network.
Identifier changes can change warning text. These differences are diagnostic measurements, not an additional acceptance condition.
This observation uses no vehicles or simulation steps. It does not collect driving warnings or prove that a warning disappeared for the correct reason.
Typed edits, the Hamburg candidate manifest, and the complete workflow report surface added warning counts without changing their acceptance decisions.

Complete Hamburg construction records the same comparison on its final network.
Each edited junction uses the last native output actually adopted for that junction.
Approach splits retain their own reference where a later movement pass does not edit that junction.
Outline-only contour trials do not replace the traffic reference.
The complete workflow also checks earlier context joins against the network before those joins.
Without an earlier context rebuild, it reuses the candidate's exact comparison.
Source-side road renaming follows the declared upstream edge correspondence and matching explicit lane indices.
The comparison does not guess lane identities from an ID prefix.
Missing reference files, changed native hashes, and unresolved correspondences remain `unavailable` with their measurements.
They do not establish preserved traffic relations. Other construction measurements remain available.
A candidate with unavailable preservation evidence reports `review_required`, rather than `pass`.
The existing preservation checks then fix the remote boundary ends and verify the complete candidate.
Reports retain the local input, raw native output, and merged proposal with their hashes.
Release validation independently reconstructs the source-derived input and the merge.
The merged proposal is explicitly marked `before_exterior_restoration`.
It is an intermediate artifact. Remote road ends can still differ until the shared restoration finishes.
Internal ownership uses junction lane references and connected movement paths in separate edge, lane, and junction namespaces.
Legacy ID fallback requires one possible parent. Ambiguous names remain unresolved.

`local_conversion.status` reports `local` or `full_network`.
Typed joins and context joins now use local inputs for the initial join and the boundary-path restoration.
Their records declare absorbed roads and source-to-joined node identities. They retain both native outputs and the joined restoration input.
For typed joins, release validation reconstructs both local inputs and their retained exteriors.
Controller relocation or demotion, shared controllers across the edit boundary, and incomplete bidirectional context retain full conversion.
A prohibition that refers to an absorbed road also retains full conversion. The report states the reason.
Official-part joining and its first boundary reconstruction now use the same local conversion function.
Their reports retain actual native commands, local inputs, native outputs, and complete merged proposals.
The boundary reimport audit still converts the complete candidate to check stability and reports that scope explicitly.
Each official movement compilation pass now uses a local native input and a separate immutable connection patch.
The report keeps every pass's native output and merged proposal before the final restoration.
Contour trials use the same conversion function with a saved baseline for the unchanged exterior.
Their original target inventory and acceptance checks remain in place, including expanded context targets.
Approach reconstruction now uses local inputs for supported lane additions and source-road splits.
Its records declare each added road and every changed endpoint. The merge rejects undeclared roads or endpoint changes.
Existing preservation checks then restore the complete exterior, including remapped upstream boundary paths.
Explicit road lengths are divided by the source spatial split fraction. Source end offsets remain on the downstream section.
The report records actual compiled lane lengths. A lane-count change can change SUMO's mean end offset and logical lane length.
Legacy permission declarations retain their effective classes when copied into plain-XML patches.
Before adding a lane, approach construction checks whether saved source connections already represent the documented transition.
Stable lane sections can cross unrelated side entries. Normal adjacent-lane access stays separate from a driving or capacity claim.
Fragmented references use distinct connected lane paths and retain sampled coverage and offset measurements.
Existing transitions outside the MAP merge footprint remain review findings with signed node-center offsets.

If native conversion fails or the merge raises, the shared converter writes `{prefix}.conversion-record.json` before returning or raising.
The file retains the command, errors, scope, input hashes, and any native output. It also records source drift as a warning.
Failure evidence therefore survives callers that raise, even when netconvert writes no network file.
Shared full-network fallbacks also receive a separate hashed input snapshot. Source drift cannot change the bytes passed to the native command.

Native SUMO 1.27.1 rejects some joins whose target ID is one of the source member IDs.
The same failure occurs with a full-source control. Native join requests therefore require an unused target ID.
Shared native joins also require an unused controller ID. Both names are checked before the command runs.
The diagnostic result counts required motor movements with valid controller and program bindings.
This check applies to the requested signal rebuild, without assuming that every signalized junction in an arbitrary source has full control.
XML inspection, exterior copying, and whole-candidate checks still read the full source.
The native rebuild uses the selected road context. This does not promise constant total runtime for large networks.
Lane-permission comparisons use the network's declared format version.
They follow [SUMO 1.27.1's legacy class rules](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/utils/common/SUMOVehicleClass.cpp),
including inherited restrictions on subway and cable-car classes in older networks.
Explicit class aliases retain their native meaning. These comparisons do not rewrite the source permissions.
Width and native stop-offset checks compare at the command's 12-decimal output precision.
Inherited lane-count patch permissions use explicit classes, so a new plain-XML patch keeps the source network's legacy meaning.
Lane and connection preservation checks both apply the corresponding network-format version.
For surviving movements, connection preservation compares loaded access through the complete internal path.
Before conversion, a separate input copy reconciles connection directives with the source's loaded lane permissions.
This prevents a width edit from opening a restricted turn or closing an open turn.
Explicit connection edits still apply after this preparation. Sources remain unchanged.
Conversion records retain the prepared input and its hash, including full-network fallback inputs when needed.
The legacy Hamburg connection repair and reviewed road correction now use the same permission preparation.
Their reports retain the prepared source hash. The road correction also retains its prepared reload input.
Existing connection-patch and road-preservation checks compare loaded access, including internal-lane restrictions.
The road reload checks both geometry and loaded access. Sources and source-matching profiles remain unchanged.
Workflow normalization and teacher-guided PlainXML export also prepare permission-preserving inputs.
Their reports record each reconciled connection's original directive, rewritten directive, and loaded vehicle classes.
If source references cannot establish loaded permissions, preparation reports that limitation and still allows native repair.
Existing candidate validation remains necessary. Normalization and export reject output paths that would replace their source.
Native conversion can emit a newer file format. Copying older permission text into that format can change effective classes.
Restoration now retains the source header and type definitions. Generated permission declarations keep their native meaning in that source format.
Inherited boundary patches retain source classes in current plain XML. Teacher copies instead follow the receiving network's format.
Join checks also compare effective permissions on every surviving external lane.
These checks preserve the model meaning of permissions. They do not establish field access rules.

Native conversion produces a proposal. The shared preservation code restores
the declared exterior and checks fixed boundary endpoints. Lane or road
identity changes outside the operation stop acceptance. Permission-only edits
use the existing attribute patcher and retain all other source bytes. The
materialization evidence checker independently repeats that byte comparison.
The six existing operations use the same exterior protection. Their road-based
scope includes the endpoints of changed roads and their incident roads.
The other ends of these incident roads stay fixed. The source scope is frozen
before conversion, and the raw proposal remains available for inspection.

The output includes the native proposal, typed plan, source/candidate hashes,
local-effect checks, SUMO load, structural checks, review locations, and the
source-snapshot rollback. The review overlay has no runtime effect. Signal
and protected-semantic changes still need the existing exact candidate-bound
review evidence before promotion. Route completion and fact-table acceptance
remain separate checks.

### Custom movement endpoints

A road edit can move lane endpoints while native import retains an accepted custom internal curve.
The checker uses the existing 0.1 m endpoint tolerance to locate broken paths in the edited junctions.
It blends their endpoint corrections through the saved spatial curve, including elevation.
The repair keeps the input curve's first and last segment directions and uses smooth weights between them.
Before blending, spatial samples lie at most 0.5 m apart and retain the original vertices.
Very short curves receive further samples so their end segments remain separate.
The correction can change the final sample spacing.
Reports give endpoint corrections, direction changes, and correction-to-length ratios.
They also report the largest interior vertex turn before and after repair. Retained end directions can still leave a sharper interior bend.
This does not guarantee alignment with an arbitrarily rotated road or certify curvature and driving performance.
Native conversion then recomputes the paths and junction requests from the original input plus the revised connection shapes.
Accepted width-transition offsets retain their existing treatment.
Conversion uses the source's `junctionLinkDetail`, or SUMO's default value of 5 when it is absent.
An explicitly declared connection length remains a model parameter. The report separates it from geometric length.

The process allows three repair attempts. Every attempt retains its previous proposal, connection patch, command result, and elapsed time.
Each attempt records its output hash. Release validation checks the command and output chain through the final native file.
An unsuccessful command restores the previous proposal for diagnosis. It cannot reuse a stale output as a successful repair.
Remaining endpoint gaps prevent physical acceptance in explore mode. They do not require human approval to retry a new candidate.
`native_rebuild_count` includes these extra calls for local conversion.
Explore output also records their time. Join reconstruction time remains unreported by that native-time total.
These checks establish construction continuity, not field accuracy or traffic performance.

## Lane-length measurements

Hamburg construction now records source and candidate lane-length measures.
It also writes `construction-stage-lane-lengths.json` for every retained `.net.xml` construction output.
The measures do not add a release condition.

Use the [same audit](../plugins/astra-sumo/scripts/audit_network_lengths.py) on other explicit network files:

```powershell
.venv/Scripts/python.exe plugins/astra-sumo/scripts/audit_network_lengths.py outputs/new-length-report.json source.net.xml candidate.net.xml
```

The report retains ordinary-lane differences, custom lengths, end offsets, and measured spatial lengths.
It reports varying internal paths separately. Those paths can have legitimate length differences.
For a custom ordinary road, the expected logical length subtracts the average end offset from the declared road length.
This follows [SUMO 1.27.1's native calculation](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/netbuild/NBEdge.cpp).
Native regression cases check this combination at several offsets.
Bidirectional roads require both partners. Geometry restoration retains native logical lengths for those roads.
The audit records their measurements without inferring an independent custom-length expectation.
Measured geometry means are not mandatory logical lengths or evidence of field accuracy.
The [dated stage-length follow-up](../benchmarks/network_construction_20261016_v1/length-and-correspondence-20261016-v1.md) records the real rebuild and remaining limits.

## Paired driving diagnosis

Use the repository-owned [comparison runner](../plugins/astra-sumo/scripts/compare_driving_cohort.py)
to run one fixed route cohort across a source and its candidates.
The [shared implementation](../plugins/astra-sumo/src/astra_sumo/core/paired_driving.py)
retains every vehicle ID, departure, and route. It does not sample replacement requests.

Save a request with this form. Input paths are relative to the request file.

```json
{
  "reference": "source",
  "networks": {"source": "source.net.xml", "candidate": "candidate.net.xml"},
  "routes": "approved.rou.xml",
  "seeds": [17, 42, 104],
  "horizon_s": 2400
}
```

```powershell
.venv/Scripts/python.exe plugins/astra-sumo/scripts/compare_driving_cohort.py request.json outputs/new-comparison --sumo-binary .venv/Lib/site-packages/sumo/bin/sumo.exe
```

The output directory must be new. Each run retains its native outputs and inspection result.
`comparison.json` retains missing arrivals, failed runs, per-vehicle durations, and input hashes.
The driving horizon also sets the teleport delay, matching the existing diagnostic convention.
The runner uses explicit routed vehicles with numeric departure times. It does not load additional operating files.
Failed or changed-input pairs remain diagnostic records. They provide no performance average.
Command failures have a separate count and return script exit code 2. Their diagnostic records remain available.
A successful synthetic driving comparison does not establish field accuracy.

The runner also saves [native collision records](https://sumo.dlr.de/docs/Simulation/Output/Collisions.html)
with their time, location, type, and vehicles.
It saves [vehicle positions](https://sumo.dlr.de/docs/Simulation/Output/FCDOutput.html) during the last simulated step.
With a one-second step, this record has time `horizon_s - 1`. It is not an exact-time snapshot at the horizon.
Vehicles waiting for insertion have no recorded road position.
New diagnostic runs retain native vehicle-route output for unfinished vehicles as well as edge exit times.
The plan records this output policy and the result binds that file by hash.
An unfinished route does not prove that its remaining edges were crossed. Missing exit times remain unobserved.
The plan states the step length and collision settings. A collision record requires diagnosis before any field-risk interpretation.

Each original request has an observation status. Arrivals retain their exact time since scheduled departure.
Unfinished requests retain the observed time until the horizon and `right_censored=true`.
This time includes insertion waiting. It is a lower bound on the complete request time, not a completed travel duration.
Future requests and failed executions remain separate. Duration deltas apply only to vehicles with two recorded arrivals.
Collision participants have a separate arrival status. With this runner's explicit teleport action, the collider also has a teleport marker.
Affected pairs retain their raw recorded duration difference, but `duration_delta_s` stays null.
This prevents a teleport-shortened trip from appearing as an ordinary duration comparison.
The [Hamburg construction comparison](../benchmarks/network_construction_20261016_v1/README.md) retains measured geometry changes and invalid driving pairs.

## Diagnostic sampling cost

Source-reachable diagnostic sampling builds the passenger connection graph once.
Strong components share destination sets. Method v2 uses loaded endpoint and
internal-lane permissions. It ignores dormant compiled connection directives.
Endpoint roles, sorted choices, and the uniform origin-then-destination rule
stay the same. Requests can change when sumolib's earlier rules disagree with
loaded SUMO permissions. The report records the method version. Historical v1
reports retain their original requests. Native `duarouter` still checks every
selected request. A failed request is never replaced. These synthetic requests
do not establish field demand.

Normalization and plain export require fresh prepared-output paths when turn
permissions need reconciliation. They never replace a prepared input from an
earlier run. If tracing is unavailable, a top-level warning records the gap.
Native repair can continue, but a successful command does not prove preservation.

## Site parameters

Nine legacy adapters now read their identifiers, correspondence tables,
expected phase images, and geometry records from
[`astra_sumo/data/sites`](../plugins/astra-sumo/src/astra_sumo/data/sites/).
The public legacy entry points keep their source-match checks. They do not
select a different city from a nearby identifier.

Each [site-parameter document](../schemas/product/astra.site-parameters.v1.schema.json)
records its identity and extraction source hash. Output metadata records the
exact parameter-file hash. Tuple and frozen-set tags preserve the legacy
comparison types. These records preserve old snapshot assumptions. They do
not become current field facts merely because they moved to JSON.

New generic edits take explicit identifiers and parameters from their ledger.
They do not read these Hamburg defaults. Use the dated road-fact table for
new source correspondence and measured calibration.

Use [local edit applicability](edit-specific-applicability.md) to check an
accepted turnaround removal, contour tightening, or reciprocal absorption
through its own technical domain and the final candidate gates.
