"""Per-run fragility metrics from a judge's native outputs, and the exact tests of the M4 fragility check.

Ported from the 106-run M2 site census (explore evidence on where the supplied model fails). One run's native
directory is read; nothing is executed. Every event is anchored on a junction (an internal lane's own junction, a
road lane's to-junction) and attributed to a declared site (``SITES``, the census clusters S01..S04) or, outside them,
to its junction cluster (junctions joined by road edges shorter than ``CLUSTER_M``) for the new-site scan.

- Standstill episodes: from ``queues.xml`` (period 1 s), the longest continuous halt on each lane; every local peak
  of at least ``STANDSTILL_S`` is one episode (a drop of more than ``EPISODE_DROP_S`` or the lane leaving the output
  ends it; one still present at the last step is ``open_at_end``).
- Wrong lane: an episode on road lane L is a wrong-lane standstill when every vehicle that covers it on L's edge
  (``vehicle-routes.xml`` exit times: on the edge from at most ``COVER_SLACK_S`` after the halt began until at least
  ``COVER_SLACK_S`` before its peak) has a next route edge that L cannot reach through a usable connection. The
  halting vehicle is one of the covering vehicles, so the rule is exact up to the slack; a mix of fitting and
  non-fitting next edges is ``ambiguous`` and never counted. A connection is usable unless its controller's executed
  program (the control file's program, else the net's) never shows it green, stop or off: a link that is never green
  is not a way out. An episode open at the end of a stalled run reads the vehicles on L from the all-stopped state.
- Gridlock: the stall monitor's ``all_stopped``; its root is the stopped vehicle with the longest waiting time in the
  saved state, with its lane, site and lane status.
- Collisions (``collisions.xml``), teleports and emergency brakings (``sumo-errors.log``) by lane and site.

Primary metrics (pre-registered for the fragility check) are arm-neutral: their wrong-lane predicate reads the
**parent's** usable links on every arm (``run_metrics(parent=)``), so an edit that adds or removes a connection cannot
make an event impossible by definition. ``S01_wrong_lane_episodes``: wrong-lane standstill episodes on the lanes of
edge 603103444 (the 266197536 approach), judged by the parent's links (a vehicle on 603103444_1 bound for 73474895
counts on every net). ``S03_trap_episodes``: wrong-lane standstills on 186821027#0_4 whose covering vehicles' next
edges are all reachable from that lane only through a link the parent's executed program never greens (the LSA535
link 5 target 186821036#1). ``S03_link7_eb``: emergency brakings on the internal lanes of the movement 186821027#0_5 ->
186821036#1_0 at LSA535_part0 (parent link 7, found by its lanes on every net); ``S03_metric = S03_trap_episodes +
S03_link7_eb``. The run's own links give the secondary statuses (``secondary``).

The tests compare a candidate arm with a reference arm of other draws (unpaired): an exact one-sided permutation test
(Wilcoxon-Mann-Whitney with midranks, or the difference of means) whose null distribution is counted exactly over all
C(N, n) relabelings by dynamic programming (conditional on ties). ``primary_tests``, ``new_failure_sites`` and
``decide`` apply the pre-registered rule of the fragility check (``PRIMARY`` metrics per candidate, Holm across a
candidate's metrics, the request comparability, the static-gate precondition, A2 health and "no calibration
regression detected" from the M2 stage against each declared parent arm), ``stage_arms`` reads the two arms of that
stage from their two ledgers, and ``analyze`` composes all of it from the two ledgers and a frozen design. Explore
evidence over simulated runs under the supplied model: no field claim, no traffic-safety claim, no promotion
authority.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from fractions import Fraction
from pathlib import Path, PureWindowsPath

SCHEMA ="astra.twin-fragility-run/v1"
STANDSTILL_S = 60.0
LONG_STANDSTILL_S = 300.0
EPISODE_DROP_S = 1.5
COVER_SLACK_S = 1.5
CLUSTER_M = 15.0
OPEN_STATES = frozenset("GgsOo")
# The census sites (sites.json cluster_junctions, 106 M2 runs): junction clusters of the parent, frozen.
SITES = {
    "S01": ("1562406613", "1966837175", "244203524", "244203525", "244203534", "244203536", "244203542", "26084079",
            "266197525", "266197536", "568500922", "6390683113", "6390683114", "6390683115", "653216752", "703147136",
            "9844196552", "9844196555", "9844196560"),
    "S02": ("13870881", "1975693623", "315029332", "315029339"),
    "S03": ("563269601", "LSA535_part0"),
    "S04": ("1561539025", "1561539027", "259593942", "3479240441", "393721378", "512493772", "8913236635",
            "8913236647"),
}
S01_EDGE = "603103444"
S03_LANE = "186821027#0_4"
S03_LINK7 = ("186821027#0_5", "186821036#1_0")
S03_TLS = "LSA535_part0"
DEFINITIONS = {
    "standstill_episode": f"queues.xml local peak of queueing_time >= {STANDSTILL_S:g} s; a drop > {EPISODE_DROP_S:g} s "
                          "or the lane leaving the output ends it; open_at_end when present at the last step",
    "wrong_lane": f"every vehicle covering the episode on the lane's edge (enter <= start + {COVER_SLACK_S:g} s, exit >= "
                  f"peak - {COVER_SLACK_S:g} s) has a next route edge the lane cannot reach through a usable "
                  "connection (a controlled link is usable when the executed program shows it G, g, s, O or o in some "
                  "phase); a mix is ambiguous and never counted",
    "primary_basis": "the wrong-lane predicate of every primary metric reads the parent's net and executed control "
                     "on every arm (arm-neutral); the secondary statuses read the run's own links",
    "S01_wrong_lane_episodes": f"wrong-lane standstill episodes (open_at_end included) on the lanes of edge {S01_EDGE}, "
                               "judged by the parent's usable links",
    "S03_trap_episodes": f"wrong-lane standstill episodes (open_at_end included) on {S03_LANE}, judged by the parent's "
                         "usable links, whose covering vehicles' next edges are all reachable from the lane only "
                         "through a link the parent's executed program never opens (LSA535 link 5)",
    "S03_link7_eb": f"emergency brakings on the internal lanes of {S03_LINK7[0]} -> {S03_LINK7[1]} at {S03_TLS}",
    "S03_metric": "S03_trap_episodes + S03_link7_eb",
    "secondary": f"S01_wrong_lane_episodes_own_links and S03_wrong_lane_episodes_own_links: the same episodes judged "
                 f"by the run's own links (any next edge on {S03_LANE}); S01_standstills_ge60s: every episode on the "
                 f"lanes of {S01_EDGE}, whatever its status",
    "gridlock": "stall monitor all_stopped; root = the stopped vehicle with the longest waiting time in the saved state",
    "site": "declared site containing the anchor junction (internal lane: its junction; road lane: its to-junction), "
            f"else new:<smallest junction id of its cluster> (junctions joined by road edges < {CLUSTER_M:g} m)",
    "new_site_scan": f"collisions, teleports, gridlock roots and resolved standstills >= {LONG_STANDSTILL_S:g} s (not "
                     "open at the end, so the final jam of a gridlock counts through its root only) outside S01..S04",
}
CLAIM = ("Explore evidence: where the supplied SUMO model fails in one simulated run. No field timing, no traffic-safety "
         "claim, no promotion authority.")
_EB = re.compile(r"Vehicle '(?P<veh>[^']+)' performs emergency braking on lane '(?P<lane>[^']+)' with "
                 r"decel=(?P<decel>[\d.]+), wished=(?P<wished>[\d.]+), severity=(?P<sev>[\d.]+), time=(?P<t>[\d.]+)\.")
_TP_COLLISION = re.compile(r"Teleporting vehicle '(?P<veh>[^']+)'; (?P<kind>\w+) collision with vehicle "
                           r"'(?P<victim>[^']+)', lane='(?P<lane>[^']*)', gap=(?P<gap>[-\d.]+), time=(?P<t>[\d.]+), "
                           r"stage=(?P<stage>\w+)\.")
_TP_JAM = re.compile(r"Teleporting vehicle '(?P<veh>[^']+)'; waited too long \((?P<reason>[^)]+)\), "
                     r"lane='(?P<lane>[^']*)', time=(?P<t>[\d.]+)\.")
_Q_DATA = re.compile(r'<data timestep="([\d.]+)"')
_Q_LANE = re.compile(r'<lane id="([^"]+)" queueing_time="([\d.]+)"')


# ---- network context -----------------------------------------------------------------------------------------------

def junction_of_internal(lane: str) -> str | None:
    """``:J_3_1`` -> ``J`` (junction ids may contain underscores)."""
    if not lane.startswith(":"):
        return None
    body = lane[1:]
    parts = body.rsplit("_", 2)
    return parts[0] if len(parts) == 3 else body


class Network:
    """The road lanes, connections, junction clusters and executed-program link usability of one run's net."""

    def __init__(self, net: bytes, control: bytes | None = None):
        root = ET.fromstring(net)
        self.sha256 = {"net": hashlib.sha256(net).hexdigest(),
                       "control": hashlib.sha256(control).hexdigest() if control else None}
        self.lane_edge: dict[str, str] = {}
        self.edge_to: dict[str, str] = {}
        self.edge_lanes: dict[str, list[str]] = {}
        lengths: dict[str, float] = {}
        road = []
        for edge in root.iter("edge"):
            eid = edge.get("id")
            lanes = [lane.get("id") for lane in edge.findall("lane")]
            for lane in edge.findall("lane"):
                self.lane_edge[lane.get("id")] = eid
                lengths[lane.get("id")] = float(lane.get("length", "0"))
            if edge.get("function") in (None, "normal"):
                self.edge_to[eid] = edge.get("to")
                self.edge_lanes[eid] = lanes
                road.append((eid, edge.get("from"), edge.get("to"), max((lengths[x] for x in lanes), default=0.0)))
        self.junctions = sorted(j.get("id") for j in root.iter("junction") if j.get("type") != "internal")
        programs: dict[str, list[str]] = {}
        for logic in root.iter("tlLogic"):
            programs.setdefault(logic.get("id"), [p.get("state") for p in logic.findall("phase")])
        if control:
            for logic in ET.fromstring(control).iter("tlLogic"):
                programs[logic.get("id")] = [p.get("state") for p in logic.findall("phase")]
        self.never_open: set[tuple[str, int]] = set()
        self.out: dict[str, list[dict]] = defaultdict(list)
        self.via_next: dict[str, str] = {}
        connections = []
        for row in root.iter("connection"):
            connections.append(row)
            start = f"{row.get('from')}_{row.get('fromLane')}"
            end = f"{row.get('to')}_{row.get('toLane')}"
            if start.startswith(":"):
                # An internal lane continues on its connection's next internal lane, else on the road lane.
                self.via_next[start] = row.get("via") or end
                continue
            tl, index = row.get("tl"), row.get("linkIndex")
            usable = True
            if tl and index is not None:
                states = programs.get(tl) or []
                usable = any(int(index) < len(state) and state[int(index)] in OPEN_STATES for state in states)
                if not usable:
                    self.never_open.add((tl, int(index)))
            self.out[start].append({"to_edge": row.get("to"), "to_lane": end, "via": row.get("via"), "tl": tl,
                                    "link_index": None if index is None else int(index), "usable": usable})
        self._clusters(road)

    def _clusters(self, road: list[tuple[str, str, str, float]]) -> None:
        parent = {junction: junction for junction in self.junctions}

        def find(node: str) -> str:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        for _, start, end, length in road:
            if length < CLUSTER_M and start in parent and end in parent:
                parent[find(start)] = find(end)
        members: dict[str, list[str]] = defaultdict(list)
        for junction in self.junctions:
            members[find(junction)].append(junction)
        self.cluster = {junction: min(group) for group in members.values() for junction in group}

    def reach(self, lane: str) -> set[str]:
        """Next edges a road lane reaches through usable connections."""
        return {row["to_edge"] for row in self.out.get(lane, []) if row["usable"]}

    def closed_only(self, lane: str) -> set[str]:
        """Next edges a road lane connects to only through links the executed program never opens (a trap target:
        LSA535 link 5 on the parent)."""
        return {row["to_edge"] for row in self.out.get(lane, []) if not row["usable"]} - self.reach(lane)

    def anchor(self, lane: str) -> str | None:
        if lane.startswith(":"):
            return junction_of_internal(lane)
        return self.edge_to.get(self.lane_edge.get(lane, lane.rpartition("_")[0]))

    def site(self, lane: str) -> str | None:
        junction = self.anchor(lane)
        if junction is None:
            return None
        for name, members in SITES.items():
            if junction in members:
                return name
        return f"new:{self.cluster.get(junction, junction)}"

    def via_chain(self, from_lane: str, to_lane: str) -> list[str]:
        """The internal lanes of the connection ``from_lane -> to_lane`` (first part, then any later parts)."""
        first = next((row["via"] for row in self.out.get(from_lane, []) if row["to_lane"] == to_lane), None)
        chain, lane = [], first
        while lane and lane.startswith(":") and lane not in chain:
            chain.append(lane)
            lane = self.via_next.get(lane)
        return chain


def lane_status(lane: str, next_edges: Sequence[str | None], network: Network) -> str:
    """``wrong_lane`` when every next edge is unreachable from ``lane``, ``correct_lane`` when every one is reachable
    (or the route ends there), ``ambiguous`` for a mix, ``unmatched`` without covering vehicles, ``internal`` on an
    internal lane."""
    if lane.startswith(":"):
        return "internal"
    if not next_edges:
        return "unmatched"
    reach = network.reach(lane)
    fits = [edge is None or edge in reach for edge in next_edges]
    return "correct_lane" if all(fits) else "wrong_lane" if not any(fits) else "ambiguous"


# ---- native outputs ------------------------------------------------------------------------------------------------

def standstill_episodes(lines, minimum_s: float = STANDSTILL_S) -> list[dict]:
    """Episodes ``{lane, start, peak_at, halt_s, open_at_end}`` from queue-output lines (census rule)."""
    last: dict[str, tuple[float, float]] = {}
    seen: set[str] = set()
    out, time = [], 0.0

    def close(lane: str, value: float, at: float, open_at_end: bool = False) -> None:
        if value >= minimum_s:
            out.append({"lane": lane, "start": round(at - value, 1), "peak_at": at, "halt_s": round(value, 1),
                        "open_at_end": open_at_end})

    for line in lines:
        if "<data " in line:
            match = _Q_DATA.search(line)
            if match:
                for lane in [lane for lane in last if lane not in seen]:
                    close(lane, *last.pop(lane))
                seen = set()
                time = float(match[1])
            continue
        if "<lane " not in line:
            continue
        match = _Q_LANE.search(line)
        if not match:
            continue
        lane, value = match[1], float(match[2])
        seen.add(lane)
        previous = last.get(lane)
        if previous is not None and value < previous[0] - EPISODE_DROP_S:
            close(lane, *previous)
        last[lane] = (value, time)
    for lane, (value, at) in last.items():
        close(lane, value, at, open_at_end=True)
    return out


def parse_log(text: str) -> dict:
    """Emergency brakings and teleports of ``sumo-errors.log``."""
    brakings, teleports = [], []
    for line in text.splitlines():
        match = _EB.search(line)
        if match:
            brakings.append({"veh": match["veh"], "lane": match["lane"], "decel": float(match["decel"]),
                             "time": float(match["t"])})
            continue
        match = _TP_COLLISION.search(line)
        if match:
            teleports.append({"veh": match["veh"], "reason": f"collision:{match['kind']}", "lane": match["lane"],
                              "time": float(match["t"]), "stage": match["stage"]})
            continue
        match = _TP_JAM.search(line)
        if match:
            teleports.append({"veh": match["veh"], "reason": match["reason"], "lane": match["lane"],
                              "time": float(match["t"])})
    return {"emergency_brakings": brakings, "teleports": teleports}


def parse_collisions(data: bytes) -> list[dict]:
    if not data.strip():
        return []
    text = data.decode("utf-8")
    start = text.find("<collisions")
    if start < 0:
        return []
    return [{"time": float(row.get("time")), "lane": row.get("lane"), "type": row.get("type"),
             "collider": row.get("collider"), "victim": row.get("victim")}
            for row in ET.fromstring(text[start:]).iter("collision")]


def edge_stays(data: bytes, edges: set[str]) -> dict[str, list[tuple[float, float, str, str | None]]]:
    """``{edge: [(enter, exit, vehicle, next edge)]}`` from ``vehicle-routes.xml`` exit times, for ``edges``."""
    import io

    stays: dict[str, list] = defaultdict(list)
    for _, element in ET.iterparse(io.BytesIO(data), events=("end",)):
        if element.tag != "vehicle":
            continue
        routes = [route for route in element.findall("route") if route.get("exitTimes")]
        if routes:
            route_edges = routes[-1].get("edges", "").split()
            exits = [float(value) for value in routes[-1].get("exitTimes", "").split()]
            enter = float(element.get("depart"))
            for position, (edge, leave) in enumerate(zip(route_edges, exits)):
                if edge in edges:
                    following = route_edges[position + 1] if position + 1 < len(route_edges) else None
                    stays[edge].append((enter, leave, element.get("id"), following))
                enter = leave
        element.clear()
    return stays


def parse_state(data: bytes) -> dict:
    """The all-stopped saved state: vehicles per lane, and each vehicle's route, route index and waiting time."""
    text = gzip.decompress(data).decode("utf-8")
    root = ET.fromstring(text[text.find("<snapshot"):])
    routes = {row.get("id"): row.get("edges", "").split() for row in root.findall("route")}
    on_lane = {}
    for lane in root.findall("lane"):
        vehicles = lane.find("vehicles")
        on_lane[lane.get("id")] = vehicles.get("value", "").split() if vehicles is not None else []
    vehicles = {}
    for row in root.findall("vehicle"):
        state = row.get("state", "").split()
        vehicles[row.get("id")] = {"route": routes.get(row.get("route"), []),
                                   "route_index": int(state[2]) if len(state) > 2 else None,
                                   "waiting_s": int(state[4]) / 1000.0 if len(state) > 4 else None}
    return {"time": float(root.get("time")), "lanes": on_lane, "vehicles": vehicles}


def _next_edge(vehicle: Mapping) -> str | None:
    route, index = vehicle.get("route") or [], vehicle.get("route_index")
    return route[index + 1] if index is not None and index + 1 < len(route) else None


def _read(directory: Path, name: str) -> bytes:
    path = directory / name
    return path.read_bytes() if path.is_file() else b""


# ---- one run -------------------------------------------------------------------------------------------------------

def run_metrics(native_dir: Path, *, network: Network | None = None, parent: Network | None = None) -> dict:
    """The fragility record of one judge's native directory (``SCHEMA``); ``network`` reuses a parsed context of the
    same net and control bytes (checked by hash). ``parent`` is the parent's net and executed control: the primary
    metrics judge wrong lanes by its links on every arm (arm-neutral); without it the run's own net is the parent (a
    reference run)."""
    native = Path(native_dir)
    net, control = _read(native, "network.net.xml"), _read(native, "signal-programs.add.xml")
    network = network or Network(net, control or None)
    own = {"net": hashlib.sha256(net).hexdigest(), "control": hashlib.sha256(control).hexdigest() if control else None}
    if network.sha256 != own:
        raise ValueError(f"{native}: the given network context is not this run's net and control")
    parent = parent or network
    stall = json.loads(_read(native, "stall-monitor.json") or b"{}")
    log = parse_log(_read(native, "sumo-errors.log").decode("utf-8", errors="replace"))
    collisions = parse_collisions(_read(native, "collisions.xml"))
    with (native / "queues.xml").open(encoding="utf-8") as stream:
        episodes = standstill_episodes(stream)
    stays = edge_stays(_read(native, "vehicle-routes.xml"),
                       {network.lane_edge.get(row["lane"]) for row in episodes if not row["lane"].startswith(":")})
    state = parse_state(_read(native, "all-stopped-state.xml.gz")) if (native / "all-stopped-state.xml.gz").is_file() \
        else None
    for row in episodes:
        lane = row["lane"]
        if lane.startswith(":"):
            row["status"], row["next_edges"] = "internal", []
            continue
        if row["open_at_end"] and state is not None:
            vehicles = state["lanes"].get(lane, [])
            following = [_next_edge(state["vehicles"].get(vehicle, {})) for vehicle in vehicles]
            row["basis"] = "all_stopped_state"
        else:
            edge = network.lane_edge.get(lane)
            covering = [stay for stay in stays.get(edge, []) if stay[0] <= row["start"] + COVER_SLACK_S
                        and stay[1] >= row["peak_at"] - COVER_SLACK_S]
            following = [stay[3] for stay in covering]
            row["basis"] = "vehicle_routes"
        row["next_edges"] = sorted(set(edge for edge in following if edge), key=str) + (
            [None] if None in following else [])
        row["status"] = lane_status(lane, following, network)
        row["parent_status"] = lane_status(lane, following, parent)
        row["site"] = network.site(lane)
    for row in episodes:
        row.setdefault("site", network.site(row["lane"]))
    gridlock = None
    if stall.get("status") == "all_stopped" and state is not None:
        stopped = set((stall.get("stop") or {}).get("vehicles") or {}) or set(state["vehicles"])
        lane_of = {vehicle: lane for lane, vehicles in state["lanes"].items() for vehicle in vehicles}
        ranked = sorted((vehicle for vehicle in stopped if vehicle in state["vehicles"]),
                        key=lambda v: (-(state["vehicles"][v]["waiting_s"] or 0), v))
        if ranked:
            root = ranked[0]
            lane = lane_of.get(root, "")
            info = state["vehicles"][root]
            gridlock = {"root_vehicle": root, "root_lane": lane, "root_site": network.site(lane) if lane else None,
                        "root_next_edge": _next_edge(info),
                        "root_status": lane_status(lane, [_next_edge(info)], network) if lane else "unmatched",
                        "root_halted_since": round(state["time"] - (info["waiting_s"] or 0), 1),
                        "stopped": len(stopped), "stop_time_s": state["time"]}
    link7 = set(network.via_chain(*S03_LINK7))
    brakings = log["emergency_brakings"]
    for row in brakings:
        row["site"] = network.site(row["lane"])
    for row in (*collisions, *log["teleports"]):
        row["site"] = network.site(row["lane"]) if row.get("lane") else None
    wrong = [row for row in episodes if row["status"] == "wrong_lane"]
    judged = [row for row in episodes if row.get("parent_status") == "wrong_lane"]
    on_s01 = [row for row in episodes if network.lane_edge.get(row["lane"]) == S01_EDGE]
    on_s03 = [row for row in episodes if row["lane"] == S03_LANE]
    s01 = [row for row in judged if network.lane_edge.get(row["lane"]) == S01_EDGE]
    trap_edges = parent.closed_only(S03_LANE)
    trap = [row for row in judged if row["lane"] == S03_LANE and row["next_edges"]
            and all(edge in trap_edges for edge in row["next_edges"])]
    link7_eb = sum(row["lane"] in link7 for row in brakings)
    sites = sorted({row["site"] for row in (*episodes, *brakings, *collisions, *log["teleports"]) if row.get("site")}
                   | ({gridlock["root_site"]} if gridlock and gridlock["root_site"] else set()))
    by_site = {site: {"emergency_brakings": sum(row["site"] == site for row in brakings),
                      "collisions": sum(row["site"] == site for row in collisions),
                      "teleports": sum(row["site"] == site for row in log["teleports"]),
                      "standstills_ge60s": sum(row["site"] == site for row in episodes),
                      "standstills_ge300s": sum(row["site"] == site and row["halt_s"] >= LONG_STANDSTILL_S
                                                for row in episodes),
                      "wrong_lane_standstills": sum(row["site"] == site for row in wrong),
                      "gridlock_root": bool(gridlock and gridlock["root_site"] == site)} for site in sites}
    new_sites = sorted({row["site"] for row in (*collisions, *log["teleports"])
                        if str(row.get("site") or "").startswith("new:")}
                       | {row["site"] for row in episodes if row["halt_s"] >= LONG_STANDSTILL_S
                          and not row["open_at_end"] and str(row["site"]).startswith("new:")}
                       | ({gridlock["root_site"]} if gridlock and str(gridlock["root_site"]).startswith("new:")
                          else set()))
    return {"schema": SCHEMA, "definitions": DEFINITIONS, "claim": CLAIM,
            "inputs_sha256": {name: hashlib.sha256(_read(native, name)).hexdigest()
                              for name in ("network.net.xml", "signal-programs.add.xml", "queues.xml",
                                           "vehicle-routes.xml", "sumo-errors.log", "collisions.xml",
                                           "stall-monitor.json") if (native / name).is_file()},
            "stall_status": stall.get("status"), "simulation_time_s": stall.get("simulation_time_s"),
            "never_open_links": sorted([tl, index] for tl, index in network.never_open),
            "primary_basis": {"parent_net_sha256": parent.sha256["net"],
                              "parent_control_sha256": parent.sha256["control"],
                              "own_links": parent.sha256 == network.sha256, "S03_trap_edges": sorted(trap_edges)},
            "primary": {"S01_wrong_lane_episodes": len(s01),
                        "S01_max_wrong_lane_halt_s": max((row["halt_s"] for row in s01), default=0.0),
                        "S03_trap_episodes": len(trap), "S03_link7_eb": link7_eb,
                        "S03_metric": len(trap) + link7_eb},
            "secondary": {"S01_wrong_lane_episodes_own_links": sum(row["status"] == "wrong_lane" for row in on_s01),
                          "S01_standstills_ge60s": len(on_s01),
                          "S01_standstills_by_status": dict(Counter(row["status"] for row in on_s01)),
                          "S03_wrong_lane_episodes_own_links": sum(row["lane"] == S03_LANE for row in wrong),
                          "S03_lane_episodes_by_parent_status": dict(Counter(
                              row["parent_status"] for row in on_s03)),
                          "S03_lane_max_halt_s": max((row["halt_s"] for row in on_s03), default=0.0),
                          "S03_trap_episode_rows": [{key: row.get(key) for key in ("start", "halt_s", "open_at_end",
                                                                                   "next_edges", "basis")}
                                                    for row in trap]},
            "s01_episodes": [{key: row.get(key) for key in ("lane", "start", "peak_at", "halt_s", "open_at_end",
                                                            "status", "parent_status", "next_edges", "basis")}
                             for row in on_s01],
            "gridlock": gridlock, "collisions": collisions, "teleports": log["teleports"],
            "emergency_brakings": {"count": len(brakings), "by_lane": dict(Counter(row["lane"] for row in brakings)),
                                   "by_site": dict(Counter(row["site"] for row in brakings))},
            "link7_lanes": sorted(link7),
            "standstills": {"episodes_ge60s": len(episodes),
                            "status": dict(Counter(row["status"] for row in episodes)),
                            "wrong_lane": wrong,
                            "ge300s": [row for row in episodes if row["halt_s"] >= LONG_STANDSTILL_S]},
            "by_site": by_site, "new_sites": new_sites}


# ---- exact unpaired tests ------------------------------------------------------------------------------------------

def _subset_sums(values: Sequence[int], size: int) -> list[Counter]:
    """``table[k][s]`` = number of k-subsets of ``values`` (by position) with sum s, for k <= ``size``."""
    table = [Counter() for _ in range(size + 1)]
    table[0][0] = 1
    for value in values:
        for k in range(size, 0, -1):
            for total, count in list(table[k - 1].items()):
                table[k][total + value] += count
    return table


def _lower_tail(values: Sequence[int], size: int, observed: int) -> Fraction:
    """P(sum of a uniformly random ``size``-subset <= observed), exactly."""
    table = _subset_sums(values, size)[size]
    total = sum(table.values())
    return Fraction(sum(count for value, count in table.items() if value <= observed), total)


def midranks(values: Sequence[float]) -> list[Fraction]:
    ordered = sorted(range(len(values)), key=lambda index: values[index])
    ranks: list[Fraction] = [Fraction(0)] * len(values)
    position = 0
    while position < len(ordered):
        end = position
        while end + 1 < len(ordered) and values[ordered[end + 1]] == values[ordered[position]]:
            end += 1
        for index in ordered[position:end + 1]:
            ranks[index] = Fraction(position + end + 2, 2)
        position = end + 1
    return ranks


def exact_rank_test(candidate: Sequence[float], reference: Sequence[float]) -> dict:
    """One-sided exact Wilcoxon-Mann-Whitney (candidate lower): p = P(R <= r_obs) for the candidate's midrank sum R
    over all C(N, n) relabelings, counted exactly (conditional on the ties)."""
    pooled = [*candidate, *reference]
    ranks = midranks(pooled)
    doubled = [int(rank * 2) for rank in ranks]
    observed = sum(doubled[: len(candidate)])
    p = _lower_tail(doubled, len(candidate), observed)
    n, m = len(candidate), len(reference)
    u = Fraction(observed, 2) - Fraction(n * (n + 1), 2)
    return {"test": "exact one-sided Wilcoxon-Mann-Whitney (midranks, conditional on ties)", "n_candidate": n,
            "n_reference": m, "rank_sum": float(Fraction(observed, 2)), "U": float(u),
            "relabelings": math.comb(n + m, n), "p": float(p), "p_exact": f"{p.numerator}/{p.denominator}"}


def exact_mean_test(candidate: Sequence[int], reference: Sequence[int]) -> dict:
    """One-sided exact permutation test of the difference of means (candidate lower) for integer metrics: p = P(S <=
    s_obs) for the candidate's sum S over all C(N, n) relabelings, counted exactly."""
    if not all(float(value).is_integer() for value in (*candidate, *reference)):
        raise ValueError("the exact mean test counts integer metrics")
    pooled = [int(value) for value in (*candidate, *reference)]
    observed = sum(pooled[: len(candidate)])
    p = _lower_tail(pooled, len(candidate), observed)
    n, m = len(candidate), len(reference)
    return {"test": "exact one-sided permutation, difference of means", "n_candidate": n, "n_reference": m,
            "mean_candidate": observed / n if n else None, "mean_reference": sum(pooled[n:]) / m if m else None,
            "relabelings": math.comb(n + m, n), "p": float(p), "p_exact": f"{p.numerator}/{p.denominator}"}


def holm(pvalues: Mapping[str, float]) -> dict[str, float]:
    """Holm step-down adjusted p values."""
    ordered = sorted(pvalues.items(), key=lambda item: item[1])
    adjusted, running = {}, 0.0
    for position, (name, value) in enumerate(ordered):
        running = max(running, min(1.0, (len(ordered) - position) * value))
        adjusted[name] = running
    return adjusted


# ---- the pre-registered fragility check -----------------------------------------------------------------------------

ALPHA = 0.05
# Primary metric(s) per candidate; a candidate with two is judged with Holm's step-down at ALPHA.
PRIMARY = {"F1": ("S03_metric",), "F2": ("S01_wrong_lane_episodes",), "F3": ("S01_wrong_lane_episodes", "S03_metric")}
# Stage labels (``m2_stage``) under which calibration non-inferiority fails, and those that block the verdict.
NI_FAILURES = ("unhealthy", "guard", "row_regress", "init", "level_regress", "fine_regress", "worse")


def primary_tests(candidate: str, candidate_runs: Sequence[Mapping], reference_runs: Sequence[Mapping]) -> dict:
    """The rate-bearing tests of one candidate: per primary metric the exact one-sided Wilcoxon-Mann-Whitney test
    (decisive) and the exact difference-of-means test (reported), Holm-adjusted across the candidate's metrics."""
    names = PRIMARY[candidate]
    bases = {(run["primary_basis"]["parent_net_sha256"], run["primary_basis"]["parent_control_sha256"])
             for run in (*candidate_runs, *reference_runs) if run.get("primary_basis")}
    if len(bases) > 1:
        raise ValueError(f"the arms' primary metrics read different parent links: {sorted(bases)}")
    tests = {}
    for name in names:
        values = [int(run["primary"][name]) for run in candidate_runs]
        reference = [int(run["primary"][name]) for run in reference_runs]
        tests[name] = {"candidate": values, "reference_sum": sum(reference), "rank": exact_rank_test(values, reference),
                       "mean": exact_mean_test(values, reference)}
    adjusted = holm({name: tests[name]["rank"]["p"] for name in names})
    for name in names:
        tests[name]["p_adjusted"] = adjusted[name]
        tests[name]["rejected"] = adjusted[name] <= ALPHA
    return {"candidate": candidate, "metrics": list(names), "alpha": ALPHA,
            "adjustment": "holm" if len(names) > 1 else "none", "tests": tests,
            "rule": "one-sided exact Wilcoxon-Mann-Whitney (candidate lower), unpaired against the reference arm; "
                    "Holm across the candidate's primary metrics"}


def new_failure_sites(candidate_runs: Sequence[Mapping], reference_runs: Sequence[Mapping]) -> dict:
    """Sites where the candidate arm shows a collision, teleport or gridlock root that no reference run shows, and
    new-site-scan sites (outside S01..S04) no reference run shows."""
    def events(runs):
        found: dict[str, set[str]] = defaultdict(set)
        for run in runs:
            for row in run.get("collisions") or []:
                found["collision"].add(str(row.get("site")))
            for row in run.get("teleports") or []:
                found["teleport"].add(str(row.get("site")))
            if run.get("gridlock"):
                found["gridlock"].add(str(run["gridlock"].get("root_site")))
            found["new_site_scan"].update(run.get("new_sites") or [])
        return found

    candidate, reference = events(candidate_runs), events(reference_runs)
    new = {kind: sorted(candidate[kind] - reference[kind]) for kind in ("collision", "teleport", "gridlock",
                                                                        "new_site_scan")}
    return {"new": new, "any": any(new.values()), "candidate": {k: sorted(v) for k, v in candidate.items()},
            "reference": {k: sorted(v) for k, v in reference.items()}}


def decide(candidate: str, *, static_screening: str, tests: Mapping | None, stages: Mapping[str, Mapping | None],
           failure_sites: Mapping | None, runs: int, comparable: bool, draws: int = 16) -> dict:
    """The pre-registered label of one candidate's fragility check, in this order.

    ``blocked``: fewer than ``draws`` scored candidate runs, a request that fails the comparability check
    (``comparable`` false), no declared stage arm, or a stage against any declared parent arm that is missing or
    blocked (H1). ``unhealthy``: the stage's H2 (A2 relative health) fails against any arm. ``new_failure_site``: a
    collision, teleport, gridlock root or new-site-scan site no reference run shows. ``not_reduced``: a primary metric
    does not reject (Holm). ``fragility_reduced_calibration_regressed``: every primary metric rejects but the stage
    against some arm labels a regression (``NI_FAILURES``). ``fragility_reduced``: every primary metric rejects and no
    calibration regression is detected against any arm (M2 guards, within parent noise; the guards only reject, so
    this is no powered non-inferiority). Then ``diagnostic_only`` replaces any label but ``blocked`` when the static
    gate did not screen ``improved``; the rule's label is kept as ``label_if_static_gate_had_passed``."""
    labels = {name: (stage or {}).get("label") for name, stage in sorted(stages.items())}
    rejected = [name for name, row in (tests or {}).get("tests", {}).items() if row["rejected"]]
    regressed = sorted(name for name, label in labels.items() if label in NI_FAILURES)
    conditions = {"static_gate_improved": static_screening == "improved", "static_screening": static_screening,
                  "runs": runs, "comparable": bool(comparable), "stage_labels": labels,
                  "calibration_no_regression_detected": bool(labels) and not regressed and None not in labels.values()
                  and "blocked" not in labels.values(), "calibration_regressed_against": regressed,
                  "primary_rejected": rejected, "new_failure_sites": (failure_sites or {}).get("new")}
    if runs < draws or not comparable or not labels or tests is None or failure_sites is None or any(
            label in (None, "blocked") for label in labels.values()):
        label = "blocked"
    elif "unhealthy" in labels.values():
        label = "unhealthy"
    elif failure_sites.get("any"):
        label = "new_failure_site"
    elif len(rejected) != len(tests["tests"]):
        label = "not_reduced"
    elif regressed:
        label = "fragility_reduced_calibration_regressed"
    else:
        label = "fragility_reduced"
    if label != "blocked" and not conditions["static_gate_improved"]:
        conditions["label_if_static_gate_had_passed"] = label
        label = "diagnostic_only"
    return {"candidate": candidate, "label": label, "conditions": conditions,
            "claim": ("Explore only: a reduction of the pre-registered fragility metrics on simulated draws of the "
                      "candidate model against parent draws, and no calibration regression detected by the M2 guards "
                      "within parent noise (no powered non-inferiority); no field claim, no promotion authority "
                      "(dirty source, D1).")}


def stage_arms(candidate: tuple[Path, Mapping[str, str]], reference: tuple[Path, Mapping[str, str]]) -> dict:
    """``m2_stage.evaluate_stage`` inputs from two ledgers: each arm's ``{draw key: ledger id}`` read with
    ``panel.read_stage_draws`` from its own store; both must share the real counts and the goal."""
    from . import panel

    found = {name: panel.read_stage_draws(Path(path), {f"{name}:{key}": run for key, run in labels.items()})
             for name, (path, labels) in (("candidate", candidate), ("reference", reference))}
    if found["candidate"]["real"] != found["reference"]["real"]:
        raise ValueError("the two arms were scored against different real counts")
    goals = {(row["provenance"]["goal"], row["provenance"]["goal_sha256"]) for row in found.values()}
    if len(goals) != 1:
        raise ValueError(f"the two arms were judged against different goals: {sorted(goals)}")
    arms = {name: {label.split(":", 1)[1]: draw for label, draw in row["draws"].items()} for name, row in found.items()}
    return {"candidate": arms["candidate"], "reference": arms["reference"], "real": found["candidate"]["real"],
            "provenance": {name: row["provenance"] for name, row in found.items()}}


# Request fields a candidate draw may differ in from a reference draw of the parent: the candidate's files, the
# key-dependent demand, the key itself and the verifier commit (the executed modules and binaries must be equal).
COMPARABLE_DIFFERENCES = frozenset({"inputs_sha256.net_file", "inputs_sha256.signal_program_file",
                                    "inputs_sha256.route_file", "perturbation.key_sha256", "candidate",
                                    "runtime.verifier_commit"})


def _flatten(value: object, prefix: str = "") -> dict[str, object]:
    if isinstance(value, Mapping) and value:
        found: dict[str, object] = {}
        for key, item in value.items():
            found.update(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return found
    return {prefix: value}


def request_differences(candidate: Mapping, reference: Mapping) -> dict:
    """The request fields where a candidate draw differs from a reference draw; ``comparable`` when only
    ``COMPARABLE_DIFFERENCES`` differ (a ``candidate`` block counts as one field)."""
    left = _flatten({key: value for key, value in candidate.items() if key != "candidate"})
    right = _flatten({key: value for key, value in reference.items() if key != "candidate"})
    differing = sorted(key for key in set(left) | set(right) if left.get(key, ...) != right.get(key, ...))
    if ("candidate" in candidate) != ("candidate" in reference):
        differing.append("candidate")
    return {"differing": differing, "comparable": set(differing) <= COMPARABLE_DIFFERENCES,
            "allowed": sorted(COMPARABLE_DIFFERENCES)}


# ---- the analysis entry point: binds the decision rule before any candidate run ------------------------------------

ANALYSIS_SCHEMA = "astra.twin-fragility-analysis/v1"
ANALYSIS_CLAIM = ("Explore only: the pre-registered fragility check composed from the two ledgers and the frozen design "
                  "(both arms' metrics recomputed by this module with the parent's links); simulated draws under the "
                  "supplied model, no field claim, no traffic-safety claim, no promotion authority.")


def is_parent_draw(request: Mapping, parent: Mapping) -> bool:
    """The reference arm's rule: a draw of the parent's request up to the key (no scenario op, no candidate, the
    declared perturbation family and seed, the parent net and control), public or secret key."""
    perturbation = request.get("perturbation") or {}
    inputs = request.get("inputs_sha256") or {}
    return (request.get("scenario_op") is None and "candidate" not in request
            and perturbation.get("family") == parent["family"]
            and (request.get("arguments") or {}).get("seed") == parent["seed"]
            and inputs.get("net_file") == parent["net_sha256"]
            and inputs.get("signal_program_file") == parent["control_sha256"])


def _run_dir(store: Path, row: Mapping) -> Path:
    return Path(store) / "runs" / PureWindowsPath(row["run_dir"]).name


def select_runs(keys: Sequence[tuple[str, str]], spares: Sequence[tuple[str, str]], rows: Sequence[Mapping], *,
                pool: int) -> dict:
    """The runs one candidate arm uses (A2.2, as in M2). ``keys`` and ``spares`` are ordered ``(label,
    fingerprint)`` pairs; ``rows`` are the candidate's ledger key runs (``panel.ledger_key_runs``: ``id``,
    ``key_sha256``, ``outcome``) in ledger order. Walking the committed keys, a key with a scored run uses its first
    scored run; a key whose runs all crashed or timed out is replaced by the next spare while the shared ``pool``
    lasts; a key without a run or with an unscored run ends the walk. A run on any other key (a spare that replaced
    nothing, an unknown key) is an extra run: no other run is allowed."""
    by_key: dict[str, list[Mapping]] = defaultdict(list)
    for row in rows:
        by_key[row["key_sha256"]].append(row)
    queue, used, replaced, spent, stop = list(spares), [], [], [], None
    for key in keys:
        current = key
        while True:
            runs = by_key.get(current[1], [])
            scored = [row for row in runs if row["outcome"] == "scored"]
            if scored:
                used.append({"label": current[0], "key_sha256": current[1], "id": scored[0]["id"],
                             "request_sha256": scored[0].get("request_sha256")})
                break
            if runs and all(row["outcome"] == "crashed" for row in runs):
                replaced.append({"label": current[0], "runs": [row["id"] for row in runs]})
                if not queue or len(spent) >= pool:
                    stop = f"{current[0]}: crashed or timed out and no spare is left"
                    break
                current = queue.pop(0)
                spent.append(current[0])
                continue
            stop = f"{current[0]}: {'no run' if not runs else 'no complete score (unscored run)'}"
            break
        if stop:
            break
    allowed = {fingerprint for _, fingerprint in keys} | {fingerprint for label, fingerprint in spares
                                                          if label in spent}
    extra = [row["id"] for row in rows if row["key_sha256"] not in allowed]
    return {"used": used, "replaced": replaced, "spares_used": spent, "stopped": stop, "extra_runs": extra,
            "complete": stop is None and len(used) == len(keys)}


def _binomial_upper(count: int, draws: int, rate: float) -> float:
    """P(X >= count) for X ~ Bin(draws, rate)."""
    return min(1.0, sum(math.comb(draws, j) * rate ** j * (1 - rate) ** (draws - j) for j in range(count, draws + 1)))


def rare_events(candidate_runs: Sequence[Mapping], reference_runs: Sequence[Mapping]) -> dict:
    """Report only, never a test: per rare event the candidate runs that show it and the binomial upper tail at the
    reference arm's rate. The A2 health rule fails any candidate gridlock although the parent gridlocks too (S01,
    1/46), so a candidate gridlock count is read against this rate."""
    def has(run: Mapping, kind: str) -> bool:
        return bool(run.get("gridlock")) if kind == "gridlock" else bool(run.get(f"{kind}s"))

    found = {}
    for kind in ("gridlock", "collision", "teleport"):
        count = sum(has(run, kind) for run in candidate_runs)
        base = sum(has(run, kind) for run in reference_runs)
        rate = base / len(reference_runs) if reference_runs else 0.0
        found[kind] = {"candidate_runs": count, "candidate_draws": len(candidate_runs), "reference_runs": base,
                       "reference_draws": len(reference_runs), "reference_rate": rate,
                       "tail_at_reference_rate": (round(_binomial_upper(count, len(candidate_runs), rate), 6)
                                                  if rate > 0 else (1.0 if count == 0 else 0.0))}
    return {"events": found, "rule": "report only: P(X >= candidate count) for X ~ Bin(candidate draws, reference "
                                     "rate); never a test, never a label"}


def ni_stage(candidate_ledger: Path, candidate_labels: Mapping[str, str], reference_ledger: Path,
             reference_labels: Mapping[str, str], *, draws: int) -> dict:
    """``m2_stage.evaluate_stage`` (screen mode, the frozen K-draw gate) of the candidate arm against one declared
    parent arm, read from the two ledgers (``stage_arms``)."""
    from . import m2_stage

    arms = stage_arms((Path(candidate_ledger), candidate_labels), (Path(reference_ledger), reference_labels))
    verdict = m2_stage.evaluate_stage(arms["candidate"], arms["reference"], real=arms["real"],
                                      gate=m2_stage.frozen_gate(draws), mode="screen")
    return {"label": verdict["label"], "status": verdict["status"], "first_failure": verdict["first_failure"],
            "gate_sha256": verdict["gate_sha256"], "provenance": arms["provenance"],
            "verdict": m2_stage.to_json(verdict)}


def _candidate_identity(request: Mapping, row: Mapping, manifest: Mapping, parent: Mapping,
                        metrics: Mapping) -> list[str]:
    """Why one candidate run is not a run of this candidate on this parent (empty when it is)."""
    problems = []
    block = request.get("candidate") or {}
    for keyword, name, parent_key in (("net_file", "net", "net_sha256"),
                                      ("signal_program_file", "control", "control_sha256")):
        row_block = block.get(keyword) or {}
        if row_block.get("candidate_sha256") != manifest[name]["sha256"]:
            problems.append(f"{row['id']}: the request's {keyword} is not the candidate's")
        if row_block.get("parent_sha256") != parent[parent_key]:
            problems.append(f"{row['id']}: the request replaces another parent {keyword}")
    native = metrics.get("inputs_sha256") or {}
    if native.get("network.net.xml") != manifest["net"]["sha256"]:
        problems.append(f"{row['id']}: the native net is not the candidate's")
    if native.get("signal-programs.add.xml") != manifest["control"]["sha256"]:
        problems.append(f"{row['id']}: the native control is not the candidate's")
    if (row.get("candidate") or {}).get("id") != manifest["id"]:
        problems.append(f"{row['id']}: the ledger names another candidate")
    return problems


def analyze(design: Mapping) -> dict:
    """The pre-registered analysis of every declared candidate from the frozen ``design`` (its ``analysis`` block):
    the reference arm re-selected by its rule and equal to the frozen list, both arms' metrics recomputed here with
    the parent's links, each candidate's runs selected by its keys, spares and the shared pool (``select_runs``),
    every used request compared with every reference request (``request_differences``) and bound to the candidate's
    files, then ``primary_tests``, ``new_failure_sites``, the M2 stage against every declared parent arm
    (``ni_stage``), ``rare_events`` and ``decide``. Stores are only read."""
    from . import panel, perturb
    from .runner import digest

    plan = design.get("analysis", design)
    parent, draws = plan["parent"], int(plan["draws"])
    reference_ledger, candidate_ledger = Path(plan["reference_ledger"]), Path(plan["candidate_ledger"])
    reference_store, candidate_store = reference_ledger.parent, candidate_ledger.parent
    chosen = []
    for row in panel.read_ledger(reference_ledger).values():
        request = json.loads((_run_dir(reference_store, row) / "request.json").read_text(encoding="utf-8"))
        if is_parent_draw(request, parent):
            chosen.append((row, request))
    frozen = sorted((row["id"], row["request_sha256"]) for row in plan["reference_arm"])
    if not chosen or sorted((row["id"], row["request_sha256"]) for row, _ in chosen) != frozen:
        raise ValueError("the reference arm selected by its rule differs from the frozen reference arm")
    native = _run_dir(reference_store, chosen[0][0]) / "native"
    network = Network(_read(native, "network.net.xml"), _read(native, "signal-programs.add.xml") or None)
    if network.sha256 != {"net": parent["net_sha256"], "control": parent["control_sha256"]}:
        raise ValueError("the reference runs' native net and control are not the declared parent's")
    reference = {row["id"]: run_metrics(_run_dir(reference_store, row) / "native", network=network, parent=network)
                 for row, _ in chosen}
    reference_runs = list(reference.values())
    arms = plan["ni_arms"]
    if not arms or any(not set(ids) <= set(reference) or len(set(ids)) != draws for ids in arms.values()):
        raise ValueError(f"every declared stage arm needs {draws} distinct reference-arm runs")
    rows = panel.read_ledger_rows(candidate_ledger) if candidate_ledger.is_file() else []
    last = panel.read_ledger(candidate_ledger) if candidate_ledger.is_file() else {}
    key_runs = panel.ledger_key_runs(candidate_store, rows)
    owner = {row["id"]: (row.get("candidate") or {}).get("id") for row in rows}
    pool, results = int(plan["spare_pool"]), {}
    for name in plan["order"]:
        spec = plan["candidates"][name]
        manifest_bytes = Path(spec["manifest"]["path"]).read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != spec["manifest"]["sha256"]:
            raise ValueError(f"{name}: the candidate manifest changed since the design was frozen")
        manifest = json.loads(manifest_bytes)
        keys = [(label, perturb.DrawKey.public_key(label).fingerprint) for label in spec["keys"]]
        spares = [(label, perturb.DrawKey.public_key(label).fingerprint) for label in spec["spare_keys"]]
        mine = [row for row in key_runs if owner.get(row["id"]) == manifest["id"]]
        selection = select_runs(keys, spares, mine, pool=pool)
        pool -= len(selection["spares_used"])
        problems, differing, comparable, metrics = [], set(), True, []
        for entry in selection["used"]:
            row = last[entry["id"]]
            run_dir = _run_dir(candidate_store, row)
            request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
            if digest(request) != row["request_sha256"]:
                problems.append(f"{row['id']}: request.json does not hash to the ledger's request")
            found = run_metrics(run_dir / "native", parent=network)
            problems += _candidate_identity(request, row, manifest, parent, found)
            for _, reference_request in chosen:
                difference = request_differences(request, reference_request)
                differing.update(difference["differing"])
                comparable = comparable and difference["comparable"]
            metrics.append(found)
        complete = selection["complete"] and not selection["extra_runs"] and len(selection["used"]) == draws
        tests = primary_tests(name, metrics, reference_runs) if complete else None
        failure = new_failure_sites(metrics, reference_runs) if complete else None
        labels = {entry["label"]: entry["id"] for entry in selection["used"]}
        stages = {arm: (ni_stage(candidate_ledger, labels, reference_ledger, {run: run for run in ids}, draws=draws)
                        if complete else None) for arm, ids in sorted(arms.items())}
        decision = decide(name, static_screening=spec["static_screening"], tests=tests, stages=stages,
                          failure_sites=failure, runs=len(selection["used"]) if complete else 0,
                          comparable=comparable and not problems, draws=draws)
        decision["conditions"]["extra_runs"] = selection["extra_runs"]
        decision["conditions"]["selection_stopped"] = selection["stopped"]
        results[name] = {"manifest": spec["manifest"], "selection": selection,
                         "comparability": {"comparable": comparable and not problems, "problems": problems,
                                           "differing_union": sorted(differing),
                                           "allowed": sorted(COMPARABLE_DIFFERENCES)},
                         "tests": tests, "failure_sites": failure, "rare_events": rare_events(metrics, reference_runs),
                         "stages": {arm: None if stage is None else {key: stage[key] for key in
                                                                     ("label", "status", "first_failure",
                                                                      "gate_sha256", "provenance")}
                                    for arm, stage in stages.items()},
                         "stage_verdicts": {arm: None if stage is None else stage["verdict"]
                                            for arm, stage in stages.items()},
                         "primary_values": [run["primary"] for run in metrics], "decision": decision}
    return {"schema": ANALYSIS_SCHEMA, "parent": dict(parent), "draws": draws,
            "reference_arm": {"runs": len(chosen), "ids": [row["id"] for row, _ in chosen],
                              "primary_values": {run_id: run["primary"] for run_id, run in reference.items()}},
            "spare_pool": {"size": int(plan["spare_pool"]), "left": pool}, "candidates": results,
            "claim": ANALYSIS_CLAIM}
