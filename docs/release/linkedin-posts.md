# LinkedIn Post Drafts

> Draft release material. Use the top-level `README.md` for the current product wording.

## Release Post

```text
I have been turning my SUMO workflow into ASTRA: Automated Simulation of TRAnsportation networks for SUMO.

ASTRA turns real-world traffic data and natural-language tasks into SUMO simulations.

The workflow is organized around three jobs:
- Build SUMO networks from OpenStreetMap, trajectory data, and road-construction information.
- Calibrate traffic demand and simulation behavior against measured observations.
- Run further SUMO experiments from natural-language instructions.

The important part is the evidence boundary. A network loading in SUMO is not treated as proof that its topology, traffic signals, demand, or field behavior are correct. ASTRA keeps source and candidate artifacts separate and records the checks used to support each result.

A current case is a digital-twin corridor in central Hamburg, where official traffic data and reconstructed SUMO artifacts are combined in one traceable workflow.

Repository: https://github.com/Tarard/ASTRA-SUMO

ASTRA is an independent project and is not affiliated with or endorsed by Eclipse SUMO, the Eclipse Foundation, DLR, OpenAI, Anthropic, or OpenStreetMap.
```

## Technical Case Post

```text
A common traffic-simulation failure is to stop at “the network loads.”

A runnable SUMO network can still contain wrong lane connections, signal ownership, routeability, demand, or calibration assumptions.

ASTRA treats those mismatches as evidence to inspect, not as details to hide. The workflow keeps the original source separate from generated candidates, records validation artifacts, and can stop at review instead of forcing an automatic answer.

That makes the useful output more than a .net.xml file: it is the network plus the evidence needed to understand what is supported and what still needs review.

Repository: https://github.com/Tarard/ASTRA-SUMO
```
