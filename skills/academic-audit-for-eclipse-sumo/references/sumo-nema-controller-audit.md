# Eclipse SUMO NEMA Controller Audit

Official NEMA documentation: https://sumo.dlr.de/docs/Simulation/NEMA.html

## Controller Identity

Require direct evidence before calling a controller NEMA:

- loaded `tlLogic type="NEMA"`;
- NEMA phase numbers via phase `name`;
- ring/barrier configuration;
- phase `minDur`, `maxDur`, `yellow`, and `red`;
- split, offset, coordinated mode, force-off, recall, detector, and controller-type parameters when claimed;
- runtime evidence from SUMO outputs or controller metadata.

Do not infer NEMA identity from file names, variable names, class names, or a paper section title.

## Timing and Parameters

Audit:

- `minDur`: minimum green;
- `maxDur`: maximum green or split-derived green in coordinated mode;
- `yellow`: yellow time;
- `red`: red clearance;
- `fixForceOff`: fixed vs floating force-off behavior;
- `controllerType`: TS2 vs Type 170 offset semantics;
- `latchingDetectors` and cross-phase switching parameters when used;
- `ignore-errors`: formal evidence should reject or demote runs that rely on it.

## NEMA TraCI Timing Calls

SUMO exposes NEMA-specific timing updates:

- `traci.trafficlight.setNemaOffset`
- `traci.trafficlight.setNemaMaxGreens`
- `traci.trafficlight.setNemaSplits`
- `traci.trafficlight.setNemaCycleLength`

Official docs state timing updates in the NEMA controller happen after the current cycle ends. Claims about immediate timing effects need runtime evidence.

## Demotion Rules

Classify as `diagnostic-demo` or `claim-overreach` when:

- the controller uses `setPhase` on a program that is not evidenced as SUMO NEMA;
- direct state override changes the program to an online state;
- `ignore-errors=true` is needed;
- timing errors are ignored;
- detector warnings remain unresolved;
- output does not show actual NEMA runtime behavior.

## Source Anchors

- NEMA docs: https://sumo.dlr.de/docs/Simulation/NEMA.html
- NEMA source path: `src/microsim/traffic_lights/NEMAController.cpp`
- Traffic-light docs: https://eclipse.dev/sumo/docs/Simulation/Traffic_Lights.html
