from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pyproj import Transformer
import pytest

from astra_sumo.cli import main
from astra_sumo.core.replay_motion import record_replay_motion

UTM = "+proj=utm +zone=33 +ellps=WGS84 +datum=WGS84 +units=m +no_defs"
NET = f"""<net>
  <location netOffset="-411000.00,-5655000.00" convBoundary="0,0,200,100" origBoundary="0,0,1,1" projParameter="{UTM}"/>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" speed="13.9" length="90" width="3.2" shape="0,50 90,50"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" speed="13.9" length="90" width="3.2" shape="110,50 200,50"/></edge>
  <tlLogic id="j" type="static" programID="0" offset="0"><phase duration="5" state="G"/><phase duration="5" state="r"/></tlLogic>
  <junction id="j" type="traffic_light" x="100" y="50" incLanes="in_0" intLanes="" shape="90,45 110,45 110,55 90,55"/>
  <connection from="in" to="out" fromLane="0" toLane="0" tl="j" linkIndex="0" dir="s" state="O"/>
</net>"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bbox() -> str:
    """The WGS84 box around network xy 20..180, 20..80."""
    to_wgs = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    west, south = to_wgs.transform(411020.0, 5655020.0)
    east, north = to_wgs.transform(411180.0, 5655080.0)
    return f"{west:.7f},{south:.7f},{east:.7f},{north:.7f}"


def _replay(tmp_path: Path, names: tuple[str, ...] = ("plain", "astra")) -> Path:
    """A load-replay report over finished runs: per network a net, a route file and the run report."""
    rows = []
    for name in names:
        folder = tmp_path / "replay" / name / "run"
        folder.mkdir(parents=True)
        net = tmp_path / f"{name}.net.xml"
        net.write_text(NET, encoding="utf-8")
        routes = folder / "load_replay.rou.xml"
        routes.write_text('<routes><vehicle id="v" depart="0"><route edges="in out"/></vehicle></routes>', encoding="utf-8")
        run = folder / "load_replay_routeability_audit.json"
        run.write_text(json.dumps({"route_file": str(routes), "seed": 7,
                                   "final_attempt": {"end": 3600, "time_to_teleport_s": 3600}}), encoding="utf-8")
        rows.append({"name": name, "status": "measured", "trip_count": 1, "net": {"path": str(net), "sha256": _sha(net)},
                     "run_report": str(run)})
    report = tmp_path / "replay" / "load-replay.json"
    report.write_text(json.dumps({"schema": "astra.load-replay/v1", "status": "measured", "networks": rows}), encoding="utf-8")
    return report


def _sumo(calls: list[list[str]], *, returncode: int = 0):
    """A stand-in for SUMO that writes the outputs the command names for the seconds it asks for."""

    def run(command: list[str], *, cwd: Path, timeout_seconds: float) -> dict:
        calls.append(command)
        option = {command[index]: command[index + 1] for index in range(1, len(command) - 1) if command[index].startswith("-")}
        if returncode == 0:
            seconds = range(int(option["--device.fcd.begin"]), int(option["--end"]))
            (cwd / option["--fcd-output"]).write_text("<fcd-export>" + "".join(
                f'<timestep time="{second}.00"><vehicle id="v" x="{40 + second % 50}" y="50" angle="90" type="car" speed="5"/>'
                "</timestep>" for second in seconds) + "</fcd-export>", encoding="utf-8")
            (cwd / option["--summary-output"]).write_text("<summary>" + "".join(
                f'<step time="{second}.00" running="1" halting="0" arrived="0" collisions="0" teleports="0"/>'
                for second in seconds) + "</summary>", encoding="utf-8")
            if "SaveTLSStates" in (cwd / option["-a"]).read_text(encoding="utf-8"):
                (cwd / "tls_states.xml").write_text("<tlsStates>" + "".join(
                    f'<tlsState time="{second}.00" id="j" programID="0" phase="0" state="G"/>' for second in seconds)
                    + "</tlsStates>", encoding="utf-8")
        return {"returncode": returncode, "stderr": "Error: no such route" if returncode else ""}

    return run


def test_replay_motion_reruns_the_replay_routes_for_the_window_and_renders_both_networks(tmp_path: Path) -> None:
    report = _replay(tmp_path)
    before = {path: path.read_bytes() for path in (tmp_path / "replay").rglob("*") if path.is_file()}
    calls: list[list[str]] = []
    result = record_replay_motion(replay_report=report, output_dir=tmp_path / "motion", bbox=_bbox(), begin_s=10, end_s=13,
                                  binaries={"sumo": "sumo"}, command_runner=_sumo(calls))

    assert (result["status"], result["networks"]) == ("measured", ["plain", "astra"])
    # The window is the xy rectangle around the box's corners; UTM is turned against longitude and latitude, so it
    # is a few metres larger than xy 20..180, 20..80. Each run gets the routes and seed of the replay.
    x0, y0, x1, y1 = result["window_xy"]
    assert 15 < x0 <= 20.01 and 15 < y0 <= 20.01 and 179.99 <= x1 < 185 and 79.99 <= y1 < 85
    for command, name in zip(calls, ("plain", "astra")):
        option = {command[index]: command[index + 1] for index in range(1, len(command) - 1)}
        assert Path(option["-r"]) == tmp_path / "replay" / name / "run" / "load_replay.rou.xml"
        assert (option["--seed"], option["--end"], option["--device.fcd.begin"], option["--time-to-teleport"]) == (
            "7", "14", "10", "3600")
        assert option["--fcd-output.filter-shapes"] == "fcd_window"
    request = json.loads(Path(result["request_file"]["path"]).read_text(encoding="utf-8"))
    assert [panel["title"] for panel in request["panels"]] == ["plain, same 1 trips (seed 7)", "astra, same 1 trips (seed 7)"]
    assert all(_sha(Path(panel[key]["path"])) == panel[key]["sha256"] for panel in request["panels"]
               for key in ("net", "fcd", "summary", "tls_states"))
    assert result["motion"]["view"]["frame_count"] == 4 and (tmp_path / "motion" / "motion" / "motion.gif").is_file()
    assert result["motion"]["panels"][0]["signals_in_view"] == 1
    # The replay folders are read, not written.
    assert {path: path.read_bytes() for path in (tmp_path / "replay").rglob("*") if path.is_file()} == before


def test_replay_motion_draws_one_named_network_without_signals_and_refuses_bad_requests(tmp_path: Path) -> None:
    report = _replay(tmp_path)
    calls: list[list[str]] = []
    result = record_replay_motion(replay_report=report, output_dir=tmp_path / "one", bbox=_bbox(), begin_s=10, end_s=11,
                                  networks=["astra"], signals=False, binaries={"sumo": "sumo"}, command_runner=_sumo(calls))
    assert (result["networks"], len(calls), result["motion"]["panels"][0]["signals_in_view"]) == (["astra"], 1, 0)

    with pytest.raises(ValueError, match="replayed networks of the report: plain, astra"):
        record_replay_motion(replay_report=report, output_dir=tmp_path / "x", bbox=_bbox(), begin_s=10, end_s=11,
                             networks=["missing"], binaries={"sumo": "sumo"}, command_runner=_sumo(calls))
    with pytest.raises(ValueError, match="ended at 3600 s"):
        record_replay_motion(replay_report=report, output_dir=tmp_path / "x", bbox=_bbox(), begin_s=3590, end_s=3600,
                             binaries={"sumo": "sumo"}, command_runner=_sumo(calls))
    (tmp_path / "astra.net.xml").write_text(NET + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after the replay"):
        record_replay_motion(replay_report=report, output_dir=tmp_path / "x", bbox=_bbox(), begin_s=10, end_s=11,
                             networks=["astra"], binaries={"sumo": "sumo"}, command_runner=_sumo(calls))


def test_replay_motion_is_blocked_without_sumo_or_when_the_run_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    report = _replay(tmp_path)
    failed = record_replay_motion(replay_report=report, output_dir=tmp_path / "failed", bbox=_bbox(), begin_s=10, end_s=11,
                                  binaries={"sumo": "sumo"}, command_runner=_sumo([], returncode=1))
    assert (failed["status"], failed["reason"], failed["network"]) == ("blocked", "sumo_run_failed", "plain")
    assert "no such route" in failed["stderr_tail"]

    monkeypatch.setattr("astra_sumo.core.replay_motion.discover_binaries", lambda: {"sumo": None})
    code = main(["review", "replay-motion", str(report), str(tmp_path / "cli"), "--bbox", _bbox(), "--begin", "10",
                 "--end", "11", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 3 and (payload["status"], payload["reason"]) == ("blocked", "sumo_binary_not_found")
    assert not (tmp_path / "cli").exists()
