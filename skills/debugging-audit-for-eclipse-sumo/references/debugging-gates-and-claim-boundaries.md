# Debugging Gates and Claim Boundaries

Use this to decide whether a debugged run can re-enter academic evidence.

## Gates

| Gate | Pass evidence | Fail response |
|---|---|---|
| Reproduction gate | exact command and inputs reproduce the symptom or success | ask for artifacts or mark blocked |
| Construction gate | network, route/demand, additional files, detectors/TLS load without unresolved errors | `construction-invalid` |
| Run-protocol gate | TraCI lifecycle, client order, close behavior, horizon, and output timing are documented | `diagnostic-demo` |
| Controller-observability gate | controller event log and SUMO state/output show the intended action-response path | no controller performance claim |
| Completion gate | natural completion or fixed-horizon unfinished accounting is explicit | no travel-time superiority claim |
| Stochasticity gate | seeds and variation are reported for stochastic components | no robustness/generalization claim |
| Regression gate | the fix does not change demand, route, seed, horizon, or metrics unless disclosed | rerun all compared baselines |

## Fix Classification

Classify every fix:

- `construction-fix`: files, route, network, detector, TLS, station, or demand corrected.
- `protocol-fix`: command, path, port, TraCI lifecycle, output naming, close/wait behavior corrected.
- `controller-fix`: controller logic, action timing, object IDs, phase/program mapping, or command sequence corrected.
- `evaluation-fix`: metric denominator, unfinished vehicles, statistics output, seed reporting, or claim wording corrected.
- `workaround`: makes a run continue but does not resolve the root cause.

Workarounds do not restore formal evidence unless the limitation is disclosed and the claim is narrowed.

## Claim Rewrite

Use:

```text
symptom -> root cause evidence -> fix status -> claim status
```

Examples:

```text
TraCI could not connect
-> exact SUMO command failed with missing route file
-> construction-fix applied and rerun passed hard gates
-> eligible for evidence after baseline rerun
```

```text
travel time improved
-> 30% of demand never arrived before fixed horizon
-> evaluation-fix required
-> no superiority claim until completion/backlog is reported
```

```text
teleports disappeared after --time-to-teleport -1
-> vehicles deadlocked in network
-> workaround revealed gridlock
-> construction-invalid or stress-diagnostic
```

## Escalation

Escalate to official docs/source/issues when:

- local artifacts and a minimal reproduction contradict documented behavior;
- version-specific behavior changes the conclusion;
- a simulator crash occurs after valid inputs and protocol are proven;
- community advice conflicts with installed-version behavior.

When escalating, prepare a minimal package with SUMO version, command, config, network, routes, additional files, controller snippet, logs, and expected/observed behavior.
