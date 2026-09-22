# Learn SUMO Knowledge

Use this reference when answering SUMO/TraCI semantics questions, resolving source conflicts, or extracting reusable lessons from official documentation, forums, issue trackers, and public traffic-simulation code.

The goal is source-bounded guidance. Do not turn forum posts or public code habits into universal rules without checking official SUMO semantics.

## Source Ladder

Prefer sources in this order:

1. Current Eclipse SUMO official documentation and TraCI API docs.
2. SUMO source, examples, and tests from the official project.
3. SUMO mailing list, forum, GitHub issues, and maintainer answers.
4. Peer-reviewed papers and benchmark docs that disclose enough SUMO details.
5. Public traffic-simulation code, treated as implementation evidence, not correctness proof.
6. User-discovered field lessons, after privacy and source checks.

If sources conflict, report the conflict and follow the highest applicable source unless the project has a documented reason to do otherwise.

## Official Semantics Checklist

Use official sources for:

- network construction, junction logic, lane indices, connection direction, and route connectivity;
- TLS phase strings, controlled links, yellow/all-red behavior, actuated behavior, NEMA parameters, and detector mapping;
- TraCI connection lifecycle, simulation stepping, expected vehicle counts, subscriptions, command boundaries, and GUI/server behavior;
- output semantics for `summary`, `tripinfo`, `edgeData`, `laneData`, `queue`, detector outputs, and TLS state outputs.

Prefer headless command-line reproduction before GUI interpretation. GUI inspection is useful for diagnosis, not sufficient evidence for formal claims.

## High-Value Operational Lessons

- Natural TraCI completion should usually wait for `getMinExpectedNumber() == 0`, meaning no active or expected vehicles remain.
- SUMO running without crashing is not enough; inspect warnings, teleports, insertion backlog, missing outputs, and route errors.
- `--ignore-route-errors`, `--ignore-errors`, disabled teleport behavior, or demand changes can be diagnostic tools, but they change claim status unless justified.
- Output paths must be unique across runs; overwritten outputs invalidate comparison evidence.
- Fixed-horizon studies must report unfinished vehicles and insertion backlog before average travel time or delay.
- Random trips, OSM imports, route generation, and detector creation should record seeds, commands, versions, and generated artifacts.

## Imported Network Modeling Gates

For OSM, netconvert, Netedit, or other imported networks, keep construction work separate from controller evidence. Use derived versions rather than overwriting a validated network, and keep rollback paths, keep/remove edge lists, commands, warnings, and smoke outputs.

Treat network modeling as a ladder:

1. **Geometry/topology candidate**: visual map and scoped rule, such as removing irrelevant classes, ramps, service roads, isolated components, or low-value length outliers.
2. **SUMO construction validity**: XML parses, a short headless SUMO load works, and component checks report the intended main component.
3. **Routeability smoke**: fixed-seed trips or routes pass routing validation, then a headless SUMO run writes `tripinfo` and `summary`.
4. **Signal semantic audit**: TLS IDs, cycle lengths, movement availability, joined TLS behavior, and warnings are understood.
5. **Formal experiment readiness**: paired demand, horizon, outputs, controller logs, and completion policy are fixed before comparing methods.

Do not call a network experiment-ready merely because it opens in GUI, has many traffic lights, or passes a one-step SUMO load.

## OSM Cleanup and TLS Modeling Heuristics

- Prefer reproducible removal rules over hand deletion: record road classes, isolated edges, largest component selection, and any length or class threshold.
- For signal-control corridor studies, high-speed through roads and ramps may dominate route choice and delay; remove or isolate them only when the scientific target is arterial signal coordination, and then rerun routeability smoke tests.
- Preserve real metric lengths unless the experiment explicitly studies an abstract network. If pruning creates very long low-class links, audit median, p95, and max edge lengths before changing geometry.
- When adding "signals at intersections", define intersection operationally and state that the rule is a modeling heuristic, not a SUMO or OSM standard.
- A useful screening heuristic is existing TLS plus non-dead-end road junctions with undirected road degree at least 3, followed by map, tag, and manual review. Avoid converting degree-2 geometry points, boundaries, and dead ends into fake signals merely because they are SUMO junctions.
- Rebuilding TLS can create anomalous joined controllers with very long cycles. Audit every `tlLogic` cycle length and max phase duration. If a joined TLS is abnormal, use source-bounded netconvert rebuild options such as `--tls.set`, `--tls.rebuild`, and `--tls.cycle.time`, then verify movement behavior.
- Options that change protected-left time, phase splitting, or signal timing must be reported because they change signal semantics.
- Netconvert warnings about minor green, turn radius, lane not connected from incoming edge, or intersecting left turns are not automatically fatal. They are construction warnings requiring visual/movement audit and route smoke before formal claims.
- In PowerShell or other shells, avoid fragile quoting for route-generation attributes. Malformed attributes can create invalid trips XML; inspect generated trips when routing fails before blaming the network.

## Community and Public-Code Lessons

Use community/forum patterns to form probes:

- empty `tripinfo` usually points to no arrivals, wrong output wiring, or too-short horizon;
- route errors often point to disconnected networks, vehicle type mismatch, invalid edges, or generated demand outside the routeable graph;
- TraCI failures often originate in SUMO startup, port reuse, command ordering, or stepping before connection state is stable;
- detector/TLS mismatches often come from lane IDs, controlled-link ordering, phase-index assumptions, or generated network changes;
- inconsistent results often trace to unpaired seeds, changed routes, overwritten outputs, or incomplete runs.

Use public code to learn workflow patterns:

- SUMO-RL highlights environment wrappers, observation/reward design, and episode termination discipline.
- RESCO-style benchmarks highlight paired baselines, standard scenarios, and reproducibility pressure.
- LibSignal-style frameworks highlight benchmark management and controller comparison, but project abstractions may hide SUMO details.
- Other SUMO control repos can reveal practical logging, config wiring, and batch-run patterns; inspect before borrowing.

## Output Shape

```text
question:
source_level:
source_used:
answer:
experiment_risk:
required_evidence:
claim_boundary:
```

If the answer depends on a SUMO version, generated network, or local script behavior, say so explicitly.
