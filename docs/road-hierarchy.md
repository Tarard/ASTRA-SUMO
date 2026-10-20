# Road hierarchy and main-road selection

Use this stage when a study needs major roads and their necessary approaches.
It creates a separate simulation scope and retains the complete source network.

```powershell
python -m astra_sumo.cli network select-roads source.net.xml outputs/main-roads --keep-tier major --json
```

Use a new output directory. Protect known study or detector roads with repeated
`--protect-edge-id` or `--protect-way-id` options.
OSM way protection matches the native numeric way ID, including direction and `#` split suffixes.
Use explicit edge protection for other naming conventions.

## Coarse tiers

| Tier | OSM class hints in the SUMO edge type |
|---|---|
| `major` | motorway, trunk, primary, secondary, and their links |
| `collector` | tertiary and its links |
| `local` | unclassified, residential, living street, service, track |
| `unknown` | No recognized class hint. Retained for review |

`--keep-tier collector` includes major and collector roads.
`--keep-tier local` includes all recognized tiers.
Existing import presets, including `arterial`, retain their separate meanings.
These tiers are a coarse study policy over OSM class hints, not measured traffic importance.
The stage preserves retained SUMO road priorities and vehicle permissions.

## Necessary roads and boundaries

Protected roads remain selected regardless of tier.
The stage retains one original approach incident to the initial retained roads.
It fixes the original traffic interfaces before adding connectors.
It checks source-supported movements between main-road interfaces for `--vehicle-class`, which defaults to `passenger`.
Missing main-road connections receive source-supported shortest-path connectors, with a recorded reason.
Side approaches retain local turns, including movements across the main junction to another side approach.
They do not require residential routes between distant side streets.
Added connectors do not create new required traffic interfaces.
The stage does not demand reverse travel where the source has no such path.

`boundaries.json` retains the original study interfaces, with entry and exit road members.
`final_cut_attachments` separately records attachments after connector construction.
Boundary locations are not calibrated demand zones. The demand stage must declare their grouping.
Nonpassenger approaches remain as geometric context and receive no passenger boundary assignment.
Approaches without retained core access remain listed. Their local crossing turns can still load a junction.
It assigns no traffic rates. Demand fitting must define how traffic enters and leaves at those locations.
A cut boundary is a simulation choice, not evidence that a street is absent in reality.
Source-model dead ends are not observed traffic origins.

`selection.json` records every source tier, selection reason, excluded road, connector repair, and boundary movement.
It also records the largest detour, added length in meters, and source/selected turnaround flags.
Selection lengths sum source ordinary-road logical lengths before native extraction and exclude internal junction paths.
Native changes at cut ends are recorded separately in the geometry report.
They are a routing proxy, not measured travel distances or travel times.
No universal detour-ratio cutoff is imposed.

## Native construction and checks

The stage reuses the existing narrow cleanup for stale pedestrian-internal signal references.
It preserves runtime rail controllers. Unsupported signal-reference repairs remain blocked with the selection report retained.
`normalized_full_network` identifies the full-network input after this recorded preparation.
Use that input when a later comparison needs a loadable full-network baseline.

Native extraction uses preload selection once.
The previous postload attempt crashed or stalled on the frozen Ring1 input. Its failed runs remain in the execution history.
Native output and any failed partial file remain separate artifacts.
A successful process status, zero exit code, and readable SUMO XML are required before a native proposal becomes a candidate.
No conversion runs when all roads remain selected or no roads match. These cases return `no_effect` without a candidate.

`report.json` verifies the realized roads, required reachability, retained local turns, lane permissions, road priorities, and SUMO load.
It retains geometry differences, source preparation, signal-program changes, and restoration findings.
The result has no automatic promotion authority.
The Ring1 `case.json` shows geometry and signal findings beside its measured status.
Road-level reachability does not establish lane-level execution, queue storage, or valid field signal timing.

## Hamburg example

The [Ring1 demonstration](../examples/06_hamburg_ring1_demo/README.md) rebuilds a frozen OSM snapshot offline.
Its first two stages are implemented. Junction consolidation, final detectors, count fitting, and comparison remain later stages.
