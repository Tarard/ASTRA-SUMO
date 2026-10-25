# Signal Controllers in SUMO

Use to audit, apply or build a traffic-signal controller: fixed-time, SUMO actuated, NEMA, max-pressure, Webster, SOTL, MPC-style, RL or custom TraCI control. Pairing rules and fair baselines for a controller comparison are in `plan-experiment.md`.

## ASTRA test beds

- `astra workflow sumo_nema_four_way_reference_workflow <req.json> --json` with `{"output_dir": "<new-dir>", "require_real_sumo": true}` writes a four-arm NEMA reference intersection (PlainXML, `tlLogic`, NEMA additional file), builds it with netconvert and runs a short SUMO check. Read `sumo_smoke_status`: without `require_real_sumo`, a missing `sumo` gives `skipped` and still `status: pass`.
- `astra workflow sumo_network_standard_nema_phase_binding <req.json> --json` with `{"net_file": "<net.xml>", "output_dir": "<new-dir>"}` writes a review queue of the network's signal junctions. Add `"junction_id"` to build a separate NEMA candidate for that junction. It accepts only an isolated, vehicle-only, geometrically standard three- or four-arm junction, never changes the source, and stays `review_required` because generic NEMA timings are not a field signal plan.
- A synthetic intersection from a text description: `sumo_intersection_scene_workflow` (`prompt`, `output_dir`); see `$astra-build`.

ASTRA has no runtime max-pressure, Webster, SOTL, MPC or RL controller. Write such a controller in the user's project as a TraCI loop, and do not report it as applied until it has run.

## What to establish for each controlled signal

TLS id; controller family; phase list and green phases; controlled links in `linkIndex` order (`traci.trafficlight.getControlledLinks`); incoming and outgoing lanes; the movement-to-phase map; detector ids and lanes; yellow and all-red policy; behaviour when data are missing. Unsupported phases or ambiguous links block the claim.

| Control pattern | Evidence that it happened |
|---|---|
| read-only observation | the TraCI getters used, and when in the step loop they run |
| direct phase control | a log of `setPhase`, `setRedYellowGreenState` or equivalent commands |
| timing or program control | program ids, phase durations, offsets and update times |
| detector-based control | detector ids, lanes, aggregation window, missing-data behaviour |
| multi-intersection coordination | shared clock, offsets, communication timing, conflict handling |

- If SUMO's own `actuated` or NEMA program is active, show that the external controller neither overrides it silently nor is overridden by it.
- Direct state strings must not create conflicting greens. Check them against the controlled links and movement permissions.
- Commands must respect the existing transitions: yellow, all-red and minimum green.
- A phase index read from the GUI, or a detector map made before the network was regenerated, does not survive a rebuild. Recheck both against the current `.net.xml`.

## Building an external controller

Each step: observe SUMO state, update the controller state, choose the next phase, choose its duration or switch time, issue the TraCI command (or write a TLS plan), and log the action with its TLS id.

| Family | Needs |
|---|---|
| max-pressure | queue or occupancy per incoming lane, and a map from each green movement to its outgoing lanes |
| Webster | flow assumptions per approach and lost time |
| SOTL | detector or queue thresholds per approach |
| fixed-time | a documented cycle, splits and offset |
| actuated | detector placement, with SUMO's `actuated` type or TraCI switching rules |

The public project `docwza/sumolights` shows these patterns. It is GPL-3.0, so check licence compatibility before copying its code into a project.

## NEMA checks

- Ring, barrier, phase, split, offset and recall parameters match the intended design.
- `traci.trafficlight.setNemaSplits`, `setNemaMaxGreens`, `setNemaCycleLength` and `setNemaOffset` target the right controller and cycle.
- Max recall and force-off behaviour suit the comparison being claimed.
- Detector lanes match the controlled movements in the generated network.

## A controller that seems to do nothing

Check in order: SUMO loaded the intended TLS program and additional files; the loop ran for the expected steps; commands targeted the intended TLS ids; the phase, movement and detector maps match the network; the logged actions changed the simulated signal states.
