# Review road findings by traffic effect

Use a new output directory for each development review:

```powershell
astra network review-effects candidate.net.xml review-v1 --parent-net-file parent.net.xml --vehicle-class passenger --json
```

The command runs the existing Connection Mode and independent movement-safety
checks. It writes the original findings and a separate ordered review. The
manifest binds the inputs, original reports, comparison, and review by SHA-256.
Each review group retains a pointer to every original finding. No finding is
discarded. Group membership does not imply that one edit resolves every member.

The Connection Mode audit also includes `traffic_effect_review` for its own
findings. This includes the existing standard network audit and OSM workflow.
Its pointers refer to that Connection Mode report. Use `review-effects` to add
independent conflict evidence and an optional parent comparison.

## Review order

| Priority | Evidence | Meaning |
|---|---|---|
| 1 | Confirmed model geometry conflicts with protected greens, or a permissive conflict lacks a yield relation | Inspect the exact signal program, phase, movements, and source geometry first |
| 2 | Empty movement permission intersection, or changed local vehicle-class access | Inspect movement availability in the model |
| 3 | Changed permitted lane supply against the parent | Inspect the lane assignment and capacity implications |
| 4 | Potential envelopes, uncertain lane assignment, control or coverage gaps, and other unresolved effects | Collect the missing traffic evidence |
| 5 | Endpoint offsets and lane-order geometry alone | Inspect the measured geometry without inferring route failure |

These are review priorities. They are not accident probabilities, safety
ratings, or field defect labels. Missing coverage remains a blocker in its
original report, regardless of review order. Potential envelope overlap never
becomes a confirmed conflict through grouping. Shared signal-group findings
also need the actual phase states before they receive priority 1.

The independent check uses the existing geometry and signal rules. The review
does not change their tolerances. It does not change any promotion decision.
Original severities, audit statuses, and unknown effects remain visible.

## Parent comparison

The optional comparison uses exact edge and lane identities. It counts lanes
that allow the selected vehicle class. It also traces local connections through
internal lanes and their permission restrictions. It cannot use an exterior
detour to replace a missing local movement.
The comparison follows SUMO's compiled-network permissions on incoming,
internal and outgoing lanes. It resolves legacy class names and the network
version's implicit exclusions. Connection `allow` and `disallow` directives
take effect during conversion; SUMO's network loader uses the resulting lane
permissions. Changing those directives alone in a compiled network does not
establish a loaded access change.
It records changed lane width, speed, and storage length for shared permitted
lanes. Unspecified dimensions remain unknown. Numeric formatting changes do
not count as changed dimensions.

A changed local movement does not establish loss of an origin-destination
route. A lane count is a capacity proxy. Neither lane count nor width supplies
a measured saturation flow. The report therefore leaves route effects and
capacity in vehicles per hour unknown. Use the existing same-request native
routeability checks and road fact scorecard to measure those effects.

Renamed or split roads require correspondence review. The comparison reports
their changed identities. It cannot establish that an absent edge is a missing
physical road. Run separate vehicle classes when bus, bicycle, or pedestrian
access matters. The independent conflict audit still covers its original modes.
Changes involving different edge endpoints or lane identities without a count
change remain correspondence tasks at priority 4.

When a parent is supplied, independent safety findings also show whether their
exact witness signature exists in the parent. This separates inherited model
findings from unmatched witnesses. An unmatched witness does not establish a
new physical defect when network identities have changed.

## Evidence limits

The command creates development artifacts. It does not replace frozen research
assessments, witness ledgers, sampling probabilities, or blinded review packages.
Run it in a separate output directory. Existing raw findings remain available
through exact JSON pointers and report hashes.

The historical 30-corridor findings have no completed field adjudication. Their
false-alarm rate remains unknown. This review order does not estimate precision
or establish automatic-cleaning accuracy.
