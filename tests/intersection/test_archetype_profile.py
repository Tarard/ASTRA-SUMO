from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
from typing import Any

import pytest

from torii_sumo.intersection.archetype_profile import (
    classify_osm_intersection_archetype,
    registered_intersection_type_vocabulary,
)
from torii_sumo.intersection.movement_hypotheses import (
    build_vehicle_movement_hypotheses,
)
from torii_sumo.intersection.osm_patch import parse_osm_xml
from torii_sumo.intersection.physical_cell import (
    infer_signal_anchor_physical_cell,
)
from torii_sumo.intersection.schema import BBox, OSMNode, OSMPatch, OSMWay
from torii_sumo.intersection.topology_evidence import build_topology_evidence


XS1_OSM = Path("examples/03_xs1_four_way_tls/input/xs1-89129156.osm.xml.gz")
XS2_OSM = Path("examples/04_xs2_three_way_tls/input/xs2-7009179660.osm.xml")


@pytest.fixture(scope="module")
def xs1_profile() -> dict[str, Any]:
    return _classify_fixture(XS1_OSM, seed_node_id="89129156")


@pytest.fixture(scope="module")
def xs2_profile() -> dict[str, Any]:
    return _classify_fixture(XS2_OSM, seed_node_id="7009179663")


def test_vocabulary_exposes_orthogonal_finite_axes_without_confidence() -> None:
    vocabulary = registered_intersection_type_vocabulary()

    assert {
        "grade_relation",
        "interaction_kind",
        "cell_structure",
        "angular_form",
        "angular_distribution",
        "minimum_angle_status",
        "circulation_form",
        "carriageway_organization",
        "control_rule",
        "controller_topology",
        "movement_graph_status",
        "arm_count_class",
        "derived_alias",
    } <= set(vocabulary["dimensions"])
    assert vocabulary["composition_model"]["control"].startswith("a separate domain")
    assert vocabulary["composition_model"]["aliases"].endswith("not canonical storage")
    assert "confidence" not in set(_nested_keys(vocabulary))
    assert "probability" not in set(_nested_keys(vocabulary))


def test_xs1_is_x4_but_all_mutating_decisions_remain_blocked(
    xs1_profile: dict[str, Any],
) -> None:
    assert xs1_profile["arm_model"]["arm_count"] == 4
    assert xs1_profile["arm_model"]["arm_count_class"] == "A4"
    assert xs1_profile["derived_alias"]["value"] == "X4"
    assert len(xs1_profile["arm_model"]["through_pairs"]) == 2
    assert xs1_profile["classification_only"] is True
    assert xs1_profile["automatic_promotion_gate"] == "blocked"
    assert xs1_profile["dimensions"]["interaction_kind"]["value"] == "unknown"
    assert "cross_and_turn" in xs1_profile["dimensions"]["interaction_kind"]["alternatives"]
    assert xs1_profile["decision_capabilities"] == {
        "type_recognition": "pass",
        "existing_t3_x4_movement_generation": "conditional",
        "automatic_node_merge": "blocked",
        "automatic_channelization_rebuild": "blocked",
        "automatic_signal_binding": "blocked",
    }


def test_xs2_is_t3_and_preserves_contradictory_movement_evidence(
    xs2_profile: dict[str, Any],
) -> None:
    assert xs2_profile["arm_model"]["arm_count"] == 3
    assert xs2_profile["arm_model"]["arm_count_class"] == "A3"
    assert xs2_profile["derived_alias"]["value"] == "T3"
    assert len(xs2_profile["arm_model"]["through_pairs"]) == 1
    assert xs2_profile["dimensions"]["movement_graph_status"] == {
        "value": "contradictory",
        "status": "contradicted",
        "decision": "review_required",
        "evidence_ids": xs2_profile["dimensions"]["movement_graph_status"]["evidence_ids"],
        "rationale": (
            "OSM-derived variants remain partial until lane connectivity, "
            "restrictions, access, and all applicable ingress-to-egress paths "
            "are closed."
        ),
        "alternatives": [],
    }


@pytest.mark.parametrize(
    ("bearings", "expected_alias", "expected_pair_count"),
    [
        ([0.0, 90.0, 180.0], "T3", 1),
        ([0.0, 120.0, 240.0], "Y3", 0),
        ([0.0, 70.0, 140.0, 250.0], "irregular_4", 1),
    ],
)
def test_alias_uses_opposition_geometry_instead_of_arm_count_alone(
    bearings: list[float],
    expected_alias: str,
    expected_pair_count: int,
) -> None:
    profile = _classify_synthetic(bearings)

    assert profile["arm_model"]["arm_count"] == len(bearings)
    assert profile["derived_alias"]["value"] == expected_alias
    assert len(profile["arm_model"]["through_pairs"]) == expected_pair_count


def test_roundabout_keeps_exact_arm_count_and_uses_ring_group_structure() -> None:
    patch = _empty_patch()
    ring_ids = []
    for index, bearing in enumerate([0.0, 72.0, 144.0, 216.0, 288.0]):
        radians = math.radians(bearing)
        ring_id = f"ring-{index}"
        outside_id = f"outside-{index}"
        ring_ids.append(ring_id)
        patch.nodes[ring_id] = OSMNode(
            id=ring_id,
            lat=0.0,
            lon=0.0,
            x=math.sin(radians),
            y=math.cos(radians),
        )
        patch.nodes[outside_id] = OSMNode(
            id=outside_id,
            lat=0.0,
            lon=0.0,
            x=2.0 * math.sin(radians),
            y=2.0 * math.cos(radians),
        )
        patch.ways[f"approach-{index}"] = OSMWay(
            id=f"approach-{index}",
            node_refs=[outside_id, ring_id],
            tags={"highway": "secondary", "name": f"Arm {index}"},
        )
    for way_id, refs in {
        "ring-0": ring_ids[0:2],
        "ring-1": ring_ids[1:3],
        "ring-2": ring_ids[2:5],
        "ring-3": [ring_ids[4], ring_ids[0]],
    }.items():
        patch.ways[way_id] = OSMWay(
            id=way_id,
            node_refs=refs,
            tags={"highway": "secondary", "junction": "roundabout"},
        )
    patch.nodes["cycle-out"] = OSMNode(
        id="cycle-out",
        lat=0.0,
        lon=0.0,
        x=3.0,
        y=3.0,
    )
    patch.ways["remote-cycleway"] = OSMWay(
        id="remote-cycleway",
        node_refs=[ring_ids[3], "cycle-out"],
        tags={"highway": "cycleway"},
    )
    physical_cell = _synthetic_physical_cell([0.0, 72.0, 144.0, 216.0, 288.0])
    physical_cell["path_closure_node_ids"] = [ring_ids[0]]

    profile = classify_osm_intersection_archetype(
        patch,
        physical_cell,
        topology_evidence=_topology_evidence(branch_count=5),
    )

    assert profile["arm_model"]["arm_count"] == 5
    assert profile["arm_model"]["entry_count"] == 5
    assert profile["arm_model"]["exit_count"] == 5
    assert profile["arm_model"]["arm_count_class"] == "A5_plus"
    assert profile["derived_alias"]["value"] == "roundabout"
    assert profile["dimensions"]["circulation_form"]["value"] == ("nontraversable_ring")
    assert profile["dimensions"]["cell_structure"]["value"] == "ring_group"
    assert profile["canonical_identity"]["arm_count"] == 5
    assert profile["semantic_arm_evidence"]["source"] == ("explicit_roundabout_ring_boundary")
    assert set(profile["semantic_arm_evidence"]["roundabout_way_ids"]) == {
        "ring-0",
        "ring-1",
        "ring-2",
        "ring-3",
    }
    assert profile["semantic_arm_evidence"]["ring_validation"]["closed"] is True
    assert profile["arm_model"]["opposition_pairing_status"] == "not_applicable"
    assert "bicycle" in profile["facility_modes"]
    assert profile["supplemental_protected_evidence"]["source"] == (
        "expanded_roundabout_ring_closure"
    )
    assert profile["supplemental_protected_evidence"]["protected_features"][
        "bicycle_way_ids"
    ] == ["remote-cycleway"]
    assert all(
        all(source_way_id.startswith("approach-") for source_way_id in arm["source_way_ids"])
        for arm in profile["arm_model"]["arms"]
    )


def test_open_roundabout_fragment_abstains_from_exact_count_claim() -> None:
    patch = _empty_patch()
    for index in range(3):
        patch.nodes[f"ring-{index}"] = OSMNode(id=f"ring-{index}", lat=0.0, lon=0.0, x=float(index), y=0.0)
        patch.nodes[f"outside-{index}"] = OSMNode(
            id=f"outside-{index}",
            lat=0.0,
            lon=0.0,
            x=float(index),
            y=float(index + 1),
        )
        patch.ways[f"approach-{index}"] = OSMWay(
            id=f"approach-{index}",
            node_refs=[f"outside-{index}", f"ring-{index}"],
            tags={"highway": "secondary", "name": f"Arm {index}"},
        )
    patch.ways["open-ring"] = OSMWay(
        id="open-ring",
        node_refs=["ring-0", "ring-1", "ring-2"],
        tags={"highway": "secondary", "junction": "roundabout"},
    )
    physical_cell = _synthetic_physical_cell([0.0, 120.0, 240.0])
    physical_cell["path_closure_node_ids"] = ["ring-0"]

    profile = classify_osm_intersection_archetype(
        patch,
        physical_cell,
        topology_evidence=_topology_evidence(branch_count=3),
    )

    assert profile["arm_model"]["arm_count"] == 3
    assert profile["arm_model"]["count_status"] == "unknown"
    assert profile["derived_alias"]["value"] == "roundabout"
    assert profile["derived_alias"]["status"] == "unknown"
    assert profile["decision_capabilities"]["type_recognition"] == "review_required"
    assert profile["semantic_arm_evidence"]["ring_validation"]["closed"] is False
    assert "semantic_arms:roundabout_ring_not_closed" in profile["review_reasons"]


def test_multiarm_alias_does_not_run_opposition_matching() -> None:
    profile = _classify_synthetic([0.0, 60.0, 120.0, 180.0, 240.0, 300.0])

    assert profile["derived_alias"]["value"] == "multiarm"
    assert profile["derived_alias"]["status"] == "rule_derived"
    assert profile["arm_model"]["through_pairs"] == []
    assert profile["arm_model"]["opposition_pairing_status"] == ("not_evaluated_multiarm")
    assert profile["arm_model"]["adjacent_bearing_gaps_deg"] == [60.0] * 6
    assert profile["dimensions"]["angular_distribution"]["value"] == "radial"
    assert profile["dimensions"]["minimum_angle_status"]["value"] == ("small_angle_present")
    assert profile["dimensions"]["angular_form"]["value"] == "skewed"
    assert "semantic_arm_opposition_pairing_unresolved" not in profile["review_reasons"]


def test_atomic_core_is_required_for_x4_but_angular_form_stays_orthogonal() -> None:
    profile = classify_osm_intersection_archetype(
        _empty_patch(),
        _synthetic_physical_cell([0.0, 90.0, 180.0, 270.0]),
        topology_evidence=_topology_evidence(branch_count=2),
    )

    assert profile["dimensions"]["cell_structure"]["value"] == "unknown"
    assert profile["derived_alias"]["value"] == "unknown"
    assert profile["dimensions"]["angular_distribution"]["value"] == ("orthogonal_like")
    assert profile["dimensions"]["minimum_angle_status"]["value"] == "non_skew"
    assert profile["dimensions"]["angular_form"]["value"] == "orthogonal_like"
    assert profile["decision_capabilities"]["type_recognition"] == "review_required"


@pytest.mark.parametrize("uncertainty", ["missing_bearing", "grouping_review"])
def test_three_or_four_arm_alias_abstains_when_arm_evidence_is_uncertain(
    uncertainty: str,
) -> None:
    physical_cell = _synthetic_physical_cell([0.0, 90.0, 180.0, 270.0])
    if uncertainty == "missing_bearing":
        physical_cell["physical_approaches"][0]["bearing_from_seed_deg"] = None
    else:
        physical_cell["physical_approaches"][0]["grouping_status"] = "review_required"

    profile = classify_osm_intersection_archetype(
        _empty_patch(),
        physical_cell,
        topology_evidence=_topology_evidence(),
    )

    assert profile["derived_alias"]["value"] == "unknown"
    assert profile["decision_capabilities"]["type_recognition"] == "review_required"
    assert "semantic_arm_opposition_pairing_unresolved" in profile["review_reasons"]


def test_pedestrian_evidence_does_not_imply_bicycle_facility() -> None:
    profile = classify_osm_intersection_archetype(
        _empty_patch(),
        _synthetic_physical_cell([0.0, 90.0, 180.0]),
        topology_evidence=_topology_evidence(
            protected_features={
                "pedestrian_way_ids": ["footway-1"],
                "bicycle_way_ids": [],
            }
        ),
    )

    assert "pedestrian" in profile["facility_modes"]
    assert "bicycle" not in profile["facility_modes"]


def test_topology_evidence_requires_explicit_path_modes_and_ignores_crossing_no() -> None:
    patch = _empty_patch()
    for node_id in ("a", "b", "c", "d", "e", "f", "g", "h"):
        patch.nodes[node_id] = OSMNode(
            id=node_id,
            lat=0.0,
            lon=0.0,
            x=0.0,
            y=0.0,
        )
    patch.nodes["a"].tags = {"crossing": "no"}
    patch.nodes["b"].tags = {"highway": "crossing"}
    patch.nodes["e"].tags = {
        "highway": "crossing",
        "foot": "no",
        "bicycle": "designated",
    }
    patch.ways = {
        "foot": OSMWay(
            id="foot",
            node_refs=["a", "b"],
            tags={"highway": "footway"},
        ),
        "path": OSMWay(
            id="path",
            node_refs=["b", "c"],
            tags={"highway": "path"},
        ),
        "cycle": OSMWay(
            id="cycle",
            node_refs=["c", "d"],
            tags={"highway": "cycleway"},
        ),
        "foot-denied": OSMWay(
            id="foot-denied",
            node_refs=["e", "f"],
            tags={"highway": "footway", "foot": "no"},
        ),
        "cycle-denied": OSMWay(
            id="cycle-denied",
            node_refs=["g", "h"],
            tags={"highway": "cycleway", "bicycle": "no"},
        ),
    }
    physical_cell = _synthetic_physical_cell([0.0, 90.0, 180.0])
    physical_cell["path_closure_node_ids"] = ["a", "b", "c", "d", "e", "f", "g", "h"]

    protected = build_topology_evidence(patch, physical_cell)["protected_features"]

    assert protected["pedestrian_way_ids"] == ["foot"]
    assert protected["bicycle_way_ids"] == ["cycle"]
    assert protected["pedestrian_or_bicycle_way_ids"] == [
        "cycle",
        "cycle-denied",
        "foot",
        "foot-denied",
        "path",
    ]
    assert protected["crossing_node_ids"] == ["b"]
    assert protected["bicycle_crossing_node_ids"] == ["e"]


def test_traffic_calming_island_remains_weak_evidence() -> None:
    patch = _empty_patch()
    patch.nodes["island"] = OSMNode(
        id="island",
        lat=0.0,
        lon=0.0,
        x=0.0,
        y=0.0,
        tags={"traffic_calming": "island"},
    )
    physical_cell = _synthetic_physical_cell([0.0, 90.0, 180.0])
    physical_cell["path_closure_node_ids"] = ["island"]
    topology = _topology_evidence()
    topology["path_closure_node_ids"] = ["island"]

    profile = classify_osm_intersection_archetype(
        patch,
        physical_cell,
        topology_evidence=topology,
    )

    assert profile["channelization"] == []
    assert any(
        item["feature"] == "island_candidate" and item["interpretation_gate"] == "review_required"
        for item in profile["evidence_features"]
    )


def test_classifier_rejects_mismatched_parent_lineage() -> None:
    physical_cell = _synthetic_physical_cell([0.0, 90.0, 180.0])
    wrong_topology = _topology_evidence()
    wrong_topology["physical_cell_hypothesis_id"] = "different-cell"

    with pytest.raises(ValueError, match="topology_evidence physical_cell_hypothesis_id"):
        classify_osm_intersection_archetype(
            _empty_patch(),
            physical_cell,
            topology_evidence=wrong_topology,
        )

    with pytest.raises(
        ValueError,
        match="movement_hypotheses parent_physical_cell_hypothesis_id",
    ):
        classify_osm_intersection_archetype(
            _empty_patch(),
            physical_cell,
            topology_evidence=_topology_evidence(),
            movement_hypotheses={
                "parent_physical_cell_hypothesis_id": "different-cell",
            },
        )


def test_classification_id_is_deterministic_across_approach_and_mapping_order() -> None:
    patch = _empty_patch()
    physical_cell = _synthetic_physical_cell([0.0, 90.0, 180.0, 270.0])
    reversed_cell = deepcopy(physical_cell)
    reversed_cell["physical_approaches"].reverse()

    first = classify_osm_intersection_archetype(
        patch,
        physical_cell,
        topology_evidence=_topology_evidence(),
        source_evidence={"path": "fixture.osm.xml", "sha256": "abc123"},
    )
    second = classify_osm_intersection_archetype(
        patch,
        reversed_cell,
        topology_evidence=_topology_evidence(),
        source_evidence={"sha256": "abc123", "path": "fixture.osm.xml"},
    )

    assert first == second
    assert first["classification_id"].startswith("intersection-classification-")


@pytest.mark.parametrize("threshold", [-1.0, 0.0, 90.0, 120.0])
def test_invalid_opposite_tolerance_fails(threshold: float) -> None:
    with pytest.raises(
        ValueError,
        match="opposite_tolerance_deg must be between 0 and 90",
    ):
        classify_osm_intersection_archetype(
            _empty_patch(),
            _synthetic_physical_cell([0.0, 180.0]),
            topology_evidence=_topology_evidence(),
            opposite_tolerance_deg=threshold,
        )


@pytest.mark.parametrize("threshold", [-1.0, 0.0, 180.0, 200.0])
def test_invalid_skew_threshold_fails(threshold: float) -> None:
    with pytest.raises(
        ValueError,
        match="minimum_non_skew_angle_deg must be between 0 and 180",
    ):
        classify_osm_intersection_archetype(
            _empty_patch(),
            _synthetic_physical_cell([0.0, 180.0]),
            topology_evidence=_topology_evidence(),
            minimum_non_skew_angle_deg=threshold,
        )


def _classify_fixture(path: Path, *, seed_node_id: str) -> dict[str, Any]:
    patch = parse_osm_xml(path)
    physical_cell = infer_signal_anchor_physical_cell(
        patch,
        seed_node_id=seed_node_id,
    )
    movements = build_vehicle_movement_hypotheses(
        patch,
        physical_cell,
        traffic_side="right",
    )
    return classify_osm_intersection_archetype(
        patch,
        physical_cell,
        movement_hypotheses=movements,
    )


def _classify_synthetic(bearings: list[float]) -> dict[str, Any]:
    return classify_osm_intersection_archetype(
        _empty_patch(),
        _synthetic_physical_cell(bearings),
        topology_evidence=_topology_evidence(),
    )


def _synthetic_physical_cell(bearings: list[float]) -> dict[str, Any]:
    return {
        "hypothesis_id": "synthetic-physical-cell",
        "path_closure_node_ids": [],
        "proposed_source_junction_ids": [],
        "signal_anchor_node_ids": [],
        "risks": [],
        "physical_approaches": [
            {
                "physical_approach_id": f"arm-{index}",
                "bearing_from_seed_deg": bearing,
                "source_way_ids": [],
                "member_boundary_port_ids": [f"port-{index}"],
                "grouping_status": "pass",
                "member_count": 1,
                "flow_roles": ["bidirectional"],
                "incoming_lane_count": 1,
                "outgoing_lane_count": 1,
            }
            for index, bearing in enumerate(bearings)
        ],
    }


def _topology_evidence(
    *,
    branch_count: int = 1,
    protected_features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    branch_ids = [f"branch-{index}" for index in range(branch_count)]
    return {
        "topology_evidence_id": "synthetic-topology-evidence",
        "physical_cell_hypothesis_id": "synthetic-physical-cell",
        "path_closure_node_ids": [],
        "branch_node_count": branch_count,
        "branch_node_ids": branch_ids,
        "branch_connectors": [],
        "storage_capable_connectors": [],
        "protected_features": protected_features or {},
    }


def _empty_patch() -> OSMPatch:
    return OSMPatch(
        nodes={},
        ways={},
        relations={},
        bbox=BBox(min_lon=0.0, min_lat=0.0, max_lon=1.0, max_lat=1.0),
    )


def _nested_keys(value: Any):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from _nested_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _nested_keys(nested)
