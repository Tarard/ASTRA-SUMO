# Claim Boundary Taxonomy

Use this before writing paper, report, or benchmark claims.

## Evidence Classes

| Class | Meaning | Allowed wording |
|---|---|---|
| `formal-evidence` | Construction, outputs, baselines, sensitivity, and semantics support the claim | "Under the audited scenarios, controller A reduced metric B relative to baseline C." |
| `diagnostic-demo` | Useful software or integration evidence but incomplete formal gates | "The prototype demonstrates integration with SUMO/TraCI." |
| `stress-diagnostic` | Boundary or extreme scenario used to probe failure modes | "This stress case identifies a limitation under high demand." |
| `construction-invalid` | Network, route, TLS, controller, or outputs are invalid | "No performance claim should be made from this run." |
| `claim-overreach` | Run is valid but the proposed claim is too broad | "The claim must be narrowed to the observed scenario." |

## Safe Academic Wording

- "In the audited scenario set..."
- "Under the tested demand regimes..."
- "Relative to the selected baselines..."
- "The evidence supports an integration demonstration, not a formal NEMA claim."
- "The run is diagnostic because controller identity/output gates are incomplete."
- "The fixed-horizon result must be interpreted with completion rate and unfinished vehicles."
- "This result suggests a mechanism that should be tested with additional seeds and demand regimes."

## Prohibited Wording Without Stronger Evidence

- "outperforms state of the art" from a narrow or single-run comparison;
- "NEMA controller" without loaded NEMA program evidence;
- "real-time deployable" without runtime below control interval;
- "robust" without sensitivity axes and seed variance;
- "safe" without phase legality, right-of-way, collision, and teleport audits;
- "validated in SUMO" when only GUI visual inspection occurred;
- "generalizes" without scenario diversity or external validation.
- "faster" or "lower delay" from arrived-only vehicles when completion rates differ.

## Claim Rewrite Pattern

Use:

```text
claim -> supporting artifacts -> missing gates -> narrowed claim
```

Example:

```text
"The MPC is a NEMA controller"
-> TraCI setPhase log and no NEMA tlLogic evidence
-> missing NEMA identity gate
-> "The MPC is a TraCI phase-sequence diagnostic controller."
```

Example:

```text
"Controller A has lower average travel time"
-> Controller A has many unfinished vehicles at the fixed horizon
-> completion gate failed for an arrived-only comparison
-> "Within the fixed horizon, Controller A completed fewer trips; compare completion rate and backlog before trip averages."
```
