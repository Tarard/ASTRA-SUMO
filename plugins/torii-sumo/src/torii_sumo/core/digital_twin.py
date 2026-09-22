from __future__ import annotations

import csv
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


UTC = timezone.utc


@dataclass(frozen=True)
class CountStream:
    stream_id: int
    thing_id: int | None
    node_id: str
    asset_id: str
    direction: str
    lane_use: str
    longitude: float
    latitude: float
    operation_start: str = ""
    layer_name: str = ""
    direction_code: str = ""
    station_arm: str = ""
    composition: tuple[str, ...] = ()

    @property
    def detector_id(self) -> str:
        asset = self.asset_id.replace(".", "_").replace(" ", "_")
        return f"hh_{self.node_id}_{asset}_{self.stream_id}"


@dataclass(frozen=True)
class CountObservation:
    stream_id: int
    observation_id: int | None
    begin_utc: datetime
    end_utc: datetime
    count: int
    result_time: str = ""


@dataclass(frozen=True)
class WindowSelection:
    local_date: date
    timezone_name: str
    begin_utc: datetime
    end_utc: datetime
    duration_seconds: int
    source_bin_seconds: int
    score: int
    complete: bool
    completeness_ratio: float
    expected_cells: int
    present_cells: int
    missing_cells: tuple[tuple[int, str], ...]
    scoring_method: str = "sum_of_all_lane_field_counts"


@dataclass(frozen=True)
class CanonicalCount:
    detector_id: str
    stream_id: int
    node_id: str
    asset_id: str
    direction: str
    lane_use: str
    longitude: float
    latitude: float
    source_begin_utc: datetime
    source_end_utc: datetime
    begin: int
    end: int
    count: int
    source_observation_count: int
    expected_source_observation_count: int
    quality_status: str


_VEHICLE_ATTRIBUTE_FLAGS = (
    "isVehicleRevocableLane", "isVehicleFlyOverLane", "hovLaneUseOnly", "restrictedToBusUse",
    "restrictedToTaxiUse", "restrictedFromPublicUse", "hasIRbeaconCoverage", "permissionOnRequest",
)
_LANE_SHARING_FLAGS = (
    "overlappingLaneDescriptionProvided", "multipleLanesTreatedAsOneLane", "otherNonMotorizedTrafficTypes",
    "individualMotorizedVehicleTraffic", "busVehicleTraffic", "taxiVehicleTraffic", "pedestriansTraffic",
    "cyclistVehicleTraffic", "trackedVehicleTraffic", "pedestrianTraffic",
)


@dataclass(frozen=True)
class MapLane:
    node_id: str
    lane_id: str
    lane_type: str
    ingress_approach: str
    egress_approach: str
    ref_longitude: float
    ref_latitude: float
    points_m: tuple[tuple[float, float], ...]
    vehicle_attribute_bits: str = ""
    shared_with_bits: str = ""

    @property
    def is_ingress(self) -> bool:
        return bool(self.ingress_approach)

    @property
    def is_vehicle(self) -> bool:
        return self.lane_type.lower() == "vehicle"

    @property
    def permission_metadata(self) -> dict[str, Any]:
        """Decode SAE/ETSI bit positions without treating sharing as an allow list."""
        flags = [name for bit, name in zip(self.vehicle_attribute_bits, _VEHICLE_ATTRIBUTE_FLAGS) if bit == "1"]
        sharing = [name for bit, name in zip(self.shared_with_bits, _LANE_SHARING_FLAGS) if bit == "1"]
        allowed = None
        status = "unrestricted" if self.vehicle_attribute_bits else "unknown"
        unresolved = {"isVehicleFlyOverLane", "hovLaneUseOnly", "restrictedFromPublicUse", "permissionOnRequest"}.intersection(flags)
        bus, taxi = "restrictedToBusUse" in flags, "restrictedToTaxiUse" in flags
        if unresolved or (bus and taxi) or "1" in self.vehicle_attribute_bits[8:]:
            status = "review_required"
        elif bus or taxi:
            allowed, status = (["bus"] if bus else ["taxi"]), "restricted"
        return {"vehicle_attribute_bits": self.vehicle_attribute_bits, "shared_with_bits": self.shared_with_bits,
                "vehicle_attribute_flags": flags, "shared_with_flags": sharing,
                "revocable": "isVehicleRevocableLane" in flags, "allowed_vehicle_classes": allowed,
                "permission_status": status if self.is_vehicle else "not_applicable"}


@dataclass(frozen=True)
class MapConnection:
    node_id: str
    connection_id: str
    ingress_lane_id: str
    egress_lane_id: str
    signal_group: str
    maneuver_bits: str


@dataclass(frozen=True)
class SignalStream:
    stream_id: int
    thing_id: int | None
    node_id: str
    connection_id: str
    ingress_lane_id: str
    egress_lane_id: str
    lane_type: str
    signal_group: str
    layer_name: str
    name: str


@dataclass(frozen=True)
class SignalObservation:
    stream_id: int
    observation_id: int | None
    phenomenon_time_utc: datetime
    result: str
    result_time: str = ""


def parse_iso_datetime(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise ValueError("timestamp is required")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include a UTC offset: {value}")
    return parsed.astimezone(UTC)


def parse_phenomenon_interval(value: str, *, default_seconds: int = 0) -> tuple[datetime, datetime]:
    parts = value.split("/", maxsplit=1)
    begin = parse_iso_datetime(parts[0])
    if len(parts) == 2:
        end_inclusive = parse_iso_datetime(parts[1])
        # Hamburg count intervals are encoded to the last whole second.  Convert
        # them to the half-open interval used by SUMO and this package.
        end = end_inclusive + timedelta(seconds=1)
    elif default_seconds > 0:
        end = begin + timedelta(seconds=default_seconds)
    else:
        end = begin
    if end < begin:
        raise ValueError(f"phenomenonTime end precedes begin: {value}")
    return begin, end


def recent_completed_saturdays(
    *,
    now: datetime | None = None,
    timezone_name: str = "Europe/Berlin",
    limit: int = 8,
) -> list[date]:
    if limit < 1:
        return []
    zone = ZoneInfo(timezone_name)
    current = (now or datetime.now(UTC)).astimezone(zone).date()
    candidate = current - timedelta(days=1)
    while candidate.weekday() != 5:
        candidate -= timedelta(days=1)
    return [candidate - timedelta(days=7 * index) for index in range(limit)]


def select_busiest_complete_window(
    streams: Sequence[CountStream],
    observations: Mapping[int, Sequence[CountObservation]],
    *,
    local_date: date,
    timezone_name: str = "Europe/Berlin",
    duration_seconds: int = 7200,
    source_bin_seconds: int = 300,
    require_complete: bool = True,
) -> WindowSelection | None:
    if not streams:
        raise ValueError("at least one count stream is required")
    if duration_seconds <= 0 or source_bin_seconds <= 0 or duration_seconds % source_bin_seconds:
        raise ValueError("duration_seconds must be a positive multiple of source_bin_seconds")
    stream_ids = [stream.stream_id for stream in streams]
    if len(set(stream_ids)) != len(stream_ids):
        raise ValueError("count stream ids must be unique")

    zone = ZoneInfo(timezone_name)
    day_begin_local = datetime.combine(local_date, datetime.min.time(), tzinfo=zone)
    day_end_local = day_begin_local + timedelta(days=1)
    day_begin_utc = day_begin_local.astimezone(UTC)
    day_end_utc = day_end_local.astimezone(UTC)
    expected_bins = duration_seconds // source_bin_seconds

    cells: dict[tuple[int, datetime], int] = {}
    for stream_id in stream_ids:
        for observation in observations.get(stream_id, ()):
            begin = observation.begin_utc.astimezone(UTC)
            if not (day_begin_utc <= begin < day_end_utc):
                continue
            if int((begin - day_begin_utc).total_seconds()) % source_bin_seconds:
                continue
            key = (stream_id, begin)
            if key in cells:
                raise ValueError(f"duplicate observation cell for stream {stream_id} at {begin.isoformat()}")
            cells[key] = observation.count

    best_complete: WindowSelection | None = None
    best_any: WindowSelection | None = None
    start = day_begin_utc
    last_start = day_end_utc - timedelta(seconds=duration_seconds)
    while start <= last_start:
        missing: list[tuple[int, str]] = []
        score = 0
        present = 0
        for stream_id in stream_ids:
            for index in range(expected_bins):
                timestamp = start + timedelta(seconds=index * source_bin_seconds)
                value = cells.get((stream_id, timestamp))
                if value is None:
                    missing.append((stream_id, timestamp.isoformat().replace("+00:00", "Z")))
                else:
                    present += 1
                    score += value
        expected = len(stream_ids) * expected_bins
        selection = WindowSelection(
            local_date=local_date,
            timezone_name=timezone_name,
            begin_utc=start,
            end_utc=start + timedelta(seconds=duration_seconds),
            duration_seconds=duration_seconds,
            source_bin_seconds=source_bin_seconds,
            score=score,
            complete=not missing,
            completeness_ratio=(present / expected) if expected else 0.0,
            expected_cells=expected,
            present_cells=present,
            missing_cells=tuple(missing),
        )
        if best_any is None or (selection.completeness_ratio, selection.score) > (
            best_any.completeness_ratio,
            best_any.score,
        ):
            best_any = selection
        if selection.complete and (best_complete is None or selection.score > best_complete.score):
            best_complete = selection
        start += timedelta(seconds=source_bin_seconds)

    return best_complete if require_complete else (best_complete or best_any)


def aggregate_window_counts(
    streams: Sequence[CountStream],
    observations: Mapping[int, Sequence[CountObservation]],
    selection: WindowSelection,
    *,
    output_bin_seconds: int = 900,
) -> list[CanonicalCount]:
    if output_bin_seconds <= 0 or output_bin_seconds % selection.source_bin_seconds:
        raise ValueError("output_bin_seconds must be a positive multiple of the source bin")
    if selection.duration_seconds % output_bin_seconds:
        raise ValueError("selected duration must be divisible by output_bin_seconds")
    expected_per_output = output_bin_seconds // selection.source_bin_seconds
    indexed: dict[tuple[int, datetime], CountObservation] = {}
    for stream in streams:
        for observation in observations.get(stream.stream_id, ()):
            indexed[(stream.stream_id, observation.begin_utc.astimezone(UTC))] = observation

    rows: list[CanonicalCount] = []
    for stream in sorted(streams, key=lambda item: (item.node_id, item.asset_id, item.stream_id)):
        for relative_begin in range(0, selection.duration_seconds, output_bin_seconds):
            absolute_begin = selection.begin_utc + timedelta(seconds=relative_begin)
            absolute_end = absolute_begin + timedelta(seconds=output_bin_seconds)
            source_rows = [
                indexed.get(
                    (
                        stream.stream_id,
                        absolute_begin + timedelta(seconds=index * selection.source_bin_seconds),
                    )
                )
                for index in range(expected_per_output)
            ]
            present = [row for row in source_rows if row is not None]
            rows.append(
                CanonicalCount(
                    detector_id=stream.detector_id,
                    stream_id=stream.stream_id,
                    node_id=stream.node_id,
                    asset_id=stream.asset_id,
                    direction=stream.direction,
                    lane_use=stream.lane_use,
                    longitude=stream.longitude,
                    latitude=stream.latitude,
                    source_begin_utc=absolute_begin,
                    source_end_utc=absolute_end,
                    begin=relative_begin,
                    end=relative_begin + output_bin_seconds,
                    count=sum(row.count for row in present),
                    source_observation_count=len(present),
                    expected_source_observation_count=expected_per_output,
                    quality_status="complete" if len(present) == expected_per_output else "missing_source_bins",
                )
            )
    return rows


def write_canonical_counts(path: Path, rows: Iterable[CanonicalCount]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "detector_id",
        "stream_id",
        "node_id",
        "asset_id",
        "direction",
        "lane_use",
        "longitude",
        "latitude",
        "source_begin_utc",
        "source_end_utc",
        "begin",
        "end",
        "interval_seconds",
        "expected_total",
        "source_observation_count",
        "expected_source_observation_count",
        "quality_status",
        "source_system",
        "aggregation_method",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "detector_id": row.detector_id,
                    "stream_id": row.stream_id,
                    "node_id": row.node_id,
                    "asset_id": row.asset_id,
                    "direction": row.direction,
                    "lane_use": row.lane_use,
                    "longitude": f"{row.longitude:.9f}",
                    "latitude": f"{row.latitude:.9f}",
                    "source_begin_utc": row.source_begin_utc.isoformat().replace("+00:00", "Z"),
                    "source_end_utc": row.source_end_utc.isoformat().replace("+00:00", "Z"),
                    "begin": row.begin,
                    "end": row.end,
                    "interval_seconds": row.end - row.begin,
                    "expected_total": row.count,
                    "source_observation_count": row.source_observation_count,
                    "expected_source_observation_count": row.expected_source_observation_count,
                    "quality_status": row.quality_status,
                    "source_system": "hamburg_official_infrared_count_fields",
                    "aggregation_method": "sum_complete_5_minute_source_bins",
                }
            )


def parse_mapem(path: Path) -> tuple[list[MapLane], list[MapConnection]]:
    root = ET.parse(path).getroot()
    lanes: list[MapLane] = []
    connections: list[MapConnection] = []
    for geometry in _descendants(root, "IntersectionGeometry"):
        identifier = _child(geometry, "id")
        node_id = _mapem_node_id(geometry, identifier)
        ref_point = _child(geometry, "refPoint")
        ref_latitude = _scaled_coordinate(_text(_child(ref_point, "lat")))
        ref_longitude = _scaled_coordinate(_text(_child(ref_point, "long")))
        lane_set = _child(geometry, "laneSet")
        if not node_id or ref_point is None or lane_set is None:
            continue
        for lane_element in _children(lane_set, "GenericLane"):
            lane_id = _text(_child(lane_element, "laneID"))
            lane_attributes = _child(lane_element, "laneAttributes")
            lane_type_parent = _child(lane_attributes, "laneType")
            lane_type_element = next(iter(lane_type_parent), None) if lane_type_parent is not None else None
            lane_type = _local_name(lane_type_element.tag) if lane_type_element is not None else "unknown"
            points = _map_lane_points(lane_element)
            lanes.append(
                MapLane(
                    node_id=node_id,
                    lane_id=lane_id,
                    lane_type=lane_type,
                    ingress_approach=_text(_child(lane_element, "ingressApproach")),
                    egress_approach=_text(_child(lane_element, "egressApproach")),
                    ref_longitude=ref_longitude,
                    ref_latitude=ref_latitude,
                    points_m=tuple(points),
                    vehicle_attribute_bits=_map_bit_string(lane_type_element, 8, "vehicle", extensible=True) if lane_type.lower() == "vehicle" else "",
                    shared_with_bits=_map_bit_string(_child(lane_attributes, "sharedWith"), 10, "sharedWith"),
                )
            )
            connects_to = _child(lane_element, "connectsTo")
            if connects_to is None:
                continue
            for connection_element in _children(connects_to, "Connection"):
                connecting_lane = _child(connection_element, "connectingLane")
                connections.append(
                    MapConnection(
                        node_id=node_id,
                        connection_id=_text(_child(connection_element, "connectionID")),
                        ingress_lane_id=lane_id,
                        egress_lane_id=_text(_child(connecting_lane, "lane")),
                        signal_group=_text(_child(connection_element, "signalGroup")),
                        maneuver_bits=_text(_child(connecting_lane, "maneuver")),
                    )
                )
    return lanes, connections


def _map_bit_string(element: ET.Element | None, width: int, label: str, *, extensible: bool = False) -> str:
    bits = "".join(_text(element).split())
    if bits and (set(bits) - {"0", "1"} or (len(bits) < width if extensible else len(bits) != width)):
        raise ValueError(f"MAP {label} bit string must contain {'at least ' if extensible else ''}{width} binary digits")
    return bits


def _mapem_node_id(geometry: ET.Element, identifier: ET.Element | None) -> str:
    """Return the official node id, including Hamburg's MAPEM name fallback.

    Some current Hamburg exports use ``<id><id>0</id></id>`` for the MAPEM
    intersection identifier while encoding the traffic-light node in the
    official asset name, for example ``MAP_ITS_00\\5\\12.1``.  The explicit
    numeric id remains authoritative when it is non-zero; the name fallback is
    limited to the zero/empty form so older files keep their exact behaviour.
    """

    explicit = _text(_child(identifier, "id")) if identifier is not None else ""
    if explicit and explicit != "0":
        return explicit
    name = _text(_child(geometry, "name"))
    match = re.search(r"MAP_ITS_\d+(?:[\\/_]|_)(\d+)(?:[\\/_]|_)", name, flags=re.IGNORECASE)
    return match.group(1) if match else explicit


def write_map_connections(path: Path, connections: Iterable[MapConnection]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "node_id",
                "connection_id",
                "ingress_lane_id",
                "egress_lane_id",
                "signal_group",
                "maneuver_bits",
            ],
        )
        writer.writeheader()
        for connection in connections:
            writer.writerow(connection.__dict__)


def map_signal_state_to_sumo(value: str | int) -> str:
    mapping = {
        "0": "o",  # dark/off
        "1": "r",
        "2": "y",
        "3": "G",
        "4": "u",  # red+yellow
        "5": "o",  # flashing amber has no exact SUMO state
        "6": "G",  # flashing green has no exact SUMO state
        "9": "o",  # unknown; replay audit retains the source value
    }
    return mapping.get(str(value), "o")


def lane_heading_degrees(points: Sequence[tuple[float, float]], *, ingress: bool) -> float | None:
    if len(points) < 2:
        return None
    if ingress:
        start, end = points[-1], points[0]
    else:
        start, end = points[0], points[-1]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if math.hypot(dx, dy) < 1e-9:
        return None
    return math.degrees(math.atan2(dy, dx)) % 360.0


def angular_difference_degrees(left: float, right: float) -> float:
    return abs((left - right + 180.0) % 360.0 - 180.0)


def _map_lane_points(lane_element: ET.Element) -> list[tuple[float, float]]:
    node_list = _child(lane_element, "nodeList")
    nodes = _child(node_list, "nodes")
    if nodes is None:
        return []
    current_x = 0.0
    current_y = 0.0
    points: list[tuple[float, float]] = []
    for node in _children(nodes, "NodeXY"):
        delta = _child(node, "delta")
        coordinate = next(iter(delta), None) if delta is not None else None
        if coordinate is None:
            continue
        try:
            current_x += float(_text(_child(coordinate, "x"))) / 100.0
            current_y += float(_text(_child(coordinate, "y"))) / 100.0
        except ValueError:
            continue
        points.append((current_x, current_y))
    return points


def _scaled_coordinate(value: str) -> float:
    return float(value) / 10_000_000.0


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def _child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    return next((child for child in parent if _local_name(child.tag) == name), None)


def _children(parent: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in parent if _local_name(child.tag) == name]


def _descendants(parent: ET.Element, name: str) -> list[ET.Element]:
    return [element for element in parent.iter() if _local_name(element.tag) == name]


def _text(element: ET.Element | None) -> str:
    return "" if element is None or element.text is None else element.text.strip()
