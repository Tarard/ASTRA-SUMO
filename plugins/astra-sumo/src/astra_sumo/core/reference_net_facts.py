"""Reference facts from a clean SUMO network: model truth by construction for known-answer Build tests.

Every fact uses the network's own object ids, so correspondence is confirmed by identity. The facts describe the
reference network, not the field: scoring a candidate against them measures how far an edit moved the network from
that reference, never field correctness.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import math
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from shapely.geometry import Polygon

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .connection_mode_audit import _MOTORIZED_MODES
from .network_calibration import NetworkFactTable, _network_root, _permissions, _RootIndex

FACT_KINDS = ("stop_line", "lane_count", "lane_permissions", "movement_inventory", "signal_group",
              "lane_centerline", "lane_width", "speed", "junction_surface")
SOURCE_ID = "reference-net"


@dataclass(frozen=True)
class ReferenceFactParameters:
    """Acceptance thresholds written into every fact; nothing is inferred from the network."""

    stop_line_tolerance_longitudinal_m: float = 0.5
    centerline_tolerance_m: float = 0.5
    centerline_sample_spacing_m: float = 1.0
    centerline_minimum_coverage: float = 0.95
    lane_width_tolerance_m: float = 0.1
    speed_tolerance_m_s: float = 0.1
    junction_minimum_iou: float = 0.9
    lane_count_vehicle_class: str = "passenger"
    kinds: tuple[str, ...] = FACT_KINDS
    edge_ids: tuple[str, ...] | None = None
    junction_ids: tuple[str, ...] | None = None

    def __post_init__(self):
        for name in ("stop_line_tolerance_longitudinal_m", "centerline_tolerance_m", "lane_width_tolerance_m",
                     "speed_tolerance_m_s"):
            value = getattr(self, name)
            # The 0.01 m and 0.01 m/s representation step is the smallest meaningful tolerance.
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0.01:
                raise ValueError(f"{name} must be a finite tolerance of at least 0.01.")
        if not 0 < self.centerline_sample_spacing_m <= 10:
            raise ValueError("centerline_sample_spacing_m must lie in (0, 10] metres.")
        if not 0 < self.centerline_minimum_coverage <= 1:
            raise ValueError("centerline_minimum_coverage must lie in (0, 1].")
        if not 0 < self.junction_minimum_iou < 1:
            raise ValueError("junction_minimum_iou must lie strictly between 0 and 1.")
        if set(self.kinds) - set(FACT_KINDS) or len(set(self.kinds)) != len(self.kinds) or not self.kinds:
            raise ValueError(f"kinds must be distinct values from {FACT_KINDS}.")


def _shape(element: ET.Element) -> list[list[float]]:
    points: list[list[float]] = []
    for token in element.get("shape", "").split():
        point = [float(value) for value in token.split(",")[:2]]
        if not all(math.isfinite(value) for value in point):
            raise ValueError("Shapes must be finite.")
        if not points or points[-1] != point:
            points.append(point)
    return points


def _traffic_mode(classes: set[str]) -> str:
    """The producer idiom of ``prepared_source_facts``: motor when any motorized class is allowed, bicycle only
    without pedestrians, then pedestrian, else unspecified."""
    if classes & _MOTORIZED_MODES:
        return "motor"
    if "bicycle" in classes and "pedestrian" not in classes:
        return "bicycle"
    return "pedestrian" if "pedestrian" in classes else "unspecified"


def build_reference_net_facts(net_file: str | Path, *, site_id: str, target_date: str | date,
                              parameters: ReferenceFactParameters | None = None,
                              license_name: str = "unspecified",
                              source_path: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(fact_table, summary)``; the table validates as ``astra.network-facts/v1``.

    ``source_path`` labels the reference net in the table's source row (default: its resolved path); a caller that
    hashes the table passes a location-free label, such as a repository-relative path, so equal bytes give equal
    tables wherever they lie.
    """
    parameters = parameters or ReferenceFactParameters()
    network = Path(net_file).resolve(strict=True)
    raw = network.read_bytes()
    root = _network_root(raw)
    # The hash binds the facts to exactly the bytes that were parsed.
    digest = hashlib.sha256(raw).hexdigest()
    version = root.get("version", "0.0")
    location = root.find("location")
    road_edges = [edge for edge in root.findall("edge") if edge.get("function") in (None, "normal")
                  and (parameters.edge_ids is None or edge.get("id") in parameters.edge_ids)]
    road_lanes = [lane for edge in road_edges for lane in edge.findall("lane")]
    # The scorer's own connection-pair index, so movement and signal facts are built the way they are scored.
    connections = _RootIndex(root).connections()
    base = {"source_ids": [SOURCE_ID], "mapping_status": "confirmed", "mapping_confidence": "confirmed",
            "mapping_method": "same_id_reference_net",
            "mapping_diagnostics": {"producer": "core.reference_net_facts", "reference_net_sha256": digest}}
    facts: dict[str, list[dict[str, Any]]] = {kind: [] for kind in parameters.kinds}
    skipped: dict[str, int] = {}

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    def add(kind, identity, selector, expected, **extra):
        if kind in facts:
            facts[kind].append({"id": identity, "kind": kind, "selector": selector, "expected": expected,
                                **base, **extra})

    for lane in road_lanes:
        lane_id = lane.get("id")
        try:
            classes = _permissions(lane, network_version=version)
        except ValueError:
            skip("lane_permissions_unknown_class")
            continue
        mode = _traffic_mode(classes)
        shape = _shape(lane)
        if len(shape) >= 2:
            add("stop_line", f"lane:{lane_id}:stop_line", {"lane_id": lane_id}, shape[-1], traffic_mode=mode,
                tolerance_longitudinal_m=parameters.stop_line_tolerance_longitudinal_m)
            add("lane_centerline", f"lane:{lane_id}:centerline", {"lane_id": lane_id}, shape, traffic_mode=mode,
                tolerance_m=parameters.centerline_tolerance_m,
                sample_spacing_m=parameters.centerline_sample_spacing_m,
                minimum_coverage=parameters.centerline_minimum_coverage)
        else:
            skip("lane_shape_degenerate")
        add("lane_permissions", f"lane:{lane_id}:permissions", {"lane_id": lane_id}, sorted(classes), traffic_mode=mode)
        add("lane_width", f"lane:{lane_id}:width", {"lane_id": lane_id}, float(lane.get("width", "3.2")),
            traffic_mode=mode, tolerance_m=parameters.lane_width_tolerance_m)
        speed = float(lane.get("speed", "nan"))
        if math.isfinite(speed) and speed > 0:
            add("speed", f"lane:{lane_id}:speed", {"lane_id": lane_id}, speed, traffic_mode=mode,
                tolerance_m_s=parameters.speed_tolerance_m_s)
        else:
            skip("lane_speed_not_positive")
        pairs = sorted(pair for pair in connections if pair[0] == lane_id)
        add("movement_inventory", f"lanes:{lane_id}:movement_inventory", {"incoming_lane_ids": [lane_id]},
            [list(pair) for pair in pairs], traffic_mode=mode)
        for pair in pairs:
            rows = connections[pair]
            if not any(row.get("tl") for row in rows):
                continue
            if len(rows) != 1 or rows[0].get("linkIndex") is None:
                skip("signal_binding_not_single_link")
                continue
            add("signal_group", f"pair:{pair[0]}>{pair[1]}:signal_group", {"lane_pair": list(pair)},
                {"tls_id": rows[0].get("tl"), "link_index": int(rows[0].get("linkIndex"))}, traffic_mode=mode)
    vehicle_class = parameters.lane_count_vehicle_class
    selected_classes = _permissions(ET.Element("lane", allow=vehicle_class))
    for edge in road_edges:
        count = sum(vehicle_class == "all" or bool(selected_classes & _permissions(lane, network_version=version))
                    for lane in edge.findall("lane"))
        add("lane_count", f"edges:{edge.get('id')}:lane_count:{vehicle_class}",
            {"edge_ids": [edge.get("id")], "vehicle_class": vehicle_class}, count)
    for junction in root.findall("junction"):
        if junction.get("type") == "internal" or (parameters.junction_ids is not None
                                                  and junction.get("id") not in parameters.junction_ids):
            continue
        shape = _shape(junction)
        if not shape:
            skip("junction_shape_missing")
            continue
        if len(shape) < 3 or not Polygon(shape).is_valid or Polygon(shape).area <= 0:
            skip("junction_shape_not_a_valid_polygon")
            continue
        add("junction_surface", f"junction:{junction.get('id')}:surface", {"junction_id": junction.get("id")}, shape,
            minimum_iou=parameters.junction_minimum_iou)
    ordered = [fact for kind in parameters.kinds for fact in sorted(facts[kind], key=lambda row: row["id"])]
    table = {"schema": "astra.network-facts/v1", "site_id": site_id,
             "target_date": target_date.isoformat() if isinstance(target_date, date) else target_date,
             "coordinate_system": {"type": "network_xy_m",
                                   "location": dict(location.attrib) if location is not None else {}},
             "sources": [{"id": SOURCE_ID, "path": str(network) if source_path is None else source_path,
                          "sha256": digest, "source_date": None,
                          "authority": "Reference SUMO network, model truth by construction; no field claim",
                          "license": license_name}],
             "facts": ordered}
    NetworkFactTable.model_validate(table)
    summary = {"reference_net": {"path": str(network) if source_path is None else source_path, "sha256": digest},
               "parameters": {key: list(value) if isinstance(value, tuple) else value
                              for key, value in asdict(parameters).items()},
               "fact_counts": {kind: len(facts[kind]) for kind in parameters.kinds}, "skipped": skipped,
               "claim_boundary": "Facts restate the reference network under its own ids; they carry no field truth."}
    return table, summary


def write_reference_net_facts(*, net_file: str | Path, output_file: str | Path, site_id: str,
                              target_date: str | date, parameters: ReferenceFactParameters | None = None,
                              license_name: str = "unspecified") -> dict[str, Any]:
    """Write one new fact table next to nothing it reads; refuses an existing output path."""
    output = Path(output_file).resolve()
    if output.exists() or output == Path(net_file).resolve():
        raise ValueError("Use a new fact-table path separate from the reference network.")
    table, summary = build_reference_net_facts(net_file, site_id=site_id, target_date=target_date,
                                               parameters=parameters, license_name=license_name)
    write_json_atomic(output, table, sort_keys=True)
    return {**summary, "output_file": str(output), "sha256": file_sha256(output), "fact_count": len(table["facts"])}
