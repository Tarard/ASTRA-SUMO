from collections import Counter
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


def _build(directory: Path, *options: str, nodes=None, roads=None, one_way=(), footways=(), bus_ways=(),
           connections=None) -> Path:
    """Build a georeferenced network from plain XML; ``roads`` are two-way node pairs."""
    nodes = {**GRID, **{name: point for name, (point, _) in ARMS.items()}, **(nodes or {})}
    if roads is None:
        roads = [(f"g{i}{j}", f"g{i + 1}{j}") for i in range(2) for j in range(3)]
        roads += [(f"g{i}{j}", f"g{i}{j + 1}") for i in range(3) for j in range(2)]
        roads += [(name, grid) for name, (_, grid) in ARMS.items()]
    edges = [(a, b, "") for pair in roads for a, b in (pair, pair[::-1])] + [(a, b, "") for a, b in one_way]
    edges += [(a, b, ' allow="pedestrian"') for a, b in footways] + [(a, b, ' allow="bus"') for a, b in bus_ways]
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


def test_min_vehicles_per_exit_sets_the_planned_counts_above_the_entry_quota(tmp_path):
    net = _build(tmp_path / "net", "--no-turnarounds")
    report = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "audit", boundary=BBOX, vehicles_per_entry=1,
                                             min_vehicles_per_exit=3, binaries={"duarouter": "duarouter", "sumo": "sumo"},
                                             command_runner=_failing_runner)
    load = report["load"]
    assert load["vehicle_count"] == 12
    assert [row["planned"] for row in load["exits"]] == [3, 3, 3, 3]
    assert sum(row["planned"] for row in load["entries"]) == 12
    assert all(row["planned"] >= 1 for row in load["entries"]) and max(row["planned"] for row in load["entries"]) > 1


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

    # A footway that continues past the cul-de-sac leaves it a dead end; a bus-only road makes it a permission gap.
    footway = _build(tmp_path / "footway", nodes={**spur, "f": (13.705, 51.0335)}, one_way=[("g11", "d"), ("d", "g11")],
                     footways=[("d", "f"), ("f", "d")])
    report = run_boundary_completeness_audit(net_file=footway, output_dir=tmp_path / "footway-audit", boundary=BBOX,
                                             run_simulation=False)
    rows = _endpoints(report)
    assert (rows[("g11_d", "exit")]["class"], rows[("g11_d", "exit")]["reason"]) == ("dead_end", "covered_by_turnaround")
    assert (report["status"], report["findings"]) == ("pass", [])
    bus = _build(tmp_path / "bus", nodes={**spur, "f": (13.705, 51.0335)}, one_way=[("g11", "d"), ("d", "g11")],
                 bus_ways=[("d", "f"), ("f", "d")])
    report = run_boundary_completeness_audit(net_file=bus, output_dir=tmp_path / "bus-audit", boundary=BBOX, run_simulation=False)
    rows = _endpoints(report)
    assert (rows[("g11_d", "exit")]["class"], rows[("g11_d", "exit")]["reason"]) == ("interior_gap", "no_other_passenger_edge")
    assert (report["status"], report["reasons"]) == ("review_required", ["interior_gap"])


def test_cut_endpoints_on_one_way_stubs_have_no_cut_counterpart(tmp_path):
    # The west arm only enters a one-way stub that ends inside the cut; the east arm is only left from one.
    roads = [(f"g{i}{j}", f"g{i + 1}{j}") for i in range(2) for j in range(3)]
    roads += [(f"g{i}{j}", f"g{i}{j + 1}") for i in range(3) for j in range(2)] + [("s", "g10"), ("n", "g12")]
    net = _build(tmp_path / "net", "--no-turnarounds", nodes={"p": (13.6998, 51.0325), "q": (13.7062, 51.0315)},
                 roads=roads, one_way=[("w", "p"), ("q", "e")])
    report = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "audit", boundary=BBOX, run_simulation=False)
    assert (report["status"], report["reasons"]) == (
        "review_required", ["cut_entry_without_cut_exit", "cut_exit_without_cut_entry", "interior_gap"])
    assert {(finding["edge"], finding["role"], finding["reason"]) for finding in report["findings"]} == {
        ("w_p", "entry", "cut_entry_without_cut_exit"), ("w_p", "exit", "interior_gap"),
        ("q_e", "exit", "cut_exit_without_cut_entry"), ("q_e", "entry", "interior_gap")}
    pairs = report["reachability"]["pairs"]
    assert (pairs["cut_to_cut"], pairs["cut_to_any"]) == ({"reachable": 2, "total": 7}, {"reachable": 3, "total": 10})
    assert report["reachability"]["exits_without_reaching_entry"] == {}
    rows = _endpoints(report)
    assert (rows[("w_p", "entry")]["class"], rows[("w_p", "entry")]["reachable_count"]) == ("cut_boundary", "1")
    assert (rows[("q_e", "exit")]["class"], rows[("q_e", "exit")]["reachable_count"]) == ("cut_boundary", "1")


def test_margin_decides_whether_an_endpoint_near_the_cut_is_cut(tmp_path):
    # The one-way entry starts about 30 m inside the south side of the cut.
    net = _build(tmp_path / "net", "--no-turnarounds", nodes={"k": (13.7, 51.02927)}, one_way=[("k", "g00")])
    near = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "default", boundary=BBOX, run_simulation=False)
    assert _endpoints(near)[("k_g00", "entry")]["class"] == "cut_boundary"
    assert (near["status"], near["endpoints"]["by_class"]["entry"]) == ("pass", {"cut_boundary": 5})
    exact = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "exact", boundary=BBOX, margin_m=0,
                                            run_simulation=False)
    row = _endpoints(exact)[("k_g00", "entry")]
    assert (row["class"], row["reason"]) == ("interior_gap", "no_other_passenger_edge")
    assert (exact["status"], exact["reasons"]) == ("review_required", ["interior_gap"])


def test_network_extent_of_a_straight_corridor_makes_every_endpoint_cut(tmp_path):
    directory = tmp_path / "line"
    directory.mkdir()
    (directory / "n.nod.xml").write_text('<nodes><node id="a" x="0" y="0"/><node id="b" x="200" y="0"/>'
                                         '<node id="c" x="400" y="0"/></nodes>', encoding="utf-8")
    (directory / "n.edg.xml").write_text("<edges>" + "".join(
        f'<edge id="{a}{b}" from="{a}" to="{b}" numLanes="1"/>' for a, b in ("ab", "ba", "bc", "cb")) + "</edges>",
        encoding="utf-8")
    subprocess.run([_binaries()["netconvert"], "--node-files", "n.nod.xml", "--edge-files", "n.edg.xml", "--output-file",
                    "line.net.xml", "--no-turnarounds"], cwd=directory, check=True, capture_output=True)
    report = run_boundary_completeness_audit(net_file=directory / "line.net.xml", output_dir=tmp_path / "audit",
                                             run_simulation=False)
    assert (report["status"], report["reasons"], report["boundary"]["source"]) == ("pass", [], "network_extent")
    assert report["endpoints"]["by_class"] == {"entry": {"cut_boundary": 2}, "exit": {"cut_boundary": 2}}
    assert report["reachability"]["pairs"]["cut_to_cut"] == {"reachable": 2, "total": 2}


def test_isolated_road_whose_dead_ends_have_turnarounds_lies_off_every_boundary_path(tmp_path):
    net = _build(tmp_path / "net", nodes={"i1": (13.7015, 51.031), "i2": (13.702, 51.0312)}, one_way=[("i1", "i2"), ("i2", "i1")])
    report = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "audit", boundary=BBOX, run_simulation=False)
    assert (report["status"], report["reasons"], report["findings"]) == ("review_required", ["edges_off_boundary_paths"], [])
    off = report["reachability"]["edges_off_boundary_paths"]
    assert (off["count"], sorted(off["edges"])) == (2, ["i1_i2", "i2_i1"]) and off["lane_km"] > 0
    network = report["network"]
    largest = network["largest_strong_component"]
    assert (largest["edge_count"], largest["edge_share"]) == (32, round(32 / 34, 6))
    assert largest["lane_km"] == pytest.approx(network["passenger_lane_km"] - off["lane_km"], abs=0.002)
    assert largest["lane_km_share"] == pytest.approx(largest["lane_km"] / network["passenger_lane_km"], abs=1e-4)
    assert largest["lane_km_share"] < 1


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


def test_collided_and_teleported_vehicles_fail_the_boundary_load_although_they_arrive(tmp_path):
    binaries = _binaries()
    net = _build(tmp_path / "net", "--no-turnarounds")

    def colliding(command, **kwargs):
        result = run_command(command, **kwargs).to_dict()
        if Path(command[0]).stem == "sumo":
            # SUMO reports a collision of b0 into b1 and a jam teleport of b2; all three still arrive.
            collisions = Path(kwargs["cwd"]) / command[command.index("--collision-output") + 1]
            collisions.write_text('<collisions><collision time="20.00" type="junction" lane=":g11_0_0" collider="b0" '
                                  'victim="b1"/></collisions>', encoding="utf-8")
            result["stderr"] += "Warning: Teleporting vehicle 'b2'; waited too long (jam), lane='g01_g11_0', time=30.00.\n"
        return result

    report = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "load", boundary=BBOX, demand_seconds=60,
                                             binaries=binaries, timeout_seconds=120, command_runner=colliding)
    load = report["load"]
    assert (report["status"], report["reasons"], load["status"]) == (
        "review_required", ["boundary_load_incomplete", "boundary_load_collision", "boundary_load_teleport"], "review_required")
    assert (load["arrived_count"], load["unfinished_count"]) == (13, 3)
    assert {row["vehicle_id"]: (row["state"], row["last_edge"]) for row in load["unfinished_vehicles"]} == {
        "b0": ("collision", ":g11_0"), "b1": ("collision", ":g11_0"), "b2": ("teleported", "g01_g11")}
    trips = {row.get("id"): row for row in ET.parse(load["trip_file"]).getroot().findall("trip")}
    failed_entries = Counter(trips[name].get("from") for name in ("b0", "b1", "b2"))
    assert {row["edge"]: row["planned"] - row["arrived"] for row in load["entries"] if row["arrived"] < row["planned"]} == failed_entries
    assert {finding["edge"] for finding in report["findings"] if finding["role"] == "entry"} == set(failed_entries)


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
    # Without SUMO the static part still runs; only the load is blocked.
    no_binaries = run_boundary_completeness_audit(net_file=net, output_dir=tmp_path / "nobin", boundary=BBOX,
                                                  binaries={"sumo": None})
    assert (no_binaries["status"], no_binaries["reasons"]) == ("blocked", ["dead_end_without_turnaround", "sumo_binaries_missing"])
    assert (no_binaries["load"]["status"], no_binaries["load"]["reason"]) == ("blocked", "sumo_binaries_missing")
    assert no_binaries["finding_count"] == 2 and no_binaries["endpoints"]["by_class"]["exit"] == {"cut_boundary": 4, "dead_end": 1}
