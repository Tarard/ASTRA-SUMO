# Ring1 native point-counter regression

These generated outputs describe an unfitted passenger cohort, not measured field traffic or calibrated demand.
The model uses the declared network programs, including placeholder timing.
All 500 vehicles arrive without collision or teleport.

The periodic file contains 156 intervals for 13 fields, including the completion tail.
It reports 538 complete contributions. The instantaneous file retains all 540 leave events.
Two events lack occupancy because their vehicles leave before a complete pass.
The shared reader reconciles the remaining events with every periodic count.
See the [pinned SUMO implementation](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/microsim/output/MSInstantInductLoop.cpp) and [E1 output definition](https://sumo.dlr.de/docs/Simulation/Output/Induction_Loops_Detectors_%28E1%29.html).

`manifest.json` binds the original outputs and normalized compressed files.
Normalization removes generator comments and retains all measurement attributes.
The instantaneous extract contains every leave row. Original complete files remain identified separately.
Instantaneous vehicle IDs are simulator instrumentation for offline model analysis. They are not information supplied by the real count fields.

The package also retains a failed ten-vehicle entry test on the same model.
Its 0.35 m source road produces three insertion collisions and teleports with either the recorded position or the source default.
The collision data is failure evidence. The arriving vehicle count does not make that run valid.
Use the model digest in the manifest and the retained route inputs to investigate this boundary condition.
