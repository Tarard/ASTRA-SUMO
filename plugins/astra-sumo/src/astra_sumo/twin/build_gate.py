"""Build gate (M3): G0 assembly, the lexicographic V with real materiality, the L2/L3 guards and the screening label.

Pure functions over rows, invariant results and routeability reports that the caller has already read. Labels sit in
``screening``; they carry no promotion authority.

V = (n_TOP, n_CTL, n_GEO, n_ATTR, E) over the frozen scored set, where n_X counts mismatched facts of class X and E
sums their normalized numeric excess. Under G0 no matched scored fact may become unmatched, so the counts can only fall.
A candidate is ``improved`` when its counts are lexicographically smaller, or the counts are equal and E falls by at
least ``min_excess_decrease``; in both cases no numeric component of any scored fact may worsen by more than
``max_worsening_tol_fraction`` of its scale (tol_eff for offsets), and no still-mismatched categorical fact may grow its
reported magnitude (|obs - exp|, FP + FN, symmetric difference). A smaller E decrease is ``not_material``.
"""

from __future__ import annotations

TARGET_CLASSES = ("TOP", "CTL", "GEO", "ATTR")
ORDER = ("G0", "n_TOP", "n_CTL", "n_GEO", "n_ATTR", "sum_numeric_excess")
MATERIALITY = {"min_excess_decrease": 0.05, "max_worsening_tol_fraction": 0.1}
V_RULE = ("V = (n_TOP, n_CTL, n_GEO, n_ATTR, E): mismatched scored facts per class, then E = sum of normalized numeric "
          "excess. improved iff counts lexicographically smaller, or counts equal and E decreases by >= "
          "min_excess_decrease; and no numeric component of a scored fact worsens by more than "
          "max_worsening_tol_fraction x its scale, and no mismatched categorical fact grows its magnitude. Smaller E "
          "decreases are not_material; ties are rejected.")
EXCESS_RULE = {
    "stop_line_m": "(|longitudinal| - tol_eff)+ / tol_eff (distance when only tolerance_m is declared)",
    "max_lateral_m": "(max sampled lateral offset - tol_eff)+ / tol_eff",
    "coverage_shortfall": "(minimum_coverage - coverage within tol_eff)+ / max(1 - minimum_coverage, 1 / samples)",
    "mean_lateral_m": "(mean lateral offset - maximum_mean)+ / tol_eff, only when a maximum mean is declared",
    "width_error_m": "(|width - expected| - tol_eff)+ / tol_eff",
    "speed_error_m_s": "(|speed - expected| - tol_eff)+ / tol_eff",
    "iou_shortfall": "(minimum_iou - IoU)+ / (1 - minimum_iou)",
}
# Identity-free routeability counts: a request in these states has no per-request record in the audit.
UNIDENTIFIED_EVENTS = ("collisions", "teleports")
IDENTITY_REASON = ("per-request collision and teleport identities are not recorded by run_routeability_audit "
                   "(aggregate counts only), so the subset rule cannot be established; a human must review it, it "
                   "never passes")
# routeability_status values of a failed audit that its per-request rows (or the unidentified event counts above)
# explain; any other failure (output-mismatch, input-artifact-changed, invalid-output, sumo-run-failed, ...) leaves
# the failing requests unknown.
EXPLAINED_FAILURES = frozenset({"incomplete", "vehicle-route-completion-failed", "discard-failure",
                                "teleport-failure", "collision-failure", "unverified_endpoints"})
# Screenings under which a candidate may become the parent of the next step.
ACCEPTED_SCREENINGS = ("improved", "improved_l2_not_run")
L2_REASON = ("L2 deterministic movement probes (run_candidate_movement_probes / run_all_turn_movement_smoke) are not "
             "implemented in M3; no candidate screens as plain improved until they run")


def v_vector(rows: dict[str, dict]) -> list:
    """V of one net over frozen scored rows ``{"class", "status", "excess"}``; E is rounded to 9 decimals."""
    counts = [sum(row["class"] == cls and row["status"] == "mismatched" for row in rows.values())
              for cls in TARGET_CLASSES]
    return [*counts, round(sum(row["excess"] for row in rows.values() if row["excess"] is not None), 9)]


def worsened_components(parent: dict[str, dict], candidate: dict[str, dict], fraction: float) -> list[dict]:
    """Numeric components that worsen by more than ``fraction`` x their scale between parent and candidate rows, and
    mismatched categorical facts whose reported magnitude grows (any increase; its scale is one unit)."""
    result = []
    for fact, before in parent.items():
        after = candidate.get(fact)
        if after is None:
            continue
        for name, part in before.get("components", {}).items():
            other = after.get("components", {}).get(name)
            if part["value"] is None or other is None or other["value"] is None:
                continue
            if other["value"] - part["value"] > fraction * part["scale"] + 1e-12:
                result.append({"fact": fact, "component": name, "parent": part["value"], "candidate": other["value"],
                               "limit": fraction * part["scale"]})
        magnitude = (before.get("magnitude"), after.get("magnitude"))
        if (after.get("status") == "mismatched" and None not in magnitude and not before.get("components")
                and magnitude[1] > magnitude[0]):
            result.append({"fact": fact, "component": "magnitude", "parent": magnitude[0], "candidate": magnitude[1],
                           "limit": 0})
    return result


def compare_v(parent: dict[str, dict], candidate: dict[str, dict], materiality: dict | None = None) -> dict:
    """The V verdict of a candidate against its parent on the same frozen scored rows."""
    materiality = {**MATERIALITY, **(materiality or {})}
    v_parent, v_candidate = v_vector(parent), v_vector(candidate)
    worse = worsened_components(parent, candidate, materiality["max_worsening_tol_fraction"])
    decrease = round(v_parent[-1] - v_candidate[-1], 9)
    tier = next((ORDER[index + 1] for index, (a, b) in enumerate(zip(v_candidate[:4], v_parent[:4])) if a != b),
                "sum_numeric_excess")
    if v_candidate[:4] < v_parent[:4]:
        verdict = "improved"
    elif v_candidate[:4] > v_parent[:4]:
        verdict = "not_improved"
    elif decrease >= materiality["min_excess_decrease"]:
        verdict = "improved"
    elif decrease > 0:
        verdict = "not_material"
    else:
        verdict = "tie" if decrease == 0 else "not_improved"
    if verdict == "improved" and worse:
        verdict = "worsened"
    reason = {"improved": f"{tier} decreased",
              "not_improved": f"{tier} did not decrease",
              "not_material": f"E decreased by {decrease:g} < {materiality['min_excess_decrease']:g} with equal counts",
              "tie": "V unchanged; ties are rejected",
              "worsened": f"{len(worse)} component(s) worsened (numeric by more than "
                          f"{materiality['max_worsening_tol_fraction']:g} x scale, categorical magnitude at all)"
              }[verdict]
    return {"verdict": verdict, "tier": tier, "cand": v_candidate, "parent": v_parent, "excess_decrease": decrease,
            "worsened": worse, "materiality": materiality, "reason": reason, "rule": V_RULE}


def g0(checks: list[dict]) -> dict:
    """G0 over invariant results: any failure rejects, with no statistics."""
    failed = next((check for check in checks if not check["pass"]), None)
    return {"pass": failed is None, "checks": checks,
            "first_fail": None if failed is None else f"{failed['id']}: {', '.join(failed['witness_ids'][:3])}"}


def g0_regressed(regressed: list[str]) -> dict:
    """``regressed_facts`` of the frozen scored set: a matched fact that stops matching rejects."""
    return {"id": "G0-REGRESSED", "pass": not regressed, "witness_ids": sorted(f"regressed:{f}" for f in regressed),
            "reason": "frozen scored facts matched on the parent must stay matched (compare_calibration_scorecards)"}


def _failing(audit: dict | None) -> tuple[str, set[str] | None, dict, str]:
    """(state, failing request ids, event counts, reason) of one routeability audit.

    A failing request is a completion failure or a missing vehicle in the final attempt, or an endpoint removed by the
    candidate (unroutable). ``state`` is ``ok``, ``all_failed`` (no route generation) or ``unavailable``: a failed
    audit whose ``routeability_status`` its per-request rows and event counts do not explain is unavailable, never a
    clean pass.
    """
    if not audit:
        return "unavailable", None, {}, "not_run"
    final = audit.get("final_attempt") or {}
    events = {key: int((final.get("inspection") or {}).get("summary", {}).get(key) or 0)
              for key in (*UNIDENTIFIED_EVENTS, "running", "waiting", "discarded")}
    if (audit.get("route_generation") or {}).get("status") != "pass":
        if audit.get("reason") == "all_source_endpoints_removed":
            return "all_failed", None, events, "all_source_endpoints_removed"
        return "all_failed", None, events, "route_generation_failed"
    routes = final.get("vehicle_routes") or {}
    if "error" in routes or not isinstance(routes.get("failures"), list):
        return "unavailable", None, events, "vehicle_routes_unreadable"
    failing = {str(row.get("vehicle_id")) for row in routes["failures"]}
    failing |= {str(value) for value in routes.get("missing_vehicle_ids", [])}
    failing |= {str(value) for value in (audit.get("cohort_coverage") or {}).get("removed_endpoint_vehicle_ids", [])}
    status, kind = audit.get("status"), audit.get("routeability_status")
    if status != "pass":
        unidentified = sum(events[key] for key in UNIDENTIFIED_EVENTS)
        if kind not in EXPLAINED_FAILURES:
            return "unavailable", None, events, f"audit {status} ({kind}) is not explained by request identities"
        if not failing and not unidentified:
            return "unavailable", None, events, f"audit {status} ({kind}) names no failing request"
    return "ok", failing, events, "identified"


def _trip_identity(audit: dict | None, key: str) -> str | None:
    return ((audit or {}).get("od_policy") or {}).get(key)


def l3_seed(seed: int, source: dict | None, candidate: dict | None, frozen_trip_sha256: str | None = None,
            check_trips: bool = False) -> dict:
    """The hard rule on one seed: the candidate's failing requests must be a subset of the source's.

    With ``check_trips`` the requests must be the goal's frozen ones for this seed: the source audit generated exactly
    ``frozen_trip_sha256`` and the candidate ran those frozen trips; anything else is identities_unavailable.
    """
    s_state, s_fail, s_events, s_reason = _failing(source)
    c_state, c_fail, c_events, c_reason = _failing(candidate)
    s_unidentified = sum(s_events.get(key, 0) for key in UNIDENTIFIED_EVENTS)
    c_unidentified = sum(c_events.get(key, 0) for key in UNIDENTIFIED_EVENTS)
    trips = {"goal": frozen_trip_sha256, "source": _trip_identity(source, "request_file_sha256"),
             "candidate": _trip_identity(candidate, "frozen_trip_sha256")}
    row = {"seed": seed, "source": {"state": s_state, "failing": sorted(s_fail or []), "events": s_events},
           "candidate": {"state": c_state, "failing": sorted(c_fail or []), "events": c_events},
           "source_dirty": bool(s_fail) or s_unidentified > 0 or s_state == "all_failed",
           **({"trips": trips} if check_trips else {})}

    def verdict(status: str, result: str, reason: str, new: list[str] | None = None) -> dict:
        return {**row, "status": status, "result": result, "new_failing": sorted(new or []), "reason": reason}

    if s_state == "unavailable":
        return verdict("blocked", "identities_unavailable", f"source requests not identified ({s_reason})")
    if s_state == "all_failed":
        if c_state == "all_failed":
            return verdict("blocked", "identities_unavailable", f"source ({s_reason}) and candidate ({c_reason}) "
                           "route generation both failed; no request identity is compared, so it never passes")
        return verdict("blocked", "identities_unavailable",
                       f"source routing failed ({s_reason}); the candidate cannot be compared on frozen requests")
    if c_state == "all_failed":
        return verdict("blocked", "new_failing_requests", f"candidate routing failed ({c_reason})", ["*"])
    if c_state == "unavailable":
        return verdict("blocked", "identities_unavailable", f"candidate requests not identified ({c_reason})")
    if check_trips and (frozen_trip_sha256 is None or trips["source"] != frozen_trip_sha256
                        or trips["candidate"] != frozen_trip_sha256):
        return verdict("blocked", "identities_unavailable",
                       "the requests are not the goal's frozen trips for this seed (generated from the goal source "
                       "at goal-create); a parent net with other trips cannot carry the subset rule")
    new = sorted(c_fail - s_fail)
    if c_unidentified and not s_fail and not s_unidentified:
        return verdict("blocked", "new_failing_requests",
                       "the candidate collides or teleports where every source request arrived cleanly", new or ["*"])
    if new and not s_unidentified:
        return verdict("blocked", "new_failing_requests", f"{len(new)} request(s) fail only on the candidate", new)
    if new or c_unidentified:
        return verdict("blocked", "identities_unavailable", IDENTITY_REASON, new)
    return verdict("pass", "subset_holds", "the candidate's failing requests are a subset of the source's")


def routeability_pairs(report: dict) -> list[tuple[int, dict | None, dict | None]]:
    """(seed, source audit, candidate audit) from a verify-edit report in either seed layout."""
    if "routeability_by_seed" in report:
        return [(row["seed"], row.get("source_routeability"), row.get("candidate_routeability"))
                for row in report["routeability_by_seed"]]
    if "source_routeability" in report:
        return [(int(report["source_routeability"].get("seed", 42)), report["source_routeability"],
                 report.get("candidate_routeability"))]
    return []


def l3_rule(pairs: list[tuple[int, dict | None, dict | None]], declared_seeds: list[int],
            frozen_trips: dict[int, str | None] | None = None) -> dict:
    """L3 over every seed: a new failing request rejects; unidentifiable or missing seeds never pass.

    ``frozen_trips`` maps each goal seed to the sha256 of the trips goal-create generated from the goal source; when
    given, every seed's requests must be those trips.
    """
    check = frozen_trips is not None
    rows = [l3_seed(seed, source, candidate, (frozen_trips or {}).get(seed), check)
            for seed, source, candidate in pairs]
    seeds = [row["seed"] for row in rows]
    hard = [row for row in rows if row["result"] == "new_failing_requests"]
    unverified = [row for row in rows if row["result"] == "identities_unavailable"]
    dirty = [row["seed"] for row in rows if row["source_dirty"]]
    if not rows:
        result, reason = "not_run", "no routeability seed was run; the guard never passes unrun"
    elif hard:
        result = "new_failing_requests"
        reason = "; ".join(f"seed {row['seed']}: {row['reason']}" for row in hard[:3])
    elif unverified:
        result = "identities_unavailable"
        reason = "; ".join(f"seed {row['seed']}: {row['reason']}" for row in unverified[:3])
    elif sorted(seeds) != sorted(declared_seeds):
        result, reason = "seeds_differ", f"seeds {sorted(seeds)} differ from the goal's {sorted(declared_seeds)}"
    else:
        result, reason = "subset_holds", f"on all {len(rows)} seeds the candidate's failing requests are a subset"
    return {"status": "pass" if result == "subset_holds" else "blocked", "result": result, "reason": reason,
            "seeds": rows, "declared_seeds": list(declared_seeds), "dirty_source_seeds": dirty,
            "source_state": (f"dirty on seeds {dirty}: not promotable without a human" if dirty else "clean")
            if rows else "not_run",
            "frozen_trips": "checked against the goal's per-seed trips" if check else "not checked",
            "reported_only": ["travel time", "arrival time"]}


def l2_not_run() -> dict:
    """L2 as the M3 profiler records it: never run, so it never lets a candidate screen as plain improved."""
    return {"status": "not_run", "result": "not_implemented", "reason": L2_REASON}


SCREENING = {"new_failing_requests": "dynamic_hard", "identities_unavailable": "dynamic_unverified",
             "not_run": "dynamic_not_run", "seeds_differ": "dynamic_unverified"}


def screening(g0_result: dict, v_result: dict, l3_result: dict, l2_result: dict | None = None) -> tuple[str, str | None]:
    """First failing tier in order G0, V, L3, then L2; ``improved`` only when all four hold, and
    ``improved_l2_not_run`` when L2 never ran (the M3 case)."""
    if not g0_result["pass"]:
        return "g0_rejected", g0_result["first_fail"]
    if v_result["verdict"] != "improved":
        return f"v_{v_result['verdict']}", v_result["reason"]
    if l3_result["status"] != "pass":
        return SCREENING[l3_result["result"]], l3_result["reason"]
    l2_result = l2_result or l2_not_run()
    if l2_result["status"] != "pass":
        return "improved_l2_not_run", l2_result["reason"]
    return "improved", None
