# Hamburg calibration follow-up, 2026-10-16

This record freezes diagnostic measurements from SUMO 1.27.1.
It does not accept a network for publication.
[`evidence.json`](evidence.json) records artifact hashes, all 21 motor-lane measurements, and the failed driving trial.
Paths under `outputs/` locate retained local runs. They are not bundled with this record.

The full test suite reports 3,161 passed and 24 skipped in 497.92 seconds.
Skipped tests require historical local inputs that are not present.
Changed Python files pass Ruff. The change passes `git diff --check`.

## Calibration and edits

Daily calibration measures tentative lane associations and reports their confidence.
Release checks still require confirmed correspondence and matching source evidence.
The four requested edits are `set_edge_shape`, `set_lane_count`, `move_stop_line`, and `set_speed`.
Their tests exercise real native SUMO construction, source preservation, and measured effects.

The revised measurement separates lateral offset from reference coverage.
It also reports coverage within 1 m, 2 m, and any declared tolerance.
Directed comparisons exclude opposite-direction lanes.
Saved ordinary-lane connections resolve internal paths separately in each candidate.
Vehicle lanes, cycle lanes, and crossings retain distinct correspondence rules.

Lane-count edits preserve surviving physical identities through an explicit index mapping.
New movements require an explicit connection list.
Edit reports compare frozen reference facts before and after construction.
MAP width facts reuse the existing cumulative-width parser at explicit stop-line nodes.

## Real-site results

Both sites now produce motor-lane measurements: 10 lanes at 2349 and 11 at 2394.
These remain automatic associations with unresolved field correspondence.
The complete Hamburg rebuild reproduces the earlier network hash:
`5bf18df7329c7eddf3dd2c394baadc24c9e023f1712a7336c6e4341551eb509a`.
Its construction manifest retains its `review_required` result.

Including saved internal paths gives these source-network measurements:

| Official lane | Mean lateral offset | Reference coverage |
|---|---:|---:|
| 2349 lane 2 | 2.560 m | 98.7% |
| 2349 lane 3 | 2.815 m | 89.5% |
| 2349 lane 9 | 0.280 m | 100.0% |

These values expose a real positional mismatch on approach 1.
They do not establish a correction for its entire road cross-section.
Unknown source dates and unmodeled nonmotor objects remain visible.

## Stop-line trial: useful measurement, rejected candidate

The trial sets `554713076#0_2` to an absolute `endOffset` of 4.73 m.
Its signed longitudinal error changes from +4.73484 m to +0.00494 m.
Its lateral error remains about -2.818 m.
No outside fact changes. The candidate loads, retains its saved driving paths, and routes the same 100 requests.

The dynamic result does not pass:

| Network | Arrivals by 2400 s | Collisions | Teleports |
|---|---:|---:|---:|
| Source | 100/100 | 1 | 1 |
| Stop-line candidate | 62/100 | 0 | 0 |

The source is already invalid for a clean driving comparison.
The candidate leaves 37 vehicles running and one waiting for insertion.
The report returns `measured` with a runtime warning. It grants no promotion authority.
Keep both failures. A smaller geometric error does not establish valid vehicle movement.

## Recovered public-model input

The MoST network revision contains its prepared input under `tools/static/most.raw.osm`.
The later repository layout had obscured this same-revision pairing.
The pinned [upstream revision](https://github.com/lcodeca/MoSTScenario/tree/d00e82945f5c6322a98ad2df4a8d5f48274fca53)
supplies the configuration, elevation settings, and type maps.
The retained files pass both Git blob identity and SHA-256 checks.

An unjoined import with those settings gives 1,559 known model targets under the existing exact correspondence rule.
They comprise 657 junction-membership targets and 902 connection targets.
The latter include 820 retained, 80 added, and two removed connections.
Another 9,410 records retain unresolved correspondence.
These are published model differences. Their manual-edit origin remains unestablished.
The prepared input includes edited and synthetic data. Its original OSM and field-validity dates remain unknown.

## Reproduction locations

- Calibration runner: [`evaluate_hamburg_network_calibration.py`](../../plugins/astra-sumo/scripts/evaluate_hamburg_network_calibration.py).
- Calibration calculations: [`network_calibration.py`](../../plugins/astra-sumo/src/astra_sumo/core/network_calibration.py).
- Edit construction and feedback: [`network_edit_operations.py`](../../plugins/astra-sumo/src/astra_sumo/core/network_edit_operations.py).
- Configuration: `outputs/claude_followup_20261015_v1/hamburg-request.json`.
- Raw results, scorecards, requests, logs, and trial runner: `outputs/claude_followup_20261015_v1/`.

The configuration uses frozen official inputs and the native project SUMO binaries.
The complete rebuild, correspondence checks, and route requests preserve their input hashes.
Further cross-section correction and independent driving validation remain necessary.
