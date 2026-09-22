from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from torii_sumo.core.detector_demand import (
    Detector,
    active_detectors,
    build_detector_anchored_routes,
)
from torii_sumo.tools.demand_tools import (
    sumo_detector_count_audit,
    sumo_detector_count_constraints,
    sumo_detector_route_support,
)


PRIVATE_MARKERS = ["private_project_name", "private_sensor_vendor", "closed_bridge_name"]


def test_active_detectors_excludes_review_only_mappings() -> None:
    active = Detector("active", "test", "", "e", "e_0", 0.0, "900", "high", "active")
    review = Detector(
        "review",
        "test",
        "",
        "e",
        "e_0",
        0.0,
        "900",
        "low",
        "needs_review",
    )

    assert active_detectors([active, review]) == [active]


def test_detector_anchored_routes_include_reachable_turning_exits() -> None:
    detector = Detector(
        detector_id="approach",
        source_system="test",
        direction="inbound",
        edge_id="measured",
        lane_id="measured_0",
        lane_position=10.0,
        period="900",
        mapping_confidence="high",
        mapping_status="active",
    )
    routes = build_detector_anchored_routes(
        [detector],
        sources=["source"],
        sinks=["left_sink", "right_sink"],
        connections={
            "source": {"measured"},
            "measured": {"left_sink", "right_sink"},
        },
        max_hops=8,
    )

    assert {route.edges for route in routes} == {
        ("source", "measured", "left_sink"),
        ("source", "measured", "right_sink"),
    }


def _write_synthetic_net(path: Path) -> None:
    path.write_text(
        """<net>
    <edge id="entry_a" from="n0" to="n1">
        <lane id="entry_a_0" index="0" allow="passenger" length="120.0"/>
    </edge>
    <edge id="detector_edge" from="n1" to="n2">
        <lane id="detector_edge_0" index="0" allow="passenger" length="80.0"/>
    </edge>
    <edge id="exit_a" from="n2" to="n3">
        <lane id="exit_a_0" index="0" allow="passenger" length="120.0"/>
    </edge>
    <connection from="entry_a" to="detector_edge"/>
    <connection from="detector_edge" to="exit_a"/>
</net>""",
        encoding="utf-8",
    )


def _write_detector_mapping(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "detector_id",
                "source_system",
                "real_direction",
                "sumo_edge",
                "sumo_lane",
                "lane_position",
                "period",
                "mapping_confidence",
                "mapping_status",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "detector_id": "det_northbound",
                "source_system": "synthetic_loop",
                "real_direction": "northbound",
                "sumo_edge": "detector_edge",
                "sumo_lane": "detector_edge_0",
                "lane_position": "40.0",
                "period": "3600",
                "mapping_confidence": "synthetic",
                "mapping_status": "active",
            }
        )


def _assert_outputs_are_sanitized(output_dir: Path) -> None:
    for path in output_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".csv", ".json", ".xml"}:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in PRIVATE_MARKERS:
            assert marker not in text
        assert str(Path.home()) not in text
        assert re.search(r"[A-Za-z]:\\", text) is None


def test_route_support_tool_builds_sanitized_detector_anchored_routes(tmp_path: Path) -> None:
    net_file = tmp_path / "network.net.xml"
    mapping_file = tmp_path / "detectors.csv"
    output_dir = tmp_path / "route_support"
    _write_synthetic_net(net_file)
    _write_detector_mapping(mapping_file)

    report = sumo_detector_route_support(
        net_file=str(net_file),
        detector_mapping_csv=str(mapping_file),
        output_dir=str(output_dir),
        prefix="synthetic",
        max_routes=20,
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"
    assert report["covered_detector_count"] == 1
    assert report["active_detector_count"] == 1
    assert report["route_count"] >= 1

    routes_file = Path(str(report["route_candidate_manifest"]))
    coverage_file = Path(str(report["route_detector_incidence"]))
    assert routes_file.is_file()
    assert coverage_file.is_file()
    assert "detector_route_det_northbound" in routes_file.read_text(encoding="utf-8")
    assert "det_northbound" in coverage_file.read_text(encoding="utf-8")
    _assert_outputs_are_sanitized(output_dir)
    json.dumps(report)


def test_count_constraint_tool_writes_edge_data_without_private_fields(tmp_path: Path) -> None:
    counts_file = tmp_path / "expected_counts.csv"
    output_dir = tmp_path / "constraints"
    with counts_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["begin", "end", "detector_id", "edge_id", "lane_id", "expected_total"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "begin": "0",
                "end": "3600",
                "detector_id": "det_northbound",
                "edge_id": "detector_edge",
                "lane_id": "detector_edge_0",
                "expected_total": "7",
            }
        )

    report = sumo_detector_count_constraints(
        expected_counts_csv=str(counts_file),
        output_dir=str(output_dir),
        prefix="synthetic",
        begin=0,
        end=3600,
    )

    assert report["status"] == "pass"
    assert report["edge_count"] == 1
    assert report["expected_total"] == 7
    edge_data = Path(str(report["edge_data_file"])).read_text(encoding="utf-8")
    assert '<edge id="detector_edge" entered="7"' in edge_data
    _assert_outputs_are_sanitized(output_dir)
    json.dumps(report)


def test_route_support_tool_rejects_empty_active_detector_mapping(tmp_path: Path) -> None:
    net_file = tmp_path / "network.net.xml"
    mapping_file = tmp_path / "detectors.csv"
    output_dir = tmp_path / "route_support"
    _write_synthetic_net(net_file)
    with mapping_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "detector_id",
                "sumo_edge",
                "sumo_lane",
                "mapping_status",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "detector_id": "inactive_detector",
                "sumo_edge": "detector_edge",
                "sumo_lane": "detector_edge_0",
                "mapping_status": "inactive",
            }
        )

    report = sumo_detector_route_support(
        net_file=str(net_file),
        detector_mapping_csv=str(mapping_file),
        output_dir=str(output_dir),
        prefix="synthetic",
    )

    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert report["active_detector_count"] == 0
    assert "no active detectors" in " ".join(report["warnings"])


def test_count_audit_tool_compares_expected_counts_to_e1_output(tmp_path: Path) -> None:
    expected_file = tmp_path / "expected_counts.csv"
    detector_xml = tmp_path / "e1.xml"
    output_dir = tmp_path / "audit"
    with expected_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["begin", "end", "detector_id", "edge_id", "expected_total"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "begin": "0",
                "end": "3600",
                "detector_id": "det_northbound",
                "edge_id": "detector_edge",
                "expected_total": "5",
            }
        )
        writer.writerow(
            {
                "begin": "3600",
                "end": "7200",
                "detector_id": "det_northbound",
                "edge_id": "detector_edge",
                "expected_total": "7",
            }
        )
    detector_xml.write_text(
        """<detector>
    <interval id="det_northbound" begin="0" end="3600" nVehEntered="4"/>
    <interval id="det_northbound" begin="3600" end="7200" nVehEntered="8"/>
</detector>""",
        encoding="utf-8",
    )

    report = sumo_detector_count_audit(
        expected_counts_csv=str(expected_file),
        detector_output_xml=str(detector_xml),
        output_dir=str(output_dir),
        prefix="synthetic",
    )

    assert report["status"] == "pass"
    assert report["edge_rows"] == 2
    assert report["expected_total"] == 12
    assert report["measured_total"] == 12
    assert report["MAE"] == 1.0
    comparison = Path(str(report["comparison_file"])).read_text(encoding="utf-8")
    assert "diff_entered_minus_expected" in comparison
    _assert_outputs_are_sanitized(output_dir)
    json.dumps(report)


def test_default_count_audit_keeps_missing_intervals_out_of_metrics(tmp_path: Path) -> None:
    from torii_sumo.mcp_contract_tools import torii_demand_audit

    expected = tmp_path / "expected.csv"
    detector = tmp_path / "e1.xml"
    expected.write_text("detector_id,begin,end,expected_total\nd,0,900,0\n", encoding="utf-8")
    detector.write_text("<detector/>", encoding="utf-8")
    result = torii_demand_audit(str(expected), str(detector), str(tmp_path / "audit"))
    assert result.status == "review_required"
    report = result.payload
    assert report["measurement_attribute"] == "nVehEntered"
    assert report["missing_measurement_rows"] == 1
    assert report["edge_rows"] == report["matched_rows"] == 0
    assert report["measured_total"] is None
    assert report["MAE"] is report["RMSE"] is report["GEH_lt5_percent"] is None
    with Path(report["comparison_file"]).open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["measurement_status"] == "missing"
    assert row["measured_nVehEntered"] == row["diff_entered_minus_expected"] == ""


def test_count_audit_preserves_entered_count_and_marks_missing_rows(tmp_path: Path) -> None:
    expected = tmp_path / "expected.csv"
    detector = tmp_path / "e1.xml"
    expected.write_text("detector_id,begin,end,expected_total\nd,0,900,8\nmissing,0,900,12\n", encoding="utf-8")
    detector.write_text('<detector><interval id="d" begin="0" end="900" nVehEntered="8" nVehContrib="3"/></detector>', encoding="utf-8")
    report = sumo_detector_count_audit(str(expected), str(detector), str(tmp_path / "audit"))
    assert report["status"] == "review_required"
    assert report["measurement_attribute"] == "nVehEntered"  # Preserve the public default; do not silently switch counts.
    assert report["expected_rows"] == 2
    assert report["matched_rows"] == report["missing_measurement_rows"] == 1
    assert report["requested_expected_total"] == 20
    assert report["expected_total"] == report["measured_total"] == 8
    assert report["MAE"] == report["RMSE"] == 0
    assert report["metric_scope"] == "matched_detector_intervals_only"


def test_count_audit_rejects_duplicate_or_incomplete_e1_intervals(tmp_path: Path) -> None:
    expected = tmp_path / "expected.csv"
    detector = tmp_path / "e1.xml"
    expected.write_text("detector_id,begin,end,expected_total\nd,0,900,0\n", encoding="utf-8")
    entered = '<interval id="d" begin="0" end="900" nVehEntered="0"/>'
    for index, content in enumerate((entered + entered, '<interval id="d" begin="0" end="900" nVehContrib="0"/>')):
        detector.write_text(f"<detector>{content}</detector>", encoding="utf-8")
        report = sumo_detector_count_audit(str(expected), str(detector), str(tmp_path / f"audit-{index}"))
        assert report["status"] == "fail"


def test_count_audit_cannot_replace_its_expected_input(tmp_path):
    source = tmp_path / "detector_demand_detector_comparison.csv"
    source.write_text("detector_id,begin,end,expected_total\nd,0,900,0\n", encoding="utf-8")
    before = source.read_bytes()
    detector = tmp_path / "detector.xml"
    detector.write_text('<detector><interval id="d" begin="0" end="900" nVehEntered="0"/></detector>', encoding="utf-8")
    report = sumo_detector_count_audit(str(source), str(detector), str(tmp_path))
    assert report["status"] == "fail"
    assert source.read_bytes() == before
