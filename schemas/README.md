# Torii Schemas

This directory contains JSON Schemas used by Torii.

## Product schemas

`product/` contains schemas for current product-facing data structures.

- `torii.signal-device-profile.v1.schema.json`
- `torii.signal-device-profile-inventory.v1.schema.json`

## Research schemas

`research/corridor/` contains frozen corridor-research and benchmark schemas. These support reproducibility for the research and held-out evaluation material under `benchmarks/` and `docs/research/`.

The research schemas are versioned artifacts. Do not silently rewrite an existing version. Create a new version when the contract changes.
