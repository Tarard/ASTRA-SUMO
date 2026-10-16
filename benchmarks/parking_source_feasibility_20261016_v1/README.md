# Hamburg parking-source snapshot

This directory retains one unchanged public WFS response and its [source check](speicherstadt-source-check.json).
The response was retrieved on 2026-10-16 at 08:25:15 UTC. It contains 127 feature members, including Speicherstadt.
Use [`hamburg-parking.gml`](hamburg-parking.gml) to verify the SHA-256 in the check.
Git keeps its original bytes. The original local response also remains in the generated run directory.

Source: [Parkhäuser Hamburg, official catalog](https://suche.transparenz.hamburg.de/dataset/parkhaeuser-hamburg17).
Attribution: Freie und Hansestadt Hamburg, Behörde für Verkehr und Mobilitätswende.
License: [Datenlizenz Deutschland Namensnennung 2.0](https://www.govdata.de/dl-de/by-2-0).

The publisher describes partial, unchecked availability data.
Keep the envelope count mismatch, unresolved timestamp meaning, and inconsistent advertised update intervals visible.
This snapshot provides no historical series, verified capacity, vehicle-portal correspondence, or field-performance claim.

## Offline audit

The [audit](snapshot-audit.json) finds availability fields in 45 records. The other 82 records have no such fields.
Four dynamic totals are nonpositive. Three of those records also report positive free spaces.
These values cannot establish parking stock. A zero total must not silently become evidence of an empty facility.
Five records have different status texts. Another has a missing situation field.
Keep these findings. A closed or fault status does not itself establish whether a reported count is wrong.

Under a UTC reading, 44 receipt times appear in the future.
Under a UTC+02 hypothesis, none do. Kunsthalle's receipt field refers to April 2025 under either hypothesis.
This supports a local-time hypothesis. It does not establish the declared time zone or the timestamp's measurement meaning.

Run `python benchmarks/parking_source_feasibility_20261016_v1/audit_snapshot.py` to reproduce the measurements.
The verifier checks the committed response hash and member count before reading its fields.
It makes no network request and configures no recurring collection.
