# Develop and Verify SUMO/TraCI Code

Use this reference when implementing or modifying SUMO/TraCI experiment code: controllers, TLS/NEMA mapping, route or config generation, metric parsers, batch runners, validators, plotting/export scripts, or audit utilities.

The default method is RED -> GREEN -> REFACTOR. If the existing project has no test harness, create the smallest observable check before changing behavior.

## When This Is Required

Use this path for:

- controller logic or phase-selection changes;
- TraCI stepping, connection, or state-query changes;
- route, demand, detector, or `.sumocfg` generation code;
- metric parsing, aggregation, or plotting code;
- experiment runners, batch scripts, seeds, output naming, or reproducibility tooling;
- validators, audit scripts, and public-release checks.

## Test Ladder

Choose the lowest test that exposes the behavior:

| Level | Use for | Evidence |
|---|---|---|
| Pure unit | parser, formula, config assembly, phase-map utility | deterministic test input/output |
| Fixture XML | SUMO file parsing, TLS states, detector maps, route/config wiring | tiny committed fixture |
| Mock TraCI | controller decision logic independent of SUMO process | fake state, expected command |
| Headless smoke | SUMO startup, route loading, output generation | short run with fixed seed |
| Paired experiment | baseline/controller comparison | same demand, seed, horizon, outputs, completion rule |

## RED

Before implementation, state:

```text
behavior_to_change:
smallest_test:
why_it_fails_now:
fixture_or_command:
expected_failure_signal:
```

Do not use a full SUMO experiment as the first test if a smaller unit or fixture test can expose the bug.

## GREEN

Implement the narrowest change needed to pass the failing test.

SUMO-specific rules:

- Do not change demand, seeds, horizon, or output names unless the test explicitly targets them.
- Do not hide route or insertion errors with permissive SUMO flags and still call the run formal evidence.
- Do not alter yellow/all-red handling without updating controller-boundary and comparison evidence.
- If a test requires SUMO binaries, record versions and command lines.

## REFACTOR

Refactor only after the behavioral test passes. Preserve:

- controller decision sequence;
- route and demand identity;
- output schema;
- metric definitions;
- baseline pairing.

## Completion Gate

Before claiming done, collect:

```text
changed_files:
red_test:
green_result:
smoke_or_regression_command:
outputs_checked:
remaining_risk:
claim_status:
```

For SUMO runs, also verify:

- `summary.xml`, `tripinfo.xml`, or the declared output files exist and are readable;
- final vehicle completion/backlog/teleport/insertion state is reported;
- controller logs prove the intended controller actually issued commands;
- paired comparisons use identical route, demand, seed, horizon, output interval, and completion criteria.

If verification cannot be run, say exactly why and keep the result at `diagnostic-demo`, `construction-invalid`, or `blocked`.

## Code Review Gate

For review feedback:

- distinguish true bug, missing evidence, unclear claim, and reviewer preference;
- reproduce or reason from artifacts before applying changes;
- reject feedback that would silently change the experimental claim without updating the plan;
- rerun the smallest affected test after each accepted change.
