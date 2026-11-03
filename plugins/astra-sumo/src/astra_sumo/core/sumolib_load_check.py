"""Raise what sumolib raises while it loads a SUMO network, without building sumolib's network model.

``sumolib.net.readNet`` keeps an object per junction, edge, lane and connection, several gigabytes for a city
network. Readers that stream a network and need only part of it still gate on whether sumolib loads it: the TLS
audit and aggregation fail, and the topology audit falls back to XY coordinates, where sumolib raises.
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

_UNSET = object()


def _read(attributes: dict[str, str], *keys: str) -> None:
    """Read attributes as sumolib reads them, raising ``KeyError`` for the first one missing."""
    for key in keys:
        attributes[key]


def _lane_index(lane_count: int, spelled: str) -> None:
    """Index the lanes as sumolib's ``edge.getLane(int(spelled))`` indexes its lane list."""
    index = int(spelled)
    if not -lane_count <= index < lane_count:
        raise IndexError("list index out of range")


class SumolibLoadCheck:
    """Mirror the element handlers of ``sumolib.net.NetReader`` for ``sumolib.net.readNet(net_file)``, or with
    ``withPrograms=True`` when ``with_programs`` is set, on a network read one top-level element at a time.

    Feed each top-level element with ``add`` and then call ``finish``, both with the document root.
    Every attribute sumolib reads is read and converted in the same order, with sumolib's own shape and number
    conversions, so the first element sumolib cannot load raises the error sumolib raises. Only the lane count of
    each plain edge, the ids of crossing, walking-area and macro-connector edges and the bidi references are kept.
    Elements of the network format before SUMO 0.13 (``succ``, ``succlane``, ``ROWLogic``, ``logicitem``) are not
    checked. A network element in an XML namespace raises ``ValueError``: sumolib reads it by its local name, which
    the streamed readers do not, so such a network fails instead of passing with nothing read.
    """

    def __init__(self, *, with_programs: bool = False) -> None:
        from sumolib.miscutils import intIfPossible  # type: ignore
        from sumolib.net import convertShape  # type: ignore

        self._number = intIfPossible
        self._shape = convertShape
        self._with_programs = with_programs
        self.lane_counts: dict[str, int] = {}
        self.unloaded_edges: set[str] = set()
        self._bidi: dict[str, str] = {}
        self._started = False
        self._edge: str | None = None
        self._lane = self._node = self._connection = False
        # NetReader sets its current program only at the first tlLogic.
        self._program: Any = _UNSET
        self._starts = {
            "net": self._start_net,
            "location": self._start_location,
            "type": self._start_type,
            "edge": self._start_edge,
            "lane": self._start_lane,
            "neigh": self._start_neigh,
            "junction": self._start_junction,
            "connection": self._start_connection,
            "request": self._start_request,
            "roundabout": self._start_roundabout,
            "param": self._start_param,
        }
        if with_programs:
            self._starts.update(tlLogic=self._start_tl_logic, phase=self._start_phase)

    def add(self, element: ET.Element, root: ET.Element) -> None:
        """Check a complete top-level element and its descendants in document order, as start and end events."""
        if not self._started:
            self._started = True
            self._start_element(root)
        self._start_element(element)
        stack = [(element, iter(element))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                stack.pop()
                self._end(node.tag)
            else:
                self._start_element(child)
                stack.append((child, iter(child)))

    def finish(self, root: ET.Element) -> None:
        """Check the end of the document root, after its last top-level element."""
        if not self._started:
            self._started = True
            self._start_element(root)
        self._end(root.tag)

    def _start_element(self, element: ET.Element) -> None:
        tag = element.tag
        if tag[:1] == "{" and tag.partition("}")[2] in self._starts:
            # sumolib's parser reports a namespaced element by its local name, the streamed readers by the full name.
            raise ValueError(f"the network element {tag} is in an XML namespace")
        handler = self._starts.get(tag)
        if handler is not None:
            handler(element.attrib)

    def _end(self, tag: str) -> None:
        if tag == "lane":
            self._lane = False
        elif tag == "edge":
            self._edge = None
        elif tag == "junction":
            self._node = False
        elif tag == "connection":
            self._connection = False
        elif self._with_programs and tag in {"tlLogic", "tl-logic"}:
            self._program = None
        elif tag == "net":
            for bidi in self._bidi.values():
                self.lane_counts[bidi]  # sumolib looks the bidi edge up and raises KeyError

    def _start_net(self, attributes: dict[str, str]) -> None:
        parts = attributes["version"].split(".", 1)
        int(parts[0])
        float(parts[1])

    def _start_location(self, attributes: dict[str, str]) -> None:
        _read(attributes, "netOffset", "convBoundary", "origBoundary", "projParameter")

    def _start_type(self, attributes: dict[str, str]) -> None:
        _read(attributes, "id")

    def _start_edge(self, attributes: dict[str, str]) -> None:
        function = attributes.get("function", "")
        if function == "":
            if "priority" in attributes:
                int(attributes["priority"])
            edge_id = attributes["id"]
            self.lane_counts.setdefault(edge_id, 0)  # a repeated id adds lanes to the first edge
            self._edge = edge_id
            self._shape(attributes.get("shape", ""))
            bidi = attributes.get("bidi", "")
            if bidi:
                self._bidi[edge_id] = bidi
        else:
            if function in {"crossing", "walkingarea", "connector"}:
                self.unloaded_edges.add(attributes["id"])
            self._edge = None

    def _start_lane(self, attributes: dict[str, str]) -> None:
        if self._edge is None:
            return
        float(attributes["speed"])
        float(attributes["length"])
        float(attributes.get("width", 3.2))
        self.lane_counts[self._edge] += 1
        self._lane = True
        self._shape(attributes.get("shape", ""))

    def _start_neigh(self, attributes: dict[str, str]) -> None:
        if self._lane:
            _read(attributes, "lane")

    def _start_junction(self, attributes: dict[str, str]) -> None:
        if attributes["id"][0] == ":":
            return
        _read(attributes, "type")
        coordinates = [attributes["x"], attributes["y"], attributes["z"] if "z" in attributes else "0"]
        tuple(map(float, coordinates))
        attributes["incLanes"].split(" ")
        self._node = True
        self._shape(attributes.get("shape", ""))

    def _start_connection(self, attributes: dict[str, str]) -> None:
        first = attributes["from"]
        if first[0] == ":":
            return
        last = attributes["to"]
        if first in self.unloaded_edges or last in self.unloaded_edges:
            return
        first_lanes = self.lane_counts[first]
        last_lanes = self.lane_counts[last]
        _lane_index(first_lanes, attributes["fromLane"])
        _lane_index(last_lanes, attributes["toLane"])
        if "tl" in attributes and attributes["tl"] != "":
            int(attributes["linkIndex"])
            int(attributes.get("linkIndex2", -1))
        _read(attributes, "dir", "state")
        self._connection = True

    def _start_request(self, attributes: dict[str, str]) -> None:
        if not self._node:
            raise AttributeError("'NoneType' object has no attribute 'setFoes'")
        int(attributes["index"])
        _read(attributes, "foes", "response")

    def _start_tl_logic(self, attributes: dict[str, str]) -> None:
        _read(attributes, "id", "programID")
        self._number(float(attributes["offset"]))
        _read(attributes, "type")
        self._program = True

    def _current_program(self, method: str) -> None:
        if self._program is _UNSET:
            raise AttributeError("'NetReader' object has no attribute '_currentProgram'")
        if not self._program:
            raise AttributeError(f"'NoneType' object has no attribute '{method}'")

    def _start_phase(self, attributes: dict[str, str]) -> None:
        self._current_program("addPhase")
        _read(attributes, "state")
        self._number(float(attributes["duration"]))
        if "minDur" in attributes:
            self._number(float(attributes["minDur"]))
        if "maxDur" in attributes:
            self._number(float(attributes["maxDur"]))
        if "next" in attributes:
            list(map(int, attributes["next"].split()))

    def _start_roundabout(self, attributes: dict[str, str]) -> None:
        attributes["nodes"].split()
        attributes["edges"].split()

    def _start_param(self, attributes: dict[str, str]) -> None:
        if self._lane or self._edge is not None or self._node or self._connection:
            pass
        elif not self._with_programs:
            return
        else:
            if self._program is _UNSET:
                self._current_program("setParam")
            if not self._program:
                return
        _read(attributes, "key", "value")
