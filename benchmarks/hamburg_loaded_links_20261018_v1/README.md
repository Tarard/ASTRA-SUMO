# Loaded link preservation across Hamburg construction stages

This fixture retains the source before context joining, two immutable native outputs, and the complete candidate.
The [manifest](fixture.json) records compressed and original hashes and assigns each edited junction its adopted native reference.
It covers three edited junctions and 125 retained junctions.

The context output supplies `LSA2403_part0`.
The final movement output supplies `LSA2349_part0` and `LSA2394_part0`.
Retired source-node IDs remain in the declared edit scope. They are not treated as unexplained exterior losses.

The [regression](../../tests/test_hamburg_loaded_link_reference.py) unpacks these files and reads the actual SUMO link relations.
It compares successors, via lanes, directions, SUMO signal assignments, yielding incoming lanes, and conflicting internal lanes.
An incorrect native-stage assignment must not pass. No vehicles are generated and no simulation steps are taken.
The signal-index regression changes two assignments while keeping the other loaded relations unchanged. The comparison must detect that change.
SUMO 1.27.1 produced the retained outputs. The regression compares the loaded models rather than assuming another version's exact conflict implementation.

The candidate SHA-256 is `0eb22507926899f40dc1cd356dc52799d14da5cd3bff6fc75c27b17d2447f30a`.
It is byte-identical to the previous corrected reconstruction.
The preserved driving limitations therefore remain relevant. This fixture supports no traffic-improvement or field-validity claim.

## Reproduce

Use the repository environment and place its SUMO binaries on `PATH`.
Run `python -m pytest tests/test_hamburg_loaded_link_reference.py -q`.
The complete construction runner remains [hamburg_topology_workflow.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_topology_workflow.py).
The local request and outputs are recorded in the manifest. They are not required for this fixture regression.

## Source notices

© OpenStreetMap contributors. The edited source databases use [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
See [OpenStreetMap copyright](https://www.openstreetmap.org/copyright).
Official MAP geometry: Freie und Hansestadt Hamburg, Landesbetrieb Straßen, Brücken und Gewässer.
Aerial references: Freie und Hansestadt Hamburg, Landesbetrieb Geoinformation und Vermessung.
The official data use [Datenlizenz Deutschland Namensnennung 2.0](https://www.govdata.de/dl-de/by-2-0).
Original official snapshots and source URLs are indexed in the [approach fixture](../hamburg_approach_reference_20261016_v1/fixture.json).
The geometry includes derived junction joins, restored road shapes, and official and aerial reference curves.
