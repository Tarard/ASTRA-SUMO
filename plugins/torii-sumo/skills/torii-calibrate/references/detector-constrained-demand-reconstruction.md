# Detector-Constrained Demand Reconstruction

Use this reference when real detector counts are the main evidence source for building SUMO demand. The goal is to reconstruct plausible routes and departures whose simulated detector outputs match the field count windows while preserving route realism, SUMO completion, and claim boundaries.

Given a SUMO network and large-scale real-world detector observations, Torii generates and audits vehicle routes whose simulated detector measurements match the real detector measurements over time. In Chinese: given a SUMO road network and many real detector observations, Torii should generate vehicles that travel through valid network routes, then verify that SUMO detector counts match the real detector counts in the declared time windows.

## Core Contract

Detector counts are route constraints, not OD observations. A valid reconstruction creates vehicles that:

- enter from plausible boundary, arterial, bridge, or local-access source edges;
- traverse mapped detector edges in the declared direction;
- leave the modeled network or terminate at plausible local sinks;
- preserve the observed time-bin shape, especially AM and PM peaks;
- produce SUMO detector outputs comparable to the real detector rows;
- report unfinished, waiting, teleported, and collided vehicles.

Creating vehicles directly at detector edges is only a detector smoke test. Do not call that OD reconstruction.

## Tool Boundary

For the default count audit, missing intervals are unknown measurements, not
zero flow. Review `missing_measurement_rows` and `metric_scope` before using
MAE or GEH. This entry keeps its existing `nVehEntered` definition; strict
replay comparison separately uses `nVehContrib`.

For route sampling, use a new output directory or prefix. The executor rejects
existing fixed output files and checks that its inputs remain unchanged. Both
new demand and mismatch files are required. Empty mismatch evidence cannot
establish a perfect fit.

This reference is a workflow and audit protocol. It can guide Torii, Codex, Claude, or a human reviewer, but it is not proof that the current plugin can automatically run the full reconstruction.

Before claiming automated support, verify the required tool path exists for:

```text
detector_manifest_generation:
route_candidate_generation:
route_detector_incidence:
routeSampler_or_optimizer_run:
SUMO_detector_output_generation:
detector_comparison:
completion_summary:
```

If one of these is manual, label the result as `diagnostic-demo` or `construction-invalid` until the manual step is documented and replayable.

## Reusable Python Executors

These helpers implement the generic workflow gates. The bundled code contains no project-specific detector data in the three generic manifest/constraint/audit helpers, and no proprietary sensor feed, private station table, or local absolute path. They accept caller-provided files and write generic manifests or comparisons. The dedicated Hamburg executor contains only a public, fixed corridor preset plus official API fetch logic; see `hamburg-sandtorkai-digital-twin.md`. Public outputs require anonymized inputs when caller-provided ids or non-public metadata are present because tool outputs preserve caller-provided detector ids and edge ids.

Available Torii MCP tools:

| Tool | Workflow Role | Output |
|---|---|---|
| `sumo_detector_route_support` | Gate 2 route support coverage | `source_sink_manifest`, `route_candidate_manifest`, `route_detector_incidence` |
| `sumo_detector_count_constraints` | Gate 3 time-bin constraint construction | `time_bin_count_constraints`, routeSampler-compatible edgeData XML |
| `sumo_detector_count_audit` | Gate 4-6 detector comparison | detector comparison CSV plus MAE, RMSE, GEH, bias, and totals |
| `sumo_hamburg_sandtorkai_digital_twin` | Fixed Hamburg Gates 0-3 and replay-input preparation | official API snapshots, complete Saturday window, MAP/SUMO bindings, E1/E2 additions, multi-interval edge counts, TLS events, route support, and optional routeSampler demand |
| `sumo_detector_route_sampler_calibrate` | Gate 4 routeSampler baseline executor | generated plausible demand route file, input/script hashes, exact command, and mismatch deficit/overflow evidence |
| `sumo_digital_twin_replay_validate` | Gates 4-6 detector/TLS replay and completion validation | SUMO E1/E2 outputs, strict `nVehContrib` count comparison, summary/tripinfo completion, and replay manifest |

These tools reduce boilerplate; they do not prove the route volumes are calibrated by themselves. The current tool boundary is:

```text
implemented:
  detector_manifest_reader:
  route_candidate_manifest_builder:
  route_detector_incidence_builder:
  time_bin_count_constraint_builder:
  detector_count_auditor:
  explicit_routeSampler_run_when_script_resolves:
  SUMO_E1_E2_output_and_completion_collection:
  fixed_Hamburg_official_count_signal_package:

still_external_or_project_specific:
  alternative_optimizer_run:
  incident_calendar_or_event_classification:
```

Treat a routeSampler output as `detector-constrained-plausible-demand`, not true OD. Treat a package as construction-incomplete until the replay tool writes readable detector and completion evidence. Any external optimizer, manual mapping acceptance, or incident classification must be replayable and included in the evidence package before claim escalation.

For lane-level sources such as Hamburg, first collapse multiple official fields only within the same
`(physical node, SUMO lane)` and retain source membership. Use that virtual expected-count file for E1/E2 replay.
Build routeSampler edgeData only from audited complete passenger-lane cross-sections; a partial-lane observation
is not the total flow of a multi-lane SUMO edge. Keep the complete-section audit beside the edgeData file.
When detector cross-sections are deliberately opened as local source/sink ports, departure-lane assignment may
control only the first edge of each generated route. A detector edge that occurs later in a route is an internal
observation, not a second departure point; leave that lane split as `review_required` unless an explicit segment or
turn-flow model controls it. Never turn the internal-bin mismatch into fabricated `departLane` attributes.

## Workflow Contract

Workflow first, tools second. Treat detector-constrained demand reconstruction as a gated workflow with named records, not as a loose set of scripts. Python, routeSampler, SUMO, MCP tools, or manual commands are executors. The workflow status is decided by gate outputs.

Every run should produce or point to these records:

```text
target_record:
detector_manifest:
source_sink_manifest:
route_candidate_manifest:
route_detector_incidence:
time_bin_count_constraints:
baseline_comparison:
calibrated_comparison:
hourly_audit:
daily_summary:
workflow_status:
```

Use one immutable `target_record` per declared experiment scope. If a mapping, network, route candidate set, time bin, or calibration knob changes, start a new target record or explicitly version the change.

Do not call a detector-entry smoke test OD reconstruction. Detector-entry probe demand is useful only for checking detector placement and E1 observability.

## Gate 0: Target Declaration

Declare the reconstruction target before building vehicles:

```text
target_record:
  time_window:
  detector_set:
  detector_mapping:
  network_version:
  route_candidate_set:
  field_count_interval:
  validation_interval:
  accepted_error:
  completion_threshold:
  calibration_stages:
  data_release_status:
```

Pass condition: the target record is complete, uses absolute local timestamps, and names the exact network and detector mapping files.

Fail condition: the run starts from "all available data", "the whole month", or "the current network" without versioned inputs.

## Gate 1: Detector Alignment

Build a detector manifest before route generation. Minimum fields:

```text
detector_manifest:
  detector_id:
  source_system:
  real_direction:
  sumo_edge:
  sumo_lane:
  lane_position:
  period:
  mapping_confidence:
  mapping_status:
```

Pass condition: active detector rows have directed SUMO edges and lanes, paired opposite-direction sensors remain separate, and same-lane conflicts are either repaired or explicitly aggregated.

Fail condition: opposing directions are merged, detector ids are silently moved to convenient roads, or deleted road sensors are ignored without an out-of-scope record.

## Gate 2: Route Support Coverage

Route support must exist before fitting volumes. Create a source/sink manifest and route candidate manifest:

```text
source_sink_manifest:
  source_edge:
  sink_edge:
  source_type:
  sink_type:
  reason:

route_candidate_manifest:
  route_id:
  source_edge:
  sink_edge:
  edge_count:
  route_length:
  edges:

route_detector_incidence:
  route_id:
  detector_id:
  detector_edge:
  detector_direction:
  incidence:
```

Pass condition: each active detector edge/direction is covered by at least one route that enters from a plausible source and leaves or terminates at a plausible sink.

Fail condition: a detector can only be matched by spawning directly on the detector edge, or route support uses a road closure, forbidden edge, wrong direction, or deleted connector.

## Gate 3: Time-Bin Constraint Construction

Convert real counts into time-bin constraints only after detector and route support gates pass:

```text
time_bin_count_constraints:
  begin:
  end:
  detector_id:
  edge_id:
  lane_id:
  expected_total:
  source_rows:
```

Pass condition: bins use the declared interval, preserve direction, and keep enough resolution to test AM peak, PM peak, low-flow, and daily totals separately.

Fail condition: full-day totals are used as the only target, or missing sensor minutes are treated as zero without an imputation or exclusion flag.

For routeSampler multi-interval edgeData, write one `<interval>` per declared bin and one `<edge count="...">` per mapped edge. Invoke routeSampler with explicit `--edgedata-attribute count`; do not collapse a two-hour target into one interval.

## Gate 4: Baseline Reconstruction

Run the route candidate set without residual supplements first. The baseline is not optional.

Required output:

```text
baseline_comparison:
  expected_total:
  measured_total:
  edge_rows:
  MAE:
  RMSE:
  max_abs_error:
  signed_bias:
  GEH_lt5_percent:
  top_residual_edges:
  completion_summary:
```

Pass condition: SUMO writes readable detector output and completion summary, even if detector error is still high.

Fail condition: routeSampler generates vehicles that SUMO cannot insert, route diversity collapses, or detector files are missing.

Use `nVehContrib` by default for E1 comparison because it counts complete detector passages. Preserve a measured zero as a matched value and mark an absent detector interval as `missing`; never coerce missing output to zero in a strict audit.

## Gate 5: Residual-Correction Calibration

Use residuals as controlled feedback. A calibrated stage may add named supplements or optimizer knobs, but it must reuse the same target record and baseline route support.

Required output:

```text
calibrated_comparison:
  baseline_reference:
  calibration_knobs:
  supplement_rules:
  expected_total:
  measured_total:
  MAE:
  RMSE:
  GEH_lt5_percent:
  top_residual_edges:
  route_diversity:
  completion_summary:
```

Pass condition: detector fit improves or the residual pattern is explained, while completion, teleports, waiting vehicles, and route diversity remain disclosed.

Fail condition: overflows are hidden by deleting arbitrary through routes, underflows are patched by detector-entry vehicles, or the calibrated set gets excellent GEH by leaving many vehicles waiting.

## Gate 6: Hourly And Daily Audit

Aggregate only after every required time bin has a readable comparison and summary:

```text
hourly_audit:
  stage:
  day:
  hour:
  detector_abs_error_sum:
  detector_squared_error_sum:
  expected_total:
  measured_total:
  MAE:
  RMSE:
  GEH_lt5_percent:
  loaded:
  inserted:
  arrived:
  running_at_end:
  waiting_for_insertion:
  teleports:
  collisions:
  completion_ratio:

daily_summary:
  stage:
  day:
  hours_expected:
  hours_with_comparison:
  hours_with_summary:
  MAE:
  RMSE:
  GEH_lt5_percent:
  completion_ratio:
  workflow_status:
```

Pass condition: daily MAE/RMSE can be recomputed from exact hourly sums, and completion is reported before any demand-quality claim.

Fail condition: daily metrics average rounded hourly values, omit missing hours, or report detector fit without SUMO completion.

## Public Data Boundary

Do not commit raw proprietary detector feeds, private station ids, unmasked precise sensor locations, or unpublished operational records to a public repository.

For public examples, use one of these:

- synthetic counts;
- openly licensed aggregate counts;
- anonymized detector ids and coarsened locations;
- manifests that show schema and mapping logic without exposing raw field data.

Commit lightweight manifests, comparisons, and audit CSVs. Keep large SUMO XML, routeSampler logs, raw feeds, and non-public sensor metadata local unless the dataset is explicitly cleared for release.

For the fixed Hamburg workflow, follow `hamburg-sandtorkai-digital-twin.md`: retain the official MetaVer links, API request URLs, snapshot hashes, `Datenlizenz Deutschland Namensnennung 2.0` attribution, and the explicit non-identifiability statement. Published infrastructure IDs do not justify inferring or releasing individual travel behavior.

## Required Target

Define the target before generating vehicles:

```text
time_window:
detector_set:
detector_mapping:
network_version:
route_candidate_set:
field_count_interval:
validation_interval:
accepted_error:
completion_threshold:
data_release_status:
```

The target is not "the same number of vehicles as the detector table." The target is "vehicles travel through valid SUMO routes and the detector outputs match the field count windows, with completion and failure status reported."

## Validity Gates

Run these gates in order:

1. Detector alignment.
   - Map every real detector to SUMO lane, edge, position, direction, and mapping confidence.
   - Treat paired detectors on a two-way road as one detector per direction, not duplicate evidence.
   - If a detector falls on a deleted segment, restore the route-relevant segment or mark the detector out of scope.

2. Time-window alignment.
   - Use exact absolute dates and local clock windows.
   - Separate background days, incident days, holidays, roadworks, weather disruptions, and event days.
   - Keep hourly or sub-hourly bins for commuter peaks; do not rely only on full-day totals.

3. Route-candidate validity.
   - Candidate routes should enter from plausible sources, traverse detector edges, and leave or terminate at plausible sinks.
   - Reject detector-entry replay as OD evidence unless the run is explicitly a detector smoke test.
   - Each detector edge should be reachable by at least one candidate route.

4. Count constraint construction.
   - Aggregate lane detectors to the constraint level used by the sampler, usually edge counts or edge-relation counts.
   - Preserve direction. Do not combine opposing directions unless the real data is explicitly directionless.
   - Keep a manifest linking real count rows, detector ids, SUMO edges/lanes, and generated constraint files.

5. Simulation completion.
   - A detector match is not valid if most vehicles remain waiting, running, or teleported without disclosure.
   - Report completion before travel time, delay, controller performance, or demand-quality claims.

## Field Pattern: Sensor-Only Demand Reconstruction

When the only field evidence is fixed detector counts, reconstruct route support first and OD second:

1. Treat each detector row as a directional route-incidence constraint.
   - A detector contributes one equation to the route-volume fit for its declared time bin.
   - Paired detectors on opposite directions are two constraints.
   - Multiple sensor systems should remain separate manifests until a documented merge rule is applied.

2. Build plausible OD support from network topology.
   - Sources should usually be boundary approaches, bridge approaches, arterial entrances, or known local access points.
   - Sinks should let vehicles leave the cropped study area or terminate at plausible internal destinations.
   - A vehicle that starts on the detector edge is a smoke-test vehicle, not reconstructed OD.

3. Fit route volumes by time bin.
   - Build a route-detector incidence table where `A * x` approximates detector counts `y`.
   - Use nonnegative route volumes and regularization for route diversity, background-flow shape, OD priors, and hour-to-hour smoothness when available.
   - Preserve baseline and calibrated stages so residual correction remains auditable.

4. Use residuals as construction feedback.
   - Underflow usually means missing route candidates, deleted connectors, wrong direction mapping, or detectors outside scope.
   - Overflow usually means over-broad route priors, merged opposing directions, or duplicated detector constraints.
   - Good detector fit with poor completion usually means insertion backlog, route collapse, insufficient clearance, or unrealistic departure concentration.

5. Validate the generated vehicles in SUMO.
   - A fitted route file is not evidence until SUMO writes readable detector comparison and completion outputs.
   - Store exact hourly aggregation fields so daily MAE/RMSE can be recomputed from sums rather than rounded hourly averages.
   - Keep teleports, collisions, waiting vehicles, running vehicles, and route diversity attached to the detector-fit report.

This pattern supports detector-constrained plausible demand reconstruction. It does not prove true OD without external OD evidence such as trajectories, travel times, surveys, boundary counts, or assignment calibration.

## RouteSampler Ladder

Use a small closed-loop ladder:

1. One-hour smoke: build expected counts, run routeSampler or the declared optimizer, run SUMO, compare detector output.
2. Peak-hour stress: repeat for AM peak, PM peak, and one low-flow hour.
3. One-day validation: run 24 hourly batches and aggregate detector error plus completion.
4. Multi-day validation: split weekdays, weekends, holidays, incident/event days, and background days.
5. Month validation: run all days with a manifest and summarize failures before claiming match quality.

Stop and repair when:

- route coverage misses a detector edge;
- SUMO inserts far fewer vehicles than the sampler generated;
- waiting or running vehicles persist at the end of an hourly run;
- teleports are nonzero in a setting where congestion is not the phenomenon under study;
- residuals concentrate on one detector edge or one direction;
- the calibrated route set collapses to too few distinct routes and creates insertion backlog.

## Month-Long Workflow Exit Criteria

For a month-scale run, keep the workflow staged:

1. `diagnostic-demo`: one hour passes Gate 0 through Gate 6.
2. `stress-diagnostic`: AM peak, PM peak, and one low-flow hour pass or produce documented failure feedback.
3. `one-day-evidence`: all declared hours for one calendar day have baseline and calibrated records.
4. `multi-day-evidence`: representative weekdays, weekends, holidays, roadworks, incident days, and background days are separated.
5. `formal-evidence`: the full declared calendar set has fixed inputs, complete manifests, daily summaries, hourly audits, completion fields, and no hidden missing files.

Do not skip from a good pilot hour to a month-level claim. Each stage should reuse the same target-record schema and explicitly record any versioned change.

## Failure Feedback Rules

Use failures to choose the next control action:

| Symptom | Likely Deviation | Next Control Action |
|---|---|---|
| underflow concentrated on one detector | missing route support, deleted connector, wrong direction, or routeSampler coupling | inspect route support and route_detector_incidence before adding vehicles |
| overflow concentrated on one detector | over-broad route prior, duplicate constraint, or merged opposing directions | audit detector_manifest and route candidates before deleting routes |
| good detector fit but low completion | insertion backlog, route collapse, insufficient clearance, or unrealistic departures | report completion first, add clearance or route diversity, then rerun |
| good daily total but bad peak hour | time-bin smoothing or wrong interval aggregation | rebuild time_bin_count_constraints at hourly or sub-hourly resolution |
| many same-lane detector mismatches | detector mapping too fine for SUMO lane geometry | repair mapping, aggregate intentionally, or mark out of scope |
| calibrated stage uses very few routes | route diversity collapse | keep baseline, try diversity-preserving supplements, and downgrade the claim |

## Month-Long Daily Workflow

For a month of real detector data, treat each day as a replayable evidence unit rather than one opaque batch:

1. Run one absolute calendar day at a time.
   - Use explicit local timestamps, for example `2024-03-15 00:00:00` to `2024-03-16 00:00:00`.
   - Keep detector mapping, network version, route-candidate file, clearance window, and calibration knobs fixed across days unless intentionally versioned.
   - Do not merge incident, holiday, event, roadwork, and background days into one quality claim.

2. Generate paired stages for every day.
   - Baseline: raw route candidates with no residual supplement.
   - Calibrated: the same candidate base plus named residual-correction knobs.
   - Keep both stages; the baseline is the residual diagnosis and protects against hiding mapping or route-prior bias.

3. Validate by hour before validating by day.
   - Preserve 24 hourly detector comparison files per stage.
   - Use hourly bins to expose AM peak, PM peak, night, and low-flow behavior.
   - Aggregate to daily metrics only after every expected hour has readable detector comparison and SUMO summary.

4. Keep exact audit fields.
   - Store per-hour absolute-error sums, squared-error sums, row counts, measured totals, expected totals, and completion fields.
   - Recompute daily MAE/RMSE from exact hourly sums; do not trust averages of rounded hourly metrics.

5. Commit lightweight evidence, not heavy runtime products.
   - Commit manifests, detector comparison CSVs, summaries, and hourly audit CSVs.
   - Keep large route files, detector XML, summary XML, tripinfo XML, routeSampler stdout, mismatch XML, and supplement internals local unless needed for a disputed claim.

## Residual-Correction Pattern

Use two stages when routeSampler or another sampler undercounts detectors:

1. Baseline stage.
   - Run the raw candidate route set with no residual supplement.
   - Save detector comparison as the baseline residual record.
   - Record route count, detector MAE/RMSE/max residual, bias, GEH<5, and SUMO completion.

2. Calibrated stage.
   - Reuse the same time window, network, detector mapping, seed, and route candidate base.
   - Enable optimization, vehicle minimization, or supplements only as named calibration knobs.
   - Add supplementary routes only for baseline underflows that pass explicit absolute and relative deficit thresholds.
   - Compare against the same detector set and report both improved detector fit and any new teleports or backlog.

Do not supplement overflows by deleting arbitrary through routes unless the route-candidate model explains why those routes are invalid.

## Route Diversity Fallback

Detector-perfect calibration can still be invalid when flow collapses into too few routes:

```text
baseline: many distinct routes, worse detector MAE, SUMO completes
calibrated: few distinct routes, excellent detector MAE, large backlog or crash
```

When this happens:

1. Keep the baseline comparison as the residual source.
2. Try a diversity-preserving fallback, such as supplementing underflow routes without full optimization or vehicle minimization.
3. Stitch the fallback hour into a hybrid hourly manifest only if it has readable detector comparison and SUMO summary outputs.
4. Report the tradeoff: detector MAE/RMSE/GEH improvement versus teleports, unfinished vehicles, collisions, and route diversity.

Do not claim the detector-perfect version is better if it crashed, left a large insertion backlog, or reduced route support to an implausibly small set.

## Metrics To Report

For detector fit:

```text
expected_total:
measured_total:
edge_rows:
MAE:
RMSE:
max_abs_error:
signed_bias:
GEH_lt5_percent:
top_residual_edges:
```

For simulation completion:

```text
loaded:
inserted:
arrived:
running_at_end:
waiting_for_insertion:
teleported:
collisions:
completion_ratio:
horizon:
clearance_seconds:
```

Evidence class:

- `diagnostic-demo`: small-window or pilot reconstruction.
- `stress-diagnostic`: intentionally congested or teleport-prone window.
- `formal-evidence`: full declared calendar set, fixed inputs, reported completion, and no hidden missing files.
- `construction-invalid`: detector files, route files, SUMO outputs, or mapping gates failed.

## Replayable Evidence Package

A replayable reconstruction should preserve:

```text
detector_manifest:
source_sink_manifest:
route_candidate_manifest:
route_detector_incidence:
time_bin_count_constraints:
route_volume_solution:
generated_route_file:
sumo_detector_output:
detector_comparison:
completion_summary:
top_residuals:
data_release_status:
```

A fresh checkout should be able to audit the published summary from committed lightweight CSVs or rerun the commands from committed manifests and cleared input data.

## Claim Boundary

Allowed claim:

```text
The generated SUMO demand is detector-constrained and reproduces the real detector counts for the declared detector set, time windows, network version, and completion criteria.
```

Treat detector-matched routes as a plausible demand reconstruction, not as proven true OD unless supported by additional OD priors such as travel times, trajectory data, boundary counts, surveys, or assignment evidence.

## March Field Lesson

The March-style workflow that should be reused is not a project script name. The reusable lesson is:

1. Build the detector manifest and route support once per network/mapping version.
2. Generate one day at a time with absolute dates.
3. Run 24 hourly bins per day, preserving AM peak, PM peak, and night periods.
4. Keep paired baseline and calibrated stages for every day.
5. Use baseline residuals to drive named supplements; do not hide the baseline.
6. Save hourly comparisons and exact aggregation fields before daily summaries.
7. Treat completion, waiting, teleports, collisions, and route diversity as part of the demand evidence.
8. Keep large generated SUMO XML and private detector feeds local unless explicitly cleared for release.

This lesson can guide future Python or MCP tooling, but the workflow contract above is the stable public interface.

## Common Mistakes

- Matching detector totals by creating vehicles at detector edges, then treating that as OD evidence.
- Comparing a 24-hour total while missing AM/PM peak shape.
- Calibrating on incident days and calling the result normal background flow.
- Combining paired opposite-direction sensors into one count.
- Reporting detector MAE without loaded/inserted/arrived/waiting/teleport status.
- Publishing raw detector feeds or private station metadata in a public demo repository.
- Treating exact detector count matching as proof of true OD.
