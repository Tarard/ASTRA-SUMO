# Stop-line transfer with SUMO logical lengths

This record supersedes the [first per-lane experiment](../network_stop_line_preservation_20261016_v1/README.md).
It retains that experiment and its failures as history.
[`evidence.json`](evidence.json) binds the revised inputs, method, candidates, tests, and raw results by hash.

## Method and independent review

An independent Claude review reproduced a different logical-length convention in native netconvert.
The first direct method shortened only the selected lane's logical length.
Native conversion instead gives normal-road lanes a common logical length.
That difference could survive a geometric scorecard unnoticed.

The revised method, `stop_line_edge_mean_3d`, transfers the saved approach segment in spatial metres, including elevation.
It handles native shapes that omit zero Z values on individual vertices.
It assigns the average transferred logical length to affected normal roads and straight internal groups.
Sibling lanes can change logical length while retaining their shapes.
The report gives every before and after value.

This follows the [SUMO road-length convention](https://sumo.dlr.de/docs/Networks/SUMO_Road_Networks.html#lanes)
and the versioned [native implementation](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/netbuild/NBEdge.cpp).
Multi-lane turning groups can use separate lengths. They retain native conversion, as do custom-length and bidirectional cases.
The direct path preserves the source's junction requests, signals, connections, and waiting structures.
It does not claim that native reconstruction produces the same topology or dynamics.

Native tests cover a signalized two-lane junction at zero elevation and on a slope.
They compare road logical lengths and stop-line endpoints with native conversion.
A further unrelated native conversion preserves those road lengths and endpoints.
The flat case also matches the native straight internal-group length.
Versioned release validation rejects the earlier unlabelled per-lane method and reconstructs the new candidate from its source.
It also checks the reported convention and logical-length values.
An unchanged requested offset returns `no_effect`.

## Real Hamburg result

The input and official facts are the same as the first experiment.
The direct candidate uses zero netconvert rebuilds.
Its longitudinal stop-line error changes from +4.734841 m to +0.004841 m.
Its lateral error remains -2.816698 m.
No supplied reference fact changes outside the declared scope.
The four lanes of `554713076#0` now share a logical length of 28.9775 m.
The two lanes of `:LSA2349_part0_0` share 59.685 m.
These are averages of transferred saved logical lengths, not a promise of numerical equivalence after arbitrary geometry reconstruction.

The same 100 source routes, departures, and vehicle IDs run under seeds 17, 42, and 104 until 2400 s.

| Seed | Source arrivals | Native-conversion candidate | Revised direct candidate |
|---|---:|---:|---:|
| 17 | 63/100 | 62/100 | 63/100 |
| 42 | 100/100 | 62/100 | 100/100 |
| 104 | 61/100 | 60/100 | 61/100 |

The source and revised candidate both retain one collision and one teleport under seed 42.
The other seeds retain unfinished vehicles. All six pairs remain invalid for a clean performance comparison.
Per-trip records retain the full requested cohort. Invalid pairs contribute no performance average.

## Dependent artifacts and remaining limits

The edit lists affected lanes and marks detector, stop, and parking positions `not_checked`.
Those files are not operating inputs to this network-only command.
Their positions require a separate check before reuse, even when the network loads successfully.
`source_baseline` distinguishes a checked dirty diagnostic from cases where no driving check ran.

The parent network has 18 normal roads with unequal declared lane lengths.
Those require a separate investigation of the construction pipeline.
Another 20 internal groups have varying lengths. Turning groups can legitimately differ, so this count is not a defect count.
This edit repairs its own affected groups and preserves the rest of the source.
The road's lateral discrepancy, mixed-demand failures, and uncertain source dates remain unresolved.

## Verified locations

- Implementation: [`network_edit_operations.py`](../../plugins/astra-sumo/src/astra_sumo/core/network_edit_operations.py).
- Release reconstruction: [`candidate_contracts.py`](../../plugins/astra-sumo/src/astra_sumo/core/candidate_contracts.py).
- Native and adversarial cases: [`test_network_edit_operations.py`](../../tests/test_network_edit_operations.py).
- Final affected, reference, and navigation tests: 140 passed.
- Scenario request and facts: `outputs/claude_followup_20261015_v1/stop-line-request-v1.json` and `frozen-two-site-facts-v1.json`.
- Direct trial and scorecards: `outputs/calibration_driving_20261016_v1/edge-mean-stop-line-v2/`.
- Comparison plan, runs, and per-trip results: `outputs/calibration_driving_20261016_v1/paired-edge-mean-v1/`.
- Runner: `outputs/calibration_driving_20261016_v1/compare_stop_line_edge_mean.py`.

Native checks use SUMO 1.27.1. This result has no promotion authority and makes no field-performance claim.
