# Select a scenario from the task and sources

The host model reads Torii's catalog, understands the request, and records one
selection. Torii checks that selection and can call its registered function.
It does not call a new model API or classify the selected request again with
keywords.

```powershell
torii workflows --json
torii workflows --scenario hamburg_network --json
```

[workflow_catalog.py](../plugins/torii-sumo/src/torii_sumo/core/workflow_catalog.py)
is the only executable mapping. The command reads its current function
signatures, required arguments, and skill references. Do not maintain a second
list of callable mappings in prompts or documentation.

As checked on 2026-09-09, the catalog has 25 entries: 19 callable entries and
six guidance entries. They are not 25 complete workflows.

| Kind | Meaning |
|---|---|
| `workflow` | A registered operation that coordinates its declared work and checks |
| `check` | A bounded inspection or comparison, not completion of the surrounding task |
| `stage` | One part of a larger process, such as fitting demand without replaying it |
| `guidance` | A reference for host-model reasoning or intake, with no callable function |

## Make the choice

Match the requested outcome, current artifacts, primary source, target year,
and required inputs. Read the selected skill reference before execution.
The host model writes the selection. Users do not need to know function names
or prepare this routing file themselves.

- Road construction changes a network. Demand fitting uses an already fixed network.
- Experiment planning produces a plan. Simulation execution requires a request to run and its configuration.
- Reviewing existing results uses those outputs. It does not silently start another simulation.
- Reconstructing a real construction drawing preserves its roads and date. A synthetic scene cannot substitute for it.

The user and the source policy determine the target year. A reviewed July 2022
drawing remains a 2022 design target. An illustrative 2013 test or a newer map
does not change it. Source review and any partial coverage remain explicit in
the construction request and the resulting evidence.

Use exactly these selection fields:

```json
{
  "user_request": "Rebuild the Hamburg junction from my reviewed July 2022 construction drawing. Keep that design year.",
  "scenario_id": "hamburg_network",
  "reason": "The drawing defines a real road reconstruction. Demand fitting and a synthetic scene were not requested.",
  "arguments": {
    "request_file": "C:/REPLACE_WITH_PROJECT/requests/hamburg-2022.json",
    "output_dir": "C:/REPLACE_WITH_PROJECT/outputs/hamburg-plan-new"
  }
}
```

The [selection example](../examples/05_hamburg_topology/workflow-selection.example.json)
contains placeholders. It is not a supplied drawing, a real network, or a
ready-to-run reconstruction. Replace its arguments with an actual reviewed
[construction request](../examples/05_hamburg_topology/construction-plan.md)
and a new output directory. The example does not supply or verify a 2022 PDF.

## Check or execute

```powershell
torii workflow selected selection.json --json
torii workflow selected selection.json --execute --json
```

The first command checks argument requirements, types, and declared input-file
presence. `ready_not_executed` does not verify nested request contents, source
hashes, drawing interpretation, or finished work. The selected function performs
its own checks when invoked with `--execute`.

Missing required values or input files return `needs_input`. Unknown IDs and
extra arguments return `blocked`, with no fallback to another workflow.
Guidance returns `guidance_only` and `executed=false`, even with `--execute`.
The host then reads the reference and continues the requested reasoning.

An executed function returns its result under `workflow_result`. Preserve its
decision, unresolved items, and failed stages. `executed=true` states that the
call ran; it does not promote `review_required` or `blocked` to success.

## Compatibility entry

The legacy `torii_auto_workflow` tool and Python `run_auto_workflow` accept the
same object through `workflow_selection`. It is dispatched before legacy
detection. `inspect-only` and `ask-first` do not execute it. Its original
request must match the outer `user_request`, and its function arguments belong
inside `workflow_selection.arguments`.

Calls without a selection retain the older regular-expression routing for
compatibility. That route is separate from the host-model selection described
here. The existing `torii workflow <tool> <request.json> --json` command also
remains available; `--execute` belongs only to `workflow selected`.
