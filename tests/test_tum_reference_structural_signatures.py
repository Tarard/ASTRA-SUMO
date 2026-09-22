from pathlib import Path

from torii_sumo.core.junction_teacher_model import extract_junction_pattern_exemplar, extract_teacher_junction_model


REFERENCE_NET = Path("examples/02_one_prompt_osm_network/networks/tum_ingolstadt_center_reference.net.xml")


def test_tum_reference_records_internal_tls_and_pedestrian_structures() -> None:
    model = extract_teacher_junction_model(REFERENCE_NET, "267517510")

    assert model["summary"] == {
        "junction_type": "traffic_light",
        "incoming_vehicle_edge_count": 4,
        "outgoing_vehicle_edge_count": 4,
        "vehicle_connection_count": 10,
        "internal_edge_count": 12,
        "internal_junction_count": 4,
        "internal_connection_count": 14,
        "pedestrian_connection_count": 12,
        "crossing_count": 3,
        "walkingarea_count": 3,
        "request_count": 13,
        "tl_phase_count": 6,
        "vehicle_connection_dirs": {"r": 2, "s": 6, "l": 2},
        "internal_mode_counts": {"all": 12, "pedestrian": 6, "bicycle": 2},
    }
    assert model["traffic_light"]["attributes"]["id"] == "267517510"
    assert model["vehicle_connections"][0]["via"].startswith(":267517510_")
    assert model["vehicle_connections"][0]["tl"] == "267517510"
    assert model["vehicle_connections"][0]["linkIndex"] == "0"
    assert model["internal_connections"][0]["from"].startswith(":267517510_")
    assert [edge["edge_id"] for edge in model["crossings"]] == [
        ":267517510_c0",
        ":267517510_c1",
        ":267517510_c2",
    ]
    assert [edge["edge_id"] for edge in model["walking_areas"]] == [
        ":267517510_w0",
        ":267517510_w1",
        ":267517510_w2",
    ]


def test_tum_reference_keeps_priority_internal_and_pedestrian_layers_separate() -> None:
    priority_with_walk = extract_teacher_junction_model(REFERENCE_NET, "1025587626")
    priority_vehicle_only = extract_teacher_junction_model(REFERENCE_NET, "2290821380")

    assert priority_with_walk["summary"]["junction_type"] == "priority"
    assert priority_with_walk["summary"]["tl_phase_count"] == 0
    assert priority_with_walk["summary"]["vehicle_connection_count"] == 8
    assert priority_with_walk["summary"]["pedestrian_connection_count"] == 2
    assert [edge["edge_id"] for edge in priority_with_walk["walking_areas"]] == [":1025587626_w1"]
    assert priority_with_walk["vehicle_connections"][0]["via"].startswith(":1025587626_")
    assert priority_with_walk["traffic_light"]["attributes"] == {}

    assert priority_vehicle_only["summary"]["junction_type"] == "priority"
    assert priority_vehicle_only["summary"]["vehicle_connection_dirs"] == {"r": 2, "l": 2, "s": 2, "t": 3}
    assert priority_vehicle_only["summary"]["crossing_count"] == 0
    assert priority_vehicle_only["summary"]["walkingarea_count"] == 0
    assert priority_vehicle_only["summary"]["internal_junction_count"] == 3
    assert priority_vehicle_only["vehicle_connections"][0]["via"].startswith(":2290821380_")


def test_tum_reference_pattern_exemplars_keep_generic_teacher_signals() -> None:
    priority_three_way = extract_junction_pattern_exemplar(REFERENCE_NET, "1433119620")
    tls_three_way = extract_junction_pattern_exemplar(REFERENCE_NET, "267380207")
    tls_four_way = extract_junction_pattern_exemplar(REFERENCE_NET, "267517510")

    assert priority_three_way["pattern_family"] == "three_way"
    assert priority_three_way["summary"]["junction_type"] == "priority"
    assert priority_three_way["summary"]["crossing_count"] == 1
    assert priority_three_way["summary"]["walkingarea_count"] == 2
    assert priority_three_way["summary"]["tl_phase_count"] == 0
    assert len(priority_three_way["movement_signatures"]) == 9
    assert all(not movement["controlled"] for movement in priority_three_way["movement_signatures"])
    assert all(movement["has_internal_via"] for movement in priority_three_way["movement_signatures"])

    assert tls_three_way["pattern_family"] == "three_way"
    assert tls_three_way["summary"]["junction_type"] == "traffic_light"
    assert tls_three_way["summary"]["crossing_count"] == 4
    assert tls_three_way["summary"]["walkingarea_count"] == 4
    assert tls_three_way["summary"]["tl_phase_count"] == 11
    assert len(tls_three_way["movement_signatures"]) == 8
    assert all(movement["controlled"] for movement in tls_three_way["movement_signatures"])
    assert all(movement["has_internal_via"] for movement in tls_three_way["movement_signatures"])

    assert tls_four_way["pattern_family"] == "four_way"
    assert tls_four_way["summary"]["junction_type"] == "traffic_light"
    assert tls_four_way["summary"]["crossing_count"] == 3
    assert tls_four_way["summary"]["walkingarea_count"] == 3
    assert tls_four_way["summary"]["tl_phase_count"] == 6
    assert len(tls_four_way["movement_signatures"]) == 10
    assert all(movement["controlled"] for movement in tls_four_way["movement_signatures"])
    assert all(movement["has_internal_via"] for movement in tls_four_way["movement_signatures"])
