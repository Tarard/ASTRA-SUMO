# Eclipse SUMO Official Semantics

Use official Eclipse SUMO sources when construction or simulator semantics matter.

## Network and Route Construction

- Record the SUMO version, command line, net files, route files, additional files, and scenario metadata.
- Treat invalid XML, missing routes, unreachable edges, and failed network loading as construction failures before debugging TraCI.
- For route feasibility, check missing connections, insertion failures, unfinished vehicles, and route completion.
- For imported or converted networks, record the conversion command and relevant `netconvert` options.

## Traffic-Light Semantics

Official traffic-light documentation: https://eclipse.dev/sumo/docs/Simulation/Traffic_Lights.html

- SUMO traffic-light phase state strings index controlled lane-to-lane connections, not whole approaches.
- Link TLS indices explain which movement each state character controls. Inspect `.net.xml` connection `linkIndex` attributes or use SUMO-GUI link-index visualization.
- Signal-state characters have different semantics. Do not collapse `G`, `g`, `s`, yellow, red, and off into one "green/red" simplification when safety or right-of-way matters.
- Actuated traffic lights may generate internal detectors. Missing or unusable detector warnings limit claims about actuated behavior.
- Mesoscopic simulation does not support all traffic-light features the same way microscopic simulation does; verify mode-specific support.

## Headless Before GUI

- A GUI run is visual inspection. It is not a substitute for repeatable headless evidence.
- Run headless with outputs and logs first. Then use GUI to inspect geometry, link indices, detector placement, and traffic-light behavior.

## Official Test and Source Anchors

- Official repository: https://github.com/eclipse-sumo/sumo
- Traffic-light tests: `tests/sumo/tls/`
- TraCI traffic-light tutorial tests: `tests/complex/tutorial/traci_tls/`
- NEMA source path: `src/microsim/traffic_lights/NEMAController.cpp`

When behavior is surprising, search official tests before assuming the simulator is wrong.
