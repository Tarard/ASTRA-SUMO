from torii_sumo.core.modal_aggregation_policy import (
    classify_cluster_modal_policy,
    classify_edge_modal_role,
)
from torii_sumo.road_semantics import classify_modal_role_from_edge


def test_ordinary_urban_vehicle_edges_are_join_core() -> None:
    edge = {"id": "e1", "type": "highway.tertiary", "allow": "passenger bus", "disallow": ""}
    role = classify_edge_modal_role(edge)
    assert role["modal_primary_role"] == "vehicle_core"
    assert role["modal_aggregation_decision"] == "join_core"
    assert role["modal_review_action"] == "safe_vehicle_core_candidate"


def test_service_driveway_is_protected_terminal() -> None:
    edge = {"id": "e1", "type": "highway.service", "name": "service=driveway"}
    role = classify_edge_modal_role(edge)
    assert role["modal_primary_role"] == "service"
    assert role["modal_aggregation_decision"] == "protected_terminal"
    assert role["modal_review_action"] == "exclude_from_join"


def test_railway_is_never_join() -> None:
    edge = {"id": "r1", "type": "railway.tram"}
    role = classify_edge_modal_role(edge)
    assert role["modal_primary_role"] == "rail"
    assert role["modal_aggregation_decision"] == "never_join"


def test_motorway_link_is_never_join_for_urban_aggregation() -> None:
    edge = {"id": "m1", "type": "highway.motorway_link"}
    role = classify_edge_modal_role(edge)
    assert role["modal_primary_role"] == "ramp"
    assert role["modal_aggregation_decision"] == "never_join"


def test_pedestrian_crossing_is_shape_support() -> None:
    edge = {"id": "c1", "function": "crossing", "type": "highway.crossing"}
    role = classify_edge_modal_role(edge)
    assert role["modal_primary_role"] == "pedestrian"
    assert role["modal_aggregation_decision"] == "shape_support"
    assert role["modal_review_action"] == "review_modal_support"


def test_cluster_with_vehicle_core_and_service_terminal_requires_review() -> None:
    policy = classify_cluster_modal_policy(
        internal_edges=[{"id": "e1", "type": "highway.tertiary"}],
        boundary_edges=[
            {"id": "e2", "type": "highway.secondary"},
            {"id": "s1", "type": "highway.service", "name": "service=parking_aisle"},
        ],
    )
    assert policy["modal_aggregation_decision"] == "review_required"
    assert policy["modal_review_action"] == "review_vehicle_core_boundary"
    assert "service_terminal_present" in policy["modal_risk_flags"]


def test_cluster_never_join_primary_role_prefers_hard_blocker() -> None:
    policy = classify_cluster_modal_policy(
        internal_edges=[
            {"id": "e1", "type": "highway.tertiary"},
            {"id": "e2", "type": "highway.secondary"},
        ],
        boundary_edges=[{"id": "r1", "type": "railway.tram"}],
    )
    assert policy["modal_aggregation_decision"] == "never_join"
    assert policy["modal_primary_role"] == "rail"
    assert policy["modal_review_action"] == "exclude_from_join"


def test_shape_support_cluster_gets_modal_support_action() -> None:
    policy = classify_cluster_modal_policy(
        internal_edges=[{"id": "c1", "function": "crossing", "type": "highway.crossing"}],
        boundary_edges=[{"id": "w1", "type": "highway.footway"}],
    )
    assert policy["modal_aggregation_decision"] == "shape_support"
    assert policy["modal_primary_role"] == "pedestrian"
    assert policy["modal_review_action"] == "review_modal_support"


def test_classify_edge_modal_role_agrees_with_shared_semantics_for_common_edges() -> None:
    edges = [
        {"id": "main", "type": "highway.tertiary", "allow": "passenger"},
        {"id": "bike", "type": "highway.cycleway", "allow": "bicycle"},
        {"id": "walk", "type": "highway.footway", "allow": "pedestrian"},
        {"id": "svc", "type": "highway.service", "name": "service=driveway"},
        {"id": "bridge", "type": "highway.primary", "name": "bridge layer=1"},
    ]

    for edge in edges:
        policy_role = classify_edge_modal_role(edge)
        shared_role = classify_modal_role_from_edge(edge)

        assert policy_role["modal_primary_role"] == shared_role.modal_primary_role
        assert policy_role["modal_aggregation_decision"] == shared_role.modal_aggregation_decision
        assert policy_role["modal_review_action"] == shared_role.modal_review_action
        assert policy_role["modal_reason"] == shared_role.modal_reason
        assert tuple(policy_role["modal_risk_flags"]) == shared_role.modal_risk_flags
