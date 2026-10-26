"""M2 stage evaluator: the one production verdict of a candidate arm against a reference arm of judged runs.

Protocol v3 as amended by A1 (before C1) and A2 (after C1), with the owner-delegated decisions. Pure: no I/O, no
SUMO. Every step is evaluated and reported, in the order of the gate table, and the first failing step labels the
case:

- H1 invariants (``blocked``): equal arms of the gate's K draws, every run scored on the target's rows, distinct draw
  keys and distinct run ids, a truth ensemble on other keys and other runs. In confirmation, in addition: the arms
  run on disjoint fresh secret keys (every fingerprint a 64-hex SHA-256, no key public or of unknown publicity); the
  gate is the frozen one; the gate, the target, both arms' bindings and both arms' ordered keys and spare keys match
  the commitment frozen before the runs; every run is bound to its arm's committed binding; the ledger shows every
  committed key's use (``key_use_step``: a scored draw is never left out, and spare i enters only after an earlier
  committed key's runs crashed or timed out, A2.2); the truth draws come with their run records; and every run,
  truth included, shares one measurement-code and runtime identity.
- H2 health (``unhealthy``, A2.1): no run shows a failure class the 14-draw parent panel never showed (stall,
  gridlock, vehicles not inserted or not arrived, discarded vehicles, a native failure), and the candidate arm's
  unhealthy draws (collision or teleport) exceed the reference arm's by at most the declared margin (1 at K = 8,
  2 at K = 16). Unhealthy draws stay in both ensembles and are scored as they are (A2.2).
- H3 guards (``guard``): quiet-entry K-mean p95, and the K-mean re-key backstops v3 section 5 declares (network p95
  and entry 44532531), brakings and post-window passages. The other busy entry edges' K-mean margins are reported
  only (``h3_report_only``): v3 declares no blocking backstop for them.
- N1 (``row_regress``): the K-mean |error| growth on every one of the target's rows (all 182) stays within the row
  margin. Enforced here, never delegated (A1.5).
- N2 (``init``): the K-mean init52 TAE difference against the frozen init52 target stays within its margin.
- N3 (``level_regress``) and N4 (``fine_regress``): the fair-CRPS difference on totals13 (with the 5-vehicle floor
  on its margin) and on main130 stays within its margin.
- ``no_effect`` (arms on the same keys only): every scored row equals the reference's on every key.
- I (``improved``, A1.3): the exact one-sided permutation p over all C(2K, K) relabelings of the fair-CRPS difference
  T on the primary objective is <= alpha and T <= -g. Otherwise ``not_significant`` (T <= -g), ``worse`` (T >= g)
  or ``not_material``.

N1, N3 and N4 are applied against the gate's declared ``guard_target`` (the stage target in the frozen gate); their
margins were calibrated against the real counts in C1, which the steps report.

Notes (report only, never a label): ``spread_driven`` when T <= -g but the mean TAE change is > -g, so the gain
comes from a wider ensemble (the fair CRPS rewards widening a biased, overconfident reference: the candidate's draws
can lie farther from the target on average); ``tae_only`` when the mean TAE gains g but T does not (a sharpening,
which the CRPS does not reward). The CRPS ignores sharpening, not widening.

Report only, never a gate: the paired sign-flip p with its exchangeability label, the ensemble-TAE flip p, v2's sign
rule and v2's S rule (no ``simplified`` verdict exists, decision 5), the split dTAE = dCRPS + dGMD/2, paired and
unpaired energy distances, the CRN ratio rho and kappa (not applicable for disjoint keys), main130/ms3/totals13
sensitivities, per-field totals against the draw ranges, the target's rank among the K + 1 members, per-cell
predictive z, the ensemble-median distance, the L-type backlog flags (decision 6), the non-blocking busy-entry
margins, the count and sum of row growths above g/2, the plausibility of each arm's unhealthy count under the
parent's rate, the truth draws' health, the identity mix and the parent's unhealthy frequency (A2.3).

A rate (alpha) is attached only to ``improved`` in confirmation mode, and then it licenses one claim: rejection of
equal candidate and reference distributions together with an observed score gain of at least g against this target.
It is not a population gain of g and does not control "different but not better". Explore evidence over simulated
counts under the supplied model: no field truth, no promotion authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import Literal

from . import ensemble, ensemble_profile, guards

GATE_SCHEMA = "astra.twin-m2-gate/v1"
STAGE_SCHEMA = "astra.twin-m2-stage/v1"
MODES = ("screen", "confirm")
STEP_LABELS = (("H2", "unhealthy"), ("H3", "guard"), ("N1", "row_regress"), ("N2", "init"),
               ("N3", "level_regress"), ("N4", "fine_regress"))
LABELS = ("blocked", *(label for _, label in STEP_LABELS), "no_effect", "improved", "not_significant", "worse",
          "not_material")
NOTES = {"spread_driven": "T <= -g while the mean TAE change is > -g: the CRPS gain comes from a wider ensemble "
                          "(d_half_gmd), not from draws closer to the target (report only)",
         "tae_only": "the mean TAE gains g while T > -g: a sharpening the CRPS does not reward (report only)"}
COMPLETION_KEYS = ("loaded", "inserted", "arrived", "running", "waiting", "teleports", "collisions", "discarded")
FAILURE_CLASSES = ("native_failure", "stall", "gridlock", "not_inserted", "not_arrived", "discarded", "collision",
                   "teleport", "unclassified")
# Failure classes the 14-draw parent panel showed (C1: k14, 1 side collision and 1 teleport on :315029332_0_2).
PARENT_FAILURE_CLASSES = ("collision", "teleport")
INFORMATIVE_FIELDS = ("0119Z11", "0119Z12", "0119Z13", "0119Z4", "0119Z5", "0535Z4")
# Entry classes of the C1 panel (unchanged from B0's 6 draws): quiet when every parent draw's p95 <= 1 s.
ENTRY_CLASSES = {"-142944318": "busy", "-983227468#1": "quiet", "139083714#0": "quiet", "142944473#0": "quiet",
                 "189764301": "busy", "25866659#1": "busy", "26623877": "quiet", "31106387": "busy",
                 "44532531": "busy", "448511533#3": "quiet", "61578529#1": "busy", "771709146#0": "quiet",
                 "963776077#0": "quiet"}
# The busy entry edges with a blocking re-key backstop: v3 section 5 declares one for 44532531 (and the network p95)
# only. The other busy edges' K-mean margins are reported and never gate.
BLOCKING_ENTRY_BACKSTOPS = ("44532531",)

# C1 recalibration on the 14-draw parent panel k1..k14 (k14 unhealthy, A2.3; m2-c1-result.json "all14"). The
# t-margins are t_{0.99,13} s sqrt(2 / K) (guards.rekey_t_margin); N1 is the draw-level bootstrap q99 of the max
# K-mean row growth on 182 rows against the real counts; N3 and N4 take the larger of the block-bootstrap MC q99 and
# the exact disjoint K = 5 split q99 scaled by sqrt(5 / K), the rule C1 used on the 13 healthy draws. The 13-draw
# values move by about 3% (e.g. network p95 114.525 / 80.981, N1 11.88 / 8.44, N3 20.79 / 14.7, N4 48.89 / 31.48).
_C1_ALL14 = {
    8: {"network_p95": 112.471, "entry_p95:44532531": 119.774, "entry_p95:-142944318": 14.392,
        "entry_p95:189764301": 4.816, "entry_p95:25866659#1": 12.462, "entry_p95:31106387": 1.521,
        "entry_p95:61578529#1": 42.296, "brakings": 8.484, "post_window": 21.217, "init52": 18.787, "n1": 11.62,
        "n3_mc": 16.66, "n4_mc": 44.502},
    16: {"network_p95": 79.529, "entry_p95:44532531": 84.693, "entry_p95:-142944318": 10.177,
         "entry_p95:189764301": 3.405, "entry_p95:25866659#1": 8.812, "entry_p95:31106387": 1.075,
         "entry_p95:61578529#1": 29.908, "brakings": 5.999, "post_window": 15.002, "init52": 13.284, "n1": 8.31,
         "n3_mc": 11.403, "n4_mc": 31.077}}
_C1_ALL14_EXACT_K5_Q99 = {"totals13": 27.1, "main130": 40.2}
HEALTH_MARGINS = {8: 1, 16: 2}  # A2.1, declared from the parent's 1/14 as a materiality margin, not a test
PARENT_UNHEALTHY = {"k": 1, "n": 14}
PLAUSIBILITY_LEVEL = 0.05
TOTALS13_FLOOR = 5.0
QUIET_ENTRY_FLOOR_S = 10.0
BACKLOG_FLOOR_S = 10.0
REKEY_SD_MS4 = 155.167  # all14; K = 8 when rho = sd(d) / re-key sd <= 0.35 (decision 4, CRN designs only)
GUARD_TARGETS = ("stage", "real")
# The measurement-code and runtime identities of the 14-draw margin panel (judge.json and request.json of each run,
# read from E:\twin-store\m2-pilot). The margins pool two measurement-code identities; reported, never a rule.
MARGIN_PANEL_IDENTITY = {
    "draws": "k1..k14 = P3-jitter-k1..k4-s104, s3-parent-k5..k6, c1-parent-k7..k14",
    "runtime_sha256": {"3c7d6e80ca310413e2f1c8075b282fac615eefb5fb8fea243f719ee551e3a9ec": "k1..k14"},
    "measurement_code_sha256": {"22d28e932ae07c2533431d8364004c45c6ea0866098058e14071dea2d6ed4cce": "k1..k6",
                                "de7747aadc218dc1bf50e87447e89e34d5bf89ae7d0b4fe0754225e9b3987dc1": "k7..k14"},
    "plan_measurement_code_sha256": {"none recorded": "k1..k4", "22d28e932ae0": "k5..k6", "de7747aadc21": "k7..k14"},
    "verifier_commit": {"6ccaac59d73ded216536c295857a3dc29c1f7aac": "k1..k6 (verifier_clean false)",
                        "d11d284b1afe3c07ca1ab2a51cec205ac6782c40": "k7..k14 (verifier_clean true)"}}
# Ledger-row fields that bind a run to its arm (the rest of the binding lives in request.json).
LEDGER_BINDING_FIELDS = ("scenario_op", "seed", "family")
LEDGER_OUTCOMES = ("scored", "crashed", "unscored")

CLAIM = ("Explore evidence over simulated counts under the supplied model. In confirmation an 'improved' label "
         "means: the exact unpaired permutation test on disjoint fresh secret keys rejects equal candidate and "
         "reference distributions at alpha, and the observed fair-CRPS gain on the frozen objective is at least g "
         "against this target. It is not a population gain of g and does not control 'different but not better' "
         "(A1.3); a 'spread_driven' note says the gain comes from a wider ensemble. Guards only reject; a guard that "
         "holds certifies no non-regression. No field truth, no promotion authority.")

Counts = Mapping[str, int]


# --- the frozen gate -------------------------------------------------------------------------------------------------

def _round(value: float) -> float:
    return round(value, 3)


def frozen_gate(draws: int) -> dict:
    """The M2 gate frozen before C2 for K = 8 or 16 confirmation draws (JSON-native, hashed by ``gate_sha256``)."""
    if draws not in _C1_ALL14:
        raise ValueError(f"The frozen M2 gate exists for K = {sorted(_C1_ALL14)}; A2.1 declares no health margin "
                         f"for K = {draws}")
    table = _C1_ALL14[draws]
    scale = math.sqrt(5 / draws)
    n3 = max(TOTALS13_FLOOR, table["n3_mc"], _round(_C1_ALL14_EXACT_K5_Q99["totals13"] * scale))
    n4 = max(table["n4_mc"], _round(_C1_ALL14_EXACT_K5_Q99["main130"] * scale))
    busy = sorted(edge for edge, kind in ENTRY_CLASSES.items() if kind == "busy")
    blocking = ("network_p95", *(f"entry_p95:{edge}" for edge in BLOCKING_ENTRY_BACKSTOPS), "brakings", "post_window")
    return {"schema": GATE_SCHEMA, "name": f"m2-confirmation-K{draws}", "draws": draws,
            "objective": ensemble.PRIMARY_OBJECTIVE, "g": 10, "alpha": 0.05, "delta_report": 5,
            "health": {"parent_failure_classes": list(PARENT_FAILURE_CLASSES),
                       "unhealthy_margin": HEALTH_MARGINS[draws], "parent_unhealthy": dict(PARENT_UNHEALTHY)},
            "entry_classes": dict(ENTRY_CLASSES), "quiet_entry_floor_s": QUIET_ENTRY_FLOOR_S,
            "backlog_floor_s": BACKLOG_FLOOR_S,
            "h3_margins": {name: table[name] for name in blocking},
            "h3_report_margins": {f"entry_p95:{edge}": table[f"entry_p95:{edge}"] for edge in busy
                                  if edge not in BLOCKING_ENTRY_BACKSTOPS},
            "n1_row_margin": table["n1"], "n2_init52_margin": table["init52"], "init52_target": "real",
            "n3_totals13_margin": n3, "n3_floor": TOTALS13_FLOOR, "n4_main130_margin": n4,
            "guard_target": "stage",
            "rekey_sd_ms4": REKEY_SD_MS4, "rho_threshold": ensemble.CRN_EFFECTIVE_RHO,
            "ensemble_profile": {**ensemble_profile.corridor_rule(),
                                 "sha256": ensemble_profile.corridor_rule_sha256()},
            "margin_panel_identity": MARGIN_PANEL_IDENTITY,
            "basis": "C1 recalibration on the 14-draw parent panel k1..k14 (A2.3); ms4 primary per C1 decision row 1; "
                     "init52 against the real initialization counts, as the judge's init52_tae and the margin "
                     "calibration use; N1, N3 and N4 calibrated against the real counts and applied against the "
                     "stage target (guard_target, an owner check: A1.7 froze N2's target only); blocking entry "
                     "backstops on the network p95 and 44532531 only, as v3 section 5 declares; the margin panel "
                     "pools two measurement-code identities (margin_panel_identity)"}


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def gate_sha256(gate: Mapping) -> str:
    return hashlib.sha256(canonical_json(gate)).hexdigest()


def target_sha256(real: Counts, truth: Mapping[str, Counts] | None = None) -> str:
    """SHA-256 of the stage target: the real counts and, for a twin-truth stage, the truth draws by key."""
    return hashlib.sha256(canonical_json({"real": dict(real), "truth": None if truth is None else
                                          {key: dict(counts) for key, counts in truth.items()}})).hexdigest()


def _check_gate(gate: Mapping) -> None:
    """A malformed gate is a programming error, refused before any step."""
    required = ("draws", "objective", "g", "alpha", "delta_report", "health", "entry_classes", "quiet_entry_floor_s",
                "backlog_floor_s", "h3_margins", "n1_row_margin", "n2_init52_margin", "init52_target",
                "n3_totals13_margin", "n3_floor", "n4_main130_margin", "guard_target", "rekey_sd_ms4",
                "rho_threshold")
    missing = [name for name in required if name not in gate]
    if missing:
        raise ValueError(f"The gate lacks {missing}")
    if not isinstance(gate["draws"], int) or not 2 <= gate["draws"] <= ensemble.MAX_UNPAIRED_DRAWS:
        raise ValueError(f"The gate's K must be an integer from 2 to {ensemble.MAX_UNPAIRED_DRAWS}")
    if gate["objective"] not in ensemble.OBJECTIVES:
        raise ValueError(f"Unknown objective {gate['objective']!r}")
    if gate["init52_target"] not in ("real", "stage"):
        raise ValueError("init52_target is 'real' or 'stage'")
    if gate["guard_target"] not in GUARD_TARGETS:
        raise ValueError(f"guard_target (the target of N1, N3 and N4) is one of {GUARD_TARGETS}")
    health = gate["health"]
    if not isinstance(health.get("unhealthy_margin"), int) or health["unhealthy_margin"] < 0:
        raise ValueError("The health margin is a nonnegative integer count of draws")
    unknown = sorted(set(health.get("parent_failure_classes", ())) - set(FAILURE_CLASSES))
    if unknown:
        raise ValueError(f"Unknown failure classes {unknown}")
    margins, report = gate["h3_margins"], gate.get("h3_report_margins") or {}
    lacking = [name for name in ("network_p95", "brakings", "post_window") if name not in margins]
    not_busy = [name for name in (*margins, *report) if name.startswith("entry_p95:")
                and gate["entry_classes"].get(name.removeprefix("entry_p95:")) != "busy"]
    both = sorted(set(margins) & set(report))
    stray = sorted(name for name in report if not name.startswith("entry_p95:"))
    if lacking or not_busy or both or stray:
        raise ValueError(f"H3 needs blocking re-key margins for the network p95, brakings and post-window, and takes "
                         f"entry margins (blocking or report-only, not both) for busy entry edges only; lacking "
                         f"{lacking}, not busy {not_busy}, in both {both}, not an entry margin {stray}")


# --- health (A2.1, A2.2) -------------------------------------------------------------------------------------------

def failure_classes(health: Mapping) -> list[str]:
    """The failure classes one judged run shows, from its judge ``health`` record (empty for a healthy run).

    ``gridlock`` is the stall monitor's all_stopped (every running vehicle beyond the standstill limit); ``stall``
    is any other monitor end than completed. A run marked unhealthy without a recognised cause is ``unclassified``.
    """
    found = set()
    if health.get("native_status") != "pass":
        found.add("native_failure")
    stall = health.get("stall_status")
    if stall == "all_stopped":
        found.add("gridlock")
    elif stall != "completed":
        found.add("stall")
    completion = health.get("completion")
    if not isinstance(completion, Mapping) or not all(_is_count(completion.get(key)) for key in COMPLETION_KEYS):
        found.add("unclassified")
    else:
        vehicles = health.get("vehicle_count")
        if (completion["waiting"] or completion["inserted"] < completion["loaded"]
                or (vehicles is not None and completion["loaded"] != vehicles)):
            found.add("not_inserted")
        if completion["running"] or completion["arrived"] < completion["inserted"]:
            found.add("not_arrived")
        for key, name in (("discarded", "discarded"), ("teleports", "teleport"), ("collisions", "collision")):
            if completion[key]:
                found.add(name)
    if not found and health.get("healthy") is not True:
        found.add("unclassified")
    return sorted(found)


def _draw_health(health: Mapping) -> dict:
    classes = failure_classes(health)
    completion = health.get("completion") if isinstance(health.get("completion"), Mapping) else {}
    return {"classes": classes, "unhealthy": bool(classes), "teleports": completion.get("teleports"),
            "collisions": completion.get("collisions"), "status": health.get("status")}


def _binomial_tail(count: int, draws: int, rate: float) -> float:
    """P(X >= count) for X ~ Bin(draws, rate)."""
    return min(1.0, sum(math.comb(draws, j) * rate ** j * (1 - rate) ** (draws - j) for j in range(count, draws + 1)))


def unhealthy_plausibility(count: int, draws: int, parent: Mapping) -> dict:
    """How surprising ``count`` unhealthy draws of ``draws`` are under the parent's unhealthy rate k/n: the binomial
    upper tail at the point rate and at its one-sided 95% Clopper-Pearson upper bound (report only)."""
    k, n = parent["k"], parent["n"]
    upper = 1 - ensemble_profile.clopper_pearson_lower(n - k, n) if k < n else 1.0
    at_upper = _binomial_tail(count, draws, upper)
    return {"unhealthy": count, "draws": draws, "parent_rate": k / n, "parent_rate_upper95": round(upper, 4),
            "tail_at_parent_rate": round(_binomial_tail(count, draws, k / n), 6),
            "tail_at_parent_upper95": round(at_upper, 6), "implausible": at_upper < PLAUSIBILITY_LEVEL}


def health_step(candidate: Mapping[str, Mapping], reference: Mapping[str, Mapping], gate: Mapping,
                truth_runs: Mapping[str, Mapping] | None = None) -> dict:
    """H2 per A2.1, with every draw's classes, teleports and collisions reported (the draws stay scored, A2.2).

    Also reported, never a rule: the plausibility of each arm's unhealthy count under the parent's rate (a reference
    arm far less healthy than the parent lets an equally unhealthy candidate pass the relative rule) and the truth
    draws' health.
    """
    allowed = set(gate["health"]["parent_failure_classes"])
    per_draw, new = {}, []
    for arm, draws in (("candidate", candidate), ("reference", reference)):
        per_draw[arm] = {key: _draw_health(draws[key].get("health") or {}) for key in sorted(draws)}
        new += [f"{arm}:{key}:{name}" for key, row in per_draw[arm].items() for name in row["classes"]
                if name not in allowed]
    counts = {arm: sum(row["unhealthy"] for row in rows.values()) for arm, rows in per_draw.items()}
    excess, margin = counts["candidate"] - counts["reference"], gate["health"]["unhealthy_margin"]
    parent = gate["health"].get("parent_unhealthy")
    plausibility = None
    known = isinstance(parent, Mapping) and all(_is_count(parent.get(name)) for name in ("k", "n"))
    if known and 0 < parent["n"] and parent["k"] <= parent["n"]:
        plausibility = {arm: unhealthy_plausibility(counts[arm], len(per_draw[arm]), parent) for arm in counts}
        plausibility.update(
            reference_implausible=plausibility["reference"]["implausible"], blocking=False,
            rule=f"flag when P(X >= count) under the parent's one-sided 95% upper rate is < {PLAUSIBILITY_LEVEL}; "
                 "blocking is an owner decision")
    truth = None if truth_runs is None else {key: _draw_health(truth_runs[key].get("health") or {})
                                             for key in sorted(truth_runs)}
    return {"rule": "no failure class the parent panel never showed; candidate unhealthy draws - reference "
                    "unhealthy draws <= margin (A2.1)",
            "passes": not new and excess <= margin, "new_failure_classes": new,
            "parent_failure_classes": sorted(allowed), "unhealthy_draws": counts, "excess": excess, "margin": margin,
            "per_draw": per_draw, "parent_unhealthy": parent, "plausibility": plausibility,
            "truth_health": truth, "scored": "unhealthy draws stay in both ensembles and are scored as they are (A2.2)"}


# --- key use against the ledger (A2.2) -----------------------------------------------------------------------------

def ledger_outcome(row: Mapping, health: Mapping | None = None) -> str:
    """``scored`` (a complete score, healthy or not), ``crashed`` (no judge, or a native failure or timeout without a
    complete score and without a gridlock) or ``unscored`` (any other run without a complete score) for one ledger row;
    ``health`` is the run's judge health record when the row is unscored."""
    if row.get("status") in ("measured", "invalid") and row.get("tae_main") is not None:
        return "scored"
    if row.get("status") == "failed":
        return "crashed"
    health = health or {}
    if health.get("native_status") not in (None, "pass") and health.get("stall_status") != "all_stopped":
        return "crashed"
    return "unscored"


def _binding_matches(row_binding: Mapping, binding: Mapping) -> bool:
    return all(row_binding.get(name) == binding.get(name) for name in LEDGER_BINDING_FIELDS)


def key_use_step(arm: str, draws: Mapping[str, Mapping], *, committed: Sequence[str], spares: Sequence[str],
                 binding: Mapping, ledger_runs: Sequence[Mapping], size: int) -> dict:
    """The arm's committed keys against the ledger (A2.2): which runs a confirmation must use.

    ``ledger_runs`` holds every ledger row with a draw key as ``{id, request_sha256, key_sha256, public, outcome,
    binding}`` (``ledger_outcome``; ``binding`` holds the ledger's scenario_op, seed and family). Walking the ordered
    keys (committed first, then spares), a key with a scored run is used, a key whose runs all crashed or timed out is
    replaced by the next key, and a key without a run or with an unscored run ends the walk. The arm must use exactly
    the first K keys so admitted, each through one scored run of its own. A scored run on a committed key that is
    left out (an unhealthy draw dropped, or a spare run without a crash before it), two scored requests on one key,
    and a committed key run under another binding are failures.

    The commitment's timing cannot be verified here, so a commitment rewritten after the runs is visible only
    through ``uncommitted_secret_runs`` (reported, never a rule): scored runs with the arm's ledger binding on secret
    keys this arm did not commit, e.g. truth draws, the other arm's runs of the same op, or a dropped draw.
    """
    order = [*committed, *spares]
    slots = {key: ("committed", i) if i < len(committed) else ("spare", i - len(committed))
             for i, key in enumerate(order)}
    by_key: dict[str, list[Mapping]] = defaultdict(list)
    for row in ledger_runs:
        if row.get("key_sha256") in slots:
            by_key[row["key_sha256"]].append(row)
    failures, status, per_key = [], {}, []
    for key in order:
        rows = by_key.get(key, [])
        slot, index = slots[key]
        other = sorted(str(row.get("id")) for row in rows if not _binding_matches(row.get("binding") or {}, binding))
        if other:
            failures.append(f"key_use: {arm} {slot} key #{index} was also run under another binding ({other})")
        scored = [row for row in rows if row.get("outcome") == "scored"]
        if len({row.get("request_sha256") for row in scored}) > 1:
            failures.append(f"key_use: {arm} {slot} key #{index} has scored runs of two requests")
        status[key] = ("scored" if scored else "unscored" if any(row.get("outcome") == "unscored" for row in rows)
                       else "crashed" if rows else "unrun")
        per_key.append({"slot": slot, "index": index, "key_sha256": key, "status": status[key],
                        "runs": sorted(str(row.get("id")) for row in rows)})
    admitted, stopped = [], False
    for key in order:
        if len(admitted) == size:
            break
        if status[key] == "scored":
            admitted.append(key)
        elif status[key] != "crashed":
            slot, index = slots[key]
            failures.append(f"key_use: {arm} {slot} key #{index} is {status[key]} (no scored run, no crash or "
                            "timeout); a spare replaces crashed or timed-out runs only (A2.2)")
            stopped = True
            break
    if len(admitted) < size and not stopped:
        failures.append(f"key_use: {arm} has {len(admitted)} admitted keys, the gate's K is {size} (spares exhausted)")
    left_out = [key for key in order if status[key] == "scored" and key not in admitted]
    if left_out:
        failures.append(f"key_use: {arm} scored runs on {[f'{slots[k][0]} #{slots[k][1]}' for k in left_out]} are "
                        "left out: every scored draw stays in the ensemble, and a spare enters only after a crash "
                        "or timeout (A2.2)")
    used = {(draw.get("run") or {}).get("key_sha256"): (draw.get("run") or {}).get("id") for draw in draws.values()}

    def named(keys) -> list[str]:
        return sorted(f"{slots[k][0]} #{slots[k][1]}" if k in slots else "uncommitted" for k in keys)

    if set(admitted) - set(used):
        failures.append(f"key_use: the {arm} arm leaves out the scored draws on {named(set(admitted) - set(used))}: "
                        "every scored draw stays in the ensemble (A2.2)")
    if set(used) - set(admitted):
        failures.append(f"key_use: the {arm} arm uses keys the ledger does not admit: "
                        f"{named(set(used) - set(admitted))}")
    for key, run_id in sorted(used.items(), key=lambda item: str(item[0])):
        scored_ids = {row.get("id") for row in by_key.get(key, []) if row.get("outcome") == "scored"}
        if key in admitted and run_id not in scored_ids:
            failures.append(f"key_use: the {arm} run {run_id} is not a scored ledger run on its committed key")
    uncommitted = sorted(str(row.get("id")) for row in ledger_runs
                         if row.get("key_sha256") not in slots and row.get("public") is False
                         and row.get("outcome") == "scored" and _binding_matches(row.get("binding") or {}, binding))
    return {"arm": arm, "passes": not failures, "failures": failures, "per_key": per_key,
            "admitted": admitted, "spares_used": sum(slots[key][0] == "spare" for key in admitted),
            "uncommitted_secret_runs": uncommitted}


# --- H1 invariants -------------------------------------------------------------------------------------------------

def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_fingerprint(value: object) -> bool:
    """A 64-character lowercase hex SHA-256."""
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _identity(run: Mapping) -> tuple[str, str] | None:
    identity = run.get("identity") or {}
    code, runtime = identity.get("measurement_code_sha256"), identity.get("runtime_sha256")
    return (code, runtime) if isinstance(code, str) and isinstance(runtime, str) else None


def _identity_mix(groups: Mapping[str, Mapping[str, Mapping]]) -> dict:
    """Which runs share a (measurement code, runtime) identity, over the arms and the truth (report)."""
    found: dict[str, list[str]] = defaultdict(list)
    for group, runs in groups.items():
        for key in sorted(runs):
            identity = _identity(runs[key])
            found["unknown" if identity is None else f"{identity[0][:12]}/{identity[1][:12]}"].append(f"{group}:{key}")
    return {"identities": dict(sorted(found.items())), "consistent": len(found) == 1 and "unknown" not in found,
            "key": "measurement_code_sha256[:12]/runtime_sha256[:12]"}


def invariants_step(candidate: Mapping[str, Mapping], reference: Mapping[str, Mapping], *, real: Counts,
                    truth: Mapping[str, Counts] | None, gate: Mapping, mode: str, commitment: Mapping | None,
                    truth_runs: Mapping[str, Mapping] | None = None,
                    ledger_runs: Sequence[Mapping] | None = None) -> dict:
    """H1: everything the statistics assume, checked before any of them; ``failures`` names each violation.

    ``keys["relation"]`` labels the unpaired test's exchangeability from the key evidence: ``disjoint`` (by
    construction) only when the arms' labels are disjoint and every run's fingerprint is a 64-hex SHA-256, distinct
    across the arms and not public; ``shared`` for CRN keys; ``unverified`` otherwise.
    """
    failures: list[str] = []
    rows = set(real)
    if not rows or not all(_is_count(value) for value in real.values()):
        failures.append("target: the real counts need at least one row and nonnegative integer counts")
    size = gate["draws"]
    for arm, draws in (("candidate", candidate), ("reference", reference)):
        if len(draws) != size:
            failures.append(f"draws: the {arm} arm has {len(draws)} draws, the gate's K is {size}")
        for key in sorted(draws):
            draw = draws[key]
            counts = draw.get("counts") or {}
            if set(counts) != rows or not all(_is_count(value) for value in counts.values()):
                failures.append(f"rows: {arm}:{key} is not scored on exactly the target's rows (a crashed or "
                                "truncated run is replaced by a spare, never scored)")
            health = draw.get("health") or {}
            if health.get("observer_complete") is False or (health.get("missing_field_bins") or 0) > 0:
                failures.append(f"rows: {arm}:{key} has missing field bins")
            entries = set((draw.get("guard_stats") or {}).get("entry_p95") or {})
            if entries != set(gate["entry_classes"]) | {guards.NETWORK}:
                failures.append(f"guard_stats: {arm}:{key} entry edges differ from the gate's entry classes")
            stats = draw.get("guard_stats") or {}
            if any(not isinstance(stats.get(name), (int, float)) or isinstance(stats.get(name), bool)
                   for name in ("brakings", "post_window")):
                failures.append(f"guard_stats: {arm}:{key} lacks brakings or post_window")
            judged = stats.get("init52")
            if judged is not None and not failures and judged != sum(
                    abs(counts[row] - real[row]) for row in rows if ensemble.parse_row(row)[1] < ensemble.MAIN_BEGIN_S):
                failures.append(f"guard_stats: {arm}:{key} init52 differs from its counts against the real target")
    shared = set(candidate) & set(reference)
    design = "shared" if set(candidate) == set(reference) else "disjoint" if not shared else "partial"
    if design == "partial":
        failures.append(f"design: the arms share only some keys {sorted(shared)}")
    runs = {"candidate": {key: candidate[key].get("run") or {} for key in candidate},
            "reference": {key: reference[key].get("run") or {} for key in reference}}
    if truth is not None:
        if not truth:
            failures.append("target: a truth ensemble needs at least one draw")
        overlap = sorted(set(truth) & (set(candidate) | set(reference)))
        if overlap:
            failures.append(f"target: truth draws on judged keys {overlap}")
        if any(set(counts) != rows or not all(_is_count(v) for v in counts.values()) for counts in truth.values()):
            failures.append("target: every truth draw needs the real counts' rows")
        if truth_runs is not None:
            if set(truth_runs) != set(truth):
                failures.append("target: the truth run records do not cover exactly the truth draws")
            runs["truth"] = {key: truth_runs[key].get("run") or {} for key in truth_runs}
    elif truth_runs is not None:
        failures.append("target: truth run records without a truth ensemble")
    prints = {group: {key: run.get("key_sha256") for key, run in found.items()} for group, found in runs.items()}
    for group, found in prints.items():
        known = [value for value in found.values() if value is not None]
        if len(known) != len(set(known)):
            failures.append(f"keys: two {group} runs share one draw-key fingerprint")
    if design == "shared" and any(None not in (prints["candidate"][key], prints["reference"][key])
                                  and prints["candidate"][key] != prints["reference"][key] for key in candidate):
        failures.append("keys: a shared draw key has two fingerprints")
    arm_prints = (set(prints["candidate"].values()) | set(prints["reference"].values())) - {None}
    if design == "disjoint" and (set(prints["candidate"].values()) & set(prints["reference"].values())) - {None}:
        failures.append("keys: the arms reuse one draw key under different labels")
    if "truth" in prints and (set(prints["truth"].values()) - {None}) & arm_prints:
        failures.append("target: a truth draw runs on a judged draw key (truth draws must be other runs)")
    ids: dict[str, list[str]] = defaultdict(list)
    for group, found in runs.items():
        for key, run in found.items():
            if run.get("id") is not None:
                ids[str(run["id"])].append(f"{group}:{key}")
    repeated = {run_id: labels for run_id, labels in sorted(ids.items()) if len(labels) > 1}
    if repeated:
        failures.append(f"runs: one ledger run is used under several labels {repeated}")
    valid = all(is_fingerprint(prints[arm][key]) for arm in ("candidate", "reference") for key in prints[arm])
    exposed = sorted(f"{arm}:{key}" for arm in ("candidate", "reference") for key, run in runs[arm].items()
                     if run.get("key_public") is not False)
    relation = ("shared" if design == "shared" else
                "disjoint" if design == "disjoint" and valid and not exposed else "unverified")
    key_use = None
    if mode == "confirm":
        extra, key_use = _commitment_failures(candidate, reference, runs=runs, real=real, truth=truth, gate=gate,
                                              commitment=commitment, design=design, ledger_runs=ledger_runs)
        failures += extra
    return {"rule": "H1 invariants", "passes": not failures, "failures": failures, "design": design,
            "draws": {"candidate": len(candidate), "reference": len(reference)},
            "keys": {"relation": relation, "fingerprints_valid": valid, "public_or_unknown": exposed},
            "key_use": key_use, "identity": _identity_mix(runs)}


def _commitment_failures(candidate: Mapping[str, Mapping], reference: Mapping[str, Mapping], *,
                         runs: Mapping[str, Mapping[str, Mapping]], real: Counts, truth: Mapping[str, Counts] | None,
                         gate: Mapping, commitment: Mapping | None, design: str,
                         ledger_runs: Sequence[Mapping] | None) -> tuple[list[str], dict | None]:
    if design != "disjoint":
        return ["design: a confirmation runs the arms on disjoint fresh keys (A1.3)"], None
    if not isinstance(commitment, Mapping):
        return ["commitment: a confirmation needs the commitment frozen before its runs"], None
    failures = []
    if gate["draws"] not in _C1_ALL14 or dict(gate) != frozen_gate(gate["draws"]):
        failures.append("gate: a confirmation uses the frozen M2 gate")
    if commitment.get("gate_sha256") != gate_sha256(gate):
        failures.append("commitment: the gate differs from the committed gate")
    if commitment.get("target_sha256") != target_sha256(real, truth):
        failures.append("commitment: the target differs from the committed target")
    lists: dict[tuple[str, str], list[str] | None] = {}
    for arm in ("candidate", "reference"):
        for slot, name, length in (("keys", f"{arm}_keys_sha256", gate["draws"]),
                                   ("spares", f"{arm}_spare_keys_sha256", None)):
            value = commitment.get(name)
            if not isinstance(value, list) or not all(is_fingerprint(item) for item in value):
                failures.append(f"commitment: {name} is not a list of 64-hex lowercase SHA-256 fingerprints")
                value = None
            elif length is not None and len(value) != length:
                failures.append(f"commitment: {name} lists {len(value)} keys, the gate's K is {length}")
                value = None
            lists[(arm, slot)] = value
    listed = [item for value in lists.values() if value for item in value]
    if len(listed) != len(set(listed)):
        failures.append("commitment: a key is committed twice (within or across the arms' keys and spare keys)")
    bindings = {}
    for arm in ("candidate", "reference"):
        found = runs[arm]
        bad = sorted(key for key, run in found.items() if not is_fingerprint(run.get("key_sha256")))
        if bad:
            failures.append(f"keys: {arm} runs {bad} lack a 64-hex lowercase key fingerprint")
        exposed = sorted(key for key, run in found.items() if run.get("key_public") is not False)
        if exposed:
            failures.append(f"keys: {arm} runs {exposed} use a public key or one of unknown publicity; a "
                            "confirmation runs on fresh secret keys (v3 section 4.3, A1.3)")
        unnamed = sorted(key for key, run in found.items() if not isinstance(run.get("id"), str) or not run["id"])
        if unnamed:
            failures.append(f"runs: {arm} runs {unnamed} lack a ledger run id")
        binding = commitment.get(f"{arm}_binding")
        if not isinstance(binding, Mapping) or not binding:
            failures.append(f"commitment: a confirmation commits the {arm} binding before its runs (v3 section 4.3)")
            binding = None
        elif any(run.get("binding") != binding for run in found.values()):
            failures.append(f"commitment: a {arm} run is bound to another {arm} than the committed one")
        bindings[arm] = binding
    if truth is not None:
        truth_found = runs.get("truth")
        if truth_found is None:
            failures.append("target: a confirmation against a truth ensemble needs the truth draws' run records")
        else:
            bad = sorted(key for key, run in truth_found.items()
                         if not is_fingerprint(run.get("key_sha256")) or not isinstance(run.get("id"), str))
            if bad:
                failures.append(f"target: truth runs {bad} lack a 64-hex key fingerprint or a run id")
    identities = [_identity(run) for found in runs.values() for run in found.values()]
    if None in identities:
        failures.append("identity: a run lacks its measurement-code or runtime identity")
    elif len(set(identities)) > 1:
        failures.append("identity: the runs (both arms and the truth) were measured or run by different code "
                        f"({sorted({f'{c[:12]}/{r[:12]}' for c, r in identities})})")
    if ledger_runs is None:
        failures.append("commitment: a confirmation needs the ledger's runs on the committed keys (A2.2)")
        return failures, None
    key_use = {}
    for arm, draws in (("candidate", candidate), ("reference", reference)):
        committed, spares = lists[(arm, "keys")], lists[(arm, "spares")]
        if committed is None or spares is None or bindings[arm] is None:
            continue
        key_use[arm] = key_use_step(arm, draws, committed=committed, spares=spares, binding=bindings[arm],
                                    ledger_runs=ledger_runs, size=gate["draws"])
        failures += key_use[arm]["failures"]
    return failures, key_use


# --- N1 to N4 ------------------------------------------------------------------------------------------------------

def _members(real: Counts, truth: Mapping[str, Counts] | None) -> list[Counts]:
    return [real] if truth is None else [truth[key] for key in sorted(truth)]


def _abs_error(counts: Counts, members: Sequence[Counts], rows: Sequence[str]) -> Fraction:
    return Fraction(sum(abs(counts[row] - member[row]) for row in rows for member in members), len(members))


def row_growth_step(candidate: Sequence[Counts], reference: Sequence[Counts], members: Sequence[Counts],
                    rows: Sequence[str], margin: float, *, report_above: float | None = None) -> dict:
    """N1: max over ``rows`` of K-mean |c - y| - K-mean |r - y| (|. - y| averaged over the target members).

    Reported only: how many rows grow by more than ``report_above`` (g/2 at the stage) and the sum of their growth,
    and the sum of every positive row growth, because N1 bounds only the worst single row.
    """
    growth = {row: (sum((_abs_error(c, members, [row]) for c in candidate), Fraction(0)) / len(candidate)
                    - sum((_abs_error(r, members, [row]) for r in reference), Fraction(0)) / len(reference))
              for row in rows}
    worst = max(sorted(growth), key=lambda row: growth[row])
    limit = ensemble._exact(margin)
    above = sorted((row for row in growth if growth[row] > limit), key=lambda row: (-growth[row], row))
    result = {"rule": "max over the rows of the K-mean |error| growth <= margin", "rows": len(rows),
              "worst_row": worst, "value": growth[worst], "value_float": float(growth[worst]), "margin": margin,
              "rows_above_margin": {row: growth[row] for row in above}, "passes": growth[worst] <= limit}
    if report_above is not None:
        threshold = ensemble._exact(report_above)
        large = [growth[row] for row in growth if growth[row] > threshold]
        result["growth_report"] = {"threshold": report_above, "rows_above": len(large),
                                   "growth_sum_above": sum(large, Fraction(0)),
                                   "positive_growth_sum": sum((v for v in growth.values() if v > 0), Fraction(0)),
                                   "never_gates": True,
                                   "note": "N1 bounds the worst row only; whether a cumulative row-growth guard is "
                                           "needed is an owner decision"}
    return result


def init52_step(candidate: Sequence[Counts], reference: Sequence[Counts], members: Sequence[Counts],
                rows: Sequence[str], margin: float, target_kind: str) -> dict:
    """N2: K-mean init52 TAE of the candidate minus the reference's, against the frozen init52 target."""
    init = [row for row in rows if ensemble.parse_row(row)[1] < ensemble.MAIN_BEGIN_S]
    c = [_abs_error(counts, members, init) for counts in candidate]
    r = [_abs_error(counts, members, init) for counts in reference]
    value = sum(c, Fraction(0)) / len(c) - sum(r, Fraction(0)) / len(r)
    return {"rule": "K-mean init52 TAE difference <= margin", "target": target_kind, "rows": len(init),
            "candidate": [float(x) for x in c], "reference": [float(x) for x in r], "value": value,
            "value_float": float(value), "margin": margin, "passes": value <= ensemble._exact(margin)}


def _objective(draws: Sequence[Counts], rows: Sequence[str], objective: str) -> list[dict]:
    return [ensemble.objective_values(counts, rows, objective) for counts in draws]


def _target_values(members: Sequence[Counts], rows: Sequence[str], objective: str) -> ensemble.Target:
    values = [ensemble.objective_values(member, rows, objective) for member in members]
    return values[0] if len(values) == 1 else values


def crps_margin_step(candidate: Sequence[Counts], reference: Sequence[Counts], members: Sequence[Counts],
                     rows: Sequence[str], objective: str, margin: float) -> dict:
    """N3 or N4: fairCRPS(candidate) - fairCRPS(reference) on ``objective`` <= margin (no pairing needed)."""
    target = _target_values(members, rows, objective)
    keys = list(target if isinstance(target, Mapping) else target[0])
    value = (ensemble.fair_crps(_objective(candidate, rows, objective), target, keys)
             - ensemble.fair_crps(_objective(reference, rows, objective), target, keys))
    return {"rule": f"dCRPS on {objective} <= margin", "objective": objective, "values": len(keys), "T": value,
            "t": float(value), "margin": margin, "passes": value <= ensemble._exact(margin)}


# --- the verdict ---------------------------------------------------------------------------------------------------

def evaluate_stage(candidate: Mapping[str, Mapping], reference: Mapping[str, Mapping], *, real: Counts,
                   gate: Mapping, truth: Mapping[str, Counts] | None = None,
                   mode: Literal["screen", "confirm"] = "screen", commitment: Mapping | None = None,
                   truth_runs: Mapping[str, Mapping] | None = None,
                   ledger_runs: Sequence[Mapping] | None = None) -> dict:
    """The verdict of ``candidate`` against ``reference`` with every gate step and the report-only statistics.

    Each arm maps a draw key to a judged draw: ``counts`` (measured count per row over exactly the rows of
    ``real``), ``health`` (the judge's health record), ``guard_stats`` (``entry_p95`` per entry edge and network,
    ``brakings``, ``post_window``, optionally the judge's ``init52``) and ``run``: the ledger ``id``, the draw key's
    ``key_sha256``, ``key_public`` (False only for a secret key), the ``binding`` and the ``identity``
    (``measurement_code_sha256``, ``runtime_sha256``). ``real`` holds the real counts on every scored row (182); the
    stage target is ``truth`` (a twin-truth ensemble of counts by key) when given, else ``real``; ``truth_runs``
    holds each truth draw's ``run`` and ``health``. ``commitment`` (confirmation only) holds ``gate_sha256``,
    ``target_sha256``, the ordered ``candidate_keys_sha256`` and ``reference_keys_sha256`` (K each), the ordered
    ``candidate_spare_keys_sha256`` and ``reference_spare_keys_sha256`` (possibly empty), and the
    ``candidate_binding`` and ``reference_binding``, all frozen before the runs. ``ledger_runs`` (confirmation only)
    holds every ledger row with a draw key as ``key_use_step`` reads it.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}; use one of {MODES}")
    _check_gate(gate)
    steps: dict[str, dict] = {"H1": invariants_step(candidate, reference, real=real, truth=truth, gate=gate,
                                                    mode=mode, commitment=commitment, truth_runs=truth_runs,
                                                    ledger_runs=ledger_runs)}
    design = steps["H1"]["design"]
    base = {"schema": STAGE_SCHEMA, "mode": mode, "design": design, "draws": gate["draws"],
            "objective": gate["objective"], "g": gate["g"], "alpha": gate["alpha"], "gate_sha256": gate_sha256(gate),
            "target": "truth" if truth is not None else "real", "target_sha256": target_sha256(real, truth),
            "claim": CLAIM}
    if not steps["H1"]["passes"]:
        return {**base, "status": "blocked", "label": "blocked", "first_failure": "H1", "rate": "none",
                "note": None, "note_detail": None, "steps": steps, "report": None}
    rows = sorted(real)
    main = ensemble.main_rows(rows)
    keys_c, keys_r = sorted(candidate), sorted(reference)
    c_counts, r_counts = [candidate[k]["counts"] for k in keys_c], [reference[k]["counts"] for k in keys_r]
    members = _members(real, truth)
    init_members = [real] if gate["init52_target"] == "real" else members
    guard_members = [real] if gate["guard_target"] == "real" else members
    guard_basis = {"target": "real" if gate["guard_target"] == "real" or truth is None else "truth",
                   "margin_basis": "calibrated against the real counts (C1)"}
    steps["H2"] = health_step(candidate, reference, gate, truth_runs)
    steps["H3"] = {"rule": "H3 guards: quiet-entry K-mean p95 and the declared re-key backstops",
                   **guards.h3_guards([candidate[k]["guard_stats"] for k in keys_c],
                                      [reference[k]["guard_stats"] for k in keys_r], margins=gate["h3_margins"],
                                      entry_classes=gate["entry_classes"], quiet_floor=gate["quiet_entry_floor_s"])}
    steps["N1"] = {**row_growth_step(c_counts, r_counts, guard_members, rows, gate["n1_row_margin"],
                                     report_above=gate["g"] / 2), **guard_basis}
    steps["N2"] = init52_step(c_counts, r_counts, init_members, rows, gate["n2_init52_margin"],
                              gate["init52_target"])
    steps["N3"] = {**crps_margin_step(c_counts, r_counts, guard_members, main, "totals13",
                                      max(gate["n3_floor"], gate["n3_totals13_margin"])),
                   "floor": gate["n3_floor"], **guard_basis}
    steps["N4"] = {**crps_margin_step(c_counts, r_counts, guard_members, main, "main130", gate["n4_main130_margin"]),
                   **guard_basis}
    steps["no_effect"] = ({"rule": "every scored row equals the reference's on every key", "applies": True,
                           "value": all(candidate[k]["counts"] == reference[k]["counts"] for k in keys_c)}
                          if design == "shared" else
                          {"rule": "every scored row equals the reference's on every key", "applies": False,
                           "value": None, "reason": "the arms run on disjoint keys"})
    objective = gate["objective"]
    target = _target_values(members, main, objective)
    value_keys = list(target if isinstance(target, Mapping) else target[0])
    c_values, r_values = _objective(c_counts, main, objective), _objective(r_counts, main, objective)
    relation = steps["H1"]["keys"]["relation"]
    steps["I"] = ensemble.unpaired_crps_test(c_values, r_values, target, value_keys, g=gate["g"],
                                             alpha=gate["alpha"], keys_relation=relation)
    first = next((step for step, _ in STEP_LABELS if not steps[step]["passes"]), None)
    statistic, g_q = steps["I"]["T"], ensemble._exact(gate["g"])
    if first is not None:
        label = dict(STEP_LABELS)[first]
    elif steps["no_effect"]["value"]:
        label, first = "no_effect", "no_effect"
    elif steps["I"]["passes"]:
        label = "improved"
    elif statistic <= -g_q:
        label = "not_significant"
    elif statistic >= g_q:
        label = "worse"
    else:
        label = "not_material"
    report = _report(candidate, reference, keys_c, keys_r, c_values, r_values, target, value_keys, real=real,
                     truth=truth, main=main, gate=gate, design=design, relation=relation)
    d_tae = report["split"]["d_mean_tae"]
    note = ("spread_driven" if statistic <= -g_q and d_tae > -g_q else
            "tae_only" if d_tae <= -g_q and statistic > -g_q else None)
    return {**base, "status": "measured", "label": label, "first_failure": first,
            "rate": "alpha" if mode == "confirm" and label == "improved" else "none",
            "note": note, "note_detail": NOTES.get(note), "steps": steps, "report": report}


# --- report-only statistics ----------------------------------------------------------------------------------------

def _rank_p(draws: Sequence[Counts], target: Counts, keys: Sequence[str]) -> dict:
    """Exchangeability rank of the target among the K + 1 members by mean L1 distance to the others."""
    pool = [*draws, target]
    spread = [Fraction(sum(ensemble.l1(pool[i], pool[j], keys) for j in range(len(pool)) if j != i), len(pool) - 1)
              for i in range(len(pool))]
    more = sum(value >= spread[-1] for value in spread[:-1])
    return {"rank_p": Fraction(1 + more, len(pool)), "draws_more_outlying": more}


def _predictive_z(draws: Sequence[Counts], target: Counts, keys: Sequence[str]) -> dict[str, float | None]:
    """(y - mean) / (sd sqrt(1 + 1/K)) per value; None when the draws have no spread."""
    found = {}
    for key in keys:
        values = [draw[key] for draw in draws]
        spread = statistics.stdev(values)
        found[key] = (None if spread == 0 else
                      round((target[key] - statistics.fmean(values)) / (spread * math.sqrt(1 + 1 / len(values))), 3))
    return found


NOT_APPLICABLE = "not applicable"
_NO_CRN = ("not applicable: the arms run on disjoint keys, so no CRN exists between them and the rho rule of "
           "decision 4 has no basis; the K rule for the unpaired design is an owner decision")


def _report(candidate: Mapping[str, Mapping], reference: Mapping[str, Mapping], keys_c: list[str],
            keys_r: list[str], c_values: list[dict], r_values: list[dict], target: ensemble.Target,
            value_keys: list[str], *, real: Counts, truth: Mapping[str, Counts] | None, main: list[str],
            gate: Mapping, design: str, relation: str) -> dict:
    g, alpha, objective = gate["g"], gate["alpha"], gate["objective"]
    members = _members(real, truth)
    d_tae = ensemble.mean_distance(c_values, target, value_keys) - ensemble.mean_distance(r_values, target, value_keys)
    d_half_gmd = (ensemble.gini_mean_difference(c_values, value_keys)
                  - ensemble.gini_mean_difference(r_values, value_keys)) / 2
    c_counts, r_counts = [candidate[k]["counts"] for k in keys_c], [reference[k]["counts"] for k in keys_r]
    sensitivity = {}
    for name in dict.fromkeys(("main130", "ms3", "totals13")):
        t_values = _target_values(members, main, name)
        names = list(t_values if isinstance(t_values, Mapping) else t_values[0])
        result = ensemble.unpaired_crps_test(_objective(c_counts, main, name), _objective(r_counts, main, name),
                                             t_values, names, g=g, alpha=alpha, keys_relation=relation)
        sensitivity[name] = {key: result[key] for key in ("T", "t", "p", "p_value")}
    # Pairing for the paired diagnostics: the shared CRN key, or (disjoint keys) the sorted key order of the arms.
    crn = design == "shared"
    paired_c = {key: candidate[key]["counts"] for key in keys_c}
    paired_r = {ck: reference[rk]["counts"] for ck, rk in zip(keys_c, keys_r)}
    case_target = real if truth is None else members
    case = ensemble.evaluate_case_v3(paired_c, paired_r, case_target, main, g=g, alpha=alpha,
                                     delta=gate["delta_report"], objective=objective)
    truth_members = 1 if truth is None else len(members)
    flip_tae = ensemble.sign_flip_lower_p([int(d * truth_members) for d in case["per_key_d"]])
    if crn:
        same = [ensemble.l1(c, ensemble.objective_values(paired_r[k], main, objective), value_keys)
                for k, c in zip(keys_c, c_values)]
        reference_pairs = statistics.fmean(ensemble.l1(a, b, value_keys) for i, a in enumerate(r_values)
                                           for b in r_values[i + 1:])
        rho = (statistics.stdev(float(d) for d in case["per_key_d"]) / gate["rekey_sd_ms4"]
               if objective == "ms4" else None)
        k_rule = None if rho is None else ensemble.confirmation_draws(rho)
        kappa = statistics.fmean(same) / reference_pairs if reference_pairs else None
    else:
        rho = k_rule = kappa = NOT_APPLICABLE
    paired = {"pairing": "crn" if crn else "index",
              "pairs": [[ck, rk] for ck, rk in zip(keys_c, keys_r)],
              "label": ("draws paired by their shared CRN key" if crn else
                        "independent draws paired by sorted key order (no CRN)"),
              "exchangeability": ensemble.PAIRED_EXCHANGEABILITY if crn else
              "independent draws are exchangeable within pairs under the sharp null; no CRN variance reduction",
              "crps_sign_flip": {key: case["primary"][key] for key in ("T", "t", "p", "p_value", "min_p", "passes")},
              "ensemble_tae_flip_p": flip_tae, "per_key_d": case["per_key_d"],
              "v2_sign_rule": case["v2_sign_rule"],
              "v2_s_rule": {**case["noninferiority"], "verdict": None,
                            "note": "report only: no simplified verdict reaches a gate (decision 5, A1.5)"},
              "energy": case["energy"], "main130_sign_flip": {key: case["main130"][key] for key in ("T", "p")},
              "rho": rho, "k_rule": k_rule, "kappa": kappa, "rho_note": None if crn else _NO_CRN,
              "never_gates": True}
    real_totals = ensemble.field_totals(real, main)
    per_field = {}
    for field_key, value in real_totals.items():
        ranges = {}
        for arm, draws in (("candidate", c_counts), ("reference", r_counts)):
            totals = [ensemble.field_totals(counts, main)[field_key] for counts in draws]
            ranges[arm] = {"min": min(totals), "max": max(totals),
                           "real": "above" if value > max(totals) else "below" if value < min(totals) else "inside"}
        per_field[field_key.removeprefix("T:")] = {"real_total": value, **ranges}
    single = isinstance(target, Mapping)
    informative = [key for key in value_keys if key.split(":", 1)[1].split("@", 1)[0] in INFORMATIVE_FIELDS]
    h3_report = {f"entry:{name.removeprefix('entry_p95:')}": result for name, result in guards.kmean_margins(
        [candidate[k]["guard_stats"] for k in keys_c], [reference[k]["guard_stats"] for k in keys_r],
        gate.get("h3_report_margins") or {}).items()}
    return {"split": {"d_mean_tae": d_tae, "d_crps": d_tae - d_half_gmd, "d_half_gmd": d_half_gmd,
                      "identity": "d_mean_tae = d_crps + d_half_gmd"},
            "sensitivity_unpaired": sensitivity,
            "energy_distance_unpaired": ensemble.energy_distance(c_values, r_values, value_keys),
            "paired": paired,
            "per_field_totals_vs_real": per_field,
            "target_rank": None if not single else {
                "candidate": _rank_p(c_values, target, value_keys),
                "reference": _rank_p(r_values, target, value_keys)},
            "predictive_z": None if not single else {
                "candidate": _predictive_z(c_values, target, informative),
                "reference": _predictive_z(r_values, target, informative)},
            "median_distance": None if case["median_distance"] is None else case["median_distance"][objective],
            "backlog": guards.backlog_flags({k: candidate[k]["guard_stats"] for k in keys_c},
                                            {k: reference[k]["guard_stats"] for k in keys_r},
                                            entry_classes=gate["entry_classes"], floor=gate["backlog_floor_s"]),
            "h3_report_only": {"guards": h3_report, "fired": [name for name, row in h3_report.items() if row["fires"]],
                               "never_gates": True,
                               "note": "busy-entry K-mean re-key margins without a declared blocking backstop "
                                       "(v3 section 5)"},
            "parent_unhealthy": gate["health"].get("parent_unhealthy")}


def to_json(value: object) -> object:
    """JSON-ready copy of a verdict: an exact Fraction becomes its 'p/q' string, tuples become lists."""
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): to_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
