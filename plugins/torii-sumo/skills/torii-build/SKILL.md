---
name: torii-build
description: Use for SUMO road-network and traffic-infrastructure construction, cleanup, review, and interpretation from OSM, engineering drawings, MAP/KML/OCIT-C, aerial or map evidence, or an existing SUMO network.
---

# Torii Build

Use this skill as a compact knowledge base for building and reviewing the physical traffic model. The agent may combine these notes with repository code, tools, workflows, and any relevant references as needed.

## Common Knowledge

- Keep the modeling target clear: current network, historical network, design drawing, or synthetic scenario.
- Treat source authority and source date as part of the modeling problem. A newer map does not automatically replace a dated engineering source.
- Distinguish topology, geometry, lane permissions, signal identity, routeability, and field accuracy. Evidence for one does not automatically prove the others.
- OSM is useful for broad road structure and context, but complex junctions, signal ownership, channelization, and dated reconstruction may need stronger sources.
- SUMO load, successful netconvert, or vehicle passage is useful construction evidence, not proof that the physical road model is correct.
- Keep original source material available when producing edited or reconstructed candidates so differences remain reviewable.

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

## Available Torii Support

Torii workflows and checks can help with OSM construction, intersection classification, signal classification, network audit, network comparison, road-design review, and Hamburg reconstruction. Use them when they reduce work or uncertainty. The agent may also reason directly from code, files, SUMO tools, or source material when that is more appropriate.

## Output

For substantial build tasks, make clear what source basis was used, what was changed or reviewed, what was checked, and what remains uncertain.
