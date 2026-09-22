import json
from pathlib import Path

import pytest

from torii_sumo import cli
from torii_sumo.core.digital_twin import CanonicalCount
from torii_sumo.core.digital_twin_mapping import DetectorMapping
from torii_sumo.core.hamburg_aerial_demand import (
    _build_official_station_edge_flows,
    _diversify_equivalent_routes,
    _scenario_group_policies,
)
from torii_sumo.core.hamburg_sensor_twin import build_station_detector_bank


def _mapping(stream_id: int, edge: str, lane: str, position: float) -> DetectorMapping:
    return DetectorMapping(
        detector_id=f"field_{stream_id}",
        stream_id=stream_id,
        node_id="1",
        asset_id=f"Z.{stream_id}",
        real_direction="east",
        lane_use="",
        longitude=10.0,
        latitude=53.5,
        official_map_lane=str(stream_id),
        official_map_distance_m=0.0,
        sumo_edge=edge,
        sumo_lane=lane,
        lane_position=position,
        distance_m=0.0,
        heading_error_deg=0.0,
        period=900,
        mapping_confidence="high",
        mapping_status="active",
        mapping_reason="test",
    )


def test_station_detector_bank_keeps_physical_field_projections(tmp_path: Path) -> None:
    net = tmp_path / "network.net.xml"
    net.write_text(
        """<net>
        <edge id="a"><lane id="a_0" length="20"/></edge>
        <edge id="b"><lane id="b_0" length="20"/></edge>
        <edge id="joined"><lane id="joined_0" length="30"/><lane id="joined_1" length="30"/></edge>
        <edge id="direct"><lane id="direct_0" length="40"/><lane id="direct_1" length="40"/></edge>
        <edge id="c"><lane id="c_0" length="20"/></edge>
        <edge id="d"><lane id="d_0" length="20"/></edge>
        <edge id="extra"><lane id="extra_0" length="20"/></edge>
        <edge id="joined2"><lane id="joined2_0" length="30"/></edge>
        <connection from="a" to="joined"/><connection from="b" to="joined"/>
        <connection from="c" fromLane="0" to="joined2"/><connection from="d" fromLane="0" to="joined2"/>
        <connection from="extra" fromLane="0" to="joined2"/>
        </net>""",
        encoding="utf-8",
    )
    groups = [
        {
            "station_stream_id": 10,
            "node_id": "1",
            "direction": "east",
            "member_stream_ids": [1, 2],
            "sumo_edges": ["a", "b"],
            "constraint_edge": "joined",
            "constraint_resolution": "unique_common_successor",
        },
        {
            "station_stream_id": 20,
            "node_id": "2",
            "direction": "west",
            "member_stream_ids": [3, 4],
            "sumo_edges": ["direct"],
            "constraint_edge": "direct",
            "constraint_resolution": "direct_member_edge",
        },
        {
            "station_stream_id": 30,
            "node_id": "3",
            "direction": "north",
            "member_stream_ids": [5, 6, 7],
            "sumo_edges": ["c", "d"],
            "constraint_edge": "joined2",
            "constraint_resolution": "unique_common_successor",
        },
    ]

    bank = build_station_detector_bank(
        net,
        groups,
        [
            _mapping(1, "a", "a_0", 5),
            _mapping(2, "b", "b_0", 5),
            _mapping(3, "direct", "direct_0", 12),
            _mapping(4, "direct", "direct_1", 13),
            _mapping(5, "c", "c_0", 5),
            _mapping(6, "d", "d_0", 5),
            _mapping(7, "d", "d_0", 6),
        ],
    )

    by_station = {row["station_stream_id"]: row for row in bank["station_groups"]}
    assert by_station[10]["placement_basis"] == "physical_field_projections"
    assert by_station[10]["detector_ids"] == ["field_1", "field_2"]
    assert by_station[20]["placement_basis"] == "physical_field_projections"
    assert by_station[20]["detector_ids"] == ["field_3", "field_4"]
    assert by_station[30]["placement_basis"] == "physical_field_projections"
    assert by_station[30]["detector_ids"] == ["field_5", "station_30_d_0"]
    grouped = next(
        row for row in by_station[30]["detector_sources"] if row["detector_id"] == "station_30_d_0"
    )
    assert grouped["lane_position"] == 6
    assert grouped["source_stream_ids"] == [6, 7]


def test_official_station_compositions_are_always_summed() -> None:
    groups = [
        {
            "node_id": "535",
            "sumo_lane": "edge_0",
            "group_status": "active",
            "aggregation": "sum_official_zusammensetzung",
        }
    ]

    assert _scenario_group_policies(groups, "conservative") == {}
    assert _scenario_group_policies(groups, "medium") == {}
    assert _scenario_group_policies(groups, "high") == {}

    with pytest.raises(ValueError, match="official station compositions"):
        _scenario_group_policies(
            [{"node_id": "535", "sumo_lane": "edge_0", "group_status": "compressed"}],
            "conservative",
        )


def test_station_edge_flow_sums_published_composition_members() -> None:
    def count(stream_id: int, value: int) -> CanonicalCount:
        return CanonicalCount(
            detector_id=f"d{stream_id}",
            stream_id=stream_id,
            node_id="535",
            asset_id=f"Z.{stream_id}",
            direction="Richtung 1",
            lane_use="",
            longitude=10.0,
            latitude=53.5,
            source_begin_utc=None,  # type: ignore[arg-type]
            source_end_utc=None,  # type: ignore[arg-type]
            begin=0,
            end=900,
            count=value,
            source_observation_count=3,
            expected_source_observation_count=3,
            quality_status="complete",
        )

    flows = _build_official_station_edge_flows(
        [
            {
                "station_stream_id": 101,
                "node_id": "535",
                "member_stream_ids": [1, 2],
                "group_status": "active",
                "constraint_edge": "edge",
            }
        ],
        [count(1, 7), count(2, 5)],
    )

    assert len(flows) == 1
    assert flows[0].edge_id == "edge"
    assert flows[0].count == 12
    assert flows[0].detector_ids == ("station_101",)


def test_route_diversification_preserves_constraint_signature(tmp_path) -> None:
    candidates = tmp_path / "candidates.rou.xml"
    candidates.write_text(
        """<routes>
        <route id="a" edges="source_a measured sink_a"/>
        <route id="b" edges="source_b measured sink_b"/>
        <route id="c" edges="source_c other sink_c"/>
        </routes>""",
        encoding="utf-8",
    )
    demand = tmp_path / "demand.rou.xml"
    demand.write_text(
        """<routes>
        <vehicle id="v1" depart="0"><route edges="source_a measured sink_a"/></vehicle>
        <vehicle id="v2" depart="1"><route edges="source_a measured sink_a"/></vehicle>
        </routes>""",
        encoding="utf-8",
    )

    report = _diversify_equivalent_routes(candidates, demand, {"measured"})

    assert report["used_path_count_before"] == 1
    assert report["used_path_count_after"] == 2
    assert report["constraint_counts_preserved"] is True


def test_cli_routes_hamburg_aerial_demand_generation(monkeypatch, tmp_path, capsys) -> None:
    request = tmp_path / "request.json"
    request.write_text("{}", encoding="utf-8")
    output = tmp_path / "out"

    def fake_build(*, request_file, output_dir):
        assert request_file == str(request)
        assert output_dir == str(output)
        return {"status": "pass", "decision": "review_required"}

    monkeypatch.setattr(
        cli,
        "generate_hamburg_aerial_demand",
        fake_build,
        raising=False,
    )

    exit_code = cli.main(
        ["hamburg", "generate-aerial-demand", str(request), str(output), "--json"]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "review_required"
