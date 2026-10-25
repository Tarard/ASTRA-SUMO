"""Conditional per-candidate Calibrate bounds and SOL, reported only.

Each bound is recomputed from the profiled run's own quantities and carries a conditions block. No rule consumes a
bound in M1-M5: a bound never refuses a hypothesis, rejects a measured candidate or stops a loop.
"""

from __future__ import annotations

USE = "reported only; no rule consumes a bound in M1-M5"
INVARIANTS_CONDITION = "INV-* on the candidate run"


def _condition(text: str, verified: bool, evidence: str) -> dict:
    return {"condition": text, "verified": bool(verified), "evidence": evidence}


def _invariants_condition(checks: list[dict]) -> dict:
    """INV-PASS and every other invariant on the run: a bound is never verified on a run that fails one."""
    failed = [check["id"] for check in checks if not check["pass"]]
    names = [check["id"] for check in checks]
    evidence = f"failed: {', '.join(failed)}" if failed else f"{len(names)}/{len(names)} pass: {', '.join(names)}"
    return _condition(INVARIANTS_CONDITION, "INV-PASS" in names and not failed, evidence)


def request_floor_b1(balance_rows: list[dict], *, demand_match: dict[str, int], invariants: list[dict],
                     all182_tae: int) -> dict:
    """B1: TAE_init52 + TAE_main130 >= sum_f max(0, E_init,f + E_main,f - N_f).

    ``demand_match`` counts the demand vehicles, the intention rows and the vehicles found in only one of them; N_f
    counts demand vehicles by their intended field, so it is the demand's own total only when both sets are equal.
    """
    per_field = {row["field"]: row["deficit"] for row in balance_rows if row["deficit"] > 0}
    value = sum(per_field.values())
    unmatched = demand_match["demand_without_intention"] + demand_match["intention_without_demand"]
    conditions = [
        _condition("per-field request totals N_f read from the candidate's demand", unmatched == 0,
                   f"N = {sum(row['requests'] for row in balance_rows)} demand vehicles by intended field; demand "
                   f"{demand_match['demand']}, intentions {demand_match['intentions']}, {unmatched} in only one"),
        _invariants_condition(invariants),
    ]
    return {"label": "conditional", "statistic": "TAE_init52 + TAE_main130", "value": value, "per_field": per_field,
            "conditions": conditions, "verified_on_parent": all(c["verified"] for c in conditions),
            "self_check": {"all182_tae": all182_tae, "satisfied": all182_tae >= value}, "use": USE}


def _supply_shortfall(main_bins: list[tuple[float, int]], arrivals: list[float], init_count: int) -> int:
    best, cumulative = 0, 0
    for end_s, target in sorted(main_bins):
        cumulative += target
        reachable = sum(a < end_s for a in arrivals) - init_count
        best = max(best, cumulative - reachable)
    return best


def main_supply_b2(balance_rows: list[dict], main_bins: dict[str, list[tuple[float, int]]],
                   arrivals_by_field: dict[str, list[float]], *, intended_vehicles: int, e1_init: dict[str, int],
                   ff_violations: int, passages: int, invariants: list[dict], main_tae: int) -> dict:
    """B2: LB_main,f = max over main bins b of (E_f(<=b) - (U_f(b) - M_init,f))+, with U_f(b) = #{a_v < end of b}.

    ``e1_init`` holds the run's E1-measured init counts per detector, which the passage-derived M_init must equal.
    """
    measured = {row["detector_id"]: _supply_shortfall(main_bins[row["detector_id"]],
                                                       arrivals_by_field[row["detector_id"]], row["m_init"])
                for row in balance_rows}
    targets = {row["detector_id"]: _supply_shortfall(main_bins[row["detector_id"]],
                                                      arrivals_by_field[row["detector_id"]], row["e_init"])
               for row in balance_rows}
    alias = {row["detector_id"]: row["field"] for row in balance_rows}
    deficit = {row["detector_id"]: row["deficit"] for row in balance_rows}
    computed = sum(map(len, arrivals_by_field.values()))
    init_equal = sum(row["m_init"] == e1_init.get(row["detector_id"]) for row in balance_rows)
    conditions = [
        _condition("a_v recomputed from the candidate's net and demand", computed == intended_vehicles,
                   f"a_v computed from this profile's network and demand for {computed}/{intended_vehicles} "
                   "intended vehicles"),
        _condition("a valid passage-time lower bound: no passage earlier than a_v", ff_violations == 0,
                   f"{ff_violations}/{passages} passages earlier than a_v"),
        _condition("the candidate's own measured init counts", init_equal == len(balance_rows),
                   f"M_init from this run's passages {sum(row['m_init'] for row in balance_rows)} equals this run's "
                   f"E1 init counts on {init_equal}/{len(balance_rows)} fields"),
        _invariants_condition(invariants),
    ]
    exceed = {alias[f]: [value, deficit[f]] for f, value in sorted(targets.items()) if value > max(deficit[f], 0)}
    return {"label": "conditional", "statistic": "TAE_main130", "value": sum(measured.values()),
            "per_field": {alias[f]: value for f, value in sorted(measured.items()) if value},
            "at_init_targets": {"value": sum(targets.values()),
                                "per_field": {alias[f]: value for f, value in sorted(targets.items()) if value},
                                "assumption": "counterfactual: init counts equal the init targets instead of "
                                              "the run's measured init counts"},
            "conditions": conditions, "verified_on_parent": all(c["verified"] for c in conditions),
            "self_check": {"main_tae": main_tae, "satisfied": main_tae >= sum(measured.values())},
            "note": "cumulative-bin bound; per field it can exceed the request deficit"
                    + "".join(f" ({name}: {value} at init targets vs deficit {d})"
                              for name, (value, d) in exceed.items()),
            "use": USE}


def speed_of_light(*, statistic: str, parent: int, candidate: int, bound: dict) -> dict:
    """SOL = (parent - candidate) / (parent - bound), only when the bound's conditions hold on this run."""
    headroom = parent - bound["value"]
    applies = bound["verified_on_parent"] and headroom > 0
    return {"statistic": statistic, "parent": parent, "candidate": candidate, "bound": bound["value"],
            "value": round((parent - candidate) / headroom, 6) if applies else None, "use": USE}
