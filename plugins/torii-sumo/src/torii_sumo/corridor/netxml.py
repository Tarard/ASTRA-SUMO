from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


def normalized_net_sha256(path: Path) -> str:
    """Hash network XML semantics while excluding generator comments/formatting."""

    canonical = ET.canonicalize(
        from_file=str(path.resolve(strict=True)),
        with_comments=False,
        strip_text=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RawLane:
    lane_id: str
    ordinal: int
    declared_index: int | None
    speed: float | None
    length: float | None
    width: float | None
    allow: tuple[str, ...]
    disallow: tuple[str, ...]
    shape: tuple[tuple[float, float], ...]
    params: dict[str, str]


@dataclass(frozen=True)
class RawLocation:
    net_offset: tuple[float, float] | None
    conv_boundary: str
    orig_boundary: str
    proj_parameter: str
    attributes: dict[str, str]


@dataclass(frozen=True)
class RawEdge:
    edge_id: str
    from_junction: str
    to_junction: str
    function: str
    edge_type: str
    priority: int | None
    name: str
    params: dict[str, str]
    crossing_edge_ids: tuple[str, ...]
    lanes: tuple[RawLane, ...]

    @property
    def external(self) -> bool:
        return not self.edge_id.startswith(":") and self.function not in {"internal", "crossing", "walkingarea"}


@dataclass(frozen=True)
class RawConnection:
    connection_index: int
    from_edge: str
    to_edge: str
    from_lane: int | None
    to_lane: int | None
    via: str
    direction: str
    state: str
    allow: tuple[str, ...]
    disallow: tuple[str, ...]
    controller_id: str
    link_index: int | None
    link_index2: int | None


@dataclass(frozen=True)
class RawRequest:
    index: int | None
    response: str
    foes: str
    cont: str


@dataclass(frozen=True)
class RawJunction:
    junction_id: str
    junction_type: str
    x: float | None
    y: float | None
    incoming_lane_ids: tuple[str, ...]
    internal_lane_ids: tuple[str, ...]
    requests: tuple[RawRequest, ...]


@dataclass(frozen=True)
class RawPhase:
    duration: float | None
    state: str
    minimum_duration: float | None
    maximum_duration: float | None
    next_phases: tuple[int, ...]
    name: str


@dataclass(frozen=True)
class RawTLSProgram:
    controller_id: str
    program_id: str
    controller_type: str
    offset: float | None
    phases: tuple[RawPhase, ...]


@dataclass(frozen=True)
class RawNetwork:
    lefthand: bool
    lefthand_attribute: str | None
    location: RawLocation | None
    edges: dict[str, RawEdge]
    lanes: dict[str, RawLane]
    lane_edge_ids: dict[str, str]
    connections: tuple[RawConnection, ...]
    junctions: dict[str, RawJunction]
    tls_programs: dict[str, tuple[RawTLSProgram, ...]]


def parse_net_xml_file(path: Path) -> RawNetwork:
    return parse_net_xml(ET.parse(path).getroot())


def parse_net_xml(root: ET.Element) -> RawNetwork:
    lefthand_attribute = root.attrib.get("lefthand")
    lefthand = _boolean(lefthand_attribute, default=False)
    location_element = root.find("location")
    location = (
        RawLocation(
            net_offset=_pair(location_element.attrib.get("netOffset")),
            conv_boundary=location_element.attrib.get("convBoundary", ""),
            orig_boundary=location_element.attrib.get("origBoundary", ""),
            proj_parameter=location_element.attrib.get("projParameter", ""),
            attributes=dict(location_element.attrib),
        )
        if location_element is not None
        else None
    )
    edges: dict[str, RawEdge] = {}
    lanes: dict[str, RawLane] = {}
    lane_edge_ids: dict[str, str] = {}
    for edge_element in root.findall("edge"):
        edge_id = edge_element.attrib.get("id", "")
        if not edge_id:
            raise ValueError("SUMO edges require IDs.")
        if edge_id in edges:
            raise ValueError(f"Duplicate SUMO edge ID: {edge_id}")
        raw_lanes: list[RawLane] = []
        for ordinal, lane_element in enumerate(edge_element.findall("lane")):
            lane_id = lane_element.attrib.get("id", "")
            if not lane_id:
                raise ValueError(f"SUMO edge {edge_id} contains a lane without an ID.")
            if lane_id in lanes:
                raise ValueError(f"Duplicate SUMO lane ID: {lane_id}")
            lane = RawLane(
                lane_id=lane_id,
                ordinal=ordinal,
                declared_index=_integer(lane_element.attrib.get("index")),
                speed=_number(lane_element.attrib.get("speed")),
                length=_number(lane_element.attrib.get("length")),
                width=_number(lane_element.attrib.get("width")),
                allow=_tokens(lane_element.attrib.get("allow")),
                disallow=_tokens(lane_element.attrib.get("disallow")),
                shape=_shape(lane_element.attrib.get("shape", "")),
                params=_params(lane_element),
            )
            lanes[lane_id] = lane
            lane_edge_ids[lane_id] = edge_id
            raw_lanes.append(lane)
        params = _params(edge_element)
        edges[edge_id] = RawEdge(
            edge_id=edge_id,
            from_junction=edge_element.attrib.get("from", ""),
            to_junction=edge_element.attrib.get("to", ""),
            function=edge_element.attrib.get("function", ""),
            edge_type=edge_element.attrib.get("type", ""),
            priority=_integer(edge_element.attrib.get("priority")),
            name=edge_element.attrib.get("name", ""),
            params=params,
            crossing_edge_ids=_tokens(edge_element.attrib.get("crossingEdges")),
            lanes=tuple(raw_lanes),
        )

    connections = tuple(
        RawConnection(
            connection_index=index,
            from_edge=element.attrib.get("from", ""),
            to_edge=element.attrib.get("to", ""),
            from_lane=_integer(element.attrib.get("fromLane")),
            to_lane=_integer(element.attrib.get("toLane")),
            via=element.attrib.get("via", ""),
            direction=element.attrib.get("dir", ""),
            state=element.attrib.get("state", ""),
            allow=_tokens(element.attrib.get("allow")),
            disallow=_tokens(element.attrib.get("disallow")),
            controller_id=element.attrib.get("tl", ""),
            link_index=_integer(element.attrib.get("linkIndex")),
            link_index2=_integer(element.attrib.get("linkIndex2")),
        )
        for index, element in enumerate(root.findall("connection"))
    )

    junctions: dict[str, RawJunction] = {}
    for element in root.findall("junction"):
        junction_id = element.attrib.get("id", "")
        if not junction_id:
            raise ValueError("SUMO junctions require IDs.")
        if junction_id in junctions:
            raise ValueError(f"Duplicate SUMO junction ID: {junction_id}")
        junctions[junction_id] = RawJunction(
            junction_id=junction_id,
            junction_type=element.attrib.get("type", ""),
            x=_number(element.attrib.get("x")),
            y=_number(element.attrib.get("y")),
            incoming_lane_ids=_tokens(element.attrib.get("incLanes")),
            internal_lane_ids=_tokens(element.attrib.get("intLanes")),
            requests=tuple(
                RawRequest(
                    index=_integer(request.attrib.get("index")),
                    response=request.attrib.get("response", ""),
                    foes=request.attrib.get("foes", ""),
                    cont=request.attrib.get("cont", ""),
                )
                for request in element.findall("request")
            ),
        )

    programs: dict[str, list[RawTLSProgram]] = {}
    for element in root.findall("tlLogic"):
        controller_id = element.attrib.get("id", "")
        if not controller_id:
            raise ValueError("SUMO tlLogic elements require IDs.")
        programs.setdefault(controller_id, []).append(
            RawTLSProgram(
                controller_id=controller_id,
                program_id=element.attrib.get("programID", ""),
                controller_type=element.attrib.get("type", ""),
                offset=_number(element.attrib.get("offset")),
                phases=tuple(
                    RawPhase(
                        duration=_number(phase.attrib.get("duration")),
                        state=phase.attrib.get("state", ""),
                        minimum_duration=_number(phase.attrib.get("minDur")),
                        maximum_duration=_number(phase.attrib.get("maxDur")),
                        next_phases=tuple(
                            value
                            for token in _tokens(phase.attrib.get("next"))
                            if (value := _integer(token)) is not None
                        ),
                        name=phase.attrib.get("name", ""),
                    )
                    for phase in element.findall("phase")
                ),
            )
        )

    return RawNetwork(
        lefthand=lefthand,
        lefthand_attribute=lefthand_attribute,
        location=location,
        edges=edges,
        lanes=lanes,
        lane_edge_ids=lane_edge_ids,
        connections=connections,
        junctions=junctions,
        tls_programs={
            controller_id: tuple(controller_programs) for controller_id, controller_programs in programs.items()
        },
    )


def _tokens(value: str | None) -> tuple[str, ...]:
    return tuple(token for token in str(value or "").replace(",", " ").split() if token)


def _params(element: ET.Element) -> dict[str, str]:
    return {
        child.attrib.get("key", ""): child.attrib.get("value", "")
        for child in element.findall("param")
        if child.attrib.get("key")
    }


def _pair(value: str | None) -> tuple[float, float] | None:
    parts = str(value or "").split(",")
    if len(parts) != 2:
        return None
    try:
        pair = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    return pair if all(math.isfinite(item) for item in pair) else None


def _integer(value: str | None) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _number(value: str | None) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _boolean(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid SUMO boolean value: {value!r}")


def _shape(value: str) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for token in value.split():
        coordinates = token.split(",")
        if len(coordinates) < 2:
            raise ValueError(f"Invalid SUMO shape point: {token!r}")
        try:
            point = float(coordinates[0]), float(coordinates[1])
        except ValueError as exc:
            raise ValueError(f"Invalid SUMO shape point: {token!r}") from exc
        if not all(math.isfinite(item) for item in point):
            raise ValueError(f"Non-finite SUMO shape point: {token!r}")
        points.append(point)
    return tuple(points)
