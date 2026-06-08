# Bad Case

This case is intentionally incomplete.

## Setup

- Controller: data-informed TraCI signal controller with rolling state estimates.
- Features: some weights are tuned using full-run outcomes from the evaluation scenario.
- Baselines: max-pressure baseline uses a different seed list.
- Outputs: controller runtime and fallback counts are not saved.
- Completion: only arrived vehicle travel time is reported.

## Intended Claim

"The data-informed controller improves delay because it uses better state estimates."

## Audit Risks

- Features or weights may leak future evaluation information.
- Baselines are not paired.
- Missing runtime and fallback logs hide feasibility failures.
- Arrived-only metrics can hide unfinished demand.
