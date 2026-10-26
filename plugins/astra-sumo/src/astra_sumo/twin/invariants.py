"""Twin invariants: Calibrate part 1 (M1: INV-INPUTS, INV-OBS, INV-HOLD, INV-PASS) and the Build part (M3).

Each check returns ``{"id", "pass", "witness_ids", "reason"}``; the witness ids name what violates the rule and the
reason says what was compared. Every check is a pure function of values its caller has already read.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import PureWindowsPath
from types import EllipsisType
from typing import TYPE_CHECKING

import numpy
import shapely
from shapely.geometry import LineString

from . import ids
from .build_gate import TARGET_CLASSES

if TYPE_CHECKING:
    from .count_profile import FieldLoop, Route

TIME_TOLERANCE_S = 1e-6


def _result(invariant: str, witnesses: list[str], reason: str) -> dict:
    return {"id": invariant, "pass": not witnesses, "witness_ids": sorted(set(witnesses)), "reason": reason}


def inv_inputs(expected_sha256: dict[str, str], resolved_sha256: dict[str, str | None],
               run_files: dict[str, bool] | None = None, committed: dict[str, bool] | None = None) -> dict:
    """Every goal input alias resolves to the raw SHA-256 the goal froze, and every run artifact read is pinned.

    ``run_files`` maps each baseline-run artifact the profile reads to whether its bytes are pinned by the packet
    (``PacketCase.pinned``: the manifest SHA-256 of a tracked file, or the declared raw SHA-256 of an alias); an
    artifact that cannot be read or decoded is unpinned. The manifest is itself a packet file, so ``committed`` maps
    the repository path of each artifact read to whether its tracked bytes equal the blob committed at HEAD
    (``PacketCase.committed``): a coherent edit of an artifact and its manifest row fails here.
    """
    run_files, committed = run_files or {}, committed or {}
    changed = [name for name, digest in expected_sha256.items() if resolved_sha256.get(name) != digest]
    unpinned = [f"manifest:{name}" for name, pinned in run_files.items() if not pinned]
    uncommitted = [f"git:{name}" for name, equal in committed.items() if not equal]
    return _result("INV-INPUTS", changed + unpinned + uncommitted,
                   f"{len(expected_sha256) - len(changed)}/{len(expected_sha256)} goal inputs resolve to the goal's "
                   f"sha256; {len(run_files) - len(unpinned)}/{len(run_files)} run artifacts match the packet "
                   f"manifest or their byte reference; {len(committed) - len(uncommitted)}/{len(committed)} equal their "
                   "blob committed at HEAD")


def inv_obs(*, goal_loops: dict[str, FieldLoop], run_loops: dict[str, FieldLoop], goal_attribute: str,
            run_attributes: list[str], goal_loops_sha256: str, run_loops_sha256: str | None) -> dict:
    """Counted attribute, E1 ids, lanes, positions and periods, and the field-loops hash are unchanged."""
    witnesses = [f"attribute:{value}" for value in run_attributes if value != goal_attribute]
    witnesses += [f"detector:{name}" for name in set(goal_loops) ^ set(run_loops)]
    witnesses += [f"detector:{name}" for name in set(goal_loops) & set(run_loops)
                  if goal_loops[name] != run_loops[name]]
    if run_loops_sha256 != goal_loops_sha256:
        witnesses.append("file:field-loops.add.xml")
    return _result("INV-OBS", witnesses,
                   f"{goal_attribute} against {len(run_attributes)} run records; {len(goal_loops)} goal E1 (id, lane, "
                   "pos, period) against the run's E1 file; field-loops sha256 against the run's recorded inputs")


def hold_node_sources(run: dict, request: dict, scenario: dict | None = None) -> dict[str, list[str] | None]:
    """The junction-body hold nodes as each run record (and optionally the prepared scenario) states them."""
    sources = {"run.json": run.get("junction_body_hold_nodes"),
               "native-request.json": request.get("junction_body_hold_nodes"),
               "run.json stall_monitor": (run.get("stall_monitor") or {}).get("junction_body_hold_nodes")}
    if scenario is not None:
        sources["prepared/scenario.json"] = scenario.get("junction_body_hold_nodes")
    return sources


def inv_hold(goal_nodes: list[str], recorded: dict[str, list[str] | None]) -> dict:
    """The junction-body hold nodes equal the goal list in every run record."""
    witnesses = []
    for source, nodes in recorded.items():
        if nodes is None:
            witnesses.append(f"missing:{source}")
        else:
            witnesses += [f"node:{node}" for node in set(nodes) ^ set(goal_nodes)]
    return _result("INV-HOLD", witnesses, f"the goal's {len(goal_nodes)} hold nodes against {', '.join(recorded)}")


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _in_interval(passage: dict, *, e1_begin_s: float, e1_period_s: float, step_s: float) -> bool:
    """The recorded interval is one E1 period on the goal's grid, holds the notification time, and the notification
    time is the step ceiling of ``time_s`` (``core/route_sensor_matrix.py`` bins a leave event that way)."""
    time_s, notified, begin, end = (_finite(passage.get(key)) for key in (
        "time_s", "notification_time_s", "interval_begin_s", "interval_end_s"))
    if time_s is None or notified is None or begin is None or end is None:
        return False
    slot = (begin - e1_begin_s) / e1_period_s
    ceiling = math.ceil(math.nextafter(time_s / step_s, -math.inf)) * step_s
    return (abs(end - begin - e1_period_s) <= TIME_TOLERANCE_S and abs(slot - round(slot)) <= TIME_TOLERANCE_S
            and begin <= notified < end and abs(notified - ceiling) <= TIME_TOLERANCE_S)


def _stay(route: Route | None, edge: str | None) -> tuple[float, float] | None:
    """When a vehicle's recorded route enters ``edge`` (its insertion when the route starts there) and its arrival."""
    if route is None or edge is None or edge not in route.edges:
        return None
    index = route.edges.index(edge)
    return (route.exit_s[index - 1] if index else route.depart_s), route.exit_s[-1]


def inv_pass(report: dict, intended: dict[str, str], *, e1_counts: dict[tuple[str, float], int],
             bindings: dict[str, tuple[str | None, str]], routes: dict[str, Route], field_edges: dict[str, str],
             e1_begin_s: float, e1_period_s: float, step_s: float) -> dict:
    """The passage report belongs to the profiled E1 output and reproduces its count on every scored row.

    The report's own ``reconciled`` and ``inputs_unchanged`` flags describe the run that wrote it, so they are
    required but never trusted alone. ``bindings`` maps each input file name the report must record to the SHA-256
    it must record for it and where that SHA-256 comes from; a missing, different or unexpected recorded input
    fails. ``e1_counts`` holds the profiled E1 nVehContrib per (detector, interval begin) of every scored row, which
    the report's passages must equal. At most one passage per vehicle per field, none off-intention.

    Each passage must also sit where its own fields and the run's tracked vehicle routes put it: its interval is one
    E1 period on the goal's grid holding the step ceiling of ``time_s`` (``interval:`` witnesses), and ``time_s`` lies
    within one step of its vehicle's recorded stay, from entering the field edge (``field_edges``) to arrival
    (``route:`` witnesses). A passage moved in time, or handed another vehicle's id, fails even when every row count
    still matches.
    """
    witnesses = [f"report:{flag}" for flag in ("reconciled", "inputs_unchanged") if report.get(flag) is not True]
    recorded: dict[str, list[str | None]] = {}
    for row in report.get("inputs", []):
        recorded.setdefault(PureWindowsPath(str(row.get("path", ""))).name, []).append(row.get("sha256"))
    unbound = sorted(name for name in set(recorded) | set(bindings)
                     if bindings.get(name, (None,))[0] is None or recorded.get(name) != [bindings[name][0]])
    witnesses += [f"report_input:{name}" for name in unbound]
    passages = report.get("passages", [])
    counted = Counter((p["detector_id"], float(p["interval_begin_s"])) for p in passages)
    differ = [ids.row_id(detector, begin) for (detector, begin), count in e1_counts.items()
              if counted[(detector, begin)] != count]
    witnesses += [f"count:{row}" for row in differ]
    repeated = Counter((p["vehicle_id"], p["detector_id"]) for p in passages)
    witnesses += [f"repeated:{vehicle}@{detector}" for (vehicle, detector), n in repeated.items() if n > 1]
    witnesses += [f"off_intention:{p['vehicle_id']}@{p['detector_id']}" for p in passages
                  if intended.get(p["vehicle_id"]) != p["detector_id"]]
    outside = [f"interval:{p['vehicle_id']}@{p['detector_id']}" for p in passages
               if not _in_interval(p, e1_begin_s=e1_begin_s, e1_period_s=e1_period_s, step_s=step_s)]
    away = []
    for p in passages:
        stay, time_s = _stay(routes.get(p["vehicle_id"]), field_edges.get(p["detector_id"])), _finite(p.get("time_s"))
        if stay is None or time_s is None or not stay[0] - step_s <= time_s <= stay[1] + step_s:
            away.append(f"route:{p['vehicle_id']}@{p['detector_id']}")
    witnesses += outside + away
    inputs = "; ".join(f"{name} sha256 {(digest or 'undeclared')[:8]} "
                       f"{'is not what the report records:' if name in unbound else 'recorded, equals'} {source}"
                       for name, (digest, source) in sorted(bindings.items()))
    extra = sorted(set(recorded) - set(bindings))
    return _result("INV-PASS", witnesses,
                   f"report inputs: {inputs}" + (f"; unexpected inputs {', '.join(extra)}" if extra else "")
                   + f"; passages equal E1 nVehContrib on {len(e1_counts) - len(differ)}/{len(e1_counts)} scored "
                   f"rows; {len(passages) - len(outside)}/{len(passages)} passages inside their recorded E1 interval "
                   f"at the step ceiling of time_s; {len(passages) - len(away)}/{len(passages)} within their vehicle's "
                   "stay on the field edge in vehicle-routes.xml; reconciled and inputs_unchanged as the report "
                   "recorded them")


# ---- Build part (M3) ----------------------------------------------------------------------------------------------

E1_TAGS = frozenset({"inductionLoop", "instantInductionLoop"})
EPSILON = 1e-9
# A measured finding (an endpoint gap, a width-transition offset) that grows at all is a regression even when its
# identity already exists on the parent. The audit reports measures at 0.001 m, so this numerical epsilon only absorbs
# float parsing; it is no tolerance.
MEASURE_EPSILON_M = 1e-9
# Decision D3: a candidate vertex within INHERITED_VERTEX_M of a parent vertex of the same lane whose signed turn
# equals the parent's turn there within INHERITED_TURN_DEG is inherited (unchanged geometry) and exempt from
# max_heading_change_deg; each parent vertex exempts at most one candidate vertex. The same must hold against the goal
# source's lane (through the composed lane remap), so a chain cannot drift an exempt bend step by step.
INHERITED_VERTEX_M = 0.01
INHERITED_TURN_DEG = 0.5
# Decision D2: a convBoundary must equal the bounding box recomputed from the candidate's own geometry within this
# distance on each of its four values, whether it changed or not (no exception for a parent already off its box).
CONV_BOUNDARY_TOLERANCE_M = 0.01
# Decision D2: <location> attributes that must be byte-identical to the parent's (every attribute but convBoundary).
FRAME_DERIVED = "convBoundary"
# Decision D2 frame anchor: a junction outside the step's mutable scope keeps its position within half netconvert's
# 0.01 m coordinate step (the most a rewrite of the same position changes it); a representation epsilon, not a
# tolerance. Translating all geometry with netOffset kept is the frame change that netOffset identity forbids.
FRAME_ANCHOR_EPSILON_M = 0.005
# INV-COVERAGE compares these coverage measures of an improved centerline (larger is better), plus every band of
# coverage_within_m.
COVERAGE_MEASURES = ("reference_coverage", "covered_reference_length_m")
# INV-SHAPE samples a candidate lane this densely and bounds its directed Hausdorff distance between samples
# (``directed_hausdorff_bound_m``); intervals near the limit are resampled until the bound is within the resolution.
HAUSDORFF_SPACING_M = 0.1
HAUSDORFF_RESOLUTION_M = 0.001
# INV-SHAPE progress rule: a candidate sample may fall behind the furthest parent position reached so far by at most
# this much (netconvert writes coordinates at 0.01 m); it is a representation epsilon, not a tolerance.
PROGRESS_EPSILON_M = 0.01
# SUMO's default lane width, used when an added lane declares none.
DEFAULT_LANE_WIDTH_M = 3.2


def _measured(items) -> dict[str, list[float | None]]:
    grouped: dict[str, list[float | None]] = {}
    for item in items:
        identity, measure = (item, None) if isinstance(item, str) else item
        grouped.setdefault(identity, []).append(measure)
    return grouped


def measured_regressions(parent, candidate, tolerance: float = MEASURE_EPSILON_M,
                         baseline: dict[str, float | None] | None = None) -> tuple[list[str], list[str]]:
    """(new, worse) between two lists of ``(identity, measure or None)`` (a bare identity has no measure).

    ``new`` is the multiset of candidate identities beyond the parent's (and the baseline's, which excuses one each);
    ``worse`` names an identity whose largest measures, paired largest with largest against the parent's (or else
    the baseline's), grew by more than ``tolerance``.
    """
    before, after, baseline = _measured(parent), _measured(candidate), baseline or {}
    new, worse = [], []
    for identity, values in sorted(after.items()):
        known = before.get(identity) or ([baseline[identity]] if identity in baseline else [])
        new += [identity] * max(0, len(values) - len(known))
        reference = sorted((value for value in known if value is not None), reverse=True)
        measured = sorted((value for value in values if value is not None), reverse=True)
        if any(b > a + tolerance + EPSILON for a, b in zip(reference, measured)):
            worse.append(identity)
    return new, worse


def inv_facts(*, goal_facts_sha256: str, facts_sha256: str, frozen_sha256: str, frozen_recomputed: str,
              candidate_facts: dict, parent_facts: dict | None = None) -> dict:
    """The goal's fact table and frozen scored set are the ones the candidate was scored with.

    ``parent_facts`` and ``candidate_facts`` are effective fact-table records ``{"sha256", "basis", "verified"}``:
    the goal table itself (``goal``), the parent's own remapped table carried over (``inherited``), or the selector
    remap a lane-count edit declares, whose bytes were reproduced from the parent's effective table (``declared_remap``,
    ``verified``). Remaps compose along the parent chain.
    """
    parent_facts = parent_facts or {"basis": "goal", "sha256": goal_facts_sha256, "verified": True}
    witnesses = []
    if facts_sha256 != goal_facts_sha256:
        witnesses.append("facts:goal_table")
    if frozen_recomputed != frozen_sha256:
        witnesses.append("frozen:scored_set")
    for role, record in (("parent", parent_facts), ("candidate", candidate_facts)):
        basis = record.get("basis")
        if basis == "goal" and record.get("sha256") != goal_facts_sha256:
            witnesses.append(f"facts:{role}_table")
        elif basis not in {"goal", "inherited", "declared_remap"} or record.get("verified") is not True:
            witnesses.append(f"facts:{role}_remap")
    if candidate_facts.get("basis") == "inherited" and candidate_facts.get("sha256") != parent_facts.get("sha256"):
        witnesses.append("facts:candidate_not_parent_table")
    return _result("INV-FACTS", witnesses,
                   f"fact table sha256 {facts_sha256[:12]} against the goal's {goal_facts_sha256[:12]}; frozen scored "
                   f"set recomputed against its recorded sha256; parent scored with the {parent_facts.get('basis')} "
                   f"table and the candidate with the {candidate_facts.get('basis')} table "
                   f"({candidate_facts.get('remaps', 0)} composed lane-count remap(s)); no correspondence lock file "
                   "is declared (mappings are frozen in the goal's fact table)")


def failure_rows(rows) -> list[dict]:
    """``(identity, measure or None)`` structural failures as ``{"id", "measure"}`` records (sorted as given)."""
    return [{"id": identity, "measure": measure} for identity, measure in rows]


def inv_materialize(*, report_status: str | None, candidate_sha256: str | None, declared_sha256: str | None,
                    structural_failures: int | None, load_status: str | None, source_immutable: bool,
                    source_sha256: str | None, parent_sha256: str, structure: dict[str, list],
                    source_dirty: bool | None = None) -> dict:
    """The profiled candidate is exactly what verify-edit materialized from the parent, its structural failures are
    the goal source's (decision D1), and it loads in SUMO.

    ``structure`` holds the network-wide connection-mode structural failures ``{"source": [...], "candidate":
    [...]}`` as ``(identity, measured metres or None)``: the GOAL SOURCE's (never the immediate parent's, so a chain
    cannot accumulate failures step by step) renamed into the candidate's lanes through its composed lane remap, and
    the candidate's, recounted with verify-edit's own network-wide audit (verify-edit records only the count, which
    must equal the recount). A clean source still requires exactly 0 failures. On a source with failures the
    candidate's must be a sub-multiset of the source's, with no measure grown beyond ``MEASURE_EPSILON_M``
    (``measured_regressions``); the row then carries ``source_dirty`` and the inherited list, and the candidate is
    not promotable without a human.

    ``source_dirty`` is whether the goal source itself has structural failures, before the remap (default: whether the
    renamed list is non-empty). A source whose every failure sits on a lane the remap removed stays dirty: its
    inherited list is empty, so the candidate may have no failure, but the row still says dirty.
    """
    witnesses = []
    if report_status not in {"measured", "no_effect"}:
        witnesses.append(f"verify_edit:{report_status}")
    if candidate_sha256 is None or candidate_sha256 != declared_sha256:
        witnesses.append("candidate:sha256")
    if structural_failures != len(structure["candidate"]):
        witnesses.append(f"structure:recount:{len(structure['candidate'])}")
    if load_status != "pass":
        witnesses.append(f"load:{load_status}")
    if not source_immutable:
        witnesses.append("source:changed")
    if source_sha256 != parent_sha256:
        witnesses.append("source:not_parent")
    source, candidate = structure["source"], structure["candidate"]
    dirty = bool(source) or bool(source_dirty)
    if dirty:
        new, worse = measured_regressions(source, candidate)
        witnesses += [f"structure:{failure}" for failure in new] + [f"worse:{failure}" for failure in worse]
        rule = (f"the candidate's structural failures a subset of the goal source's ({len(source)} after the composed "
                f"lane remap), no measure grown beyond {MEASURE_EPSILON_M:g} m (D1, dirty source: not promotable "
                "without a human)")
    else:
        witnesses += [f"structure:{failure}" for failure, _ in candidate]
        if structural_failures != 0:
            witnesses.append(f"structure:count:{structural_failures}")
        rule = "structural failures 0 (D1, clean goal source)"
    result = _result("INV-MATERIALIZE", witnesses,
                     f"verify-edit status measured or no_effect; candidate bytes equal its recorded candidate_sha256; "
                     f"{rule}: verify-edit counts {structural_failures}, recounted {len(candidate)}; SUMO load pass; "
                     "source unchanged and equal to the parent net")
    result.update(decision="D1", source_dirty=dirty, inherited=failure_rows(source))
    return result


def inv_opscope(*, operations: list[dict], goal_ops: list[str], max_ops: int, targets: dict[str, list[str]],
                goal_edges: list[str], goal_junctions: list[str], outside_fact_changes: list[str] | None) -> dict:
    """One typed op per round, of a goal op type, aimed inside the goal scope, with no fact change outside it."""
    witnesses = [f"op:{row.get('operation')}" for row in operations if row.get("operation") not in goal_ops]
    if not operations or len(operations) > max_ops:
        witnesses.append(f"ops:{len(operations)}")
    witnesses += [f"edge:{edge}" for edge in targets.get("edges", []) if edge not in goal_edges]
    witnesses += [f"junction:{node}" for node in targets.get("junctions", []) if node not in goal_junctions]
    if outside_fact_changes is None:
        witnesses.append("outside:not_measured")
    else:
        witnesses += [f"outside:{fact}" for fact in outside_fact_changes]
    return _result("INV-OPSCOPE", witnesses,
                   f"{len(operations)} op(s) against max {max_ops} of {len(goal_ops)} goal op types; target elements "
                   "against the goal's mutable edges and junctions; verify-edit outside_fact_changes must be "
                   "measured and empty")


def inv_measurable(*, lost: list[str], lost_components: list[str]) -> dict:
    """Every frozen scored fact stays judged, and every excess component measured on the parent stays measured."""
    return _result("INV-MEASURABLE", [f"lost:{fact}" for fact in lost] + [f"component:{c}" for c in lost_components],
                   "frozen scored facts matched or mismatched on the parent must stay so (compare_calibration_"
                   "scorecards measurability_lost), and each numeric component (offset, coverage, IoU) measured on "
                   "the parent must stay measured")


def coverage_measures(metrics: dict) -> dict[str, float]:
    """The coverage measures of one centerline scorecard row (larger is better), every band included."""
    result = {name: metrics[name] for name in COVERAGE_MEASURES if isinstance(metrics.get(name), (int, float))}
    result.update({f"coverage_within_m:{band}": value for band, value in sorted(
        (metrics.get("coverage_within_m") or {}).items()) if isinstance(value, (int, float))})
    return result


def inv_coverage(improved: list[tuple[str, dict[str, float], dict[str, float]]]) -> dict:
    """No coverage measure decreases on any centerline whose lateral offset or excess improved.

    ``improved`` holds ``(fact id, parent measures, candidate measures)`` from ``coverage_measures``: the share and
    length of reference samples with an aligned foot at any distance, and the share within every band. A shortened
    lane that cuts off badly placed samples keeps its within-tolerance coverage but loses some of these.
    """
    witnesses = [f"coverage:{fact}" for fact, before, after in improved
                 if any(name not in after or after[name] < value - EPSILON for name, value in before.items())]
    return _result("INV-COVERAGE", witnesses,
                   f"{len(improved)} improved centerline(s): reference coverage, covered length and coverage within "
                   "every band must not decrease (a shortened or cut lane cannot buy a smaller lateral offset)")


def _turns(points: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """``(x, y, signed turn degrees)`` of every interior vertex (repeated points dropped): counter-clockwise turns are
    positive, so a bend mirrored to the other side at the same vertex has the opposite sign."""
    unique = [point for index, point in enumerate(points) if index == 0 or point != points[index - 1]]
    rows = []
    for a, b, c in zip(unique, unique[1:], unique[2:]):
        first, second = math.atan2(b[1] - a[1], b[0] - a[0]), math.atan2(c[1] - b[1], c[0] - b[0])
        rows.append((b[0], b[1], math.degrees((second - first + math.pi) % (2 * math.pi) - math.pi)))
    return rows


def turn_difference_deg(first: float, second: float) -> float:
    """The angle between two signed turns in degrees, wrapped to [0, 180] (a turn of +179.9 equals -179.9 within
    0.2)."""
    return abs((first - second + 180.0) % 360.0 - 180.0)


def _samples(candidate: list[tuple[float, float]], spacing_m: float):
    """The candidate polyline sampled at most ``spacing_m`` apart along every segment, every vertex included."""
    return shapely.points(shapely.get_coordinates(shapely.segmentize(LineString(candidate), spacing_m)))


def directed_hausdorff_m(candidate: list[tuple[float, float]], parent: list[tuple[float, float]],
                         spacing_m: float = HAUSDORFF_SPACING_M) -> float:
    """Largest distance from the candidate polyline, sampled every ``spacing_m`` (every vertex included), to the
    parent polyline.

    Every sample counts, including one whose nearest parent point is an endpoint: a lane extended beyond a parent end
    lies as far from the parent as its new end. No longitudinal-extension exception is defined. The true distance can
    exceed this sampled value between samples, so a gate compares ``directed_hausdorff_bound_m`` instead.
    """
    if len(candidate) < 2 or len(parent) < 2:
        return 0.0 if candidate == parent else math.inf
    return float(shapely.distance(_samples(candidate, spacing_m), LineString(parent)).max())


def directed_hausdorff_bound_m(candidate: list[tuple[float, float]], parent: list[tuple[float, float]], *,
                               limit_m: float | None = None, spacing_m: float = HAUSDORFF_SPACING_M,
                               resolution_m: float = HAUSDORFF_RESOLUTION_M) -> tuple[float, float]:
    """``(sampled, bound)``: the largest distance from the candidate's samples (every ``spacing_m``, every vertex
    included) to the parent polyline, and an upper bound of the true directed Hausdorff distance.

    The distance to the parent is 1-Lipschitz along the candidate, so between two samples ``h`` apart at distances
    ``a`` and ``b`` it never exceeds ``(a + b + h) / 2``; the bound is the largest such value. Every interval whose
    bound exceeds ``limit_m`` is resampled every ``2 * resolution_m``, so there the bound lies within ``resolution_m``
    of the true distance: with a limit, a true distance at or below it never bounds above ``limit_m +
    resolution_m``, and one above ``limit_m + resolution_m`` always does."""
    if len(candidate) < 2 or len(parent) < 2:
        return (0.0, 0.0) if candidate == parent else (math.inf, math.inf)
    line = LineString(parent)
    coords = shapely.get_coordinates(shapely.segmentize(LineString(candidate), spacing_m))
    distance = shapely.distance(shapely.points(coords), line)
    steps = numpy.hypot(*numpy.diff(coords, axis=0).T)
    bounds = (distance[:-1] + distance[1:] + steps) / 2
    sampled = float(distance.max())
    coarse = numpy.flatnonzero(bounds > limit_m + EPSILON) if limit_m is not None else numpy.array([], dtype=int)
    if coarse.size:
        pieces = numpy.maximum(1, numpy.ceil(steps[coarse] / (2 * resolution_m))).astype(int)
        counts = pieces + 1
        owner = numpy.repeat(numpy.arange(coarse.size), counts)
        position = numpy.arange(counts.sum()) - numpy.repeat(numpy.cumsum(counts) - counts, counts)
        share = (position / numpy.repeat(pieces, counts))[:, None]
        start, end = coords[coarse][owner], coords[coarse + 1][owner]
        fine = shapely.distance(shapely.points(start + (end - start) * share), line)
        same = owner[:-1] == owner[1:]
        pairs = (fine[:-1] + fine[1:] + (steps[coarse] / pieces)[owner[:-1]]) / 2
        refined = numpy.full(coarse.size, -math.inf)
        numpy.maximum.at(refined, owner[:-1][same], pairs[same])
        bounds[coarse] = refined
        sampled = max(sampled, float(fine.max()))
    return sampled, max(sampled, float(bounds.max()))


def backward_travel_m(candidate: list[tuple[float, float]], parent: list[tuple[float, float]],
                      spacing_m: float = HAUSDORFF_SPACING_M) -> tuple[float, int]:
    """``(largest backward travel in metres, samples behind)`` of the candidate's samples projected onto the parent.

    Walking the candidate forward, the projection parameter of each sample onto the parent polyline must never fall
    behind the largest one reached so far: a lane that loops or doubles back (each turn small, the vertex count the
    parent's, the excursion inside the Hausdorff limit) or runs reversed moves backward along the parent."""
    if len(candidate) < 2 or len(parent) < 2:
        return 0.0, 0
    positions = shapely.line_locate_point(LineString(parent), _samples(candidate, spacing_m))
    behind = numpy.maximum.accumulate(positions) - positions
    return float(behind.max()), int((behind > PROGRESS_EPSILON_M).sum())


def shape_change(parent: list[tuple[float, float]] | None, candidate: list[tuple[float, float]], *,
                 edge_lanes: list[list[tuple[float, float]]] | None = None, width_m: float | None = None,
                 limit_m: float | None = None, spacing_m: float = HAUSDORFF_SPACING_M,
                 source: list[tuple[float, float]] | None | EllipsisType = ...) -> dict:
    """Vertex delta, every interior turn of both shapes, the candidate-to-parent directed Hausdorff distance (sampled
    every ``spacing_m``, with its upper bound refined near ``limit_m``, the goal's max_directed_hausdorff_m;
    ``directed_hausdorff_bound_m``) and the backward travel along the parent.

    ``source`` is the same lane on the goal source (through the composed lane remap; None when the goal source has no
    counterpart; omitted when the parent is the goal source): its turns (``source_turns``) anchor decision D3's
    inherited bends (``bounded_turns``).

    A lane without a parent counterpart (None, added by a lane-count edit) has no vertex delta. When its edge has
    lanes on the parent (``edge_lanes``) it is judged against them: its distance to the nearest of them, which may
    exceed the limit by its own width (``width_m``, ``allowance_m``), and its progress along that nearest lane. A lane
    of an edge the parent lacks has no reference and is judged on its turns only."""
    parent_turns, candidate_turns = _turns(parent or []), _turns(candidate)
    source_turns = list(parent_turns) if source is ... else _turns(source or [])
    result = {"vertex_delta": None if parent is None else abs(len(candidate) - len(parent)),
              "max_turn_parent_deg": max((abs(row[2]) for row in parent_turns), default=0.0),
              "max_turn_candidate_deg": max((abs(row[2]) for row in candidate_turns), default=0.0),
              "parent_turns": parent_turns, "candidate_turns": candidate_turns, "source_turns": source_turns,
              "spacing_m": spacing_m,
              "directed_hausdorff_m": None, "directed_hausdorff_bound_m": None, "allowance_m": 0.0,
              "backward_m": None, "backward_samples": None}
    reference = parent
    if parent is None:
        lanes = [lane for lane in edge_lanes or [] if len(lane) >= 2]
        result["added"] = True
        if not lanes or len(candidate) < 2:
            return result
        reference = min(lanes, key=lambda lane: directed_hausdorff_m(candidate, lane, spacing_m))
        result["allowance_m"] = DEFAULT_LANE_WIDTH_M if width_m is None else float(width_m)
    distance, bound = directed_hausdorff_bound_m(
        candidate, reference, limit_m=None if limit_m is None else limit_m + result["allowance_m"],
        spacing_m=spacing_m)
    backward, behind = backward_travel_m(candidate, reference, spacing_m)
    result.update(directed_hausdorff_m=distance, directed_hausdorff_bound_m=bound, backward_m=backward,
                  backward_samples=behind)
    return result


def bounded_turns(change: dict, limit_deg: float) -> tuple[list[tuple[float, float, float]],
                                                             list[tuple[float, float, float]]]:
    """``(bounded, inherited)`` candidate turns above ``limit_deg`` under decision D3.

    A candidate vertex is inherited, and exempt from the limit, when an unused parent vertex of the same lane (the
    parent counterpart ``shape_change`` was given, through the lane remap) lies within ``INHERITED_VERTEX_M`` of it
    and turns by the same signed angle within ``INHERITED_TURN_DEG`` (a bend mirrored to the other side is not the
    parent's), AND an unused vertex of the same lane on the goal source (``source_turns``, through the composed lane
    remap) does too: the exemption is anchored to the goal source as decision D1 is, so a chain cannot sharpen an
    exempt bend by up to ``INHERITED_TURN_DEG`` per step. Only a violation the goal source already has is inherited:
    the matching source vertex must itself turn by more than ``limit_deg``, so a turn within the limit there (34.6 deg
    against 35) cannot be sharpened past it (35.05 deg) as "inherited". The nearest matching vertex is used on each,
    so each parent and each source vertex exempts one candidate vertex at most. Every other turn above the limit is
    bounded (a witness). A lane without a parent or a goal-source counterpart inherits nothing."""
    available = [list(change["parent_turns"]), list(change.get("source_turns", change["parent_turns"]))]
    bounded, inherited = [], []
    for x, y, turn in change["candidate_turns"]:
        if abs(turn) <= limit_deg + EPSILON:
            continue
        nearest = []
        for index, rows in enumerate(available):
            matches = [row for row in rows if math.hypot(row[0] - x, row[1] - y) <= INHERITED_VERTEX_M + EPSILON
                       and turn_difference_deg(row[2], turn) <= INHERITED_TURN_DEG + EPSILON
                       and (index == 0 or abs(row[2]) > limit_deg + EPSILON)]
            nearest.append(min(matches, key=lambda row: math.hypot(row[0] - x, row[1] - y)) if matches else None)
        if all(row is not None for row in nearest):
            for rows, row in zip(available, nearest):
                rows.remove(row)
            inherited.append((x, y, turn))
        else:
            bounded.append((x, y, turn))
    return bounded, inherited


def inv_shape(changes: dict[str, dict], g0_shape: dict) -> dict:
    """Changed road-lane shapes stay within the goal's declared ``g0_shape`` regularity: vertex delta, every interior
    turn of the candidate shape (heading change) that is not an inherited bend (decision D3), the candidate-to-parent
    directed Hausdorff distance and progress along the parent.

    D3 exempts only unchanged geometry: a candidate vertex that coincides with a parent vertex of the same lane
    within ``INHERITED_VERTEX_M`` and turns as the parent does there (signed) within ``INHERITED_TURN_DEG``, and does
    the same against the goal source's lane (``bounded_turns``); every other turn of a changed lane is bounded
    strictly. Inherited bends are reported as ``[x, y, signed turn]``. The distance is judged by its upper
    bound (``directed_hausdorff_bound_m``: 1-Lipschitz between samples every ``HAUSDORFF_SPACING_M``, resampled near
    the limit), which may exceed the limit by at most the numerical resolution ``HAUSDORFF_RESOLUTION_M``, so a true
    distance above the limit never passes between samples. The progress rule rejects a lane that falls behind the
    furthest parent position it reached by more than ``PROGRESS_EPSILON_M`` (a loop, a doubling back or a reversal,
    however small each turn). A lane added by a lane-count edit is judged against the parent lanes of its edge (its
    distance may exceed the limit by its own width) and on its progress along the nearest of them; a lane of an edge
    the parent lacks is judged on its turns only. Neither inherits a bend.
    """
    witnesses, inherited = [], {}
    for element, change in sorted(changes.items()):
        if change["vertex_delta"] is not None and change["vertex_delta"] > g0_shape["max_vertex_delta"]:
            witnesses.append(f"vertex_delta:{element}")
        bound = change.get("directed_hausdorff_bound_m", change["directed_hausdorff_m"])
        limit = g0_shape["max_directed_hausdorff_m"] + change.get("allowance_m", 0.0)
        if bound is not None and bound > limit + HAUSDORFF_RESOLUTION_M:
            witnesses.append(f"hausdorff:{element}")
        if (change.get("backward_m") or 0.0) > PROGRESS_EPSILON_M + EPSILON:
            witnesses.append(f"direction:{element}")
        bounded, exempt = bounded_turns(change, g0_shape["max_heading_change_deg"])
        if bounded:
            witnesses.append(f"heading:{element}")
        if exempt:
            inherited[element] = [[round(value, 6) for value in row] for row in exempt]
    added = sum(1 for change in changes.values() if change.get("added"))
    result = _result("INV-SHAPE", witnesses,
                     f"{len(changes)} changed road lane shape(s) ({added} added) against g0_shape: vertex delta <= "
                     f"{g0_shape['max_vertex_delta']}, every interior turn <= {g0_shape['max_heading_change_deg']} "
                     f"deg except an inherited bend (D3: within {INHERITED_VERTEX_M:g} m of a vertex of the same lane "
                     f"on the parent and on the goal source, turning as each does there, signed, within "
                     f"{INHERITED_TURN_DEG:g} deg, where the goal source already exceeds the limit; "
                     f"{sum(len(rows) for rows in inherited.values())} exempt), directed "
                     "Hausdorff upper bound "
                     f"(1-Lipschitz between samples every {HAUSDORFF_SPACING_M:g} m, resampled near the limit; "
                     f"endpoint projections included) <= {g0_shape['max_directed_hausdorff_m']} m + "
                     f"{HAUSDORFF_RESOLUTION_M:g} m resolution (an added lane: plus its width, against its edge's "
                     f"parent lanes), no sample more than {PROGRESS_EPSILON_M:g} m behind the furthest parent position "
                     f"already reached ({g0_shape.get('status', 'declared')})")
    result.update(decision="D3", inherited_bends=inherited)
    return result


def _boundary(text: str | None) -> list[float] | None:
    try:
        values = [float(value) for value in str(text).split(",")]
    except ValueError:
        return None
    return values if len(values) == 4 and all(math.isfinite(value) for value in values) else None


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def frame_anchor(parent: dict[str, tuple[float, float]], candidate: dict[str, tuple[float, float]],
                 scope: list[str], source: dict[str, tuple[float, float]] | None = None) -> tuple[list[str], str]:
    """``(witnesses, text)`` of decision D2's frame anchor over non-internal junction positions ``{id: (x, y)}``.

    Every junction the parent and the candidate share outside the step's mutable ``scope`` keeps its parent position
    within ``FRAME_ANCHOR_EPSILON_M``; one the chain has kept at the goal source's position (``source``, the parent
    within the epsilon of it) also keeps that position, so the epsilon cannot accumulate along a chain. At least one
    shared junction keeps its parent position (``anchor:none_fixed``, for a scope that holds them all)."""
    source, inside = source or {}, set(scope)
    shared = sorted(parent.keys() & candidate.keys())
    moved = [node for node in shared if _distance(parent[node], candidate[node]) > FRAME_ANCHOR_EPSILON_M + EPSILON]
    drifted = [node for node in shared if node not in moved and node in source
               and _distance(parent[node], source[node]) <= FRAME_ANCHOR_EPSILON_M + EPSILON
               and _distance(candidate[node], source[node]) > FRAME_ANCHOR_EPSILON_M + EPSILON]
    outside = sorted(node for node in moved + drifted if node not in inside)
    witnesses = [f"anchor:junction:{node}" for node in outside]
    if shared and len(moved) == len(shared):
        witnesses.append("anchor:none_fixed")
    return witnesses, (f"{len(shared) - len(moved)}/{len(shared)} shared junctions keep their parent position within "
                       f"{FRAME_ANCHOR_EPSILON_M:g} m ({len(drifted)} drifted from the goal source's), {len(outside)} "
                       f"moved outside the step's {len(inside)} mutable junction(s)")


def inv_frame(parent_locations: list[dict], candidate_locations: list[dict],
              recomputed_boundary: list[float] | None, *, anchor: dict | None = None) -> dict:
    """The candidate's ``<location>`` frame is the parent's (decision D2).

    Every ``<location>`` element of each net counts (``root.iter``, nested ones too): SUMO applies the last one while
    netconvert and the scorer read the first, so a candidate must carry exactly one, as the parent does
    (``location:element``), and every copy it carries is compared. netOffset, origBoundary and projParameter (every
    attribute but convBoundary) must be byte-identical to the parent's. convBoundary must be ``recomputed_boundary``,
    the bounding box ``build_profile`` recomputes from the candidate's own geometry, within
    ``CONV_BOUNDARY_TOLERANCE_M`` on each value, whether it changed or not: an unchanged convBoundary behind geometry
    that grew past it is stale, and a parent convBoundary already off its own box is no exception (it would let the
    box drift step by step while convBoundary stays wrong).

    ``anchor`` holds the non-internal junction positions ``{"parent": {id: (x, y)}, "candidate": {...}, "source":
    {...}}`` and the step's mutable junctions (``"scope"``): a translation of all geometry with netOffset kept changes
    the frame as a netOffset change does (``frame_anchor``)."""
    before = parent_locations[-1] if parent_locations else {}
    copies = candidate_locations or [{}]
    witnesses = []
    if len(candidate_locations) != len(parent_locations) or len(candidate_locations) > 1:
        witnesses.append("location:element")
    parent_conv = before.get(FRAME_DERIVED)
    shown = "none" if recomputed_boundary is None else ",".join(f"{value:.6f}" for value in recomputed_boundary)
    conv = []
    for after in copies:
        witnesses += [f"location:{key}" for key in sorted(before.keys() | after.keys())
                      if key != FRAME_DERIVED and before.get(key) != after.get(key)]
        value = after.get(FRAME_DERIVED)
        if value is None:
            deviation = 0.0 if parent_conv is None else None
            conv.append("absent on both" if parent_conv is None else f"removed (was {parent_conv})")
        else:
            values = _boundary(value)
            deviation = (None if values is None or recomputed_boundary is None else
                         max(abs(a - b) for a, b in zip(values, recomputed_boundary)))
            conv.append(("unchanged " + value if value == parent_conv else f"changed from {parent_conv} to {value}")
                        + f"; recomputed {shown}; deviation "
                        + ("unmeasured" if deviation is None else f"{deviation:.6f} m"))
        if deviation is None or deviation > CONV_BOUNDARY_TOLERANCE_M + EPSILON:
            witnesses.append(f"location:{FRAME_DERIVED}")
    anchored = "not checked"
    if anchor is not None:
        found, anchored = frame_anchor(anchor["parent"], anchor["candidate"], anchor.get("scope") or [],
                                       anchor.get("source"))
        witnesses += found
    result = _result("INV-FRAME", witnesses,
                     f"{len(candidate_locations)} location element(s) against the parent's {len(parent_locations)} "
                     "(exactly one); netOffset, origBoundary and projParameter byte-identical to the parent's; "
                     "convBoundary equal to the bounding box recomputed from the candidate's geometry within "
                     f"{CONV_BOUNDARY_TOLERANCE_M:g} m (D2): convBoundary {' | '.join(conv)}; frame anchor: {anchored}")
    result["decision"] = "D2"
    return result


def inv_artifacts(declared: list[dict], checked: dict | None) -> dict:
    """Every lane artifact the goal declares was checked on the candidate and lies inside its lane length."""
    if not declared:
        return _result("INV-ARTIFACTS", [], "the goal declares no lane artifacts; positions are not checked")
    checked = checked or {}
    witnesses = []
    if sorted(row["sha256"] for row in declared) != sorted(row["sha256"] for row in checked.get("files", [])):
        witnesses.append("files:not_the_declared_artifacts")
    for row in checked.get("artifacts", []):
        if row.get("inside") is False:
            witnesses.append(f"outside:{row.get('id') or row.get('tag')}")
        elif row.get("inside") is None:
            witnesses.append(f"unchecked:{row.get('id') or row.get('tag')}")
    return _result("INV-ARTIFACTS", witnesses,
                   f"{len(declared)} declared artifact file(s), by sha256, against verify-edit "
                   f"lane_coordinate_artifacts (status {checked.get('status', 'not_run')}); a multi-lane artifact "
                   "cannot be checked and fails")


def inv_e1_lane(declared: list[dict], checked: dict | None) -> dict:
    """No lane that hosts an E1 detector of the declared artifacts changes its coordinates."""
    if not declared:
        return _result("INV-E1-LANE", [], "the goal declares no lane artifacts, so no E1 lane is known")
    rows = [row for row in (checked or {}).get("artifacts", []) if row.get("tag") in E1_TAGS]
    witnesses = [f"e1:{row.get('id')}@{row.get('lane_id')}" for row in rows if row.get("lane_coordinates_changed")]
    if checked is None:
        witnesses.append("e1:not_checked")
    return _result("INV-E1-LANE", witnesses,
                   f"{len(rows)} E1 detector(s) on declared lanes; a changed lane coordinate moves what is counted")


def inv_cor(scored_classes: dict[str, str], mapping_changes: list[str]) -> dict:
    """No escalated fact is a task: the frozen scored set holds only target classes and mappings stay frozen."""
    witnesses = [f"class:{fact}" for fact, cls in sorted(scored_classes.items()) if cls not in TARGET_CLASSES]
    witnesses += [f"mapping:{fact}" for fact in mapping_changes]
    return _result("INV-COR", witnesses,
                   f"{len(scored_classes)} frozen scored facts hold no COR, SRC, OPX or UNK class; correspondence "
                   "fields of the candidate's fact table equal the goal's (frozen at goal-create, never recomputed)")


def inv_green(new_conflicts: list[dict]) -> dict:
    """No new protected-green foe conflict in the mutable junctions."""
    witnesses = [f"green:{row.get('junction_id')}:{ids.short_hash(repr(sorted(row.items())))}"
                 for row in new_conflicts]
    return _result("INV-GREEN", witnesses,
                   "verify-edit new_protected_green_conflicts (inherited source conflicts are not witnesses)")


def inv_audit(parent_rows: list, candidate_rows: list, junctions: list[str],
              baseline: dict[str, float | None] | None = None) -> dict:
    """No audit finding (connection mode, request matrix, independent movement safety) in the scope junctions that
    neither the parent nor the goal's frozen INT baseline already has (restoring the reference's own state is never
    a regression), and no measured finding (an endpoint gap) that grew at all (``MEASURE_EPSILON_M``) against the
    parent, or against the baseline for a finding only the baseline has.

    Rows are ``(identity, measured metres or None)`` or bare identities, all in the candidate's lane naming: the caller
    renames the parent's rows through the step's declared lane remap and the baseline through the candidate's composed
    chain mapping (``junction_integrity.comparable``, ``build_profile.remapped_baseline``), so an inherited finding on
    a renamed lane is not new and a renamed key cannot mask a new one.
    """
    baseline = baseline or {}
    new, worse = measured_regressions(parent_rows, candidate_rows, baseline=baseline)
    return _result("INV-AUDIT", new + [f"worse:{identity}" for identity in worse],
                   f"connection-mode, request-matrix and independent-safety findings on {len(junctions)} scope "
                   f"junction(s): {len(candidate_rows)} on the candidate, {len(parent_rows)} on the parent, "
                   f"{len(baseline)} in the frozen INT baseline; a measured finding that grows is new (numerical "
                   f"epsilon {MEASURE_EPSILON_M:g} m on measures reported at 0.001 m)")


def inv_dir(new_findings: list[dict], rule: str) -> dict:
    """No direction finding, or changed contradicting label, that the parent's frozen baseline does not already hold
    (differential gate)."""
    return _result("INV-DIR", [row["id"] for row in new_findings],
                   f"candidate direction findings ({rule}) as (connection, dir, geometry turn) against the parent's "
                   "baseline after the declared lane remap")
