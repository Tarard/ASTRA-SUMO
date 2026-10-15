# Parking facilities in a road network

Use parking facility recognition before treating a covered driveway as a road defect.
The current implementation preserves the complete driving network and adds facility metadata.
The aerial overview shows the building outline, a P marker, and entry/exit arrows.
The full detail image retains every lane, turn curve, and junction surface.

## Build a parking-aware network

Use the original detailed SUMO network and a full OSM XML snapshot.
The OSM snapshot must retain building outlines and driveway ways.
A roads-only filtered snapshot can omit that evidence.

```powershell
astra network parking-facilities <source.net.xml> <source.osm.xml> <new-output-dir> `
  --expected-net-sha256 <source-sha256> `
  --expected-osm-sha256 <osm-sha256> --json
```

Both hashes are required. Input files remain unchanged.
Use a new output directory.
The command writes:

- `parking-aware.net.xml`: the complete source network with `astra.parking.*` metadata.
- `parking-facilities.json`: source identities, building footprints, matched roads, internal lane chains, entries, exits, and unresolved cases.

It preserves all source roads, lanes, connection order, signal programs, and existing parameters.
It does not run netconvert or change lane geometry.
The result retains `review_required`, so the CLI returns exit code 1 for a successful annotation requiring review.
`not_applicable` means no supported facility matched. It does not establish that the area has no parking.

## Recognition rules

The code recognizes closed ways tagged `building=parking`.
It also recognizes `amenity=parking` with `parking=multi-storey` or `parking=underground`.
These declarations follow the [OSM parking building definition](https://wiki.openstreetmap.org/wiki/Tag:building%3Dparking).

An interior service way must have a covered/indoor/tunnel declaration or `service=parking_aisle`.
Its complete source line must lie inside exactly one supported parking footprint.
SUMO `origId` parameters must resolve exclusively to those interior OSM ways.
The code keeps mixed or missing correspondence unresolved.
It does not group an ordinary warehouse and its driveway as a parking facility.

Entry and exit roles follow saved directed lane connections across the facility boundary.
Internal lane membership follows complete `via` chains, including lanes split by internal waiting points.
Shared access annotations that would disagree require a separate review.
Open or incomplete footprints, overlapping facility matches, and multipolygon relations remain explicit review cases.
The current implementation does not assemble relation footprints or read PBF files.

Capacity is copied only from a numeric OSM `capacity` value.
Missing capacity remains unknown. Building floor count does not determine capacity.
Source clearance tags remain metadata. This command does not enforce vehicle height or width.

## Draw a facility overview

Add the generated report to a [network aerial review request](network-aerial-review.md):

```json
{
  "parking_facilities": {
    "path": "parking-facilities.json",
    "sha256": "<report-sha256>"
  }
}
```

Keep the request's candidate, aerial image, and view fields.
The candidate must match the report's original or annotated network hash.
If a source network is supplied, it must match one of those hashes too.
Every input and output remains hash-bound.

Blue outlines and P markers identify parking facilities.
Green arrows mark entries. Orange arrows mark exits.
The overview omits matched interior geometry from the image only.
Expand the detail section to inspect the complete saved network.
The report retains the full object inventory and marks related turn reviews as parking-interior geometry.
Those turns are not automatic street-repair candidates.

Generic deletion and micro-merge proposals protect parking aisles and annotated parking roads.
The deletion validator also reads retained bridge and tunnel parameters, including `tunnel=building_passage`.
An old generic deletion ledger cannot silently remove these protected objects.

## Driving and parking behavior

This is a facility representation with an unchanged driving network.
It does not create parking spaces, occupancy, parking search, dwell times, queues, or arrival/departure demand.
An actual SUMO [parking area](https://sumo.dlr.de/docs/Simulation/ParkingArea.html) needs lane placement and parking definitions.
Supply those inputs before adding parking operations.

Retaining the detailed network preserves trips that start or end inside the facility.
Removing the interior and replacing it with an arrival/departure boundary needs a separate demand mapping.
Keep those approaches distinct when interpreting a traffic experiment.

The [Speicherstadt case](../examples/05_hamburg_topology/speicherstadt-parking-facility.v2.json)
selects the detailed network and supersedes the earlier interior-exclusion decision.
That old candidate and its failed traffic checks remain historical evidence.

## Current solution and remaining work

| Need | Current result | Limit |
|---|---|---|
| Recognize a parking building and preserve its access | Implemented through source correspondence and facility annotations | Incomplete or ambiguous sources still require review |
| Replace dense indoor lines in the aerial overview | Implemented through a building outline, P marker, and directed access arrows | This changes the ASTRA review image only |
| Simplify the native SUMO-GUI view | Not implemented | The saved network still contains every indoor lane and turn |
| Simulate parking | Not implemented | Facility annotations do not define parking spaces or vehicle stops |

SUMO load and driving checks establish only that the tested driving case still works.
They do not establish indoor geometry, legal turns, parking capacity, or parking behavior.
The Speicherstadt comparison completed all 100 original requests in both networks, with zero collisions or teleports.
Trip records, route records, and per-step summaries matched, apart from runtime duration.
The implementation checks passed 135 tests without failures or skips.
These results cover the detailed, annotated network, not the proposed simplified parking model below.

## Proposed native SUMO parking model

For a street-network study, represent the garage with verified access roads and one or more SUMO parking areas.
Use this as a separate candidate when detailed indoor circulation is outside the study scope.
Keep the complete detailed network as the comparison model.
The current command does not create this candidate.

The proposed model has these requirements:

- Retain the verified street entrances, exits, travel directions, and connections to public roads.
- Place each `parkingArea` on a suitable service lane with explicit position and capacity.
- Define parking duration through vehicle stops that refer to the parking area.
- Use observed parking arrivals and departures when available. Keep assumed demand and duration distributions explicit.
- Map every trip with an indoor origin or destination to its assigned parking access. Record unmapped trips as unresolved.
- Preserve entrance storage where queues can extend toward the street. Check behavior when the parking area is full.
- Keep street lanes, signals, crossings, and unrelated movements unchanged.

SUMO's [parking area documentation](https://sumo.dlr.de/docs/Simulation/ParkingArea.html)
defines the lane placement, capacity, additional-file input, and vehicle stops.
Unknown capacity or access correspondence prevents acceptance of an operational parking model.
Do not infer either from the roof image or the number of building floors.

For native display, add a separate building polygon and parking marker to the simulation view.
Keep an inspectable view of the access lanes and parking queues.
SUMO supports [polygon and point overlays](https://sumo.dlr.de/docs/Simulation/Shapes.html).
An overlay alone does not simplify indoor roads or add parking behavior.

Accept the simplified candidate only after checking all entry and exit movements and every original trip assignment.
Test parking arrival, the requested stop duration, departure, and a full-capacity case.
Compare street access, completion, travel times, queues, collisions, and teleports with the detailed model under the same demand.
Record intentional differences caused by parking and retain failed runs.
Verify the native SUMO-GUI appearance separately from the ASTRA aerial overview.
Until those checks pass, the simplified parking candidate remains `review_required`.
