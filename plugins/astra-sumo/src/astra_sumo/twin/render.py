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
ROW_FORMAT = "r y p(lanned) m(easured) ontime/late_in/delayed_out n(delayed/peers); first row leads"
_SHORT_FLAGS = {"junction-body hold node: declared driver assumption": "hold node"}


def _more(total: int, shown: int) -> str | None:
    return f"+{total - shown} more" if total > shown else None


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
            f"{row['delayed_out']} n{row['n']}")


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
    return (f"{lead['n_delayed']} delayed_out vs {lead['n_peer']} on-time; entry {lead['entry']}; "
            f"{lead['route_prefix_edges']}-edge prefix; excluded {other}")


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


def compact_profile(profile: dict, *, hotspots: int = HOTSPOTS, rows: int = ROWS_PER_HOTSPOT,
                    anchors: int = 3, clip: bool = False) -> dict:
    score, integrity, decomp = profile["score"], profile["integrity"], profile["decomp"]
    balance, bounds = profile["field_balance"], profile["bounds"]
    b1, b2 = bounds["B1_all182_request_floor"], bounds["B2_main_supply"]
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
                       else [_id(w, clip) for w in check["witness_ids"][:3 if clip else 5]]
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
        "bounds": {"use": bounds["use"],
                   "B1_all182_request_floor": {"value": b1["value"],
                                               "applies_if": [c["condition"] for c in b1["conditions"]],
                                               "verified_on_parent": b1["verified_on_parent"],
                                               "parent_all182": b1["self_check"]["all182_tae"]},
                   "B2_main_supply": {"value": b2["value"], "at_init_targets": b2["at_init_targets"]["value"],
                                      "applies_if": [c["condition"] for c in b2["conditions"]],
                                      "verified_on_parent": b2["verified_on_parent"],
                                      "note": _id(b2["note"], clip, CLIP_NOTE)},
                   "SOL": {sol["statistic"]: sol["value"] for sol in bounds["SOL"]},
                   "surrogate": bounds["surrogate"]},
        "hot": [_hotspot(h, rows, anchors) for h in profile["hotspots"][:hotspots]],
        "hot_legend": {"w": "under-count explained: LOC sum of min(-r, delayed_out); field |R_main|",
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
    b1, b2 = profile["bounds"]["B1_all182_request_floor"], profile["bounds"]["B2_main_supply"]
    integrity = profile["integrity"]
    deficits = _capped([f"{_id(name, clip)} {value}" for value, name in sorted(
        ((row["deficit"], name) for name, row in balance["fields"].items() if row["deficit"]),
        key=lambda item: (-item[0], item[1]))], clip)
    invariants = " | ".join(f"{c['id'][4:]} " + ("ok" if c["pass"] else "FAIL " + ",".join(
        _id(w, clip) for w in c["witness_ids"][:3])) for c in profile["invariants"])
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
        + f") | post-window {balance['post_window']} | "
        f"unpassed {balance['unpassed']} | identity {balance['identity_fields']} | B1 all182 >= {b1['value']} if "
        "totals kept",
        f"BOUND  B2 main supply {b2['value']} @ measured init counts, {b2['at_init_targets']['value']} @ init targets "
        "(conditional, reported only)"]
    for index, hotspot in enumerate(profile["hotspots"][:hotspots], start=1):
        item = _hotspot(hotspot, rows, anchors)
        if hotspot["kind"] == "BAL":
            lines.append(f"HOT{index}   {item['id']} w {item['w']}: {item['reading']}")
            continue
        shown = "; ".join(f"{name} r{row['r']:+d} dout {row['delayed_out']}" for name, row in zip(
            [name for name in item["rows"] if name != "more"], hotspot["rows"]))
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
