# Removed endpoints and the remaining request cohort

Production code: `642b2f8`.
The [frozen record](removed-endpoint-coverage-20261020-v1.json) binds the committed implementation, source fixture, request partitions, reports and preceding complete CI.

## Repair

An endpoint on a removed source road cannot be submitted as an ordinary-road trip to the candidate router.
At `ddedffb`, that unknown ID stopped the native batch before other requests could be checked.
The audit now retains the complete original cohort and records removed-endpoint requests separately.
A separate native-input file contains every remaining request.
The router remains strict. Unknown physical positions on retained roads still receive labelled logical probes.

The report enforces the request partition:

- Physical, logical-only and removed-endpoint counts sum to the original request count.
- The native-input IDs equal the original IDs minus the recorded removed-endpoint IDs.
- The original and complete candidate request files retain every ID and departure time.
- Removed endpoints prevent a full-cohort pass. A successful subset retains review and partial execution.
- A failure between retained roads remains a failure.
- If every endpoint is removed, the complete cohort remains recorded and native routing is not run.

No request is replaced or resampled. No new coverage threshold or command-line switch is added.
The evidence validator checks the counts and normal completion against the actual native subset size.

## Real Hamburg repetition

The decompressed source fixture has SHA-256 `0eb22507926899f40dc1cd356dc52799d14da5cd3bff6fc75c27b17d2447f30a`.
It is the selected Hamburg corridor model.

| Case | Original requests | Native input | Removed endpoints | Result |
|---|---:|---:|---:|---|
| Delete spur `609417456` | 20 | 18 | 2 | All 18 complete by 600 s; overall routing evidence retains review |
| Delete that spur and `60578487#0` | 100 | 98 | 2 | Strict routing fails on retained-road disconnections |
| Join `5856005698` and `7709567150` | 100 | 98 | 2 | All 98 receive routing; 61 arrive, 36 run and 1 waits at 2400 s |
| Reduce `47854310#1` from 3 to 2 lanes | 100 | 100 | 0 | 3 physical lane selections remain unresolved; 63 arrive, 36 run and 1 waits at 2400 s |

Spur and two-road deletion retain removed vehicle IDs `1` and `12`.
The join retains IDs `16` and `31` on the absorbed road.
The joint edit now returns explore measurements instead of stopping at an unknown-road routing error.
Its driving result remains failed/incomplete, and the source baseline remains dirty.

The direct spur routing result does not approve release or establish the road's field validity.
Modal connectivity and collateral effects remain separate checks.
The join's 98 native requests and the original baseline's 100 are different executed cohorts.
No traffic-performance comparison or mean is reported.

## Checks and complete prior CI

The endpoint group passes 83 tests. Shared consumers and release checks pass 98.
A final boundary group passes 6 tests. Counts overlap.
Native controls cover a successful retained subset, another disconnected retained request, and an entirely removed cohort.
They also check full-cohort retention, input/output protection, malformed coverage and rejection of a forged full pass.
Ruff and whitespace checks pass.

Both complete Windows runs for the preceding code `ddedffb` pass **3754 tests, with 24 skipped and zero failures**.
JUnit and the version-bound execution policy confirm all **54 required native cases** ran successfully, including the bus-permission test.
The earlier failed `b2810d5` runs remain in the [previous dated record](endpoint-routing-coverage-20261020-v1.md).
These completed runs do not qualify the later partition repair.
The current code requires 57 executed native cases. Its full CI was running when this record was written.

## Verified locations and limits

- Runner: [four Hamburg cases](../../outputs/probe_removed_endpoint_hamburg_20261020_v2.py).
- Analysis: [partition and CI receipt builder](../../outputs/freeze_removed_endpoint_coverage_20261020_v1.py).
- Configuration and hashes: [declaration](../../outputs/removedep26v2/declaration.json).
- Raw results: [result directory](../../outputs/removedep26v2/).
- Native regression: [removed-endpoint tests](../../tests/test_routeability_od_mapping.py).

Generated local files are not distributed with the repository.
The public JSON retains the input and report hashes, coverage and removed identities.
Absorbed endpoints still have no qualified ordinary-road physical counterpart.
Incomplete source driving and remaining modal or geometry findings remain open.
