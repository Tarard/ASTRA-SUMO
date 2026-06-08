# Source Ladder

Use this source order for academic audits of experiments using Eclipse SUMO:

1. **Installed SUMO and local artifacts**: `sumo --version`, `sumo --help`, command lines, logs, generated XML/CSV/JSON, network files, route files, additional files, and controller code.
2. **Official Eclipse SUMO documentation and source/tests**: prefer official pages and the Eclipse SUMO repository for semantics.
3. **Official support channels and reproducible reports**: FAQ, issue tracker, mailing list, and forum threads are useful for failure modes, not proof by themselves.
4. **Public traffic-control codebases and benchmarks**: useful for evaluation structure, baseline classes, and reproducibility patterns.
5. **Academic papers and reports**: useful for claim framing and evaluation norms, but simulator semantics still require SUMO sources.
6. **Agent memory or notes**: use only as hints.

## Verification Rules

- If an option name matters, check the installed binary with `sumo --help`, `sumo-gui --help`, `netconvert --help`, or the relevant tool help.
- If TLS or TraCI semantics matter, verify against official documentation for the installed version when possible.
- If a public codebase shows a pattern, copy the evaluation idea, not the code or the claim.
- If a forum answer conflicts with official docs or local behavior, run a minimal reproduction.

## Primary SUMO Anchors

- NEMA: https://sumo.dlr.de/docs/Simulation/NEMA.html
- Traffic lights: https://eclipse.dev/sumo/docs/Simulation/Traffic_Lights.html
- TraCI traffic-light changes: https://eclipse.dev/sumo/docs/TraCI/Change_Traffic_Lights_State.html
- Traffic-light outputs: https://sumo.dlr.de/docs/Simulation/Output/Traffic_Lights.html
- Summary output: https://sumo.dlr.de/docs/Simulation/Output/Summary.html
- TripInfo output: https://sumo.dlr.de/docs/Simulation/Output/TripInfo.html
- Statistic output: https://sumo.dlr.de/docs/Simulation/Output/StatisticOutput.html
- Vehicle insertion: https://eclipse.dev/sumo/docs/Simulation/VehicleInsertion.html
- Teleports: https://sumo.dlr.de/docs/Simulation/Why_Vehicles_are_teleporting.html
- Randomness: https://eclipse.dev/sumo/docs/Simulation/Randomness.html
- Routing and route repair: https://sumo.dlr.de/docs/Demand/Shortest_or_Optimal_Path_Routing.html
- FAQ and TraCI debugging: https://sumo.dlr.de/docs/FAQ.html
- Official support/contact: https://eclipse.dev/sumo/contact/
- User mailing-list archive: https://www.eclipse.org/lists/sumo-user/
- Eclipse SUMO forum: https://www.eclipse.org/forums/index.php/f/486/
- Official repository: https://github.com/eclipse-sumo/sumo
