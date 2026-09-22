# Mailing List Announcement Draft

> Draft release material. Use the top-level `README.md` and `ARCHITECTURE.md` for the current product and claim boundary.

Subject:

```text
[Tool] Torii: agent-driven SUMO construction, calibration, and simulation workflows
```

Body:

```text
Hello SUMO community,

I am sharing Torii, an independent workflow project for Eclipse SUMO.

Torii turns real-world traffic data and natural-language tasks into SUMO simulations. Its current workflow covers three broad jobs:

- building SUMO network artifacts from sources such as OpenStreetMap and other traffic-infrastructure data;
- calibrating demand and simulation behavior against measured observations;
- running follow-on SUMO experiments from natural-language instructions.

Torii also keeps an explicit evidence boundary. Successful network generation, SUMO load, route completion, or KPI improvement are not treated as proof that topology, traffic signals, demand, or field behavior are correct. Source and candidate artifacts remain separate, and validation evidence is retained for review.

The current public example includes a digital-twin corridor in central Hamburg that combines official traffic data, network reconstruction, and calibration evidence.

Repository: https://github.com/Tarard/Torii-SUMO

Torii is independent research tooling. It is not affiliated with or endorsed by Eclipse SUMO, the Eclipse Foundation, DLR, OpenAI, Anthropic, or OpenStreetMap.

Feedback is welcome, especially on reproducibility, network validation, demand calibration, and agent-driven SUMO workflows.
```

## Posting Notes

- Use a practical tool tone.
- Do not imply official SUMO endorsement.
- Do not describe a runnable network as automatically correct.
- Keep the current README and architecture document as the source of product claims.
