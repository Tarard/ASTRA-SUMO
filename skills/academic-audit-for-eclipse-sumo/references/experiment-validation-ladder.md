# Experiment Validation Ladder

Use this to move from idea to defensible evidence.

## Ladder

1. **Intake**: produce and confirm the Experiment Readiness Record.
2. **Construction sanity**: verify network, routes, TLS, detectors, controller identity, and output configuration.
3. **Single-case smoke**: run one headless case and check logs, outputs, and basic controller action.
4. **Sweep**: vary one meaningful axis at a time after construction sanity passes.
5. **Ablation**: remove or simplify model/controller components to identify mechanism.
6. **Sensitivity**: vary demand, seeds, observability, noise, detectors, route uncertainty, control interval, and horizon where relevant.
7. **Runtime feasibility**: compare mean, p95, and max controller compute time against the control interval.
8. **Claim decision**: classify evidence and write only supported claims.

## Debugging Discipline

Do not start with parameter tuning. First check:

- code vs model consistency;
- simulator construction validity;
- controller identity;
- output completeness;
- hard mathematical or simulator boundaries.
- official operational pitfalls: TraCI stop behavior, multi-client synchronization, route validity, insertion backlog, teleports, random seeds, and output closure.
- community FAQ patterns: connection failures, stale TraCI sessions, generated-route surprises, missing devices, detector coverage, and version mismatch.

Only then run parameter sweeps.

## Cause Classes

| Cause class | Signal | Response |
|---|---|---|
| Parameter cause | Sweep changes behavior and reveals a stable region | Report chosen value and sensitivity |
| Structural cause | Extreme parameters do not change the outcome | Change network, observability, phase structure, or claim |
| Model-claim cause | Run is valid but does not support the intended claim | Demote or rewrite the claim |
| Construction cause | Network, routes, TLS, outputs, or logs fail hard gates | Fix construction before analysis |
| Operational cause | SUMO/TraCI run protocol, seed, insertion, teleport, or output timing changes the result | Fix run protocol or demote to diagnostic |

## Design Response Chain

Every controller term or experiment module should have:

```text
observed problem -> understood cause -> design response -> validation check
```

Example pattern:

```text
low observability -> queue estimate is biased -> add detector/probe fusion -> test penetration-rate sensitivity
```

## Stop Rule

When evidence shows a structural limitation, stop tuning and report the limitation. A diagnostic result can still be valuable if the boundary is clear.
