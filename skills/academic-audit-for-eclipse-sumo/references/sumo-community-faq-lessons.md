# Eclipse SUMO Community FAQ Lessons

Use this when an Eclipse SUMO task resembles a recurring mailing-list, Eclipse forum, GitHub issue, or user-support problem. Treat community material as a failure-mode index, not as final simulator semantics.

## Source Boundary

Community sources can suggest what to inspect first. They cannot by themselves prove a paper claim. Always confirm with:

1. local SUMO version, command, logs, and artifacts;
2. official documentation or source/tests;
3. a minimal reproducible case when behavior is surprising.

Do not copy user code from forums. Do not quote private-looking details from support threads. Paraphrase the operational lesson.

## Recurring Symptoms and Audit Actions

| Symptom | Likely first checks | Academic audit response |
|---|---|---|
| `FatalTraCIError: Could not connect` | Print the exact SUMO command; run it in a normal shell; check binary path, `SUMO_HOME`, Python environment, port availability, firewall, and whether SUMO started with `--remote-port` when connecting externally. | Do not debug controller logic until SUMO startup and TraCI server creation are proven. |
| `connection closed by SUMO` after a few steps or many episodes | Inspect SUMO `--log` / `--error-log`; check whether SUMO quit due to input error, route error, GUI close, stale connection, missing `traci.close()`, or repeated-run resource handling. | Treat as a run-protocol or construction failure until logs identify the cause. |
| GUI run works but TraCI run fails | Compare the exact command, binary, working directory, config paths, `--start`, remote-port behavior, and output/log paths. | GUI success is not evidence that the headless/TraCI experimental command is valid. |
| Many repeated RL/training episodes fail randomly | Ensure each episode starts a fresh SUMO process or a documented `load` workflow, closes TraCI, uses unique output paths, and avoids stale ports/labels. | Do not report failed episodes as missing data without a failure-rate audit. |
| Multiple TraCI clients hang or change behavior | Check `--num-clients`, unique `setOrder`, every-client `simulationStep`, and every-client close behavior. | Multi-client ordering is part of the method, not implementation detail. |
| Vehicle commands appear to do nothing | Verify the vehicle is inserted and active; use the correct domain (`vehicle` vs `vehicletype`); check command timing, speed/lane-change modes, route state, and whether another controller overrides the command later in the same step. | Require controller-event logs showing command issue and observed state response. |
| Route replacement or rerouting fails | Check edge connectivity, vehicle class permissions, closed edges, required stops/via edges, rerouting-device assumptions, and whether the vehicle has already passed a mandatory point. | Treat as route feasibility or demand-construction evidence, not controller performance evidence. |
| Random trips, OSM imports, or generated routes behave oddly | Validate generated trips/routes; count discarded trips; inspect disconnected edges, fringe selection, turn restrictions, and vehicle-class permissions. | Generated demand must be audited before it becomes benchmark demand. |
| Teleporting vehicles appear in OSM or congested scenarios | Classify teleport reasons: jam, yield, wrong lane, disconnected route, collision, blocked vehicle, or rail/deadlock constraint. | Teleports can bias travel time, safety, emissions, and completion metrics; report or demote. |
| Setting `--time-to-teleport -1` creates deadlock | Inspect the underlying cause: demand overload, missing connections, priorities, traffic-light timing, lane-change/weaving, or wrong-lane insertion. | Disabling teleport is a diagnostic stress setting unless the deadlock behavior itself is studied. |
| Detector output looks empty or inconsistent | Verify detector type, lane placement, length, aggregation period, file path, lane permissions, and whether vehicles actually pass the detector area. | Detector-based claims need detector geometry and output coverage evidence. |
| Public transport, taxi, parking, charging, or station behavior fails | Check whether stops/stations/parking areas are on the current or reachable route, whether the needed device is enabled, and whether person/ride outputs are being used instead of vehicle-only outputs. | Use entity-specific statistics: vehicle, person, ride, transport, battery, or charging outputs. |
| Emissions, battery, or EV values look missing | Verify vehicle emission class, battery/emissions device activation, per-vehicle vs vehicle-type parameters, and version-specific units. | Energy/emission claims require explicit model/device configuration and unit reporting. |
| Collision or "vehicles pass through each other" reports | Check unsafe insertion settings, collision options, junction model, minGap, step length, lane-change model, and network geometry. | Safety claims require collision, emergency-stop, conflict, and configuration audits. |
| SUMO version or Python package mismatch | Verify `sumo --version`, `python -c "import traci; print(traci.__file__)"`, and whether `pip` TraCI or bundled SUMO tools are used. | Version mismatch invalidates debugging conclusions until reproduced with pinned versions. |

## Community Debugging Protocol

When a task matches a community FAQ pattern:

1. Reproduce with the exact command printed in full.
2. Add `--log`, `--error-log`, and unique output paths.
3. Minimize the network, route file, additional file, and controller until the symptom remains.
4. Classify the cause as environment, SUMO input construction, run protocol, controller logic, or simulator limitation.
5. Only after classification decide whether to fix, rerun, demote, or narrow the claim.

## Claim Boundaries from Community Patterns

- A workaround is not a validation method.
- A forum suggestion that makes a run continue does not make the run academically valid.
- Ignored errors, discarded trips, teleports, stale ports, GUI-only success, and missing devices must be disclosed or excluded from formal evidence.
- If the failure pattern is unresolved, label the run `diagnostic-demo` or `construction-invalid`.

## Search Terms for Future Audits

Use these query patterns when new symptoms appear:

- `site:eclipse.org/lists/sumo-user "FatalTraCIError" "connection closed by SUMO"`
- `site:eclipse.org/lists/sumo-user "Could not connect to TraCI server"`
- `site:eclipse.org/lists/sumo-user "Teleporting Vehicle" "SUMO"`
- `site:eclipse.org/lists/sumo-user "Route replacement failed"`
- `site:github.com/eclipse-sumo/sumo/issues "TraCI" "connection closed"`
- `site:eclipse.org/forums "Eclipse Sumo" "FatalTraCIError"`

## Source Anchors

- SUMO contact/support entry point: https://eclipse.dev/sumo/contact/
- SUMO user mailing-list archive: https://www.eclipse.org/lists/sumo-user/
- Eclipse SUMO forum: https://www.eclipse.org/forums/index.php/f/486/
- Eclipse SUMO GitHub issues: https://github.com/eclipse-sumo/sumo/issues
- TraCI connection troubleshooting example: https://www.eclipse.org/lists/sumo-user/msg10158.html
- TraCI repeated-run connection example: https://www.eclipse.org/lists/sumo-user/msg01784.html
- Teleporting scenario discussion: https://www.eclipse.org/lists/sumo-user/msg02146.html
- GitHub TraCI startup issue example: https://github.com/eclipse-sumo/sumo/issues/14050
