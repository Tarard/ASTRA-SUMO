"""Replay one frozen trip file on several networks and report, per network, what the same demand did.

A boundary load of ``astra network boundary-completeness`` at four vehicles per entry clears on networks that jam at
fifteen, and two builds of one area are only comparable under identical trips. This replay gives every network the
same trip file and the same SUMO seed, with teleporting off and the junction collision check on, and counts a
collider, its victim and a teleported vehicle as not arrived. Arrivals describe these runs: they do not prove
topology, demand, control or field correctness, and the trips are not observed demand.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic, write_text_atomic
from .boundary_completeness import _load_outcome, _xml_rows
from .candidate_contracts import file_sha256
from .osm_access import _permission_set
from .routeability_audit import run_routeability_audit
from .split_junctions import _elements

SCHEMA = "astra.load-replay/v1"
CLAIM_BOUNDARY = ("Every network got the same trips and SUMO seed. Arrivals, collisions and standing vehicles describe "
                  "these runs; they do not prove topology, demand, control or field correctness, and the trips are not "
                  "observed demand.")
_LIST_LIMIT = 12
_PREFIX = "load_replay"


def _read_trips(trip_file: Path) -> list[dict[str, Any]]:
    """The trips of the file in order, each with its id, departure second, first and last edge and its XML row."""
    trips = []
    for row in ET.parse(trip_file).getroot().findall("trip"):
        if row.get("id") and row.get("from") and row.get("to"):
            trips.append({"id": row.get("id"), "depart": int(float(row.get("depart", "0"))), "entry": row.get("from"),
                          "exit": row.get("to"), "row": row})
    return trips


def _passenger_edges(net_file: Path) -> set[str]:
    """The edges of a net with a lane open to passenger cars."""
    edges: set[str] = set()
    for element in _elements(net_file):
        if element.tag == "edge" and not element.get("function"):
            for lane in element.findall("lane"):
                attributes = lane.attrib if {"allow", "disallow"} & lane.attrib.keys() else element.attrib
                if "passenger" in _permission_set(attributes):
                    edges.add(element.get("id"))
                    break
    return edges


def _last_positions(path: Any) -> dict[str, tuple[str, str]]:
    steps = _xml_rows(path, "timestep")
    return {row.get("id"): (row.get("lane", ""), row.get("pos", "")) for row in steps[-1].findall("vehicle")} if steps else {}


def _junction(edge_id: str | None) -> str | None:
    """The junction of an internal edge id (``:node_7`` gives ``node``); None for a normal edge."""
    return edge_id[1:].rsplit("_", 1)[0] if edge_id and edge_id.startswith(":") else None


def _replay_one(name: str, net_file: Path, trips: list[dict[str, Any]], trip_file: Path, trip_sha256: str, folder: Path, *,
                seed: int, initial_end: int, max_end: int, timeout_seconds: float,
                runner: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "net": {"path": str(net_file), "sha256": file_sha256(net_file)}}
    known = _passenger_edges(net_file)
    replayed = [trip for trip in trips if trip["entry"] in known and trip["exit"] in known]
    row.update(trip_count=len(replayed), skipped_trip_count=len(trips) - len(replayed))
    if not replayed:
        return {**row, "status": "blocked", "reason": "no_trip_has_both_edges_in_this_network"}
    frozen, frozen_sha256 = trip_file, trip_sha256
    if len(replayed) < len(trips):
        # The trips whose first and last edge this network has, written to a new file; the input stays as it is.
        subset = ET.Element("routes")
        subset.extend(trip["row"] for trip in replayed)
        ET.indent(subset)
        frozen = folder / f"{_PREFIX}.replayable.trips.xml"
        write_text_atomic(frozen, ET.tostring(subset, encoding="unicode", xml_declaration=True))
        frozen_sha256 = file_sha256(frozen)
        row["replayed_trip_file"] = {"path": str(frozen), "sha256": frozen_sha256}
    report = runner(net_file=net_file, output_dir=folder / "run", prefix=_PREFIX, vehicle_count=len(replayed), seed=seed,
                    initial_end=initial_end, max_end=max_end, timeout_seconds=timeout_seconds, frozen_trip_file=frozen,
                    expected_frozen_trip_sha256=frozen_sha256, diagnostic_outputs=True)
    final = report.get("final_attempt") or {}
    if not final:
        return {**row, "status": "blocked", "reason": "replay_did_not_run",
                "detail": str(report.get("reason") or report.get("routeability_status") or report.get("status"))}
    outcome = _load_outcome(report, replayed)
    summary = (final.get("inspection") or {}).get("summary") or {}
    states = Counter(vehicle["state"] for vehicle in outcome["vehicles"])
    attempts = [attempt for attempt in report.get("attempts", []) if attempt.get("diagnostic_outputs")]
    standing = None
    if len(attempts) > 1:
        # A vehicle at the same lane position at the end of the last two attempts did not move in between.
        before, after = (_last_positions(attempt["diagnostic_outputs"].get("last_step_positions")) for attempt in attempts[-2:])
        standing = {"from_s": attempts[-2].get("end"), "to_s": attempts[-1].get("end"),
                    "vehicle_count": sum(1 for vehicle_id, place in before.items() if after.get(vehicle_id) == place),
                    "edges": Counter(place[0].rsplit("_", 1)[0] for vehicle_id, place in before.items()
                                     if after.get(vehicle_id) == place).most_common(_LIST_LIMIT)}
    return {**row, "status": "measured", "routeability_status": report.get("routeability_status"),
            "final_end_s": final.get("end"), "arrived_count": len(outcome["complete"]),
            "arrived_share": round(len(outcome["complete"]) / len(replayed), 4),
            "not_arrived_states": dict(sorted(states.items())),
            "collision_count": summary.get("collisions"), "teleport_count": summary.get("teleports"),
            "collision_junctions": Counter(
                junction for vehicle in outcome["vehicles"]
                if vehicle["state"] == "collision" and (junction := _junction(vehicle["last_edge"]))).most_common(_LIST_LIMIT),
            "unfinished_last_edges": Counter(vehicle["last_edge"] for vehicle in outcome["vehicles"]
                                             if vehicle["state"] == "unfinished").most_common(_LIST_LIMIT),
            "standing_vehicles": standing, "not_arrived_vehicles": outcome["vehicles"],
            "run_report": report.get("report_file")}


def run_load_replay(*, trip_file: str | Path, networks: Mapping[str, str | Path], output_dir: str | Path, seed: int = 42,
                    initial_end: int = 2700, max_end: int = 3600, timeout_seconds: float = 2400.0,
                    runner: Callable[..., dict[str, Any]] = run_routeability_audit) -> dict[str, Any]:
    """Replay ``trip_file`` on every network of ``networks`` (name: net file) and write ``load-replay.json``.

    A network that lacks the first or last edge of a trip replays the other trips; its row says how many it skipped
    and is compared with the first network only when both replayed the same trips. Each network writes below its own
    folder of ``output_dir``, which must be new.
    """
    source = Path(trip_file).resolve(strict=True)
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("Choose a new replay output directory.")
    if not networks:
        raise ValueError("Name at least one network.")
    nets = {name: Path(path).resolve(strict=True) for name, path in networks.items()}
    if any(not name or Path(name).name != name or name in {".", ".."} for name in nets):
        raise ValueError("A network name must be a single file-name component.")
    trip_sha256 = file_sha256(source)
    trips = _read_trips(source)
    report: dict[str, Any] = {"schema": SCHEMA, "status": "blocked", "claim_boundary": CLAIM_BOUNDARY,
                              "trip_file": {"path": str(source), "sha256": trip_sha256}, "trip_count": len(trips),
                              "seed": seed, "initial_end_s": initial_end, "max_end_s": max_end, "networks": []}
    destination.mkdir(parents=True)
    report_file = destination / "load-replay.json"
    if not trips:
        report["reason"] = "trip_file_has_no_trips"
    else:
        for name, net_file in nets.items():
            report["networks"].append(_replay_one(
                name, net_file, trips, source, trip_sha256, destination / name, seed=seed, initial_end=initial_end,
                max_end=max_end, timeout_seconds=timeout_seconds, runner=runner))
        measured = [row for row in report["networks"] if row["status"] == "measured"]
        if measured:
            report["status"] = "measured"
            reference = report["networks"][0]
            report["reference"] = reference["name"]
            for row in measured:
                same = reference["status"] == "measured" and row["trip_count"] == reference["trip_count"] == len(trips)
                row["arrived_share_minus_reference"] = (
                    round(row["arrived_share"] - reference["arrived_share"], 4) if same else None)
        else:
            report["reason"] = "no_network_was_replayed"
    if file_sha256(source) != trip_sha256 or any(file_sha256(path) != row["net"]["sha256"]
                                                 for path, row in zip(nets.values(), report["networks"])):
        report.update(status="blocked", reason="an_input_changed_during_the_replay")
    write_json_atomic(report_file, report)
    return {**{key: value for key, value in report.items() if key != "networks"},
            "networks": [{key: value for key, value in row.items() if key != "not_arrived_vehicles"}
                         for row in report["networks"]],
            "report_file": str(report_file), "report_sha256": file_sha256(report_file), "output_dir": str(destination)}
