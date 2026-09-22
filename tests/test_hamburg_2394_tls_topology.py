from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from torii_sumo.core.digital_twin import MapConnection, MapLane, SignalStream
from torii_sumo.core.digital_twin_mapping import MapLaneBinding
from torii_sumo.core.hamburg_2394_tls_topology import (
    EXPECTED_CURRENT_MAIN_ROUTING,
    EXPECTED_MAP_LANE_TO_SUMO_LANE,
    EXPECTED_MOVEMENTS,
    MAIN_OWNER_ID,
    PASSIVE_OWNER_IDS,
    SIGNAL_OWNER_IDS,
    Hamburg2394TlsTopologyError,
    PhysicalLink,
    build_hamburg_2394_tls_topology_plan,
    compile_hamburg_2394_tls_topology,
)
from torii_sumo.core.ocit_c import (
    OcitCConfig,
    OcitGroupSignal,
    OcitMotorSignalGroup,
    OcitPhase,
    OcitVehicleTopologyInventory,
    OcitVehicleTopologyMovement,
)


def _write_candidate(path: Path, *, add_unofficial_main_link: bool = False) -> None:
    root = ET.Element("net")
    junction_ids = {
        *SIGNAL_OWNER_IDS,
        *PASSIVE_OWNER_IDS,
        "outside_w",
        "outside_e",
        "outside_s",
        "outside_n",
    }
    for junction_id in sorted(junction_ids):
        ET.SubElement(root, "junction", {"id": junction_id, "type": "priority"})

    edges = (
        ("47854310#2", "outside_w", "759714726", 3),
        ("60578519", "759714726", MAIN_OWNER_ID, 3),
        ("9702432#0", "outside_e", "cluster_2761334279_757036795", 2),
        ("9702432#2", "cluster_2761334279_757036795", MAIN_OWNER_ID, 2),
        ("381540198#1", "3847369288", MAIN_OWNER_ID, 2),
        ("-381540198#1", MAIN_OWNER_ID, "3847369288", 1),
        ("193847534#0", MAIN_OWNER_ID, "3847369285", 2),
        ("193847534#1", "3847369285", "outside_e", 2),
        ("554713077", MAIN_OWNER_ID, "outside_w", 1),
    )
    for edge_id, from_node, to_node, lane_count in edges:
        edge = ET.SubElement(
            root,
            "edge",
            {"id": edge_id, "from": from_node, "to": to_node},
        )
        for lane_index in range(lane_count):
            ET.SubElement(
                edge,
                "lane",
                {"id": f"{edge_id}_{lane_index}", "index": str(lane_index)},
            )

    links = [
        PhysicalLink("47854310#2", index, "60578519", index)
        for index in range(3)
    ]
    links.extend(
        PhysicalLink("9702432#0", index, "9702432#2", index)
        for index in range(2)
    )
    links.extend(EXPECTED_CURRENT_MAIN_ROUTING)
    links.extend(
        (
            PhysicalLink("193847534#0", 0, "193847534#1", 0),
            PhysicalLink("193847534#0", 1, "193847534#1", 1),
        )
    )
    if add_unofficial_main_link:
        links.append(PhysicalLink("381540198#1", 0, "554713077", 0))
    for link in links:
        ET.SubElement(
            root,
            "connection",
            {
                "from": link.from_edge,
                "fromLane": str(link.from_lane),
                "to": link.to_edge,
                "toLane": str(link.to_lane),
            },
        )
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _map_evidence() -> tuple[list[MapLane], list[MapConnection]]:
    lanes = [
        MapLane(
            node_id="2394",
            lane_id=lane_id,
            lane_type="vehicle",
            ingress_approach="1" if lane_id in {"2", "3", "6", "7", "10", "11", "12"} else "",
            egress_approach="" if lane_id in {"2", "3", "6", "7", "10", "11", "12"} else "1",
            ref_longitude=0.0,
            ref_latitude=0.0,
            points_m=((0.0, 0.0), (1.0, 0.0)),
        )
        for lane_id in EXPECTED_MAP_LANE_TO_SUMO_LANE
    ]
    lanes.extend(
        (
            MapLane("2394", "1", "bikeLane", "2", "", 0.0, 0.0, ((0.0, 0.0),)),
            MapLane("2394", "13", "bikeLane", "", "4", 0.0, 0.0, ((0.0, 0.0),)),
        )
    )
    pairs = {
        "1": ("10", "9"),
        "2": ("11", "4"),
        "3": ("12", "5"),
        "4": ("1", "13"),
        "5": ("2", "14"),
        "6": ("3", "9"),
        "7": ("6", "4"),
        "8": ("6", "5"),
        "9": ("7", "14"),
    }
    connections = [
        MapConnection("2394", connection_id, ingress, egress, connection_id, "")
        for connection_id, (ingress, egress) in pairs.items()
    ]
    return lanes, connections


def _inventory_and_streams() -> tuple[OcitVehicleTopologyInventory, list[SignalStream]]:
    movements: list[OcitVehicleTopologyMovement] = []
    streams: list[SignalStream] = []
    for stream_id, connection_id in enumerate(sorted(EXPECTED_MOVEMENTS, key=int), start=1):
        ingress, egress, expression, tld_group = EXPECTED_MOVEMENTS[connection_id]
        primary_text, secondary_text = expression.removeprefix("P_").split("__S_")
        primary = () if primary_text == "NONE" else tuple(primary_text.split("+"))
        secondary = () if secondary_text == "NONE" else tuple(secondary_text.split("+"))
        movements.append(
            OcitVehicleTopologyMovement(
                node_id="2394",
                connection_id=connection_id,
                ingress_lane_id=ingress,
                egress_lane_id=egress,
                map_signal_group=connection_id,
                primary_motor_groups=primary,
                secondary_motor_groups=secondary,
                topology_control_key=expression,
                observed_stream_ids=(stream_id,),
                observed_signal_groups=(tld_group,),
            )
        )
        streams.append(
            SignalStream(
                stream_id=stream_id,
                thing_id=None,
                node_id="2394",
                connection_id=connection_id,
                ingress_lane_id=ingress,
                egress_lane_id=egress,
                lane_type="KFZ",
                signal_group=tld_group,
                layer_name="primary_signal",
                name=f"2394-{connection_id}",
            )
        )
    inventory = OcitVehicleTopologyInventory(
        status="pass",
        source_movement_count=9,
        excluded_non_vehicle_movement_count=1,
        movement_count=8,
        observed_stream_count=8,
        observed_match_count=8,
        group_resolution_policy="test",
        movements=tuple(movements),
        topology_streams=tuple(streams),
    )
    return inventory, streams


def _ocit() -> OcitCConfig:
    phase_images = {
        "Phase 1": ("30", "03", "00", "03", "03", "00", "30"),
        "Phase 2": ("03", "03", "00", "30", "30", "30", "03"),
        "Phase 3": ("30", "30", "30", "03", "03", "00", "03"),
    }
    return OcitCConfig(
        node_id="2394",
        node_name="test",
        motor_signal_groups=tuple(
            OcitMotorSignalGroup(f"K{index}", str(index), (f"K{index}",))
            for index in range(1, 8)
        ),
        phases=tuple(
            OcitPhase(
                name,
                str(index),
                tuple(
                    OcitGroupSignal(f"K{group}", image)
                    for group, image in enumerate(images, start=1)
                ),
            )
            for index, (name, images) in enumerate(phase_images.items(), start=1)
        ),
        signal_program_ids=("1", "2", "3"),
        saturday_plans=(),
        has_vehicle_actuated_control=True,
        saturday_vehicle_actuated=True,
        saturday_plan_semantics="program selection only",
        source_path="test.xml",
        vehicle_movements=(),
    )


def _lane_bindings() -> list[MapLaneBinding]:
    return [
        MapLaneBinding(
            node_id="2394",
            map_lane_id=map_lane_id,
            map_lane_type="vehicle",
            map_role=(
                "ingress"
                if map_lane_id in {"2", "3", "6", "7", "10", "11", "12"}
                else "egress"
            ),
            sumo_edge=sumo_lane.rsplit("_", 1)[0],
            sumo_lane=sumo_lane,
            lane_position=1.0,
            distance_m=1.0,
            heading_error_deg=1.0,
            mapping_confidence="high",
            mapping_status="active",
        )
        for map_lane_id, sumo_lane in EXPECTED_MAP_LANE_TO_SUMO_LANE.items()
    ]


def _compile(path: Path) -> dict[str, object]:
    map_lanes, map_connections = _map_evidence()
    inventory, streams = _inventory_and_streams()
    return compile_hamburg_2394_tls_topology(
        source_net_file=path,
        map_lanes=map_lanes,
        map_connections=map_connections,
        ocit=_ocit(),
        inventory=inventory,
        lane_bindings=_lane_bindings(),
        observed_streams=streams,
        controller_evidence={
            "controller_domain_count": 1,
            "controller_domain_ids": ["2394"],
            "technical_subnode_ids": ["1"],
        },
    )


def test_compiler_proves_hard_topology_and_keeps_timing_blocked(tmp_path: Path) -> None:
    candidate = tmp_path / "first-pass.net.xml"
    _write_candidate(candidate)
    source_bytes = candidate.read_bytes()

    plan = _compile(candidate)

    assert plan["hard_acceptance"] == {
        "vehicle_movement_count": 8,
        "control_expression_count": 6,
        "controlled_physical_link_count": 8,
        "signal_owner_count": 3,
        "controller_count": 1,
        "status": "pass",
    }
    assert plan["controller"]["signal_owner_ids"] == list(SIGNAL_OWNER_IDS)
    assert plan["controller"]["passive_priority_owner_ids"] == list(PASSIVE_OWNER_IDS)
    assert plan["routing_patch"]["resulting_main_owner_connection_count"] == 8
    assert plan["materialization"]["status"] == "not_run"
    assert plan["operational_signal_timing"]["status"] == "blocked"
    assert plan["operational_signal_timing"]["ocit_phase_images_are_historical_durations"] is False
    assert plan["operational_signal_timing"]["historical_two_hour_replay_status"] == "not_run"
    assert candidate.read_bytes() == source_bytes


def test_compiler_fails_closed_when_first_pass_main_routing_has_an_extra_link(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "unexpected.net.xml"
    _write_candidate(candidate, add_unofficial_main_link=True)

    with pytest.raises(Hamburg2394TlsTopologyError, match="main-owner first-pass routing differs"):
        _compile(candidate)


def test_file_builder_checks_hashes_before_parsing_evidence(tmp_path: Path) -> None:
    files = [tmp_path / name for name in ("source.net.xml", "map.xml", "ocit.xml", "class.json")]
    for path in files:
        path.write_text("x", encoding="utf-8")

    with pytest.raises(Hamburg2394TlsTopologyError, match="source_net SHA-256 mismatch"):
        build_hamburg_2394_tls_topology_plan(
            source_net_file=files[0],
            map_file=files[1],
            ocit_file=files[2],
            classification_file=files[3],
            accepted_classification_id="accepted",
            expected_source_sha256="0" * 64,
            expected_map_sha256="0" * 64,
            expected_ocit_sha256="0" * 64,
            expected_classification_sha256="0" * 64,
        )
