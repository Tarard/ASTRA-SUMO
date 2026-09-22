from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from torii_sumo.core.digital_twin import SignalStream
from torii_sumo.core.digital_twin_mapping import MapLaneBinding
from torii_sumo.core.official_tls_rebuild import (
    HAMBURG_SANDTORKAI_CONNECTION_REPAIRS,
    HAMBURG_SANDTORKAI_GROUP_INDEX_BY_NODE,
    HAMBURG_SANDTORKAI_TLS_PRESET_VERSION,
    ConnectionRepair,
    OfficialTlsGroup,
    OfficialTlsPlan,
    OfficialTlsPlanError,
    PhysicalControlledLink,
    apply_official_tls_plan_to_plain,
    audit_external_lane_geometry,
    audit_retired_tls_absence,
    derive_official_tls_plan,
    edge_lane_signature,
    hamburg_sandtorkai_official_tls_plan,
    source_tls_controller_ids,
)


def _write_xml(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _connection(
    parent: ET.Element,
    from_edge: str,
    from_lane: int,
    to_edge: str,
    to_lane: int,
    *,
    tls_id: str = "",
    link_index: int | None = None,
) -> ET.Element:
    attributes = {
        "from": from_edge,
        "fromLane": str(from_lane),
        "to": to_edge,
        "toLane": str(to_lane),
    }
    if tls_id:
        attributes["tl"] = tls_id
    if link_index is not None:
        attributes["linkIndex"] = str(link_index)
    return ET.SubElement(parent, "connection", attributes)


def _tllogic(parent: ET.Element, tls_id: str) -> None:
    logic = ET.SubElement(
        parent,
        "tlLogic",
        {"id": tls_id, "type": "static", "programID": "0", "offset": "0"},
    )
    ET.SubElement(logic, "phase", {"duration": "30", "state": "G"})


def _tls_connection(
    parent: ET.Element,
    from_edge: str,
    from_lane: int,
    to_edge: str,
    to_lane: int,
    *,
    tls_id: str,
    link_index: int,
) -> ET.Element:
    return _connection(
        parent,
        from_edge,
        from_lane,
        to_edge,
        to_lane,
        tls_id=tls_id,
        link_index=link_index,
    )


def _simple_net(path: Path, *, connection_tls_id: str) -> None:
    root = ET.Element("net")
    for edge_id, from_node, to_node, shape in (
        ("a", "n0", "n1", "0.00,0.00 10.00,0.00"),
        ("b", "n1", "n2", "10.00,0.00 20.00,0.00"),
    ):
        edge = ET.SubElement(
            root,
            "edge",
            {"id": edge_id, "from": from_node, "to": to_node, "priority": "1"},
        )
        ET.SubElement(
            edge,
            "lane",
            {
                "id": f"{edge_id}_0",
                "index": "0",
                "speed": "13.89",
                "length": "10.00",
                "shape": shape,
            },
        )
    _connection(root, "a", 0, "b", 0, tls_id=connection_tls_id, link_index=0)
    _tllogic(root, connection_tls_id)
    _write_xml(path, root)


def _derivation_net(
    path: Path,
    *,
    edge_ids: tuple[str, ...],
    connections: tuple[tuple[str, str, str], ...],
) -> None:
    root = ET.Element("net")
    for edge_index, edge_id in enumerate(edge_ids):
        edge = ET.SubElement(
            root,
            "edge",
            {
                "id": edge_id,
                "from": f"n{edge_index}",
                "to": f"n{edge_index + 1}",
                "priority": "1",
            },
        )
        ET.SubElement(
            edge,
            "lane",
            {
                "id": f"{edge_id}_0",
                "index": "0",
                "speed": "13.89",
                "length": "10.00",
            },
        )
    tls_link_index = 0
    for from_edge, to_edge, tls_id in connections:
        _connection(
            root,
            from_edge,
            0,
            to_edge,
            0,
            tls_id=tls_id,
            link_index=tls_link_index if tls_id else None,
        )
        if tls_id:
            tls_link_index += 1
    _write_xml(path, root)


def _signal_stream(
    stream_id: int,
    signal_group: str,
    *,
    ingress_lane_id: str = "1",
    egress_lane_id: str = "2",
) -> SignalStream:
    return SignalStream(
        stream_id=stream_id,
        thing_id=None,
        node_id="N1",
        connection_id=str(stream_id),
        ingress_lane_id=ingress_lane_id,
        egress_lane_id=egress_lane_id,
        lane_type="KFZ",
        signal_group=signal_group,
        layer_name="primary_signal",
        name=f"stream-{stream_id}",
    )


def _lane_binding(map_lane_id: str, sumo_edge: str, map_role: str) -> MapLaneBinding:
    return MapLaneBinding(
        node_id="N1",
        map_lane_id=map_lane_id,
        map_lane_type="vehicle",
        map_role=map_role,
        sumo_edge=sumo_edge,
        sumo_lane=f"{sumo_edge}_0",
        lane_position=0.0,
        distance_m=0.0,
        heading_error_deg=0.0,
        mapping_confidence="high",
        mapping_status="active",
    )


def test_multiple_physical_connections_share_one_official_link_index(tmp_path: Path) -> None:
    source_connections = tmp_path / "source.con.xml"
    source_tllogic = tmp_path / "source.tll.xml"
    connection_root = ET.Element("connections")
    _connection(connection_root, "a", 0, "b", 0)
    _connection(connection_root, "b", 0, "c", 0)
    _write_xml(source_connections, connection_root)
    tllogic_root = ET.Element("tlLogics")
    _tllogic(tllogic_root, "old_a")
    _tllogic(tllogic_root, "old_b")
    _tls_connection(tllogic_root, "a", 0, "b", 0, tls_id="old_a", link_index=0)
    _tls_connection(tllogic_root, "b", 0, "c", 0, tls_id="old_b", link_index=0)
    _write_xml(source_tllogic, tllogic_root)
    group = OfficialTlsGroup(
        official_node_id="0228",
        signal_group="K3",
        tls_id="HH_0228",
        link_index=2,
        physical_links=(
            PhysicalControlledLink("a", 0, "b", 0),
            PhysicalControlledLink("b", 0, "c", 0),
        ),
    )
    plan = OfficialTlsPlan(
        plan_id="test",
        version="1",
        groups=(group,),
        retired_tls_ids=("old_a", "old_b"),
    )

    report = apply_official_tls_plan_to_plain(
        source_connections_file=source_connections,
        source_tllogic_file=source_tllogic,
        output_connections_file=tmp_path / "official.con.xml",
        output_tllogic_file=tmp_path / "official.tll.xml",
        plan=plan,
    )

    output_connections = ET.parse(tmp_path / "official.con.xml").getroot().findall("connection")
    assert all("tl" not in row.attrib and "linkIndex" not in row.attrib for row in output_connections)
    output_tllogic_root = ET.parse(tmp_path / "official.tll.xml").getroot()
    output_tls_bindings = output_tllogic_root.findall("connection")
    assert {(row.attrib["tl"], row.attrib["linkIndex"]) for row in output_tls_bindings} == {
        ("HH_0228", "2")
    }
    assert {
        (row.attrib["from"], row.attrib["to"])
        for row in output_tls_bindings
    } == {("a", "b"), ("b", "c")}
    output_tllogics = output_tllogic_root.findall("tlLogic")
    assert [logic.attrib["id"] for logic in output_tllogics] == ["HH_0228"]
    assert output_tllogics[0].find("phase").attrib["state"] == "rrr"
    assert report["physical_link_counts_by_shared_index"] == {"HH_0228[2]": 2}


def test_real_netconvert_accepts_tls_bindings_only_in_tllogic_file(
    tmp_path: Path,
) -> None:
    netconvert = shutil.which("netconvert")
    if netconvert is None:
        installed = Path(r"C:\Program Files (x86)\Eclipse\Sumo\bin\netconvert.exe")
        if not installed.is_file():
            pytest.skip("netconvert is not installed")
        netconvert = str(installed)

    source_nodes = tmp_path / "source.nod.xml"
    nodes = ET.Element("nodes")
    ET.SubElement(nodes, "node", {"id": "n0", "x": "0", "y": "10"})
    ET.SubElement(nodes, "node", {"id": "n1", "x": "0", "y": "-10"})
    ET.SubElement(
        nodes,
        "node",
        {"id": "j", "x": "10", "y": "0", "type": "traffic_light", "tl": "old"},
    )
    ET.SubElement(nodes, "node", {"id": "n2", "x": "20", "y": "0"})
    _write_xml(source_nodes, nodes)

    source_edges = tmp_path / "source.edg.xml"
    edges = ET.Element("edges")
    for edge_id, from_node, to_node in (
        ("a", "n0", "j"),
        ("x", "n1", "j"),
        ("b", "j", "n2"),
    ):
        ET.SubElement(
            edges,
            "edge",
            {
                "id": edge_id,
                "from": from_node,
                "to": to_node,
                "numLanes": "1",
                "speed": "13.89",
            },
        )
    _write_xml(source_edges, edges)

    source_connections = tmp_path / "source.con.xml"
    connections = ET.Element("connections")
    _connection(connections, "a", 0, "b", 0)
    _connection(connections, "x", 0, "b", 0)
    _write_xml(source_connections, connections)

    source_tllogic = tmp_path / "source.tll.xml"
    tllogics = ET.Element("tlLogics")
    _tllogic(tllogics, "old")
    _tls_connection(tllogics, "a", 0, "b", 0, tls_id="old", link_index=0)
    _tls_connection(tllogics, "x", 0, "b", 0, tls_id="old", link_index=1)
    _write_xml(source_tllogic, tllogics)

    plan = OfficialTlsPlan(
        plan_id="netconvert-schema-probe",
        version="1",
        groups=(
            OfficialTlsGroup(
                "N1",
                "K1",
                "HH_N1",
                0,
                (
                    PhysicalControlledLink("a", 0, "b", 0),
                    PhysicalControlledLink("x", 0, "b", 0),
                ),
            ),
        ),
        retired_tls_ids=("old",),
    )
    output_connections = tmp_path / "official.con.xml"
    output_tllogic = tmp_path / "official.tll.xml"
    output_nodes = tmp_path / "official.nod.xml"
    apply_official_tls_plan_to_plain(
        source_connections_file=source_connections,
        source_tllogic_file=source_tllogic,
        source_nodes_file=source_nodes,
        output_connections_file=output_connections,
        output_tllogic_file=output_tllogic,
        output_nodes_file=output_nodes,
        plan=plan,
    )

    assert not any(
        "tl" in row.attrib or "linkIndex" in row.attrib
        for row in ET.parse(output_connections).getroot().findall("connection")
    )
    result = subprocess.run(
        [
            netconvert,
            "--node-files",
            str(output_nodes),
            "--edge-files",
            str(source_edges),
            "--connection-files",
            str(output_connections),
            "--tllogic-files",
            str(output_tllogic),
            "--output-file",
            str(tmp_path / "rebuilt.net.xml"),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    rebuilt_root = ET.parse(tmp_path / "rebuilt.net.xml").getroot()
    rebuilt_bindings = [
        row
        for row in rebuilt_root.findall("connection")
        if row.attrib.get("tl") == "HH_N1"
    ]
    assert len(rebuilt_bindings) == 2
    assert {row.attrib["linkIndex"] for row in rebuilt_bindings} == {"0"}


def test_physical_connection_group_conflict_fails_closed(tmp_path: Path) -> None:
    source_connections = tmp_path / "source.con.xml"
    connection_root = ET.Element("connections")
    _connection(connection_root, "a", 0, "b", 0)
    _write_xml(source_connections, connection_root)
    physical_link = PhysicalControlledLink("a", 0, "b", 0)
    plan = OfficialTlsPlan(
        plan_id="conflict",
        version="1",
        groups=(
            OfficialTlsGroup("0228", "K2", "HH_0228", 0, (physical_link,)),
            OfficialTlsGroup("0228", "K3", "HH_0228", 1, (physical_link,)),
        ),
    )

    with pytest.raises(OfficialTlsPlanError, match="physical connection assigned more than once"):
        apply_official_tls_plan_to_plain(
            source_connections_file=source_connections,
            source_tllogic_file=None,
            output_connections_file=tmp_path / "official.con.xml",
            output_tllogic_file=tmp_path / "official.tll.xml",
            plan=plan,
        )


def test_connection_repair_is_idempotent(tmp_path: Path) -> None:
    source_connections = tmp_path / "source.con.xml"
    _write_xml(source_connections, ET.Element("connections"))
    repair = ConnectionRepair("0228", "a", 0, "b", 0, reason="official MAP")
    group = OfficialTlsGroup(
        official_node_id="0228",
        signal_group="K4",
        tls_id="HH_0228",
        link_index=0,
        physical_links=(PhysicalControlledLink("a", 0, "b", 0),),
    )
    plan = OfficialTlsPlan(plan_id="idempotent", version="1", groups=(group,), repairs=(repair,))
    first_connections = tmp_path / "first.con.xml"
    first_tllogic = tmp_path / "first.tll.xml"
    first = apply_official_tls_plan_to_plain(
        source_connections_file=source_connections,
        source_tllogic_file=None,
        output_connections_file=first_connections,
        output_tllogic_file=first_tllogic,
        plan=plan,
    )
    second_connections = tmp_path / "second.con.xml"
    second_tllogic = tmp_path / "second.tll.xml"
    second = apply_official_tls_plan_to_plain(
        source_connections_file=first_connections,
        source_tllogic_file=first_tllogic,
        output_connections_file=second_connections,
        output_tllogic_file=second_tllogic,
        plan=plan,
    )

    assert first["repair_added_count"] == 1
    assert second["repair_added_count"] == 0
    assert second["repair_existing_count"] == 1
    assert first_connections.read_bytes() == second_connections.read_bytes()
    assert first_tllogic.read_bytes() == second_tllogic.read_bytes()
    assert len(ET.parse(second_connections).getroot().findall("connection")) == 1


def test_plain_from_only_connection_directive_is_preserved_and_not_indexed(
    tmp_path: Path,
) -> None:
    source_connections = tmp_path / "source.con.xml"
    root = ET.Element("connections")
    ET.SubElement(root, "connection", {"from": "deleted-outgoing-edge"})
    _connection(root, "a", 0, "b", 0)
    _write_xml(source_connections, root)
    plan = OfficialTlsPlan(
        plan_id="plain-delete-directive",
        version="1",
        groups=(
            OfficialTlsGroup(
                "N1",
                "K1",
                "HH_N1",
                0,
                (PhysicalControlledLink("a", 0, "b", 0),),
            ),
        ),
    )
    output_connections = tmp_path / "official.con.xml"

    report = apply_official_tls_plan_to_plain(
        source_connections_file=source_connections,
        source_tllogic_file=None,
        output_connections_file=output_connections,
        output_tllogic_file=tmp_path / "official.tll.xml",
        plan=plan,
    )

    rows = ET.parse(output_connections).getroot().findall("connection")
    assert rows[0].attrib == {"from": "deleted-outgoing-edge"}
    assert rows[1].attrib == {
        "from": "a",
        "fromLane": "0",
        "to": "b",
        "toLane": "0",
        "uncontrolled": "false",
    }
    tls_rows = ET.parse(tmp_path / "official.tll.xml").getroot().findall("connection")
    assert len(tls_rows) == 1
    assert tls_rows[0].attrib["tl"] == "HH_N1"
    assert report["preserved_plain_connection_directive_count"] == 1


def test_other_incomplete_plain_connection_still_fails_closed(tmp_path: Path) -> None:
    source_connections = tmp_path / "invalid.con.xml"
    root = ET.Element("connections")
    ET.SubElement(root, "connection", {"from": "a", "to": "b"})
    _write_xml(source_connections, root)
    plan = OfficialTlsPlan(
        plan_id="invalid-plain-connection",
        version="1",
        groups=(
            OfficialTlsGroup(
                "N1",
                "K1",
                "HH_N1",
                0,
                (PhysicalControlledLink("c", 0, "d", 0),),
            ),
        ),
    )

    with pytest.raises(OfficialTlsPlanError, match="invalid plain connection attributes"):
        apply_official_tls_plan_to_plain(
            source_connections_file=source_connections,
            source_tllogic_file=None,
            output_connections_file=tmp_path / "official.con.xml",
            output_tllogic_file=tmp_path / "official.tll.xml",
            plan=plan,
        )


def test_retired_controller_requires_complete_takeover(tmp_path: Path) -> None:
    source_connections = tmp_path / "source.con.xml"
    root = ET.Element("connections")
    _connection(root, "a", 0, "b", 0, tls_id="old", link_index=0)
    _connection(root, "x", 0, "y", 0, tls_id="old", link_index=1)
    _write_xml(source_connections, root)
    plan = OfficialTlsPlan(
        plan_id="incomplete",
        version="1",
        groups=(
            OfficialTlsGroup(
                "0228",
                "K3",
                "HH_0228",
                0,
                (PhysicalControlledLink("a", 0, "b", 0),),
            ),
        ),
        retired_tls_ids=("old",),
    )

    with pytest.raises(OfficialTlsPlanError, match="not fully taken over"):
        apply_official_tls_plan_to_plain(
            source_connections_file=source_connections,
            source_tllogic_file=None,
            output_connections_file=tmp_path / "official.con.xml",
            output_tllogic_file=tmp_path / "official.tll.xml",
            plan=plan,
        )


def test_application_keeps_sources_unchanged_and_edge_lane_signature_stable(tmp_path: Path) -> None:
    source_connections = tmp_path / "source.con.xml"
    source_tllogic = tmp_path / "source.tll.xml"
    connections = ET.Element("connections")
    _connection(connections, "a", 0, "b", 0, tls_id="old", link_index=0)
    _write_xml(source_connections, connections)
    tllogics = ET.Element("tlLogics")
    _tllogic(tllogics, "old")
    _write_xml(source_tllogic, tllogics)
    source_connection_bytes = source_connections.read_bytes()
    source_tllogic_bytes = source_tllogic.read_bytes()
    source_net = tmp_path / "source.net.xml"
    rebuilt_net = tmp_path / "rebuilt.net.xml"
    _simple_net(source_net, connection_tls_id="old")
    _simple_net(rebuilt_net, connection_tls_id="HH_0228")
    plan = OfficialTlsPlan(
        plan_id="source-stability",
        version="1",
        groups=(
            OfficialTlsGroup(
                "0228",
                "K3",
                "HH_0228",
                0,
                (PhysicalControlledLink("a", 0, "b", 0),),
            ),
        ),
        retired_tls_ids=("old",),
    )

    apply_official_tls_plan_to_plain(
        source_connections_file=source_connections,
        source_tllogic_file=source_tllogic,
        output_connections_file=tmp_path / "official.con.xml",
        output_tllogic_file=tmp_path / "official.tll.xml",
        plan=plan,
    )

    assert source_connections.read_bytes() == source_connection_bytes
    assert source_tllogic.read_bytes() == source_tllogic_bytes
    assert edge_lane_signature(source_net) == edge_lane_signature(rebuilt_net)


def test_source_tls_controller_ids_include_logic_and_connection_references(
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "controllers.net.xml"
    _simple_net(net_file, connection_tls_id="old_a")
    tree = ET.parse(net_file)
    _tllogic(tree.getroot(), "old_b")
    tree.write(net_file, encoding="utf-8", xml_declaration=True)

    assert source_tls_controller_ids(net_file) == ("old_a", "old_b")


def test_external_lane_geometry_allows_bounded_junction_clipping_but_not_large_shift(
    tmp_path: Path,
) -> None:
    source_net = tmp_path / "source.net.xml"
    clipped_net = tmp_path / "clipped.net.xml"
    shifted_net = tmp_path / "shifted.net.xml"
    _simple_net(source_net, connection_tls_id="old")
    _simple_net(clipped_net, connection_tls_id="new")
    clipped_tree = ET.parse(clipped_net)
    clipped_lane = clipped_tree.getroot().find("./edge[@id='a']/lane")
    assert clipped_lane is not None
    clipped_lane.set("shape", "1.00,0.00 10.00,0.00")
    clipped_lane.set("length", "9.00")
    clipped_lane.set("customShape", "1")
    clipped_tree.write(clipped_net, encoding="utf-8", xml_declaration=True)

    assert edge_lane_signature(source_net) == edge_lane_signature(clipped_net)
    bounded = audit_external_lane_geometry(
        source_net,
        clipped_net,
        max_shape_deviation_m=1.0,
    )
    assert bounded["status"] == "pass"
    assert bounded["maximum_observed_shape_deviation_m"] == pytest.approx(1.0)

    shutil.copy2(clipped_net, shifted_net)
    shifted_tree = ET.parse(shifted_net)
    shifted_lane = shifted_tree.getroot().find("./edge[@id='a']/lane")
    assert shifted_lane is not None
    shifted_lane.set("shape", "1.00,20.00 10.00,20.00")
    shifted_tree.write(shifted_net, encoding="utf-8", xml_declaration=True)
    rejected = audit_external_lane_geometry(
        source_net,
        shifted_net,
        max_shape_deviation_m=10.0,
    )
    assert rejected["status"] == "fail"
    assert rejected["violations"][0]["lane_id"] == "a_0"


def test_external_lane_geometry_can_require_exact_lane_lengths(tmp_path: Path) -> None:
    source_net = tmp_path / "source.net.xml"
    changed_net = tmp_path / "changed.net.xml"
    _simple_net(source_net, connection_tls_id="old")
    _simple_net(changed_net, connection_tls_id="new")
    changed_tree = ET.parse(changed_net)
    changed_lane = changed_tree.getroot().find("./edge[@id='a']/lane")
    assert changed_lane is not None
    changed_lane.set("length", "10.01")
    changed_tree.write(changed_net, encoding="utf-8", xml_declaration=True)

    report = audit_external_lane_geometry(
        source_net,
        changed_net,
        max_shape_deviation_m=0.0,
        max_length_deviation_m=0.0,
    )

    assert report["status"] == "fail"
    assert report["maximum_observed_shape_deviation_m"] == 0.0
    assert report["maximum_observed_length_deviation_m"] == pytest.approx(0.01)
    assert report["length_violations"] == [
        {"lane_id": "a_0", "absolute_length_deviation_m": pytest.approx(0.01)}
    ]


def test_hamburg_corridor_preset_contains_exactly_nine_versioned_repairs() -> None:
    group = OfficialTlsGroup(
        "0228",
        "K3",
        "HH_0228",
        0,
        (PhysicalControlledLink("a", 0, "b", 0),),
    )
    plan = hamburg_sandtorkai_official_tls_plan(groups=(group,))

    assert plan.version == HAMBURG_SANDTORKAI_TLS_PRESET_VERSION
    assert plan.repairs == HAMBURG_SANDTORKAI_CONNECTION_REPAIRS
    assert len(plan.repairs) == 9
    assert {repair.official_node_id for repair in plan.repairs} == {"0228", "2421", "2394"}
    assert HAMBURG_SANDTORKAI_GROUP_INDEX_BY_NODE == {
        "0228": {"K1": 0, "K2": 1, "K3": 2, "K4": 3, "K6": 4, "K7": 5, "K8": 6},
        "2421": {"K1": 0, "K2": 1, "K3": 2},
        "2394": {"K1": 0, "K2": 1, "K4": 2, "K5": 3, "K7": 4},
    }


def test_derivation_uses_stopline_tls_and_keeps_later_repair_topology_only(
    tmp_path: Path,
) -> None:
    source_net = tmp_path / "source.net.xml"
    _derivation_net(
        source_net,
        edge_ids=("in", "mid", "out"),
        connections=(("in", "mid", "old"),),
    )
    repair = ConnectionRepair("N1", "mid", 0, "out", 0, reason="official MAP")

    plan, audit = derive_official_tls_plan(
        signal_streams=(_signal_stream(1, "K3"),),
        lane_bindings=(
            _lane_binding("1", "in", "ingress"),
            _lane_binding("2", "out", "egress"),
        ),
        source_net_file=source_net,
        repairs=(repair,),
        group_index_by_node={"N1": {"K3": 0}},
        plan_id="derived",
        version="1",
        retired_tls_ids=("old",),
    )

    assert plan.groups == (
        OfficialTlsGroup(
            "N1",
            "K3",
            "HH_N1",
            0,
            (PhysicalControlledLink("in", 0, "mid", 0),),
        ),
    )
    assert audit["status"] == "pass"
    assert audit["hit_repair_count"] == 1
    assert audit["movements"][0]["path_lane_ids"] == ["in_0", "mid_0", "out_0"]
    assert [
        row["evidence"] for row in audit["movements"][0]["selected_physical_links"]
    ] == [["source_tls"]]
    assert audit["movements"][0]["demoted_duplicate_control_links"] == []


def test_derivation_demotes_later_source_tls_on_same_movement(tmp_path: Path) -> None:
    source_net = tmp_path / "sequential-tls.net.xml"
    _derivation_net(
        source_net,
        edge_ids=("in", "mid", "out"),
        connections=(("in", "mid", "old_stopline"), ("mid", "out", "old_downstream")),
    )

    plan, audit = derive_official_tls_plan(
        signal_streams=(_signal_stream(1, "K3"),),
        lane_bindings=(
            _lane_binding("1", "in", "ingress"),
            _lane_binding("2", "out", "egress"),
        ),
        source_net_file=source_net,
        repairs=(),
        group_index_by_node={"N1": {"K3": 0}},
        plan_id="sequential-tls",
        version="1",
    )

    assert plan.groups[0].physical_links == (
        PhysicalControlledLink("in", 0, "mid", 0),
    )
    assert plan.demoted_links == (
        PhysicalControlledLink("mid", 0, "out", 0),
    )
    assert audit["movements"][0]["demoted_duplicate_control_links"] == [
        {"from_edge": "mid", "from_lane": 0, "to_edge": "out", "to_lane": 0}
    ]


def test_derivation_accepts_explicit_stopline_repair_without_source_tls(
    tmp_path: Path,
) -> None:
    source_net = tmp_path / "stopline-repair.net.xml"
    _derivation_net(source_net, edge_ids=("in", "out"), connections=())

    plan, audit = derive_official_tls_plan(
        signal_streams=(_signal_stream(1, "K3"),),
        lane_bindings=(
            _lane_binding("1", "in", "ingress"),
            _lane_binding("2", "out", "egress"),
        ),
        source_net_file=source_net,
        repairs=(
            ConnectionRepair(
                "N1", "in", 0, "out", 0, evidence="official_map_stopline"
            ),
        ),
        group_index_by_node={"N1": {"K3": 0}},
        plan_id="stopline-repair",
        version="1",
    )

    assert plan.groups[0].physical_links == (
        PhysicalControlledLink("in", 0, "out", 0),
    )
    assert audit["movements"][0]["selection_policy"] == (
        "explicit_official_stopline_control_arc"
    )
    assert audit["movements"][0]["selected_physical_links"][0]["evidence"] == [
        "declared_repair",
        "official_map_stopline",
    ]


def test_derivation_rejects_multiple_stopline_arcs_for_one_movement(
    tmp_path: Path,
) -> None:
    source_net = tmp_path / "multiple-stoplines.net.xml"
    _derivation_net(source_net, edge_ids=("in", "mid", "out"), connections=())

    with pytest.raises(OfficialTlsPlanError, match="multiple official stop-line"):
        derive_official_tls_plan(
            signal_streams=(_signal_stream(1, "K3"),),
            lane_bindings=(
                _lane_binding("1", "in", "ingress"),
                _lane_binding("2", "out", "egress"),
            ),
            source_net_file=source_net,
            repairs=(
                ConnectionRepair(
                    "N1", "in", 0, "mid", 0, evidence="official_map_stopline"
                ),
                ConnectionRepair(
                    "N1", "mid", 0, "out", 0, evidence="official_map_stopline"
                ),
            ),
            group_index_by_node={"N1": {"K3": 0}},
            plan_id="multiple-stoplines",
            version="1",
        )


def test_derivation_rejects_stopline_shared_by_different_groups(tmp_path: Path) -> None:
    source_net = tmp_path / "shared-stopline.net.xml"
    _derivation_net(source_net, edge_ids=("in", "out"), connections=())

    with pytest.raises(OfficialTlsPlanError, match="shared by different control groups"):
        derive_official_tls_plan(
            signal_streams=(_signal_stream(1, "K1"), _signal_stream(2, "K2")),
            lane_bindings=(
                _lane_binding("1", "in", "ingress"),
                _lane_binding("2", "out", "egress"),
            ),
            source_net_file=source_net,
            repairs=(
                ConnectionRepair(
                    "N1", "in", 0, "out", 0, evidence="official_map_stopline"
                ),
            ),
            group_index_by_node={"N1": {"K1": 0, "K2": 1}},
            plan_id="shared-stopline",
            version="1",
        )


def test_derivation_rejects_multiple_bounded_lane_paths(tmp_path: Path) -> None:
    source_net = tmp_path / "ambiguous.net.xml"
    _derivation_net(
        source_net,
        edge_ids=("in", "m1", "m2", "out"),
        connections=(
            ("in", "m1", ""),
            ("m1", "out", ""),
            ("in", "m2", ""),
            ("m2", "out", ""),
        ),
    )

    with pytest.raises(OfficialTlsPlanError, match="ambiguous bounded lane paths"):
        derive_official_tls_plan(
            signal_streams=(_signal_stream(1, "K1"),),
            lane_bindings=(
                _lane_binding("1", "in", "ingress"),
                _lane_binding("2", "out", "egress"),
            ),
            source_net_file=source_net,
            repairs=(),
            group_index_by_node={"N1": {"K1": 0}},
            plan_id="ambiguous",
            version="1",
        )


def test_derivation_rejects_different_groups_on_same_physical_connection(
    tmp_path: Path,
) -> None:
    source_net = tmp_path / "conflict.net.xml"
    _derivation_net(
        source_net,
        edge_ids=("in", "out"),
        connections=(("in", "out", "old"),),
    )

    with pytest.raises(OfficialTlsPlanError, match="private movement core"):
        derive_official_tls_plan(
            signal_streams=(_signal_stream(1, "K1"), _signal_stream(2, "K2")),
            lane_bindings=(
                _lane_binding("1", "in", "ingress"),
                _lane_binding("2", "out", "egress"),
            ),
            source_net_file=source_net,
            repairs=(),
            group_index_by_node={"N1": {"K1": 0, "K2": 1}},
            plan_id="conflict",
            version="1",
            retired_tls_ids=("old",),
        )


def test_derivation_first_arc_fallback_is_explicit_and_requires_visual_review(
    tmp_path: Path,
) -> None:
    source_net = tmp_path / "uncontrolled.net.xml"
    _derivation_net(
        source_net,
        edge_ids=("in", "mid", "out"),
        connections=(("in", "mid", ""), ("mid", "out", "")),
    )
    arguments = {
        "signal_streams": (_signal_stream(1, "K1"),),
        "lane_bindings": (
            _lane_binding("1", "in", "ingress"),
            _lane_binding("2", "out", "egress"),
        ),
        "source_net_file": source_net,
        "repairs": (),
        "group_index_by_node": {"N1": {"K1": 0}},
        "plan_id": "fallback",
        "version": "1",
    }

    with pytest.raises(OfficialTlsPlanError, match="no source TLS or official stop-line authority"):
        derive_official_tls_plan(**arguments)

    plan, audit = derive_official_tls_plan(
        **arguments,
        uncontrolled_path_policy="first_arc_visual_review",
    )

    assert plan.groups[0].physical_links == (PhysicalControlledLink("in", 0, "mid", 0),)
    assert audit["status"] == "visual_review_required"
    assert audit["visual_review_required_count"] == 1
    assert audit["movements"][0]["selection_policy"] == "first_private_core_arc_visual_review"
    assert audit["movements"][0]["visual_review_required"] is True


def test_derivation_auto_discovers_retired_source_tls_ids(tmp_path: Path) -> None:
    source_net = tmp_path / "auto-retired.net.xml"
    _derivation_net(
        source_net,
        edge_ids=("in", "out"),
        connections=(("in", "out", "osm_tls"),),
    )
    arguments = {
        "signal_streams": (_signal_stream(1, "K1"),),
        "lane_bindings": (
            _lane_binding("1", "in", "ingress"),
            _lane_binding("2", "out", "egress"),
        ),
        "source_net_file": source_net,
        "repairs": (),
        "group_index_by_node": {"N1": {"K1": 0}},
        "plan_id": "auto-retired",
        "version": "1",
    }

    plan, audit = derive_official_tls_plan(**arguments)

    assert plan.retired_tls_ids == ("osm_tls",)
    assert audit["retired_tls_resolution"] == "auto_from_selected_source_arcs"
    assert audit["discovered_source_tls_ids"] == ["osm_tls"]
    assert audit["retired_tls_ids"] == ["osm_tls"]

    with pytest.raises(OfficialTlsPlanError, match="non-retired source TLS"):
        derive_official_tls_plan(**arguments, retired_tls_ids=())


def test_auto_discovered_source_tls_requires_complete_takeover(tmp_path: Path) -> None:
    source_net = tmp_path / "incomplete-auto-retired.net.xml"
    _derivation_net(
        source_net,
        edge_ids=("in", "out", "x", "y"),
        connections=(("in", "out", "osm_tls"), ("x", "y", "osm_tls")),
    )

    with pytest.raises(OfficialTlsPlanError, match="not fully taken over"):
        derive_official_tls_plan(
            signal_streams=(_signal_stream(1, "K1"),),
            lane_bindings=(
                _lane_binding("1", "in", "ingress"),
                _lane_binding("2", "out", "egress"),
            ),
            source_net_file=source_net,
            repairs=(),
            group_index_by_node={"N1": {"K1": 0}},
            plan_id="incomplete-auto-retired",
            version="1",
        )

    plan, audit = derive_official_tls_plan(
        signal_streams=(_signal_stream(1, "K1"),),
        lane_bindings=(
            _lane_binding("1", "in", "ingress"),
            _lane_binding("2", "out", "egress"),
        ),
        source_net_file=source_net,
        repairs=(),
        group_index_by_node={"N1": {"K1": 0}},
        plan_id="explicit-inventory-demotion",
        version="1",
        unclaimed_retired_link_policy="demote_after_complete_official_inventory",
    )

    assert plan.demoted_links == (PhysicalControlledLink("x", 0, "y", 0),)
    assert audit["unclaimed_retired_link_policy"] == (
        "demote_after_complete_official_inventory"
    )
    assert audit["unclaimed_retired_links"] == [
        {
            "from_edge": "x",
            "from_lane": 0,
            "to_edge": "y",
            "to_lane": 0,
            "source_tls_id": "osm_tls",
            "source_link_index": 1,
            "classification": "demoted",
            "reason": "outside_complete_official_primary_inventory",
        }
    ]


def test_derivation_demotes_cross_group_shared_prefix_and_suffix(tmp_path: Path) -> None:
    prefix_net = tmp_path / "shared-prefix.net.xml"
    _derivation_net(
        prefix_net,
        edge_ids=("in", "trunk", "out1", "out2"),
        connections=(
            ("in", "trunk", "old_shared_prefix"),
            ("trunk", "out1", ""),
            ("trunk", "out2", "old_k2"),
        ),
    )
    prefix_plan, prefix_audit = derive_official_tls_plan(
        signal_streams=(
            _signal_stream(1, "K1", egress_lane_id="2"),
            _signal_stream(2, "K2", egress_lane_id="3"),
        ),
        lane_bindings=(
            _lane_binding("1", "in", "ingress"),
            _lane_binding("2", "out1", "egress"),
            _lane_binding("3", "out2", "egress"),
        ),
        source_net_file=prefix_net,
        repairs=(
            ConnectionRepair(
                "N1",
                "trunk",
                0,
                "out1",
                0,
                evidence="official_map_stopline",
            ),
        ),
        group_index_by_node={"N1": {"K1": 0, "K2": 1}},
        plan_id="shared-prefix",
        version="1",
    )
    shared_prefix = PhysicalControlledLink("in", 0, "trunk", 0)
    assert prefix_plan.demoted_links == (shared_prefix,)
    assert prefix_audit["demoted_physical_links"] == [
        {"from_edge": "in", "from_lane": 0, "to_edge": "trunk", "to_lane": 0}
    ]
    assert all(
        shared_prefix not in group.physical_links for group in prefix_plan.groups
    )
    assert prefix_audit["status"] == "visual_review_required"
    assert prefix_audit["movements"][0]["visual_review_reasons"] == [
        "selected_control_arc_not_on_bound_ingress"
    ]

    suffix_net = tmp_path / "shared-suffix.net.xml"
    _derivation_net(
        suffix_net,
        edge_ids=("in1", "in2", "merge", "out"),
        connections=(
            ("in1", "merge", "old_k1"),
            ("in2", "merge", "old_k2"),
            ("merge", "out", "old_shared_suffix"),
        ),
    )
    suffix_plan, _ = derive_official_tls_plan(
        signal_streams=(
            _signal_stream(1, "K1", ingress_lane_id="1", egress_lane_id="3"),
            _signal_stream(2, "K2", ingress_lane_id="2", egress_lane_id="3"),
        ),
        lane_bindings=(
            _lane_binding("1", "in1", "ingress"),
            _lane_binding("2", "in2", "ingress"),
            _lane_binding("3", "out", "egress"),
        ),
        source_net_file=suffix_net,
        repairs=(),
        group_index_by_node={"N1": {"K1": 0, "K2": 1}},
        plan_id="shared-suffix",
        version="1",
    )
    assert suffix_plan.demoted_links == (
        PhysicalControlledLink("merge", 0, "out", 0),
    )


def test_plain_application_demotes_links_and_reclassifies_retired_tls_nodes(
    tmp_path: Path,
) -> None:
    source_connections = tmp_path / "source.con.xml"
    connections = ET.Element("connections")
    _connection(connections, "a", 0, "b", 0)
    _connection(connections, "c", 0, "d", 0)
    _write_xml(source_connections, connections)
    source_tllogic = tmp_path / "source.tll.xml"
    tllogics = ET.Element("tlLogics")
    _tllogic(tllogics, "old_demoted")
    _tllogic(tllogics, "old_assigned")
    _tls_connection(tllogics, "a", 0, "b", 0, tls_id="old_demoted", link_index=0)
    _tls_connection(tllogics, "c", 0, "d", 0, tls_id="old_assigned", link_index=0)
    _write_xml(source_tllogic, tllogics)
    source_nodes = tmp_path / "source.nod.xml"
    nodes = ET.Element("nodes")
    ET.SubElement(nodes, "node", {"id": "old_demoted", "type": "traffic_light", "tl": "old_demoted"})
    ET.SubElement(nodes, "node", {"id": "old_assigned", "type": "traffic_light", "tl": "old_assigned"})
    _write_xml(source_nodes, nodes)
    source_node_bytes = source_nodes.read_bytes()
    plan = OfficialTlsPlan(
        plan_id="demotion",
        version="1",
        groups=(
            OfficialTlsGroup(
                "N1",
                "K1",
                "HH_N1",
                0,
                (PhysicalControlledLink("c", 0, "d", 0),),
            ),
        ),
        retired_tls_ids=("old_assigned", "old_demoted"),
        demoted_links=(PhysicalControlledLink("a", 0, "b", 0),),
    )
    output_nodes = tmp_path / "official.nod.xml"

    report = apply_official_tls_plan_to_plain(
        source_connections_file=source_connections,
        source_tllogic_file=source_tllogic,
        source_nodes_file=source_nodes,
        output_connections_file=tmp_path / "official.con.xml",
        output_tllogic_file=tmp_path / "official.tll.xml",
        output_nodes_file=output_nodes,
        plan=plan,
    )

    rows = {
        (row.attrib["from"], row.attrib["to"]): row.attrib
        for row in ET.parse(tmp_path / "official.con.xml").getroot().findall("connection")
    }
    assert "tl" not in rows[("a", "b")]
    assert "linkIndex" not in rows[("a", "b")]
    assert rows[("a", "b")]["uncontrolled"] == "true"
    assert "tl" not in rows[("c", "d")]
    assert "linkIndex" not in rows[("c", "d")]
    assert rows[("c", "d")]["uncontrolled"] == "false"
    tls_bindings = ET.parse(tmp_path / "official.tll.xml").getroot().findall("connection")
    assert [binding.attrib for binding in tls_bindings] == [
        {
            "from": "c",
            "to": "d",
            "fromLane": "0",
            "toLane": "0",
            "tl": "HH_N1",
            "linkIndex": "0",
        }
    ]
    output_node_index = {
        node.attrib["id"]: node for node in ET.parse(output_nodes).getroot().findall("node")
    }
    assert output_node_index["old_demoted"].attrib["type"] == "priority"
    assert "tl" not in output_node_index["old_demoted"].attrib
    assert output_node_index["old_assigned"].attrib["type"] == "traffic_light"
    assert output_node_index["old_assigned"].attrib["tl"] == "HH_N1"
    assert source_nodes.read_bytes() == source_node_bytes
    assert report["demoted_physical_link_count"] == 1


def test_retired_tls_absence_audit_detects_regenerated_controller(tmp_path: Path) -> None:
    stale_net = tmp_path / "stale.net.xml"
    root = ET.Element("net")
    _connection(root, "a", 0, "b", 0, tls_id="old", link_index=0)
    _tllogic(root, "old")
    _write_xml(stale_net, root)

    audit = audit_retired_tls_absence(stale_net, {"old"})

    assert audit["status"] == "fail"
    assert audit["retired_connection_tls_ids"] == ["old"]
    assert audit["retired_tllogic_ids"] == ["old"]
