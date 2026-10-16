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
| `set_junction_shape` | Valid polygon strictly inside the original polygon | Exact requested polygon, preserved boundary access, fixed remote lane ends, and unchanged lane widths, speeds, and permissions |
| `absorb_reciprocal_fragment` | Two connected uncontrolled nodes. Reciprocal roads within the explicitly supplied length limit. Fixed-lane paths and preserved control | Exact boundary lane identities and all explicit vehicle-class paths through the former two nodes remain |

The profiles do not require four arms, a single fixed program, or a network
without pedestrians. Signals and mixed traffic outside the edit remain in
the network. The checks protect their exact outside state.

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

The old aggregate modal counts remain in the report as diagnostics. Removing
a declared turn decreases connection count. Absorbing a declared fragment
decreases road and node counts. Those count changes cannot determine whether
boundary access was lost. For these three verified profiles, the final modal
gate uses exact vehicle-class boundary paths. It requires the same materialized
ledger and source. Invalid applicability evidence cannot replace that gate.

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
