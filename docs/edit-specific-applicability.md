# Local edit applicability

Use an edit profile to check one explicitly accepted, source-bound action:

```powershell
astra network verify-edit ledger.json verification-v1 --netconvert-binary C:/project/.venv/Scripts/netconvert.exe --json
```

Use a new output directory. The ledger uses the existing
[scoped edit format](network-edit-operations.md). The source network, accepted
action, evidence files, and rollback must be explicit. The source SHA-256 and
every evidence hash must match before construction.

## Supported profiles

| Edit | Local preconditions | Candidate checks |
|---|---|---|
| `suppress_turnaround` | Exact existing uncontrolled turnaround. Every affected vehicle class retains another local exit | Only the declared movement disappears. All other explicit boundary paths and their vehicle classes remain |
| `set_junction_shape` | Valid polygon strictly inside the original polygon | Exact requested polygon, stable native source-contour control, preserved boundary access, fixed remote lane ends, and unchanged lane widths, speeds, and permissions |
| `absorb_reciprocal_fragment` | Two connected uncontrolled nodes. Reciprocal roads within the explicitly supplied length limit. Fixed-lane paths and preserved control | Exact boundary lane identities and all explicit vehicle-class paths through the former two nodes remain |

The profiles do not require four arms, a single fixed program, or a network
without pedestrians. Signals and mixed traffic outside the edit remain in
the network. The checks protect their exact outside state.

Contour edits retain native geometry reconstruction. Final candidate gates also
materialize the source's own polygon as a custom contour. This identity control
uses the same converter and construction path as the requested edit.
The report separates source-to-control, source-to-candidate, and control-to-candidate
geometry changes. It records XY and XYZ endpoint movement, shape displacement,
declared lengths, missing lanes, and unavailable comparisons.

The stability screen uses the existing connection-audit tolerance of 0.1 m.
Large changes under the identity control return
`custom_contour_recuts_source_geometry`. Missing or unverified controls require
review before automatic acceptance. Explore mode retains its measurements and
does not require this final qualification.

The control has no promotion authority. Its source, native materialization, and
artifacts remain bound to their hashes. A stable, verified control becomes the
pedestrian geometry reference. Candidate pedestrian paths and facilities must
match that reference. Newly invalid walking areas also disqualify the control.
Support for automatically accepting a changed pedestrian layout remains incomplete.

The requested edit has a separate geometry screen. It compares ordinary lane
ends with the measured polygon displacement plus the existing 0.1 m tolerance.
The logical-length budget covers two moving ends and the observed spatial grade.
Boundary checks allow declared stop-line offsets and compare with the control's
existing boundary distances. Large effects remain measured and require review.

Final gates retain `review_required` when no gate has a definite failure.
Unexecuted gates carry `execution_status=not_run` and remain unverified. They do
not become executed failures, and they cannot satisfy publication requirements.

Local rail, bridge, and tunnel semantics require separate review. Controlled
turn removal also requires signal review. Absorption with local pedestrian
lanes, crossings, or walking areas requires pedestrian-route evidence. Diagnostic signal rebuilds
and assumed interior lane changes do not enter these automatic profiles.
Other edit types return `not_applicable` from this entry point. Their existing
construction tools remain available.

## Verification chain

The command checks the local preconditions, calls the existing materializer,
and independently checks the actual candidate. It compares every explicit
boundary path for every SUMO vehicle class. A path must stay inside the declared
nodes. It cannot use an exterior detour or assume an interior lane change.
Boundary access uses loaded lane permissions and each network's format version.
Compiled connection directives supply topology but do not restrict loaded vehicle classes.

It also checks outside geometry and state, fixed remote lane ends, protected
feature counts, unchanged signal programs, preserved pedestrian facilities,
connection structure, and new independent safety findings. A candidate that
adds a hard safety finding remains blocked.
Shape coordinates use the native writer's declared 12 decimal places. The
requested polygon and the actual polygon must match at that representation.
The materializer restores the declared polygon after native input quantization.
Polygons that collapse at the declared output precision are rejected.
The materializer restores unchanged external lane speeds exactly from the
source after native conversion. Raw native proposals retain any conversion drift.

The existing final candidate gates then check persisted construction and review
evidence, protected semantics and TLS bindings, SUMO load, native route
completion, and topology. Required map review still applies. Route requests
come from the original network. SUMO and duarouter come from the same directory
as the selected netconvert binary.

A materialization that requires review can still run load, route, and topology
diagnostics when its persisted evidence is valid. These measurements do not
approve the candidate. Existing blocked checks remain blocked in the final
decision. Invalid artifact identities or unreadable XML prevent execution.

Controlled-connection comparison follows source-bound retained lane indices
from the hashed materialization plan. Declared lane removals appear separately
from missing retained connections. Removed controlled connections still count
as missing and still fail this gate. Changed signal bindings also fail.
Vehicle-mode connectivity
counts ordinary movements with loaded permissions, including their internal
paths. Changing the number of internal path pieces alone does not remove a
movement. Pedestrian connectivity retains its separate explicit-connection
comparison and does not prove every implicit walking route.
An unresolved internal-lane reference blocks the modal check with a saved cause.
It does not abort the report or count as a measured loss of zero movements.

If no shared diagnostic origin or destination remains, routeability reports
`review_required` with `execution_status: not_run`. This can occur in a small
extract when the edit moves every incident road. It does not establish route
preservation or identify an invalid network. Route generation and execution
failures still block qualification.

If the contour identity control is unqualified, source-relative pedestrian
geometry checks cannot distinguish native recutting from the requested effect.
Their original results remain under `unqualified_source_pedestrian_checks`.
They require review instead of reporting a confirmed edit defect. Independent
permission, structure, safety, and requested-geometry failures still block.
Under a stable control, a changed pedestrian layout also requires review because
automatic layout acceptance is unsupported. The report uses
`pedestrian_layout_change_requires_review`. A newly invalid walking-area polygon
remains blocked. Existing invalid source areas are retained as source findings.
Missing or added pedestrian facilities, changed facility types, and changed
facility lane identities remain blocked. The report separates these structural
changes from geometry-only differences, even when facility counts stay equal.
Facility structure includes the roads spanned by each crossing and the incoming
and outgoing lane neighbours of each walking area. These bindings must remain
unchanged even when the overall boundary-path sets are preserved.
Structure is compared with the immutable source, regardless of the identity
control's geometry result. An unqualified control changes only the interpretation
of geometry-dependent comparisons.
Walking-area validity also uses the immutable source. A newly invalid candidate
area remains blocked when the identity control is unqualified or absent.
The reason is `candidate_newly_invalid_walking_areas`; the control record retains
whether the same defect was already present in the reconstruction control.
When topology never runs, `whole_network_review.execution_status` is `not_run`.
OD diagnostics retain excluded road IDs, reasons, and source/destination counts.

The old aggregate modal counts remain in the report as diagnostics. Removing
a declared turn decreases connection count. Absorbing a declared fragment
decreases road and node counts. Those count changes cannot determine whether
boundary access was lost. For these three verified profiles, the final modal
gate uses exact vehicle-class boundary paths. It requires the same materialized
ledger and source. Invalid applicability evidence cannot replace that gate.

### Inherited topology findings

Typed scoped edits also compare candidate findings with the source audit.
The materialization plan must match its manifest hash and actual source.
The checker recomputes the operation's node and road scope before using it.
An unchanged finding outside that scope does not fail the local edit.
A finding that intersects the scope still requires review, even if inherited.
The report retains the whole-network review status and inherited finding count.
For verified direct speed, permission, and stop-line changes, an unchanged
in-scope finding is informational. Its record remains in the report.
New or changed findings still require review. Edits that reconstruct junctions,
including lane-width changes, retain the ordinary scope rule.

A valid topology audit with unresolved findings returns `review_required`.
Missing, changed, or invalid audit evidence remains `blocked`.

For an edit that passes its automatic applicability profile, the final topology
check also audits the immutable source with the same parameters and OSM context.
It compares cluster membership by source node IDs. A declared fragment absorption
maps its removed nodes to the declared join ID. Positional cluster labels do not
identify a finding.

New clusters, changed classifications, and increased recorded risks require review.
The comparison also checks node types, edge and connected-pair counts, connected
node pairs, maximum internal length, and aggregation confidence. A failed OSM
corridor audit cannot qualify as an unchanged baseline.
Unchanged inherited findings outside the edit scope do not prevent technical
qualification. A source finding that contains an edited node requires review,
even when that finding is unchanged.
The report retains both audits, the inherited finding count, and a separate
`whole_network_status`. The outer gate and edit-verification reports also expose
`whole_network_review`, including the inherited and local finding counts.
An unscoped candidate still requires whole-network review.
This comparison does not resolve source findings or establish field accuracy.

All other callers retain their existing aggregate modal gate. The report and
manifest bind the ledger, source, candidate evidence, and results. Changed
inputs block the result.

## Claim limits

`pass` means that this accepted edit meets its technical profile and all
existing candidate gates. It does not mean that the edit was needed in the
field. Source proximity does not establish one physical junction. A SUMO
turnaround direction does not establish a real prohibition. Use independent,
dated road facts to select an edit for an actual road.

Explicit pedestrian lane connections do not cover every implicit walking-area
route. The absorption profile therefore retains its local pedestrian review
boundary. Lane storage lengths and internal priority rules can change inside
an accepted join. The check does not establish unchanged queues or capacity.

This is a development verification path. The frozen whole-intersection
certification envelope, blinded research study, and automatic-precision claims
remain separate. No historical case receives a new certification label.

## SUMO basis

SUMO supports explicit node joins and lane-to-lane patches in
[PlainXML](https://sumo.dlr.de/docs/Networks/PlainXML.html#joining-junctions).
Its [turnaround documentation](https://sumo.dlr.de/docs/Simulation/Turnarounds.html)
describes removal of turnaround connections. These capabilities define how the
edits are applied. Their availability does not establish when a real road needs
an edit.
