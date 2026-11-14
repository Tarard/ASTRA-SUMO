# Load replay: the same trips on several networks

Use `astra network load-replay` to see what one demand does on two or more networks of the same area, for example a
plain netconvert build and an ASTRA build, or a build before and after a change.

Every network gets the same trip file and the same SUMO seed. Teleporting is off and the junction collision check is
on. A collider, its victim and a teleported vehicle count as not arrived, even when they reach their exit later.

The result describes these runs. More arrivals do not prove topology, demand, control or field correctness, and the
trips are not observed demand.

## Why a separate replay

The boundary load of the OSM workflow uses four vehicles per entry. It finds endpoints that cannot be reached. It does
not find a junction that passes one car per signal cycle; such a queue only shows at a heavier load. Two builds are
also only comparable when they carry identical trips, and each build draws its own.

## Steps

1. Write the trips once, on the network whose entries and exits define the demand:

   ```powershell
   astra network boundary-completeness <reference.net.xml> <new-dir> --bbox W,S,E,N --vehicles-per-entry 15 --seed 42 --json
   ```

   The trips are in `<new-dir>/boundary_completeness.trips.xml`. Keep that file; it is the frozen demand.

2. Replay it:

   ```powershell
   astra network load-replay <trips.xml> <new-dir> --net plain=<plain.net.xml> --net astra=<astra.net.xml> --seed 42 --json
   ```

   The first `--net` is the reference of the comparison. The output directory must be new.

3. Repeat both steps with other seeds. One seed is one draw; report every seed you ran.

`--initial-end` (2700) and `--max-end` (3600) are the two simulated end times. The run stops at the first one when every
vehicle has arrived.

## Result

`<new-dir>/load-replay.json` holds one row per network:

| Field | Meaning |
|---|---|
| `trip_count`, `skipped_trip_count` | Trips replayed, and trips skipped because the network lacks their first or last edge for passenger cars. The replayed subset is written to `replayed_trip_file`. |
| `arrived_count`, `arrived_share` | Vehicles that arrived without a collision or teleport, and their share of the replayed trips. |
| `arrived_share_minus_reference` | Difference to the first network. It is `null` unless both replayed every trip of the file. |
| `not_arrived_states` | Counts of `collision`, `teleported`, `unfinished` and `not_inserted`. |
| `collision_count`, `collision_junctions` | Collision events of the final run, and the junctions where the vehicles collided. |
| `teleport_count` | Teleports in SUMO's summary. SUMO removes a collider by a teleport, so this includes the collisions; a teleport for another reason shows as `teleported` in `not_arrived_states`. |
| `unfinished_last_edges` | The edges where vehicles still stood or drove at the end, most frequent first. A long list on one edge is a queue. |
| `standing_vehicles` | Vehicles at the same lane position at both end times, with their edges. These did not move for the time in between. |
| `not_arrived_vehicles` | Every vehicle that did not arrive, with entry, exit, state and last edge (in the file only). |

The status is `measured` when at least one network was replayed and `blocked` with a `reason` otherwise. A network that
has none of the trips' edges is `blocked` in its own row.

## Draw a window of the replay

To look at a queue or a collision, or to show two networks side by side, draw a window of the replay:

```powershell
astra review replay-motion <new-dir>/load-replay.json <another-new-dir> --bbox W,S,E,N --begin 1800 --end 1860 --json
```

It runs the routes of the replay again with the same seed and options, up to `--end`, and records the positions
inside the window, the signal states there and the summary. It then writes `motion.request.json` with the hash of
every file and renders it as [motion review](motion-review.md) does, into `motion/`. The replay folders are only
read.

- Without `--network`, the first two replayed networks are drawn; name one or two with `--network <name>`.
- `--begin` to `--end` is at most 599 seconds and must end before the replay did.
- `--buildings <file.poly.xml>` draws building footprints under the lanes; `--no-signals` leaves the signals out.
- `--pixels-per-metre` sets the scale. For a whole district use about 0.2 and `--no-signals`.

The status is `measured`, or `blocked` with a `reason` when SUMO is missing or a run fails.
