# Plan a SUMO Experiment

Use for a new or vague experiment request, and for comparisons of controllers or perturbation mechanisms.

## Before running

- Read what exists first: the `.sumocfg`, network, route and additional files, controller code and earlier outputs. Do not ask the user what a file already answers.
- Decide, and state in your answer, the claim, scope, demand and arrival type, baseline, metrics with the output file each comes from, completion policy, seeds and per-run output paths. Mark assumptions.
- Ask only when an answer would change the claim, for example natural completion or a fixed horizon, or which baseline counts.

## Pairing rules

- Besides the paired inputs listed under "Facts that prevent mistakes" in `SKILL.md`, keep yellow and all-red times, lost time, detector placement and phase availability identical unless one of them is the studied variable, and name that variable in the design.
- Name the baseline family. Never compare against a broken, uncalibrated or unobserved baseline.
- Natural completion (run until no vehicle is expected) suits controller comparison. A fixed horizon must report unfinished and waiting vehicles, and the planned-demand denominator, with every average.
- Choose seeds from the scenario definition or the arrival features before any treatment result exists, never from treatment performance.
- Run root-cause fixes as separate diagnostic runs, not inside the formal comparison.

## Arrival type

Arrival type is the timing pattern of vehicles entering the network or a demand group. Record it separately from demand volume, OD structure, route choice and controller.

| Arrival type | Meaning |
|---|---|
| `smooth_even` | near-uniform over the window; a diagnostic simplification, often unrealistic at signals |
| `poisson_random` | independent random arrivals around a target rate |
| `platoon_burst` | clusters separated by gaps, from upstream signals, ramp metering or parking exits |
| `scheduled_pulse` | concentrated around known schedules: events, shift changes, school release, ferry or train arrivals |
| `detector_matched_profile` | follows measured count bins; build it with `$astra-calibrate` |
| `mixed_profile` | several patterns; state which demand groups use which |

## Perturbation and mechanism comparisons

Use when the question is whether a localization signal, a mechanism channel or an information interface helps a controller.

- Fix the spatial scope first: the core segment or zone, the affected junctions, edges and lanes, why the scope matters, and what stays outside. Every perturbation acts on that same scope; otherwise location confounds the comparison.
- Vary the mechanism, not the place: extra demand whose routes cross the scope, lower speed inside it, reduced capacity in it (a closed lane or a restricted movement), or changed signal timing, phase availability or controller information.
- Besides the paired inputs, keep fixed the scope, background demand, warm-up, output parser, vehicle types, route generation and controller update interval.
- Include a primary baseline, a localization-only treatment, the full-information treatment, a shuffled or misaligned information control, and an operational baseline. Localization-only beating the baseline shows that location is informative. Full information beating localization-only shows that the mechanism channels add value. A shuffled control that does worse shows that alignment matters. An operational baseline that wins limits the performance claim, not the information claim.
- Report scope-level and network-level metrics against the same baseline, `reduction_pct = 100 * (baseline - method) / baseline`. Use metrics that exist for every perturbation type, such as edge or lane delay, time loss, throughput or queue; a trip-level metric may suit a demand pulse but not a speed or capacity change.
- If an impact signal targets one node while the perturbation spans several edges, say so. Check that predicted impact nodes map to controllable signals, movements or detectors. An impact signal gives localization scope, not pairwise coupling, unless coupling is computed. Its graph must follow the road network (say so if intermediate nodes are abstracted), and a fine-grained mechanism claim needs proof that the logged coordinates, entities and mappings exist in the network.
- Check that every planned run, across perturbation, intensity, controller and seed, acts on the intended scope.

Demote the claim when perturbation locations differ, the scope is not visible in the generated SUMO files, a controller label has no explicit configuration, a baseline row is missing for a seed and perturbation, completion differs and is not reported, a single seed or scope is described as robustness, or an information claim has no negative control.
