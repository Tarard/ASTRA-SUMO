# Fixed Case

This case describes the minimum evidence expected before reporting data-informed signal-control results.

## Setup

- Controller: data-informed or MPC-style TraCI controller with documented decision interval, objective, constraints, and fallback behavior.
- Information timing: every input is labeled as historical, prior-run, live-detector, forecast, or unavailable at decision time.
- Baselines: fixed-time, actuated, max-pressure, and ablations share identical route files and seed lists.
- Outputs: SUMO logs, tripinfo, summary, edgeData, TLS switch output, controller decisions, runtime, infeasible steps, and fallback count.
- Completion: natural completion or fixed-horizon reporting with unfinished vehicles, backlog, and teleports.

## Claim Boundary

After real outputs pass the gates, this case may support claims about the tested information source, controller design, network, demand levels, seeds, and metric definitions.
