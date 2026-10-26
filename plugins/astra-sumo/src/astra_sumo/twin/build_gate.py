"""Build gate (M3): G0 assembly, the lexicographic V with real materiality, the L2/L3 guards and the screening label.

Pure functions over rows, invariant results, movement-guard records and routeability reports that the caller has
already read. Labels sit in ``screening``; they carry no promotion authority.

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
# Screenings under which a candidate may become the parent of the next step: only a candidate whose G0, V, L2 and L3
# all hold. An unrun, unverified or failed L2 or L3 is never accepted.
ACCEPTED_SCREENINGS = ("improved",)
L2_RULE = ("L2: on every touched junction, no probe or smoke failure on the candidate that the parent does not have "
           "(after the declared lane remap); a failure is an expected movement (frozen movement facts) that is not "
           "driven or probed or does not arrive, a driven movement that does not arrive, a failed SUMO run, or a "
           "teleport or collision in a run; an expected movement on the op's targets that fails on both nets is "
           "unprobed and L2 stays unverified; a probe or smoke run that is missing or did not execute cleanly on "
           "either net (a failed, nonzero or timed-out command, or missing or unreadable outputs) is no evidence, "
           "so nothing is compared there and L2 stays unverified")
L2_NOT_RUN = "no L2 movement-guard record was supplied; L2 never passes unrun"


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


MOVEMENT_GUARD_SCHEMA = "astra.twin-movement-guard/v1"
NETWORK_RUN = "*"
# Failure tokens of a run that is no evidence: missing, or not executed cleanly (``run_ok`` false).
UNCLEAN_RUN = frozenset({"run:missing", "run:failed"})


def l2_not_run(reason: str = L2_NOT_RUN) -> dict:
    """L2 without a movement-guard record: never passes."""
    return {"status": "not_run", "result": "not_run", "reason": reason, "rule": L2_RULE, "junctions": [],
            "new_failures": []}


def _pair(pair) -> str:
    return f"movement:{pair[0]}>{pair[1]}"


def l2_failing(run: dict | None, expected: list) -> set[str]:
    """Failure tokens of one smoke run (or one probe run, ``failing``) of a net at one junction.

    ``movement:<from lane>><to lane>`` for an expected movement that is not driven (absent, or not permitted for
    the probe vehicle) or does not arrive, and for a driven movement that does not arrive; ``run:failed`` when SUMO
    did not run cleanly (``run_ok`` false: a probe or smoke command failed, exited nonzero or timed out, or its outputs
    are missing or unreadable; then nothing counts as arrived); ``run:teleports`` and ``run:collisions`` when the run
    had any (the smoke records no per-vehicle identity for them); ``junction:absent`` when the net lacks a junction
    at which movements are expected (a junction an op merged away expects none: its movements belong to the new one).
    """
    expected_pairs = {tuple(pair) for pair in expected}
    if run is None:
        return {"run:missing"}
    if run.get("status") == "absent":
        return ({"junction:absent"} if expected_pairs else set()) | {_pair(pair) for pair in expected_pairs}
    tokens = set()
    if "failing" in run:
        # A probe run: every probe that did not pass, and every expected movement it did not probe or lists as not
        # tested (absent from the manifest's declared movements, or on a removed lane).
        tested = {tuple(pair) for pair in run.get("tested", [])}
        tokens |= {_pair(pair) for pair in run["failing"]}
        tokens |= {_pair(pair) for pair in expected_pairs - tested}
        tokens |= {_pair(pair) for pair in expected_pairs & {tuple(pair) for pair in run.get("not_tested", [])}}
        if not run.get("run_ok"):
            tokens.add("run:failed")
        return tokens
    driven = {tuple(pair) for pair in run.get("driven", [])}
    arrived = {tuple(pair) for pair in run.get("arrived", [])} if run.get("run_ok") else set()
    if not run.get("run_ok"):
        tokens.add("run:failed")
    tokens |= {_pair(pair) for pair in driven - arrived}
    tokens |= {_pair(pair) for pair in expected_pairs - driven}
    tokens |= {f"run:{key}" for key in ("teleports", "collisions") if (run.get("events") or {}).get(key)}
    return tokens


def _remapped_token(token: str, mapping: dict[str, dict[str, str]]) -> str | None:
    """A parent failure token under the declared lane remap ``{edge: {old index: new index}}``; None when it names a
    lane the edit removed."""
    if not token.startswith("movement:"):
        return token
    lanes = []
    for lane in token[len("movement:"):].split(">"):
        edge, _, index = lane.rpartition("_")
        if edge in mapping:
            if index not in mapping[edge]:
                return None
            lane = f"{edge}_{mapping[edge][index]}"
        lanes.append(lane)
    return "movement:" + ">".join(lanes)


def _touches(token: str, junction: str | None, targets: dict) -> bool:
    """Whether a movement token's lanes lie on a target edge of the op, or its junction is a target junction."""
    edges = {lane.rpartition("_")[0] for lane in token[len("movement:"):].split(">")}
    return bool(edges & set(targets.get("edges") or [])) or junction in set(targets.get("junctions") or [])


def l2_rule(record: dict | None, *, parent_sha256: str, candidate_sha256: str, junctions: list[str],
            expected: dict[str, dict[str, list]], lane_mapping: dict[str, dict[str, str]] | None = None,
            verify_edit_sha256: str | None = None, method: str | None = None,
            targets: dict[str, list[str]] | None = None) -> dict:
    """L2 over a movement-guard record: pass only when it binds the parent and candidate nets (and, when given, the
    verify-edit report and the ``method`` the goal requires), covers exactly the touched ``junctions`` with the
    ``expected`` movements the profile derives from the frozen facts (``{"parent": {junction: pairs}, "candidate":
    {...}}``), every run on both nets executed cleanly, no junction shows a failure on the candidate that the parent
    (remapped) does not have, and no expected movement on the op's ``targets`` (its edges and junctions) fails on both
    nets: such a movement is never compared, so a candidate that breaks it is invisible to the differential rule
    (``unprobed``, unverified). A run that is missing or did not execute cleanly on EITHER net (``UNCLEAN_RUN``) is
    no evidence: nothing is compared at its junction (its tokens count neither as new nor as unprobed), and L2 is
    unverified unless a cleanly run junction shows a new failure, so identical execution failures on the two nets
    never cancel out into a pass, whatever movements the frozen facts expect."""
    if record is None:
        return l2_not_run()
    problems = []
    if record.get("schema") != MOVEMENT_GUARD_SCHEMA:
        problems.append("schema")
    if method is not None and record.get("method") != method:
        problems.append(f"method {record.get('method')} instead of {method}")
    if (record.get("parent") or {}).get("sha256") != parent_sha256:
        problems.append("parent net sha256")
    if (record.get("candidate") or {}).get("sha256") != candidate_sha256:
        problems.append("candidate net sha256")
    if verify_edit_sha256 is not None and (record.get("verify_edit") or {}).get("sha256") != verify_edit_sha256:
        problems.append("verify-edit report sha256")
    if record.get("junctions") != junctions:
        problems.append("touched junctions")
    if record.get("expected") != expected:
        problems.append("expected movements")
    if (record.get("lane_mapping") or {}) != (lane_mapping or {}):
        problems.append("lane mapping")
    if record.get("nets_unchanged") is not True:
        problems.append("nets changed during the probes")
    base = {"rule": L2_RULE, "method": record.get("method"), "junctions": [], "new_failures": []}
    if problems:
        return {**base, "status": "blocked", "result": "unverified",
                "reason": f"the movement-guard record does not bind this candidate ({', '.join(problems)})"}
    if not junctions:
        return {**base, "status": "not_run", "result": "not_run", "reason": "no touched junction to probe"}
    keys = [NETWORK_RUN] if record.get("method") == "movement_probes" else junctions
    runs = record.get("runs") or {}
    junction_of = {_pair(pair): junction for junction, pairs in expected["candidate"].items() for pair in pairs}
    rows, new, unrun, unprobed = [], [], [], []
    for key in keys:
        wanted = {role: (expected[role].get(key, []) if key != NETWORK_RUN else
                         sorted(pair for pairs in expected[role].values() for pair in pairs))
                  for role in ("parent", "candidate")}
        parent_run = (runs.get("parent") or {}).get(key)
        parent = l2_failing(parent_run, wanted["parent"])
        candidate = l2_failing((runs.get("candidate") or {}).get(key), wanted["candidate"])
        mapped = {token for token in (_remapped_token(row, lane_mapping or {}) for row in parent) if token}
        fresh = sorted(candidate - mapped)
        both = sorted(candidate & mapped & {_pair(pair) for pair in wanted["candidate"]})
        unclean = [role for role, tokens in (("parent", parent), ("candidate", candidate)) if UNCLEAN_RUN & tokens]
        rows.append({"junction": key, "parent_failing": sorted(parent), "candidate_failing": sorted(candidate),
                     "new": fresh, "unprobed": both, "unclean_runs": unclean})
        # A run that is missing or did not execute cleanly on either net leaves nothing to compare, as an L3 source
        # that failed to route: its tokens are no evidence and it never passes.
        if unclean:
            unrun.append(f"{key} ({', '.join(unclean)})")
            continue
        new += [f"{key}:{token}" for token in fresh]
        unprobed += [f"{key}:{token}" for token in both
                     if _touches(token, junction_of.get(token, key), targets or {})]
    if new:
        return {**base, "status": "blocked", "result": "new_failures", "junctions": rows, "new_failures": new,
                "reason": f"{len(new)} failure(s) only on the candidate: {', '.join(new[:3])}"}
    if unrun:
        return {**base, "status": "blocked", "result": "unverified", "junctions": rows, "unclean": unrun,
                "reason": f"the probes or smoke did not execute cleanly at {', '.join(unrun[:3])}; nothing is "
                          "compared there"}
    if unprobed:
        return {**base, "status": "blocked", "result": "unverified", "junctions": rows, "unprobed": unprobed,
                "reason": f"{len(unprobed)} expected movement(s) on the op's targets fail on both nets, so L2 compares "
                          f"nothing there: {', '.join(unprobed[:3])}"}
    return {**base, "status": "pass", "result": "no_new_failures", "junctions": rows,
            "reason": f"no new probe or smoke failure on {len(keys)} touched junction(s)"}


SCREENING = {"new_failing_requests": "dynamic_hard", "identities_unavailable": "dynamic_unverified",
             "not_run": "dynamic_not_run", "seeds_differ": "dynamic_unverified"}
SCREENING_L2 = {"new_failures": "l2_new_failures", "unverified": "l2_unverified", "not_run": "l2_not_run"}


def screening(g0_result: dict, v_result: dict, l3_result: dict, l2_result: dict | None = None) -> tuple[str, str | None]:
    """First failing tier in the design's order G0, V, L2 (deterministic probes), L3 (seed-level routeability);
    ``improved`` only when all four hold. A missing L2 result is an unrun L2, which never passes."""
    if not g0_result["pass"]:
        return "g0_rejected", g0_result["first_fail"]
    if v_result["verdict"] != "improved":
        return f"v_{v_result['verdict']}", v_result["reason"]
    l2_result = l2_result or l2_not_run()
    if l2_result["status"] != "pass":
        return SCREENING_L2[l2_result["result"]], l2_result["reason"]
    if l3_result["status"] != "pass":
        return SCREENING[l3_result["result"]], l3_result["reason"]
    return "improved", None
