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
| `set_edge_shape` | `edge_id`, `shape` | At least two finite network XY points with positive line length. SUMO rebuilds lane offsets and local connections |
| `set_lane_count` | `edge_id`, `num_lanes` | Positive integer. SUMO rebuilds the changed road and its local connections. Record resulting movement changes |
| `move_stop_line` | `lane_id`, `end_offset_m` | Nonnegative absolute setback from the road end. The remaining driving length must stay positive |
| `set_speed` | `lane_id`, `speed_m_s` | Positive speed in metres per second. Change only that lane attribute without native conversion |

The existing operations remain `add_edge`, `add_sidewalk`, `add_crossing`,
`add_ramp`, `delete_edge`, and `merge_edges`.

## Daily exploration and release

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

Supply `--facts-file <facts.json>` to measure the source and candidate against one frozen fact table.
The output includes both scorecards, `fact_deltas`, and `outside_fact_changes`.
It records numerical differences inside nested coverage metrics too.
The report retains outside geometry drift before restoration and its residual after restoration.
Those records include the count of outside lanes displaced by more than 0.01 m and their maximum shape displacement.
Native command time remains separate from the measurements.

For lane-count edits, `remove_index` removes one selected lane and remaps surviving indices.
`add_side` adds one lane on the left or right. Existing lanes retain their permissions, widths, and speeds.
Without these fields, a reduction removes the highest indices and an increase adds lanes on the left.
The default connection policy preserves existing movements of surviving lanes, with their remapped indices.
New lanes need an explicit complete `connections` list when they should serve additional movements.
The tool does not guess these movements. The report lists added and removed ordinary lane connections.
With `--facts-file`, surviving lanes use the declared index mapping in a separate candidate fact table.
Reference geometry and expected physical values stay fixed. Removed lanes retain a missing-lane result.
The report records both table hashes and every changed selector.

The road shape is the SUMO edge reference line, not one individual lane centerline.
Road-shape and lane-count edits clear custom shapes on the retained target lanes so SUMO can recompute their geometry.
Use the [SUMO PlainXML definitions](https://sumo.dlr.de/docs/Networks/PlainXML.html) for shape, lane count, and lane speed.
Stop-line movement uses a geometric lane `endOffset`, as described in
[SUMO intersections](https://sumo.dlr.de/docs/Simulation/Intersections.html).
It does not configure a separate vehicle-class `stopOffset`.

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
