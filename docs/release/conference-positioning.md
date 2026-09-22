# Conference and Demo Positioning

> Draft release material. Use the top-level `README.md` and `ARCHITECTURE.md` as the current product and claim boundary.

## Recommended Angle

Present Torii as an agent-driven SUMO workflow for turning real-world traffic data and natural-language tasks into auditable simulation artifacts. The strongest story is not that Torii is another plugin, but that it connects construction, calibration, simulation, and evidence checks in one workflow.

Suggested title:

```text
Torii: Agent-Driven Traffic Scenario Construction and Evidence-Aware SUMO Workflows
```

Suggested abstract framing:

```text
Building a useful SUMO scenario requires more than producing a runnable network. Torii combines task routing, SUMO domain skills, local execution tools, and evidence checks to build networks from real-world sources, calibrate them against observations, and run follow-on simulation tasks from natural-language instructions. The system keeps source and candidate artifacts separate, records validation evidence, and limits claims when topology, demand, signal control, or field truth remain uncertain.
```

## Demo Checklist

- Show one real-world input being converted into a SUMO network artifact.
- Show one validation step that catches a problem a successful SUMO load would miss.
- Show one calibration or comparison against measured traffic data.
- Show one follow-on simulation task started from a natural-language request.
- End with the evidence boundary: runnable is not the same as correct.

The Hamburg digital-twin workflow is the preferred current demonstration because it connects reconstruction, official traffic data, validation, and calibration in one traceable case.

## Claim Boundaries

- Do not claim arbitrary real-world networks can be reconstructed automatically to expert quality.
- Do not treat SUMO load, route completion, or a lower KPI as proof of model correctness.
- Do not present a map or reference network as ground truth without stating its scope and date.
- Keep uncertain topology, signal, demand, and calibration decisions reviewable and traceable.
