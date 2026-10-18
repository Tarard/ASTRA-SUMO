# Public network references for development

Use published networks to test development changes without waiting for a formal
human study. Keep their model targets separate from field defect labels.

## Build the corpus

Run from the repository root. Use a new result directory for each run.

```powershell
./.venv/Scripts/python.exe -m astra_sumo.cli network reference-corpus benchmarks/public_network_development_v1/catalog.json outputs/public-reference-cache outputs/public-reference-run-v1 --json
```

The command downloads exact Git revisions and verifies every file's byte size
and SHA-256. It reuses an intact cache offline. A changed cache file stops the
run. The command preserves that file. Use a new cache to investigate a mismatch.

The command prefers netconvert beside the current Python executable. Otherwise
it uses PATH. Supply `--netconvert-binary` to select an explicit executable.
SUMO comes from the same directory. Reports record executable paths and hashes.

The [historical MoST supplement](../benchmarks/public_network_development_v2/README.md)
adds a recovered same-revision prepared input and its published import settings.
Use its separate catalog to retain the original four-model experiment unchanged.
Catalog-bound configurations preserve supported import options, including elevation and type maps.
The builder overrides joining and geometry removal for its unjoined comparison.
Upstream output and report paths are ignored. Actual arguments and overrides remain in the result.
The importer uses the current binary, so its differences are not automatically human edits.
For catalog-bound configurations, a second import retains the published joining and geometry settings.
`generator-control.json` records this command, its network hash, and its load check.
`generator-control-labels.jsonl` compares the same original case IDs with this control.
It separates recipe agreement, unexplained differences, unresolved control correspondence, and unknown reference records.
It also counts differences present in the unjoined baseline. Unknown records remain in the table.
This control uses the published configuration with current SUMO defaults. It does not reconstruct the historical executable or all historical defaults.
Recipe agreement provides reproducibility evidence. Unexplained differences do not become human edits or field defects.
The [dated MoST control](../benchmarks/public_network_development_v2/recipe-control-20261016-v1.md) records the real-data result and remaining unknowns.

The unjoined import now follows the reference network's coordinate-normalization convention.
A zero declared offset keeps global coordinates. A nonzero offset enables native normalization.
`import_coordinate_frame` records that choice and the reference offset. Exact registration rules remain unchanged.
It also records the actual import offset and projection, with separate equality results against the reference.
Missing metadata remains unknown. A missing reference offset keeps the global-coordinate default.
Different saved offsets do not by themselves prove incompatible world coordinates. These fields do not add an acceptance condition.
Native normalization can also change generated geometry or connections. Each new import remains a separate baseline.
The [coordinate-frame repeat](../benchmarks/public_network_development_v2/coordinate-frame-20261018-v1.md) retains improved correspondence and the observed conversion changes.

The [catalog](../benchmarks/public_network_development_v1/catalog.json) freezes
four public models from three cities:

| Model | Project license | Available development evidence |
|---|---|---|
| TUM Ingolstadt | Apache-2.0 | Published node and movement targets. The original OSM input is absent from this snapshot |
| LuST | MIT | Published targets and a prepared legacy JOSM input. Their exact relationship is unverified |
| MoST | GPLv3 | Published targets and prepared OSM-like data. The current input replaced an older input after the published network was generated |
| InTAS | GPLv3 | Published targets and the preceding network revision for a documented connection correction |

The cache retains upstream licenses and source descriptions. Generated data
remain under the result directory. This repository stores the catalog and code.
It does not redistribute the downloaded network files.

## What the records mean

`model-targets.jsonl` contains every published SUMO junction record, including
road ends. Each record retains its source identity, junction attributes,
ordinary connections, approach identifiers, and any readable joined-member
trace. A `cluster_*` name records a model grouping. It does not establish that
a person made that grouping or that it is correct in the field.

For a traceable source pair, `model-labels.jsonl` also records:

- whether adjacent source nodes belong to the same published junction;
- ordinary lane connections added, removed, or retained in the published model;
- unresolved source correspondence.

Registration requires the same declared projection and exact position after
removing network offsets. A new joined node also requires a complete member
trace and a position inside the source members' coordinate bounds. Lane
connection labels require registered approaches and unchanged lane identities.
Coordinate changes, missing members, and ambiguous identifiers remain unknown.
This includes conversion rounding that changes the recorded position. Exact
registration can therefore abstain on a geometrically close candidate.
The code does not use a nearest-junction guess or an adjustable distance limit.

Custom joins without a readable member trace can remain unresolved. This is
a limit of the current comparison. It does not establish a wrong edit.

The InTAS development pair uses the network before
[the published connection correction](https://github.com/silaslobo/InTAS/commit/641652c7588fd42e2cb1b40fe601177ba2cdb0a9).
Its model targets contain two added and four removed ordinary connections.
Retained connections provide regression checks against unintended changes.
These counts describe the selected revisions. They are not field precision.
With SUMO 1.27.1, the selected preceding InTAS network fails to load because
one internal lane has two predecessors. The published target loads successfully.
The failing input remains available for static regression checks. It does not
count as a valid traffic simulation. The corpus reports this limitation as
`review_required` while retaining its usable static records.

## Compare a candidate

Write a request with the exact dataset, source, and candidate identities:

```json
{
  "schema": "astra.public-reference-request/v1",
  "dataset_sha256": "SHA256_OF_DATASET_JSON",
  "comparison_source_sha256": "SHA256_OF_THE_FROZEN_DEVELOPMENT_INPUT",
  "candidate_net_file": "C:/project/candidate.net.xml",
  "candidate_sha256": "SHA256_OF_CANDIDATE"
}
```

```powershell
./.venv/Scripts/python.exe -m astra_sumo.cli network reference-check outputs/public-reference-run-v1/InTAS/dataset.json request.json comparison-v1.json --json
```

The report separates membership and connection outcomes. Added, removed, and
retained connections also have separate counts. Unknown reference records and
unresolved candidate records remain in the denominator. A successful check
requires every record to be resolved and match its model target.
Additional candidate connections and new unscored roads also require review.
They cannot disappear from a successful comparison simply because no original
case named them.

The candidate's source identity is a caller-supplied provenance claim. The
command checks its hash, the source files, the label file, and the dataset.
It then independently checks candidate node registration and connection
presence. It does not infer construction history from a network filename.

A published-model self-check tests extraction consistency. It is not evidence
of cleaning performance. Comparing the preceding model checks that known
revision differences are visible. Use independent candidate outputs to assess
a development method.

## Source and research limits

All records use `development_only`. The two Ingolstadt sources share a city
family. The command creates no training/test split and does not change frozen
blinded cases, reviewer decisions, or study thresholds. Daily development can
use these references while formal human validation remains separate.

OSM snapshot dates and field-validity dates remain unknown when upstream does
not establish them. The command does not substitute current OSM for missing
historical input. LuST and the current MoST prepared input receive no inferred
paired labels. Their import reports remain compatibility observations.

The references have different modeling goals:

- TUM warns about missing pedestrian crossings and requires its WAUT setup
  for working signal operation. A network-only load does not test that setup.
- LuST's published mobility validation uses SUMO 0.26. Loading it with a newer
  version does not preserve that validation.
- MoST adds some signals to represent pedestrian interactions. Those signals
  do not establish field control locations.
- The InTAS paper describes an earlier vehicle-focused model. The current
  repository includes later pedestrian changes.

These limits follow the upstream descriptions and methods:
[TUM](https://github.com/TUM-VT/sumo_ingolstadt),
[LuST](https://github.com/lcodeca/LuSTScenario),
[MoST](https://easychair.org/publications/open/x3nd), and
[InTAS](https://arxiv.org/abs/2011.11995).

This workflow adapts published models into versioned development references.
Its exact registration and comparison are engineering checks. They do not
establish field defects, calibrated demand, collision risk, or automatic-cleaning
precision. Full scenario validation and independent field evidence remain open.
