from pathlib import Path
import gzip
import json
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_corridor_pruning import OFFICIAL_PREFIXES, map_retained_control, select_ring1_corridor


def test_real_corridor_keeps_complete_five_junction_arms_and_all_fields():
    source = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_primary_entry_full_20261022_v1/inputs"
    result = select_ring1_corridor(net_file=source / "network.net.xml", route_file=source / "demand.rou.xml.gz",
                                  detector_file=source / "field-loops.add.xml")
    selected = set(result["selected_edge_ids"])
    assert len(selected) < result["source_road_count"] == 360
    assert len(result["official_junction_ids"]) == 9
    root = ET.parse(source / "network.net.xml").getroot()
    arms = {row.get("id") for row in root.findall("edge") if not row.get("function")
            and any(row.get(end, "").startswith(OFFICIAL_PREFIXES) for end in ("from", "to"))}
    assert arms <= selected
    demands = ET.fromstring(gzip.decompress((source / "demand.rou.xml.gz").read_bytes()))
    assert {edge for vehicle in demands for route in vehicle.findall("route")
            for edge in route.get("edges").split()} <= selected
    assert len([row for row in result["roads"] if "observed_detector_cross_section" in row["reasons"]]) > 0
    assert {row["edge_id"] for row in result["roads"]} == selected | set(result["excluded_edge_ids"])


def test_control_projection_keeps_timing_and_retained_movement_states(tmp_path):
    old, new, control = [tmp_path / name for name in ("old.xml", "new.xml", "control.xml")]
    old.write_text('<net><connection from="a" fromLane="0" to="b" toLane="0" tl="t" linkIndex="2"/></net>')
    new.write_text('<net><connection from="a" fromLane="0" to="b" toLane="0" tl="t" linkIndex="0"/></net>')
    control.write_text('<additional><tlLogic id="t" type="actuated" programID="p" offset="4">'
                       '<phase duration="10" minDur="5" maxDur="20" state="rrG"/>'
                       '<phase duration="3" state="rry"/></tlLogic></additional>')
    output = tmp_path / "mapped.xml"
    records = map_retained_control(source_net=old, candidate_net=new, source_control=control, output_file=output)
    assert records[0]["old_links"] == 3 and records[0]["new_links"] == 1
    program = ET.parse(output).getroot()[0]
    assert program.get("offset") == "4" and program.get("type") == "actuated"
    assert [phase.attrib for phase in program] == [
        {"duration": "10", "minDur": "5", "maxDur": "20", "state": "G"}, {"duration": "3", "state": "y"}]
    new.write_text('<net><connection from="x" fromLane="0" to="b" toLane="0" tl="t" linkIndex="0"/></net>')
    with pytest.raises(ValueError, match="matching source"):
        map_retained_control(source_net=old, candidate_net=new, source_control=control, output_file=output)


def test_crosswalk_reindexing_uses_crossed_roads_and_keeps_unused_link_slots(tmp_path):
    old, new, control = [tmp_path / name for name in ("old.xml", "new.xml", "control.xml")]
    old.write_text('<net><edge id=":j_c2" function="crossing" crossingEdges="a b"/>'
                   '<connection from=":j_w0" fromLane="0" to=":j_c2" toLane="0" tl="t" linkIndex="3"/></net>')
    new.write_text('<net><edge id=":j_c1" function="crossing" crossingEdges="b a"/>'
                   '<connection from=":j_w9" fromLane="0" to=":j_c1" toLane="0" tl="t" linkIndex="3"/></net>')
    control.write_text('<additional><tlLogic id="t"><phase duration="10" state="GGGr"/></tlLogic></additional>')
    output = tmp_path / "mapped.xml"
    map_retained_control(source_net=old, candidate_net=new, source_control=control, output_file=output)
    assert ET.parse(output).getroot()[0][0].get("state") == "rrrr"


def test_native_corridor_keeps_source_geometry_permissions_and_official_programs(tmp_path):
    from astra_sumo.core.hamburg_corridor_pruning import build_ring1_corridor
    from astra_sumo.core.local_network_conversion import loaded_turn_permissions
    from astra_sumo.core.sumo_commands import run_sumo_load_audit

    root = Path(__file__).parents[1]
    binaries = root / ".venv/Lib/site-packages/sumo/bin"
    if not (binaries / "netconvert.exe").is_file():
        pytest.skip("Repository SUMO 1.27.1 Windows binaries required")
    out = tmp_path / "corridor"
    result = build_ring1_corridor(repository=root, output_dir=out,
        netconvert_binary=binaries / "netconvert.exe", sumo_binary=binaries / "sumo.exe")
    assert result["status"] == "measured"
    source = root / "benchmarks/hamburg_ring1_primary_entry_full_20261022_v1/inputs"
    before, after = [ET.parse(path).getroot() for path in (source / "network.net.xml", out / "inputs/network.net.xml")]
    original = {row.get("id"): row for row in before.findall("edge") if not row.get("function")}
    actual = {row.get("id"): row for row in after.findall("edge") if not row.get("function")}
    assert len(actual) == 236 and all(ET.tostring(row) == ET.tostring(original[key]) for key, row in actual.items())
    original_moves = {key: value for key, value in loaded_turn_permissions(before).items()
                      if key[0] in actual and key[2] in actual}
    assert loaded_turn_permissions(after) == original_moves
    for name in ("field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"):
        assert (source / name).read_bytes() == (out / "inputs" / name).read_bytes()
    controls = {row.get("id"): row for row in ET.parse(source / "control.add.xml").getroot()}
    mapped = {row.get("id"): row for row in ET.parse(out / "inputs/control.add.xml").getroot()}
    official = [key for key in controls if key.startswith(OFFICIAL_PREFIXES)]
    assert len(official) == 9 and all(ET.tostring(controls[key]) == ET.tostring(mapped[key]) for key in official)
    assert json.loads((out / "inputs/scenario.json").read_bytes())["network_sha256"] == result["candidate"]["sha256"]
    final = run_sumo_load_audit(net_file=out / "inputs/network.net.xml", output_dir=out / "final-load",
                              sumo_binary=str(binaries / "sumo.exe"))
    assert final["status"] == "pass"
    from collections import Counter
    from astra_sumo.core.hamburg_corridor_pruning import prepare_corridor_demand

    prepared = out / "calibration-inputs"
    proposal = prepare_corridor_demand(repository=root, corridor_dir=out, output_dir=prepared)
    assert sum(row["vehicles"] for row in proposal["allocations"]) == 8640
    counts = Counter()
    for row in proposal["allocations"]:
        counts[row["entry"], row["begin_s"]] += row["vehicles"]
    assert all(count <= proposal["entry_prior_per_900s"][entry] for (entry, _begin), count in counts.items())
    new_vehicles = {row.get("id"): row for row in ET.parse(prepared / "demand.rou.xml").getroot().findall("vehicle")}
    old_vehicles = {row.get("id"): row for row in ET.parse(out / "inputs/demand.rou.xml").getroot().findall("vehicle")}
    assert set(new_vehicles) == set(old_vehicles) and len(new_vehicles) == 8640
    assert all(row.get("depart") == old_vehicles[identity].get("depart") for identity, row in new_vehicles.items())
    assert (source / "expected-counts.csv").read_bytes() == (prepared / "expected-counts.csv").read_bytes()


def test_corridor_calibration_does_not_fit_after_a_collision(tmp_path, monkeypatch):
    from astra_sumo.core.hamburg_corridor_calibration import run_corridor_calibration

    prepared, figures = tmp_path / "prepared", tmp_path / "figures"
    prepared.mkdir()
    figures.mkdir()
    (prepared / "scenario.json").write_text("{}")
    monkeypatch.setattr("astra_sumo.core.hamburg_corridor_calibration.run_point_detector_demand",
        lambda **options: {"native_status": "pass", "observer_complete": True,
            "stall_monitor": {"status": "completed"}, "completion": {"collisions": 1, "teleports": 0, "discarded": 0},
            "exact_count_fit": False})

    def summary(**options):
        out = options["output_dir"]
        out.mkdir()
        for name in ("field-comparison.csv", "station-comparison.csv", "passage-reconciliation.json", "vehicle-field-passages.csv"):
            (out / name).write_text("retained")
        return {"diagnostic_field_MAE": 1, "diagnostic_max_abs_error": 1}

    def forbidden(**options):
        pytest.fail("An unsafe native seed must not start a count-fitting loop")

    monkeypatch.setattr("astra_sumo.core.hamburg_corridor_calibration.summarize_hamburg_readme", summary)
    monkeypatch.setattr("astra_sumo.core.hamburg_corridor_calibration.calibrate_hamburg_native_counts", forbidden)
    result = run_corridor_calibration(repository=Path(__file__).parents[1], prepared_dir=prepared, figure_dir=figures,
        output_dir=tmp_path / "run", sumo_binary="unused")
    assert result["feedback"] is None and not result["formal_result"]
