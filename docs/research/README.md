# ASTRA Research Index

Research documents are dated evidence snapshots, protocols or plans. For current product behaviour use the
top-level [`README.md`](../../README.md), [Architecture](../architecture.md) and the
[documentation index](../README.md).

## Hamburg Ring1 calibration

- Current result and how to reproduce it: [Hamburg README case](../hamburg-readme-case.md).
- Method notes: [classical calibration sources](hamburg-classic-calibration-2026-10-23.md),
  [OD definition and SPSA](hamburg-spsa-od-method-2026-10-22-v1.md),
  [corridor pruning literature](hamburg-pruning-literature-2026-10-22-v1.md),
  [count-fit basis](ring1-count-fit-basis-2026-10-20.md) and its [V2](ring1-count-fit-basis-2026-10-21-v2.md),
  [OCIT program timelines](ocit-program-timelines-2026-10-21.md).
- Dated Ring1 notes from 2026-10-21: [driver scope](ring1-data-led-driver-scope-2026-10-21.md),
  [junction-body hold](ring1-junction-body-hold-2026-10-21.md), [collision trace](ring1-collision-trace-2026-10-21.md),
  [through-route coverage](ring1-through-route-coverage-2026-10-21.md),
  [primary exit prior](ring1-primary-exit-prior-2026-10-21.md), [Z15 K2 routes](ring1-z15-k2-routes-2026-10-21.md),
  [Z15 K8 route delay](ring1-z15-k8-route-delay-2026-10-21.md), [entry prefix test](ring1-entry-prefix-test-2026-10-21.md),
  [initialization demand](ring1-initialization-demand-2026-10-21.md) and its
  [V2](ring1-initialization-demand-2026-10-21-v2.md).
- Each experiment package under `benchmarks/hamburg_ring1_*` has its own README with inputs, commands and results.

## ASTRA Twin Profiler (kernel-style test and profile)

- Ring1 parent fragility check, 2026-10-26: [pre-registration](ring1-fragility-prereg-2026-10-26.md) and the
  [owner-delegated decisions](ring1-fragility-owner-decisions-2026-10-26.md) taken before any candidate run.

## Corridor human-modeling study (dormant)

- [Research plan](../torii-corridor-human-modeling-research-plan.md),
  [implementation status](../torii-corridor-human-modeling-implementation-status.md),
  [corridor research implementation status](../torii-corridor-research-implementation-status.md),
  [Stage 1-M machine review-ready plan](../stage1-machine-review-ready-plan.md).
- Frozen protocols: [held-out blind review v1](../held-out-corridor-blind-review-protocol.md) and
  [v2](../held-out-corridor-blind-review-protocol-v2.md). Do not tune on the held-out corpus.

## Network-construction experiments

- [Standard small-network v1](../standard-small-network-v1.md)
- [Teacher-free OSM signal discovery v2](../teacher-free-osm-signal-discovery-v2.md),
  [materialization v3](../teacher-free-materialization-v3.md),
  [topology discrimination v4](../teacher-free-topology-discrimination-v4.md)
- [Ingolstadt human-cleaning teacher protocol](../ingolstadt-human-cleaning-teacher-protocol.md)
