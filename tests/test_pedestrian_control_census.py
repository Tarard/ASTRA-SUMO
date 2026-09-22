from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from torii_sumo.corridor.canonicalizer import (
    CanonicalEntity,
    CanonicalNetworkSnapshot,
)
from torii_sumo.corridor.enums import GateStatus, TrafficSide
from torii_sumo.corridor.ids import stable_id
from torii_sumo.corridor.pedestrian_control_census import (
    build_effective_tls_program_inventory,
    classify_controlled_pedestrian_bindings,
)


def _entity(
    kind: str,
    entity_id: str,
    payload: dict,
    *,
    owner_cell_ids: tuple[str, ...] = (),
) -> CanonicalEntity:
    return CanonicalEntity(
        kind=kind,
        stable_entity_id=entity_id,
        semantic_signature=stable_id(
            "signature",
            {"kind": kind, "payload": payload},
        ),
        owner_physical_cell_ids=owner_cell_ids,
        payload=payload,
    )


def _net_xml(
    *,
    controller_id: str = "tls",
    link_index: int = 0,
    controller_type: str | None = None,
    phase_state: str = "G",
    second_program: bool = False,
) -> str:
    programs = ""
    if controller_type is not None:
        programs = (
            f'<tlLogic id="{controller_id}" type="{controller_type}" '
            'programID="0" offset="0">'
            f'<phase duration="30" state="{phase_state}"/>'
            "</tlLogic>"
        )
        if second_program:
            programs += (
                f'<tlLogic id="{controller_id}" type="{controller_type}" '
                'programID="1" offset="0">'
                f'<phase duration="30" state="{phase_state}"/>'
                "</tlLogic>"
            )
    return (
        "<net>"
        '<connection from="walk" to="cross" fromLane="0" toLane="0" '
        f'tl="{controller_id}" linkIndex="{link_index}" dir="s" state="M"/>'
        f"{programs}"
        "</net>"
    )


def _snapshot(
    net_file: Path,
    *,
    junction_type: str,
    raw_controller_ids: tuple[str, ...] = ("tls",),
    link_indices: tuple[int, ...] = (0,),
    link_index2_present: bool = False,
    shared_controller: bool = False,
) -> CanonicalNetworkSnapshot:
    cell_a = stable_id("cell", {"cell": "a"})
    movement_a = stable_id("movement", {"movement": "pedestrian-a"})
    binding_a = stable_id(
        "binding",
        {
            "movement_id": movement_a,
            "control_kind": "signalized",
        },
    )
    entities = [
        _entity(
            "physical_cell",
            cell_a,
            {
                "junction_type": junction_type,
                "position_xy": (10.0, 20.0),
                "requests": (
                    {
                        "index": 0,
                        "response": "0",
                        "foes": "0",
                        "cont": "0",
                    },
                ),
            },
            owner_cell_ids=(cell_a,),
        ),
        _entity(
            "movement",
            movement_a,
            {"movement_kind": "pedestrian-crossing-occupancy"},
            owner_cell_ids=(cell_a,),
        ),
        _entity(
            "pedestrian_control_binding",
            binding_a,
            {
                "movement_id": movement_a,
                "control_kind": "signalized",
                "source_link_indices": link_indices,
                "multiple_source_indices": len(link_indices) > 1,
                "raw_controller_ids": raw_controller_ids,
                "owner_junction_types": (junction_type,),
                "program_sources": ("unclassified",),
                "source_connection_states": ("M",),
                "link_index2_present": link_index2_present,
                "evidence_refs": (
                    stable_id("evidence", {"movement": movement_a}),
                ),
                "classification_status": "unclassified",
            },
            owner_cell_ids=(cell_a,),
        ),
    ]
    raw_id_maps = {
        "connection_index_to_movement": {"0": movement_a},
        "tls_to_controller": {},
    }
    if shared_controller:
        cell_b = stable_id("cell", {"cell": "b"})
        movement_b = stable_id(
            "movement",
            {"movement": "pedestrian-b"},
        )
        binding_b = stable_id(
            "binding",
            {
                "movement_id": movement_b,
                "control_kind": "signalized",
            },
        )
        controller_id = stable_id(
            "controller",
            {"controller": "tls"},
        )
        signal_group_id = stable_id(
            "signal_group",
            {"movement": movement_b},
        )
        entities.extend(
            (
                _entity(
                    "physical_cell",
                    cell_b,
                    {
                        "junction_type": "priority",
                        "position_xy": (30.0, 40.0),
                        "requests": (),
                    },
                    owner_cell_ids=(cell_b,),
                ),
                _entity(
                    "movement",
                    movement_b,
                    {"movement_kind": "pedestrian-crossing-occupancy"},
                    owner_cell_ids=(cell_b,),
                ),
                _entity(
                    "pedestrian_control_binding",
                    binding_b,
                    {
                        "movement_id": movement_b,
                        "control_kind": "signalized",
                        "source_link_indices": (0,),
                        "multiple_source_indices": False,
                        "raw_controller_ids": ("tls",),
                        "owner_junction_types": ("priority",),
                        "program_sources": ("embedded-net",),
                        "source_connection_states": ("M",),
                        "link_index2_present": False,
                        "evidence_refs": (),
                        "classification_status": "unclassified",
                    },
                    owner_cell_ids=(cell_b,),
                ),
                _entity(
                    "signal_group",
                    signal_group_id,
                    {
                        "controller_id": controller_id,
                        "movement_ids": (movement_b,),
                        "source_link_indices": (0,),
                        "multiple_source_indices": False,
                    },
                    owner_cell_ids=(cell_a, cell_b),
                ),
            )
        )
        raw_id_maps["connection_index_to_movement"]["1"] = movement_b
        raw_id_maps["tls_to_controller"]["tls"] = controller_id
    return CanonicalNetworkSnapshot(
        traffic_side=TrafficSide.RIGHT,
        source_sha256=hashlib.sha256(net_file.read_bytes()).hexdigest(),
        entities=tuple(entities),
        raw_id_maps=raw_id_maps,
    )


@pytest.mark.parametrize(
    (
        "junction_type",
        "controller_type",
        "link_index",
        "link_index2_present",
        "shared_controller",
        "expected_class",
        "expected_hard",
    ),
    (
        (
            "rail_crossing",
            None,
            0,
            False,
            False,
            "runtime-special-controller",
            False,
        ),
        (
            "priority",
            None,
            0,
            False,
            False,
            "ordinary-program-truly-absent",
            True,
        ),
        (
            "priority",
            "static",
            2,
            False,
            False,
            "program-present-link-invalid",
            True,
        ),
        (
            "priority",
            "static",
            0,
            False,
            True,
            "shared-controller-scope-incomplete",
            False,
        ),
        (
            "priority",
            "actuated",
            0,
            False,
            False,
            "unsupported-program-form",
            False,
        ),
        (
            "priority",
            "static",
            0,
            False,
            False,
            "stale-or-ambiguous-control-reference",
            False,
        ),
    ),
)
def test_pcb_primary_class_decision_tree(
    tmp_path: Path,
    junction_type: str,
    controller_type: str | None,
    link_index: int,
    link_index2_present: bool,
    shared_controller: bool,
    expected_class: str,
    expected_hard: bool,
) -> None:
    net_file = tmp_path / "case.net.xml"
    net_file.write_text(
        _net_xml(
            controller_type=controller_type,
            link_index=link_index,
        ),
        encoding="utf-8",
    )
    snapshot = _snapshot(
        net_file,
        junction_type=junction_type,
        link_indices=(link_index,),
        link_index2_present=link_index2_present,
        shared_controller=shared_controller,
    )

    inventory = build_effective_tls_program_inventory(net_file)
    first = classify_controlled_pedestrian_bindings(snapshot, inventory)
    second = classify_controlled_pedestrian_bindings(snapshot, inventory)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.automatic_promotion_gate is GateStatus.BLOCKED
    assert first.unresolved_binding_count == 1
    assert first.assessments[0].primary_class == expected_class
    assert first.assessments[0].hard_structural_error is expected_hard
    assert first.assessments[0].review_position_xy == (10.0, 20.0)
    assert first.assessments[0].raw_connection_xml


def test_effective_external_program_is_not_reported_missing(
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "case.net.xml"
    net_file.write_text(_net_xml(), encoding="utf-8")
    additional_file = tmp_path / "programs.add.xml"
    additional_file.write_text(
        "<additional>"
        '<tlLogic id="tls" type="static" programID="external" offset="0">'
        '<phase duration="30" state="G"/>'
        "</tlLogic>"
        "</additional>",
        encoding="utf-8",
    )
    config_file = tmp_path / "case.sumocfg"
    config_file.write_text(
        "<configuration><input>"
        '<additional-files value="programs.add.xml"/>'
        "</input></configuration>",
        encoding="utf-8",
    )
    snapshot = _snapshot(net_file, junction_type="priority")

    inventory = build_effective_tls_program_inventory(
        net_file,
        sumocfg_file=config_file,
    )
    census = classify_controlled_pedestrian_bindings(
        snapshot,
        inventory,
    )

    assessment = census.assessments[0]
    assert assessment.primary_class == "external-program-present"
    assert assessment.hard_structural_error is False
    assert assessment.program_sources == ("additional-file",)
    assert assessment.indexed_states_by_program[
        assessment.program_evidence_ids[0]
    ] == ("G",)


def test_link_index2_is_a_hard_link_binding_failure(
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "case.net.xml"
    net_file.write_text(
        _net_xml(controller_type="static"),
        encoding="utf-8",
    )
    snapshot = _snapshot(
        net_file,
        junction_type="priority",
        link_index2_present=True,
    )

    census = classify_controlled_pedestrian_bindings(
        snapshot,
        build_effective_tls_program_inventory(net_file),
    )

    assessment = census.assessments[0]
    assert assessment.primary_class == "program-present-link-invalid"
    assert "link_index2_present" in assessment.secondary_flags
    assert assessment.hard_structural_error is True
