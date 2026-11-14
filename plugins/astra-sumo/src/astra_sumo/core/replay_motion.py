"""Record a window of the runs of a load replay and draw them side by side.

A load replay (load_replay.run_load_replay) keeps the routes of every network but no positions. This step runs the
same routes again, with the seed and options of the replay, up to the end of the wanted window, and has SUMO write
the positions inside the window, the signal states there and the summary. It then writes the motion-review request
with the hash of every file and renders it (motion_review.render_motion_review). The replay folders are only read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .load_replay import SCHEMA as REPLAY_SCHEMA
from .motion_review import _MAX_FRAMES, _read_net, render_motion_review
from .sumo_commands import discover_binaries

SCHEMA = "astra.replay-motion/v1"
_WINDOW = "fcd_window"


def _location(net_file: Path) -> dict[str, str]:
    with net_file.open("rb") as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            if element.tag == "location":
                return dict(element.attrib)
            if element.tag in {"edge", "junction"}:
                break
    raise ValueError(f"The network has no location element: {net_file}")


def _window_xy(location: Mapping[str, str], bbox: str) -> tuple[float, float, float, float]:
    """The rectangle in network xy around the four corners of a W,S,E,N WGS84 box."""
    from pyproj import CRS, Transformer

    from .osm_network import parse_bbox

    box = parse_bbox(bbox)
    projection = location.get("projParameter", "").strip()
    if not projection or projection == "!":
        raise ValueError("The network declares no geographic projection.")
    offset_x, offset_y = (float(value) for value in location.get("netOffset", "0,0").split(","))
    transformer = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_user_input(projection), always_xy=True)
    xs, ys = transformer.transform([box.west, box.east, box.east, box.west], [box.south, box.south, box.north, box.north])
    return (round(min(xs) + offset_x, 2), round(min(ys) + offset_y, 2), round(max(xs) + offset_x, 2), round(max(ys) + offset_y, 2))


def _artifact(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": file_sha256(path)}


def record_replay_motion(*, replay_report: str | Path, output_dir: str | Path, bbox: str, begin_s: int, end_s: int,
                         networks: Iterable[str] | None = None, buildings: str | Path | None = None,
                         pixels_per_metre: float = 1.5, signals: bool = True, timeout_seconds: float = 1800.0,
                         binaries: Mapping[str, str | None] | None = None,
                         command_runner: Callable[..., Any] = run_command) -> dict[str, Any]:
    """Run one or two networks of a load replay again up to ``end_s`` and draw ``begin_s`` to ``end_s`` in ``bbox``.

    ``networks`` names the rows of the replay report to draw, at most two; without it the first two replayed
    networks are drawn. ``bbox`` is W,S,E,N in WGS84. ``output_dir`` must be new.
    """
    report_path = Path(replay_report).resolve(strict=True)
    replay = json.loads(report_path.read_text(encoding="utf-8"))
    if replay.get("schema") != REPLAY_SCHEMA:
        raise ValueError(f"The report is not a {REPLAY_SCHEMA} file.")
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("Choose a new output directory.")
    if not 0 <= begin_s <= end_s or end_s - begin_s >= _MAX_FRAMES:
        raise ValueError(f"Choose begin <= end, at most {_MAX_FRAMES - 1} seconds apart.")
    rows = {row["name"]: row for row in replay.get("networks", []) if row.get("status") == "measured"}
    names = list(networks) if networks else list(rows)[:2]
    if not 1 <= len(names) <= 2 or len(set(names)) != len(names) or any(name not in rows for name in names):
        raise ValueError(f"Name one or two different replayed networks of the report: {', '.join(rows) or 'none'}.")
    buildings_path = Path(buildings).resolve(strict=True) if buildings else None
    result: dict[str, Any] = {"schema": SCHEMA, "status": "blocked", "replay_report": _artifact(report_path),
                              "output_dir": str(destination)}
    sumo = (binaries if binaries is not None else discover_binaries()).get("sumo")
    if not sumo:
        return {**result, "reason": "sumo_binary_not_found"}
    panels, window = [], None
    for index, name in enumerate(names):
        row = rows[name]
        net_file = Path(row["net"]["path"])
        if file_sha256(net_file) != row["net"]["sha256"]:
            raise ValueError(f"The network of {name} changed after the replay.")
        run = json.loads(Path(row["run_report"]).read_text(encoding="utf-8"))
        final = run["final_attempt"]
        if end_s >= int(final["end"]):
            raise ValueError(f"The replay of {name} ended at {final['end']} s; choose an earlier end.")
        route_file = Path(run["route_file"]).resolve(strict=True)
        window = _window_xy(_location(net_file), bbox)
        heads = sorted(_read_net(net_file, window)["heads"]) if signals else []
        folder = destination / f"panel{index}"
        x0, y0, x1, y1 = window
        write_text_atomic(folder / "window.add.xml", "<additional>\n"
                          f'  <poly id="{_WINDOW}" color="255,0,0" fill="false" layer="-1" '
                          f'shape="{x0},{y0} {x1},{y0} {x1},{y1} {x0},{y1} {x0},{y0}"/>\n'
                          + "".join(f'  <timedEvent type="SaveTLSStates" source="{tl_id}" dest="tls_states.xml"/>\n'
                                    for tl_id in heads) + "</additional>\n")
        # The options of the replay's own run (routeability_audit._run_attempt with diagnostic outputs), ending
        # after the window instead of at the replay's end.
        command = [str(sumo), "-n", str(net_file), "-r", str(route_file), "-a", "window.add.xml", "--begin", "0",
                   "--end", str(end_s + 1), "--seed", str(run["seed"]), "--quit-on-end", "--no-step-log", "true",
                   "--collision.check-junctions", "true", "--time-to-teleport", str(final["time_to_teleport_s"]),
                   "--step-length", "1", "--collision.action", "teleport", "--summary-output", "summary.xml",
                   "--fcd-output", "fcd.xml", "--fcd-output.filter-shapes", _WINDOW,
                   "--fcd-output.attributes", "id,x,y,angle,type,speed", "--device.fcd.begin", str(begin_s),
                   "--device.fcd.period", "1"]
        done = command_runner(command, cwd=folder, timeout_seconds=timeout_seconds)
        done = done.to_dict() if hasattr(done, "to_dict") else dict(done)
        if done.get("returncode") != 0 or not (folder / "fcd.xml").is_file():
            return {**result, "reason": "sumo_run_failed", "network": name, "returncode": done.get("returncode"),
                    "stderr_tail": str(done.get("stderr") or done.get("error") or "")[-800:]}
        panel = {"title": f"{name}, same {row['trip_count']} trips (seed {run['seed']})"[:80],
                 "net": row["net"], "fcd": _artifact(folder / "fcd.xml"), "summary": _artifact(folder / "summary.xml")}
        if heads and (folder / "tls_states.xml").is_file():
            panel["tls_states"] = _artifact(folder / "tls_states.xml")
        panels.append(panel)
    request = {"schema": "astra.motion-review-request/v1", "panels": panels, "bbox": list(window), "begin_s": begin_s,
               "end_s": end_s, "pixels_per_metre": pixels_per_metre}
    if buildings_path is not None:
        request["buildings"] = _artifact(buildings_path)
    request_file = destination / "motion.request.json"
    write_json_atomic(request_file, request)
    rendered = render_motion_review(request_file=request_file, output_dir=destination / "motion")
    return {**result, "status": "measured", "claim_boundary": rendered["claim_boundary"], "networks": names,
            "window_xy": list(window), "begin_s": begin_s, "end_s": end_s, "request_file": _artifact(request_file),
            "motion": {key: rendered[key] for key in ("view", "panels", "artifacts", "output_dir")}}
