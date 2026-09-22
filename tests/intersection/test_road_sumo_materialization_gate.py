from __future__ import annotations

import copy

import pytest

from torii_sumo.intersection.road_sumo_materialization_gate import (
    ROAD_SUMO_MATERIALIZATION_GATE_SCHEMA,
    gate_road_sumo_connection_intents_for_materialization,
)


SUMO_SHA = "b" * 64


def _binding() -> dict[str, object]:
    return {
        "schema": "torii.intersection-road-sumo-binding/v1",
        "road_sumo_binding_id": "binding-fixture",
        "automatic_promotion_gate": "blocked",
        "lineage_source_sha256_binding": {
            "osm_source_sha256": "a" * 64,
            "sumo_source_sha256": SUMO_SHA,
            "status": "pass",
        },
        "connection_intents": [
            {
                "connection_intent_id": "intent-west-east",
                "status": "ready_for_lane_connection_review",
                "from_trusted_sumo_edge_ids": ["west-in"],
                "to_trusted_sumo_edge_ids": ["east-out"],
            }
        ],
    }


def _planned(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "planned_connection_id": "official-movement-1",
        "from_edge": "west-in",
        "from_lane": 1,
        "to_edge": "east-out",
        "to_lane": 0,
        "evidence": {
            "junction_facing_lane_and_stop_line_binding": ["map-lane:10"],
            "physical_connector_geometry_or_map_evidence": ["map-connection:10-20"],
            "legal_turn_or_signal_control_evidence": ["ocit:K3"],
            "SUMO_owner_and_link_index_decision": ["owner:2394/link:2"],
        },
    }
    result.update(overrides)
    return result


def test_exact_source_and_one_ready_intent_are_required_before_materialization() -> None:
    result = gate_road_sumo_connection_intents_for_materialization(
        _binding(),
        source_sumo_sha256=SUMO_SHA,
        planned_lane_connections=[_planned()],
    )

    assert result["schema"] == ROAD_SUMO_MATERIALIZATION_GATE_SCHEMA
    assert result["status"] == "pass"
    assert result["automatic_promotion_gate"] == "blocked"
    assert result["counts"] == {
        "planned_lane_connection_count": 1,
        "passed_lane_connection_count": 1,
        "blocked_lane_connection_count": 0,
        "ready_connection_intent_count": 1,
    }
    assert result["planned_lane_connections"][0]["matching_connection_intent_ids"] == [
        "intent-west-east"
    ]


def test_wrong_source_snapshot_blocks_even_if_the_edge_pair_is_covered() -> None:
    result = gate_road_sumo_connection_intents_for_materialization(
        _binding(),
        source_sumo_sha256="c" * 64,
        planned_lane_connections=[_planned()],
    )

    assert result["status"] == "blocked"
    assert result["source_snapshot_binding_status"] == "blocked"
    assert "binding_source_sumo_sha256_mismatch" in result["blocking_reasons"]


def test_uncovered_or_ambiguous_edge_pairs_fail_closed() -> None:
    uncovered = gate_road_sumo_connection_intents_for_materialization(
        _binding(),
        source_sumo_sha256=SUMO_SHA,
        planned_lane_connections=[_planned(to_edge="south-out")],
    )
    ambiguous_binding = copy.deepcopy(_binding())
    ambiguous_binding["connection_intents"].append(
        {
            "connection_intent_id": "intent-west-east-duplicate",
            "status": "ready_for_lane_connection_review",
            "from_trusted_sumo_edge_ids": ["west-in"],
            "to_trusted_sumo_edge_ids": ["east-out"],
        }
    )
    ambiguous = gate_road_sumo_connection_intents_for_materialization(
        ambiguous_binding,
        source_sumo_sha256=SUMO_SHA,
        planned_lane_connections=[_planned()],
    )

    assert uncovered["status"] == "blocked"
    assert uncovered["planned_lane_connections"][0]["blocking_reasons"] == [
        "no_ready_connection_intent_covers_directed_edge_pair"
    ]
    assert ambiguous["status"] == "blocked"
    assert ambiguous["planned_lane_connections"][0]["blocking_reasons"] == [
        "multiple_ready_connection_intents_cover_directed_edge_pair"
    ]


def test_each_lane_level_plan_must_carry_all_intent_evidence_classes() -> None:
    planned = _planned()
    planned["evidence"] = {
        "junction_facing_lane_and_stop_line_binding": ["map-lane:10"],
        "physical_connector_geometry_or_map_evidence": [],
        "legal_turn_or_signal_control_evidence": "",
        "SUMO_owner_and_link_index_decision": ["owner:2394/link:2"],
    }

    result = gate_road_sumo_connection_intents_for_materialization(
        _binding(),
        source_sumo_sha256=SUMO_SHA,
        planned_lane_connections=[planned],
    )

    assert result["status"] == "blocked"
    row = result["planned_lane_connections"][0]
    assert row["missing_evidence_classes"] == [
        "physical_connector_geometry_or_map_evidence",
        "legal_turn_or_signal_control_evidence",
    ]


def test_binding_without_the_required_schema_or_frozen_promotion_gate_is_rejected() -> None:
    invalid = _binding()
    invalid["automatic_promotion_gate"] = "pass"

    with pytest.raises(ValueError, match="automatic_promotion_gate=blocked"):
        gate_road_sumo_connection_intents_for_materialization(
            invalid,
            source_sumo_sha256=SUMO_SHA,
            planned_lane_connections=[_planned()],
        )
