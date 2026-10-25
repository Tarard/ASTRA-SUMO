"""Twin invariants: Calibrate part 1 (M1: INV-INPUTS, INV-OBS, INV-HOLD, INV-PASS) and the Build part (M3).

Each check returns ``{"id", "pass", "witness_ids", "reason"}``; the witness ids name what violates the rule and the
reason says what was compared. Every check is a pure function of values its caller has already read.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING

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
# A measured finding (an endpoint gap, a width-transition offset) that grows by more than this is a regression even
# when its identity already exists on the parent; it equals the connection-mode audit's endpoint tolerance.
MEASURE_TOLERANCE_M = 0.1
# INV-SHAPE: a candidate turn above max_heading_change_deg must sit at a parent vertex (within the declared directed
# Hausdorff distance) whose own turn is at most this much smaller; each parent vertex excuses one candidate vertex.
HEADING_MATCH_TOLERANCE_DEG = 5.0
# INV-COVERAGE compares these coverage measures of an improved centerline (larger is better), plus every band of
# coverage_within_m.
COVERAGE_MEASURES = ("reference_coverage", "covered_reference_length_m")


def _measured(items) -> dict[str, list[float | None]]:
    grouped: dict[str, list[float | None]] = {}
    for item in items:
        identity, measure = (item, None) if isinstance(item, str) else item
        grouped.setdefault(identity, []).append(measure)
    return grouped


def measured_regressions(parent, candidate, tolerance: float = MEASURE_TOLERANCE_M,
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
              candidate_facts: dict) -> dict:
    """The goal's fact table and frozen scored set are the ones the candidate was scored with.

    ``candidate_facts`` is ``{"sha256", "basis", "verified"}``: the goal table itself, or the selector remap that a
    lane-count edit declares, whose bytes were reproduced from the goal table (``verified``).
    """
    witnesses = []
    if facts_sha256 != goal_facts_sha256:
        witnesses.append("facts:goal_table")
    if frozen_recomputed != frozen_sha256:
        witnesses.append("frozen:scored_set")
    if candidate_facts.get("basis") == "goal" and candidate_facts.get("sha256") != goal_facts_sha256:
        witnesses.append("facts:candidate_table")
    if candidate_facts.get("basis") != "goal" and candidate_facts.get("verified") is not True:
        witnesses.append("facts:candidate_remap")
    return _result("INV-FACTS", witnesses,
                   f"fact table sha256 {facts_sha256[:12]} against the goal's {goal_facts_sha256[:12]}; frozen scored "
                   "set recomputed against its recorded sha256; candidate scored with the "
                   f"{candidate_facts.get('basis')} table; no correspondence lock file is declared (mappings are "
                   "frozen in the goal's fact table)")


def inv_materialize(*, report_status: str | None, candidate_sha256: str | None, declared_sha256: str | None,
                    structural_failures: int | None, load_status: str | None, source_immutable: bool,
                    source_sha256: str | None, parent_sha256: str, structure: dict[str, list]) -> dict:
    """The profiled candidate is exactly what verify-edit materialized from the parent, and it loads in SUMO.

    ``structure`` holds the network-wide connection-mode structural failures ``{"parent": [...], "candidate":
    [...]}`` as ``(identity, measured metres or None)``. The check is differential (a design amendment to "structural
    failures 0", pending human sign-off): a real net can carry failures outside any edit (clean Ring1 nominal has 5
    endpoint gaps at one junction), so a witness is a failure the parent did not have, a pre-existing failure whose
    measured gap grew by more than ``MEASURE_TOLERANCE_M``, or a candidate count that is not verify-edit's.
    """
    witnesses = []
    if report_status not in {"measured", "no_effect"}:
        witnesses.append(f"verify_edit:{report_status}")
    if candidate_sha256 is None or candidate_sha256 != declared_sha256:
        witnesses.append("candidate:sha256")
    new, worse = measured_regressions(structure["parent"], structure["candidate"])
    witnesses += [f"structure:{failure}" for failure in new] + [f"worse:{failure}" for failure in worse]
    if structural_failures != len(structure["candidate"]):
        witnesses.append(f"structure:count:{structural_failures}")
    if load_status != "pass":
        witnesses.append(f"load:{load_status}")
    if not source_immutable:
        witnesses.append("source:changed")
    if source_sha256 != parent_sha256:
        witnesses.append("source:not_parent")
    return _result("INV-MATERIALIZE", witnesses,
                   "verify-edit status measured or no_effect; candidate bytes equal its recorded candidate_sha256; no "
                   f"structural failure the parent lacks and none {MEASURE_TOLERANCE_M:g} m worse "
                   f"({len(structure['candidate'])} on the candidate, {len(structure['parent'])} on the parent; "
                   "differential amendment); SUMO load pass; source unchanged and equal to the parent net")


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
    """``(x, y, turn degrees)`` of every interior vertex (repeated points dropped)."""
    unique = [point for index, point in enumerate(points) if index == 0 or point != points[index - 1]]
    rows = []
    for a, b, c in zip(unique, unique[1:], unique[2:]):
        first, second = math.atan2(b[1] - a[1], b[0] - a[0]), math.atan2(c[1] - b[1], c[0] - b[0])
        rows.append((b[0], b[1], abs(math.degrees((second - first + math.pi) % (2 * math.pi) - math.pi))))
    return rows


def directed_hausdorff_m(candidate: list[tuple[float, float]], parent: list[tuple[float, float]],
                         spacing_m: float = 1.0) -> float:
    """Largest distance from the candidate polyline, sampled every ``spacing_m``, to the parent polyline.

    Only samples that project strictly inside the parent count: a longitudinal tail (a lane that got longer, e.g.
    when a junction shrinks back) is a length change, which INV-COVERAGE and the stop-line facts judge, not a lateral
    excursion such as a zigzag.
    """
    if len(candidate) < 2 or len(parent) < 2:
        return 0.0 if candidate == parent else math.inf
    line = LineString(parent)
    points = shapely.points(shapely.get_coordinates(shapely.segmentize(LineString(candidate), spacing_m)))
    along = shapely.line_locate_point(line, points)
    inside = (along > EPSILON) & (along < line.length - EPSILON)
    distances = shapely.distance(points, line)[inside]
    return float(distances.max()) if distances.size else 0.0


def shape_change(parent: list[tuple[float, float]], candidate: list[tuple[float, float]]) -> dict:
    """Vertex delta, every interior turn of both shapes and the candidate-to-parent directed Hausdorff distance."""
    parent_turns, candidate_turns = _turns(parent), _turns(candidate)
    return {"vertex_delta": abs(len(candidate) - len(parent)),
            "max_turn_parent_deg": max((row[2] for row in parent_turns), default=0.0),
            "max_turn_candidate_deg": max((row[2] for row in candidate_turns), default=0.0),
            "parent_turns": parent_turns, "candidate_turns": candidate_turns,
            "directed_hausdorff_m": directed_hausdorff_m(candidate, parent)}


def unexcused_turns(change: dict, limit_deg: float, distance_m: float) -> list[tuple[float, float, float]]:
    """Candidate vertices turning more than ``limit_deg`` that no parent vertex excuses: an unused parent vertex
    within ``distance_m`` whose turn is at most ``HEADING_MATCH_TOLERANCE_DEG`` smaller. Sharpest first, so an
    existing bend excuses its own vertex once and a zigzag next to it is still a witness."""
    sharp = sorted((row for row in change["candidate_turns"] if row[2] > limit_deg + EPSILON), key=lambda row: -row[2])
    available = list(change["parent_turns"])
    unexcused = []
    for x, y, turn in sharp:
        matches = [row for row in available if math.hypot(row[0] - x, row[1] - y) <= distance_m + EPSILON
                   and row[2] >= turn - HEADING_MATCH_TOLERANCE_DEG - EPSILON]
        if matches:
            available.remove(min(matches, key=lambda row: math.hypot(row[0] - x, row[1] - y)))
        else:
            unexcused.append((x, y, turn))
    return unexcused


def inv_shape(changes: dict[str, dict], g0_shape: dict) -> dict:
    """Changed road-lane shapes stay within the goal's declared ``g0_shape`` regularity.

    A candidate interior turn above ``max_heading_change_deg`` is allowed only where the parent already turns about
    as sharply (``unexcused_turns``): an existing bend is never a witness, but a new sharp vertex anywhere on the lane,
    a bend next to an existing one included, is.
    """
    witnesses = []
    for element, change in sorted(changes.items()):
        if change["vertex_delta"] > g0_shape["max_vertex_delta"]:
            witnesses.append(f"vertex_delta:{element}")
        if unexcused_turns(change, g0_shape["max_heading_change_deg"], g0_shape["max_directed_hausdorff_m"]):
            witnesses.append(f"heading:{element}")
        if change["directed_hausdorff_m"] > g0_shape["max_directed_hausdorff_m"] + EPSILON:
            witnesses.append(f"hausdorff:{element}")
    return _result("INV-SHAPE", witnesses,
                   f"{len(changes)} changed road lane shape(s) against g0_shape: vertex delta <= "
                   f"{g0_shape['max_vertex_delta']}, an interior turn above {g0_shape['max_heading_change_deg']} deg "
                   f"only within {g0_shape['max_directed_hausdorff_m']} m of a parent vertex turning at most "
                   f"{HEADING_MATCH_TOLERANCE_DEG:g} deg less sharply (one each), directed Hausdorff <= "
                   f"{g0_shape['max_directed_hausdorff_m']} m ({g0_shape.get('status', 'declared')})")


FRAME_DERIVED = frozenset({"convBoundary"})


def inv_frame(parent_location: dict | None, candidate_location: dict | None) -> dict:
    """The ``<location>`` frame (netOffset, projParameter, origBoundary and any other attribute) is unchanged.

    ``convBoundary`` is the bounding box netconvert recomputes from the edited geometry, so its change is reported in
    the reason, never a witness; the frame itself is what ``network_geometry_freeze`` compares.
    """
    before, after = parent_location or {}, candidate_location or {}
    changed = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
    witnesses = [f"location:{key}" for key in changed if key not in FRAME_DERIVED]
    if (parent_location is None) != (candidate_location is None):
        witnesses.append("location:element")
    derived = [key for key in changed if key in FRAME_DERIVED]
    return _result("INV-FRAME", witnesses,
                   "every <location> attribute of the candidate against the parent's"
                   + (f"; derived {', '.join(derived)} changed with the geometry (reported only)" if derived else ""))


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
    a regression), and no measured finding (an endpoint gap) that grew by more than ``MEASURE_TOLERANCE_M`` against
    the parent, or against the baseline for a finding only the baseline has.

    Rows are ``(identity, measured metres or None)`` or bare identities.
    """
    baseline = baseline or {}
    new, worse = measured_regressions(parent_rows, candidate_rows, baseline=baseline)
    return _result("INV-AUDIT", new + [f"worse:{identity}" for identity in worse],
                   f"connection-mode, request-matrix and independent-safety findings on {len(junctions)} scope "
                   f"junction(s): {len(candidate_rows)} on the candidate, {len(parent_rows)} on the parent, "
                   f"{len(baseline)} in the frozen INT baseline; measured findings may not grow by more than "
                   f"{MEASURE_TOLERANCE_M:g} m")


def inv_dir(new_findings: list[dict], rule: str) -> dict:
    """No direction finding, or changed contradicting label, that the parent's frozen baseline does not already hold
    (differential gate)."""
    return _result("INV-DIR", [row["id"] for row in new_findings],
                   f"candidate direction findings ({rule}) as (connection, dir, geometry turn) against the parent's "
                   "baseline after the declared lane remap")
