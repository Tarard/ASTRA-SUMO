from __future__ import annotations

import csv
import re
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from torii_sumo.core.detector_demand import audit_expected_to_e1_strict, read_csv_rows
from torii_sumo.core.digital_twin import CanonicalCount
from torii_sumo.core.digital_twin_mapping import (
    DetectorMapping,
    aggregate_virtual_counts_to_complete_edge_sections,
    build_virtual_sensor_aggregation,
    write_virtual_detector_mapping,
    write_virtual_e2_additional,
    write_virtual_expected_counts,
)


UTC = timezone.utc


def _mapping(
    stream_id: int,
    *,
    node_id: str = "0228",
    edge_id: str = "west_in",
    lane_id: str = "west_in_0",
    lane_position: float = 90.0,
) -> DetectorMapping:
    return DetectorMapping(
        detector_id=f"official field {stream_id}",
        stream_id=stream_id,
        node_id=node_id,
        asset_id=f"Z.{stream_id}",
        real_direction="Richtung 1",
        lane_use="Geradeaus",
        longitude=9.98,
        latitude=53.54,
        official_map_lane=str(stream_id),
        official_map_distance_m=1.0,
        sumo_edge=edge_id,
        sumo_lane=lane_id,
        lane_position=lane_position,
        distance_m=1.0,
        heading_error_deg=2.0,
        period=900,
        mapping_confidence="high",
        mapping_status="active",
        mapping_reason="test",
    )


def _count(
    stream_id: int,
    count: int,
    *,
    node_id: str = "0228",
    begin: int = 0,
    quality_status: str = "complete",
    source_observation_count: int = 3,
) -> CanonicalCount:
    source_begin = datetime(2026, 7, 11, 8, tzinfo=UTC) + timedelta(seconds=begin)
    return CanonicalCount(
        detector_id=f"official field {stream_id}",
        stream_id=stream_id,
        node_id=node_id,
        asset_id=f"Z.{stream_id}",
        direction="Richtung 1",
        lane_use="Geradeaus",
        longitude=9.98,
        latitude=53.54,
        source_begin_utc=source_begin,
        source_end_utc=source_begin + timedelta(seconds=900),
        begin=begin,
        end=begin + 900,
        count=count,
        source_observation_count=source_observation_count,
        expected_source_observation_count=3,
        quality_status=quality_status,
    )


def test_same_lane_fields_become_one_virtual_detector_and_summed_counts(tmp_path: Path) -> None:
    mappings = [_mapping(2, lane_position=91.0), _mapping(1, lane_position=88.0)]
    counts = [_count(1, 7), _count(2, 5), _count(1, 11, begin=900), _count(2, 3, begin=900)]

    result = build_virtual_sensor_aggregation(
        mappings,
        counts,
        expected_begin=0,
        expected_end=1800,
    )

    assert len(result.groups) == len(result.detectors) == 1
    group = result.groups[0]
    assert group.source_stream_ids == (1, 2)
    assert group.source_detector_ids == ("official field 1", "official field 2")
    assert group.lane_position == 91.0
    assert group.position_rule == "downstream_most_active_source_position_max"
    assert re.fullmatch(r"[A-Za-z0-9_.-]+", group.virtual_detector_id)
    assert [row.expected_total for row in result.expected_counts] == [12, 14]

    mapping_csv = tmp_path / "virtual_mapping.csv"
    expected_csv = tmp_path / "virtual_expected.csv"
    write_virtual_detector_mapping(mapping_csv, result.groups)
    write_virtual_expected_counts(expected_csv, result.expected_counts)
    with mapping_csv.open(encoding="utf-8", newline="") as handle:
        mapping_row = next(csv.DictReader(handle))
    assert mapping_row["source_stream_ids"] == "[1,2]"
    assert mapping_row["source_detector_ids"] == '["official field 1","official field 2"]'

    e1_output = tmp_path / "e1.xml"
    e1_output.write_text(
        "<detector>"
        f'<interval id="{group.virtual_detector_id}" begin="0" end="900" nVehContrib="12"/>'
        f'<interval id="{group.virtual_detector_id}" begin="900" end="1800" nVehContrib="14"/>'
        "</detector>",
        encoding="utf-8",
    )
    audit = audit_expected_to_e1_strict(read_csv_rows(expected_csv), e1_output)
    assert [row["measurement_status"] for row in audit] == ["matched", "matched"]
    assert [row["diff_nVehContrib_minus_expected"] for row in audit] == [0, 0]


def test_same_lane_fields_can_use_max_policy_for_conservative_demand() -> None:
    result = build_virtual_sensor_aggregation(
        [_mapping(1), _mapping(2)],
        [_count(1, 7), _count(2, 5)],
        group_policies={("0228", "west_in_0"): "max"},
    )

    assert result.expected_counts[0].expected_total == 7


def test_distinct_lanes_remain_distinct_virtual_detectors() -> None:
    result = build_virtual_sensor_aggregation(
        [_mapping(1, lane_id="west_in_0"), _mapping(2, lane_id="west_in_1")],
        [_count(1, 7), _count(2, 5)],
    )

    assert len(result.groups) == 2
    assert {group.sumo_lane for group in result.groups} == {"west_in_0", "west_in_1"}
    assert sorted(row.expected_total for row in result.expected_counts) == [5, 7]


def test_route_sampler_edges_require_complete_passenger_lane_cross_section(tmp_path: Path) -> None:
    net_file = tmp_path / "network.net.xml"
    net_file.write_text(
        """<net>
        <edge id="complete" from="a" to="b">
            <lane id="complete_0" index="0" allow="passenger" length="100"/>
            <lane id="complete_1" index="1" allow="passenger" length="100"/>
        </edge>
        <edge id="partial" from="b" to="c">
            <lane id="partial_0" index="0" allow="passenger" length="100"/>
            <lane id="partial_1" index="1" allow="passenger" length="100"/>
        </edge>
        </net>""",
        encoding="utf-8",
    )
    aggregation = build_virtual_sensor_aggregation(
        [
            _mapping(1, edge_id="complete", lane_id="complete_0", lane_position=40),
            _mapping(2, edge_id="complete", lane_id="complete_1", lane_position=42),
            _mapping(3, edge_id="partial", lane_id="partial_0", lane_position=50),
        ],
        [_count(1, 7), _count(2, 5), _count(3, 99)],
    )

    flows, audit = aggregate_virtual_counts_to_complete_edge_sections(net_file, aggregation)

    assert [(flow.edge_id, flow.count) for flow in flows] == [("complete", 12)]
    assert {row.edge_id: row.constraint_status for row in audit} == {
        "complete": "active",
        "partial": "excluded",
    }
    partial = next(row for row in audit if row.edge_id == "partial")
    assert "partial passenger-lane coverage" in partial.constraint_reason


def test_same_sumo_lane_at_different_nodes_is_never_merged() -> None:
    result = build_virtual_sensor_aggregation(
        [_mapping(1, node_id="0228"), _mapping(2, node_id="2421")],
        [_count(1, 7, node_id="0228"), _count(2, 5, node_id="2421")],
    )

    assert len(result.groups) == 2
    assert {group.node_id for group in result.groups} == {"0228", "2421"}
    assert len({group.virtual_detector_id for group in result.groups}) == 2
    assert sorted(row.expected_total for row in result.expected_counts) == [5, 7]


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([_count(1, 7, quality_status="incomplete"), _count(2, 5)], "quality_status"),
        ([_count(1, 7, source_observation_count=2), _count(2, 5)], "incomplete source observations"),
        ([_count(1, 7), _count(2, 5), _count(1, 3, begin=900)], "coverage differs"),
    ],
)
def test_incomplete_virtual_source_rows_fail_closed(rows: list[CanonicalCount], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build_virtual_sensor_aggregation([_mapping(1), _mapping(2)], rows)


def test_virtual_e2_writer_emits_one_queue_detector_for_collapsed_lane(tmp_path: Path) -> None:
    result = build_virtual_sensor_aggregation(
        [_mapping(1, lane_position=80.0), _mapping(2, lane_position=90.0)],
        [_count(1, 7), _count(2, 5)],
    )
    output = tmp_path / "virtual_e2.add.xml"

    write_virtual_e2_additional(output, result.groups)

    detectors = ET.parse(output).getroot().findall("laneAreaDetector")
    assert len(detectors) == 1
    assert detectors[0].attrib["lane"] == "west_in_0"
    assert detectors[0].attrib["id"] == f"queue_{result.groups[0].virtual_detector_id}"


def test_virtual_e2_writer_rejects_same_sumo_lane_reused_across_nodes(tmp_path: Path) -> None:
    result = build_virtual_sensor_aggregation(
        [_mapping(1, node_id="0228"), _mapping(2, node_id="2421")],
        [_count(1, 7, node_id="0228"), _count(2, 5, node_id="2421")],
    )

    with pytest.raises(ValueError, match="belongs to multiple virtual detectors"):
        write_virtual_e2_additional(tmp_path / "virtual_e2.add.xml", result.detectors)


def test_virtual_e2_writer_allows_disjoint_sections_on_one_corridor_lane(tmp_path: Path) -> None:
    result = build_virtual_sensor_aggregation(
        [
            _mapping(1, node_id="0228", lane_position=9.7),
            _mapping(2, node_id="2421", lane_position=185.3),
        ],
        [_count(1, 7, node_id="0228"), _count(2, 5, node_id="2421")],
    )

    output = tmp_path / "virtual_e2.add.xml"
    write_virtual_e2_additional(output, result.detectors)

    detectors = ET.parse(output).getroot().findall("laneAreaDetector")
    assert len(detectors) == 2
    assert {float(detector.attrib["pos"]) for detector in detectors} == {0.0, 85.3}


def test_virtual_detector_id_is_stable_under_input_reordering() -> None:
    mappings = [_mapping(1), _mapping(2)]
    counts = [_count(1, 7), _count(2, 5)]

    forward = build_virtual_sensor_aggregation(mappings, counts)
    reverse = build_virtual_sensor_aggregation(list(reversed(mappings)), list(reversed(counts)))

    assert forward.groups == reverse.groups
    assert forward.expected_counts == reverse.expected_counts


def test_non_active_source_count_fails_instead_of_becoming_zero() -> None:
    inactive = replace(_mapping(2), mapping_status="needs_review")

    with pytest.raises(ValueError, match="does not have an active detector mapping"):
        build_virtual_sensor_aggregation([_mapping(1), inactive], [_count(1, 7), _count(2, 5)])
