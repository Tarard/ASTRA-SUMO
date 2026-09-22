from __future__ import annotations

import hashlib
import heapq
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from pyproj import CRS, Transformer

from .command_runner import run_command
from .detector_demand import lane_allows_passenger, read_net


COMPACT_CORRIDOR_SCHEMA_ID = "torii.compact-corridor-scope.v1"


class CorridorScopeError(ValueError):
    """Raised when a compact corridor cannot be selected without losing required lanes."""


@dataclass(frozen=True)
class DirectionalSiblingEvidence:
    """Source-network evidence for one missing reverse OSM edge sibling."""

    osm_way_id: str
    selected_edge_id: str
    sibling_edge_id: str
    selected_from_node: str
    selected_to_node: str
    sibling_from_node: str
    sibling_to_node: str
    selected_allows_passenger: bool
    sibling_allows_passenger: bool
    selected_oneway: str
    sibling_oneway: str
    reciprocal_endpoints: bool
    connected_to_retained_scope: bool
    decision: str
    reason: str


@dataclass(frozen=True)
class CorridorScopeSelection:
    selected_edge_ids: tuple[str, ...]
    geometry_seed_edge_ids: tuple[str, ...]
    required_edge_ids: tuple[str, ...]
    bridge_edge_ids: tuple[str, ...]
    dropped_seed_edge_ids: tuple[str, ...]
    centers_xy: tuple[tuple[float, float], ...]
    source_external_edge_count: int
    source_passenger_edge_count: int
    seed_component_sizes: tuple[int, ...]
    selected_component_count: int
    bridge_length_m: float
    added_directional_sibling_edge_ids: tuple[str, ...]
    excluded_directional_sibling_edge_ids: tuple[str, ...]
    directional_sibling_evidence: tuple[DirectionalSiblingEvidence, ...]
    directional_sibling_evidence_sha256: str


@dataclass(frozen=True)
class _EdgeGeometry:
    edge_id: str
    from_node: str
    to_node: str
    points: tuple[tuple[float, float], ...]
    length_m: float
    allows_passenger: bool
    oneway: str


def select_compact_corridor_edges(
    source_net_file: Path,
    *,
    centers_lonlat: Sequence[tuple[float, float]],
    required_lane_ids: Sequence[str] = (),
    corridor_buffer_m: float = 25.0,
    intersection_stub_radius_m: float = 80.0,
    max_bridge_length_m: float = 300.0,
) -> CorridorScopeSelection:
    """Select the smallest connected passenger scope around an ordered junction corridor.

    Geometry creates local seeds only.  Every seed component containing an official required
    lane is retained, and disconnected required components are joined by the shortest passenger
    edge path in the frozen source network.  Unrelated seed components are dropped instead of
    widening the corridor rectangle.
    """

    _validate_scope_parameters(
        centers_lonlat,
        corridor_buffer_m,
        intersection_stub_radius_m,
        max_bridge_length_m,
    )
    try:
        root = ET.parse(source_net_file).getroot()
    except (OSError, ET.ParseError) as exc:
        raise CorridorScopeError(f"cannot read source SUMO network: {exc}") from exc
    if root.tag != "net":
        raise CorridorScopeError(f"source file root must be <net>, got <{root.tag}>")
    centers_xy = _project_centers(root, centers_lonlat)
    edges, lane_to_edge = _read_edge_geometries(root)
    passenger_edge_ids = {edge_id for edge_id, edge in edges.items() if edge.allows_passenger}
    required_edges: set[str] = set()
    missing_required_lanes: list[str] = []
    non_passenger_required_lanes: list[str] = []
    for lane_id in sorted(set(required_lane_ids)):
        edge_id = lane_to_edge.get(lane_id)
        if edge_id is None:
            missing_required_lanes.append(lane_id)
            continue
        if edge_id not in passenger_edge_ids:
            non_passenger_required_lanes.append(lane_id)
            continue
        required_edges.add(edge_id)
    if missing_required_lanes:
        raise CorridorScopeError(
            "required official lanes are absent from the source network: "
            + ", ".join(missing_required_lanes)
        )
    if non_passenger_required_lanes:
        raise CorridorScopeError(
            "required official lanes do not permit passenger traffic: "
            + ", ".join(non_passenger_required_lanes)
        )

    geometry_seed = {
        edge_id
        for edge_id in passenger_edge_ids
        if _edge_intersects_scope(
            edges[edge_id].points,
            centers_xy,
            corridor_buffer_m=corridor_buffer_m,
            intersection_stub_radius_m=intersection_stub_radius_m,
        )
    }
    geometry_seed.update(required_edges)
    if not geometry_seed:
        raise CorridorScopeError("corridor geometry selected no passenger edges")

    graph = _passenger_edge_graph(root, passenger_edge_ids)
    seed_components = _weak_components(geometry_seed, graph)
    required_components = [component for component in seed_components if component & required_edges]
    if required_edges and not required_components:
        raise CorridorScopeError("no geometry seed component contains a required official lane")
    if not required_components:
        required_components = [seed_components[0]]

    required_components.sort(key=lambda item: (-len(item), min(item)))
    selected = set(required_components[0])
    remaining = [set(component) for component in required_components[1:]]
    bridge_edges: set[str] = set()
    bridge_length_m = 0.0
    while remaining:
        path, target_index = _shortest_component_bridge(
            selected,
            remaining,
            graph,
            edges,
        )
        target_component = remaining[target_index]
        added = [
            edge_id
            for edge_id in path
            if edge_id not in selected and edge_id not in target_component
        ]
        added_length = sum(edges[edge_id].length_m for edge_id in added)
        if bridge_length_m + added_length > max_bridge_length_m:
            raise CorridorScopeError(
                "required official corridor components need an excessive bridge: "
                f"{bridge_length_m + added_length:.3f}m > {max_bridge_length_m:.3f}m"
            )
        bridge_edges.update(added)
        bridge_length_m += added_length
        selected.update(path)
        selected.update(remaining.pop(target_index))

    sibling_edges, sibling_evidence = _close_directional_sibling_parity(
        selected,
        graph=graph,
        edges=edges,
    )
    selected.update(sibling_edges)
    selected_components = _weak_components(selected, graph)
    if len(selected_components) != 1:
        raise CorridorScopeError(
            f"compact corridor selection is not weakly connected: {len(selected_components)} components"
        )
    if not required_edges.issubset(selected):
        raise CorridorScopeError("compact corridor selection lost one or more required official lanes")
    excluded_siblings = {
        item.sibling_edge_id
        for item in sibling_evidence
        if item.decision == "excluded"
    }
    return CorridorScopeSelection(
        selected_edge_ids=tuple(sorted(selected)),
        geometry_seed_edge_ids=tuple(sorted(geometry_seed)),
        required_edge_ids=tuple(sorted(required_edges)),
        bridge_edge_ids=tuple(sorted(bridge_edges)),
        dropped_seed_edge_ids=tuple(sorted(geometry_seed - selected)),
        centers_xy=tuple(centers_xy),
        source_external_edge_count=len(edges),
        source_passenger_edge_count=len(passenger_edge_ids),
        seed_component_sizes=tuple(len(component) for component in seed_components),
        selected_component_count=len(selected_components),
        bridge_length_m=bridge_length_m,
        added_directional_sibling_edge_ids=tuple(sorted(sibling_edges)),
        excluded_directional_sibling_edge_ids=tuple(sorted(excluded_siblings)),
        directional_sibling_evidence=tuple(sibling_evidence),
        directional_sibling_evidence_sha256=_sha256_json(
            [asdict(item) for item in sibling_evidence]
        ),
    )


def build_compact_corridor_variant(
    *,
    source_net_file: Path,
    output_dir: Path,
    centers_lonlat: Sequence[tuple[float, float]],
    required_lane_ids: Sequence[str] = (),
    prefix: str = "compact_corridor",
    corridor_buffer_m: float = 25.0,
    intersection_stub_radius_m: float = 80.0,
    max_bridge_length_m: float = 300.0,
    netconvert_binary: str = "netconvert",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., Any] = run_command,
) -> dict[str, Any]:
    """Materialize an immutable-source, minimal three-junction corridor network variant."""

    source_net_file = source_net_file.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_net_file = output_dir / f"{prefix}.net.xml"
    keep_edges_file = output_dir / f"{prefix}.keep_edges.txt"
    manifest_file = output_dir / f"{prefix}.manifest.json"
    source_hash_before = _sha256_file(source_net_file) if source_net_file.is_file() else ""
    report: dict[str, Any] = {
        "schema_id": COMPACT_CORRIDOR_SCHEMA_ID,
        "status": "fail",
        "claim_status": "construction-invalid",
        "source_net_file": str(source_net_file),
        "source_net_sha256_before": source_hash_before,
        "output_net_file": str(output_net_file),
        "scope": {
            "centers_lonlat": [list(item) for item in centers_lonlat],
            "corridor_buffer_m": corridor_buffer_m,
            "intersection_stub_radius_m": intersection_stub_radius_m,
            "max_bridge_length_m": max_bridge_length_m,
            "vehicle_scope": "passenger/private lanes only",
        },
    }
    try:
        if not source_net_file.is_file():
            raise CorridorScopeError(f"source SUMO network does not exist: {source_net_file}")
        selection = select_compact_corridor_edges(
            source_net_file,
            centers_lonlat=centers_lonlat,
            required_lane_ids=required_lane_ids,
            corridor_buffer_m=corridor_buffer_m,
            intersection_stub_radius_m=intersection_stub_radius_m,
            max_bridge_length_m=max_bridge_length_m,
        )
        keep_edges_file.write_text(
            "\n".join(selection.selected_edge_ids) + "\n",
            encoding="utf-8",
        )
        keep_edges_sha256 = _sha256_file(keep_edges_file)
        command = [
            netconvert_binary,
            "--sumo-net-file",
            str(source_net_file),
            "--keep-edges.input-file",
            str(keep_edges_file),
            "--keep-edges.postload",
            "--output-file",
            str(output_net_file),
        ]
        result = _result_to_dict(
            command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds)
        )
        command_attempts = [command]
        fallback_used = False
        if result.get("status") != "pass" or not output_net_file.is_file():
            fallback = [value for value in command if value != "--keep-edges.postload"]
            command_attempts.append(fallback)
            result = _result_to_dict(
                command_runner(fallback, cwd=output_dir, timeout_seconds=timeout_seconds)
            )
            fallback_used = result.get("status") == "pass" and output_net_file.is_file()
        if result.get("status") != "pass" or not output_net_file.is_file():
            raise CorridorScopeError("netconvert did not create the compact corridor network")

        output_root = ET.parse(output_net_file).getroot()
        output_edges, output_lane_to_edge = _read_edge_geometries(output_root)
        missing_selected_edges = sorted(set(selection.selected_edge_ids) - set(output_edges))
        missing_required_lanes = sorted(set(required_lane_ids) - set(output_lane_to_edge))
        output_passenger_edges, output_connections = read_net(output_net_file)
        passenger_ids = {
            edge_id for edge_id, edge in output_passenger_edges.items() if edge.allows_passenger
        }
        passenger_graph = {edge_id: set() for edge_id in passenger_ids}
        for from_edge, to_edges in output_connections.items():
            if from_edge not in passenger_ids:
                continue
            for to_edge in to_edges:
                if to_edge in passenger_ids:
                    passenger_graph[from_edge].add(to_edge)
                    passenger_graph[to_edge].add(from_edge)
        passenger_components = _weak_components(passenger_ids, passenger_graph)
        source_hash_after = _sha256_file(source_net_file)
        source_unchanged = source_hash_before == source_hash_after
        strict_reduction = len(output_edges) < selection.source_external_edge_count
        gates = {
            "source_unchanged": source_unchanged,
            "all_selected_edges_preserved": not missing_selected_edges,
            "all_added_directional_siblings_preserved": not (
                set(selection.added_directional_sibling_edge_ids) - set(output_edges)
            ),
            "all_required_lanes_preserved": not missing_required_lanes,
            "one_passenger_component": len(passenger_components) == 1,
            "strict_scope_reduction": strict_reduction,
        }
        status = "pass" if all(gates.values()) else "fail"
        report.update(
            {
                "status": status,
                "claim_status": (
                    "compact-three-intersection-corridor"
                    if status == "pass"
                    else "construction-invalid"
                ),
                "selection": asdict(selection),
                "keep_edges_file": str(keep_edges_file),
                "keep_edges_sha256": keep_edges_sha256,
                "directional_sibling_evidence_sha256": (
                    selection.directional_sibling_evidence_sha256
                ),
                "command_attempts": command_attempts,
                "command_result": result,
                "postload_fallback_used": fallback_used,
                "source_net_sha256_after": source_hash_after,
                "output_net_sha256": _sha256_file(output_net_file),
                "source_unchanged": source_unchanged,
                "output_external_edge_count": len(output_edges),
                "output_passenger_edge_count": len(passenger_ids),
                "output_passenger_component_count": len(passenger_components),
                "scope_reduction_fraction": 1.0
                - (len(output_edges) / selection.source_external_edge_count),
                "missing_selected_edges": missing_selected_edges,
                "missing_required_lanes": missing_required_lanes,
                "gates": gates,
            }
        )
    except (CorridorScopeError, ET.ParseError, OSError, ValueError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["source_net_sha256_after"] = (
            _sha256_file(source_net_file) if source_net_file.is_file() else ""
        )
        report["source_unchanged"] = bool(source_hash_before) and (
            report["source_net_sha256_after"] == source_hash_before
        )
    _write_json(manifest_file, report)
    report["manifest_file"] = str(manifest_file)
    report["manifest_sha256"] = _sha256_file(manifest_file)
    return report


def _validate_scope_parameters(
    centers_lonlat: Sequence[tuple[float, float]],
    corridor_buffer_m: float,
    intersection_stub_radius_m: float,
    max_bridge_length_m: float,
) -> None:
    if len(centers_lonlat) < 2:
        raise CorridorScopeError("an ordered corridor requires at least two junction centers")
    for longitude, latitude in centers_lonlat:
        if not math.isfinite(longitude) or not math.isfinite(latitude):
            raise CorridorScopeError("corridor center coordinates must be finite")
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise CorridorScopeError(f"invalid corridor center: {(longitude, latitude)!r}")
    for name, value in (
        ("corridor_buffer_m", corridor_buffer_m),
        ("intersection_stub_radius_m", intersection_stub_radius_m),
        ("max_bridge_length_m", max_bridge_length_m),
    ):
        if not math.isfinite(value) or value <= 0:
            raise CorridorScopeError(f"{name} must be finite and positive")


def _project_centers(
    root: ET.Element,
    centers_lonlat: Sequence[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    location = root.find("location")
    if location is None:
        raise CorridorScopeError("SUMO network has no <location> projection metadata")
    projection = location.attrib.get("projParameter", "").strip()
    if not projection or projection == "!":
        raise CorridorScopeError("SUMO network does not declare a geographic projection")
    try:
        offset_x, offset_y = (
            float(value) for value in location.attrib.get("netOffset", "").split(",")
        )
        transformer = Transformer.from_crs(
            CRS.from_epsg(4326),
            CRS.from_user_input(projection),
            always_xy=True,
        )
        projected = []
        for longitude, latitude in centers_lonlat:
            x_value, y_value = transformer.transform(longitude, latitude)
            projected.append((x_value + offset_x, y_value + offset_y))
    except (ValueError, TypeError) as exc:
        raise CorridorScopeError(f"invalid SUMO projection metadata: {exc}") from exc
    if any(not math.isfinite(value) for point in projected for value in point):
        raise CorridorScopeError("projected corridor centers are not finite")
    return tuple(projected)


def _read_edge_geometries(
    root: ET.Element,
) -> tuple[dict[str, _EdgeGeometry], dict[str, str]]:
    edges: dict[str, _EdgeGeometry] = {}
    lane_to_edge: dict[str, str] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function"):
            continue
        lanes = edge.findall("lane")
        points: list[tuple[float, float]] = []
        length_m = 0.0
        for lane in lanes:
            lane_id = lane.attrib.get("id", "")
            if lane_id:
                previous = lane_to_edge.setdefault(lane_id, edge_id)
                if previous != edge_id:
                    raise CorridorScopeError(f"lane id {lane_id!r} belongs to multiple edges")
            try:
                length_m = max(length_m, float(lane.attrib.get("length", "0")))
            except ValueError as exc:
                raise CorridorScopeError(f"edge {edge_id!r} has an invalid lane length") from exc
            for token in lane.attrib.get("shape", "").split():
                try:
                    x_text, y_text = token.split(",", 1)
                    point = (float(x_text), float(y_text))
                except ValueError as exc:
                    raise CorridorScopeError(
                        f"edge {edge_id!r} has an invalid lane shape point {token!r}"
                    ) from exc
                if not points or points[-1] != point:
                    points.append(point)
        if not points:
            raise CorridorScopeError(f"external edge {edge_id!r} has no lane geometry")
        oneway_values = {
            parameter.attrib.get("value", "").strip().lower()
            for parameter in edge.findall("param")
            if parameter.attrib.get("key", "").strip().lower() == "oneway"
        }
        if len(oneway_values) > 1:
            raise CorridorScopeError(
                f"external edge {edge_id!r} has conflicting oneway source evidence"
            )
        edges[edge_id] = _EdgeGeometry(
            edge_id=edge_id,
            from_node=edge.attrib.get("from", ""),
            to_node=edge.attrib.get("to", ""),
            points=tuple(points),
            length_m=max(length_m, 0.001),
            allows_passenger=any(lane_allows_passenger(lane) for lane in lanes),
            oneway=next(iter(oneway_values), ""),
        )
    return edges, lane_to_edge


def _close_directional_sibling_parity(
    selected: set[str],
    *,
    graph: Mapping[str, set[str]],
    edges: Mapping[str, _EdgeGeometry],
) -> tuple[set[str], tuple[DirectionalSiblingEvidence, ...]]:
    """Add only source-proven reverse siblings that remain in the retained component.

    SUMO's OSM importer encodes opposite directions as signed edge IDs.  A suffix such as
    ``#2`` denotes a split of the same OSM way, so endpoint reciprocity is also required before
    two signed IDs are treated as siblings.  Candidate siblings must permit passenger traffic,
    carry no affirmative OSM ``oneway`` evidence, and join the already retained passenger
    component.  This last condition prevents an opposite-direction fragment outside the compact
    operational scope from being pulled in merely because its forward span was selected.
    """

    original_selected = set(selected)
    grouped: dict[tuple[str, int], list[str]] = {}
    for edge_id in edges:
        identity = _osm_way_direction(edge_id)
        if identity is None:
            continue
        grouped.setdefault(identity, []).append(edge_id)

    provisional: dict[str, tuple[str, _EdgeGeometry, _EdgeGeometry]] = {}
    excluded: list[tuple[str, _EdgeGeometry, _EdgeGeometry, str]] = []
    claimed_siblings: dict[str, str] = {}
    for selected_edge_id in sorted(original_selected):
        selected_identity = _osm_way_direction(selected_edge_id)
        if selected_identity is None:
            continue
        osm_way_id, direction = selected_identity
        selected_edge = edges[selected_edge_id]
        reciprocal = [
            edges[candidate_id]
            for candidate_id in sorted(grouped.get((osm_way_id, -direction), []))
            if edges[candidate_id].from_node == selected_edge.to_node
            and edges[candidate_id].to_node == selected_edge.from_node
        ]
        if len(reciprocal) > 1:
            candidate_ids = ", ".join(item.edge_id for item in reciprocal)
            raise CorridorScopeError(
                "ambiguous reverse OSM sibling for "
                f"{selected_edge_id!r}: {candidate_ids}"
            )
        if not reciprocal:
            continue
        sibling = reciprocal[0]
        if sibling.edge_id in original_selected:
            continue
        previous_owner = claimed_siblings.setdefault(sibling.edge_id, selected_edge_id)
        if previous_owner != selected_edge_id:
            raise CorridorScopeError(
                f"reverse OSM sibling {sibling.edge_id!r} is claimed by both "
                f"{previous_owner!r} and {selected_edge_id!r}"
            )
        if _is_affirmative_oneway(selected_edge.oneway) or _is_affirmative_oneway(
            sibling.oneway
        ):
            excluded.append((osm_way_id, selected_edge, sibling, "oneway_evidence"))
            continue
        if not selected_edge.allows_passenger or not sibling.allows_passenger:
            excluded.append((osm_way_id, selected_edge, sibling, "passenger_permission"))
            continue
        provisional[sibling.edge_id] = (osm_way_id, selected_edge, sibling)

    augmented = original_selected | set(provisional)
    augmented_components = _weak_components(augmented, graph)
    retained_component = next(
        (
            component
            for component in augmented_components
            if original_selected.issubset(component)
        ),
        set(),
    )
    if not retained_component:
        raise CorridorScopeError(
            "cannot identify the retained passenger component while auditing reverse siblings"
        )

    included: set[str] = set()
    evidence: list[DirectionalSiblingEvidence] = []
    for sibling_id, (osm_way_id, selected_edge, sibling) in sorted(provisional.items()):
        connected = sibling_id in retained_component
        decision = "included" if connected else "excluded"
        reason = (
            "reciprocal_source_span_connected_to_retained_scope"
            if connected
            else "reverse_span_disconnected_from_retained_scope"
        )
        if connected:
            included.add(sibling_id)
        evidence.append(
            _directional_sibling_evidence(
                osm_way_id,
                selected_edge,
                sibling,
                connected_to_retained_scope=connected,
                decision=decision,
                reason=reason,
            )
        )
    for osm_way_id, selected_edge, sibling, exclusion_reason in excluded:
        evidence.append(
            _directional_sibling_evidence(
                osm_way_id,
                selected_edge,
                sibling,
                connected_to_retained_scope=False,
                decision="excluded",
                reason=exclusion_reason,
            )
        )
    evidence.sort(key=lambda item: (item.selected_edge_id, item.sibling_edge_id))
    return included, tuple(evidence)


def _directional_sibling_evidence(
    osm_way_id: str,
    selected_edge: _EdgeGeometry,
    sibling: _EdgeGeometry,
    *,
    connected_to_retained_scope: bool,
    decision: str,
    reason: str,
) -> DirectionalSiblingEvidence:
    return DirectionalSiblingEvidence(
        osm_way_id=osm_way_id,
        selected_edge_id=selected_edge.edge_id,
        sibling_edge_id=sibling.edge_id,
        selected_from_node=selected_edge.from_node,
        selected_to_node=selected_edge.to_node,
        sibling_from_node=sibling.from_node,
        sibling_to_node=sibling.to_node,
        selected_allows_passenger=selected_edge.allows_passenger,
        sibling_allows_passenger=sibling.allows_passenger,
        selected_oneway=selected_edge.oneway,
        sibling_oneway=sibling.oneway,
        reciprocal_endpoints=(
            selected_edge.from_node == sibling.to_node
            and selected_edge.to_node == sibling.from_node
        ),
        connected_to_retained_scope=connected_to_retained_scope,
        decision=decision,
        reason=reason,
    )


def _osm_way_direction(edge_id: str) -> tuple[str, int] | None:
    match = re.fullmatch(r"(-?)([0-9]+)(?:#[0-9]+)?", edge_id)
    if match is None:
        return None
    return match.group(2), -1 if match.group(1) else 1


def _is_affirmative_oneway(value: str) -> bool:
    return value.strip().lower() not in {"", "0", "false", "no"}


def _edge_intersects_scope(
    points: Sequence[tuple[float, float]],
    centers_xy: Sequence[tuple[float, float]],
    *,
    corridor_buffer_m: float,
    intersection_stub_radius_m: float,
) -> bool:
    edge_segments = list(zip(points, points[1:]))
    if not edge_segments:
        edge_segments = [(points[0], points[0])]
    corridor_segments = list(zip(centers_xy, centers_xy[1:]))
    for edge_start, edge_end in edge_segments:
        if any(
            _point_segment_distance(center, edge_start, edge_end)
            <= intersection_stub_radius_m
            for center in centers_xy
        ):
            return True
        if any(
            _segment_distance(edge_start, edge_end, start, end) <= corridor_buffer_m
            for start, end in corridor_segments
        ):
            return True
    return False


def _passenger_edge_graph(
    root: ET.Element,
    passenger_edge_ids: set[str],
) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {edge_id: set() for edge_id in passenger_edge_ids}
    for connection in root.findall("connection"):
        from_edge = connection.attrib.get("from", "")
        to_edge = connection.attrib.get("to", "")
        if from_edge in passenger_edge_ids and to_edge in passenger_edge_ids:
            graph[from_edge].add(to_edge)
            graph[to_edge].add(from_edge)
    return graph


def _weak_components(
    edge_ids: set[str],
    graph: Mapping[str, set[str]],
) -> list[set[str]]:
    remaining = set(edge_ids)
    components: list[set[str]] = []
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        component = {start}
        queue: deque[str] = deque([start])
        while queue:
            current = queue.popleft()
            for neighbor in sorted(graph.get(current, set()) & remaining):
                remaining.remove(neighbor)
                component.add(neighbor)
                queue.append(neighbor)
        components.append(component)
    components.sort(key=lambda item: (-len(item), min(item)))
    return components


def _shortest_component_bridge(
    selected: set[str],
    remaining: Sequence[set[str]],
    graph: Mapping[str, set[str]],
    edges: Mapping[str, _EdgeGeometry],
) -> tuple[list[str], int]:
    target_by_edge = {
        edge_id: index for index, component in enumerate(remaining) for edge_id in component
    }
    distances = {edge_id: 0.0 for edge_id in selected}
    previous: dict[str, str] = {}
    queue = [(0.0, edge_id) for edge_id in selected]
    heapq.heapify(queue)
    target_edge = ""
    target_index = -1
    while queue:
        distance, current = heapq.heappop(queue)
        if distance != distances[current]:
            continue
        if current in target_by_edge:
            target_edge = current
            target_index = target_by_edge[current]
            break
        for neighbor in sorted(graph.get(current, set())):
            next_distance = distance + edges[neighbor].length_m
            if next_distance < distances.get(neighbor, math.inf):
                distances[neighbor] = next_distance
                previous[neighbor] = current
                heapq.heappush(queue, (next_distance, neighbor))
    if not target_edge:
        raise CorridorScopeError("required official corridor components are disconnected in source network")
    path = [target_edge]
    while path[-1] not in selected:
        predecessor = previous.get(path[-1])
        if predecessor is None:
            raise CorridorScopeError("cannot reconstruct corridor component bridge")
        path.append(predecessor)
    path.reverse()
    return path, target_index


def _point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    delta_x = end[0] - start[0]
    delta_y = end[1] - start[1]
    denominator = delta_x * delta_x + delta_y * delta_y
    if denominator == 0:
        return math.dist(point, start)
    ratio = (
        (point[0] - start[0]) * delta_x + (point[1] - start[1]) * delta_y
    ) / denominator
    ratio = max(0.0, min(1.0, ratio))
    projection = (start[0] + ratio * delta_x, start[1] + ratio * delta_y)
    return math.dist(point, projection)


def _segment_distance(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> float:
    if _segments_intersect(first_start, first_end, second_start, second_end):
        return 0.0
    return min(
        _point_segment_distance(first_start, second_start, second_end),
        _point_segment_distance(first_end, second_start, second_end),
        _point_segment_distance(second_start, first_start, first_end),
        _point_segment_distance(second_end, first_start, first_end),
    )


def _segments_intersect(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> bool:
    def orientation(
        first: tuple[float, float],
        second: tuple[float, float],
        third: tuple[float, float],
    ) -> float:
        return (second[0] - first[0]) * (third[1] - first[1]) - (
            second[1] - first[1]
        ) * (third[0] - first[0])

    first = orientation(first_start, first_end, second_start)
    second = orientation(first_start, first_end, second_end)
    third = orientation(second_start, second_end, first_start)
    fourth = orientation(second_start, second_end, first_end)
    tolerance = 1e-9
    if (
        abs(first) <= tolerance
        and _point_segment_distance(second_start, first_start, first_end) <= tolerance
    ):
        return True
    if (
        abs(second) <= tolerance
        and _point_segment_distance(second_end, first_start, first_end) <= tolerance
    ):
        return True
    if (
        abs(third) <= tolerance
        and _point_segment_distance(first_start, second_start, second_end) <= tolerance
    ):
        return True
    if (
        abs(fourth) <= tolerance
        and _point_segment_distance(first_end, second_start, second_end) <= tolerance
    ):
        return True
    return (first > 0) != (second > 0) and (third > 0) != (fourth > 0)


def _result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, Mapping):
        return dict(result)
    if hasattr(result, "to_dict"):
        return dict(result.to_dict())
    if hasattr(result, "model_dump"):
        return dict(result.model_dump(mode="json"))
    raise TypeError(f"unsupported command result: {type(result).__name__}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
