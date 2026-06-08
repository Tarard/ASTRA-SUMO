# Fixed Case

This case describes the minimum evidence expected before a fixed-time baseline is used.

## Setup

- Controller: fixed-time TLS program with documented phase sequence, cycle length, yellow time, and all-red policy.
- Demand: one committed route file per demand level, generated with recorded seeds.
- Outputs: unique output directory per controller, seed, demand level, and run timestamp.
- Horizon: either natural completion or fixed horizon with unfinished vehicles and backlog reported.
- TLS evidence: TLS switch output and controller log are saved for every run.

## Claim Boundary

After real SUMO outputs pass the gates, this case may support a paired baseline comparison for the specified network, demand levels, seeds, and metrics.
