# Fixed Case

This case describes the minimum evidence expected before reporting max-pressure results.

## Setup

- Controller: TraCI max-pressure script with logged phase choices and pressure values.
- Detector mapping: detector-to-lane and movement-to-phase table verified against the loaded `.net.xml`.
- Baselines: fixed-time, actuated, and max-pressure all share identical route files and seed lists.
- Outputs: `tripinfo`, `summary`, `edgeData`, TLS switch output, controller logs, and warnings saved per run.
- Completion: natural completion or fixed-horizon reporting with unfinished vehicles, insertion backlog, and teleport counts.

## Claim Boundary

After real outputs pass the gates, this case may support a paired comparison for the selected network, demand levels, seeds, and pressure definition.
