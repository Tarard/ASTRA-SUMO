from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from torii_sumo.intersection.road_sumo_binding import (
    ROAD_SUMO_BINDING_SCHEMA,
    bind_intersection_road_detail_to_sumo,
)
from torii_sumo.road_network.adapters.osm import read_osm_road_snapshot
from torii_sumo.road_network.adapters.sumo import read_sumo_road_snapshot
from torii_sumo.road_network.conflation import build_osm_sumo_lineage_relations


OSM_SHA = "a" * 64
SUMO_SHA = "b" * 64
FIXTURES = Path(__file__).parents[1] / "fixtures" / "road_network"


def _road_detail() -> dict[str, object]:
    return {
        "schema": "torii.intersection-road-detail/v1",
        "automatic_promotion_gate": "blocked",
        "road_detail_id": "road-detail-fixture",
        "parent_physical_cell_hypothesis_id": "cell-fixture",
        "road_arms": [
            {
                "road_arm_id": "arm-west",
                "physical_approach_id": "approach-west",
                "source_way_ids": ["100"],
            },
            {
                "road_arm_id": "arm-east",
                "physical_approach_id": "approach-east",
                "source_way_ids": ["200"],
            },
        ],
        "connection_relations": [
            {
                "connector_id": "connector-west-east",
                "from_physical_approach_id": "approach-west",
                "to_physical_approach_id": "approach-east",
                "relation": "through_axis",
                "evidence_ids": ["topology-connector-1"],
            }
        ],
    }


def _lineage(*, first_status: str = "pass", second_status: str = "pass") -> dict[str, object]:
    return {
        "schema": "torii.road-network-semantics/v1/conflation-candidates/v1",
        "relation_layer": "osm_to_sumo",
        "automatic_promotion_gate": "blocked",
        "source_sha256_binding": {
            "osm_source_sha256": OSM_SHA,
            "sumo_source_sha256": SUMO_SHA,
            "sumo_declared_imported_source_sha256": SUMO_SHA,
            "status": "pass",
        },
        "relations": [
            {
                "relation_id": "lineage-100",
                "status": first_status,
                "direction": "with",
                "left_refs": [
                    {"namespace": "osm", "object_type": "way", "object_id": "100"}
                ],
                "right_refs": [
                    {"namespace": "sumo", "object_type": "edge", "object_id": "100#0"}
                ],
                "review_reasons": [],
            },
            {
                "relation_id": "lineage-200",
                "status": second_status,
                "direction": "with",
                "left_refs": [
                    {"namespace": "osm", "object_type": "way", "object_id": "200"}
                ],
                "right_refs": [
                    {"namespace": "sumo", "object_type": "edge", "object_id": "200#0"}
                ],
                "review_reasons": [],
            },
        ],
    }


def test_binds_fully_traced_road_arms_and_stops_before_lane_materialization() -> None:
    result = bind_intersection_road_detail_to_sumo(_road_detail(), _lineage())

    assert result["schema"] == ROAD_SUMO_BINDING_SCHEMA
    assert result["status"] == "ready_for_lane_connection_review"
    assert result["automatic_promotion_gate"] == "blocked"
    assert result["counts"] == {
        "road_arm_count": 2,
        "bound_road_arm_count": 2,
        "connection_intent_count": 1,
        "lane_connection_review_ready_count": 1,
    }
    east = result["road_arm_bindings"][0]
    assert east["trusted_sumo_edge_ids"] == ["200#0"]
    assert result["connection_intents"][0]["from_trusted_sumo_edge_ids"] == ["100#0"]
    assert "junction_facing_lane_and_stop_line_binding" in result["connection_intents"][0][
        "required_before_materialization"
    ]


def test_review_status_lineage_does_not_make_a_connection_intent_ready() -> None:
    result = bind_intersection_road_detail_to_sumo(
        _road_detail(),
        _lineage(second_status="review_required"),
    )

    assert result["status"] == "review_required"
    assert result["road_arm_bindings"][0]["binding_status"] == "review_required"
    assert result["connection_intents"][0]["status"] == "blocked"


def test_lineage_with_untrusted_source_hash_binding_is_rejected() -> None:
    lineage = _lineage()
    lineage["source_sha256_binding"] = copy.deepcopy(lineage["source_sha256_binding"])
    lineage["source_sha256_binding"]["status"] = "review_required"

    with pytest.raises(ValueError, match="source_sha256_binding must pass"):
        bind_intersection_road_detail_to_sumo(_road_detail(), lineage)


def test_relation_ref_that_contradicts_the_enclosing_sumo_snapshot_is_rejected() -> None:
    lineage = _lineage()
    lineage["relations"][0]["right_refs"][0]["source_sha256"] = "c" * 64

    with pytest.raises(ValueError, match="does not bind the enclosing source SHA-256"):
        bind_intersection_road_detail_to_sumo(_road_detail(), lineage)


def test_consumes_actual_hash_bound_osm_to_sumo_lineage_report() -> None:
    target_time = datetime(2026, 7, 19, 12, tzinfo=UTC)
    valid_from = datetime(2026, 7, 19, tzinfo=UTC)
    valid_to = datetime(2026, 7, 20, tzinfo=UTC)
    osm = read_osm_road_snapshot(
        FIXTURES / "r1.osm.xml",
        target_time=target_time,
        retrieved_at=target_time,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    sumo = read_sumo_road_snapshot(
        FIXTURES / "r1.net.xml",
        target_time=target_time,
        retrieved_at=target_time,
        valid_from=valid_from,
        valid_to=valid_to,
        imported_from="osm",
        imported_source_sha256=osm["source_snapshot"]["sha256"],
    )
    lineage = build_osm_sumo_lineage_relations(osm, sumo, target_time=target_time)
    detail = _road_detail()
    detail["road_arms"] = [
        {
            "road_arm_id": "arm-observed",
            "physical_approach_id": "approach-observed",
            "source_way_ids": ["100"],
        },
        {
            "road_arm_id": "arm-derived",
            "physical_approach_id": "approach-derived",
            "source_way_ids": ["101"],
        },
    ]
    detail["connection_relations"] = [
        {
            "connector_id": "connector-actual",
            "from_physical_approach_id": "approach-observed",
            "to_physical_approach_id": "approach-derived",
            "relation": "branch_to_axis",
            "evidence_ids": [],
        }
    ]

    result = bind_intersection_road_detail_to_sumo(detail, lineage)

    assert result["lineage_source_sha256_binding"]["sumo_source_sha256"] == sumo[
        "source_snapshot"
    ]["sha256"]
    by_arm = {item["road_arm_id"]: item for item in result["road_arm_bindings"]}
    assert by_arm["arm-observed"]["binding_status"] == "bound"
    assert by_arm["arm-derived"]["binding_status"] == "review_required"
    assert result["connection_intents"][0]["status"] == "blocked"
