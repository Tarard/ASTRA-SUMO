from __future__ import annotations

from collections import Counter
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from torii_sumo.core.digital_twin import MapConnection, MapLane, parse_mapem
from torii_sumo.core.digital_twin_mapping import bind_map_lanes_to_network
from torii_sumo.core.hamburg_movement_path import (
    HAMBURG_SANDTORKAI_CONNECTION_EVIDENCE,
    derive_hamburg_official_movement_paths,
)
from torii_sumo.core.hamburg_teacher_cell import (
    HamburgOfficialMovementPath,
    HamburgTeacherCellContract,
    build_hamburg_teacher_cell_contract,
    build_hamburg_teacher_expression_grouping_variant,
    build_hamburg_official_approach_components,
    derive_hamburg_candidate_cell_boundary,
    materialize_hamburg_single_teacher_cell,
)
from torii_sumo.core.ocit_c import (
    OcitVehicleTopologyInventory,
    OcitVehicleTopologyMovement,
    VEHICLE_TOPOLOGY_GROUP_POLICY,
    build_vehicle_topology_inventory,
    parse_ocit_c,
)
from torii_sumo.intersection.compile_plain import compile_intersection_to_plain


def test_candidate_boundary_uses_sumo_all_permissions(tmp_path: Path) -> None:
    candidate = _write_candidate_net(tmp_path / "candidate.net.xml", west_lane_count=2)
    tree = ET.parse(candidate)
    tree.find("edge[@id='west_in']/lane[@index='0']").set("allow", "all")
    closed = tree.find("edge[@id='west_in']/lane[@index='1']")
    closed.attrib.pop("allow")
    closed.set("disallow", "all")
    tree.write(candidate, encoding="utf-8")
    boundary = derive_hamburg_candidate_cell_boundary(candidate_net_file=candidate, node_id="0228",
        map_lanes=_map_lanes(), movement_paths=_movement_paths())
    assert boundary.passenger_lane_indices_by_edge["west_in"] == (0,)


def test_hamburg_teacher_cell_encodes_official_movements_and_expression_groups(
    tmp_path: Path,
) -> None:
    candidate_net = _write_candidate_net(tmp_path / "candidate.net.xml", west_lane_count=2)
    contract = build_hamburg_teacher_cell_contract(
        node_id="0228",
        map_lanes=_map_lanes(),
        map_connections=_map_connections(),
        topology_inventory=_topology_inventory(),
        movement_paths=_movement_paths(),
        candidate_net_file=candidate_net,
    )

    assert contract.topology_status == "ready_for_scoped_teacher_replay"
    assert contract.review_gates == ()
    assert contract.candidate_junction_id == "j"
    assert contract.candidate_junction_ids == ("j", "m")
    assert contract.expression_index_by_key == {
        "P_K1__S_NONE": 0,
        "P_K2__S_NONE": 1,
    }
    assert contract.expression_compression_budget == 2
    assert len(contract.ir.approaches) == 2
    assert len(contract.ir.movement_matrix.movements) == 3
    ingress_approach = next(
        approach
        for approach in contract.ir.approaches
        if approach.role == "hamburg_official_ingress"
    )
    assert ingress_approach.source_shape_xy == [(-20.0, 1.5), (-1.0, 1.5)]
    assert ingress_approach.endpoint_xy == (-20.0, 1.5)
    assert (
        "teacher_edge_centered_on_official_stopline_cross_section"
        in ingress_approach.direction_evidence
    )
    assert contract.ir.core.center_xy == (-1.0, 1.5)
    assert (
        "teacher_core_is_centroid_of_unique_official_movement_stopline_anchors"
        in contract.ir.control.source
    )
    assert len(contract.approach_components) == 1
    assert Counter(contract.ir.control.link_index_map.values()) == Counter({0: 2, 1: 1})
    assert [phase.state for phase in contract.ir.control.phases] == ["gr", "rg"]
    assert contract.exact_signal_replay_gates == (
        "topology_basis_phases_are_not_an_official_signal_program",
    )

    ingress_rows = [row for row in contract.lane_indices if row.direction == "ingress"]
    assert [(row.official_lane_id, row.teacher_lane_index) for row in ingress_rows] == [
        ("1", 0),
        ("2", 1),
    ]
    assert {
        (str(pair["direction"]), str(pair["candidate_edge_id"]))
        for pair in contract.approach_pairs
    } == {("ingress", "west_in"), ("egress", "east_out")}


def test_hamburg_teacher_cell_reuses_identical_tls_column_compression(tmp_path: Path) -> None:
    candidate_net = _write_candidate_net(tmp_path / "candidate.net.xml", west_lane_count=2)
    contract = build_hamburg_teacher_cell_contract(
        node_id="0228",
        map_lanes=_map_lanes(),
        map_connections=_map_connections(),
        topology_inventory=_topology_inventory(),
        movement_paths=_movement_paths(),
        candidate_net_file=candidate_net,
    )
    artifacts = compile_intersection_to_plain(
        contract.ir,
        tmp_path / "plain",
        "teacher",
        compile_net=False,
    )
    net_file = tmp_path / "teacher.net.xml"
    net_root = ET.Element("net")
    for tl_logic in ET.parse(artifacts.plain_tllogic_file).getroot().findall("tlLogic"):
        net_root.append(tl_logic)
    for connection in ET.parse(artifacts.plain_connection_file).getroot().findall("connection"):
        net_root.append(connection)
    ET.ElementTree(net_root).write(net_file, encoding="utf-8", xml_declaration=True)

    report = build_hamburg_teacher_expression_grouping_variant(
        teacher_net_file=net_file,
        output_dir=tmp_path / "grouped",
        contract=contract,
    )

    assert report["status"] == "pass"
    assert report["tls_signal_grouping_merged_group_count"] == 1
    grouped_root = ET.parse(report["tls_signal_grouping_variant_file"]).getroot()
    grouped_connections = grouped_root.findall("connection")
    assert [connection.attrib["linkIndex"] for connection in grouped_connections] == ["0", "0", "1"]
    assert {len(phase.attrib["state"]) for phase in grouped_root.findall("tlLogic/phase")} == {2}


@pytest.mark.skipif(shutil.which("netconvert") is None, reason="SUMO netconvert is required")
def test_hamburg_single_teacher_materializer_restores_2421_expression_binding_after_netconvert(
    tmp_path: Path,
) -> None:
    map_lanes, map_connections, topology_inventory, movement_paths = _official_2421_fixture()
    contract = build_hamburg_teacher_cell_contract(
        node_id="2421",
        map_lanes=map_lanes,
        map_connections=map_connections,
        topology_inventory=topology_inventory,
        movement_paths=movement_paths,
        candidate_net_file=_write_2421_candidate_net(tmp_path / "candidate_2421.net.xml"),
    )

    assert contract.topology_status == "ready_for_scoped_teacher_replay"
    assert len(contract.approach_components) == 1
    result = materialize_hamburg_single_teacher_cell(
        contract=contract,
        output_dir=tmp_path / "materialized",
        prefix="HH_2421",
    )

    assert result["status"] == "pass"
    assert result["materialization_mode"] == "single_official_approach_cell"
    assert result["official_movement_count"] == 9
    assert result["topology_expression_count"] == 6
    assert result["compiled_controlled_connection_count"] == 9
    assert result["duplicate_controlled_connection_count"] == 0
    assert result["grouped_link_index_count"] == 7
    assert result["foe_separated_link_index_count"] == 1
    assert result["physical_key_control_signature_mismatch_count"] == 0
    assert result["diagnostic_phase_alphabet"] == ["g", "r"]
    assert result["physical_key_control_restore"] == {
        "status": "pass",
        "physical_key_policy": "from,to,fromLane,toLane",
        "requested_controlled_connection_count": 9,
        "restored_controlled_connection_count": 9,
        "restored_controller_id": "HH_2421",
        "replaced_owner_controller_ids": ["HH_2421"],
        "restored_phase_count": 6,
    }

    grouped_root = ET.parse(Path(str(result["grouped_teacher_net_file"]))).getroot()
    grouped_connections = [
        connection
        for connection in grouped_root.findall("connection")
        if connection.attrib.get("tl") == "HH_2421"
        and connection.attrib.get("linkIndex") is not None
    ]
    actual_link_index_by_key = {
        _physical_connection_key(connection): int(connection.attrib["linkIndex"])
        for connection in grouped_connections
    }
    expected_expression_by_key = _expected_expression_by_physical_key(contract)

    assert len(actual_link_index_by_key) == 9
    assert set(actual_link_index_by_key) == set(expected_expression_by_key)
    for left_key, left_expression in expected_expression_by_key.items():
        for right_key, right_expression in expected_expression_by_key.items():
            if actual_link_index_by_key[left_key] == actual_link_index_by_key[right_key]:
                assert left_expression == right_expression
    secondary_k3_expression = contract.expression_index_by_key["P_NONE__S_K3"]
    secondary_k3_keys = sorted(
        key
        for key, expression in expected_expression_by_key.items()
        if expression == secondary_k3_expression
    )
    assert len(secondary_k3_keys) == 2
    assert len({actual_link_index_by_key[key] for key in secondary_k3_keys}) == 2
    assert sorted(Counter(actual_link_index_by_key.values()).values()) == [1, 1, 1, 1, 1, 2, 2]

    grouping_report = result["tls_signal_grouping_report"]
    assert grouping_report["tls_signal_grouping_request_foe_evidence_status"] == "available"
    assert grouping_report["tls_signal_grouping_request_bound_connection_count"] == 9
    assert grouping_report["tls_signal_grouping_blocked_foe_pair_count"] == 1
    control_bindings = grouping_report["tls_signal_grouping_control_key_to_link_indices"][
        "HH_2421"
    ]
    assert set(control_bindings) == set(contract.expression_index_by_key)
    assert len(control_bindings["P_NONE__S_K3"]) == 2

    phases = grouped_root.findall("tlLogic[@id='HH_2421']/phase")
    assert len(phases) == 6
    assert {len(phase.attrib["state"]) for phase in phases} == {7}
    assert all(set(phase.attrib["state"]) <= {"r", "g"} for phase in phases)
    assert all("G" not in phase.attrib["state"] for phase in phases)


def test_hamburg_teacher_cell_blocks_partial_candidate_boundary_lane_coverage(
    tmp_path: Path,
) -> None:
    candidate_net = _write_candidate_net(tmp_path / "candidate.net.xml", west_lane_count=3)
    contract = build_hamburg_teacher_cell_contract(
        node_id="0228",
        map_lanes=_map_lanes(),
        map_connections=_map_connections(),
        topology_inventory=_topology_inventory(),
        movement_paths=_movement_paths(),
        candidate_net_file=candidate_net,
    )

    assert contract.topology_status == "blocked"
    assert contract.review_gates == (
        "candidate_boundary_passenger_lane_subset:west_in:used=[0, 1]:expected=[0, 1, 2]",
    )


def test_hamburg_teacher_cell_keeps_official_lanes_when_osm_alias_is_shared(
    tmp_path: Path,
) -> None:
    candidate_net = _write_candidate_net(tmp_path / "candidate.net.xml", west_lane_count=1)
    paths = [
        HamburgOfficialMovementPath(
            path.node_id,
            path.connection_id,
            path.ingress_lane_id,
            path.egress_lane_id,
            (("west_in_0", *path.lane_ids[1:]) if path.ingress_lane_id == "2" else path.lane_ids),
        )
        for path in _movement_paths()
    ]
    contract = build_hamburg_teacher_cell_contract(
        node_id="0228",
        map_lanes=_map_lanes(),
        map_connections=_map_connections(),
        topology_inventory=_topology_inventory(),
        movement_paths=paths,
        candidate_net_file=candidate_net,
    )

    ingress_rows = [row for row in contract.lane_indices if row.direction == "ingress"]
    assert [(row.official_lane_id, row.candidate_lane_id, row.teacher_lane_index) for row in ingress_rows] == [
        ("1", "west_in_0", 0),
        ("2", "west_in_0", 1),
    ]
    ingress_approach = next(
        approach for approach in contract.ir.approaches if approach.role == "hamburg_official_ingress"
    )
    assert ingress_approach.incoming_lane_count == 2


def test_hamburg_teacher_cell_does_not_merge_distinct_official_approaches_on_one_edge(
    tmp_path: Path,
) -> None:
    candidate_net = _write_candidate_net(tmp_path / "candidate.net.xml", west_lane_count=2)
    map_lanes = _map_lanes()
    map_lanes[1] = MapLane(
        "228",
        "2",
        "vehicle",
        "6",
        "",
        10.0,
        53.0,
        ((-1.0, 3.0), (-20.0, 3.0)),
    )

    contract = build_hamburg_teacher_cell_contract(
        node_id="0228",
        map_lanes=map_lanes,
        map_connections=_map_connections(),
        topology_inventory=_topology_inventory(),
        movement_paths=_movement_paths(),
        candidate_net_file=candidate_net,
    )

    ingress_rows = [row for row in contract.lane_indices if row.direction == "ingress"]
    assert {row.official_approach_id for row in ingress_rows} == {"4", "6"}
    assert len({row.teacher_approach_id for row in ingress_rows}) == 2
    assert len([approach for approach in contract.ir.approaches if approach.role.endswith("ingress")]) == 2
    assert (
        "candidate_boundary_edge_reused_by_multiple_teacher_edges:"
        "west_in:official_approaches=['4', '6']"
    ) in contract.review_gates


def test_hamburg_teacher_cell_uses_common_cross_section_across_split_candidate_edges(
    tmp_path: Path,
) -> None:
    candidate_net = _write_candidate_net(tmp_path / "candidate.net.xml", west_lane_count=2)
    paths = [
        path
        if path.ingress_lane_id != "2"
        else HamburgOfficialMovementPath(
            path.node_id,
            path.connection_id,
            path.ingress_lane_id,
            path.egress_lane_id,
            ("upstream_0", *path.lane_ids),
        )
        for path in _movement_paths()
    ]

    contract = build_hamburg_teacher_cell_contract(
        node_id="0228",
        map_lanes=_map_lanes(),
        map_connections=_map_connections(),
        topology_inventory=_topology_inventory(),
        movement_paths=paths,
        candidate_net_file=candidate_net,
    )

    ingress_rows = [row for row in contract.lane_indices if row.direction == "ingress"]
    assert len({row.teacher_approach_id for row in ingress_rows}) == 1
    ingress_pair = next(
        pair for pair in contract.approach_pairs if pair["direction"] == "ingress"
    )
    assert ingress_pair["candidate_edge_id"] == "west_in"
    assert {row.candidate_edge_id for row in ingress_rows} == {"west_in"}
    assert contract.review_gates == ()


def test_hamburg_teacher_cell_fails_closed_without_endpoint_supported_common_section(
    tmp_path: Path,
) -> None:
    candidate_net = _write_candidate_net(tmp_path / "candidate.net.xml", west_lane_count=2)
    paths = [
        path
        if path.ingress_lane_id != "2"
        else HamburgOfficialMovementPath(
            path.node_id,
            path.connection_id,
            path.ingress_lane_id,
            path.egress_lane_id,
            ("upstream_0", "straight_1", "east_out_1"),
        )
        for path in _movement_paths()
    ]

    contract = build_hamburg_teacher_cell_contract(
        node_id="0228",
        map_lanes=_map_lanes(),
        map_connections=_map_connections(),
        topology_inventory=_topology_inventory(),
        movement_paths=paths,
        candidate_net_file=candidate_net,
    )

    assert contract.topology_status == "blocked"
    assert not any(pair["direction"] == "ingress" for pair in contract.approach_pairs)
    assert any(
        gate.startswith(
            "official_approach_cross_section_missing_endpoint_frontier:ingress:4:"
        )
        for gate in contract.review_gates
    )


def test_hamburg_teacher_cell_fails_closed_when_one_official_lane_changes_lane_at_section(
    tmp_path: Path,
) -> None:
    candidate_net = _write_candidate_net(tmp_path / "candidate.net.xml", west_lane_count=2)
    paths = [
        HamburgOfficialMovementPath(
            path.node_id,
            path.connection_id,
            path.ingress_lane_id,
            path.egress_lane_id,
            ("west_in_1", *path.lane_ids[1:]),
        )
        if path.connection_id == "3"
        else path
        for path in _movement_paths()
    ]

    contract = build_hamburg_teacher_cell_contract(
        node_id="0228",
        map_lanes=_map_lanes(),
        map_connections=_map_connections(),
        topology_inventory=_topology_inventory(),
        movement_paths=paths,
        candidate_net_file=candidate_net,
    )

    assert contract.topology_status == "blocked"
    assert not any(pair["direction"] == "ingress" for pair in contract.approach_pairs)
    assert any(
        gate.startswith(
            "official_approach_cross_section_lane_ambiguous:ingress:4:edge=west_in:"
        )
        for gate in contract.review_gates
    )


def test_real_0228_uses_all_twelve_official_approach_cross_sections() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    source_net = (
        repo_root
        / "artifacts"
        / "hamburg_sandtorkai_twin_20260711"
        / "network"
        / "official_osm_recovery_v1"
        / "compact_corridor"
        / "hamburg_sandtorkai_official_osm_compact.net.xml"
    )
    asset_dir = (
        repo_root
        / "artifacts"
        / "hamburg_sandtorkai_twin_20260711"
        / "twin"
        / "official"
        / "signals"
        / "assets"
    )
    map_file = asset_dir / "0228_map_xml.xml"
    ocit_file = asset_dir / "0228_ocit_xml.xml"
    if not all(path.is_file() for path in (source_net, map_file, ocit_file)):
        pytest.skip("real Hamburg 0228 regression assets are not present")

    map_lanes, map_connections = parse_mapem(map_file)
    topology_inventory = build_vehicle_topology_inventory(
        [parse_ocit_c(ocit_file)],
        map_lanes,
        map_connections,
        (),
    )
    movement_paths = derive_hamburg_official_movement_paths(
        candidate_net_file=source_net,
        official_movements=topology_inventory.movements,
        lane_bindings=bind_map_lanes_to_network(source_net, map_lanes),
        connection_evidence=HAMBURG_SANDTORKAI_CONNECTION_EVIDENCE,
    )
    contract = build_hamburg_teacher_cell_contract(
        node_id="0228",
        map_lanes=map_lanes,
        map_connections=map_connections,
        topology_inventory=topology_inventory,
        movement_paths=movement_paths,
        candidate_net_file=source_net,
    )

    actual_edge_by_approach = {
        (str(pair["direction"]), str(pair["official_approach_id"])): str(
            pair["candidate_edge_id"]
        )
        for pair in contract.approach_pairs
    }
    assert actual_edge_by_approach == {
        ("ingress", "3"): "19199492",
        ("ingress", "4"): "74543943#1",
        ("ingress", "7"): "24732668#0",
        ("ingress", "8"): "22649707#0",
        ("ingress", "10"): "22649708#1",
        ("ingress", "11"): "234561088#0",
        ("egress", "1"): "22649708#0",
        ("egress", "2"): "31274978",
        ("egress", "5"): "74543943#0",
        ("egress", "6"): "158068424",
        ("egress", "9"): "74547371#0",
        ("egress", "13"): "234561088#1",
    }
    assert len(contract.approach_pairs) == 12
    assert not any(
        "official_approach_candidate_" in gate
        or "official_approach_cross_section_" in gate
        for gate in contract.review_gates
    )


def test_hamburg_official_approach_components_split_0228_like_topology_into_four_cells() -> None:
    map_lanes, topology_inventory = _approach_component_fixture(
        "0228",
        [
            ("1", "3", "1"),
            ("2", "4", "1"),
            ("3", "4", "2"),
            ("4", "7", "5"),
            ("5", "7", "6"),
            ("6", "8", "9"),
            ("7", "10", "9"),
            ("8", "11", "13"),
        ],
    )

    components = build_hamburg_official_approach_components(
        node_id="228",
        map_lanes=map_lanes,
        topology_inventory=topology_inventory,
    )

    assert [component.movement_ids for component in components] == [
        ("1", "2", "3"),
        ("4", "5"),
        ("6", "7"),
        ("8",),
    ]
    assert [component.ingress_approach_ids for component in components] == [
        ("3", "4"),
        ("7",),
        ("8", "10"),
        ("11",),
    ]
    assert [component.egress_approach_ids for component in components] == [
        ("1", "2"),
        ("5", "6"),
        ("9",),
        ("13",),
    ]


def test_hamburg_official_approach_components_keep_2394_like_topology_as_one_cell() -> None:
    map_lanes, topology_inventory = _approach_component_fixture(
        "2394",
        [
            ("1", "1", "3"),
            ("2", "2", "3"),
            ("3", "2", "4"),
        ],
    )

    components = build_hamburg_official_approach_components(
        node_id="02394",
        map_lanes=map_lanes,
        topology_inventory=topology_inventory,
    )

    assert len(components) == 1
    assert components[0].movement_ids == ("1", "2", "3")
    assert components[0].ingress_approach_ids == ("1", "2")
    assert components[0].egress_approach_ids == ("3", "4")


def _map_lanes() -> list[MapLane]:
    return [
        MapLane("228", "1", "vehicle", "4", "", 10.0, 53.0, ((-1.0, 0.0), (-20.0, 0.0))),
        MapLane("228", "2", "vehicle", "4", "", 10.0, 53.0, ((-1.0, 3.0), (-20.0, 3.0))),
        MapLane("228", "3", "vehicle", "", "2", 10.0, 53.0, ((1.0, 0.0), (20.0, 0.0))),
        MapLane("228", "4", "vehicle", "", "2", 10.0, 53.0, ((1.0, 3.0), (20.0, 3.0))),
    ]


def _map_connections() -> list[MapConnection]:
    return [
        MapConnection("228", "1", "1", "3", "1", "100000000000"),
        MapConnection("228", "2", "2", "4", "1", "100000000000"),
        MapConnection("228", "3", "1", "4", "2", "001000000000"),
    ]


def _topology_inventory() -> OcitVehicleTopologyInventory:
    movements = (
        OcitVehicleTopologyMovement(
            node_id="228",
            connection_id="1",
            ingress_lane_id="1",
            egress_lane_id="3",
            map_signal_group="1",
            primary_motor_groups=("K1",),
            secondary_motor_groups=(),
            topology_control_key="P_K1__S_NONE",
            observed_stream_ids=(),
            observed_signal_groups=(),
        ),
        OcitVehicleTopologyMovement(
            node_id="228",
            connection_id="2",
            ingress_lane_id="2",
            egress_lane_id="4",
            map_signal_group="1",
            primary_motor_groups=("K1",),
            secondary_motor_groups=(),
            topology_control_key="P_K1__S_NONE",
            observed_stream_ids=(),
            observed_signal_groups=(),
        ),
        OcitVehicleTopologyMovement(
            node_id="228",
            connection_id="3",
            ingress_lane_id="1",
            egress_lane_id="4",
            map_signal_group="2",
            primary_motor_groups=("K2",),
            secondary_motor_groups=(),
            topology_control_key="P_K2__S_NONE",
            observed_stream_ids=(),
            observed_signal_groups=(),
        ),
    )
    return OcitVehicleTopologyInventory(
        status="pass",
        source_movement_count=3,
        excluded_non_vehicle_movement_count=0,
        movement_count=3,
        observed_stream_count=0,
        observed_match_count=0,
        group_resolution_policy=VEHICLE_TOPOLOGY_GROUP_POLICY,
        movements=movements,
        topology_streams=(),
    )


def _movement_paths() -> list[HamburgOfficialMovementPath]:
    return [
        HamburgOfficialMovementPath("228", "1", "1", "3", ("west_in_0", "straight_0", "east_out_0")),
        HamburgOfficialMovementPath("228", "2", "2", "4", ("west_in_1", "straight_1", "east_out_1")),
        HamburgOfficialMovementPath("228", "3", "1", "4", ("west_in_0", "right_0", "east_out_1")),
    ]


def _approach_component_fixture(
    node_id: str,
    movement_specs: list[tuple[str, str, str]],
) -> tuple[list[MapLane], OcitVehicleTopologyInventory]:
    ingress_approach_ids = sorted({item[1] for item in movement_specs}, key=int)
    egress_approach_ids = sorted({item[2] for item in movement_specs}, key=int)
    map_lanes = [
        MapLane(
            node_id,
            f"i{approach_id}",
            "vehicle",
            approach_id,
            "",
            10.0,
            53.0,
            ((-1.0, float(index)), (-20.0, float(index))),
        )
        for index, approach_id in enumerate(ingress_approach_ids)
    ]
    map_lanes.extend(
        MapLane(
            node_id,
            f"e{approach_id}",
            "vehicle",
            "",
            approach_id,
            10.0,
            53.0,
            ((1.0, float(index)), (20.0, float(index))),
        )
        for index, approach_id in enumerate(egress_approach_ids)
    )
    movements = tuple(
        OcitVehicleTopologyMovement(
            node_id=node_id,
            connection_id=movement_id,
            ingress_lane_id=f"i{ingress_approach_id}",
            egress_lane_id=f"e{egress_approach_id}",
            map_signal_group=movement_id,
            primary_motor_groups=(f"K{movement_id}",),
            secondary_motor_groups=(),
            topology_control_key=f"P_K{movement_id}__S_NONE",
            observed_stream_ids=(),
            observed_signal_groups=(),
        )
        for movement_id, ingress_approach_id, egress_approach_id in movement_specs
    )
    return map_lanes, OcitVehicleTopologyInventory(
        status="pass",
        source_movement_count=len(movements),
        excluded_non_vehicle_movement_count=0,
        movement_count=len(movements),
        observed_stream_count=0,
        observed_match_count=0,
        group_resolution_policy=VEHICLE_TOPOLOGY_GROUP_POLICY,
        movements=movements,
        topology_streams=(),
    )


def _write_candidate_net(path: Path, *, west_lane_count: int) -> Path:
    west_lanes = "".join(
        f'<lane id="west_in_{index}" index="{index}" allow="passenger"/>'
        for index in range(west_lane_count)
    )
    path.write_text(
        f"""<net>
  <edge id="upstream" from="u" to="w"><lane id="upstream_0" index="0" allow="passenger"/></edge>
  <edge id="west_in" from="w" to="m">{west_lanes}</edge>
  <edge id="straight"><lane id="straight_0" index="0" allow="passenger"/><lane id="straight_1" index="1" allow="passenger"/></edge>
  <edge id="right" from="m" to="j"><lane id="right_0" index="0" allow="passenger"/></edge>
  <edge id="east_out" from="j" to="e"><lane id="east_out_0" index="0" allow="passenger"/><lane id="east_out_1" index="1" allow="passenger"/></edge>
  <junction id="w" type="priority"/>
  <junction id="u" type="priority"/>
  <junction id="m" type="traffic_light"/>
  <junction id="j" type="traffic_light"/>
  <junction id="e" type="priority"/>
</net>""".replace('<edge id="straight">', '<edge id="straight" from="m" to="j">'),
        encoding="utf-8",
    )
    return path


def _physical_connection_key(connection: ET.Element) -> tuple[str, str, str, str]:
    return (
        connection.attrib.get("from", ""),
        connection.attrib.get("to", ""),
        connection.attrib.get("fromLane", ""),
        connection.attrib.get("toLane", ""),
    )


def _expected_expression_by_physical_key(
    contract: HamburgTeacherCellContract,
) -> dict[tuple[str, str, str, str], int]:
    approaches = {approach.approach_id: approach for approach in contract.ir.approaches}
    result: dict[tuple[str, str, str, str], int] = {}
    for movement in contract.ir.movement_matrix.movements:
        source = approaches[movement.from_approach_id]
        target = approaches[movement.to_approach_id]
        key = (
            source.incoming_edge_ids[0],
            target.outgoing_edge_ids[0],
            str(movement.from_lane_indices[0]),
            str(movement.to_lane_indices[0]),
        )
        assert key not in result
        result[key] = contract.ir.control.link_index_map[movement.movement_id]
    return result


def _official_2421_fixture() -> tuple[
    list[MapLane],
    list[MapConnection],
    OcitVehicleTopologyInventory,
    list[HamburgOfficialMovementPath],
]:
    map_lanes = [
        MapLane("2421", "7", "vehicle", "2", "", 53.5, 10.0, ((-4.0, 1.6), (-50.0, 1.6))),
        MapLane("2421", "8", "vehicle", "2", "", 53.5, 10.0, ((-4.0, -1.6), (-50.0, -1.6))),
        MapLane("2421", "10", "vehicle", "3", "", 53.5, 10.0, ((-1.6, -4.0), (-1.6, -50.0))),
        MapLane("2421", "12", "vehicle", "3", "", 53.5, 10.0, ((1.6, -4.0), (1.6, -50.0))),
        MapLane("2421", "1", "vehicle", "4", "", 53.5, 10.0, ((4.0, -1.6), (50.0, -1.6))),
        MapLane("2421", "3", "vehicle", "4", "", 53.5, 10.0, ((4.0, 1.6), (50.0, 1.6))),
        MapLane("2421", "9", "vehicle", "", "2", 53.5, 10.0, ((4.0, -1.6), (50.0, -1.6))),
        MapLane("2421", "6", "vehicle", "", "2", 53.5, 10.0, ((4.0, 1.6), (50.0, 1.6))),
        MapLane("2421", "11", "vehicle", "", "3", 53.5, 10.0, ((0.0, 4.0), (0.0, 50.0))),
        MapLane("2421", "2", "vehicle", "", "4", 53.5, 10.0, ((-4.0, 0.0), (-50.0, 0.0))),
    ]
    connection_specs = [
        ("1", "1", "9", "1", "100000000000", ("K3",), (), "P_K3__S_NONE"),
        ("2", "3", "6", "1", "100000000000", ("K3",), (), "P_K3__S_NONE"),
        ("4", "7", "2", "2", "100000000000", ("K1",), (), "P_K1__S_NONE"),
        ("5", "8", "11", "7", "010000000000", ("K4Z",), ("K1",), "P_K4Z__S_K1"),
        ("6", "10", "9", "8", "001000000000", ("K5",), ("K2",), "P_K5__S_K2"),
        ("7", "10", "6", "8", "001000000000", ("K5",), ("K2",), "P_K5__S_K2"),
        ("8", "3", "11", "3", "001000000000", (), ("K3",), "P_NONE__S_K3"),
        ("9", "1", "11", "3", "001000000000", (), ("K3",), "P_NONE__S_K3"),
        ("10", "12", "2", "4", "010000000000", (), ("K2",), "P_NONE__S_K2"),
    ]
    map_connections = [
        MapConnection("2421", connection_id, ingress, egress, signal_group, maneuver)
        for (
            connection_id,
            ingress,
            egress,
            signal_group,
            maneuver,
            _primary,
            _secondary,
            _expression,
        ) in connection_specs
    ]
    movements = tuple(
        OcitVehicleTopologyMovement(
            node_id="2421",
            connection_id=connection_id,
            ingress_lane_id=ingress,
            egress_lane_id=egress,
            map_signal_group=signal_group,
            primary_motor_groups=primary,
            secondary_motor_groups=secondary,
            topology_control_key=expression,
            observed_stream_ids=(),
            observed_signal_groups=(),
        )
        for (
            connection_id,
            ingress,
            egress,
            signal_group,
            _maneuver,
            primary,
            secondary,
            expression,
        ) in connection_specs
    )
    topology_inventory = OcitVehicleTopologyInventory(
        status="pass",
        source_movement_count=len(movements),
        excluded_non_vehicle_movement_count=0,
        movement_count=len(movements),
        observed_stream_count=0,
        observed_match_count=0,
        group_resolution_policy=VEHICLE_TOPOLOGY_GROUP_POLICY,
        movements=movements,
        topology_streams=(),
    )
    candidate_lane_by_official_lane = {
        "7": "candidate_i2_0",
        "8": "candidate_i2_1",
        "10": "candidate_i3_0",
        "12": "candidate_i3_1",
        "1": "candidate_i4_0",
        "3": "candidate_i4_1",
        "9": "candidate_e2_0",
        "6": "candidate_e2_1",
        "11": "candidate_e3_0",
        "2": "candidate_e4_0",
    }
    movement_paths = [
        HamburgOfficialMovementPath(
            node_id="2421",
            connection_id=connection_id,
            ingress_lane_id=ingress,
            egress_lane_id=egress,
            lane_ids=(
                candidate_lane_by_official_lane[ingress],
                candidate_lane_by_official_lane[egress],
            ),
        )
        for connection_id, ingress, egress, *_rest in connection_specs
    ]
    return map_lanes, map_connections, topology_inventory, movement_paths


def _write_2421_candidate_net(path: Path) -> Path:
    path.write_text(
        """<net>
  <edge id="candidate_i2" from="i2" to="j"><lane id="candidate_i2_0" index="0" allow="passenger"/><lane id="candidate_i2_1" index="1" allow="passenger"/></edge>
  <edge id="candidate_i3" from="i3" to="j"><lane id="candidate_i3_0" index="0" allow="passenger"/><lane id="candidate_i3_1" index="1" allow="passenger"/></edge>
  <edge id="candidate_i4" from="i4" to="j"><lane id="candidate_i4_0" index="0" allow="passenger"/><lane id="candidate_i4_1" index="1" allow="passenger"/></edge>
  <edge id="candidate_e2" from="j" to="e2"><lane id="candidate_e2_0" index="0" allow="passenger"/><lane id="candidate_e2_1" index="1" allow="passenger"/></edge>
  <edge id="candidate_e3" from="j" to="e3"><lane id="candidate_e3_0" index="0" allow="passenger"/></edge>
  <edge id="candidate_e4" from="j" to="e4"><lane id="candidate_e4_0" index="0" allow="passenger"/></edge>
  <junction id="i2" type="priority"/><junction id="i3" type="priority"/><junction id="i4" type="priority"/>
  <junction id="j" type="traffic_light"/>
  <junction id="e2" type="priority"/><junction id="e3" type="priority"/><junction id="e4" type="priority"/>
</net>""",
        encoding="utf-8",
    )
    return path
