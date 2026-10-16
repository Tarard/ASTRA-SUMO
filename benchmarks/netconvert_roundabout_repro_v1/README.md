# OSM roundabout grouping reproduction

Reported upstream as [SUMO issue #18375](https://github.com/eclipse-sumo/sumo/issues/18375) on 2026-10-15.

This synthetic input isolates unstable roundabout grouping in native Windows netconvert.
It contains five nodes and two closed, one-way OSM roundabout ways that share one node.
It contains no downloaded map data. The repository MIT license applies.
The example is small. We do not claim it is the smallest possible graph.

## Run

Use Python 3.11 or later and an absolute native netconvert executable.
The script uses only the Python standard library.
Use a new output directory for each experiment.

```powershell
./.venv/Scripts/python.exe benchmarks/netconvert_roundabout_repro_v1/reproduce.py `
  C:/absolute/path/to/sumo/bin/netconvert.exe `
  outputs/roundabout-reproduction-v1 --runs 64
```

The script starts separate processes sequentially with identical arguments, input bytes, and `--seed 42`.
It disables roundabout guessing and turnarounds.
It retains each network and log, the command, program version, input hashes, and platform.
It refuses an existing output directory.

`reproduced` means successful processes produced different roundabout records.
`not_observed` means this batch found no difference. It does not establish determinism.
`failed` means a process failed or an input changed. Failed processes cannot establish reproduction.
Invocation errors and timeouts raise an exception. Preserve their partial output for diagnosis.

The comparison ignores XML comments, whitespace, attribute order, root element order, and order within roundabout lists.
It retains duplicate roundabout entries and duplicate edge or node IDs.
It hashes every other network element separately, including lanes, connections, junctions, and signal programs.

## Observed on 2026-10-15

| Input | Native version | Successful processes | Roundabout variants | Other element variants |
|---|---|---:|---:|---:|
| Two touching rings | 1.27.1 | 64 | 4 | 1 |
| Two touching rings | 1.28.0 | 64 | 4 | 1 |
| One ring control | 1.27.1 | 64 | 1 | 1 |

The control removes way 20 and nodes 4 and 5 from the fixture.
All inputs and executables retained their hashes.
Both versions produced the same four forms of roundabout grouping, with different frequencies.
One representative of each form passed a SUMO load check with its matching package, for eight successful checks.
These checks used end time zero. They are not traffic experiments.

[observed.json](observed.json) records hashes, counts, and representative groups.
Its raw reports and complete generated networks remain under the listed `outputs/` paths.
Those generated directories are not committed. The input and runner are committed for independent reproduction.
The tests are in [test_netconvert_roundabout_repro.py](../../tests/test_netconvert_roundabout_repro.py).

## Interpretation and limits

The output groups can merge both rings, retain two rings, or split one ring into single-edge records.
This changes membership, not only the order of XML records.
The tested example retained identical non-roundabout elements within each version.
It does not demonstrate changed traffic behavior or a change to ordinary connections.

The inspected [upstream function](https://github.com/eclipse-sumo/sumo/blob/430625897227e3317e34c7d2e731dbe5419f6843/src/netbuild/NBEdgeCont.cpp#L1457)
uses pointer ordering for candidate traversal, one shared visited set, and the first matching outgoing edge.
This is consistent with the observed start-dependent grouping.
No instrumented upstream build or patch validation was performed.
Changing only the ordering can make an incorrect group stable. Correct grouping also needs an explicit rule for shared-node rings.

Windows native releases 1.27.1 and 1.28.0 were tested.
Upstream main was inspected at the commit above, but was not compiled.
Linux and macOS were not tested.
The 1.28.0 wheel was unpacked under `outputs/` and did not change the project environment.

ASTRA must continue to compare roundabout membership during repeated builds.
A fixed seed alone does not remove this failure.
The existing PlainXML normalizer remains restricted to its checked closed-cycle and permission-partition cases.
Do not treat this fixture as permission to normalize arbitrary overlapping or incomplete rings.

See [upstream-issue.md](upstream-issue.md) for the self-contained upstream report.
