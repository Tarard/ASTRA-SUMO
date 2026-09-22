# Field Lesson Capture

Use this reference when a resolved SUMO/TraCI case reveals a reusable rule that Torii previously missed. Preserve the diagnostic sequence, not the private project.

## Capture When

- the user reports that Torii was wrong, blocked, or too narrow and later found a working path;
- a failed diagnostic becomes resolved through a reusable artifact, command sequence, environment check, controller check, route step, or output interpretation;
- a public SUMO source explains a failure mode not yet covered by Torii.

Do not persist one-off preferences, private dataset facts, unpublished collaborator methods, or unverified guesses.

## Record

```text
field_lesson_record:
  original_goal:
  observed_gap:
  failed_assumption:
  decisive_evidence:
  successful_intervention:
  why_it_worked:
  generalized_rule:
  applies_when:
  does_not_apply_when:
  risk_if_overgeneralized:
  privacy_redactions:
  source_status:
  proposed_destination:
```

## Destination

Choose the topic owner:

- network or infrastructure lesson -> `$torii-build`;
- detector, demand, calibration, or digital-twin lesson -> `$torii-calibrate`;
- execution, controller, debugging, or experiment-design lesson -> `$torii-simulate`;
- result interpretation, reporting, or release lesson -> `$torii-report`.

Prefer updating an existing reference. Add a new reference only for a distinct repeated workflow.

## Privacy and Source Rules

- Remove local paths, private project names, collaborator names, unpublished methods, private datasets, and raw transcripts.
- Keep exact commands only when generic and reusable.
- Preserve public source links when available.
- Mark anecdotal workarounds as heuristics rather than simulator facts.
- Do not claim endorsement by Eclipse SUMO, the Eclipse Foundation, DLR, OpenAI, or another project.

Ask before persisting a user-derived lesson to the public repository. If the user does not approve persistence, keep it in the current conversation only.
