# Scoped approach additions and durable conversion failures

Status: diagnostic evidence, 2026-10-16. Construction code: `91b11eb`. Failure records: `ed0db24`.
The [machine record](approach-local-20261016-v1.json) retains controls, failures, lengths, and the complete Hamburg regression.

## Approach conversion

The shared converter now accepts explicit endpoint declarations for added and rewritten ordinary roads.
Its merge rejects missing or undeclared roads and endpoint changes outside those declarations.
Approach construction includes the original upstream junction in the conversion scope so remapped incoming connections have complete inputs.
The existing exterior restoration then preserves the complete candidate and its remapped upstream boundary paths.
Native curve detail follows the source declaration instead of a fixed value of 25.

Four native SUMO 1.27.1 controls cover an interior split, an existing merge junction, a right-side pocket, and a bounded bus lane.
Each converts three of 15 existing roads. The split cases also create one declared upstream road.
All four pass the existing construction checks. Their non-location elements equal a complete-source conversion under the same construction inputs.
Their saved local inputs and merged proposals also reconstruct exactly from the source and native snapshots.
The remote roads retain their original state. These counts do not measure total workflow cost.

## Permission and length corrections

Plain-XML patches now preserve effective permission classes from legacy source formats.
Approach preservation also compares effective classes rather than declaration spelling.
Two regressions failed before that comparison correction. Native TraCI queries then verify the source and constructed lanes under formats 1.2 and 1.3.
Existing controls still reject actual permission changes and illegal passenger stops on the bus-only lane.

A native regression reproduced an explicit 500 m road becoming two 500 m roads.
The split now distributes that declaration by the original spatial split fraction.
With zero end offsets, the result has 300 m upstream and 200 m downstream logical lane lengths.
The upstream section no longer duplicates the source end offsets. Those offsets remain on the downstream section.

Changing lane count can change SUMO's average end offset.
In the asymmetric-offset control, source lane length is 497.5 m. The constructed sections sum to about 498.33 m.
The declared edge lengths still sum to 500 m. The report retains the actual lane lengths and allocation.
This correction does not establish identical travel times or surveyed stop-line accuracy.

## Failure evidence

The shared converter writes a conversion record before returning a native error or propagating a merge exception.
The record retains the command, error text, local scope and input hashes, source checks, and any native output.
A real invalid-lane-index command exits without a network file and still retains that record.
A partial-output mock and an injected merge failure retain their records too.
A source-drift control returns its result and saves a warning. It does not introduce another daily exploration gate.

## Complete Hamburg regression and limits

The same-input rebuild remains `review_required`, with complete topology and all 16 official vehicle movements materialized.
Its final network remains byte-identical to `f09c24c7ef15950f52565a70eed698ef620c993a0188a027a666bc901c1c933a`.
The source inputs remain unchanged.

**No Hamburg approach addition materializes in this run.** Six approach findings remain unresolved.
They include continuing-lane ambiguity, source lane-count mismatch, and a non-unique merge event.
The full rebuild is a regression check. The four native controls exercise the new addition path.
No fixed-cohort driving comparison was repeated. Earlier collisions and unfinished requests remain unresolved.
Contour release-chain validation, joint correspondence, field audit precision, and independent dated labels also remain open.

Affected checks pass in overlapping runs of 148, 89, and 151. Four final failure-record checks pass separately.
Navigation passes seven checks. Ruff and whitespace checks pass. The local full suite was not repeated.

## Verified locations

- Construction: [`hamburg_aerial_approach.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_aerial_approach.py).
- Conversion, merge, and failure records: [`local_network_conversion.py`](../../plugins/astra-sumo/src/astra_sumo/core/local_network_conversion.py).
- Reproducible controls: [`test_hamburg_aerial_approach.py`](../../tests/test_hamburg_aerial_approach.py) and [`test_local_network_conversion.py`](../../tests/test_local_network_conversion.py).
- Construction request: `outputs/claude_followup_20261015_v1/hamburg-request.json`.
- Native controls, failed cases, measurements, and raw rebuild: `outputs/local_approach_20261016_v1/`.
