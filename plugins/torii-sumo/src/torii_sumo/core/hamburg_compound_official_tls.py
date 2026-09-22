"""Build Hamburg 2349/2394 TLS on preserved multi-owner SUMO topology.

The official MAP/OCIT controller id is a control domain, not a physical
junction polygon.  This adapter therefore starts from an already joined,
hash-bound topology candidate, proves every official movement on that graph,
and delegates the actual TLS rewrite to :mod:`official_tls_rebuild`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .digital_twin import MapConnection, MapLane, parse_mapem
from .digital_twin_mapping import (
    LaneConnectionEvidence,
    MapLaneBinding,
    bind_map_lanes_to_network,
)
from .hamburg_map_kml import bind_hamburg_map_kml_to_mapem, parse_hamburg_map_kml
from .hamburg_movement_path import derive_hamburg_official_movement_paths
from .hamburg_named_scope import (
    HamburgNamedScopeError,
    validate_hamburg_named_scope_manifest,
)
from .hamburg_stopline_binding import (
    StoplineBindingEstimate,
    estimate_ingress_stopline_bindings,
)
from .hamburg_2394_tls_materializer import _patch_connections
from .hamburg_2394_tls_topology import PhysicalLink, ROUTING_REMOVALS
from .ocit_c import (
    OcitCConfig,
    build_vehicle_topology_inventory,
    parse_ocit_c,
    topology_control_index_by_node,
)
from .official_tls_rebuild import (
    ConnectionRepair,
    OfficialTlsPlan,
    PhysicalControlledLink,
    apply_official_tls_plan_to_plain,
    audit_external_lane_geometry,
    audit_phase_capacity,
    audit_retired_tls_absence,
    derive_official_tls_plan,
    edge_lane_signature,
    external_connection_delta,
)
from .sumo_commands import run_sumo_load_audit
from .surface_overlap_audit import (
    audit_sumo_lane_junction_surface_overlaps,
    compare_sumo_surface_overlap_reports,
)


SCHEMA_ID = "torii.hamburg-compound-official-tls/v1"
OFFICIAL_NODE_IDS = ("2349", "2394")
CONTROLLER_BY_NODE = {"2349": "HH_2349", "2394": "HH_2394"}

JOIN_GROUPS = (
    ("25737304", "759714733"),
    ("739654528", "759714704"),
    ("3847369287", "757036909", "76463166"),
    ("2761334279", "757036795"),
)
CONFLICT_CORE_2349 = (
    "cluster_25737304_759714733",
    "cluster_2761334249_610506352_cluster_739654528_759714704",
)
CONTROLLER_OWNERS_2349 = (
    "2761334245",
    "2761334257",
    "cluster_2761334249_610506352_cluster_739654528_759714704",
)
SIGNAL_OWNERS_2394 = (
    "759714726",
    "cluster_2761334279_757036795",
    "3847369288",
)
PASSIVE_OWNERS_2394 = ("cluster_3847369287_757036909_76463166",)

# Exactly five missing post-join lane connections.  They are connectivity
# evidence only and carry no TLS/link-index authority.
POST_JOIN_CONNECTION_EVIDENCE = (
    LaneConnectionEvidence(
        "554713078#2_0",
        "554713075#0_1",
        "hamburg-map:2349:7->4",
        "official MAP movement 7->4",
    ),
    LaneConnectionEvidence(
        "194672083_1",
        "127716467#0_0",
        "hamburg-map:2349:6->11",
        "official MAP movement 6->11",
    ),
    LaneConnectionEvidence(
        "60578519_1",
        "193847534#0_0",
        "hamburg-map:2394:11->4",
        "official MAP movement 11->4",
    ),
    LaneConnectionEvidence(
        "60578519_2",
        "193847534#0_1",
        "hamburg-map:2394:12->5",
        "official MAP movement 12->5",
    ),
    LaneConnectionEvidence(
        "381540198#1_0",
        "193847534#0_1",
        "hamburg-map:2394:6->5",
        "official MAP movement 6->5",
    ),
)

# The five ``official_map`` rows above are the only connectivity repairs that
# may be added.  The three 2349 C4/C5/C6 rows already exist after the bounded
# channelized-geometry pass and are authoritative stop-line controls.  Further
# stop-line rows are derived from the official KML ingress anchor and a unique
# SUMO predecessor connection at runtime.
PLAN_CONNECTION_EVIDENCE = (
    ConnectionRepair(
        "2349", "554713078#2", 0, "554713075#0", 1,
        reason="official MAP movement 7->4",
    ),
    ConnectionRepair(
        "2349", "194672083", 1, "127716467#0", 0,
        reason="official MAP movement 6->11",
    ),
    ConnectionRepair(
        "2349", "61649647#0", 1, "59990286", 1,
        evidence="official_map_stopline",
        reason="official MAP movement 9->4 at the channelized 2349 stop line",
    ),
    ConnectionRepair(
        "2349", "61649647#0", 0, "59990286", 0,
        evidence="official_map_stopline",
        reason="official MAP movement 10->5 at the channelized 2349 stop line",
    ),
    ConnectionRepair(
        "2349", "61649647#0", 0, "61649649#0", 0,
        evidence="official_map_stopline",
        reason="official MAP movement 10->8 at the channelized 2349 stop line",
    ),
    ConnectionRepair(
        "2394", "60578519", 1, "193847534#0", 0,
        reason="official MAP movement 11->4",
    ),
    ConnectionRepair(
        "2394", "60578519", 2, "193847534#0", 1,
        reason="official MAP movement 12->5",
    ),
    ConnectionRepair(
        "2394", "381540198#1", 0, "193847534#0", 1,
        reason="official MAP movement 6->5",
    ),
)

EXPECTED_ESTIMATED_STOPLINE_REPAIR_KEYS = {
    ("554713076#0", 0, "554713076#1", 0),
    ("554713076#0", 1, "554713076#1", 1),
    ("554713078#0", 0, "554713078#1", 0),
    ("554713078#0", 1, "554713078#1", 1),
    ("381540198#0", 0, "381540198#1", 0),
    ("381540198#0", 1, "381540198#1", 1),
}

EXPECTED_STOPLINE_LINK_BY_MOVEMENT = {
    ("2349", "2"): ("554713076#0", 0, "554713076#1", 0),
    ("2349", "3"): ("554713076#0", 1, "554713076#1", 1),
    ("2349", "4"): ("61649647#0", 1, "59990286", 1),
    ("2349", "5"): ("61649647#0", 0, "59990286", 0),
    ("2349", "6"): ("61649647#0", 0, "61649649#0", 0),
    ("2349", "7"): ("554713078#0", 0, "554713078#1", 0),
    ("2349", "8"): ("554713078#0", 0, "554713078#1", 0),
    ("2349", "9"): ("554713078#0", 1, "554713078#1", 1),
    ("2394", "1"): ("47854310#2", 0, "60578519", 0),
    ("2394", "2"): ("47854310#2", 1, "60578519", 1),
    ("2394", "3"): ("47854310#2", 2, "60578519", 2),
    ("2394", "5"): ("9702432#0", 0, "9702432#2", 0),
    ("2394", "6"): ("9702432#0", 1, "9702432#2", 1),
    ("2394", "7"): ("381540198#0", 0, "381540198#1", 0),
    ("2394", "8"): ("381540198#0", 0, "381540198#1", 0),
    ("2394", "9"): ("381540198#0", 1, "381540198#1", 1),
}

# OCIT-C is the complete motor-vehicle movement inventory for these two
# hash-bound official cells.  Reuse the already audited 2394 removals and add
# the single equivalent 2349 OSM legacy turn.  These are routing removals, not
# merely retired signal links: leaving them uncontrolled would still permit
# movements that the official inventory excludes.
COMPOUND_ROUTING_REMOVALS = (
    *ROUTING_REMOVALS,
    PhysicalLink("554713078#2", 1, "554713075#0", 1),
)


class HamburgCompoundOfficialTlsError(ValueError):
    """Raised when compound topology or official evidence is incomplete."""


def materialize_hamburg_compound_official_tls_candidate(
    *,
    source_net_file: Path,
    join_evidence_file: Path,
    signal_asset_dir: Path,
    output_dir: Path,
    expected_source_sha256: str,
    expected_join_evidence_sha256: str,
    expected_asset_sha256: Mapping[str, str],
    named_scope_manifest_file: Path | None = None,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
    command_runner: Any = run_command,
) -> dict[str, Any]:
    """Build a separate, geometry-preserving 2349/2394 TLS candidate.

    ``source_net_file`` must already contain the four proven local join groups.
    The function never creates a junction polygon from a controller-domain
    hull and never mutates the source network.
    """

    source = Path(source_net_file).resolve(strict=True)
    join_evidence = Path(join_evidence_file).resolve(strict=True)
    assets = Path(signal_asset_dir).resolve(strict=True)
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise HamburgCompoundOfficialTlsError(
            f"output_dir must not already exist: {destination}"
        )
    if timeout_seconds <= 0:
        raise HamburgCompoundOfficialTlsError("timeout_seconds must be positive")
    named_scope_manifest: dict[str, Any] | None = None
    if named_scope_manifest_file is not None:
        try:
            named_scope_manifest = validate_hamburg_named_scope_manifest(
                named_scope_manifest_file,
                require_signal_assets=True,
            )
        except (HamburgNamedScopeError, OSError, ValueError) as exc:
            raise HamburgCompoundOfficialTlsError(
                "named corridor signal-stage gate blocked materialization: " + str(exc)
            ) from exc
        manifest_node_ids = tuple(
            str(row.get("node_id"))
            for row in named_scope_manifest.get("nodes", ())
            if isinstance(row, Mapping)
        )
        if manifest_node_ids != OFFICIAL_NODE_IDS:
            raise HamburgCompoundOfficialTlsError(
                "the 2349/2394 compound adapter cannot claim a complete named corridor "
                f"scope; received node IDs {manifest_node_ids!r}"
            )
    _require_hash(source, expected_source_sha256, "source network")
    _require_hash(join_evidence, expected_join_evidence_sha256, "join evidence")

    asset_paths = _asset_paths(assets)
    expected_asset_keys = set(asset_paths)
    if set(expected_asset_sha256) != expected_asset_keys:
        raise HamburgCompoundOfficialTlsError(
            "expected_asset_sha256 must contain exactly: "
            + ", ".join(sorted(expected_asset_keys))
        )
    for role, path in asset_paths.items():
        _require_hash(path, expected_asset_sha256[role], role)

    source_hash_before = file_sha256(source)
    topology_evidence = _validate_compound_topology(source, join_evidence)
    map_lanes, map_connections, ocit_configs, kml_bindings = _read_official_assets(
        asset_paths
    )
    inventory = build_vehicle_topology_inventory(
        ocit_configs, map_lanes, map_connections, []
    )
    movement_counts = Counter(_normalize_node(row.node_id) for row in inventory.movements)
    if movement_counts != Counter({"2349": 8, "2394": 8}):
        raise HamburgCompoundOfficialTlsError(
            f"official vehicle movement inventory changed: {dict(movement_counts)}"
        )
    control_indices = topology_control_index_by_node(inventory)
    if {node: len(control_indices.get(node, {})) for node in OFFICIAL_NODE_IDS} != {
        "2349": 6,
        "2394": 6,
    }:
        raise HamburgCompoundOfficialTlsError(
            "expected six official control expressions at both 2349 and 2394"
        )

    raw_lane_bindings = bind_map_lanes_to_network(source, map_lanes)
    _validate_lane_bindings(raw_lane_bindings)
    stopline_estimate = estimate_ingress_stopline_bindings(
        source,
        raw_lane_bindings,
    )
    _validate_stopline_estimate(stopline_estimate)
    lane_bindings = stopline_estimate.bindings
    stopline_repairs = _connection_repairs_from_stopline_estimate(stopline_estimate)
    plan_repairs = _merge_connection_repairs(
        PLAN_CONNECTION_EVIDENCE,
        stopline_repairs,
    )
    movement_paths = derive_hamburg_official_movement_paths(
        candidate_net_file=source,
        official_movements=inventory.movements,
        lane_bindings=lane_bindings,
        connection_evidence=POST_JOIN_CONNECTION_EVIDENCE,
        max_path_hops=8,
        max_path_span_m=200.0,
    )
    if len(movement_paths) != 16:
        raise HamburgCompoundOfficialTlsError(
            f"expected 16 unique official movement paths, got {len(movement_paths)}"
        )

    plan, derivation = derive_official_tls_plan(
        signal_streams=inventory.topology_streams,
        lane_bindings=lane_bindings,
        source_net_file=source,
        repairs=plan_repairs,
        group_index_by_node=control_indices,
        plan_id="hamburg-sandtorkai-2349-2394-compound-v1",
        version="2026-07-19.v9",
        tls_id_by_node=CONTROLLER_BY_NODE,
        uncontrolled_path_policy="fail",
        unclaimed_retired_link_policy="demote_after_complete_official_inventory",
        max_path_hops=8,
        max_path_span_m=200.0,
    )
    if (
        derivation.get("status") != "pass"
        or derivation.get("visual_review_required_count") != 0
        or derivation.get("hit_repair_count") != len(plan_repairs)
        or derivation.get("unused_repairs")
    ):
        raise HamburgCompoundOfficialTlsError(
            "generic official TLS derivation did not close without guessing"
        )
    _validate_compound_stopline_plan(plan, derivation)
    plan, passive_demotions = _demote_declared_passive_owners(source, plan)

    destination.mkdir(parents=True)
    evidence_dir = destination / "evidence"
    evidence_dir.mkdir()
    lane_binding_file = evidence_dir / "map_lane_bindings.json"
    raw_lane_binding_file = evidence_dir / "map_lane_bindings.raw.json"
    stopline_binding_file = evidence_dir / "official_stopline_bindings.json"
    movement_path_file = evidence_dir / "official_movement_paths.json"
    derivation_file = evidence_dir / "official_tls_derivation.json"
    write_json_atomic(
        lane_binding_file,
        [asdict(row) for row in lane_bindings],
        sort_keys=True,
    )
    write_json_atomic(
        raw_lane_binding_file,
        [asdict(row) for row in raw_lane_bindings],
        sort_keys=True,
    )
    write_json_atomic(
        stopline_binding_file,
        {
            "status": stopline_estimate.status,
            "source_net_sha256": stopline_estimate.source_net_sha256,
            "evidence": [asdict(row) for row in stopline_estimate.evidence],
            "derived_repairs": [asdict(row) for row in stopline_repairs],
        },
        sort_keys=True,
    )
    write_json_atomic(
        movement_path_file,
        [asdict(row) for row in movement_paths],
        sort_keys=True,
    )
    write_json_atomic(derivation_file, derivation, sort_keys=True)

    rebuild = _build_compiled_source_patch_variant(
        source_net_file=source,
        plan=plan,
        output_dir=destination / "network",
        netconvert_binary=netconvert_binary,
        timeout_seconds=timeout_seconds,
        command_runner=command_runner,
    )
    if rebuild.get("status") != "pass":
        raise HamburgCompoundOfficialTlsError(
            "official TLS rebuild failed: " + str(rebuild.get("error", "unknown error"))
        )
    rebuilt = Path(str(rebuild["rebuilt_net_file"])).resolve(strict=True)

    final_raw_bindings = bind_map_lanes_to_network(rebuilt, map_lanes)
    _validate_lane_bindings(final_raw_bindings)
    final_stopline_estimate = estimate_ingress_stopline_bindings(
        rebuilt,
        final_raw_bindings,
    )
    _validate_stopline_estimate(final_stopline_estimate)
    _validate_stopline_binding_parity(stopline_estimate, final_stopline_estimate)
    final_bindings = final_stopline_estimate.bindings
    final_paths = derive_hamburg_official_movement_paths(
        candidate_net_file=rebuilt,
        official_movements=inventory.movements,
        lane_bindings=final_bindings,
        max_path_hops=8,
        max_path_span_m=200.0,
    )
    if len(final_paths) != 16:
        raise HamburgCompoundOfficialTlsError(
            "rebuilt network does not preserve all 16 official movement paths"
        )
    network_audit = _audit_compound_candidate(rebuilt, plan)
    if network_audit["status"] != "pass":
        raise HamburgCompoundOfficialTlsError(
            "compound owner audit failed: " + "; ".join(network_audit["errors"])
        )

    sumo_load = run_sumo_load_audit(
        net_file=rebuilt,
        output_dir=destination / "sumo_load",
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
        command_runner=command_runner,
    )
    baseline_surface = audit_sumo_lane_junction_surface_overlaps(
        source,
        report_file=destination / "surface_overlap" / "baseline.json",
    )
    candidate_surface = audit_sumo_lane_junction_surface_overlaps(
        rebuilt,
        report_file=destination / "surface_overlap" / "candidate.json",
    )
    surface_comparison = compare_sumo_surface_overlap_reports(
        baseline_surface,
        candidate_surface,
        focus_junction_ids=[
            *CONFLICT_CORE_2349,
            *SIGNAL_OWNERS_2394,
            *PASSIVE_OWNERS_2394,
        ],
        report_file=destination / "surface_overlap" / "comparison.json",
    )
    source_unchanged = file_sha256(source) == source_hash_before
    topology_pass = (
        source_unchanged
        and sumo_load.get("status") == "pass"
        and surface_comparison.get("status") == "pass"
        and network_audit["status"] == "pass"
    )
    if not topology_pass:
        raise HamburgCompoundOfficialTlsError(
            "one or more automatic topology/load/surface gates failed"
        )

    manifest_file = destination / "hamburg_compound_official_tls.manifest.json"
    manifest: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "status": "topology_ready",
        "claim_status": "official-static-topology-candidate",
        "human_review_required": False,
        "automatic_topology_gate": "pass",
        "operational_simulation_gate": "blocked_until_historical_signal_timing_replay",
        "named_scope_signal_stage_gate": (
            {
                "status": "pass",
                "path": str(Path(named_scope_manifest_file).resolve()),
                "sha256": file_sha256(Path(named_scope_manifest_file).resolve()),
                "scope_id": named_scope_manifest["scope_id"],
            }
            if named_scope_manifest_file is not None and named_scope_manifest is not None
            else {"status": "not_provided"}
        ),
        "source": {
            "path": str(source),
            "sha256": source_hash_before,
            "immutable": source_unchanged,
        },
        "join_evidence": {
            "path": str(join_evidence),
            "sha256": file_sha256(join_evidence),
            "audit": topology_evidence,
        },
        "official_assets": {
            role: {"path": str(path), "sha256": file_sha256(path)}
            for role, path in asset_paths.items()
        },
        "official_kml_map_binding": kml_bindings,
        "inventory": {
            "vehicle_movement_count": len(inventory.movements),
            "movement_count_by_node": dict(sorted(movement_counts.items())),
            "control_expression_count_by_node": {
                node: len(control_indices[node]) for node in OFFICIAL_NODE_IDS
            },
            "raw_map_lane_binding_count": len(raw_lane_bindings),
            "stopline_aligned_map_lane_binding_count": len(lane_bindings),
            "stopline_adjusted_ingress_count": len(stopline_estimate.evidence),
            "movement_path_count": len(movement_paths),
        },
        "official_stopline_alignment": {
            "status": stopline_estimate.status,
            "source_net_sha256": stopline_estimate.source_net_sha256,
            "adjusted_ingress_count": len(stopline_estimate.evidence),
            "derived_repair_count": len(stopline_repairs),
            "expected_physical_control_count": 14,
            "movement_count": 16,
        },
        "connection_evidence": {
            "missing_connection_count": len(POST_JOIN_CONNECTION_EVIDENCE),
            "missing_connections": [asdict(row) for row in POST_JOIN_CONNECTION_EVIDENCE],
            "static_stopline_control_count": sum(
                row.evidence == "official_map_stopline"
                for row in PLAN_CONNECTION_EVIDENCE
            ),
            "estimated_stopline_control_count": len(stopline_repairs),
            "declared_plan_connection_count": len(plan_repairs),
            "rebuild_added_connection_count": rebuild["plain_application"]["repair_added_count"],
            "rebuild_existing_connection_count": rebuild["plain_application"]["repair_existing_count"],
        },
        "physical_topology": {
            "2349": {
                "conflict_core_owner_count": 2,
                "conflict_core_owner_ids": list(CONFLICT_CORE_2349),
                "shared_controller_owner_ids": list(CONTROLLER_OWNERS_2349),
                "merge_policy": (
                    "preserve_two_upstream_stopline_owners; join_only_the_bounded_"
                    "C4_C5_C6_channelized_core"
                ),
                "join_evidence": {
                    "consumed_connector_edge_ids": ["59626578", "61649650"],
                    "connector_usable_lane_length_m": 0.2,
                    "raw_pair_surface_overlap_area_m2_approx": 15.9,
                },
            },
            "2394": {
                "owner_component_count": 5,
                "signal_owner_count": 3,
                "signal_owner_ids": list(SIGNAL_OWNERS_2394),
                "passive_owner_count": 2,
                "passive_owner_ids": list(PASSIVE_OWNERS_2394),
            },
        },
        "passive_owner_demotions": passive_demotions,
        "tls_derivation": derivation,
        "network_rebuild": rebuild,
        "network_audit": network_audit,
        "sumo_load_audit": sumo_load,
        "surface_overlap_comparison": surface_comparison,
        "artifacts": {
            "network": {"path": str(rebuilt), "sha256": file_sha256(rebuilt)},
            "lane_bindings": {
                "path": str(lane_binding_file),
                "sha256": file_sha256(lane_binding_file),
            },
            "raw_lane_bindings": {
                "path": str(raw_lane_binding_file),
                "sha256": file_sha256(raw_lane_binding_file),
            },
            "official_stopline_bindings": {
                "path": str(stopline_binding_file),
                "sha256": file_sha256(stopline_binding_file),
            },
            "movement_paths": {
                "path": str(movement_path_file),
                "sha256": file_sha256(movement_path_file),
            },
            "tls_derivation": {
                "path": str(derivation_file),
                "sha256": file_sha256(derivation_file),
            },
            "manifest": str(manifest_file),
        },
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    manifest["manifest_sha256"] = file_sha256(manifest_file)
    return manifest


def _build_compiled_source_patch_variant(
    *,
    source_net_file: Path,
    plan: OfficialTlsPlan,
    output_dir: Path,
    netconvert_binary: str,
    timeout_seconds: float,
    command_runner: Any,
) -> dict[str, Any]:
    """Patch a compiled network without a full edge/node PlainXML round trip."""

    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True)
    source_hash_before = file_sha256(source_net_file)
    base_prefix = destination / "source_plain"
    export_command = [
        str(netconvert_binary),
        "--sumo-net-file",
        str(source_net_file),
        "--plain-output-prefix",
        str(base_prefix),
        "--plain-output.lanes",
        "true",
    ]
    export_result = _result_dict(
        command_runner(
            export_command,
            cwd=destination,
            timeout_seconds=timeout_seconds,
        )
    )
    if export_result.get("status") != "pass" or export_result.get("returncode") != 0:
        raise HamburgCompoundOfficialTlsError("source PlainXML export failed")
    base_connections = destination / "source_plain.con.xml"
    base_tllogic = destination / "source_plain.tll.xml"
    if not base_connections.is_file() or not base_tllogic.is_file():
        raise HamburgCompoundOfficialTlsError("PlainXML export omitted connections or TLS")

    official_connections = destination / "official_tls.con.xml"
    official_tllogic = destination / "official_tls.tll.xml"
    application = apply_official_tls_plan_to_plain(
        source_connections_file=base_connections,
        source_tllogic_file=base_tllogic,
        output_connections_file=official_connections,
        output_tllogic_file=official_tllogic,
        plan=plan,
    )
    source_connection_keys = _external_connection_keys(source_net_file)
    effective_routing_removals = tuple(
        link for link in COMPOUND_ROUTING_REMOVALS
        if link.key in source_connection_keys
    )
    if effective_routing_removals:
        routing_prune = _patch_connections(
            official_connections,
            repairs=(),
            removals=effective_routing_removals,
        )
        routing_delete_patch = _append_connection_delete_directives(
            official_connections,
            removals=effective_routing_removals,
        )
    else:
        routing_prune = {
            "status": "pass",
            "removed_count": 0,
            "reason": "all declared non-official movements were already absent",
        }
        routing_delete_patch = {
            "status": "pass",
            "delete_directive_count": 0,
            "delete_directives": [],
        }
    node_patch = destination / "official_tls_owner_patch.nod.xml"
    owner_application = _write_compiled_source_owner_patch(
        source_net_file,
        plan,
        node_patch,
    )
    rebuilt = destination / "official_tls_compound.net.xml"
    error_log = destination / "netconvert.log"
    rebuild_command = [
        str(netconvert_binary),
        "--sumo-net-file",
        str(source_net_file),
        "--node-files",
        str(node_patch),
        "--connection-files",
        str(official_connections),
        "--tllogic-files",
        str(official_tllogic),
        "--no-turnarounds",
        "--offset.disable-normalization",
        "true",
        "--output-file",
        str(rebuilt),
        "--error-log",
        str(error_log),
    ]
    rebuild_result = _result_dict(
        command_runner(
            rebuild_command,
            cwd=destination,
            timeout_seconds=timeout_seconds,
        )
    )
    if (
        rebuild_result.get("status") != "pass"
        or rebuild_result.get("returncode") != 0
        or not rebuilt.is_file()
    ):
        raise HamburgCompoundOfficialTlsError(
            "compiled-source TLS patch failed: "
            + str(rebuild_result.get("stderr") or rebuild_result.get("error", ""))
        )

    source_signature = edge_lane_signature(source_net_file)
    rebuilt_signature = edge_lane_signature(rebuilt)
    geometry = audit_external_lane_geometry(
        source_net_file,
        rebuilt,
        max_shape_deviation_m=0.0,
        max_length_deviation_m=0.0,
    )
    connection_delta = external_connection_delta(source_net_file, rebuilt)
    expected_added = {
        repair.key
        for repair in plan.repairs
        if repair.evidence == "official_map"
        and repair.key not in source_connection_keys
    }
    actual_added = {
        (
            str(row["from_edge"]),
            int(row["from_lane"]),
            str(row["to_edge"]),
            int(row["to_lane"]),
        )
        for row in connection_delta["added"]
    }
    expected_removed = {link.key for link in effective_routing_removals}
    actual_removed = {
        (
            str(row["from_edge"]),
            int(row["from_lane"]),
            str(row["to_edge"]),
            int(row["to_lane"]),
        )
        for row in connection_delta["removed"]
    }
    rebuilt_connection_keys = _external_connection_keys(rebuilt)
    forbidden_routing_links_present = sorted(
        link.key
        for link in COMPOUND_ROUTING_REMOVALS
        if link.key in rebuilt_connection_keys
    )
    phase_capacity = audit_phase_capacity(
        rebuilt,
        set(CONTROLLER_BY_NODE.values()),
    )
    retired_absence = audit_retired_tls_absence(rebuilt, set(plan.retired_tls_ids))
    source_unchanged = file_sha256(source_net_file) == source_hash_before
    status = (
        "pass"
        if source_unchanged
        and source_signature["sha256"] == rebuilt_signature["sha256"]
        and geometry["status"] == "pass"
        and actual_removed == expected_removed
        and actual_added == expected_added
        and not forbidden_routing_links_present
        and phase_capacity["status"] == "pass"
        and retired_absence["status"] == "pass"
        else "blocked"
    )
    report = {
        "status": status,
        "claim_status": "official-compiled-source-tls-patch",
        "source_net_file": str(source_net_file),
        "source_sha256_before": source_hash_before,
        "source_sha256_after": file_sha256(source_net_file),
        "source_unchanged": source_unchanged,
        "rebuilt_net_file": str(rebuilt),
        "plan": asdict(plan),
        "plain_export": {"command": export_command, "result": export_result},
        "plain_application": application,
        "official_inventory_routing_prune": {
            "plain_removal": routing_prune,
            "compiled_net_delete_patch": routing_delete_patch,
            "declared_forbidden_connection_count": len(COMPOUND_ROUTING_REMOVALS),
            "effective_removal_count": len(effective_routing_removals),
            "forbidden_connections_present_after_rebuild": [
                {
                    "from_edge": key[0],
                    "from_lane": key[1],
                    "to_edge": key[2],
                    "to_lane": key[3],
                }
                for key in forbidden_routing_links_present
            ],
        },
        "owner_application": owner_application,
        "rebuild": {"command": rebuild_command, "result": rebuild_result},
        "edge_lane_signature_match": source_signature["sha256"] == rebuilt_signature["sha256"],
        "external_lane_geometry_audit": geometry,
        "connection_delta": connection_delta,
        "expected_added_connections": [
            {
                "from_edge": key[0],
                "from_lane": key[1],
                "to_edge": key[2],
                "to_lane": key[3],
            }
            for key in sorted(expected_added)
        ],
        "expected_removed_connections": [
            {
                "from_edge": key[0],
                "from_lane": key[1],
                "to_edge": key[2],
                "to_lane": key[3],
            }
            for key in sorted(expected_removed)
        ],
        "phase_capacity_audit": phase_capacity,
        "retired_tls_absence_audit": retired_absence,
    }
    report_file = destination / "compiled_source_tls_patch.json"
    write_json_atomic(report_file, report, sort_keys=True)
    report["report_file"] = str(report_file)
    report["report_sha256"] = file_sha256(report_file)
    if status != "pass":
        raise HamburgCompoundOfficialTlsError(
            "compiled-source patch failed an exact geometry/connection/TLS gate"
        )
    return report


def _append_connection_delete_directives(
    path: Path,
    *,
    removals: Sequence[PhysicalLink],
) -> dict[str, Any]:
    """Write SUMO's explicit patch form for removing compiled-net connections."""

    tree = ET.parse(path)
    root = tree.getroot()
    existing_delete_keys = {
        (
            element.attrib.get("from", ""),
            int(element.attrib.get("fromLane", "-1")),
            element.attrib.get("to", ""),
            int(element.attrib.get("toLane", "-1")),
        )
        for element in root.findall("delete")
    }
    removal_keys = {item.key for item in removals}
    if existing_delete_keys & removal_keys:
        raise HamburgCompoundOfficialTlsError(
            "official routing delete patch already contains a declared removal"
        )
    for item in sorted(removals, key=lambda value: value.key):
        root.append(
            ET.Element(
                "delete",
                {
                    "from": item.from_edge,
                    "to": item.to_edge,
                    "fromLane": str(item.from_lane),
                    "toLane": str(item.to_lane),
                },
            )
        )
    ET.indent(tree, space="    ")
    write_text_atomic(path, ET.tostring(root, encoding="unicode") + "\n")
    return {
        "status": "pass",
        "delete_directive_count": len(removal_keys),
        "delete_directives": [
            {
                "from_edge": key[0],
                "from_lane": key[1],
                "to_edge": key[2],
                "to_lane": key[3],
            }
            for key in sorted(removal_keys)
        ],
    }


def _external_connection_keys(
    net_file: Path,
) -> set[tuple[str, int, str, int]]:
    result = set()
    for element in ET.parse(net_file).getroot().findall("connection"):
        from_edge = element.attrib.get("from", "")
        to_edge = element.attrib.get("to", "")
        if not from_edge or not to_edge or from_edge.startswith(":") or to_edge.startswith(":"):
            continue
        try:
            result.add(
                (
                    from_edge,
                    int(element.attrib["fromLane"]),
                    to_edge,
                    int(element.attrib["toLane"]),
                )
            )
        except (KeyError, ValueError):
            continue
    return result


def _write_compiled_source_owner_patch(
    source_net_file: Path,
    plan: OfficialTlsPlan,
    output_file: Path,
) -> dict[str, Any]:
    root = ET.parse(source_net_file).getroot()
    edges = {
        edge.attrib.get("id", ""): (
            edge.attrib.get("from", ""),
            edge.attrib.get("to", ""),
        )
        for edge in root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib.get("id", "").startswith(":")
    }
    owner_controller: dict[str, str] = {}
    for group in plan.groups:
        for link in group.physical_links:
            if link.from_edge not in edges or link.to_edge not in edges:
                raise HamburgCompoundOfficialTlsError(
                    f"planned physical link references missing edge: {link.key}"
                )
            owner = edges[link.from_edge][1]
            if not owner or owner != edges[link.to_edge][0]:
                raise HamburgCompoundOfficialTlsError(
                    f"planned physical link has no single junction owner: {link.key}"
                )
            previous = owner_controller.setdefault(owner, group.tls_id)
            if previous != group.tls_id:
                raise HamburgCompoundOfficialTlsError(
                    f"junction owner {owner} belongs to multiple official controllers"
                )

    retired_owners: set[str] = set()
    for connection in root.findall("connection"):
        tls_id = connection.attrib.get("tl", "")
        from_edge = connection.attrib.get("from", "")
        to_edge = connection.attrib.get("to", "")
        if (
            tls_id not in plan.retired_tls_ids
            or from_edge not in edges
            or to_edge not in edges
            or from_edge.startswith(":")
            or to_edge.startswith(":")
        ):
            continue
        owner = edges[from_edge][1]
        if owner and owner == edges[to_edge][0] and owner not in owner_controller:
            retired_owners.add(owner)

    # A controller id may be reused by the new official program while one of
    # its former physical owners becomes routing-only.  Such an owner is not
    # discoverable from ``retired_tls_ids`` because the logical controller is
    # intentionally retained.  Explicitly demoted links therefore also
    # contribute passive owners, unless the owner carries another selected
    # official stop-line link.
    for link in plan.demoted_links:
        if link.from_edge not in edges or link.to_edge not in edges:
            raise HamburgCompoundOfficialTlsError(
                f"demoted physical link references missing edge: {link.key}"
            )
        owner = edges[link.from_edge][1]
        if owner and owner == edges[link.to_edge][0] and owner not in owner_controller:
            retired_owners.add(owner)

    junction_ids = {
        junction.attrib.get("id", "")
        for junction in root.findall("junction")
        if junction.attrib.get("id")
    }
    missing = sorted((set(owner_controller) | retired_owners) - junction_ids)
    if missing:
        raise HamburgCompoundOfficialTlsError(
            "owner patch references missing junctions: " + ", ".join(missing)
        )
    nodes = ET.Element("nodes")
    for owner, tls_id in sorted(owner_controller.items()):
        ET.SubElement(
            nodes,
            "node",
            {"id": owner, "type": "traffic_light", "tl": tls_id},
        )
    for owner in sorted(retired_owners):
        ET.SubElement(nodes, "node", {"id": owner, "type": "priority"})
    ET.indent(nodes, space="    ")
    write_text_atomic(
        output_file,
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(nodes, encoding="unicode")
        + "\n",
    )
    return {
        "status": "pass",
        "signal_owner_count": len(owner_controller),
        "signal_owner_by_controller": {
            tls_id: sorted(
                owner for owner, candidate_tls in owner_controller.items()
                if candidate_tls == tls_id
            )
            for tls_id in sorted(set(owner_controller.values()))
        },
        "demoted_owner_count": len(retired_owners),
        "demoted_owner_ids": sorted(retired_owners),
        "node_patch_file": str(output_file),
    }


def _asset_paths(asset_dir: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for node_id in OFFICIAL_NODE_IDS:
        for kind, suffix in (
            ("map_xml", "map_xml.xml"),
            ("map_kml", "map_kml.kml"),
            ("ocit_xml", "ocit_xml.xml"),
        ):
            role = f"{node_id}_{kind}"
            result[role] = (asset_dir / f"{node_id}_{suffix}").resolve(strict=True)
    return result


def _read_official_assets(
    asset_paths: Mapping[str, Path],
) -> tuple[list[MapLane], list[MapConnection], list[OcitCConfig], dict[str, Any]]:
    map_lanes: list[MapLane] = []
    map_connections: list[MapConnection] = []
    ocit_configs: list[OcitCConfig] = []
    kml_bindings: dict[str, Any] = {}
    for node_id in OFFICIAL_NODE_IDS:
        node_lanes, node_connections = parse_mapem(asset_paths[f"{node_id}_map_xml"])
        ocit = parse_ocit_c(asset_paths[f"{node_id}_ocit_xml"])
        if _normalize_node(ocit.node_id) != node_id:
            raise HamburgCompoundOfficialTlsError(
                f"OCIT node mismatch for {node_id}: {ocit.node_id!r}"
            )
        kml = parse_hamburg_map_kml(asset_paths[f"{node_id}_map_kml"])
        kml_binding = bind_hamburg_map_kml_to_mapem(
            kml,
            node_lanes,
            node_connections,
            expected_node_id=node_id,
        )
        if kml_binding.get("status") != "pass":
            raise HamburgCompoundOfficialTlsError(
                f"official MAP/KML geometry binding failed for {node_id}"
            )
        kml_bindings[node_id] = {
            "status": "pass",
            "counts": kml_binding["counts"],
            "gates": kml_binding["gates"],
        }
        map_lanes.extend(node_lanes)
        map_connections.extend(node_connections)
        ocit_configs.append(ocit)
    return map_lanes, map_connections, ocit_configs, kml_bindings


def _validate_lane_bindings(bindings: Sequence[MapLaneBinding]) -> None:
    if len(bindings) != 21:
        raise HamburgCompoundOfficialTlsError(
            f"expected 21 official vehicle MAP lane bindings, got {len(bindings)}"
        )
    failures = [
        f"{_normalize_node(row.node_id)}/{row.map_lane_id}:{row.mapping_status}/{row.mapping_confidence}"
        for row in bindings
        if row.mapping_status != "active" or row.mapping_confidence != "high"
    ]
    if failures:
        raise HamburgCompoundOfficialTlsError(
            "official MAP lanes are not all active/high: " + ", ".join(failures)
        )


def _validate_stopline_estimate(estimate: StoplineBindingEstimate) -> None:
    if estimate.status != "pass":
        reasons = [
            f"{row.node_id}/{row.map_lane_id}:{row.status}:{row.reason}"
            for row in estimate.evidence
            if row.status != "pass"
        ]
        raise HamburgCompoundOfficialTlsError(
            "official KML stop-line alignment did not close: " + "; ".join(reasons)
        )
    observed = {
        row.connection_repair_key
        for row in estimate.evidence
        if row.connection_repair_key is not None
    }
    if observed != EXPECTED_ESTIMATED_STOPLINE_REPAIR_KEYS:
        raise HamburgCompoundOfficialTlsError(
            "official KML stop-line predecessor inventory changed: "
            f"expected {sorted(EXPECTED_ESTIMATED_STOPLINE_REPAIR_KEYS)}, "
            f"got {sorted(observed)}"
        )


def _connection_repairs_from_stopline_estimate(
    estimate: StoplineBindingEstimate,
) -> tuple[ConnectionRepair, ...]:
    repairs = []
    for row in estimate.evidence:
        if row.status != "pass" or row.connection_repair_key is None:
            continue
        from_edge, from_lane, to_edge, to_lane = row.connection_repair_key
        repairs.append(
            ConnectionRepair(
                _normalize_node(row.node_id),
                from_edge,
                from_lane,
                to_edge,
                to_lane,
                evidence="official_map_stopline",
                reason=(
                    f"official KML ingress lane {row.map_lane_id} stop line; "
                    f"anchor {row.anchor_to_lane_start_m:.3f}m from mapped lane start"
                ),
            )
        )
    return tuple(sorted(repairs))


def _merge_connection_repairs(
    *repair_groups: Sequence[ConnectionRepair],
) -> tuple[ConnectionRepair, ...]:
    by_key: dict[tuple[str, int, str, int], ConnectionRepair] = {}
    for repair in (item for group in repair_groups for item in group):
        existing = by_key.get(repair.key)
        if existing is not None and existing != repair:
            raise HamburgCompoundOfficialTlsError(
                "conflicting official connection evidence for " + str(repair.key)
            )
        by_key[repair.key] = repair
    return tuple(by_key[key] for key in sorted(by_key))


def _validate_stopline_binding_parity(
    before: StoplineBindingEstimate,
    after: StoplineBindingEstimate,
) -> None:
    before_bindings = {
        (_normalize_node(row.node_id), row.map_lane_id): (
            row.sumo_edge,
            row.sumo_lane,
            round(row.lane_position, 6),
        )
        for row in before.bindings
    }
    after_bindings = {
        (_normalize_node(row.node_id), row.map_lane_id): (
            row.sumo_edge,
            row.sumo_lane,
            round(row.lane_position, 6),
        )
        for row in after.bindings
    }
    if before_bindings != after_bindings:
        raise HamburgCompoundOfficialTlsError(
            "official stop-line bindings drifted during TLS materialization"
        )


def _validate_compound_stopline_plan(
    plan: OfficialTlsPlan,
    derivation: Mapping[str, Any],
) -> None:
    movements = derivation.get("movements")
    if not isinstance(movements, list) or len(movements) != 16:
        raise HamburgCompoundOfficialTlsError(
            "official TLS derivation must audit exactly 16 motor movements"
        )
    observed: dict[tuple[str, str], tuple[str, int, str, int]] = {}
    for movement in movements:
        if not isinstance(movement, Mapping):
            raise HamburgCompoundOfficialTlsError("invalid official movement audit row")
        links = movement.get("selected_physical_links")
        if not isinstance(links, list) or len(links) != 1:
            raise HamburgCompoundOfficialTlsError(
                "every official movement must select exactly one physical stop-line link"
            )
        link = links[0]
        if not isinstance(link, Mapping):
            raise HamburgCompoundOfficialTlsError("invalid selected stop-line link row")
        key = (
            str(link.get("from_edge", "")),
            int(link.get("from_lane", -1)),
            str(link.get("to_edge", "")),
            int(link.get("to_lane", -1)),
        )
        movement_key = (
            _normalize_node(str(movement.get("official_node_id", ""))),
            str(movement.get("connection_id", "")),
        )
        observed[movement_key] = key
        if movement.get("selected_control_arc_path_index") != 0:
            raise HamburgCompoundOfficialTlsError(
                f"official movement {movement_key} is not controlled at its bound stop line"
            )
        if movement.get("visual_review_required"):
            raise HamburgCompoundOfficialTlsError(
                f"official movement {movement_key} still requires visual control selection"
            )
    if observed != EXPECTED_STOPLINE_LINK_BY_MOVEMENT:
        raise HamburgCompoundOfficialTlsError(
            "official movement-to-stop-line inventory changed: "
            f"expected {EXPECTED_STOPLINE_LINK_BY_MOVEMENT}, got {observed}"
        )
    physical_links = {
        link.key
        for group in plan.groups
        for link in group.physical_links
    }
    if len(physical_links) != 14 or derivation.get("derived_physical_link_count") != 14:
        raise HamburgCompoundOfficialTlsError(
            "expected 14 unique physical stop-line links for 16 official movements"
        )


def _validate_compound_topology(net_file: Path, join_file: Path) -> dict[str, Any]:
    join_root = ET.parse(join_file).getroot()
    observed_groups = {
        frozenset(element.attrib.get("nodes", "").split())
        for element in join_root.findall("join")
        if element.attrib.get("nodes", "").strip()
    }
    expected_groups = {frozenset(group) for group in JOIN_GROUPS}
    missing_groups = sorted(" ".join(sorted(group)) for group in expected_groups - observed_groups)
    compound_raw_ids = set().union(*expected_groups)
    conflicting_groups = sorted(
        " ".join(sorted(group))
        for group in observed_groups
        if group & compound_raw_ids and group not in expected_groups
    )

    root = ET.parse(net_file).getroot()
    junctions = {
        element.attrib.get("id", ""): element
        for element in root.findall("junction")
        if element.attrib.get("id")
    }
    expected_owners = {
        *CONFLICT_CORE_2349,
        *SIGNAL_OWNERS_2394,
        *PASSIVE_OWNERS_2394,
    }
    missing_owners = sorted(expected_owners - set(junctions))
    origin_checks = {
        "cluster_25737304_759714733": {"25737304", "759714733"},
        "cluster_739654528_759714704": {"739654528", "759714704"},
        "cluster_2761334279_757036795": {"2761334279", "757036795"},
        "cluster_3847369287_757036909_76463166": {
            "3847369287",
            "757036909",
            "76463166",
        },
    }
    origin_mismatches = []
    for owner_id, expected_origins in origin_checks.items():
        junction = junctions.get(owner_id)
        if junction is None:
            continue
        observed_origins = set()
        for parameter in junction.findall("param"):
            if parameter.attrib.get("key") == "origId":
                observed_origins.update(parameter.attrib.get("value", "").split())
        if observed_origins != expected_origins:
            origin_mismatches.append(
                {
                    "junction_id": owner_id,
                    "expected_orig_ids": sorted(expected_origins),
                    "observed_orig_ids": sorted(observed_origins),
                }
            )
    status = (
        "pass"
        if not missing_groups
        and not conflicting_groups
        and not missing_owners
        and not origin_mismatches
        else "blocked"
    )
    if status != "pass":
        raise HamburgCompoundOfficialTlsError(
            "compound topology evidence failed: "
            f"missing_groups={missing_groups}, conflicting_groups={conflicting_groups}, "
            f"missing_owners={missing_owners}, origin_mismatches={origin_mismatches}"
        )
    return {
        "status": status,
        "join_groups": [list(group) for group in JOIN_GROUPS],
        "2349_conflict_core_owner_count": 2,
        "2394_owner_component_count": 5,
        "origin_lineage_status": "pass",
    }


def _demote_declared_passive_owners(
    source_net_file: Path,
    plan: OfficialTlsPlan,
) -> tuple[OfficialTlsPlan, dict[str, Any]]:
    root = ET.parse(source_net_file).getroot()
    edges = {
        edge.attrib.get("id", ""): (
            edge.attrib.get("from", ""),
            edge.attrib.get("to", ""),
        )
        for edge in root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib.get("id", "").startswith(":")
    }
    assigned = {
        link.key for group in plan.groups for link in group.physical_links
    }
    demoted = {link.key: link for link in plan.demoted_links}
    retired = set(plan.retired_tls_ids)
    owner_rows: list[dict[str, Any]] = []
    for owner_id in PASSIVE_OWNERS_2394:
        controlled: list[tuple[PhysicalControlledLink, str]] = []
        for connection in root.findall("connection"):
            from_edge = connection.attrib.get("from", "")
            to_edge = connection.attrib.get("to", "")
            tls_id = connection.attrib.get("tl", "")
            if (
                not from_edge
                or not to_edge
                or from_edge.startswith(":")
                or to_edge.startswith(":")
                or not tls_id
                or from_edge not in edges
                or to_edge not in edges
                or edges[from_edge][1] != owner_id
                or edges[to_edge][0] != owner_id
            ):
                continue
            link = PhysicalControlledLink(
                from_edge,
                int(connection.attrib["fromLane"]),
                to_edge,
                int(connection.attrib["toLane"]),
            )
            if link.key in assigned:
                raise HamburgCompoundOfficialTlsError(
                    f"passive 2394 owner {owner_id} carries an official motor control link"
                )
            controlled.append((link, tls_id))
            demoted[link.key] = link
            if tls_id not in CONTROLLER_BY_NODE.values():
                retired.add(tls_id)
        if not controlled:
            raise HamburgCompoundOfficialTlsError(
                f"passive 2394 owner {owner_id} has no inherited TLS evidence to demote"
            )
        owner_rows.append(
            {
                "owner_id": owner_id,
                "retired_tls_ids": sorted({tls_id for _link, tls_id in controlled}),
                "demoted_link_count": len(controlled),
            }
        )
    augmented = OfficialTlsPlan(
        plan_id=plan.plan_id,
        version=plan.version,
        groups=plan.groups,
        repairs=plan.repairs,
        retired_tls_ids=tuple(sorted(retired)),
        demoted_links=tuple(demoted[key] for key in sorted(demoted)),
    )
    return augmented, {
        "status": "pass",
        "policy": "2394 classification demotes the downstream routing-only TLS owner",
        "owners": owner_rows,
    }


def _audit_compound_candidate(
    net_file: Path,
    plan: OfficialTlsPlan,
) -> dict[str, Any]:
    root = ET.parse(net_file).getroot()
    junctions = {
        element.attrib.get("id", ""): element
        for element in root.findall("junction")
        if element.attrib.get("id")
    }
    edges = {
        edge.attrib.get("id", ""): (
            edge.attrib.get("from", ""),
            edge.attrib.get("to", ""),
        )
        for edge in root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib.get("id", "").startswith(":")
    }
    owner_by_tls: dict[str, set[str]] = {tls_id: set() for tls_id in CONTROLLER_BY_NODE.values()}
    controlled_count_by_tls = Counter()
    for connection in root.findall("connection"):
        tls_id = connection.attrib.get("tl", "")
        from_edge = connection.attrib.get("from", "")
        to_edge = connection.attrib.get("to", "")
        if tls_id not in owner_by_tls or from_edge not in edges or to_edge not in edges:
            continue
        if from_edge.startswith(":") or to_edge.startswith(":"):
            continue
        owner = edges[from_edge][1]
        if owner != edges[to_edge][0]:
            continue
        owner_by_tls[tls_id].add(owner)
        controlled_count_by_tls[tls_id] += 1

    errors: list[str] = []
    expected_owner_by_tls = {
        "HH_2349": set(CONTROLLER_OWNERS_2349),
        "HH_2394": set(SIGNAL_OWNERS_2394),
    }
    for tls_id, expected in expected_owner_by_tls.items():
        if owner_by_tls[tls_id] != expected:
            errors.append(
                f"{tls_id} owners {sorted(owner_by_tls[tls_id])} != {sorted(expected)}"
            )
    for owner_id in (*CONTROLLER_OWNERS_2349, *SIGNAL_OWNERS_2394):
        if owner_id not in junctions or junctions[owner_id].attrib.get("type") != "traffic_light":
            errors.append(f"signal owner {owner_id} is not a traffic_light junction")
    for owner_id in PASSIVE_OWNERS_2394:
        if owner_id not in junctions or junctions[owner_id].attrib.get("type") != "priority":
            errors.append(f"passive owner {owner_id} is not a priority junction")
    logic_ids = [logic.attrib.get("id", "") for logic in root.findall("tlLogic")]
    for tls_id in CONTROLLER_BY_NODE.values():
        if logic_ids.count(tls_id) != 1:
            errors.append(f"expected exactly one tlLogic {tls_id}")
    planned_counts = Counter(
        group.tls_id
        for group in plan.groups
        for _link in group.physical_links
    )
    if controlled_count_by_tls != planned_counts:
        errors.append(
            f"compiled controlled counts {dict(controlled_count_by_tls)} != "
            f"planned {dict(planned_counts)}"
        )
    return {
        "status": "pass" if not errors else "blocked",
        "errors": errors,
        "2349_conflict_core_owner_count": len(CONFLICT_CORE_2349),
        "2394_owner_component_count": len(SIGNAL_OWNERS_2394) + len(PASSIVE_OWNERS_2394),
        "controller_owners": {
            tls_id: sorted(owners) for tls_id, owners in owner_by_tls.items()
        },
        "controlled_connection_count_by_tls": dict(sorted(controlled_count_by_tls.items())),
    }


def _require_hash(path: Path, expected: str, label: str) -> None:
    actual = file_sha256(path)
    if not expected or actual.lower() != expected.lower():
        raise HamburgCompoundOfficialTlsError(
            f"{label} SHA-256 mismatch: expected {expected}, observed {actual}"
        )


def _result_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, Mapping):
        return dict(result)
    if hasattr(result, "to_dict"):
        return dict(result.to_dict())
    raise TypeError(f"unsupported command result: {type(result).__name__}")


def _normalize_node(value: str) -> str:
    text = str(value).strip()
    return str(int(text)) if text.isdigit() else text
