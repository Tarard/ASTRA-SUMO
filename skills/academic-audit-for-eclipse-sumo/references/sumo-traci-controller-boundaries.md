# Eclipse SUMO TraCI Controller Boundaries

Official TraCI traffic-light changes: https://eclipse.dev/sumo/docs/TraCI/Change_Traffic_Lights_State.html

## API Boundary Table

| TraCI path | Evidence it supports | Claim boundary |
|---|---|---|
| `setPhase` | Switches to a valid phase index in the current program | Valid phase-sequence integration evidence; not NEMA proof by itself |
| `setPhaseDuration` | Modifies remaining duration of current phase | Timing intervention evidence; not persistent cycle design by itself |
| `setProgram` | Switches to a predefined program | Program-switch evidence; verify loaded program semantics |
| `setProgramLogic` | Inserts a program definition | Static/program construction evidence; audit states, durations, and transitions |
| `setRedYellowGreenState` | Directly sets state string and program becomes online | Online state override; script must handle phases/transitions |
| NEMA-specific calls | Updates SUMO NEMA timing parameters | NEMA timing-update evidence only if a NEMA program is loaded |

## Common TraCI Patterns

SUMO docs describe a common adaptive-control pattern: load a program with long green durations and use `setPhase` to switch. This is a valid integration pattern, but it must be reported as TraCI phase control unless the underlying program semantics support a stronger claim.

Direct state control via `setRedYellowGreenState` can bypass normal program behavior. Do not describe it as normal static, actuated, or NEMA operation unless the program is restored and audited.

## Debug Boundaries

SUMO FAQ: https://sumo.dlr.de/docs/FAQ.html

- `TraCIFatalError: connection closed by SUMO` usually points to a SUMO-side failure. Inspect SUMO logs and input files.
- Connection reset by peer often points to script-side behavior, such as ending without clean close.
- TraCI client and SUMO version mismatch can invalidate debugging conclusions.
- Use `--log <FILE>` for headless diagnostics.

## GUI and Server Behavior

When using SUMO-GUI as a TraCI server, commands may require starting the simulation. Record whether the run used `sumo` or `sumo-gui`, whether `--start` was used, and whether GUI actions changed behavior.
