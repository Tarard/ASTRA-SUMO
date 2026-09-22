# Aerial boundaries with Hamburg road references

Add this optional field to an intersection in a `build-network` request:

```json
{
  "road_reference": {
    "topology": {
      "path": "sources/road-topology.geojson",
      "sha256": "REPLACE_WITH_SHA256"
    },
    "cross_sections": {
      "path": "sources/road-cross-sections.geojson",
      "sha256": "REPLACE_WITH_SHA256"
    }
  }
}
```

The two files must be GeoJSON FeatureCollections in longitude and latitude.
Use the Hamburg HH-SIB road network and cross-section services. Keep the
download date and license in your source records. Relative paths resolve
from the construction request.

- [Road topology](https://api.hamburg.de/datasets/v1/strassen_und_wegenetz/collections/strassennetz_gesamt/items?f=json)
- [Road cross sections](https://api.hamburg.de/datasets/v1/querschnitte/collections/querschnitte/items?f=json)

Use a `bbox` and check pagination when downloading a local subset. The
cross-section service contains generalized trapezoids from a 2016 survey.
Its recent publication or retrieval date does not make its boundaries current.
[Hamburg dataset description](https://suche.transparenz.hamburg.de/dataset/strassenquerschnittsflaechen-sib-hamburg16)

## What the workflow does

For each supplied intersection, `road_edges.<node_id>` records road names,
directional lane-count metadata, surface categories, and missing geometries.
It searches the aerial image within 3 m of generalized motor-road boundaries.
Short probes compare both sides of a possible boundary. A continuous sequence
limits lateral changes to 1 m between successive observation stations.
Weak contrast, competing edges, image borders, and search limits remain unresolved.

The stage writes:

- `road-edge-evidence.json`: reference identities, candidate lines, individual
  observations, contrast scores, and limitations.
- `boundary-review.png`: the original image with orange reference outlines and
  cyan image-contrast candidates. The original source image remains unchanged.
- `motor-road-prior.npy`: a soft preference for generalized motor-road areas.

The soft prior, bound to the exact aerial image and geographic bounds, enters
the existing movement tracer. It reduces preference outside the historical
motor-road area but never sets that area to zero. Cyan boundary candidates
remain review material. They do not directly replace lane or junction geometry.

The curve tracer now checks every segment for image support, including the
final segment to its endpoint. Smoothing cannot introduce an unsupported
shortcut. A failed trace falls back to the official movement geometry and
records the failure. That fallback is not an aerial validation result.

## Method and limits

The road-prior and moving-probe ideas draw on
[Google's US8938094B1](https://patents.google.com/patent/US8938094B1/en).
This implementation uses explicit image scores. It does not reproduce Google's
trained classifiers, use Google imagery, or establish equivalence to Google Maps.

Contrast can come from a curb, a painted marking, a vehicle, a tree crown,
or a shadow. A continuous line can still be wrong. The diagnostic thresholds
are not calibrated probabilities or measured positional accuracy.
The current source mask includes the exterior of connected motor-road areas.
It does not recover every internal island, crossing, parking area, or bicycle strip.

Official topology remains the source for road relationships. Image crossings
do not create road connections. The stage changes neither lane counts nor
permissions. Bike-lane and time-dependent bus restrictions require separate
evidence and construction work.

The construction decision stays `review_required` when this stage is present.
Check NetEdit against the aerial images before accepting any resulting network.
