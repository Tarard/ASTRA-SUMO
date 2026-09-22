import json

from torii_sumo import cli
from torii_sumo.core.digital_twin import CountStream
from torii_sumo.core.digital_twin_mapping import DetectorMapping
from torii_sumo.core.hamburg_aerial_count import (
    enforce_official_station_compositions,
    reconcile_count_binding_groups,
    resolve_station_group_constraint_edges,
)


def _mapping(
    stream_id: int,
    *,
    direction: str,
    map_lane: str,
    sumo_lane: str,
    sumo_edge: str = "e",
) -> DetectorMapping:
    return DetectorMapping(
        detector_id=f"d{stream_id}",
        stream_id=stream_id,
        node_id="535",
        asset_id=f"Z.{stream_id}",
        real_direction=direction,
        lane_use="",
        longitude=10.0,
        latitude=53.5,
        official_map_lane=map_lane,
        official_map_distance_m=1.0,
        sumo_edge=sumo_edge,
        sumo_lane=sumo_lane,
        lane_position=10.0,
        distance_m=1.0,
        heading_error_deg=0.0,
        period=900,
        mapping_confidence="high",
        mapping_status="active",
        mapping_reason="test",
    )


def test_count_group_reconciliation_blocks_mixed_direction_but_allows_compression() -> None:
    mappings, groups = reconcile_count_binding_groups(
        [
            _mapping(1, direction="Richtung 1", map_lane="1", sumo_lane="mixed_0"),
            _mapping(2, direction="Richtung 2", map_lane="1", sumo_lane="mixed_0"),
            _mapping(3, direction="Richtung 2", map_lane="13", sumo_lane="compressed_0"),
            _mapping(4, direction="Richtung 2", map_lane="14", sumo_lane="compressed_0"),
        ]
    )

    assert [row.mapping_status for row in mappings[:2]] == ["needs_review", "needs_review"]
    assert [row.mapping_status for row in mappings[2:]] == ["active", "active"]
    assert {row["group_status"] for row in groups} == {"mixed_direction", "compressed"}
    compressed = next(row for row in groups if row["group_status"] == "compressed")
    assert compressed["effective_official_lane_count"] == 2


def test_official_station_composition_excludes_unlisted_fields() -> None:
    fields = [
        CountStream(8, None, "535", "Z.8", "Richtung 1", "", 10.0, 53.5),
        CountStream(14, None, "535", "Z.14", "Richtung 2", "", 10.0, 53.5),
        CountStream(15, None, "535", "Z.15", "Richtung 2", "", 10.0, 53.5),
        CountStream(16, None, "535", "Z.16", "Richtung 2", "", 10.0, 53.5),
    ]
    stations = [
        CountStream(
            101,
            None,
            "535",
            "0331931",
            "Nordost nach Südwest",
            "",
            10.0,
            53.5,
            direction_code="1",
            station_arm="3",
            composition=("0535-Z.8",),
        ),
        CountStream(
            102,
            None,
            "535",
            "0331932",
            "Südwest nach Nordost",
            "",
            10.0,
            53.5,
            direction_code="2",
            station_arm="3",
            composition=("0535-Z.14",),
        ),
    ]
    mappings = [
        _mapping(8, direction="Richtung 1", map_lane="1", sumo_lane="edge_0"),
        _mapping(14, direction="Richtung 2", map_lane="2", sumo_lane="edge_1"),
        _mapping(15, direction="Richtung 2", map_lane="2", sumo_lane="edge_1"),
        _mapping(16, direction="Richtung 2", map_lane="2", sumo_lane="edge_1"),
    ]

    corrected, groups = enforce_official_station_compositions(mappings, fields, stations)

    by_stream = {row.stream_id: row for row in corrected}
    assert by_stream[8].mapping_status == "active"
    assert by_stream[8].real_direction == "Nordost nach Südwest"
    assert by_stream[14].mapping_status == "active"
    assert by_stream[15].mapping_status == "needs_review"
    assert by_stream[16].mapping_status == "needs_review"
    assert {row["station_stream_id"] for row in groups} == {101, 102}
    assert all(row["aggregation"] == "sum_official_zusammensetzung" for row in groups)

    filtered, _groups = enforce_official_station_compositions(mappings, fields, stations)
    reconciled, lane_groups = reconcile_count_binding_groups(filtered)
    final, _groups = enforce_official_station_compositions(reconciled, fields, stations)
    assert {row.stream_id: row.mapping_status for row in final}[8] == "active"
    assert not any(row["group_status"] == "mixed_direction" for row in lane_groups)


def test_split_station_group_uses_unique_common_adjacent_edge(tmp_path) -> None:
    network = tmp_path / "test.net.xml"
    network.write_text(
        """<net>
        <edge id="pred" from="a" to="b"><lane id="pred_0" index="0" speed="10" length="20"/></edge>
        <edge id="e1" from="b" to="j"><lane id="e1_0" index="0" speed="10" length="1"/></edge>
        <edge id="e2" from="b" to="j"><lane id="e2_0" index="0" speed="10" length="1"/></edge>
        <edge id="s1" from="j" to="c"><lane id="s1_0" index="0" speed="10" length="20"/></edge>
        <connection from="pred" to="e1" fromLane="0" toLane="0"/>
        <connection from="pred" to="e2" fromLane="0" toLane="0"/>
        <connection from="e1" to="s1" fromLane="0" toLane="0"/>
        </net>""",
        encoding="utf-8",
    )
    groups = [
        {
            "station_stream_id": 101,
            "node_id": "535",
            "member_stream_ids": [1, 2],
            "sumo_edges": ["e1", "e2"],
            "group_status": "split_edge",
        }
    ]
    mappings = [
        _mapping(1, direction="Richtung 1", map_lane="1", sumo_lane="e1_0", sumo_edge="e1"),
        _mapping(2, direction="Richtung 1", map_lane="2", sumo_lane="e2_0", sumo_edge="e2"),
    ]

    resolved = resolve_station_group_constraint_edges(network, groups, mappings)

    assert resolved[0]["group_status"] == "active"
    assert resolved[0]["constraint_edge"] == "pred"
    assert resolved[0]["constraint_resolution"] == "unique_common_predecessor"


def test_cli_routes_hamburg_aerial_count_binding(monkeypatch, tmp_path, capsys) -> None:
    request = tmp_path / "request.json"
    request.write_text("{}", encoding="utf-8")
    output = tmp_path / "out"

    def fake_build(*, request_file, output_dir):
        assert request_file == str(request)
        assert output_dir == str(output)
        return {"status": "pass", "decision": "review_required"}

    monkeypatch.setattr(
        cli,
        "build_hamburg_aerial_count_binding",
        fake_build,
        raising=False,
    )

    exit_code = cli.main(
        ["hamburg", "bind-aerial-counts", str(request), str(output), "--json"]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "review_required"
