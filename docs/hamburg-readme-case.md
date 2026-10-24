# Hamburg case shown in the README

The lowest measured healthy main-window count error is the [chronological OD feedback result](../benchmarks/hamburg_ring1_chronological_od_feedback_20261024_v1/README.md): MAE **2.115384615**, TAE 275, SSE 2833 and maximum error 22. All 8589 input vehicles enter and arrive, with zero waiting, running, collisions, teleports or discards. Each has one unique intended-field contribution, and the 56 values for 535 Z5–Z8 remain exact. Calibration of the 130 main-window values remains incomplete.

Initialization counts retain every native interval attribute, with TAE 97 and SSE 1117. Across all 182 values, TAE is 372 and derived station MAE is 6.125. The first main window has 11 exact fields; Z12 still lacks five contributions and Z13 lacks four. With fixed initialization, current field totals and one intended contribution per request, Z5 lacks one and 535 Z4 lacks eleven contributions in the necessary inventory bound. This limits that fixed input, not the observations or the overall calibration goal.

The complete input chain includes three deletions from the former 8597 best, 269 first-window departures delayed 30 seconds, one counter-prefix Z11 route and five further Z5 deletions. Roads, controls, types, original route sections after 24483192 and real targets stay fixed. The counter route adds a 22nd OD pair, so actual quantities and metadata use 308 cells. This result cannot be attributed to only the final five deletions. Its direct input parent was measured only to 4500 seconds; unmeasured future values are not filled from the separate complete 448 reference. Compact prefix records retain the actual 13 values and 117 explicit unknowns.

The preceding [late-demand quantity comparison](../benchmarks/hamburg_ring1_119_late_demand_thinning_20261024_v1/README.md) has healthy MAE 2.492308, TAE 324 and SSE 3598, with all 8597 vehicles arriving.

The three nested input subsets contain 8612, 8610 and 8597 requests, compared with the source's 8640. Surviving vehicle XML, intentions, departure times, routes, stops and relative order remain unchanged. Controls, driving rules and real observations stay fixed. Initialization TAE stays 97; the lowest-error case has all182 TAE 421 and derived station MAE 7.95. The 169 equal E1 prefix intervals do not establish identical trajectories. These finite input-prior tests do not identify real OD quantities or establish an optimum.

Two [early-window quantity responses](../benchmarks/hamburg_ring1_early_quantity_feedback_20261024_v1/README.md) retain healthy completion but worsen full main130 TAE to 356 and 448. The combined case adds ten explicit hypothetical requests and removes three; the independent removal-only case deletes those same three from the 8597-request source. First-main-window TAE falls from 13 to 9 and 10, while initialization counts remain identical. Those local gains do not replace the better full130 result above. Original records, new-ID versus survivor accounting and the post-analysis offset-sign correction remain available.

A [761 lane fan-out comparison](../benchmarks/hamburg_ring1_761_fanout_20261024_v1/README.md) preserves each of two parent demands while adding two continuations at one priority node. Both complete safely, but main130 TAE rises from 324 to 448 for N8597 and from 365 to 442 for N8640. Initialization TAE rises from 97 to 143. Ordinary roads remain exact; SUMO's shared internal logical length changes to its native group mean of 8.555760785474 meters. Source geometry and three-car actual-via coverage support a model hypothesis, not field certification. Worse count fit does not disprove the physical connection. The better result above remains unchanged.

The preceding [535 late-departure comparison](../benchmarks/hamburg_ring1_535_late_departure_feedback_20261024_v1/README.md) has healthy MAE 2.807692, TAE 365 and SSE 4117, with all 8640 vehicles arriving.

Only 29 existing 535 Z4 requests depart 30 seconds earlier than in the healthy 525 source; the other 8611 requests stay fixed. Routes, fields, controls, driving rules and all observations remain unchanged. Initialization TAE stays 97, all182 TAE falls from 492 to 462, and derived station MAE falls from 9.725 to 8.975.

A failed compression and the +30/−60/−90-second comparisons remain in one package. The two larger advances move one request to the preceding OD period, with actual metadata rebuilt. The five finite responses do not establish monotonicity, convergence, a global optimum or a capacity bound. Their 156 equal E1 prefix rows do not establish identical vehicle trajectories.

The preceding [525 partial-control comparison](../benchmarks/hamburg_ring1_266197525_partial_control_20261024_v1/README.md) has healthy MAE 3.038462, TAE 395 and SSE 5039. Only four 734 movements lose their independent signal binding at 525. The two counter movements retain their control, and the native conflict-yielding relation stays fixed. Roads, curves, lengths, vehicle permissions, all programs, demand and observations stay fixed. Actual actuated histories can change despite unchanged programs.

That source comparison lowers initialization TAE from 136 to 97, all182 TAE from 557 to 492, and derived station MAE from 9.875 to 9.725. However, maximum error rises from 21 to 26 and Z5 main-window TAE from 79 to 108.

Six bounded smoke calls retain all 96 arrivals, separate car and bicycle yielding episodes, corrected API decoding and the original review verdicts. The first construction was blocked for new internal waiting segments; the second explicitly preserves the source waiting structure. These results support the declared model comparison, not field control or SPaT certification.

The preceding [209 lane-connection comparison](../benchmarks/hamburg_ring1_209_ordinal_connection_20261024_v1/README.md) has healthy main MAE 3.238462, TAE 421, SSE 5131 and maximum error 21. It retains the +15 source demand, observations, lane permissions and driving rules, while correcting the declared 603 lane order and adding a primary continuation from 734 lane0. Aggregate main130, initialization52, all182 and derived40 errors improve against +15. Field regressions remain: 119 Z5 main-window TAE rises from 67 to 79 and 535 Z4 from 41 to 63. Initialization TAE is 136, better than +15's 168 but worse than offset0's 105; all182 TAE is 557 and derived station MAE is 9.875. The source/model date and control applicability remain `review_required`. The 209 MAP/KML with 2022 publication metadata, unresolved OCIT/SPaT, source observation, failed constructions and five-car support test are retained in the same package. The old source observer is not treated as a required unchanged prefix of the new network.

A [single Z5 departure-spread test](../benchmarks/hamburg_ring1_209_z5_spread_20261024_v1/README.md) did not improve that earlier 209 reference. It expands 521 source-main-Z5 departures by 1.5 around each source-bin min/max midpoint. Fifty-five requests cross into a previous bin, with actual OD-period metadata rebuilt; routes, fields, controls and observations remain fixed. All 8640 vehicles arrive safely, but main MAE rises to 3.284615, SSE to 5529 and maximum error to 31. Initialization counts remain identical and the 56 protected 535 values stay exact. Z5 gains 19 main contributions while Z4 loses 17, and Z12/Z13 errors increase. This pair does not establish a capacity limit or a correct departure prior. The 3.238462 reference, complete non-improving result and metadata history remain available.

The preceding [+15-second common119 offset sensitivity](../benchmarks/hamburg_ring1_119_common_offset_20261024_v1/README.md) has healthy MAE **3.538462**, SSE 5818 and maximum error 24, with all 8640 vehicles arriving. Initialization absolute error rises to 168 and all182 error to 628. Its original `rejected` verdict remains: it fails only the study's stated all182-error gate. This is a conditional Pareto point, not completed calibration or measured field SPaT.

The same study's +30-second point has healthy MAE **3.569231**, SSE 6942, initialization absolute error 91 and all182 error 555. It is also `rejected`, solely because main-window SSE exceeds the original 6709 reference. The −15-second point has MAE 4.630769 and 89 unfinished vehicles. All three preserve the explicit demand, observations and current relative timing of the two 119 controllers; all have zero safety events and retain the 56 exact 535 Z5–Z8 values. These finite single-seed points do not establish a smooth response, an optimum or the field's absolute phase origin.

The original offset-0 reference is the [conditional GS266 priority comparison](../benchmarks/hamburg_ring1_119_receiving_priority_20261023_v1/README.md). Native field MAE is **3.715385**, SSE 6709 and maximum error 27; initialization absolute error is 105 and all182 error is 588. All 8640 vehicles enter and arrive, with zero collisions, teleports and discards. All 56 initialization and main-window values for 535 Z5–Z8 remain exact. Of the 130 main-window field values, 71 are exact and 59 still have nonzero residuals. Calibration of the 130 main-window values remains incomplete.

This reference keeps the corrected roads, geometry, legal turns, vehicle permissions, demand and observations. At the real merge after 119, it replaces the independently inferred GS signal with native priority operation. Native right of way gives the 603 approach priority over the 790 approach. Existing sources do not establish that priority direction or the independent field signal identity. This is a declared model hypothesis, not a verified field control repair or field SPaT.
Relative to the preceding 4.653846 model, all 34 unfinished vehicles now arrive, main-window absolute error falls from 605 to 483, and the 40 derived station values have MAE 10.975. However, initialization absolute error rises from 78 to 105, and 119 Z4 main-window absolute error rises from 91 to 128. The worst interval is 119 Z4 at 7200–8100 seconds: 68 observed and 41 simulated vehicles. The complete 130/40/52/182 results and these regressions remain in the linked package.
The matching 7200-second observation preserves 104 E1 intervals, 72000 traffic-summary steps and all 51 actual signal histories within that prefix. Two-car comparisons do not reproduce the full-traffic stop by themselves. Two further pairs verify actual native yielding at shared exits, with four safe arrivals. These tests support the bounded comparison, not universal merge safety or field correctness.

A separate [native lane-level conflict comparison](../benchmarks/hamburg_ring1_119_lane_foes_20261023_v1/README.md) is not adopted. It preserves the three shared-exit or crossing conflicts and removes five noncrossing, different-exit pairs at the same merge. Controls, demand, geometry and all real observations stay fixed. Three small pairs arrive safely, but the full run worsens main-window MAE to 5.938462 and SSE to 14184, with 61 requests waiting and 94 vehicles still running. Initialization absolute error improves to 61 and 535 Z5–Z8 stay exact; these gains do not override the full-run regression. This comparison does not establish that the native option is physically wrong or that zero residuals are infeasible. The healthy 3.715385 candidate remains the working reference for demand feedback.

Two [bounded Z4 departure comparisons](../benchmarks/hamburg_ring1_healthy_z4_feedback_20261023_v1/README.md) also fail their stated selection conditions. They change one and three explicit departure times while retaining all 8640 vehicles, routes, lane intentions, controls and 294 OD-period counts. The requested four-vehicle cap yields only three eligible moves. Native MAE rises to 4.092308 and 4.576923, with six and 100 unfinished vehicles. Both retain the 535 zero counts and have no collision, teleport or discard.
In the one-vehicle comparison, delaying departure by 31 seconds leaves that vehicle's native detector notification at 9899.0 seconds, while 253 unchanged vehicles change their field period. The checked traffic records remain identical before the changed insertion, and unchanged vehicles retain their input order. This shows why translating the old passage lag is not a verified demand response; the healthy 3.715385 reference remains selected. [All 130 residuals on a common scale](../benchmarks/hamburg_ring1_healthy_z4_feedback_20261023_v1/baseline-count-review/all130-before-after.png) compare that reference with the preceding 197-only model, not with these rejected departure changes.

A [passive control-history comparison](../benchmarks/hamburg_ring1_healthy_z4_control_response_20261024_v1/README.md) reproduces both the healthy reference and the rejected one-car run. All 20 native correspondence checks pass for each recording. The current model has 40 actuated and 10 static controllers. The first signal-color difference is pedestrian-only at 8943 seconds; the first passenger-movement color difference follows at 8948 seconds. Traffic already differs at 8247.6 seconds, so signal response cannot explain the earliest traffic changes. Recorded signal colors describe the simulation, not field SPaT. Original phase indices are compared only at emitted events.

A [fixed simulated-history pair](../benchmarks/hamburg_ring1_healthy_z4_fixed_history_20261024_v1/README.md) then executes the healthy reference's recorded colors in both runs. The unmodified-demand replay exactly reproduces the healthy native traffic. Under those same colors, the one-car change yields MAE 3.746154, SSE 6929 and all 8640 arrivals. Its own detector notification remains at 9899 seconds. Fixing the colors changes the response but still does not make the departure adjustment improve the count fit. This conditional comparison neither identifies field SPaT nor replaces the healthy working reference.

A [bounded joint OD proposal](../benchmarks/hamburg_ring1_healthy_joint_od_feedback_20261024_v1/README.md) also fails native acceptance. It changes 22 period quantities while preserving every OD total, the total of 8640 vehicles and the initialization quantities. The observed assignment predicts main-window absolute error of 368.413577, but the new native run gives 504, MAE 3.876923 and SSE 8858, with 34 vehicles still running. The existing quantile generator also changes 1102 common departure times and some route, stop and field templates. Thus the test includes those realization changes; it is not a pure 64-vehicle time shift. The complete response, unchanged observations and all failed gates remain available. The assignment table describes the source run; it is not a verified marginal response to changed demand.

The [same OD quantities with 64 explicit time changes](../benchmarks/hamburg_ring1_healthy_joint_od_stable_20261024_v1/README.md) retain every vehicle ID, route, stop and intended field. The other 8576 requests remain unchanged. This alternative realization also fails: native MAE is 5.1, SSE 12467, and 130 vehicles remain running. All initialization counts stay identical, all 535 Z5–Z8 values stay exact, and safety-event counts remain zero. Its largest departure change is 5408.089 seconds; this comparison has no 900-second shift limit.

That audit also establishes a necessary limit for fixed field assignments and fixed initialization counts. Z5 has 623 assigned requests, with 102 already counted during initialization. At most 521 can contribute during the main window, whose measured target is 522. That restricted realization cannot reach zero residuals. Legal changes to field shares can remove this quantity limit, but their native effects still require measurement; the bound does not explain the remaining large errors.

A [single legal lane-share change](../benchmarks/hamburg_ring1_z5_single_share_20261024_v1/README.md) removes that quantity shortfall while preserving all vehicle IDs, departure times, routes and OD quantities. One existing moving-stop lane and its intended field change from Z4 to Z5. The changed vehicle actually contributes once at Z5 and reaches the original exit. All 8640 vehicles arrive safely, and Z5 has 624 complete contributions over the full run. However, 72 occur after the main window; main-window MAE rises to 3.938462 and SSE to 8032. The change establishes legal execution and sufficient planned quantity, while failing the count-improvement rule. It provides a healthy reference for further demand tests without replacing the better 3.715385 count fit.

The same evidence traces the 761/623 Z4/Z5 prior to the earlier `probe-minus1` quantile generation. The original prior had 760/624. That count perturbation duplicated one Z4 template and omitted one Z5 template. The restriction appears when those resulting field totals are subsequently fixed; it is not an explanation for all count errors.

A [field-consistent paired demand direction](../benchmarks/hamburg_ring1_one_share_field_stratified_feedback_20261024_v1/README.md) separates 29 OD/field groups across 14 periods. Both sides preserve OD and field totals, routes and initialization, changing only 16 departure times each. The source assignment predicts main absolute errors of 480 and 544. Native errors are 663 and 667, with MAE 5.1 and 5.130769 and 98 and 97 unfinished vehicles. Both sides fail the stated conditions against both the 3.938462 search reference and the 3.715385 best fit. All observations and failed responses remain available. Consistent field groups and full row rank in the descriptive matrix do not establish a useful native response direction or a feasible zero-residual solution.

A [normal-road exit-prior comparison](../benchmarks/hamburg_ring1_normal_exit_prior_20261024_v1/README.md) replaces 882 parking-ending route tails with an existing paired road cut; actual destinations and 332 metadata indices change. Its four-car smoke verifies route execution only. The full run is rejected: MAE 4.261538, SSE 9390 and two vehicles still running at the fixed end. The road cut and 100% through prior do not establish real destinations or parking shares.

A [causal initialization-prefix study](../benchmarks/hamburg_ring1_causal_initial_prefix_20261024_v1/README.md) retains all 8640 future requests and all 182 real observations. Five 900-second runs give absolute errors of 22, 22, 7, 7 and 0 for the first 13 initialization rows; the other 169 rows remain explicitly unmeasured in those short records. The zero-prefix candidate preserves its first 900 seconds in the full run but is rejected: main-window MAE 5.469231, SSE 11917, initialization absolute error 86 and 133 unfinished vehicles, with no safety events. This does not replace the best main-window fit or establish complete calibration.

A [second initialization-prefix study](../benchmarks/hamburg_ring1_causal_second_prefix_20261024_v1/README.md) measures 0–1800 seconds from the fixed stage-one input. The reference and two candidates have absolute errors of 30, 3 and 0 across 26 initialization rows, while preserving the first 13 E1 intervals and traffic prefix. The zero-prefix case records 901 unique intended-field contributions, but 156 real values remain unmeasured and no full stage-two validation is claimed.

A [whole-period timing extension](../benchmarks/hamburg_ring1_cohort_timing_extension_20261024_v1/README.md) retains those 901 requests and advances the remaining 7739. Its new input preserves 26 exact initialization rows in both the 1800-second prefix and full run, but the full result is rejected: main-window MAE 4.323077, SSE 9896, initialization absolute error 42 and 35 vehicles still running, with no safety events. The 379 vehicles that cross a departure period change 53 OD-period quantities with L1 distance 114. There is no full stage-two parent comparison that isolates the extension's effect; the original offset-0 reference remains 3.715385.

The preceding [197 through-connection correction](../benchmarks/hamburg_ring1_197_through_support_20261023_v1/README.md) provides the source-supported road geometry retained in the current candidate.
Official 535 lane geometry and aerial evidence support two missing continuations across the parking access: incoming motor lane3 to outgoing lane4, and lane4 to lane5. The correction keeps all 238 ordinary roads, existing complete internal curves and lengths, vehicle-class permissions, crossings and legal turns. It adds two signal indices and changes three existing protected-green states to permissive green so the parking left turn and U-turn yield to the new straight movement. Phase parameters and old signal colors are preserved; the actual actuated history can change with traffic.
With the same 8640 requests, native field MAE falls from 5.092308 to **4.653846**. All vehicles enter, with zero collisions, teleports and discards. However, SSE rises from 6898 to 9473, maximum error rises from 27 to 35, and 34 vehicles remain running. This is not completed calibration.
All 56 real initialization and main-window values for 535 Z5–Z8 are now exact; their 1459 vehicles all arrive. The remaining 34 vehicles all originate at `963776077#0` and target 119 Z5: 25 have not completed that detector and nine have passed it in the tail period. The worst main-window residual is 119 Z5 at 6300–7200 seconds, with 59 observed and 24 simulated vehicles.
Ten bounded native probes retain nine passes and one field/route-incompatible fixture, followed by a successful corrected pair. The complete 130/40/52/182 results, 208 E1 intervals, rejected construction attempts and independent checks remain available. [Fixed-range aerial comparisons and NetEdit views](../benchmarks/hamburg_ring1_197_through_support_20261023_v1/review/README.md) show the scoped change. Field SPaT and true OD remain unverified; the former 5.092308 result is retained as the comparison on the earlier network.

A [matching 54-vehicle observation](../benchmarks/hamburg_ring1_197_z5_observation_20261023_v1/README.md) preserves all 208 E1 intervals, 144000 traffic-summary steps, 154367 instantaneous events and 8606 arrived routes. Of the earlier network's 32 contributions in the worst Z5 interval, 12 keep their passage time, 12 arrive later within the same interval, and eight move into the next interval. This explains the new loss from 32 to 24, but not the original shortfall from 59 to 32. The first route-exit differences appear at `24483344#0` or `#1`; actual 119 and GS266 signal histories remain identical across both full runs.
At the last one-second sample, 29 of the 34 unfinished vehicles are moving and five are stopped. These observations narrow the receiving-space investigation; they do not establish a deadlock or justify a control change. The complete cohort, unchanged comparison vehicles, source hashes and raw-event clock conventions remain available.

The [five-round joint OD study](../benchmarks/hamburg_ring1_joint_od_spsa_20261023_v1/README.md) has ended. It reuses the verified 5.092308 initial evaluation and adds ten native perturbation evaluations and one final replay.
The final replay has MAE 6.2, SSE 9692, maximum error 29, 45 vehicles waiting to enter and 19 still running. All eleven new runs have zero collisions, teleports and discards, but none improves the initial count fit. The 5.092308 reference remains the comparison for that earlier-network study; calibration is incomplete.
Independent checks reproduce all five updates, integer demand generation and all native 130-field and 40-derived-station results. Updates three and four change real-valued parameters without changing the generated integer demand. Five completed updates do not establish convergence.

The [two-vehicle route-support check](../benchmarks/hamburg_ring1_multifield_native_support_20261023_v1/README.md) tests a limitation of that fixed OD prior.
The selected 8640-request run produces 8626 complete contributions from 8626 distinct vehicles, each at one field. The fixed 21-OD model cannot generate two legal boundary-to-boundary routes that connect 119 Z12 and 535 Z4.
Both routes pass the native check on the unchanged network and control: each vehicle completes both E1 contributions and reaches its exit, with no collision, teleport, discard or unfinished trip.
This establishes isolated route support under the declared driver assumptions. It does not establish real route shares or zero count error.
The [full-demand substitution tests](../benchmarks/hamburg_ring1_multifield_substitution_20261023_v1/README.md) then replace one and twenty pairs of separate trips with these two-field trips. All retained departures and planned field totals per departure period stay unchanged; network, control and observations retain their original bytes.
Every substituted vehicle completes both fields, with no collision, teleport, discard or repeated field contribution. One pair leaves MAE unchanged at 5.092308 but raises SSE to 6964 and waiting/running counts to 27/16. Twenty pairs raise MAE to 6.5 and SSE to 12123, with 29 waiting and 76 running.
Both fail the declared count-and-backlog rule. The legal additional routes are now tested under full traffic, but these two allocations do not improve the selected reference and do not establish real OD shares.

The [matching observation replay](../benchmarks/hamburg_ring1_multifield_delay_trace_20261023_v1/README.md) preserves all 208 E1 intervals, 144000 traffic-summary steps, 235746 instantaneous events and 8596 arrived routes from the one-pair case.
It shows that the substituted vehicle already follows a legal early lane path and waits behind an unchanged 535 Z4 vehicle. Its through-route exit time did not predict its position in the left-turn service queue. No additional lane waypoint is justified by this observation.
The receiving-control source check also finds that node `1975693623` is a priority junction in the legacy network, while the current network inherits an automatically inferred actuated controller. The original OSM node has no signal tag; official 535 approach geometry and a nearby signal point are retained as evidence. The controller's independent field status and possible duplication remain `review_required`; no control removal is accepted.

The [observed-service pair test](../benchmarks/hamburg_ring1_multifield_service_pair_20261023_v1/README.md) instead removes the unchanged left-turn vehicle observed ahead of the substituted trip. The resulting 8639-request run has exactly the same 208 native counts, all 130/52/182 comparisons and 40 derived station counts as the 8640-request reference. The same 21 vehicle IDs remain waiting or running.
Its 8626 complete contributions come from 8625 vehicles, including one two-field trip; the reference has 8626 contributing vehicles. One interval's speed and occupancy attributes differ, so this is count equality rather than full simulation equality. MAE remains 5.092308 and SSE 6898, with zero collisions, teleports and discards. The strict improvement rule still rejects it. This is a concrete alternative demand with the same count vector, not a count-fit improvement, true-OD identification or zero-residual result.

The [Z5 upstream delay record and paired lane-prior tests](../benchmarks/hamburg_ring1_z5_upstream_delay_20261023_v1/README.md) retain a matching observer replay and two new complete evaluations.
The observer adds vehicle positions, leaders, lane changes and signal states. All 208 native E1 intervals, 144000 traffic-summary steps and 8619 arrived routes match the selected OD case.
The focal long delay follows a stopped leader on `234423652#1_2` after the LSA104 body hold has ended. Actual LSA104/118 states match the declared programs; they are not field SPaT.
Exchanging one pair of Z4/Z5 lane intentions lowers diagnostic MAE to 4.969231 and SSE to 6636, but leaves 10 waiting and 15 running vehicles.
Its Z4/Z5 absolute errors increase by 7 and 3; the aggregate improvement comes from other fields. The fresh native records also show changes to the affected vehicle's entry lane and passage time.
Five pairs allow all 8640 vehicles to enter, with 16 still running, but increase MAE to 5.523077 and SSE to 8644. Both evaluations have zero collision, teleport, discard, repeated field contributions and wrong-field contributions.
These explicit lane-prior variants retain every OD quantity, route, departure and field total per departure period. They are separate from the quantile OD model. Both fail the stated joint count-and-backlog selection rule, so the 5.092308 OD reference remains available and neither variant is formally accepted.

The [four OD response probes](../benchmarks/hamburg_ring1_classical_od_response_20261023_v1/README.md) follow the [classical-method reassessment](research/hamburg-classic-calibration-2026-10-23.md).
The rebuilt 294-cell prior preserves the repaired network and all 8640 initial explicit requests. Four fresh native evaluations test one OD's adjacent departure periods at integer amplitudes of 6 and 1; these are directional measurements, not SPSA iterations.
The selected `probe-minus1` reduces field MAE from 5.961538 to 5.092308, SSE from 10749 to 6898 and maximum error from 32 to 27.
Waiting requests fall from 68 to 7, with 14 vehicles still running. Collisions, teleports and discarded requests are zero. Its derived station MAE is 14.8.
All 130 main observations and 52 initialization observations remain intact. The initialization counts are unchanged, while Z12/Z13 field errors worsen despite the overall improvement.
The existing quantile generator changes 151 departure times and 56 lane-template assignments for this two-cell count adjustment. The two cells' Z4/Z5 intentions change from 77/76 to 78/75. This is a joint empirical-prior response, not a pure single-vehicle time shift or an identified real OD.
The selected vector remains a diagnostic candidate. The goal of 130 exact native fields and valid completion is still unmet; all three rejected probes are retained.

The [LSA119 single-connection regression](../benchmarks/hamburg_ring1_119_single_split_20261023_v1/README.md) reproduces two collisions with four vehicles and a measured leader speed boundary.
Only the Z4 lane-0 left movement changes to a complete native internal path. The paired candidate has no collisions.
The candidate preserves all ordinary roads, legal turns, complete curves, signal programs and exterior elements.
The regression retains native speed safety checks. Its measured leader boundary does not establish natural four-vehicle queue formation or field control.
Count calibration remains incomplete. Intermediate MAE reductions and the historical V1 metric do not satisfy the current 130-field zero-residual goal.

The [complete route and timing comparisons](../benchmarks/hamburg_ring1_route_timing_feedback_20261023_v1/README.md) retain seven native evaluations and all 130/40/182 rows.
The complete single-connection candidate has field MAE 5.961538, maximum error 32 and zero collisions or teleports.
It still has 68 waiting and 50 running vehicles. Its derived station MAE is 17.275, so calibration remains incomplete.
The uncorrected half-route split caused two collisions and is rejected despite its lower error than earlier candidates.
Four bounded departure batches of 16, 32, 64 and 107 vehicles all worsened native field error and were rejected.
The unchanged observations, failed candidates and source-bound timing projections remain available. Projection arithmetic alone does not validate traffic response.

The [historical README V1 replay](../benchmarks/hamburg_ring1_readme_v1_zero_20261023_v1/README.md) now matches all 40 station bins: MAE and maximum error are zero.
All 5037 vehicles arrive without collisions, teleports, discarded requests or insertion backlog.
Four departure times change by at most 14 seconds. Observations, routes, network and detector positions remain unchanged.
This July case counts unique station entry notifications. Its native `nVehContrib` comparison still has MAE 12.825.
It does not establish zero error for the October case's 130 native field bins or resolve the inherited detector and signal limitations.
All 22 complete native search runs, including rejected candidates, remain frozen.

The [Alsterglacis branch repair](../benchmarks/hamburg_ring1_primary_branch_repair_20261023_v1/README.md) restores two source primary roads omitted by corridor selection.
The selector previously counted ordinary road lengths but omitted internal junction paths. The corrected cost selects the shorter complete primary path.
The candidate retains all 236 existing roads and 683 lane turns. It restores two roads and eight source turns at three declared nodes.
The paired 8640-request native run reduces October field MAE from 14.030769 to 9.838462 and maximum error from 66 to 47.
Waiting requests fall from 744 to 178, with 227 vehicles still running. Collisions, teleports and discarded requests remain zero.
All 130 field bins and 40 derived station bins are present. The latter have MAE 27.575 and provide no independent evidence.
This meets the declared intermediate MAE and backlog limits. It remains uncalibrated, with nonzero residuals, unfinished traffic and unverified field control.
The 2589 post-detector tail changes represent a declared route prior, not a measured branch split.
The native warnings, including LSA535's missing green phase and emergency braking, remain retained.
LSA119 Z5 worsens: its ten-bin absolute error rises from 137 to 335 despite the aggregate improvement.
The [illustrated report](../benchmarks/hamburg_ring1_primary_branch_repair_20261023_v1/report.html) shows every field and the fixed-aerial comparison.

The [six preceding backlog trials](../benchmarks/hamburg_ring1_directed_backlog_20261023_v1/README.md) remain diagnostic and preserve all rejected results.
Control-only changes and frozen-lag departure adjustments did not reach the intermediate limits.
The original-seed check uses 8640 requests; the other five trials use the inherited 8603-request vector.
The failed preflight that selected 8622 requests did not start a native evaluation and has no measured count result.

The [complete Libsumo correspondence check](../benchmarks/hamburg_ring1_libsumo_parity_20261023_v1/README.md) reduces native duration from 958.258 to 547.051 seconds at the same 0.1-second step.
All 130 field counts, 40 derived station values and per-vehicle traffic records agree. Same-step records for different vehicles can have a different list order.
This single pair measures a 1.75-fold speed increase. Diagnostic MAE remains 16.730769, with 1381 waiting and 409 running vehicles, so calibration is still incomplete.

The [two directed OD timing checks](../benchmarks/hamburg_ring1_directed_od_time_20261023_v1/README.md) are complete and rejected.
Their native field MAEs are 16.923077 and 17.046154 against the retained repaired baseline's 16.730769.
Both have zero collisions and teleports, but remain backlogged. All 130 field and 40 derived station rows, including other-field losses, are retained.
No count calibration or zero-residual claim follows from these local response proposals.

The [LSA119 straight-curve repair](../benchmarks/hamburg_ring1_119_straight_order_20261023_v1/README.md) matches the recorded collision and isolates it with two vehicles.
Ordering two explicitly selected straight curves removes long internal returns while retaining ordinary roads, turns, signals and other movement curves.
The same 8603-request full evaluation has zero collisions and teleports, field diagnostic MAE 16.730769 and maximum error 58.
It remains unaccepted: 1381 vehicles never enter and 409 remain running. All 130 field and 40 derived station rows remain available.

The [authorized five-round extension](../benchmarks/hamburg_ring1_od_spsa_five_rounds_20261023_v1/README.md) stopped after two complete native evaluations and zero updates.
Its first positive perturbation reduced diagnostic field MAE from 17.761538 to 17.2, but caused one junction collision and teleport at `:LSA119_part0_10_1`.
The safety guard prevented the negative perturbation and later rounds. Neither evaluation is accepted.
All 130 field and 40 derived station rows remain available, together with read-only baseline entry and passage-time attribution.

The OD SPSA runner reads declared `junction_body_hold_nodes` from the prepared `scenario.json` (or `.json.gz`).
This preserves the LSA104 driver assumption when evaluating a repaired network. Preparations without that field retain the historical three-node default.
Cached native records must match the declared scope. A changed native call still requires a fresh evaluation rather than edited cache hashes.
Use `run_hamburg_od_spsa.py --stop-on-unsafe` to stop subsequent SPSA evaluations after a completed evaluation reports collision, teleport or discard.
Backlogged evaluations remain diagnostic losses; only healthy zero-residual results satisfy calibration acceptance.

The [LSA104 split-fanout diagnosis and complete-window check](../benchmarks/hamburg_ring1_104_split_fanout_20261023_v1/README.md) reproduces a collision with two vehicles.
An explicit native complete-path candidate retains all roads, turns, curves and signal programs.
With the declared LSA104 junction-body hold, the same final OD completes the 14400-second run with zero collisions and teleports.
It remains invalid: 1482 requests never enter, 394 vehicles remain running, and diagnostic field MAE is 17.761538, with only 5/130 exact rows.
All 130 field and 40 derived station rows are retained. Neither this result nor the native-only predecessor is accepted as calibrated.

The [completed first SPSA round, V3](../benchmarks/hamburg_ring1_od_spsa_completed_20261023_v3/README.md) retains all four native evaluations and every 130/40 comparison.
One update and its full remeasurement are complete. The final diagnostic field MAE is 15.646154 against 14.923077 initially, with six collisions, six teleports and 1212 waiting vehicles.
All evaluations remain invalid. The thirteen collisions in the perturbation and final runs occur on `:LSA104_part0_12_0`. No exact calibration or convergence claim is made.

The [SPSA first-round V2](../benchmarks/hamburg_ring1_od_spsa_first_round_20261022_v2/README.md) fixes a numeric-time metadata join and reuses the complete initial native replay.
Its initial 130/40 comparisons remain unaccepted: field MAE 14.923077, three collisions, three teleports and 976 waiting vehicles.
The confirmed one-update settings and zero-error acceptance condition remain unchanged.

The [standard ordinary SPSA study](../benchmarks/hamburg_ring1_od_spsa_standard_20261022_v1/README.md) fixes the user-authorized corridor settings.
The user selected one update and a complete native remeasurement. It derives the initial gain from its first perturbation pair and retains the zero-error acceptance condition.
Both [completed spillback control trials](../benchmarks/hamburg_ring1_spillback_repair_complete_20261022_v2/README.md) remain invalid, with all 130 field and 40 station residuals retained.

The [Antoniou literature and standard SPSA method check](research/hamburg-spsa-od-method-2026-10-22-v1.md) identifies the missing joint time-dependent OD estimation.
The standard update has passed known-solution and native E1 checks. The user selected the inherited 21-OD, 14-departure-interval baseline.
The baseline retains all 8640 initial requests and departure times while extending 2589 internal trip ends to a model exit. No Hamburg SPSA iteration result is claimed.
The read-only OD audit identifies 2589 source requests ending on an internal road. All 130 targets remain unchanged.

The [completed spillback check](../benchmarks/hamburg_ring1_spillback_diagnosis_20261022_v1/README.md) uses the existing 236-road run without new simulation or model changes.
It finds reported receiving queues reaching lane starts while upstream queues face declared static green at 119 Z15, Z4 and Z5, including before the first collision.
Pre-collision examples have 60, 14 and 14 consecutive one-second samples and no complete native E1 contribution in the corresponding intervals.
All 13 fields, 130 target rows and 40 derived station rows remain retained. These observations support model spillback; they do not establish excess unique demand, causal residual shares or real signal timing.

The [18-source pruning review and road inventory](research/hamburg-pruning-literature-2026-10-22-v1.md) separates corridor selection, peripheral demand, and junction simplification.
The [new five-junction corridor](../benchmarks/hamburg_ring1_munich_corridor_20261022_v1/README.md) reduces the previous 360 normal roads to 236.
It retains the existing complete arms of LSA104, 118, 119, 200 and 535, all 13 detector fields, and all 130 main-window targets.
The fixed-aerial before/after was rendered and shown before the new native count run started.
Road geometry, lane permissions, retained turn permissions and the nine official programs remain unchanged.
Entry shares are refit from existing native route templates and declared road-class priors.
The new continuous run completes 14400 s with all 130/40 rows available, but has one collision and one teleport at LSA104, 621 uninserted vehicles and 394 still running.
Diagnostic field MAE is 12.753846 and maximum error is 52, with 22/130 exact fields and 0/40 exact derived stations.
The run is invalid; bounded count correction is not started. All residuals, raw native records and the [new illustrated report](../benchmarks/hamburg_ring1_munich_corridor_20261022_v1/seed/report.html) remain retained.
The combined uninserted and running total rises from 950 to 1015. The new corridor is not accepted as a calibration improvement.

The previous network had 343 passenger-permitted roads, of which explicit demand used 116.
The 227 passenger-permitted roads without planned traffic were not automatically deletion candidates. The new selection records each retained road and its reason.

The [earlier bounded timing feedback](../benchmarks/hamburg_ring1_native_count_feedback_20261022_v1/README.md) is complete.
Both complete native trials are rejected: field total absolute errors are 1681 and 1691 against 1685 in the reference, while uninserted demand rises from 644 to 796 and 758.
All 130/40 residual rows remain available. That finite timing rule does not establish infeasibility and is not promoted.

The [control-domain source check and revised independent review](../benchmarks/hamburg_ring1_control_domain_sources_20261022_v1/README.md) leaves four candidate signal bindings unresolved.
Three candidates are nearer the separately registered LSA209. Proximity does not establish any binding.
No signal is removed or rebound. The unrun free-flow offset prototype is archived and excluded from the active code.
Exact count calibration remains unfinished. Saved-state restart is not validated, and a queued replay does not prove infeasibility.

The [119 green-service diagnosis and nominal-control comparison](../benchmarks/hamburg_ring1_119_control_diagnosis_20261022_v1/README.md) retains the same demand and all targets.
Read-only snapshots show a green 119 K2 followed by a red downstream signal and stopped vehicles. All 1778 E1 events through 3780 s match the reference.
The sampled lane head has no remaining waypoint. The unrun waypoint-retirement prototype was reverted after checking the speed-upper-bound semantics.
Fixing all 58 remaining OSM programs to their nominal sequences worsens initialization MAE from 4.635 to 10.885 and leaves 473 vehicles uninserted.
That combination is not promoted. Main-window measurements remain unknown in its prefix; the complete primary-entry reference remains unchanged.

The [complete western-entry test](../benchmarks/hamburg_ring1_western_entry_full_20261022_v1/README.md) is not promoted.
All 130/40 rows are available with zero collisions and teleports, but 934 vehicles never enter and 410 remain running.
Diagnostic field MAE rises from 12.962 to 14.369, maximum error from 59 to 62, and derived station MAE from 39.725 to 45.500.
All 522 rerouted requests enter through the existing Ring 1 boundary, but 147 have no complete field passage by the end.
The initialization prefix reproduces exactly. All other-field losses and all 130 residual decompositions are retained.
This share is rejected. The complete primary-entry measurement remains the reference, and remains uncalibrated.

The [western primary-entry prefix](../benchmarks/hamburg_ring1_western_entry_prefix_20261022_v1/README.md) retains all requests and times while changing 95 initialization and 427 future-main road prefixes.
The existing Ring 1 boundary routes pass a sparse 40-vehicle native check. The short service driveway receives no extra demand.
The initialization hour has zero collisions and teleports, but E1 contributions fall from 1716 to 1670 and diagnostic MAE rises from 4.635 to 6.596.
All other-field losses remain reported. Main-window effects are unestablished; all 130/40 main rows stay unknown in that prefix.
The complete primary-entry measurement remains the reference below. The western share's later complete-window measurement is reported above.

The [bounded native-lag timing check](../benchmarks/hamburg_ring1_native_departure_windows_20261022_v1/README.md) is not promoted.
It retains every vehicle, route and count target, and advances 2094 requests by at most 900 s using frozen native passage lags.
One initialization trial has zero collisions and teleports, but E1 contributions fall from 1716 to 1656 and diagnostic MAE rises from 4.635 to 6.019.
The original initialization cohort has 114 uninserted vehicles, compared with 120. Another 145 advanced main-window requests do not enter.
No main-window trial follows this failed timing rule. All 130/40 main rows stay unknown in that prefix.
The complete primary-entry measurement remains the reference below. Calibration is unfinished.

The [complete shared-population alternative](../benchmarks/hamburg_ring1_shared_population_full_20261022_v1/README.md) is not promoted.
It retains all 130/40 rows and has zero collisions and teleports, but 882 model vehicles never enter, compared with 644 in the primary-entry reference.
Diagnostic field MAE rises from 12.962 to 14.331 and maximum error rises from 59 to 71. All losses remain reported.
All 455 shared vehicles realize their two distinct fields. Target-ledger and route-signature correspondence do not establish useful demand timing or correct OD.
Its frozen-time check finds 333 declared pairs without a common uniform shift into both target windows with a 90 s margin. This is not physical infeasibility.
The primary-entry measurement below remains the reference, and remains uncalibrated. Further correction must use native time responses and consistent joint target windows.

The [complete primary-entry measurement](../benchmarks/hamburg_ring1_primary_entry_full_20261022_v1/README.md) retains every request and count target while moving 294 Z12 requests to the existing primary-road prefix.
It completes 14400 s with zero collisions and teleports. All six original collision participants rear-clear and arrive.
Uninserted demand falls from 984 to 644, but the run remains invalid, with 306 vehicles still running.
All 130/40 rows are available, with 31/7 exact. Diagnostic field MAE is 12.962 and maximum error is 59.
Derived station MAE is 39.725, while maximum error rises to 150. All losses and residuals remain reported.
The initialization hour reproduces all 52 prefix intervals exactly. This supports correspondence only, not exact calibration.
That complete measurement includes no request-time, signal or vehicle-count correction. Its subsequent terminal and route checks are reported below.

The subsequent [terminal query and buffer-lane check](../benchmarks/hamburg_ring1_terminal_queue_diagnosis_20261022_v1/README.md) retains all source files and advances no snapshot time.
Vehicle positions and active signal phases correspond, but 24 loaded lane-change modes differ from the recorded controller. The loaded pending API also does not represent the original 644 queued requests.
The sparse buffer-lane alternative changes lane use without additional E1 contributions or an earlier final E1 passage. It is not promoted as a capacity fix.

The [distinct-field shared-route probe](../benchmarks/hamburg_ring1_shared_field_probe_20261022_v1/README.md) completes 40 vehicles and 80 native E1 contributions safely.
Each vehicle contributes once to an upstream 119 field and once to 535 Z4 on one of four supported simple routes.
The existing first-field driver suffices for that route test. It does not establish larger-demand performance; the subsequent complete alternative is reported above.

The subsequent [shared-population initialization check](../benchmarks/hamburg_ring1_shared_population_prefix_20261022_v1/README.md) conserves all 182 initialization/main field quotas.
It uses 8185 model vehicles for the same 8640 observation targets, retaining unmatched independent requests.
In the initialization hour, 105 vehicles contribute to both declared fields, with no collisions, teleports, repeated fields or reversed field order.
E1 contributions rise from 1716 to 1735, while diagnostic MAE falls from 4.635 to 4.385 and maximum error falls from 50 to 37.
Exact initialization rows fall from 34 to 32; other-field losses remain reported. All 130/40 main rows stay unknown in that prefix.
Its [complete-window alternative](../benchmarks/hamburg_ring1_shared_population_full_20261022_v1/README.md) is reported separately and is not promoted. The target ledger and route response do not identify real OD or unique vehicle totals.

The [complete three-node driver run](../benchmarks/hamburg_ring1_three_node_body_full_20261022_v1/README.md) reaches 14400 s with zero collisions and teleports.
All six original collision participants rear-clear their junctions and arrive. All 412 observed exit changes at the third site occur after rear clearance.
It remains invalid: 984 vehicles never enter and 314 remain running. All 130/40 rows are available, with 32/6 exact.
Diagnostic field MAE is 13.654 and maximum error is 61. Derived station MAE is 43.225 and maximum error is 130.
That run includes no entry, departure-time, signal or integer-demand correction. The later entry measurement is reported above.
The report also discloses the earlier trace wrapper-summary overwrite. Native commands and measurements remain retained, and saved-observation comparison is now read-only.

The subsequent [primary-entry prefix](../benchmarks/hamburg_ring1_primary_entry_prefix_20261022_v1/README.md) moves 73 initialization and 221 future-main Z12 requests to an existing primary-road prefix.
All requests, times, targets, destinations and post-merge routes remain fixed. The declared 3:1 passenger-lane proxy is not measured OD or capacity.
In the initialization hour, uninserted demand falls from 187 to 120 and complete E1 passages rise from 1638 to 1716, with no collisions or teleports.
Z4 loses three passages and maximum bin error rises from 48 to 50. These losses remain reported.
All 130 main field targets and 40 derived station targets stay unknown in that prefix. Its complete-window evaluation is separate.

The [complete localized driver run](../benchmarks/hamburg_ring1_localized_junction_body_full_20261021_v1/README.md) reaches 14400 s without timeout and retains all 130 field rows and 40 derived station rows.
It is invalid: 1 collision, 1 teleport, 1367 uninserted vehicles and 491 still running. Only 29 field rows and 6 station rows match.
Diagnostic field MAE is 13.885 and maximum absolute error is 59. These are not accepted calibration metrics.
The [late-event trace](../benchmarks/hamburg_ring1_late_collision_trace_20261021_v1/README.md) matches every closed E1 interval, collision, completion count, hold event and arrived vehicle route record before 9420 s.
It confirms an exit lane change at node `18653534` while the vehicle rear still occupies its turning connection.
The full residual decomposition retains every request and separates time-bin transfers, uninserted demand and incomplete field passages.
That earlier two-node run includes no demand correction. The subsequent three-node measurement is reported above.

The subsequent [paired 40-vehicle exit test](../benchmarks/hamburg_ring1_late_exit_body_probe_20261022_v1/README.md) retains both original whole-route profiles and native lane permissions.
Both cases finish safely, with 20 complete E1 passages per assigned field and one assigned passage per vehicle.
Adding only node `18653534` delays every tested exit lane change until rear clearance. The earlier sparse case has eight changes before clearance.
The passenger permission check leaves only outlet lane 2 for the next road. Lane 1 is bicycle-only and remains restricted.
This artificial maneuver test does not replace any real count target. Its subsequent complete measurement is reported above.

The [full global junction-body-hold run](../benchmarks/hamburg_ring1_junction_body_full_20261021_v1/README.md) completes the planned duration with all 130/40 count rows available.
It has no collisions or teleports, but remains invalid: 2,771 vehicles never enter and 329 remain running. Diagnostic field MAE is 25.077, with maximum error 154.
Native records and an isolated suffix test identify a hold-release conflict on a 2.10 m connector. The global driver candidate is rejected.
The [localized 6000 s candidate](../benchmarks/hamburg_ring1_localized_junction_body_20261021_v1/README.md) releases that blockage and retains zero collisions in its prefix.
It preserves all targets with later rows unknown. Its subsequent complete run is reported above; count correction and zero-error calibration remain unfinished.

The [junction-body lane-hold candidate](../benchmarks/hamburg_ring1_junction_body_hold_20261021_v1/README.md) keeps all traffic inputs fixed.
Its initialization hour has no collisions or teleports. All four original collision participants clear their target junctions.
All 52 count rows remain available, with 33 exact, diagnostic MAE 6.577 and maximum absolute error 43.
There are still 147 uninserted and 145 running vehicles. First-entry-step lane changes and the existing 0.1 m terminal boundary remain declared limits.
This is an exploratory driver assumption. No new main-window result or accepted calibration is established.

The [native collision reproduction](../benchmarks/hamburg_ring1_collision_trace_20261021_v1/README.md) matches all preceding-hour E1 intervals, completion counts and vehicle route exit times.
It identifies an internal urgent lane change and two exit lane changes while a vehicle's rear still occupies the turning connection.
The relevant runtime conflicts already exist. A missing XML conflict bit is not an established cause.
No network or driver repair has been validated. The unsafe candidate and all residuals remain retained.

The [through-route coverage check](../benchmarks/hamburg_ring1_through_route_coverage_20261021_v1/README.md) corrects an excessive one-observed-road restriction.
Both artificial 40-vehicle cases finish safely with only their assigned complete E1 passages.
The larger initialization run has two collisions and two teleports. It is invalid despite lower diagnostic residuals.
All 52 initialization comparisons remain available. Collision causes at ordinary junctions `14398547` and `205069138` remain unresolved.
No new main-window run was performed. The latest 130/40-row result below remains unchanged.

The latest [initialization and K31/K2 main-window attempt](../benchmarks/hamburg_ring1_z15_k2_full_20261021_v1/README.md) retains all 130 field rows and 40 derived station rows.
Only 32 field rows and 7 station rows match. Diagnostic field MAE is 19.885, with maximum absolute error 86.
It timed out during the tail. Its last complete snapshot at 13,645 s records 2,373 uninserted and 390 running vehicles.
These are snapshot counts, not final completion. The run remains invalid and exact calibration remains unfinished.
The [preceding-hour paired check](../benchmarks/hamburg_ring1_z15_k2_routes_20261021_v1/README.md) improved Z15 counts but did not establish main-window success.

The subsequent [119 destination-prior check](../benchmarks/hamburg_ring1_primary_exit_prior_20261021_v1/README.md) retains every request time and count target.
Its paired artificial cases complete all 40 vehicles. Its initialization hour has 226 uninserted vehicles, down from 364.
All 52 initialization field rows remain available, with 33 exact. Diagnostic MAE is 7.788 and maximum absolute error 61.
Other directions lose passages. No new main-window run was performed, so the main 130/40-row result above remains unchanged.

The [end-to-end workflow trial](../benchmarks/hamburg_ring1_end_to_end_20261021_v1/README.md) supplies the complete artifact chain, fixed-aerial before/after figures, actual NetEdit captures, and every field/station comparison.
Its full-window native run is invalid: 50 collisions, 50 teleports, and 2,242 vehicles never inserted. Calibration remains incomplete; all 130 field rows and 40 derived station rows are retained.

The subsequent [local lane-guidance trial](../benchmarks/hamburg_ring1_local_lane_treatment_20261021_v1/README.md) keeps the same roads, signals, observations, vehicle routes, and departure times.
It has zero collisions and teleports, with 5,100 vehicles inserted. It remains invalid: 1,587 vehicles never inserted and 342 remained in the network.
All observation rows are available. Only 31 of 130 field rows and 8 of 40 derived station rows match exactly.
Its diagnostic field MAE is 20.154, with maximum absolute error 91. These are not accepted calibration metrics.
The [capacity diagnosis](../benchmarks/hamburg_ring1_capacity_screen_20261021_v1/README.md) concerns a finite fixed-lane planning model, not physical infeasibility of SUMO or field traffic.

The [local lane-hold probe](../benchmarks/hamburg_ring1_lane_section_hold_probe_20261021_v1/README.md) tests ten vehicles for each of the 13 fields.
All 130 test vehicles pass their assigned field once, restore normal lane choice, and arrive without collisions or teleports.
Its artificial 13-row count target is separate from the real 130-row calibration target.
The [full lane-hold run](../benchmarks/hamburg_ring1_lane_section_hold_full_20261021_v1/README.md) records 4,902 complete field passages, all at the assigned field.
It still fails calibration: 1,593 vehicles never inserted, 322 remained in the network, and only 29 of 130 field bins match.
All 40 derived station bins are available, with 6 exact. Diagnostic field MAE is 19.154 and maximum absolute error is 96.

A [single-entry departure advance](../benchmarks/hamburg_ring1_departure_advance_20261021_v1/README.md) is rejected under its fixed one-field-per-car population model.
Its native pre-window run records 206 complete passages. The remaining population cannot supply the required 6,687 window passages.
The [official preceding-hour counts](../benchmarks/hamburg_ring1_initialization_counts_20261021_v1/README.md) now provide 52 complete initialization field bins, totaling 1,953 field passages.
The original 130 comparison values remain unchanged. Independent vehicle totals remain unidentified.

The [separate initialization-population trial](../benchmarks/hamburg_ring1_initialization_population_20261021_v1/README.md) adds 1,953 model vehicles using those source counts and inherited field/route shares.
All 6,687 main-window vehicles and the original targets remain unchanged.
Its preceding-hour native run has zero collisions and teleports, but 420 initialization vehicles never inserted.
All 52 initialization field bins are available, with 32 exact. The main observation window was not rerun.
Entry allocation and departure pressure remain unresolved. This trial does not establish calibrated initialization or real distinct-vehicle totals.

The [single-entry prefix test](../benchmarks/hamburg_ring1_ballindamm_entry_probe_20261021_v1/README.md) replaces the Z15 Brandsende entry with an existing Ballindamm route.
Every request time and count target remains fixed. All 52 preceding-hour native counts remain identical.
Eight more vehicles enter but remain in the network. Arrival and complete field-passage totals do not increase.
The candidate is not promoted as a count improvement. The common post-merge constraints still need diagnosis before another demand change.

The [shared-route diagnosis](../benchmarks/hamburg_ring1_z15_K8_diagnosis_20261021_v1/README.md) retains all 309 Z15 initialization requests, including running and uninserted vehicles.
Both entry routes use LSA200 K8. Its installed source SP5 gives 5 s green per 90 s, with six native road-front exits in each of 38 successive cycles.
Those 230 road-front exits remain separate from the 222 native E1 complete passages.
This identifies a constrained approach under the declared route and control model. It does not establish field capacity or justify changing the signal program.

## Site identity

The two `torii-v1-*` README figures depict the Binnenalster / Ring1 corridor, including LSA118.
The registered five-junction preset contains nodes `104`, `118`, `119`, `200`, and `535`.
Its definition is in [hamburg_corridor_candidate.py](../plugins/astra-sumo/src/astra_sumo/core/hamburg_corridor_candidate.py).
The four displayed station count streams are `35822`, `33023`, `30666`, and `26585`.

This case is separate from the [Am Sandtorkai workflow](hamburg-sandtorkai-codex-execution-workflow.md).
That named corridor uses nodes `2349`, `2394`, and `2403`.
Its existing network fixtures do not establish results for the README case.

## Published count inputs

The official service still publishes the four displayed count streams.
Their metadata identifies processed station totals, with a 15-minute count interval.
The station composition lists constituent detector fields. Those field names are not SUMO lane numbers.
Stations 33023 and 26585 include fields with different published direction labels.
Their observation operator follows the explicit field membership. It does not assume one corridor direction per station.

| Stream | Published detector composition |
|---|---|
| [35822](https://iot.hamburg.de/v1.1/Datastreams(35822)) | `0119-Z.13,0119-Z.11,0119-Z.12` |
| [33023](https://iot.hamburg.de/v1.1/Datastreams(33023)) | `0119-Z.5,0119-Z.4,0119-Z.15` |
| [30666](https://iot.hamburg.de/v1.1/Datastreams(30666)) | `0535-Z.6,0535-Z.7,0535-Z.8,0535-Z.5` |
| [26585](https://iot.hamburg.de/v1.1/Datastreams(26585)) | `0535-Z.14,0535-Z.4,0535-Z.9` |

A local snapshot retrieved on 2026-10-20 covers `2026-10-06T00:00:00Z` to `2026-10-20T00:00:00Z`, excluding the latter endpoint.
Each stream contains 1,344 distinct intervals, with no missing grid entries or invalid count values.
The service records each interval's final second as `hh:14:59`, `hh:29:59`, `hh:44:59`, or `hh:59:59`.
These input checks do not establish sensor accuracy, lane correspondence, or calibrated simulation accuracy.
The acquisition range is not a selected fitting or validation split.

The [frozen source-count package](../benchmarks/hamburg_ring1_count_window_20261020_v1/README.md) covers 2026-10-19 from 05:30 to 08:00 UTC.
It retains 390 five-minute field observations and 40 fifteen-minute station totals.
An independent aggregation check reproduces all 40 published totals from their constituent fields.
This is a source-data check. It is not a SUMO calibration result or a selected fitting/validation split.

### Detector positions and source geometry

The binding tool accepts `map_kml_files` paired with `map_files` in the same order.
Its CLI uses repeated `--map-kml-file` and `--map-file` arguments.
Road-fact snapshots and count bindings use the same explicit KML geometry when available.
The MAP files supply lane identities and direction semantics. The KML files supply geographic polylines.
The association uses the reference heading at the bound endpoint, rather than the chord across a curved lane.
Parallel lanes are assigned within their nearest road arms when a channelized approach spans several roads.

The detector point determines its position on the selected SUMO lane.
Its XML position uses SUMO's logical lane length, which can differ from the saved XY polyline length.
It does not reuse the MAP anchor's position. A native coordinate round-trip regression checks this conversion.
The binding report keeps field-to-reference and reference-to-network distances separate.
These are geometric associations with confidence labels. They do not establish independently surveyed field identity or historical validity.
The [published-point check](../benchmarks/hamburg_ring1_road_scope_20261020_v1/published-field-point-evidence-v1.json) places all 13 points within 0.80 m along the reference from its first anchor.
Their lateral distances are below 2.16 m.
This proximity does not establish surveyed sensor cross-sections. The simulation uses projections of the published points as an explicit placement assumption.
It does not establish that the observations are measured at the stop line or represent discharge counts.

## Signal observation availability

The [2026-10-20 signal screen](../benchmarks/hamburg_ring1_road_scope_20261020_v1/signal-history-screen-v1.json) records the current metadata and five bounded observation queries.
One primary stream at each of 104, 118, and 200 returned an event between 06:00 and 08:00 UTC on 2026-10-19.
The sampled streams at 119 and 535 returned no event in that interval.
Their published motor-stream metadata ends in July 2024 and February 2024 respectively.
Their lane pairs also differ from the frozen current MAP inputs. They are not current signal bindings.
The screen does not establish complete histories, initial states, or availability across every possible stream.
It does not select a fitting window.
The frozen OCIT files contain static signal programs. Their actual use during the count window still needs evidence.
Any use of static programs must remain distinct from same-window signal replay in the report.

The post-exclusion candidate also retains an OSM-derived controller at `6390683126`.
Field `0119-Z.15` binds to lane `603103445#0_0`, immediately upstream of that controller's one controlled link.
Its generated actuated program is separate from `LSA119_part0`. Thus, importing only the named LSA119 program would leave this field under placeholder control.
The official group correspondence for this section remains unresolved. An assumed discharge headway cannot establish its signal group.
Other retained OSM controllers also need an explicit treatment before interpreting a traffic-count fit.

### Source intergreens and the LSA119 collision diagnostic

The program reader now reports `safety_intergreens`: required gaps, measured gaps, release overlap, slack, and unresolved source rows.
The safety matrix is directional. Its reverse entry can have a different requirement, as defined in [OCIT-C section 3.4.18](https://www.ocit.org/media/ocit-c_lsa_versorgungsdaten_v2.0_a01.pdf#page=56).
Seven selected source programs contain 850 matrix entries. Their expanded source timelines satisfy all 850 entries.
These checks do not establish which program operated during the count window.

The [clearance diagnostic](../benchmarks/hamburg_ring1_road_scope_20261020_v1/signal-clearance-evidence-v1.json) identifies two short gaps in the generated LSA119 program.
K2→K6 has 3 s against a required 7 s. K6→K3 has 3 s against a required 4 s.
Only K2, K3, and K6 are assessed in this comparison. Other control correspondences remain unresolved.

All three native trials use the same road geometry, routes, departures, seed, and 0.1 s step.
They stop at 6,000 s. Only the LSA119 program changes.

| Temporary control | Cycle | Collisions / teleports | Arrived by 6,000 s |
|---|---:|---:|---:|
| Original generated program | 90 s | 4 / 4 | 583 |
| Protected diagnostic stages | 155 s | 0 / 0 | 530 |
| Two clearance extensions, with continuing greens retained | 95 s | 0 / 0 | 601 |

The shared clearance function adds 4 s and 1 s after the two existing yellow transitions.
It turns closing yellow links red and preserves greens that continue across each transition.
All original phase records, other programs, geometry, and connections remain unchanged.
The six declared K2/K3/K6 intergreen constraints then hold. This is still a temporary control candidate, not imported field timing.
The scheduled demand contains 4,494 vehicles. These partial trials do not establish their complete arrival or an exact count fit.
Existing protected-green findings and crossings between paths from the same approach still need separate assessment.

The [longer replay](../benchmarks/hamburg_ring1_road_scope_20261020_v1/clearance-timeout-evidence-v1.json) reaches its 900 s wall-clock limit before the requested 14,400 s simulation end.
Its last complete snapshot is at 10,607.0 s: 671 arrivals and 628 running vehicles, all halting, with zero recorded collisions or teleports.
These are partial snapshot counts, not final trip totals. The run remains invalid.

The shared recovery function reads completed E1 records from the truncated output and retains the original file.
It keeps strict checks on count values, identifiers, and duplicate intervals.
This run supplies 85 of 130 field comparisons and 26 of 40 station comparisons. The other entries remain missing.
Seven available field pairs happen to match exactly. They do not establish an accepted fit, and no aggregate fit metric is reported.
The record also freezes the failed [4,494-vehicle demand proposal](../benchmarks/hamburg_ring1_road_scope_20261020_v1/lsa119-stress-demand-v1.rou.xml.gz) for independent checks.
That demand is a diagnostic input, not calibrated traffic.

The local raw responses, request URLs, retrieval times, and hashes are under
`outputs/hamburg-readme-case-20261020-v1/counts-discovery-v1/`.
The official service has a rolling observation range. A later query can return different coverage or revised records.

## Calibration evidence

The earlier README claimed 0.15 vehicles MAE per 15-minute bin.
No linked raw run or manifest currently supports that number in the repository.
It is therefore removed from the current README while the case is revalidated.

The [dated Ring1 record](development-history/ring1-road-continuity-and-calibration-2026-09-11.md)
reports different sample-window results: 3.125 vehicles per formal 15-minute bin at station 30666, and 2.9 across ten bins.
That record covers one station and four E1 loops. It also records incomplete turn construction and diagnostic signal timing.
Those historical numbers have not been reproduced from the current repository's input package.
They are not substitutes for a new measured before/after result.

A before/after comparison requires the same frozen observations, physical detector sections, vehicle-count definition, and time convention.
The demand fitting method and signal treatment must be recorded for both networks.
Fitting-window error and independent validation error are separate results.
A vehicle can pass several stations, so the sum of station counts is not the number of distinct corridor vehicles.

### Exact count-fit target

The requested calibration target is exact integer agreement at every declared field, station, and 15-minute bin in the fitting window.
Both MAE and maximum absolute count error must equal zero. An average error of 0.15 does not meet this target.
The comparison must use actual SUMO detector output with fixed observations, declared detector sections, count definitions, and time boundaries.
The comparison will also report all 130 field-level fifteen-minute residuals before their aggregation into the 40 station totals.
This prevents errors in different fields or directions from cancelling within a station total.
A station total cannot pass while a declared constituent field has a nonzero residual.
Any initialization period precedes the declared comparison window. Declared first-bin observations remain in the result.
The report must retain every declared station and bin, including residuals that remain nonzero.
It must record demand variables, signal treatment, vehicle insertion, unfinished traffic, collisions, and teleports.
Exact agreement is not achieved or established as feasible for the current reconstructed corridor yet.
If constraints prevent exact agreement, the report must identify the remaining residuals and their causes.
An exact fitting-window result does not establish independent prediction accuracy or exact field geometry.

### Current reconstruction and fitting status

The current construction has independent Windows and Linux replays with identical canonical network and detector XML contents.
The [v7 record](../benchmarks/hamburg_ring1_road_scope_20261020_v1/public-replay-evidence-v7.json) records the repaired model recipe.
This reproducibility result does not reproduce the historical README images or establish count calibration.

The [native count probe](../benchmarks/hamburg_ring1_count_probe_20261021_v1/README.md) runs 500 unfitted vehicles to completion without collision or teleport.
It supplies all 130 field and 40 station comparisons. None of the field intervals matches the source exactly.
Its measured responses admit exact integer source-count vectors, but the first larger native replay is invalid.
An exact response equation does not establish an exact SUMO count fit under changed traffic conditions.

The [entry-context repair](../benchmarks/hamburg_ring1_road_scope_20261020_v1/entry-context-evidence-v1.json) retains real upstream roads before vehicle injection.
The ten-vehicle short-entry reproduction then completes without collision or teleport. All 13 native detector coordinates remain unchanged.
The larger replay still records collisions inside LSA119. Thus, the zero-error target remains unachieved.
The later clearance diagnostic above removes those four collisions within its 6,000 s horizon. Full-window count calibration remains unfinished.
Signal treatment, unresolved movements, and demand plausibility still need work before a valid full-demand fit.

The [entry checks](../benchmarks/hamburg_ring1_entry_probe_20261021_v1/README.md) compare two short entries with declared main-road alternatives.
Each of the four runs inserts and completes 20 passenger vehicles without collision or teleport.
For the residential entry, the main-road alternative reduces mean insertion delay from 153.035 s to 3.800 s.
The service-road alternative extends travel time and remains a candidate.
A separate 5,498-vehicle demand proposal changes 3,628 upstream route sections without changing departure times or measured-road sequences.
It has not completed a full-window replay. Measured-road agreement does not establish lane-level count agreement.
These checks use temporary generated control programs and do not establish an exact count fit or true OD.

The [535 lane-binding check](../benchmarks/hamburg_ring1_lsa535_binding_probe_20261021_v1/README.md) retains a failed bounded candidate.
Its reference completes 1,270 vehicles with one collision and one teleport.
The candidate retains the turnaround but moves it to lane 5 and removes the supplemental lane-4 left turn.
It stops at 6,709.9 s after all 97 running vehicles remain stationary for 300 s. Only 136 vehicles arrive.
All 130 field and 40 station rows remain in its diagnostic tables, including 91 missing field rows and 28 missing station rows.
Formal error metrics remain unavailable for both invalid runs. The candidate is not adopted.
The shared demand contains 667 routes that revisit the same 535 core without repeating a directed road.
This identifies remaining tour candidates, not a proven sole cause of the standstill or a zero-error infeasibility result.

The [M1 offline demand package](../benchmarks/hamburg_ring1_m1_demand_pool_20261021_v1/README.md) retains 1,809 routes and 2,515 ideal lane intentions.
All 13 fields retain single-pass witnesses. Its provisional integer proposal has 5,334 vehicles and satisfies all 130 field equations.
The distance objective includes ordinary and internal roads. These are free-flow calculations, with no traffic simulation or native count-fit result.
The 8.91 m entry still receives 2,250 vehicles and reaches its assumed limit in ten intervals.
The 452 selected indirect-turn vehicles remain explicit. Entry plausibility, control, and native lane realization still need verification.

The [M1.1 attribution and entry check](../benchmarks/hamburg_ring1_m1_1_demand_pool_20261021_v1/README.md) reduces local-road inflow under declared road-class ceilings.
None of its 452 selected indirect vehicles matches an unresolved official movement. The proposal still exploits turnarounds and is retained as rejected demand evidence.
The [M1.2 single-pass proposal](../benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/README.md) removes the source-absence exception and excludes every repeated junction.
All 13 fields retain coverage. Base, half, and double local-entry ceilings each admit 6,297 vehicles with no selected turnaround or repeated junction.
All 130 integer equations and 40 derived station equations hold. A rational dual certificate gives the matching finite-pool vehicle lower bound of 6,297.
These remain offline proposals. Native E1 counts, lane response, and installed control are not yet verified.

The [M2 control package](../benchmarks/hamburg_ring1_m2_control_20261021_v1/README.md) adds fixed OCIT-based programs without changing the network.
Native readback verifies all 68 active static programs and 76 bound intergreen pairs. A 13-vehicle cohort completes without collisions or teleports at begin 0 and 3,600, with identical relative trips and E1 integer counts.
Its unmatched motor links still need explicit state correction before a full-window run. The original `O` fallback is retained as a candidate, not accepted final control.

The existing figures remain illustrations of the earlier reconstruction.
Their source networks, calibration inputs, and dated aerial registration need a new reproducible case package before paper use.

## Proposed main-road demonstration

This section describes the agreed demonstration scope.
Road classification and scope selection are implemented in the [offline Ring1 example](../examples/06_hamburg_ring1_demo/README.md).
The complete six-stage workflow is not implemented yet.
The demonstration uses one case configuration, one frozen input package, and one report.
The configuration records the bbox, corridor, road tier, observation window, and signal treatment.

### Current construction and driving

The [latest five-node driving check](../benchmarks/hamburg_ring1_road_scope_20261020_v1/full-movement-driving-evidence-v1.json) also applies the explicit missing-lane correction at 119.
It verifies 92 of the 114 official motor movements on the new scope. All 92 matched movements pass their inlet/internal/outlet probes.

| Node | Verified movements | Official total |
|---|---:|---:|
| 104 | 13 | 14 |
| 118 | 17 | 26 |
| 119 | 16 | 16 |
| 200 | 27 | 39 |
| 535 | 19 | 19 |

The other 22 movements remain unresolved. The report retains their lane reviews, unexplained extra connections, and composed-boundary reviews.
Some unresolved references are bus-only or revocable lanes. Adding ordinary motor lanes is not a general correction for them.
An earlier movement-only conversion left four endpoint gaps after it recalculated automatic contours and restored the ordinary roads.
Contour-preservation mode now fixes existing contours as well as ordinary roads. A four-road native regression reproduces the failure and checks both repaired movements by driving.
The earlier lane-count edit changed one neighboring lane shape by up to 0.91 m. That historical change was unaccepted.
A normal passenger vehicle completes a requested stop on the added lane and arrives without collision or teleport.

All 13 count-field associations are active on this five-node candidate, with no shared lane assignments.
Thirteen E1 loops load in SUMO, and their membership reproduces the four published station groups.
The one-second layout load has no traffic demand. It supplies no count-fit result and does not select the experiment window.
The [shared example command](../examples/06_hamburg_ring1_demo/README.md#continue-through-junctions-and-detectors) now rebuilds construction and detector placement from saved inputs.
The [fresh public replay](../benchmarks/hamburg_ring1_road_scope_20261020_v1/public-replay-evidence-v2.json) reproduces 92 passing movements and 13 loops with the implementation recorded at `e8ea94b`.
Its canonical network and detector XML contents match the independent Linux replay of the same model recipe at `91254d6`.
The earlier [saved-input replay](../benchmarks/hamburg_ring1_road_scope_20261020_v1/public-replay-evidence-v1.json) reused a road-scope input from before the shared source-cut repair.
That input had different centerline precision. The fresh rebuild removes the observed content mismatch without changing the comparison recipe.
Inputs and implementation files stay unchanged during the fresh run. The added motor lane retains passenger and bus access.
The separate static influence check finds possible count influence for 19 unresolved movements and unknown outlets for two more.
The remaining movement has no path in its saved outlet hypotheses. That finding does not establish absence of count influence.
Signal treatment and fitting remain unfinished. The complete six-stage demonstration is not available yet.

The [subsequent complete replay](../benchmarks/hamburg_ring1_road_scope_20261020_v1/public-replay-evidence-v3.json) applies four explicit supplementary-connection exclusions from the case configuration.
It retains 92 passing official movements, 13 field loops, and four station groups. No required boundary pair disappears.
The four exclusions preserve all other loaded movement permissions, ordinary geometry, logical lengths, and junction contours.
They define this model candidate. Missing verified source support does not establish real-world turn prohibitions.
The shared connection-edit repair also restores pre-cleanup precision on 13 lane lengths and two shapes.
The maximum restored shape difference is 0.0057 m. This is separate from the remaining neighboring lane change of up to 0.91 m.
The changed network has one complete Windows replay and differs from the retained v1 content reference.
Its detector XML content is unchanged. The earlier model's cross-platform agreement does not establish it for this new version.

Subsequent independent Linux review reproduced the new network and loop hashes as well.
The pre-exclusion and post-exclusion networks each contain ten protected-green foe findings in the temporary programs.
Those physical conflict relations persist across renumbered link indices. They remain unresolved control findings.
The verifier now compares physical connection identities and separates inherited from new relations without relaxing release checks.
Two source lanes lose their direct motor exit: `368989705_4` and `626677498_0`.
The first binds official ingress lane 1 at 200, whose required movements remain unresolved. Neither lane carries a count loop.
The workflow records terminal lanes and their official identities. It does not establish service by lane changes.

The next 200 diagnostic concerns official egress lanes 16 and 17, each about 14 m long and separated by about 3.2 m.
Both currently fit one SUMO motor lane on `551284796#0`, a roughly 49 m road with a separate bicycle lane.
The [dated aerial reference](../benchmarks/hamburg_ring1_lsa200_aerial_20261020_v1/README.md) supports visual review of this receiving section.
Its tile metadata dates the image to 2026-03-19. The MAP validity date remains unresolved.
A full-road lane addition is a diagnostic candidate. Its source agreement and a shorter merge interpretation still need review.

That diagnostic exposed neighboring road-end displacement during lane-count conversion.
Source-derived contour hints and a narrower geometric edit scope now preserve neighboring roads at junctions without pedestrian permission.
Automatic pedestrian junctions retain their walking-area rebuilding behavior, with remaining neighboring changes reported.
The repair has a failing source regression and a passing repaired regression. A complete current-profile replay is still required.

The first complete replay with that narrower scope failed at junction `9810392490`.
It retained a 0.2 m inlet road while the compiled internal entries followed a temporary native shift, leaving three gaps after restoration.
The failed run remains in `outputs/ring1-public-junction-demo-20261020-v5/`.
Native curve repair now uses the final retained road endpoints. The [short-context regression input](../benchmarks/hamburg_ring1_road_scope_20261020_v1/lane-count-short-context-input.json) preserves this failure case.
The repaired real-input check has zero structural failures and no neighboring shape, length, or width changes.
This result still requires complete corridor driving and detector verification on the new recipe.

Official signal-group names also require their declared traffic class.
At 200, `R6` is explicitly `Kfz`, despite its name. The parser now retains its VT reference for movements 1→16 and 1→17.
This corrects source classification. It does not establish current signal timing or that these movements were available during the count window.

The [repaired complete replay](../benchmarks/hamburg_ring1_road_scope_20261020_v1/public-replay-evidence-v5.json) retains the failed predecessor and its successful repair.
The new run verifies all 92 mapped movements out of 114 and loads 13 field loops in four published station groups.
It has zero structural failures. The lane-count stage preserves neighboring shapes, logical lengths, and widths exactly.
Three local transition checks also complete their requested lane chains for passenger, bus, and bicycle classes without collision or teleport.
The bicycle starts on the local inlet. These checks do not establish upstream bicycle access or capacity.

A native coordinate comparison covers all 13 declared field loops between the previous and repaired models.
Only Z.11 changes its logical position, from 10.763 m to 13.936 m as the saved lane length changes.
Its native XY displacement is 0.00236 m. The other twelve positions are unchanged.
The comparison records 207 automatic controller loops separately. They are outside the declared count-field inventory.
These are model-coordinate checks, not surveyed field correspondence or a count-fit result.
The 22 unresolved movements and ten inherited protected-green foe findings remain explicit.

### Earlier construction diagnostics

A separate prototype constructs LSA535 and LSA119.
It materializes all 19 official motor movements at 535 and all 16 at 119.
All 35 mapped movements pass isolated SUMO probes on the combined prototype.
Each probe verifies its inlet lane, complete internal lane sequence, and first outlet lane.
The earlier missing movement required a second motor lane alongside a source bicycle lane.
The typed lane-count correction retains bicycle access and supplies the official motor connection.
A normal passenger vehicle completes an explicit stop on the new lane and then arrives without collision or teleport.
This requested stop does not establish natural lane allocation or capacity.
The lane-count stage still changes three neighboring lane shapes. Those changes remain measured and unaccepted.
The subsequent movement-only conversion preserves ordinary lane shapes, lengths, and widths exactly in contour-preservation mode.

This prototype comes from the earlier 468-road scope. It is separate from the current 467-road replay.
Its 13 count-field associations are active and have no shared lane assignments.
They use physical field points, explicit paired KML geometry, and SUMO logical positions.
These model-relative associations do not establish independently surveyed identity or final detector placement.

Channelized official approaches can span several source roads.
Lane-order matching now handles each unambiguously selected source road separately, with equal counts and consistent shared-section order.
It does not select a more distant road to obtain a desired lane count.
Two extra 535 connections remain unexplained. The earlier source diagnostic also records nine failures at four outside junctions.
The probes disable autonomous lane changes and do not establish multi-vehicle capacity, field signal timing, or count accuracy.
The [diagnostic record](../benchmarks/hamburg_ring1_road_scope_20261020_v1/junction-diagnostic-evidence-v2.json) records the prototype lineage, results, and remaining limits.
At that stage, the complete junction replay and final sensor mapping were unfinished.

The [new-scope approach replay](../benchmarks/hamburg_ring1_road_scope_20261020_v1/approach-replay-evidence-v1.json) constructs nine physical parts across all five nodes on the 467-road scope.
It records the failed initial approach conversion and its source-derived geometry repairs.
The repaired stage preserves its declared outside geometry, attributes, and connection set.
Two pre-existing movements have no effective permitted vehicle class. Their explicit deletion preserves every remaining movement's loaded permissions.
The resulting intermediate network has zero structural path failures. Its full official movement inventory and driving still need verification.
Those diagnostic runners depend on local outputs. The shared example command now replaces their construction and detector steps.

### Road importance and retained roads

The first version uses three coarse tiers: major roads, collector roads, and local roads.
OSM `highway` tags supply the initial classification.
They describe a road's role and importance in its network, as defined in the [OSM highway documentation](https://wiki.openstreetmap.org/wiki/Key:highway).
Missing classification remains visible in the report.
The tier is separate from SUMO's `priority` attribute, which affects [junction right-of-way rules](https://sumo.dlr.de/docs/Networks/SUMO_Road_Networks.html).

The corridor, observed road sections, necessary connectors, and required entry/exit approaches receive retention reasons before pruning.
Their source road class remains recorded even when the study needs a lower-class road.
For example, the V1 calibration scripts reference OSM way `186821027`, Ernst-Merck-Straße, currently tagged `tertiary`.
Way `186821036`, Glockengießerwall, is tagged `secondary` with `ref=Ring 1`.
These source identities were checked on 2026-10-20. Their classes alone do not decide their use in this study.

Pruning defines the simulation scope. It does not establish that an excluded street is absent in the field.
Cut branches that feed or drain the retained corridor require explicit traffic boundaries.
Required entry-to-exit movements are checked in their driving direction, with path-length changes recorded.
The first version does not impose a universal detour-ratio threshold.
Parking access can remain a declared boundary when its internal driving area is outside the chosen study.

### Six visible stages

| Stage | Demonstration output |
|---|---|
| 1. Original OSM | Full import from the frozen bbox, with the original road inventory |
| 2. Classify and prune | Coarse road tiers, retained connectors, excluded branches, and entry/exit boundaries |
| 3. Consolidate junctions | Actual joined junctions, including an LSA118 detail view and remaining unresolved groups |
| 4. Place virtual sensors | Final-network detectors at the recorded physical sections, with mapping status |
| 5. Calibrate | Demand fitting on one frozen network using the declared observations and signal treatment |
| 6. Compare counts | Observed and simulated 15-minute counts, station residuals, and run validity |

Detector sections are identified before pruning. The detector objects are placed after the lane layout is final.
The main corridor view and the LSA118 detail view each keep equal geographic bounds in their before/after pair.
The comparison uses actual NetEdit screenshots over one registered Google background.
The joined junction is selected in Inspect mode for the after detail.
These figures are part of the core demonstration.
The screenshots retain Google and imagery-provider attribution under the [Google geo guidelines](https://about.google/brand-resource-center/products-and-services/geo-guidelines/).
Declared OSM and official road sources supply the network geometry. The Google background supplies visual context.

The report shows retained road and junction counts, each station's count curve, total count error, and MAE per 15-minute bin.
It also records collisions, teleports, unfinished vehicles, insertion backlog, and the demand variables constrained by the observations.
Same-window count matching is labelled as calibration fit.
It does not establish independent prediction accuracy or geometric accuracy at LSA118.

The frozen package supports an offline presentation and a reproducible rerun.
The report retains original inputs, intermediate networks, selection reasons, and final count outputs.
Internet access is used for source acquisition, before the live demonstration.

Withheld-counter checks and comparisons across pruning levels are separate optional analyses.
They retain the same fitting method and prior and state which observations can affect demand.
They are not prerequisites for producing the core demonstration.

### Existing implementation and missing composition

The repository already contains road-class presets in `core/road_scope.py`,
road-arm classification in `intersection/road_detail.py`, and corridor extraction in `core/corridor_scope.py`.
The Hamburg topology, detector, demand, and NetEdit capture modules provide the later stages.
Road selection now fixes the original interfaces, preserves main-road movements and local turns, and avoids expanding residential repair obligations.
The offline example currently retains 467 of 1,156 ordinary road segments, including tentative official approach context.
The class-only variant retained 367 segments but excluded some official approach fragments.
The current result retains the source parts of 135 tentative motor-lane associations. Fourteen official motor lanes remain unmapped.
Seven associated lanes have zero directed overlap, so their lateral offset remains unavailable.
Its main report exposes geometry and signal changes beside the measured status.
The [official static source package](../benchmarks/hamburg_ring1_official_sources_20261020_v1/README.md) contains all five MAP/KML/OCIT triplets.
Exact XML/KML joins pass for all five nodes. This does not establish SUMO correspondence or a completed junction.
Nearest signal clusters remain grouping proposals. Chained nearby nodes must not become an automatic physical-junction merge.
The remaining work is junction construction, detector correspondence, count fitting, matched figures, and the demonstration report.
The Am Sandtorkai mainline profile supplies an implementation pattern. Its fixed road IDs do not belong to this case.
