# Official joins and boundary reconstruction with local native inputs

Status: diagnostic only, 2026-10-16. No field-promotion authority.
The [evidence record](official-local-20261016-v1.json) retains exact artifact hashes and measured scopes.
Construction uses `4ac0db1`; immutable snapshots use `8e70263`. Native SUMO is 1.27.1.

## Conversion scope

The official-part join and its first boundary reconstruction now share the conversion function used by declared context joins.
Each conversion retains a local native input, its raw output, and a complete merged proposal.
The existing restoration then fixes remote road ends and verifies the complete candidate.

| Hamburg stage | Original ordinary roads | Roads given to native conversion |
|---|---:|---:|
| Official-part join | 219 | 35 |
| First boundary reconstruction | 196 | 12 |

The join declares its source-node aliases and absorbed ordinary roads.
The boundary conversion starts from the already joined network and introduces no further road identity change.
The complete-network reimport remains an explicit stability audit.
Its report states `full_network_reimport_audit`; its raw differences and protected restoration remain available.
This preserves the scope of the original audit.
Raw reimport is not lossless: this run records 25 changed edge records, including nonmotor internal edges.
Protected restoration passes afterward. The raw changes remain visible in the evidence record.

## Source preservation and evidence correction

The shared function writes to new files and protects its input.
It retains failed partial native output when conversion fails.
An empty local road context falls back to the full input with a declared reason.
Tests cover multiple join groups and an unrelated retained signal program.

The first complete run exposed a provenance error during evidence curation.
The merged-proposal record pointed at the candidate file, which restoration later changed.
The final driving network was unchanged, but that record no longer described the retained file bytes.
The first result and failed curation log remain under `hamburg-rebuild-v1/` and `curation-v1.log` in the raw output root.

The correction writes an immutable merged proposal separately from the mutable candidate.
Full-input fallbacks also retain an immutable native snapshot.
Tests modify the later candidate and verify that the saved snapshot and its hash remain unchanged.
The repeated complete run independently reconstructs both local inputs and both merges from source and native files.
These comparisons normalize XML namespace-prefix and whitespace differences. Artifact hashes still identify exact retained bytes.

## Complete reconstruction result

The repeated run uses the same original Hamburg request and immutable source files.
It materializes all 16 official vehicle movements with zero fallback parts.
Official connectivity, exterior geometry, movement probes, and corridor routes pass.
Construction coverage and the wider construction audit still require review.

The final network is byte-identical to the prior network `5af15e815d0b8eb56f82873979eed0e7a765599089732a3bc0ef5bd4c9e79b22`.
The [prior fixed-cohort result](local-joins-20261016-v1.md) therefore remains applicable to these exact network bytes.
Its three pairs remain invalid for clean performance evidence.
This change does not resolve the existing mixed-demand failures or establish field correctness.
No new driving rerun or traffic-improvement claim is added.

## Verification and locations

The initial affected-domain run passed 89 checks.
A later run passed 97 construction and navigation checks, followed by two empty-input and failure checks.
The snapshot correction passed 91 affected checks and two focused native snapshot checks.
These runs overlap. Ruff and `git diff --check` pass for the changes.

- Shared native conversion: [`local_network_conversion.py`](../../plugins/astra-sumo/src/astra_sumo/core/local_network_conversion.py).
- Official-part composition: [`hamburg_aerial_corridor_candidate.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_aerial_corridor_candidate.py).
- Boundary reconstruction and full reimport audit: [`boundaries.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_junctions/boundaries.py).
- Context consumer: [`groups.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_junctions/groups.py).
- Checks: [`test_local_network_conversion.py`](../../tests/test_local_network_conversion.py) and [`test_hamburg_junction_boundaries.py`](../../tests/test_hamburg_junction_boundaries.py).
- Scenario: `outputs/claude_followup_20261015_v1/hamburg-request.json`.
- Runner: `astra hamburg build-network <request.json> <new-output-dir> --json`.
- Raw results: `outputs/official_local_20261016_v1/hamburg-rebuild-v2/`.

Movement compilation, approach reconstruction, controller changes, and the documented incomplete-context cases remain separate conversion work.
This is not a total workflow speed benchmark. Complete-model checks and the full reimport audit remain in the workflow.
