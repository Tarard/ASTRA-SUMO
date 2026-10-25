# Local joins, format preservation, and complete Hamburg reconstruction

Status: diagnostic only, 2026-10-16. No field-promotion authority.
The [evidence record](local-joins-20261016-v1.json) identifies exact sources, candidates, requests, checks, and raw results.
Native SUMO is 1.27.1. Join construction uses `caf2ddd`; signal checks use `de53ba8`; format preservation uses `721a5eb`.

## Implemented scope

Typed joins and declared context joins now provide local inputs for both native conversion steps.
The first step joins the nodes. The second restores source-supported boundary paths and geometry.
Inputs retain complete incident roads, relevant internal paths, affected roundabout rings, and applicable right-of-way declarations.
The merge declares exactly which ordinary roads disappear and which source nodes map to the joined node.
It restores the exterior and checks the complete candidate afterward.

Typed release validation reconstructs each local input and its merge from retained source and native files.
The joined input to the second step is immutable. The final candidate no longer overwrites that evidence.
Context construction retains the equivalent artifacts and scope counts.

| Real case | First conversion | Boundary restoration |
|---|---:|---:|
| Hamburg context, 16 source nodes joined | 27 of 238 ordinary roads | 8 of 219 roads |
| MoST, one selected node pair | 9 of 4404 ordinary roads | 7 of 4402 roads |

The reduced denominators after joining reflect absorbed ordinary roads.
The Hamburg context absorbs 19 roads. The MoST case absorbs two.
These are conversion scopes, not measures of field grouping accuracy.
Whole-model parsing, copying, and verification remain expensive.
The recorded concurrent runs do not establish total workflow acceleration.

## A failed full-source control exposed a format bug

The MoST source declares file format 1.3. Native conversion writes format 1.20.
The earlier full-source control restored older permission text while retaining the newer header.
An element comparison found only 30 extra type declarations in the local candidate.
Both candidates initially passed the available technical checks.

That comparison missed the meaning of the text under each format.
Version-aware decoding then found **12,676 lane permission differences** between the candidates.
The old full-source result is therefore invalid evidence of semantic preservation.
Its files and reported pass remain retained as a failed control.

The correction uses one permission-translation rule for grafted elements, plain-XML boundary patches, restoration, and replayed teacher fragments.
Same-source restoration retains source format metadata and type definitions.
Generated declarations preserve their native effective classes in that format.
A teacher fragment instead follows its receiving network's format.
Join checks now compare permissions on every surviving external lane.

The repeated MoST experiment uses the same source and the same preselected operation on road `-152135#0`.
The selection rule chooses the first eligible uncontrolled passenger road from 1 to 30 m in lexical order, before materialization outcomes.
The new run has:

- zero canonical element differences between local and full candidates;
- equal header attributes;
- zero effective permission differences across all shared lanes, including internal lanes;
- zero changed effective permissions on source lanes retained by each candidate;
- passing technical construction checks and unchanged source hashes.

Canonical element equality does not prove identical trajectories under arbitrary demand.
The experiment is a technical perturbation, not a verified correction of Monaco roads.

## Native naming and signal-control limits

SUMO 1.27.1 cannot read a join patch that reuses an existing node ID as its target.
The retained full-source reproduction fails in the same way as the local input.
Requests now reject that unsupported name before conversion.
Diagnostic joins also require a fresh controller identity because a collision can silently remove control.
The result reports how many source-supported motor movements have valid controller, program, and link bindings.
This requirement applies to the explicit diagnostic rebuild. It does not assume full control at every arbitrary source junction.

The complete Hamburg context has **35 required movements and 35 valid control bindings** after rebuilding.
Historical signal timing and nonmotor control remain separate questions.

## Complete Hamburg result

The unchanged request and immutable source set were rebuilt from the start.
The final model materializes all 16 official vehicle movements, with zero fallback parts.
Exterior geometry, movement probes, and corridor routes pass.
Construction coverage and the broader construction audit remain `review_required`.
All retained stage networks have zero ordinary roads with unequal logical lane lengths.

The new and previous final networks have the same canonical elements and effective lane permissions.
The final hash changes from `4ad06c08...` to `5af15e81...`.
The only header difference is `avoidOverlap="0"` versus its absence.
The pinned [SUMO importer](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/netimport/NIImporter_SUMO.cpp)
uses false when that attribute is absent. This is source-code correspondence for the pinned version, not a universal compatibility claim.

The fixed original 100 routed requests retain their paths, departures, and seeds.
Each run uses a 2400 s horizon, one-second steps, junction collision checks, and a 2400 s teleport delay.

| Seed | Previous arrivals | New arrivals | Collisions in each | Teleports in each |
|---|---:|---:|---:|---:|
| 17 | 64/100 | 64/100 | 1 | 1 |
| 42 | 100/100 | 100/100 | 1 | 1 |
| 104 | 60/100 | 60/100 | 0 | 0 |

The terminal counts and last-step vehicle positions match in each pair.
All three pairs remain invalid for clean performance comparison. They contribute no performance average.
The new conversion scope does not resolve the existing mixed-demand failures or establish field accuracy.

## Verification and retained failures

Separate focused runs passed 134 control and construction checks, 99 format checks, 111 affected-caller checks,
9 receiver-format checks, 306 shared-restoration checks, and 51 final format and navigation checks.
These runs overlap. They do not constitute a new local full-suite result.
Native tests include permission transfers among formats 1.2, 1.3, and 1.20 and a new restricted connection in a legacy-format candidate.
Ruff and `git diff --check` pass for the changed code.

One final-run native fixture took 3270 s, while the same case later passed in 4.47 s.
Its cause remains unresolved. Small fixtures now allow at most three connection retries per startup attempt.
This changes test startup handling, without changing model inputs or simulation horizons.
The slow result remains recorded. No timing from it supports a simulation-performance claim.
Earlier development failures and the invalid MoST control remain under the raw output root.

## Verified locations and remaining work

- Native input, merge, and reconstruction: [`local_network_conversion.py`](../../plugins/astra-sumo/src/astra_sumo/core/local_network_conversion.py).
- Join construction: [`network_edit_operations.py`](../../plugins/astra-sumo/src/astra_sumo/core/network_edit_operations.py), [`junction_boundary_rebuild.py`](../../plugins/astra-sumo/src/astra_sumo/core/junction_boundary_rebuild.py), and [`groups.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_junctions/groups.py).
- Permission translation and restoration: [`osm_access.py`](../../plugins/astra-sumo/src/astra_sumo/core/osm_access.py) and [`restoration.py`](../../plugins/astra-sumo/src/astra_sumo/core/junction_rebuild/restoration.py).
- Driving runner and observations: [`compare_driving_cohort.py`](../../plugins/astra-sumo/scripts/compare_driving_cohort.py) and [`paired_driving.py`](../../plugins/astra-sumo/src/astra_sumo/core/paired_driving.py).
- Regression checks: [`test_local_network_conversion.py`](../../tests/test_local_network_conversion.py), [`test_legacy_lane_permissions.py`](../../tests/test_legacy_lane_permissions.py), and [`test_junction_boundary_rebuild.py`](../../tests/test_junction_boundary_rebuild.py).
- Scenario: `outputs/claude_followup_20261015_v1/hamburg-request.json` and `outputs/local_join_20261016_v1/hamburg-paired-request-v1.json`.
- Raw reconstruction and controls: `outputs/local_join_20261016_v1/hamburg-rebuild-v2/`, `most-join-v2/`, and `hamburg-paired-v1/`.

Official-part joining, approach reconstruction, and other full-construction stages still have whole-network conversion work.
Controller relocation and demotion retain full conversion, as do the documented incomplete-context cases.
Audit field precision, source-dated labels, joint lane correspondence, and mixed-demand diagnosis remain open.
Generated networks and upstream MoST files remain local. The evidence record freezes hashes and selected measurements.
