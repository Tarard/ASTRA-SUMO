# Bad Case

This case is intentionally incomplete.

## Setup

- Controller: fixed-time TLS program edited manually in `netedit`.
- Demand: route file regenerated for each run with no saved seed list.
- Outputs: only one `tripinfo.xml` path reused across all runs.
- Horizon: simulation stops at `3600s`.
- TLS evidence: no TLS switch output or phase log.

## Intended Claim

"The adaptive controller reduces average waiting time by 18% compared with fixed time."

## Audit Risks

- Route demand may differ between controllers.
- Output files may be overwritten.
- Arrived-only tripinfo may hide unfinished vehicles.
- TLS phases and yellow/all-red behavior are not documented.
