"""Offline Build profile (M3) of one SUMO net against a frozen Build goal.

Per-kind acceptance over the frozen scored set, classes TOP/CTL/GEO/ATTR/INT with the escalations COR/SRC/OPX/UNK that
goal-create froze, hotspots by anchor element with suggested typed ops, and, for a verify-edit candidate, the G0 hard
checks, the lexicographic V against its parent and the L2/L3 guards (``twin/build_gate.py``).

Everything is explore evidence against the goal's reference facts: it measures how far a net is from those facts under
their declared correspondence, never field correctness.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shapely.geometry import LineString

from ..core.candidate_contracts import file_sha256
from ..core.connection_mode_audit import audit_network_connection_mode
from ..core.network_calibration import (NetworkFactTable, _coordinate_transform, _network_root, _points, _score,
                                        _shape, centerline_overlap_metrics, compare_calibration_scorecards)
from . import build_gate, ids, invariants, junction_integrity, render
from .count_profile import write_staged_profile
from .direction_check import RULE as DIRECTION_RULE, check_connection_directions, direction_baseline
from .direction_check import new_direction_findings

if TYPE_CHECKING:
    from .goal import BuildGoal

PROFILE_SCHEMA = "astra.twin-profile/v1"
SCORECARD_SCHEMA = "astra.twin-scorecard-cache/v1"
KIND_CLASS = {"lane_count": "TOP", "movement_inventory": "TOP", "lane_permissions": "TOP", "stop_line": "CTL",
              "signal_group": "CTL", "lane_centerline": "GEO", "junction_surface": "GEO", "speed": "ATTR",
              "lane_width": "ATTR"}
NUMERIC_KINDS = ("stop_line", "lane_centerline", "lane_width", "speed", "junction_surface")
TOLERANCE_KINDS = ("stop_line", "lane_centerline", "lane_width", "speed")
TARGET_CLASSES = build_gate.TARGET_CLASSES
ESCALATION_CLASSES = ("COR", "SRC", "OPX", "UNK")
CLASS_RANK = {"INT": 0, "TOP": 1, "CTL": 2, "GEO": 3, "ATTR": 4}
LANE_KINDS = {"stop_line", "lane_centerline", "lane_width", "speed", "lane_permissions"}
REPRESENTATION_STEP = 0.01
SOURCE_DATE_GAP_DAYS = 365
MAX_PARENT_CHAIN = 64
ACCEPT = {"lane_count": "exact integer per vehicle class", "lane_permissions": "exact vehicle-class set",
          "movement_inventory": "exact lane-pair set", "signal_group": "exact [tls_id, link_index]",
          "stop_line": "|longitudinal| <= tol_eff", "lane_centerline":
          "max lateral <= tol_eff and coverage within tol_eff >= minimum_coverage (and mean <= maximum_mean)",
          "lane_width": "|width - expected| <= tol_eff", "speed": "|speed - expected| <= tol_eff (m/s)",
          "junction_surface": "IoU >= minimum_iou"}
ACCEPT_STOP_DISTANCE = "distance <= tol_eff"
COR_TEXT = "correspondence unresolved; geometry neither confirmed nor refuted"
COR_ALL_FAIL_TEXT = ("correspondence unresolved; a geometry error is likely under every mapping tried (primary, index "
                     "shift -1 and +1). Not a proof: other edges, split or merged lanes and missing lanes are not "
                     "covered")
OPX_TEXT = ("op_not_expressible: the fix needs a lane-specific shape, while set_edge_shape moves every lane of the "
            "edge (a limit of the op vocabulary, not of SUMO)")
SRC_TEXT = "source conflict: values are never averaged; resolve by authority rank, then target date, then a human"
UNK_TEXT = "unknown: no expected value or no measurement on the source; never a task"
CLAIM = ("Explore only. Model-relative comparison against the goal's reference facts under their frozen "
         "correspondence; no field truth, no traffic-safety claim, no promotion authority.")
LOOKUPS = (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError, ET.ParseError)
# Deviations from the approved design text, recorded in every Build profile until a human signs them off.
AMENDMENTS = {
    "G0-materialize-differential": "INV-MATERIALIZE requires no structural failure the parent lacks and none whose "
                                   "measured gap grew by more than 0.1 m, instead of 'structural failures 0' (clean "
                                   "Ring1 nominal already carries 5 endpoint gaps at LSA535_part0)",
    "G0-frame-convboundary": "INV-FRAME treats <location convBoundary> as derived: netconvert recomputes it on every "
                             "netconvert-based op; netOffset, projParameter and origBoundary stay strict",
    "G0-shape-local-turns": "INV-SHAPE allows a turn above max_heading_change_deg only at a parent vertex within "
                            "max_directed_hausdorff_m that turns at most 5 deg less sharply (one candidate vertex "
                            "each); the lanes of a set_lane_count edge are judged through the remapped facts",
    "L2-not-implemented": "L2 movement probes are not implemented; a candidate that passes G0, V and L3 screens as "
                          "improved_l2_not_run, never improved",
    "L3-frozen-trips": "L3 checks every seed's requests against the trips goal-create generated from the goal source; "
                       "verify-edit explore still generates them from the ledger source, so a chained step whose trips "
                       "differ is identities_unavailable (passing goal trips into explore needs a core kwarg outside "
                       "the approved small edits)",
    "parent-scorecard-cache": "a judged profile whose parent is the goal source reuses the goal-create scorecard "
                              "(content-addressed and bound by the goal) when the scorer fingerprint is unchanged, "
                              "instead of rescoring the parent (acceptance f)",
    "core-vectorized-sampling": "core network_calibration centerline sampling uses vectorized shapely calls, "
                                "bit-identical to per-sample interpolate(), beyond the SMALL CORE EDITS list",
}


# ---- facts, scoring and per-kind acceptance ----------------------------------------------------------------------

def parse_table(data: bytes) -> NetworkFactTable:
    table = NetworkFactTable.model_validate_json(data)
    if len({fact.id for fact in table.facts}) != len(table.facts):
        raise ValueError("Fact identifiers must be unique.")
    return table


def score_root(root: ET.Element, table: NetworkFactTable) -> dict:
    """Explore scorecard of one parsed net; a frame the table cannot read makes every fact unknown."""
    try:
        return _score(root, table, mode="explore")
    except ValueError as error:
        return {"error": str(error), "facts": [{"id": fact.id, "kind": fact.kind, "status": "unknown", "metrics": {},
                                                "observed": None, "reason": str(error)} for fact in table.facts]}


def score(net_bytes: bytes, table: NetworkFactTable) -> dict:
    """Explore scorecard of one net in memory."""
    return score_root(_network_root(net_bytes), table)


def scorer_fingerprint() -> str:
    """The code a scorecard depends on: core network_calibration's bytes and the geometry library versions."""
    import pyproj
    import shapely

    from ..core import network_calibration

    digest = hashlib.sha256(Path(network_calibration.__file__).read_bytes()).hexdigest()[:16]
    return f"network_calibration@{digest};shapely {shapely.__version__};pyproj {pyproj.__version__}"


def scorecard_cache(card: dict, *, net_sha256: str, facts_sha256: str) -> bytes:
    """The scorecard fields a judged profile reads from its parent (id, kind, status, reason, metrics), as bytes."""
    payload = {"schema": SCORECARD_SCHEMA, "net_sha256": net_sha256, "facts_sha256": facts_sha256,
               "scorer": scorer_fingerprint(),
               "facts": [{key: row.get(key) for key in ("id", "kind", "status", "reason", "metrics")}
                         for row in card["facts"]]}
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def components(entry: dict, metrics: dict) -> dict[str, dict]:
    """Numeric acceptance components ``{value, limit, scale, excess}`` of one frozen scored fact.

    ``excess = (value - limit)+ / scale`` (``build_gate.EXCESS_RULE``); a value the net does not measure is None.
    """
    kind, tolerance = entry["kind"], entry.get("tol_eff")

    def part(value, limit, scale):
        excess = None if value is None or limit is None or not scale else max(0.0, value - limit) / scale
        return {"value": value, "limit": limit, "scale": scale, "excess": excess}

    if kind == "stop_line":
        value = metrics.get("longitudinal_offset_m" if entry.get("stop_line_mode") == "longitudinal" else "offset_m")
        return {"stop_line_m": part(None if value is None else abs(value), tolerance, tolerance)}
    if kind == "lane_centerline":
        minimum = entry.get("minimum_coverage", 1.0)
        covered = (metrics.get("coverage_within_m") or {}).get(str(float(tolerance))) if tolerance else None
        samples = metrics.get("coverage_sample_count")
        unit = max(1 - minimum, 1 / samples) if samples else (1 - minimum or 1.0)
        result = {"max_lateral_m": part(metrics.get("max_sampled_lateral_offset_m"), tolerance, tolerance),
                  "coverage_shortfall": part(None if covered is None else 1 - covered, 1 - minimum, unit)}
        if entry.get("maximum_mean_m") is not None:
            result["mean_lateral_m"] = part(metrics.get("mean_lateral_offset_m"), entry["maximum_mean_m"], tolerance)
        return result
    if kind == "lane_width":
        return {"width_error_m": part(metrics.get("absolute_width_error_m"), tolerance, tolerance)}
    if kind == "speed":
        return {"speed_error_m_s": part(metrics.get("absolute_speed_error_m_s"), tolerance, tolerance)}
    if kind == "junction_surface":
        iou, minimum = metrics.get("intersection_over_union"), entry.get("minimum_iou")
        scale = None if minimum is None else 1 - minimum
        return {"iou_shortfall": part(None if iou is None else 1 - iou, scale, scale)}
    return {}


def _magnitude(kind: str, metrics: dict) -> int | None:
    if kind == "lane_count":
        return metrics.get("absolute_count_error")
    if kind == "lane_permissions":
        return metrics.get("permission_difference_count")
    if kind == "movement_inventory" and "false_positive" in metrics:
        return metrics["false_positive"] + metrics["false_negative"]
    if kind == "signal_group" and "binding_match" in metrics:
        return 1 - metrics["binding_match"]
    return None


def fact_row(entry: dict, card_row: dict) -> dict:
    parts = components(entry, card_row.get("metrics") or {})
    excesses = [part["excess"] for part in parts.values() if part["excess"] is not None]
    return {"id": entry["id"], "kind": entry["kind"], "class": entry["class"], "anchor": entry["anchor"],
            "status": card_row["status"], "reason": card_row.get("reason"),
            "excess": sum(excesses) if excesses else None, "components": parts,
            "magnitude": _magnitude(entry["kind"], card_row.get("metrics") or {})}


# ---- goal-create: anchors, escalations and the frozen scored set --------------------------------------------------

def _lane_maps(root: ET.Element) -> tuple[dict[str, str], dict[str, str], dict[str, list[str]]]:
    """lane -> edge, edge -> to-junction and road edge -> its lane ids, over non-internal edges."""
    lane_edge, edge_to, edge_lanes = {}, {}, {}
    for edge in root.findall("edge"):
        edge_to[edge.get("id")] = edge.get("to")
        if edge.get("function") != "internal":
            edge_lanes[edge.get("id")] = [lane.get("id") for lane in edge.findall("lane")]
        for lane in edge.findall("lane"):
            lane_edge[lane.get("id")] = edge.get("id")
    return lane_edge, edge_to, edge_lanes


def _first_lane(selector: dict) -> str | None:
    if isinstance(selector.get("lane_id"), str):
        return selector["lane_id"]
    for key in ("lane_ids", "incoming_lane_ids", "lane_pair"):
        values = [value for value in selector.get(key) or [] if isinstance(value, str)]
        if values:
            return values[0]
    return None


def fact_anchor(kind: str, selector: dict, expected: Any, lane_edge: dict[str, str], edge_to: dict[str, str]) -> str:
    """Hotspot anchor: edge (centerline, width, speed, lane count), lane (stop line, permissions), junction
    (movements, surface) or ``tls:<id>#<link>`` (signal binding)."""
    lane = _first_lane(selector)
    edge = lane_edge.get(lane, lane.rpartition("_")[0] if lane else None) if lane else None
    if kind in {"stop_line", "lane_permissions"} and lane:
        return ids.element_id("lane", lane)
    if kind in {"lane_centerline", "lane_width", "speed"} and edge:
        return ids.element_id("edge", edge)
    if kind == "lane_count" and selector.get("edge_ids"):
        return ids.element_id("edge", str(selector["edge_ids"][0]))
    if kind == "movement_inventory":
        junctions = selector.get("junction_ids") or [edge_to.get(edge)]
        if junctions and junctions[0]:
            return ids.element_id("junction", str(junctions[0]))
    if kind == "junction_surface" and selector.get("junction_id"):
        return ids.element_id("junction", str(selector["junction_id"]))
    if kind == "signal_group" and isinstance(expected, dict) and "tls_id" in expected:
        return f"tls:{expected['tls_id']}#{expected.get('link_index')}"
    return ids.element_id("lane", lane) if lane else f"fact:{kind}"


def _locked(fact) -> bool:
    return fact.mapping_status == "confirmed" and fact.mapping_confidence == "confirmed"


def correspondence_reason(fact, sigma_m: float) -> str | None:
    """Why a fact is COR, or None. Unlocked lane facts need high confidence and an index-shift margin >= max(1 m,
    2 sigma); every other unlocked fact (lane counts, movements, bindings, surfaces) stays COR until locked."""
    if _locked(fact):
        return None
    if fact.mapping_status == "unresolved":
        return "mapping_unresolved"
    if fact.mapping_confidence in {"conflict", "low", "none"}:
        return f"mapping_confidence_{fact.mapping_confidence}"
    if fact.kind not in LANE_KINDS:
        return "mapping_not_locked"
    if fact.mapping_confidence != "high":
        return f"mapping_confidence_{fact.mapping_confidence}_not_high"
    margin = fact.mapping_diagnostics.get("best_alternative_margin_m")
    if type(margin) in (int, float) and margin < max(1.0, 2 * sigma_m):
        return "index_shift_margin_below_max_1m_2sigma"
    return None


def source_conflict_reason(fact, sources: dict, target_date: date) -> str | None:
    """SRC from source dates only: a source dated more than 365 days from the target. No producer declares per-source
    values yet, so the design's second SRC rule (two values disagreeing by more than 2 sqrt(s1^2 + s2^2)) has no input
    and is not evaluated."""
    gaps = [abs((target_date - sources[name].source_date).days) for name in fact.source_ids
            if name in sources and sources[name].source_date is not None]
    if gaps and max(gaps) > SOURCE_DATE_GAP_DAYS:
        return f"source_date_gap_{max(gaps)}d"
    return None


def index_shift_probe(fact, root: ET.Element, transform) -> dict:
    """Heuristic index-shift probe of a COR centerline (``core/lane_correspondence.py`` diagnose_lane_match): the
    largest lateral offset under the primary mapping and the -1/+1 lane shifts. A fit under some mapping never proves
    the geometry; no fit says a geometry error is likely under every mapping tried, which is still no proof."""
    from ..core.lane_correspondence import diagnose_lane_match

    selector = fact.selector
    lanes = [selector["lane_id"]] if "lane_id" in selector else list(selector.get("lane_ids") or [])
    if (fact.kind != "lane_centerline" or selector.get("include_internal_paths") or not lanes or transform is None
            or fact.sample_spacing_m is None or fact.tolerance_m is None):
        return {"status": "not_probed", "text": COR_TEXT}
    elements = {lane.get("id"): (edge, lane) for edge in root.findall("edge") if edge.get("function") != "internal"
                for lane in edge.findall("lane")}
    if any(lane not in elements for lane in lanes):
        return {"status": "not_probed", "text": COR_TEXT, "reason": "network_lane_missing"}
    edges = {elements[lane][0].get("id") for lane in lanes}
    try:
        network = {key: (edge, lane, LineString(transform(_shape(lane)))) for key, (edge, lane) in elements.items()
                   if edge.get("id") in edges}
        reference = LineString(_points(fact.expected))
        primary = centerline_overlap_metrics(reference, [list(network[lane][2].coords) for lane in lanes],
                                             fact.sample_spacing_m, direction=fact.reference_direction)
        _, alternatives, _ = diagnose_lane_match(parts=lanes, reference=reference, network_lanes=network,
                                                 spacing=fact.sample_spacing_m, direction=fact.reference_direction)
    except (ValueError, KeyError) as error:
        return {"status": "not_probed", "text": COR_TEXT, "reason": str(error)}
    tried = [{"mapping": "primary", "lane_ids": lanes, "max_lateral_m": primary["max_sampled_lateral_offset_m"]}]
    tried += [{"mapping": f"shift{row['index_shift']:+d}", "lane_ids": row["lane_ids"],
               "max_lateral_m": row["metrics"]["max_sampled_lateral_offset_m"]} for row in alternatives]
    fits = [row["mapping"] for row in tried if row["max_lateral_m"] is not None
            and row["max_lateral_m"] <= fact.tolerance_m]
    return {"status": "probed", "tried": tried, "fits": fits, "text": COR_TEXT if fits else COR_ALL_FAIL_TEXT}


def _per_lane_shape_facts(table: NetworkFactTable, rows: dict[str, dict], lane_edge: dict[str, str],
                          edge_lanes: dict[str, list[str]]) -> set[str]:
    """Mismatched single-lane centerlines whose edge has a sibling lane that matches: one edge shape cannot fix them."""
    centerline = {fact.selector["lane_id"]: fact.id for fact in table.facts
                  if fact.kind == "lane_centerline" and set(fact.selector) == {"lane_id"}}
    result = set()
    for lane, fact_id in centerline.items():
        if rows[fact_id]["status"] != "mismatched":
            continue
        siblings = [other for other in edge_lanes.get(lane_edge.get(lane), []) if other != lane and other in centerline]
        if any(rows[centerline[other]]["status"] == "matched" for other in siblings):
            result.add(fact_id)
    return result


def tolerance_floor(unit: str, sigma: dict) -> float:
    """max(2 sigma, representation step): the smallest tol_eff a goal may declare for that unit."""
    spread = sigma.get("speed_m_s", 0.0) if unit == "m/s" else sigma.get("comb_m", 0.0)
    return max(2 * spread, REPRESENTATION_STEP)


def scored_entry(fact, anchor: str, sigma: dict) -> dict:
    """The frozen scored-set entry of one fact: kind, class, acceptance and tol_eff (equal to the declared tolerance;
    lint refuses one below its floor instead of raising it silently)."""
    kind = fact.kind
    entry: dict[str, Any] = {"id": fact.id, "kind": kind, "class": KIND_CLASS[kind], "accept": ACCEPT[kind],
                             "anchor": anchor}
    if kind == "stop_line":
        mode = "longitudinal" if fact.tolerance_longitudinal_m is not None else "distance"
        entry.update(stop_line_mode=mode, tol_eff=fact.tolerance_longitudinal_m if mode == "longitudinal"
                     else fact.tolerance_m, unit="m")
        if mode == "distance":
            entry["accept"] = ACCEPT_STOP_DISTANCE
    elif kind == "lane_centerline":
        entry.update(tol_eff=fact.tolerance_m, unit="m", maximum_mean_m=fact.maximum_mean_lateral_offset_m,
                     minimum_coverage=1.0 if fact.minimum_coverage is None else fact.minimum_coverage)
    elif kind == "lane_width":
        entry.update(tol_eff=fact.tolerance_m, unit="m")
    elif kind == "speed":
        entry.update(tol_eff=fact.tolerance_m_s, unit="m/s")
    elif kind == "junction_surface":
        entry["minimum_iou"] = fact.minimum_iou
    else:
        # A categorical fact never carries a tolerance; one that declares any is kept so that lint refuses it.
        declared = (fact.tolerance_m, fact.tolerance_m_s, fact.tolerance_longitudinal_m, fact.minimum_iou)
        entry["tol_eff"] = next((value for value in declared if value is not None), None)
    if "unit" in entry:
        entry["tol_floor"] = tolerance_floor(entry["unit"], sigma)
    return {key: value for key, value in entry.items() if value is not None}


def freeze_scored_set(table: NetworkFactTable, card: dict, root: ET.Element, *,
                      sigma: dict) -> tuple[list[dict], dict[str, dict]]:
    """Freeze the scored set from the source's explore scorecard; escalated and threshold-free facts are excluded.

    Order: UNK (no expected value), COR, SRC, UNK (not measured on the source), measured only (no declared threshold),
    OPX (a mismatched centerline whose sibling lane matches), then a scored target of the kind's class.
    """
    rows = {row["id"]: row for row in card["facts"]}
    lane_edge, edge_to, edge_lanes = _lane_maps(root)
    sources = {source.id: source for source in table.sources}
    try:
        transform = _coordinate_transform(root, table.coordinate_system)
    except ValueError:
        transform = None
    per_lane = _per_lane_shape_facts(table, rows, lane_edge, edge_lanes)
    scored, excluded = [], {}
    for fact in table.facts:
        row, anchor = rows[fact.id], fact_anchor(fact.kind, fact.selector, fact.expected, lane_edge, edge_to)
        lane = _first_lane(fact.selector)
        lane_anchor = ids.element_id("lane", lane) if lane and fact.kind in LANE_KINDS else anchor
        base = {"kind": fact.kind, "anchor": anchor, "source_status": row["status"]}
        cor = correspondence_reason(fact, sigma.get("comb_m", 0.0)) if fact.expected is not None else None
        src = source_conflict_reason(fact, sources, table.target_date) if fact.expected is not None else None
        if fact.expected is None:
            excluded[fact.id] = {**base, "class": "UNK", "reason": "reference_fact_unknown", "note": UNK_TEXT}
        elif cor:
            probe = index_shift_probe(fact, root, transform)
            excluded[fact.id] = {**base, "class": "COR", "anchor": lane_anchor, "reason": cor, "note": probe["text"],
                                 **({"probe": probe["tried"]} if probe["status"] == "probed" else {})}
        elif src:
            excluded[fact.id] = {**base, "class": "SRC", "reason": src, "note": SRC_TEXT}
        elif row["status"] == "unknown":
            excluded[fact.id] = {**base, "class": "UNK", "reason": str(row.get("reason")), "note": UNK_TEXT}
        elif row["status"] == "measured":
            excluded[fact.id] = {**base, "class": KIND_CLASS[fact.kind], "reason": "acceptance_threshold_not_declared",
                                 "note": "measured only; never in the frozen scored set"}
        elif fact.id in per_lane:
            excluded[fact.id] = {**base, "class": "OPX", "anchor": lane_anchor, "reason": "op_not_expressible",
                                 "note": OPX_TEXT}
        else:
            scored.append(scored_entry(fact, anchor, sigma))
    return scored, excluded


def frozen_sha256(scored: list[dict], excluded: dict[str, dict]) -> str:
    payload = json.dumps({"scored_facts": scored, "excluded": excluded}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---- INT findings ------------------------------------------------------------------------------------------------

def scope_junctions(root: ET.Element, edge_ids: list[str], junction_ids: list[str]) -> list[str]:
    """The declared junctions plus both end nodes of every declared edge, as present in ``root``."""
    present = {junction.get("id") for junction in root.findall("junction")}
    nodes = set(junction_ids)
    for edge in root.findall("edge"):
        if edge.get("id") in set(edge_ids):
            nodes |= {edge.get("from"), edge.get("to")}
    return sorted(node for node in nodes if node in present)


def int_findings(root: ET.Element, junctions: list[str]) -> tuple[list[dict], dict]:
    """INT rows: every direction finding (``twin/direction_check.py``), and on the given junctions every
    connection-mode audit finding (structural, review and TLS-binding), request-matrix finding and independent
    movement-safety finding (``twin/junction_integrity.py``). Each row names the road lanes it refers to and, for a
    measured audit finding, its largest measured length (``measure``)."""
    index = junction_integrity.road_lane_index(root)
    direction = check_connection_directions(root)
    rows = {row["id"]: {"id": row["id"], "class": "INT", "source": "direction", "finding": row["reason"],
                        "anchor": ids.element_id("junction", row["junction_id"]), "measure": None,
                        "lanes": junction_integrity.named_lanes((row["id"],), index)} for row in direction["findings"]}
    if junctions:
        try:
            audit = audit_network_connection_mode(root, junction_ids=junctions, endpoint_tolerance_m=0.1)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
            # A net the audit cannot read is itself an integrity finding, never a crash of the profile.
            identity = f"audit:{ids.short_hash(*junctions)}:audit_failed:{type(error).__name__}"
            rows[identity] = {"id": identity, "class": "INT", "source": "connection_mode_audit",
                              "finding": f"audit failed: {error}", "anchor": ids.element_id("junction", junctions[0]),
                              "measure": None, "lanes": []}
            audit = {"junctions": []}
        keys = junction_integrity.connection_keys(root)
        for record in audit["junctions"]:
            mode, tls = record["connection_mode_audit"], record.get("tls_link_binding_audit") or {}
            for finding in (*mode.get("structural_failures", []), *mode.get("review_findings", []),
                            *tls.get("structural_failures", []), *tls.get("review_findings", [])):
                identity = f"audit:{record['junction_id']}:{junction_integrity.audit_identity(finding, keys)}"
                measures = [value for value in (junction_integrity.audit_measure(finding),
                                                (rows.get(identity) or {}).get("measure")) if value is not None]
                rows[identity] = {"id": identity, "class": "INT", "source": "connection_mode_audit",
                                  "finding": str(finding), "anchor": ids.element_id("junction", record["junction_id"]),
                                  "measure": max(measures) if measures else None,
                                  "lanes": junction_integrity.named_lanes((identity,), index)}
        rows.update((row["id"], row) for row in junction_integrity.integrity_rows(root, junctions, index))
    return [rows[key] for key in sorted(rows)], direction


def structural_failures(root: ET.Element) -> list[tuple[str, float | None]]:
    """Network-wide connection-mode structural failures, as verify-edit counts them (``structural_failure_count``),
    as ``(connection-order-free identity, measured length or None)`` (``junction_integrity.audit_identity`` and
    ``audit_measure``)."""
    try:
        audit = audit_network_connection_mode(root, endpoint_tolerance_m=0.1)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
        return [(f"audit_failed:{type(error).__name__}", None)]
    keys = junction_integrity.connection_keys(root)
    rows = [(f"{record['junction_id']}:{junction_integrity.audit_identity(failure, keys)}",
             junction_integrity.audit_measure(failure))
            for record in audit["junctions"] for failure in record["connection_mode_audit"].get("structural_failures", [])]
    rows += [(f"tls:{junction_integrity.audit_identity(failure, keys)}", junction_integrity.audit_measure(failure))
             for failure in (audit.get("tls_link_binding_audit") or {}).get("structural_failures", [])]
    rows += [(f"config:{failure}", None) for failure in audit.get("configuration_failures", [])]
    return sorted(rows, key=lambda row: (row[0], -1.0 if row[1] is None else row[1]))


# ---- suggested typed ops ----------------------------------------------------------------------------------------

def _lane_pair(lane_id: str) -> tuple[str, int | None]:
    edge, _, index = lane_id.rpartition("_")
    return edge, int(index) if index.isdigit() else None


def _connection_params(pair: list[str]) -> dict:
    (start, start_lane), (end, end_lane) = _lane_pair(pair[0]), _lane_pair(pair[1])
    return {"from": start, "fromLane": start_lane, "to": end, "toLane": end_lane}


class _NetIndex:
    def __init__(self, root: ET.Element):
        self.lanes = {lane.get("id"): lane for edge in root.findall("edge") for lane in edge.findall("lane")}
        self.edge_lanes = Counter(edge.get("id") for edge in root.findall("edge") for _ in edge.findall("lane"))
        self.directions = {ids.connection_lanes(c): c.get("dir") for c in root.findall("connection")}


def suggested_ops(fact, card_row: dict, net: _NetIndex) -> list[dict]:
    """Typed ops that would fix one mismatched fact; ``complete`` is False when a parameter needs a human choice."""
    if card_row["status"] != "mismatched":
        return []
    kind, selector, metrics, expected = fact.kind, fact.selector, card_row.get("metrics") or {}, fact.expected
    lane_id = selector.get("lane_id")

    def op(name: str, params: dict, complete: bool = True, note: str | None = None) -> dict:
        return {"op": name, "params": params, "complete": complete, **({"note": note} if note else {})}

    if kind == "stop_line":
        lane, longitudinal = net.lanes.get(lane_id), metrics.get("longitudinal_offset_m")
        if lane is None or longitudinal is None:
            return [op("move_stop_line", {"lane_id": lane_id}, False, "the lane or its longitudinal offset is missing")]
        current = float(lane.get("endOffset", "0"))
        target = round(current + longitudinal, 3)
        if target < 0:
            return [op("move_stop_line", {"lane_id": lane_id}, False,
                       f"the reference stop line lies {-target:.3f} m beyond the edge end; end_offset_m >= 0")]
        return [op("move_stop_line", {"lane_id": lane_id, "end_offset_m": target}, True,
                   f"current endOffset {current:g} + longitudinal {longitudinal:+.3f} m")]
    if kind == "lane_count":
        edge = str(selector["edge_ids"][0])
        observed = card_row.get("observed")
        if type(observed) is int and type(expected) is int:
            return [op("set_lane_count", {"edge_id": edge, "num_lanes": net.edge_lanes[edge] + expected - observed},
                       False, "choose remove_index or add_side; verify-edit remaps the lane selectors")]
        return [op("set_lane_count", {"edge_id": edge}, False, "the edge or its count is missing")]
    if kind == "movement_inventory":
        result = [op("add_lane_connection", _connection_params(pair)) for pair in metrics.get("missing_movements", [])]
        for pair in metrics.get("extra_movements", []):
            turnaround = net.directions.get(tuple(pair)) in {"t", "T"}
            result.append(op("suppress_turnaround" if turnaround else "delete_lane_connection",
                             _connection_params(pair)))
        return result
    if kind == "lane_permissions":
        allowed = " ".join(sorted(expected)) if expected else None
        return [op("set_lane_permissions", {"lane_id": lane_id, "allow": allowed} if allowed
                   else {"lane_id": lane_id, "disallow": "all"})]
    if kind == "speed":
        return [op("set_speed", {"lane_id": lane_id, "speed_m_s": expected})]
    if kind == "lane_width":
        return [op("set_lane_width", {"lane_id": lane_id, "width_m": expected})]
    if kind == "lane_centerline":
        lane = _first_lane(selector)
        lateral = metrics.get("max_sampled_lateral_offset_m")
        note = ("coverage only: the lane is shorter or longer than the reference; check its stop line "
                "(move_stop_line) first" if lateral is not None and lateral <= (fact.tolerance_m or 0)
                else "derive the edge axis from the reference lane centerline(s); every lane of the edge moves")
        return [op("set_edge_shape", {"edge_id": _lane_pair(lane)[0] if lane else None}, False, note)]
    if kind == "junction_surface":
        return [op("set_junction_shape", {"node_id": selector.get("junction_id"), "shape": expected})]
    observed = card_row.get("observed") or []
    name = "demote_duplicate_tls" if len(observed) > 1 else "relocate_tls_control"
    return [op(name, {"tls_id": expected.get("tls_id") if isinstance(expected, dict) else None}, False,
               f"bind {selector.get('lane_pair')} to {expected}; observed {observed}")]


# ---- rows, hotspots and escalations ----------------------------------------------------------------------------

def _row_weight(row: dict) -> float:
    return row["excess"] if row["excess"] is not None and row["kind"] in NUMERIC_KINDS else 1.0


def rank_hotspots(rows: list[dict], int_rows: list[dict]) -> list[dict]:
    """Mismatched scored rows and INT rows grouped by (class, anchor) as ``H:<class>:<anchor>``.

    Sort: class (INT > TOP > CTL > GEO > ATTR), then the summed weight (normalized numeric excess; a categorical or
    unmeasured row counts 1), then the stable id. The design's traffic weight sits between the last two; it is not
    available offline, so it is equal for every hotspot and left out.
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row["status"] == "mismatched":
            groups[(row["class"], row["anchor"])].append(row)
    for row in int_rows:
        groups[("INT", row["anchor"])].append(row)
    hotspots = []
    for (cls, anchor), members in groups.items():
        weighted = sorted(((1.0 if cls == "INT" else _row_weight(row), row) for row in members),
                          key=lambda item: (-item[0], item[1]["id"]))
        hotspots.append({"id": ids.hotspot_id(cls, anchor), "class": cls, "anchor": anchor,
                         "weight": round(sum(weight for weight, _ in weighted), 9),
                         "rows": [{**row, "weight": round(weight, 9)} for weight, row in weighted]})
    return sorted(hotspots, key=lambda h: (CLASS_RANK[h["class"]], -h["weight"], h["id"]))


def escalation_rows(excluded: dict[str, dict], card: dict[str, dict]) -> list[dict]:
    """Frozen COR/SRC/OPX/UNK facts as hotspot-like escalations; never tasks, never in V."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for fact, row in sorted(excluded.items()):
        if row["class"] in ESCALATION_CLASSES:
            current = card.get(fact, {})
            groups[ids.hotspot_id(row["class"], row["anchor"])].append(
                {"fact": fact, "kind": row["kind"], "class": row["class"], "reason": row["reason"],
                 "status": row["note"], "net_status": current.get("status"),
                 "metrics": {key: current.get("metrics", {}).get(key) for key in (
                     "mean_lateral_offset_m", "reference_coverage", "longitudinal_offset_m")
                     if current.get("metrics", {}).get(key) is not None},
                 **({"probe": row["probe"]} if "probe" in row else {})})
    return [{"id": key, "class": members[0]["class"], "facts": members} for key, members in sorted(
        groups.items(), key=lambda item: (ESCALATION_CLASSES.index(item[1][0]["class"]), item[0]))]


# ---- subjects: the goal source, a raw net or a verify-edit candidate ---------------------------------------------

def _read_bytes(path: Path, sha256: str | None, label: str) -> bytes:
    data = Path(path).read_bytes()
    if sha256 is not None and hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError(f"profile refused: {label} {path} does not match its recorded sha256")
    return data


def goal_source_path(goal: BuildGoal, repository_root: Path) -> Path:
    path = Path(goal.source.net)
    return path if path.is_absolute() else Path(repository_root) / path


def _location(root: ET.Element) -> dict | None:
    element = root.find("location")
    return dict(element.attrib) if element is not None else None


def _road_lane_shapes(root: ET.Element) -> dict[str, list[tuple[float, float]]]:
    return {lane.get("id"): _shape(lane) for edge in root.findall("edge") if edge.get("function") != "internal"
            for lane in edge.findall("lane") if lane.get("shape")}


def op_targets(operation: dict, root: ET.Element) -> dict[str, list[str]]:
    """The edges and junctions a typed op aims at (not netconvert's derived mutable scope)."""
    kind, params = operation.get("operation"), operation.get("params") or {}
    lane_edge, edge_to, _ = _lane_maps(root)
    if kind in {"move_stop_line", "set_lane_width", "set_speed", "set_lane_permissions"}:
        return {"edges": [lane_edge.get(params.get("lane_id"), str(params.get("lane_id")))], "junctions": []}
    if kind in {"set_edge_shape", "set_lane_count"}:
        return {"edges": [str(params.get("edge_id"))], "junctions": []}
    if kind in {"add_lane_connection", "delete_lane_connection", "suppress_turnaround"}:
        return {"edges": [], "junctions": [str(edge_to.get(params.get("from")))]}
    nodes = [params.get(key) for key in ("node_id", "retained_node_id", "from_node", "to_node", "join_id")]
    nodes += list(params.get("node_ids") or [])
    return {"edges": [], "junctions": sorted({str(node) for node in nodes if node})}


def _op_text(operation: dict | None, root: ET.Element | None) -> str | None:
    if not operation:
        return None
    kind, params = operation.get("operation"), dict(operation.get("params") or {})
    if kind == "move_stop_line" and root is not None:
        lane = next((row for row in root.iter("lane") if row.get("id") == params.get("lane_id")), None)
        before = lane.get("endOffset", "0") if lane is not None else "?"
        return f"move_stop_line lane:{params.get('lane_id')} endOffset {before} -> {params.get('end_offset_m')}"
    shown = {key: (f"<{len(value)} points>" if key == "shape" and isinstance(value, list) else value)
             for key, value in sorted(params.items())}
    return f"{kind} {json.dumps(shown, sort_keys=True, ensure_ascii=False)}"


def _remapped_facts(report: dict, facts_path: Path, scratch_dir: Path | None) -> tuple[bytes | None, dict]:
    """The lane-count selector remap verify-edit declared, reproduced from the goal table and the recorded plan in a
    transient directory under ``scratch_dir`` (the system temp directory when None)."""
    from ..core.network_edit_operations import _remap_lane_count_facts

    declared = report.get("fact_selector_remap") or {}
    plan = json.loads(Path(report["plan_file"]).read_text(encoding="utf-8"))
    if scratch_dir is not None:
        Path(scratch_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".twin-remap-", dir=scratch_dir) as scratch:
        output = Path(scratch) / "candidate-facts.json"
        _remap_lane_count_facts(facts_path, plan, output)
        data = output.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    recorded = (declared.get("candidate_facts") or {}).get("sha256")
    return data, {"sha256": digest, "basis": "declared_remap", "verified": recorded == digest,
                  "recorded_sha256": recorded}


def _scorecard_facts_sha(report: dict, key: str) -> list[str]:
    """Input hashes a verify-edit scorecard recorded (its net and fact table)."""
    try:
        return sorted(json.loads(Path(report[key]).read_text(encoding="utf-8")).get("inputs", {}).values())
    except LOOKUPS:
        return []


def _mapping_changes(goal_table: NetworkFactTable, candidate_table: NetworkFactTable) -> list[str]:
    fields = ("mapping_status", "mapping_confidence", "mapping_method", "alternatives", "mapping_diagnostics",
              "reference_direction")
    before = {fact.id: [getattr(fact, name) for name in fields] for fact in goal_table.facts}
    return sorted(fact.id for fact in candidate_table.facts
                  if before.get(fact.id) != [getattr(fact, name) for name in fields])


def _goal_scorecard(goal: BuildGoal, facts_path: Path, facts_sha: str) -> tuple[dict | None, str]:
    """The goal source's scorecard frozen at goal-create, when it is intact and made by the current scorer."""
    record = goal.frozen.source_scorecard
    path = Path(facts_path).parent / f"{record.ref.removeprefix('cas:sha256:')}.json"
    try:
        data = path.read_bytes()
    except OSError:
        return None, "rescored: the goal-create scorecard is missing from the store"
    if ids.cas_reference(hashlib.sha256(data).hexdigest()) != record.ref:
        return None, "rescored: the goal-create scorecard does not match the goal's cas reference"
    card = json.loads(data)
    if card.get("scorer") != scorer_fingerprint() or record.scorer != card.get("scorer"):
        return None, "rescored: the scorer changed since goal-create"
    if card.get("net_sha256") != goal.source.sha256 or card.get("facts_sha256") != facts_sha:
        return None, "rescored: the goal-create scorecard belongs to another net or fact table"
    return card, f"goal-create scorecard {record.ref[:24]} (scorer unchanged)"


def _verify_edit_gate(goal: BuildGoal, *, table: NetworkFactTable, facts_sha: str, frozen_recomputed: str,
                      report: dict, ledger: dict, parent_root: ET.Element, parent_sha: str, root: ET.Element,
                      net_sha: str, parent_card: dict, candidate_facts: dict, candidate_table: NetworkFactTable,
                      entries: list[dict], rows: dict[str, dict], card: dict) -> dict:
    """G0, V, L2 and L3 of one verify-edit candidate against its parent."""
    parent_by_id = {row["id"]: row for row in parent_card["facts"]}
    card_by_id = {row["id"]: row for row in card["facts"]}
    parent_rows = {entry["id"]: fact_row(entry, parent_by_id[entry["id"]]) for entry in entries}
    scored = {entry["id"] for entry in entries}
    comparison = compare_calibration_scorecards(parent_card, card)
    lost = [fact for fact in comparison["measurability_lost"] if fact in scored]
    regressed = [fact for fact in comparison["regressed_facts"] if fact in scored]
    lost_components = sorted(f"{fact}:{name}" for fact, before in parent_rows.items()
                             for name, part in before["components"].items()
                             if part["value"] is not None and rows[fact]["components"][name]["value"] is None)
    improved = []
    for entry in entries:
        if entry["kind"] != "lane_centerline":
            continue
        before, after = parent_rows[entry["id"]], rows[entry["id"]]
        lateral = (before["components"]["max_lateral_m"]["value"], after["components"]["max_lateral_m"]["value"])
        better = ((lateral[0] is not None and lateral[1] is not None and lateral[1] < lateral[0] - 1e-12)
                  or (before["excess"] is not None and after["excess"] is not None
                      and after["excess"] < before["excess"] - 1e-12))
        if better:
            improved.append((entry["id"], invariants.coverage_measures(parent_by_id[entry["id"]].get("metrics") or {}),
                             invariants.coverage_measures(card_by_id[entry["id"]].get("metrics") or {})))
    construction = report.get("construction") or {}
    operations = ledger.get("operations", [])
    operation = operations[0] if operations else {}
    # A lane-count edit re-lays out and renumbers its edge's lanes; they are judged through the remapped facts.
    relaid = (str(operation["params"]["edge_id"]) if operation.get("operation") == "set_lane_count" else None)
    parent_shapes, shapes = _road_lane_shapes(parent_root), _road_lane_shapes(root)
    lane_edge = _lane_maps(parent_root)[0]
    changes = {ids.element_id("lane", lane): invariants.shape_change(parent_shapes[lane], shapes[lane])
               for lane in sorted(parent_shapes.keys() & shapes.keys())
               if parent_shapes[lane] != shapes[lane] and lane_edge.get(lane) != relaid}
    plan = json.loads(Path(report["plan_file"]).read_text(encoding="utf-8")) if report.get("plan_file") else {}
    junctions = sorted(set(scope_junctions(parent_root, goal.scope.mutable_edge_ids, goal.scope.mutable_junction_ids))
                       | set(plan.get("mutable_junction_ids", [])))
    parent_int, parent_direction = int_findings(parent_root, [j for j in junctions if j in _junction_ids(parent_root)])
    candidate_int, candidate_direction = int_findings(root, [j for j in junctions if j in _junction_ids(root)])
    lane_mapping = ({str(operation["params"]["edge_id"]): {str(old): str(new) for old, new in
                                                          (plan.get("lane_index_mapping") or {}).items()}}
                    if operation.get("operation") == "set_lane_count" else None)
    new_direction = new_direction_findings(direction_baseline(parent_direction, parent_sha), candidate_direction,
                                           lane_mapping)
    bound = facts_sha in _scorecard_facts_sha(report, "source_scorecard")
    outside = report.get("outside_fact_changes") if bound else None
    artifacts = [row.model_dump() for row in goal.guards.lane_artifacts]
    lane_artifacts = construction.get("lane_coordinate_artifacts")
    baseline = {identity: goal.int_baseline.measures.get(identity) for identity in goal.int_baseline.finding_ids}
    checks = [
        invariants.inv_facts(goal_facts_sha256=goal.reference.facts.removeprefix("cas:sha256:"), facts_sha256=facts_sha,
                             frozen_sha256=goal.frozen.sha256, frozen_recomputed=frozen_recomputed,
                             candidate_facts=candidate_facts),
        invariants.inv_materialize(report_status=report.get("status"), candidate_sha256=net_sha,
                                   declared_sha256=report.get("candidate_sha256"),
                                   structural_failures=construction.get("structural_failure_count"),
                                   load_status=(construction.get("sumo_load") or {}).get("status"),
                                   source_immutable=bool(report.get("source_immutable"))
                                   and bool(construction.get("source_immutable")),
                                   source_sha256=(report.get("source") or {}).get("sha256"), parent_sha256=parent_sha,
                                   structure={"parent": structural_failures(parent_root),
                                              "candidate": structural_failures(root)}),
        invariants.inv_opscope(operations=operations, goal_ops=goal.scope.ops, max_ops=goal.scope.max_ops_per_round,
                               targets=op_targets(operation, parent_root), goal_edges=goal.scope.mutable_edge_ids,
                               goal_junctions=goal.scope.mutable_junction_ids, outside_fact_changes=outside),
        invariants.inv_measurable(lost=lost, lost_components=lost_components),
        build_gate.g0_regressed(regressed),
        invariants.inv_coverage(improved),
        invariants.inv_shape(changes, goal.g0_shape.model_dump()),
        invariants.inv_frame(_location(parent_root), _location(root)),
        invariants.inv_artifacts(artifacts, lane_artifacts if lane_artifacts and "files" in lane_artifacts else None),
        invariants.inv_e1_lane(artifacts, lane_artifacts if lane_artifacts and "files" in lane_artifacts else None),
        invariants.inv_cor({entry["id"]: entry["class"] for entry in entries},
                           _mapping_changes(table, candidate_table)),
        invariants.inv_green(construction.get("new_protected_green_conflicts") or []),
        invariants.inv_audit([(row["id"], row["measure"]) for row in parent_int if row["source"] != "direction"],
                             [(row["id"], row["measure"]) for row in candidate_int if row["source"] != "direction"],
                             junctions, baseline),
        invariants.inv_dir(new_direction, DIRECTION_RULE)]
    g0 = build_gate.g0(checks)
    v = build_gate.compare_v(parent_rows, rows, goal.objective.materiality.model_dump())
    trips = {row.seed: row.sha256 for row in goal.guards.routeability.trips}
    l3 = build_gate.l3_rule(build_gate.routeability_pairs(report), goal.guards.routeability.seeds, trips)
    l2 = build_gate.l2_not_run()
    label, first_fail = build_gate.screening(g0, v, l3, l2)
    return {"g0": g0, "v": v, "l3": l3, "l2": l2, "screening": label, "first_fail": first_fail,
            "parent_rows": parent_rows, "lost": lost, "regressed": regressed, "outside": outside,
            "shape_changes": changes, "new_direction": new_direction,
            "parent_int_ids": sorted(row["id"] for row in parent_int), "candidate_int": candidate_int,
            "candidate_direction": candidate_direction}


def _junction_ids(root: ET.Element) -> set[str]:
    return {junction.get("id") for junction in root.findall("junction")}


def _load_report(path: Path) -> tuple[dict, Path]:
    path = Path(path)
    if path.is_dir():
        path = path / "edit-verification.json"
    return json.loads(path.read_text(encoding="utf-8")), path


def verified_parent(goal: BuildGoal, goal_ref: str, verifier: dict, path: Path) -> tuple[dict, dict]:
    """Read a parent profile and walk its recorded chain back to the goal source; returns ``(parent, record)``.

    A parent is the goal source's own profile, or a judged candidate that passed G0 and screened as accepted
    (``build_gate.ACCEPTED_SCREENINGS``) whose candidate net and verify-edit report still hash to what it recorded,
    and whose own parent (by recorded path and sha256) is again such a profile, down to the goal source. A raw-net,
    blocked or rejected profile is never a parent, so a G0 exploit cannot be laundered through an unjudged hop.
    """
    current, expected, chain, first = Path(path), None, [], None
    while True:
        data = current.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if expected is not None and digest != expected:
            raise ValueError(f"profile refused: parent chain profile {current} does not match its recorded sha256")
        profile = json.loads(data)
        first = profile if first is None else first
        if profile.get("kind") != "build" or profile.get("goal") != goal_ref:
            raise ValueError("profile refused: the parent profile belongs to another goal; diffs across goal_ids are "
                             "refused")
        if (profile.get("verifier") or {}).get("commit") != verifier.get("commit"):
            raise ValueError("profile refused: the parent profile was made by another verifier commit")
        inputs, gate = profile.get("inputs") or {}, profile.get("gate") or {}
        chain.append({"cand": profile.get("cand"), "subject": profile.get("subject"), "sha256": digest})
        if profile.get("subject") == "source":
            if (inputs.get("net") or {}).get("sha256") != goal.source.sha256 or gate.get("screening") != "source_profile":
                raise ValueError("profile refused: the parent chain does not reach the goal source")
            break
        if profile.get("subject") != "candidate":
            raise ValueError(f"profile refused: a {profile.get('subject')} profile is never a parent; only the goal "
                             "source profile or an accepted candidate is")
        if (profile.get("status") != "measured" or gate.get("G0") != "pass"
                or gate.get("screening") not in build_gate.ACCEPTED_SCREENINGS):
            raise ValueError(f"profile refused: parent {profile.get('cand')} was not accepted (status "
                             f"{profile.get('status')}, G0 {gate.get('G0')}, screening {gate.get('screening')})")
        for key, label in (("net", "candidate net"), ("verify_edit", "verify-edit report")):
            row = inputs.get(key) or {}
            try:
                intact = file_sha256(Path(row["path"])) == row.get("sha256")
            except (KeyError, OSError, TypeError):
                intact = False
            if not intact:
                raise ValueError(f"profile refused: the {label} of parent {profile.get('cand')} changed or is missing")
        grand = inputs.get("parent_profile")
        if grand is None:
            if (inputs.get("parent_net") or {}).get("sha256") != goal.source.sha256:
                raise ValueError("profile refused: the parent chain does not reach the goal source")
            break
        if len(chain) > MAX_PARENT_CHAIN:
            raise ValueError(f"profile refused: the parent chain is longer than {MAX_PARENT_CHAIN} steps")
        current, expected = Path(grand["path"]), grand.get("sha256")
    record = {"path": str(Path(path)), "sha256": chain[0]["sha256"], "cand": first.get("cand"),
              "subject": first.get("subject"), "depth": len(chain) - 1, "chain": [row["cand"] for row in chain]}
    return first, record


def build_build_profile(goal: BuildGoal, goal_bytes: bytes, facts_bytes: bytes, *, facts_path: Path,
                        repository_root: Path, verifier: dict, net: Path | None = None,
                        verify_edit: Path | None = None, parent_profile: Path | None = None,
                        scratch_dir: Path | None = None) -> tuple[dict, dict]:
    """Profile the goal source (default), a raw net (``net``, never judged) or a verify-edit candidate.

    ``parent_profile`` is a parent's ``profile.full.json`` (``verified_parent``); without one a candidate's parent is
    the goal source. ``scratch_dir`` holds the transient lane-count remap (default: the system temp directory).
    """
    if net is not None and verify_edit is not None:
        raise ValueError("Profile either a raw net or a verify-edit candidate, not both.")
    goal_ref = ids.goal_reference(goal.goal_id, goal_bytes)
    facts_sha = hashlib.sha256(facts_bytes).hexdigest()
    if ids.cas_reference(facts_sha) != goal.reference.facts:
        raise ValueError("profile refused: the fact table does not match the goal's cas reference")
    table = parse_table(facts_bytes)
    entries = [entry.model_dump(by_alias=True, exclude_none=True) for entry in goal.frozen.scored_facts]
    excluded = {key: value.model_dump(by_alias=True, exclude_none=True) for key, value in goal.frozen.excluded.items()}
    recomputed = frozen_sha256(entries, excluded)
    subject, report, ledger, report_path = "source", None, {}, None
    if verify_edit is not None:
        subject = "candidate"
        report, report_path = _load_report(verify_edit)
        ledger_path = Path(report["ledger"]["path"])
        ledger = json.loads(_read_bytes(ledger_path, report["ledger"]["sha256"], "ledger"))
        net_path = Path(report["candidate_net_file"])
    elif net is not None:
        subject, net_path = "raw_net", Path(net)
    else:
        net_path = goal_source_path(goal, repository_root)
    if parent_profile is not None and subject == "source":
        raise ValueError("profile refused: the goal source has no parent")
    parent, parent_record = (verified_parent(goal, goal_ref, verifier, Path(parent_profile))
                             if parent_profile is not None else (None, None))
    net_bytes = _read_bytes(net_path, goal.source.sha256 if subject == "source" else None, "net")
    net_sha = hashlib.sha256(net_bytes).hexdigest()
    parent_sha = parent["inputs"]["net"]["sha256"] if parent is not None else goal.source.sha256
    candidate_table, candidate_facts = table, {"sha256": facts_sha, "basis": "goal", "verified": True}
    if report is not None and ledger.get("operations", [{}])[0].get("operation") == "set_lane_count":
        remapped, candidate_facts = _remapped_facts(report, facts_path, scratch_dir)
        candidate_table = parse_table(remapped)
    root = _network_root(net_bytes)
    card = score_root(root, candidate_table)
    card_by_id = {row["id"]: row for row in card["facts"]}
    facts_by_id = {fact.id: fact for fact in candidate_table.facts}
    rows = {entry["id"]: fact_row(entry, card_by_id[entry["id"]]) for entry in entries}
    index = _NetIndex(root)
    suggestions = {fact: suggested_ops(facts_by_id[fact], card_by_id[fact], index)
                   for fact, row in rows.items() if row["status"] == "mismatched"}
    for fact, ops in suggestions.items():
        rows[fact]["suggested_ops"] = ops
        rows[fact]["est_le"] = {"class": rows[fact]["class"], "count": -1,
                                "excess": None if rows[fact]["excess"] is None else -round(rows[fact]["excess"], 9)}
    gate: dict[str, Any]
    invariants_rows: list[dict] = []
    parent_v = parent_root = None
    parent_net = None
    if report is not None:
        parent_path = Path(report["source"]["path"])
        if report["source"].get("sha256") != parent_sha:
            raise ValueError("profile refused: the ledger source is neither the goal source nor the parent profile's "
                             "net")
        parent_bytes = _read_bytes(parent_path, parent_sha, "parent net")
        parent_root = _network_root(parent_bytes)
        parent_net = {"path": str(parent_path), "sha256": parent_sha}
        cached, parent_net["scorecard"] = (_goal_scorecard(goal, facts_path, facts_sha) if parent_sha ==
                                           goal.source.sha256 else (None, "rescored: the parent is not the goal "
                                                                          "source"))
        parent_card = cached if cached is not None else score_root(parent_root, table)
        judged = _verify_edit_gate(goal, table=table, facts_sha=facts_sha, frozen_recomputed=recomputed, report=report,
                                   ledger=ledger, parent_root=parent_root, parent_sha=parent_sha, root=root,
                                   net_sha=net_sha, parent_card=parent_card, candidate_facts=candidate_facts,
                                   candidate_table=candidate_table, entries=entries, rows=rows, card=card)
        invariants_rows, parent_v = judged["g0"]["checks"], judged["v"]["parent"]
        fixed = [fact for fact, row in rows.items() if judged["parent_rows"][fact]["status"] == "mismatched"
                 and row["status"] == "matched"]
        gate = {"screening": judged["screening"], "first_fail": judged["first_fail"],
                "G0": "pass" if judged["g0"]["pass"] else "fail", "V": judged["v"], "L2": judged["l2"],
                "L3": judged["l3"], "outside_fact_changes": judged["outside"], "measurability_lost": judged["lost"],
                "regressed_facts": judged["regressed"], "fixed": fixed,
                "fixed_rows": [{"fact": fact, "parent": _row_value(judged["parent_rows"][fact]),
                                "candidate": _row_value(rows[fact])} for fact in fixed],
                "shape_changes": len(judged["shape_changes"]), "new_direction_findings": judged["new_direction"],
                "verify_edit_status": report.get("status")}
        # no_effect only when nothing materialized; verify-edit's own status stays in the gate.
        status = ("blocked" if not judged["g0"]["pass"] or report.get("status") == "blocked"
                  else "no_effect" if net_sha == parent_sha else "measured")
    elif subject == "raw_net":
        gate = {"screening": "not_judged", "G0": "not_run",
                "first_fail": "a raw net has no verify-edit materialization; it is profiled, never judged"}
        status = "measured"
    else:
        gate = {"screening": "source_profile", "G0": "not_run",
                "first_fail": "the goal source is profiled; there is no candidate to judge"}
        status = "measured"
    junctions = scope_junctions(root, goal.scope.mutable_edge_ids, goal.scope.mutable_junction_ids)
    if report is not None:
        # The gate already audited a superset of these junctions on this net; the audit is per junction.
        keep = {ids.element_id("junction", junction) for junction in junctions}
        found = [row for row in judged["candidate_int"] if row["source"] == "direction" or row["anchor"] in keep]
        direction = judged["candidate_direction"]
    else:
        found, direction = int_findings(root, junctions)
    baseline = {identity: goal.int_baseline.measures.get(identity) for identity in goal.int_baseline.finding_ids}
    parent_int = set(judged["parent_int_ids"]) if report is not None else set()
    int_rows = [{**row, **({"new_vs_parent": row["id"] not in parent_int} if report is not None else {})}
                for row in found if row["id"] not in baseline
                or (row["measure"] is not None and baseline[row["id"]] is not None
                    and row["measure"] > baseline[row["id"]] + invariants.MEASURE_TOLERANCE_M)]
    hotspots = rank_hotspots(list(rows.values()), int_rows)
    escalations = escalation_rows(excluded, card_by_id)
    v_cand = build_gate.v_vector(rows)
    operation = (ledger.get("operations") or [None])[0]
    layers = {cls: {"scored": sum(row["class"] == cls for row in rows.values()),
                    "mismatched": sum(row["class"] == cls and row["status"] == "mismatched" for row in rows.values()),
                    "excess": round(sum(row["excess"] or 0.0 for row in rows.values() if row["class"] == cls), 9)}
              for cls in TARGET_CLASSES}
    delta = None
    if parent is not None:
        before = parent.get("facts", {})
        delta = {"parent": parent.get("cand"),
                 "fixed": sorted(fact for fact, row in rows.items()
                                 if before.get(fact, [None])[0] == "mismatched" and row["status"] == "matched"),
                 "regressed": sorted(fact for fact, row in rows.items()
                                     if before.get(fact, [None])[0] == "matched" and row["status"] != "matched"),
                 "excess_changed": {fact: [before[fact][1], _rounded(row["excess"])]
                                    for fact, row in sorted(rows.items())
                                    if fact in before and before[fact][1] != _rounded(row["excess"])},
                 "hotspots_new": sorted({h["id"] for h in hotspots} - set(parent.get("hotspot_ids", []))),
                 "hotspots_gone": sorted(set(parent.get("hotspot_ids", [])) - {h["id"] for h in hotspots})}
    profile = {
        "schema": PROFILE_SCHEMA, "kind": "build", "view": "full", "status": status, "goal": goal_ref,
        "goal_id": goal.goal_id, "verifier": verifier, "subject": subject,
        "cand": ids.candidate_id(net_sha, "", "", ""),
        "parent": ids.candidate_id(parent_sha, "", "", "") if report is not None or parent is not None else None,
        "op": operation, "op_text": _op_text(operation, parent_root),
        "inputs": {"net": {"path": str(net_path), "sha256": net_sha},
                   "facts": {"ref": goal.reference.facts, "rows": len(table.facts)},
                   "source": {"path": goal.source.net, "sha256": goal.source.sha256},
                   "verify_edit": None if report is None else {"path": str(report_path),
                                                               "sha256": file_sha256(report_path),
                                                               "status": report.get("status")},
                   "parent_net": parent_net, "parent_profile": parent_record},
        "invariants": invariants_rows, "gate": gate,
        "V": {"order": list(build_gate.ORDER), "cand": v_cand, "parent": parent_v,
              "excess_rule": build_gate.EXCESS_RULE, "rule": build_gate.V_RULE},
        "layers": layers,
        "excluded": dict(sorted(Counter(row["reason"] if row["class"] not in ESCALATION_CLASSES else row["class"]
                                        for row in excluded.values()).items())),
        "rows": [_public_row(rows[fact]) for fact in sorted(rows) if rows[fact]["status"] != "matched"],
        "facts": {fact: [row["status"], _rounded(row["excess"])] for fact, row in sorted(rows.items())},
        "int_findings": int_rows,
        "direction": {key: direction[key] for key in ("rule", "checked_count", "finding_count", "ambiguous_count",
                                                       "unsupported_count")},
        "hotspots": [{**h, "rows": [_public_row(row) for row in h["rows"]]} for h in hotspots],
        "hotspot_ids": [h["id"] for h in hotspots],
        "escalations": escalations, "delta": delta, "amendments": AMENDMENTS, "claim": CLAIM}
    details = {"facts.csv": [_csv_row(rows.get(fact), excluded.get(fact), card_by_id[fact]) for fact in
                             [fact.id for fact in table.facts]],
               "int-findings.csv": [{key: (" ".join(row[key]) if key == "lanes" else row.get(key))
                                     for key in ("id", "class", "source", "anchor", "finding", "measure", "lanes",
                                                 "new_vs_parent")} for row in int_rows]}
    return profile, details


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 9)


def _row_value(row: dict) -> dict:
    return {"status": row["status"], "excess": _rounded(row["excess"]),
            **{name: part["value"] for name, part in row["components"].items()}}


def _public_row(row: dict) -> dict:
    result = {key: row[key] for key in ("id", "kind", "class", "anchor", "status") if key in row}
    if row.get("class") == "INT":
        return {**result, **{key: row[key] for key in ("source", "finding", "measure", "lanes", "new_vs_parent")
                             if key in row}}
    result["excess"] = _rounded(row.get("excess"))
    result["components"] = {name: {key: _rounded(value) if isinstance(value, float) else value
                                   for key, value in part.items()} for name, part in row.get("components", {}).items()}
    for key in ("magnitude", "reason", "suggested_ops", "est_le", "weight"):
        if row.get(key) is not None:
            result[key] = row[key]
    return result


def _csv_row(row: dict | None, frozen: dict | None, card_row: dict) -> dict:
    if row is None:
        return {"fact_id": card_row["id"], "kind": card_row["kind"], "class": frozen["class"], "scored": False,
                "status": card_row["status"], "excess": "", "anchor": frozen["anchor"], "components": "",
                "suggested_ops": "", "note": frozen["reason"]}
    return {"fact_id": row["id"], "kind": row["kind"], "class": row["class"], "scored": True, "status": row["status"],
            "excess": "" if row["excess"] is None else repr(round(row["excess"], 9)), "anchor": row["anchor"],
            "components": json.dumps({name: part["value"] for name, part in row["components"].items()},
                                     sort_keys=True),
            "suggested_ops": json.dumps(row.get("suggested_ops", []), sort_keys=True), "note": row.get("reason") or ""}


def write_build_profile(goal: BuildGoal, goal_bytes: bytes, facts_bytes: bytes, *, facts_path: Path,
                        repository_root: Path, out_dir: Path, verifier: dict, net: Path | None = None,
                        verify_edit: Path | None = None, parent_profile: Path | None = None) -> dict:
    """Write a NEW build profile directory (full JSON, capped compact JSON and text, detail CSVs); every transient
    file stays beside ``out_dir``."""
    started = time.perf_counter()
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} exists; every profile is written to a new directory")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    profile, details = build_build_profile(goal, goal_bytes, facts_bytes, facts_path=facts_path,
                                           repository_root=repository_root, verifier=verifier, net=net,
                                           verify_edit=verify_edit, parent_profile=parent_profile,
                                           scratch_dir=out_dir.parent)
    compact = dict(zip(("profile.compact.json", "profile.compact.txt"), render.render_build_compact(profile)))
    write_staged_profile(out_dir, profile, details, compact, started=started, empty_header="id")
    return profile


def lane_artifact_path(path: str, repository_root: Path) -> Path:
    """A goal's lane artifact path: repository-relative when the artifact lies in the repository."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else Path(repository_root) / candidate


def run_verify_edit(goal: BuildGoal, *, ledger_file: Path, facts_path: Path, output_dir: Path,
                    repository_root: Path, netconvert_binary: str = "netconvert",
                    routeability_seeds: tuple[int, ...] | None = None) -> dict:
    """``verify_network_edit`` in explore mode with the goal's facts, seeds and lane artifacts (unchanged).

    Its command runner writes transient ``astra-command-*`` directories under the system temp directory."""
    from ..core.network_edit_applicability import verify_network_edit

    artifacts = [lane_artifact_path(row.path, repository_root) for row in goal.guards.lane_artifacts]
    for row, path in zip(goal.guards.lane_artifacts, artifacts):
        if file_sha256(path) != row.sha256:
            raise ValueError(f"profile refused: lane artifact {row.path} changed since goal-create")
    seeds = tuple(goal.guards.routeability.seeds) if routeability_seeds is None else tuple(routeability_seeds)
    return verify_network_edit(ledger_file=ledger_file, output_dir=output_dir, netconvert_binary=netconvert_binary,
                               mode="explore", facts_file=facts_path, routeability_seeds=seeds,
                               lane_artifact_files=artifacts or None)
