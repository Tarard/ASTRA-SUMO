# MoST contour regression fixtures

These compiled-network extracts retain junctions 132844, 151337, 132981, 133196, and 140489 from the
[pinned MoST prepared input](https://github.com/lcodeca/MoSTScenario/tree/d00e82945f5c6322a98ad2df4a8d5f48274fca53).
The upstream repository declares GPLv3. Its unchanged [license](LICENSE.md) accompanies these extracts.
The prepared input includes OpenStreetMap-derived and synthetic records.
OpenStreetMap data attribution: [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).
The original OSM snapshot and field-validity dates remain unknown.

The parent is the native-normalized unjoined import recorded in
[the source-sensitivity evidence](../../../benchmarks/public_network_development_v2/source-sensitivity-20261018-v1.md).
Its SHA-256 is `8acab7d4767bdda9d1523da0a398735b495a66cb853cb9724654b5dbbfbbac6b`.

The shared local-conversion extractor selects incident roads and target internal
paths. The extracts retain those saved lane coordinates and lengths. Boundary
junctions become dead ends, and their exterior connections are removed. These
terminal boundaries make each extract loadable without the rest of Monaco.
Both extracts pass native SUMO 1.27.1 load checks.

Re-declaring the saved contour as a custom contour causes large native geometry
changes. Junction 132844 includes pedestrian facilities. Junction 151337 has
no local sidewalk and exposes the earlier automatic-applicability false pass.
The regression tests measure this conversion effect before automatic acceptance.
These are model and converter regression labels. They are not field-defect labels.

The [additional requests](provenance-v2.json) retain a stable 132981 control whose
requested pedestrian paths are identical to that control. Junction 133196 has a
stable identity control but a large requested recut. It checks the separate
control-to-candidate geometry screen. Both additional extracts pass native load.

The [folded-area request](provenance-v3.json) retains junction 140489. Its stable
control has a valid walking area. A 0.5 m contour tightening produces a
self-intersecting walking area even though the requested-geometry screen passes.
This case comes from an independent review finding. It is a selected converter
regression, not an additional member of the fixed three-case assessment cohort.
The extract passes native load before the edit.
The [standalone node patch](140489-tighten.nodes.xml) is the exact generated
request used by the independent native replay. It produces the same folded
walking area directly from the public fixture under locked SUMO 1.27.1.
