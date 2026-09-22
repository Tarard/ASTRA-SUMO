# Experiment Problem Solving

Use this reference to turn a confusing simulation or controller experiment result into a precise plan or next diagnostic step. The goal is not a larger run. The goal is one bounded question that reduces uncertainty.

```text
observation -> uncertainty -> fixed variables -> smallest contrast -> feedback -> next prompt
```

Simulators, scripts, controllers, harnesses, and plugins are actuators. The experiment should decide what uncertainty they are allowed to reduce.

## Diagnostic Frame

Every nontrivial experiment diagnosis should identify:

- `phenomenon`: what was observed, without interpretation.
- `desired_claim`: what the user wants to be able to say if the evidence supports it.
- `blocking_uncertainty`: the one unknown that currently prevents that claim.
- `fixed_variables`: scenario, inputs, seed, horizon, activation time, controller schedule, constraints, and output schema that must stay paired.
- `allowed_change`: the smallest mechanism, code path, or parameter that may change.
- `feedback_signal`: the metric, log, trace, or diff that will decide whether the idea survives.
- `claim_boundary`: what must not be claimed from this run.

If these fields cannot be filled, collect observation before execution.

## Mental Moves

1. Separate observation from explanation.
2. Compare against the strongest previous result under the same scenario before inventing new logic.
3. Treat negative results as information about a failure mode, not as proof the research direction is dead.
4. Prefer paired action-log comparison over a larger performance matrix when timing or trigger logic is unclear.
5. Keep actuator usefulness separate from trigger correctness.
6. Check completion, backlog, teleports, warnings, and controller action traces before ranking metrics.
7. End each diagnostic pass with a stopping condition.

## Common Patterns

### When Performance Gets Worse

```text
We observed [new controller] is worse than [old controller].
Do not tune broadly yet.
First pair them under the same [inputs/seed/horizon/activation].
Compare action timing by [time, controller unit, state, target, reason].
Determine whether the failure is over-action, under-action, wrong scope, wrong timing, or metric invalidity.
Stop if completion/backlog makes performance ranking invalid.
```

### When an Old Diagnostic Worked

```text
The old diagnostic fixture showed [action direction] can help.
Now isolate what was fixture-only or oracle-like.
Keep the actuator fixed and test only the trigger that decides when/where to act.
Do not claim deployability unless the trigger uses observable runtime information.
```

### When the Mechanism Is Unclear

```text
We do not know whether [candidate mechanism] is the real reason.
Design the smallest contrast where only [one information structure] changes.
Use logs to prove the controller actually used that information.
Report what remains unproven.
```

### When the Run Is Incomplete

```text
Do not rank controllers from this output yet.
First decide whether incomplete vehicles, backlog, teleports, or warnings changed the denominator.
Extend or repair the run only enough to make the comparison interpretable.
```

## Reusable Boundaries

- Event-impact signals define activation or localization scope unless pairwise coupling is explicitly computed.
- Actuator direction is separate from trigger logic. A useful action can still be driven by a wrong trigger.
- Trigger density means action frequency or action budget, not traffic density.
- Diagnostic or oracle fixtures are lower-claim evidence than observable runtime controllers.
- Runtime actions must obey the simulator's existing legality and transition constraints.
- Fine-grained mechanism claims require proof that logged coordinates, entities, and mappings are real.

## Decision Ladder

1. If completion is invalid, repair completion before interpreting performance.
2. If the comparison is unpaired, request a paired rerun or mark the claim non-identical.
3. If a controller did not log its intended action, add logging or audit the execution seam.
4. If old and new methods differ in many ways, compare action logs under the same scenario.
5. If the actuator worked but the runtime controller failed, isolate the trigger.
6. If the trigger is too dense, inspect action density before proposing new architecture.
7. If the mechanism survives a paired contrast, then broaden seeds or perturbations.

## Generic Control Chain

```text
event or observer -> affected scope -> candidate request -> legality constraints -> priority/filter gate -> legal action -> action trace -> paired metric feedback
```

When an old diagnostic fixture beats a new runtime trigger, the next experiment is usually a paired action-timing comparison under the same inputs and seed, not a larger matrix.

## Output Shape

```text
phenomenon:
desired_claim:
blocking_uncertainty:
fixed_variables:
allowed_change:
feedback_signal:
smallest_next_step:
stop_condition:
claim_boundary:
```
