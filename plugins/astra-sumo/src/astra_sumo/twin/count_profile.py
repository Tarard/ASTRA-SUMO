"""Offline Calibrate count profile over one tracked SUMO run.

Score from the tracked E1 output, the free-flow decomposition against the lower-bound schedule a_v, the per-field
balance identity, route-matched exit-time localization with co-anchors, and typed integrity rows. Everything here is
accounting over simulated passages under the supplied model: it locates counts in time, it does not explain causes.
"""

from __future__ import annotations

import gzip
import io
import json
import math
import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

from ..core.artifact_io import write_json_atomic, write_text_atomic
from ..core.benchmark_references import (LOCAL_ONLY_FILE, BenchmarkReferenceError, PacketCase, ResolvedReference,
                                         csv_rows, local_only_row)
from ..core.candidate_contracts import file_sha256
from ..core.detector_demand import audit_expected_to_e1_strict, geh_value, summarize_comparison, write_csv
from . import bounds, ids, invariants, render

if TYPE_CHECKING:
    from .goal import CalibrateGoal

PROFILE_SCHEMA = "astra.twin-profile/v1"
TIME_EPS_S = 1e-9
COANCHOR_ABS_S = 15.0
COANCHOR_REL = 0.25
SPREAD_MIN_S = 1.0
ANCHORS_KEPT = 6
WINDOW_S = 60.0
HOLD_FLAG = "junction-body hold node: declared driver assumption"
E1_OUTPUT = "field-counts.xml"
INSTANT_E1 = "instant-e1.xml"
RUN_FILES = ("run.json", "native-request.json", "command.json", E1_OUTPUT, "vehicle-routes.xml", "e1.add.xml")
# A packet file that cannot be read through its references: missing (FileNotFoundError), undeclared or mismatched
# (BenchmarkReferenceError), or with a gzip layer that does not decode (gzip.BadGzipFile is an OSError; EOFError and
# zlib.error for a truncated or corrupt stream). INV-INPUTS names it instead of the profile failing on a raw error.
UNREADABLE = (OSError, EOFError, zlib.error, BenchmarkReferenceError)


@dataclass(frozen=True)
class Network:
    lanes: dict[str, tuple[float, float]]
    edge_lanes: dict[str, tuple[str, ...]]
    min_internal_s: dict[tuple[str, str], float]
    edge_to: dict[str, str]
    junction_type: dict[str, str]
    tls_links: dict[tuple[str, str], frozenset[tuple[str, str]]]
    tls_type: dict[str, str]

    def edge_free_flow_s(self, edge: str, *, start_m: float = 0.0) -> float:
        lanes = self.edge_lanes[edge]
        length = min(self.lanes[lane][0] for lane in lanes)
        return max(0.0, length - start_m) / max(self.lanes[lane][1] for lane in lanes)


@dataclass(frozen=True)
class Request:
    depart_s: float
    edges: tuple[str, ...]
    depart_pos_m: float


@dataclass(frozen=True)
class FieldLoop:
    lane: str
    pos_m: float
    period_s: float

    @property
    def edge(self) -> str:
        return self.lane.rsplit("_", 1)[0]


@dataclass(frozen=True)
class Route:
    depart_s: float
    edges: tuple[str, ...]
    exit_s: tuple[float, ...]


def parse_network(data: bytes, control: bytes | None = None) -> Network:
    root = ET.fromstring(data)
    lanes: dict[str, tuple[float, float]] = {}
    edge_lanes: dict[str, list[str]] = defaultdict(list)
    edge_to: dict[str, str] = {}
    for edge in root.findall("edge"):
        internal = edge.get("function") == "internal"
        for lane in edge.findall("lane"):
            lanes[lane.get("id")] = (float(lane.get("length")), float(lane.get("speed")))
            if not internal:
                edge_lanes[edge.get("id")].append(lane.get("id"))
        if not internal:
            edge_to[edge.get("id")] = edge.get("to")
    via_next: dict[str, str] = {}
    vias: dict[tuple[str, str], list[str | None]] = defaultdict(list)
    tls: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for connection in root.findall("connection"):
        source = connection.get("from")
        if source.startswith(":"):
            if connection.get("via"):
                via_next[f"{source}_{connection.get('fromLane')}"] = connection.get("via")
            continue
        pair = (source, connection.get("to"))
        vias[pair].append(connection.get("via"))
        if connection.get("tl"):
            tls[pair].add((connection.get("tl"), connection.get("linkIndex")))

    def internal_s(lane: str | None) -> float:
        total, seen = 0.0, set()
        while lane and lane not in seen:
            seen.add(lane)
            length, speed = lanes[lane]
            total += length / speed
            lane = via_next.get(lane)
        return total

    tls_type = {logic.get("id"): logic.get("type") for logic in root.findall("tlLogic")}
    if control is not None:
        tls_type.update({logic.get("id"): logic.get("type") for logic in ET.fromstring(control).iter("tlLogic")})
    return Network(lanes=lanes, edge_lanes={edge: tuple(values) for edge, values in edge_lanes.items()},
                   min_internal_s={pair: min(internal_s(via) for via in values) for pair, values in vias.items()},
                   edge_to=edge_to, junction_type={j.get("id"): j.get("type") for j in root.findall("junction")},
                   tls_links={pair: frozenset(values) for pair, values in tls.items()}, tls_type=tls_type)


def _route_edges(vehicle: ET.Element, source: str) -> tuple[ET.Element, tuple[str, ...]]:
    route = vehicle.find("route")
    edges = tuple((route.get("edges") or "").split()) if route is not None else ()
    if not edges:
        raise ValueError(f"{source}: vehicle {vehicle.get('id')!r} has no embedded route with edges")
    return route, edges


def _number(vehicle: ET.Element, attribute: str, source: str, default: str | None = None) -> float:
    value = vehicle.get(attribute, default)
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{source}: vehicle {vehicle.get('id')!r} needs a numeric {attribute}, has {value!r}") from error


def parse_demand(data: bytes) -> dict[str, Request]:
    requests = {}
    for vehicle in ET.fromstring(data).findall("vehicle"):
        requests[vehicle.get("id")] = Request(_number(vehicle, "depart", "demand"), _route_edges(vehicle, "demand")[1],
                                              _number(vehicle, "departPos", "demand (a_v)", "0"))
    return requests


def parse_field_loops(data: bytes) -> dict[str, FieldLoop]:
    return {loop.get("id"): FieldLoop(loop.get("lane"), float(loop.get("pos")), float(loop.get("period")))
            for loop in ET.fromstring(data).iter("inductionLoop")}


def parse_vehicle_routes(data: bytes) -> dict[str, Route]:
    routes = {}
    for vehicle in ET.fromstring(data).iter("vehicle"):
        route, edges = _route_edges(vehicle, "vehicle routes")
        try:
            exit_s = tuple(float(value) for value in (route.get("exitTimes") or "").split())
        except ValueError:
            exit_s = ()
        if len(exit_s) != len(edges):
            raise ValueError(f"vehicle routes: vehicle {vehicle.get('id')!r} has invalid exitTimes")
        routes[vehicle.get("id")] = Route(_number(vehicle, "depart", "vehicle routes"), edges, exit_s)
    return routes


def parse_intentions(data: bytes) -> dict[str, str]:
    return {row["vehicle_id"]: row["field_id"] for row in json.loads(data)["vehicles"]}


def free_flow_arrivals(network: Network, requests: dict[str, Request], intended: dict[str, str],
                       loops: dict[str, FieldLoop]) -> dict[str, float]:
    """a_v: scheduled depart plus free-flow time to the field E1 (shortest lane, highest speed, minimum internal path).

    Acceleration, yielding and insertion delay are ignored, so a_v is a lower bound on the passage time.
    """
    missing = sorted(set(intended) - set(requests))
    if missing:
        raise ValueError(f"{len(missing)} intentions have no demand vehicle, e.g. {missing[0]!r}; a_v is undefined")
    arrivals = {}
    for vehicle, field_id in sorted(intended.items()):
        if field_id not in loops:
            raise ValueError(f"vehicle {vehicle!r} intends field {field_id!r}, which has no field E1")
        request, loop = requests[vehicle], loops[field_id]
        edges, speed = request.edges, network.lanes[loop.lane][1]
        if loop.edge not in edges:
            raise ValueError(f"vehicle {vehicle!r}: its route never reaches {field_id!r} on edge {loop.edge!r}")
        k = edges.index(loop.edge)
        if k == 0:
            arrivals[vehicle] = request.depart_s + max(0.0, loop.pos_m - request.depart_pos_m) / speed
            continue
        time = request.depart_s + network.edge_free_flow_s(edges[0], start_m=request.depart_pos_m)
        for i in range(1, k + 1):
            time += network.min_internal_s.get((edges[i - 1], edges[i]), 0.0)
            if i < k:
                time += network.edge_free_flow_s(edges[i])
        arrivals[vehicle] = time + loop.pos_m / speed
    return arrivals


def incomplete_rows(rows: list[dict[str, str]]) -> list[str]:
    """Row ids whose source observations fall short of the expected count; such a row is never scored or imputed."""
    return [ids.row_id(row["detector_id"], float(row["begin"])) for row in rows
            if not row.get("source_observation_count")
            or row.get("source_observation_count") != row.get("expected_source_observation_count")]


def _comparisons(expected_rows: list[dict[str, str]], field_counts: bytes) -> list[dict[str, object]]:
    # audit_expected_to_e1_strict hands its detector_xml straight to ET.parse, which takes a file object as well as
    # the annotated Path, so the tracked E1 output is parsed once and never unpacked to disk.
    rows = audit_expected_to_e1_strict(expected_rows, io.BytesIO(field_counts), count_attribute="nVehContrib")
    missing = [row for row in rows if row["measured_nVehContrib"] is None]
    if missing:
        raise ValueError(f"{len(missing)} expected bins have no E1 measurement; a missing bin is unknown, not zero")
    return rows


def score_counts(main_rows: list[dict[str, str]], init_rows: list[dict[str, str]],
                 field_counts: bytes) -> tuple[dict, list[dict[str, object]]]:
    incomplete = incomplete_rows(main_rows + init_rows)
    if incomplete:
        raise ValueError(f"{len(incomplete)} rows have incomplete source observations, e.g. {incomplete[0]}; "
                         "an incomplete row needs a new goal, never imputation")
    compared = _comparisons(main_rows + init_rows, field_counts)
    main, init = compared[:len(main_rows)], compared[len(main_rows):]
    # summarize_comparison reads its measured column as "measured_nVehEntered"; the values passed are nVehContrib.
    summary = summarize_comparison([{"expected_total": row["expected_total"],
                                     "measured_nVehEntered": row["measured_nVehContrib"]} for row in main])
    diffs = [int(row["diff_nVehContrib_minus_expected"]) for row in main]
    geh = [geh_value(float(row["expected_total"]), float(row["measured_nVehContrib"])) for row in main]
    hourly = [2.0 * value for value in geh]
    init_tae = sum(abs(int(row["diff_nVehContrib_minus_expected"])) for row in init)
    score = {"rows_main": len(main), "rows_init": len(init), "tae_main": sum(map(abs, diffs)),
             "sse": sum(d * d for d in diffs), "mae": round(summary["MAE"], 9), "rmse": round(summary["RMSE"], 9),
             "max": summary["max_abs_error"], "exact": sum(d == 0 for d in diffs),
             "signed_bias": round(summary["signed_bias"], 9), "expected_total": summary["expected_total"],
             "measured_total": summary["measured_total"], "init52_tae": init_tae,
             "all182_tae": sum(map(abs, diffs)) + init_tae,
             "geh_raw": {"lt5_percent": round(summary["GEH_lt5_percent"], 2), "max": round(max(geh), 2)},
             "geh_hourly": {"lt5_percent": round(100.0 * sum(v < 5 for v in hourly) / len(hourly), 2),
                            "max": round(max(hourly), 2),
                            "basis": "2 x geh_value on 15-min counts (hourly flow = 4 x count)"}}
    rows = []
    for window, comparisons in (("init", init), ("main", main)):
        for row in comparisons:
            expected, measured = int(row["expected_total"]), int(row["measured_nVehContrib"])
            value = geh_value(float(expected), float(measured))
            rows.append({"row_id": ids.row_id(str(row["detector_id"]), float(row["begin"])),
                         "detector_id": row["detector_id"], "window": window, "begin_s": row["begin"],
                         "end_s": row["end"], "expected": expected, "measured": measured, "diff": measured - expected,
                         "geh": round(value, 6), "geh_hourly": round(2.0 * value, 6)})
    return score, rows


def _bin_begin(time_s: float, begin_s: float, period_s: float) -> float:
    return begin_s + math.floor((time_s - begin_s) / period_s) * period_s


def _bin_counts(counts: Counter) -> dict[str, int]:
    named = {f"{key:g}": counts[key] for key in sorted(key for key in counts if key != "unpassed")}
    return {**named, "unpassed": counts["unpassed"]} if "unpassed" in counts else named


def decompose(main_targets: dict[tuple[str, float], int], passages: list[dict], intended: dict[str, str],
              arrivals: dict[str, float], *, bin_begin_s: float, period_s: float) -> tuple[dict, list[dict]]:
    """Per main row: r = (|planned| - y) + late_in - delayed_out, against the free-flow schedule a_v."""
    measured: dict[tuple[str, float], set[str]] = defaultdict(set)
    passage_bin = {}
    for passage in passages:
        key = (passage["detector_id"], float(passage["interval_begin_s"]))
        measured[key].add(passage["vehicle_id"])
        passage_bin[(passage["vehicle_id"], passage["detector_id"])] = key[1]
    planned: dict[tuple[str, float], set[str]] = defaultdict(set)
    for vehicle, field_id in intended.items():
        planned[(field_id, _bin_begin(arrivals[vehicle], bin_begin_s, period_s))].add(vehicle)
    rows = []
    for (field_id, begin), y in sorted(main_targets.items()):
        m_set, p_set = measured[(field_id, begin)], planned[(field_id, begin)]
        ontime, late_in, delayed_out = len(m_set & p_set), len(m_set - p_set), len(p_set - m_set)
        supply, residual = len(p_set) - y, len(m_set) - y
        bins = Counter(passage_bin.get((vehicle, field_id), "unpassed") for vehicle in p_set - m_set)
        rows.append({"row_id": ids.row_id(field_id, begin), "detector_id": field_id, "begin_s": begin, "y": y,
                     "planned": len(p_set), "measured": len(m_set), "ontime": ontime, "late_in": late_in,
                     "delayed_out": delayed_out, "supply": supply, "r": residual,
                     "identity_closes": residual == supply + late_in - delayed_out,
                     "delayed_out_measured_in": _bin_counts(bins)})
    intended_passages = [p for p in passages if intended.get(p["vehicle_id"]) == p["detector_id"]]
    delays = sorted(p["time_s"] - arrivals[p["vehicle_id"]] for p in intended_passages)
    quantile = (lambda share: round(delays[min(len(delays) - 1, int(share * len(delays)))], 3)) if delays else None
    summary = {"kind": "accounting identity against the free-flow lower-bound schedule",
               "ontime": sum(r["ontime"] for r in rows), "late_in": sum(r["late_in"] for r in rows),
               "delayed_out": sum(r["delayed_out"] for r in rows),
               "supply_term_abs": sum(abs(r["supply"]) for r in rows),
               "identity_tae": sum(abs(r["r"]) for r in rows),
               "identity_rows": f"{sum(r['identity_closes'] for r in rows)}/{len(rows)}",
               "ff_violations": sum(p["time_s"] < arrivals[p["vehicle_id"]] - TIME_EPS_S for p in intended_passages),
               "passages": len(intended_passages),
               "delay_vs_ff_s": {name: quantile(share) if quantile else None
                                 for name, share in (("p50", 0.5), ("p90", 0.9), ("p99", 0.99))}}
    return summary, rows


def field_balance(fields: list[str], intended: dict[str, str], passages: list[dict], init_targets: dict[str, int],
                  main_targets: dict[str, int], arrivals: dict[str, float], *, init_end_s: float,
                  main_end_s: float) -> tuple[dict, list[dict]]:
    """N_f = M_init,f + M_main,f + post_f + unpassed_f, so R_init,f + R_main,f = -(deficit_f + post_f + unpassed_f).

    A field E1 without target rows in a window has an unknown target there, never zero, so it is refused.
    """
    untargeted = [f"{field_id} ({window})" for field_id in fields
                  for window, targets in (("init", init_targets), ("main", main_targets)) if field_id not in targets]
    if untargeted:
        raise ValueError(f"field E1 without target rows: {', '.join(untargeted)}; a missing interval is unknown")
    vehicles: dict[str, list[str]] = defaultdict(list)
    for vehicle, field_id in intended.items():
        vehicles[field_id].append(vehicle)
    own = defaultdict(list)
    for passage in passages:
        if intended.get(passage["vehicle_id"]) == passage["detector_id"]:
            own[passage["detector_id"]].append(passage)
    rows = []
    for field_id in fields:
        found = own[field_id]
        begins = [float(p["interval_begin_s"]) for p in found]
        n_requests = len(vehicles[field_id])
        e_init, e_main = init_targets[field_id], main_targets[field_id]
        m_init = sum(b < init_end_s for b in begins)
        m_main = sum(init_end_s <= b < main_end_s for b in begins)
        post = sum(b >= main_end_s for b in begins)
        unpassed = n_requests - len({p["vehicle_id"] for p in found})
        deficit = e_init + e_main - n_requests
        r_init, r_main = m_init - e_init, m_main - e_main
        rows.append({"field": ids.detector_alias(field_id), "detector_id": field_id, "requests": n_requests,
                     "e_init": e_init, "e_main": e_main, "m_init": m_init, "m_main": m_main, "post_window": post,
                     "unpassed": unpassed, "deficit": deficit, "r_init": r_init, "r_main": r_main,
                     "identity_closes": r_init + r_main == -(deficit + post + unpassed),
                     "u_init_end": sum(arrivals[v] < init_end_s for v in vehicles[field_id]),
                     "u_main_end": sum(arrivals[v] < main_end_s for v in vehicles[field_id])})
    summary = {"identity": "R_init + R_main = -(deficit + post + unpassed)",
               "requests": sum(r["requests"] for r in rows), "targets": sum(r["e_init"] + r["e_main"] for r in rows),
               "deficit_total": sum(r["deficit"] for r in rows), "post_window": sum(r["post_window"] for r in rows),
               "unpassed": sum(r["unpassed"] for r in rows),
               "identity_fields": f"{sum(r['identity_closes'] for r in rows)}/{len(rows)}",
               "ff_init_equals_init_targets": f"{sum(r['u_init_end'] == r['e_init'] for r in rows)}/{len(rows)}",
               "ff_reach_before_main_end": f"{sum(r['u_main_end'] == r['requests'] for r in rows)}/{len(rows)}"}
    return summary, rows


def _excess_profile(network: Network, request: Request, route: Route, loop: FieldLoop,
                    passage_s: float) -> dict[str, float]:
    """Time lost per anchor against free flow; the anchors sum to passage time minus a_v."""
    excess = {"insertion": route.depart_s - request.depart_s}
    previous = route.depart_s
    for i, edge in enumerate(route.edges):
        internal = network.min_internal_s.get((route.edges[i - 1], edge), 0.0) if i else 0.0
        start = request.depart_pos_m if i == 0 else 0.0
        if edge == loop.edge:
            free = max(0.0, loop.pos_m - start) / network.lanes[loop.lane][1]
            excess[edge] = passage_s - previous - internal - free
            break
        excess[edge] = route.exit_s[i] - previous - internal - network.edge_free_flow_s(edge, start_m=start)
        previous = route.exit_s[i]
    return excess


def _anchor_times(route: Route, loop: FieldLoop, passage_s: float) -> dict[str, float]:
    """When each anchor's time loss ends: the actual insertion, each edge exit and, on the field edge, the passage."""
    times = {"insertion": route.depart_s}
    for edge, exit_s in zip(route.edges, route.exit_s):
        if edge == loop.edge:
            times[edge] = passage_s
            break
        times[edge] = exit_s
    return times


def _anchor_flags(network: Network, prefix: tuple[str, ...], index: int, hold_nodes: set[str]) -> tuple[str, list]:
    edge = prefix[index]
    if index == len(prefix) - 1:
        return "", []
    node, following = network.edge_to.get(edge, ""), prefix[index + 1]
    flags = [HOLD_FLAG] if node in hold_nodes else []
    links = network.tls_links.get((edge, following), frozenset())
    for tls_id in sorted({tls for tls, _ in links}):
        count = sum(tls == tls_id for tls, _ in links)
        kind = network.tls_type.get(tls_id) or "unknown"
        flags.append(f"{kind} TLS {tls_id}: {count} candidate links to {following}")
    return node, flags


def localize_row(row: dict, *, network: Network, requests: dict[str, Request], routes: dict[str, Route],
                 loop: FieldLoop, passage: dict[tuple[str, str], dict], planned: list[str],
                 hold_nodes: set[str]) -> dict:
    """Contrast delayed_out vehicles with on-time peers of the same planned bin and the same route prefix."""
    field_id, begin = row["detector_id"], row["begin_s"]
    result = {"row_id": row["row_id"], "r": row["r"], "delayed_out": row["delayed_out"], "anchors": [],
              "coanchors": [], "hotspot_id": None, "next": None, "spread": None}
    on_time = [v for v in planned if (v, field_id) in passage and passage[(v, field_id)]["interval_begin_s"] == begin]
    delayed = [v for v in planned if (v, field_id) in passage and passage[(v, field_id)]["interval_begin_s"] != begin]

    def prefix(vehicle: str) -> tuple[str, ...]:
        edges = routes[vehicle].edges
        return edges[: edges.index(loop.edge) + 1]

    if not delayed:
        return {**result, "localization": "unmatched", "reason": "no passed delayed_out vehicle"}
    counts = Counter(prefix(v) for v in delayed)
    route = min(counts, key=lambda item: (-counts[item], item))
    group_d = [v for v in delayed if prefix(v) == route]
    group_o = [v for v in on_time if prefix(v) == route]
    excluded = Counter(routes[v].edges[0] for v in on_time if prefix(v) != route)
    result.update(entry=route[0], n_delayed=len(group_d), n_peer=len(group_o),
                  n_delayed_other_prefix=len(delayed) - len(group_d),
                  n_delayed_unpassed=row["delayed_out"] - len(delayed),
                  excluded_peers_by_entry=dict(sorted(excluded.items())), route_prefix_edges=len(route))
    if not group_o:
        return {**result, "localization": "unmatched", "reason": "no route-matched on-time peer"}
    profiles = {v: _excess_profile(network, requests[v], routes[v], loop, passage[(v, field_id)]["time_s"])
                for v in group_d + group_o}
    times = {v: _anchor_times(routes[v], loop, passage[(v, field_id)]["time_s"]) for v in group_d}
    anchors = []
    for index, name in enumerate(("insertion",) + route):
        mean_d = sum(profiles[v][name] for v in group_d) / len(group_d)
        mean_o = sum(profiles[v][name] for v in group_o) / len(group_o)
        at_s = sum(times[v][name] for v in group_d) / len(group_d)
        node, flags = ("", []) if name == "insertion" else _anchor_flags(network, route, index - 1, hold_nodes)
        anchors.append({"anchor": name, "element": ids.element_id("entry", route[0]) if name == "insertion"
                        else ids.element_id("edge", name), "node": node,
                        "node_type": network.junction_type.get(node, "") if node else "",
                        "window_s": int(math.floor(at_s / WINDOW_S) * WINDOW_S), "diff": mean_d - mean_o,
                        "mean_delayed": mean_d, "mean_peer": mean_o, "flags": flags})
    top = max(a["diff"] for a in anchors)
    if top <= 0:
        return {**result, "localization": "no_positive_differential", "reason": "no anchor loses more time than peers",
                "anchors": _rounded(anchors, ANCHORS_KEPT)}
    margin = max(COANCHOR_ABS_S, COANCHOR_REL * top)
    co = [a for a in anchors if a["diff"] > 0 and a["diff"] >= top - margin]
    co_names = {a["anchor"] for a in co}
    elements = sorted(a["element"] for a in co)
    hotspot = ids.coanchor_hotspot_id(elements) if len(co) > 1 else ids.hotspot_id("LOC", elements[0])
    ranked = sorted(anchors, key=lambda a: (-a["diff"], a["element"]))
    following = next((a for a in ranked if a["anchor"] not in co_names), None)
    result.update(localization="localized", hotspot_id=hotspot, anchors_all=anchors,
                  anchors=_rounded(ranked, ANCHORS_KEPT),
                  coanchors=_rounded(sorted(co, key=lambda a: (-a["diff"], a["element"])), len(co)),
                  next=_rounded([following], 1)[0] if following else None, spread=_spread(anchors),
                  delayed_measured_in=_bin_counts(Counter(passage[(v, field_id)]["interval_begin_s"]
                                                          for v in group_d)))
    return result


def _rounded(anchors: list[dict], limit: int) -> list[dict]:
    return [{**a, "diff": round(a["diff"], 1), "mean_delayed": round(a["mean_delayed"], 1),
             "mean_peer": round(a["mean_peer"], 1)} for a in anchors[:limit]]


def _spread(anchors: list[dict]) -> dict | None:
    """The run of consecutive route edges around the top edge anchor whose differential exceeds SPREAD_MIN_S."""
    edges = [a for a in anchors if a["anchor"] != "insertion"]
    if not edges:
        return None
    top = max(range(len(edges)), key=lambda i: edges[i]["diff"])
    if edges[top]["diff"] < max(a["diff"] for a in anchors) or edges[top]["diff"] <= SPREAD_MIN_S:
        return None
    first = last = top
    while first > 0 and edges[first - 1]["diff"] > SPREAD_MIN_S:
        first -= 1
    while last + 1 < len(edges) and edges[last + 1]["diff"] > SPREAD_MIN_S:
        last += 1
    downstream = edges[last + 1:]
    return {"from": edges[first]["anchor"], "to": edges[last]["anchor"], "edges": last - first + 1,
            "sum_s": round(sum(a["diff"] for a in edges[first:last + 1]), 1),
            "downstream_sum_s": round(sum(a["diff"] for a in downstream), 1),
            "downstream": [[a["anchor"], round(a["diff"], 1)] for a in downstream]}


_WARNINGS = {
    "emergency_braking": re.compile(r"Vehicle '([^']+)' performs emergency braking on lane '([^']+)'"),
    "actuated_phase_without_controlling_detector": re.compile(
        r"At actuated tlLogic '([^']+)', actuated phase (\d+) has no controlling detector"),
    "missing_green_phase": re.compile(
        r"Missing green phase in tlLogic '([^']+)', program '([^']+)' for tl-index (\d+)"),
    "unused_states": re.compile(r"Unused states in tlLogic '([^']+)', program '([^']+)' in phase (\d+)"),
}


def integrity_rows(command: dict, run: dict, passages_report: dict, intended: dict[str, str]) -> dict:
    """Typed SUMO warnings and observation-contract rows; reported, never part of the health predicate."""
    found: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    unclassified = 0
    for line in command.get("stderr", "").splitlines():
        if not line.startswith("Warning:"):
            continue
        for kind, pattern in _WARNINGS.items():
            match = pattern.search(line)
            if match:
                found[kind].append(match.groups())
                break
        else:
            unclassified += 1
    actuated = found["actuated_phase_without_controlling_detector"]
    stated = re.search(r"Emergency Braking:\s*(\d+)", command.get("stdout", ""))
    early = passages_report.get("early_departures", [])
    completion = run.get("completion", {})
    return {"healthy": bool(run.get("healthy")), "native_status": run.get("native_status"), "completion": completion,
            "arrived": f"{completion.get('arrived')}/{completion.get('loaded')}",
            "emergency_braking": {"count": len(found["emergency_braking"]),
                                  "summary_count": int(stated[1]) if stated else None,
                                  "witness_ids": sorted({g[0] for g in found["emergency_braking"]})},
            "actuated_phase_without_controlling_detector": {
                "warnings": len(actuated), "phases": len(set(actuated)), "tl_logics": len({g[0] for g in actuated}),
                "reports_per_phase": sorted(set(Counter(actuated).values())),
                "witness_ids": sorted(f"{tls}#phase{phase}" for tls, phase in set(actuated))},
            "missing_green_phase": {"count": len(found["missing_green_phase"]),
                                    "witness_ids": sorted(f"{t}@{p}#{i}" for t, p, i in found["missing_green_phase"])},
            "unused_states": {"count": len(found["unused_states"]),
                              "witness_ids": sorted(f"{t}@{p}#phase{i}" for t, p, i in found["unused_states"])},
            "unclassified_warnings": unclassified,
            "early_departures": {"count": len(early),
                                 "intended": sum(intended.get(e["vehicle_id"]) == e["detector_id"] for e in early),
                                 "rule": "leave events without occupancy never count (nVehContrib)"}}


def run_field_loop_sha(run: dict, name: str = "field-loops.add.xml") -> str | None:
    for row in run.get("inputs", []):
        if PureWindowsPath(row.get("path", "")).name == name:
            return row.get("sha256")
    return None


def passage_report(data: bytes) -> dict:
    """The archived report is itself a ``.json.gz``; the packet tracks it once more as ``.gz.gz``."""
    return json.loads(gzip.decompress(data))


def read_passage_report(reader: PacketCase, relative: str) -> dict:
    return passage_report(reader.read(relative))


CANNOT_IDENTIFY = ["the lane used", "the TLS link when an edge pair has more than one controlled connection",
                   "red-light waiting vs spillback from downstream",
                   "insertion capacity vs spillback onto the entry edge",
                   "a queue moving along the approach vs a downstream bottleneck", "any causal effect"]
CLAIM = ("Accounting over simulated passages under the supplied model and declared driver holds; no cause, no field "
         "SPaT, no OD identification.")


def rank_hotspots(localized: list[dict], decomposition: list[dict], balance: list[dict]) -> list[dict]:
    """Group localized rows by anchor set; weight = the under-count its analyzed vehicles can explain.

    A row weighs min(-r, n_delayed): only the delayed_out vehicles of the route-prefix group the anchors were
    measured on count, never those on another prefix or unpassed, so no hotspot claims a vehicle it did not localize.
    Field-balance hotspots weigh |R_main,f|, the floor of that field's main-window TAE.
    """
    rows = {row["row_id"]: row for row in decomposition}
    groups: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for loc in localized:
        if loc["localization"] == "localized":
            groups[loc["hotspot_id"]].append((min(max(-rows[loc["row_id"]]["r"], 0), loc["n_delayed"]), loc))
    hotspots = []
    for hotspot, members in groups.items():
        members.sort(key=lambda item: (-item[0], -item[1]["n_delayed"], item[1]["row_id"]))
        lead = members[0][1]
        kind = "entry" if len(lead["coanchors"]) == 1 and lead["coanchors"][0]["anchor"] == "insertion" else "edge"
        cannot_tell, knob = render.LOC_TEXT[kind]
        summary = [{"row_id": loc["row_id"], "weight": weight,
                    **{key: rows[loc["row_id"]][key] for key in ("r", "y", "planned", "measured", "ontime",
                                                                 "late_in", "delayed_out")},
                    "n": f"{loc['n_delayed']}/{loc['n_peer']}", "analyzed": loc["n_delayed"],
                    "excluded": loc["n_delayed_other_prefix"] + loc["n_delayed_unpassed"],
                    "top_diff_s": loc["coanchors"][0]["diff"]}
                   for weight, loc in members]
        hotspots.append({"id": hotspot, "kind": "LOC", "class": kind, "weight": sum(weight for weight, _ in members),
                         "rows": summary,
                         "lead": {key: lead[key] for key in ("row_id", "entry", "delayed_out", "n_delayed", "n_peer",
                                                             "n_delayed_other_prefix", "n_delayed_unpassed",
                                                             "excluded_peers_by_entry", "route_prefix_edges",
                                                             "coanchors", "next", "spread", "delayed_measured_in")},
                         "cannot_tell": cannot_tell, "knob": knob, "conf": "accounting"})
    for row in balance:
        if row["deficit"] + row["post_window"] + row["unpassed"] > 0:
            hotspots.append({
                "id": ids.hotspot_id("BAL", ids.element_id("field", row["field"])), "kind": "BAL", "class": "field",
                "weight": abs(row["r_main"]),
                "rows": [{key: row[key] for key in ("field", "r_init", "r_main", "deficit", "post_window",
                                                    "unpassed")}],
                "reading": (f"main {row['r_main']:+d} = -(deficit {row['deficit']} + post-window {row['post_window']}"
                            f" + unpassed {row['unpassed']}) + init shortfall {-row['r_init']}"),
                "knob": "quantity (solver) or timing", "conf": "accounting"})
    return sorted(hotspots, key=lambda h: (-h["weight"], h["id"]))


def _read_inputs(goal: CalibrateGoal, reader: PacketCase) -> tuple[dict[str, bytes], dict[str, str | None]]:
    data: dict[str, bytes] = {}
    resolved: dict[str, str | None] = {}
    for name in goal.inputs.files:
        try:
            reference = reader.resolve(f"prepared/{name}")
        except UNREADABLE:
            resolved[name] = None
            continue
        resolved[name] = reference.sha256 if reference.scope != "direct" else None
        data[name] = reference.data
    return data, resolved


def _read_run_files(goal: CalibrateGoal, reader: PacketCase,
                    run_dir: str) -> tuple[dict[str, ResolvedReference], dict[str, bool], dict[str, bool]]:
    """Every baseline-run artifact the profile reads, whether the packet pins the bytes read for each, and whether
    those tracked bytes equal the blob committed at HEAD. A missing, undeclared or undecodable artifact is unpinned."""
    names = {name: reader.key(f"{run_dir}/{name}") for name in RUN_FILES}
    names["passages"] = reader.key(goal.inputs.baseline_run.passages)
    names[LOCAL_ONLY_FILE] = LOCAL_ONLY_FILE
    files: dict[str, ResolvedReference] = {}
    pinned: dict[str, bool] = {}
    for name, relative in names.items():
        try:
            files[name] = reader.resolve_root(relative)
        except UNREADABLE:
            pinned[relative] = False
            continue
        pinned[relative] = reader.pinned(files[name])
    return files, pinned, reader.committed(files.values())


def build_calibrate_profile(goal: CalibrateGoal, goal_bytes: bytes, reader: PacketCase, *, run_dir: str,
                            verifier: dict) -> tuple[dict, dict[str, list[dict]]]:
    """Profile the goal's baseline run from tracked files; return the full profile and its detail tables."""
    if run_dir != goal.inputs.baseline_run.dir:
        raise ValueError(f"M1 profiles only the goal's archived baseline run ({goal.inputs.baseline_run.dir}); "
                         "other runs need read_contributing_e1_passages on their own instant-E1 output (M2)")
    data, resolved = _read_inputs(goal, reader)
    run_files, pinned, committed = _read_run_files(goal, reader, run_dir)
    input_check = invariants.inv_inputs(goal.inputs.files, resolved, pinned, committed)
    if not input_check["pass"]:
        raise ValueError(f"profile refused: INV-INPUTS failed for {input_check['witness_ids']}")
    run, request, command = (json.loads(run_files[name].data) for name in
                             ("run.json", "native-request.json", "command.json"))
    report = passage_report(run_files["passages"].data)
    instant = local_only_row(run_files[LOCAL_ONLY_FILE].data, reader.key(f"{run_dir}/{INSTANT_E1}"))
    network = parse_network(data["network.net.xml"], data["control.add.xml"])
    requests = parse_demand(data["demand.rou.xml"])
    loops = parse_field_loops(data["field-loops.add.xml"])
    intended = parse_intentions(data["lane-intentions.json"])
    main_rows, init_rows = csv_rows(data["expected-counts.csv"]), csv_rows(data["initialization-counts.csv"])
    hold_nodes = list(goal.run.junction_body_hold_nodes)
    routes = parse_vehicle_routes(run_files["vehicle-routes.xml"].data)
    score, score_rows = score_counts(main_rows, init_rows, run_files[E1_OUTPUT].data)
    bindings = {E1_OUTPUT: (run_files[E1_OUTPUT].sha256, "the profiled E1 output"),
                INSTANT_E1: (instant["sha256"] if instant else None,
                             f"{LOCAL_ONLY_FILE}; that file is local-only, its bytes are not in the repository and "
                             "are not re-read, so the passage identities are the archived report's")}
    checks = [input_check,
              invariants.inv_obs(goal_loops=loops, run_loops=parse_field_loops(run_files["e1.add.xml"].data),
                                 goal_attribute=goal.observation.attribute,
                                 run_attributes=[run.get("measurement_attribute"), report.get("measurement_attribute")],
                                 goal_loops_sha256=goal.inputs.files["field-loops.add.xml"],
                                 run_loops_sha256=run_field_loop_sha(run)),
              invariants.inv_hold(hold_nodes, invariants.hold_node_sources(run, request)),
              invariants.inv_pass(report, intended, bindings=bindings,
                                  e1_counts={(str(row["detector_id"]), float(row["begin_s"])): int(row["measured"])
                                             for row in score_rows},
                                  routes=routes, field_edges={name: loop.edge for name, loop in loops.items()},
                                  e1_begin_s=goal.observation.e1_begin_s, e1_period_s=goal.observation.e1_period_s,
                                  step_s=goal.run.step_s)]
    arrivals = free_flow_arrivals(network, requests, intended, loops)
    passages = report["passages"]
    begin_s, period_s = goal.observation.e1_begin_s, goal.observation.e1_period_s
    decomp, decomp_rows = decompose({(r["detector_id"], float(r["begin"])): int(r["expected_total"])
                                     for r in main_rows}, passages, intended, arrivals,
                                    bin_begin_s=begin_s, period_s=period_s)
    targets: dict[str, dict[str, int]] = {"init": defaultdict(int), "main": defaultdict(int)}
    main_bins: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for window, rows in (("init", init_rows), ("main", main_rows)):
        for r in rows:
            targets[window][r["detector_id"]] += int(r["expected_total"])
    for r in main_rows:
        main_bins[r["detector_id"]].append((float(r["end"]), int(r["expected_total"])))
    balance, balance_rows = field_balance(
        sorted(loops), intended, passages, dict(targets["init"]), dict(targets["main"]), arrivals,
        init_end_s=min(float(r["begin"]) for r in main_rows), main_end_s=max(float(r["end"]) for r in main_rows))
    by_field: dict[str, list[float]] = defaultdict(list)
    planned: dict[tuple[str, float], list[str]] = defaultdict(list)
    for vehicle, field_id in sorted(intended.items()):
        by_field[field_id].append(arrivals[vehicle])
        planned[(field_id, _bin_begin(arrivals[vehicle], begin_s, period_s))].append(vehicle)
    e1_init: dict[str, int] = defaultdict(int)
    for row in score_rows:
        if row["window"] == "init":
            e1_init[str(row["detector_id"])] += int(row["measured"])
    demand_match = {"demand": len(requests), "intentions": len(intended),
                    "demand_without_intention": len(set(requests) - set(intended)),
                    "intention_without_demand": len(set(intended) - set(requests))}
    b1 = bounds.request_floor_b1(balance_rows, demand_match=demand_match, invariants=checks,
                                 all182_tae=score["all182_tae"])
    b2 = bounds.main_supply_b2(balance_rows, main_bins, by_field, intended_vehicles=len(intended), e1_init=e1_init,
                               ff_violations=decomp["ff_violations"], passages=decomp["passages"], invariants=checks,
                               main_tae=score["tae_main"])

    passage_of = {(p["vehicle_id"], p["detector_id"]): p for p in passages}
    localized = [localize_row(row, network=network, requests=requests, routes=routes, loop=loops[row["detector_id"]],
                              passage=passage_of, planned=planned[(row["detector_id"], row["begin_s"])],
                              hold_nodes=set(hold_nodes))
                 for row in decomp_rows if row["delayed_out"] > 0]
    localization_rows = [
        {"row_id": loc["row_id"], "hotspot_id": loc["hotspot_id"], "anchor": a["anchor"], "element": a["element"],
         "node": a["node"], "node_type": a["node_type"], "window_s": a["window_s"], "diff_s": round(a["diff"], 3),
         "mean_delayed_s": round(a["mean_delayed"], 3), "mean_peer_s": round(a["mean_peer"], 3),
         "n_delayed": loc["n_delayed"], "n_peer": loc["n_peer"],
         "coanchor": a["anchor"] in {c["anchor"] for c in loc["coanchors"]}, "flags": "; ".join(a["flags"])}
        for loc in localized for a in loc.get("anchors_all", [])]
    files = goal.inputs.files
    profile = {
        "schema": PROFILE_SCHEMA, "kind": "calibrate", "view": "full",
        "status": "measured" if all(check["pass"] for check in checks) else "blocked",
        "goal": ids.goal_reference(goal.goal_id, goal_bytes), "goal_id": goal.goal_id, "verifier": verifier,
        "cand": ids.candidate_id(files["network.net.xml"], files["control.add.xml"], files["demand.rou.xml"],
                                 files["field-loops.add.xml"]),
        "parent": None,
        "run": {"ref": f"packet:{run_dir}", "files": {name: {"tracked": reader.tracked_name(ref), "sha256": ref.sha256}
                                                       for name, ref in run_files.items()}},
        "inputs": {"resolved": f"{sum(v is not None for v in resolved.values())}/{len(files)} aliases, "
                               "sha256 verified",
                   "files": files, "hold_nodes": hold_nodes,
                   "passages": "archived report, " + ("reconciled" if checks[-1]["pass"] else "NOT reconciled")},
        "invariants": checks, "score": score, "integrity": integrity_rows(command, run, report, intended),
        "decomp": decomp,
        "field_balance": {**balance, "fields": {row["field"]: {key: row[key] for key in (
            "requests", "e_init", "e_main", "m_init", "m_main", "r_init", "r_main", "deficit", "post_window",
            "unpassed", "identity_closes")} for row in balance_rows}},
        "bounds": {"use": bounds.USE, "B1_all182_request_floor": b1, "B2_main_supply": b2,
                   "SOL": [bounds.speed_of_light(statistic="TAE_all182", parent=score["all182_tae"],
                                                 candidate=score["all182_tae"], bound=b1),
                           bounds.speed_of_light(statistic="TAE_main130", parent=score["tae_main"],
                                                 candidate=score["tae_main"], bound=b2)],
                   "surrogate": "MILP over observed assignment fractions: ranking only; not computed in M1"},
        "localization": {"method": "exit-time excess against free flow; delayed_out vs on-time peers of the same "
                                   "planned bin and route prefix; co-anchors within max(15 s, 25% of top)",
                         "anchor": "(edge or entry, downstream node, 60-s window_s of the delayed group's mean time "
                                   "there); a hotspot groups rows by co-anchor elements across bins and windows, so "
                                   "its id persists across rounds",
                         "cannot_identify": CANNOT_IDENTIFY,
                         "rows": [{k: v for k, v in loc.items() if k != "anchors_all"} for loc in localized]},
        "hotspots": rank_hotspots(localized, decomp_rows, balance_rows), "claim": CLAIM}
    details = {"rows.csv": score_rows,
               "decomposition.csv": [{**row, "delayed_out_measured_in": " ".join(
                   f"{k}:{v}" for k, v in row["delayed_out_measured_in"].items())} for row in decomp_rows],
               "field-balance.csv": balance_rows, "localization.csv": localization_rows}
    return profile, details


def write_calibrate_profile(goal: CalibrateGoal, goal_bytes: bytes, reader: PacketCase, *, run_dir: str,
                            out_dir: Path, verifier: dict) -> dict:
    """Write a NEW profile directory (full JSON, capped compact JSON and text, detail CSVs); refuse an existing one.

    Every file goes into a sibling staging directory that is renamed into place only once complete, so a failure
    never leaves a partial profile behind and never blocks a rerun to the same ``out_dir``.
    """
    started = time.perf_counter()
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} exists; every profile is written to a new directory")
    profile, details = build_calibrate_profile(goal, goal_bytes, reader, run_dir=run_dir, verifier=verifier)
    compact = dict(zip(("profile.compact.json", "profile.compact.txt"), render.render_compact(profile)))
    write_staged_profile(out_dir, profile, details, compact, started=started, empty_header="row_id")
    return profile


def write_staged_profile(out_dir: Path, profile: dict, details: dict[str, list[dict]], compact: dict[str, str], *,
                         started: float, empty_header: str) -> None:
    """Write the detail CSVs, the compact views and ``profile.full.json`` (with their hashes and the profile time
    since ``started``) into a sibling staging directory, renamed to ``out_dir`` only once complete."""
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.tmp-", dir=out_dir.parent))
    try:
        profile["details"] = {}
        for name, rows in details.items():
            write_csv(staging / name, rows, list(rows[0]) if rows else [empty_header])
            profile["details"][name] = {"path": name, "sha256": file_sha256(staging / name), "rows": len(rows)}
        for name, text in compact.items():
            write_text_atomic(staging / name, text)
        profile["compact"] = {name: {"path": name, "sha256": file_sha256(staging / name), "characters": len(text)}
                              for name, text in compact.items()}
        profile["timing"] = {"profile_s": round(time.perf_counter() - started, 3)}
        write_json_atomic(staging / "profile.full.json", profile)
        staging.rename(out_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
