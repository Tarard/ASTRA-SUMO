import csv
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo import cli
from astra_sumo.core.boundary_completeness import run_boundary_completeness_audit
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.command_runner import run_command


# A 3x3 grid about 210 m apart with one two-way arm per side; every arm ends outside BBOX.
BBOX = "13.6985,51.029,13.7075,51.035"
GRID = {f"g{i}{j}": (13.700 + 0.003 * i, 51.030 + 0.002 * j) for i in range(3) for j in range(3)}
ARMS = {"w": ((13.697, 51.032), "g01"), "e": ((13.709, 51.032), "g21"), "s": ((13.703, 51.028), "g10"),
        "n": ((13.703, 51.036), "g12")}


def _binaries() -> dict[str, str]:
    sumo = pytest.importorskip("sumo")
    directory = Path(sumo.__file__).parent / "bin"
    suffix = ".exe" if os.name == "nt" else ""
    found = {name: directory / f"{name}{suffix}" for name in ("netconvert", "duarouter", "sumo")}
    if not all(path.is_file() for path in found.values()):
        pytest.skip("The SUMO package binaries are required.")
    return {name: str(path) for name, path in found.items()}


def _build(directory: Path, *options: str, nodes=None, roads=None, one_way=(), footways=(), connections=None) -> Path:
    """Build a georeferenced network from plain XML; ``roads`` are two-way node pairs."""
    nodes = {**GRID, **{name: point for name, (point, _) in ARMS.items()}, **(nodes or {})}
    if roads is None:
        roads = [(f"g{i}{j}", f"g{i + 1}{j}") for i in range(2) for j in range(3)]
        roads += [(f"g{i}{j}", f"g{i}{j + 1}") for i in range(3) for j in range(2)]
        roads += [(name, grid) for name, (_, grid) in ARMS.items()]
    edges = [(a, b, "") for pair in roads for a, b in (pair, pair[::-1])] + [(a, b, "") for a, b in one_way]
    edges += [(a, b, ' allow="pedestrian"') for a, b in footways]
    directory.mkdir(parents=True)
    (directory / "n.nod.xml").write_text("<nodes>" + "".join(
        f'<node id="{name}" x="{x:.6f}" y="{y:.6f}"/>' for name, (x, y) in nodes.items()) + "</nodes>", encoding="utf-8")
    (directory / "n.edg.xml").write_text("<edges>" + "".join(
        f'<edge id="{a}_{b}" from="{a}" to="{b}" numLanes="1"{extra}/>' for a, b, extra in edges) + "</edges>", encoding="utf-8")
    command = [_binaries()["netconvert"], "--node-files", "n.nod.xml", "--edge-files", "n.edg.xml", "--proj.utm",
               "--output-file", "grid.net.xml", *options]
    if connections is not None:
        (directory / "n.con.xml").write_text(connections, encoding="utf-8")
        command += ["--connection-files", "n.con.xml"]
    subprocess.run(command, cwd=directory, check=True, capture_output=True)
    return directory / "grid.net.xml"


def _endpoints(report) -> dict[tuple[str, str], dict[str, str]]:
    with open(report["endpoints_file"], encoding="utf-8", newline="") as handle:
        return {(row["edge"], row["role"]): row for row in csv.DictReader(handle)}


def _failing_runner(command, **kwargs):
    return {"status": "fail", "returncode": 1, "stdout": "", "stderr": "not run"}


def test_grid_cut_by_a_bbox_loads_every_cut_entry_and_exit_deterministically(tmp_path):
    net = _build(tmp_path / "net", "--no-turnarounds")
    source_hash = file_sha256(net)
    static = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "static", boundary=BBOX, run_simulation=False)

    assert (static["status"], static["reasons"], static["boundary"]["source"]) == ("pass", [], "bbox")
    assert static["endpoints"]["by_class"] == {"entry": {"cut_boundary": 4}, "exit": {"cut_boundary": 4}}
    assert static["reachability"]["pairs"]["cut_to_cut"] == {"reachable": 12, "total": 12}
    assert static["reachability"]["entries_without_reachable_exit"] == {}
    assert static["reachability"]["edges_off_boundary_paths"]["count"] == 0
    assert static["load"]["status"] == "not_applicable" and static["findings"] == []
    assert static["network"]["largest_strong_component"]["edge_count"] == 24
    assert all(row["loaded"] == "true" and row["class"] == "cut_boundary" for row in _endpoints(static).values())

    binaries = {"duarouter": "duarouter", "sumo": "sumo"}
    runs = [run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / name, boundary=BBOX, seed=seed,
                                            binaries=binaries, command_runner=_failing_runner)
            for name, seed in (("first", 7), ("again", 7), ("other", 8))]
    trips = [ET.parse(run["load"]["trip_file"]).getroot().findall("trip") for run in runs]
    assert Path(runs[0]["load"]["trip_file"]).read_bytes() == Path(runs[1]["load"]["trip_file"]).read_bytes()
    assert [(row.get("from"), row.get("to"), row.get("depart")) for row in trips[0]] != [
        (row.get("from"), row.get("to"), row.get("depart")) for row in trips[2]]
    for rows in trips:
        assert [row.get("id") for row in rows] == [f"b{number}" for number in range(16)]
        departs = [float(row.get("depart")) for row in rows]
        assert departs == sorted(departs) and 0 <= departs[0] and departs[-1] < 1800
        entries = [row.get("from") for row in rows]
        exits = [row.get("to") for row in rows]
        assert {edge: entries.count(edge) for edge in entries} == {f"{name}_{grid}": 4 for name, (_, grid) in ARMS.items()}
        assert {edge for edge in exits} == {f"{grid}_{name}" for name, (_, grid) in ARMS.items()}
        assert all(exits.count(edge) >= 2 for edge in exits)
        # No vehicle leaves through the arm it came in on.
        assert all(entry.split("_")[0] != target.split("_")[1] for entry, target in zip(entries, exits))
    assert (runs[0]["status"], runs[0]["reasons"]) == ("review_required", ["boundary_load_run_failed"])
    assert runs[0]["load"]["unfinished_count"] == 16 and runs[0]["findings"] == []
    assert file_sha256(net) == source_hash and runs[0]["net_unchanged"]


def test_cul_de_sac_without_turnaround_is_loaded_and_with_one_is_covered(tmp_path):
    spur = {"d": (13.7045, 51.033)}
    trap = _build(tmp_path / "trap", "--no-turnarounds", nodes=spur, one_way=[("g11", "d"), ("d", "g11")])
    report = run_boundary_completeness_audit(net_file=trap, output_dir=tmp_path / "trap-audit", boundary=BBOX, run_simulation=False)
    rows = _endpoints(report)
    assert (rows[("g11_d", "exit")]["class"], rows[("g11_d", "exit")]["reason"], rows[("g11_d", "exit")]["loaded"]) == (
        "dead_end", "dead_end_without_turnaround", "true")
    assert (rows[("d_g11", "entry")]["class"], rows[("d_g11", "entry")]["loaded"]) == ("dead_end", "true")
    assert report["status"] == "review_required" and report["reasons"] == ["dead_end_without_turnaround"]
    assert {(finding["edge"], finding["role"]) for finding in report["findings"]} == {("g11_d", "exit"), ("d_g11", "entry")}

    covered = _build(tmp_path / "covered", "--no-turnarounds.except-deadend", nodes=spur, one_way=[("g11", "d"), ("d", "g11")])
    report = run_boundary_completeness_audit(net_file=covered, output_dir=tmp_path / "covered-audit", boundary=BBOX,
                                             run_simulation=False)
    rows = _endpoints(report)
    assert (rows[("g11_d", "exit")]["class"], rows[("g11_d", "exit")]["reason"], rows[("g11_d", "exit")]["loaded"]) == (
        "dead_end", "covered_by_turnaround", "false")
    assert rows[("d_g11", "entry")]["loaded"] == "false"
    assert report["endpoints"]["dead_ends_with_turnaround"] == {"entry": 1, "exit": 1}
    assert (report["status"], report["findings"]) == ("pass", [])
    # The arms keep their cut class although they have turnarounds now.
    assert report["endpoints"]["by_class"]["entry"] == {"cut_boundary": 4, "dead_end": 1}


def test_one_way_road_that_ends_where_only_a_footway_continues_is_an_interior_gap(tmp_path):
    net = _build(tmp_path / "net", "--no-turnarounds", nodes={"t": (13.7015, 51.031), "u": (13.702, 51.0315)},
                 one_way=[("g00", "t")], footways=[("t", "u")])
    report = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "audit", boundary=BBOX, run_simulation=False)
    row = _endpoints(report)[("g00_t", "exit")]
    assert (row["class"], row["reason"], row["loaded"]) == ("interior_gap", "no_other_passenger_edge", "true")
    assert [(finding["edge"], finding["reason"], finding["detail"]) for finding in report["findings"]] == [
        ("g00_t", "interior_gap", "no_other_passenger_edge")]
    assert report["endpoints"]["interior_gap_reasons"] == {"entry": {}, "exit": {"no_other_passenger_edge": 1}}
    assert "t_u" not in {edge for edge, _ in _endpoints(report)}


def test_removed_lane_connection_is_an_interior_gap_without_passenger_connection(tmp_path):
    roads = [(a, b) for a, b in [(f"g{i}{j}", f"g{i + 1}{j}") for i in range(2) for j in range(3)] if (a, b) != ("g02", "g12")]
    roads += [(f"g{i}{j}", f"g{i}{j + 1}") for i in range(3) for j in range(2)]
    roads += [(name, grid) for name, (_, grid) in ARMS.items()] + [("g02", "m"), ("m", "g12")]
    net = _build(tmp_path / "net", "--no-turnarounds", nodes={"m": (13.7015, 51.034)}, roads=roads,
                 connections='<connections><delete from="g02_m" to="m_g12"/></connections>')
    report = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "audit", boundary=BBOX, run_simulation=False)
    rows = _endpoints(report)
    assert (rows[("g02_m", "exit")]["class"], rows[("g02_m", "exit")]["reason"]) == ("interior_gap", "no_passenger_connection")
    assert (rows[("m_g12", "entry")]["class"], rows[("m_g12", "entry")]["reason"]) == ("interior_gap", "no_passenger_connection")
    assert report["endpoints"]["interior_gap_reasons"] == {"entry": {"no_passenger_connection": 1},
                                                           "exit": {"no_passenger_connection": 1}}
    assert report["status"] == "review_required" and report["reasons"] == ["interior_gap"]


def test_boundary_load_in_sumo_passes_when_every_vehicle_arrives_and_names_the_unfinished_ones(tmp_path):
    binaries = _binaries()
    net = _build(tmp_path / "net", "--no-turnarounds")
    report = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "load", boundary=BBOX, demand_seconds=60,
                                             binaries=binaries, timeout_seconds=120)
    load = report["load"]
    assert (report["status"], report["reasons"], load["status"]) == ("pass", [], "pass"), load
    assert (load["vehicle_count"], load["arrived_count"], load["inserted_count"], load["unfinished_vehicles"]) == (16, 16, 16, [])
    assert all(row["planned"] == row["inserted"] == row["arrived"] == 4 for row in load["entries"])
    assert sum(row["arrived"] for row in load["exits"]) == 16 and all(row["arrived"] >= 2 for row in load["exits"])
    assert (load["routeability"]["summary"]["teleports"], load["routeability"]["summary"]["collisions"]) == (0, 0)
    assert Path(load["routeability"]["report_file"]).parent == (tmp_path / "load" / "load").resolve()
    assert report["net_unchanged"]

    def stopped_early(command, **kwargs):
        if Path(command[0]).stem == "sumo":
            command = [*command, "--end", "20"]
        return run_command(command, **kwargs)

    report = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "early", boundary=BBOX, demand_seconds=60,
                                             binaries=binaries, timeout_seconds=120, command_runner=stopped_early)
    load = report["load"]
    assert (report["status"], report["reasons"], load["status"]) == ("review_required", ["boundary_load_incomplete"], "review_required")
    assert load["arrived_count"] < 16 and load["unfinished_count"] == 16 - load["arrived_count"]
    states = {row["state"] for row in load["unfinished_vehicles"]}
    assert states <= {"not_inserted", "unfinished"} and "unfinished" in states
    assert all(row["last_edge"] for row in load["unfinished_vehicles"] if row["state"] == "unfinished")
    assert {row["entry"] for row in load["unfinished_vehicles"]} <= {row["edge"] for row in load["entries"]}
    assert {finding["reason"] for finding in report["findings"]} == {"boundary_load_incomplete"}


def test_cli_static_only_reports_the_network_extent_boundary(tmp_path, capsys):
    net = _build(tmp_path / "net", "--no-turnarounds")
    code = cli.main(["network", "boundary-completeness", str(net), str(tmp_path / "cli"), "--static-only", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["status"] == "pass"
    assert (payload["boundary"]["source"], payload["load"]["status"]) == ("network_extent", "not_applicable")
    code = cli.main(["network", "boundary-completeness", str(net), str(tmp_path / "bbox"), "--bbox", BBOX, "--margin-m", "20",
                     "--static-only", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and (payload["boundary"]["source"], payload["boundary"]["margin_m"]) == ("bbox", 20.0)
    assert Path(payload["report_file"]).is_file() and Path(payload["endpoints_file"]).is_file()


def test_review_overlay_holds_only_display_elements_and_missing_inputs_block(tmp_path):
    net = _build(tmp_path / "net", "--no-turnarounds", nodes={"d": (13.7045, 51.033)}, one_way=[("g11", "d"), ("d", "g11")])
    report = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "audit", boundary=BBOX, run_simulation=False)
    root = ET.parse(report["review_overlay_file"]).getroot()
    assert root.tag == "additional" and {element.tag for element in root.iter()} <= {"additional", "poi", "poly", "param"}
    pois = root.findall("poi")
    assert len(pois) == report["finding_count"] == 2
    assert all({param.get("key"): param.get("value") for param in poi.findall("param")}["display_only"] == "true" for poi in pois)

    missing = run_boundary_completeness_audit(net_file=tmp_path / "missing.net.xml", output_dir=tmp_path / "missing")
    assert (missing["status"], missing["reasons"]) == ("blocked", ["network_missing"])
    unprojected = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "bad", boundary="1,2,3", run_simulation=False)
    assert (unprojected["status"], unprojected["reasons"]) == ("blocked", ["boundary_unusable"])
    no_binaries = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "nobin", binaries={"sumo": None})
    assert (no_binaries["status"], no_binaries["reasons"]) == ("blocked", ["sumo_binaries_missing"])
