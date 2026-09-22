from __future__ import annotations

import csv
import math
import re
import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .digital_twin import MapConnection, MapLane, SignalStream, parse_mapem, write_map_connections
from .command_runner import run_command
from .digital_twin_mapping import (
    MapLaneBinding,
    TlsBinding,
    bind_map_lanes_to_explicit_network_lanes,
    bind_map_lanes_to_network,
    build_local_lane_graph,
    find_local_lane_paths,
    write_tls_bindings,
)
from .hamburg_movement_path import (
    HAMBURG_SANDTORKAI_CONNECTION_EVIDENCE,
    derive_hamburg_official_movement_paths,
)
from .hamburg_official import (
    hamburg_sandtorkai_primary_signal_snapshot,
    sha256_file,
    write_json,
)
from .hamburg_teacher_cell import (
    HamburgTeacherCellContract,
    build_hamburg_teacher_cell_contract,
)
from .hamburg_teacher_workflow import run_hamburg_teacher_replay_workflow
from .ocit_c import (
    OcitCConfig,
    OcitVehicleTopologyInventory,
    build_vehicle_topology_inventory,
    parse_ocit_c,
    topology_control_index_by_node,
    validate_primary_signal_groups,
)
from .road_connectivity_teacher_model import (
    canonical_road_connectivity_bundle,
    compare_road_connectivity_bundles,
)
from .sumo_commands import run_sumo_load_audit
from .tls_aggregation import demote_tls_ids


HAMBURG_OFFICIAL_TLS_WORKFLOW_SCHEMA_ID = "torii.hamburg-official-tls-workflow.v1"
HAMBURG_SANDTORKAI_TLS_PRESET_ID = "hamburg-sandtorkai-0228-2421-2394"
HAMBURG_SANDTORKAI_TLS_PRESET_VERSION = "2026-07-18.native-v1"
HAMBURG_SANDTORKAI_NODE_IDS = ("0228", "2421", "2394")
# Applying the smaller single-cell teachers first keeps every replay stage local;
# the multi-owner 0228 controller is deliberately last and remains fail-closed.
HAMBURG_SANDTORKAI_TEACHER_REPLAY_ORDER = ("2421", "2394", "0228")
HAMBURG_SANDTORKAI_GROUP_INDEX_BY_NODE: dict[str, dict[str, int]] = {
    "0228": {"K1": 0, "K2": 1, "K3": 2, "K4": 3, "K6": 4, "K7": 5, "K8": 6},
    "2421": {"K1": 0, "K2": 1, "K3": 2},
    "2394": {"K1": 0, "K2": 1, "K4": 2, "K5": 3, "K7": 4},
}
HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT = 27
HAMBURG_SANDTORKAI_OCIT_SOURCE_MOVEMENT_COUNT = 43
HAMBURG_SANDTORKAI_EXCLUDED_NON_VEHICLE_MOVEMENT_COUNT = 10
HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT = 33
HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_COUNTS_BY_NODE = {
    "0228": 16,
    "2421": 9,
    "2394": 8,
}
HAMBURG_SANDTORKAI_TOPOLOGY_CONTROL_COUNT = 21
HAMBURG_SANDTORKAI_TOPOLOGY_CONTROL_COUNTS_BY_NODE = {
    "0228": 9,
    "2421": 6,
    "2394": 6,
}
HAMBURG_SANDTORKAI_REQUIRED_MAP_LANE_COUNT = 44
HAMBURG_SANDTORKAI_RETIRED_LINK_POLICY = "demote_after_complete_official_inventory"
HAMBURG_SANDTORKAI_STATIC_CLOSURE_LANE_TARGETS = {
    ("0228", "24"): "158068424_0",
}
SUMO_EDGE_OVERLAP_WARNING_RE = re.compile(
    r"Warning:\s+Edge\s+'(?P<first>[^']+)'\s+overlaps\s+with\s+edge\s+"
    r"'(?P<second>[^']+)'\s+by\s+(?P<distance>[0-9.eE+-]+)\."
)


def rebuild_hamburg_sandtorkai_official_tls(
    *,
    source_net_file: Path,
    signal_asset_dir: Path,
    output_dir: Path,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    """Rebuild the three Sandtorkai controllers from cached Hamburg MAP/OCIT assets.

    This workflow never downloads data and never mutates ``source_net_file``.  It binds the
    complete 33-movement OCIT-C/MAP motor-vehicle topology to the rebuilt network.  The versioned
    27-stream TLD metadata snapshot is an observed subset used only for official-group and rebuilt
    binding cross-checks; it is never treated as topology completeness evidence.
    """

    source_net_file = source_net_file.resolve()
    signal_asset_dir = signal_asset_dir.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = output_dir / "hamburg_official_tls_rebuild.manifest.json"
    report: dict[str, Any] = {
        "schema_id": HAMBURG_OFFICIAL_TLS_WORKFLOW_SCHEMA_ID,
        "status": "fail",
        "claim_status": "construction-invalid",
        "automatic_promotion_gate": "blocked",
        "stage": "input_validation",
        "preset_id": HAMBURG_SANDTORKAI_TLS_PRESET_ID,
        "preset_version": HAMBURG_SANDTORKAI_TLS_PRESET_VERSION,
        "source_net_file": str(source_net_file),
        "signal_asset_dir": str(signal_asset_dir),
        "output_dir": str(output_dir),
        "expected_primary_stream_count": HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT,
        "expected_observation_stream_count": HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT,
        "expected_vehicle_topology_movement_count": (
            HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT
        ),
        "expected_ocit_source_movement_count": HAMBURG_SANDTORKAI_OCIT_SOURCE_MOVEMENT_COUNT,
        "expected_excluded_non_vehicle_movement_count": (
            HAMBURG_SANDTORKAI_EXCLUDED_NON_VEHICLE_MOVEMENT_COUNT
        ),
        "expected_topology_control_count": HAMBURG_SANDTORKAI_TOPOLOGY_CONTROL_COUNT,
        "retired_link_policy": {
            "value": HAMBURG_SANDTORKAI_RETIRED_LINK_POLICY,
            "authorization_gate": (
                "enabled only after OCIT-C movements joined to official MAP vehicle lanes prove "
                "the complete 33-movement motor-vehicle topology for all three nodes"
            ),
        },
        "source_tls_replacement_policy": {
            "value": "replace_all_source_tls_in_compact_scope",
            "authorization": (
                "user explicitly authorized removal of every OSM signal controller inside the "
                "compact corridor and replacement by the three official Hamburg controllers"
            ),
        },
    }
    artifact_paths: list[tuple[str, Path]] = []

    try:
        _validate_inputs(
            source_net_file=source_net_file,
            signal_asset_dir=signal_asset_dir,
            netconvert_binary=netconvert_binary,
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
        )
        source_hash_before = sha256_file(source_net_file)
        report["source_net_sha256_before"] = source_hash_before
        retired_source_tls_ids = _source_tls_controller_ids(source_net_file)
        report["source_tls_replacement_policy"].update(
            {
                "source_tls_controller_count": len(retired_source_tls_ids),
                "source_tls_controller_ids": list(retired_source_tls_ids),
            }
        )

        report["stage"] = "asset_parsing"
        map_lanes: list[MapLane] = []
        map_connections: list[MapConnection] = []
        ocit_configs: list[OcitCConfig] = []
        asset_inventory: list[dict[str, Any]] = []
        for node_id in HAMBURG_SANDTORKAI_NODE_IDS:
            map_path = signal_asset_dir / f"{node_id}_map_xml.xml"
            ocit_path = signal_asset_dir / f"{node_id}_ocit_xml.xml"
            node_lanes, node_connections = parse_mapem(map_path)
            ocit_config = parse_ocit_c(ocit_path)
            _validate_asset_node(
                expected_node_id=node_id,
                map_lanes=node_lanes,
                map_connections=node_connections,
                ocit_config=ocit_config,
            )
            map_lanes.extend(node_lanes)
            map_connections.extend(node_connections)
            ocit_configs.append(ocit_config)
            for kind, path in (("map_xml", map_path), ("ocit_xml", ocit_path)):
                asset_inventory.append(
                    {
                        "node_id": node_id,
                        "kind": kind,
                        "path": str(path),
                        "sha256": sha256_file(path),
                        "bytes": path.stat().st_size,
                    }
                )
        report["official_asset_inventory"] = asset_inventory

        map_connection_file = output_dir / "official_map_connections.csv"
        write_map_connections(map_connection_file, map_connections)
        artifact_paths.append(("official_map_connections", map_connection_file))

        observation_streams = hamburg_sandtorkai_primary_signal_snapshot()
        _validate_primary_stream_inventory(observation_streams)
        stream_file = output_dir / "official_primary_signal_streams.csv"
        _write_dataclass_csv(stream_file, observation_streams)
        artifact_paths.append(("official_primary_signal_streams", stream_file))

        ocit_validation = validate_primary_signal_groups(observation_streams, ocit_configs)
        if (
            ocit_validation.status != "pass"
            or ocit_validation.primary_stream_count != HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT
        ):
            raise ValueError(
                "OCIT primary-group validation did not cover the complete 27-stream snapshot"
            )
        ocit_validation_file = output_dir / "official_ocit_group_validation.json"
        write_json(ocit_validation_file, asdict(ocit_validation))
        artifact_paths.append(("official_ocit_group_validation", ocit_validation_file))
        report["ocit_group_validation"] = asdict(ocit_validation)

        observed_group_audit = _audit_observed_ocit_motor_group_subset(
            observation_streams,
            ocit_configs,
        )
        observed_group_file = output_dir / "official_tld_observed_group_subset.json"
        write_json(observed_group_file, observed_group_audit)
        artifact_paths.append(("official_tld_observed_group_subset", observed_group_file))
        report["tld_observed_group_subset_audit"] = observed_group_audit
        if observed_group_audit["status"] != "pass":
            raise ValueError(
                "TLD observation groups contradict the OCIT motor-group inventory: "
                + "; ".join(observed_group_audit["errors"])
            )

        report["stage"] = "ocit_vehicle_topology_inventory"
        vehicle_topology_inventory = build_vehicle_topology_inventory(
            ocit_configs,
            map_lanes,
            map_connections,
            observation_streams,
        )
        topology_streams = vehicle_topology_inventory.topology_streams
        topology_index_by_node = {
            _normalize_node(node_id): indices
            for node_id, indices in topology_control_index_by_node(
                vehicle_topology_inventory
            ).items()
        }
        topology_inventory_audit = _audit_vehicle_topology_inventory(
            vehicle_topology_inventory,
            topology_index_by_node,
        )
        report["vehicle_topology_inventory_audit"] = topology_inventory_audit
        if topology_inventory_audit["status"] != "pass":
            raise ValueError(
                "OCIT-C/MAP motor-vehicle topology inventory is incomplete: "
                + "; ".join(topology_inventory_audit["errors"])
            )
        topology_inventory_file = output_dir / "official_ocit_vehicle_movements.json"
        write_json(topology_inventory_file, asdict(vehicle_topology_inventory))
        artifact_paths.append(("official_ocit_vehicle_movements", topology_inventory_file))
        report["inventory_counts"] = {
            "topology_source": "OCIT-C movements joined to official MAP vehicle lanes",
            "ocit_source_movement_count": vehicle_topology_inventory.source_movement_count,
            "excluded_non_vehicle_movement_count": (
                vehicle_topology_inventory.excluded_non_vehicle_movement_count
            ),
            "vehicle_topology_movement_count": vehicle_topology_inventory.movement_count,
            "observation_source": "versioned TLD primary_signal metadata snapshot",
            "observation_stream_count": len(observation_streams),
            "observation_match_count": vehicle_topology_inventory.observed_match_count,
        }

        report["stage"] = "base_map_binding"
        base_lane_bindings = bind_map_lanes_to_network(source_net_file, map_lanes)
        base_binding_audit = _audit_required_lane_bindings(
            topology_streams,
            base_lane_bindings,
        )
        base_binding_file = output_dir / "official_map_lane_to_sumo_base.csv"
        _write_dataclass_csv(base_binding_file, base_lane_bindings)
        artifact_paths.append(("base_map_lane_bindings", base_binding_file))
        report["base_map_lane_binding_audit"] = base_binding_audit
        if base_binding_audit["status"] != "pass":
            raise ValueError("official MAP lanes required by vehicle topology are not fully bound")

        report["stage"] = "native_teacher_derivation"
        movement_paths = derive_hamburg_official_movement_paths(
            candidate_net_file=source_net_file,
            official_movements=vehicle_topology_inventory.movements,
            lane_bindings=base_lane_bindings,
            connection_evidence=HAMBURG_SANDTORKAI_CONNECTION_EVIDENCE,
        )
        movement_path_file = output_dir / "official_movement_lane_paths.json"
        write_json(movement_path_file, [asdict(path) for path in movement_paths])
        artifact_paths.append(("official_movement_lane_paths", movement_path_file))

        contracts = tuple(
            build_hamburg_teacher_cell_contract(
                node_id=node_id,
                map_lanes=map_lanes,
                map_connections=map_connections,
                topology_inventory=vehicle_topology_inventory,
                movement_paths=movement_paths,
                candidate_net_file=source_net_file,
            )
            for node_id in HAMBURG_SANDTORKAI_TEACHER_REPLAY_ORDER
        )
        derivation = {
            "status": "pass",
            "engine": "torii_native_teacher_replay",
            "movement_path_count": len(movement_paths),
            "contract_count": len(contracts),
            "controller_order": list(HAMBURG_SANDTORKAI_TEACHER_REPLAY_ORDER),
            "contract_topology_statuses": {
                node_id: contract.topology_status
                for node_id, contract in zip(
                    HAMBURG_SANDTORKAI_TEACHER_REPLAY_ORDER,
                    contracts,
                    strict=True,
                )
            },
            "contract_review_gates": {
                node_id: list(contract.review_gates)
                for node_id, contract in zip(
                    HAMBURG_SANDTORKAI_TEACHER_REPLAY_ORDER,
                    contracts,
                    strict=True,
                )
            },
            "visual_review_required_count": 0,
        }
        derivation_file = output_dir / "official_tls_native_teacher_derivation.json"
        write_json(derivation_file, derivation)
        artifact_paths.append(("official_tls_native_teacher_derivation", derivation_file))
        # Preserve the facade field consumed by older product manifests while
        # making the Torii-native engine explicit in the artifact itself.
        report["derivation_status"] = derivation["status"]
        report["native_teacher_derivation"] = derivation
        report["visual_review_required_count"] = 0

        report["stage"] = "native_teacher_replay"
        rebuild_report = run_hamburg_teacher_replay_workflow(
            source_net_file=source_net_file,
            contracts=contracts,
            output_dir=output_dir / "network",
            prefix="hamburg_sandtorkai_official_native",
            netconvert_binary=netconvert_binary,
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
        )
        report["network_rebuild"] = rebuild_report
        rebuild_manifest = Path(str(rebuild_report.get("report_file", "")))
        if rebuild_manifest.is_file():
            artifact_paths.append(("official_tls_native_replay_report", rebuild_manifest))
        if rebuild_report.get("status") != "pass":
            raise ValueError(
                "Torii-native official TLS replay failed: "
                + str(
                    rebuild_report.get("reason")
                    or rebuild_report.get("error")
                    or "one or more native replay gates failed"
                )
            )
        rebuilt_net_file = Path(str(rebuild_report.get("final_net_file", ""))).resolve()
        if not rebuilt_net_file.is_file():
            raise ValueError("Torii-native replay reported pass without a final net file")
        artifact_paths.append(("native_replay_net_file", rebuilt_net_file))
        if sha256_file(source_net_file) != source_hash_before:
            raise ValueError("frozen source SUMO network changed during official TLS rebuilding")

        report["stage"] = "native_geometry_continuity_audit"
        geometry_audit = audit_hamburg_native_replay_geometry(
            source_net_file,
            rebuilt_net_file,
            replay_report=rebuild_report,
        )
        geometry_audit_file = output_dir / "official_tls_native_geometry_continuity.json"
        write_json(geometry_audit_file, geometry_audit)
        artifact_paths.append(("official_tls_native_geometry_continuity", geometry_audit_file))
        report["native_geometry_continuity_audit"] = geometry_audit
        rendered_overlap_audit = audit_sumo_rendered_edge_overlaps(
            rebuilt_net_file,
            output_dir=output_dir / "rendered_overlap_audit",
            netconvert_binary=netconvert_binary,
            timeout_seconds=timeout_seconds,
        )
        rendered_overlap_audit_file = output_dir / "official_tls_rendered_overlap_audit.json"
        write_json(rendered_overlap_audit_file, rendered_overlap_audit)
        artifact_paths.append(
            ("official_tls_rendered_overlap_audit", rendered_overlap_audit_file)
        )
        overlap_probe_net_file = Path(
            str(rendered_overlap_audit.get("normalized_probe_net_file", ""))
        )
        if overlap_probe_net_file.is_file():
            artifact_paths.append(
                ("official_tls_rendered_overlap_probe_net", overlap_probe_net_file)
            )
        report["rendered_edge_overlap_audit"] = rendered_overlap_audit
        if (
            geometry_audit["status"] != "pass"
            or rendered_overlap_audit["status"] != "pass"
        ):
            raise ValueError(
                "Torii-native replay geometry continuity gate failed: "
                f"edge_id_delta={geometry_audit['external_edge_id_delta_count']}, "
                f"geometry_mismatches={geometry_audit['geometry_mismatch_count']}, "
                f"lane_length_mismatches="
                f"{geometry_audit.get('external_lane_shape_length_audit', {}).get('failure_count', 0)}, "
                f"rendered_edge_overlaps="
                f"{rendered_overlap_audit.get('overlap_warning_count', 0)}"
            )

        report["stage"] = "compact_scope_tls_retirement"
        compact_tls_net_file = output_dir / "hamburg_sandtorkai_official_tls.net.xml"
        tls_retirement = retire_compact_scope_non_official_tls(
            rebuilt_net_file=rebuilt_net_file,
            output_net_file=compact_tls_net_file,
            replay_report=rebuild_report,
            topology_inventory_audit=topology_inventory_audit,
            source_tls_controller_ids=retired_source_tls_ids,
        )
        tls_retirement_file = output_dir / "official_tls_compact_scope_retirement.json"
        write_json(tls_retirement_file, tls_retirement)
        artifact_paths.append(("official_tls_compact_scope_retirement", tls_retirement_file))
        report["compact_scope_tls_retirement"] = tls_retirement
        report["source_tls_replacement_policy"].update(
            {
                "status": tls_retirement["status"],
                "official_controller_ids": tls_retirement["official_controller_ids"],
                "retired_controller_ids": tls_retirement["retired_controller_ids"],
                "remaining_controller_ids": tls_retirement["after_controller_ids"],
            }
        )
        if tls_retirement["status"] != "pass":
            raise ValueError(
                "compact-scope OSM TLS retirement gate failed: "
                + "; ".join(tls_retirement["errors"])
            )
        native_replay_net_file = rebuilt_net_file
        rebuilt_net_file = compact_tls_net_file.resolve()
        report["network_rebuild"].update(
            {
                "native_replay_final_net_file": str(native_replay_net_file),
                "final_net_file": str(rebuilt_net_file),
                "post_replay_compact_scope_tls_retirement_status": "pass",
            }
        )
        artifact_paths.append(("rebuilt_net_file", rebuilt_net_file))

        report["stage"] = "post_retirement_sumo_load"
        final_sumo_load = run_sumo_load_audit(
            net_file=rebuilt_net_file,
            output_dir=output_dir / "post_retirement_sumo_load",
            sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds,
        )
        report["post_retirement_sumo_load_audit"] = final_sumo_load
        for role, key in (
            ("post_retirement_sumo_load_report", "report_file"),
            ("post_retirement_sumo_load_manifest", "manifest_file"),
        ):
            path = Path(str(final_sumo_load.get(key, "")))
            if path.is_file():
                artifact_paths.append((role, path))
        if final_sumo_load.get("status") != "pass":
            raise ValueError("post-retirement official TLS network does not load in SUMO")

        report["stage"] = "effective_map_binding"
        nearest_effective_lane_bindings = bind_map_lanes_to_network(
            rebuilt_net_file, map_lanes
        )
        nearest_effective_binding_file = (
            output_dir / "official_map_lane_to_sumo_effective_nearest.csv"
        )
        _write_dataclass_csv(
            nearest_effective_binding_file, nearest_effective_lane_bindings
        )
        artifact_paths.append(
            ("nearest_effective_map_lane_bindings", nearest_effective_binding_file)
        )
        effective_lane_bindings, contract_projection_audit = (
            _project_contract_lane_bindings(
                net_file=rebuilt_net_file,
                map_lanes=map_lanes,
                nearest_bindings=nearest_effective_lane_bindings,
                contracts=contracts,
            )
        )
        contract_projection_file = output_dir / "official_map_lane_contract_projection.json"
        write_json(contract_projection_file, contract_projection_audit)
        artifact_paths.append(
            ("official_map_lane_contract_projection", contract_projection_file)
        )
        report["effective_map_lane_contract_projection_audit"] = (
            contract_projection_audit
        )
        if contract_projection_audit["status"] != "pass":
            raise ValueError(
                "teacher-contract projection onto rebuilt MAP lanes failed: "
                + "; ".join(contract_projection_audit["errors"])
            )
        effective_binding_audit = _audit_required_lane_bindings(
            topology_streams,
            effective_lane_bindings,
        )
        effective_binding_file = output_dir / "official_map_lane_to_sumo_effective.csv"
        _write_dataclass_csv(effective_binding_file, effective_lane_bindings)
        artifact_paths.append(("effective_map_lane_bindings", effective_binding_file))
        report["effective_map_lane_binding_audit"] = effective_binding_audit
        if effective_binding_audit["status"] != "pass":
            raise ValueError(
                "rebuilt network lost one or more MAP lanes required by vehicle topology"
            )

        report["stage"] = "official_movement_physical_endpoint_audit"
        movement_endpoint_audit = audit_hamburg_official_movement_endpoints(
            net_file=rebuilt_net_file,
            topology_inventory=vehicle_topology_inventory,
            lane_bindings=effective_lane_bindings,
            contracts=contracts,
            replay_report=rebuild_report,
        )
        movement_endpoint_file = output_dir / "official_movement_physical_endpoints.json"
        write_json(movement_endpoint_file, movement_endpoint_audit)
        artifact_paths.append(
            ("official_movement_physical_endpoints", movement_endpoint_file)
        )
        report["official_movement_physical_endpoint_audit"] = movement_endpoint_audit
        if movement_endpoint_audit["status"] != "pass":
            raise ValueError(
                "strict 33-movement physical endpoint gate failed: "
                + "; ".join(movement_endpoint_audit["errors"])
            )

        report["stage"] = "primary_stream_tls_binding"
        tls_bindings = bind_observed_streams_to_replayed_tls(
            streams=observation_streams,
            topology_inventory=vehicle_topology_inventory,
            movement_endpoint_audit=movement_endpoint_audit,
        )
        tls_binding_file = output_dir / "official_primary_signal_tls_bindings.csv"
        write_tls_bindings(tls_binding_file, tls_bindings)
        artifact_paths.append(("official_primary_signal_tls_bindings", tls_binding_file))
        binding_audit = _audit_primary_tls_bindings(
            observation_streams,
            tls_bindings,
            vehicle_topology_inventory,
            movement_endpoint_audit,
        )
        report["primary_stream_tls_binding_audit"] = binding_audit
        if binding_audit["status"] != "pass":
            raise ValueError(
                "strict 27-stream TLS binding gate failed: "
                + "; ".join(binding_audit["errors"])
            )

        report["stage"] = "complete"
        report["status"] = "pass"
        report["claim_status"] = "official-tls-topology-ready"
        report["rebuilt_net_file"] = str(rebuilt_net_file)
        report["source_net_sha256_after"] = sha256_file(source_net_file)
        report["source_net_unchanged"] = report["source_net_sha256_after"] == source_hash_before
    except (ET.ParseError, OSError, ValueError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        if source_net_file.is_file():
            report["source_net_sha256_after"] = sha256_file(source_net_file)
            before = str(report.get("source_net_sha256_before", ""))
            report["source_net_unchanged"] = bool(before) and before == report["source_net_sha256_after"]

    report["artifacts"] = _artifact_manifest(artifact_paths)
    write_json(manifest_file, report)
    report["manifest_file"] = str(manifest_file)
    report["manifest_sha256"] = sha256_file(manifest_file)
    return report


def audit_sumo_rendered_edge_overlaps(
    net_file: Path,
    *,
    output_dir: Path,
    netconvert_binary: str = "netconvert",
    overlap_threshold_m: float = 0.1,
    timeout_seconds: float = 240.0,
    command_runner: Any = run_command,
) -> dict[str, Any]:
    """Fail closed on overlaps reported by SUMO's own rendered-edge checker.

    ``.net.xml`` topology alone cannot prove that lane surfaces are geometrically
    valid.  NetEdit renders the lane shapes reconstructed by ``netconvert`` and
    can therefore expose overlaps hidden by an XML connectivity audit.  This
    audit asks the installed SUMO version to normalize a disposable copy with
    ``--geometry.check-overlap`` and treats every reported external-edge pair as
    a blocking geometry defect.  The source network is hashed before and after
    the probe and is never used as an output target.
    """

    source = net_file.resolve()
    destination = output_dir.resolve()
    normalized_probe = destination / "rendered_overlap_probe.net.xml"
    if not math.isfinite(overlap_threshold_m) or overlap_threshold_m < 0:
        raise ValueError("overlap_threshold_m must be finite and non-negative")
    if not source.is_file():
        return {
            "status": "fail",
            "source_net_file": str(source),
            "normalized_probe_net_file": str(normalized_probe),
            "overlap_threshold_m": overlap_threshold_m,
            "overlap_warning_count": 0,
            "overlap_warnings": [],
            "error": "source network file does not exist",
            "source_network_mutation": False,
        }
    if not netconvert_binary.strip():
        raise ValueError("netconvert_binary is required")

    destination.mkdir(parents=True, exist_ok=True)
    source_sha256 = sha256_file(source)
    command = [
        netconvert_binary,
        "--sumo-net-file",
        str(source),
        "--output-file",
        str(normalized_probe),
        "--geometry.check-overlap",
        str(overlap_threshold_m),
        "--no-warnings",
        "false",
    ]
    try:
        command_result = command_runner(
            command,
            cwd=destination,
            timeout_seconds=timeout_seconds,
        )
        command_report = (
            command_result.to_dict()
            if hasattr(command_result, "to_dict")
            else dict(command_result)
        )
    except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
        command_report = {
            "status": "fail",
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "error": f"{type(exc).__name__}: {exc}",
        }

    diagnostic_text = "\n".join(
        str(command_report.get(field, ""))
        for field in ("stdout", "stderr", "error")
        if command_report.get(field)
    )
    warnings = [
        {
            "first_edge_id": match.group("first"),
            "second_edge_id": match.group("second"),
            "overlap_m": float(match.group("distance")),
        }
        for match in SUMO_EDGE_OVERLAP_WARNING_RE.finditer(diagnostic_text)
    ]
    source_unchanged = sha256_file(source) == source_sha256
    command_passed = (
        command_report.get("status") == "pass"
        and command_report.get("returncode") == 0
        and normalized_probe.is_file()
    )
    status = (
        "pass"
        if command_passed and source_unchanged and not warnings
        else "fail"
    )
    return {
        "status": status,
        "audit_engine": "sumo.netconvert.geometry.check-overlap",
        "policy": (
            "SUMO-rendered external-edge overlap warnings fail closed; the normalized "
            "probe is diagnostic only and never replaces the reviewed source"
        ),
        "source_net_file": str(source),
        "source_sha256": source_sha256,
        "source_network_mutation": not source_unchanged,
        "normalized_probe_net_file": str(normalized_probe),
        "overlap_threshold_m": overlap_threshold_m,
        "overlap_warning_count": len(warnings),
        "overlap_warnings": warnings,
        "command": command,
        "command_result": command_report,
    }


def audit_hamburg_native_replay_geometry(
    source_net_file: Path,
    candidate_net_file: Path,
    *,
    replay_report: Mapping[str, Any] | None = None,
    endpoint_tolerance_m: float = 10.0,
    local_endpoint_tolerance_m: float = 35.0,
    boundary_overlap_tolerance_m: float = 7.5,
    unchanged_edge_overlap_tolerance_m: float = 1.0,
    internal_lane_length_limit_m: float = 75.0,
    external_lane_length_tolerance_m: float = 2.0,
) -> dict[str, Any]:
    """Audit compact-corridor geometry with the native replay scope as authority.

    Without a replay report this retains the legacy strict parity gate.  A native
    junction replay is allowed to absorb source edges only when both endpoints are
    members of an explicitly collapsed cell.  Its mapped boundary edges may move
    only at the local endpoint: the remote junction id and coordinate must stay
    fixed, the local displacement is bounded, and the old/new lane centre-lines
    must still overlap.  All other external edges retain strict identity apart
    from bounded SUMO junction-shape trimming along the same centre-line.
    """

    tolerances = {
        "endpoint_tolerance_m": endpoint_tolerance_m,
        "local_endpoint_tolerance_m": local_endpoint_tolerance_m,
        "boundary_overlap_tolerance_m": boundary_overlap_tolerance_m,
        "unchanged_edge_overlap_tolerance_m": unchanged_edge_overlap_tolerance_m,
        "internal_lane_length_limit_m": internal_lane_length_limit_m,
        "external_lane_length_tolerance_m": external_lane_length_tolerance_m,
    }
    if any(not math.isfinite(value) or value < 0 for value in tolerances.values()):
        raise ValueError("geometry audit tolerances must be finite and non-negative")
    source_edge_ids = _external_edge_ids(source_net_file)
    candidate_edge_ids = _external_edge_ids(candidate_net_file)
    common_edge_ids = sorted(source_edge_ids & candidate_edge_ids)
    source_bundle = canonical_road_connectivity_bundle(
        source_net_file,
        seed_edge_ids=common_edge_ids,
        hop_radius=0,
    )
    candidate_bundle = canonical_road_connectivity_bundle(
        candidate_net_file,
        seed_edge_ids=common_edge_ids,
        hop_radius=0,
    )
    parity = compare_road_connectivity_bundles(
        source_bundle,
        candidate_bundle,
        geometry_tolerance=endpoint_tolerance_m,
    )
    mismatches = list(parity.get("common_edge_geometry_mismatches", []))
    missing_edge_ids = sorted(source_edge_ids - candidate_edge_ids)
    unexpected_edge_ids = sorted(candidate_edge_ids - source_edge_ids)
    edge_id_delta_count = len(missing_edge_ids) + len(unexpected_edge_ids)
    result: dict[str, Any] = {
        "status": "fail",
        "audit_engine": "torii.road_connectivity_teacher_model",
        "policy": "strict_external_edge_parity_without_native_replay_scope",
        **tolerances,
        "source_external_edge_count": len(source_edge_ids),
        "candidate_external_edge_count": len(candidate_edge_ids),
        "common_external_edge_count": len(common_edge_ids),
        "external_edge_id_delta_count": edge_id_delta_count,
        "missing_external_edge_ids": missing_edge_ids,
        "unexpected_external_edge_ids": unexpected_edge_ids,
        "geometry_mismatch_count": len(mismatches),
        "geometry_mismatches": mismatches,
        "torii_parity_status": parity.get("status", "blocked"),
        "torii_parity_summary": parity.get("summary", {}),
    }
    if replay_report is None:
        result["status"] = (
            "pass"
            if source_edge_ids and not edge_id_delta_count and not mismatches
            else "fail"
        )
        return result

    replay_scope = _native_replay_geometry_scope(replay_report)
    source_root = ET.parse(source_net_file).getroot()
    candidate_root = ET.parse(candidate_net_file).getroot()
    source_edges = _external_edges_by_id(source_root)
    candidate_edges = _external_edges_by_id(candidate_root)
    source_junctions = _junction_world_coordinates(source_root)
    candidate_junctions = _junction_world_coordinates(candidate_root)

    collapse_ids = replay_scope["collapse_junction_ids"]
    expected_absorbable = sorted(
        edge_id
        for edge_id, edge in source_edges.items()
        if edge.attrib.get("from", "") in collapse_ids
        and edge.attrib.get("to", "") in collapse_ids
    )
    unauthorized_missing = sorted(set(missing_edge_ids) - set(expected_absorbable))
    retained_absorbable = sorted(set(expected_absorbable) - set(missing_edge_ids))

    boundary_checks = [
        _audit_native_boundary_edge(
            edge_id=edge_id,
            source_edges=source_edges,
            candidate_edges=candidate_edges,
            source_junctions=source_junctions,
            candidate_junctions=candidate_junctions,
            collapse_junction_ids=collapse_ids,
            local_endpoint_tolerance_m=local_endpoint_tolerance_m,
            overlap_tolerance_m=boundary_overlap_tolerance_m,
        )
        for edge_id in sorted(replay_scope["mapped_boundary_edge_ids"])
    ]
    boundary_failures = [check for check in boundary_checks if check["status"] != "pass"]

    mapped_boundary_ids = replay_scope["mapped_boundary_edge_ids"]
    unchanged_checks = [
        _audit_unchanged_edge_overlap(
            mismatch=mismatch,
            source_edge=source_edges.get(str(mismatch.get("edge_id", ""))),
            candidate_edge=candidate_edges.get(str(mismatch.get("edge_id", ""))),
            endpoint_tolerance_m=max(endpoint_tolerance_m, 25.0),
            overlap_tolerance_m=unchanged_edge_overlap_tolerance_m,
        )
        for mismatch in mismatches
        if str(mismatch.get("edge_id", "")) not in mapped_boundary_ids
    ]
    unchanged_failures = [check for check in unchanged_checks if check["status"] != "pass"]
    internal_lane_checks = _audit_internal_lane_lengths(
        candidate_root,
        limit_m=internal_lane_length_limit_m,
    )
    external_lane_length_checks = _audit_external_lane_shape_lengths(
        candidate_root,
        tolerance_m=external_lane_length_tolerance_m,
    )

    gate_failures = list(replay_scope["gate_failures"])
    parity_status = str(parity.get("status", "blocked"))
    status = "pass" if all(
        (
            source_edge_ids,
            parity_status == "pass",
            not unauthorized_missing,
            not retained_absorbable,
            not unexpected_edge_ids,
            not boundary_failures,
            not unchanged_failures,
            not internal_lane_checks["over_limit"],
            external_lane_length_checks["status"] == "pass",
            not gate_failures,
        )
    ) else "fail"
    result.update(
        {
            "status": status,
            "policy": (
                "native replay may absorb exactly the source edges whose two endpoints are "
                "inside an explicit collapse cell; mapped boundaries preserve the remote "
                "junction and bounded centre-line overlap; Torii parity, rendered lane-length "
                "consistency, all other edge changes, and long internal shortcuts fail closed"
            ),
            "native_replay_stage_count": replay_scope["stage_count"],
            "collapse_junction_ids": sorted(collapse_ids),
            "expected_absorbable_external_edge_ids": expected_absorbable,
            "authorized_absorbed_external_edge_ids": sorted(
                set(missing_edge_ids) & set(expected_absorbable)
            ),
            "unauthorized_missing_external_edge_ids": unauthorized_missing,
            "retained_absorbable_external_edge_ids": retained_absorbable,
            "unauthorized_edge_id_delta_count": (
                len(unauthorized_missing) + len(unexpected_edge_ids)
            ),
            "mapped_boundary_edge_ids": sorted(mapped_boundary_ids),
            "mapped_boundary_geometry_checks": boundary_checks,
            "mapped_boundary_geometry_failure_count": len(boundary_failures),
            "unchanged_edge_overlap_checks": unchanged_checks,
            "unchanged_edge_overlap_failure_count": len(unchanged_failures),
            "accepted_local_geometry_change_count": (
                len(boundary_checks) + len(unchanged_checks)
                - len(boundary_failures) - len(unchanged_failures)
            ),
            "internal_lane_length_audit": internal_lane_checks,
            "external_lane_shape_length_audit": external_lane_length_checks,
            "native_replay_gate_failures": gate_failures,
        }
    )
    return result


def _native_replay_geometry_scope(replay_report: Mapping[str, Any]) -> dict[str, Any]:
    collapse_ids: set[str] = set()
    mapped_boundary_ids: set[str] = set()
    gate_failures: list[str] = []
    stages = replay_report.get("stage_reports", ())
    if not isinstance(stages, Sequence) or isinstance(stages, (str, bytes)) or not stages:
        return {
            "stage_count": 0,
            "collapse_junction_ids": collapse_ids,
            "mapped_boundary_edge_ids": mapped_boundary_ids,
            "gate_failures": ["native_replay_stage_reports_missing"],
        }
    for index, stage in enumerate(stages, start=1):
        if not isinstance(stage, Mapping):
            gate_failures.append(f"stage_{index}:report_invalid")
            continue
        if stage.get("status") != "pass":
            gate_failures.append(f"stage_{index}:stage_status_not_pass")
        replay = stage.get("native_teacher_replay", {})
        variants = replay.get("variant_reports", ()) if isinstance(replay, Mapping) else ()
        passing = [
            variant
            for variant in variants
            if isinstance(variant, Mapping) and variant.get("status") == "pass"
        ]
        if len(passing) != 1:
            gate_failures.append(f"stage_{index}:unique_passing_variant_missing")
            continue
        variant = passing[0]
        target = variant.get("target_internal_replay", {})
        if not isinstance(target, Mapping) or target.get("status") != "pass":
            gate_failures.append(f"stage_{index}:target_internal_replay_not_pass")
            continue
        collapse_ids.update(str(value) for value in target.get("collapse_junction_ids", ()))
        mapped_boundary_ids.update(
            str(value)
            for value in target.get("preserved_mapped_boundary_geometry_edge_ids", ())
        )
        for field in (
            "boundary_geometry_preservation_failure_count",
            "unanchored_boundary_edge_count",
            "dangling_connection_count",
            "invalid_connection_count",
            "invalid_controlled_connection_count",
        ):
            if int(target.get(field, 0) or 0):
                gate_failures.append(f"stage_{index}:{field}")
        via = variant.get("tls_via_path_semantics", {})
        if not isinstance(via, Mapping) or via.get("status") != "pass":
            gate_failures.append(f"stage_{index}:tls_via_path_semantics_not_pass")
        elif int(via.get("via_path_failure_count", 0) or 0):
            gate_failures.append(f"stage_{index}:tls_via_path_failure")
        routeability = variant.get("routeability_smoke", {})
        if not isinstance(routeability, Mapping) or routeability.get("status") != "pass":
            gate_failures.append(f"stage_{index}:routeability_not_pass")
    if not collapse_ids:
        gate_failures.append("native_replay_collapse_scope_empty")
    if not mapped_boundary_ids:
        gate_failures.append("native_replay_mapped_boundary_scope_empty")
    return {
        "stage_count": len(stages),
        "collapse_junction_ids": collapse_ids,
        "mapped_boundary_edge_ids": mapped_boundary_ids,
        "gate_failures": gate_failures,
    }


def _external_edges_by_id(root: ET.Element) -> dict[str, ET.Element]:
    return {
        edge_id: edge
        for edge in root.findall("edge")
        if (edge_id := edge.attrib.get("id", ""))
        and not edge_id.startswith(":")
        and not edge.attrib.get("function")
    }


def _junction_world_coordinates(root: ET.Element) -> dict[str, tuple[float, float]]:
    offset = _xml_net_offset(root)
    return {
        junction_id: (
            float(junction.attrib.get("x", "0")) - offset[0],
            float(junction.attrib.get("y", "0")) - offset[1],
        )
        for junction in root.findall("junction")
        if (junction_id := junction.attrib.get("id", ""))
        and "x" in junction.attrib
        and "y" in junction.attrib
    }


def _xml_net_offset(root: ET.Element) -> tuple[float, float]:
    location = root.find("location")
    parts = (location.attrib.get("netOffset", "0,0") if location is not None else "0,0").split(",")
    if len(parts) < 2:
        return (0.0, 0.0)
    return (float(parts[0]), float(parts[1]))


def _audit_native_boundary_edge(
    *,
    edge_id: str,
    source_edges: Mapping[str, ET.Element],
    candidate_edges: Mapping[str, ET.Element],
    source_junctions: Mapping[str, tuple[float, float]],
    candidate_junctions: Mapping[str, tuple[float, float]],
    collapse_junction_ids: set[str],
    local_endpoint_tolerance_m: float,
    overlap_tolerance_m: float,
) -> dict[str, Any]:
    source = source_edges.get(edge_id)
    candidate = candidate_edges.get(edge_id)
    if source is None or candidate is None:
        return {"status": "fail", "edge_id": edge_id, "reason": "boundary_edge_missing"}
    source_from = source.attrib.get("from", "")
    source_to = source.attrib.get("to", "")
    local_at_start = source_from in collapse_junction_ids
    local_at_end = source_to in collapse_junction_ids
    if local_at_start == local_at_end:
        return {
            "status": "fail",
            "edge_id": edge_id,
            "reason": "source_boundary_does_not_have_one_local_endpoint",
        }
    remote_attr = "to" if local_at_start else "from"
    local_attr = "from" if local_at_start else "to"
    source_remote = source.attrib.get(remote_attr, "")
    candidate_remote = candidate.attrib.get(remote_attr, "")
    source_local = source.attrib.get(local_attr, "")
    candidate_local = candidate.attrib.get(local_attr, "")
    remote_coordinate_delta = _coordinate_delta(
        source_junctions.get(source_remote), candidate_junctions.get(candidate_remote)
    )
    local_coordinate_delta = _coordinate_delta(
        source_junctions.get(source_local), candidate_junctions.get(candidate_local)
    )
    source_shape = _primary_lane_world_shape(source)
    candidate_shape = _primary_lane_world_shape(candidate)
    overlap = _minimum_directed_shape_distance(source_shape, candidate_shape)
    reasons: list[str] = []
    if source_remote != candidate_remote:
        reasons.append("remote_junction_id_changed")
    if remote_coordinate_delta > 1e-3:
        reasons.append("remote_junction_coordinate_changed")
    if local_coordinate_delta > local_endpoint_tolerance_m:
        reasons.append("local_junction_displacement_exceeds_limit")
    if overlap > overlap_tolerance_m:
        reasons.append("boundary_centreline_overlap_exceeds_limit")
    return {
        "status": "fail" if reasons else "pass",
        "edge_id": edge_id,
        "source_local_junction_id": source_local,
        "candidate_local_junction_id": candidate_local,
        "source_remote_junction_id": source_remote,
        "candidate_remote_junction_id": candidate_remote,
        "remote_coordinate_delta_m": round(remote_coordinate_delta, 6),
        "local_coordinate_delta_m": round(local_coordinate_delta, 6),
        "minimum_directed_shape_distance_m": round(overlap, 6),
        "reasons": reasons,
    }


def _audit_unchanged_edge_overlap(
    *,
    mismatch: Mapping[str, Any],
    source_edge: ET.Element | None,
    candidate_edge: ET.Element | None,
    endpoint_tolerance_m: float,
    overlap_tolerance_m: float,
) -> dict[str, Any]:
    edge_id = str(mismatch.get("edge_id", ""))
    reasons: list[str] = []
    if source_edge is None or candidate_edge is None:
        reasons.append("common_edge_missing")
        overlap = math.inf
    else:
        stable_fields = (
            ("teacher_from", "candidate_from"),
            ("teacher_to", "candidate_to"),
            ("teacher_type", "candidate_type"),
            ("teacher_lane_count", "candidate_lane_count"),
            ("teacher_lane_signature", "candidate_lane_signature"),
        )
        for source_field, candidate_field in stable_fields:
            if mismatch.get(source_field) != mismatch.get(candidate_field):
                reasons.append(f"{source_field}_changed")
        overlap = _minimum_directed_shape_distance(
            _primary_lane_world_shape(source_edge),
            _primary_lane_world_shape(candidate_edge),
        )
        if float(mismatch.get("endpoint_delta", math.inf)) > endpoint_tolerance_m:
            reasons.append("endpoint_extension_exceeds_limit")
        if overlap > overlap_tolerance_m:
            reasons.append("centreline_overlap_exceeds_limit")
    return {
        "status": "fail" if reasons else "pass",
        "edge_id": edge_id,
        "endpoint_delta_m": mismatch.get("endpoint_delta"),
        "minimum_directed_shape_distance_m": round(overlap, 6),
        "reasons": reasons,
    }


def _audit_internal_lane_lengths(root: ET.Element, *, limit_m: float) -> dict[str, Any]:
    rows = []
    for edge in root.findall("edge"):
        if edge.attrib.get("function") != "internal" and not edge.attrib.get("id", "").startswith(":"):
            continue
        for lane in edge.findall("lane"):
            length = float(lane.attrib.get("length", "0") or 0)
            rows.append({"lane_id": lane.attrib.get("id", ""), "length_m": length})
    rows.sort(key=lambda row: (-float(row["length_m"]), str(row["lane_id"])))
    over_limit = [row for row in rows if float(row["length_m"]) > limit_m]
    return {
        "limit_m": limit_m,
        "internal_lane_count": len(rows),
        "maximum_length_m": rows[0]["length_m"] if rows else 0.0,
        "over_limit_count": len(over_limit),
        "over_limit": over_limit,
        "longest_lanes": rows[:10],
    }


def _audit_external_lane_shape_lengths(
    root: ET.Element,
    *,
    tolerance_m: float,
) -> dict[str, Any]:
    """Compare SUMO's declared lane length with its rendered centre-line.

    A native replay can remain XML-valid after replacing ``shape`` while
    accidentally retaining the teacher lane's old ``length``.  The resulting
    network loads, but NetEdit and simulation positions no longer describe the
    same road.  Normal ``netconvert`` output stays within a small trimming and
    rounding tolerance, so larger deltas fail closed.
    """

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function"):
            continue
        for lane in edge.findall("lane"):
            lane_id = lane.attrib.get("id", "")
            row: dict[str, Any] = {
                "edge_id": edge_id,
                "lane_id": lane_id,
                "status": "fail",
                "reasons": [],
            }
            try:
                declared_length = float(lane.attrib["length"])
            except (KeyError, ValueError):
                row["reasons"].append("declared_length_missing_or_invalid")
                failures.append(row)
                rows.append(row)
                continue
            points = _shape_points(lane.attrib.get("shape", ""))
            if len(points) < 2:
                row.update({"declared_length_m": declared_length})
                row["reasons"].append("rendered_shape_missing_or_invalid")
                failures.append(row)
                rows.append(row)
                continue
            rendered_length = sum(
                math.hypot(right[0] - left[0], right[1] - left[1])
                for left, right in zip(points, points[1:])
            )
            delta = abs(declared_length - rendered_length)
            row.update(
                {
                    "declared_length_m": round(declared_length, 6),
                    "rendered_shape_length_m": round(rendered_length, 6),
                    "absolute_delta_m": round(delta, 6),
                }
            )
            if not math.isfinite(declared_length) or declared_length <= 0:
                row["reasons"].append("declared_length_not_positive_finite")
            if not math.isfinite(rendered_length) or rendered_length <= 0:
                row["reasons"].append("rendered_shape_length_not_positive_finite")
            if delta > tolerance_m:
                row["reasons"].append("declared_length_vs_rendered_shape_exceeds_tolerance")
            if row["reasons"]:
                failures.append(row)
            else:
                row["status"] = "pass"
            rows.append(row)
    rows.sort(
        key=lambda row: (
            -float(row.get("absolute_delta_m", math.inf)),
            str(row.get("lane_id", "")),
        )
    )
    failures.sort(
        key=lambda row: (
            -float(row.get("absolute_delta_m", math.inf)),
            str(row.get("lane_id", "")),
        )
    )
    return {
        "status": "pass" if rows and not failures else "fail",
        "tolerance_m": tolerance_m,
        "external_lane_count": len(rows),
        "failure_count": len(failures),
        "failures": failures,
        "largest_deltas": rows[:10],
    }


def _shape_points(shape: str) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for part in shape.split():
        coordinates = part.split(",")
        if len(coordinates) < 2:
            continue
        try:
            points.append((float(coordinates[0]), float(coordinates[1])))
        except ValueError:
            return ()
    return tuple(points)


def _primary_lane_world_shape(edge: ET.Element) -> tuple[tuple[float, float], ...]:
    lanes = sorted(
        edge.findall("lane"),
        key=lambda lane: int(lane.attrib.get("index", "0") or 0),
    )
    shape = lanes[0].attrib.get("shape", "") if lanes else edge.attrib.get("shape", "")
    return _shape_points(shape)


def _minimum_directed_shape_distance(
    first: Sequence[tuple[float, float]],
    second: Sequence[tuple[float, float]],
) -> float:
    if len(first) < 2 or len(second) < 2:
        return math.inf if first != second else 0.0
    return min(
        max(_point_to_polyline_distance(point, second) for point in first),
        max(_point_to_polyline_distance(point, first) for point in second),
    )


def _point_to_polyline_distance(
    point: tuple[float, float],
    shape: Sequence[tuple[float, float]],
) -> float:
    return min(
        _point_to_segment_distance(point, start, end)
        for start, end in zip(shape, shape[1:])
    )


def _point_to_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0.0 and dy == 0.0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    fraction = max(
        0.0,
        min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)),
    )
    projection = (start[0] + fraction * dx, start[1] + fraction * dy)
    return math.hypot(point[0] - projection[0], point[1] - projection[1])


def _coordinate_delta(
    first: tuple[float, float] | None,
    second: tuple[float, float] | None,
) -> float:
    if first is None or second is None:
        return math.inf
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _external_edge_ids(net_file: Path) -> set[str]:
    root = ET.parse(net_file).getroot()
    return {
        edge_id
        for edge in root.findall("edge")
        if (edge_id := edge.attrib.get("id", ""))
        and not edge_id.startswith(":")
        and not edge.attrib.get("function")
    }


def _source_tls_controller_ids(net_file: Path) -> tuple[str, ...]:
    root = ET.parse(net_file).getroot()
    controller_ids = {
        logic.attrib.get("id", "").strip()
        for logic in root.findall("tlLogic")
        if logic.attrib.get("id", "").strip()
    }
    controller_ids.update(
        connection.attrib.get("tl", "").strip()
        for connection in root.findall("connection")
        if connection.attrib.get("tl", "").strip()
    )
    return tuple(sorted(controller_ids))


def retire_compact_scope_non_official_tls(
    *,
    rebuilt_net_file: Path,
    output_net_file: Path,
    replay_report: Mapping[str, Any],
    topology_inventory_audit: Mapping[str, Any],
    source_tls_controller_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Keep only the three replay-proven official controllers in the compact net.

    This is deliberately a fail-closed promotion gate around Torii's reusable
    :func:`demote_tls_ids` operation.  The mutation is authorized only by a
    complete 33-movement OCIT-C/MAP inventory and three successful native replay
    stages.  The native replay artifact is never edited in place.
    """

    rebuilt_net_file = rebuilt_net_file.resolve()
    output_net_file = output_net_file.resolve()
    source_ids = sorted(
        {
            str(controller_id).strip()
            for controller_id in source_tls_controller_ids
            if str(controller_id).strip()
        }
    )
    before_ids = list(_source_tls_controller_ids(rebuilt_net_file))
    before_evidence = _tls_controller_evidence(rebuilt_net_file, before_ids)
    errors: list[str] = []

    movement_count = int(topology_inventory_audit.get("movement_count", 0) or 0)
    control_index_count = int(
        topology_inventory_audit.get("control_index_count", 0) or 0
    )
    movement_counts_by_node = {
        _normalize_node(str(node_id)): int(count or 0)
        for node_id, count in dict(
            topology_inventory_audit.get("movement_counts_by_node", {}) or {}
        ).items()
    }
    if topology_inventory_audit.get("status") != "pass":
        errors.append("OCIT-C/MAP vehicle topology audit is not pass")
    if movement_count != HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT:
        errors.append(
            f"vehicle topology movement_count={movement_count}, expected "
            f"{HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT}"
        )
    if movement_counts_by_node != HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_COUNTS_BY_NODE:
        errors.append(
            "vehicle topology per-node movement counts do not match the official preset"
        )
    if control_index_count != HAMBURG_SANDTORKAI_TOPOLOGY_CONTROL_COUNT:
        errors.append(
            f"vehicle topology control_index_count={control_index_count}, expected "
            f"{HAMBURG_SANDTORKAI_TOPOLOGY_CONTROL_COUNT}"
        )

    stage_rows = replay_report.get("stage_reports", ())
    if not isinstance(stage_rows, (list, tuple)):
        stage_rows = ()
    official_controller_by_node: dict[str, str] = {}
    logical_controller_by_node: dict[str, str] = {}
    if len(stage_rows) != len(HAMBURG_SANDTORKAI_TEACHER_REPLAY_ORDER):
        errors.append(
            f"native replay stage count={len(stage_rows)}, expected "
            f"{len(HAMBURG_SANDTORKAI_TEACHER_REPLAY_ORDER)}"
        )
    for position, node_id in enumerate(HAMBURG_SANDTORKAI_TEACHER_REPLAY_ORDER):
        if position >= len(stage_rows) or not isinstance(stage_rows[position], Mapping):
            continue
        stage = stage_rows[position]
        logical_controller_id = str(stage.get("controller_id", "")).strip()
        expected_logical_controller_id = f"HH_{node_id}"
        if stage.get("status") != "pass":
            errors.append(f"native replay stage for {node_id} is not pass")
        if not logical_controller_id:
            errors.append(f"native replay stage for {node_id} has no controller_id")
            continue
        logical_controller_by_node[node_id] = logical_controller_id
        if logical_controller_id != expected_logical_controller_id:
            errors.append(
                f"native replay stage {position + 1} declares {logical_controller_id!r}, "
                f"expected {expected_logical_controller_id!r}"
            )

        native_replay = stage.get("native_teacher_replay", {})
        if not isinstance(native_replay, Mapping) or native_replay.get("status") != "pass":
            errors.append(f"native teacher replay evidence for {node_id} is not pass")
            continue
        variants = native_replay.get("variant_reports", ())
        if not isinstance(variants, (list, tuple)):
            variants = ()
        successful_variants = [
            variant
            for variant in variants
            if isinstance(variant, Mapping) and variant.get("status") == "pass"
        ]
        if len(successful_variants) != 1:
            errors.append(
                f"native replay for {node_id} has {len(successful_variants)} successful "
                "candidate variants, expected exactly 1"
            )
            continue
        variant = successful_variants[0]
        target_replay = variant.get("target_internal_replay", {})
        if not isinstance(target_replay, Mapping) or target_replay.get("status") != "pass":
            errors.append(f"target internal replay evidence for {node_id} is not pass")
            continue
        physical_controller_id = str(
            target_replay.get("candidate_controller_id")
            or target_replay.get("junction_id")
            or ""
        ).strip()
        scoped_plan = variant.get("scoped_tls_cell_plan", {})
        plan_controller_id = (
            str(scoped_plan.get("candidate_junction_id", "")).strip()
            if isinstance(scoped_plan, Mapping)
            else ""
        )
        if not physical_controller_id:
            errors.append(f"native replay for {node_id} has no physical candidate controller id")
            continue
        if plan_controller_id and plan_controller_id != physical_controller_id:
            errors.append(
                f"native replay candidate controller mismatch for {node_id}: "
                f"target={physical_controller_id!r}, plan={plan_controller_id!r}"
            )
        official_controller_by_node[node_id] = physical_controller_id

    official_ids = sorted(set(official_controller_by_node.values()))
    if len(official_ids) != len(HAMBURG_SANDTORKAI_NODE_IDS):
        errors.append(
            f"unique official controller count={len(official_ids)}, expected "
            f"{len(HAMBURG_SANDTORKAI_NODE_IDS)}"
        )
    for controller_id in official_ids:
        evidence = before_evidence.get(controller_id, {})
        if int(evidence.get("tl_logic_count", 0) or 0) < 1:
            errors.append(f"official controller {controller_id!r} has no tlLogic")
        if int(evidence.get("controlled_connection_count", 0) or 0) < 1:
            errors.append(f"official controller {controller_id!r} has no controlled connection")
        if int(evidence.get("missing_link_index_count", 0) or 0):
            errors.append(
                f"official controller {controller_id!r} has connections without linkIndex"
            )

    retirement_candidates = sorted(set(before_ids) - set(official_ids))
    report: dict[str, Any] = {
        "status": "fail",
        "authorization_policy": (
            "retire every non-official TLS controller in the compact corridor only after "
            "the complete 33-movement OCIT-C/MAP inventory and all three native teacher "
            "replay stages pass"
        ),
        "scope": "entire supplied compact SUMO network",
        "input_net_file": str(rebuilt_net_file),
        "output_net_file": str(output_net_file),
        "input_net_sha256_before": sha256_file(rebuilt_net_file),
        "expected_official_controller_count": len(HAMBURG_SANDTORKAI_NODE_IDS),
        "logical_controller_by_node": logical_controller_by_node,
        "official_controller_by_node": official_controller_by_node,
        "official_controller_ids": official_ids,
        "source_controller_ids": source_ids,
        "source_controller_ids_reused_by_official_replay": sorted(
            set(source_ids) & set(official_ids)
        ),
        "before_controller_count": len(before_ids),
        "before_controller_ids": before_ids,
        "before_controller_evidence": before_evidence,
        "retirement_candidate_count": len(retirement_candidates),
        "retirement_candidate_ids": retirement_candidates,
        "retired_controller_ids": [],
        "after_controller_count": 0,
        "after_controller_ids": [],
        "after_controller_evidence": {},
        "remaining_non_official_controller_ids": [],
        "topology_authorization_evidence": {
            "status": topology_inventory_audit.get("status"),
            "movement_count": movement_count,
            "movement_counts_by_node": movement_counts_by_node,
            "control_index_count": control_index_count,
        },
        "native_replay_authorization_evidence": {
            "status": replay_report.get("status"),
            "stage_count": len(stage_rows),
            "stage_statuses": [
                stage.get("status") if isinstance(stage, Mapping) else "invalid"
                for stage in stage_rows
            ],
        },
        "errors": errors,
    }
    if replay_report.get("status") != "pass":
        errors.append("native replay workflow is not pass")
    if rebuilt_net_file == output_net_file:
        errors.append("TLS retirement output must not overwrite the native replay artifact")
    if errors:
        return report

    output_net_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rebuilt_net_file, output_net_file)
    demotion = demote_tls_ids(output_net_file, retirement_candidates)
    after_ids = list(_source_tls_controller_ids(output_net_file))
    after_evidence = _tls_controller_evidence(output_net_file, after_ids)
    remaining_non_official = sorted(set(after_ids) - set(official_ids))
    missing_official = sorted(set(official_ids) - set(after_ids))
    retired_ids = sorted(set(before_ids) - set(after_ids))

    if remaining_non_official:
        errors.append(
            f"non-official controllers remain after retirement: {remaining_non_official}"
        )
    if missing_official:
        errors.append(f"official controllers were removed: {missing_official}")
    if set(after_ids) != set(official_ids):
        errors.append(
            f"post-retirement controller inventory has {len(after_ids)} ids, expected exactly "
            f"{len(official_ids)} official ids"
        )
    for controller_id in official_ids:
        evidence = after_evidence.get(controller_id, {})
        if int(evidence.get("tl_logic_count", 0) or 0) < 1:
            errors.append(f"official controller {controller_id!r} lost its tlLogic")
        if int(evidence.get("controlled_connection_count", 0) or 0) < 1:
            errors.append(f"official controller {controller_id!r} lost all controlled connections")
        if int(evidence.get("missing_link_index_count", 0) or 0):
            errors.append(
                f"official controller {controller_id!r} has post-retirement linkIndex gaps"
            )
    if sha256_file(rebuilt_net_file) != report["input_net_sha256_before"]:
        errors.append("native replay artifact changed during compact-scope TLS retirement")

    report.update(
        {
            "status": "pass" if not errors else "fail",
            "demotion": demotion,
            "retired_controller_ids": retired_ids,
            "after_controller_count": len(after_ids),
            "after_controller_ids": after_ids,
            "after_controller_evidence": after_evidence,
            "remaining_non_official_controller_ids": remaining_non_official,
            "missing_official_controller_ids": missing_official,
            "source_controller_ids_retired": sorted(set(source_ids) & set(retired_ids)),
            "input_net_sha256_after": sha256_file(rebuilt_net_file),
            "output_net_sha256": sha256_file(output_net_file),
            "errors": errors,
        }
    )
    return report


def _tls_controller_evidence(
    net_file: Path,
    controller_ids: Sequence[str],
) -> dict[str, dict[str, int]]:
    root = ET.parse(net_file).getroot()
    logics = root.findall("tlLogic")
    connections = root.findall("connection")
    return {
        controller_id: {
            "tl_logic_count": sum(
                logic.attrib.get("id", "").strip() == controller_id for logic in logics
            ),
            "connection_count": sum(
                connection.attrib.get("tl", "").strip() == controller_id
                for connection in connections
            ),
            "controlled_connection_count": sum(
                connection.attrib.get("tl", "").strip() == controller_id
                and connection.attrib.get("linkIndex") is not None
                for connection in connections
            ),
            "missing_link_index_count": sum(
                connection.attrib.get("tl", "").strip() == controller_id
                and connection.attrib.get("linkIndex") is None
                for connection in connections
            ),
        }
        for controller_id in controller_ids
    }


def _validate_inputs(
    *,
    source_net_file: Path,
    signal_asset_dir: Path,
    netconvert_binary: str,
    sumo_binary: str,
    timeout_seconds: float,
) -> None:
    if not source_net_file.is_file():
        raise ValueError(f"source_net_file must point to an existing file: {source_net_file}")
    if not signal_asset_dir.is_dir():
        raise ValueError(f"signal_asset_dir must point to an existing directory: {signal_asset_dir}")
    if not netconvert_binary.strip():
        raise ValueError("netconvert_binary is required")
    if not sumo_binary.strip():
        raise ValueError("sumo_binary is required")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    missing = [
        str(signal_asset_dir / filename)
        for node_id in HAMBURG_SANDTORKAI_NODE_IDS
        for filename in (f"{node_id}_map_xml.xml", f"{node_id}_ocit_xml.xml")
        if not (signal_asset_dir / filename).is_file()
    ]
    if missing:
        raise ValueError("required cached Hamburg signal assets are missing: " + ", ".join(missing))


def _validate_asset_node(
    *,
    expected_node_id: str,
    map_lanes: Sequence[MapLane],
    map_connections: Sequence[MapConnection],
    ocit_config: OcitCConfig,
) -> None:
    expected = _normalize_node(expected_node_id)
    if not map_lanes:
        raise ValueError(f"MAP asset for node {expected_node_id} contains no lanes")
    if not map_connections:
        raise ValueError(f"MAP asset for node {expected_node_id} contains no connections")
    map_nodes = {
        _normalize_node(row.node_id) for row in (*map_lanes, *map_connections) if row.node_id
    }
    if map_nodes != {expected}:
        raise ValueError(
            f"MAP asset filename node {expected_node_id} does not match parsed nodes {sorted(map_nodes)}"
        )
    if _normalize_node(ocit_config.node_id) != expected:
        raise ValueError(
            f"OCIT asset filename node {expected_node_id} does not match parsed node "
            f"{ocit_config.node_id!r}"
        )


def _validate_primary_stream_inventory(streams: Sequence[SignalStream]) -> None:
    primary = [stream for stream in streams if stream.layer_name == "primary_signal"]
    stream_ids = [stream.stream_id for stream in primary]
    if len(primary) != HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT:
        raise ValueError(
            f"official snapshot contains {len(primary)} primary streams; expected "
            f"{HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT}"
        )
    if len(set(stream_ids)) != len(stream_ids):
        raise ValueError("official primary snapshot contains duplicate stream ids")
    nodes = {_normalize_node(stream.node_id) for stream in primary}
    expected_nodes = set(HAMBURG_SANDTORKAI_NODE_IDS)
    if nodes != expected_nodes:
        raise ValueError(
            f"official primary snapshot nodes {sorted(nodes)} do not match {sorted(expected_nodes)}"
        )
    snapshot_groups = {
        (_normalize_node(stream.node_id), _normalize_group(stream.signal_group))
        for stream in primary
    }
    declared_groups = {
        (node_id, group)
        for node_id, groups in HAMBURG_SANDTORKAI_GROUP_INDEX_BY_NODE.items()
        for group in groups
    }
    undeclared_groups = sorted(snapshot_groups - declared_groups)
    if undeclared_groups:
        raise ValueError(
            "official primary snapshot contains groups without a declared shared linkIndex: "
            + ", ".join(f"{node}/{group}" for node, group in undeclared_groups)
        )


def _audit_observed_ocit_motor_group_subset(
    streams: Sequence[SignalStream],
    ocit_configs: Sequence[OcitCConfig],
) -> dict[str, Any]:
    snapshot_groups = {
        (_normalize_node(stream.node_id), _normalize_group(stream.signal_group))
        for stream in streams
        if stream.layer_name == "primary_signal"
    }
    ocit_groups = {
        (_normalize_node(config.node_id), _normalize_group(group_id))
        for config in ocit_configs
        for group_id in config.motor_group_ids
    }
    unobserved_ocit_groups = sorted(ocit_groups - snapshot_groups)
    absent_from_ocit = sorted(snapshot_groups - ocit_groups)
    errors = []
    if absent_from_ocit:
        errors.append(
            "TLD snapshot groups absent from OCIT: "
            + ", ".join(f"{node}/{group}" for node, group in absent_from_ocit)
        )
    return {
        "status": "pass" if not errors else "fail",
        "policy": (
            "the 27-stream TLD snapshot is an observed subset cross-check only; topology "
            "completeness comes from OCIT-C movements joined to official MAP vehicle lanes"
        ),
        "snapshot_group_count": len(snapshot_groups),
        "ocit_motor_group_count": len(ocit_groups),
        "snapshot_group_counts_by_node": dict(
            sorted(Counter(node for node, _group in snapshot_groups).items())
        ),
        "ocit_motor_group_counts_by_node": dict(
            sorted(Counter(node for node, _group in ocit_groups).items())
        ),
        "snapshot_groups": [
            {"node_id": node, "signal_group": group}
            for node, group in sorted(snapshot_groups)
        ],
        "ocit_motor_groups": [
            {"node_id": node, "signal_group": group}
            for node, group in sorted(ocit_groups)
        ],
        "ocit_groups_unobserved_in_tld_snapshot": [
            {"node_id": node, "signal_group": group}
            for node, group in unobserved_ocit_groups
        ],
        "snapshot_groups_absent_from_ocit": [
            {"node_id": node, "signal_group": group}
            for node, group in absent_from_ocit
        ],
        "errors": errors,
    }


def _audit_vehicle_topology_inventory(
    inventory: OcitVehicleTopologyInventory,
    control_index_by_node: dict[str, dict[str, int]],
) -> dict[str, Any]:
    movement_counts_by_node = dict(
        sorted(
            Counter(_normalize_node(movement.node_id) for movement in inventory.movements).items()
        )
    )
    topology_stream_ids = [stream.stream_id for stream in inventory.topology_streams]
    control_count = sum(len(indices) for indices in control_index_by_node.values())
    control_counts_by_node = {
        _normalize_node(node_id): len(indices)
        for node_id, indices in control_index_by_node.items()
    }
    errors: list[str] = []
    if inventory.status != "pass":
        errors.append(f"inventory status is {inventory.status!r}")
    if inventory.source_movement_count != HAMBURG_SANDTORKAI_OCIT_SOURCE_MOVEMENT_COUNT:
        errors.append(
            f"source_movement_count={inventory.source_movement_count}, expected "
            f"{HAMBURG_SANDTORKAI_OCIT_SOURCE_MOVEMENT_COUNT}"
        )
    if (
        inventory.excluded_non_vehicle_movement_count
        != HAMBURG_SANDTORKAI_EXCLUDED_NON_VEHICLE_MOVEMENT_COUNT
    ):
        errors.append(
            "excluded_non_vehicle_movement_count="
            f"{inventory.excluded_non_vehicle_movement_count}, expected "
            f"{HAMBURG_SANDTORKAI_EXCLUDED_NON_VEHICLE_MOVEMENT_COUNT}"
        )
    if inventory.movement_count != HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT:
        errors.append(
            f"movement_count={inventory.movement_count}, expected "
            f"{HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT}"
        )
    if len(inventory.movements) != HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT:
        errors.append(
            f"movement row count={len(inventory.movements)}, expected "
            f"{HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT}"
        )
    if len(inventory.topology_streams) != HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT:
        errors.append(
            f"topology stream count={len(inventory.topology_streams)}, expected "
            f"{HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT}"
        )
    if len(set(topology_stream_ids)) != len(topology_stream_ids):
        errors.append("topology stream ids are not unique")
    if movement_counts_by_node != HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_COUNTS_BY_NODE:
        errors.append(
            f"movement counts by node={movement_counts_by_node}, expected "
            f"{HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_COUNTS_BY_NODE}"
        )
    if control_count != HAMBURG_SANDTORKAI_TOPOLOGY_CONTROL_COUNT:
        errors.append(
            f"topology control index count={control_count}, expected "
            f"{HAMBURG_SANDTORKAI_TOPOLOGY_CONTROL_COUNT}"
        )
    if control_counts_by_node != HAMBURG_SANDTORKAI_TOPOLOGY_CONTROL_COUNTS_BY_NODE:
        errors.append(
            f"topology control counts by node={control_counts_by_node}, expected "
            f"{HAMBURG_SANDTORKAI_TOPOLOGY_CONTROL_COUNTS_BY_NODE}"
        )
    if inventory.observed_stream_count != HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT:
        errors.append(
            f"observed_stream_count={inventory.observed_stream_count}, expected "
            f"{HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT}"
        )
    if inventory.observed_match_count != HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT:
        errors.append(
            f"observed_match_count={inventory.observed_match_count}, expected "
            f"{HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT}"
        )
    return {
        "status": "pass" if not errors else "fail",
        "topology_source": "OCIT-C movements joined to official MAP vehicle lanes",
        "expected_movement_count": HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT,
        "source_movement_count": inventory.source_movement_count,
        "excluded_non_vehicle_movement_count": (
            inventory.excluded_non_vehicle_movement_count
        ),
        "movement_count": inventory.movement_count,
        "topology_stream_count": len(inventory.topology_streams),
        "movement_counts_by_node": movement_counts_by_node,
        "control_index_count": control_count,
        "control_index_counts_by_node": control_counts_by_node,
        "observation_source": "versioned TLD primary_signal metadata snapshot",
        "expected_observation_stream_count": HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT,
        "observed_stream_count": inventory.observed_stream_count,
        "observed_match_count": inventory.observed_match_count,
        "errors": errors,
    }


def _audit_required_lane_bindings(
    streams: Sequence[SignalStream],
    bindings: Sequence[MapLaneBinding],
) -> dict[str, Any]:
    required = {
        (_normalize_node(stream.node_id), lane_id)
        for stream in streams
        if stream.layer_name == "primary_signal"
        for lane_id in (stream.ingress_lane_id, stream.egress_lane_id)
    }
    indexed: dict[tuple[str, str], list[MapLaneBinding]] = {}
    for binding in bindings:
        indexed.setdefault(
            (_normalize_node(binding.node_id), binding.map_lane_id), []
        ).append(binding)
    missing = sorted(required - set(indexed))
    duplicate = sorted(key for key in required if len(indexed.get(key, ())) > 1)
    inactive = sorted(
        key
        for key in required
        if len(indexed.get(key, ())) == 1
        and (
            indexed[key][0].mapping_status != "active"
            or not indexed[key][0].sumo_lane
            or not indexed[key][0].sumo_edge
        )
    )
    return {
        "status": "pass" if not missing and not duplicate and not inactive else "fail",
        "required_lane_count": len(required),
        "active_required_lane_count": len(required) - len(missing) - len(duplicate) - len(inactive),
        "missing": [_lane_key_dict(key) for key in missing],
        "duplicate_or_non_unique": [_lane_key_dict(key) for key in duplicate],
        "inactive": [_lane_key_dict(key) for key in inactive],
    }


def _project_contract_lane_bindings(
    *,
    net_file: Path,
    map_lanes: Sequence[MapLane],
    nearest_bindings: Sequence[MapLaneBinding],
    contracts: Sequence[HamburgTeacherCellContract],
) -> tuple[list[MapLaneBinding], dict[str, Any]]:
    """Project final MAP lanes through the teacher contract that built them.

    Nearest-lane matching remains as diagnostic evidence, but it cannot override
    an injective teacher lane created by the official MAP/OCIT replay.  Every
    projected target is re-measured against the final network and any unexpected
    identity, geometry, or cardinality change fails closed.
    """

    errors: list[str] = []
    target_by_key: dict[tuple[str, str], str] = {}
    target_evidence: dict[tuple[str, str], dict[str, Any]] = {}
    for contract in contracts:
        node_id = _normalize_node(contract.ir.control.tls_id)
        for lane_index in contract.lane_indices:
            key = (node_id, lane_index.official_lane_id)
            target_lane = f"{lane_index.candidate_edge_id}_{lane_index.teacher_lane_index}"
            previous = target_by_key.setdefault(key, target_lane)
            if previous != target_lane:
                errors.append(
                    f"teacher contract maps {key} to both {previous!r} and {target_lane!r}"
                )
                continue
            target_evidence[key] = {
                "direction": lane_index.direction,
                "teacher_edge_id": lane_index.teacher_edge_id,
                "teacher_lane_index": lane_index.teacher_lane_index,
                "candidate_edge_id": lane_index.candidate_edge_id,
                "candidate_lane_id_before_replay": lane_index.candidate_lane_id,
                "projected_final_lane_id": target_lane,
            }
    if len(target_by_key) != HAMBURG_SANDTORKAI_REQUIRED_MAP_LANE_COUNT:
        errors.append(
            f"teacher contract lane count={len(target_by_key)}, expected "
            f"{HAMBURG_SANDTORKAI_REQUIRED_MAP_LANE_COUNT}"
        )

    explicit_rows = bind_map_lanes_to_explicit_network_lanes(
        net_file,
        map_lanes,
        target_by_key,
    )
    explicit_by_key: dict[tuple[str, str], list[MapLaneBinding]] = {}
    for binding in explicit_rows:
        explicit_by_key.setdefault(
            (_normalize_node(binding.node_id), binding.map_lane_id), []
        ).append(binding)
    nearest_by_key: dict[tuple[str, str], list[MapLaneBinding]] = {}
    for binding in nearest_bindings:
        nearest_by_key.setdefault(
            (_normalize_node(binding.node_id), binding.map_lane_id), []
        ).append(binding)

    projected_by_key: dict[tuple[str, str], MapLaneBinding] = {}
    closure_exceptions: list[dict[str, Any]] = []
    projection_rows: list[dict[str, Any]] = []
    drift_rows: list[dict[str, Any]] = []
    for key, target_lane in sorted(target_by_key.items()):
        explicit = explicit_by_key.get(key, ())
        nearest = nearest_by_key.get(key, ())
        if len(explicit) != 1:
            errors.append(f"explicit final binding count for {key} is {len(explicit)}, expected 1")
            continue
        if len(nearest) != 1:
            errors.append(f"nearest final binding count for {key} is {len(nearest)}, expected 1")
        binding = explicit[0]
        exception_reason = ""
        if binding.mapping_status != "active":
            allowed_closure_target = HAMBURG_SANDTORKAI_STATIC_CLOSURE_LANE_TARGETS.get(key)
            if (
                target_lane == allowed_closure_target
                and binding.sumo_lane == allowed_closure_target
                and binding.distance_m <= 8.0
                and binding.heading_error_deg is not None
                and binding.heading_error_deg <= 120.0
            ):
                exception_reason = (
                    "official MAP/OCIT C10 construction branch retained as static topology; "
                    "the dated demand package must keep this branch at zero flow"
                )
                binding = replace(
                    binding,
                    mapping_confidence="medium",
                    mapping_status="active",
                )
                closure_exceptions.append(
                    {
                        "node_id": key[0],
                        "map_lane_id": key[1],
                        "sumo_lane": binding.sumo_lane,
                        "distance_m": binding.distance_m,
                        "heading_error_deg": binding.heading_error_deg,
                        "reason": exception_reason,
                    }
                )
            else:
                errors.append(
                    f"contract projection for {key} is {binding.mapping_status}: "
                    f"target={target_lane!r}, distance={binding.distance_m:.3f}, "
                    f"heading_error={binding.heading_error_deg!r}"
                )
        if binding.sumo_lane != target_lane:
            errors.append(
                f"contract projection for {key} resolved {binding.sumo_lane!r}, "
                f"expected {target_lane!r}"
            )
        projected_by_key[key] = binding
        nearest_lane = nearest[0].sumo_lane if len(nearest) == 1 else ""
        row = {
            "node_id": key[0],
            "map_lane_id": key[1],
            **target_evidence[key],
            "nearest_final_lane_id": nearest_lane,
            "projected_final_lane_id": binding.sumo_lane,
            "distance_m": binding.distance_m,
            "heading_error_deg": binding.heading_error_deg,
            "mapping_confidence": binding.mapping_confidence,
            "mapping_status": binding.mapping_status,
            "closure_exception": bool(exception_reason),
        }
        projection_rows.append(row)
        if nearest_lane != binding.sumo_lane:
            drift_rows.append(row)

    merged: list[MapLaneBinding] = []
    merged_keys: set[tuple[str, str]] = set()
    for binding in nearest_bindings:
        key = (_normalize_node(binding.node_id), binding.map_lane_id)
        replacement = projected_by_key.get(key)
        merged.append(replacement or binding)
        if replacement is not None:
            merged_keys.add(key)
    missing_from_nearest = sorted(set(projected_by_key) - merged_keys)
    if missing_from_nearest:
        errors.append(
            f"projected MAP lanes are absent from nearest-binding inventory: {missing_from_nearest}"
        )
        merged.extend(projected_by_key[key] for key in missing_from_nearest)

    report = {
        "status": "pass" if not errors else "fail",
        "policy": (
            "official MAP/OCIT teacher contracts own final lane identity; generic nearest-lane "
            "projection is retained as drift evidence and cannot override replayed topology"
        ),
        "expected_projected_lane_count": HAMBURG_SANDTORKAI_REQUIRED_MAP_LANE_COUNT,
        "projected_lane_count": len(projected_by_key),
        "active_projected_lane_count": sum(
            binding.mapping_status == "active" for binding in projected_by_key.values()
        ),
        "generic_projection_drift_count": len(drift_rows),
        "generic_projection_drift": drift_rows,
        "static_closure_exception_count": len(closure_exceptions),
        "static_closure_exceptions": closure_exceptions,
        "projection_rows": projection_rows,
        "errors": errors,
    }
    return merged, report


def _replay_tls_control_evidence(
    contracts: Sequence[HamburgTeacherCellContract],
    replay_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Read control keys/link indices and numeric owners from replay evidence."""

    errors: list[str] = []
    contract_by_node: dict[str, HamburgTeacherCellContract] = {}
    for contract in contracts:
        node = _normalize_node(contract.ir.control.tls_id or contract.ir.core.core_id)
        if node in contract_by_node:
            errors.append(f"duplicate teacher contract for node {node}")
        contract_by_node[node] = contract
    stages = replay_report.get("stage_reports", ())
    stage_by_node: dict[str, Mapping[str, Any]] = {}
    if not isinstance(stages, (list, tuple)):
        stages = ()
    for stage in stages:
        if not isinstance(stage, Mapping):
            errors.append("native replay contains a non-object stage")
            continue
        node = _normalize_node(str(stage.get("controller_id", "")))
        if node in stage_by_node:
            errors.append(f"duplicate native replay stage for node {node}")
        stage_by_node[node] = stage
    expected_nodes = set(HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_COUNTS_BY_NODE)
    if set(contract_by_node) != expected_nodes:
        errors.append(
            f"teacher nodes={sorted(contract_by_node)}, expected {sorted(expected_nodes)}"
        )
    if set(stage_by_node) != expected_nodes:
        errors.append(
            f"replay nodes={sorted(stage_by_node)}, expected {sorted(expected_nodes)}"
        )

    owners: dict[str, str] = {}
    controls: dict[str, dict[str, list[int]]] = {}
    logical_by_node: dict[str, str] = {}
    for node in sorted(expected_nodes):
        contract = contract_by_node.get(node)
        stage = stage_by_node.get(node)
        if contract is None or stage is None:
            continue
        logical = str(contract.ir.control.tls_id or contract.ir.core.core_id).strip()
        logical_by_node[node] = logical
        if stage.get("status") != "pass" or str(stage.get("controller_id", "")).strip() != logical:
            errors.append(f"native replay stage for {node} does not pass as {logical!r}")
        material = stage.get("teacher_materialization", {})
        grouping = material.get("tls_signal_grouping_report", {}) if isinstance(material, Mapping) else {}
        raw_owners = (
            grouping.get("tls_signal_grouping_control_key_to_link_indices", {})
            if isinstance(grouping, Mapping)
            else {}
        )
        raw = raw_owners.get(logical, {}) if isinstance(raw_owners, Mapping) else {}
        node_controls: dict[str, list[int]] = {}
        if not isinstance(grouping, Mapping) or grouping.get("status") != "pass" or not isinstance(raw, Mapping):
            errors.append(f"teacher grouping evidence for {node} is not pass")
        else:
            for key, values in raw.items():
                try:
                    indices = sorted({int(value) for value in values})
                except (TypeError, ValueError):
                    indices = []
                if not indices or indices[0] < 0:
                    errors.append(f"replay control {node}/{key} has invalid link indices")
                else:
                    node_controls[str(key)] = indices
        if set(node_controls) != set(contract.expression_index_by_key):
            errors.append(
                f"replay control keys for {node} do not equal the teacher contract"
            )
        controls[node] = node_controls

        native = stage.get("native_teacher_replay", {})
        variants = native.get("variant_reports", ()) if isinstance(native, Mapping) else ()
        successful = [
            row for row in variants
            if isinstance(row, Mapping) and row.get("status") == "pass"
        ]
        if len(successful) != 1:
            errors.append(f"native replay for {node} has {len(successful)} passing variants")
            continue
        target = successful[0].get("target_internal_replay", {})
        if not isinstance(target, Mapping) or target.get("status") != "pass":
            errors.append(f"target replay for {node} is not pass")
            continue
        owner = str(target.get("candidate_controller_id") or target.get("junction_id") or "").strip()
        if not owner:
            errors.append(f"target replay for {node} has no physical controller")
        else:
            owners[node] = owner
    if len(set(owners.values())) != len(expected_nodes):
        errors.append(
            f"unique numeric replay controller count={len(set(owners.values()))}, "
            f"expected {len(expected_nodes)}"
        )
    return {
        "status": "pass" if not errors else "fail",
        "logical_controller_by_node": logical_by_node,
        "official_controller_by_node": owners,
        "link_indices_by_control": controls,
        "errors": errors,
    }


def audit_hamburg_official_movement_endpoints(
    *,
    net_file: Path,
    topology_inventory: OcitVehicleTopologyInventory,
    lane_bindings: Sequence[MapLaneBinding],
    contracts: Sequence[HamburgTeacherCellContract],
    replay_report: Mapping[str, Any],
    max_path_hops: int = 8,
    max_path_span_m: float = 200.0,
) -> dict[str, Any]:
    """Prove all 33 official movements against unique final physical TLS arcs."""

    evidence = _replay_tls_control_evidence(contracts, replay_report)
    errors = list(evidence["errors"])
    owners = evidence["official_controller_by_node"]
    controls = evidence["link_indices_by_control"]
    bindings: dict[tuple[str, str], list[MapLaneBinding]] = {}
    for binding in lane_bindings:
        bindings.setdefault((_normalize_node(binding.node_id), binding.map_lane_id), []).append(binding)
    contract_lanes: dict[tuple[str, str, str], list[str]] = {}
    for contract in contracts:
        node = _normalize_node(contract.ir.control.tls_id or contract.ir.core.core_id)
        for lane in contract.lane_indices:
            contract_lanes.setdefault(
                (node, str(lane.direction), str(lane.official_lane_id)), []
            ).append(f"{lane.candidate_edge_id}_{lane.teacher_lane_index}")
    try:
        graph, lane_ids = build_local_lane_graph(net_file)
        root = ET.parse(net_file).getroot()
    except (ET.ParseError, OSError, ValueError) as exc:
        graph, lane_ids, root = {}, frozenset(), ET.Element("net")
        errors.append(f"invalid final lane graph: {type(exc).__name__}: {exc}")
    logic_ids = {row.attrib.get("id", "") for row in root.findall("tlLogic")}
    final_control_keys: set[tuple[str, int]] = set()
    for connection in root.findall("connection"):
        owner = connection.attrib.get("tl", "").strip()
        raw_index = connection.attrib.get("linkIndex")
        if owner and raw_index not in (None, ""):
            try:
                final_control_keys.add((owner, int(raw_index)))
            except ValueError:
                errors.append(f"controller {owner!r} has invalid final linkIndex")
    for node, owner in owners.items():
        if owner not in logic_ids:
            errors.append(f"numeric replay controller {owner!r} for {node} has no tlLogic")

    rows: list[dict[str, Any]] = []
    official_keys: list[tuple[str, str, str, str]] = []
    for movement in topology_inventory.movements:
        node = _normalize_node(movement.node_id)
        official_key = (
            node, movement.connection_id, movement.ingress_lane_id, movement.egress_lane_id
        )
        official_keys.append(official_key)
        row_errors: list[str] = []
        ingress_rows = bindings.get((node, movement.ingress_lane_id), ())
        egress_rows = bindings.get((node, movement.egress_lane_id), ())
        ingress = ingress_rows[0] if len(ingress_rows) == 1 else None
        egress = egress_rows[0] if len(egress_rows) == 1 else None
        if ingress is None or egress is None:
            row_errors.append("official endpoints lack unique MAP bindings")
        elif ingress.mapping_status != "active" or egress.mapping_status != "active":
            row_errors.append("official endpoint binding is not active")
        expected_ingress = contract_lanes.get((node, "ingress", movement.ingress_lane_id), ())
        expected_egress = contract_lanes.get((node, "egress", movement.egress_lane_id), ())
        if len(expected_ingress) != 1 or len(expected_egress) != 1:
            row_errors.append("teacher contract endpoint is not unique")
        elif ingress is not None and egress is not None and (
            ingress.sumo_lane != expected_ingress[0] or egress.sumo_lane != expected_egress[0]
        ):
            row_errors.append("MAP endpoint differs from replay teacher contract")
        expected_owner = owners.get(node, "")
        allowed_indices = controls.get(node, {}).get(movement.topology_control_key, [])
        if not expected_owner or not allowed_indices:
            row_errors.append("replay control evidence is incomplete")

        path_lanes: list[str] = []
        controlled_from = controlled_to = actual_owner = ""
        actual_index: int | None = None
        if not row_errors and ingress is not None and egress is not None:
            paths, overflow = find_local_lane_paths(
                graph, ingress.sumo_lane, egress.sumo_lane,
                max_hops=max_path_hops, max_span_m=max_path_span_m, max_paths=64,
            )
            if overflow or len(paths) != 1:
                row_errors.append(
                    "final movement path is not unique"
                    if not overflow else "final movement path search overflowed"
                )
            else:
                path = paths[0]
                path_lanes = [ingress.sumo_lane, *(arc.to_lane for arc in path)]
                controlled = [arc for arc in path if arc.tls_id and arc.link_index is not None]
                if len(controlled) != 1:
                    row_errors.append(
                        f"movement path has {len(controlled)} controlled arcs, expected 1"
                    )
                else:
                    arc = controlled[0]
                    controlled_from, controlled_to = arc.from_lane, arc.to_lane
                    actual_owner, actual_index = arc.tls_id, arc.link_index
                    if actual_owner != expected_owner:
                        row_errors.append(
                            f"controller {actual_owner!r} != replay controller {expected_owner!r}"
                        )
                    if actual_index not in allowed_indices:
                        row_errors.append(
                            f"linkIndex {actual_index!r} not in replay indices {allowed_indices}"
                        )
                    if (actual_owner, actual_index) not in final_control_keys:
                        row_errors.append("controlled key is absent from final SUMO XML")
                    if controlled_from not in lane_ids or controlled_to not in lane_ids:
                        row_errors.append("physical controlled endpoint lane is absent")
        rows.append(
            {
                "status": "pass" if not row_errors else "fail",
                "node_id": node,
                "connection_id": movement.connection_id,
                "official_ingress_lane": movement.ingress_lane_id,
                "official_egress_lane": movement.egress_lane_id,
                "topology_control_key": movement.topology_control_key,
                "observed_stream_ids": list(movement.observed_stream_ids),
                "sumo_ingress_lane": ingress.sumo_lane if ingress else "",
                "sumo_egress_lane": egress.sumo_lane if egress else "",
                "sumo_path_lane_ids": path_lanes,
                "sumo_controlled_from_lane": controlled_from,
                "sumo_controlled_to_lane": controlled_to,
                "sumo_tls_id": actual_owner,
                "sumo_link_index": actual_index,
                "allowed_replay_link_indices": list(allowed_indices),
                "errors": row_errors,
            }
        )
    duplicate_official = [key for key, count in Counter(official_keys).items() if count != 1]
    if duplicate_official:
        errors.append(f"official movement identities are not unique: {sorted(duplicate_official)}")
    physical = [
        (row["sumo_controlled_from_lane"], row["sumo_controlled_to_lane"])
        for row in rows if row["status"] == "pass"
    ]
    duplicate_physical = [key for key, count in Counter(physical).items() if count != 1]
    if duplicate_physical:
        errors.append(f"physical movement endpoints are not unique: {sorted(duplicate_physical)}")
    counts = dict(sorted(Counter(row["node_id"] for row in rows).items()))
    if len(rows) != HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT:
        errors.append(
            f"movement count={len(rows)}, expected {HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT}"
        )
    if counts != HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_COUNTS_BY_NODE:
        errors.append(f"movement counts by node={counts} do not match official inventory")
    failures = [row for row in rows if row["status"] != "pass"]
    if failures:
        errors.append(f"{len(failures)} official movements failed physical endpoint proof")
    return {
        "status": "pass" if not errors else "fail",
        "net_file": str(Path(net_file).resolve()),
        "expected_movement_count": HAMBURG_SANDTORKAI_VEHICLE_TOPOLOGY_MOVEMENT_COUNT,
        "movement_count": len(rows),
        "validated_movement_count": len(rows) - len(failures),
        "movement_counts_by_node": counts,
        "unique_physical_endpoint_count": len(set(physical)),
        "replay_control_evidence": evidence,
        "movements": rows,
        "errors": errors,
    }


def bind_observed_streams_to_replayed_tls(
    *,
    streams: Sequence[SignalStream],
    topology_inventory: OcitVehicleTopologyInventory,
    movement_endpoint_audit: Mapping[str, Any],
) -> list[TlsBinding]:
    """Create the 27 TLD bindings from observed_stream_ids and proven arcs."""

    if movement_endpoint_audit.get("status") != "pass":
        raise ValueError("movement physical endpoint audit is not pass")
    primary: dict[int, SignalStream] = {}
    for stream in streams:
        if stream.layer_name == "primary_signal":
            if stream.stream_id in primary:
                raise ValueError(f"duplicate primary stream id {stream.stream_id}")
            primary[stream.stream_id] = stream
    endpoints = {
        (
            _normalize_node(str(row["node_id"])), str(row["connection_id"]),
            str(row["official_ingress_lane"]), str(row["official_egress_lane"]),
        ): row
        for row in movement_endpoint_audit.get("movements", ())
        if isinstance(row, Mapping)
    }
    seen: dict[int, tuple[str, str, str, str]] = {}
    candidates: list[tuple[TlsBinding, str]] = []
    for movement in topology_inventory.movements:
        key = (
            _normalize_node(movement.node_id), movement.connection_id,
            movement.ingress_lane_id, movement.egress_lane_id,
        )
        endpoint = endpoints.get(key)
        for stream_id in movement.observed_stream_ids:
            if endpoint is None or endpoint.get("status") != "pass":
                raise ValueError(f"observed movement has no proven endpoint: {key}")
            previous = seen.setdefault(stream_id, key)
            if previous != key:
                raise ValueError(f"stream {stream_id} maps to multiple official movements")
            stream = primary.get(stream_id)
            if stream is None:
                raise ValueError(f"observed stream {stream_id} is absent from TLD metadata")
            if (
                _normalize_node(stream.node_id), stream.connection_id,
                stream.ingress_lane_id, stream.egress_lane_id,
            ) != key:
                raise ValueError(f"stream {stream_id} contradicts its OCIT-C/MAP movement")
            link_index = endpoint.get("sumo_link_index")
            if not isinstance(link_index, int):
                raise ValueError(f"stream {stream_id} has no numeric replay linkIndex")
            candidates.append(
                (
                    TlsBinding(
                        stream_id=stream.stream_id, node_id=stream.node_id,
                        connection_id=stream.connection_id, signal_group=stream.signal_group,
                        official_ingress_lane=stream.ingress_lane_id,
                        official_egress_lane=stream.egress_lane_id,
                        sumo_from_lane=str(endpoint["sumo_controlled_from_lane"]),
                        sumo_to_lane=str(endpoint["sumo_controlled_to_lane"]),
                        sumo_tls_id=str(endpoint["sumo_tls_id"]),
                        sumo_link_index=link_index, mapping_confidence="high",
                        mapping_status="active",
                        mapping_reason=(
                            "observed_stream_ids -> OCIT-C/MAP movement -> teacher-contract "
                            "endpoints -> unique final replayed TLS arc"
                        ),
                    ),
                    movement.topology_control_key,
                )
            )
    if set(seen) != set(primary) or len(candidates) != HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT:
        raise ValueError(
            f"observed stream coverage mismatch: bound={sorted(seen)}, metadata={sorted(primary)}"
        )
    grouped: dict[tuple[str, int], list[tuple[TlsBinding, str]]] = {}
    for candidate in candidates:
        binding = candidate[0]
        assert binding.sumo_link_index is not None
        grouped.setdefault((binding.sumo_tls_id, binding.sumo_link_index), []).append(candidate)
    result: list[TlsBinding] = []
    for physical_key, group in sorted(grouped.items()):
        control_keys = {control_key for _, control_key in group}
        signal_groups = {_normalize_group(binding.signal_group) for binding, _ in group}
        if len(control_keys) != 1 or len(signal_groups) != 1:
            raise ValueError(
                f"physical TLS key {physical_key} mixes controls={sorted(control_keys)} "
                f"or groups={sorted(signal_groups)}"
            )
        ordered = sorted(group, key=lambda row: (row[0].stream_id, row[0].connection_id))
        representative = ordered[0][0]
        result.append(representative)
        result.extend(
            replace(
                binding,
                mapping_status="redundant",
                mapping_reason=(
                    f"same replayed official control as representative stream "
                    f"{representative.stream_id} on {physical_key[0]}[{physical_key[1]}]"
                ),
            )
            for binding, _control_key in ordered[1:]
        )
    return sorted(result, key=lambda row: row.stream_id)


def _audit_primary_tls_bindings(
    streams: Sequence[SignalStream],
    bindings: Sequence[TlsBinding],
    topology_inventory: OcitVehicleTopologyInventory,
    movement_endpoint_audit: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        stream.stream_id: stream
        for stream in streams
        if stream.layer_name == "primary_signal"
    }
    indexed: dict[int, list[TlsBinding]] = {}
    for binding in bindings:
        indexed.setdefault(binding.stream_id, []).append(binding)
    missing = sorted(set(expected) - set(indexed))
    unexpected = sorted(set(indexed) - set(expected))
    duplicate = sorted(stream_id for stream_id, rows in indexed.items() if len(rows) != 1)
    endpoint_rows = {
        (
            _normalize_node(str(row.get("node_id", ""))),
            str(row.get("connection_id", "")),
            str(row.get("official_ingress_lane", "")),
            str(row.get("official_egress_lane", "")),
        ): row
        for row in movement_endpoint_audit.get("movements", ())
        if isinstance(row, Mapping)
    }
    observed_control_keys: dict[int, tuple[str, str]] = {}
    observed_physical_targets: dict[int, tuple[str, int, str, str]] = {}
    duplicate_topology_observations: set[int] = set()
    for movement in topology_inventory.movements:
        movement_target = (
            _normalize_node(movement.node_id),
            movement.topology_control_key,
        )
        endpoint = endpoint_rows.get(
            (
                movement_target[0],
                movement.connection_id,
                movement.ingress_lane_id,
                movement.egress_lane_id,
            )
        )
        for stream_id in movement.observed_stream_ids:
            previous = observed_control_keys.setdefault(stream_id, movement_target)
            if previous != movement_target:
                duplicate_topology_observations.add(stream_id)
            if endpoint is not None and isinstance(endpoint.get("sumo_link_index"), int):
                physical = (
                    str(endpoint.get("sumo_tls_id", "")),
                    int(endpoint["sumo_link_index"]),
                    str(endpoint.get("sumo_controlled_from_lane", "")),
                    str(endpoint.get("sumo_controlled_to_lane", "")),
                )
                prior_physical = observed_physical_targets.setdefault(stream_id, physical)
                if prior_physical != physical:
                    duplicate_topology_observations.add(stream_id)
    errors: list[str] = []
    if missing:
        errors.append(f"missing stream ids {missing}")
    if unexpected:
        errors.append(f"unexpected stream ids {unexpected}")
    if duplicate:
        errors.append(f"non-unique stream ids {duplicate}")
    if duplicate_topology_observations:
        errors.append(
            "observation stream ids map to multiple topology controls "
            f"{sorted(duplicate_topology_observations)}"
        )

    status_counts: Counter[str] = Counter()
    wrong_targets: list[dict[str, Any]] = []
    group_active_counts: Counter[tuple[str, str]] = Counter()
    physical_active_counts: Counter[tuple[str, int]] = Counter()
    for stream_id, stream in sorted(expected.items()):
        rows = indexed.get(stream_id, ())
        if len(rows) != 1:
            continue
        binding = rows[0]
        status_counts[binding.mapping_status] += 1
        node_id = _normalize_node(stream.node_id)
        group = _normalize_group(stream.signal_group)
        topology_target = observed_control_keys.get(stream_id)
        physical_target = observed_physical_targets.get(stream_id)
        reasons: list[str] = []
        if binding.mapping_status not in {"active", "redundant"}:
            reasons.append(f"status={binding.mapping_status}")
        if topology_target is None:
            reasons.append("stream is absent from OCIT-C topology observation cross-reference")
        elif topology_target[0] != node_id:
            reasons.append(
                f"topology observation node={topology_target[0]!r}, expected {node_id!r}"
            )
        if physical_target is None:
            reasons.append("stream has no replay-proven physical TLS target")
        else:
            expected_tls_id, expected_index, expected_from, expected_to = physical_target
            if binding.sumo_tls_id != expected_tls_id:
                reasons.append(f"tls={binding.sumo_tls_id!r}, expected {expected_tls_id!r}")
            if binding.sumo_link_index != expected_index:
                reasons.append(
                    f"linkIndex={binding.sumo_link_index!r}, expected {expected_index!r}"
                )
            if binding.sumo_from_lane != expected_from or binding.sumo_to_lane != expected_to:
                reasons.append(
                    "controlled endpoints do not match the replay-proven physical movement"
                )
        if _normalize_node(binding.node_id) != node_id:
            reasons.append(f"binding node={binding.node_id!r}, expected {node_id!r}")
        if _normalize_group(binding.signal_group) != group:
            reasons.append(
                f"binding group={binding.signal_group!r}, expected {stream.signal_group!r}"
            )
        if not binding.sumo_from_lane or not binding.sumo_to_lane:
            reasons.append("controlled physical lane endpoints are blank")
        if binding.mapping_status == "active":
            group_active_counts[(node_id, group)] += 1
            if physical_target is not None:
                physical_active_counts[(physical_target[0], physical_target[1])] += 1
        if reasons:
            wrong_targets.append({"stream_id": stream_id, "reasons": reasons})

    expected_groups = {
        (_normalize_node(stream.node_id), _normalize_group(stream.signal_group))
        for stream in expected.values()
    }
    expected_movements = {
        observed_control_keys[stream_id]
        for stream_id in expected
        if stream_id in observed_control_keys
    }
    physical_by_movement: dict[tuple[str, str], set[tuple[str, int]]] = {}
    for stream_id, movement in observed_control_keys.items():
        physical = observed_physical_targets.get(stream_id)
        if physical is not None:
            physical_by_movement.setdefault(movement, set()).add((physical[0], physical[1]))
    split_observed_controls = {
        movement: sorted(targets)
        for movement, targets in physical_by_movement.items()
        if len(targets) != 1
    }
    expected_physical_controls = {
        next(iter(targets))
        for targets in physical_by_movement.values()
        if len(targets) == 1
    }
    active_group_violations = [
        {
            "node_id": node_id,
            "signal_group": group,
            "active_binding_count": group_active_counts[(node_id, group)],
        }
        for node_id, group in sorted(expected_groups)
        if group_active_counts[(node_id, group)] < 1
    ]
    active_movement_violations = [
        {
            "sumo_tls_id": tls_id,
            "sumo_link_index": link_index,
            "active_binding_count": physical_active_counts[(tls_id, link_index)],
        }
        for tls_id, link_index in sorted(expected_physical_controls)
        if physical_active_counts[(tls_id, link_index)] != 1
    ]
    if split_observed_controls:
        errors.append(
            f"{len(split_observed_controls)} observed topology controls resolve to multiple "
            "physical TLS keys"
        )
    if wrong_targets:
        errors.append(f"{len(wrong_targets)} streams have invalid status or target")
    if active_group_violations:
        errors.append(
            f"{len(active_group_violations)} observed official groups have no active binding"
        )
    if active_movement_violations:
        errors.append(
            f"{len(active_movement_violations)} observed movements do not have exactly one "
            "active representative"
        )
    if len(expected) != HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT:
        errors.append(
            f"expected inventory contains {len(expected)} streams, not "
            f"{HAMBURG_SANDTORKAI_PRIMARY_STREAM_COUNT}"
        )
    validated_count = sum(
        count for status, count in status_counts.items() if status in {"active", "redundant"}
    )
    return {
        "status": "pass" if not errors else "fail",
        "expected_stream_count": len(expected),
        "binding_row_count": len(bindings),
        "validated_active_or_redundant_count": validated_count,
        "status_counts": dict(sorted(status_counts.items())),
        "expected_official_group_count": len(expected_groups),
        "active_official_group_count": sum(
            count >= 1 for group, count in group_active_counts.items() if group in expected_groups
        ),
        "expected_observed_movement_count": len(expected),
        "validated_observed_movement_count": validated_count,
        "expected_observed_control_count": len(expected_movements),
        "active_observed_control_count": sum(
            physical_active_counts[target] == 1 for target in expected_physical_controls
        ),
        "missing_stream_ids": missing,
        "unexpected_stream_ids": unexpected,
        "duplicate_stream_ids": duplicate,
        "wrong_targets": wrong_targets,
        "active_group_violations": active_group_violations,
        "active_movement_violations": active_movement_violations,
        "split_observed_controls": [
            {
                "node_id": movement[0],
                "topology_control_key": movement[1],
                "physical_tls_keys": [
                    {"sumo_tls_id": target[0], "sumo_link_index": target[1]}
                    for target in targets
                ],
            }
            for movement, targets in sorted(split_observed_controls.items())
        ],
        "errors": errors,
    }


def _write_dataclass_csv(path: Path, rows: Sequence[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fieldnames = list(rows[0].__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def _artifact_manifest(paths: Iterable[tuple[str, Path]]) -> list[dict[str, Any]]:
    manifest = []
    seen: set[Path] = set()
    for role, path in paths:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        manifest.append(
            {
                "role": role,
                "path": str(resolved),
                "sha256": sha256_file(resolved),
                "bytes": resolved.stat().st_size,
            }
        )
    return manifest


def _normalize_node(value: str) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.zfill(4) if digits else str(value).strip().upper()


def _normalize_group(value: str) -> str:
    text = str(value).strip().upper()
    if not text.startswith("K"):
        return text
    suffix = text[1:]
    digits = "".join(character for character in suffix if character.isdigit())
    letters = "".join(character for character in suffix if character.isalpha())
    return f"K{int(digits)}{letters}" if digits else text


def _lane_key_dict(key: tuple[str, str]) -> dict[str, str]:
    return {"node_id": key[0], "map_lane_id": key[1]}
