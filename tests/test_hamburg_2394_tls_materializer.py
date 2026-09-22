from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from torii_sumo.core.hamburg_2394_tls_materializer import (
    _audit_materialized_network,
    _patch_connections,
    _patch_nodes,
    _patch_tllogic,
)
from torii_sumo.core.hamburg_2394_tls_topology import (
    CONTROLLER_ID,
    PASSIVE_OWNER_IDS,
    ROUTING_REPAIRS,
    ROUTING_REMOVALS,
    SIGNAL_OWNER_IDS,
)


def _write_xml(path: Path, root: ET.Element) -> None:
    ET.indent(root, space="    ")
    path.write_text(ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")


def test_patch_nodes_marks_three_signal_owners_and_preserves_passive_nodes(tmp_path: Path) -> None:
    root = ET.Element("nodes")
    for node_id in (*SIGNAL_OWNER_IDS, *PASSIVE_OWNER_IDS):
        ET.SubElement(root, "node", {"id": node_id, "type": "priority"})
    path = tmp_path / "nodes.nod.xml"
    _write_xml(path, root)

    report = _patch_nodes(path)
    assert report == {"status": "pass", "signal_owner_count": 3}
    parsed = ET.parse(path).getroot()
    nodes = {node.attrib["id"]: node.attrib for node in parsed.findall("node")}
    for node_id in SIGNAL_OWNER_IDS:
        assert nodes[node_id]["type"] == "traffic_light"
        assert nodes[node_id]["tl"] == CONTROLLER_ID
    for node_id in PASSIVE_OWNER_IDS:
        assert nodes[node_id] == {"id": node_id, "type": "priority"}


def test_patch_connections_applies_post_join_delete_five_add_three_contract(tmp_path: Path) -> None:
    root = ET.Element("connections")
    for link in (*ROUTING_REMOVALS, *ROUTING_REPAIRS):
        ET.SubElement(
            root,
            "connection",
            {
                "from": link.from_edge,
                "to": link.to_edge,
                "fromLane": str(link.from_lane),
                "toLane": str(link.to_lane),
            },
        )
    path = tmp_path / "connections.con.xml"
    _write_xml(path, root)
    # The repairs must be absent in the actual first-pass input.  Keep only the
    # removals to exercise the exact materializer contract.
    root = ET.parse(path).getroot()
    for element in list(root.findall("connection"))[5:]:
        root.remove(element)
    _write_xml(path, root)

    report = _patch_connections(path, repairs=ROUTING_REPAIRS, removals=ROUTING_REMOVALS)
    assert report == {"status": "pass", "removed_count": 5, "added_count": 3}
    parsed = ET.parse(path).getroot()
    actual = {
        (
            element.attrib["from"],
            int(element.attrib["fromLane"]),
            element.attrib["to"],
            int(element.attrib["toLane"]),
        )
        for element in parsed.findall("connection")
    }
    assert actual == {link.key for link in ROUTING_REPAIRS}


def test_patch_tllogic_emits_six_link_all_red_placeholder(tmp_path: Path) -> None:
    path = tmp_path / "tls.tll.xml"
    _write_xml(path, ET.Element("tlLogics"))
    links = [
        ("47854310#2", 0, "60578519", 0),
        ("47854310#2", 1, "60578519", 1),
        ("47854310#2", 2, "60578519", 2),
        ("9702432#0", 0, "9702432#2", 0),
        ("9702432#0", 1, "9702432#2", 1),
        ("381540198#1", 0, "193847534#0", 0),
        ("381540198#1", 0, "193847534#0", 1),
        ("381540198#1", 1, "554713077", 0),
    ]
    rows = [
        {
            "connection_id": str(index + 1),
            "controlled_stopline_link": {
                "from_edge": link[0],
                "from_lane": link[1],
                "to_edge": link[2],
                "to_lane": link[3],
            },
            "link_index": (2, 3, 3, 0, 4, 1, 1, 5)[index],
        }
        for index, link in enumerate(links)
    ]

    report = _patch_tllogic(path, movement_bindings=rows)
    assert report == {"status": "pass", "controller_id": CONTROLLER_ID, "link_count": 8}
    parsed = ET.parse(path).getroot()
    logic = parsed.find("tlLogic")
    assert logic is not None
    assert logic.attrib["id"] == CONTROLLER_ID
    assert logic.find("phase").attrib == {"duration": "1", "state": "rrrrrr"}
    bindings = [element for element in parsed.findall("connection")]
    assert len(bindings) == 8
    assert {int(element.attrib["linkIndex"]) for element in bindings} == set(range(6))


def test_materialized_network_audit_accepts_shared_controller_and_eight_links(tmp_path: Path) -> None:
    root = ET.Element("net")
    for node_id in SIGNAL_OWNER_IDS:
        ET.SubElement(root, "junction", {"id": node_id, "type": "traffic_light"})
    for node_id in PASSIVE_OWNER_IDS:
        ET.SubElement(root, "junction", {"id": node_id, "type": "priority"})
    for index in range(8):
        owner = SIGNAL_OWNER_IDS[index % 3]
        ET.SubElement(
            root,
            "connection",
            {
                "from": f"from{index}",
                "to": f"to{index}",
                "fromLane": "0",
                "toLane": "0",
                "via": f":{owner}_{index}_0",
                "tl": CONTROLLER_ID,
                "linkIndex": str(index % 6),
            },
        )
    logic = ET.SubElement(root, "tlLogic", {"id": CONTROLLER_ID})
    ET.SubElement(logic, "phase", {"state": "rrrrrr"})
    path = tmp_path / "candidate.net.xml"
    _write_xml(path, root)
    plan = {
        "movement_bindings": [
            {
                "controlled_stopline_link": {
                    "from_edge": f"from{index}",
                    "from_lane": 0,
                    "to_edge": f"to{index}",
                    "to_lane": 0,
                },
                "link_index": index % 6,
            }
            for index in range(8)
        ]
    }
    report = _audit_materialized_network(path, plan)
    assert report["status"] == "pass"
    assert report["signal_owner_ids"] == list(SIGNAL_OWNER_IDS)
    assert report["controlled_link_count"] == 8
