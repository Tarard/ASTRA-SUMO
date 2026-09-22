"""Sensor-aligned replay helpers for the Hamburg corridor."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy import sparse
from scipy.optimize import Bounds, LinearConstraint, lsq_linear, milp

from .detector_demand import Detector, read_net_lanes, safe_id
from .digital_twin_mapping import DetectorMapping


@dataclass(frozen=True)
class TimeResponseColumn:
    route_edges: tuple[str, ...]
    depart: int
    slot_seconds: int
    sensor_bins: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class PathTimeAssignmentEvidence:
    assignment: np.ndarray
    prior_counts: np.ndarray
    route_order: tuple[tuple[str, ...], ...]
    sensor_order: tuple[str, ...]
    bins_per_route: int


def build_path_time_assignment_evidence(
    demand_route_file: Path,
    vehroute_file: Path,
    *,
    sensor_pass_edges: Mapping[str, Sequence[str]],
    target_begin: int,
    target_end: int,
    departure_begin: int,
    departure_end: int,
    bin_seconds: int = 900,
    departure_bin_seconds: int | None = None,
) -> PathTimeAssignmentEvidence:
    """Estimate one joint route-departure to sensor-time assignment matrix."""

    departure_period = bin_seconds if departure_bin_seconds is None else departure_bin_seconds
    if (
        bin_seconds <= 0
        or departure_period <= 0
        or target_begin < 0
        or target_end <= target_begin
        or departure_begin < 0
        or departure_end <= departure_begin
        or (target_end - target_begin) % bin_seconds
        or (departure_end - departure_begin) % departure_period
    ):
        raise ValueError("path-time assignment intervals are invalid")
    sensors = tuple(sensor_pass_edges)
    if not sensors or any(not tuple(edges) for edges in sensor_pass_edges.values()):
        raise ValueError("sensor_pass_edges must contain non-empty edge choices")
    target_bins = (target_end - target_begin) // bin_seconds
    departure_bins = (departure_end - departure_begin) // departure_period
    demand_root = ET.parse(Path(demand_route_file).resolve(strict=True)).getroot()
    scheduled: dict[str, tuple[float, tuple[str, ...]]] = {}
    for vehicle in demand_root.findall("vehicle"):
        route = vehicle.find("route")
        if route is None or not vehicle.attrib.get("id"):
            continue
        scheduled[vehicle.attrib["id"]] = (
            float(vehicle.attrib["depart"]),
            tuple(route.attrib.get("edges", "").split()),
        )
    routes = tuple(sorted({route for _depart, route in scheduled.values()}))
    route_index = {route: index for index, route in enumerate(routes)}
    sensor_index = {sensor: index for index, sensor in enumerate(sensors)}
    prior = np.zeros(len(routes) * departure_bins, dtype=np.int64)
    for depart, route in scheduled.values():
        departure_bin = int((depart - departure_begin) // departure_period)
        if 0 <= departure_bin < departure_bins:
            prior[route_index[route] * departure_bins + departure_bin] += 1

    counts = np.zeros((len(sensors) * target_bins, prior.size), dtype=np.float64)
    for vehicle in ET.parse(Path(vehroute_file).resolve(strict=True)).getroot().findall("vehicle"):
        vehicle_id = vehicle.attrib.get("id", "")
        route = vehicle.find("route")
        if vehicle_id not in scheduled or route is None:
            continue
        scheduled_depart, planned_route = scheduled[vehicle_id]
        edges = tuple(route.attrib.get("edges", "").split())
        if edges != planned_route:
            raise ValueError(f"vehicle {vehicle_id!r} changed its planned route")
        exit_times = [float(value) for value in route.attrib.get("exitTimes", "").split()]
        if len(exit_times) != len(edges):
            raise ValueError(f"vehicle {vehicle_id!r} has invalid exitTimes")
        departure_bin = int((scheduled_depart - departure_begin) // departure_period)
        if not 0 <= departure_bin < departure_bins:
            continue
        column = route_index[planned_route] * departure_bins + departure_bin
        for sensor, pass_edges in sensor_pass_edges.items():
            pass_edge = next((edge for edge in pass_edges if edge in edges), None)
            if pass_edge is None:
                continue
            passage_bin = int((exit_times[edges.index(pass_edge)] - target_begin) // bin_seconds)
            if 0 <= passage_bin < target_bins:
                row = sensor_index[sensor] * target_bins + passage_bin
                counts[row, column] += 1
    assignment = np.zeros_like(counts)
    nonzero = prior > 0
    assignment[:, nonzero] = counts[:, nonzero] / prior[nonzero]
    return PathTimeAssignmentEvidence(
        assignment=assignment,
        prior_counts=prior,
        route_order=routes,
        sensor_order=sensors,
        bins_per_route=departure_bins,
    )


def solve_conserved_path_time_profiles(
    assignment: np.ndarray,
    target: np.ndarray,
    prior: np.ndarray,
    *,
    route_count: int,
    bins_per_route: int,
    prior_weight: float = 0.5,
    conservation_weight: float = 100.0,
    damping: float = 0.2,
) -> np.ndarray:
    """Update route-time counts while preserving every route total."""

    matrix = np.asarray(assignment, dtype=float)
    target_values = np.asarray(target, dtype=float)
    prior_values = np.asarray(prior, dtype=float)
    variable_count = route_count * bins_per_route
    if (
        route_count <= 0
        or bins_per_route <= 0
        or matrix.ndim != 2
        or matrix.shape[1] != variable_count
        or target_values.shape != (matrix.shape[0],)
        or prior_values.shape != (variable_count,)
    ):
        raise ValueError("conserved path-time dimensions are invalid")
    if (
        np.any(~np.isfinite(matrix))
        or np.any(~np.isfinite(target_values))
        or np.any(~np.isfinite(prior_values))
        or np.any(matrix < 0)
        or np.any(target_values < 0)
        or np.any(prior_values < 0)
        or prior_weight < 0
        or conservation_weight <= 0
        or not 0 < damping <= 1
    ):
        raise ValueError("conserved path-time inputs are invalid")
    blocks = [matrix]
    values = [target_values]
    if prior_weight:
        blocks.append(np.sqrt(prior_weight) * np.eye(variable_count))
        values.append(np.sqrt(prior_weight) * prior_values)
    conservation = np.zeros((route_count, variable_count))
    route_totals = np.zeros(route_count)
    for route_index in range(route_count):
        start = route_index * bins_per_route
        stop = start + bins_per_route
        conservation[route_index, start:stop] = conservation_weight
        route_totals[route_index] = prior_values[start:stop].sum()
    blocks.append(conservation)
    values.append(route_totals * conservation_weight)
    proposal = lsq_linear(
        np.vstack(blocks),
        np.concatenate(values),
        bounds=(0, np.inf),
    ).x
    damped = prior_values + damping * (proposal - prior_values)
    result = np.zeros(variable_count, dtype=np.int64)
    for route_index, total_value in enumerate(route_totals):
        start = route_index * bins_per_route
        stop = start + bins_per_route
        route_values = np.maximum(damped[start:stop], 0)
        total = int(round(float(total_value)))
        if total == 0:
            continue
        if route_values.sum() <= 0:
            raise ValueError(f"route {route_index} path-time update lost all demand")
        scaled = route_values * (total / route_values.sum())
        rounded = np.floor(scaled).astype(np.int64)
        remaining = total - int(rounded.sum())
        rounded[np.argsort(-(scaled - rounded))[:remaining]] += 1
        result[start:stop] = rounded
    return result


def solve_bounded_l1_path_time_profiles(
    assignment: np.ndarray,
    target: np.ndarray,
    prior: np.ndarray,
    *,
    route_count: int,
    bins_per_route: int,
    prior_weight: float = 0.05,
    max_bin_change: int = 10,
) -> np.ndarray:
    """Apply a bounded L1 count correction while conserving route totals."""

    matrix = np.asarray(assignment, dtype=float)
    target_values = np.asarray(target, dtype=float)
    prior_values = np.asarray(prior, dtype=float)
    variable_count = route_count * bins_per_route
    if (
        route_count <= 0
        or bins_per_route <= 0
        or matrix.ndim != 2
        or matrix.shape[1] != variable_count
        or target_values.shape != (matrix.shape[0],)
        or prior_values.shape != (variable_count,)
        or prior_weight < 0
        or isinstance(max_bin_change, bool)
        or max_bin_change < 0
        or np.any(~np.isfinite(matrix))
        or np.any(~np.isfinite(target_values))
        or np.any(~np.isfinite(prior_values))
        or np.any(matrix < 0)
        or np.any(target_values < 0)
        or np.any(prior_values < 0)
        or np.any(prior_values != np.rint(prior_values))
    ):
        raise ValueError("bounded L1 path-time inputs are invalid")

    row_count = matrix.shape[0]
    identity_rows = sparse.eye(row_count, format="csr")
    identity_variables = sparse.eye(variable_count, format="csr")
    zero_rows_variables = sparse.csr_matrix((row_count, variable_count))
    zero_variables_rows = sparse.csr_matrix((variable_count, row_count))
    measurement = sparse.hstack(
        [
            sparse.csr_matrix(matrix),
            -identity_rows,
            identity_rows,
            zero_rows_variables,
            zero_rows_variables,
        ],
        format="csr",
    )
    prior_constraint = sparse.hstack(
        [
            identity_variables,
            zero_variables_rows,
            zero_variables_rows,
            -identity_variables,
            identity_variables,
        ],
        format="csr",
    )
    conservation = np.zeros((route_count, variable_count))
    route_totals = np.zeros(route_count)
    for route_index in range(route_count):
        start = route_index * bins_per_route
        stop = start + bins_per_route
        conservation[route_index, start:stop] = 1
        route_totals[route_index] = prior_values[start:stop].sum()
    conservation_constraint = sparse.hstack(
        [
            sparse.csr_matrix(conservation),
            sparse.csr_matrix(
                (route_count, row_count * 2 + variable_count * 2)
            ),
        ],
        format="csr",
    )
    equality = sparse.vstack(
        [measurement, prior_constraint, conservation_constraint],
        format="csr",
    )
    equality_values = np.concatenate([target_values, prior_values, route_totals])

    lower_x = np.maximum(0, prior_values - max_bin_change)
    upper_x = prior_values + max_bin_change
    trailing_count = row_count * 2 + variable_count * 2
    result = milp(
        c=np.concatenate(
            [
                np.zeros(variable_count),
                np.ones(row_count * 2),
                np.full(variable_count * 2, prior_weight),
            ]
        ),
        integrality=np.concatenate(
            [np.ones(variable_count), np.zeros(trailing_count)]
        ),
        bounds=Bounds(
            np.concatenate([lower_x, np.zeros(trailing_count)]),
            np.concatenate([upper_x, np.full(trailing_count, np.inf)]),
        ),
        constraints=LinearConstraint(equality, equality_values, equality_values),
        options={"time_limit": 300.0},
    )
    if not result.success or result.x is None:
        raise RuntimeError(f"bounded L1 path-time correction failed: {result.message}")
    profiles = np.rint(result.x[:variable_count]).astype(np.int64)
    for route_index, expected_total in enumerate(route_totals):
        start = route_index * bins_per_route
        stop = start + bins_per_route
        if int(profiles[start:stop].sum()) != int(round(expected_total)):
            raise RuntimeError("bounded L1 correction violated route conservation")
    return profiles


def write_path_time_profiles(
    source_route_file: Path,
    output_route_file: Path,
    *,
    route_order: Sequence[tuple[str, ...]],
    profiles: np.ndarray,
    departure_begin: int,
    bin_seconds: int = 900,
    depart_lane: str | None = "free",
) -> int:
    """Retain routes and totals while replacing their departure-bin profiles."""

    routes = tuple(tuple(route) for route in route_order)
    values = np.asarray(profiles)
    if (
        not routes
        or values.ndim != 1
        or values.size % len(routes)
        or np.any(values < 0)
        or np.any(values != np.rint(values))
        or departure_begin < 0
        or bin_seconds <= 0
        or (depart_lane is not None and not depart_lane.strip())
    ):
        raise ValueError("path-time output profile is invalid")
    bins = values.size // len(routes)
    tree = ET.parse(Path(source_route_file).resolve(strict=True))
    root = tree.getroot()
    by_route: dict[tuple[str, ...], list[ET.Element]] = {route: [] for route in routes}
    for vehicle in root.findall("vehicle"):
        route = vehicle.find("route")
        if route is None:
            raise ValueError(f"vehicle {vehicle.attrib.get('id')!r} has no inline route")
        edges = tuple(route.attrib.get("edges", "").split())
        if edges not in by_route:
            raise ValueError(f"demand contains an undeclared route: {edges}")
        by_route[edges].append(vehicle)
    for route_index, route in enumerate(routes):
        vehicles = sorted(
            by_route[route],
            key=lambda vehicle: (float(vehicle.attrib.get("depart", "0")), vehicle.attrib.get("id", "")),
        )
        route_profile = values[route_index * bins : (route_index + 1) * bins].astype(int)
        if int(route_profile.sum()) != len(vehicles):
            raise ValueError(f"path-time profile changes the total for route {route_index}")
        offsets = [float(vehicle.attrib.get("depart", "0")) % bin_seconds for vehicle in vehicles]
        cursor = 0
        for bin_index, count in enumerate(route_profile):
            for _within_bin in range(int(count)):
                depart = departure_begin + bin_index * bin_seconds + offsets[cursor]
                vehicle = vehicles[cursor]
                vehicle.set("depart", f"{depart:.2f}")
                if depart_lane is not None:
                    vehicle.set("departLane", depart_lane)
                cursor += 1
    ordered = sorted(
        root.findall("vehicle"),
        key=lambda vehicle: (float(vehicle.attrib["depart"]), vehicle.attrib.get("id", "")),
    )
    for vehicle in list(root.findall("vehicle")):
        root.remove(vehicle)
    root.extend(ordered)
    destination = Path(output_route_file).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="    ")
    tree.write(destination, encoding="utf-8", xml_declaration=True)
    return len(ordered)


def solve_vehicle_shift_selection(
    residual: np.ndarray,
    effects: np.ndarray,
    *,
    vehicle_ids: Sequence[str],
    shift_costs: np.ndarray,
    max_changes: int,
) -> np.ndarray:
    """Select bounded vehicle shifts that minimize the linearized E1 L1 residual."""

    residual_values = np.asarray(residual, dtype=float)
    effect_matrix = np.asarray(effects, dtype=float)
    costs = np.asarray(shift_costs, dtype=float)
    candidate_count = effect_matrix.shape[1] if effect_matrix.ndim == 2 else -1
    if (
        residual_values.ndim != 1
        or effect_matrix.shape != (residual_values.size, candidate_count)
        or candidate_count < 0
        or len(vehicle_ids) != candidate_count
        or costs.shape != (candidate_count,)
        or np.any(~np.isfinite(residual_values))
        or np.any(~np.isfinite(effect_matrix))
        or np.any(~np.isfinite(costs))
        or np.any(costs < 0)
        or max_changes <= 0
    ):
        raise ValueError("vehicle shift selection inputs are invalid")
    if candidate_count == 0:
        return np.asarray([], dtype=np.int64)

    row_count = residual_values.size
    variable_count = candidate_count + row_count * 2
    effect_sparse = sparse.csr_matrix(effect_matrix)
    identity = sparse.eye(row_count, format="csr")
    equality = sparse.hstack(
        [effect_sparse, -identity, identity],
        format="csr",
    )
    equality_constraint = LinearConstraint(equality, -residual_values, -residual_values)

    group_index: dict[str, int] = {}
    group_rows = []
    group_columns = []
    for column, vehicle_id in enumerate(vehicle_ids):
        row = group_index.setdefault(str(vehicle_id), len(group_index))
        group_rows.append(row)
        group_columns.append(column)
    group_matrix = sparse.coo_matrix(
        (np.ones(candidate_count), (group_rows, group_columns)),
        shape=(len(group_index), variable_count),
    ).tocsr()
    group_constraint = LinearConstraint(
        group_matrix,
        np.full(len(group_index), -np.inf),
        np.ones(len(group_index)),
    )
    total_matrix = sparse.csr_matrix(
        (np.ones(candidate_count), (np.zeros(candidate_count), np.arange(candidate_count))),
        shape=(1, variable_count),
    )
    total_constraint = LinearConstraint(total_matrix, -np.inf, float(max_changes))

    scale = max(float(costs.max()), 1.0)
    objective = np.concatenate(
        [1e-4 * costs / scale, np.ones(row_count * 2)]
    )
    upper = np.concatenate(
        [np.ones(candidate_count), np.full(row_count * 2, np.inf)]
    )
    integrality = np.concatenate(
        [np.ones(candidate_count), np.zeros(row_count * 2)]
    )
    result = milp(
        c=objective,
        integrality=integrality,
        bounds=Bounds(np.zeros(variable_count), upper),
        constraints=[equality_constraint, group_constraint, total_constraint],
        options={"time_limit": 300.0},
    )
    if not result.success or result.x is None:
        raise RuntimeError(f"vehicle shift selection failed: {result.message}")
    return np.flatnonzero(result.x[:candidate_count] > 0.5).astype(np.int64)


def build_time_expanded_response(
    route_sensor_delays: Mapping[tuple[str, ...], Mapping[str, float]],
    sensor_order: Sequence[str],
    *,
    target_begin: int,
    target_end: int,
    bin_seconds: int = 900,
    departure_step_seconds: int = 60,
) -> tuple[np.ndarray, list[TimeResponseColumn]]:
    """Build route-time columns whose entries are simulated sensor contributions."""

    if (
        target_begin < 0
        or target_end <= target_begin
        or bin_seconds <= 0
        or departure_step_seconds <= 0
        or (target_end - target_begin) % bin_seconds
    ):
        raise ValueError("time-expanded response interval is invalid")
    sensors = tuple(sensor_order)
    if not sensors or len(set(sensors)) != len(sensors):
        raise ValueError("sensor_order must be non-empty and unique")
    bins = (target_end - target_begin) // bin_seconds
    sensor_index = {sensor: index for index, sensor in enumerate(sensors)}
    vectors: list[np.ndarray] = []
    columns: list[TimeResponseColumn] = []
    for route_edges, delays in sorted(route_sensor_delays.items()):
        if not route_edges:
            continue
        unknown = set(delays).difference(sensor_index)
        if unknown:
            raise ValueError(f"route response contains unknown sensors: {sorted(unknown)}")
        for depart in range(0, target_end, departure_step_seconds):
            vector = np.zeros(len(sensors) * bins, dtype=np.int64)
            hits: list[tuple[str, int]] = []
            for sensor, delay in delays.items():
                arrival = depart + float(delay)
                arrival_bin = int((arrival - target_begin) // bin_seconds)
                if 0 <= arrival_bin < bins:
                    vector[sensor_index[sensor] * bins + arrival_bin] += 1
                    hits.append((sensor, arrival_bin))
            if not hits:
                continue
            vectors.append(vector)
            columns.append(
                TimeResponseColumn(
                    route_edges=tuple(route_edges),
                    depart=depart,
                    slot_seconds=departure_step_seconds,
                    sensor_bins=tuple(sorted(hits)),
                )
            )
    if not columns:
        raise ValueError("time-expanded response contains no observable columns")
    return np.column_stack(vectors), columns


def build_station_detector_bank(
    net_file: Path,
    station_groups: Sequence[Mapping[str, Any]],
    field_mappings: Sequence[DetectorMapping],
) -> dict[str, object]:
    """Build station validation detectors from physical count-field projections."""

    net_path = Path(net_file).resolve(strict=True)
    lanes = read_net_lanes(net_path)
    mappings = {int(row.stream_id): row for row in field_mappings}
    detectors: list[Detector] = []
    reports: list[dict[str, object]] = []
    used_detector_ids: set[str] = set()
    for raw_group in station_groups:
        group = dict(raw_group)
        station_id = int(group["station_stream_id"])
        member_ids = [int(value) for value in group.get("member_stream_ids", [])]
        missing = [stream_id for stream_id in member_ids if stream_id not in mappings]
        if missing:
            raise ValueError(f"station {station_id} is missing field mappings: {missing}")
        physical = [mappings[stream_id] for stream_id in member_ids]
        by_lane: dict[str, list[DetectorMapping]] = {}
        for row in physical:
            lane = lanes.get(row.sumo_lane)
            if lane is None or lane.edge_id != row.sumo_edge:
                raise ValueError(f"station {station_id} field {row.stream_id} has an invalid SUMO lane")
            by_lane.setdefault(row.sumo_lane, []).append(row)

        group_detectors: list[Detector] = []
        detector_sources: list[dict[str, object]] = []
        for lane_id, source_rows in sorted(by_lane.items()):
            source_rows.sort(key=lambda row: (row.stream_id, row.detector_id))
            detector_id = (
                source_rows[0].detector_id
                if len(source_rows) == 1
                else f"station_{station_id}_{safe_id(lane_id)}"
            )
            position = max(float(row.lane_position) for row in source_rows)
            group_detectors.append(
                Detector(
                    detector_id=detector_id,
                    source_system="hamburg_official_count_field_projection",
                    direction=str(group.get("direction", "")),
                    edge_id=source_rows[0].sumo_edge,
                    lane_id=lane_id,
                    lane_position=position,
                    period=str(source_rows[0].period),
                    mapping_confidence=(
                        source_rows[0].mapping_confidence if len(source_rows) == 1 else "derived"
                    ),
                    mapping_status="active",
                )
            )
            detector_sources.append(
                {
                    "detector_id": detector_id,
                    "sumo_lane": lane_id,
                    "lane_position": position,
                    "source_detector_ids": [row.detector_id for row in source_rows],
                    "source_stream_ids": [row.stream_id for row in source_rows],
                }
            )
        exact_location = all(
            row.mapping_status == "active" and row.mapping_confidence == "high" for row in physical
        ) and all(len(source_rows) == 1 for source_rows in by_lane.values())

        detector_ids = sorted(detector.detector_id for detector in group_detectors)
        duplicates = used_detector_ids.intersection(detector_ids)
        if duplicates:
            raise ValueError(f"station detector bank repeats ids: {sorted(duplicates)}")
        used_detector_ids.update(detector_ids)
        detectors.extend(group_detectors)
        reports.append(
            {
                "station_stream_id": station_id,
                "node_id": str(group.get("node_id", "")),
                "direction": str(group.get("direction", "")),
                "detector_ids": detector_ids,
                "detector_sources": detector_sources,
                "placement_basis": "physical_field_projections",
                "exact_physical_field_positions": exact_location,
                "constraint_edge": str(group.get("constraint_edge", "")),
            }
        )

    return {
        "detectors": tuple(sorted(detectors, key=lambda row: row.detector_id)),
        "station_groups": sorted(reports, key=lambda row: int(row["station_stream_id"])),
    }


__all__ = [
    "PathTimeAssignmentEvidence",
    "TimeResponseColumn",
    "build_path_time_assignment_evidence",
    "build_station_detector_bank",
    "build_time_expanded_response",
    "solve_bounded_l1_path_time_profiles",
    "solve_conserved_path_time_profiles",
    "solve_vehicle_shift_selection",
    "write_path_time_profiles",
]
