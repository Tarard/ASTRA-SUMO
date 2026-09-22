# Held-out corridor blind-review protocol v1

> **Frozen pilot artifact.** This file and its v1 policy are retained unchanged
> as a historical pilot contract. They are not the active Stage 1-M trial and
> must not be edited in place to make the current corpus pass.

This protocol implements the Stage 1 human-evidence gate defined by the
Torii-SUMO corridor research plan. It is a preregistered pilot protocol, not a
claim that human validation has already happened.

## Separation of information

The packager emits two different artifacts:

1. A reviewer-visible dataset containing random case and variant codes, source
   and candidate hashes, a neutral question, and required observations.
2. A restricted unblinding key containing true review/candidate IDs, machine
   labels, finding categories, safety-critical flags, city/morphology strata,
   and the blinding seed.

Before the reviewer-visible dataset is released, each machine assessment is
also written as a separate restricted JSON artifact. The unblinding key binds
its relative path and SHA-256. Evaluation reloads that artifact and requires it
to equal the embedded assessment, preventing machine labels from being revised
after human decisions are known.

The reviewer-visible artifact must not contain a machine label, recommendation,
true review-case ID, true candidate ID, peer decision, or unblinding seed. The
two artifacts have independent SHA-256 identities and the evaluation key binds
the exact reviewer-visible dataset hash.

## Human roles

- Reviewer A and Reviewer B inspect every case independently. They attest that
  the machine recommendation and peer decision were hidden.
- A third person adjudicates every case after both decisions are frozen.
- Reviewer and adjudicator slot IDs in the public preregistration are
  pseudonymous role identifiers. The study administrator must retain a private,
  access-controlled identity-to-slot record; Torii does not publish personal
  information.

Every decision records timezone-aware start/end times, observed facts, a
rationale, and a candidate choice when applicable. Missing, duplicate, stale,
or cross-trial records block the evaluation.

## Labels

- `defect`: the presented model is not acceptable and no candidate should be
  promoted as shown.
- `acceptable`: the selected candidate is supported by the reviewed evidence.
- `ambiguous`: available evidence does not justify a unique decision.

Machine `defect` and `ambiguous` labels both count as “requires attention.” A
machine `acceptable` label counts toward auto coverage. This keeps precision
and coverage separate and does not reward forced decisions.

## Frozen pilot gates

The versioned policy in
`benchmarks/corridor_human_modeling_v1/held_out_review_preregistration.v1.json`
requires:

- at least 30 cases from at least 3 held-out city groups;
- at least 6 morphology groups and 5 cases per city group;
- both right- and left-hand traffic;
- pedestrian, bicycle, ramp, rail, bridge, and tunnel feature coverage;
- raw reviewer agreement at least 0.80 and Cohen's kappa at least 0.60;
- attention precision at least 0.90 and attention recall at least 0.95;
- auto precision at least 0.99;
- median review time no more than 300 seconds;
- zero safety-critical false negatives.

Thresholds cannot be changed after human decisions are visible. A revised
protocol requires a new schema/policy version and a fresh trial ID.

## Current status

The contracts, blinding packager, evaluator, metrics, schemas, and fail-closed
tests are implemented. No real held-out human decisions or adjudications are
present in the repository, so Stage 1 remains incomplete and automatic
promotion remains blocked.

The current machine corpus also exposes a structural v1 trial contradiction:

- only 27 corridors have complete review packages; three are replay-invalid;
- every current machine label is `defect`, so the prospective auto-pass count
  is zero;
- v1 correctly reports auto precision as undefined when that denominator is
  zero, while its frozen gate requires auto precision of at least 0.99.

Neither lowering the case count nor treating undefined precision as 1.0 is
allowed. The active successor is a new v2 trial with a separate audit-attention
cohort and a future prospective safe-pass cohort, a new parent benchmark hash,
trial ID, schema, deterministic reserve replacements, and finding-cluster
decisions. Its design is frozen by
[`stage1-machine-review-ready-plan.md`](stage1-machine-review-ready-plan.md)
and implemented by the active
[`held-out-corridor-blind-review-protocol-v2.md`](held-out-corridor-blind-review-protocol-v2.md).
