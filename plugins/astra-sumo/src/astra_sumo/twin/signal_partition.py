"""sg_partition (M3): colour consistency of evidenced MAP signal groups, a derived CTL check (no new fact kind).

MAP gives one ``signalGroup`` per MAP connection (``core/digital_twin.py`` ``MapConnection``) and carries no conflict
matrix, so the only rule is that the SUMO links bound to one signal group show the same colour class in every phase.
The check runs only on evidenced bindings: a MAP connection whose ingress and egress lanes both have a locked
correspondence in the fact table (every fact of that MAP lane, ``<node>:lane:<MAP lane>:<kind>`` as the Hamburg
producer writes them, confirmed/confirmed and naming one SUMO lane), and exactly one SUMO connection between those
two SUMO lanes with ``tl`` and ``linkIndex``. The goal freezes the MAP connections at goal-create; the locks come from
the fact table each net is scored with, so a lane-count remap keeps physical lanes.

Colour classes: G and g are green (G against g encodes SUMO priority, not colour), y amber, r red, u red-amber; o, O
and s need a declared mapping, which M3 does not have, so a link in such a state is unscored for that phase.
Partition consistency is necessary, not sufficient, for correct signals. Without evidenced bindings the check is
``not_applicable``, never ``pass``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict

COLOUR = {"G": "green", "g": "green", "y": "amber", "r": "red", "u": "red-amber"}
UNSCORED_STATES = frozenset({"o", "O", "s"})
RULE = ("links bound to one evidenced MAP signal group show one colour class per phase of every program of their "
        "controller: {G, g} green, {y} amber, {r} red, {u} red-amber; o, O, s unscored without a declared mapping")
CLAIM = "necessary, not sufficient: MAP carries no conflict matrix and no SPaT event states"
LANE_FACT = re.compile(r"^(?P<node>[^:]+):lane:(?P<lane>[^:]+):")


def map_lane_locks(table) -> dict[tuple[str, str], str]:
    """``(MAP node, MAP lane) -> SUMO lane`` for every MAP lane whose correspondence is locked: each of its facts is
    confirmed/confirmed and every single-lane selector among them names the same SUMO lane."""
    lanes: dict[tuple[str, str], set[str]] = defaultdict(set)
    unlocked = set()
    for fact in table.facts:
        match = LANE_FACT.match(fact.id)
        if not match:
            continue
        key = (match["node"], match["lane"])
        if fact.mapping_status != "confirmed" or fact.mapping_confidence != "confirmed":
            unlocked.add(key)
        lane = fact.selector.get("lane_id") if isinstance(fact.selector, dict) else None
        if isinstance(lane, str):
            lanes[key].add(lane)
    return {key: next(iter(values)) for key, values in sorted(lanes.items())
            if len(values) == 1 and key not in unlocked}


def evidenced_bindings(connections, locks: dict[tuple[str, str], str]) -> list[dict]:
    """MAP connections with a signal group whose ingress and egress MAP lanes are both locked."""
    rows = []
    for connection in connections:
        group = str(connection["signal_group"] or "").strip()
        ingress = locks.get((connection["node_id"], connection["ingress_lane_id"]))
        egress = locks.get((connection["node_id"], connection["egress_lane_id"]))
        if group and ingress and egress:
            rows.append({"node_id": connection["node_id"], "connection_id": connection["connection_id"],
                         "signal_group": group, "map_lanes": [connection["ingress_lane_id"],
                                                              connection["egress_lane_id"]],
                         "lane_pair": [ingress, egress]})
    return rows


def _connections(root: ET.Element) -> dict[tuple[str, str], list[ET.Element]]:
    result: dict[tuple[str, str], list[ET.Element]] = defaultdict(list)
    for row in root.findall("connection"):
        result[(f"{row.get('from')}_{row.get('fromLane')}", f"{row.get('to')}_{row.get('toLane')}")].append(row)
    return result


def _programs(root: ET.Element) -> dict[str, list[tuple[str, list[str]]]]:
    result: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    for logic in root.findall("tlLogic"):
        result[logic.get("id")].append((logic.get("programID") or "", [phase.get("state") or ""
                                                                       for phase in logic.findall("phase")]))
    return result


def not_applicable(reason: str) -> dict:
    return {"status": "not_applicable", "rule": RULE, "claim": CLAIM, "evidenced_bindings": 0, "groups": [],
            "rows": {}, "reason": reason}


def check(root: ET.Element, bindings: list[dict]) -> dict:
    """The sg_partition result of one net: per evidenced group its links, phase comparisons, conflicts and status,
    plus one derived CTL row per compared group (``rows``, keyed ``sg:<node>:<group>``).

    A binding is evidenced only when its locked lane pair resolves to exactly one SUMO connection with ``tl`` and
    ``linkIndex``; without any, the check is ``not_applicable``. A group is compared in a phase only when two or more
    of its links show a scored colour class there (a short state string or o, O, s leaves a link unscored); a group
    compared in no phase is ``unscored`` with no row, and the check passes only when some group was compared and none
    is inconsistent."""
    if not bindings:
        return not_applicable("no MAP connection has locked ingress and egress lanes (no evidenced binding)")
    connections, programs = _connections(root), _programs(root)
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    unresolved = 0
    for binding in bindings:
        rows = [row for row in connections.get(tuple(binding["lane_pair"]), [])
                if row.get("tl") and (row.get("linkIndex") or "").isdigit()]
        if len(rows) != 1:
            unresolved += 1
            continue
        groups[(binding["node_id"], binding["signal_group"])].append(
            {**binding, "link": [rows[0].get("tl"), int(rows[0].get("linkIndex"))]})
    evidenced = len(bindings) - unresolved
    if not evidenced:
        return {**not_applicable("no locked MAP lane pair resolves to exactly one SUMO connection with tl and "
                                 "linkIndex (no evidenced binding)"), "locked_bindings": len(bindings),
                "unresolved_bindings": unresolved}
    result_groups, rows = [], {}
    for (node, group), links in sorted(groups.items()):
        identity = f"sg:{node}:{group}"
        controllers = sorted({row["link"][0] for row in links})
        record = {"id": identity, "map_node_id": node, "signal_group": group,
                  "links": [{key: row[key] for key in ("connection_id", "map_lanes", "lane_pair", "link")}
                            for row in links],
                  "controllers": controllers, "conflicts": [], "compared_phases": 0, "unscored_phases": 0}
        if len(links) < 2:
            record.update(status="unscored", reason="fewer than two evidenced links")
        elif len(controllers) != 1 or controllers[0] not in programs:
            record.update(status="unscored", reason="the links are not driven by one tlLogic of this net")
        else:
            for program, states in programs[controllers[0]]:
                for phase, state in enumerate(states):
                    letters = {f"{row['lane_pair'][0]}>{row['lane_pair'][1]}": (state[row["link"][1]]
                               if row["link"][1] < len(state) else None) for row in links}
                    if any(letter in UNSCORED_STATES or letter is None for letter in letters.values()):
                        record["unscored_phases"] += 1
                    scored = {pair: letter for pair, letter in letters.items() if letter in COLOUR}
                    if len(scored) < 2:
                        continue
                    record["compared_phases"] += 1
                    if len({COLOUR[letter] for letter in scored.values()}) > 1:
                        record["conflicts"].append({"program": program, "phase": phase,
                                                    "states": dict(sorted(scored.items()))})
            if not record["compared_phases"]:
                record.update(status="unscored", reason="no phase shows two or more links in a scored colour class")
            else:
                record["status"] = "inconsistent" if record["conflicts"] else "consistent"
                rows[identity] = {"id": identity, "kind": "sg_partition", "class": "CTL",
                                  "anchor": f"tls:{controllers[0]}",
                                  "status": "mismatched" if record["conflicts"] else "matched",
                                  "reason": (f"{len(record['conflicts'])} phase(s) show more than one colour class"
                                             if record["conflicts"] else None),
                                  "excess": None, "components": {}, "magnitude": len(record["conflicts"])}
        result_groups.append(record)
    status = ("inconsistent" if any(row["status"] == "inconsistent" for row in result_groups)
              else "pass" if rows else "unscored")
    return {"status": status, "rule": RULE, "claim": CLAIM, "evidenced_bindings": evidenced,
            "locked_bindings": len(bindings), "unresolved_bindings": unresolved,
            "groups": result_groups, "rows": rows,
            **({"reason": "no evidenced group has two links compared in any phase"} if not rows else {})}
