# Scoped road-network edits

The corridor edit ledger supports the existing six road operations and ten
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

The existing operations remain `add_edge`, `add_sidewalk`, `add_crossing`,
`add_ramp`, `delete_edge`, and `merge_edges`.

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
