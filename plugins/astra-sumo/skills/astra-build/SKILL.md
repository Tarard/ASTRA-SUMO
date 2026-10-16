---
name: astra-build
description: Use for SUMO road-network and traffic-infrastructure construction, cleanup, review, and interpretation from OSM, engineering drawings, MAP/KML/OCIT-C, aerial or map evidence, or an existing SUMO network.
---

# ASTRA Build

Use this skill as a compact knowledge base for building and reviewing the physical traffic model. The agent may combine these notes with repository code, tools, workflows, and any relevant references as needed.

## Common Knowledge

- Keep the modeling target clear: current network, historical network, design drawing, or synthetic scenario.
- Treat source authority and source date as part of the modeling problem. A newer map does not automatically replace a dated engineering source.
- Distinguish topology, geometry, lane permissions, signal identity, routeability, and field accuracy. Evidence for one does not automatically prove the others.
- OSM is useful for broad road structure and context, but complex junctions, signal ownership, channelization, and dated reconstruction may need stronger sources.
- SUMO load, successful netconvert, or vehicle passage is useful construction evidence, not proof that the physical road model is correct.
- Keep original source material available when producing edited or reconstructed candidates so differences remain reviewable.
- For a suspected false road, trace its exact SUMO objects and compare dated imagery with independent access evidence. Verify building use and entrances where a roof obscures the road. OSM tags also need independent verification.
- For parking buildings or underground parking, use `astra network parking-facilities` with the detailed network, full OSM snapshot, and exact input hashes. Retain access and internal demand endpoints. Give its report to `astra review render` for a building outline, P marker, and entry/exit arrows. Inspect the detail image when needed. Capacity, dwell times, and parking queues need their own inputs.
- When the evidence supports deletion, materialize a reviewed candidate and check exterior state and affected access. Generic deletion protects parking facilities, covered passages, and their access roads. Preserve failed traffic checks with the candidate.
- For review images, use `astra review render <request.json> <new-output-dir> --json` with a hash-bound north-up aerial image, projected bounds, and fixed views. It draws every lane and junction directly from the network. Use `astra review create` for a schematic view when aerial coordinates are unavailable. Use NetEdit for manual edits or an explicit display inspection. Images do not replace connection, signal, or field checks.

## Reference Library

Read only the references that help with the current task.

| Topic | Reference |
|---|---|
| OSM-to-SUMO construction and cleanup | `references/osm-to-sumo-workflow.md` |
| OSM import and source patterns | `references/osm-source-patterns.md` |
| OSM fragmentation and road-axis reconstruction | `references/osm-way-fragmentation-and-road-axis-reconstruction.md` |
| Road arms and lane connections | `references/road-arm-and-connection-classification.md` |
| Physical intersection classification | `references/composable-intersection-classification.md` |
| Signal-device classification | `references/composable-signal-device-classification.md` |
| Map, TLS, detector, and source-alignment notes | `references/model-osm-detectors.md` |
| Hamburg road reconstruction | `references/hamburg-five-intersection-aerial-workflow.md` |

## Available ASTRA Support

ASTRA workflows and checks can help with OSM construction, intersection classification, signal classification, network audit, network comparison, road-design review, and Hamburg reconstruction. Use them when they reduce work or uncertainty. The agent may also reason directly from code, files, SUMO tools, or source material when that is more appropriate.

## Output

For substantial build tasks, make clear what source basis was used, what was changed or reviewed, what was checked, and what remains uncertain.
