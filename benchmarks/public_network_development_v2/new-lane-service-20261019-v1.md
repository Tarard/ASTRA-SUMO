# Added-lane service and review qualification

This follows the [modal lane repair](modal-lanes-20261019-v1.md). Commits
`95afde2` and `716c9ba` add service inventories, preserve review outcomes, and
exercise a connected native case. Commit `bc2efd8` repairs persisted JSON
evidence comparison.

## Service inventory and outcomes

`new_lane_service` lists each added lane's compatible ordinary incoming and
outgoing movements. It uses the shared loaded-permission reader, including
internal paths. An XML connection with no compatible class access does not
count. Source departure/arrival boundaries do not acquire requirements absent
from compatible source lanes.

Missing boundary service requires review when construction checks otherwise
pass. It does not become a failed native construction. Definite failures still
block. The result, evidence validator, and public release command preserve that
distinction. Skipped later checks remain `not_run`.

The frozen Hamburg passenger-selected addition still has zero incoming and
outgoing ordinary movements on the new lane. Its geometry and permissions
therefore remain insufficient to establish a new through lane or capacity gain.

| Public command | Result | Materialization | Evidence | Later candidate checks |
|---|---|---|---|---|
| Explore | `measured` | constructed | recorded measurements | not publication checks |
| Release | `review_required` | `review_required` | `review_required` | `not_run` |

Both commands retain passing signal and dedicated nonmotor preservation checks.
Construction checks pass, but release qualification does not. Explore still
measures against the same fact table and records zero outside-scope fact changes.
Inputs and implementation stayed unchanged during the committed-code repeat.

## Connected native example

A separate synthetic road adds a lane with explicit compatible incoming and
outgoing connections. Both movement counts are one. Construction and static
service qualification pass. A native vehicle then traverses the road with a
required stop on the new lane. All 35 observed middle-road FCD samples use that
lane at a 0.1-second simulation step, and the vehicle completes its route.

This verifies a finite connected example. It does not establish extra capacity,
traffic benefit, lane-change service, or field validity. The real Hamburg default
candidate remains unverified for through service. Intentional auxiliary use
still needs separate evidence.

## Persisted-evidence false alarm

The real-data repeat exposed a second issue. Native path repair stores coordinate
tuples in memory. JSON writes them as arrays, which return as Python lists.
The old comparator treated those representations as different evidence.

The recorded Hamburg report has 463 such tuple/array representation differences,
with equal JSON values. Before repair, its in-memory evidence check failed, while
reloading the same report from JSON passed. The shared comparator now compares
the supplied JSON values with the persisted values. Altered coordinates and
unserializable metadata still fail. No coordinate tolerance or hash check was
removed.

The first probe also supplied exploration-only `facts_file` to release and failed
before construction. Corrected probes use separate final calibration. Those
failed calls, the repeated metadata mismatch, and the successful repeat remain
in separate folders.

## Verification and reproduction

Local checks include 111 intermediate consumer tests, 48 final review/contract
tests, five native-through/review tests, and 37 JSON/contract tests. Counts
overlap. Ruff and `git diff --check` passed. Latest complete CI was still running
when this record was written.

The [frozen summary](new-lane-service-20261019-v1.json) binds the raw records and
includes the captured connected native case. Paths are repository-relative:

- Real runner: `outputs/probe_new_lane_service_20261019_v4.py`.
- Configuration and identities: `outputs/new_lane_service_20261019_v1/hamburg-v4/declaration.json`.
- Real command reports, scorecards and diagnostics: `outputs/new_lane_service_20261019_v1/hamburg-v4/`.
- Connected native source, candidate, route, trip and FCD: `outputs/new_lane_service_20261019_v1/connected-native-case/`.
- Reusable logic: `plugins/astra-sumo/src/astra_sumo/core/network_edit_operations.py` and `candidate_contracts.py` in that directory.
- Summary runner: `outputs/freeze_new_lane_service_20261019_v1.py`.
- Offline regressions: `tests/test_new_lane_service.py` and `tests/test_persisted_evidence_json.py`.

Stochastic-driver behavior, automatic-cleaning precision, remaining whole-network
conversion scopes, and operational parking remain open. Static compatible
movements do not prove signal availability over time or discharge capacity.
