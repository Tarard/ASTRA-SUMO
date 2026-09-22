"""Materialize one Hamburg official MAP/OCIT-C intersection as SUMO PlainXML.

This is a deliberately narrow, OSM-free topology core.  Hamburg's MAP XML
provides lane and connection identities, the matching MAP KML provides lane
centre-lines and drive-line geometry, and OCIT-C decides which lane-to-lane
vehicle movements and primary/secondary control expressions are admissible.

Only vehicle lanes are materialized in v1.  Bicycle and pedestrian lanes and
movements remain explicit exclusions in the manifest.  The emitted traffic
light program is a one-step all-red structural placeholder: it proves the
controller/link-index topology can be compiled, but it is not field timing and
must not be used as a calibrated simulation program.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pyproj import Transformer

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .digital_twin import MapConnection, MapLane, parse_mapem
from .hamburg_map_kml import (
    bind_hamburg_map_kml_to_mapem,
    parse_hamburg_map_kml,
)
from .ocit_c import (
    OcitVehicleTopologyInventory,
    build_vehicle_topology_inventory,
    parse_ocit_c,
    topology_control_index_by_node,
)


HAMBURG_OFFICIAL_INTERSECTION_PLAINXML_SCHEMA = (
    "torii.hamburg-official-map-ocit-intersection-plainxml/v1"
)
GEOMETRY_POLICY = "official-map-kml-lane-bounded-drive-line/v1"
CONTROL_POLICY = "one-link-index-per-distinct-primary-secondary-expression/v1"
DEFAULT_PROJECTION = "EPSG:25832"

_SAFE_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_SAFE_XML_ID = re.compile(r"[^A-Za-z0-9.-]+")
_MOTOR_VCLASSES = "passenger taxi bus coach delivery truck motorcycle emergency"
# SUMO's local schema resolver keys off the canonical HTTP URI written by the
# SUMO tools themselves; using HTTPS makes older/local resolvers miss the XSD.
_SUMO_XSD_BASE = "http://sumo.dlr.de/xsd"


class HamburgOfficialIntersectionPlainXmlError(ValueError):
    """Raised when official evidence cannot support a deterministic candidate."""


def materialize_hamburg_official_intersection_plainxml(
    *,
    map_xml_file: Path,
    map_kml_file: Path,
    ocit_c_file: Path,
    output_dir: Path,
    classification_file: Path | None = None,
    accepted_classification_id: str | None = None,
    expected_classification_sha256: str | None = None,
    expected_node_id: str | None = None,
    expected_sha256: Mapping[str, str] | None = None,
    prefix: str | None = None,
    projection_crs: str = DEFAULT_PROJECTION,
    structural_speed_mps: float = 13.89,
    structural_lane_width_m: float = 3.2,
    geometry_tolerance_m: float = 0.25,
    compile_net: bool = True,
    netconvert_binary: str = "netconvert",
    timeout_seconds: float = 60.0,
    strict_movement_vt: bool = True,
    command_runner: Callable[..., object] = run_command,
) -> dict[str, Any]:
    """Write a separate official single-intersection PlainXML candidate.

    The three official inputs and the accepted single-core classification are
    immutable evidence. ``output_dir`` must not already
    exist and may not be an input directory.  Missing or contradictory lane,
    movement, endpoint, or OCIT-C control evidence raises before any artifact
    is written.  A netconvert failure is retained as a hash-bound blocked
    manifest rather than being mistaken for a usable network.
    """

    if (
        classification_file is None
        or not accepted_classification_id
        or not expected_classification_sha256
    ):
        raise HamburgOfficialIntersectionPlainXmlError(
            "single-core materialization requires classification_file, "
            "accepted_classification_id, and expected_classification_sha256"
        )
    classification_digest = str(expected_classification_sha256).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", classification_digest):
        raise HamburgOfficialIntersectionPlainXmlError(
            "expected_classification_sha256 must be a SHA-256 digest"
        )
    expected = _validate_expected_hashes(expected_sha256)

    sources = {
        "map_xml": Path(map_xml_file).expanduser().resolve(),
        "map_kml": Path(map_kml_file).expanduser().resolve(),
        "ocit_c": Path(ocit_c_file).expanduser().resolve(),
    }
    classification_path = Path(classification_file).expanduser().resolve()
    sources["classification"] = classification_path
    destination = Path(output_dir).expanduser().resolve()
    _validate_destination(destination, sources)

    classification_payload, classification_hash = _load_single_core_layout_profile(
        classification_path
    )
    if classification_hash.lower() != classification_digest:
        raise HamburgOfficialIntersectionPlainXmlError(
            "classification SHA-256 does not match expected_classification_sha256"
        )
    classification = _validate_single_core_layout_profile_payload(
        classification_payload,
        node_id=expected_node_id,
        accepted_classification_id=accepted_classification_id,
    )

    _validate_distinct_sources(sources)
    source_hashes = {
        role: classification_hash if role == "classification" else file_sha256(path)
        for role, path in sources.items()
    }
    for role, digest in expected.items():
        if source_hashes[role].lower() != digest:
            raise HamburgOfficialIntersectionPlainXmlError(
                f"{role} SHA-256 does not match expected_sha256"
            )

    if not math.isfinite(structural_speed_mps) or structural_speed_mps <= 0:
        raise HamburgOfficialIntersectionPlainXmlError(
            "structural_speed_mps must be finite and positive"
        )
    if not math.isfinite(structural_lane_width_m) or structural_lane_width_m <= 0:
        raise HamburgOfficialIntersectionPlainXmlError(
            "structural_lane_width_m must be finite and positive"
        )
    if not math.isfinite(geometry_tolerance_m) or geometry_tolerance_m <= 0:
        raise HamburgOfficialIntersectionPlainXmlError(
            "geometry_tolerance_m must be finite and positive"
        )
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise HamburgOfficialIntersectionPlainXmlError(
            "timeout_seconds must be finite and positive"
        )

    map_lanes, map_connections = parse_mapem(sources["map_xml"])
    kml_report = parse_hamburg_map_kml(
        sources["map_kml"],
        expected_sha256=source_hashes["map_kml"],
    )
    binding = bind_hamburg_map_kml_to_mapem(
        kml_report,
        map_lanes,
        map_connections,
        expected_node_id=expected_node_id,
    )
    ocit_config = parse_ocit_c(
        sources["ocit_c"],
        strict_movement_vt=strict_movement_vt,
        ignore_non_motor_vt=True,
    )
    if _normalize_node_id(ocit_config.node_id) != _normalize_node_id(binding["node_id"]):
        raise HamburgOfficialIntersectionPlainXmlError(
            "MAP and OCIT-C node identifiers do not match"
        )
    classification = _validate_single_core_layout_profile_payload(
        classification,
        node_id=str(binding["node_id"]),
        accepted_classification_id=accepted_classification_id,
    )
    inventory = build_vehicle_topology_inventory(
        [ocit_config],
        map_lanes,
        map_connections,
        [],
    )
    if inventory.status != "pass" or not inventory.movements:
        raise HamburgOfficialIntersectionPlainXmlError(
            "OCIT-C/MAP vehicle topology inventory is empty or non-passing"
        )

    artifact_prefix = prefix or f"hamburg-map-{_safe_id(str(binding['node_id']))}"
    if not _SAFE_PREFIX.fullmatch(artifact_prefix):
        raise HamburgOfficialIntersectionPlainXmlError(
            "prefix must be a safe 1-96 character artifact stem"
        )

    plan = _build_plan(
        binding=binding,
        map_lanes=map_lanes,
        map_connections=map_connections,
        inventory=inventory,
        projection_crs=projection_crs,
        geometry_tolerance_m=geometry_tolerance_m,
        structural_speed_mps=structural_speed_mps,
        structural_lane_width_m=structural_lane_width_m,
    )

    paths = {
        "nodes": destination / f"{artifact_prefix}.nod.xml",
        "edges": destination / f"{artifact_prefix}.edg.xml",
        "connections": destination / f"{artifact_prefix}.con.xml",
        "tllogic": destination / f"{artifact_prefix}.tll.xml",
        "types": destination / f"{artifact_prefix}.typ.xml",
        "netconvert_config": destination / f"{artifact_prefix}.netccfg",
        "network": destination / f"{artifact_prefix}.net.xml",
        "manifest": destination / f"{artifact_prefix}.manifest.json",
    }
    destination.mkdir(parents=True)
    _write_nodes(paths["nodes"], plan)
    _write_edges(paths["edges"], plan)
    _write_connections(paths["connections"], plan)
    _write_tllogic(paths["tllogic"], plan)
    _write_types(paths["types"], plan)
    _write_netconvert_config(paths, artifact_prefix)

    plain_artifact_roles = (
        "nodes",
        "edges",
        "connections",
        "tllogic",
        "types",
        "netconvert_config",
    )
    plain_artifacts = {
        role: _artifact_record(paths[role], destination)
        for role in plain_artifact_roles
    }
    plan_digest = _stable_digest(
        {
            "geometry_policy": GEOMETRY_POLICY,
            "control_policy": CONTROL_POLICY,
            "source_hashes": source_hashes,
            "plain_artifact_hashes": {
                role: plain_artifacts[role]["sha256"] for role in plain_artifact_roles
            },
        }
    )
    candidate_id = f"hamburg-official-map-{binding['node_id']}-{plan_digest[:20]}"

    netconvert_report: dict[str, Any]
    compiled_audit: dict[str, Any]
    if compile_net and not plan["permission_review_lane_ids"]:
        command = [str(netconvert_binary), "-c", paths["netconvert_config"].name]
        command_result = _result_dict(
            command_runner(command, cwd=destination, timeout_seconds=timeout_seconds)
        )
        netconvert_report = {"status": command_result.get("status", "fail"), "command": command, "result": command_result}
        if command_result.get("status") == "pass" and command_result.get("returncode") == 0 and paths["network"].is_file():
            compiled_audit = _audit_compiled_network(
                paths["network"],
                plan,
                geometry_tolerance_m=max(geometry_tolerance_m, 0.5),
            )
        else:
            compiled_audit = {
                "status": "blocked",
                "reason": "netconvert_failed_or_produced_no_network",
            }
    else:
        netconvert_report = {
            "status": "not_run",
            "command": [str(netconvert_binary), "-c", paths["netconvert_config"].name],
        }
        compiled_audit = {"status": "not_run"}

    structural_status = (
        "review_required"
        if plan["permission_review_lane_ids"]
        else
        "pass"
        if not compile_net or (
            netconvert_report["status"] == "pass" and compiled_audit["status"] == "pass"
        )
        else "blocked"
    )
    artifacts = dict(plain_artifacts)
    if paths["network"].is_file():
        artifacts["network"] = _artifact_record(paths["network"], destination)

    excluded = _excluded_modal_inventory(binding, inventory)
    unmapped_movement_count = sum(
        bool(movement.unmapped_primary_vt or movement.unmapped_secondary_vt)
        for movement in ocit_config.vehicle_movements
    )
    ocit_movement_gate = (
        "pass"
        if strict_movement_vt and unmapped_movement_count == 0
        else "review_required"
    )
    manifest: dict[str, Any] = {
        "schema": HAMBURG_OFFICIAL_INTERSECTION_PLAINXML_SCHEMA,
        "candidate_id": candidate_id,
        "status": structural_status,
        "claim_status": "official_vehicle_topology_structural_candidate_only",
        "automatic_promotion_gate": "blocked",
        "human_review_required": False,
        "node_id": str(binding["node_id"]),
        "controller_id": plan["controller_id"],
        "sources": {
            role: {
                "path": str(path),
                "sha256": source_hashes[role],
                "bytes": path.stat().st_size,
                "expected_hash_supplied": role in expected or role == "classification",
            }
            for role, path in sources.items()
        },
        "projection": {
            "source_crs": "EPSG:4326",
            "target_crs": projection_crs,
            "always_xy": True,
            "coordinate_policy": "project official KML longitude/latitude directly",
        },
        "geometry": {
            "policy": GEOMETRY_POLICY,
            "lane_orientation_rule": (
                "ingress A-to-B; egress B-to-A, validated against official KML endpoint placemarks"
            ),
            "connection_shape_rule": (
                "project ingress endpoint B, retain de-duplicated Drive-line control points whose "
                "arc positions lie strictly between the B projections, then project egress endpoint B"
            ),
            "lane_index_rule": (
                "SUMO index 0 is the geometry-derived rightmost lane at endpoint B in traffic direction"
            ),
            "junction_shape_rule": (
                "only after the single-core/one-owner gate, use a simple angular polygon through the "
                "official vehicle-lane B endpoints; this prevents netconvert from moving the external "
                "lane ends away from the official stop-line boundary"
            ),
            "geometry_tolerance_m": geometry_tolerance_m,
            "movement_geometry": plan["movement_geometry_evidence"],
            "single_core_layout_classification": {
                "classification_id": classification["classification_id"],
                "physical_conflict_core_count": 1,
                "owner_count": 1,
                "controller_domain_count": 1,
            },
        },
        "control": {
            "policy": CONTROL_POLICY,
            "ocit_movement_vt_policy": {
                "strict": strict_movement_vt,
                "unmapped_movement_count": unmapped_movement_count,
                "semantics": (
                    "all OCIT vehicle signal references must map to a motor group"
                    if strict_movement_vt
                    else "unmapped official OCIT vehicle references are retained and block promotion"
                ),
            },
            "inventory_policy": inventory.group_resolution_policy,
            "control_key_to_link_index": plan["control_key_to_link_index"],
            "program": {
                "program_id": "structural-all-red",
                "state": "r" * plan["link_index_count"],
                "duration_seconds": 1,
                "semantics": (
                    "compiler-only all-red placeholder; no phase sequence, green split, cycle, offset, "
                    "or Saturday timing is inferred"
                ),
            },
        },
        "lane_groups": plan["manifest_lane_groups"],
        "vehicle_permissions": {
            "status": "review_required" if plan["permission_review_lane_ids"] else "pass",
            "unresolved_lane_ids": plan["permission_review_lane_ids"],
            "policy": "Explicit MAP bus/taxi restrictions override compiler defaults. Unresolved lanes remain closed in the PlainXML draft and network compilation is deferred.",
        },
        "movements": plan["manifest_movements"],
        "excluded_modal_features": excluded,
        "structural_compiler_defaults": {
            "speed_mps": structural_speed_mps,
            "lane_width_m": structural_lane_width_m,
            "field_evidence": "not_available_in_MAP_KML_or_OCIT-C",
            "claim": "compilation_only",
        },
        "counts": {
            "approach_count": len(plan["approach_nodes"]),
            "edge_count": len(plan["edge_groups"]),
            "vehicle_lane_count": len(plan["lane_mapping"]),
            "vehicle_movement_count": len(plan["movements"]),
            "distinct_control_expression_count": plan["link_index_count"],
            "excluded_lane_count": len(excluded["lanes"]),
            "excluded_connection_count": len(excluded["connections"]),
        },
        "gates": {
            "source_hashes": "pass",
            "single_official_node": "pass",
            "single_core_layout_profile": "pass",
            "exact_kml_mapem_lane_identity": "pass",
            "exact_kml_mapem_connection_identity": "pass",
            "ocit_vetted_vehicle_movements": ocit_movement_gate,
            "official_lane_orientation": "pass",
            "official_vehicle_permissions": "review_required" if plan["permission_review_lane_ids"] else "pass",
            "official_drive_line_clipping": "pass",
            "explicit_lane_to_lane_connections": "pass",
            "distinct_control_expression_link_indices": "pass",
            "netconvert": netconvert_report["status"],
            "compiled_network_geometry": compiled_audit["status"],
            "field_signal_timing": "blocked",
            "automatic_promotion": "blocked",
        },
        "netconvert": netconvert_report,
        "compiled_network_audit": compiled_audit,
        "artifacts": artifacts,
        "excluded_inputs": ["OpenStreetMap", "Google Maps", "manual geometry tracing"],
        "official_references": [
            "https://sumo.dlr.de/docs/Networks/PlainXML.html#lane-specific-definitions",
            "https://sumo.dlr.de/docs/Networks/PlainXML.html#lane-to-lane-connectivity",
            "https://sumo.dlr.de/docs/Networks/PlainXML.html#controlled-connections",
            "https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html#signal-state-definitions",
        ],
        "claim_boundary": (
            "The candidate transcribes official MAP vehicle lane geometry, OCIT-C-vetted lane-to-lane "
            "topology, and primary/secondary control-expression grouping for one intersection. It excludes "
            "bike/pedestrian topology and makes no field claim about lane width, speed, priority, signal "
            "phase timing, detector actuation, coordination, or simulation readiness."
        ),
    }
    write_json_atomic(paths["manifest"], manifest, sort_keys=True)
    return {
        **manifest,
        "output_dir": str(destination),
        "manifest_file": str(paths["manifest"]),
    }


def _build_plan(
    *,
    binding: Mapping[str, Any],
    map_lanes: Sequence[MapLane],
    map_connections: Sequence[MapConnection],
    inventory: OcitVehicleTopologyInventory,
    projection_crs: str,
    geometry_tolerance_m: float,
    structural_speed_mps: float,
    structural_lane_width_m: float,
) -> dict[str, Any]:
    try:
        transformer = Transformer.from_crs("EPSG:4326", projection_crs, always_xy=True)
    except Exception as exc:  # noqa: BLE001 - pyproj emits several domain-specific subclasses.
        raise HamburgOfficialIntersectionPlainXmlError(
            f"invalid or unavailable projection {projection_crs!r}: {exc}"
        ) from exc

    bound_lane_by_id = {str(row["lane_id"]): dict(row) for row in binding["lanes"]}
    permission_by_id = {lane.lane_id: lane.permission_metadata for lane in map_lanes if lane.is_vehicle}
    if len(bound_lane_by_id) != len(binding["lanes"]):
        raise HamburgOfficialIntersectionPlainXmlError("bound KML lanes are not unique")
    vehicle_lane_ids = {
        lane.lane_id for lane in map_lanes if lane.is_vehicle
    }
    if not vehicle_lane_ids:
        raise HamburgOfficialIntersectionPlainXmlError("official MAP contains no vehicle lanes")
    if not vehicle_lane_ids.issubset(bound_lane_by_id):
        raise HamburgOfficialIntersectionPlainXmlError(
            "official vehicle lane is missing from KML binding"
        )

    movement_keys = {
        (movement.ingress_lane_id, movement.egress_lane_id)
        for movement in inventory.movements
    }
    official_vehicle_connections = {
        (connection.ingress_lane_id, connection.egress_lane_id)
        for connection in map_connections
        if connection.ingress_lane_id in vehicle_lane_ids
        and connection.egress_lane_id in vehicle_lane_ids
    }
    excluded_non_motor_connections = set(inventory.excluded_non_motor_pairs)
    missing_vehicle_connections = official_vehicle_connections - movement_keys
    extra_vehicle_connections = movement_keys - official_vehicle_connections
    allowed_excluded_non_motor_connections = (
        excluded_non_motor_connections & official_vehicle_connections
    )
    if (
        missing_vehicle_connections != allowed_excluded_non_motor_connections
        or extra_vehicle_connections
    ):
        raise HamburgOfficialIntersectionPlainXmlError(
            "MAP vehicle connections and OCIT-C-vetted movement inventory differ; "
            f"missing={sorted(missing_vehicle_connections)}, "
            f"known_non_motor_only={sorted(allowed_excluded_non_motor_connections)}, "
            f"extra={sorted(extra_vehicle_connections)}"
        )

    lane_rows: list[dict[str, Any]] = []
    for lane_id in sorted(vehicle_lane_ids, key=_natural_key):
        row = bound_lane_by_id[lane_id]
        ingress = str(row.get("ingress_approach", "")).strip()
        egress = str(row.get("egress_approach", "")).strip()
        role = str(row.get("kml_direction_role", ""))
        if bool(ingress) == bool(egress):
            raise HamburgOfficialIntersectionPlainXmlError(
                f"vehicle lane {lane_id} must have exactly one ingress/egress approach"
            )
        direction = "ingress" if ingress else "egress"
        approach = ingress or egress
        if role != direction:
            raise HamburgOfficialIntersectionPlainXmlError(
                f"vehicle lane {lane_id} KML role {role!r} contradicts MAP {direction} semantics"
            )
        coordinates = [_project_coordinate(transformer, value) for value in row["coordinates"]]
        endpoint_a = _project_coordinate(transformer, row["endpoint_a"])
        endpoint_b = _project_coordinate(transformer, row["endpoint_b"])
        oriented = _orient_lane_shape(
            lane_id=lane_id,
            coordinates=coordinates,
            endpoint_a=endpoint_a,
            endpoint_b=endpoint_b,
            direction=direction,
            tolerance_m=geometry_tolerance_m,
        )
        lane_rows.append(
            {
                "lane_id": lane_id,
                "direction": direction,
                "approach": approach,
                "shape": oriented,
                "endpoint_a": endpoint_a,
                "endpoint_b": endpoint_b,
                "source_coordinates": row["coordinates"],
                "permission_metadata": permission_by_id[lane_id],
            }
        )

    node_token = _safe_id(str(binding["node_id"]))
    controller_id = f"hh-map-{node_token}"
    core_node_id = f"hh-map-{node_token}-core"
    approach_nodes: dict[str, dict[str, Any]] = {}
    for approach in sorted({row["approach"] for row in lane_rows}, key=_natural_key):
        rows = [row for row in lane_rows if row["approach"] == approach]
        approach_nodes[approach] = {
            "node_id": f"hh-map-{node_token}-a{_safe_id(approach)}-boundary",
            "xy": _mean_point([row["endpoint_a"] for row in rows]),
        }
    core_xy = _mean_point([row["endpoint_b"] for row in lane_rows])
    junction_shape = _angular_endpoint_polygon(
        [row["endpoint_b"] for row in lane_rows],
        center=core_xy,
        tolerance_m=geometry_tolerance_m,
    )

    edge_groups: list[dict[str, Any]] = []
    lane_mapping: dict[str, dict[str, Any]] = {}
    seen_edge_ids: set[str] = set()
    for approach, direction in sorted(
        {(row["approach"], row["direction"]) for row in lane_rows},
        key=lambda value: (_natural_key(value[0]), value[1]),
    ):
        rows = [
            dict(row)
            for row in lane_rows
            if row["approach"] == approach and row["direction"] == direction
        ]
        rows = _sort_right_to_left(rows)
        edge_id = f"hh-map-{node_token}-a{_safe_id(approach)}-{'in' if direction == 'ingress' else 'out'}"
        if edge_id in seen_edge_ids:
            raise HamburgOfficialIntersectionPlainXmlError(
                "approach identifiers collide after safe SUMO id normalization"
            )
        seen_edge_ids.add(edge_id)
        for index, row in enumerate(rows):
            row["lane_index"] = index
            row["sumo_lane_id"] = f"{edge_id}_{index}"
            lane_mapping[row["lane_id"]] = {
                **row["permission_metadata"],
                "edge_id": edge_id,
                "lane_index": index,
                "sumo_lane_id": row["sumo_lane_id"],
                "approach": approach,
                "direction": direction,
                "endpoint_a_xy": _point_list(row["endpoint_a"]),
                "endpoint_b_xy": _point_list(row["endpoint_b"]),
                "traffic_shape_xy": [_point_list(point) for point in row["shape"]],
            }
        boundary_node_id = approach_nodes[approach]["node_id"]
        edge_groups.append(
            {
                "edge_id": edge_id,
                "approach": approach,
                "direction": direction,
                "from_node": boundary_node_id if direction == "ingress" else core_node_id,
                "to_node": core_node_id if direction == "ingress" else boundary_node_id,
                "lanes": rows,
            }
        )
    if set(lane_mapping) != vehicle_lane_ids:
        raise HamburgOfficialIntersectionPlainXmlError(
            "not every official vehicle lane received one SUMO edge/lane binding"
        )

    control_indices_by_node = topology_control_index_by_node(inventory)
    normalized_node = _normalize_node_id(str(binding["node_id"]))
    control_indices = control_indices_by_node.get(normalized_node)
    if control_indices is None:
        raise HamburgOfficialIntersectionPlainXmlError(
            "OCIT-C topology did not produce a control-index table for the MAP node"
        )
    if set(control_indices.values()) != set(range(len(control_indices))):
        raise HamburgOfficialIntersectionPlainXmlError(
            "OCIT-C topology control indices are not contiguous from zero"
        )

    bound_connection_by_id = {
        str(row["connection_id"]): dict(row) for row in binding["connections"]
    }
    if len(bound_connection_by_id) != len(binding["connections"]):
        raise HamburgOfficialIntersectionPlainXmlError(
            "bound KML connections are not unique"
        )
    movements: list[dict[str, Any]] = []
    movement_geometry_evidence: list[dict[str, Any]] = []
    for movement in inventory.movements:
        connection = bound_connection_by_id.get(movement.connection_id)
        if connection is None:
            raise HamburgOfficialIntersectionPlainXmlError(
                f"OCIT-C movement {movement.connection_id} has no bound KML connection"
            )
        ingress = bound_lane_by_id[movement.ingress_lane_id]
        egress = bound_lane_by_id[movement.egress_lane_id]
        ingress_b = _project_coordinate(transformer, ingress["endpoint_b"])
        egress_b = _project_coordinate(transformer, egress["endpoint_b"])
        connection_coordinates = [
            _project_coordinate(transformer, value)
            for value in connection["connection_coordinates"]
        ]
        if (
            _distance(connection_coordinates[0], ingress_b) > geometry_tolerance_m
            or _distance(connection_coordinates[-1], egress_b) > geometry_tolerance_m
        ):
            raise HamburgOfficialIntersectionPlainXmlError(
                f"connection {movement.connection_id} does not run from ingress B to egress B"
            )
        raw_drive_line = [
            _project_coordinate(transformer, value)
            for value in connection["drive_line_coordinates"]
        ]
        clipped_shape, clipping_evidence = _clip_drive_line_to_lane_b_endpoints(
            raw_drive_line,
            ingress_b=ingress_b,
            egress_b=egress_b,
            tolerance_m=geometry_tolerance_m,
        )
        ingress_shape = lane_rows_by_id(lane_rows)[movement.ingress_lane_id]["shape"]
        egress_shape = lane_rows_by_id(lane_rows)[movement.egress_lane_id]["shape"]
        raw_start_lane_distance = _point_to_polyline_distance(raw_drive_line[0], ingress_shape)
        raw_end_lane_distance = _point_to_polyline_distance(raw_drive_line[-1], egress_shape)
        if raw_start_lane_distance > geometry_tolerance_m or raw_end_lane_distance > geometry_tolerance_m:
            raise HamburgOfficialIntersectionPlainXmlError(
                f"Drive-line {movement.connection_id} does not attach to its official external lanes"
            )
        link_index = control_indices.get(movement.topology_control_key)
        if link_index is None:
            raise HamburgOfficialIntersectionPlainXmlError(
                f"movement {movement.connection_id} lacks a control-expression link index"
            )
        source_binding = lane_mapping[movement.ingress_lane_id]
        target_binding = lane_mapping[movement.egress_lane_id]
        row = {
            "connection_id": movement.connection_id,
            "ingress_lane_id": movement.ingress_lane_id,
            "egress_lane_id": movement.egress_lane_id,
            "from_edge": source_binding["edge_id"],
            "from_lane": source_binding["lane_index"],
            "to_edge": target_binding["edge_id"],
            "to_lane": target_binding["lane_index"],
            "shape": clipped_shape,
            "topology_control_key": movement.topology_control_key,
            "link_index": link_index,
            "map_signal_group": movement.map_signal_group,
            "primary_motor_groups": list(movement.primary_motor_groups),
            "secondary_motor_groups": list(movement.secondary_motor_groups),
            "drive_line_variant": connection["drive_line_variant"],
        }
        movements.append(row)
        movement_geometry_evidence.append(
            {
                "connection_id": movement.connection_id,
                **clipping_evidence,
                "raw_drive_line_start_to_ingress_lane_m": raw_start_lane_distance,
                "raw_drive_line_end_to_egress_lane_m": raw_end_lane_distance,
                "clipped_shape_xy": [_point_list(point) for point in clipped_shape],
            }
        )

    movements.sort(key=lambda row: _natural_key(row["connection_id"]))
    movement_geometry_evidence.sort(key=lambda row: _natural_key(row["connection_id"]))
    movement_keys_in_plan = {
        (row["from_edge"], row["from_lane"], row["to_edge"], row["to_lane"])
        for row in movements
    }
    if len(movement_keys_in_plan) != len(movements):
        raise HamburgOfficialIntersectionPlainXmlError(
            "distinct official movements collapse to one SUMO lane-to-lane connection"
        )

    manifest_lane_groups = [
        {
            "approach": edge["approach"],
            "direction": edge["direction"],
            "edge_id": edge["edge_id"],
            "boundary_node_id": approach_nodes[edge["approach"]]["node_id"],
            "official_lane_ids_right_to_left": [lane["lane_id"] for lane in edge["lanes"]],
            "lane_bindings": [lane_mapping[lane["lane_id"]] for lane in edge["lanes"]],
        }
        for edge in edge_groups
    ]
    manifest_movements = [
        {
            key: value
            for key, value in movement.items()
            if key != "shape"
        }
        | {"connection_shape_xy": [_point_list(point) for point in movement["shape"]]}
        for movement in movements
    ]
    return {
        "node_id": str(binding["node_id"]),
        "controller_id": controller_id,
        "core_node_id": core_node_id,
        "core_xy": core_xy,
        "junction_shape": junction_shape,
        "approach_nodes": approach_nodes,
        "edge_groups": edge_groups,
        "lane_mapping": lane_mapping,
        "permission_review_lane_ids": sorted(lane_id for lane_id, permission in permission_by_id.items() if permission["permission_status"] in {"unknown", "review_required"}),
        "movements": movements,
        "link_index_count": len(control_indices),
        "control_key_to_link_index": dict(sorted(control_indices.items(), key=lambda item: item[1])),
        "movement_geometry_evidence": movement_geometry_evidence,
        "manifest_lane_groups": manifest_lane_groups,
        "manifest_movements": manifest_movements,
        "structural_speed_mps": structural_speed_mps,
        "structural_lane_width_m": structural_lane_width_m,
    }


def _orient_lane_shape(
    *,
    lane_id: str,
    coordinates: Sequence[tuple[float, float]],
    endpoint_a: tuple[float, float],
    endpoint_b: tuple[float, float],
    direction: str,
    tolerance_m: float,
) -> list[tuple[float, float]]:
    if len(coordinates) < 2:
        raise HamburgOfficialIntersectionPlainXmlError(
            f"vehicle lane {lane_id} has fewer than two geometry points"
        )
    direct_error = _distance(coordinates[0], endpoint_a) + _distance(coordinates[-1], endpoint_b)
    reverse_error = _distance(coordinates[0], endpoint_b) + _distance(coordinates[-1], endpoint_a)
    if min(direct_error, reverse_error) > 2 * tolerance_m:
        raise HamburgOfficialIntersectionPlainXmlError(
            f"vehicle lane {lane_id} geometry does not terminate at official A/B endpoints"
        )
    a_to_b = list(coordinates) if direct_error < reverse_error else list(reversed(coordinates))
    if abs(direct_error - reverse_error) <= 1e-6:
        raise HamburgOfficialIntersectionPlainXmlError(
            f"vehicle lane {lane_id} A/B geometry orientation is ambiguous"
        )
    traffic_shape = a_to_b if direction == "ingress" else list(reversed(a_to_b))
    if _polyline_length(traffic_shape) <= tolerance_m:
        raise HamburgOfficialIntersectionPlainXmlError(
            f"vehicle lane {lane_id} geometry is degenerate"
        )
    return traffic_shape


def _clip_drive_line_to_lane_b_endpoints(
    drive_line: Sequence[tuple[float, float]],
    *,
    ingress_b: tuple[float, float],
    egress_b: tuple[float, float],
    tolerance_m: float,
) -> tuple[list[tuple[float, float]], dict[str, Any]]:
    if len(drive_line) < 2:
        raise HamburgOfficialIntersectionPlainXmlError(
            "Drive-line requires at least two points"
        )
    ingress_s, ingress_projection_distance = _project_point_onto_polyline(ingress_b, drive_line)
    egress_s, egress_projection_distance = _project_point_onto_polyline(egress_b, drive_line)
    if ingress_projection_distance > tolerance_m or egress_projection_distance > tolerance_m:
        raise HamburgOfficialIntersectionPlainXmlError(
            "Drive-line does not pass through official lane-B endpoints within tolerance"
        )
    if egress_s - ingress_s <= tolerance_m:
        raise HamburgOfficialIntersectionPlainXmlError(
            "Drive-line orientation does not progress from ingress B to egress B"
        )
    cumulative = _cumulative_lengths(drive_line)
    retained = [
        point
        for point, arc in zip(drive_line, cumulative, strict=True)
        if ingress_s + 1e-6 < arc < egress_s - 1e-6
    ]
    clipped = _dedupe_consecutive([ingress_b, *retained, egress_b], tolerance_m=1e-3)
    if len(clipped) < 2 or _polyline_length(clipped) <= tolerance_m:
        raise HamburgOfficialIntersectionPlainXmlError(
            "lane-bounded Drive-line clipping produced a degenerate connection shape"
        )
    return clipped, {
        "source_drive_line_point_count": len(drive_line),
        "retained_drive_line_control_point_count": max(0, len(clipped) - 2),
        "clipped_shape_point_count": len(clipped),
        "ingress_b_projection_distance_m": ingress_projection_distance,
        "egress_b_projection_distance_m": egress_projection_distance,
        "raw_drive_line_start_to_ingress_b_m": _distance(drive_line[0], ingress_b),
        "raw_drive_line_end_to_egress_b_m": _distance(drive_line[-1], egress_b),
        "clipped_length_m": _polyline_length(clipped),
    }


def _sort_right_to_left(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    tangents: list[tuple[float, float]] = []
    for row in rows:
        shape = row["shape"]
        if row["direction"] == "ingress":
            start, end = shape[-2], shape[-1]
        else:
            start, end = shape[0], shape[1]
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            raise HamburgOfficialIntersectionPlainXmlError(
                f"lane {row['lane_id']} has no endpoint-B traffic tangent"
            )
        tangents.append((dx / length, dy / length))
    mean_x = sum(value[0] for value in tangents)
    mean_y = sum(value[1] for value in tangents)
    mean_length = math.hypot(mean_x, mean_y)
    if mean_length / len(tangents) < 0.5:
        raise HamburgOfficialIntersectionPlainXmlError(
            "lane group has contradictory endpoint-B traffic headings"
        )
    forward = (mean_x / mean_length, mean_y / mean_length)
    left_normal = (-forward[1], forward[0])
    ranked = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            row["endpoint_b"][0] * left_normal[0]
            + row["endpoint_b"][1] * left_normal[1],
            _natural_key(row["lane_id"]),
        ),
    )
    projections = [
        row["endpoint_b"][0] * left_normal[0]
        + row["endpoint_b"][1] * left_normal[1]
        for row in ranked
    ]
    if any(abs(right - left) < 0.05 for left, right in zip(projections, projections[1:])):
        raise HamburgOfficialIntersectionPlainXmlError(
            "lane group right-to-left order is geometrically ambiguous"
        )
    return ranked


def _write_nodes(path: Path, plan: Mapping[str, Any]) -> None:
    root = _sumo_root("nodes", "nodes_file.xsd")
    ET.SubElement(
        root,
        "node",
        {
            "id": str(plan["core_node_id"]),
            "x": _number(plan["core_xy"][0]),
            "y": _number(plan["core_xy"][1]),
            "type": "traffic_light",
            "tl": str(plan["controller_id"]),
            "shape": _shape(plan["junction_shape"]),
        },
    )
    for _approach, row in sorted(plan["approach_nodes"].items(), key=lambda item: _natural_key(item[0])):
        ET.SubElement(
            root,
            "node",
            {
                "id": str(row["node_id"]),
                "x": _number(row["xy"][0]),
                "y": _number(row["xy"][1]),
                "type": "priority",
                "fringe": "outer",
            },
        )
    _write_xml(path, root)


def _write_edges(path: Path, plan: Mapping[str, Any]) -> None:
    root = _sumo_root("edges", "edges_file.xsd")
    for group in plan["edge_groups"]:
        edge = ET.SubElement(
            root,
            "edge",
            {
                "id": group["edge_id"],
                "from": group["from_node"],
                "to": group["to_node"],
                "type": "official-map-vehicle-structural",
                "numLanes": str(len(group["lanes"])),
                "spreadType": "center",
                "allow": _MOTOR_VCLASSES,
            },
        )
        for lane in group["lanes"]:
            permission = lane["permission_metadata"]
            lane_access = (
                {"allow": " ".join(permission["allowed_vehicle_classes"] or _MOTOR_VCLASSES.split())}
                if permission["permission_status"] in {"unrestricted", "restricted"}
                else {"disallow": "all"}
            )
            ET.SubElement(
                edge,
                "lane",
                {
                    "index": str(lane["lane_index"]),
                    **lane_access,
                    "speed": _number(plan["structural_speed_mps"]),
                    "width": _number(plan["structural_lane_width_m"]),
                    "shape": _shape(lane["shape"]),
                    "type": f"official-map-lane-{lane['lane_id']}",
                },
            )
    _write_xml(path, root)


def _write_connections(path: Path, plan: Mapping[str, Any]) -> None:
    root = _sumo_root("connections", "connections_file.xsd")
    for movement in plan["movements"]:
        ET.SubElement(
            root,
            "connection",
            {
                "from": movement["from_edge"],
                "to": movement["to_edge"],
                "fromLane": str(movement["from_lane"]),
                "toLane": str(movement["to_lane"]),
                "shape": _shape(movement["shape"]),
            },
        )
    _write_xml(path, root)


def _write_tllogic(path: Path, plan: Mapping[str, Any]) -> None:
    root = _sumo_root("tlLogics", "tllogic_file.xsd")
    logic = ET.SubElement(
        root,
        "tlLogic",
        {
            "id": str(plan["controller_id"]),
            "type": "static",
            "programID": "structural-all-red",
            "offset": "0",
        },
    )
    ET.SubElement(
        logic,
        "phase",
        {
            "duration": "1",
            "state": "r" * int(plan["link_index_count"]),
        },
    )
    for movement in plan["movements"]:
        ET.SubElement(
            root,
            "connection",
            {
                "from": movement["from_edge"],
                "to": movement["to_edge"],
                "fromLane": str(movement["from_lane"]),
                "toLane": str(movement["to_lane"]),
                "tl": str(plan["controller_id"]),
                "linkIndex": str(movement["link_index"]),
            },
        )
    _write_xml(path, root)


def _write_types(path: Path, plan: Mapping[str, Any]) -> None:
    root = _sumo_root("types", "types_file.xsd")
    ET.SubElement(
        root,
        "type",
        {
            "id": "official-map-vehicle-structural",
            "numLanes": "1",
            "speed": _number(plan["structural_speed_mps"]),
            "width": _number(plan["structural_lane_width_m"]),
            "allow": _MOTOR_VCLASSES,
        },
    )
    _write_xml(path, root)


def _write_netconvert_config(paths: Mapping[str, Path], prefix: str) -> None:
    root = _sumo_root("configuration", "netconvertConfiguration.xsd")
    input_element = ET.SubElement(root, "input")
    for tag, role in (
        ("node-files", "nodes"),
        ("edge-files", "edges"),
        ("connection-files", "connections"),
        ("tllogic-files", "tllogic"),
        ("type-files", "types"),
    ):
        ET.SubElement(input_element, tag, {"value": paths[role].name})
    output = ET.SubElement(root, "output")
    ET.SubElement(output, "output-file", {"value": paths["network"].name})
    processing = ET.SubElement(root, "processing")
    ET.SubElement(processing, "no-turnarounds", {"value": "true"})
    ET.SubElement(processing, "geometry.remove", {"value": "false"})
    report = ET.SubElement(root, "report")
    ET.SubElement(report, "verbose", {"value": "true"})
    # ``local`` validates against the SUMO installation's bundled XSDs and is
    # deterministic offline. ``always`` attempts network schema retrieval and
    # can reject the same valid files when the schema host is unavailable.
    ET.SubElement(report, "xml-validation", {"value": "local"})
    _write_xml(paths["netconvert_config"], root)


def _audit_compiled_network(
    path: Path,
    plan: Mapping[str, Any],
    *,
    geometry_tolerance_m: float,
) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    offset = _net_offset(root)
    direct_connections = [
        element
        for element in root.findall("connection")
        if not element.attrib.get("from", "").startswith(":")
        and element.attrib.get("from") in {edge["edge_id"] for edge in plan["edge_groups"]}
    ]
    actual_by_key: dict[tuple[str, int, str, int], list[ET.Element]] = {}
    for element in direct_connections:
        key = (
            element.attrib.get("from", ""),
            int(element.attrib.get("fromLane", "-1")),
            element.attrib.get("to", ""),
            int(element.attrib.get("toLane", "-1")),
        )
        actual_by_key.setdefault(key, []).append(element)
    expected_keys = {
        (
            movement["from_edge"],
            movement["from_lane"],
            movement["to_edge"],
            movement["to_lane"],
        )
        for movement in plan["movements"]
    }
    if set(actual_by_key) != expected_keys or any(len(rows) != 1 for rows in actual_by_key.values()):
        return {
            "status": "blocked",
            "reason": "compiled_direct_connection_set_differs_from_official_inventory",
            "missing": sorted(expected_keys - set(actual_by_key)),
            "extra": sorted(set(actual_by_key) - expected_keys),
        }

    lane_by_id = {
        lane.attrib["id"]: lane
        for lane in root.iter("lane")
        if lane.attrib.get("id")
    }
    for lane_id, binding in plan["lane_mapping"].items():
        lane = lane_by_id.get(binding["sumo_lane_id"])
        expected_access = set(binding["allowed_vehicle_classes"] or _MOTOR_VCLASSES.split())
        if lane is None or set(lane.get("allow", "").split()) != expected_access:
            return {"status": "blocked", "reason": "compiled_lane_permissions_differ_from_map", "lane_id": lane_id}
    geometry_rows: list[dict[str, Any]] = []
    max_connection_deviation = 0.0
    for movement in plan["movements"]:
        key = (
            movement["from_edge"],
            movement["from_lane"],
            movement["to_edge"],
            movement["to_lane"],
        )
        connection = actual_by_key[key][0]
        if (
            connection.attrib.get("tl") != plan["controller_id"]
            or int(connection.attrib.get("linkIndex", "-1")) != movement["link_index"]
        ):
            return {
                "status": "blocked",
                "reason": "compiled_tls_binding_differs_from_official_control_expression",
                "connection_id": movement["connection_id"],
            }
        via = connection.attrib.get("via", "")
        via_lane = lane_by_id.get(via)
        if via_lane is None or not via_lane.attrib.get("shape"):
            return {
                "status": "blocked",
                "reason": "compiled_connection_has_no_internal_lane_geometry",
                "connection_id": movement["connection_id"],
                "via": via,
            }
        actual_shape = _parse_shape(via_lane.attrib["shape"])
        declared_shape = _parse_shape(connection.attrib.get("shape", ""))
        expected_shape = [
            (point[0] + offset[0], point[1] + offset[1])
            for point in movement["shape"]
        ]
        declared_deviation = _symmetric_polyline_deviation(declared_shape, expected_shape)
        operational_to_clipped_deviation = _symmetric_polyline_deviation(actual_shape, expected_shape)
        ingress_binding = plan["lane_mapping"][movement["ingress_lane_id"]]
        egress_binding = plan["lane_mapping"][movement["egress_lane_id"]]
        ingress_lane = lane_by_id.get(ingress_binding["sumo_lane_id"])
        egress_lane = lane_by_id.get(egress_binding["sumo_lane_id"])
        if (
            ingress_lane is None
            or egress_lane is None
            or not ingress_lane.attrib.get("shape")
            or not egress_lane.attrib.get("shape")
        ):
            return {
                "status": "blocked",
                "reason": "compiled_movement_missing_external_lane_geometry",
                "connection_id": movement["connection_id"],
            }
        compiled_ingress_shape = _parse_shape(ingress_lane.attrib["shape"])
        compiled_egress_shape = _parse_shape(egress_lane.attrib["shape"])
        official_ingress_shape = [
            (point[0] + offset[0], point[1] + offset[1])
            for point in ingress_binding["traffic_shape_xy"]
        ]
        official_egress_shape = [
            (point[0] + offset[0], point[1] + offset[1])
            for point in egress_binding["traffic_shape_xy"]
        ]
        ingress_validation_length = min(
            10.0,
            _polyline_length(compiled_ingress_shape),
            _polyline_length(official_ingress_shape),
        )
        egress_validation_length = min(
            10.0,
            _polyline_length(compiled_egress_shape),
            _polyline_length(official_egress_shape),
        )
        compiled_ingress_official_range = _terminal_polyline(
            compiled_ingress_shape,
            length_m=ingress_validation_length,
            at_end=True,
        )
        official_ingress_range = _terminal_polyline(
            official_ingress_shape,
            length_m=ingress_validation_length,
            at_end=True,
        )
        compiled_egress_official_range = _terminal_polyline(
            compiled_egress_shape,
            length_m=egress_validation_length,
            at_end=False,
        )
        official_egress_range = _terminal_polyline(
            official_egress_shape,
            length_m=egress_validation_length,
            at_end=False,
        )
        ingress_trace_deviation = _symmetric_polyline_deviation(
            compiled_ingress_official_range,
            official_ingress_range,
        )
        via_trace_deviation = _sampled_polyline_deviation_to_union(
            actual_shape,
            [official_ingress_shape, expected_shape, official_egress_shape],
        )
        egress_trace_deviation = _symmetric_polyline_deviation(
            compiled_egress_official_range,
            official_egress_range,
        )
        deviation = max(
            declared_deviation,
            ingress_trace_deviation,
            via_trace_deviation,
            egress_trace_deviation,
        )
        max_connection_deviation = max(max_connection_deviation, deviation)
        geometry_rows.append(
            {
                "connection_id": movement["connection_id"],
                "via_lane_id": via,
                "actual_point_count": len(actual_shape),
                "compiled_declared_point_count": len(declared_shape),
                "official_clipped_point_count": len(expected_shape),
                "compiled_declared_shape_deviation_m": declared_deviation,
                "operational_internal_lane_to_clipped_shape_deviation_m": (
                    operational_to_clipped_deviation
                ),
                "compiled_ingress_to_official_lane_deviation_m": ingress_trace_deviation,
                "compiled_ingress_near_b_validation_length_m": ingress_validation_length,
                "compiled_via_to_official_three_part_union_deviation_m": via_trace_deviation,
                "compiled_egress_to_official_lane_deviation_m": egress_trace_deviation,
                "compiled_egress_near_b_validation_length_m": egress_validation_length,
            }
        )
    if max_connection_deviation > geometry_tolerance_m:
        return {
            "status": "blocked",
            "reason": "compiled_three_part_movement_trace_deviates_from_official_geometry",
            "max_connection_shape_deviation_m": max_connection_deviation,
            "geometry_tolerance_m": geometry_tolerance_m,
            "connections": geometry_rows,
        }

    stopline_rows: list[dict[str, Any]] = []
    max_stopline_deviation = 0.0
    for lane_id, binding in sorted(plan["lane_mapping"].items(), key=lambda item: _natural_key(item[0])):
        compiled_lane = lane_by_id.get(binding["sumo_lane_id"])
        if compiled_lane is None or not compiled_lane.attrib.get("shape"):
            return {
                "status": "blocked",
                "reason": "compiled_network_missing_official_external_lane",
                "lane_id": lane_id,
            }
        compiled_shape = _parse_shape(compiled_lane.attrib["shape"])
        compiled_b = compiled_shape[-1] if binding["direction"] == "ingress" else compiled_shape[0]
        official_b = (
            binding["endpoint_b_xy"][0] + offset[0],
            binding["endpoint_b_xy"][1] + offset[1],
        )
        deviation = _distance(compiled_b, official_b)
        max_stopline_deviation = max(max_stopline_deviation, deviation)
        stopline_rows.append(
            {
                "lane_id": lane_id,
                "sumo_lane_id": binding["sumo_lane_id"],
                "endpoint_b_deviation_m": deviation,
            }
        )
    if max_stopline_deviation > geometry_tolerance_m:
        return {
            "status": "blocked",
            "reason": "compiled_external_lane_end_moved_from_official_endpoint_b",
            "max_stopline_deviation_m": max_stopline_deviation,
            "geometry_tolerance_m": geometry_tolerance_m,
            "lanes": stopline_rows,
        }

    logic = next(
        (element for element in root.findall("tlLogic") if element.attrib.get("id") == plan["controller_id"]),
        None,
    )
    if logic is None:
        return {"status": "blocked", "reason": "compiled_network_missing_structural_tllogic"}
    phases = logic.findall("phase")
    expected_state = "r" * int(plan["link_index_count"])
    if len(phases) != 1 or phases[0].attrib.get("state") != expected_state:
        return {
            "status": "blocked",
            "reason": "compiled_tllogic_is_not_exact_all_red_structural_placeholder",
        }
    return {
        "status": "pass",
        "network_sha256": file_sha256(path),
        "direct_connection_count": len(direct_connections),
        "distinct_link_index_count": len(
            {int(element.attrib["linkIndex"]) for element in direct_connections}
        ),
        "max_connection_shape_deviation_m": max_connection_deviation,
        "max_stopline_deviation_m": max_stopline_deviation,
        "geometry_tolerance_m": geometry_tolerance_m,
        "net_offset": list(offset),
        "connections": geometry_rows,
        "external_lane_endpoints": stopline_rows,
    }


def _excluded_modal_inventory(
    binding: Mapping[str, Any],
    inventory: OcitVehicleTopologyInventory,
) -> dict[str, Any]:
    vehicle_lane_ids = {
        str(row["lane_id"])
        for row in binding["lanes"]
        if str(row["lane_type"]).casefold() == "vehicle"
    }
    included_connections = {
        movement.connection_id for movement in inventory.movements
    }
    lanes = [
        {
            "lane_id": str(row["lane_id"]),
            "lane_type": str(row["lane_type"]),
            "reason": "v1_vehicle_only",
        }
        for row in binding["lanes"]
        if str(row["lane_id"]) not in vehicle_lane_ids
    ]
    connections = [
        {
            "connection_id": str(row["connection_id"]),
            "ingress_lane_id": str(row["ingress_lane_id"]),
            "egress_lane_id": str(row["egress_lane_id"]),
            "reason": (
                "known_non_motor_signal_group_only"
                if (
                    str(row["ingress_lane_id"]),
                    str(row["egress_lane_id"]),
                ) in set(inventory.excluded_non_motor_pairs)
                else "non_vehicle_or_not_ocit_vehicle_vetted"
            ),
        }
        for row in binding["connections"]
        if str(row["connection_id"]) not in included_connections
    ]
    return {
        "policy": "exclude bicycle and pedestrian lanes/movements from v1 without substitution",
        "lanes": sorted(lanes, key=lambda row: _natural_key(row["lane_id"])),
        "connections": sorted(
            connections, key=lambda row: _natural_key(row["connection_id"])
        ),
        "ocit_source_movement_count": inventory.source_movement_count,
        "ocit_excluded_non_vehicle_movement_count": inventory.excluded_non_vehicle_movement_count,
        "ocit_excluded_non_motor_movement_count": inventory.excluded_non_motor_movement_count,
        "ocit_excluded_non_motor_pairs": [
            {"ingress_lane_id": pair[0], "egress_lane_id": pair[1]}
            for pair in inventory.excluded_non_motor_pairs
        ],
    }


def _validate_distinct_sources(sources: Mapping[str, Path]) -> None:
    resolved = list(sources.values())
    if len(set(resolved)) != len(resolved):
        raise HamburgOfficialIntersectionPlainXmlError(
            "MAP XML, MAP KML, OCIT-C, and classification inputs must be distinct files"
        )
    for role, path in sources.items():
        if not path.is_file():
            raise HamburgOfficialIntersectionPlainXmlError(
                f"{role} is not a regular file: {path}"
            )


def _validate_expected_hashes(value: Mapping[str, str] | None) -> dict[str, str]:
    if value is None:
        return {}
    expected_keys = {"map_xml", "map_kml", "ocit_c"}
    extra = set(value) - expected_keys
    if extra:
        raise HamburgOfficialIntersectionPlainXmlError(
            f"unknown expected_sha256 roles: {sorted(extra)}"
        )
    result: dict[str, str] = {}
    for role, digest in value.items():
        normalized = str(digest).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise HamburgOfficialIntersectionPlainXmlError(
                f"expected_sha256[{role!r}] must be a SHA-256 digest"
            )
        result[role] = normalized
    return result


def _validate_single_core_layout_profile(
    path: Path,
    *,
    node_id: str | None,
    accepted_classification_id: str,
) -> Mapping[str, Any]:
    payload, _digest = _load_single_core_layout_profile(path)
    return _validate_single_core_layout_profile_payload(
        payload,
        node_id=node_id,
        accepted_classification_id=accepted_classification_id,
    )


def _load_single_core_layout_profile(path: Path) -> tuple[Mapping[str, Any], str]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        raise HamburgOfficialIntersectionPlainXmlError(
            f"invalid single-core classification file: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise HamburgOfficialIntersectionPlainXmlError(
            "single-core classification must be a JSON object"
        )
    return payload, hashlib.sha256(raw).hexdigest()


def _validate_single_core_layout_profile_payload(
    payload: Mapping[str, Any],
    *,
    node_id: str | None,
    accepted_classification_id: str,
) -> Mapping[str, Any]:
    classification = payload.get("classification")
    counts = payload.get("counts")
    execution_hint = payload.get("execution_hint")
    expected = {
        "schema_id": "torii.composable-intersection-archetype/v2",
        "status": "classified",
        "physical_arrangement": "single_core",
        "control_domain": "one_owner_one_controller",
        "physical_conflict_core_status": "known",
    }
    observed = {
        "schema_id": payload.get("schema_id"),
        "status": payload.get("status"),
        "physical_arrangement": (
            classification.get("physical_arrangement")
            if isinstance(classification, Mapping)
            else None
        ),
        "control_domain": (
            classification.get("control_domain")
            if isinstance(classification, Mapping)
            else None
        ),
        "physical_conflict_core_status": payload.get(
            "physical_conflict_core_status"
        ),
    }
    mismatches = {
        key: {"expected": value, "observed": observed.get(key)}
        for key, value in expected.items()
        if observed.get(key) != value
    }
    if not isinstance(counts, Mapping):
        mismatches["counts"] = {"expected": "object", "observed": type(counts).__name__}
    else:
        for key in (
            "physical_conflict_core_count",
            "owner_count_after_rebuild_candidate",
            "controller_domain_count",
        ):
            value = counts.get(key)
            if isinstance(value, bool) or value != 1:
                mismatches[key] = {"expected": 1, "observed": value}
    if not isinstance(execution_hint, Mapping):
        mismatches["execution_hint"] = {
            "expected": "object",
            "observed": type(execution_hint).__name__,
        }
    else:
        if execution_hint.get("classification_only") is not True:
            mismatches["classification_only"] = {
                "expected": True,
                "observed": execution_hint.get("classification_only"),
            }
        if execution_hint.get("automatic_authorization") != "blocked":
            mismatches["automatic_authorization"] = {
                "expected": "blocked",
                "observed": execution_hint.get("automatic_authorization"),
            }
        controller_ids = execution_hint.get("controller_domain_ids")
        normalized_controllers = (
            {_normalize_node_id(str(value)) for value in controller_ids}
            if isinstance(controller_ids, list)
            and all(isinstance(value, (str, int)) and not isinstance(value, bool) for value in controller_ids)
            else set()
        )
        expected_controllers = (
            {_normalize_node_id(node_id)} if node_id is not None else normalized_controllers
        )
        if len(normalized_controllers) != 1 or normalized_controllers != expected_controllers:
            mismatches["controller_domain_ids"] = {
                "expected": (
                    [_normalize_node_id(node_id)]
                    if node_id is not None
                    else "one controller domain id"
                ),
                "observed": sorted(normalized_controllers),
            }
    observed_node_id = _normalize_node_id(str(payload.get("junction_id", "")))
    if not observed_node_id or (
        node_id is not None and observed_node_id != _normalize_node_id(node_id)
    ):
        mismatches["junction_id"] = {
            "expected": (
                _normalize_node_id(node_id) if node_id is not None else "non-empty node id"
            ),
            "observed": payload.get("junction_id"),
        }
    if payload.get("classification_id") != accepted_classification_id:
        mismatches["classification_id"] = {
            "expected": accepted_classification_id,
            "observed": payload.get("classification_id"),
        }
    if mismatches:
        raise HamburgOfficialIntersectionPlainXmlError(
            "single-core classification gate failed: "
            + json.dumps(mismatches, sort_keys=True)
        )
    return payload


def _validate_destination(destination: Path, sources: Mapping[str, Path]) -> None:
    if destination.exists():
        raise HamburgOfficialIntersectionPlainXmlError(
            "output_dir must not already exist; candidates are immutable and never overwritten"
        )
    for path in sources.values():
        if destination == path.parent or _is_relative_to(path, destination):
            raise HamburgOfficialIntersectionPlainXmlError(
                "output_dir must be separate from official source artifacts"
            )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _project_coordinate(
    transformer: Transformer,
    coordinate: Sequence[float],
) -> tuple[float, float]:
    if len(coordinate) < 2:
        raise HamburgOfficialIntersectionPlainXmlError(
            "official KML coordinate lacks longitude/latitude"
        )
    x, y = transformer.transform(float(coordinate[0]), float(coordinate[1]))
    if not math.isfinite(x) or not math.isfinite(y):
        raise HamburgOfficialIntersectionPlainXmlError(
            "official KML coordinate projection is non-finite"
        )
    return float(x), float(y)


def _project_point_onto_polyline(
    point: tuple[float, float],
    line: Sequence[tuple[float, float]],
) -> tuple[float, float]:
    cumulative = _cumulative_lengths(line)
    best_s = 0.0
    best_distance = math.inf
    for index, (start, end) in enumerate(zip(line, line[1:])):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-12:
            fraction = 0.0
        else:
            fraction = max(
                0.0,
                min(
                    1.0,
                    ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
                    / length_sq,
                ),
            )
        projected = (start[0] + fraction * dx, start[1] + fraction * dy)
        distance = _distance(point, projected)
        arc = cumulative[index] + fraction * math.sqrt(length_sq)
        if distance < best_distance - 1e-9 or (
            abs(distance - best_distance) <= 1e-9 and arc < best_s
        ):
            best_s = arc
            best_distance = distance
    return best_s, best_distance


def _point_to_polyline_distance(
    point: tuple[float, float],
    line: Sequence[tuple[float, float]],
) -> float:
    return _project_point_onto_polyline(point, line)[1]


def _symmetric_polyline_deviation(
    left: Sequence[tuple[float, float]],
    right: Sequence[tuple[float, float]],
) -> float:
    if len(left) < 2 or len(right) < 2:
        return math.inf
    return max(
        max(_point_to_polyline_distance(point, right) for point in left),
        max(_point_to_polyline_distance(point, left) for point in right),
    )


def _sampled_polyline_deviation_to_union(
    candidate: Sequence[tuple[float, float]],
    official_parts: Sequence[Sequence[tuple[float, float]]],
    *,
    sample_step_m: float = 0.25,
) -> float:
    if len(candidate) < 2 or not official_parts or any(len(part) < 2 for part in official_parts):
        return math.inf
    sampled: list[tuple[float, float]] = []
    for start, end in zip(candidate, candidate[1:]):
        length = _distance(start, end)
        steps = max(1, math.ceil(length / sample_step_m))
        sampled.extend(
            (
                start[0] + (end[0] - start[0]) * index / steps,
                start[1] + (end[1] - start[1]) * index / steps,
            )
            for index in range(steps)
        )
    sampled.append(candidate[-1])
    return max(
        min(_point_to_polyline_distance(point, part) for part in official_parts)
        for point in sampled
    )


def _terminal_polyline(
    line: Sequence[tuple[float, float]],
    *,
    length_m: float,
    at_end: bool,
) -> list[tuple[float, float]]:
    if len(line) < 2 or length_m <= 0:
        raise HamburgOfficialIntersectionPlainXmlError(
            "terminal polyline requires positive length and at least two points"
        )
    oriented = list(reversed(line)) if at_end else list(line)
    result = [oriented[0]]
    remaining = length_m
    for start, end in zip(oriented, oriented[1:]):
        segment_length = _distance(start, end)
        if segment_length <= 1e-9:
            continue
        if segment_length <= remaining + 1e-9:
            result.append(end)
            remaining -= segment_length
            if remaining <= 1e-9:
                break
            continue
        fraction = remaining / segment_length
        result.append(
            (
                start[0] + (end[0] - start[0]) * fraction,
                start[1] + (end[1] - start[1]) * fraction,
            )
        )
        remaining = 0.0
        break
    if len(result) < 2:
        raise HamburgOfficialIntersectionPlainXmlError(
            "terminal polyline extraction produced a degenerate range"
        )
    return list(reversed(result)) if at_end else result


def _cumulative_lengths(line: Sequence[tuple[float, float]]) -> list[float]:
    result = [0.0]
    for start, end in zip(line, line[1:]):
        result.append(result[-1] + _distance(start, end))
    return result


def _dedupe_consecutive(
    points: Sequence[tuple[float, float]],
    *,
    tolerance_m: float,
) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for point in points:
        if not result or _distance(result[-1], point) > tolerance_m:
            result.append(point)
    return result


def _polyline_length(points: Sequence[tuple[float, float]]) -> float:
    return sum(_distance(start, end) for start, end in zip(points, points[1:]))


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _mean_point(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    if not points:
        raise HamburgOfficialIntersectionPlainXmlError("cannot average an empty point set")
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _angular_endpoint_polygon(
    points: Sequence[tuple[float, float]],
    *,
    center: tuple[float, float],
    tolerance_m: float,
) -> list[tuple[float, float]]:
    unique: list[tuple[float, float]] = []
    for point in points:
        if not any(_distance(point, existing) <= tolerance_m for existing in unique):
            unique.append(point)
    if len(unique) < 3:
        raise HamburgOfficialIntersectionPlainXmlError(
            "official vehicle-lane B endpoints cannot define a junction polygon"
        )
    ordered = sorted(
        unique,
        key=lambda point: math.atan2(point[1] - center[1], point[0] - center[0]),
    )
    area_twice = sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(ordered, [*ordered[1:], ordered[0]])
    )
    if abs(area_twice) <= tolerance_m * tolerance_m:
        raise HamburgOfficialIntersectionPlainXmlError(
            "official vehicle-lane B endpoint polygon is degenerate"
        )
    if _polygon_has_self_intersection(ordered, tolerance_m=tolerance_m):
        raise HamburgOfficialIntersectionPlainXmlError(
            "official vehicle-lane B endpoint polygon is self-intersecting"
        )
    return ordered


def _polygon_has_self_intersection(
    polygon: Sequence[tuple[float, float]],
    *,
    tolerance_m: float,
) -> bool:
    segments = list(zip(polygon, [*polygon[1:], polygon[0]]))
    count = len(segments)
    for left_index, (left_a, left_b) in enumerate(segments):
        for right_index in range(left_index + 1, count):
            if right_index in {left_index, (left_index + 1) % count}:
                continue
            if left_index == 0 and right_index == count - 1:
                continue
            right_a, right_b = segments[right_index]
            if _proper_segments_intersect(
                left_a,
                left_b,
                right_a,
                right_b,
                tolerance_m=tolerance_m,
            ):
                return True
    return False


def _proper_segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
    *,
    tolerance_m: float,
) -> bool:
    def cross(
        origin: tuple[float, float],
        first: tuple[float, float],
        second: tuple[float, float],
    ) -> float:
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (
            first[1] - origin[1]
        ) * (second[0] - origin[0])

    scale = max(_distance(a, b), _distance(c, d), 1.0)
    threshold = tolerance_m * scale
    ab_c = cross(a, b, c)
    ab_d = cross(a, b, d)
    cd_a = cross(c, d, a)
    cd_b = cross(c, d, b)
    return (
        ab_c * ab_d < -(threshold * threshold)
        and cd_a * cd_b < -(threshold * threshold)
    )


def lane_rows_by_id(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Return the already-validated lane plan indexed by official MAP lane id."""

    return {str(row["lane_id"]): row for row in rows}


def _net_offset(root: ET.Element) -> tuple[float, float]:
    location = root.find("location")
    if location is None:
        return 0.0, 0.0
    values = location.attrib.get("netOffset", "0,0").split(",")
    if len(values) != 2:
        raise HamburgOfficialIntersectionPlainXmlError(
            "compiled network has an invalid netOffset"
        )
    return float(values[0]), float(values[1])


def _parse_shape(value: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for token in value.split():
        fields = token.split(",")
        if len(fields) < 2:
            raise HamburgOfficialIntersectionPlainXmlError(
                "compiled network contains an invalid lane shape"
            )
        points.append((float(fields[0]), float(fields[1])))
    return points


def _artifact_record(path: Path, base: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(base).as_posix(),
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
    }


def _stable_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _result_dict(result: object) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        return dict(result.to_dict())  # type: ignore[union-attr]
    if isinstance(result, Mapping):
        return dict(result)
    raise HamburgOfficialIntersectionPlainXmlError(
        "command_runner must return a mapping or expose to_dict()"
    )


def _write_xml(path: Path, root: ET.Element) -> None:
    ET.indent(root, space="    ")
    text = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
    write_text_atomic(path, text)


def _sumo_root(tag: str, schema_name: str) -> ET.Element:
    return ET.Element(
        tag,
        {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": f"{_SUMO_XSD_BASE}/{schema_name}",
        },
    )


def _shape(points: Sequence[tuple[float, float]]) -> str:
    return " ".join(f"{_number(point[0])},{_number(point[1])}" for point in points)


def _number(value: float) -> str:
    return f"{float(value):.3f}"


def _point_list(point: tuple[float, float]) -> list[float]:
    return [round(float(point[0]), 6), round(float(point[1]), 6)]


def _safe_id(value: str) -> str:
    normalized = _SAFE_XML_ID.sub("-", value.strip()).strip(".-")
    if not normalized:
        raise HamburgOfficialIntersectionPlainXmlError(
            f"official identifier {value!r} cannot form a safe SUMO id"
        )
    return normalized


def _normalize_node_id(value: str) -> str:
    text = value.strip()
    if text.isdigit():
        return text.lstrip("0") or "0"
    return text.casefold()


def _natural_key(value: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(token)) if token.isdigit() else (1, token.casefold())
        for token in re.split(r"(\d+)", str(value))
        if token
    )


__all__ = [
    "CONTROL_POLICY",
    "DEFAULT_PROJECTION",
    "GEOMETRY_POLICY",
    "HAMBURG_OFFICIAL_INTERSECTION_PLAINXML_SCHEMA",
    "HamburgOfficialIntersectionPlainXmlError",
    "materialize_hamburg_official_intersection_plainxml",
]
