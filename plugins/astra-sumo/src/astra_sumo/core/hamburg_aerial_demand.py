"""Generate detector-constrained demand for a Hamburg aerial candidate."""

from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .cached_detector_demand import read_canonical_count_file
from .candidate_contracts import file_sha256
from .detector_demand import read_net_lanes, write_e1_additional
from .digital_twin_mapping import (
    DetectorMapping,
    EdgeFlow,
    build_virtual_sensor_aggregation,
    write_route_sampler_edge_counts,
    write_virtual_detector_mapping,
    write_virtual_e2_additional,
    write_virtual_expected_counts,
)
from .route_sampler import run_route_sampler
from .hamburg_sensor_twin import build_station_detector_bank

REQUEST_SCHEMA = "torii.hamburg-aerial-demand-request/v1"
REPORT_SCHEMA = "torii.hamburg-aerial-demand/v1"


def generate_hamburg_aerial_demand(
    *,
    request_file: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Build one plausible route set from active complete count cross-sections."""
    request_path = Path(request_file).expanduser().resolve()
    request = _load_object(request_path, "request")
    if request.get("schema") != REQUEST_SCHEMA:
        raise ValueError(f"request schema must be {REQUEST_SCHEMA}")
    scenario = str(request.get("scenario", "medium")).strip().lower()
    if scenario not in {"conservative", "medium", "high"}:
        raise ValueError("request scenario must be conservative, medium, or high")
    minimize_vehicles = float(request.get("minimize_vehicles", 0.0))
    diversify_routes = bool(request.get("diversify_routes", True))
    count_binding_manifest = _verified_artifact(
        request.get("count_binding_manifest"),
        "count_binding_manifest",
    )
    route_candidate_manifest = _verified_artifact(
        request.get("route_candidate_manifest"),
        "route_candidate_manifest",
    )
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")

    binding = _load_object(count_binding_manifest, "count binding manifest")
    if binding.get("schema") != "torii.hamburg-aerial-count-binding/v1":
        raise ValueError("count binding manifest schema is invalid")
    if binding.get("status") != "pass":
        raise ValueError("count binding manifest is not pass")
    network = _verified_artifact(binding.get("inputs", {}).get("network"), "network")
    detector_file = _verified_artifact(
        binding.get("artifacts", {}).get("detector_mapping"),
        "detector_mapping",
    )
    binding_group_file = _verified_artifact(
        binding.get("artifacts", {}).get("binding_groups"),
        "binding_groups",
    )
    count_scope_file = _verified_artifact(
        binding.get("inputs", {}).get("count_scope_manifest"),
        "count_scope_manifest",
    )
    count_scope = _load_object(count_scope_file, "count scope manifest")
    canonical_file = _verified_artifact(
        count_scope.get("artifacts", {}).get("counts_simulation_15min"),
        "counts_simulation_15min",
    )

    all_mappings = _read_detector_mappings(detector_file)
    mappings = [row for row in all_mappings if row.mapping_status == "active"]
    binding_groups = json.loads(binding_group_file.read_text(encoding="utf-8"))
    group_policies = _scenario_group_policies(binding_groups, scenario)
    active_groups = [row for row in binding_groups if row.get("group_status") == "active"]
    active_stream_ids = {
        int(stream_id)
        for row in active_groups
        for stream_id in row.get("member_stream_ids", [])
    }
    counts = [
        row for row in read_canonical_count_file(canonical_file) if row.stream_id in active_stream_ids
    ]
    edge_flows = _build_official_station_edge_flows(active_groups, counts)
    active_edge_sections = len({row.edge_id for row in edge_flows})
    if active_edge_sections == 0:
        raise ValueError("no active official station edge constraints are available for routeSampler")

    mapped_stream_ids = {row.stream_id for row in mappings}
    aggregation = build_virtual_sensor_aggregation(
        mappings,
        [row for row in counts if row.stream_id in mapped_stream_ids],
        bin_seconds=900,
        expected_begin=0,
        expected_end=9000,
        group_policies=group_policies,
    )
    destination.mkdir(parents=True)
    detector_dir = destination / "detectors"
    demand_dir = destination / "demand"
    detector_dir.mkdir()
    demand_dir.mkdir()
    virtual_mapping_file = detector_dir / "virtual-detector-mapping.csv"
    expected_file = detector_dir / "virtual-expected-counts-15min.csv"
    e1_file = detector_dir / "e1-detectors.add.xml"
    station_e1_file = detector_dir / "station-group-e1.add.xml"
    station_bank_file = detector_dir / "station-detector-bank.json"
    e2_file = detector_dir / "e2-detectors.add.xml"
    edge_audit_file = demand_dir / "edge-constraint-audit.csv"
    edge_data_file = demand_dir / "official-edge-counts-15min.xml"
    write_virtual_detector_mapping(virtual_mapping_file, aggregation.groups)
    write_virtual_expected_counts(expected_file, aggregation.expected_counts)
    write_e1_additional(
        e1_file,
        list(aggregation.detectors),
        lanes=read_net_lanes(network),
        period=900,
        output_file="e1-15min.xml",
    )
    station_bank = build_station_detector_bank(network, active_groups, all_mappings)
    write_e1_additional(
        station_e1_file,
        station_bank["detectors"],
        lanes=read_net_lanes(network),
        period=900,
        output_file="station-group-e1-15min.xml",
    )
    station_bank_file.write_text(
        json.dumps(station_bank["station_groups"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_virtual_e2_additional(
        e2_file,
        aggregation.groups,
        output_file="e2-15min.xml",
        period=900,
    )
    _write_station_edge_constraint_audit(edge_audit_file, active_groups)
    write_route_sampler_edge_counts(edge_data_file, edge_flows)
    route_sampler = run_route_sampler(
        candidate_manifest_csv=route_candidate_manifest,
        edge_data_file=edge_data_file,
        output_dir=demand_dir,
        prefix=f"hamburg_five_aerial_{scenario}",
        begin=0,
        end=9000,
        interval=900,
        seed=42,
        optimize="full",
        minimize_vehicles=minimize_vehicles,
        route_sampler_script=None,
        timeout_seconds=300.0,
    )
    diversification = (
        _diversify_equivalent_routes(
            Path(str(route_sampler["candidate_route_file"])),
            Path(str(route_sampler["demand_route_file"])),
            {row.edge_id for row in edge_flows},
        )
        if diversify_routes
        else {
            "method": "disabled",
            "constraint_counts_preserved": True,
        }
    )
    route_sampler["demand_route_sha256"] = file_sha256(
        Path(str(route_sampler["demand_route_file"]))
    )
    route_sampler["route_diversification"] = diversification
    route_sampler_pass = route_sampler.get("status") == "pass"
    report = {
        "schema": REPORT_SCHEMA,
        "status": "pass" if route_sampler_pass else "blocked",
        "decision": "review_required",
        "claim_status": "detector-constrained-plausible-demand" if route_sampler_pass else "construction-invalid",
        "scenario": scenario,
        "minimize_vehicles": minimize_vehicles,
        "diversify_routes": diversify_routes,
        "inputs": {
            "request": {"path": str(request_path), "sha256": file_sha256(request_path)},
            "count_binding_manifest": {
                "path": str(count_binding_manifest),
                "sha256": file_sha256(count_binding_manifest),
            },
            "network": {"path": str(network), "sha256": file_sha256(network)},
            "route_candidate_manifest": {
                "path": str(route_candidate_manifest),
                "sha256": file_sha256(route_candidate_manifest),
            },
            "binding_groups": {
                "path": str(binding_group_file),
                "sha256": file_sha256(binding_group_file),
            },
            "canonical_counts": {"path": str(canonical_file), "sha256": file_sha256(canonical_file)},
        },
        "selected_window": binding.get("selected_window"),
        "counts": {
            "active_count_streams": len(active_stream_ids),
            "canonical_count_rows": len(counts),
            "virtual_detectors": len(aggregation.detectors),
            "station_group_detectors": len(station_bank["detectors"]),
            "complete_edge_sections": active_edge_sections,
            "edge_flow_rows": len(edge_flows),
            "official_station_group_count": len(binding_groups),
            "aggregation_policy_override_count": len(group_policies),
        },
        "route_sampler": route_sampler,
        "gates": {
            "source_hashes": "pass",
            "active_count_bindings_only": "pass",
            "complete_edge_sections_available": "pass",
            "route_sampler": "pass" if route_sampler_pass else "blocked",
            "sumo_replay": "not_run",
            "official_signal_timing": "not_run",
        },
        "artifacts": {
            "virtual_detector_mapping": _record(virtual_mapping_file),
            "virtual_expected_counts": _record(expected_file),
            "e1_additional": _record(e1_file),
            "station_group_e1_additional": _record(station_e1_file),
            "station_detector_bank": _record(station_bank_file),
            "e2_additional": _record(e2_file),
            "edge_constraint_audit": _record(edge_audit_file),
            "edge_counts": _record(edge_data_file),
        },
        "claim_boundary": (
            "Official Zählstelle composition members are always summed; the scenario label does not change "
            "published counts. The route file is one detector-constrained plausible demand realization over "
            "active complete edge sections. It is not a unique OD matrix and does not include official signal timing."
        ),
    }
    manifest_file = destination / "manifest.json"
    manifest_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**report, "manifest_file": str(manifest_file), "manifest_sha256": file_sha256(manifest_file)}


def _verified_artifact(value: Any, label: str) -> Path:
    if not isinstance(value, Mapping) or not value.get("path") or not value.get("sha256"):
        raise ValueError(f"request {label} must contain path and sha256")
    path = Path(str(value["path"])).expanduser().resolve(strict=True)
    if file_sha256(path) != str(value["sha256"]).lower():
        raise ValueError(f"{label} SHA-256 does not match")
    return path


def _load_object(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _optional_float(value: str) -> float | None:
    return float(value) if value.strip() else None


def _scenario_group_policies(
    groups: Any,
    scenario: str,
) -> dict[tuple[str, str], str]:
    if not isinstance(groups, list):
        raise ValueError("binding_groups must be a JSON list")
    for row in groups:
        if not isinstance(row, Mapping):
            raise ValueError("binding_groups rows must be objects")
        if row.get("aggregation") != "sum_official_zusammensetzung":
            raise ValueError("binding_groups must contain official station compositions")
    return {}


def _build_official_station_edge_flows(
    groups: Any,
    counts: list[Any],
) -> list[EdgeFlow]:
    if not groups:
        raise ValueError("at least one active official station group is required")
    counts_by_stream: dict[int, dict[tuple[int, int], Any]] = {}
    for row in counts:
        if row.quality_status != "complete":
            raise ValueError(f"count stream {row.stream_id} has incomplete source bins")
        key = (row.begin, row.end)
        stream_rows = counts_by_stream.setdefault(row.stream_id, {})
        if key in stream_rows:
            raise ValueError(f"count stream {row.stream_id} repeats interval {key}")
        stream_rows[key] = row

    flows = []
    seen_edges: set[str] = set()
    for group in groups:
        edge_id = str(group.get("constraint_edge") or "")
        if not edge_id:
            raise ValueError("active official station group has no constraint_edge")
        if edge_id in seen_edges:
            raise ValueError(f"multiple official station groups constrain SUMO edge {edge_id}")
        seen_edges.add(edge_id)
        member_ids = [int(value) for value in group.get("member_stream_ids", [])]
        if not member_ids:
            raise ValueError("active official station group has no member streams")
        missing = [stream_id for stream_id in member_ids if stream_id not in counts_by_stream]
        if missing:
            raise ValueError(f"official station group is missing count streams {missing}")
        intervals = set(counts_by_stream[member_ids[0]])
        if any(set(counts_by_stream[stream_id]) != intervals for stream_id in member_ids[1:]):
            raise ValueError("official station composition members have different count intervals")
        station_id = int(group["station_stream_id"])
        node_id = str(group["node_id"])
        for begin, end in sorted(intervals):
            flows.append(
                EdgeFlow(
                    begin=begin,
                    end=end,
                    edge_id=edge_id,
                    count=sum(counts_by_stream[value][(begin, end)].count for value in member_ids),
                    detector_ids=(f"station_{station_id}",),
                    node_ids=(node_id,),
                    lane_ids=(),
                )
            )
    return flows


def _write_station_edge_constraint_audit(path: Path, groups: list[Any]) -> None:
    fieldnames = [
        "station_stream_id",
        "station_id",
        "node_id",
        "direction",
        "constraint_edge",
        "constraint_resolution",
        "member_stream_ids",
        "aggregation",
        "constraint_status",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in groups:
            writer.writerow(
                {
                    **{key: row.get(key, "") for key in fieldnames},
                    "member_stream_ids": json.dumps(row.get("member_stream_ids", [])),
                    "constraint_status": "active",
                }
            )


def _diversify_equivalent_routes(
    candidate_route_file: Path,
    demand_route_file: Path,
    measured_edges: set[str],
) -> dict[str, Any]:
    """Round-robin routes within identical measured-edge signatures."""

    if not measured_edges:
        raise ValueError("route diversification requires measured edges")
    candidates: dict[tuple[str, ...], list[tuple[str, ...]]] = defaultdict(list)
    for route in ET.parse(candidate_route_file).getroot().findall("route"):
        edges = tuple(route.attrib.get("edges", "").split())
        signature = tuple(sorted(measured_edges.intersection(edges)))
        if signature and edges not in candidates[signature]:
            candidates[signature].append(edges)
    for values in candidates.values():
        values.sort(key=lambda edges: (edges[0], edges[-1], edges))

    tree = ET.parse(demand_route_file)
    vehicles = tree.getroot().findall("vehicle")
    before_paths = []
    before_counts = Counter()
    after_counts = Counter()
    positions: dict[tuple[str, ...], int] = defaultdict(int)
    for vehicle in vehicles:
        route = vehicle.find("route")
        if route is None:
            raise ValueError(f"demand vehicle {vehicle.attrib.get('id')} has no inline route")
        edges = tuple(route.attrib.get("edges", "").split())
        before_paths.append(edges)
        signature = tuple(sorted(measured_edges.intersection(edges)))
        if not signature or signature not in candidates:
            raise ValueError(f"demand route has no equivalent measured-edge candidate: {edges}")
        for edge_id in signature:
            before_counts[edge_id] += 1
        options = candidates[signature]
        replacement = options[positions[signature] % len(options)]
        positions[signature] += 1
        route.attrib["edges"] = " ".join(replacement)
        for edge_id in signature:
            after_counts[edge_id] += 1
    if before_counts != after_counts:
        raise ValueError("route diversification changed official edge constraint counts")
    ET.indent(tree, space="    ")
    tree.write(demand_route_file, encoding="utf-8", xml_declaration=True)
    after_paths = [
        tuple(vehicle.find("route").attrib["edges"].split())  # type: ignore[union-attr]
        for vehicle in vehicles
    ]
    return {
        "method": "round_robin_within_identical_measured_edge_signature",
        "constraint_counts_preserved": True,
        "used_path_count_before": len(set(before_paths)),
        "used_path_count_after": len(set(after_paths)),
        "source_count_before": len({edges[0] for edges in before_paths}),
        "source_count_after": len({edges[0] for edges in after_paths}),
        "constraint_vehicle_counts": dict(sorted(after_counts.items())),
    }


def _read_detector_mappings(path: Path) -> list[DetectorMapping]:
    result = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            result.append(
                DetectorMapping(
                    detector_id=row["detector_id"],
                    stream_id=int(row["stream_id"]),
                    node_id=row["node_id"],
                    asset_id=row["asset_id"],
                    real_direction=row["real_direction"],
                    lane_use=row["lane_use"],
                    longitude=float(row["longitude"]),
                    latitude=float(row["latitude"]),
                    official_map_lane=row["official_map_lane"],
                    official_map_distance_m=_optional_float(row["official_map_distance_m"]),
                    sumo_edge=row["sumo_edge"],
                    sumo_lane=row["sumo_lane"],
                    lane_position=float(row["lane_position"]),
                    distance_m=_optional_float(row["distance_m"]),
                    heading_error_deg=_optional_float(row["heading_error_deg"]),
                    period=int(row["period"]),
                    mapping_confidence=row["mapping_confidence"],
                    mapping_status=row["mapping_status"],
                    mapping_reason=row["mapping_reason"],
                )
            )
    if not result:
        raise ValueError("detector mapping is empty")
    return result


def _record(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": file_sha256(path)}


__all__ = ["REPORT_SCHEMA", "REQUEST_SCHEMA", "generate_hamburg_aerial_demand"]
