from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from torii_sumo.core.command_runner import CommandResult
from torii_sumo.core.detector_demand import EdgeInfo, source_sink_rows
from torii_sumo.core.route_sampler import (
    apply_departure_lane_targets,
    apply_vehicle_speed_factors,
    audit_route_constraint_structure,
    run_route_sampler,
    run_route_sampler_ensemble,
    validate_route_sampler_edge_counts,
)


def _single_sampler_inputs(tmp_path: Path) -> dict:
    manifest = tmp_path / "routes.csv"
    counts = tmp_path / "counts.xml"
    script = tmp_path / "routeSampler.py"
    manifest.write_text("route_id,edges\nr0,a b\n", encoding="utf-8")
    counts.write_text('<data><interval begin="0" end="900"><edge id="b" count="1"/></interval></data>', encoding="utf-8")
    script.write_text("# synthetic process input\n", encoding="utf-8")
    return dict(candidate_manifest_csv=manifest, edge_data_file=counts, output_dir=tmp_path / "out",
                prefix="sample", begin=0, end=900, interval=900, route_sampler_script=script)


@pytest.mark.parametrize("suffix", ["candidate_routes.rou.xml", "demand.rou.xml", "route_sampler_mismatch.xml", "route_sampler_command.json"])
def test_single_sampler_rejects_reused_fixed_outputs_before_execution(tmp_path: Path, suffix: str) -> None:
    arguments = _single_sampler_inputs(tmp_path)
    output = arguments["output_dir"]
    output.mkdir()
    stale = output / f"sample_{suffix}"
    stale.write_text("previous run must remain unchanged", encoding="utf-8")
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return CommandResult(command=command, cwd=str(kwargs["cwd"]), status="pass", returncode=0)

    with pytest.raises(ValueError, match="new|exist"):
        run_route_sampler(**arguments, command_runner=runner)
    assert not calls
    assert stale.read_text(encoding="utf-8") == "previous run must remain unchanged"
    assert list(output.iterdir()) == [stale]


@pytest.mark.parametrize("written", [(), ("-o",), ("--mismatch-output",)])
def test_single_sampler_requires_new_demand_and_mismatch_outputs(tmp_path: Path, written: tuple[str, ...]) -> None:
    arguments = _single_sampler_inputs(tmp_path)

    def runner(command, **kwargs):
        for option in written:
            text = '<routes><vehicle id="v0" depart="0"><route edges="a b"/></vehicle></routes>' if option == "-o" else '<data><edge id="b" deficit="0"/></data>'
            Path(command[command.index(option) + 1]).write_text(text, encoding="utf-8")
        return CommandResult(command=command, cwd=str(kwargs["cwd"]), status="pass", returncode=0)

    report = run_route_sampler(**arguments, command_runner=runner)
    assert report["status"] == "fail"
    assert report["missing_output_files"]
    manifest = json.loads(Path(report["command_manifest"]).read_text(encoding="utf-8"))
    assert manifest["output_policy"] == "new_files_only"


def test_single_sampler_refuses_source_output_overlap_without_changing_source(tmp_path: Path) -> None:
    arguments = _single_sampler_inputs(tmp_path)
    output = arguments["output_dir"]
    output.mkdir()
    source = output / "sample_candidate_routes.rou.xml"
    source.write_bytes(arguments["edge_data_file"].read_bytes())
    arguments["edge_data_file"] = source
    before = source.read_bytes()
    with pytest.raises(ValueError, match="source|input|overlap"):
        run_route_sampler(**arguments)
    assert source.read_bytes() == before


def test_single_sampler_rejects_input_changes_during_execution(tmp_path: Path) -> None:
    arguments = _single_sampler_inputs(tmp_path)
    before = arguments["edge_data_file"].read_bytes()

    def runner(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_text('<routes><vehicle id="v0" depart="0"><route edges="a b"/></vehicle></routes>', encoding="utf-8")
        Path(command[command.index("--mismatch-output") + 1]).write_text('<data><edge id="b" deficit="0"/></data>', encoding="utf-8")
        arguments["edge_data_file"].write_bytes(before.replace(b'count="1"', b'count="2"'))
        return CommandResult(command=command, cwd=str(kwargs["cwd"]), status="pass", returncode=0)

    report = run_route_sampler(**arguments, command_runner=runner)
    assert report["status"] == "fail"
    assert report["inputs_unchanged"] is False
    manifest = json.loads(Path(report["command_manifest"]).read_text(encoding="utf-8"))
    assert manifest["inputs_unchanged"] is False
    import hashlib
    assert manifest["inputs"]["edge_data_sha256"] == hashlib.sha256(before).hexdigest()


def test_empty_new_mismatch_is_not_a_perfect_match(tmp_path):
    arguments = _single_sampler_inputs(tmp_path)
    def runner(command, **kwargs):
        Path(command[command.index('-o')+1]).write_text('<routes/>', encoding='utf-8')
        Path(command[command.index('--mismatch-output')+1]).write_text('<data/>', encoding='utf-8')
        return CommandResult(command=command, cwd=str(kwargs['cwd']), status='pass', returncode=0)
    result = run_route_sampler(**arguments, command_runner=runner)
    assert result['status'] != 'pass'
    assert result['constraint_match_fraction'] is None


def test_route_sampler_wrapper_writes_candidates_executes_and_hashes(tmp_path: Path) -> None:
    manifest = tmp_path / "routes.csv"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["route_id", "edges"])
        writer.writeheader()
        writer.writerow({"route_id": "r0", "edges": "a b c"})
    edge_data = tmp_path / "counts.xml"
    root = ET.Element("data")
    for begin in range(0, 1800, 900):
        interval = ET.SubElement(root, "interval", begin=str(begin), end=str(begin + 900))
        ET.SubElement(interval, "edge", id="b", count="4")
    ET.ElementTree(root).write(edge_data, encoding="utf-8", xml_declaration=True)
    script = tmp_path / "routeSampler.py"
    script.write_text("# synthetic routeSampler\n", encoding="utf-8")

    def fake_runner(command: list[str], **kwargs: object) -> CommandResult:
        assert kwargs["cwd"] == tmp_path / "out"
        assert command[command.index("--optimize") + 1] == "full"
        assert command[command.index("--minimize-vehicles") + 1] == "0.9"
        for option in ("-r", "--edgedata-files", "--mismatch-output", "-o"):
            assert Path(command[command.index(option) + 1]).is_absolute()
        output = Path(command[command.index("-o") + 1])
        mismatch = Path(command[command.index("--mismatch-output") + 1])
        output.write_text("<routes><vehicle id='v0' depart='0'><route edges='a b c'/></vehicle></routes>", encoding="utf-8")
        mismatch.write_text("<data><edge id='b' deficit='0'/></data>", encoding="utf-8")
        return CommandResult(command=command, cwd=str(tmp_path), status="pass", returncode=0)

    report = run_route_sampler(
        candidate_manifest_csv=manifest,
        edge_data_file=edge_data,
        output_dir=tmp_path / "out",
        prefix="sample",
        begin=0,
        end=1800,
        interval=900,
        optimize="full",
        minimize_vehicles=0.9,
        route_sampler_script=script,
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert report["candidate_route_count"] == 1
    assert report["demand_vehicle_count"] == 1
    assert Path(str(report["demand_route_file"])).is_file()
    assert report["mismatch"]["absolute_deficit"] == 0
    assert Path(str(report["command_manifest"])).is_file()
    assert report["constraint_structure"]["status"] == "pass"


def test_route_sampler_rejects_minimize_vehicle_factor_at_one(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="less than 1"):
        run_route_sampler(
            candidate_manifest_csv=tmp_path / "missing-routes.csv",
            edge_data_file=tmp_path / "missing-counts.xml",
            output_dir=tmp_path / "out",
            prefix="sample",
            begin=0,
            end=900,
            interval=900,
            minimize_vehicles=1.0,
        )


def test_route_sampler_edge_data_rejects_missing_bin(tmp_path: Path) -> None:
    edge_data = tmp_path / "counts.xml"
    edge_data.write_text("<data><interval begin='0' end='900'><edge id='a' count='1'/></interval></data>", encoding="utf-8")
    try:
        validate_route_sampler_edge_counts(edge_data, begin=0, end=1800, interval=900)
    except ValueError as exc:
        assert "exactly cover" in str(exc)
    else:
        raise AssertionError("missing routeSampler bin was accepted")


def test_route_sampler_reports_nonzero_mismatch_as_partial(tmp_path: Path) -> None:
    manifest = tmp_path / "routes.csv"
    manifest.write_text("route_id,edges\nr0,a b\n", encoding="utf-8")
    edge_data = tmp_path / "counts.xml"
    edge_data.write_text(
        "<data><interval begin='0' end='900'><edge id='a' count='10'/></interval></data>",
        encoding="utf-8",
    )
    script = tmp_path / "routeSampler.py"
    script.write_text("# synthetic routeSampler\n", encoding="utf-8")

    def fake_runner(command: list[str], **_kwargs: object) -> CommandResult:
        Path(command[command.index("-o") + 1]).write_text("<routes/>", encoding="utf-8")
        Path(command[command.index("--mismatch-output") + 1]).write_text(
            "<data><edge id='a' deficit='2'/></data>",
            encoding="utf-8",
        )
        return CommandResult(command=command, cwd=str(tmp_path), status="pass", returncode=0)

    report = run_route_sampler(
        candidate_manifest_csv=manifest,
        edge_data_file=edge_data,
        output_dir=tmp_path / "out",
        prefix="sample",
        begin=0,
        end=900,
        interval=900,
        route_sampler_script=script,
        command_runner=fake_runner,
    )

    assert report["status"] == "partial"
    assert report["constraint_match_fraction"] == 0.8


def test_route_sampler_ensemble_reports_distinct_feasible_allocations(tmp_path: Path) -> None:
    manifest = tmp_path / "routes.csv"
    manifest.write_text("route_id,edges\nr0,a b\nr1,a c\nr2,x y\n", encoding="utf-8")
    edge_data = tmp_path / "counts.xml"
    edge_data.write_text(
        "<data><interval begin='0' end='900'><edge id='a' count='2'/></interval></data>",
        encoding="utf-8",
    )
    script = tmp_path / "routeSampler.py"
    script.write_text("# synthetic routeSampler\n", encoding="utf-8")

    def fake_runner(command: list[str], **_kwargs: object) -> CommandResult:
        seed = int(command[command.index("--seed") + 1])
        edges = ("a b", "a c") if seed != 2 else ("a b", "a b")
        output = Path(command[command.index("-o") + 1])
        mismatch = Path(command[command.index("--mismatch-output") + 1])
        output.write_text(
            "<routes>"
            f"<vehicle id='v0' depart='{seed}'><route edges='{edges[0]}'/></vehicle>"
            f"<vehicle id='v1' depart='{seed + 1}'><route edges='{edges[1]}'/></vehicle>"
            "</routes>",
            encoding="utf-8",
        )
        mismatch.write_text("<data><edge id='a' deficit='0'/></data>", encoding="utf-8")
        return CommandResult(command=command, cwd=str(output.parent), status="pass", returncode=0)

    report = run_route_sampler_ensemble(
        candidate_manifest_csv=manifest,
        edge_data_file=edge_data,
        output_dir=tmp_path / "ensemble",
        prefix="sample",
        seeds=[1, 2, 3],
        begin=0,
        end=900,
        interval=900,
        route_sampler_script=script,
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert report["feasible_solution_count"] == 3
    assert report["distinct_feasible_solution_count"] == 2
    assert report["selection"] is None
    assert report["promotion_decision"] == "review_required"
    assert report["identifiability"]["nullity"] == 2
    assert report["diversity"]["vehicle_count_min"] == 2
    assert report["diversity"]["vehicle_count_max"] == 2
    assert report["diversity"]["pair_count"] == 3
    assert any(row["interval_route_l1_distance"] == 2 for row in report["diversity"]["pairwise"])
    assert all("identifiability" not in run["route_sampler"] for run in report["runs"])
    assert Path(str(report["manifest"])).is_file()


def test_route_sampler_ensemble_requires_empty_versioned_output(tmp_path: Path) -> None:
    output = tmp_path / "ensemble"
    output.mkdir()
    (output / "stale.txt").write_text("stale", encoding="utf-8")

    try:
        run_route_sampler_ensemble(
            candidate_manifest_csv=tmp_path / "missing-routes.csv",
            edge_data_file=tmp_path / "missing-counts.xml",
            output_dir=output,
            prefix="sample",
            seeds=[1],
        )
    except ValueError as exc:
        assert "must be empty" in str(exc)
    else:
        raise AssertionError("non-empty ensemble output was accepted")


def test_route_constraint_structure_reports_identical_path_incidence_conflict(tmp_path: Path) -> None:
    manifest = tmp_path / "routes.csv"
    manifest.write_text(
        "route_id,edges\n"
        "r0,\"a b c\"\n"
        "r1,\"a b d\"\n",
        encoding="utf-8",
    )
    edge_data = tmp_path / "counts.xml"
    edge_data.write_text(
        "<data><interval begin='0' end='900'>"
        "<edge id='a' count='10'/><edge id='b' count='9'/>"
        "</interval></data>",
        encoding="utf-8",
    )

    report = audit_route_constraint_structure(
        manifest,
        edge_data,
        begin=0,
        end=900,
        interval=900,
    )

    assert report["status"] == "fail"
    assert report["measurement_row_count"] == 2
    assert report["candidate_edge_count"] == 4
    assert report["conflict_count"] == 1
    assert report["conflicts"][0]["kind"] == "identical_route_incidence_count_conflict"


def test_route_constraint_structure_reports_positive_uncovered_edge(tmp_path: Path) -> None:
    manifest = tmp_path / "routes.csv"
    manifest.write_text("route_id,edges\nr0,a,b\n", encoding="utf-8")
    edge_data = tmp_path / "counts.xml"
    edge_data.write_text(
        "<data><interval begin='0' end='900'>"
        "<edge id='a' count='0'/><edge id='missing' count='2'/>"
        "</interval></data>",
        encoding="utf-8",
    )

    report = audit_route_constraint_structure(
        manifest,
        edge_data,
        begin=0,
        end=900,
        interval=900,
    )

    assert report["status"] == "fail"
    assert report["conflicts"] == [
        {
            "kind": "positive_count_without_candidate_route",
            "interval_begin": 0,
            "edge": "missing",
            "count": 2,
        }
    ]


def test_source_sink_rows_labels_detector_cross_section_boundaries() -> None:
    edges = {
        "network": EdgeInfo("network", "n0", "n1", True, 20.0),
        "detector": EdgeInfo("detector", "n1", "n2", True, 30.0),
    }

    rows = source_sink_rows(
        edges,
        ["network", "detector"],
        ["detector"],
        measured_edge_ids=["detector"],
    )

    assert [row["reason"] for row in rows] == [
        "network_boundary",
        "official_detector_cross_section",
        "official_detector_cross_section",
    ]


def test_departure_lane_targets_are_written_per_edge_and_bin(tmp_path: Path) -> None:
    demand = tmp_path / "demand.rou.xml"
    demand.write_text(
        "<routes>"
        "<vehicle id='v0' depart='2'><route edges='edge'/></vehicle>"
        "<vehicle id='v1' depart='3'><route edges='edge'/></vehicle>"
        "<vehicle id='v2' depart='901'><route edges='edge'/></vehicle>"
        "</routes>",
        encoding="utf-8",
    )

    report = apply_departure_lane_targets(
        demand,
        {
            ("edge", 0): {"edge_0": 1, "edge_1": 1},
            ("edge", 900): {"edge_1": 1},
        },
        interval=900,
        lane_positions={("edge", "edge_0"): 20.0, ("edge", "edge_1"): 21.0},
    )

    assert report["status"] == "pass"
    root = ET.parse(demand).getroot()
    assert [vehicle.attrib["departLane"] for vehicle in root.findall("vehicle")] == ["0", "1", "1"]
    assert [vehicle.attrib["departPos"] for vehicle in root.findall("vehicle")] == ["19", "20", "20"]


def test_vehicle_speed_factors_require_complete_legal_assignment(tmp_path: Path) -> None:
    demand = tmp_path / "demand.rou.xml"
    demand.write_text(
        "<routes>"
        "<vehicle id='v0' depart='0'><route edges='edge'/></vehicle>"
        "<vehicle id='v1' depart='1'><route edges='edge'/></vehicle>"
        "</routes>",
        encoding="utf-8",
    )

    partial = apply_vehicle_speed_factors(demand, {"v0": 0.95})
    assert partial["status"] == "review_required"
    assert partial["implicit_speed_factor_count"] == 1

    complete = apply_vehicle_speed_factors(demand, {"v1": 1.0})
    assert complete["status"] == "pass"
    assert complete["explicit_speed_factor_count"] == 2
    assert [vehicle.attrib["speedFactor"] for vehicle in ET.parse(demand).getroot().findall("vehicle")] == [
        "0.95",
        "1",
    ]


def test_vehicle_speed_factors_reject_speeding_and_unknown_ids(tmp_path: Path) -> None:
    demand = tmp_path / "demand.rou.xml"
    demand.write_text(
        "<routes><vehicle id='v0' depart='0'><route edges='edge'/></vehicle></routes>",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be finite"):
        apply_vehicle_speed_factors(demand, {"v0": 1.01})
    with pytest.raises(ValueError, match="unknown vehicle"):
        apply_vehicle_speed_factors(demand, {"missing": 0.9})


def test_departure_lane_targets_skip_internal_detector_edges(tmp_path: Path) -> None:
    demand = tmp_path / "demand.rou.xml"
    demand.write_text(
        "<routes>"
        "<vehicle id='v0' depart='2'><route edges='source internal'/></vehicle>"
        "</routes>",
        encoding="utf-8",
    )

    report = apply_departure_lane_targets(
        demand,
        {
            ("source", 0): {"source_0": 1},
            ("internal", 0): {"internal_0": 1},
        },
        interval=900,
        source_edges={"source"},
    )

    assert report["status"] == "review_required"
    assert report["skipped_non_source_bin_count"] == 1
    assert report["skipped_non_source_edges"] == ["internal"]
    assert report["controlled_target_vehicle_count"] == 1
    root = ET.parse(demand).getroot()
    vehicle = root.find("vehicle")
    assert vehicle is not None
    assert vehicle.attrib["departLane"] == "0"


def test_departure_lane_targets_mark_partial_source_coverage_for_review(tmp_path: Path) -> None:
    demand = tmp_path / "demand.rou.xml"
    demand.write_text(
        "<routes>"
        "<vehicle id='v0' depart='2'><route edges='source'/></vehicle>"
        "<vehicle id='v1' depart='3'><route edges='source'/></vehicle>"
        "</routes>",
        encoding="utf-8",
    )

    report = apply_departure_lane_targets(
        demand,
        {("source", 0): {"source_0": 1}},
        interval=900,
        source_edges={"source"},
    )

    assert report["status"] == "review_required"
    assert report["unmatched_bins"] == [
        {"edge_id": "source", "begin": 0, "target_count": 1, "vehicle_count": 2}
    ]
