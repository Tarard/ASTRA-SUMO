"""Build known-answer harnesses (M3): K1 on the synthetic X4 fixtures and K2 on the clean Ring1 nominal net.

K1 applies every registered synthetic mutation (``corridor/synthetic_networks.py``) to its clean fixture, freezes a
Build goal against reference facts produced from that clean fixture (``core/reference_net_facts.py``) with every
junction of the fixture in scope, profiles the mutant and checks where the profile points: the top hotspots for the
fact-observable mutations, the direction check (and the NEMA binding scan) for wrong-turn, INT rows for the internal
mutations, and zero direction findings on every clean fixture. X4 has one junction, so naming it is automatic;
attribution is scored at lane level (a hotspot row names a lane of a changed connection) against null predictors.

K2 injects faults into the clean Ring1 nominal net. Ledger faults go through verify-edit explore (one typed op, no
routeability seeds); they are a mechanics test of the profiler and the gate, not independent evidence, because the
same typed-op vocabulary makes the fault and suggests the fix. Raw-XML perturbations edit the net bytes directly.
Each faulted net becomes a goal source with the clean net as its reference and every junction within 100 m of the
injected element in scope (so INT rows can fall outside it); its hotspots must be exactly the injected elements, and
the inverse typed op must reach V = 0 with no outside fact change. Acceptance (f) is timed separately: timings never
enter the deterministic report, so ``run_k2`` returns ``timing_checks`` beside it.

Reports are deterministic JSON without absolute paths or timings (those go to a separate timing file). Everything is
explore evidence against model-relative known answers; no field claim.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import time
import xml.etree.ElementTree as ET
from collections import Counter
from copy import deepcopy
from pathlib import Path

from ..core.artifact_io import write_json_atomic
from . import build_gate, build_profile, goal as goal_module, ids, junction_integrity

SCHEMA = "astra.twin-known-answers/v1"
TARGET_DATE = "2026-10-25"
CLAIM = ("Known-answer mechanics only: model-relative reference facts produced from clean nets, explore mode, no "
         "field claim and no promotion authority.")

# ---- K1: X4 independent faults ------------------------------------------------------------------------------------

K1_FIXTURES = {"crossed-lane-map": "parallel-two-lane-x4", "pedestrian-conflict": "pedestrian-x4",
               "rail-conflict": "rail-x4"}
K1_DEFAULT_FIXTURE = "standard-x4"
K1_SIDES = ("right", "left")
# The design's fact-observable list (movement facts, then signal-binding facts); the harness derives the set from the
# scorecards and records whether it equals this one.
DESIGN_FACT_OBSERVABLE = ("wrong-to-lane", "crossed-lane-map", "missing-connection", "illegal-movement",
                          "missing-legal-movement", "shared-link-index-conflict", "out-of-range-link-index",
                          "duplicate-connection")
K1_TARGETS = {"top1": 0.90, "top3": 0.95}
NEMA_MISMATCH = "movement_turn_geometry_mismatch"
# Deviations from the design's acceptance text that the harnesses apply; each report lists the ones it uses.
HARNESS_AMENDMENTS = {
    "K1-lane-level": "(a) top-1/top-3 is scored at lane level (a row of the hotspot names a road lane of a connection "
                     "the mutation changed) and must beat a constant and a cross-case null predictor; junction-level "
                     "naming is reported only, because X4's single junction J0 is in every truth set",
    "K2-class-on-first-target": "(b) every hotspot must lie in the injected element family; class, anchor and op are "
                                "checked on the first target-class hotspot, while an INT hotspot (ranked first by "
                                "design) is checked for family membership only",
    "K2-neighbourhood-scope": "K2 goals scope every junction within 100 m of the injected element, so INT rows are "
                              "not confined to the injected element's own junctions",
}


def _net_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _write_new(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
    return path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mutation_truth(clean: ET.Element, mutant: ET.Element) -> dict:
    """The elements a mutation changed, from a structural diff of the two nets.

    ``anchors`` uses the profile's hotspot anchors (junction, ``tls:<id>#<link>``, lane, edge); ``lanes`` are the
    road lanes of every changed connection, for the stricter lane-level attribution."""
    junctions = {row.get("id") for row in clean.findall("junction")} | {row.get("id") for row in mutant.findall(
        "junction")}
    road_edges = {row.get("id"): row for root in (clean, mutant) for row in root.findall("edge")
                  if row.get("function") not in {"internal", "crossing", "walkingarea"}}

    def connections(root: ET.Element) -> Counter:
        return Counter(json.dumps(sorted(row.attrib.items())) for row in root.findall("connection"))

    changed_connections = [dict(json.loads(row)) for row in sorted(
        set((connections(clean) - connections(mutant)) | (connections(mutant) - connections(clean))))]
    anchors, lanes, nodes = set(), set(), set()
    for row in changed_connections:
        for side in ("from", "to"):
            edge = row.get(side, "")
            if edge in road_edges:
                lanes.add(f"{edge}_{row.get(side + 'Lane')}")
                nodes.add(road_edges[edge].get("to" if side == "from" else "from"))
            else:
                nodes.add(junction_integrity.junction_prefix_owner(edge, junctions))
        if row.get("tl") and row.get("linkIndex") is not None:
            anchors.add(f"tls:{row['tl']}#{row['linkIndex']}")
    for tag in ("junction", "tlLogic", "edge"):
        before = {row.get("id"): ET.tostring(row) for row in clean.findall(tag)}
        after = {row.get("id"): ET.tostring(row) for row in mutant.findall(tag)}
        for key in sorted(set(before) | set(after), key=str):
            if before.get(key) == after.get(key):
                continue
            if tag == "junction":
                nodes.add(key)
            elif tag == "tlLogic":
                anchors.add(f"tls:{key}")
                nodes |= {_tls_junction(root, key) for root in (clean, mutant)} - {None}
            elif key in road_edges:
                anchors.add(ids.element_id("edge", key))
            else:
                nodes.add(junction_integrity.junction_prefix_owner(key, junctions))
    nodes.discard(None)
    anchors |= {ids.element_id("junction", node) for node in nodes} | {ids.element_id("lane", lane) for lane in lanes}
    anchors |= {ids.element_id("edge", lane.rpartition("_")[0]) for lane in lanes}
    return {"anchors": sorted(anchors), "lanes": sorted(lanes), "junctions": sorted(nodes),
            "changed_connections": len(changed_connections)}


def _tls_junction(root: ET.Element, tls_id: str) -> str | None:
    for junction in root.findall("junction"):
        if junction.get("id") == tls_id or tls_id in (junction.get("tl") or "").split():
            return junction.get("id")
    return None


def anchor_named(anchor: str, truth: dict) -> bool:
    """A hotspot anchor names the mutated element when it is one of the truth anchors (a ``tls:<id>#<link>`` anchor
    also names it when the whole program changed)."""
    tls = anchor.partition("#")[0]
    return anchor in truth["anchors"] or (anchor.startswith("tls:") and tls in truth["anchors"])


def _row_lanes(row: dict) -> set[str]:
    """Road lanes one hotspot row names: an INT row's ``lanes`` (``junction_integrity.named_lanes`` and the safety
    findings' movements), or the lane of a fact id (``lanes:<lane>:...``, ``lane:<lane>:...``, ``pair:<a>><b>:...``)."""
    if "lanes" in row:
        return set(row["lanes"])
    kind, _, rest = row["id"].partition(":")
    if kind in {"lane", "lanes"}:
        return {rest.rpartition(":")[0] if kind == "lane" else rest.partition(":")[0]}
    if kind == "pair":
        return set(rest.rpartition(":")[0].split(">"))
    return set()


def hotspot_lanes(hotspot: dict) -> set[str]:
    return set().union(*(_row_lanes(row) for row in hotspot["rows"]))


def lanes_named(hotspot: dict, truth: dict) -> bool:
    return bool(hotspot_lanes(hotspot) & set(truth["lanes"]))


def null_baselines(cases: list[dict]) -> dict:
    """Lane-level top-1 rates a predictor without the profile reaches on the same cases.

    ``constant``: the k road lanes most often in a truth set (chosen in hindsight), k = the mean number of lanes the
    profile's top-1 hotspots name, rounded up; ``constant_1`` the single most frequent one. ``cross_case``: every
    case answered with the top-1 lanes of the next case (another mutation, same side, in case order)."""
    frequency = Counter(lane for case in cases for lane in set(case["truth"]["lanes"]))
    ranked = [lane for lane, _ in sorted(frequency.items(), key=lambda item: (-item[1], item[0]))]
    sizes = [len(case["top"][0]["lanes"]) if case["top"] else 0 for case in cases]
    k = max(1, math.ceil(sum(sizes) / len(sizes))) if sizes else 1

    def rate(hits: int) -> float | None:
        return round(hits / len(cases), 6) if cases else None

    constant = {name: rate(sum(bool(set(ranked[:size]) & set(case["truth"]["lanes"])) for case in cases))
                for name, size in (("constant_1", 1), ("constant", k))}
    cross = 0
    for case in cases:
        others = [other for other in cases if other["side"] == case["side"] and other["mutation"] != case["mutation"]]
        later = [other for other in others if other["case"] > case["case"]] or others
        answer = set(later[0]["top"][0]["lanes"]) if later and later[0]["top"] else set()
        cross += bool(answer & set(case["truth"]["lanes"]))
    return {**constant, "cross_case": rate(cross), "k": k, "constant_lanes": ranked[:k]}


def _goal_and_profile(repository_root: Path, *, net: Path, reference: Path, site_id: str, store: Path,
                      out_dir: Path, verifier: dict, mutable_junction_ids=(), mutable_edge_ids=(),
                      target_date: str = TARGET_DATE) -> dict:
    created, facts, _ = goal_module.create_build_goal(
        repository_root, net=net, reference_net=reference, site_id=site_id, target_date=target_date,
        mutable_junction_ids=list(mutable_junction_ids), mutable_edge_ids=list(mutable_edge_ids), seeds=[42],
        scratch_dir=store.parent)
    path, _, _ = goal_module.write_build_goal(created, facts, store)
    loaded, data, facts_bytes, facts_path = goal_module.load_build_goal(path)
    return build_profile.write_build_profile(loaded, data, facts_bytes, facts_path=facts_path,
                                             repository_root=repository_root, out_dir=out_dir, verifier=verifier)


def _nema(net: Path, out_dir: Path) -> dict:
    from ..core.standard_nema_binding import build_standard_nema_phase_binding

    report = build_standard_nema_phase_binding(net, output_dir=out_dir, run_runtime_checks=False,
                                               run_routeability=False)
    candidates = [{"junction_id": row.get("junction_id"), "blockers": sorted(row.get("blockers") or [])}
                  for row in report.get("candidates") or []]
    return {"status": report.get("status"), "nema_binding_status": report.get("nema_binding_status"),
            "candidates": candidates,
            "turn_geometry_mismatch": sorted(blocker for row in candidates for blocker in row["blockers"]
                                             if blocker.startswith(NEMA_MISMATCH))}


def fixture_junctions(root: ET.Element) -> list[str]:
    """Every non-internal junction of a fixture: the K1 scope, independent of the mutation."""
    return sorted(row.get("id") for row in root.findall("junction") if row.get("type") != "internal")


def _k1_case(repository_root: Path, *, mutation: str, fixture: str, side: str, clean_root: ET.Element,
             clean_path: Path, out_dir: Path, verifier: dict) -> dict:
    from ..corridor.synthetic_networks import apply_synthetic_mutation

    key = f"{mutation}.{fixture}.{side}"
    mutant = apply_synthetic_mutation(clean_root, mutation)
    data = _net_bytes(mutant)
    net = _write_new(out_dir / "cases" / f"{key}.net.xml", data)
    truth = mutation_truth(clean_root, mutant)
    profile = _goal_and_profile(repository_root, net=net, reference=clean_path, site_id=f"k1-{fixture}-{side}",
                                store=out_dir / "store", out_dir=out_dir / "profiles" / key, verifier=verifier,
                                mutable_junction_ids=fixture_junctions(clean_root))
    hotspots = profile["hotspots"]
    nonmatched = sorted(fact for fact, (status, _) in profile["facts"].items() if status != "matched")
    escalated = sorted(row["fact"] for group in profile["escalations"] for row in group["facts"])
    sources = Counter(row["source"] for row in profile["int_findings"])
    top = [{"rank": rank, "id": h["id"], "class": h["class"], "anchor": h["anchor"], "weight": h["weight"],
            "named": anchor_named(h["anchor"], truth), "lanes_named": lanes_named(h, truth),
            "lanes": sorted(hotspot_lanes(h)), "rows": [row["id"] for row in h["rows"]][:6]}
           for rank, h in enumerate(hotspots[:5], 1)]
    return {"case": key, "mutation": mutation, "fixture": fixture, "side": side,
            "clean_sha256": _sha(_net_bytes(clean_root)), "mutant_sha256": _sha(data),
            "changed": _sha(data) != _sha(_net_bytes(clean_root)), "truth": truth,
            "fact_observable": bool(nonmatched or escalated), "nonmatched_facts": nonmatched,
            "escalated_facts": escalated, "V": profile["V"]["cand"],
            "int": {"count": len(profile["int_findings"]), "sources": dict(sorted(sources.items())),
                    "ids": [row["id"] for row in profile["int_findings"]][:12]},
            "direction": {**profile["direction"],
                          "finding_ids": [row["id"] for row in profile["int_findings"]
                                          if row["source"] == "direction"]},
            "hotspot_count": len(hotspots), "top": top,
            "top1": bool(top) and top[0]["named"], "top3": any(row["named"] for row in top[:3]),
            "top1_lanes": bool(top) and top[0]["lanes_named"], "top3_lanes": any(row["lanes_named"] for row in top[:3]),
            "status": profile["status"]}


def _rate(rows: list[dict], key: str) -> dict:
    hits = sum(bool(row[key]) for row in rows)
    return {"hits": hits, "cases": len(rows), "rate": round(hits / len(rows), 6) if rows else None}


def run_k1(out_dir: Path, *, repository_root: Path, verifier: dict, sides: tuple[str, ...] = K1_SIDES,
           mutations: tuple[str, ...] | None = None) -> dict:
    """K1 on the X4 fixtures into a NEW directory; returns the report (plus ``timing``)."""
    from ..corridor.enums import TrafficSide
    from ..corridor.synthetic_networks import _MUTATIONS, FIXTURES, build_synthetic_fixture
    from .direction_check import check_connection_directions

    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} exists; every harness run writes a new directory")
    out_dir.mkdir(parents=True)
    started = time.perf_counter()
    timing: dict[str, float] = {}
    names = tuple(sorted(_MUTATIONS)) if mutations is None else tuple(mutations)
    clean_direction, controls, cases, nema = [], [], [], {}
    for side in sides:
        traffic = TrafficSide(side)
        for fixture in sorted(FIXTURES):
            report = check_connection_directions(build_synthetic_fixture(fixture, traffic_side=traffic))
            clean_direction.append({"fixture": fixture, "side": side, "checked": report["checked_count"],
                                    "findings": report["finding_count"], "ambiguous": report["ambiguous_count"]})
        used = sorted({K1_FIXTURES.get(name, K1_DEFAULT_FIXTURE) for name in names})
        cleans = {}
        for fixture in used:
            root = build_synthetic_fixture(fixture, traffic_side=traffic)
            path = _write_new(out_dir / "fixtures" / f"{fixture}.{side}.clean.net.xml", _net_bytes(root))
            cleans[fixture] = (root, path)
            tick = time.perf_counter()
            # False-positive control: the clean fixture profiled against its own reference facts.
            profile = _goal_and_profile(repository_root, net=path, reference=path, site_id=f"k1-{fixture}-{side}",
                                        store=out_dir / "store", out_dir=out_dir / "profiles" / f"clean.{fixture}.{side}",
                                        verifier=verifier, mutable_junction_ids=fixture_junctions(root))
            timing[f"clean.{fixture}.{side}"] = round(time.perf_counter() - tick, 3)
            controls.append({"fixture": fixture, "side": side, "V": profile["V"]["cand"],
                             "hotspots": profile["hotspot_ids"], "int": len(profile["int_findings"]),
                             "escalations": [row["id"] for row in profile["escalations"]],
                             "scored": sum(layer["scored"] for layer in profile["layers"].values())})
        for mutation in names:
            fixture = K1_FIXTURES.get(mutation, K1_DEFAULT_FIXTURE)
            tick = time.perf_counter()
            case = _k1_case(repository_root, mutation=mutation, fixture=fixture, side=side,
                            clean_root=cleans[fixture][0], clean_path=cleans[fixture][1], out_dir=out_dir,
                            verifier=verifier)
            timing[case["case"]] = round(time.perf_counter() - tick, 3)
            cases.append(case)
            if mutation == "wrong-turn":
                nema[side] = {"clean": _nema(cleans[fixture][1], out_dir / "nema" / f"clean.{side}"),
                              "wrong_turn": _nema(out_dir / "cases" / f"{case['case']}.net.xml",
                                                  out_dir / "nema" / f"wrong-turn.{side}")}
    observable = [case for case in cases if case["fact_observable"]]
    direction_cases = [case for case in cases if not case["fact_observable"] and case["direction"]["finding_ids"]]
    internal = [case for case in cases if not case["fact_observable"] and not case["direction"]["finding_ids"]]
    derived = sorted({case["mutation"] for case in observable})
    rates = {"top1_lanes": _rate(observable, "top1_lanes"), "top3_lanes": _rate(observable, "top3_lanes"),
             "top1_junction_level": _rate(observable, "top1"), "top3_junction_level": _rate(observable, "top3")}
    null = null_baselines(observable)
    best_null = max((value for key, value in null.items() if key in {"constant_1", "constant", "cross_case"}
                     and value is not None), default=None)
    wrong_turn = [case for case in cases if case["mutation"] == "wrong-turn"]
    top1, top3 = rates["top1_lanes"]["rate"], rates["top3_lanes"]["rate"]
    checks = {
        "every_mutation_changed_its_fixture": all(case["changed"] for case in cases),
        "fact_observable_set_equals_design": derived == sorted(set(DESIGN_FACT_OBSERVABLE) & set(names)),
        "top1_lane_rate_at_least_0.90": top1 is not None and top1 >= K1_TARGETS["top1"],
        "top3_lane_rate_at_least_0.95": top3 is not None and top3 >= K1_TARGETS["top3"],
        "top1_lane_rate_beats_every_null_predictor": top1 is not None and best_null is not None and top1 > best_null,
        "wrong_turn_found_by_direction_check": bool(wrong_turn) and all(
            case["direction"]["finding_ids"] and not case["fact_observable"] for case in wrong_turn),
        "internal_mutations_surface_as_int": all(case["int"]["count"] > 0 and case["top"]
                                                 and case["top"][0]["class"] == "INT" for case in internal),
        "clean_fixtures_have_zero_direction_findings": all(row["findings"] == 0 for row in clean_direction),
        "clean_controls_have_no_hotspots": all(not row["hotspots"] and row["int"] == 0
                                               and set(map(float, row["V"])) == {0.0} for row in controls)}
    report = {
        "schema": SCHEMA, "harness": "k1", "status": "pass" if all(checks.values()) else "blocked",
        "checks": checks, "failed_checks": sorted(name for name, ok in checks.items() if not ok),
        "rates": rates, "targets": K1_TARGETS, "null_baselines": null,
        "attribution_rule": ("lane level (the acceptance rates): a hotspot names the mutated element when one of its "
                             "rows names a road lane of a connection that a structural diff of clean and mutant "
                             "changed (fact ids by their lane; INT rows by the lanes their finding names); top-1/top-3 "
                             "over the fact-observable mutations, against the null predictors. Junction level "
                             "(reported only): the hotspot anchor is a changed junction, tls#link, lane or edge, which "
                             "X4's single junction makes automatic"),
        "amendments": {key: HARNESS_AMENDMENTS[key] for key in ("K1-lane-level",)},
        "fixtures": {name: K1_FIXTURES.get(name, K1_DEFAULT_FIXTURE) for name in names},
        "groups": {"fact_observable": derived, "design_fact_observable": sorted(DESIGN_FACT_OBSERVABLE),
                   "direction": sorted({case["mutation"] for case in direction_cases}),
                   "internal": sorted({case["mutation"] for case in internal})},
        "misses": {"top1_lanes": sorted(case["case"] for case in observable if not case["top1_lanes"]),
                   "top3_lanes": sorted(case["case"] for case in observable if not case["top3_lanes"]),
                   "internal_without_int": sorted(case["case"] for case in internal if not case["int"]["count"])},
        "int_sources_by_mutation": {case["case"]: case["int"]["sources"] for case in cases},
        "wrong_turn": {"cases": [{"case": case["case"], "direction_findings": case["direction"]["finding_ids"],
                                  "top": case["top"][:1]} for case in wrong_turn],
                       "nema_binding_scan": nema,
                       "nema_also_blocks": {side: bool(row["wrong_turn"]["turn_geometry_mismatch"])
                                            for side, row in sorted(nema.items())}},
        "clean_direction": clean_direction, "clean_controls": controls, "cases": cases, "claim": CLAIM}
    write_json_atomic(out_dir / "k1-report.json", report, sort_keys=True)
    timing["total_s"] = round(time.perf_counter() - started, 3)
    write_json_atomic(out_dir / "k1-timing.json", timing, sort_keys=True)
    return {**report, "timing": timing}


# ---- K2: Ring1 nominal faults -------------------------------------------------------------------------------------

RING1_NOMINAL = "benchmarks/hamburg_ring1_119_control_diagnosis_20261022_v1/nominal-inputs/network.net.xml"
RING1_SHA256_PREFIX = "cc0453749470"
K2_SITE = "ring1-nominal-k2"
K2_TARGET_DATE = "2026-10-22"
K2_MAX_STEPS = 3
K2_SCOPE_RADIUS_M = 100.0
PERF_LIMIT_S = 2.0
ZERO_V = [0, 0, 0, 0, 0.0]
LEDGER_LABEL = ("mechanics test, not independent: the typed-op vocabulary that injects the fault also suggests the "
                "repair")
RAW_LABEL = "raw XML perturbation of a temporary copy; no typed op made it"
# One typed op each (stop line, speed, width, lane count, and a signalized movement whose deletion also breaks its
# signal binding), then three raw perturbations. Elements were chosen where verify-edit materializes cleanly: a
# connection with a saved custom shape cannot be re-added with that shape by add_lane_connection, and a lane count
# next to junctions without a custom shape rebuilds their contours.
K2_LEDGER_FAULTS = (
    {"id": "stop_line", "operation": "move_stop_line", "params": {"lane_id": "-142692918_1", "end_offset_m": 3.0},
     "expect": {"class": "CTL", "anchor": "lane:-142692918_1", "op": "move_stop_line"}},
    {"id": "speed", "operation": "set_speed", "params": {"lane_id": "-186713427_0", "speed_m_s": 8.0},
     "expect": {"class": "ATTR", "anchor": "edge:-186713427", "op": "set_speed"}},
    {"id": "width", "operation": "set_lane_width", "params": {"lane_id": "25218459_0", "width_m": 3.6},
     "expect": {"class": "ATTR", "anchor": "edge:25218459", "op": "set_lane_width"}},
    {"id": "lane_count", "operation": "set_lane_count",
     "params": {"edge_id": "606141103#1", "num_lanes": 4, "add_side": "left"},
     "expect": {"class": "TOP", "anchor": "edge:606141103#1", "op": "set_lane_count"},
     "inverse": {"operation": "set_lane_count", "params": {"edge_id": "606141103#1", "num_lanes": 3, "remove_index": 3},
                 "why": "the suggestion leaves remove_index or add_side to a human; the injected lane is index 3"}},
    {"id": "movement_binding", "operation": "delete_lane_connection",
     "params": {"from": "186821036#2", "fromLane": 1, "to": "142944477", "toLane": 2},
     "expect": {"class": "TOP", "anchor": "junction:1564056277", "op": "add_lane_connection",
                "also": ["H:CTL:tls:1564056277#2"]}},
)
K2_RAW_FAULTS = (
    {"id": "vertex_jitter", "kind": "jitter", "lane_id": "-25218459_0", "offset_m": 1.0,
     "expect": {"class": "GEO", "anchor": "edge:-25218459", "op": "set_edge_shape"},
     "inverse": {"operation": "set_edge_shape", "params": {"edge_id": "-25218459", "shape": "reference_edge_shape"},
                 "why": "the suggestion needs an edge axis; the clean net's edge shape is the known answer"}},
    {"id": "width_rewrite", "kind": "width", "lane_id": "-142770551_0", "width_m": 2.6,
     "expect": {"class": "ATTR", "anchor": "edge:-142770551", "op": "set_lane_width"}},
    {"id": "per_lane_shape", "kind": "shift", "lane_id": "-199239120_1", "offset_m": 1.0,
     "expect": {"escalation": "H:OPX:lane:-199239120_1"}},
)


def _points(text: str) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y, *_ in (token.split(",") for token in text.split())]


def _format(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.6f},{y:.6f}" for x, y in points)


def _road_lane(root: ET.Element, lane_id: str) -> ET.Element:
    for edge in root.findall("edge"):
        for lane in edge.findall("lane"):
            if lane.get("id") == lane_id:
                return lane
    raise ValueError(f"K2 refused: lane {lane_id} is not in the net")


def perturb_raw(root: ET.Element, case: dict) -> ET.Element:
    """A raw XML perturbation on a copy: ``jitter`` moves interior lane vertices alternately left and right,
    ``shift`` moves a whole lane sideways, ``width`` rewrites the lane width attribute."""
    result = deepcopy(root)
    lane = _road_lane(result, case["lane_id"])
    if case["kind"] == "width":
        lane.set("width", f"{case['width_m']:.2f}")
        return result
    points = _points(lane.get("shape", ""))
    if case["kind"] == "shift":
        (x0, y0), (x1, y1) = points[0], points[-1]
        norm = math.hypot(x1 - x0, y1 - y0)
        dx, dy = -(y1 - y0) / norm * case["offset_m"], (x1 - x0) / norm * case["offset_m"]
        lane.set("shape", _format([(x + dx, y + dy) for x, y in points]))
        return result
    if len(points) < 3:
        raise ValueError(f"K2 refused: lane {case['lane_id']} has no interior vertex to jitter")
    moved = [points[0]]
    for index in range(1, len(points) - 1):
        (xa, ya), (xb, yb) = points[index - 1], points[index + 1]
        norm = math.hypot(xb - xa, yb - ya)
        side = case["offset_m"] if index % 2 else -case["offset_m"]
        moved.append((points[index][0] - (yb - ya) / norm * side, points[index][1] + (xb - xa) / norm * side))
    lane.set("shape", _format(moved + [points[-1]]))
    return result


def injected_family(root: ET.Element, edges: list[str], junctions: list[str]) -> list[str]:
    """Anchors of the injected elements: each edge, its lanes and both end junctions, each junction, and the
    ``tls:<id>#`` prefixes of the controllers at those junctions."""
    family, nodes = set(), set(junctions)
    for edge in root.findall("edge"):
        if edge.get("id") in edges:
            family.add(ids.element_id("edge", edge.get("id")))
            family |= {ids.element_id("lane", lane.get("id")) for lane in edge.findall("lane")}
            nodes |= {edge.get("from"), edge.get("to")}
    family |= {ids.element_id("junction", node) for node in nodes if node}
    road = {edge.get("id"): edge.get("to") for edge in root.findall("edge") if not edge.get("function")}
    family |= {f"tls:{row.get('tl')}#" for row in root.findall("connection")
               if row.get("tl") and road.get(row.get("from")) in nodes}
    return sorted(family)


def neighbourhood(root: ET.Element, edges: list[str], junctions: list[str], radius_m: float) -> list[str]:
    """Every non-internal junction within ``radius_m`` of the injected element's junctions (the declared ones and
    both ends of each declared edge): a K2 scope that does not end at the injected element."""
    positions = {row.get("id"): (float(row.get("x")), float(row.get("y"))) for row in root.findall("junction")
                 if row.get("type") != "internal" and row.get("x") is not None and row.get("y") is not None}
    centres = [positions[node] for node in build_profile.scope_junctions(root, edges, junctions) if node in positions]
    return sorted(node for node, (x, y) in positions.items()
                  if any(math.hypot(x - cx, y - cy) <= radius_m for cx, cy in centres))


def _in_family(anchor: str, family: list[str]) -> bool:
    return anchor in family or any(anchor.startswith(prefix) for prefix in family if prefix.endswith("#"))


def hotspot_checks(profile: dict, expect: dict, family: list[str]) -> dict:
    """Whether a faulted source's hotspots are exactly the injected elements: every hotspot (INT included) lies in
    the family, and the first target-class hotspot has the expected class, anchor and first suggested op (plus any
    ``also`` hotspot ids); an OPX case has no target hotspot and exactly the expected escalation."""
    hotspots = profile["hotspots"]
    targets = [h for h in hotspots if h["class"] != "INT"]
    escalations = [row["id"] for row in profile["escalations"]]
    result = {"hotspot_ids": profile["hotspot_ids"], "escalation_ids": escalations, "family": family,
              "outside_family": [h["id"] for h in hotspots if not _in_family(h["anchor"], family)],
              "int_rows": [row["id"] for row in profile["int_findings"]]}
    if "escalation" in expect:
        reasons = sorted({row["reason"] for group in profile["escalations"] for row in group["facts"]})
        result.update(target=None, reasons=reasons,
                      ok=not targets and escalations == [expect["escalation"]] and reasons == ["op_not_expressible"]
                      and not result["outside_family"])
        return result
    first = targets[0] if targets else None
    op = ((first["rows"][0].get("suggested_ops") or [{}])[0].get("op")) if first else None
    also = [hotspot for hotspot in expect.get("also", []) if hotspot not in profile["hotspot_ids"]]
    result.update(target={"id": first["id"], "class": first["class"], "anchor": first["anchor"], "op": op}
                  if first else None, missing_also=also,
                  ok=bool(first) and first["class"] == expect["class"] and first["anchor"] == expect["anchor"]
                  and op == expect["op"] and not also and not result["outside_family"] and not escalations)
    return result


def suggested_operation(profile: dict, expect: dict | None = None) -> tuple[dict | None, str | None]:
    """The first complete suggested op of the first target-class hotspot (only of the expected op type, and only
    from that first hotspot, when ``expect`` is given)."""
    for hotspot in profile["hotspots"]:
        if hotspot["class"] == "INT":
            continue
        for row in hotspot["rows"]:
            for op in row.get("suggested_ops") or []:
                if op.get("complete") and (expect is None or op["op"] == expect.get("op")):
                    return {"operation": op["op"], "params": op["params"]}, hotspot["id"]
        if expect is not None:
            return None, hotspot["id"]
    return None, None


def _resolve(params: dict, clean: ET.Element) -> dict:
    resolved = dict(params)
    if resolved.get("shape") == "reference_edge_shape":
        edge = next(row for row in clean.findall("edge") if row.get("id") == params["edge_id"])
        resolved["shape"] = [list(point) for point in _points(edge.get("shape", ""))]
    return resolved


def _ledger(path: Path, source: Path, identity: str, operation: dict) -> Path:
    payload = {"source_net_file": str(source), "operations": [
        {"id": identity, "operation": operation["operation"], "params": operation["params"]}]}
    return _write_new(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _step_record(profile: dict, operation: dict, basis: str) -> dict:
    gate = profile["gate"]
    return {"op": operation["operation"], "params": operation["params"], "basis": basis,
            "verify_edit_status": gate.get("verify_edit_status"), "status": profile["status"],
            "screening": gate["screening"], "G0": gate["G0"],
            "failed_invariants": {row["id"]: row["witness_ids"][:6] for row in profile["invariants"] if not row["pass"]},
            "outside_fact_changes": gate.get("outside_fact_changes"), "V": profile["V"]["cand"],
            "V_parent": profile["V"]["parent"], "V_verdict": gate["V"]["verdict"], "fixed": len(gate.get("fixed", [])),
            "cand": profile["cand"], "hotspot_ids": profile["hotspot_ids"]}


def _k2_case(repository_root: Path, *, case: dict, ledger: bool, clean: Path, clean_root: ET.Element,
             facts: Path, out_dir: Path, verifier: dict, netconvert_binary: str, timing: dict) -> dict:
    from ..core.network_edit_applicability import verify_network_edit

    case_dir = out_dir / "cases" / case["id"]
    fault: dict = {"label": LEDGER_LABEL if ledger else RAW_LABEL}
    tick = time.perf_counter()
    if ledger:
        operation = {"operation": case["operation"], "params": case["params"]}
        report = verify_network_edit(ledger_file=_ledger(case_dir / "fault.ledger.json", clean, f"{case['id']}-fault",
                                                         operation),
                                     output_dir=case_dir / "fault", netconvert_binary=netconvert_binary,
                                     mode="explore", facts_file=facts, routeability_seeds=())
        net = _write_new(case_dir / "faulted.net.xml", Path(report["candidate_net_file"]).read_bytes())
        targets = build_profile.op_targets(operation, clean_root)
        fault.update(op=case["operation"], params=case["params"], verify_edit_status=report["status"],
                     outside_fact_changes=report.get("outside_fact_changes"))
    else:
        net = _write_new(case_dir / "faulted.net.xml", _net_bytes(perturb_raw(clean_root, case)))
        edge = next(row.get("id") for row in clean_root.findall("edge")
                    for lane in row.findall("lane") if lane.get("id") == case["lane_id"])
        targets = {"edges": [edge], "junctions": []}
        fault.update(kind=case["kind"], lane_id=case["lane_id"],
                     **{key: case[key] for key in ("offset_m", "width_m") if key in case})
    timing[f"{case['id']}.fault_s"] = round(time.perf_counter() - tick, 3)
    fault["net_sha256"] = _sha(net.read_bytes())
    tick = time.perf_counter()
    scope = neighbourhood(clean_root, targets["edges"], targets["junctions"], K2_SCOPE_RADIUS_M)
    created, facts_bytes, summary = goal_module.create_build_goal(
        repository_root, net=net, reference_net=clean, site_id=K2_SITE, target_date=K2_TARGET_DATE,
        mutable_edge_ids=targets["edges"], mutable_junction_ids=sorted(set(scope) | set(targets["junctions"])),
        seeds=[42], scratch_dir=case_dir)
    goal_path, _, _ = goal_module.write_build_goal(created, facts_bytes, out_dir / "store")
    goal, goal_bytes, table_bytes, facts_path = goal_module.load_build_goal(goal_path)
    source = build_profile.write_build_profile(goal, goal_bytes, table_bytes, facts_path=facts_path,
                                               repository_root=repository_root, out_dir=case_dir / "source-profile",
                                               verifier=verifier)
    timing[f"{case['id']}.goal_and_source_profile_s"] = round(time.perf_counter() - tick, 3)
    hot = hotspot_checks(source, case["expect"], injected_family(clean_root, targets["edges"], targets["junctions"]))
    steps, parent_net, parent_profile = [], net, case_dir / "source-profile" / "profile.full.json"
    inverse_note = chain_stopped = None
    if "escalation" in case["expect"]:
        inverse_note = "op_not_expressible: no typed op sets one lane's shape; escalated, never a task"
        operation = None
    else:
        operation, hotspot = suggested_operation(source, case["expect"])
        basis = f"suggested_op of {hotspot}"
        if operation is None and case.get("inverse"):
            operation = {"operation": case["inverse"]["operation"],
                         "params": _resolve(case["inverse"]["params"], clean_root)}
            basis = f"declared inverse ({case['inverse']['why']})"
    for step in range(1, K2_MAX_STEPS + 1):
        if operation is None:
            break
        step_dir = case_dir / f"step-{step}"
        tick = time.perf_counter()
        build_profile.run_verify_edit(goal, ledger_file=_ledger(step_dir / "ledger.json", parent_net,
                                                                f"{case['id']}-step{step}", operation),
                                      facts_path=facts_path, output_dir=step_dir / "verify-edit",
                                      repository_root=repository_root, netconvert_binary=netconvert_binary,
                                      routeability_seeds=())
        timing[f"{case['id']}.step{step}.verify_edit_s"] = round(time.perf_counter() - tick, 3)
        tick = time.perf_counter()
        profile = build_profile.write_build_profile(
            goal, goal_bytes, table_bytes, facts_path=facts_path, repository_root=repository_root,
            out_dir=step_dir / "profile", verifier=verifier, verify_edit=step_dir / "verify-edit",
            parent_profile=parent_profile)
        timing[f"{case['id']}.step{step}.profile_s"] = round(time.perf_counter() - tick, 3)
        steps.append(_step_record(profile, operation, basis))
        if profile["V"]["cand"] == ZERO_V or profile["gate"]["G0"] != "pass":
            break
        if profile["gate"]["screening"] not in build_gate.ACCEPTED_SCREENINGS:
            # Only an accepted candidate may be the next parent (build_profile.verified_parent).
            chain_stopped = f"step {step} screened {profile['gate']['screening']}; it cannot be the next parent"
            break
        parent_net = Path(profile["inputs"]["net"]["path"])
        parent_profile = step_dir / "profile" / "profile.full.json"
        operation, hotspot = suggested_operation(profile)
        basis = f"suggested_op of {hotspot}"
    checks = {"hotspots_are_the_injected_elements": hot["ok"]}
    if ledger:
        checks["fault_step_measured_with_outside_fact_changes_0"] = (fault["verify_edit_status"] == "measured"
                                                                     and fault["outside_fact_changes"] == [])
    if inverse_note is None:
        checks["inverse_reaches_V_0"] = bool(steps) and steps[-1]["V"] == ZERO_V
        checks["every_step_outside_fact_changes_0"] = bool(steps) and all(
            row["outside_fact_changes"] == [] for row in steps)
        checks["every_step_passes_G0"] = bool(steps) and all(row["G0"] == "pass" for row in steps)
    return {"id": case["id"], "ledger": ledger, "fault": fault, "scope": {**targets, "junctions_in_scope": scope},
            "expect": case["expect"],
            "source": {"V": source["V"]["cand"], "status": source["status"], "counts": summary["counts"],
                       "excluded": source["excluded"], **hot},
            "steps": steps, "inverse_note": inverse_note, "chain_stopped": chain_stopped, "checks": checks,
            "status": "pass" if all(checks.values()) else "blocked"}


def _perf_internal_paths(repository_root: Path, *, clean: Path, clean_root: ET.Element, out_dir: Path,
                         verifier: dict, netconvert_binary: str, count: int = 800,
                         scope_edge: str = "-186713427") -> tuple[dict, dict]:
    """Acceptance (f): a goal of ``count`` two-lane internal-path centerline facts plus one scope-edge fact on Ring1,
    then the source profile and a judged verify-edit candidate profile (set_speed on the scope edge), timed."""
    from ..core.network_calibration import _saved_lane_paths

    lanes = {lane.get("id"): lane for edge in clean_root.findall("edge") for lane in edge.findall("lane")}
    paths = _saved_lane_paths(clean_root)
    unique = sorted(pair for pair, rows in paths.items() if len(rows) == 1)[:count]

    def fact(identity: str, selector: dict, points: list[tuple[float, float]]) -> dict:
        return {"id": identity, "kind": "lane_centerline", "selector": selector, "expected": [list(p) for p in points],
                "sample_spacing_m": 1.0, "tolerance_m": 0.5, "traffic_mode": "motor",
                "mapping_status": "confirmed", "mapping_confidence": "confirmed"}

    facts = []
    for index, pair in enumerate(unique):
        points = [point for lane_id in paths[pair][0] for point in _points(lanes[lane_id].get("shape", ""))]
        dedup = [points[0]] + [point for previous, point in zip(points, points[1:]) if point != previous]
        facts.append(fact(f"path{index:04d}", {"lane_ids": list(pair), "include_internal_paths": True}, dedup))
    facts.append(fact("scope-edge", {"lane_id": f"{scope_edge}_0"},
                      _points(lanes[f"{scope_edge}_0"].get("shape", ""))))
    table = {"schema": "astra.network-facts/v1", "site_id": "ring1-internal-paths", "target_date": K2_TARGET_DATE,
             "coordinate_system": {"type": "network_xy_m", "location": dict(clean_root.find("location").attrib)},
             "sources": [], "facts": facts}
    perf_dir = out_dir / "perf"
    table_file = _write_new(perf_dir / "internal-path-facts.json",
                            (json.dumps(table, sort_keys=True) + "\n").encode("utf-8"))
    created, facts_bytes, _ = goal_module.create_build_goal(repository_root, net=clean, facts=table_file,
                                                            mutable_edge_ids=[scope_edge], seeds=[42],
                                                            scratch_dir=perf_dir)
    goal_path, _, _ = goal_module.write_build_goal(created, facts_bytes, perf_dir / "store")
    goal, goal_bytes, table_bytes, facts_path = goal_module.load_build_goal(goal_path)
    timing = {}
    tick = time.perf_counter()
    source = build_profile.write_build_profile(goal, goal_bytes, table_bytes, facts_path=facts_path,
                                               repository_root=repository_root, out_dir=perf_dir / "source-profile",
                                               verifier=verifier)
    timing["source_profile_s"] = round(time.perf_counter() - tick, 3)
    operation = {"operation": "set_speed", "params": {"lane_id": f"{scope_edge}_0", "speed_m_s": 10.0}}
    tick = time.perf_counter()
    build_profile.run_verify_edit(goal, ledger_file=_ledger(perf_dir / "ledger.json", clean, "perf", operation),
                                  facts_path=facts_path, output_dir=perf_dir / "verify-edit",
                                  repository_root=repository_root, netconvert_binary=netconvert_binary,
                                  routeability_seeds=())
    timing["verify_edit_s"] = round(time.perf_counter() - tick, 3)
    tick = time.perf_counter()
    judged = build_profile.write_build_profile(goal, goal_bytes, table_bytes, facts_path=facts_path,
                                               repository_root=repository_root, out_dir=perf_dir / "judged-profile",
                                               verifier=verifier, verify_edit=perf_dir / "verify-edit",
                                               parent_profile=perf_dir / "source-profile" / "profile.full.json")
    timing["judged_profile_s"] = round(time.perf_counter() - tick, 3)
    record = {"facts": len(facts), "internal_path_facts": len(unique), "scored": len(goal.frozen.scored_facts),
              "source_V": source["V"]["cand"], "limit_s": PERF_LIMIT_S,
              "timed": "write_build_profile in-process (scoring, INT, structure, gate, views and files) of the source "
                       "profile and of the judged set_speed candidate; checked in timing_checks, never in this report",
              "judged": {"G0": judged["gate"]["G0"], "V": judged["V"]["cand"],
                         "screening": judged["gate"]["screening"],
                         "parent_scorecard": judged["inputs"]["parent_net"]["scorecard"]}}
    return record, timing


def timing_checks(timing: dict) -> dict[str, bool]:
    """Acceptance (f) on measured wall times: both the source and the judged 800 internal-path profile within the
    limit. Kept out of the deterministic report; a missing timing fails."""
    return {f"perf_{name}_within_{PERF_LIMIT_S:g}s": isinstance(timing.get(f"perf.{name}_s"), (int, float))
            and timing[f"perf.{name}_s"] <= PERF_LIMIT_S for name in ("source_profile", "judged_profile")}


def run_k2(out_dir: Path, *, repository_root: Path, verifier: dict, netconvert_binary: str = "netconvert",
           case_ids: tuple[str, ...] | None = None, perf: bool = True) -> dict:
    """K2 on the clean Ring1 nominal net into a NEW directory; returns the report (plus ``timing``)."""
    from ..core.reference_net_facts import write_reference_net_facts
    from .direction_check import check_connection_directions, direction_baseline

    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} exists; every harness run writes a new directory")
    data = (Path(repository_root) / RING1_NOMINAL).read_bytes()
    if not _sha(data).startswith(RING1_SHA256_PREFIX):
        raise ValueError(f"K2 refused: {RING1_NOMINAL} is not the pinned Ring1 nominal net ({RING1_SHA256_PREFIX}...)")
    resolved = shutil.which(netconvert_binary) or netconvert_binary
    out_dir.mkdir(parents=True)
    started, timing = time.perf_counter(), {}
    clean = _write_new(out_dir / "clean" / "network.net.xml", data)
    clean_root = ET.fromstring(data)
    control_scope = neighbourhood(clean_root, ["-186713427"], [], K2_SCOPE_RADIUS_M)
    facts = out_dir / "reference-facts.json"
    write_reference_net_facts(net_file=clean, output_file=facts, site_id=K2_SITE, target_date=K2_TARGET_DATE)
    direction = check_connection_directions(clean_root)
    baseline = direction_baseline(direction, _sha(data))
    tick = time.perf_counter()
    control = _goal_and_profile(repository_root, net=clean, reference=clean, site_id=K2_SITE, store=out_dir / "store",
                                out_dir=out_dir / "clean-control", verifier=verifier, mutable_edge_ids=["-186713427"],
                                mutable_junction_ids=control_scope, target_date=K2_TARGET_DATE)
    timing["clean_control_s"] = round(time.perf_counter() - tick, 3)
    selected = [(case, True) for case in K2_LEDGER_FAULTS] + [(case, False) for case in K2_RAW_FAULTS]
    if case_ids is not None:
        selected = [(case, ledger) for case, ledger in selected if case["id"] in case_ids]
    cases = [_k2_case(repository_root, case=case, ledger=ledger, clean=clean, clean_root=clean_root, facts=facts,
                      out_dir=out_dir, verifier=verifier, netconvert_binary=resolved, timing=timing)
             for case, ledger in selected]
    perf_record = None
    if perf:
        perf_record, perf_timing = _perf_internal_paths(repository_root, clean=clean, clean_root=clean_root,
                                                        out_dir=out_dir, verifier=verifier,
                                                        netconvert_binary=resolved)
        timing.update({f"perf.{key}": value for key, value in perf_timing.items()})
    checks = {f"{case['id']}:{name}": ok for case in cases for name, ok in case["checks"].items()}
    checks["clean_control_has_no_hotspots"] = (not control["hotspot_ids"] and not control["escalations"]
                                               and set(map(float, control["V"]["cand"])) == {0.0})
    checks["ring1_direction_baseline_frozen"] = direction["finding_count"] == len(baseline["finding_ids"])
    direction_record = {key: direction[key] for key in ("rule", "checked_count", "finding_count", "ambiguous_count",
                                                        "unsupported_count")}
    report = {
        "schema": SCHEMA, "harness": "k2", "status": "pass" if all(checks.values()) else "blocked",
        "checks": checks, "failed_checks": sorted(name for name, ok in checks.items() if not ok),
        "net": {"path": RING1_NOMINAL, "sha256": _sha(data), "lane_elements": sum(1 for _ in clean_root.iter("lane"))},
        "labels": {"ledger": LEDGER_LABEL, "raw": RAW_LABEL},
        "routeability": "not run (routeability_seeds=()): K2 does not exercise L3, so screening is dynamic_not_run",
        "timing_checks": (f"acceptance (f), both 800 internal-path profiles within {PERF_LIMIT_S:g} s, is evaluated "
                          "outside this deterministic report: run_k2's timing_checks, the CLI status and "
                          "k2-timing.json" if perf else "not run (--no-perf)"),
        "amendments": {key: HARNESS_AMENDMENTS[key] for key in ("K2-class-on-first-target", "K2-neighbourhood-scope")},
        "ring1_direction": {**direction_record, "baseline_sha256": baseline["sha256"],
                            "finding_ids": baseline["finding_ids"], "ambiguous_ids": baseline["ambiguous_ids"]},
        "clean_control": {"V": control["V"]["cand"], "hotspots": control["hotspot_ids"],
                          "escalations": [row["id"] for row in control["escalations"]],
                          "scored": sum(layer["scored"] for layer in control["layers"].values())},
        "cases": cases, "perf_internal_paths": perf_record, "claim": CLAIM}
    write_json_atomic(out_dir / "k2-report.json", report, sort_keys=True)
    timing["total_s"] = round(time.perf_counter() - started, 3)
    timed = timing_checks(timing) if perf else {}
    write_json_atomic(out_dir / "k2-timing.json", {**timing, "timing_checks": timed}, sort_keys=True)
    return {**report, "timing": timing, "timing_checks": timed}
