# Baseline and Ablation Design

Use this when an experiment compares controllers or claims performance.

## Baseline Classes

Choose baselines that match the claim:

- **Current/no-control baseline**: existing plan, no intervention, or simple uncontrolled scenario.
- **Fixed-time/Webster-like baseline**: useful for signal timing claims.
- **Actuated or delay-based baseline**: useful when detector-responsive behavior is claimed.
- **Max Pressure or Max Wave baseline**: useful for network or queue-pressure controller comparisons.
- **Public benchmark controller**: useful when comparing to learning or benchmark literature.
- **Ablation variant**: simplified version of the proposed method with one component removed.
- **Oracle or full-information diagnostic**: useful only as an upper-bound diagnostic, not as deployable baseline.

## Fairness Rules

- Same network and demand unless the experiment intentionally varies them.
- Same random seeds or matched seed sets.
- Same simulation horizon and warm-up handling.
- Same output definitions and aggregation windows.
- Same observability assumptions unless observability is the tested axis.
- Same phase legality and safety constraints.
- Runtime and control interval reported for all online controllers.

## Ablation Rules

An ablation should answer "which component supports which claim?"

Common ablations:

- remove coordination term;
- remove prediction term;
- remove detector/probe fusion;
- remove robustness term;
- remove privacy or noise mechanism;
- replace online update with preset timing;
- use full-information observation only as a diagnostic upper bound.

## Bad Baseline Smells

- Only one weak baseline.
- Baseline has outdated or unfair parameters.
- Proposed controller uses full SUMO state while baseline uses realistic sensors.
- Controller comparison ignores yellow/all-red/min-green constraints.
- RL or MPC controller is compared to fixed time only and then claims general superiority.
- Single seed or single demand regime supports a broad robustness claim.
