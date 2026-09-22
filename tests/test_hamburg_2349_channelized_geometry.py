from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from torii_sumo.core.hamburg_2349_channelized_geometry import (
    Hamburg2349ChannelizedGeometryError,
    _audit_direct_movements,
    _stage_connections,
    _stage_tllogic,
    _validate_source_and_build_shapes,
)


def _edge(
    root: ET.Element,
    edge_id: str,
    from_id: str = "",
    to_id: str = "",
    lane_shapes: tuple[str, ...] = (),
    *,
    internal: bool = False,
) -> None:
    attributes = {"id": edge_id}
    if internal:
        attributes["function"] = "internal"
    else:
        attributes.update({"from": from_id, "to": to_id})
    edge = ET.SubElement(root, "edge", attributes)
    for index, shape in enumerate(lane_shapes):
        ET.SubElement(
            edge,
            "lane",
            {"id": f"{edge_id}_{index}", "index": str(index), "shape": shape},
        )


def _connection(
    root: ET.Element,
    from_edge: str,
    from_lane: int,
    to_edge: str,
    to_lane: int,
    *,
    via: str = "",
    tl: str = "",
    link_index: int | None = None,
) -> ET.Element:
    attributes = {
        "from": from_edge,
        "fromLane": str(from_lane),
        "to": to_edge,
        "toLane": str(to_lane),
    }
    if via:
        attributes["via"] = via
    if tl:
        attributes["tl"] = tl
    if link_index is not None:
        attributes["linkIndex"] = str(link_index)
    return ET.SubElement(root, "connection", attributes)


def _source_root() -> ET.Element:
    root = ET.Element("net")
    _edge(root, "61649647#0", "outside", "2761334249", ("0,0 1,0", "0,1 1,1"))
    _edge(root, "61649647#1", "2761334249", "610506352", ("1.2,0 2,0", "1.2,1 2,1"))
    _edge(
        root,
        "61649647#2",
        "610506352",
        "cluster_739654528_759714704",
        ("2.2,0 3,0", "2.2,1 3,1"),
    )
    _edge(root, "59990286", "cluster_739654528_759714704", "east", ("4,0 5,0", "4,1 5,1"))
    _edge(root, "61649649#0", "cluster_739654528_759714704", "south", ("4,-1 5,-1",))
    _edge(root, ":2761334249_0", lane_shapes=("1,0 1.2,0", "1,1 1.2,1"), internal=True)
    _edge(root, ":610506352_0", lane_shapes=("2,0 2.2,0", "2,1 2.2,1"), internal=True)
    _edge(root, ":core_4", lane_shapes=("3,0 3.5,-0.5 4,-1",), internal=True)
    _edge(root, ":core_5", lane_shapes=("3,0 4,0", "3,1 4,1"), internal=True)
    _connection(root, "61649647#0", 0, "61649647#1", 0, via=":2761334249_0_0", tl="2761334249", link_index=0)
    _connection(root, "61649647#0", 1, "61649647#1", 1, via=":2761334249_0_1", tl="2761334249", link_index=1)
    _connection(root, "61649647#1", 0, "61649647#2", 0, via=":610506352_0_0")
    _connection(root, "61649647#1", 1, "61649647#2", 1, via=":610506352_0_1", tl="HH_2349", link_index=2)
    _connection(root, "61649647#2", 0, "59990286", 0, via=":core_5_0", tl="HH_2349", link_index=2)
    _connection(root, "61649647#2", 0, "61649649#0", 0, via=":core_4_0", tl="HH_2349", link_index=5)
    _connection(root, "61649647#2", 1, "59990286", 1, via=":core_5_1")
    return root


def test_source_contract_builds_three_five_piece_channelized_shapes() -> None:
    shapes = _validate_source_and_build_shapes(_source_root())

    assert set(shapes) == {"C4", "C5", "C6"}
    assert shapes["C5"] == "1.000000,0.000000 1.200000,0.000000 2.000000,0.000000 2.200000,0.000000 3.000000,0.000000 4.000000,0.000000"
    assert "3.500000,-0.500000" in shapes["C6"]
    assert shapes["C4"].startswith("1.000000,1.000000")


def test_source_contract_rejects_an_extra_absorbed_edge_movement() -> None:
    root = _source_root()
    _connection(root, "61649647#1", 0, "61649647#2", 1, via=":610506352_0_1")

    with pytest.raises(Hamburg2349ChannelizedGeometryError, match="movement chain"):
        _validate_source_and_build_shapes(root)


def test_plain_staging_keeps_exact_three_movements_and_rebinds_tls(tmp_path) -> None:
    connections = ET.Element("connections")
    for from_lane, to_edge, to_lane in (
        (0, "59990286", 0),
        (0, "61649649#0", 0),
        (1, "59990286", 1),
    ):
        item = _connection(connections, "61649647#0", from_lane, to_edge, to_lane)
        item.set("uncontrolled", "1")
    _connection(connections, "outside-in", 0, "outside-out", 0)
    raw_connections = tmp_path / "raw.con.xml"
    ET.ElementTree(connections).write(raw_connections, encoding="utf-8", xml_declaration=True)
    staged_connections = tmp_path / "staged.con.xml"
    direct = _stage_connections(
        raw_connections,
        staged_connections,
        {"C4": "0,1 1,1", "C5": "0,0 1,0", "C6": "0,0 1,-1"},
    )
    assert len(direct) == 3
    assert all("uncontrolled" not in item.attrib for item in direct)

    tll = ET.Element("tlLogics")
    ET.SubElement(tll, "tlLogic", {"id": "2761334249"})
    ET.SubElement(tll, "tlLogic", {"id": "HH_2349"})
    for key, tl, index in (
        (("61649647#0", 0, "61649647#1", 0), "2761334249", 0),
        (("61649647#0", 1, "61649647#1", 1), "2761334249", 1),
        (("61649647#1", 1, "61649647#2", 1), "HH_2349", 2),
        (("61649647#2", 0, "59990286", 0), "HH_2349", 2),
        (("61649647#2", 0, "61649649#0", 0), "HH_2349", 5),
    ):
        _connection(tll, *key, tl=tl, link_index=index)
    _connection(tll, "outside-in", 0, "outside-out", 0, tl="OTHER", link_index=0)
    raw_tll = tmp_path / "raw.tll.xml"
    ET.ElementTree(tll).write(raw_tll, encoding="utf-8", xml_declaration=True)
    staged_tll = tmp_path / "staged.tll.xml"
    _stage_tllogic(raw_tll, staged_tll)

    staged_root = ET.parse(staged_tll).getroot()
    assert {item.attrib["id"] for item in staged_root.findall("tlLogic")} == {"HH_2349"}
    candidate = ET.Element("net")
    for item in staged_root.findall("connection"):
        candidate.append(item)
    audit = _audit_direct_movements(candidate)
    assert audit["status"] == "pass"
    _connection(candidate, "61649647#0", 1, "61649649#0", 0, tl="HH_2349", link_index=5)
    assert _audit_direct_movements(candidate)["status"] == "fail"
