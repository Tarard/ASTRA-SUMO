# Ring1 count fitting: method basis

## Question and fixed target

Can a declared Ring1 model reproduce every observed field and station count in the same fifteen-minute bins?
The user requires zero MAE and zero maximum absolute error. This is a fitting target, not evidence of unique demand or prediction accuracy.
The current package has 130 field bins and 40 station bins. Their common source window has not yet been selected as a fitting/validation split.

## Checked sources

| Source | Publication and assessment | Adopted part and limit |
|---|---|---|
| Behrisch and Hartwig, *A comparison of SUMO's count based and countless demand generation tools*, DOI [10.52825/scp.v2i.107](https://doi.org/10.52825/scp.v2i.107) | Full SUMO conference paper, volume labelled 2021, published June 2022. T2: method comparison. E2: one small afternoon scenario. | Sections 4–5 compare count-driven route selection and subsequent simulation. The study also checks locations withheld from fitting. Adopt these distinctions, without assuming its outcome transfers to Ring1. |
| Keler et al., *Calibration of a Microscopic Traffic Simulation in an Urban Scenario Using Loop Detector Data*, DOI [10.52825/scp.v4i.223](https://doi.org/10.52825/scp.v4i.223) | Full 2023 SUMO conference paper. T2: model/data preparation. E2: limited detector coverage and unsuccessful demand estimates. | Sections 2–4 connect geometry, turning restrictions, detector placement and simulation. Their sparse Munich case develops implausible turnarounds and congestion. Adopt route plausibility checks. Do not copy its removal of cycling/pedestrian paths or its red/green-only signal approximation. |
| [SUMO routes from observation points](https://sumo.dlr.de/docs/Demand/Routes_from_Observation_Points.html) | Maintainer documentation, checked 2026-10-20. Implementation reference, not a research evaluation. | Count constraints do not identify a unique route set. RouteSampler selects from supplied candidate routes. Use this to distinguish route eligibility from count fitting. |

The original papers' method and experiment sections were read. Neither paper establishes exact-fit feasibility for this corridor.

## Ring1 decisions

- **Adopt:** validate candidate routes and measure simulated counts after demand generation.
- **Adapt:** retain field-level residuals before summing published station membership. Mixed direction labels cannot cancel unnoticed.
- **User requirement:** retain every declared bin and seek exact integer agreement. The papers use other fit measures.
- **Engineering inference:** static downstream reachability can prioritize unresolved lane relations. It does not prove driving, capacity or field correctness.
- **Not adopted:** a universal saturation-flow number as a rejection threshold. Local discharge capacity and signal treatment remain unmeasured.

Before fitting, the reproducible construction must expose unsupported extra turns, unresolved lanes, detector-placement assumptions and signal timing.
Any initialization period precedes the declared comparison window. Fitting results and independent validation remain separate.
