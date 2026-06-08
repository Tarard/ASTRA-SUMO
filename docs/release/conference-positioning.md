# Conference and Demo Positioning

## Recommended Angle

Use "error auditing" and "reproducibility checks" rather than "I made a skill."

Suggested title:

```text
Auditing Reproducibility Failures in SUMO/TraCI Traffic Signal Control Experiments
```

Suggested abstract framing:

```text
Traffic signal control experiments in SUMO/TraCI can pass execution checks while still producing invalid comparisons because demand, seeds, TLS semantics, detector mappings, outputs, completion status, or metric definitions differ across controllers. This demo presents a reusable agent skill and checklist that audits fixed-time, actuated, max-pressure, data-informed, and MPC-style signal-control workflows before claims are reported.
```

## SUMO Conference Note

As of 2026-06-08, SUMO Conference 2026 has already taken place in Berlin from 2026-06-01 to 2026-06-04. For the next cycle, watch the official SUMO Conference page and position this as a short paper, poster, or tool demo about reproducibility failures in signal-control experiments.

## Demo Checklist

- Show one broken fixed-time baseline case.
- Show one max-pressure detector mapping or seed pairing issue.
- Show one data-informed controller leakage or missing ablation issue.
- End with the corrected claim boundary, not a broad performance claim.
