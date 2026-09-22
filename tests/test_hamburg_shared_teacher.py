from __future__ import annotations

from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from torii_sumo.core.hamburg_shared_teacher import (
    derive_hamburg_directional_components,
    materialize_hamburg_shared_controller_teacher,
    replay_hamburg_shared_teacher,
)
from torii_sumo.core.junction_rebuild_candidate import (
    build_scoped_teacher_tls_cell_replay_plan,
    build_shared_teacher_tls_controller_replay_plan,
)
from torii_sumo.intersection.schema import (
    Approach,
    BBox,
    ControlModel,
    IntersectionCore,
    IntersectionIR,
    Movement,
    MovementMatrix,
    OSMPatch,
    PatchSeed,
    RoadPairRelationGraph,
    TLSPhase,
)


def test_directional_components_are_physical_owners() -> None:
    ir = _two_owner_ir()

    components = derive_hamburg_directional_components(ir)

    assert len(components) == 2
    assert {component.movement_ids for component in components} == {("m_a",), ("m_b",)}


@pytest.mark.skipif(shutil.which("netconvert") is None, reason="SUMO netconvert is required")
def test_shared_teacher_preserves_movements_and_exposes_multiple_owners_to_native_planner(
    tmp_path: Path,
) -> None:
    result = materialize_hamburg_shared_controller_teacher(
        ir=_two_owner_ir(),
        approach_pairs=(),
        output_dir=tmp_path,
        prefix="two_owner_teacher",
        expected_component_count=2,
    )

    assert result["status"] == "pass"
    assert result["directional_component_count"] == 2
    assert result["official_movement_count"] == 2
    assert result["compiled_controlled_connection_count"] == 2
    assert result["duplicate_controlled_connection_count"] == 0
    assert result["grouped_link_index_count"] == 1
    assert result["shared_tllogic_count"] == 1
    assert result["candidate_boundary_mapping_status"] == "incomplete_or_ambiguous"
    assert len(result["candidate_boundary_mapping_issues"]) == 1
    owner_ids = result["teacher_owner_ids"]
    assert owner_ids == ["HH_TEST", "HH_TEST__owner_01"]

    teacher_net_file = Path(str(result["grouped_teacher_net_file"]))
    root = ET.parse(teacher_net_file).getroot()
    controlled = [
        connection
        for connection in root.findall("connection")
        if connection.attrib.get("tl") == "HH_TEST"
        and connection.attrib.get("linkIndex") is not None
    ]
    assert len(controlled) == 2
    assert {connection.attrib["linkIndex"] for connection in controlled} == {"0"}
    assert [phase.attrib["state"] for phase in root.findall("tlLogic[@id='HH_TEST']/phase")] == [
        "g"
    ]
    assert len(
        {
            (
                connection.attrib["from"],
                connection.attrib["fromLane"],
                connection.attrib["to"],
                connection.attrib["toLane"],
            )
            for connection in controlled
        }
    ) == 2

    blocked_replay = replay_hamburg_shared_teacher(
        candidate_net_file=teacher_net_file,
        teacher_net_file=teacher_net_file,
        output_file=tmp_path / "must_not_write.net.xml",
        teacher_controller_id="HH_TEST",
        candidate_controller_id="HH_TEST",
        candidate_junction_ids=set(owner_ids),
        approach_pairs=(),
        collapse_junction_ids=set(owner_ids),
        candidate_owner_map={owner_id: owner_id for owner_id in owner_ids},
    )
    assert blocked_replay["status"] == "blocked"
    assert (
        blocked_replay["shared_controller_replay_reason"]
        == "official_candidate_boundary_mapping_not_one_to_one"
    )
    assert not (tmp_path / "must_not_write.net.xml").exists()

    candidate_ids = set(owner_ids)
    identity_pairs = _identity_approach_pairs()
    scoped = build_scoped_teacher_tls_cell_replay_plan(
        candidate_net_file=teacher_net_file,
        teacher_net_file=teacher_net_file,
        teacher_junction_id="HH_TEST",
        candidate_junction_id="HH_TEST",
        candidate_junction_ids=candidate_ids,
        approach_pairs=identity_pairs,
    )
    assert scoped["status"] == "pass"
    assert scoped["shared_controller_scope"]["status"] == "needs_expanded_scope"
    assert scoped["shared_controller_scope"]["teacher_internal_owner_ids"] == owner_ids
    assert scoped["shared_controller_scope"]["extra_teacher_internal_owner_ids"] == [
        "HH_TEST__owner_01"
    ]

    shared = build_shared_teacher_tls_controller_replay_plan(
        candidate_net_file=teacher_net_file,
        teacher_net_file=teacher_net_file,
        teacher_controller_id="HH_TEST",
        candidate_controller_id="HH_TEST",
        candidate_junction_ids=candidate_ids,
        approach_pairs=identity_pairs,
        collapse_junction_ids=candidate_ids,
        candidate_owner_map={owner_id: owner_id for owner_id in owner_ids},
    )
    assert shared["status"] == "pass"
    assert set(shared["teacher_owner_ids"]) == candidate_ids
    assert shared["teacher_controller_connection_count"] == 2


def _two_owner_ir() -> IntersectionIR:
    approaches = [
        _approach(
            approach_id="a_in",
            edge_id="a_in",
            endpoint=(-50.0, 0.0),
            center=(0.0, 0.0),
            incoming=True,
        ),
        _approach(
            approach_id="a_out",
            edge_id="a_out",
            endpoint=(50.0, 0.0),
            center=(0.0, 0.0),
            incoming=False,
        ),
        _approach(
            approach_id="b_in",
            edge_id="b_in",
            endpoint=(-50.0, 100.0),
            center=(0.0, 100.0),
            incoming=True,
        ),
        _approach(
            approach_id="b_out",
            edge_id="b_out",
            endpoint=(50.0, 100.0),
            center=(0.0, 100.0),
            incoming=False,
        ),
    ]
    movements = [
        _movement("m_a", "a_in", "a_out"),
        _movement("m_b", "b_in", "b_out"),
    ]
    return IntersectionIR(
        intersection_id="hamburg-shared-synthetic",
        osm_patch=OSMPatch(
            nodes={},
            ways={},
            relations={},
            bbox=BBox(min_lon=0.0, min_lat=0.0, max_lon=0.0, max_lat=0.0),
            seed=PatchSeed(center_latlon=(0.0, 0.0)),
        ),
        core=IntersectionCore(
            core_id="HH_TEST",
            center_xy=(0.0, 50.0),
            center_latlon=(0.0, 0.0),
            core_osm_node_ids=[],
            core_way_ids=[],
            core_radius_m=100.0,
            topology_type="complex",
            internal_fragment_count=0,
            short_internal_edge_count=0,
            confidence=1.0,
        ),
        approaches=approaches,
        road_pair_graph=RoadPairRelationGraph(
            relations=[],
            missing_connection_count=0,
            wrong_connection_count=0,
            overlap_conflict_count=0,
            near_miss_count=0,
            duplicate_parallel_count=0,
            blocking_error_count=0,
        ),
        movement_matrix=MovementMatrix(
            movements=movements,
            legal_movement_count=2,
            forbidden_movement_count=0,
            inferred_movement_count=0,
        ),
        control=ControlModel(
            control_type="traffic_light",
            source=["synthetic_official_truth"],
            priority_approach_ids=[],
            tls_id="HH_TEST",
            phases=[TLSPhase(phase_id="source", duration=1.0, state="G", movement_ids=["m_a", "m_b"])],
            link_index_map={"m_a": 0, "m_b": 0},
            confidence=1.0,
        ),
        claim_status="semantic-model-built",
    )


def _approach(
    *,
    approach_id: str,
    edge_id: str,
    endpoint: tuple[float, float],
    center: tuple[float, float],
    incoming: bool,
) -> Approach:
    return Approach(
        approach_id=approach_id,
        role="official_ingress" if incoming else "official_egress",
        source_way_ids=[approach_id],
        road_name="synthetic",
        highway_class="primary",
        bearing_to_core=90.0 if incoming else 270.0,
        bearing_from_core=270.0 if incoming else 90.0,
        endpoint_xy=endpoint,
        source_shape_xy=[endpoint, center],
        incoming_lane_count=1 if incoming else 0,
        outgoing_lane_count=0 if incoming else 1,
        incoming_edge_ids=[edge_id if incoming else f"unused_{edge_id}"],
        outgoing_edge_ids=[f"unused_{edge_id}" if incoming else edge_id],
        oneway=True,
        has_incoming_vehicle_flow=incoming,
        has_outgoing_vehicle_flow=not incoming,
        direction_evidence=["synthetic_direction"],
        allowed_modes={"passenger"},
    )


def _movement(movement_id: str, source_id: str, target_id: str) -> Movement:
    return Movement(
        movement_id=movement_id,
        from_approach_id=source_id,
        to_approach_id=target_id,
        road_pair_relation_id=f"{source_id}->{target_id}",
        turn="straight",
        allowed=True,
        from_lane_indices=[0],
        to_lane_indices=[0],
        allowed_modes={"passenger"},
        evidence=["synthetic_official_movement"],
        confidence=1.0,
    )


def _identity_approach_pairs() -> list[dict[str, object]]:
    return [
        {
            "reference_edge_id": edge_id,
            "candidate_edge_id": edge_id,
            "official_approach_id": edge_id,
        }
        for edge_id in ("a_in", "a_out", "b_in", "b_out")
    ]
