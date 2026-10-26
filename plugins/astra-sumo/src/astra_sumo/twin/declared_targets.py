"""Declared target deltas of a Build goal (fragility fixes on a Calibrate parent).

A fragility-fix goal takes the goal source's own reference facts (``core/reference_net_facts.py``, so every fact is
matched and frozen) and declares the small set of in-scope movement and signal facts the typed op must change: a
movement inventory that gains or loses a lane pair, a signal binding that is removed, added or renumbered by the
materialized op's linkIndex layout. Only those facts are mismatched on the source (V_source > 0); every other fact
stays matched, so G0-REGRESSED protects the whole network outside the scope and every in-scope fact the op must not
touch. Each entry is tagged ``fragility_fix`` and is explore evidence only.

The delta is derived from a materialization of the declared op on the source (``derive_targets``): both nets'
reference facts are built by the same producer and only movement and signal facts anchored on the scope junctions
that differ enter the delta. Geometry, lane, permission and speed facts are never declared: a materialization that
moves them regresses honestly. Because the delta is fitted to that materialization, meeting it (V = 0) holds by
construction: V then shows only that the gate's materialization did what the declared op implies, never that the
edit is right. So lint (``lint_targets``, ``implied_errors``) refuses every entry the declared typed ops
(``derived_from.steps[].op``) do not imply: a movement change only on an op's from-lane, equal to the source's set
plus or minus that op's pair; a removed or added signal link only for a delete or add op's own pair; a renumbered
link only at the same controller, and together a bijection of the controller's links onto 0..n-1 (the control
re-index checks it against netconvert's own map, ``control_reindex.crosscheck_declared``); never a changed
controller. Every entry is also tied to its fact id (``entry_errors``, ``source_errors``): a declared selector is the
producer's own for the id (the id's incoming lane or lane pair), so a target never scores another connection than the
one it names; a signal entry declares bindings (``tls_id``, nonnegative integer ``link_index``); a set or remove
declares the source fact's own value as its parent (for a signal link, its controller and index); and an added link
sits at a controller that binds the source links of the id's incoming edge.

A merge the op creates on purpose is declared in ``evidence_justified_destination_merges`` with the core audit's key
(``source|target|toLane``) and exact source lanes (``core/connection_mode_audit.py``), basis
``model_justified_simulation_fragility`` and a label that says no field evidence backs it. ``justify_merges`` takes
exactly those findings out of the INT rows INV-AUDIT compares and reports them. This is a twin-layer exemption of the
explore gate only: the core audit itself (which also serves release gates) keeps accepting only official MAP and
engineering-plan evidence, so a release audit still reports the merge.

Explore only: a declared target is a model-relative intent, never a field fact or a promotion argument.
"""

from __future__ import annotations

import copy
import math
import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

TAG = "fragility_fix"
KINDS = ("movement_inventory", "signal_group")
ACTIONS = ("set", "add", "remove")
REASONS = ("movement_changed", "link_removed", "link_added", "link_renumbered", "binding_changed")
MERGE_BASIS = "model_justified_simulation_fragility"
COPY_RULES = ("verbatim", "minor_where_yielding")
OPS = ("add_lane_connection", "delete_lane_connection")
CLAIM = ("Declared fragility-fix targets: model-relative intents derived from the materialized op on the goal source; "
         "explore only, no field claim, no promotion authority.")
_MOVEMENT_ID = re.compile(r"^lanes:(?P<lane>.+):movement_inventory$")
_SIGNAL_ID = re.compile(r"^pair:(?P<a>.+)>(?P<b>.+):signal_group$")


def _lane_edge(lane: str) -> str:
    return lane.rpartition("_")[0]


def _incoming_lane(fact_id: str, kind: str) -> str | None:
    pattern = _MOVEMENT_ID if kind == "movement_inventory" else _SIGNAL_ID
    match = pattern.match(fact_id)
    if match is None:
        return None
    return match["lane"] if kind == "movement_inventory" else match["a"]


def _facts_by_id(table: Mapping) -> dict[str, dict]:
    return {fact["id"]: fact for fact in table["facts"]}


def _mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def fact_selector(fact_id: str, kind: Any) -> dict | None:
    """The selector ``core/reference_net_facts.py`` writes for a movement or signal fact id: the id's incoming lane
    (``incoming_lane_ids``) or lane pair (``lane_pair``); ``None`` for any other id."""
    if kind == "movement_inventory":
        match = _MOVEMENT_ID.match(fact_id)
        return {"incoming_lane_ids": [match["lane"]]} if match else None
    if kind == "signal_group":
        match = _SIGNAL_ID.match(fact_id)
        return {"lane_pair": [match["a"], match["b"]]} if match else None
    return None


def _binding(value: Any) -> bool:
    """A signal binding as the scorer accepts it: exactly a controller id and a nonnegative integer link index."""
    return (isinstance(value, Mapping) and set(value) == {"tls_id", "link_index"}
            and isinstance(value["tls_id"], str) and bool(value["tls_id"])
            and type(value["link_index"]) is int and value["link_index"] >= 0)


def _binding_fields(action: Any) -> tuple[str, ...]:
    return {"set": ("parent_expected", "expected"), "add": ("expected",), "remove": ("parent_expected",)}.get(
        action, ())


def entry_errors(entry: Mapping) -> list[str]:
    """One entry against its own fact id: a declared selector (required for an add) is the producer's own for the id,
    so it scores the lane or lane pair the id names, and a signal entry's declared values are bindings."""
    fact, kind, action = str(entry.get("fact")), entry.get("kind"), entry.get("action")
    wanted = fact_selector(fact, kind)
    if wanted is None:
        return []
    errors = []
    selector = entry.get("selector")
    if (action == "add" or selector is not None) and selector != wanted:
        errors.append(f"{fact}: the selector {selector} is not the fact id's own {wanted}")
    if kind == "signal_group":
        errors += [f"{fact}: {field} is not a signal binding (tls_id and a nonnegative integer link_index)"
                   for field in _binding_fields(action) if not _binding(entry.get(field))]
    return errors


def _edge_controllers(base: Mapping) -> dict[str, set[str]]:
    """Per incoming edge, the controllers that bind one of its source links."""
    found: dict[str, set[str]] = defaultdict(set)
    for fact in base.get("facts") or []:
        match = _SIGNAL_ID.match(str(fact.get("id"))) if fact.get("kind") == "signal_group" else None
        if match is not None:
            found[_lane_edge(match["a"])].add(str(_mapping(fact.get("expected")).get("tls_id")))
    return found


def source_errors(entry: Mapping, facts: Mapping[str, Mapping], controllers: Mapping[str, set[str]]) -> list[str]:
    """One entry against the base table (the goal source's facts by id, ``controllers`` from ``_edge_controllers``): a
    set or remove names a base fact of its kind, with the producer's selector for the id, whose value is the declared
    parent value (for a signal link, its tls_id and link_index); an add names a new fact, and an added signal link sits
    at a controller that binds the source links of the id's incoming edge (any source controller when none does)."""
    fact_id, kind, action = str(entry.get("fact")), entry.get("kind"), entry.get("action")
    found = facts.get(fact_id)
    if action == "add":
        if found is not None:
            return [f"{fact_id}: an add target names an existing fact of the base table"]
        lane = _incoming_lane(fact_id, kind) if kind == "signal_group" else None
        if lane is None:
            return []
        tls = _mapping(entry.get("expected")).get("tls_id")
        allowed = controllers.get(_lane_edge(lane)) or {name for names in controllers.values() for name in names}
        if tls not in allowed:
            return [f"{fact_id}: the added link's controller {tls} is not one that binds the source links of incoming "
                    f"edge {_lane_edge(lane)} {sorted(allowed)}"]
        return []
    if action not in ("set", "remove"):
        return []
    if found is None or found.get("kind") != kind:
        return [f"{fact_id}: a {action} target names no {kind} fact of the base table"]
    if found.get("selector") != fact_selector(fact_id, kind):
        return [f"{fact_id}: the base fact's selector {found.get('selector')} is not the fact id's own"]
    if found.get("expected") != entry.get("parent_expected"):
        return [f"{fact_id}: the base table's value differs from the declared parent value"]
    return []


def derive_targets(parent_table: Mapping, candidate_table: Mapping, *, edge_to: Mapping[str, str],
                   junctions: list[str] | set[str]) -> list[dict]:
    """The movement and signal facts that differ between two reference fact tables of the same producer (the goal
    source's and a materialized candidate's), restricted to incoming lanes whose edge ends at a scope junction.

    ``edge_to`` maps every road edge to its to-junction on the source. Each entry names the fact, its kind, the action
    (``set``, ``add`` or ``remove``), the expected value the delta declares, the source's value and a reason."""
    wanted = set(junctions)
    before, after = _facts_by_id(parent_table), _facts_by_id(candidate_table)
    entries = []
    for fact_id in sorted(set(before) | set(after)):
        old, new = before.get(fact_id), after.get(fact_id)
        kind = (old or new)["kind"]
        if kind not in KINDS:
            continue
        lane = _incoming_lane(fact_id, kind)
        junction = edge_to.get(_lane_edge(lane)) if lane else None
        if junction not in wanted:
            continue
        if old is not None and new is not None:
            if old["expected"] == new["expected"] and old["selector"] == new["selector"]:
                continue
            if old["selector"] != new["selector"]:
                raise ValueError(f"{fact_id}: the selector changed; a declared target only changes expected values")
            if kind == "movement_inventory":
                reason = "movement_changed"
            elif old["expected"]["tls_id"] == new["expected"]["tls_id"]:
                reason = "link_renumbered"
            else:
                reason = "binding_changed"
            entries.append({"fact": fact_id, "kind": kind, "action": "set", "expected": new["expected"],
                            "parent_expected": old["expected"], "anchor": f"junction:{junction}", "reason": reason,
                            "tag": TAG})
        elif new is not None:
            entries.append({"fact": fact_id, "kind": kind, "action": "add", "selector": new["selector"],
                            "expected": new["expected"], "anchor": f"junction:{junction}",
                            "reason": "link_added" if kind == "signal_group" else "movement_changed", "tag": TAG})
        else:
            entries.append({"fact": fact_id, "kind": kind, "action": "remove", "parent_expected": old["expected"],
                            "anchor": f"junction:{junction}",
                            "reason": "link_removed" if kind == "signal_group" else "movement_changed", "tag": TAG})
    return entries


def merge_entries(*groups: list[dict]) -> list[dict]:
    """The union of several derived deltas (a chain of ops on disjoint junctions); one fact declared twice with
    different values is refused."""
    found: dict[str, dict] = {}
    for group in groups:
        for entry in group:
            seen = found.get(entry["fact"])
            if seen is not None and seen != entry:
                raise ValueError(f"{entry['fact']}: declared twice with different targets")
            found[entry["fact"]] = entry
    return [found[key] for key in sorted(found)]


def apply_targets(table: Mapping, entries: list[dict]) -> dict:
    """``table`` (an ``astra.network-facts/v1`` dict) with the declared targets applied, facts kept in the producer's
    order (kind, then id). A ``set`` or ``remove`` must name an existing fact whose value equals ``parent_expected``;
    an ``add`` must name a new one. Every entry must also fit its fact id (``entry_errors``, ``source_errors``), so an
    add never writes a selector that scores another lane or lane pair than its id names. Applied facts carry the
    delta's tag and reason in their mapping diagnostics."""
    result = copy.deepcopy(dict(table))
    facts = _facts_by_id(result)
    source, controllers = _facts_by_id(table), _edge_controllers(table)
    kinds = list(dict.fromkeys(fact["kind"] for fact in result["facts"]))
    template = next((fact for fact in result["facts"] if fact["kind"] in KINDS), None)
    for entry in entries:
        fact_id, action = entry["fact"], entry["action"]
        problems = entry_errors(entry) + source_errors(entry, source, controllers)
        if problems:
            raise ValueError("; ".join(problems))
        marker = {"declared_target": {"tag": entry["tag"], "reason": entry["reason"], "action": action}}
        if action in ("set", "remove"):
            fact = facts.get(fact_id)
            if fact is None or fact["kind"] != entry["kind"]:
                raise ValueError(f"{fact_id}: a {action} target names no {entry['kind']} fact of the base table")
            if fact["expected"] != entry.get("parent_expected"):
                raise ValueError(f"{fact_id}: the base table's value differs from the declared parent value")
            if action == "remove":
                del facts[fact_id]
            else:
                fact["expected"] = copy.deepcopy(entry["expected"])
                fact["mapping_diagnostics"] = {**fact.get("mapping_diagnostics", {}), **marker}
        else:
            if fact_id in facts or template is None:
                raise ValueError(f"{fact_id}: an add target names an existing fact or the table has no template")
            fact = {key: copy.deepcopy(value) for key, value in template.items()
                    if key in ("source_ids", "mapping_status", "mapping_confidence", "mapping_method", "traffic_mode")}
            fact.update(id=fact_id, kind=entry["kind"], selector=copy.deepcopy(entry["selector"]),
                        expected=copy.deepcopy(entry["expected"]),
                        mapping_diagnostics={**template.get("mapping_diagnostics", {}), **marker})
            facts[fact_id] = fact
            if entry["kind"] not in kinds:
                kinds.append(entry["kind"])
    result["facts"] = [fact for kind in kinds for fact in sorted((f for f in facts.values() if f["kind"] == kind),
                                                                 key=lambda row: row["id"])]
    return result


def declared_ops(block: Mapping) -> tuple[list[dict], list[str]]:
    """The typed lane-connection ops of ``derived_from.steps`` (``operation``, ``from_lane``, ``to_lane``) and the
    errors of a block without such a step."""
    errors, ops = [], []
    steps = (block.get("derived_from") or {}).get("steps") or []
    if not steps:
        errors.append("derived_from names no step with a typed op")
    for position, step in enumerate(steps):
        op = (step or {}).get("op") or {}
        params = op.get("params") or {}
        if op.get("operation") not in OPS or any(params.get(key) is None for key in ("from", "fromLane", "to",
                                                                                      "toLane")):
            errors.append(f"derived_from step {position}: not a typed lane-connection op {OPS}")
            continue
        ops.append({"operation": op["operation"], "from_lane": f"{params['from']}_{params['fromLane']}",
                    "to_lane": f"{params['to']}_{params['toLane']}"})
    return ops, errors


def _pairs(value: Any) -> set[tuple[str, str]] | None:
    if not isinstance(value, list) or not all(isinstance(row, (list, tuple)) and len(row) == 2 for row in value):
        return None
    return {(str(row[0]), str(row[1])) for row in value}


def _signal_layouts(base: Mapping) -> dict[str, dict[tuple[str, str], int]]:
    layouts: dict[str, dict[tuple[str, str], int]] = {}
    for fact in base.get("facts") or []:
        match = _SIGNAL_ID.match(str(fact.get("id"))) if fact.get("kind") == "signal_group" else None
        if match is not None:
            expected = fact["expected"]
            layouts.setdefault(expected["tls_id"], {})[(match["a"], match["b"])] = int(expected["link_index"])
    return layouts


def implied_errors(entries: list[Mapping], ops: list[Mapping], *, base: Mapping | None = None) -> list[str]:
    """Entries the declared ops do not imply or that do not fit their fact id (``entry_errors``), and (with ``base``,
    the goal source's reference facts) entries that do not fit the source's facts (``source_errors``) and controller
    layouts the declared signal entries do not keep a bijection. An empty list means the delta is the ops' own."""
    errors: list[str] = []
    change: dict[str, dict[str, set]] = defaultdict(lambda: {"add": set(), "remove": set()})
    for op in ops:
        side = "add" if op["operation"] == "add_lane_connection" else "remove"
        change[op["from_lane"]][side].add((op["from_lane"], op["to_lane"]))
    added = {pair for row in change.values() for pair in row["add"]}
    removed = {pair for row in change.values() for pair in row["remove"]}
    lanes_seen: set[str] = set()
    signals: dict[str, list[tuple[Mapping, tuple[str, str]]]] = defaultdict(list)
    for entry in entries:
        fact, kind, reason, action = (str(entry.get("fact")), entry.get("kind"), entry.get("reason"),
                                      entry.get("action"))
        errors += entry_errors(entry)
        if kind == "movement_inventory":
            lane = _incoming_lane(fact, kind)
            if reason != "movement_changed":
                errors.append(f"{fact}: a movement entry's reason must be movement_changed")
            if lane not in change:
                errors.append(f"{fact}: no declared op starts on {lane}")
                continue
            lanes_seen.add(lane)
            before = set() if action == "add" else _pairs(entry.get("parent_expected"))
            after = set() if action == "remove" else _pairs(entry.get("expected"))
            if before is None or after is None:
                errors.append(f"{fact}: the movement values are not lists of lane pairs")
                continue
            ops_here = change[lane]
            if ops_here["add"] & before or not ops_here["remove"] <= before:
                errors.append(f"{fact}: the declared ops do not apply to the source's movement set")
            implied = (before | ops_here["add"]) - ops_here["remove"]
            if after != implied:
                errors.append(f"{fact}: the declared set is not the source's set plus or minus the ops' pairs")
            if (action == "add" and before) or (action == "remove" and implied):
                errors.append(f"{fact}: action {action} does not fit the implied change")
            continue
        match = _SIGNAL_ID.match(fact) if kind == "signal_group" else None
        if match is None:
            continue
        pair = (match["a"], match["b"])
        old, new = _mapping(entry.get("parent_expected")), _mapping(entry.get("expected"))
        if reason == "link_removed":
            if pair not in removed or action != "remove":
                errors.append(f"{fact}: a removed link must be a delete op's own pair")
        elif reason == "link_added":
            if pair not in added or action != "add":
                errors.append(f"{fact}: an added link must be an add op's own pair")
        elif reason == "link_renumbered":
            if (action != "set" or pair in added | removed or old.get("tls_id") != new.get("tls_id")
                    or old.get("link_index") == new.get("link_index")):
                errors.append(f"{fact}: a renumbered link keeps its controller, changes its index and is no op pair")
        else:
            errors.append(f"{fact}: reason {reason} is not implied by a lane-connection op")
        signals[str((new or old).get("tls_id"))].append((entry, pair))
    for lane, row in sorted(change.items()):
        if lane not in lanes_seen and row["add"] != row["remove"]:
            errors.append(f"op from-lane {lane}: the delta declares no movement change for it")
    if base is None:
        return errors
    facts, controllers = {fact.get("id"): fact for fact in base.get("facts") or []}, _edge_controllers(base)
    for entry in entries:
        if entry.get("kind") in KINDS:
            errors += source_errors(entry, facts, controllers)
    layouts = _signal_layouts(base)
    declared = {pair for rows in signals.values() for entry, pair in rows if entry.get("reason") == "link_removed"}
    for pair in sorted(removed):
        if any(pair in layout for layout in layouts.values()) and pair not in declared:
            errors.append(f"{pair[0]}>{pair[1]}: a deleted signal link without a link_removed entry")
    for tls, rows in sorted(signals.items()):
        layout = dict(layouts.get(tls, {}))
        contiguous = sorted(layout.values()) == list(range(len(layout)))
        for entry, pair in rows:
            reason = entry.get("reason")
            if reason == "link_removed":
                if layout.pop(pair, None) is None:
                    errors.append(f"{tls}: removed link {pair} is not a source link")
            elif reason in ("link_added", "link_renumbered") and _binding(entry.get("expected")):
                # A malformed binding is refused above (entry_errors or the reason check); it never enters the layout.
                if reason == "link_added" and pair in layout:
                    errors.append(f"{tls}: added link {pair} is already a source link")
                if reason == "link_renumbered" and pair not in layout:
                    errors.append(f"{tls}: renumbered link {pair} is not a source link")
                layout[pair] = entry["expected"]["link_index"]
        indices = sorted(layout.values())
        if len(set(indices)) != len(indices) or (contiguous and indices != list(range(len(indices)))):
            errors.append(f"{tls}: the declared links are not a bijection of the controller's links onto "
                          f"{'0..n-1' if contiguous else 'distinct indices'}")
    return errors


def _in_scope(anchor: str, edges: set[str], junctions: set[str]) -> bool:
    kind, _, value = str(anchor).partition(":")
    return (kind == "junction" and value in junctions) or (kind == "edge" and value in edges)


def lint_targets(block: Mapping, scope: Mapping) -> list[str]:
    """GOAL LINT of a declared-target block against the goal's scope; an empty list means it may be written.

    Every entry is a movement or signal fact tagged ``fragility_fix`` whose anchor junction is a mutable junction and
    whose incoming lane lies on a mutable edge; set and add declare a value, remove declares none; every entry fits
    its fact id (``entry_errors`` through ``implied_errors``: an add's selector, and any other declared selector, is
    the id's own incoming lane or lane pair, and a signal entry declares bindings); every added or removed signal
    binding has its control declaration at the link's own controller (an added link names a sibling and a copy rule);
    every declared merge has the model-justified basis, a site, a label saying there is no field evidence and exact
    source lanes on a mutable source edge, and is the destination of an add op. ``derived_from.steps`` names the typed
    ops, each starting on a mutable edge, and every entry and control declaration is one the ops imply
    (``implied_errors``; the source-fact checks and the controller bijection need the source's facts and are checked
    by goal-create)."""
    errors = []
    edges, junctions = set(scope.get("mutable_edge_ids") or []), set(scope.get("mutable_junction_ids") or [])
    if block.get("tag") != TAG:
        errors.append(f"declared targets must be tagged {TAG}")
    if block.get("mode") != "explore":
        errors.append("declared targets are explore only")
    entries = block.get("entries") or []
    if not entries:
        errors.append("a declared-target block needs at least one entry")
    seen: set[str] = set()
    for entry in entries:
        fact, kind, action = entry.get("fact"), entry.get("kind"), entry.get("action")
        if fact in seen:
            errors.append(f"{fact}: declared twice")
        seen.add(fact)
        if kind not in KINDS:
            errors.append(f"{fact}: kind {kind} is not a movement or signal kind")
            continue
        if entry.get("tag") != TAG:
            errors.append(f"{fact}: not tagged {TAG}")
        if action not in ACTIONS:
            errors.append(f"{fact}: unknown action {action}")
        if entry.get("reason") not in REASONS:
            errors.append(f"{fact}: unknown reason {entry.get('reason')}")
        lane = _incoming_lane(str(fact), kind)
        if lane is None:
            errors.append(f"{fact}: the id is not a {kind} fact id")
        elif _lane_edge(lane) not in edges:
            errors.append(f"{fact}: incoming lane {lane} is not on a mutable edge")
        if not _in_scope(entry.get("anchor", ""), edges, junctions):
            errors.append(f"{fact}: anchor {entry.get('anchor')} is outside the scope")
        if action in ("set", "add") and entry.get("expected") is None:
            errors.append(f"{fact}: a {action} target declares no value")
        if action == "remove" and entry.get("expected") is not None:
            errors.append(f"{fact}: a remove target declares a value")
    ops, op_errors = declared_ops(block)
    errors += op_errors
    for op in ops:
        if _lane_edge(op["from_lane"]) not in edges:
            errors.append(f"op {op['operation']} {op['from_lane']}: the from-lane is not on a mutable edge")
        if scope.get("ops") is not None and op["operation"] not in scope["ops"]:
            errors.append(f"op {op['operation']}: not one of the goal's scope ops")
    errors += implied_errors([entry for entry in entries if isinstance(entry, Mapping)], ops)
    control = block.get("control") or {}
    added = {tuple(row.get("pair") or ()): row for row in control.get("added_links") or []}
    removed = {tuple(row.get("pair") or ()): row for row in control.get("removed_links") or []}
    op_pairs = {(op["from_lane"], op["to_lane"]): op["operation"] for op in ops}
    for pair in sorted(added):
        if op_pairs.get(pair) != "add_lane_connection":
            errors.append(f"control: added link {list(pair)} is not an add op's pair")
    for pair in sorted(removed):
        if op_pairs.get(pair) != "delete_lane_connection":
            errors.append(f"control: removed link {list(pair)} is not a delete op's pair")
    for entry in entries:
        if entry.get("kind") != "signal_group":
            continue
        match = _SIGNAL_ID.match(str(entry.get("fact")))
        pair = (match["a"], match["b"]) if match else ()
        if entry.get("reason") == "link_added":
            row = added.get(pair)
            if row is None:
                errors.append(f"{entry.get('fact')}: an added link without a control declaration")
            elif row.get("copy_rule") not in COPY_RULES or len(row.get("sibling_pair") or ()) != 2:
                errors.append(f"{entry.get('fact')}: an added link needs a sibling pair and a copy rule {COPY_RULES}")
            elif row.get("tls_id") != _mapping(entry.get("expected")).get("tls_id"):
                errors.append(f"{entry.get('fact')}: the declared sibling is not at the added link's controller")
        if entry.get("reason") == "link_removed":
            row = removed.get(pair)
            if row is None:
                errors.append(f"{entry.get('fact')}: a removed link without a control declaration")
            elif row.get("tls_id") != _mapping(entry.get("parent_expected")).get("tls_id"):
                errors.append(f"{entry.get('fact')}: the removal's control declaration is not at the removed link's "
                              "controller")
    for key, evidence in sorted((block.get("evidence_justified_destination_merges") or {}).items()):
        parts = str(key).split("|")
        if len(parts) != 3 or not parts[2].isdigit():
            errors.append(f"merge {key}: the key is not source|target|toLane")
        elif parts[0] not in edges:
            errors.append(f"merge {key}: source edge {parts[0]} is not mutable")
        if not isinstance(evidence, Mapping) or evidence.get("basis") != MERGE_BASIS:
            errors.append(f"merge {key}: basis must be {MERGE_BASIS}")
            continue
        if "no field evidence" not in str(evidence.get("label", "")).lower() or not str(evidence.get("site", "")):
            errors.append(f"merge {key}: needs a site and a label stating that no field evidence backs it")
        lanes = [str(value) for value in evidence.get("source_lanes") or []]
        if len(lanes) < 2 or len(set(lanes)) != len(lanes) or not all(value.isdigit() for value in lanes):
            errors.append(f"merge {key}: source_lanes must be two or more distinct lane indices")
        if len(parts) == 3 and not any(
                op["operation"] == "add_lane_connection" and op["from_lane"].rpartition("_")[0] == parts[0]
                and op["to_lane"] == f"{parts[1]}_{parts[2]}" and op["from_lane"].rpartition("_")[2] in lanes
                for op in ops):
            errors.append(f"merge {key}: no declared add op creates this merge")
    return errors


def justify_merges(rows: list[dict], evidence: Mapping[str, Mapping] | None) -> tuple[list[dict], list[dict]]:
    """``(kept, justified)`` INT rows: a connection-mode ``destination_lane_merge`` finding whose key and exact source
    lanes a declared model-justified merge names is justified (taken out of INV-AUDIT, reported with its evidence);
    every other row is kept. The match is the core audit's own rule (key and sorted source lanes)."""
    if not evidence:
        return list(rows), []
    kept, justified = [], []
    for row in rows:
        # The audit text is ``[<audit>:]destination_lane_merge:<source|target|toLane>:<fromLane,...>``.
        tokens = str(row.get("finding", "")).split(":")
        kind = tokens.index("destination_lane_merge") if "destination_lane_merge" in tokens else None
        found = tokens[kind + 1:] if kind is not None else []
        declared = evidence.get(found[0]) if len(found) == 2 else None
        if (row.get("source") == "connection_mode_audit" and isinstance(declared, Mapping)
                and declared.get("basis") == MERGE_BASIS
                and sorted(str(value) for value in declared.get("source_lanes") or []) == sorted(found[1].split(","))):
            justified.append({"id": row["id"], "finding": row["finding"], "anchor": row.get("anchor"),
                              "evidence": dict(declared)})
        else:
            kept.append(row)
    return kept, justified


def block_evidence(block: Any) -> dict[str, dict]:
    """The declared merges of a goal's declared-target block (a model or a dict), as plain dicts."""
    if block is None:
        return {}
    data = block.model_dump() if hasattr(block, "model_dump") else dict(block)
    return {key: dict(value) for key, value in (data.get("evidence_justified_destination_merges") or {}).items()}


# ---- in-scope side effects of a materialization beyond its declared targets ---------------------------------------

SIDE_EFFECT_TOLERANCE_M = 1e-3


def _points(shape: str | None) -> list[tuple[float, float]]:
    return [tuple(float(value) for value in point.split(",")[:2]) for point in (shape or "").split()]


def _segment_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    (x, y), (ax, ay), (bx, by) = point, start, end
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    t = 0.0 if span == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / span))
    return math.hypot(x - ax - t * dx, y - ay - t * dy)


def _sampled(points: list[tuple[float, float]], step: float = 0.1) -> list[tuple[float, float]]:
    found = list(points[:1])
    for start, end in zip(points, points[1:]):
        count = max(1, math.ceil(math.dist(start, end) / step))
        found += [(start[0] + (end[0] - start[0]) * k / count, start[1] + (end[1] - start[1]) * k / count)
                  for k in range(1, count + 1)]
    return found


def hausdorff(first: list[tuple[float, float]], second: list[tuple[float, float]]) -> float:
    """Symmetric Hausdorff distance between two polylines (points sampled every 0.1 m against exact segments)."""
    def one_way(a, b):
        if len(b) == 1:
            return max(math.dist(point, b[0]) for point in _sampled(a))
        return max(min(_segment_distance(point, s, e) for s, e in zip(b, b[1:])) for point in _sampled(a))

    return max(one_way(first, second), one_way(second, first)) if first and second else math.inf


def _internal_junction(lane: str) -> str:
    return lane[1:].rsplit("_", 2)[0] if lane.startswith(":") else lane


def _path_records(root) -> tuple[dict, dict, dict]:
    """``(lanes, chains, requests)`` of one net: lane length, speed and shape by id; per road connection with a via
    lane its internal-lane chain and attributes; per junction the request rows keyed by the connection each row is."""
    lanes = {lane.get("id"): {"length": float(lane.get("length", "nan")), "speed": float(lane.get("speed", "nan")),
                              "shape": _points(lane.get("shape"))}
             for edge in root.iter("edge") for lane in edge.findall("lane")}
    following = {f"{row.get('from')}_{row.get('fromLane')}": row.get("via") for row in root.iter("connection")
                 if str(row.get("from")).startswith(":") and row.get("via")}
    chains, by_via = {}, {}
    for row in root.iter("connection"):
        if str(row.get("from")).startswith(":") or not row.get("via"):
            continue
        chain = [row.get("via")]
        while following.get(chain[-1]) and following[chain[-1]] not in chain:
            chain.append(following[chain[-1]])
        key = (f"{row.get('from')}_{row.get('fromLane')}", f"{row.get('to')}_{row.get('toLane')}")
        chains[key] = {"vias": chain, "attrs": dict(row.attrib)}
        # A junction's intLanes name one lane of each link's chain (the last part for a link with an internal
        # junction), so every part maps back to its connection.
        by_via.update({lane: key for lane in chain})
    requests = {}
    for junction in root.iter("junction"):
        if junction.get("type") == "internal":
            continue
        local = {position: by_via[lane] for position, lane in enumerate((junction.get("intLanes") or "").split())
                 if lane in by_via}
        rows = {}
        for request in junction.findall("request"):
            index = int(request.get("index"))
            if index not in local:
                continue
            bits = {name: {local[len(value) - 1 - position] for position, bit in enumerate(value)
                           if bit == "1" and len(value) - 1 - position in local}
                    for name, value in (("response", request.get("response", "")), ("foes", request.get("foes", "")))}
            rows[local[index]] = {**bits, "cont": request.get("cont")}
        requests[junction.get("id")] = rows
    return lanes, chains, requests


def _numbers(value: str) -> list[float] | None:
    try:
        return [float(token) for token in re.split(r"[ ,]+", value.strip()) if token]
    except ValueError:
        return None


def materialized_side_effects(parent_root, candidate_root, junctions: list[str] | set[str],
                              op_pairs: set[tuple[str, str]] | list = ()) -> dict:
    """What a materialization changes at the scope junctions beyond its declared targets, per retained road
    connection (``from lane -> to lane`` present on both nets, the ops' own pairs excluded): its internal-lane chain
    (ids, part lengths, total length, part speeds), the Hausdorff distance between the two chains' shapes, the shift
    of its internal-junction wait point (end of the first part, for a chain of two or more parts), every changed
    connection attribute but ``linkIndex`` and ``via`` (numeric ones with their largest absolute change), and its
    right-of-way (``response``, ``foes`` and ``cont``, as sets of connections; links of the op pairs reported apart).
    Model-relative geometry only: no evidence that either layout is right."""
    wanted, pairs = set(junctions), {tuple(pair) for pair in op_pairs}
    lanes_p, chains_p, requests_p = _path_records(parent_root)
    lanes_c, chains_c, requests_c = _path_records(candidate_root)
    rows = []
    for key in sorted(set(chains_p) & set(chains_c)):
        old, new = chains_p[key], chains_c[key]
        junction = _internal_junction(old["vias"][0])
        if junction not in wanted or key in pairs:
            continue
        parts_p, parts_c = [lanes_p[lane] for lane in old["vias"]], [lanes_c[lane] for lane in new["vias"]]
        shape_p = [point for part in parts_p for point in part["shape"]]
        shape_c = [point for part in parts_c for point in part["shape"]]
        wait = (round(math.dist(parts_p[0]["shape"][-1], parts_c[0]["shape"][-1]), 3)
                if len(parts_p) > 1 and len(parts_c) > 1 else None)
        attributes = {}
        for name in sorted(set(old["attrs"]) | set(new["attrs"]) - {"linkIndex", "via"}):
            before, after = old["attrs"].get(name), new["attrs"].get(name)
            if name in ("linkIndex", "via") or before == after:
                continue
            a, b = _numbers(before or ""), _numbers(after or "")
            numeric = a is not None and b is not None and len(a) == len(b) and bool(a)
            attributes[name] = {"parent": before, "candidate": after,
                                "max_abs_change": max(abs(x - y) for x, y in zip(a, b)) if numeric else None}
        right = {}
        for side in ("response", "foes"):
            before = requests_p.get(junction, {}).get(key, {}).get(side, set())
            after = requests_c.get(junction, {}).get(key, {}).get(side, set())
            right[side] = {"added": sorted(f"{a}>{b}" for a, b in after - before - pairs),
                           "removed": sorted(f"{a}>{b}" for a, b in before - after - pairs),
                           "op_pairs_added": sorted(f"{a}>{b}" for a, b in (after - before) & pairs),
                           "op_pairs_removed": sorted(f"{a}>{b}" for a, b in (before - after) & pairs)}
        cont = (requests_p.get(junction, {}).get(key, {}).get("cont"),
                requests_c.get(junction, {}).get(key, {}).get("cont"))
        row = {"connection": f"{key[0]}>{key[1]}", "junction": junction,
               "vias": {"parent": old["vias"], "candidate": new["vias"]},
               "part_lengths_m": {"parent": [part["length"] for part in parts_p],
                                  "candidate": [part["length"] for part in parts_c]},
               "length_change_m": round(sum(p["length"] for p in parts_c) - sum(p["length"] for p in parts_p), 3),
               "part_speeds_m_s": {"parent": [part["speed"] for part in parts_p],
                                   "candidate": [part["speed"] for part in parts_c]},
               "hausdorff_m": round(hausdorff(shape_p, shape_c), 3), "wait_point_shift_m": wait,
               "attribute_changes": attributes, "right_of_way": right,
               "cont": {"parent": cont[0], "candidate": cont[1]}}
        row["changed"] = bool(
            old["vias"] != new["vias"] or abs(row["length_change_m"]) > SIDE_EFFECT_TOLERANCE_M
            or row["hausdorff_m"] > SIDE_EFFECT_TOLERANCE_M or (wait or 0) > SIDE_EFFECT_TOLERANCE_M
            or row["part_speeds_m_s"]["parent"] != row["part_speeds_m_s"]["candidate"] or attributes
            or cont[0] != cont[1] or any(right[side][kind] for side in right for kind in ("added", "removed")))
        rows.append(row)
    def speed_change(row: Mapping) -> float | None:
        before, after = row["part_speeds_m_s"]["parent"], row["part_speeds_m_s"]["candidate"]
        return max(abs(a - b) for a, b in zip(before, after)) if len(before) == len(after) else None

    for row in rows:
        row["speed_change_m_s"] = speed_change(row)

    def listed(rows_: list, field: str, threshold: float) -> dict:
        found = sorted(((row[field], row["connection"]) for row in rows_ if (row[field] or 0) > threshold),
                       reverse=True)
        return {"count": len(found), "range": [found[-1][0], found[0][0]] if found else None,
                "connections": {name: value for value, name in found}}

    right_of_way = [row["connection"] for row in rows if row["cont"]["parent"] != row["cont"]["candidate"]
                    or any(row["right_of_way"][side][kind] for side in row["right_of_way"]
                           for kind in ("added", "removed"))]
    op_foes = sorted({name for row in rows for side in row["right_of_way"].values()
                      for name in side["op_pairs_added"] + side["op_pairs_removed"]})
    numeric = [change["max_abs_change"] for row in rows for change in row["attribute_changes"].values()
               if change["max_abs_change"] is not None]
    summary = {"reshaped_over_tolerance": listed(rows, "hausdorff_m", SIDE_EFFECT_TOLERANCE_M),
               "reshaped_over_0.5m": listed(rows, "hausdorff_m", 0.5),
               "wait_point_shift_over_0.5m": listed(rows, "wait_point_shift_m", 0.5),
               "length_change_over_0.1m": {"count": sum(abs(r["length_change_m"]) > 0.1 for r in rows),
                                           "connections": {r["connection"]: r["length_change_m"] for r in rows
                                                           if abs(r["length_change_m"]) > 0.1}},
               "speed_change_over_0.01m_s": listed(rows, "speed_change_m_s", 0.01),
               "renamed_chains": sum(r["vias"]["parent"] != r["vias"]["candidate"] for r in rows),
               "attribute_changes": {"names": sorted({name for r in rows for name in r["attribute_changes"]}),
                                     "connections": sum(bool(r["attribute_changes"]) for r in rows),
                                     "max_numeric_change": max(numeric, default=0.0),
                                     "non_numeric": sorted({name for r in rows for name, change in
                                                            r["attribute_changes"].items()
                                                            if change["max_abs_change"] is None})},
               "right_of_way_changed_beyond_op_pairs": right_of_way,
               "right_of_way_op_pairs": op_foes}
    return {"rule": "retained road connections at the scope junctions (op pairs excluded): internal-lane chains, "
                    "lengths, speeds, Hausdorff shape distance, wait-point shift, connection attributes and "
                    "right-of-way, parent against candidate",
            "tolerance_m": SIDE_EFFECT_TOLERANCE_M, "junctions": sorted(wanted), "retained": len(rows),
            "changed": sum(row["changed"] for row in rows), "summary": summary,
            "connections": [row for row in rows if row["changed"]],
            "claim": "Model-relative geometry of the materialized candidate; no field claim."}
