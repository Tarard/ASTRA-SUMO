# Independent road-design rule study

This first set translates three reviewed rule fragments into ordinary Python
functions. The modules run independently of `build-network`. They do not
construct lanes or certify an existing road.

| Module | Responsibility |
|---|---|
| [design_rules.py](../../plugins/torii-sumo/src/torii_sumo/road_network/design_rules.py) | Version and context checks, followed by three explicit width checks |
| [engineering_plan.py](../../plugins/torii-sumo/src/torii_sumo/road_network/adapters/engineering_plan.py) | Manual PDF dimension records, source hashes, dates, units, and measurement basis |
| [design_review.py](../../plugins/torii-sumo/src/torii_sumo/road_network/design_review.py) | Combine checks and observations without losing either source identity |

No rule language, automatic standard selector, or model-training dependency is
introduced. Each rule function can be tested and extended on its own.

## Implemented fragments

| Function | Reviewed source | Scope and interpretation |
|---|---|---|
| `check_cycle_lane_width` | ReStra 2017, revision 2026-03-23, section 6.1.7.4, printed p.24 / PDF p.37 | Confirmed `radfahrstreifen` in Hamburg. Regular width 2.75 m and minimum width 2.25 m both include markings. Meeting the minimum alone does not meet the regular value. |
| `check_two_lane_carriageway_width` | ReStra section 6.1.1.2, printed p.16 / PDF p.29; RASt table 7, printed p.68 / PDF p.70 | A two-way, two-lane urban main road with infrequent, reduced-speed HGV meetings. The 5.90 m value concerns the whole carriageway and is a specified design value, not a universal minimum. |
| `check_left_turn_lane_width` | RASt 2006, official English translation 2012, section 6.3.3 table 46 and footnotes, printed p.106 / PDF p.108; section 6.3.1, printed p.104 / PDF p.106 | Priority-sign-controlled junctions only in this first implementation. Normally at least 3.00 m; restricted space can permit 2.75 m. Scheduled buses require at least 3.00 m. The turn lane may be at most 0.25 m narrower than the through lane. |

The carriageway function excludes scheduled bus service and advisory cycle
lanes in this first implementation because table 7 provides other rows for
those conditions. This is an implementation scope limit, not a prohibition in
the standard. Unknown conditions remain unresolved. A larger width does not
authorize automatic narrowing.

Table 46 is not applied to signal-controlled or right-before-left junctions.
The local applicability of the cited RASt edition must be checked explicitly.
The functions cover neither the complete standards nor project approval.

## Direct use

```python
from torii_sumo.road_network.design_rules import RESTRA, check_cycle_lane_width

result = check_cycle_lane_width(
    2.75,
    context={
        "purpose": "design_review",
        "jurisdiction": "DE-HH",
        "assessment_date": "2026-09-08",
        "standard_id": RESTRA,
    },
    facility_type="radfahrstreifen",
    width_basis="including_markings",
)
```

The default zero uncertainty applies to an exact, caller-supplied design
parameter. Supply the uncertainty when comparing a measurement. An interval
that crosses a threshold remains unresolved.

`purpose="existing_road_reconstruction"` does not turn a design value into an
observed dimension. A source revision newer than the assessment date is not
applied. Missing conditions return `review_required`; known scope differences
return `not_applicable`. A `pass` checks only the cited dimensions.

## Engineering-plan observations

`read_engineering_plan_observations(request_file, target_date=None)` reads a
`torii.engineering-plan-observations-request/v1` file. It requires a source PDF
path and SHA-256, document title, date or null, and document kind. Each observation
requires an ID, one-based PDF page, location, property, value, and unit.

Units are `m`, `cm`, or `mm`. Width bases are `clear_width`, `including_markings`,
`whole_carriageway`, `standard_lane_width`, or `unknown`. Select a specific
basis only after checking dimension endpoints and the drawing legend.
No fixed marking width is subtracted automatically.

Original values and units remain in `raw`. A design, evaluation, or even an
as-built document does not automatically establish current field conditions.
PDF page numbers are checked as positive integers; actual page bounds and
contents require separate reading. These limits remain explicit in the output.

## Reproducible combined review

```powershell
python plugins/torii-sumo/scripts/review_road_design_rules.py request.json new-output-dir
```

The request schema is `torii.road-design-review-request/v1`. Supply
`standard_files` keyed by the exact source IDs, optional `plan_requests` with
unique IDs, and `checks`. Each check names `cycle_lane_width`,
`two_lane_carriageway_width`, or `left_turn_lane_width` and its `parameters`.
Every referenced file requires a path and SHA-256. Paths may be relative to
the request file.

A check may link `observation: {"plan_id": "...", "observation_id": "..."}`.
The linked width and basis come from the observation and cannot be overridden.
Uncertainty defaults to unknown for linked measurements. Both plan and standard
identities remain attached to the result. Shared files cannot change identity
between reads.

The command writes `design-review.json` into a new directory. Exit code 0 means
the review ran. Its overall decision remains `review_required`. Independent
road and image validation is still required before enabling automatic use in
network construction.
