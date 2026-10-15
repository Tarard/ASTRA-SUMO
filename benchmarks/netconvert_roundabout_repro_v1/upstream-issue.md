# netconvert: identical OSM input produces different roundabout groups across processes on Windows

## Summary

Repeated native netconvert processes produce different `<roundabout>` membership for identical OSM bytes and arguments.
I reproduced this with Windows releases 1.27.1 and 1.28.0, with seed 42 and roundabout guessing disabled.
The input is synthetic: five nodes and two one-way roundabout ways that share node 1.
This reports a reproducibility failure. It does not assert that this topology represents a recommended road design.

## Input

Save the following as `input.osm.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="ASTRA synthetic reproduction">
  <node id="1" lon="13" lat="52"/>
  <node id="2" lon="13.001" lat="52"/>
  <node id="3" lon="13.0005" lat="52.001"/>
  <node id="4" lon="12.999" lat="52"/>
  <node id="5" lon="12.9995" lat="51.999"/>
  <way id="10">
    <nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="1"/>
    <tag k="highway" v="secondary"/>
    <tag k="junction" v="roundabout"/>
    <tag k="oneway" v="yes"/>
  </way>
  <way id="20">
    <nd ref="1"/><nd ref="4"/><nd ref="5"/><nd ref="1"/>
    <tag k="highway" v="secondary"/>
    <tag k="junction" v="roundabout"/>
    <tag k="oneway" v="yes"/>
  </way>
</osm>
```

## Reproduce

Run the same command repeatedly in separate processes:

```text
netconvert --osm-files input.osm.xml --output-file network.net.xml --seed 42 --roundabouts.guess false --no-turnarounds
```

For example, run this PowerShell loop in the input directory:

```powershell
$converter = 'C:/absolute/path/to/native/netconvert.exe'
1..64 | ForEach-Object {
    & $converter --osm-files input.osm.xml --output-file network.net.xml --seed 42 --roundabouts.guess false --no-turnarounds
    if ($LASTEXITCODE -ne 0) { throw 'netconvert failed' }
    Copy-Item network.net.xml ('run-{0:D3}.net.xml' -f $_)
}
Select-String -Path run-*.net.xml -Pattern '<roundabout '
```

Compare the roundabout records as a multiset and sort their edge and node lists.
XML timestamps, record order, and list order are irrelevant to this result.

## Actual result

Both versions produced four distinct membership variants in 64 successful sequential processes.
Examples include:

```xml
<roundabout nodes="1 1 3 5" edges="10#0 10#1 20#0 20#1"/>
```

and:

```xml
<roundabout nodes="1 3" edges="10#0 10#1"/>
<roundabout nodes="1 5" edges="20#0 20#1"/>
```

and:

```xml
<roundabout nodes="1 3" edges="10#0 10#1"/>
<roundabout nodes="5" edges="20#0"/>
<roundabout nodes="1" edges="20#1"/>
```

The fourth form merges one edge of the second ring into the first ring and retains the other edge separately.
All non-roundabout network elements matched within each version after excluding comments and serialization order.
The input and native executable hashes remained unchanged.
All eight representative networks passed a load check with the matching SUMO package at end time zero.
No traffic behavior comparison was performed.

A control input containing only way 10 and nodes 1, 2, and 3 produced one membership variant in 64 processes.
This control used 1.27.1.

## Expected result

Identical input and arguments should produce stable roundabout membership across separate processes.
If this shared-node topology is unsupported, a stable diagnostic would also avoid silent changes between builds.

## Source observation

[`NBEdgeCont::extractRoundabouts()` at 430625897227e3317e34c7d2e731dbe5419f6843](https://github.com/eclipse-sumo/sumo/blob/430625897227e3317e34c7d2e731dbe5419f6843/src/netbuild/NBEdgeCont.cpp#L1457)
iterates `std::set<NBEdge*> candidateEdges` in pointer order.
It uses a shared visited set and follows the first outgoing edge with roundabout priority.
The same traversal appears in tag `v1_27_1`.
This appears consistent with the start-dependent output, but I have not validated a source patch.
A stable candidate order alone might retain an incorrect decomposition for touching or overlapping rings.

## Environment

- Native Windows x64 executables from the PyPI `eclipse-sumo` wheels, versions 1.27.1 and 1.28.0.
- The 1.27.1 version reports MSVC 19.51.36247.0 Release.
- Executable 1.27.1 SHA-256: `b85bbcecd3c60799c3a0eb7f1077f9608b0187da29babc3a20f326f97d23942f`.
- Executable 1.28.0 SHA-256: `5b48c72620a1e3afbde1a1805af971d37c41d59386f56ad06679383cb59be3f5`.
- Input SHA-256: `07c8ab6e163eb9dd2fa7c79b1adc880b13a00291abf97a1fc7d60a5deb81b704`.
- Platform: `Windows-11-10.0.26300-SP0`.
- Executables were invoked directly. A Python launcher was not used as netconvert.
- Upstream main was inspected but not compiled. Linux and macOS were not tested.
- Searches for existing roundabout reproducibility, deterministic, nondeterministic, and `extractRoundabouts` issues found no matching report.

The small fixture, Python standard-library runner, tests, and measured summary are available in ASTRA-SUMO under `benchmarks/netconvert_roundabout_repro_v1`.
