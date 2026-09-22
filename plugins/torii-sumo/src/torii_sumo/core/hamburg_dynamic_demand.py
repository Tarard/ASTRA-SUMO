"""Classic detector-to-departure calibration for the Hamburg corridor."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import lsq_linear


@dataclass(frozen=True)
class DynamicAssignmentEvidence:
    assignment: np.ndarray
    departure_counts: np.ndarray
    arrival_counts: np.ndarray
    selected_vehicle_count: int
    after_window_count: int


@dataclass(frozen=True)
class JointDynamicAssignmentEvidence:
    assignment: np.ndarray
    departure_counts: np.ndarray
    arrival_counts: np.ndarray
    measured_edges: tuple[str, ...]
    after_window_counts: np.ndarray


def build_dynamic_assignment_evidence(
    demand_route_file: Path,
    vehroute_file: Path,
    *,
    measured_edge: str,
    begin: int,
    end: int,
    bin_seconds: int = 900,
) -> DynamicAssignmentEvidence:
    """Estimate departure-bin to detector-arrival-bin proportions from SUMO exit times."""
    if begin < 0 or end <= begin or bin_seconds <= 0 or (end - begin) % bin_seconds:
        raise ValueError("dynamic assignment interval is invalid")
    bins = (end - begin) // bin_seconds
    demand = ET.parse(Path(demand_route_file).resolve(strict=True)).getroot()
    scheduled = {
        vehicle.get("id", ""): float(vehicle.get("depart", "0"))
        for vehicle in demand.findall("vehicle")
        if vehicle.get("id")
    }
    counts = np.zeros((bins, bins), dtype=float)
    departures = np.zeros(bins, dtype=float)
    arrivals = np.zeros(bins, dtype=float)
    selected = 0
    after_window = 0
    for vehicle in ET.parse(Path(vehroute_file).resolve(strict=True)).getroot().findall("vehicle"):
        vehicle_id = vehicle.get("id", "")
        route = vehicle.find("route")
        if vehicle_id not in scheduled or route is None:
            continue
        edges = route.get("edges", "").split()
        if measured_edge not in edges:
            continue
        exit_times = [float(value) for value in route.get("exitTimes", "").split()]
        if len(exit_times) != len(edges):
            raise ValueError(f"vehicle {vehicle_id!r} has invalid edge exit times")
        departure_bin = int((scheduled[vehicle_id] - begin) // bin_seconds)
        if not 0 <= departure_bin < bins:
            continue
        selected += 1
        departures[departure_bin] += 1
        arrival_time = exit_times[edges.index(measured_edge)]
        arrival_bin = int((arrival_time - begin) // bin_seconds)
        if 0 <= arrival_bin < bins:
            counts[arrival_bin, departure_bin] += 1
            arrivals[arrival_bin] += 1
        elif arrival_time >= end:
            after_window += 1
    assignment = np.zeros_like(counts)
    for column, total in enumerate(departures):
        if total > 0:
            assignment[:, column] = counts[:, column] / total
    return DynamicAssignmentEvidence(
        assignment=assignment,
        departure_counts=departures,
        arrival_counts=arrivals,
        selected_vehicle_count=selected,
        after_window_count=after_window,
    )


def build_joint_dynamic_assignment_evidence(
    demand_route_file: Path,
    vehroute_file: Path,
    *,
    measured_edges: list[str],
    begin: int,
    end: int,
    bin_seconds: int = 900,
) -> JointDynamicAssignmentEvidence:
    """Estimate a block assignment matrix for singleton measured-edge route classes."""
    if not measured_edges or len(set(measured_edges)) != len(measured_edges):
        raise ValueError("joint measured edges must be non-empty and unique")
    bins = (end - begin) // bin_seconds
    if begin < 0 or end <= begin or bins <= 0 or (end - begin) % bin_seconds:
        raise ValueError("joint dynamic assignment interval is invalid")
    edge_order = tuple(measured_edges)
    edge_index = {edge_id: index for index, edge_id in enumerate(edge_order)}
    size = len(edge_order) * bins
    scheduled = {
        vehicle.get("id", ""): float(vehicle.get("depart", "0"))
        for vehicle in ET.parse(Path(demand_route_file).resolve(strict=True)).getroot().findall("vehicle")
        if vehicle.get("id")
    }
    counts = np.zeros((size, size), dtype=float)
    departures = np.zeros(size, dtype=float)
    arrivals = np.zeros(size, dtype=float)
    after = np.zeros(len(edge_order), dtype=int)
    for vehicle in ET.parse(Path(vehroute_file).resolve(strict=True)).getroot().findall("vehicle"):
        vehicle_id = vehicle.get("id", "")
        route = vehicle.find("route")
        if vehicle_id not in scheduled or route is None:
            continue
        edges = route.get("edges", "").split()
        signature = [edge_id for edge_id in edge_order if edge_id in edges]
        if len(signature) != 1:
            raise ValueError(f"vehicle {vehicle_id!r} has joint measured-edge signature {signature}")
        group = edge_index[signature[0]]
        departure_bin = int((scheduled[vehicle_id] - begin) // bin_seconds)
        if not 0 <= departure_bin < bins:
            continue
        column = group * bins + departure_bin
        departures[column] += 1
        exit_times = [float(value) for value in route.get("exitTimes", "").split()]
        if len(exit_times) != len(edges):
            raise ValueError(f"vehicle {vehicle_id!r} has invalid edge exit times")
        arrival_time = exit_times[edges.index(signature[0])]
        arrival_bin = int((arrival_time - begin) // bin_seconds)
        if 0 <= arrival_bin < bins:
            row = group * bins + arrival_bin
            counts[row, column] += 1
            arrivals[row] += 1
        elif arrival_time >= end:
            after[group] += 1
    assignment = np.zeros_like(counts)
    for column, total in enumerate(departures):
        if total > 0:
            assignment[:, column] = counts[:, column] / total
    return JointDynamicAssignmentEvidence(
        assignment=assignment,
        departure_counts=departures,
        arrival_counts=arrivals,
        measured_edges=edge_order,
        after_window_counts=after,
    )


def solve_regularized_departure_profile(
    assignment: np.ndarray,
    target: np.ndarray,
    prior: np.ndarray,
    *,
    prior_weight: float = 0.2,
    smoothness_weight: float = 0.05,
) -> np.ndarray:
    """Solve a nonnegative dynamic OD update with prior and temporal smoothness."""
    matrix = np.asarray(assignment, dtype=float)
    target_values = np.asarray(target, dtype=float)
    prior_values = np.asarray(prior, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] != target_values.size or matrix.shape[1] != prior_values.size:
        raise ValueError("dynamic assignment dimensions do not match")
    if min(prior_weight, smoothness_weight) < 0 or np.any(target_values < 0) or np.any(prior_values < 0):
        raise ValueError("dynamic calibration weights and counts must be nonnegative")
    blocks = [matrix]
    values = [target_values]
    if prior_weight:
        blocks.append(np.sqrt(prior_weight) * np.eye(prior_values.size))
        values.append(np.sqrt(prior_weight) * prior_values)
    if smoothness_weight and prior_values.size > 1:
        difference = np.zeros((prior_values.size - 1, prior_values.size))
        for index in range(prior_values.size - 1):
            difference[index, index : index + 2] = (-1.0, 1.0)
        blocks.append(np.sqrt(smoothness_weight) * difference)
        values.append(np.zeros(prior_values.size - 1))
    total = int(round(float(target_values.sum())))
    blocks.append(np.ones((1, prior_values.size)) * 10.0)
    values.append(np.array([total * 10.0]))
    solution = lsq_linear(np.vstack(blocks), np.concatenate(values), bounds=(0, np.inf)).x
    if solution.sum() <= 0:
        raise ValueError("dynamic departure calibration produced no demand")
    scaled = solution * (total / solution.sum())
    rounded = np.floor(scaled).astype(int)
    remainder = total - int(rounded.sum())
    order = np.argsort(-(scaled - rounded))
    rounded[order[:remainder]] += 1
    return rounded


def solve_joint_regularized_profiles(
    assignment: np.ndarray,
    target: np.ndarray,
    prior: np.ndarray,
    *,
    group_count: int,
    bins_per_group: int,
    prior_weight: float = 0.5,
    smoothness_weight: float = 0.1,
) -> np.ndarray:
    """Solve joint detector-group profiles with one conserved total per group."""
    matrix = np.asarray(assignment, dtype=float)
    target_values = np.asarray(target, dtype=float)
    prior_values = np.asarray(prior, dtype=float)
    size = group_count * bins_per_group
    if group_count <= 0 or bins_per_group <= 0 or matrix.shape != (size, size):
        raise ValueError("joint dynamic assignment dimensions are invalid")
    if target_values.size != size or prior_values.size != size:
        raise ValueError("joint target and prior dimensions are invalid")
    blocks = [matrix]
    values = [target_values]
    if prior_weight:
        blocks.append(np.sqrt(prior_weight) * np.eye(size))
        values.append(np.sqrt(prior_weight) * prior_values)
    if smoothness_weight and bins_per_group > 1:
        difference = np.zeros((group_count * (bins_per_group - 1), size))
        row = 0
        for group in range(group_count):
            start = group * bins_per_group
            for index in range(bins_per_group - 1):
                difference[row, start + index : start + index + 2] = (-1.0, 1.0)
                row += 1
        blocks.append(np.sqrt(smoothness_weight) * difference)
        values.append(np.zeros(difference.shape[0]))
    totals = np.array(
        [target_values[group * bins_per_group : (group + 1) * bins_per_group].sum() for group in range(group_count)]
    )
    conservation = np.zeros((group_count, size))
    for group in range(group_count):
        conservation[group, group * bins_per_group : (group + 1) * bins_per_group] = 10.0
    blocks.append(conservation)
    values.append(totals * 10.0)
    solution = lsq_linear(np.vstack(blocks), np.concatenate(values), bounds=(0, np.inf)).x
    rounded = np.zeros(size, dtype=int)
    for group, total_value in enumerate(totals):
        start = group * bins_per_group
        stop = start + bins_per_group
        group_values = solution[start:stop]
        total = int(round(float(total_value)))
        if group_values.sum() <= 0:
            raise ValueError(f"joint calibration group {group} produced no demand")
        scaled = group_values * (total / group_values.sum())
        integers = np.floor(scaled).astype(int)
        remainder = total - int(integers.sum())
        integers[np.argsort(-(scaled - integers))[:remainder]] += 1
        rounded[start:stop] = integers
    return rounded


def write_prerolled_demand(
    source_route_file: Path,
    output_route_file: Path,
    *,
    pre_roll_counts: dict[str, int],
    shift_seconds: int,
) -> int:
    """Shift frozen demand and prepend one observed interval by constraint signature."""
    if shift_seconds <= 0 or any(value < 0 for value in pre_roll_counts.values()):
        raise ValueError("pre-roll shift and counts must be nonnegative")
    tree = ET.parse(Path(source_route_file).resolve(strict=True))
    root = tree.getroot()
    measured_edges = set(pre_roll_counts)
    templates: dict[str, list[tuple[str, ...]]] = {edge_id: [] for edge_id in measured_edges}
    vehicles = list(root.findall("vehicle"))
    for vehicle in vehicles:
        route = vehicle.find("route")
        if route is None:
            raise ValueError(f"vehicle {vehicle.get('id')!r} has no inline route")
        edges = tuple(route.get("edges", "").split())
        signature = measured_edges.intersection(edges)
        if len(signature) == 1:
            templates[next(iter(signature))].append(edges)
        vehicle.set("depart", f"{float(vehicle.get('depart', '0')) + shift_seconds:.2f}")
    for edge_id, count in sorted(pre_roll_counts.items()):
        options = list(dict.fromkeys(templates[edge_id]))
        if count and not options:
            raise ValueError(f"no route template crosses pre-roll edge {edge_id}")
        safe_edge = edge_id.replace("#", "_").replace("-", "m")
        for index in range(count):
            vehicle = ET.Element(
                "vehicle",
                id=f"preroll_{safe_edge}_{index}",
                depart=f"{(index + 0.5) * shift_seconds / count:.2f}",
            )
            ET.SubElement(vehicle, "route", edges=" ".join(options[index % len(options)]))
            root.append(vehicle)
    ordered = sorted(root.findall("vehicle"), key=lambda row: (float(row.get("depart", "0")), row.get("id", "")))
    for vehicle in list(root.findall("vehicle")):
        root.remove(vehicle)
    root.extend(ordered)
    ET.indent(tree, space="    ")
    output = Path(output_route_file).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return len(ordered)


def write_calibrated_departure_profile(
    source_route_file: Path,
    output_route_file: Path,
    *,
    measured_edge: str,
    departure_counts: list[int],
    begin: int = 0,
    bin_seconds: int = 900,
) -> int:
    """Re-time one measured route population without changing routes or total demand."""
    if begin < 0 or bin_seconds <= 0 or any(value < 0 for value in departure_counts):
        raise ValueError("calibrated departure profile is invalid")
    tree = ET.parse(Path(source_route_file).resolve(strict=True))
    root = tree.getroot()
    selected = []
    for vehicle in root.findall("vehicle"):
        route = vehicle.find("route")
        if route is not None and measured_edge in route.get("edges", "").split():
            selected.append(vehicle)
    if len(selected) != sum(departure_counts):
        raise ValueError(
            f"calibrated profile total {sum(departure_counts)} does not match "
            f"the {len(selected)} measured vehicles"
        )
    selected.sort(key=lambda row: (float(row.get("depart", "0")), row.get("id", "")))
    cursor = 0
    for bin_index, count in enumerate(departure_counts):
        for within_bin in range(count):
            depart = begin + bin_index * bin_seconds + (within_bin + 0.5) * bin_seconds / count
            selected[cursor].set("depart", f"{depart:.2f}")
            cursor += 1
    ordered = sorted(root.findall("vehicle"), key=lambda row: (float(row.get("depart", "0")), row.get("id", "")))
    for vehicle in list(root.findall("vehicle")):
        root.remove(vehicle)
    root.extend(ordered)
    ET.indent(tree, space="    ")
    output = Path(output_route_file).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return len(selected)


def write_joint_calibrated_departure_profiles(
    source_route_file: Path,
    output_route_file: Path,
    *,
    measured_edges: list[str],
    departure_profiles: dict[str, list[int]],
    begin: int = 0,
    bin_seconds: int = 900,
) -> int:
    """Re-time all singleton measured-edge route classes in one frozen demand file."""
    if set(measured_edges) != set(departure_profiles):
        raise ValueError("joint departure profiles do not match measured edges")
    tree = ET.parse(Path(source_route_file).resolve(strict=True))
    root = tree.getroot()
    selected: dict[str, list[ET.Element]] = {edge_id: [] for edge_id in measured_edges}
    measured = set(measured_edges)
    for vehicle in root.findall("vehicle"):
        route = vehicle.find("route")
        if route is None:
            continue
        signature = measured.intersection(route.get("edges", "").split())
        if len(signature) != 1:
            raise ValueError(f"vehicle {vehicle.get('id')!r} has joint signature {sorted(signature)}")
        selected[next(iter(signature))].append(vehicle)
    for edge_id in measured_edges:
        vehicles = sorted(selected[edge_id], key=lambda row: (float(row.get("depart", "0")), row.get("id", "")))
        profile = departure_profiles[edge_id]
        if len(vehicles) != sum(profile):
            raise ValueError(f"joint profile for {edge_id} does not preserve its vehicle total")
        cursor = 0
        for bin_index, count in enumerate(profile):
            for within_bin in range(count):
                depart = begin + bin_index * bin_seconds + (within_bin + 0.5) * bin_seconds / count
                vehicles[cursor].set("depart", f"{depart:.2f}")
                cursor += 1
    ordered = sorted(root.findall("vehicle"), key=lambda row: (float(row.get("depart", "0")), row.get("id", "")))
    for vehicle in list(root.findall("vehicle")):
        root.remove(vehicle)
    root.extend(ordered)
    ET.indent(tree, space="    ")
    output = Path(output_route_file).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return len(ordered)


__all__ = [
    "DynamicAssignmentEvidence",
    "JointDynamicAssignmentEvidence",
    "build_dynamic_assignment_evidence",
    "build_joint_dynamic_assignment_evidence",
    "solve_regularized_departure_profile",
    "solve_joint_regularized_profiles",
    "write_prerolled_demand",
    "write_calibrated_departure_profile",
    "write_joint_calibrated_departure_profiles",
]
