# Bad Case

This case is intentionally incomplete.

## Setup

- Controller: TraCI max-pressure script using hand-written lane groups.
- Detector mapping: detector IDs copied from an older network.
- Baselines: fixed-time and actuated runs use different random demand seeds.
- Outputs: `edgeData` interval differs between controllers.
- Completion: stopped at `3600s`; unfinished vehicles not written.

## Intended Claim

"Max pressure is superior because it has lower average delay and higher throughput."

## Audit Risks

- Lane groups may not match the loaded network.
- Different seeds make controller comparisons unpaired.
- Output intervals make metrics non-comparable.
- Throughput may be inflated if unfinished vehicles are ignored.
