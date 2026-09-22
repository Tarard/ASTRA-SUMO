from __future__ import annotations

import json
import random
from xml.etree import ElementTree as ET

from torii_sumo.corridor.canonicalizer import (
    CanonicalEntity,
    CanonicalNetworkSnapshot,
    canonicalize_raw_network,
)
from torii_sumo.corridor.conflict_graph import (
    audit_independent_movement_safety,
    build_movement_conflict_graph,
)
from torii_sumo.corridor.enums import GateStatus, TrafficSide
from torii_sumo.corridor.ids import stable_id
from torii_sumo.corridor.netxml import parse_net_xml
from torii_sumo.corridor.pedestrian_crossings import (
    infer_pedestrian_facility_owners,
)
from torii_sumo.corridor.review import (
    PedestrianCrossingReviewSubject,
    build_pedestrian_coverage_gap,
)


def _entity(
    kind: str,
    entity_id: str,
    payload: dict,
    *,
    cell_id: str,
) -> CanonicalEntity:
    return CanonicalEntity(
        kind=kind,
        stable_entity_id=entity_id,
        semantic_signature=stable_id(
            "signature",
            {"kind": kind, "payload": payload},
        ),
        owner_physical_cell_ids=(cell_id,),
        payload=payload,
    )


def _pedestrian_review_subject(
    *,
    cell_id: str,
    index: int,
) -> dict:
    semantic_subject = {
        "facility_kind": "pedestrian-crossing",
        "control_kind": "unknown-unsignalized",
        "crossing_shape_xy": ((float(index), -1.0), (float(index), 1.0)),
        "crossing_width_m": 4.0,
        "source_endpoint_shape_xy": ((float(index) - 1.0, -1.0),),
        "destination_endpoint_shape_xy": ((float(index) + 1.0, 1.0),),
        "crossed_edge_signatures": (
            stable_id("signature", {"crossed_edge": index}),
        ),
    }
    return PedestrianCrossingReviewSubject(
        review_subject_id=stable_id("review", semantic_subject),
        **semantic_subject,
        position_xy=(float(index), 0.0),
        physical_cell_id=cell_id,
        boundary_port_ids=(),
        permission_contract={
            "coverage": "complete",
            "status": "pass",
            "basis": "explicit_allow_intersection",
            "allow": ("pedestrian",),
            "disallow": (),
            "element_count": 3,
        },
        rejection_reasons=("test_unmodeled_crossing",),
        machine_question="Which physical cell owns this crossing?",
        required_observations=("crossing ownership",),
    ).model_dump(mode="python")


def _crossing_snapshot(
    *,
    shared_signal_group: bool = False,
    protected_both: bool = True,
    pedestrian_facilities: int = 0,
    unsupported_controlled_links: int = 0,
    shape_a=((-10.0, 0.0), (10.0, 0.0)),
    shape_b=((0.0, -10.0), (0.0, 10.0)),
):
    cell_id = stable_id("cell", {"test": "crossing"})
    controller_id = stable_id("controller", {"cell": cell_id})
    source_a = stable_id("lane_role", {"lane": "west-in"})
    target_a = stable_id("lane_role", {"lane": "east-out"})
    source_b = stable_id("lane_role", {"lane": "south-in"})
    target_b = stable_id("lane_role", {"lane": "north-out"})
    movement_a = stable_id("movement", {"movement": "west-east"})
    movement_b = stable_id("movement", {"movement": "south-north"})
    path_a = stable_id("path", {"movement_id": movement_a})
    path_b = stable_id("path", {"movement_id": movement_b})

    def path_payload(movement_id: str, shape):
        return {
            "movement_id": movement_id,
            "multiplicity": 1,
            "path_variants": [
                {
                    "path_signature": stable_id(
                        "signature",
                        {"movement_id": movement_id, "shape": shape},
                    ),
                    "path": {
                        "status": "pass",
                        "segments": [
                            {
                                "shape_xy": shape,
                                "permissions": {
                                    "allow": ["passenger"],
                                    "disallow": [],
                                },
                                "width_m": 3.2,
                                "speed_mps": 13.9,
                            }
                        ],
                        "failures": [],
                    },
                }
            ],
        }

    def movement_payload(
        movement_id: str,
        path_id: str,
        source_role_id: str,
        destination_role_id: str,
    ):
        return {
            "multiplicity": 1,
            "variants": [
                {
                    "source_lane_role_id": source_role_id,
                    "destination_lane_role_id": destination_role_id,
                    "mode": "passenger",
                    "turn_class": "straight",
                }
            ],
            "internal_path_id": path_id,
        }

    entities = [
        _entity(
            "internal_path",
            path_a,
            path_payload(movement_a, shape_a),
            cell_id=cell_id,
        ),
        _entity(
            "internal_path",
            path_b,
            path_payload(movement_b, shape_b),
            cell_id=cell_id,
        ),
        _entity(
            "movement",
            movement_a,
            movement_payload(movement_a, path_a, source_a, target_a),
            cell_id=cell_id,
        ),
        _entity(
            "movement",
            movement_b,
            movement_payload(movement_b, path_b, source_b, target_b),
            cell_id=cell_id,
        ),
        _entity(
            "request_foes",
            stable_id("request", {"cell": cell_id}),
            {
                "physical_cell_id": cell_id,
                "request_rows": [
                    {"index": 0, "response": "00", "foes": "00", "cont": "0"},
                    {"index": 1, "response": "00", "foes": "00", "cont": "0"},
                ],
            },
            cell_id=cell_id,
        ),
    ]
    if shared_signal_group:
        group_ids = (stable_id("signal_group", {"shared": [movement_a, movement_b]}),)
        group_movements = ((movement_a, movement_b),)
    else:
        group_ids = (
            stable_id("signal_group", {"movement": movement_a}),
            stable_id("signal_group", {"movement": movement_b}),
        )
        group_movements = ((movement_a,), (movement_b,))
    for group_id, movement_ids in zip(group_ids, group_movements, strict=True):
        entities.append(
            _entity(
                "signal_group",
                group_id,
                {
                    "controller_id": controller_id,
                    "movement_ids": movement_ids,
                    "source_link_indices": [0],
                    "multiple_source_indices": False,
                },
                cell_id=cell_id,
            )
        )
    program_id = stable_id("program", {"controller": controller_id})
    review_subject_records = [
        _pedestrian_review_subject(
            cell_id=cell_id,
            index=index,
        )
        for index in range(pedestrian_facilities)
    ]
    for review_subject_record in review_subject_records:
        gap = build_pedestrian_coverage_gap(
            PedestrianCrossingReviewSubject.model_validate(
                review_subject_record
            )
        )
        entities.append(
            _entity(
                "pedestrian_coverage_gap",
                gap.coverage_gap_id,
                gap.model_dump(mode="python"),
                cell_id=cell_id,
            )
        )
    entities.append(
        _entity(
            "controller_program",
            program_id,
            {
                "controller_id": controller_id,
                "controller_type": "static",
                "offset": 0.0,
                "signal_group_ids": group_ids,
                "phases": [
                    {
                        "duration": 30.0,
                        "group_states": [
                            {
                                "signal_group_id": group_id,
                                "states": ["G" if protected_both or group_id == group_ids[0] else "r"],
                                "consistent": True,
                            }
                            for group_id in group_ids
                        ],
                    }
                ],
            },
            cell_id=cell_id,
        )
    )
    entities.append(
        _entity(
            "safety_coverage",
            stable_id("coverage", {"cell": cell_id}),
            {
                "canonical_movement_count": 2,
                "controlled_connection_count": 2 + unsupported_controlled_links,
                "mapped_controlled_movement_count": 2,
                "unsupported_controlled_connection_count": unsupported_controlled_links,
                "unsupported_controlled_connections": (
                    [
                        {
                            "from_function": "walkingarea",
                            "to_function": "crossing",
                            "direction": "straight",
                        }
                    ]
                    if unsupported_controlled_links
                    else []
                ),
                "crossing_edge_count": pedestrian_facilities,
                "walkingarea_edge_count": pedestrian_facilities,
                "modeled_controlled_pedestrian_movement_count": 0,
                "modeled_crossing_edge_count": 0,
                "unmodeled_crossing_edge_count": pedestrian_facilities,
                "unmodeled_pedestrian_crossings": review_subject_records,
                "link_index2_connection_count": 0,
                "movement_mode_class_counts": {"road-motorized": 2},
                "ownership_status": "resolved",
            },
            cell_id=cell_id,
        )
    )
    return (
        CanonicalNetworkSnapshot(
            traffic_side=TrafficSide.RIGHT,
            entities=tuple(entities),
        ),
        movement_a,
        movement_b,
    )


def _pedestrian_tls_snapshot(
    *,
    protected_together: bool,
    controlled_crossing: bool = True,
    include_continuation: bool = True,
    include_tls_program: bool = True,
    link_index2: bool = False,
    facility_prefix: str = ":j",
    declare_facility_ownership: bool = True,
) -> CanonicalNetworkSnapshot:
    crossing_id = f"{facility_prefix}_c0"
    source_walkingarea_id = f"{facility_prefix}_w1"
    destination_walkingarea_id = f"{facility_prefix}_w0"
    crossing_control = (
        'tl="tls" linkIndex="1"' + (' linkIndex2="2"' if link_index2 else "") if controlled_crossing else ""
    )
    if link_index2:
        phases = (
            '<phase duration="30" state="GGG"/>' if protected_together else '<phase duration="30" state="Grr"/>'
        ) + '<phase duration="30" state="rGG"/>'
    else:
        phases = (
            '<phase duration="30" state="GG"/>' if protected_together else '<phase duration="30" state="Gr"/>'
        ) + '<phase duration="30" state="rG"/>'
    tls_logic = (
        f'<tlLogic id="tls" type="static" programID="0" offset="0">{phases}</tlLogic>'
        if include_tls_program
        else ""
    )
    continuation = (
        f'<connection from="{crossing_id}" '
        f'to="{destination_walkingarea_id}" fromLane="0" toLane="0" '
        'dir="s" state="M"/>'
        if include_continuation
        else ""
    )
    facility_incoming_lane = (
        f" {source_walkingarea_id}_0"
        if declare_facility_ownership
        else ""
    )
    facility_internal_lane = (
        f" {crossing_id}_0" if declare_facility_ownership else ""
    )
    root = ET.fromstring(
        f"""<net>
  <edge id="in" from="a" to="j">
    <lane id="in_0" index="0" allow="passenger" width="3.2" shape="-10,0 -1,0"/>
  </edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" allow="passenger" width="3.2" shape="1,0 10,0"/>
  </edge>
  <edge id=":j_0" function="internal">
    <lane id=":j_0_0" index="0" allow="passenger" width="3.2" shape="-1,0 1,0"/>
  </edge>
  <edge id="{crossing_id}" function="crossing" crossingEdges="in out">
    <lane id="{crossing_id}_0" index="0" allow="pedestrian" width="4" shape="0,-5 0,5"/>
  </edge>
  <edge id="{source_walkingarea_id}" function="walkingarea">
    <lane id="{source_walkingarea_id}_0" index="0" allow="pedestrian" width="4" shape="-2,-5 2,-5"/>
  </edge>
  <edge id="{destination_walkingarea_id}" function="walkingarea">
    <lane id="{destination_walkingarea_id}_0" index="0" allow="pedestrian" width="4" shape="-2,5 2,5"/>
  </edge>
  <junction id="a" type="dead_end" incLanes="" intLanes=""/>
  <junction id="j" type="traffic_light" incLanes="in_0{facility_incoming_lane}"
            intLanes=":j_0_0{facility_internal_lane}">
    <request index="0" response="10" foes="10" cont="0"/>
    <request index="1" response="00" foes="01" cont="0"/>
  </junction>
  <junction id="b" type="dead_end" incLanes="out_0" intLanes=""/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0"
              tl="tls" linkIndex="0" dir="s" state="O"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s" state="M"/>
  {continuation}
  <connection from="{source_walkingarea_id}" to="{crossing_id}"
              fromLane="0" toLane="0" {crossing_control} dir="s" state="M"/>
  {tls_logic}
</net>"""
    )
    return canonicalize_raw_network(
        parse_net_xml(root),
        traffic_side=TrafficSide.RIGHT,
    )


def _movement_only_snapshot(
    shapes: tuple[tuple[tuple[float, float], ...], ...],
    *,
    shared_destination: tuple[int, int] | None = None,
) -> CanonicalNetworkSnapshot:
    cell_id = stable_id("cell", {"test": "broad-phase-equivalence"})
    shared_destination_id = stable_id(
        "lane_role",
        {"test": "shared-destination"},
    )
    entities: list[CanonicalEntity] = []
    for index, shape in enumerate(shapes):
        movement_id = stable_id("movement", {"index": index})
        path_id = stable_id("path", {"movement_id": movement_id})
        source_role_id = stable_id("lane_role", {"source": index})
        destination_role_id = (
            shared_destination_id
            if shared_destination is not None and index in shared_destination
            else stable_id("lane_role", {"destination": index})
        )
        entities.extend(
            (
                _entity(
                    "internal_path",
                    path_id,
                    {
                        "movement_id": movement_id,
                        "multiplicity": 1,
                        "path_variants": [
                            {
                                "path_signature": stable_id(
                                    "signature",
                                    {"movement_id": movement_id, "shape": shape},
                                ),
                                "path": {
                                    "status": "pass",
                                    "segments": [
                                        {
                                            "shape_xy": shape,
                                            "width_m": 3.2,
                                        }
                                    ],
                                    "failures": [],
                                },
                            }
                        ],
                    },
                    cell_id=cell_id,
                ),
                _entity(
                    "movement",
                    movement_id,
                    {
                        "multiplicity": 1,
                        "variants": [
                            {
                                "source_lane_role_id": source_role_id,
                                "destination_lane_role_id": destination_role_id,
                                "mode": "passenger",
                                "turn_class": "straight",
                            }
                        ],
                        "internal_path_id": path_id,
                    },
                    cell_id=cell_id,
                ),
            )
        )
    return CanonicalNetworkSnapshot(
        traffic_side=TrafficSide.RIGHT,
        entities=tuple(entities),
    )


def test_independent_conflict_graph_ignores_request_foes_self_consistency() -> None:
    snapshot, movement_a, movement_b = _crossing_snapshot()

    graph = build_movement_conflict_graph(snapshot)

    assert len(graph.conflicts) == 1
    conflict = graph.conflicts[0]
    assert {conflict.movement_a_id, conflict.movement_b_id} == {
        movement_a,
        movement_b,
    }
    assert conflict.reason == "centerline-crossing"
    assert conflict.certainty == "confirmed"


def test_protected_green_crossing_is_a_hard_independent_safety_failure() -> None:
    snapshot, _, _ = _crossing_snapshot()

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.BLOCKED
    assert report.automatic_promotion_gate is GateStatus.BLOCKED
    assert report.protected_conflict_count == 1
    assert {finding.category for finding in report.findings} >= {"protected_green_movement_conflict"}


def test_conflicting_movements_cannot_share_one_signal_group() -> None:
    snapshot, _, _ = _crossing_snapshot(shared_signal_group=True)

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.BLOCKED
    assert report.shared_signal_group_conflict_count == 1
    assert {finding.category for finding in report.findings} >= {"conflicting_movements_share_signal_group"}


def test_unmodelled_pedestrian_facilities_block_automatic_promotion_as_review() -> None:
    snapshot, _, _ = _crossing_snapshot(
        protected_both=False,
        pedestrian_facilities=1,
    )

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.REVIEW
    assert report.automatic_promotion_gate is GateStatus.BLOCKED
    assert report.applicability_status is GateStatus.REVIEW
    assert report.coverage.crossing_edge_count == 1
    assert report.coverage.unmodeled_pedestrian_review_subject_count == 1
    assert len(report.coverage.unmodeled_pedestrian_crossings) == 1
    assert report.coverage.pedestrian_coverage_gap_count == 1
    assert len(report.coverage.pedestrian_coverage_gaps) == 1
    review_finding = next(
        finding
        for finding in report.findings
        if finding.category
        == "pedestrian_facilities_outside_independent_conflict_model"
    )
    assert len(
        review_finding.witness["unmodeled_pedestrian_crossings"]
    ) == 1
    assert "pedestrian_facilities_outside_independent_conflict_model" in (report.limitations)


def test_unmodeled_pedestrian_review_subject_closure_mismatch_is_hard_failure() -> None:
    snapshot, _, _ = _crossing_snapshot(
        protected_both=False,
        pedestrian_facilities=1,
    )
    coverage = next(
        entity for entity in snapshot.entities if entity.kind == "safety_coverage"
    )
    coverage.payload["unmodeled_pedestrian_crossings"] = []

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.BLOCKED
    closure = next(
        finding
        for finding in report.findings
        if finding.category
        == "unmodeled_pedestrian_review_subject_closure_mismatch"
    )
    assert closure.witness["unmodeled_crossing_edge_count"] == 1
    assert closure.witness["review_subject_record_count"] == 0
    assert closure.witness["valid_unique_review_subject_count"] == 0


def test_unmapped_controlled_link_is_a_hard_safety_coverage_failure() -> None:
    snapshot, _, _ = _crossing_snapshot(
        protected_both=False,
        unsupported_controlled_links=1,
    )

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.BLOCKED
    assert report.coverage.unsupported_controlled_connection_count == 1
    assert {finding.category for finding in report.findings} >= {"controlled_link_outside_independent_conflict_model"}


def test_controlled_pedestrian_crossing_is_a_stable_canonical_movement() -> None:
    snapshot = _pedestrian_tls_snapshot(protected_together=False)
    movements = [entity for entity in snapshot.entities if entity.kind == "movement"]
    pedestrian = next(
        movement
        for movement in movements
        if movement.payload["variants"][0].get("movement_kind") == "pedestrian-crossing-occupancy"
    )
    coverage = next(entity for entity in snapshot.entities if entity.kind == "safety_coverage")

    assert len(movements) == 2
    assert pedestrian.payload["variants"][0]["mode_classes"] == ("pedestrian",)
    assert len(pedestrian.boundary_port_ids) == 2
    assert coverage.payload["controlled_connection_count"] == 2
    assert coverage.payload["mapped_controlled_movement_count"] == 2
    assert coverage.payload["unsupported_controlled_connection_count"] == 0
    assert coverage.payload["modeled_pedestrian_movement_count"] == 1
    assert coverage.payload["modeled_controlled_pedestrian_movement_count"] == 1
    assert coverage.payload["modeled_uncontrolled_pedestrian_movement_count"] == 0
    assert coverage.payload["modeled_crossing_edge_count"] == 1
    assert coverage.payload["unmodeled_crossing_edge_count"] == 0
    assert coverage.payload["movement_mode_class_counts"] == {
        "pedestrian": 1,
        "road-motorized": 1,
    }
    binding = next(
        entity
        for entity in snapshot.entities
        if entity.kind == "pedestrian_control_binding"
    )
    assert binding.payload["control_kind"] == "signalized"
    assert binding.payload["source_link_indices"] == (1,)
    assert binding.payload["raw_controller_ids"] == ("tls",)
    assert binding.payload["program_sources"] == ("embedded-net",)
    assert binding.payload["classification_status"] == "unclassified"
    assert len(binding.payload["evidence_refs"]) == 1


def test_protected_vehicle_and_pedestrian_green_is_a_hard_failure() -> None:
    snapshot = _pedestrian_tls_snapshot(protected_together=True)

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.BLOCKED
    assert report.protected_conflict_count == 1
    assert len(report.conflict_graph.conflicts) == 1
    assert {finding.category for finding in report.findings} >= {"protected_green_movement_conflict"}


def test_separated_vehicle_and_pedestrian_phases_pass_independent_safety() -> None:
    snapshot = _pedestrian_tls_snapshot(protected_together=False)

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.PASS
    assert report.automatic_promotion_gate is GateStatus.PASS
    assert report.coverage.modeled_controlled_pedestrian_movement_count == 1
    assert report.coverage.modeled_uncontrolled_pedestrian_movement_count == 0
    assert report.coverage.unmodeled_crossing_edge_count == 0
    assert report.conflict_graph.conflicts[0].reason == "centerline-crossing"
    assert report.findings == ()


def test_malformed_controlled_pedestrian_chain_remains_fail_closed() -> None:
    snapshot = _pedestrian_tls_snapshot(
        protected_together=False,
        include_continuation=False,
    )

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.BLOCKED
    assert report.coverage.unsupported_controlled_connection_count == 1
    assert report.coverage.modeled_pedestrian_movement_count == 0
    assert report.coverage.modeled_controlled_pedestrian_movement_count == 0
    assert report.coverage.modeled_uncontrolled_pedestrian_movement_count == 0
    controlled_finding = next(
        finding
        for finding in report.findings
        if finding.category == "controlled_link_outside_independent_conflict_model"
    )
    assert controlled_finding.witness["connections"][0]["rejection_reasons"] == (
        "pedestrian_continuation_count:0",
        "pedestrian_permission_incompatible",
    )


def test_unknown_unsignalized_crossing_stays_right_of_way_review_only() -> None:
    snapshot = _pedestrian_tls_snapshot(
        protected_together=False,
        controlled_crossing=False,
    )

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.REVIEW
    assert report.coverage.modeled_pedestrian_movement_count == 1
    assert report.coverage.modeled_controlled_pedestrian_movement_count == 0
    assert report.coverage.modeled_uncontrolled_pedestrian_movement_count == 1
    assert report.coverage.modeled_unsignalized_pedestrian_movement_count == 1
    assert report.coverage.modeled_crossing_edge_count == 1
    assert report.coverage.unmodeled_crossing_edge_count == 0
    assert report.coverage.unsupported_controlled_connection_count == 0
    assert report.protected_conflict_count == 0
    assert len(report.conflict_graph.conflicts) == 1
    assert (
        "pedestrian_unsignalized_right_of_way_not_independently_verified"
        in report.limitations
    )
    assert {finding.category for finding in report.findings} >= {
        "pedestrian_vehicle_conflict_requires_right_of_way_review"
    }
    pedestrian_id = next(
        entity.stable_entity_id
        for entity in snapshot.entities
        if entity.kind == "movement"
        and entity.payload["variants"][0].get("movement_kind")
        == "pedestrian-crossing-occupancy"
    )
    controlled_ids = {
        movement_id
        for entity in snapshot.entities
        if entity.kind == "signal_group"
        for movement_id in entity.payload["movement_ids"]
    }
    assert pedestrian_id not in controlled_ids
    binding = next(
        entity
        for entity in snapshot.entities
        if entity.kind == "pedestrian_control_binding"
    )
    assert binding.payload["movement_id"] == pedestrian_id
    assert binding.payload["control_kind"] == "unknown-unsignalized"
    assert binding.payload["source_link_indices"] == ()
    assert binding.payload["raw_controller_ids"] == ()
    assert binding.payload["program_sources"] == ("not-applicable",)


def test_controlled_pedestrian_without_program_is_not_downgraded_to_unsignalized() -> None:
    snapshot = _pedestrian_tls_snapshot(
        protected_together=False,
        include_tls_program=False,
    )

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.BLOCKED
    assert report.coverage.modeled_controlled_pedestrian_movement_count == 1
    assert report.coverage.modeled_uncontrolled_pedestrian_movement_count == 0
    assert {finding.category for finding in report.findings} >= {
        "controlled_pedestrian_effective_program_unresolved"
    }
    binding = next(
        entity
        for entity in snapshot.entities
        if entity.kind == "pedestrian_control_binding"
    )
    assert binding.payload["program_sources"] == ("unresolved",)
    assert not {
        finding.category
        for finding in report.findings
        if finding.category.startswith("pedestrian_vehicle")
    }


def test_pedestrian_link_index2_is_still_fail_closed() -> None:
    snapshot = _pedestrian_tls_snapshot(
        protected_together=False,
        link_index2=True,
    )

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.BLOCKED
    assert report.coverage.link_index2_connection_count == 1
    assert {finding.category for finding in report.findings} >= {"link_index2_outside_independent_conflict_model"}


def test_pedestrian_movement_identity_ignores_internal_facility_ids() -> None:
    first = _pedestrian_tls_snapshot(
        protected_together=False,
        facility_prefix=":first",
    )
    second = _pedestrian_tls_snapshot(
        protected_together=False,
        facility_prefix=":renumbered",
    )

    def pedestrian_movement_id(snapshot: CanonicalNetworkSnapshot) -> str:
        return next(
            entity.stable_entity_id
            for entity in snapshot.entities
            if entity.kind == "movement"
            and entity.payload["variants"][0].get("movement_kind") == "pedestrian-crossing-occupancy"
        )

    assert pedestrian_movement_id(first) == pedestrian_movement_id(second)


def test_uncontrolled_pedestrian_identity_ignores_internal_facility_ids() -> None:
    first = _pedestrian_tls_snapshot(
        protected_together=False,
        controlled_crossing=False,
        facility_prefix=":first",
    )
    second = _pedestrian_tls_snapshot(
        protected_together=False,
        controlled_crossing=False,
        facility_prefix=":renumbered",
    )

    def pedestrian_movement_id(snapshot: CanonicalNetworkSnapshot) -> str:
        return next(
            entity.stable_entity_id
            for entity in snapshot.entities
            if entity.kind == "movement"
            and entity.payload["variants"][0].get("movement_kind")
            == "pedestrian-crossing-occupancy"
        )

    assert pedestrian_movement_id(first) == pedestrian_movement_id(second)


def test_rejected_owner_unresolved_crossing_has_stable_raw_id_free_review_subject() -> None:
    first = _pedestrian_tls_snapshot(
        protected_together=False,
        controlled_crossing=False,
        facility_prefix=":first",
        declare_facility_ownership=False,
    )
    second = _pedestrian_tls_snapshot(
        protected_together=False,
        controlled_crossing=False,
        facility_prefix=":renumbered",
        declare_facility_ownership=False,
    )

    def review_subject(snapshot: CanonicalNetworkSnapshot) -> dict:
        records = [
            record
            for entity in snapshot.entities
            if entity.kind == "safety_coverage"
            for record in entity.payload.get(
                "unmodeled_pedestrian_crossings",
                (),
            )
        ]
        assert len(records) == 1
        return records[0]

    first_subject = review_subject(first)
    second_subject = review_subject(second)
    assert first_subject["review_subject_id"] == second_subject[
        "review_subject_id"
    ]
    assert first_subject["physical_cell_id"] is None
    assert first_subject["rejection_reasons"] == (
        "pedestrian_crossed_ports_empty",
        "pedestrian_destination_owner_unresolved",
        "pedestrian_owner_unresolved",
    )
    serialized = json.dumps(first_subject, sort_keys=True)
    assert ":first" not in serialized
    assert ":renumbered" not in serialized

    def coverage_gap(snapshot: CanonicalNetworkSnapshot) -> dict:
        gaps = [
            entity.payload
            for entity in snapshot.entities
            if entity.kind == "pedestrian_coverage_gap"
        ]
        assert len(gaps) == 1
        return gaps[0]

    first_gap = coverage_gap(first)
    second_gap = coverage_gap(second)
    assert first_gap["coverage_gap_id"] == second_gap["coverage_gap_id"]
    assert first_gap["source_crossing_edge_signature"] == second_gap[
        "source_crossing_edge_signature"
    ]

    report = audit_independent_movement_safety(first)

    assert report.status is GateStatus.REVIEW
    assert report.coverage.unmodeled_crossing_edge_count == 1
    assert report.coverage.unmodeled_pedestrian_review_subject_count == 1
    assert report.coverage.pedestrian_coverage_gap_count == 1
    coverage_gap = report.coverage.pedestrian_coverage_gaps[0]
    assert coverage_gap.position_xy == (0.0, 0.0)
    assert coverage_gap.primary_classification == "safety-coverage-blocker"
    assert coverage_gap.review_subject_id == first_subject[
        "review_subject_id"
    ]
    assert not {
        finding.category
        for finding in report.findings
        if finding.category
        == "unmodeled_pedestrian_review_subject_closure_mismatch"
    }
    assert not {
        finding.category
        for finding in report.findings
        if finding.category == "pedestrian_coverage_gap_closure_mismatch"
    }


def test_crossed_edge_neighbour_cell_does_not_claim_crossing_ownership() -> None:
    network = parse_net_xml(
        ET.fromstring(
            """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0"/></edge>
  <edge id=":j_c0" function="crossing" crossingEdges="in out">
    <lane id=":j_c0_0" index="0" allow="pedestrian" shape="0,-1 0,1"/>
  </edge>
  <junction id="a" type="dead_end" incLanes="" intLanes=""/>
  <junction id="j" type="traffic_light" incLanes="in_0" intLanes=":j_c0_0"/>
  <junction id="b" type="traffic_light" incLanes="out_0" intLanes=""/>
</net>"""
        )
    )
    owner_cell = stable_id("cell", {"junction": "j"})
    neighbour_cell = stable_id("cell", {"junction": "b"})

    owners = infer_pedestrian_facility_owners(
        network,
        junction_cell_ids={"j": owner_cell, "b": neighbour_cell},
    )

    assert owners[":j_c0"].physical_cell_id == owner_cell
    assert owners[":j_c0"].junction_id == "j"


def test_lane_envelope_proximity_is_review_not_a_confirmed_safety_failure() -> None:
    snapshot, _, _ = _crossing_snapshot(
        shape_a=((-1.0, 0.0), (1.0, 0.0)),
        shape_b=((0.0, 3.2), (1.0, 2.8)),
    )

    report = audit_independent_movement_safety(snapshot)

    assert len(report.conflict_graph.conflicts) == 1
    assert report.conflict_graph.conflicts[0].certainty == "potential"
    assert report.status is GateStatus.REVIEW
    assert report.protected_conflict_count == 0
    assert report.potential_signal_conflict_count == 1
    assert {finding.category for finding in report.findings} >= {"protected_green_potential_envelope_conflict"}


def test_permissive_conflict_requires_yield_review_not_a_claimed_safety_defect() -> None:
    snapshot, _, _ = _crossing_snapshot(protected_both=False)
    program = next(entity for entity in snapshot.entities if entity.kind == "controller_program")
    phases = program.payload["phases"]
    phases[0]["group_states"][1]["states"] = ["g"]

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.REVIEW
    assert report.automatic_promotion_gate is GateStatus.BLOCKED
    assert report.permissive_without_yield_count == 0
    assert report.protected_conflict_count == 0
    assert {finding.category for finding in report.findings} >= {
        "permissive_conflict_requires_independent_yield_review"
    }
    assert all(finding.severity.value != "safety" for finding in report.findings)


def test_permissive_conflict_passes_with_explicit_request_yield_relation() -> None:
    snapshot, movement_a, movement_b = _crossing_snapshot(
        protected_both=False
    )
    movements = {
        entity.stable_entity_id: entity
        for entity in snapshot.entities
        if entity.kind == "movement"
    }
    movements[movement_a].payload["variants"][0]["request_index"] = 0
    movements[movement_b].payload["variants"][0]["request_index"] = 1
    requests = next(
        entity
        for entity in snapshot.entities
        if entity.kind == "request_foes"
    ).payload["request_rows"]
    requests[0].update(response="00", foes="10")
    requests[1].update(response="01", foes="01")
    program = next(
        entity
        for entity in snapshot.entities
        if entity.kind == "controller_program"
    )
    program.payload["phases"][0]["group_states"][1]["states"] = ["g"]

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.PASS
    assert report.permissive_without_yield_count == 0
    assert report.findings == ()


def test_permissive_conflict_blocks_when_rightmost_request_bit_is_clear() -> None:
    snapshot, movement_a, movement_b = _crossing_snapshot(
        protected_both=False
    )
    movements = {
        entity.stable_entity_id: entity
        for entity in snapshot.entities
        if entity.kind == "movement"
    }
    movements[movement_a].payload["variants"][0]["request_index"] = 0
    movements[movement_b].payload["variants"][0]["request_index"] = 1
    requests = next(
        entity
        for entity in snapshot.entities
        if entity.kind == "request_foes"
    ).payload["request_rows"]
    requests[0].update(response="00", foes="10")
    # The leftmost bit represents request index 1, not request index 0.
    requests[1].update(response="10", foes="01")
    program = next(
        entity
        for entity in snapshot.entities
        if entity.kind == "controller_program"
    )
    program.payload["phases"][0]["group_states"][1]["states"] = ["g"]

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.BLOCKED
    assert report.permissive_without_yield_count == 1
    finding = next(
        finding
        for finding in report.findings
        if finding.category == "permissive_conflict_without_yield_relation"
    )
    assert finding.severity.value == "safety"
    assert finding.witness["request_relation"]["bit_order"] == (
        "rightmost-bit-is-index-zero"
    )


def test_same_approach_adjacent_envelopes_are_not_conflicts() -> None:
    snapshot, movement_a, movement_b = _crossing_snapshot(
        shape_a=((-1.0, 0.0), (1.0, 0.0)),
        shape_b=((0.0, 3.2), (1.0, 2.8)),
    )
    source_port_id = stable_id("port", {"test": "shared-approach"})
    for movement in (
        entity
        for entity in snapshot.entities
        if entity.kind == "movement"
        and entity.stable_entity_id in {movement_a, movement_b}
    ):
        movement.payload["variants"][0][
            "source_boundary_port_id"
        ] = source_port_id

    graph = build_movement_conflict_graph(snapshot)

    assert graph.conflicts == ()


def test_canonicalizer_binds_vehicle_movement_to_request_not_tls_index() -> None:
    snapshot = _pedestrian_tls_snapshot(protected_together=False)
    vehicle_movement = next(
        entity
        for entity in snapshot.entities
        if entity.kind == "movement"
        and entity.payload["variants"][0].get("movement_kind")
        != "pedestrian-crossing-occupancy"
    )

    assert vehicle_movement.payload["variants"][0]["request_index"] == 0


def test_no_internal_links_geometry_is_reviewed_as_unavailable_by_design() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j">
    <lane id="in_0" index="0" allow="passenger" width="3.2" shape="-10,0 0,0"/>
  </edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" allow="passenger" width="3.2" shape="0,0 10,0"/>
  </edge>
  <junction id="a" type="dead_end" incLanes="" intLanes=""/>
  <junction id="j" type="priority" incLanes="in_0" intLanes="">
    <request index="0" response="0" foes="0"/>
  </junction>
  <junction id="b" type="dead_end" incLanes="out_0" intLanes=""/>
  <connection from="in" to="out" fromLane="0" toLane="0" dir="s" state="M"/>
</net>"""
    )
    snapshot = canonicalize_raw_network(
        parse_net_xml(root),
        traffic_side=TrafficSide.RIGHT,
    )

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.REVIEW
    assert report.automatic_promotion_gate is GateStatus.BLOCKED
    assert report.conflict_graph.geometry_missing_movement_ids == ()
    assert len(report.conflict_graph.geometry_unavailable_by_design_movement_ids) == 1
    assert report.limitations == ("movement_geometry_unavailable_no_internal_links",)
    categories = {finding.category for finding in report.findings}
    assert "movement_geometry_unavailable_no_internal_links" in categories
    assert "movement_geometry_missing_for_independent_safety" not in categories


def test_permission_incompatible_path_is_blocked_and_excluded_from_conflicts() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j">
    <lane id="in_0" index="0" allow="passenger" width="3.2" shape="-10,0 0,0"/>
  </edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" allow="passenger" width="3.2" shape="10,0 20,0"/>
  </edge>
  <edge id=":j_0" function="internal">
    <lane id=":j_0_0" index="0" allow="bicycle" width="1.0" shape="0,0 10,0"/>
  </edge>
  <junction id="a" type="dead_end" incLanes="" intLanes=""/>
  <junction id="j" type="priority" incLanes="in_0" intLanes=":j_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <junction id="b" type="dead_end" incLanes="out_0" intLanes=""/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" allow="bicycle" dir="s"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s"/>
</net>"""
    )
    snapshot = canonicalize_raw_network(
        parse_net_xml(root),
        traffic_side=TrafficSide.RIGHT,
    )

    movement = next(entity for entity in snapshot.entities if entity.kind == "movement")
    variant = movement.payload["variants"][0]
    assert variant["mode_classes"] == ("incompatible",)
    path = next(entity for entity in snapshot.entities if entity.kind == "internal_path").payload["path_variants"][0][
        "path"
    ]
    assert path["permission_contract"] == {
        "coverage": "complete",
        "status": "fail",
        "basis": "explicit_allow_intersection",
        "allow": (),
        "disallow": (),
        "element_count": 5,
    }

    report = audit_independent_movement_safety(snapshot)

    assert report.status is GateStatus.BLOCKED
    assert report.conflict_graph.movement_ids == ()
    assert report.conflict_graph.geometry_missing_movement_ids == ()
    assert report.coverage.movement_mode_class_counts["incompatible"] == 1
    assert "movement_path_permission_empty" in {finding.category for finding in report.findings}


def test_shared_inner_vertices_are_confirmed_centerline_crossings() -> None:
    snapshot, _, _ = _crossing_snapshot(
        shape_a=((-10.0, 0.0), (0.0, 0.0), (10.0, 0.0)),
        shape_b=((0.0, -10.0), (0.0, 0.0), (0.0, 10.0)),
    )

    graph = build_movement_conflict_graph(snapshot)

    assert len(graph.conflicts) == 1
    assert graph.conflicts[0].reason == "centerline-crossing"
    assert graph.conflicts[0].certainty == "confirmed"
    assert graph.conflicts[0].crossing_angle_deg == 90.0


def test_spatial_broad_phase_is_exactly_equivalent_to_exhaustive_pairs() -> None:
    snapshot = _movement_only_snapshot(
        (
            ((-10.0, 0.0), (10.0, 0.0)),
            ((0.0, -10.0), (0.0, 10.0)),
            ((90.0, 100.0), (110.0, 100.0)),
            ((100.0, 90.0), (100.0, 110.0)),
            ((1000.0, 1000.0), (1010.0, 1000.0)),
            ((2000.0, 2000.0), (2010.0, 2000.0)),
        ),
        shared_destination=(4, 5),
    )

    broad = build_movement_conflict_graph(snapshot)
    exhaustive = build_movement_conflict_graph(
        snapshot,
        use_spatial_broad_phase=False,
    )

    assert broad.conflicts == exhaustive.conflicts
    assert {conflict.reason for conflict in broad.conflicts} == {
        "centerline-crossing",
        "shared-destination-merge",
    }


def test_spatial_broad_phase_matches_seeded_random_exhaustive_reference() -> None:
    generator = random.Random(20260714)
    shapes = tuple(
        (
            (generator.uniform(-250, 250), generator.uniform(-250, 250)),
            (generator.uniform(-250, 250), generator.uniform(-250, 250)),
        )
        for _ in range(48)
    )
    snapshot = _movement_only_snapshot(shapes)

    broad = build_movement_conflict_graph(snapshot)
    exhaustive = build_movement_conflict_graph(
        snapshot,
        use_spatial_broad_phase=False,
    )

    assert broad.conflicts == exhaustive.conflicts
