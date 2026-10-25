"""Compact JSON and text views of a twin profile, each hard-capped at 6,000 characters."""

from __future__ import annotations

import json

from . import ids

CAP_CHARACTERS = 6000
HOTSPOTS = 5
ROWS_PER_HOTSPOT = 6
# Progressively smaller views (hotspots, rows, anchors, clip); the first where both views fit the cap is used. The
# last one also shortens every id and caps every list, which bounds the views well below the cap.
_BUDGETS = ((HOTSPOTS, ROWS_PER_HOTSPOT, 3, False), (HOTSPOTS, 3, 2, False), (3, 2, 1, False), (1, 1, 1, False),
            (0, 0, 0, False), (0, 0, 0, True))
CLIP_ID = 48
CLIP_NOTE = 200
CLIP_ITEMS = 8
# Interpretation limits per hotspot class, shared by every hotspot of that class.
LOC_TEXT = {"entry": ("insertion capacity vs spillback onto the entry edge", "demand timing | network entry"),
            "edge": ("queue along the approach vs spillback from downstream vs hold side effects; which lane or link; "
                     "red vs blocking", "structural hypothesis only")}
ROW_FORMAT = "r y p(lanned) m(easured) ontime/late_in/delayed_out n(analyzed/peers) x(not analyzed); first row leads"
_SHORT_FLAGS = {"junction-body hold node: declared driver assumption": "hold node"}
# Witness kinds (the text before the first ':') shown first: whole-artifact and pairing witnesses, then per-vehicle
# ones; per-row count mismatches come last, so a wholesale mismatch is never hidden behind row witnesses.
WITNESS_KIND_ORDER = ("report", "report_input", "git", "manifest", "repeated", "off_intention", "interval", "route")
WITNESS_KIND_LAST = ("count",)
WITNESSES_SHOWN = 5
WITNESSES_SHOWN_TEXT = 3


def _more(total: int, shown: int) -> str | None:
    return f"+{total - shown} more" if total > shown else None


def _witness_kind(witness: str) -> str:
    return witness.split(":", 1)[0] if ":" in witness else "id"


def _witnesses(witness_ids: list[str], limit: int, clip: bool) -> dict:
    """A failing invariant in both compact views: the witness total, the count per kind, and the first ``limit``
    witnesses, which take the first witness of every kind before any second one of a kind."""
    by_kind: dict[str, list[str]] = {}
    for witness in witness_ids:
        by_kind.setdefault(_witness_kind(witness), []).append(witness)
    kinds = sorted(by_kind, key=lambda kind: (kind in WITNESS_KIND_LAST, WITNESS_KIND_ORDER.index(kind)
                                              if kind in WITNESS_KIND_ORDER else len(WITNESS_KIND_ORDER), kind))
    ordered = [by_kind[kind][0] for kind in kinds] + [w for kind in kinds for w in by_kind[kind][1:]]
    counts = {_id(kind, clip): len(by_kind[kind]) for kind in kinds}
    if clip and len(counts) > CLIP_ITEMS:
        rest = sum(list(counts.values())[CLIP_ITEMS:])
        counts = {**dict(list(counts.items())[:CLIP_ITEMS]), f"+{len(kinds) - CLIP_ITEMS} kinds": rest}
    return {"n": len(witness_ids), "by_kind": counts, "first": [_id(w, clip) for w in ordered[:limit]]}


def _witness_text(view: dict) -> str:
    kinds = ", ".join(f"{kind} {count}" for kind, count in view["by_kind"].items())
    return (f"FAIL {view['n']} ({kinds}): " + ",".join(view["first"])
            + (f" {more}" if (more := _more(view["n"], len(view["first"]))) else ""))


def _id(text: str, clip: bool, limit: int = CLIP_ID) -> str:
    """An id as shown: unchanged, or in the last-resort budget cut to ``limit`` characters with a hash suffix."""
    text = str(text)
    return text if not clip or len(text) <= limit else f"{text[:limit - 9]}~{ids.short_hash(text)}"


def _capped(items: list, clip: bool) -> list:
    if not clip or len(items) <= CLIP_ITEMS:
        return list(items)
    return [*items[:CLIP_ITEMS], f"+{len(items) - CLIP_ITEMS} more"]


def _verifier(verifier: dict) -> str | None:
    """The verifier commit, marked ``+dirty`` unless the clone was recorded clean."""
    commit = verifier.get("commit")
    return None if commit is None else commit if verifier.get("clean") is True else f"{commit}+dirty"


def _row_text(row: dict) -> str:
    if "field" in row:
        return (f"init {row['r_init']:+d}, main {row['r_main']:+d}, deficit {row['deficit']}, "
                f"post {row['post_window']}, unpassed {row['unpassed']}")
    return (f"r{row['r']:+d} y{row['y']} p{row['planned']} m{row['measured']} {row['ontime']}/{row['late_in']}/"
            f"{row['delayed_out']} n{row['n']}" + (f" x{row['excluded']}" if row.get("excluded") else ""))


def _anchor_text(anchor: dict, *, means: bool = True) -> str:
    flags = "; ".join(_SHORT_FLAGS.get(flag, flag).replace("candidate links", "links") for flag in anchor["flags"])
    where = anchor["anchor"] if anchor["anchor"] != "insertion" else f"insertion@{anchor['element'].split(':', 1)[1]}"
    text = f"{where}>{anchor['node'] or '-'} {anchor['diff']:+.1f}s"
    if means:
        text += f" ({anchor['mean_delayed']:.1f} vs {anchor['mean_peer']:.1f})"
    return text + (f" [{flags}]" if flags else "")


def _spread_text(spread: dict | None) -> str | None:
    if not spread:
        return None
    downstream = ", ".join(f"{edge} {value:+.1f}" for edge, value in spread["downstream"])
    return (f"{spread['sum_s']:+.1f}s over {spread['edges']} edges {spread['from']}..{spread['to']}; downstream "
            f"{spread['downstream_sum_s']:+.1f}s ({downstream or 'none'})")


def _peers_text(lead: dict) -> str:
    excluded = lead["excluded_peers_by_entry"]
    if len(excluded) == 1:
        (edge, count), = excluded.items()
        other = f"{count} on {edge}"
    else:
        other = f"{sum(excluded.values())} on {len(excluded)} entries" if excluded else "none"
    # These are on-time peers on another route prefix, not delayed_out vehicles left out of the analysis (x / "not
    # analyzed" on the rows), so the clause never reuses the word "excluded".
    return (f"{lead['n_delayed']} analyzed vs {lead['n_peer']} on-time; entry {lead['entry']}; "
            f"{lead['route_prefix_edges']}-edge prefix; other-prefix peers {other}")


def _hotspot(hotspot: dict, rows: int, anchors: int) -> dict:
    item = {"id": hotspot["id"], "w": hotspot["weight"],
            "rows": {row.get("row_id", f"field:{row.get('field')}"): _row_text(row) for row in hotspot["rows"][:rows]}}
    if more := _more(len(hotspot["rows"]), rows):
        item["rows"]["more"] = more
    if hotspot["kind"] == "BAL":
        return {**item, "reading": hotspot["reading"]}
    lead = hotspot["lead"]
    item.update({"peers": _peers_text(lead),
                 "co": [_anchor_text(a) for a in lead["coanchors"][:anchors]]})
    if more := _more(len(lead["coanchors"]), anchors):
        item["co"].append(more)
    if lead["next"]:
        item["next"] = _anchor_text(lead["next"], means=False)
    if spread := _spread_text(lead["spread"]):
        item["spread"] = spread
    item["measured_in"] = lead["delayed_measured_in"]
    return item


def _bounds_view(bounds: dict, clip: bool) -> dict:
    """The bound block of both compact views: each value with its conditions and whether they hold on this run."""
    b1, b2 = bounds["B1_all182_request_floor"], bounds["B2_main_supply"]

    def conditions(bound: dict) -> dict:
        view = {"applies_if": [c["condition"] for c in bound["conditions"]],
                "verified_on_parent": bound["verified_on_parent"]}
        if failed := [c["condition"] for c in bound["conditions"] if not c["verified"]]:
            view["not_verified"] = failed
        return view

    return {"use": bounds["use"],
            "B1_all182_request_floor": {"value": b1["value"], **conditions(b1),
                                        "parent_all182": b1["self_check"]["all182_tae"]},
            "B2_main_supply": {"value": b2["value"], "at_init_targets": b2["at_init_targets"]["value"],
                               **conditions(b2), "note": _id(b2["note"], clip, CLIP_NOTE)},
            "SOL": {sol["statistic"]: sol["value"] for sol in bounds["SOL"]},
            "surrogate": bounds["surrogate"]}


def _bound_lines(view: dict) -> list[str]:
    """The text rendering of ``_bounds_view``: the same values, conditions and verification."""
    b1, b2 = view["B1_all182_request_floor"], view["B2_main_supply"]

    def conditions(bound: dict) -> str:
        text = (f"verified_on_parent {json.dumps(bound['verified_on_parent'])} | applies if: "
                + "; ".join(bound["applies_if"]))
        return text + (" | NOT verified: " + "; ".join(bound["not_verified"]) if "not_verified" in bound else "")

    sol = ", ".join(f"{name} {json.dumps(value)}" for name, value in view["SOL"].items())
    return [f"BOUND  B1 all182 request floor {b1['value']} (parent all182 {b1['parent_all182']}) | {conditions(b1)}",
            f"BOUND  B2 main supply {b2['value']} @ measured init counts, {b2['at_init_targets']} @ init targets | "
            f"{conditions(b2)} | {b2['note']}",
            f"BOUND  SOL {sol} | {view['use']}; surrogate: {view['surrogate']}"]


def compact_profile(profile: dict, *, hotspots: int = HOTSPOTS, rows: int = ROWS_PER_HOTSPOT,
                    anchors: int = 3, clip: bool = False) -> dict:
    score, integrity, decomp = profile["score"], profile["integrity"], profile["decomp"]
    balance = profile["field_balance"]
    actuated = integrity["actuated_phase_without_controlling_detector"]
    completion = integrity["completion"]
    shown = [(_id(name, clip), f"init {row['r_init']:+d}, main {row['r_main']:+d}, deficit {row['deficit']}, "
                               f"post {row['post_window']}") for name, row in balance["fields"].items()
             if row["deficit"] or row["post_window"] or row["unpassed"] or row["r_init"] or row["r_main"]]
    nonzero = dict(shown[:CLIP_ITEMS] if clip else shown)
    if len(nonzero) < len(shown):
        nonzero[f"+{len(shown) - len(nonzero)} fields"] = "not zero; see the full profile"
    quiet = len(balance["fields"]) - len(shown)
    if quiet:
        nonzero[f"{quiet} fields"] = "all zero"
    view = {
        "schema": profile["schema"], "kind": profile["kind"], "view": "compact", "status": profile["status"],
        "goal": _id(profile["goal"], clip), "verifier": _verifier(profile["verifier"]),
        "cand": _id(profile["cand"], clip), "parent": profile["parent"],
        "inputs": {"resolved": profile["inputs"]["resolved"],
                   "hold_nodes": _capped([_id(node, clip) for node in profile["inputs"]["hold_nodes"]], clip),
                   "passages": profile["inputs"]["passages"]},
        "invariants": {check["id"]: check["pass"] if check["pass"]
                       else _witnesses(check["witness_ids"], WITNESSES_SHOWN_TEXT if clip else WITNESSES_SHOWN, clip)
                       for check in profile["invariants"]},
        "score": {"tae_main": score["tae_main"], "sse": score["sse"], "mae": round(score["mae"], 7),
                  "max": score["max"], "exact": f"{score['exact']}/{score['rows_main']}", "init52": score["init52_tae"],
                  "all182": score["all182_tae"],
                  "geh_raw_lt5": f"{score['geh_raw']['lt5_percent']:g}% (max {score['geh_raw']['max']:g})",
                  "geh_hourly_lt5": (f"{score['geh_hourly']['lt5_percent']:g}% (max {score['geh_hourly']['max']:g};"
                                     " 2 x geh_value on 15-min counts)")},
        "integrity": {"healthy": integrity["healthy"],
                      "completion": (f"{integrity['arrived']} arrived; {completion.get('teleports')} teleport, "
                                     f"{completion.get('collisions')} collision, {completion.get('waiting')} waiting"),
                      "emergency_braking": integrity["emergency_braking"]["count"],
                      "sumo_warnings": {
                          "actuated_phase_without_controlling_detector": (
                              f"{actuated['warnings']} warnings = {actuated['phases']} phases on "
                              f"{actuated['tl_logics']} tlLogics"),
                          "missing_green_phase": integrity["missing_green_phase"]["count"],
                          "unused_states": integrity["unused_states"]["count"],
                          "unclassified": integrity["unclassified_warnings"]},
                      "early_departures": (f"{integrity['early_departures']['count']} "
                                           f"({integrity['early_departures']['intended']} intended)")},
        "decomp": {"kind": decomp["kind"], "ontime": decomp["ontime"], "late_in": decomp["late_in"],
                   "delayed_out": decomp["delayed_out"], "supply_term_abs": decomp["supply_term_abs"],
                   "ff_violations": f"{decomp['ff_violations']}/{decomp['passages']}",
                   "delay_vs_ff_s": {k: round(v) for k, v in decomp["delay_vs_ff_s"].items() if v is not None}},
        "field_balance": {"identity": balance["identity"], "deficit_total": balance["deficit_total"],
                          "post_window": balance["post_window"], "unpassed": balance["unpassed"],
                          "closes": balance["identity_fields"], "fields": nonzero},
        "bounds": _bounds_view(profile["bounds"], clip),
        "hot": [_hotspot(h, rows, anchors) for h in profile["hotspots"][:hotspots]],
        "hot_legend": {"w": "under-count: LOC sum of min(-r, analyzed); field |R_main|",
                       "rows": ROW_FORMAT, "conf": "accounting",
                       **{f"H:LOC:{name}": {"cannot_tell": tell, "knob": knob} for name, (tell, knob) in
                          (("entry", LOC_TEXT["entry"]), ("edge|co", LOC_TEXT["edge"]))},
                       "H:BAL": {"knob": "quantity (solver) or timing"}},
        "claim": profile["claim"]}
    if more := _more(len(profile["hotspots"]), hotspots):
        view["hot_more"] = more
    return view


def compact_text(profile: dict, *, hotspots: int = HOTSPOTS, rows: int = ROWS_PER_HOTSPOT, anchors: int = 3,
                 clip: bool = False) -> str:
    score, decomp, balance = profile["score"], profile["decomp"], profile["field_balance"]
    integrity = profile["integrity"]
    deficits = _capped([f"{_id(name, clip)} {value}" for value, name in sorted(
        ((row["deficit"], name) for name, row in balance["fields"].items() if row["deficit"]),
        key=lambda item: (-item[0], item[1]))], clip)
    invariants = " | ".join(f"{c['id'][4:]} " + ("ok" if c["pass"] else _witness_text(
        _witnesses(c["witness_ids"], WITNESSES_SHOWN_TEXT, clip))) for c in profile["invariants"])
    verifier = _verifier(profile["verifier"]) or "-"
    lines = [
        f"== astra-twin calibrate {_id(profile['goal'], clip)} cand {_id(profile['cand'], clip)} parent "
        f"{profile['parent'] or '-'} verifier {verifier[:12]}{'+dirty' if verifier.endswith('+dirty') else ''} "
        f"({len(profile['inputs']['hold_nodes'])} hold nodes) status {profile['status']}",
        f"SCORE  TAE {score['tae_main']} | SSE {score['sse']} | max {score['max']} | exact {score['exact']}/"
        f"{score['rows_main']} | init52 {score['init52_tae']} | all182 {score['all182_tae']} | GEH<5 "
        f"{score['geh_raw']['lt5_percent']:g}% | GEH_h<5 {score['geh_hourly']['lt5_percent']:g}%",
        f"INV    {invariants}",
        f"DECOMP ontime {decomp['ontime']} late_in {decomp['late_in']} delayed_out {decomp['delayed_out']} supply "
        f"{decomp['supply_term_abs']} | ff violations {decomp['ff_violations']}/{decomp['passages']} | delay vs ff "
        + " ".join(f"{k} {round(v)}" for k, v in decomp["delay_vs_ff_s"].items() if v is not None) + " s (accounting)",
        f"BAL    request deficit {balance['deficit_total']} (" + ", ".join(deficits)
        + f") | post-window {balance['post_window']} | unpassed {balance['unpassed']} | identity "
        f"{balance['identity_fields']}",
        *_bound_lines(_bounds_view(profile["bounds"], clip))]
    for index, hotspot in enumerate(profile["hotspots"][:hotspots], start=1):
        item = _hotspot(hotspot, rows, anchors)
        if hotspot["kind"] == "BAL":
            lines.append(f"HOT{index}   {item['id']} w {item['w']}: {item['reading']}")
            continue
        shown = "; ".join(f"{name} r{row['r']:+d} dout {row['delayed_out']} w {row['weight']}"
                          + (f" ({row['excluded']} not analyzed)" if row["excluded"] else "")
                          for name, row in zip([name for name in item["rows"] if name != "more"], hotspot["rows"]))
        lines.append(f"HOT{index}   {item['id']} w {item['w']} ({hotspot['class']}): lead {hotspot['lead']['row_id']} "
                     f"{item['peers']}; "
                     f"co-anchors {' | '.join(item['co'])}" + (f"; {item['spread']}" if item.get("spread") else "")
                     + f"; rows {shown}" + (f" {item['rows']['more']}" if "more" in item["rows"] else ""))
    if more := _more(len(profile["hotspots"]), hotspots):
        lines.append(f"HOT    {more} hotspots")
    actuated = integrity["actuated_phase_without_controlling_detector"]
    lines.append(f"INTEG  healthy {integrity['healthy']} | {integrity['arrived']} arrived | emergency braking "
                 f"{integrity['emergency_braking']['count']} | actuated phases w/o detector {actuated['phases']} "
                 f"({actuated['warnings']} warnings) | missing green {integrity['missing_green_phase']['count']} | "
                 f"unused states {integrity['unused_states']['count']} | early departures "
                 f"{integrity['early_departures']['count']} ({integrity['early_departures']['intended']} intended)")
    lines.append(f"CLAIM  {profile['claim']}")
    return "\n".join(lines) + "\n"


# ---- Build (M3) -------------------------------------------------------------------------------------------------

# (hotspots, rows, escalations, clip, minimal). The last budget keeps only counts of every list and failing invariant
# and clips every free text, so its size is bounded by construction (the fixed keys, nine classes and 14 invariants).
_BUILD_BUDGETS = ((HOTSPOTS, ROWS_PER_HOTSPOT, 5, False, False), (HOTSPOTS, 3, 3, False, False),
                  (3, 2, 2, False, False), (1, 1, 1, False, False), (0, 0, 0, False, False), (0, 0, 0, True, False),
                  (0, 0, 0, True, True))
BUILD_ROW_FORMAT = ("<fact id>: <kind> <component> <value> (limit <limit>) e<normalized excess> -> <first suggested "
                    "op>; categorical rows show |difference| and count 1")


def _number(value) -> str:
    return "-" if value is None else f"{value:.4g}" if isinstance(value, float) else str(value)


def _op_brief(ops: list[dict] | None, clip: bool) -> str | None:
    if not ops:
        return None
    first = ops[0]
    params = ",".join(
        f"{key}={_id(json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value, clip, 24)}"
        for key, value in first["params"].items() if key != "shape" or not isinstance(value, list))
    text = f"{first['op']}{{{params}}}" + ("" if first["complete"] else " (incomplete)")
    return text + (f" +{len(ops) - 1} op(s)" if len(ops) > 1 else "")


def _build_row_text(row: dict, clip: bool) -> str:
    if row.get("class") == "INT":
        finding = "" if str(row.get("finding")) in str(row.get("id")) else f": {_id(row.get('finding'), clip)}"
        return f"{row.get('source')}{finding}" + (" (new vs parent)" if row.get("new_vs_parent") else "")
    parts = [f"{name} {_number(part['value'])} (limit {_number(part['limit'])})"
             for name, part in row.get("components", {}).items() if part.get("excess")]
    text = f"{row['kind']} " + ("; ".join(parts) if parts else f"|d| {_number(row.get('magnitude'))}")
    if row.get("excess") is not None:
        text += f" e{row['excess']:.4g}"
    op = _op_brief(row.get("suggested_ops"), clip)
    return text + (f" -> {op}" if op else "")


def _build_hotspot(hotspot: dict, rows: int, clip: bool) -> dict:
    item = {"id": _id(hotspot["id"], clip), "class": hotspot["class"], "w": hotspot["weight"],
            "rows": {_id(row["id"], clip): _build_row_text(row, clip) for row in hotspot["rows"][:rows]}}
    if more := _more(len(hotspot["rows"]), rows):
        item["rows"]["more"] = more
    return item


def _build_escalation(escalation: dict, clip: bool) -> dict:
    first = escalation["facts"][0]
    item = {"id": _id(escalation["id"], clip), "fact": _id(first["fact"], clip), "status": first["status"]}
    if first.get("metrics"):
        item["metrics"] = {key: round(value, 3) for key, value in first["metrics"].items()}
    if more := _more(len(escalation["facts"]), 1):
        item["more"] = more
    return item


def _counted(items: list, clip: bool, minimal: bool) -> list | dict:
    return {"n": len(items)} if minimal else _capped(items, clip)


def compact_build(profile: dict, *, hotspots: int = HOTSPOTS, rows: int = ROWS_PER_HOTSPOT, escalations: int = 5,
                  clip: bool = False, minimal: bool = False) -> dict:
    gate, v = profile["gate"], profile["V"]
    view = {"schema": profile["schema"], "kind": "build", "view": "compact", "status": profile["status"],
            "goal": _id(profile["goal"], clip), "verifier": _verifier(profile["verifier"]),
            "cand": profile["cand"], "parent": profile["parent"], "subject": profile["subject"],
            "op": _id(profile["op_text"], clip, CLIP_NOTE) if profile.get("op_text") else None,
            "gate": {"screening": gate["screening"],
                     "first_fail": _id(gate["first_fail"], clip, CLIP_NOTE) if gate.get("first_fail") else None,
                     "G0": gate["G0"]},
            "V": {"cand": v["cand"], "parent": v["parent"]},
            "layers": {cls: f"{row['mismatched']}/{row['scored']} e{row['excess']:.4g}"
                       for cls, row in profile["layers"].items()},
            "excluded": ({"n": sum(profile["excluded"].values())} if minimal else
                         {_id(key, clip): n for key, n in list(profile["excluded"].items())[:CLIP_ITEMS if clip else None]})}
    if "V" in gate:
        view["V"].update(verdict=gate["V"]["verdict"], tier=gate["V"]["tier"])
        view["gate"].update(outside_fact_changes=None if gate["outside_fact_changes"] is None
                            else len(gate["outside_fact_changes"]),
                            measurability_lost=_counted([_id(f, clip) for f in gate["measurability_lost"]], True,
                                                        minimal),
                            regressed=_counted([_id(f, clip) for f in gate["regressed_facts"]], True, minimal))
        view["invariants"] = {check["id"]: check["pass"] if check["pass"] else {"n": len(check["witness_ids"])}
                              if minimal else _witnesses(check["witness_ids"],
                                                         WITNESSES_SHOWN_TEXT if clip else WITNESSES_SHOWN, clip)
                              for check in profile["invariants"]}
        view["fixed"] = _counted([f"F:{_id(row['fact'], clip)} e{_number(row['parent']['excess'])} -> "
                                  f"{row['candidate']['status']}" for row in gate["fixed_rows"]], True, minimal)
        l3 = gate["L3"]
        view["L3"] = {"result": l3["result"], "source_state": l3["source_state"],
                      "seeds": f"{sum(row['status'] == 'pass' for row in l3['seeds'])}/{len(l3['seeds'])} pass "
                               f"(goal {len(l3['declared_seeds'])})"}
    view["hot"] = [_build_hotspot(h, rows, clip) for h in profile["hotspots"][:hotspots]]
    if more := _more(len(profile["hotspots"]), hotspots):
        view["hot_more"] = more
    view["escalations"] = [_build_escalation(e, clip) for e in profile["escalations"][:escalations]]
    if more := _more(len(profile["escalations"]), escalations):
        view["escalations_more"] = more
    view["legend"] = {"rows": BUILD_ROW_FORMAT, "V": "(n_TOP, n_CTL, n_GEO, n_ATTR, E)",
                      "w": "sum of normalized excess; categorical, INT and unmeasured rows count 1"}
    view["claim"] = profile["claim"]
    return view


def _listed(items: list | dict) -> str:
    return f"{items['n']} (counts only)" if isinstance(items, dict) else "; ".join(items)


def compact_build_text(profile: dict, *, hotspots: int = HOTSPOTS, rows: int = ROWS_PER_HOTSPOT,
                       escalations: int = 5, clip: bool = False, minimal: bool = False) -> str:
    view = compact_build(profile, hotspots=hotspots, rows=rows, escalations=escalations, clip=clip, minimal=minimal)
    verifier = view["verifier"] or "-"
    gate = view["gate"]
    lines = [f"== astra-twin build {view['goal']} cand {view['cand']} parent {view['parent'] or '-'} verifier "
             f"{verifier[:12]}{'+dirty' if verifier.endswith('+dirty') else ''} ({view['subject']}) status "
             f"{view['status']}"]
    if view["op"]:
        lines.append(f"OP     {view['op']}")
    text = f"GATE   {gate['screening']} | G0 {gate['G0']}"
    if "outside_fact_changes" in gate:
        lost, regressed = gate["measurability_lost"], gate["regressed"]
        text += (f" | outside {_number(gate['outside_fact_changes'])} | lost "
                 f"{lost['n'] if isinstance(lost, dict) else len(lost)} | regressed "
                 f"{regressed['n'] if isinstance(regressed, dict) else len(regressed)}")
    lines.append(text + (f" | first fail: {gate['first_fail']}" if gate["first_fail"] else ""))
    lines.append(f"V      cand {view['V']['cand']} parent {view['V']['parent'] or '-'}"
                 + (f" -> {view['V']['verdict']} ({view['V']['tier']})" if "verdict" in view["V"] else ""))
    lines.append("LAYERS " + " | ".join(f"{cls} {value}" for cls, value in view["layers"].items())
                 + " | excluded " + (", ".join(f"{key} {n}" for key, n in view["excluded"].items()) or "none"))
    if "invariants" in view:
        lines.append("INV    " + " | ".join(f"{check['id'][4:] if check['id'].startswith('INV-') else check['id']} "
                                            + ("ok" if check["pass"] else f"FAIL {len(check['witness_ids'])}" if minimal
                                               else _witness_text(_witnesses(check["witness_ids"],
                                                                             WITNESSES_SHOWN_TEXT, clip)))
                                            for check in profile["invariants"]))
        if view["fixed"]:
            lines.append("FIXED  " + _listed(view["fixed"]))
        lines.append(f"L3     {view['L3']['result']} | {view['L3']['seeds']} | source {view['L3']['source_state']}")
    for index, hotspot in enumerate(view["hot"], start=1):
        shown = "; ".join(f"{name}: {text}" for name, text in hotspot["rows"].items() if name != "more")
        lines.append(f"HOT{index}   {hotspot['id']} w {hotspot['w']:.4g}: {shown}"
                     + (f" {hotspot['rows']['more']}" if "more" in hotspot["rows"] else ""))
    if "hot_more" in view:
        lines.append(f"HOT    {view['hot_more']} hotspots")
    for index, escalation in enumerate(view["escalations"], start=1):
        lines.append(f"ESC{index}   {escalation['id']} {escalation['fact']}: {escalation['status']}"
                     + (f" {escalation['more']}" if "more" in escalation else ""))
    if "escalations_more" in view:
        lines.append(f"ESC    {view['escalations_more']} escalations")
    lines.append(f"CLAIM  {view['claim']}")
    return "\n".join(lines) + "\n"


def render_build_compact(profile: dict, *, cap: int = CAP_CHARACTERS) -> tuple[str, str]:
    """Return (compact JSON, compact text) of a build profile at the largest budget where both fit ``cap``."""
    for hotspots, rows, escalations, clip, minimal in _BUILD_BUDGETS:
        budget = {"hotspots": hotspots, "rows": rows, "escalations": escalations, "clip": clip, "minimal": minimal}
        views = (json.dumps(compact_build(profile, **budget), ensure_ascii=False, separators=(",", ":")) + "\n",
                 compact_build_text(profile, **budget))
        if all(len(view) <= cap for view in views):
            return views
    raise ValueError(f"build compact view exceeds {cap} characters even with every id and list clipped")


def render_compact(profile: dict, *, cap: int = CAP_CHARACTERS) -> tuple[str, str]:
    """Return (compact JSON, compact text) at the largest budget where both fit within ``cap`` characters."""
    for hotspots, rows, anchors, clip in _BUDGETS:
        budget = {"hotspots": hotspots, "rows": rows, "anchors": anchors, "clip": clip}
        views = (json.dumps(compact_profile(profile, **budget), ensure_ascii=False, separators=(",", ":")) + "\n",
                 compact_text(profile, **budget))
        if all(len(view) <= cap for view in views):
            return views
    # Unreachable at the default cap: the clipped budget bounds every id and list.
    raise ValueError(f"compact view exceeds {cap} characters even with every id and list clipped")
