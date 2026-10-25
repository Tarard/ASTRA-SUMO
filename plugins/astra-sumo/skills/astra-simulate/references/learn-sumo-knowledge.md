# SUMO Knowledge

Use to answer how SUMO or TraCI behaves without running a simulation, or to settle conflicting sources. Say when the answer depends on the SUMO version (`astra preflight` reports it), the generated network or a local script.

## Source order

1. The current Eclipse SUMO documentation (sumo.dlr.de/docs), including the TraCI API pages.
2. Official SUMO source, examples and tests (github.com/eclipse-sumo/sumo).
3. The SUMO mailing list, GitHub issues and maintainer answers.
4. Peer-reviewed papers and benchmark documents that state their SUMO details.
5. Public traffic-simulation code: evidence of practice, not of correctness.

When sources conflict, report the conflict and follow the highest applicable source. Do not turn a forum habit into a rule without checking the official semantics.

## Answer these from the official documentation

- network construction, junction logic, lane indices, connection direction and route connectivity;
- TLS phase strings, controlled links (`linkIndex`), yellow and all-red, `actuated` and NEMA parameters, detector definitions;
- the TraCI connection lifecycle, stepping, `getMinExpectedNumber`, subscriptions, and GUI versus server behaviour;
- the meaning of `summary`, `tripinfo`, `edgeData`, `laneData`, `queue`, detector and TLS state outputs.

Reproduce on the command line, headless, before interpreting the GUI. The GUI helps diagnosis; it is not evidence.

## Imported networks

- A network is not ready for experiments because it opens in the GUI, has many traffic lights or survives a one-step load. It needs a completed `astra network routeability` run and a signal review; construction belongs to `$astra-build`.
- High-speed through roads and ramps can dominate route choice and delay. Remove or isolate them only when the study is about arterial signals, then rerun `astra network routeability`.
- netconvert warnings such as minor green, turn radius, lane not connected from an incoming edge, or intersecting left turns are not fatal by themselves. Review the affected movements and run a route smoke test.
- Rebuilt or joined traffic lights can get abnormal cycles. Audit every `tlLogic` cycle length and longest phase. netconvert `--tls.set`, `--tls.rebuild` and `--tls.cycle.time` rebuild plans; any option that changes protected-left time, phase splitting or timing changes signal semantics and must be reported.
- When adding signals "at intersections", define an intersection operationally, for example existing TLS plus non-dead-end junctions with road degree of at least 3, then review them on a map. Degree-2 geometry points, boundaries and dead ends are not intersections.
- Keep real edge lengths unless the study is deliberately abstract. After pruning, check the median, 95th percentile and maximum edge length.

## Lessons from public code

- SUMO-RL: environment wrappers, observation and reward design, episode termination.
- RESCO: paired baselines, standard scenarios, reproducibility.
- LibSignal: benchmark management and controller comparison; its abstractions can hide SUMO details.

Check a repository's licence and SUMO version before borrowing from it.
