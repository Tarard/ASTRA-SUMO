from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from torii_sumo.core.detector_demand import (
    Detector,
    aggregate_edge_counts_by_interval,
    audit_expected_to_e1_strict,
    e1_counts_by_detector_interval_strict,
    read_detector_mapping,
    read_net_lanes,
    validate_detector_lane_positions,
    write_e1_additional,
    write_interval_edge_data,
)


def _detector(
    detector_id: str = "detector_a",
    *,
    edge_id: str = "edge_a",
    lane_id: str = "edge_a_0",
    lane_position: float = 50.0,
) -> Detector:
    return Detector(
        detector_id=detector_id,
        source_system="synthetic",
        direction="eastbound",
        edge_id=edge_id,
        lane_id=lane_id,
        lane_position=lane_position,
        period="300",
        mapping_confidence="synthetic",
        mapping_status="active",
    )


def _write_net(path: Path) -> None:
    path.write_text(
        """<net>
    <edge id="edge_a" from="n0" to="n1">
        <lane id="edge_a_0" index="0" allow="passenger" length="100"/>
        <lane id="edge_a_1" index="1" allow="passenger" length="100"/>
    </edge>
    <edge id="edge_b" from="n1" to="n2">
        <lane id="edge_b_0" index="0" allow="passenger" length="80"/>
    </edge>
</net>""",
        encoding="utf-8",
    )


def test_aggregate_edge_counts_by_interval_preserves_each_bin() -> None:
    rows = [
        {
            "begin": "0",
            "end": "300",
            "edge_id": "edge_a",
            "lane_id": "edge_a_0",
            "detector_id": "detector_a",
            "expected_total": "3",
        },
        {
            "begin": "0",
            "end": "300",
            "edge_id": "edge_a",
            "lane_id": "edge_a_1",
            "detector_id": "detector_b",
            "expected_total": "2",
        },
        {
            "begin": "300",
            "end": "600",
            "edge_id": "edge_a",
            "lane_id": "edge_a_0",
            "detector_id": "detector_a",
            "expected_total": "7",
        },
    ]

    counts = aggregate_edge_counts_by_interval(rows, begin=0, end=600)

    assert [(count.begin, count.end, count.edge_id, count.entered) for count in counts] == [
        (0.0, 300.0, "edge_a", 5),
        (300.0, 600.0, "edge_a", 7),
    ]
    assert counts[0].detector_ids == ("detector_a", "detector_b")
    assert counts[0].lane_ids == ("edge_a_0", "edge_a_1")


def test_write_interval_edge_data_uses_explicit_route_sampler_count_attribute(tmp_path: Path) -> None:
    rows = [
        {
            "begin": "0",
            "end": "300",
            "edge_id": "edge_a",
            "detector_id": "detector_a",
            "expected_total": "4",
        },
        {
            "begin": "300",
            "end": "600",
            "edge_id": "edge_a",
            "detector_id": "detector_a",
            "expected_total": "6",
        },
    ]
    counts = aggregate_edge_counts_by_interval(rows)
    output = tmp_path / "counts.xml"

    write_interval_edge_data(output, counts)

    intervals = ET.parse(output).getroot().findall("interval")
    assert [(item.attrib["begin"], item.attrib["end"]) for item in intervals] == [
        ("0", "300"),
        ("300", "600"),
    ]
    edges = [item.find("edge") for item in intervals]
    assert [edge.attrib for edge in edges if edge is not None] == [
        {"id": "edge_a", "count": "4"},
        {"id": "edge_a", "count": "6"},
    ]


def test_write_e1_additional_validates_lane_and_uses_bin_period(tmp_path: Path) -> None:
    net_file = tmp_path / "network.net.xml"
    additional_file = tmp_path / "detectors.add.xml"
    _write_net(net_file)
    lanes = read_net_lanes(net_file)

    id_mapping = write_e1_additional(
        additional_file,
        [_detector("detector east")],
        lanes=lanes,
        period=300,
        output_file="e1-output.xml",
    )

    assert id_mapping == {"detector east": "detector_east"}
    loop = ET.parse(additional_file).getroot().find("inductionLoop")
    assert loop is not None
    assert loop.attrib == {
        "id": "detector_east",
        "lane": "edge_a_0",
        "pos": "50",
        "period": "300",
        "file": "e1-output.xml",
    }


@pytest.mark.parametrize(
    ("detector", "message"),
    [
        (_detector(lane_id="missing_lane"), "unknown lane"),
        (_detector(edge_id="edge_b"), "belongs to edge"),
        (_detector(lane_position=-0.1), "outside"),
        (_detector(lane_position=100.1), "outside"),
        (_detector(lane_position=float("nan")), "finite"),
    ],
)
def test_detector_lane_position_validation_fails_closed(
    tmp_path: Path,
    detector: Detector,
    message: str,
) -> None:
    net_file = tmp_path / "network.net.xml"
    _write_net(net_file)

    with pytest.raises(ValueError, match=message):
        validate_detector_lane_positions([detector], read_net_lanes(net_file))


def test_safe_id_collisions_fail_closed_for_mapping_and_e1_output(tmp_path: Path) -> None:
    mapping_file = tmp_path / "mapping.csv"
    with mapping_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["detector_id", "edge_id", "lane_id", "lane_position"])
        writer.writeheader()
        writer.writerow(
            {
                "detector_id": "detector east",
                "edge_id": "edge_a",
                "lane_id": "edge_a_0",
                "lane_position": "50",
            }
        )
        writer.writerow(
            {
                "detector_id": "detector@east",
                "edge_id": "edge_a",
                "lane_id": "edge_a_0",
                "lane_position": "50",
            }
        )

    with pytest.raises(ValueError, match="collide after sanitization"):
        read_detector_mapping(mapping_file)

    net_file = tmp_path / "network.net.xml"
    _write_net(net_file)
    with pytest.raises(ValueError, match="collide after sanitization"):
        write_e1_additional(
            tmp_path / "detectors.add.xml",
            [_detector("detector east"), _detector("detector@east")],
            lanes=read_net_lanes(net_file),
            period=300,
            output_file="e1-output.xml",
        )


def test_strict_e1_audit_defaults_to_contrib_and_distinguishes_missing_from_zero(tmp_path: Path) -> None:
    detector_xml = tmp_path / "e1.xml"
    detector_xml.write_text(
        """<detector>
    <interval id="detector_a" begin="0" end="300" nVehContrib="0" nVehEntered="1"/>
</detector>""",
        encoding="utf-8",
    )
    expected_rows = [
        {"detector_id": "detector_a", "edge_id": "edge_a", "begin": "0", "end": "300", "count": "4"},
        {"detector_id": "detector_a", "edge_id": "edge_a", "begin": "300", "end": "600", "count": "5"},
    ]

    comparisons = audit_expected_to_e1_strict(expected_rows, detector_xml)

    assert comparisons[0]["measurement_attribute"] == "nVehContrib"
    assert comparisons[0]["measurement_status"] == "matched"
    assert comparisons[0]["measured_nVehContrib"] == 0
    assert comparisons[0]["diff_nVehContrib_minus_expected"] == -4
    assert comparisons[1]["measurement_status"] == "missing"
    assert comparisons[1]["measured_nVehContrib"] is None
    assert comparisons[1]["diff_nVehContrib_minus_expected"] is None
    assert e1_counts_by_detector_interval_strict(detector_xml, count_attribute="nVehEntered") == {
        ("detector_a", "0", "300"): 1
    }


def test_strict_e1_parser_rejects_missing_count_and_sanitized_id_collision(tmp_path: Path) -> None:
    missing_count = tmp_path / "missing-count.xml"
    missing_count.write_text(
        '<detector><interval id="detector_a" begin="0" end="300" nVehEntered="1"/></detector>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="nVehContrib is required"):
        e1_counts_by_detector_interval_strict(missing_count)

    collision = tmp_path / "collision.xml"
    collision.write_text(
        """<detector>
    <interval id="detector east" begin="0" end="300" nVehContrib="1"/>
    <interval id="detector@east" begin="300" end="600" nVehContrib="2"/>
</detector>""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="collide after sanitization"):
        e1_counts_by_detector_interval_strict(collision)
