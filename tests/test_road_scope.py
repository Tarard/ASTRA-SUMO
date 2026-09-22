from torii_sumo.core.network_plan import derive_network_plan
from torii_sumo.core.road_scope import resolve_highway_classes


def test_default_motor_network_keeps_side_roads_without_relaxing_permissions():
    assert {"residential", "unclassified", "service"} <= resolve_highway_classes(None)
    plan = derive_network_plan(traffic_layers=["passenger"])
    assert {"residential", "unclassified", "service"} <= set(plan["highway_classes"])
    assert plan["service_passenger_policy"] == "sumo_default"


def test_an_explicit_arterial_only_scope_is_still_respected():
    assert "service" not in resolve_highway_classes("arterial")
    plan = derive_network_plan(highway_classes="arterial", traffic_layers=["passenger"])
    assert "service" not in plan["highway_classes"]
