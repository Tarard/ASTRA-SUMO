from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from astra_sumo.core.sumolib_load_check import SumolibLoadCheck
from astra_sumo.core.xml_stream import DirectChildren, binary_blocks

sumolib_net = pytest.importorskip("sumolib.net")


def _outcome(call) -> str:
    try:
        call()
    except Exception as exc:  # noqa: BLE001 - the error sumolib raises is the result
        return f"{type(exc).__name__}: {exc}"
    return "loaded"


def _check(path: Path, *, with_programs: bool) -> None:
    check = SumolibLoadCheck(with_programs=with_programs)
    stream = DirectChildren(binary_blocks(path))
    for element in stream:
        check.add(element, stream.root)
    check.finish(stream.root)


def _set(xpath: str, key: str, value: str | None):
    def mutate(root: ET.Element) -> None:
        element = root.find(xpath)
        assert element is not None, xpath
        if value is None:
            del element.attrib[key]
        else:
            element.attrib[key] = value
    return mutate


def _insert(position: int, tag: str, **attributes: str):
    def mutate(root: ET.Element) -> None:
        root.insert(position if position >= 0 else len(root), ET.Element(tag, attributes))
    return mutate


MUTATIONS = {
    "unchanged": lambda root: None,
    "lane_without_speed": _set("edge[@id='AB']/lane", "speed", None),
    "lane_length_not_a_number": _set("edge[@id='AB']/lane", "length", "x"),
    "lane_shape_not_a_number": _set("edge[@id='AB']/lane", "shape", "1,a"),
    "edge_priority_not_an_integer": _set("edge[@id='AB']", "priority", "1.5"),
    "junction_without_type": _set("junction[@id='D']", "type", None),
    "junction_without_incoming_lanes": _set("junction[@id='D']", "incLanes", None),
    "junction_x_not_a_number": _set("junction[@id='D']", "x", "inf x"),
    "connection_without_direction": _set("connection[@from='AB']", "dir", None),
    "connection_without_state": _set("connection[@from='AB']", "state", None),
    "connection_to_unknown_edge": _set("connection[@from='AB']", "to", "nowhere"),
    "connection_lane_out_of_range": _set("connection[@from='AB']", "fromLane", "-9"),
    "controller_link_index_not_an_integer": _set("connection[@tl='D']", "linkIndex", "x"),
    "request_index_not_an_integer": _set("junction[@id='D']/request", "index", "x"),
    "tl_logic_offset_infinite": _set("tlLogic[@id='D']", "offset", "inf"),
    "tl_logic_without_type": _set("tlLogic[@id='D']", "type", None),
    "phase_without_duration": _set("tlLogic[@id='D']/phase", "duration", None),
    "phase_next_not_an_integer": _set("tlLogic[@id='D']/phase", "next", "1 x"),
    "location_without_projection": _set("location", "projParameter", None),
    "version_without_minor": lambda root: root.set("version", "1"),
    "bidi_to_unknown_edge": _set("edge[@id='AB']", "bidi", "nowhere"),
    "phase_before_any_program": _insert(0, "phase", state="G", duration="5"),
    "parameter_before_any_program": _insert(0, "param", key="k"),
    "phase_after_the_last_program": _insert(-1, "phase", state="G", duration="5"),
    "request_outside_a_junction": _insert(-1, "request", index="0", foes="0", response="0"),
    "roundabout_without_edges": _insert(-1, "roundabout", nodes="D"),
}


@pytest.mark.parametrize("with_programs", [True, False])
@pytest.mark.parametrize("name", list(MUTATIONS))
def test_load_check_raises_what_sumolib_read_net_raises(signal_network: Path, tmp_path: Path, name, with_programs):
    tree = ET.parse(signal_network)
    MUTATIONS[name](tree.getroot())
    network = tmp_path / f"{name}.net.xml"
    tree.write(network, encoding="utf-8", xml_declaration=True)

    expected = _outcome(lambda: sumolib_net.readNet(str(network), withPrograms=with_programs))
    assert _outcome(lambda: _check(network, with_programs=with_programs)) == expected
    if name == "unchanged":
        assert expected == "loaded"
    elif name == "lane_without_speed":
        assert expected == "KeyError: 'speed'"


def test_load_check_counts_the_lanes_sumolib_loads(signal_network: Path) -> None:
    check = SumolibLoadCheck()
    stream = DirectChildren(binary_blocks(signal_network))
    for element in stream:
        check.add(element, stream.root)
    check.finish(stream.root)
    net = sumolib_net.readNet(str(signal_network))
    assert check.lane_counts == {edge.getID(): edge.getLaneNumber() for edge in net.getEdges(withInternal=False)}
    assert check.unloaded_edges == net._crossings_and_walkingAreas | net._macroConnectors


def test_load_check_raises_for_a_network_in_an_xml_namespace(tmp_path: Path) -> None:
    # sumolib reads such elements by their local name; the streamed readers would read nothing and pass.
    net = tmp_path / "namespaced.net.xml"
    net.write_text(
        '<net xmlns="http://example.org/net" version="1.20">'
        '<edge id="e" from="a" to="b"><lane id="e_0" speed="1" length="5" shape="0,0 5,0"/></edge></net>',
        encoding="utf-8",
    )
    check = SumolibLoadCheck(with_programs=True)
    stream = DirectChildren(binary_blocks(net))
    with pytest.raises(ValueError, match="namespace"):
        for element in stream:
            check.add(element, stream.root)
        check.finish(stream.root)
