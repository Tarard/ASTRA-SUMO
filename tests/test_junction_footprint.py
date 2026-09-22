from torii_sumo.core.junction_footprint import build_lane_buffered_approach_footprint


def test_lane_buffered_footprint_uses_lane_width_not_only_centerline() -> None:
    net = {
        "edges": [
            {
                "id": "west_in",
                "from": "w",
                "to": "core",
                "type": "highway.primary",
                "internal": False,
                "shape": [(-10.0, 0.0), (0.0, 0.0)],
                "lane_count": 2,
                "lane_width": 3.5,
            },
            {
                "id": "east_out",
                "from": "core",
                "to": "e",
                "type": "highway.primary",
                "internal": False,
                "shape": [(0.0, 0.0), (10.0, 0.0)],
                "lane_count": 2,
                "lane_width": 3.5,
            },
            {
                "id": "north_out",
                "from": "core",
                "to": "n",
                "type": "highway.secondary",
                "internal": False,
                "shape": [(0.0, 0.0), (0.0, 10.0)],
                "lane_count": 1,
                "lane_width": 3.2,
            },
        ]
    }

    result = build_lane_buffered_approach_footprint(net, {"core"}, setback_m=5.0)

    assert result["shape_support_edge_ids"] == ["east_out", "north_out", "west_in"]
    assert result["review_support_edge_ids"] == []
    assert result["buffer_point_count"] == 6
    assert max(x for x, _ in result["polygon"]) > 3.0
    assert min(x for x, _ in result["polygon"]) <= -5.0
    assert min(y for _, y in result["polygon"]) < -3.0


def test_lane_buffered_footprint_uses_service_but_reviews_foot_and_cycle_edges() -> None:
    net = {
        "edges": [
            {
                "id": "vehicle",
                "from": "core",
                "to": "e",
                "type": "highway.residential",
                "internal": False,
                "shape": [(0.0, 0.0), (10.0, 0.0)],
                "lane_count": 1,
                "lane_width": 3.2,
            },
            {
                "id": "service",
                "from": "core",
                "to": "s",
                "type": "highway.service",
                "internal": False,
                "shape": [(0.0, 0.0), (0.0, -10.0)],
                "lane_count": 1,
                "lane_width": 2.8,
            },
            {
                "id": "foot",
                "from": "core",
                "to": "p",
                "type": "highway.footway",
                "internal": False,
                "shape": [(0.0, 0.0), (-10.0, 0.0)],
                "lane_count": 1,
                "lane_width": 1.5,
            },
            {
                "id": "cycle",
                "from": "c",
                "to": "core",
                "type": "highway.cycleway",
                "internal": False,
                "shape": [(0.0, 10.0), (0.0, 0.0)],
                "lane_count": 1,
                "lane_width": 1.5,
            },
        ]
    }

    result = build_lane_buffered_approach_footprint(net, {"core"}, setback_m=4.0)

    assert result["shape_support_edge_ids"] == ["service", "vehicle"]
    assert result["review_support_edge_ids"] == ["cycle", "foot"]
    assert result["buffer_point_count"] == 4
    assert all(x >= -2.0 for x, _ in result["polygon"])
