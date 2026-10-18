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
The stop-state regression changes a vehicle movement from a major-road link to a stop-sign link.
Its conflicts and saved paths stay unchanged. The comparison must still detect the changed priority and state.
SUMO 1.27.1 produced the retained outputs. The regression compares the loaded models rather than assuming another version's exact conflict implementation.

The candidate SHA-256 is `0eb22507926899f40dc1cd356dc52799d14da5cd3bff6fc75c27b17d2447f30a`.
It is byte-identical to the previous corrected reconstruction.
The preserved driving limitations therefore remain relevant. This fixture supports no traffic-improvement or field-validity claim.

## Complete reconstruction result

The [verification record](evidence.json) binds the complete rebuild to commit `22827f8` and the actual implementation hashes.
The final workflow comparison covers three edited junctions and 125 retained junctions, with no differences.
All 16 official vehicle movements remain materialized. No fallback part is used.
The workflow still reports `review_required` for construction coverage and the broader audit.
Source files, generated inputs, and the final candidate remain unchanged during verification.

A repeat on MoST uses the same typed-edit implementation.
Its width perturbation converts 9 of 4404 ordinary roads.
The two edited junctions and 2002 retained junctions match their respective references, including signal assignments.
This is a technical preservation check. It does not validate actual road width or traffic demand.

The focused runs pass 100, 53, 18, and 38 checks. These runs overlap and must not be added together.
They include source correspondence, missing observations, exact native stages, split-road identities, signal-index corruption, and the 14 typed edits.
The raw results and their hashes are listed in the verification record.

The follow-up adds priority and state for links without signal assignments.
It passes 104 focused checks and a further 25 checks of typed edits, approach construction, and navigation.
The priority-aware comparison also retains zero differences across Hamburg's 128 junctions and MoST's 2004 junctions.
Unavailable observations produce a review decision on the candidate itself. Other construction measurements are retained.
Only the active signal program is observed. This does not validate switching plans, runtime additional files, or historical control.

The load-warning follow-up retains complete native warning and error logs with hashes.
It compares source and candidate warning-line counts without mixing in the partial native networks.
The current Hamburg and MoST repeats have zero load warnings and zero loaded-link differences.
The warning regression adds an unused signal-state character while preserving loaded links.
The added warning is measured separately. It does not create another acceptance condition.
Failed loads retain their raw logs. An incomplete observation cannot claim that no warnings occurred.
The [verification record](evidence.json) retains the implementation hashes and raw report identities.

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
