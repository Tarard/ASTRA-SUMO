from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from torii_sumo.core.command_runner import CommandResult
from torii_sumo.core.routeability_audit import (
    inspect_routeability_outputs,
    run_routeability_audit,
)


def _write_od_network(path: Path) -> None:
    path.write_text('<net version="1.20"><location netOffset="0,0" convBoundary="0,0,20,1" origBoundary="0,0,20,1" projParameter="!"/><edge id="a" from="x" to="y" priority="1"><lane id="a_0" index="0" speed="10" length="20" shape="0,0 20,0" allow="passenger"/></edge><edge id="b" from="y" to="x" priority="1"><lane id="b_0" index="0" speed="10" length="20" shape="20,1 0,1" allow="passenger"/></edge><junction id="x" type="priority" x="0" y="0" incLanes="b_0" intLanes="" shape="0,0 0,1"/><junction id="y" type="priority" x="20" y="0" incLanes="a_0" intLanes="" shape="20,0 20,1"/><connection from="a" to="b" fromLane="0" toLane="0" dir="t" state="M"/><connection from="b" to="a" fromLane="0" toLane="0" dir="t" state="M"/></net>', encoding="utf-8")


def _fake_route_stage(command: list[str], cwd: Path) -> bool:
    if command[0] == "duarouter":
        assert "--ignore-errors" not in command
        root = ET.Element("routes")
        for trip in ET.parse(command[command.index("--route-files") + 1]).getroot():
            vehicle = ET.SubElement(root, "vehicle", id=trip.get("id"), depart=trip.get("depart"))
            ET.SubElement(vehicle, "route", edges=f"{trip.get('from')} {trip.get('to')}")
        ET.ElementTree(root).write(command[command.index("--output-file") + 1], encoding="utf-8")
        return True
    return False


def _fake_vehicle_routes(cwd: Path, cfg_root: ET.Element, cfg_path: Path, arrived: int, fault: str | None = None) -> None:
    rows = ET.parse(cwd / cfg_root.find("input/route-files").get("value")).getroot().findall("vehicle")
    output = cfg_root.find("output/vehroute-output")
    path = cwd / output.get("value") if output is not None else cwd / (cfg_path.stem + "_vehroute.xml")
    root = ET.Element("routes")
    for row in rows[:0 if fault == "missing" else arrived]:
        arrival = float(row.get("depart")) + 10
        if fault == "after_window":
            arrival = 6000.0
        vehicle = ET.SubElement(root, "vehicle", id=row.get("id"), depart=row.get("depart"), arrival=str(-1 if fault == "unfinished" else arrival))
        edges = row.find("route").get("edges").split()
        if fault == "wrong_route":
            edges.insert(1, "wrong")
        attrs = {"edges": " ".join(edges), "exitTimes": " ".join(str(arrival) for _ in edges)}
        if fault == "replaced":
            attrs["replacedOnEdge"] = edges[0]
        ET.SubElement(vehicle, "route", **attrs)
    ET.ElementTree(root).write(path, encoding="utf-8")


def test_routeability_outputs_reject_incomplete_final_summary(tmp_path: Path) -> None:
    summary = tmp_path / "summary.xml"
    tripinfo = tmp_path / "tripinfo.xml"
    summary.write_text(
        """<summary>
  <step time="119.00" loaded="60" inserted="60" arrived="4" ended="4" running="56" waiting="0" teleports="0" collisions="0"/>
</summary>""",
        encoding="utf-8",
    )
    tripinfo.write_text(
        """<tripinfos>
  <tripinfo id="veh0" duration="10" waitingTime="0" timeLoss="1"/>
  <tripinfo id="veh1" duration="10" waitingTime="0" timeLoss="1"/>
  <tripinfo id="veh2" duration="10" waitingTime="0" timeLoss="1"/>
  <tripinfo id="veh3" duration="10" waitingTime="0" timeLoss="1"/>
</tripinfos>""",
        encoding="utf-8",
    )

    report = inspect_routeability_outputs(
        summary_path=summary,
        tripinfo_path=tripinfo,
        expected_vehicle_count=60,
    )

    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert report["routeability_status"] == "incomplete"
    assert report["summary"]["arrived"] == 4
    assert report["summary"]["running"] == 56
    assert any("arrived 4/60 vehicles" in warning for warning in report["warnings"])
    assert any("56 vehicles still running" in warning for warning in report["warnings"])


def test_routeability_audit_extends_horizon_until_all_vehicles_finish(tmp_path: Path) -> None:
    net_file = tmp_path / "network.net.xml"
    _write_od_network(net_file)
    calls: list[list[str]] = []

    def fake_runner(command: list[str], *, cwd: Path | None = None, timeout_seconds: float = 60.0):
        calls.append(command)
        assert cwd is not None
        if _fake_route_stage(command, cwd):
            return CommandResult(command=command, cwd=str(cwd), status="pass", returncode=0)

        cfg_path = cwd / command[command.index("-c") + 1]
        cfg_root = ET.parse(cfg_path).getroot()
        end = int(cfg_root.find("time/end").attrib["value"])
        summary_path = cwd / cfg_root.find("output/summary-output").attrib["value"]
        tripinfo_path = cwd / cfg_root.find("output/tripinfo-output").attrib["value"]
        if end == 300:
            arrived = 4
            running = 6
        else:
            arrived = 10
            running = 0
        summary_path.write_text(
            f"""<summary>
  <step time="{end}.00" loaded="10" inserted="10" arrived="{arrived}" ended="{arrived}" running="{running}" waiting="0" teleports="0" collisions="0"/>
</summary>""",
            encoding="utf-8",
        )
        tripinfo_path.write_text(
            "<tripinfos>"
            + "".join(
                f'<tripinfo id="{i}" depart="{i}" arrival="{i + 10}" duration="10" waitingTime="0" timeLoss="1"/>'
                for i in range(arrived)
            )
            + "</tripinfos>",
            encoding="utf-8",
        )
        _fake_vehicle_routes(cwd, cfg_root, cfg_path, arrived)
        return CommandResult(command=command, cwd=str(cwd), status="pass", returncode=0)

    report = run_routeability_audit(
        net_file=net_file,
        output_dir=tmp_path / "audit",
        prefix="demo",
        vehicle_count=10,
        initial_end=300,
        max_end=600,
        seed=7,
        binaries={"sumo": "sumo", "randomTrips": "randomTrips.py", "duarouter": "duarouter"},
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"
    assert report["routeability_status"] == "pass"
    assert [attempt["end"] for attempt in report["attempts"]] == [300, 600]
    assert report["final_attempt"]["inspection"]["summary"]["arrived"] == 10
    assert report["final_attempt"]["inspection"]["summary"]["running"] == 0
    assert Path(report["report_file"]).is_file()
    assert any(command[0] == "sumo" for command in calls)
    sumo_calls = [command for command in calls if command[0] == "sumo"]
    assert all("--collision.check-junctions" in command for command in sumo_calls)
    assert all(
        command[command.index("--collision.check-junctions") + 1] == "true"
        for command in sumo_calls
    )


def test_routeability_audit_passes_absolute_net_file_to_strict_router(tmp_path: Path) -> None:
    net_file = tmp_path / "network.net.xml"
    _write_od_network(net_file)

    def fake_runner(command: list[str], *, cwd: Path | None = None, timeout_seconds: float = 60.0):
        assert cwd is not None
        if command[0] == "duarouter":
            net_arg = Path(command[command.index("--net-file") + 1])
            assert net_arg.is_absolute()
        if _fake_route_stage(command, cwd):
            return CommandResult(command=command, cwd=str(cwd), status="pass", returncode=0)

        cfg_path = cwd / command[command.index("-c") + 1]
        cfg_root = ET.parse(cfg_path).getroot()
        assert Path(cfg_root.find("input/net-file").attrib["value"]).is_absolute()
        summary_path = cwd / cfg_root.find("output/summary-output").attrib["value"]
        tripinfo_path = cwd / cfg_root.find("output/tripinfo-output").attrib["value"]
        summary_path.write_text(
            """<summary>
  <step time="300.00" loaded="1" inserted="1" arrived="1" ended="1" running="0" waiting="0" teleports="0" collisions="0"/>
</summary>""",
            encoding="utf-8",
        )
        tripinfo_path.write_text(
            """<tripinfos>
  <tripinfo id="0" depart="0" arrival="10" duration="10" waitingTime="0" timeLoss="1"/>
</tripinfos>""",
            encoding="utf-8",
        )
        _fake_vehicle_routes(cwd, cfg_root, cfg_path, 1)
        return CommandResult(command=command, cwd=str(cwd), status="pass", returncode=0)

    report = run_routeability_audit(
        net_file=net_file,
        output_dir=tmp_path / "audit",
        prefix="demo",
        vehicle_count=1,
        initial_end=300,
        max_end=300,
        binaries={"sumo": "sumo", "randomTrips": "randomTrips.py", "duarouter": "duarouter"},
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"


def test_routeability_audit_does_not_reuse_stale_route_generation_outputs(tmp_path: Path) -> None:
    net_file = tmp_path / "network.net.xml"
    output_dir = tmp_path / "audit"
    output_dir.mkdir()
    _write_od_network(net_file)
    (output_dir / "demo.trips.xml").write_text("<stale-trips/>", encoding="utf-8")
    (output_dir / "demo.rou.xml").write_text("<stale-routes/>", encoding="utf-8")

    report = run_routeability_audit(
        net_file=net_file,
        output_dir=output_dir,
        prefix="demo",
        vehicle_count=1,
        initial_end=300,
        max_end=300,
        binaries={"sumo": "sumo", "randomTrips": "randomTrips.py", "duarouter": "duarouter"},
        command_runner=lambda command, **_kwargs: {
            "status": "pass",
            "returncode": 0,
            "command": command,
        },
    )

    assert report["status"] == "fail"
    assert report["routeability_status"] == "route-generation-failed"
    # Fresh source-reachable requests remain as evidence when routing fails.
    assert ET.parse(output_dir / "demo.trips.xml").getroot().tag == "routes"
    assert len(ET.parse(output_dir / "demo.trips.xml").getroot().findall("trip")) == 1
    assert not (output_dir / "demo.rou.xml").exists()
    assert Path(report["report_file"]).is_file()
    assert Path(report["manifest_file"]).is_file()


def test_routeability_audit_persists_external_runner_exception(tmp_path: Path) -> None:
    net_file = tmp_path / "network.net.xml"
    _write_od_network(net_file)

    def broken_runner(*_args, **_kwargs):
        raise RuntimeError("runner unavailable")

    report = run_routeability_audit(
        net_file=net_file,
        output_dir=tmp_path / "audit",
        prefix="demo",
        vehicle_count=1,
        initial_end=300,
        max_end=300,
        binaries={"sumo": "sumo", "randomTrips": "randomTrips.py", "duarouter": "duarouter"},
        command_runner=broken_runner,
    )

    assert report["status"] == "fail"
    assert report["routeability_status"] == "route-generation-failed"
    assert "runner unavailable" in report["route_generation"]["error"]
    assert Path(report["manifest_file"]).is_file()


@pytest.mark.skipif(any(shutil.which(name) is None for name in ("netconvert", "sumo", "duarouter")), reason="SUMO tools are not installed")
@pytest.mark.parametrize("permanent_red", [False, True])
def test_bounded_completion_waits_for_long_red_but_rejects_permanent_red(tmp_path: Path, permanent_red: bool) -> None:
    nodes, edges, tls = (tmp_path / name for name in ("nodes.xml", "edges.xml", "tls.xml"))
    nodes.write_text('<nodes><node id="w" x="-100" y="0"/><node id="J" x="0" y="0" type="traffic_light"/><node id="e" x="100" y="0"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="w" to="J" speed="10"/><edge id="out" from="J" to="e" speed="10"/></edges>', encoding="utf-8")
    phases = '<phase duration="60" state="r"/>' if permanent_red else '<phase duration="340" state="r"/><phase duration="60" state="G"/>'
    tls.write_text(f'<tlLogics><tlLogic id="J" type="static" programID="0" offset="0">{phases}</tlLogic></tlLogics>', encoding="utf-8")
    net = tmp_path / "network.net.xml"
    subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "--tllogic-files", str(tls), "-o", str(net)], check=True, capture_output=True, timeout=30)
    requests = tmp_path / "requests.xml"
    requests.write_text('<routes><trip id="0" depart="0" from="in" to="out"/></routes>', encoding="utf-8")
    digest = hashlib.sha256(requests.read_bytes()).hexdigest()
    initial, maximum = (30, 60) if permanent_red else (300, 600)
    report = run_routeability_audit(net_file=net, frozen_trip_file=requests, expected_frozen_trip_sha256=digest,
        output_dir=tmp_path / "audit", vehicle_count=1, seed=104, initial_end=initial, max_end=maximum)
    summary = report["final_attempt"]["inspection"]["summary"]
    assert report["status"] == ("fail" if permanent_red else "pass")
    assert summary["arrived"] == (0 if permanent_red else 1)
    assert summary["running"] == (1 if permanent_red else 0)
    assert summary["teleports"] == summary["collisions"] == 0
    assert report["completion_policy"]["mode"] == "bounded_natural_completion"
    assert report["completion_policy"]["time_to_teleport_s"] == maximum
    assert report["final_attempt"]["vehicle_routes"]["status"] == ("fail" if permanent_red else "pass")
    assert [attempt["end"] for attempt in report["attempts"]] == [initial, maximum]
    for attempt in report["attempts"]:
        command = attempt["command"]["command"]
        assert command[command.index("--time-to-teleport") + 1] == str(maximum)
        assert attempt["time_to_teleport_s"] == maximum
    assert hashlib.sha256(requests.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("field,reason", [("collisions", "collision-failure"), ("teleports", "teleport-failure"), ("discarded", "discard-failure")])
def test_bounded_completion_rejects_abnormal_recovery_even_with_all_arrivals(tmp_path: Path, field: str, reason: str) -> None:
    summary, tripinfo = tmp_path / "summary.xml", tmp_path / "tripinfo.xml"
    attrs = {"loaded": "1", "inserted": "1", "arrived": "1", "running": "0", "waiting": "0", "collisions": "0", "teleports": "0", "discarded": "0", field: "1"}
    root = ET.Element("summary")
    ET.SubElement(root, "step", time="59", **attrs)
    ET.ElementTree(root).write(summary, encoding="utf-8")
    tripinfo.write_text('<tripinfos><tripinfo id="0" arrival="59" duration="59"/></tripinfos>', encoding="utf-8")
    report = inspect_routeability_outputs(summary_path=summary, tripinfo_path=tripinfo, expected_vehicle_count=1)
    assert report["status"] == "fail"
    assert report["routeability_status"] == reason


@pytest.mark.parametrize("fault", ["wrong_route", "missing", "unfinished", "replaced", "after_window"])
def test_bounded_completion_requires_the_actual_complete_approved_vehicle_routes(tmp_path: Path, fault: str) -> None:
    net = tmp_path / "network.net.xml"
    _write_od_network(net)

    def fake_runner(command, *, cwd, timeout_seconds):
        if not _fake_route_stage(command, cwd):
            cfg_path = cwd / command[command.index("-c") + 1]
            cfg = ET.parse(cfg_path).getroot()
            (cwd / cfg.find("output/summary-output").get("value")).write_text('<summary><step time="59" loaded="1" inserted="1" arrived="1" running="0" waiting="0" teleports="0" collisions="0"/></summary>', encoding="utf-8")
            (cwd / cfg.find("output/tripinfo-output").get("value")).write_text('<tripinfos><tripinfo id="0" depart="0" arrival="10" duration="10"/></tripinfos>', encoding="utf-8")
            _fake_vehicle_routes(cwd, cfg, cfg_path, 1, fault)
        return CommandResult(command=command, cwd=str(cwd), status="pass", returncode=0)

    report = run_routeability_audit(net_file=net, output_dir=tmp_path / "audit", vehicle_count=1, initial_end=60, max_end=60,
        binaries={"sumo": "sumo", "duarouter": "duarouter"}, command_runner=fake_runner)
    assert report["status"] == "fail"
    assert report["routeability_status"] == "vehicle-route-completion-failed"


def test_vehicle_route_output_cannot_overwrite_a_hardlinked_frozen_input(tmp_path: Path) -> None:
    net = tmp_path / "network.net.xml"
    _write_od_network(net)
    requests = tmp_path / "requests.xml"
    content = b'<routes><trip id="0" depart="0" from="a" to="b"/></routes>'
    requests.write_bytes(content)
    output = tmp_path / "audit"
    output.mkdir()
    os.link(requests, output / "demo_end60_vehroute.xml")
    calls = []
    report = run_routeability_audit(net_file=net, frozen_trip_file=requests, expected_frozen_trip_sha256=hashlib.sha256(content).hexdigest(), output_dir=output,
        prefix="demo", vehicle_count=1, initial_end=60, max_end=60, binaries={"sumo": "sumo", "duarouter": "duarouter"}, command_runner=lambda *args, **kwargs: calls.append(args))
    assert report["status"] == "fail"
    assert "overlap" in report["error"]
    assert calls == []
    assert requests.read_bytes() == content


def test_vehicle_route_output_cannot_replace_the_approved_router_output(tmp_path: Path) -> None:
    net = tmp_path / "network.net.xml"
    _write_od_network(net)
    approved = {}

    def fake_runner(command, *, cwd, timeout_seconds):
        assert _fake_route_stage(command, cwd), "SUMO must not run with overlapping route output"
        path = Path(command[command.index("--output-file") + 1])
        approved["path"], approved["bytes"] = path, path.read_bytes()
        os.link(path, cwd / "demo_end60_vehroute.xml")
        return CommandResult(command=command, cwd=str(cwd), status="pass", returncode=0)

    report = run_routeability_audit(net_file=net, output_dir=tmp_path / "audit", prefix="demo", vehicle_count=1, initial_end=60, max_end=60,
        binaries={"sumo": "sumo", "duarouter": "duarouter"}, command_runner=fake_runner)
    assert report["status"] == "fail"
    assert report["routeability_status"] == "output-input-overlap"
    assert approved["path"].read_bytes() == approved["bytes"]


@pytest.mark.parametrize("fault", [None, "valid_depart_delay", "explicit_false", "depart_after_arrival", "before_approved_depart", "exit_before_depart", "vaporized", "tripinfo_time_mismatch", "tripinfo_wrong_id", "tripinfo_unfinished"])
def test_bounded_completion_rejects_inconsistent_actual_departures_and_tripinfo(tmp_path: Path, fault: str | None) -> None:
    net = tmp_path / "network.net.xml"
    _write_od_network(net)
    requests = tmp_path / "requests.xml"
    requests.write_text('<routes><trip id="0" depart="20" from="a" to="b"/></routes>', encoding="utf-8")

    def fake_runner(command, *, cwd, timeout_seconds):
        if not _fake_route_stage(command, cwd):
            cfg_path = cwd / command[command.index("-c") + 1]
            cfg = ET.parse(cfg_path).getroot()
            (cwd / cfg.find("output/summary-output").get("value")).write_text('<summary><step time="59" loaded="1" inserted="1" arrived="1" running="0" waiting="0" teleports="0" collisions="0"/></summary>', encoding="utf-8")
            _fake_vehicle_routes(cwd, cfg, cfg_path, 1)
            observed_path = cwd / cfg.find("output/vehroute-output").get("value")
            observed = ET.parse(observed_path)
            vehicle = observed.getroot().find("vehicle")
            tripinfos = ET.Element("tripinfos")
            tripinfo = ET.SubElement(tripinfos, "tripinfo", id="0", depart="20", arrival="30", duration="10", vaporized="")
            if fault == "valid_depart_delay":
                vehicle.set("depart", "22")
                tripinfo.set("depart", "22")
            elif fault == "explicit_false":
                tripinfo.set("vaporized", "false")
            elif fault == "depart_after_arrival":
                vehicle.set("depart", "40")
                tripinfo.set("depart", "40")
            elif fault == "before_approved_depart":
                vehicle.set("depart", "10")
                tripinfo.set("depart", "10")
            elif fault == "exit_before_depart":
                vehicle.find("route").set("exitTimes", "15 30")
            elif fault == "vaporized":
                tripinfo.set("vaporized", "true")
            elif fault == "tripinfo_time_mismatch":
                tripinfo.set("arrival", "29")
            elif fault == "tripinfo_wrong_id":
                tripinfo.set("id", "other")
            elif fault == "tripinfo_unfinished":
                tripinfo.set("arrival", "-1")
            observed.write(observed_path, encoding="utf-8")
            ET.ElementTree(tripinfos).write(cwd / cfg.find("output/tripinfo-output").get("value"), encoding="utf-8")
        return CommandResult(command=command, cwd=str(cwd), status="pass", returncode=0)

    report = run_routeability_audit(net_file=net, frozen_trip_file=requests, expected_frozen_trip_sha256=hashlib.sha256(requests.read_bytes()).hexdigest(),
        output_dir=tmp_path / "audit", vehicle_count=1, initial_end=60, max_end=60, binaries={"sumo": "sumo", "duarouter": "duarouter"}, command_runner=fake_runner)
    valid = fault in (None, "valid_depart_delay", "explicit_false")
    assert report["status"] == ("pass" if valid else "fail")
    assert report["routeability_status"] == ("pass" if valid else "vehicle-route-completion-failed")
