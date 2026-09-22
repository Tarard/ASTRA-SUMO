# Hamburg Count Calibration on a Fixed Road Network

Use this reference when the user asks for traffic-count fitting, demand
reconstruction, or signal replay. It is separate from
[road construction](hamburg-five-intersection-aerial-workflow.md).

Start with `network-handoff.json` from the road workflow. Verify the road
network and construction manifest against its SHA-256 hashes. Check its
`construction_decision`. A diagnostic handoff with `review_required` or `blocked`
does not establish an accepted road network.
The existing signal/count commands do not accept the handoff file directly.
Prepare their existing requests with the same network hash and the matching
`candidate/manifest.json`. The handoff records which checked network to use.

Keep the network fixed while fitting demand. If a road change is needed,
return to road construction, repeat the affected checks, and rebuild the
affected signal and detector bindings. Do not improve a count fit by silently
changing road geometry or vehicle permissions.

Use a new output directory and the existing signal, count, and demand tools.
The `torii hamburg build-network` command does not run these stages. Counts
and historical signal states are not prerequisites for building a road network.
A diagnostic signal plan does not reproduce historical operation.

For general count-to-route methods, load
[detector-constrained-demand-reconstruction.md](detector-constrained-demand-reconstruction.md)
only as needed. Retain the declared source dates and comparison window.

## 1. Bind Signal Identities When Needed

```powershell
torii hamburg bind-aerial-signals <request.json> <output-dir> --json
```

Bind a signal only when node, official connection id, ingress lane, and egress
lane match exactly and the materialized internal lane owns one SUMO controlled
link. Record MAP/TLD asset-version mismatches instead of repairing them by
distance. This stage binds identities only. Official timing and historical
states remain separate gates.

After a demand route file is available, a separate diagnostic signal candidate can use:

```powershell
torii hamburg build-protected-signals <request.json> <output-dir> --json
```

The request binds the network, signal-binding CSV, and demand route file by
hash. Torii translates SUMO junction-request conflicts to TLS link indices,
keeps exact Hamburg signal groups intact, uses protected green only, computes
yellow and clearance intervals, and can allocate one common cycle from the
fixed demand. Unknown links remain conservative pseudo-groups. Feedback green
or offset overrides must preserve the declared cycle and remain diagnostic.

## 2. Bind Official Count Stations

```powershell
torii hamburg bind-aerial-counts <request.json> <output-dir> --json
```

Use Hamburg's published `Zählstelle` inventory with the frozen `Zählfeld`
window. Direction 1 and 2 stations are directional totals. Direction 0 is a
QA total only. The station `zusammensetzung` property is the membership list.
never replace it with a sum of every nearby field. A `Z.*` asset number is not
a MAP, TLD, or SUMO lane number.

Bind only the fields named by one complete directional station composition.
Use the station's physical direction. Do not guess a turn when `fahrspur` is
empty. Keep a station group as validation-only when its members map to more
than one directed SUMO edge. If Hamburg publishes no station composition for
a node, leave that node unconstrained instead of creating an aggregate.

Keep calibration and validation detectors separate. A common predecessor,
common successor, or other conserved cross-section may constrain demand, but
it must not replace the published count-field positions in the final replay.
Build the validation E1 bank from the count-field mapping rows. If multiple
official fields project onto the same `(node, SUMO lane)`, write one E1 at the
downstream-most mapped field position and retain every source field id for the
expected-count aggregation. Mark low-confidence, `needs_review`, or collapsed
projections as diagnostic instead of claiming an exact physical match.

Write the Hamburg source URLs and published rules separately from Torii's
interpretation rules. Preserve the comparison window and warm-up from the
declared count-scope manifest. This stage does not generate routes.

Before route sampling, call the existing `sumo_detector_route_support` workflow
with the candidate network and detector-mapping CSV. Torii must exclude
`needs_review`, `inactive`, `out_of_scope`, and `ignored` detector mappings.
route support counts only explicit `active` mappings. Require every active
detector to appear in at least one boundary-to-boundary candidate route.

## 3. Generate and Check Demand

```powershell
torii hamburg generate-aerial-demand <request.json> <output-dir> --json
```

Use only active station-composition bindings and complete passenger-lane edge
cross-sections. Always sum the published composition members. A conservative,
medium, or high label must not change an official count.
Write virtual E1/E2 detectors, fifteen-minute expected counts, edgeData,
routeSampler evidence, and one demand route file. Require zero routeSampler
deficit and overflow. The result is a detector-constrained plausible demand,
not a unique OD matrix.

For time-profile calibration, do not equate departure interval and detector
passage interval. Record per-vehicle edge exit times, estimate a dynamic
assignment-ratio matrix, and update all active station groups jointly with a
nonnegative prior-regularized least-squares model. Rebuild the matrix after
each SUMO run. Preserve the protected signal plan and reject any update that
introduces collisions, teleports, unfinished demand, or worse held-out error.
Include pre-window traffic state instead of forcing first-bin detector counts
to originate at the simulation boundary at time zero.

For final departure-time corrections, use `scripts/hamburg_event_retiming.py`.
Reproduce the frozen run first. Fix each vehicle's speed factor from its
high-precision output and keep the random seed fixed. Sorting departures can
otherwise assign new random speed factors to unchanged vehicles. Evaluate
each proposed departure change in a full SUMO run using instantaneous E1
events and the declared station/bin counting rule. Accept a change only when
total error decreases, no station error increases, and all vehicles complete
without warnings, collisions, or teleports. Check vehicle identities, speed
factors, and periodic-versus-instantaneous detector records. This procedure
fits the calibration window. An exact fit is not held-out validation.

Run SUMO with the exact frozen network, demand, detector files, and declared
horizon. The existing two-hour example uses its 9,000-second horizon, including
warm-up. Report loaded, inserted, arrived, running, waiting,
teleports, collisions, and unfinished tripinfo rows. Any backlog or teleport
keeps the replay blocked even when routeSampler matches every count constraint.
Before comparing counts, require every station detector-bank row to report
`placement_basis=physical_field_projections`. Treat a conserved or movement
proxy as calibration-only evidence and block a same-location digital-twin
claim.

Audit the detector event meaning before changing demand. Near the end of a
very short SUMO edge, `nVehContrib` can omit vehicles that enter the loop but
cannot leave it normally on that edge. Do not repair this residual by adding
vehicles. If road geometry needs repair, return to construction and recheck
a separate network. A declared entry probe can support a diagnostic result.
When a station sums several lane loops, `nVehEntered` can
count one lane-changing vehicle more than once. Use instantaneous loop entry
events to deduplicate by `(station, vehicle, interval)` for the station audit.
retain the raw per-lane E1 output for traceability.

If departures continue until the comparison horizon, add a later flush horizon
before judging completion. Do not treat vehicles that departed near the last
comparison second as permanent backlog. For cause isolation, keep the network,
route file, seed, departure window, and flush horizon fixed. Compare the current
signal program, `--tls.all-off`, and a declared scaled-demand run. Turning
signals off is diagnostic only because priority rules may create collisions.
If scaled demand clears while full demand does not, investigate boundary
storage and demand realization before tuning signal timing.
