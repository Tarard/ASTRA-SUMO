from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from html import escape
from pathlib import Path
from typing import Any, Iterable, Mapping

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import (
    build_artifact_identity,
    build_review_decision_template,
    file_sha256,
    validate_materialization_evidence,
    validate_review_decision,
    validate_routeability_evidence,
    validate_topology_evidence,
)
from .command_runner import run_command
from .map_review import build_map_review_evidence, validate_map_review_evidence


EDIT_OPERATION_TYPES = (
    "add_edge",
    "add_sidewalk",
    "add_crossing",
    "add_ramp",
    "delete_edge",
    "merge_edges",
    "review_marker",
)

_OPERATION_ALIASES = {
    "add_road": "add_edge",
    "add_path": "add_edge",
    "add_bicycle": "add_edge",
    "add_bikeway": "add_edge",
    "add_pedestrian": "add_sidewalk",
    "add_pedestrian_facility": "add_sidewalk",
    "add_ramp_edge": "add_ramp",
    "delete_road": "delete_edge",
    "delete_path": "delete_edge",
    "remove_edge": "delete_edge",
    "merge_path": "merge_edges",
    "merge_road_fragments": "merge_edges",
    "flag": "review_marker",
    "mark_for_review": "review_marker",
}

_PROTECTED_FUNCTIONS = {"crossing", "walkingarea"}
_RAIL_TOKENS = ("railway", "rail", "tram", "light_rail", "subway", "monorail")
_PROTECTED_EDGE_ATTRS = ("bridge", "tunnel")
_MUTATING_OPERATIONS = {
    "add_edge",
    "add_sidewalk",
    "add_crossing",
    "add_ramp",
    "delete_edge",
    "merge_edges",
}
_ADDITIVE_OPERATIONS = {"add_edge", "add_sidewalk", "add_crossing", "add_ramp"}
_DEFAULT_CONSTRAINTS = {
    "preserve_tls": True,
    "preserve_rail": True,
    "preserve_bridge": True,
    "preserve_tunnel": True,
    "preserve_modal_connectivity": True,
    "preserve_routeability": True,
}
_MODAL_MODES = ("pedestrian", "bicycle")


def propose_corridor_edits(
    net_file: Path,
    *,
    reference_net_file: Path | None = None,
    include_isolated: bool = True,
    include_duplicates: bool = True,
    include_micro_merges: bool = True,
) -> list[dict[str, Any]]:
    """Generate conservative, review-only edit proposals from a SUMO network.

    The proposals model the first human pass: obvious isolated fragments and
    exact duplicates are flagged for deletion, while previously proven
    corridor micro-nodes are proposed as merges. No proposal is applied here.
    """
    root = ET.parse(net_file).getroot()
    edges = _normal_edges(root)
    proposals: list[dict[str, Any]] = []

    if include_isolated:
        incident: dict[str, list[str]] = defaultdict(list)
        for edge_id, edge in edges.items():
            incident[edge.attrib.get("from", "")].append(edge_id)
            incident[edge.attrib.get("to", "")].append(edge_id)
        for edge_id, edge in sorted(edges.items()):
            from_id = edge.attrib.get("from", "")
            to_id = edge.attrib.get("to", "")
            if not from_id or not to_id:
                continue
            if len(incident[from_id]) != 1 or len(incident[to_id]) != 1:
                continue
            if _is_protected_edge(edge, root):
                continue
            proposals.append(
                _proposal(
                    operation_id=f"delete-isolated-{edge_id}",
                    operation="delete_edge",
                    target_ids=[edge_id],
                    rationale="edge is a one-edge connected fragment with no neighboring normal edge",
                    evidence=[
                        {
                            "kind": "topology",
                            "rule": "isolated_normal_edge",
                            "from": from_id,
                            "to": to_id,
                        }
                    ],
                    rollback={"action": "restore_source_edge", "edge_ids": [edge_id]},
                    location=_edge_location(edge),
                )
            )

    if include_duplicates:
        duplicate_groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)
        for edge_id, edge in edges.items():
            if _is_protected_edge(edge, root):
                continue
            duplicate_groups[_edge_duplicate_signature(edge)].append(edge_id)
        for signature, edge_ids in sorted(duplicate_groups.items(), key=lambda item: str(item[0])):
            if len(edge_ids) < 2:
                continue
            for duplicate_id in sorted(edge_ids)[1:]:
                keep_id = sorted(edge_ids)[0]
                proposals.append(
                    _proposal(
                        operation_id=f"delete-duplicate-{duplicate_id}",
                        operation="delete_edge",
                        target_ids=[duplicate_id],
                        rationale="edge is an exact semantic duplicate of a deterministic retained edge",
                        evidence=[
                            {
                                "kind": "duplicate_signature",
                                "retained_edge_id": keep_id,
                                "duplicate_group": sorted(edge_ids),
                                "signature": list(signature),
                            }
                        ],
                        rollback={"action": "restore_source_edge", "edge_ids": [duplicate_id]},
                        location=_edge_location(edges[duplicate_id]),
                    )
                )

    if include_micro_merges:
        try:
            from .corridor_simplification import find_removable_corridor_geometry_nodes

            micro_nodes = find_removable_corridor_geometry_nodes(
                net_file,
                reference_net_file=reference_net_file,
            )
        except (OSError, ET.ParseError, ValueError):
            micro_nodes = []
        junctions = {
            junction.attrib.get("id", ""): junction
            for junction in root.findall("junction")
            if junction.attrib.get("id")
        }
        for candidate in micro_nodes:
            node_id = str(candidate["node_id"])
            incident_ids = [str(edge_id) for edge_id in candidate["incident_edge_ids"]]
            location = _junction_location(junctions.get(node_id))
            proof_evidence = {
                "kind": "corridor_simplification_proof",
                "node_id": node_id,
                "proof": candidate.get("proof", ""),
                "minimum_incident_edge_length_m": candidate.get("minimum_incident_edge_length_m"),
            }
            if _local_controlled_target_edges(root, set(incident_ids)):
                proposals.append(
                    _proposal(
                        operation_id=f"review-tls-micro-node-{node_id}",
                        operation="review_marker",
                        target_ids=incident_ids,
                        rationale="micro-node merge candidate touches a local TLS-controlled connection and must be reviewed instead of auto-applied",
                        evidence=[proof_evidence, {"kind": "safety_gate", "rule": "local_tls_connection"}],
                        rollback={"action": "no_change_until_human_review", "junction_id": node_id},
                        location=location,
                        params={"node_id": node_id, "corridor_ref": candidate.get("corridor_ref", "")},
                    )
                )
                continue
            proposals.append(
                _proposal(
                    operation_id=f"merge-micro-node-{node_id}",
                    operation="merge_edges",
                    target_ids=incident_ids,
                    rationale="reference-absent micro node has identical corridor/lane semantics and straight lane-preserving connections",
                    evidence=[proof_evidence],
                    rollback={
                        "action": "restore_source_edges_and_junction",
                        "edge_ids": incident_ids,
                        "junction_id": node_id,
                    },
                    location=location,
                    params={"node_id": node_id, "corridor_ref": candidate.get("corridor_ref", "")},
                )
            )

    return _deduplicate_operations(proposals)


def build_corridor_edit_ledger(
    *,
    net_file: Path,
    output_dir: Path,
    operations: Iterable[Mapping[str, Any]] | None = None,
    reference_net_file: Path | None = None,
    osm_file: Path | None = None,
    prefix: str = "corridor_edit_ledger",
    include_auto_proposals: bool = True,
    map_temporal_scope: str = "unspecified",
    map_target_date: str | None = None,
) -> dict[str, Any]:
    """Write an auditable, reversible corridor edit candidate.

    This stage intentionally produces a review candidate rather than silently
    mutating the source network. The candidate is ready for a later
    netconvert/SUMO materialization gate.
    """
    if not net_file.exists():
        return _failure(f"net file does not exist: {net_file}")
    if reference_net_file is not None and not reference_net_file.exists():
        return _failure(f"reference net file does not exist: {reference_net_file}")
    try:
        root = ET.parse(net_file).getroot()
    except (OSError, ET.ParseError) as exc:
        return _failure(f"{type(exc).__name__}: {exc}")

    auto_operations = (
        propose_corridor_edits(net_file, reference_net_file=reference_net_file)
        if include_auto_proposals
        else []
    )
    requested_operations = [normalize_edit_operation(operation) for operation in (operations or [])]
    normalized_operations = _deduplicate_operations([*requested_operations, *auto_operations])
    ledger: dict[str, Any] = {
        "schema": "torii.corridor_edit_ledger.v1",
        "source_net_file": str(net_file.resolve()),
        "reference_net_file": str(reference_net_file.resolve()) if reference_net_file else "",
        "osm_file": str(osm_file.resolve()) if osm_file else "",
        "map_review": {
            "temporal_scope": str(map_temporal_scope or "unspecified").strip().lower(),
            "target_date": str(map_target_date or "").strip(),
        },
        "source_inventory": _network_inventory(root),
        "operations": normalized_operations,
        "candidate_variant": {
            "status": "review_only",
            "materialized": False,
            "source_net_file": str(net_file.resolve()),
            "requires_gates": [
                "netconvert",
                "sumo_load",
                "routeability",
                "tls_preservation",
                "rail_bridge_tunnel_preservation",
                "modal_connectivity",
            ],
        },
    }
    validation = validate_edit_ledger(ledger, root=root)
    ledger["validation"] = validation
    ledger["decision_summary"] = _decision_summary(normalized_operations, validation)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_file = output_dir / f"{prefix}.json"
    rollback_file = output_dir / f"{prefix}.rollback.json"
    overlay_file = output_dir / f"{prefix}.review.add.xml"
    manifest_file = output_dir / f"{prefix}.manifest.json"

    rollback = _build_rollback(root, ledger)
    ledger["artifacts"] = {
        "ledger": str(ledger_file),
        "rollback": str(rollback_file),
        "review_overlay": str(overlay_file),
        "manifest": str(manifest_file),
    }
    write_json_atomic(ledger_file, ledger, sort_keys=True)
    write_json_atomic(rollback_file, rollback, sort_keys=True)
    _write_review_overlay(overlay_file, normalized_operations, root)

    artifacts = [
        _artifact_record(ledger_file, "edit_ledger"),
        _artifact_record(rollback_file, "rollback_ledger"),
        _artifact_record(overlay_file, "review_additional_xml"),
    ]
    manifest = {
        "schema": "torii.corridor_edit_manifest.v1",
        "status": validation["status"],
        "claim_status": "diagnostic-demo" if validation["status"] == "pass" else "blocked",
        "candidate_variant_status": "review_only",
        "source_net_file": str(net_file.resolve()),
        "operation_count": len(normalized_operations),
        "accepted_operation_count": sum(1 for item in normalized_operations if item.get("status") == "accepted"),
        "review_operation_count": sum(1 for item in normalized_operations if item.get("status") != "accepted"),
        "artifacts": artifacts,
        "rollback_artifact": str(rollback_file),
        "validation": validation,
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return {
        "status": validation["status"],
        "claim_status": manifest["claim_status"],
        "corridor_edit_ledger_status": "review_candidate",
        "candidate_variant_status": "review_only",
        "operation_count": len(normalized_operations),
        "decision_summary": ledger["decision_summary"],
        "validation": validation,
        "ledger_file": str(ledger_file),
        "rollback_file": str(rollback_file),
        "review_overlay_file": str(overlay_file),
        "manifest_file": str(manifest_file),
        "warnings": [
            "no source network mutation was performed",
            "review overlay contains proposal markers; it is not a substitute for a materialized SUMO network",
        ],
    }


def _write_additive_plain_files(
    *,
    output_dir: Path,
    prefix: str,
    source_root: ET.Element,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write one accepted additive family as netconvert plain XML inputs."""
    junctions = {
        str(junction.attrib.get("id", "")): junction
        for junction in source_root.findall("junction")
        if junction.attrib.get("id")
    }
    existing_edges = set(_normal_edges(source_root))
    edge_operations = [
        operation
        for operation in operations
        if operation["operation"] in {"add_edge", "add_sidewalk", "add_ramp"}
    ]
    crossing_operations = [operation for operation in operations if operation["operation"] == "add_crossing"]
    errors: list[dict[str, Any]] = []
    edge_root = ET.Element("edges")
    generated_edge_ids: set[str] = set()
    generated_edge_records: list[dict[str, Any]] = []

    for operation in edge_operations:
        params = dict(operation.get("params") or {})
        from_id = str(params.get("from", "")).strip()
        to_id = str(params.get("to", "")).strip()
        if from_id not in junctions or to_id not in junctions:
            errors.append(
                {
                    "operation_id": operation["id"],
                    "code": "unknown_addition_endpoint",
                    "from": from_id,
                    "to": to_id,
                }
            )
            continue
        edge_id = str(params.get("id", f"torii-add-{operation['id']}"))
        if edge_id in existing_edges or edge_id in generated_edge_ids:
            errors.append(
                {
                    "operation_id": operation["id"],
                    "code": "addition_edge_id_collision",
                    "edge_id": edge_id,
                }
            )
            continue
        operation_type = operation["operation"]
        edge_type = str(params.get("type", "")).strip()
        edge_type_lower = edge_type.lower()
        default_allow = "pedestrian" if operation_type == "add_sidewalk" else "passenger"
        if operation_type == "add_ramp" and any(
            token in edge_type_lower for token in ("foot", "walk", "sidewalk", "pedestrian")
        ):
            default_allow = "pedestrian"
        if "bicycle" in edge_type_lower or "cycle" in edge_type_lower:
            default_allow = "bicycle pedestrian"
        attributes = {
            "id": edge_id,
            "from": from_id,
            "to": to_id,
            "type": edge_type,
            "priority": str(params.get("priority", "1")),
            "numLanes": str(params.get("lanes", "1")),
            "speed": str(
                params.get(
                    "speed",
                    "1.4" if operation_type == "add_sidewalk" else "13.9",
                )
            ),
            "allow": str(params.get("allow", default_allow)),
        }
        for key in ("disallow", "width", "name", "ref", "shape"):
            if str(params.get(key, "")).strip():
                attributes[key] = str(params[key])
        ET.SubElement(edge_root, "edge", attributes)
        generated_edge_ids.add(edge_id)
        generated_edge_records.append(
            {
                "operation_id": operation["id"],
                "operation": operation_type,
                "edge_id": edge_id,
                "from": from_id,
                "to": to_id,
                "type": edge_type,
                "allow": attributes["allow"],
            }
        )

    connection_root = ET.Element("connections")
    generated_crossing_records: list[dict[str, Any]] = []
    for operation in crossing_operations:
        params = dict(operation.get("params") or {})
        node_id = str(params.get("node_id", "")).strip()
        crossing_edges = params.get("crossing_edges", operation.get("target_ids", []))
        if isinstance(crossing_edges, str):
            crossing_edges = crossing_edges.replace(",", " ").split()
        crossing_edges = [str(edge_id) for edge_id in (crossing_edges or []) if str(edge_id).strip()]
        missing_edges = sorted(set(crossing_edges) - existing_edges - generated_edge_ids)
        if node_id not in junctions:
            errors.append({"operation_id": operation["id"], "code": "unknown_crossing_node", "node_id": node_id})
            continue
        if not crossing_edges or missing_edges:
            errors.append(
                {
                    "operation_id": operation["id"],
                    "code": "unknown_crossing_edges" if missing_edges else "missing_crossing_edges",
                    "edge_ids": missing_edges,
                }
            )
            continue
        attributes = {
            "node": node_id,
            "edges": " ".join(crossing_edges),
            "width": str(params.get("width", "4.0")),
            "priority": str(params.get("priority", "1")),
        }
        if str(params.get("tl", "")).strip():
            attributes["tl"] = str(params["tl"])
        ET.SubElement(connection_root, "crossing", attributes)
        generated_crossing_records.append(
            {
                "operation_id": operation["id"],
                "node_id": node_id,
                "crossing_edges": crossing_edges,
            }
        )

    if errors:
        return {"status": "blocked", "errors": errors}

    edge_file: Path | None = None
    if edge_operations:
        edge_file = output_dir / f"{prefix}.add.edg.xml"
        ET.indent(edge_root, space="  ")
        ET.ElementTree(edge_root).write(edge_file, encoding="utf-8", xml_declaration=True)
    connection_file: Path | None = None
    if crossing_operations:
        connection_file = output_dir / f"{prefix}.add.con.xml"
        ET.indent(connection_root, space="  ")
        ET.ElementTree(connection_root).write(connection_file, encoding="utf-8", xml_declaration=True)

    return {
        "status": "pass",
        "edge_file": edge_file,
        "connection_file": connection_file,
        "edge_records": generated_edge_records,
        "crossing_records": generated_crossing_records,
    }


def materialize_corridor_edit_variant(
    *,
    output_dir: Path,
    ledger_file: Path | None = None,
    ledger: Mapping[str, Any] | None = None,
    netconvert_binary: str = "netconvert",
    prefix: str = "corridor_materialized_variant",
    timeout_seconds: float = 240.0,
    command_runner: Any = run_command,
    map_temporal_scope: str | None = None,
    map_target_date: str | None = None,
) -> dict[str, Any]:
    """Materialize a narrowly supported, explicitly accepted edit variant.

    The ledger remains the authority for what may be applied. One operation
    family is materialized per candidate: destructive corridor operations
    (``merge_edges``/``delete_edge``) or a compatible family of additive plain-file
    operations (``add_edge``/``add_sidewalk``/``add_ramp``/``add_crossing``). Additive
    output is still review-only and is rejected if the requested modal
    structure is not present after netconvert. The source network is never
    overwritten.
    """
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_file = output_dir / f"{prefix}.json"
    manifest_file = output_dir / f"{prefix}.manifest.json"
    command_file = output_dir / f"{prefix}.command.txt"
    review_file = output_dir / f"{prefix}.review.json"

    loaded_ledger: Mapping[str, Any] | None = ledger
    if loaded_ledger is None and ledger_file is not None:
        try:
            loaded_ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return _write_materialization_outcome(
                output_dir=output_dir,
                report_file=report_file,
                manifest_file=manifest_file,
                command_file=command_file,
                report={
                    "schema": "torii.corridor_materialization.v1",
                    "status": "blocked",
                    "claim_status": "construction-invalid",
                    "materialization_status": "invalid_ledger_file",
                    "error": f"{type(exc).__name__}: {exc}",
                    "warnings": ["no candidate network was created"],
                },
                source_net_file=None,
                ledger_file=ledger_file,
            )
    if loaded_ledger is None:
        return _write_materialization_outcome(
            output_dir=output_dir,
            report_file=report_file,
            manifest_file=manifest_file,
            command_file=command_file,
            report={
                "schema": "torii.corridor_materialization.v1",
                "status": "blocked",
                "claim_status": "construction-invalid",
                "materialization_status": "ledger_required",
                "error": "ledger_file or ledger mapping is required",
                "warnings": ["no candidate network was created"],
            },
            source_net_file=None,
            ledger_file=ledger_file,
        )

    ledger_map_review = loaded_ledger.get("map_review")
    if not isinstance(ledger_map_review, Mapping):
        ledger_map_review = {}
    resolved_map_temporal_scope = str(
        map_temporal_scope
        if map_temporal_scope is not None
        else ledger_map_review.get(
            "temporal_scope",
            loaded_ledger.get("map_temporal_scope", "unspecified"),
        )
    ).strip().lower() or "unspecified"
    resolved_map_target_date = str(
        map_target_date
        if map_target_date is not None
        else ledger_map_review.get(
            "target_date",
            loaded_ledger.get("map_target_date", ""),
        )
    ).strip()

    source_text = str(loaded_ledger.get("source_net_file", "")).strip()
    source_net_file = Path(source_text) if source_text else None
    if source_net_file is None or not source_net_file.exists():
        return _write_materialization_outcome(
            output_dir=output_dir,
            report_file=report_file,
            manifest_file=manifest_file,
            command_file=command_file,
            report={
                "schema": "torii.corridor_materialization.v1",
                "status": "blocked",
                "claim_status": "construction-invalid",
                "materialization_status": "source_net_missing",
                "source_net_file": source_text,
                "warnings": ["no candidate network was created"],
            },
            source_net_file=source_net_file,
            ledger_file=ledger_file,
        )
    try:
        source_root = ET.parse(source_net_file).getroot()
    except (OSError, ET.ParseError) as exc:
        return _write_materialization_outcome(
            output_dir=output_dir,
            report_file=report_file,
            manifest_file=manifest_file,
            command_file=command_file,
            report={
                "schema": "torii.corridor_materialization.v1",
                "status": "blocked",
                "claim_status": "construction-invalid",
                "materialization_status": "source_net_invalid",
                "source_net_file": str(source_net_file),
                "error": f"{type(exc).__name__}: {exc}",
                "warnings": ["no candidate network was created"],
            },
            source_net_file=source_net_file,
            ledger_file=ledger_file,
        )

    validation = validate_edit_ledger(loaded_ledger, root=source_root)
    operations = [normalize_edit_operation(operation) for operation in (loaded_ledger.get("operations") or [])]
    accepted = [operation for operation in operations if operation.get("status") == "accepted"]
    accepted_mutating = [operation for operation in accepted if operation["operation"] in _MUTATING_OPERATIONS]
    supported_operation_types = {
        "delete_edge",
        "merge_edges",
        "add_edge",
        "add_sidewalk",
        "add_ramp",
        "add_crossing",
    }
    unsupported = [
        operation for operation in accepted_mutating if operation["operation"] not in supported_operation_types
    ]
    if validation["status"] != "pass":
        return _write_materialization_outcome(
            output_dir=output_dir,
            report_file=report_file,
            manifest_file=manifest_file,
            command_file=command_file,
            report={
                "schema": "torii.corridor_materialization.v1",
                "status": "blocked",
                "claim_status": "construction-invalid",
                "materialization_status": "ledger_validation_failed",
                "source_net_file": str(source_net_file.resolve()),
                "validation": validation,
                "warnings": ["no candidate network was created"],
            },
            source_net_file=source_net_file,
            ledger_file=ledger_file,
        )
    if not accepted_mutating:
        return _write_materialization_outcome(
            output_dir=output_dir,
            report_file=report_file,
            manifest_file=manifest_file,
            command_file=command_file,
            report={
                "schema": "torii.corridor_materialization.v1",
                "status": "blocked",
                "claim_status": "blocked",
                "materialization_status": "no_accepted_operations",
                "source_net_file": str(source_net_file.resolve()),
                "validation": validation,
                "accepted_operation_count": len(accepted),
                "accepted_mutating_operation_count": 0,
                "warnings": ["candidate materialization requires an explicitly accepted mutating operation"],
            },
            source_net_file=source_net_file,
            ledger_file=ledger_file,
        )
    if unsupported:
        return _write_materialization_outcome(
            output_dir=output_dir,
            report_file=report_file,
            manifest_file=manifest_file,
            command_file=command_file,
            report={
                "schema": "torii.corridor_materialization.v1",
                "status": "blocked",
                "claim_status": "blocked",
                "materialization_status": "unsupported_accepted_operations",
                "source_net_file": str(source_net_file.resolve()),
                "unsupported_operations": [
                    {"id": operation["id"], "operation": operation["operation"]}
                    for operation in unsupported
                ],
                "warnings": [
                    "the accepted operation is not supported by the current plain-file materializer",
                    "no candidate network was created",
                ],
            },
            source_net_file=source_net_file,
            ledger_file=ledger_file,
        )

    operation_types = {operation["operation"] for operation in accepted_mutating}
    if operation_types <= _ADDITIVE_OPERATIONS:
        operation_family = "additive"
    elif len(operation_types) == 1 and operation_types <= {"delete_edge", "merge_edges"}:
        operation_family = next(iter(operation_types))
    else:
        return _write_materialization_outcome(
            output_dir=output_dir,
            report_file=report_file,
            manifest_file=manifest_file,
            command_file=command_file,
            report={
                "schema": "torii.corridor_materialization.v1",
                "status": "blocked",
                "claim_status": "blocked",
                "materialization_status": "mixed_operation_family",
                "source_net_file": str(source_net_file.resolve()),
                "accepted_operation_types": sorted(operation_types),
                "warnings": [
                    "destructive and additive operations must be materialized as separate candidate variants",
                    "no candidate network was created",
                ],
            },
            source_net_file=source_net_file,
            ledger_file=ledger_file,
        )

    edges = _normal_edges(source_root)
    target_ids = sorted({edge_id for operation in accepted_mutating for edge_id in operation["target_ids"]})
    missing_targets = sorted(set(target_ids) - set(edges))
    target_required = operation_family in {"delete_edge", "merge_edges"}
    if target_required and missing_targets:
        return _write_materialization_outcome(
            output_dir=output_dir,
            report_file=report_file,
            manifest_file=manifest_file,
            command_file=command_file,
            report={
                "schema": "torii.corridor_materialization.v1",
                "status": "blocked",
                "claim_status": "construction-invalid",
                "materialization_status": "unknown_target_edges",
                "source_net_file": str(source_net_file.resolve()),
                "edge_ids": missing_targets,
                "warnings": ["no candidate network was created"],
            },
            source_net_file=source_net_file,
            ledger_file=ledger_file,
        )
    if target_required and not target_ids:
        return _write_materialization_outcome(
            output_dir=output_dir,
            report_file=report_file,
            manifest_file=manifest_file,
            command_file=command_file,
            report={
                "schema": "torii.corridor_materialization.v1",
                "status": "blocked",
                "claim_status": "blocked",
                "materialization_status": "empty_target_set",
                "source_net_file": str(source_net_file.resolve()),
                "warnings": ["no candidate network was created"],
            },
            source_net_file=source_net_file,
            ledger_file=ledger_file,
        )

    candidate_net_file = output_dir / f"{prefix}.net.xml"
    if candidate_net_file.resolve() == source_net_file.resolve():
        return _write_materialization_outcome(
            output_dir=output_dir,
            report_file=report_file,
            manifest_file=manifest_file,
            command_file=command_file,
            report={
                "schema": "torii.corridor_materialization.v1",
                "status": "blocked",
                "claim_status": "construction-invalid",
                "materialization_status": "source_overwrite_forbidden",
                "source_net_file": str(source_net_file.resolve()),
                "warnings": ["the source network is never overwritten"],
            },
            source_net_file=source_net_file,
            ledger_file=ledger_file,
        )

    plan_file: Path
    additional_plan_files: list[tuple[Path, str]] = []
    extra_artifacts: list[tuple[Path, str]] = []
    additive_plan: dict[str, Any] | None = None
    if operation_family == "merge_edges":
        keep_ids = sorted(set(edges) - set(target_ids))
        if not keep_ids:
            return _write_materialization_outcome(
                output_dir=output_dir,
                report_file=report_file,
                manifest_file=manifest_file,
                command_file=command_file,
                report={
                    "schema": "torii.corridor_materialization.v1",
                    "status": "blocked",
                    "claim_status": "blocked",
                    "materialization_status": "empty_keep_edge_set",
                    "source_net_file": str(source_net_file.resolve()),
                    "target_edge_ids": target_ids,
                    "warnings": ["netconvert geometry removal requires at least one retained normal edge"],
                },
                source_net_file=source_net_file,
                ledger_file=ledger_file,
            )
        plan_file = output_dir / f"{prefix}.keep-edges.txt"
        write_text_atomic(plan_file, "\n".join(keep_ids) + "\n")
        command = [
            netconvert_binary,
            "--sumo-net-file",
            str(source_net_file.resolve()),
            "--geometry.remove",
            "--geometry.remove.keep-edges.input-file",
            str(plan_file.resolve()),
            "--geometry.remove.keep-ptstops",
            "--output.removed-nodes",
            "--output-file",
            str(candidate_net_file.resolve()),
        ]
        plan_description = "keep_edges"
    elif operation_family == "delete_edge":
        plan_file = output_dir / f"{prefix}.remove-edges.txt"
        write_text_atomic(plan_file, "\n".join(target_ids) + "\n")
        command = [
            netconvert_binary,
            "--sumo-net-file",
            str(source_net_file.resolve()),
            "--remove-edges.input-file",
            str(plan_file.resolve()),
            "--output-file",
            str(candidate_net_file.resolve()),
        ]
        plan_description = "remove_edges"
    else:
        additive_plan = _write_additive_plain_files(
            output_dir=output_dir,
            prefix=prefix,
            source_root=source_root,
            operations=accepted_mutating,
        )
        if additive_plan.get("status") != "pass":
            return _write_materialization_outcome(
                output_dir=output_dir,
                report_file=report_file,
                manifest_file=manifest_file,
                command_file=command_file,
                report={
                    "schema": "torii.corridor_materialization.v1",
                    "status": "blocked",
                    "claim_status": "construction-invalid",
                    "materialization_status": "invalid_additive_plain_plan",
                    "source_net_file": str(source_net_file.resolve()),
                    "operation_family": operation_family,
                    "validation": validation,
                    "errors": additive_plan.get("errors", []),
                    "warnings": ["no candidate network was created"],
                },
                source_net_file=source_net_file,
                ledger_file=ledger_file,
            )
        plan_files = [
            (path, "additive_plain_input")
            for path in (additive_plan.get("edge_file"), additive_plan.get("connection_file"))
            if isinstance(path, Path)
        ]
        if not plan_files:
            return _write_materialization_outcome(
                output_dir=output_dir,
                report_file=report_file,
                manifest_file=manifest_file,
                command_file=command_file,
                report={
                    "schema": "torii.corridor_materialization.v1",
                    "status": "blocked",
                    "claim_status": "construction-invalid",
                    "materialization_status": "empty_additive_plain_plan",
                    "source_net_file": str(source_net_file.resolve()),
                    "warnings": ["no candidate network was created"],
                },
                source_net_file=source_net_file,
                ledger_file=ledger_file,
            )
        plan_file = plan_files[0][0]
        additional_plan_files = plan_files[1:]
        command = [
            netconvert_binary,
            "--sumo-net-file",
            str(source_net_file.resolve()),
        ]
        if isinstance(additive_plan.get("edge_file"), Path):
            command.extend(["--edge-files", str(additive_plan["edge_file"].resolve())])
        if isinstance(additive_plan.get("connection_file"), Path):
            command.extend(["--connection-files", str(additive_plan["connection_file"].resolve())])
        command.extend(["--walkingareas", "--output-file", str(candidate_net_file.resolve())])
        plan_description = "additive_plain_xml"
    write_text_atomic(command_file, " ".join(_quote_command_token(token) for token in command) + "\n")

    stale_candidate_removed = False
    try:
        if candidate_net_file.exists():
            candidate_net_file.unlink()
            stale_candidate_removed = True
    except OSError as exc:
        return _write_materialization_outcome(
            output_dir=output_dir,
            report_file=report_file,
            manifest_file=manifest_file,
            command_file=command_file,
            report={
                "schema": "torii.corridor_materialization.v1",
                "status": "blocked",
                "claim_status": "construction-invalid",
                "materialization_status": "stale_candidate_cleanup_failed",
                "source_net_file": str(source_net_file.resolve()),
                "candidate_net_file": str(candidate_net_file.resolve()),
                "operation_family": operation_family,
                "command": command,
                "error": f"{type(exc).__name__}: {exc}",
                "warnings": ["the pre-existing candidate could not be removed; netconvert was not run"],
            },
            source_net_file=source_net_file,
            ledger_file=ledger_file,
            plan_file=plan_file,
            additional_plan_files=additional_plan_files,
            extra_artifacts=extra_artifacts,
        )

    try:
        command_result = _result_to_dict(
            command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds)
        )
    except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
        command_result = {
            "status": "fail",
            "returncode": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    command_status = (
        command_result.get("status") == "pass"
        and type(command_result.get("returncode")) is int
        and command_result.get("returncode") == 0
        and candidate_net_file.is_file()
    )
    pedestrian_restore_report: list[dict[str, Any]] = []
    if command_status and additive_plan is not None:
        touched_junction_ids = {
            str(record.get("from", ""))
            for record in additive_plan.get("edge_records", [])
            if str(record.get("from", ""))
        }
        touched_junction_ids.update(
            str(record.get("to", ""))
            for record in additive_plan.get("edge_records", [])
            if str(record.get("to", ""))
        )
        touched_junction_ids.update(
            str(record.get("node_id", ""))
            for record in additive_plan.get("crossing_records", [])
            if str(record.get("node_id", ""))
        )
        try:
            from .junction_rebuild.pedestrians import restore_scoped_pedestrian_internal_semantics_after_normalize

            for junction_id in sorted(touched_junction_ids):
                pedestrian_restore_report.append(
                    dict(
                        restore_scoped_pedestrian_internal_semantics_after_normalize(
                            source_net_file=source_net_file,
                            target_net_file=candidate_net_file,
                            junction_id=junction_id,
                        )
                    )
                )
        except (ImportError, OSError, ET.ParseError, TypeError, ValueError) as exc:
            pedestrian_restore_report.append(
                {
                    "status": "blocked",
                    "error": f"{type(exc).__name__}: {exc}",
                    "policy": "scoped pedestrian restore failed closed",
                }
            )
    pedestrian_restore_status = "pass" if all(
        report.get("status") == "pass" for report in pedestrian_restore_report
    ) else "blocked"
    candidate_root: ET.Element | None = None
    semantic_validation: dict[str, Any] = {
        "status": "blocked",
        "reason": "netconvert did not create a successful candidate",
    }
    if command_status:
        try:
            candidate_root = ET.parse(candidate_net_file).getroot()
            semantic_validation = {"status": "pass", "candidate_xml_status": "pass"}
            if additive_plan is not None:
                candidate_edges = _normal_edges(candidate_root)
                expected_edge_ids = [record["edge_id"] for record in additive_plan.get("edge_records", [])]
                missing_added_edges = sorted(set(expected_edge_ids) - set(candidate_edges))
                source_counts = _protected_semantic_counts(source_root)
                candidate_counts = _protected_semantic_counts(candidate_root)
                protected_decreases = {
                    key: (source_counts[key], candidate_counts.get(key, 0))
                    for key in source_counts
                    if candidate_counts.get(key, 0) < source_counts[key]
                }
                crossing_expected = len(additive_plan.get("crossing_records", []))
                source_crossing_count = source_counts.get("crossing_edge_count", 0)
                candidate_crossing_count = candidate_counts.get("crossing_edge_count", 0)
                crossing_shortfall = max(0, source_crossing_count + crossing_expected - candidate_crossing_count)
                modal_addition_report = _modal_addition_gate(
                    source_root,
                    candidate_root,
                    additive_plan.get("edge_records", []),
                )
                semantic_validation = {
                    "status": "pass"
                    if not missing_added_edges
                    and not protected_decreases
                    and crossing_shortfall == 0
                    and pedestrian_restore_status == "pass"
                    and modal_addition_report.get("status") == "pass"
                    else "blocked",
                    "candidate_xml_status": "pass",
                    "expected_added_edge_ids": expected_edge_ids,
                    "missing_added_edge_ids": missing_added_edges,
                    "source_protected_counts": source_counts,
                    "candidate_protected_counts": candidate_counts,
                    "protected_count_decreases": protected_decreases,
                    "crossing_expected_count": crossing_expected,
                    "crossing_shortfall": crossing_shortfall,
                    "pedestrian_restore_status": pedestrian_restore_status,
                    "modal_connectivity": modal_addition_report,
                }
        except (OSError, ET.ParseError) as exc:
            semantic_validation = {"status": "blocked", "error": f"{type(exc).__name__}: {exc}"}

    artifact_identity = (
        build_artifact_identity(source_net_file, candidate_net_file)
        if candidate_net_file.exists()
        else {
            "status": "blocked",
            "source_net_file": str(source_net_file.resolve()),
            "candidate_net_file": str(candidate_net_file.resolve()),
            "source_sha256": "",
            "candidate_sha256": "",
            "errors": [{"code": "candidate_artifact_missing"}],
        }
    )
    map_review_file = output_dir / f"{prefix}.map-review.json"
    accepted_overlay_file = output_dir / f"{prefix}.accepted.review.add.xml"
    map_review_evidence: dict[str, Any] = {}
    map_review_validation: dict[str, Any] = {
        "status": "blocked",
        "review_readiness_status": "blocked",
        "required_location_ids": [],
        "errors": [{"code": "candidate_not_ready_for_map_review"}],
    }
    map_review_evidence_sha256 = ""
    map_review_error = ""
    overlay_status = "blocked"
    overlay_error = ""
    if candidate_root is not None and artifact_identity.get("status") == "pass":
        try:
            map_review_evidence = build_map_review_evidence(
                source_net_file=source_net_file,
                candidate_net_file=candidate_net_file,
                candidate_sha256=str(artifact_identity.get("candidate_sha256", "")),
                locations=_map_review_locations(accepted_mutating, source_root),
                temporal_scope=resolved_map_temporal_scope,
                target_date=resolved_map_target_date or None,
            )
            write_json_atomic(map_review_file, map_review_evidence, sort_keys=True)
            map_review_evidence_sha256 = file_sha256(map_review_file)
            map_review_validation = validate_map_review_evidence(
                map_review_evidence,
                source_net_file=source_net_file,
                candidate_net_file=candidate_net_file,
                evidence_file=map_review_file,
                evidence_sha256=map_review_evidence_sha256,
            )
            extra_artifacts.append((map_review_file, "map_review_evidence"))
        except (OSError, ET.ParseError, TypeError, ValueError) as exc:
            map_review_error = f"{type(exc).__name__}: {exc}"
            map_review_validation = {
                "status": "blocked",
                "review_readiness_status": "blocked",
                "required_location_ids": [],
                "errors": [{"code": "map_review_evidence_build_failed", "error": map_review_error}],
            }

        if map_review_validation.get("status") == "pass":
            try:
                overlay_result = _write_review_overlay(
                    accepted_overlay_file,
                    accepted_mutating,
                    source_root,
                    candidate_root=candidate_root,
                    map_review_evidence=map_review_evidence,
                    candidate_sha256=str(artifact_identity.get("candidate_sha256", "")),
                    map_review_evidence_file=map_review_file,
                    map_review_evidence_sha256=map_review_evidence_sha256,
                )
                extra_artifacts.append((accepted_overlay_file, "accepted_review_additional_xml"))
                overlay_status = str(overlay_result.get("status", "blocked"))
                if overlay_status != "pass":
                    overlay_error = "missing SUMO review markers for operation ids: " + ", ".join(
                        overlay_result.get("missing_operation_ids", [])
                    )
            except (OSError, TypeError, ValueError) as exc:
                overlay_error = f"{type(exc).__name__}: {exc}"

    review_html_file = output_dir / f"{prefix}.review.html"
    review_html_status = "blocked"
    review_html_error = ""
    review_template: dict[str, Any] = {}
    review_template_status = "blocked"
    review_template_error = ""
    if (
        candidate_root is not None
        and artifact_identity.get("status") == "pass"
        and map_review_validation.get("status") == "pass"
    ):
        source_counts = _protected_semantic_counts(source_root)
        candidate_counts = _protected_semantic_counts(candidate_root)
        semantic_allowances = {
            key: candidate_counts[key] - source_counts[key]
            for key in source_counts
            if candidate_counts.get(key, 0) > source_counts[key]
        }
        tls_comparison = _compare_tls_logic_semantics(source_root, candidate_root)
        candidate_tls_signatures = tls_comparison.get("candidate_signature", {})
        tls_logic_allowances = {
            logic_id: candidate_tls_signatures[logic_id]
            for logic_id in tls_comparison.get("differences", {})
            if logic_id in candidate_tls_signatures
        }
        review_template = build_review_decision_template(
            source_net_file=source_net_file,
            candidate_net_file=candidate_net_file,
            semantic_allowances=semantic_allowances,
            tls_logic_allowances=tls_logic_allowances,
            map_review_evidence=map_review_evidence,
            map_review_evidence_file=map_review_file,
            map_review_evidence_sha256=map_review_evidence_sha256,
        )
        try:
            write_json_atomic(review_file, review_template, sort_keys=True)
            extra_artifacts.append((review_file, "candidate_review_decision_template"))
            review_template_status = "pass"
        except OSError as exc:
            review_template_error = f"{type(exc).__name__}: {exc}"

    if review_template_status == "pass" and overlay_status == "pass":
        try:
            _write_candidate_review_html(
                review_html_file,
                operations=accepted_mutating,
                map_review_evidence=map_review_evidence,
                artifact_identity=artifact_identity,
                map_review_evidence_file=map_review_file,
                map_review_evidence_sha256=map_review_evidence_sha256,
                review_overlay_file=accepted_overlay_file,
                review_decision_file=review_file,
                review_required=bool(review_template.get("review_required")),
            )
            extra_artifacts.append((review_html_file, "candidate_review_html"))
            review_html_status = "pass"
        except (OSError, TypeError, ValueError) as exc:
            review_html_error = f"{type(exc).__name__}: {exc}"

    final_status = (
        command_status
        and semantic_validation.get("status") == "pass"
        and artifact_identity.get("status") == "pass"
        and map_review_validation.get("status") == "pass"
        and overlay_status == "pass"
        and review_template_status == "pass"
        and review_html_status == "pass"
    )
    if final_status:
        materialization_status = "variant_created_for_review"
    elif not command_status:
        materialization_status = "netconvert_failed"
    elif artifact_identity.get("status") != "pass":
        materialization_status = "artifact_identity_failed"
    elif semantic_validation.get("status") != "pass":
        materialization_status = "semantic_gate_failed"
    elif map_review_validation.get("status") != "pass":
        materialization_status = "map_review_evidence_failed"
    elif overlay_status != "pass":
        materialization_status = "review_overlay_failed"
    elif review_template_status != "pass":
        materialization_status = "review_template_failed"
    else:
        materialization_status = "review_html_failed"
    report = {
        "schema": "torii.corridor_materialization.v1",
        "status": "pass" if final_status else "blocked",
        "claim_status": "diagnostic-demo" if final_status else "construction-invalid",
        "materialization_status": materialization_status,
        "source_net_file": str(source_net_file.resolve()),
        "candidate_net_file": str(candidate_net_file.resolve()) if candidate_net_file.exists() else "",
        "source_sha256": artifact_identity.get("source_sha256", ""),
        "candidate_sha256": artifact_identity.get("candidate_sha256", ""),
        "artifact_identity": artifact_identity,
        "ledger_file": str(ledger_file.resolve()) if ledger_file else "",
        "operation_family": operation_family,
        "operation_ids": [operation["id"] for operation in accepted_mutating],
        "target_edge_ids": target_ids,
        "plan_description": plan_description,
        "plan_file": str(plan_file),
        "additional_plan_files": [str(path) for path, _kind in additional_plan_files],
        "accepted_review_additional_xml": str(accepted_overlay_file)
        if accepted_overlay_file.exists()
        else "",
        "accepted_review_additional_xml_status": overlay_status,
        "accepted_review_additional_xml_error": overlay_error,
        "map_review_evidence_file": str(map_review_file) if map_review_file.exists() else "",
        "map_review_evidence_sha256": map_review_evidence_sha256,
        "map_review_evidence_status": map_review_validation.get("status", "blocked"),
        "map_review_readiness_status": map_review_validation.get(
            "review_readiness_status",
            "blocked",
        ),
        "map_review_required_location_ids": list(
            map_review_validation.get("required_location_ids", [])
        ),
        "map_review_errors": list(map_review_validation.get("errors", [])),
        "map_review_error": map_review_error,
        "map_temporal_scope": resolved_map_temporal_scope,
        "map_target_date": resolved_map_target_date,
        "candidate_review_html_file": str(review_html_file) if review_html_file.exists() else "",
        "candidate_review_html_status": review_html_status,
        "candidate_review_html_error": review_html_error,
        "command_file": str(command_file),
        "command": command,
        "command_result": command_result,
        "stale_candidate_removed": stale_candidate_removed,
        "pedestrian_restore": pedestrian_restore_report,
        "semantic_validation": semantic_validation,
        "review_decision_template_file": str(review_file) if review_file.exists() else "",
        "review_template_status": review_template_status,
        "review_template_error": review_template_error,
        "review_required": bool(review_template.get("review_required")),
        "validation": validation,
        "candidate_variant_status": "review_only",
        "warnings": [
            "source network was not modified",
            "candidate must pass SUMO load, routeability, topology, and semantic gates before promotion",
        ],
    }
    return _write_materialization_outcome(
        output_dir=output_dir,
        report_file=report_file,
        manifest_file=manifest_file,
        command_file=command_file,
        report=report,
        source_net_file=source_net_file,
        candidate_net_file=candidate_net_file if candidate_net_file.exists() else None,
        ledger_file=ledger_file,
        plan_file=plan_file,
        additional_plan_files=additional_plan_files,
        extra_artifacts=extra_artifacts,
    )


def run_corridor_candidate_gates(
    *,
    source_net_file: Path,
    candidate_net_file: Path,
    output_dir: Path,
    materialization_report: Mapping[str, Any] | None = None,
    review_decision: Mapping[str, Any] | None = None,
    osm_file: Path | None = None,
    prefix: str = "corridor_candidate_gates",
    sumo_binary: str = "sumo",
    vehicle_count: int = 20,
    initial_end: int = 300,
    max_end: int = 1200,
    timeout_seconds: float = 240.0,
    routeability_audit_func: Any | None = None,
    topology_audit_func: Any | None = None,
    command_runner: Any = run_command,
) -> dict[str, Any]:
    """Run promotion gates for an already materialized corridor candidate.

    The materialization report and optional review decision are hash-bound to
    the source and candidate. Raw semantic allowances are deliberately not
    accepted by this boundary.
    """
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_file = output_dir / f"{prefix}.json"
    manifest_file = output_dir / f"{prefix}.manifest.json"

    artifact_identity = build_artifact_identity(source_net_file, candidate_net_file)
    materialization_evidence = validate_materialization_evidence(
        materialization_report,
        source_net_file=source_net_file,
        candidate_net_file=candidate_net_file,
    )
    map_review_evidence_value = materialization_evidence.get("map_review_evidence")
    map_review_evidence = (
        map_review_evidence_value if isinstance(map_review_evidence_value, Mapping) else None
    )
    map_review_evidence_file_value = str(
        materialization_evidence.get("map_review_evidence_file", "")
    ).strip()
    map_review_evidence_file = (
        Path(map_review_evidence_file_value) if map_review_evidence_file_value else None
    )
    review_evidence = validate_review_decision(
        review_decision,
        source_net_file=source_net_file,
        candidate_net_file=candidate_net_file,
        map_review_evidence=map_review_evidence,
        map_review_evidence_file=map_review_evidence_file,
        map_review_evidence_sha256=str(
            materialization_evidence.get("map_review_evidence_sha256", "")
        ),
    )
    semantic_allowances = review_evidence.get("semantic_allowances", {})
    tls_logic_allowances = review_evidence.get("tls_logic_allowances", {})

    xml_errors: list[dict[str, Any]] = []
    source_root: ET.Element | None = None
    candidate_root: ET.Element | None = None
    for role, path in (("source", source_net_file), ("candidate", candidate_net_file)):
        try:
            root = ET.parse(path).getroot()
            if role == "source":
                source_root = root
            else:
                candidate_root = root
        except (OSError, ET.ParseError) as exc:
            xml_errors.append(
                {
                    "code": f"{role}_net_xml_invalid",
                    "path": str(path.resolve()),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    xml_gate = {"status": "pass" if not xml_errors else "blocked", "errors": xml_errors}

    if source_root is not None and candidate_root is not None:
        semantic_report = _candidate_semantic_gate(
            source_root,
            candidate_root,
            source_net_file,
            candidate_net_file,
            allowed_deltas=semantic_allowances,
            allowed_tls_logic_changes=tls_logic_allowances,
        )
        modal_report = _modal_connectivity_gate(source_root, candidate_root)
    else:
        semantic_report = _not_run_gate("source or candidate XML could not be parsed")
        modal_report = _not_run_gate("source or candidate XML could not be parsed")

    preflight_gates = {
        "artifact_identity": artifact_identity,
        "netconvert": materialization_evidence,
        "review_contract": review_evidence,
        "xml_parse": xml_gate,
        "tls_modal_rail_bridge_tunnel": semantic_report,
        "modal_connectivity": modal_report,
    }
    preflight_pass = all(gate.get("status") == "pass" for gate in preflight_gates.values())

    sumo_load_gate = _not_run_gate("candidate preflight did not pass")
    routeability_gate = _not_run_gate("candidate preflight did not pass")
    topology_gate = _not_run_gate("candidate preflight did not pass")
    routeability_report: dict[str, Any] = {}
    topology_report: dict[str, Any] = {}

    if preflight_pass:
        load_file = output_dir / f"{prefix}_sumo_load.txt"
        load_command = [
            sumo_binary,
            "--net-file",
            str(candidate_net_file.resolve()),
            "--quit-on-end",
            "--duration-log.disable",
            "--no-step-log",
            "--error-log",
            str(load_file),
        ]
        load_cleanup_error = ""
        try:
            if load_file.exists():
                load_file.unlink()
        except OSError as exc:
            load_cleanup_error = f"{type(exc).__name__}: {exc}"
        if load_cleanup_error:
            load_result = {"status": "fail", "returncode": None, "error": load_cleanup_error}
        else:
            try:
                load_result = _result_to_dict(
                    command_runner(load_command, cwd=output_dir, timeout_seconds=timeout_seconds)
                )
            except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
                load_result = {
                    "status": "fail",
                    "returncode": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        load_pass = (
            load_result.get("status") == "pass"
            and type(load_result.get("returncode")) is int
            and load_result.get("returncode") == 0
        )
        sumo_load_gate = {
            "status": "pass" if load_pass else "blocked",
            "command": load_command,
            "result": load_result,
            "log_file": str(load_file),
        }

        candidate_token = str(artifact_identity.get("candidate_sha256", ""))[:12] or "unknown"
        if routeability_audit_func is None:
            from .routeability_audit import run_routeability_audit

            routeability_audit_func = run_routeability_audit
        try:
            raw_routeability = routeability_audit_func(
                net_file=candidate_net_file,
                output_dir=output_dir / f"routeability_{candidate_token}",
                prefix=f"{prefix}_routeability",
                vehicle_count=vehicle_count,
                initial_end=initial_end,
                max_end=max_end,
                timeout_seconds=timeout_seconds,
            )
            if not isinstance(raw_routeability, Mapping):
                raise TypeError("routeability audit must return a mapping")
            routeability_report = dict(raw_routeability)
        except (OSError, RuntimeError, TimeoutError, TypeError, ValueError, ET.ParseError) as exc:
            routeability_report = {"status": "blocked", "error": f"{type(exc).__name__}: {exc}"}
        routeability_gate = validate_routeability_evidence(
            routeability_report,
            candidate_net_file=candidate_net_file,
        )

        if topology_audit_func is None:
            from .topology_audit import audit_topology_fragmentation

            topology_audit_func = audit_topology_fragmentation
        try:
            raw_topology = topology_audit_func(
                net_file=candidate_net_file,
                output_dir=output_dir / f"topology_{candidate_token}",
                prefix=f"{prefix}_topology",
                cluster_radius_m=30.0,
                min_cluster_nodes=3,
                osm_file=osm_file,
            )
            if not isinstance(raw_topology, Mapping):
                raise TypeError("topology audit must return a mapping")
            topology_report = dict(raw_topology)
        except (OSError, RuntimeError, TimeoutError, TypeError, ValueError, ET.ParseError) as exc:
            topology_report = {"status": "blocked", "error": f"{type(exc).__name__}: {exc}"}
        topology_gate = validate_topology_evidence(
            topology_report,
            candidate_net_file=candidate_net_file,
        )

    gates = {
        **preflight_gates,
        "sumo_load": sumo_load_gate,
        "routeability": routeability_gate,
        "topology": topology_gate,
    }
    status = "pass" if all(gate.get("status") == "pass" for gate in gates.values()) else "blocked"
    report = {
        "schema": "torii.corridor_candidate_gates.v2",
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "blocked",
        "source_net_file": str(source_net_file.resolve()),
        "candidate_net_file": str(candidate_net_file.resolve()),
        "source_sha256": artifact_identity.get("source_sha256", ""),
        "candidate_sha256": artifact_identity.get("candidate_sha256", ""),
        "review_decision_status": review_evidence.get("decision_status", "not_supplied"),
        "map_review_status": review_evidence.get("map_review", {}).get("status", "pass"),
        "map_review_evidence_file": map_review_evidence_file_value,
        "map_review_required_location_ids": list(
            (map_review_evidence or {}).get("required_location_ids", [])
        ),
        "applied_semantic_allowances": dict(semantic_allowances),
        "applied_tls_logic_allowances": dict(tls_logic_allowances),
        "gates": gates,
        "routeability_report": routeability_report,
        "topology_report": topology_report,
        "warnings": [] if status == "pass" else ["candidate remains review-only until every required gate passes"],
    }
    return _write_candidate_gate_outcome(
        report=report,
        report_file=report_file,
        manifest_file=manifest_file,
        source_net_file=source_net_file,
        candidate_net_file=candidate_net_file,
        materialization_report=materialization_report,
        review_decision=review_decision,
    )


def normalize_edit_operation(operation: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(operation)
    operation_type = str(raw.get("operation", raw.get("type", ""))).strip().lower().replace("-", "_")
    operation_type = _OPERATION_ALIASES.get(operation_type, operation_type)
    operation_id = str(raw.get("id", raw.get("operation_id", ""))).strip()
    if not operation_id:
        canonical = json.dumps(
            raw,
            sort_keys=True,
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        operation_id = f"operation-{hashlib.sha256(canonical).hexdigest()[:16]}"
    params = dict(raw.get("params") or {})
    promoted_keys = (
        "from",
        "to",
        "type",
        "lanes",
        "speed",
        "allow",
        "disallow",
        "width",
        "name",
        "ref",
        "node_id",
        "edge_ids",
        "crossing_edges",
        "x",
        "y",
    )
    for key in promoted_keys:
        if key in raw and key not in params:
            params[key] = raw[key]
    target_ids = raw.get("target_ids", raw.get("edge_ids", raw.get("targets", [])))
    if isinstance(target_ids, str):
        target_ids = [item for item in target_ids.replace(",", " ").split() if item]
    evidence = raw.get("evidence", [])
    if isinstance(evidence, (str, Mapping)):
        evidence = [evidence]
    rollback = raw.get("rollback")
    constraints = dict(_DEFAULT_CONSTRAINTS)
    constraints.update(dict(raw.get("constraints") or {}))
    raw_review_requirements = raw.get("review_requirements", {})
    review_requirements: object
    if isinstance(raw_review_requirements, Mapping):
        normalized_review_requirements = dict(raw_review_requirements)
        for key in ("map_review_required", "review_question"):
            if key in raw and key not in normalized_review_requirements:
                normalized_review_requirements[key] = raw[key]
        review_requirements = normalized_review_requirements
    else:
        review_requirements = raw_review_requirements
    return {
        "id": operation_id,
        "operation": operation_type,
        "status": str(raw.get("status", "candidate")).strip().lower() or "candidate",
        "target_ids": [str(item) for item in (target_ids or [])],
        "rationale": str(raw.get("rationale", raw.get("reason", ""))).strip(),
        "evidence": list(evidence or []),
        "rollback": rollback,
        "constraints": constraints,
        "review_requirements": review_requirements,
        "location": dict(raw.get("location") or {}),
        "params": params,
    }


def _candidate_semantic_gate(
    source_root: ET.Element,
    candidate_root: ET.Element,
    source_file: Path,
    candidate_file: Path,
    *,
    allowed_deltas: Mapping[str, int] | None = None,
    allowed_tls_logic_changes: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    source_counts = _protected_semantic_counts(source_root)
    candidate_counts = _protected_semantic_counts(candidate_root)
    deltas = {
        key: candidate_counts[key] - source_counts[key]
        for key in source_counts
        if candidate_counts.get(key) != source_counts.get(key)
    }
    normalized_allowances: dict[str, int] = {}
    for key, value in (allowed_deltas or {}).items():
        try:
            normalized_allowances[str(key)] = max(0, int(value))
        except (TypeError, ValueError):
            normalized_allowances[str(key)] = 0
    disallowed_deltas = {
        key: delta
        for key, delta in deltas.items()
        if delta < 0 or delta != normalized_allowances.get(key, 0)
    }
    mismatched_allowances = {
        key: allowance
        for key, allowance in normalized_allowances.items()
        if allowance > 0 and deltas.get(key) != allowance
    }
    connection_audit = {"status": "pass", "controlled_missing_count": 0, "controlled_extra_count": 0}
    try:
        from .corridor_simplification import audit_alias_normalized_connections

        connection_audit = audit_alias_normalized_connections(source_file, candidate_file)
    except (OSError, ET.ParseError, ValueError) as exc:
        connection_audit = {"status": "fail", "error": f"{type(exc).__name__}: {exc}"}
    tls_logic_report = _compare_tls_logic_semantics(
        source_root,
        candidate_root,
        allowed_changes=allowed_tls_logic_changes,
    )
    status = (
        "pass"
        if not disallowed_deltas
        and not mismatched_allowances
        and connection_audit.get("status") == "pass"
        and tls_logic_report.get("status") == "pass"
        else "blocked"
    )
    return {
        "status": status,
        "source_counts": source_counts,
        "candidate_counts": candidate_counts,
        "deltas": deltas,
        "allowed_deltas": normalized_allowances,
        "disallowed_deltas": disallowed_deltas,
        "mismatched_allowances": mismatched_allowances,
        "alias_normalized_connection_audit": connection_audit,
        "tls_logic_signature_status": tls_logic_report.get("status", "blocked"),
        "source_tls_logic_signature": tls_logic_report.get("source_signature", {}),
        "candidate_tls_logic_signature": tls_logic_report.get("candidate_signature", {}),
        "tls_logic_signature_differences": tls_logic_report.get("differences", {}),
        "tls_logic_signature_accepted_changes": tls_logic_report.get("accepted_changes", {}),
        "tls_logic_signature_unexpected_changes": tls_logic_report.get("unexpected_changes", {}),
    }


def _edge_allows_modal_mode(edge: ET.Element, mode: str) -> bool:
    lanes = list(edge.findall("lane"))
    if not lanes:
        return _permission_allows_mode(edge.attrib, {}, mode)
    return any(_permission_allows_mode(edge.attrib, lane.attrib, mode) for lane in lanes)


def _permission_allows_mode(
    edge_attributes: Mapping[str, str],
    lane_attributes: Mapping[str, str],
    mode: str,
) -> bool:
    edge_allow = set(str(edge_attributes.get("allow", "")).split())
    edge_disallow = set(str(edge_attributes.get("disallow", "")).split())
    lane_allow = set(str(lane_attributes.get("allow", "")).split())
    lane_disallow = set(str(lane_attributes.get("disallow", "")).split())
    if mode in lane_disallow or mode in edge_disallow:
        return False
    if lane_allow:
        return mode in lane_allow
    if edge_allow:
        return mode in edge_allow
    return True


def _edge_lane_allows_modal_mode(edge: ET.Element, lane_index: str, mode: str) -> bool:
    lanes = list(edge.findall("lane"))
    if not lanes:
        return _edge_allows_modal_mode(edge, mode)
    lane = next((item for item in lanes if str(item.attrib.get("index", "")) == lane_index), None)
    if lane is None:
        return False
    return _permission_allows_mode(edge.attrib, lane.attrib, mode)


def _connected_components(adjacency: Mapping[str, set[str]]) -> list[set[str]]:
    components: list[set[str]] = []
    unseen = set(adjacency)
    while unseen:
        root_id = unseen.pop()
        component = {root_id}
        stack = [root_id]
        while stack:
            current = stack.pop()
            for neighbor in adjacency.get(current, set()) - component:
                component.add(neighbor)
                unseen.discard(neighbor)
                stack.append(neighbor)
        components.append(component)
    return components


def _modal_graph_stats(root: ET.Element, mode: str) -> dict[str, Any]:
    normal_edges = _normal_edges(root)
    all_edges = {
        str(edge.attrib["id"]): edge
        for edge in root.findall("edge")
        if edge.attrib.get("id")
    }
    modal_edges = {
        edge_id: edge
        for edge_id, edge in all_edges.items()
        if _edge_allows_modal_mode(edge, mode)
    }
    normal_modal_edges: dict[str, ET.Element] = {}
    junction_adjacency: dict[str, set[str]] = defaultdict(set)
    for edge_id, edge in normal_edges.items():
        if edge_id not in modal_edges:
            continue
        from_id = str(edge.attrib.get("from", ""))
        to_id = str(edge.attrib.get("to", ""))
        if not from_id or not to_id:
            continue
        normal_modal_edges[edge_id] = edge
        junction_adjacency[from_id].add(to_id)
        junction_adjacency[to_id].add(from_id)

    connection_adjacency: dict[str, set[str]] = {edge_id: set() for edge_id in modal_edges}
    edge_connection_counts = {edge_id: 0 for edge_id in normal_modal_edges}
    connection_count = 0
    for connection in root.findall("connection"):
        from_edge_id = str(connection.attrib.get("from", ""))
        to_edge_id = str(connection.attrib.get("to", ""))
        from_edge = modal_edges.get(from_edge_id)
        to_edge = modal_edges.get(to_edge_id)
        if from_edge is None or to_edge is None:
            continue
        from_lane = str(connection.attrib.get("fromLane", "0"))
        to_lane = str(connection.attrib.get("toLane", "0"))
        if not _edge_lane_allows_modal_mode(from_edge, from_lane, mode):
            continue
        if not _edge_lane_allows_modal_mode(to_edge, to_lane, mode):
            continue
        connection_count += 1
        connection_adjacency[from_edge_id].add(to_edge_id)
        connection_adjacency[to_edge_id].add(from_edge_id)
        if from_edge_id in edge_connection_counts:
            edge_connection_counts[from_edge_id] += 1
        if to_edge_id in edge_connection_counts:
            edge_connection_counts[to_edge_id] += 1

    junction_components = _connected_components(junction_adjacency)
    all_connection_components = _connected_components(connection_adjacency)
    normal_edge_ids = set(normal_modal_edges)
    connection_components = [
        component & normal_edge_ids
        for component in all_connection_components
        if component & normal_edge_ids
    ]
    connected_edge_count = sum(
        1
        for edge_id in normal_edge_ids
        if connection_adjacency.get(edge_id)
    )
    return {
        "edge_count": len(normal_modal_edges),
        "node_count": len(junction_adjacency),
        "component_count": len(junction_components),
        "largest_component_node_count": max((len(component) for component in junction_components), default=0),
        "connection_count": connection_count,
        "connected_edge_count": connected_edge_count,
        "isolated_edge_count": len(normal_modal_edges) - connected_edge_count,
        "connection_component_count": len(connection_components),
        "largest_connection_component_edge_count": max(
            (len(component) for component in connection_components),
            default=0,
        ),
        "nodes": sorted(junction_adjacency),
        "edge_ids": sorted(normal_modal_edges),
        "edge_connection_counts": dict(sorted(edge_connection_counts.items())),
    }


def _modal_connectivity_gate(source_root: ET.Element, candidate_root: ET.Element) -> dict[str, Any]:
    source = {mode: _modal_graph_stats(source_root, mode) for mode in _MODAL_MODES}
    candidate = {mode: _modal_graph_stats(candidate_root, mode) for mode in _MODAL_MODES}
    decreases: dict[str, dict[str, tuple[int, int]]] = {}
    for mode in _MODAL_MODES:
        mode_decreases: dict[str, tuple[int, int]] = {}
        for key in (
            "edge_count",
            "largest_component_node_count",
            "connection_count",
            "connected_edge_count",
            "largest_connection_component_edge_count",
        ):
            if candidate[mode][key] < source[mode][key]:
                mode_decreases[key] = (source[mode][key], candidate[mode][key])
        if candidate[mode]["isolated_edge_count"] > source[mode]["isolated_edge_count"]:
            mode_decreases["isolated_edge_count_increase"] = (
                source[mode]["isolated_edge_count"],
                candidate[mode]["isolated_edge_count"],
            )
        if mode_decreases:
            decreases[mode] = mode_decreases
    return {
        "status": "pass" if not decreases else "blocked",
        "source": source,
        "candidate": candidate,
        "decreases": decreases,
    }


def _modal_addition_gate(
    source_root: ET.Element,
    candidate_root: ET.Element,
    edge_records: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    source_stats = {mode: _modal_graph_stats(source_root, mode) for mode in _MODAL_MODES}
    candidate_stats = {mode: _modal_graph_stats(candidate_root, mode) for mode in _MODAL_MODES}
    candidate_edges = _normal_edges(candidate_root)
    errors: list[dict[str, Any]] = []
    checked: list[dict[str, Any]] = []
    for record in edge_records:
        edge_id = str(record.get("edge_id", ""))
        operation = str(record.get("operation", ""))
        edge = candidate_edges.get(edge_id)
        if edge is None:
            continue
        allow = set(str(record.get("allow", "")).split())
        modes = [mode for mode in _MODAL_MODES if mode in allow]
        if operation == "add_sidewalk" and "pedestrian" not in modes:
            modes.append("pedestrian")
        if operation == "add_ramp":
            edge_type = str(record.get("type", "")).lower()
            if any(token in edge_type for token in ("foot", "walk", "sidewalk", "pedestrian")) and "pedestrian" not in modes:
                modes.append("pedestrian")
        from_id = str(edge.attrib.get("from", ""))
        to_id = str(edge.attrib.get("to", ""))
        for mode in sorted(set(modes)):
            if not _edge_allows_modal_mode(edge, mode):
                errors.append({"edge_id": edge_id, "mode": mode, "code": "added_edge_mode_not_materialized"})
                continue
            source_nodes = set(source_stats[mode].get("nodes", []))
            if source_nodes and from_id not in source_nodes and to_id not in source_nodes:
                errors.append({"edge_id": edge_id, "mode": mode, "code": "added_edge_isolated_from_existing_modal_graph"})
            modal_connection_count = int(
                candidate_stats[mode].get("edge_connection_counts", {}).get(edge_id, 0)
            )
            if source_stats[mode].get("edge_count", 0) and modal_connection_count == 0:
                errors.append({"edge_id": edge_id, "mode": mode, "code": "added_edge_has_no_modal_connection"})
            checked.append(
                {
                    "edge_id": edge_id,
                    "mode": mode,
                    "from": from_id,
                    "to": to_id,
                    "modal_connection_count": modal_connection_count,
                }
            )
    connectivity = _modal_connectivity_gate(source_root, candidate_root)
    if connectivity.get("status") != "pass":
        errors.append({"code": "modal_graph_decreased", "decreases": connectivity.get("decreases", {})})
    return {
        "status": "pass" if not errors else "blocked",
        "checked_additions": checked,
        "errors": errors,
        "connectivity": connectivity,
    }


def _tls_logic_signature(root: ET.Element) -> dict[str, dict[str, Any]]:
    """Capture the semantic parts of every TLS program, including phase states."""
    signature: dict[str, dict[str, Any]] = {}
    for logic in sorted(root.findall("tlLogic"), key=lambda item: item.attrib.get("id", "")):
        logic_id = str(logic.attrib.get("id", ""))
        if not logic_id:
            continue
        attrs = {
            str(key): str(value)
            for key, value in sorted(logic.attrib.items())
            if key != "id"
        }
        phases = [
            {
                str(key): str(value)
                for key, value in sorted(phase.attrib.items())
            }
            for phase in logic.findall("phase")
        ]
        params = [
            {
                str(key): str(value)
                for key, value in sorted(param.attrib.items())
            }
            for param in logic.findall("param")
        ]
        signature[logic_id] = {"attributes": attrs, "phases": phases, "params": params}
    return signature


def _compare_tls_logic_semantics(
    source_root: ET.Element,
    candidate_root: ET.Element,
    *,
    allowed_changes: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    source_signature = _tls_logic_signature(source_root)
    candidate_signature = _tls_logic_signature(candidate_root)
    differences: dict[str, Any] = {}
    accepted_changes: dict[str, Any] = {}
    unexpected_changes: dict[str, Any] = {}
    for logic_id in sorted(set(source_signature) | set(candidate_signature)):
        source_value = source_signature.get(logic_id)
        candidate_value = candidate_signature.get(logic_id)
        if source_value != candidate_value:
            difference = {
                "source": source_value,
                "candidate": candidate_value,
            }
            differences[logic_id] = difference
            expected = (allowed_changes or {}).get(logic_id)
            if expected is not None and candidate_value == expected:
                accepted_changes[logic_id] = difference
            else:
                unexpected_changes[logic_id] = difference
    return {
        "status": "pass" if not unexpected_changes else "blocked",
        "source_signature": source_signature,
        "candidate_signature": candidate_signature,
        "differences": differences,
        "accepted_changes": accepted_changes,
        "unexpected_changes": unexpected_changes,
    }


def _protected_semantic_counts(root: ET.Element) -> dict[str, int]:
    edges = _normal_edges(root)
    controlled_ids = {connection.attrib.get("tl", "") for connection in root.findall("connection") if connection.attrib.get("tl")}
    logic_ids = {logic.attrib.get("id", "") for logic in root.findall("tlLogic") if logic.attrib.get("id")}
    return {
        "controlled_connection_count": sum(1 for connection in root.findall("connection") if connection.attrib.get("tl")),
        "active_tl_logic_count": len(controlled_ids & logic_ids),
        "rail_edge_count": sum(1 for edge in edges.values() if _is_rail_edge(edge)),
        "bridge_edge_count": sum(1 for edge in edges.values() if edge.attrib.get("bridge") in {"true", "yes"}),
        "tunnel_edge_count": sum(1 for edge in edges.values() if edge.attrib.get("tunnel") in {"true", "yes"}),
        "crossing_edge_count": sum(1 for edge in root.findall("edge") if edge.attrib.get("function") == "crossing"),
        "walkingarea_edge_count": sum(1 for edge in root.findall("edge") if edge.attrib.get("function") == "walkingarea"),
    }


def _not_run_gate(reason: str) -> dict[str, Any]:
    return {"status": "blocked", "execution_status": "not_run", "reason": reason}


def _result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        report = dict(result)
        if "status" not in report and "returncode" in report:
            report["status"] = "pass" if report.get("returncode") == 0 else "fail"
        return report
    return {
        "status": "pass" if getattr(result, "returncode", 1) == 0 else "fail",
        "returncode": getattr(result, "returncode", 1),
        "stdout": getattr(result, "stdout", ""),
        "stderr": getattr(result, "stderr", ""),
    }


def validate_edit_ledger(
    ledger: Mapping[str, Any],
    *,
    root: ET.Element | None = None,
) -> dict[str, Any]:
    operations = list(ledger.get("operations") or [])
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    edges = _normal_edges(root) if root is not None else {}
    for operation in operations:
        item = normalize_edit_operation(operation)
        operation_id = item["id"]
        operation_type = item["operation"]
        if operation_id in seen_ids:
            errors.append({"operation_id": operation_id, "code": "duplicate_operation_id"})
        seen_ids.add(operation_id)
        if operation_type not in EDIT_OPERATION_TYPES:
            errors.append({"operation_id": operation_id, "code": "unsupported_operation", "value": operation_type})
            continue
        if operation_type in _MUTATING_OPERATIONS:
            if not item["rationale"]:
                errors.append({"operation_id": operation_id, "code": "missing_rationale"})
            if not item["evidence"]:
                errors.append({"operation_id": operation_id, "code": "missing_evidence"})
            if not item["rollback"]:
                errors.append({"operation_id": operation_id, "code": "missing_rollback"})
        _validate_operation_shape(item, errors)
        if root is not None:
            _validate_against_network(item, edges, root, errors, warnings)
    return {
        "status": "pass" if not errors else "fail",
        "operation_count": len(operations),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def _validate_operation_shape(item: Mapping[str, Any], errors: list[dict[str, Any]]) -> None:
    operation_id = str(item["id"])
    operation_type = str(item["operation"])
    target_ids = list(item.get("target_ids") or [])
    params = dict(item.get("params") or {})
    review_requirements = item.get("review_requirements", {})
    if not isinstance(review_requirements, Mapping):
        errors.append({"operation_id": operation_id, "code": "review_requirements_must_be_object"})
    elif "map_review_required" in review_requirements and type(
        review_requirements.get("map_review_required")
    ) is not bool:
        errors.append({"operation_id": operation_id, "code": "map_review_required_must_be_boolean"})
    if operation_type in {"delete_edge", "merge_edges"} and not target_ids:
        errors.append({"operation_id": operation_id, "code": "missing_target_ids"})
    if operation_type in {"add_edge", "add_sidewalk", "add_ramp"}:
        for field in ("from", "to", "type"):
            if not str(params.get(field, "")).strip():
                errors.append({"operation_id": operation_id, "code": f"missing_params_{field}"})
        if operation_type == "add_ramp" and not _looks_like_ramp(str(params.get("type", ""))):
            errors.append({"operation_id": operation_id, "code": "ramp_type_not_link"})
    if operation_type == "add_crossing":
        if not str(params.get("node_id", "")).strip():
            errors.append({"operation_id": operation_id, "code": "missing_params_node_id"})
        crossing_edges = params.get("crossing_edges", target_ids)
        if isinstance(crossing_edges, str):
            crossing_edges = crossing_edges.split()
        if not crossing_edges:
            errors.append({"operation_id": operation_id, "code": "missing_crossing_edges"})


def _validate_against_network(
    item: Mapping[str, Any],
    edges: Mapping[str, ET.Element],
    root: ET.Element,
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    operation_id = str(item["id"])
    operation_type = str(item["operation"])
    target_ids = [str(value) for value in item.get("target_ids", [])]
    if operation_type in {"delete_edge", "merge_edges"}:
        missing = sorted(set(target_ids) - set(edges))
        if missing:
            errors.append({"operation_id": operation_id, "code": "unknown_target_edges", "edge_ids": missing})
            return
        protected = [edge_id for edge_id in target_ids if _is_structurally_protected_edge(edges[edge_id])]
        if protected:
            errors.append(
                {
                    "operation_id": operation_id,
                    "code": "protected_target_requires_explicit_review",
                    "edge_ids": sorted(protected),
                }
            )
        controlled = _controlled_edges(root)
        tls_targets = sorted(set(target_ids) & controlled) if operation_type == "delete_edge" else sorted(
            _local_controlled_target_edges(root, set(target_ids))
        )
        if tls_targets:
            errors.append({"operation_id": operation_id, "code": "tls_controlled_target", "edge_ids": tls_targets})
        rail_targets = sorted(edge_id for edge_id in target_ids if _is_rail_edge(edges[edge_id]))
        if rail_targets:
            errors.append({"operation_id": operation_id, "code": "rail_target", "edge_ids": rail_targets})
        if operation_type == "merge_edges":
            edge_types = {edges[edge_id].attrib.get("type", "") for edge_id in target_ids}
            if len(edge_types) != 1:
                errors.append({"operation_id": operation_id, "code": "merge_type_mismatch"})
            if len({_edge_lane_signature(edges[edge_id]) for edge_id in target_ids}) != 1:
                errors.append({"operation_id": operation_id, "code": "merge_lane_semantics_mismatch"})
    if operation_type == "add_crossing":
        node_id = str(item.get("params", {}).get("node_id", ""))
        junction_ids = {junction.attrib.get("id", "") for junction in root.findall("junction")}
        if node_id and node_id not in junction_ids:
            errors.append({"operation_id": operation_id, "code": "unknown_crossing_node", "node_id": node_id})
        crossing_edges = item.get("params", {}).get("crossing_edges", target_ids)
        if isinstance(crossing_edges, str):
            crossing_edges = crossing_edges.split()
        missing = sorted(set(str(value) for value in (crossing_edges or [])) - set(edges))
        if missing:
            errors.append({"operation_id": operation_id, "code": "unknown_crossing_edges", "edge_ids": missing})


def _proposal(
    *,
    operation_id: str,
    operation: str,
    target_ids: list[str],
    rationale: str,
    evidence: list[Mapping[str, Any]],
    rollback: Mapping[str, Any],
    location: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return normalize_edit_operation(
        {
            "id": operation_id,
            "operation": operation,
            "status": "candidate",
            "target_ids": target_ids,
            "rationale": rationale,
            "evidence": list(evidence),
            "rollback": dict(rollback),
            "location": dict(location or {}),
            "params": dict(params or {}),
        }
    )


def _network_inventory(root: ET.Element) -> dict[str, Any]:
    edges = _normal_edges(root)
    return {
        "normal_edge_count": len(edges),
        "junction_count": sum(1 for junction in root.findall("junction") if not junction.attrib.get("id", "").startswith(":")),
        "connection_count": len(root.findall("connection")),
        "controlled_connection_count": sum(1 for connection in root.findall("connection") if connection.attrib.get("tl")),
        "tls_logic_count": len(root.findall("tlLogic")),
        "crossing_edge_count": sum(1 for edge in root.findall("edge") if edge.attrib.get("function") == "crossing"),
        "walkingarea_edge_count": sum(1 for edge in root.findall("edge") if edge.attrib.get("function") == "walkingarea"),
        "rail_edge_count": sum(1 for edge in edges.values() if _is_rail_edge(edge)),
        "bridge_edge_count": sum(1 for edge in edges.values() if edge.attrib.get("bridge") == "true" or edge.attrib.get("bridge") == "yes"),
        "tunnel_edge_count": sum(1 for edge in edges.values() if edge.attrib.get("tunnel") == "true" or edge.attrib.get("tunnel") == "yes"),
    }


def _build_rollback(root: ET.Element, ledger: Mapping[str, Any]) -> dict[str, Any]:
    edges = _normal_edges(root)
    inverse: list[dict[str, Any]] = []
    for operation in reversed(list(ledger.get("operations") or [])):
        item = normalize_edit_operation(operation)
        operation_type = item["operation"]
        if operation_type in {"add_edge", "add_sidewalk", "add_ramp", "add_crossing"}:
            inverse.append(
                {
                    "operation_id": item["id"],
                    "action": "remove_candidate_addition",
                    "target_ids": item["target_ids"] or [str(item.get("params", {}).get("id", item["id"]))],
                }
            )
        elif operation_type == "delete_edge":
            inverse.append(
                {
                    "operation_id": item["id"],
                    "action": "restore_source_edges",
                    "edges": [_xml_text(edges[edge_id]) for edge_id in item["target_ids"] if edge_id in edges],
                }
            )
        elif operation_type == "merge_edges":
            inverse.append(
                {
                    "operation_id": item["id"],
                    "action": "restore_source_edges_and_junction",
                    "edges": [_xml_text(edges[edge_id]) for edge_id in item["target_ids"] if edge_id in edges],
                    "junction_id": item.get("params", {}).get("node_id", ""),
                }
            )
    return {
        "schema": "torii.corridor_edit_rollback.v1",
        "source_net_file": ledger.get("source_net_file", ""),
        "inverse_operations": inverse,
        "safe_to_apply": False,
        "reason": "rollback is a review artifact until the candidate variant passes all materialization gates",
    }


def _write_review_overlay(
    path: Path,
    operations: list[dict[str, Any]],
    root: ET.Element,
    *,
    candidate_root: ET.Element | None = None,
    map_review_evidence: Mapping[str, Any] | None = None,
    candidate_sha256: str = "",
    map_review_evidence_file: Path | None = None,
    map_review_evidence_sha256: str = "",
) -> dict[str, Any]:
    """Write a display-only SUMO review layer.

    The overlay deliberately contains only ``poi``, ``poly``, and ``param``
    elements. Decisions remain in the hash-bound JSON review contract.
    """
    additional = ET.Element("additional")
    evidence_by_proposal = {
        str(item.get("proposal_id", "")): item
        for item in (map_review_evidence or {}).get("locations", []) or []
        if isinstance(item, Mapping) and str(item.get("proposal_id", "")).strip()
    }
    expected_operation_ids = {str(operation["id"]) for operation in operations}
    marked_operation_ids: set[str] = set()
    poly_count = 0
    for operation in operations:
        operation_id = str(operation["id"])
        evidence = evidence_by_proposal.get(operation_id, {})
        coordinate = evidence.get("coordinate") if isinstance(evidence, Mapping) else {}
        coordinate = coordinate if isinstance(coordinate, Mapping) else {}
        location = _resolved_operation_location(operation, root)
        requirements = _review_requirements(operation)
        color = _review_overlay_color(operation, requirements)
        if "x" in location and "y" in location:
            poi = ET.SubElement(
                additional,
                "poi",
                {
                    "id": f"torii-{operation_id}",
                    "type": f"torii.edit.{operation['operation']}",
                    "x": _format_number(location["x"]),
                    "y": _format_number(location["y"]),
                    "color": color,
                    "layer": "1000",
                },
            )
            params = {
                "proposal_id": operation_id,
                "operation": operation["operation"],
                "rationale": operation.get("rationale", ""),
                "status": operation.get("status", "candidate"),
                "candidate_sha256": candidate_sha256,
                "map_review_required": str(
                    requirements.get("map_review_required") is True
                ).lower(),
                "review_question": evidence.get(
                    "review_question",
                    requirements.get("review_question", ""),
                ),
                "location_id": evidence.get("location_id", ""),
                "geometry_source": evidence.get("geometry_source", "source_net"),
                "coordinate_status": coordinate.get("coordinate_status", ""),
                "google_maps_url": evidence.get("google_maps_url", ""),
                "google_maps_satellite_url": evidence.get("google_maps_satellite_url", ""),
                "regional_map_url": evidence.get("regional_map_url", ""),
                "mapillary_url": evidence.get("mapillary_url", ""),
                "kartaview_url": evidence.get("kartaview_url", ""),
                "map_review_evidence_file": (
                    str(map_review_evidence_file.resolve())
                    if map_review_evidence_file is not None
                    else ""
                ),
                "map_review_evidence_sha256": map_review_evidence_sha256,
            }
            for key, value in params.items():
                ET.SubElement(poi, "param", {"key": str(key), "value": str(value)})
            marked_operation_ids.add(operation_id)

        for index, geometry in enumerate(
            _operation_review_shapes(operation, root, candidate_root=candidate_root)
        ):
            poly = ET.SubElement(
                additional,
                "poly",
                {
                    "id": f"torii-{operation_id}-geometry-{index}",
                    "type": f"torii.edit.geometry.{operation['operation']}",
                    "color": color,
                    "fill": "false",
                    "layer": "999",
                    "lineWidth": "3",
                    "shape": str(geometry["shape"]),
                },
            )
            ET.SubElement(poly, "param", {"key": "proposal_id", "value": operation_id})
            ET.SubElement(poly, "param", {"key": "edge_id", "value": str(geometry["edge_id"])})
            ET.SubElement(
                poly,
                "param",
                {"key": "geometry_source", "value": str(geometry["geometry_source"])},
            )
            poly_count += 1
    ET.indent(additional, space="  ")
    write_text_atomic(
        path,
        '<?xml version="1.0" encoding="utf-8"?>\n' + _xml_text(additional) + "\n",
    )
    missing_operation_ids = sorted(expected_operation_ids - marked_operation_ids)
    return {
        "status": "pass" if not missing_operation_ids else "blocked",
        "poi_count": len(marked_operation_ids),
        "poly_count": poly_count,
        "missing_operation_ids": missing_operation_ids,
    }


def _write_candidate_review_html(
    path: Path,
    *,
    operations: Iterable[Mapping[str, Any]],
    map_review_evidence: Mapping[str, Any],
    artifact_identity: Mapping[str, Any],
    map_review_evidence_file: Path,
    map_review_evidence_sha256: str,
    review_overlay_file: Path,
    review_decision_file: Path,
    review_required: bool,
) -> None:
    evidence_by_proposal = {
        str(item.get("proposal_id", "")): item
        for item in map_review_evidence.get("locations", []) or []
        if isinstance(item, Mapping) and str(item.get("proposal_id", "")).strip()
    }
    rows: list[str] = []
    for operation in operations:
        operation_id = str(operation.get("id", ""))
        evidence = evidence_by_proposal.get(operation_id, {})
        coordinate_value = evidence.get("coordinate")
        coordinate = coordinate_value if isinstance(coordinate_value, Mapping) else {}
        if coordinate.get("status") == "available":
            coordinate_text = f"{coordinate.get('lat')}, {coordinate.get('lon')}"
        else:
            coordinate_text = str(coordinate.get("coordinate_status", "unavailable"))
        links = " · ".join(
            _review_html_link(label, str(evidence.get(field, "")))
            for label, field in (
                ("regional map", "regional_map_url"),
                ("satellite", "google_maps_satellite_url"),
                ("Mapillary", "mapillary_url"),
                ("KartaView", "kartaview_url"),
            )
            if str(evidence.get(field, "")).strip()
        ) or "No geographic map link available"
        required = evidence.get("map_review_required") is True
        rows.append(
            "<tr>"
            f"<td><code>{escape(operation_id)}</code></td>"
            f"<td>{escape(str(operation.get('operation', '')))}</td>"
            f"<td>{escape(str(operation.get('rationale', '')))}</td>"
            f"<td><span class=\"badge {'required' if required else 'optional'}\">"
            f"{'required' if required else 'optional'}</span></td>"
            f"<td>{escape(str(evidence.get('review_question', '')))}</td>"
            f"<td>{escape(coordinate_text)}</td>"
            f"<td>{links}</td>"
            "</tr>"
        )
    warning_items = "".join(
        f"<li>{escape(str(warning))}</li>"
        for warning in map_review_evidence.get("warnings", []) or []
    )
    status_text = "Human approval required" if review_required else "No hard human gate declared"
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Torii corridor candidate review</title>
  <style>
    :root {{ color-scheme: light dark; font-family: Inter, system-ui, sans-serif; }}
    body {{ margin: 0 auto; max-width: 1500px; padding: 2rem; line-height: 1.45; }}
    h1 {{ margin-bottom: .25rem; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: .75rem; }}
    .card {{ border: 1px solid #7f8c8d; border-radius: .6rem; padding: .9rem; overflow-wrap: anywhere; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
    th, td {{ border: 1px solid #7f8c8d; padding: .55rem; text-align: left; vertical-align: top; }}
    th {{ position: sticky; top: 0; background: Canvas; }}
    .badge {{ border-radius: 999px; padding: .15rem .5rem; font-weight: 650; }}
    .required {{ background: #ff9800; color: #111; }}
    .optional {{ background: #2e7d32; color: #fff; }}
    code {{ overflow-wrap: anywhere; }}
  </style>
</head>
<body>
  <h1>Torii corridor candidate review</h1>
  <p>{escape(status_text)}. This page is a rendering aid; approval belongs only in the bound JSON decision.</p>
  <div class="summary">
    <div class="card"><strong>Candidate SHA-256</strong><br><code data-role="candidate-sha256">{escape(str(artifact_identity.get('candidate_sha256', '')))}</code></div>
    <div class="card"><strong>Map evidence SHA-256</strong><br><code data-role="map-evidence-sha256">{escape(map_review_evidence_sha256)}</code></div>
    <div class="card"><strong>Map readiness</strong><br>{escape(str(map_review_evidence.get('review_readiness_status', 'blocked')))}</div>
    <div class="card"><strong>Time scope</strong><br>{escape(str(map_review_evidence.get('google_maps_temporal_scope', 'unspecified')))} {escape(str(map_review_evidence.get('google_maps_target_date', '')))}</div>
  </div>
  <h2>Candidate-bound files</h2>
  <ul>
    <li>Map evidence: <code>{escape(str(map_review_evidence_file.resolve()))}</code></li>
    <li>SUMO review overlay: <code>{escape(str(review_overlay_file.resolve()))}</code></li>
    <li>Review decision template: <code>{escape(str(review_decision_file.resolve()))}</code></li>
  </ul>
  <h2>Review warnings</h2>
  <ul>{warning_items}</ul>
  <h2>Edit locations</h2>
  <table>
    <thead><tr><th>Proposal</th><th>Operation</th><th>Rationale</th><th>Gate</th><th>Question</th><th>Coordinate</th><th>Map evidence</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>
"""
    write_text_atomic(path, document)


def _review_html_link(label: str, url: str) -> str:
    if not url.startswith(("https://", "http://")):
        return ""
    return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(label)}</a>'


def _map_review_locations(
    operations: Iterable[Mapping[str, Any]],
    source_root: ET.Element,
) -> list[dict[str, Any]]:
    locations: list[dict[str, Any]] = []
    for operation in operations:
        requirements = _review_requirements(operation)
        operation_type = str(operation.get("operation", "review_marker"))
        locations.append(
            {
                "location_id": f"corridor_edit:{operation.get('id', '')}",
                "proposal_id": str(operation.get("id", "")),
                "operation": operation_type,
                "location": _resolved_operation_location(operation, source_root),
                "map_review_required": requirements.get("map_review_required") is True,
                "review_question": str(requirements.get("review_question", "")),
                "geometry_source": (
                    "candidate_net"
                    if operation_type in {"add_edge", "add_sidewalk", "add_ramp"}
                    else "source_net"
                ),
            }
        )
    return locations


def _review_requirements(operation: Mapping[str, Any]) -> dict[str, Any]:
    value = operation.get("review_requirements")
    return dict(value) if isinstance(value, Mapping) else {}


def _resolved_operation_location(
    operation: Mapping[str, Any],
    root: ET.Element,
) -> dict[str, Any]:
    raw_location = operation.get("location")
    location = dict(raw_location) if isinstance(raw_location, Mapping) else {}
    inferred = _operation_location(operation, root)
    for key in ("x", "y"):
        if key not in location and key in inferred:
            location[key] = inferred[key]
    return location


def _review_overlay_color(
    operation: Mapping[str, Any],
    requirements: Mapping[str, Any],
) -> str:
    if requirements.get("map_review_required") is True:
        return "255,140,0,255"
    if operation.get("operation") in {"delete_edge", "merge_edges"}:
        return "220,20,60,255"
    if operation.get("operation") in _ADDITIVE_OPERATIONS:
        return "34,139,34,255"
    return "30,144,255,255"


def _operation_review_shapes(
    operation: Mapping[str, Any],
    source_root: ET.Element,
    *,
    candidate_root: ET.Element | None,
) -> list[dict[str, str]]:
    operation_type = str(operation.get("operation", ""))
    params = dict(operation.get("params") or {})
    if operation_type in {"add_edge", "add_sidewalk", "add_ramp"}:
        edge_ids = [str(params.get("id", f"torii-add-{operation.get('id', '')}"))]
        geometry_root = candidate_root
        geometry_source = "candidate_net"
    elif operation_type == "add_crossing":
        crossing_edges = params.get("crossing_edges", operation.get("target_ids", []))
        if isinstance(crossing_edges, str):
            crossing_edges = crossing_edges.replace(",", " ").split()
        edge_ids = [str(value) for value in (crossing_edges or [])]
        geometry_root = source_root
        geometry_source = "source_net"
    else:
        edge_ids = [str(value) for value in operation.get("target_ids", []) or []]
        geometry_root = source_root
        geometry_source = "source_net"
    edges = _normal_edges(geometry_root)
    shapes: list[dict[str, str]] = []
    for edge_id in edge_ids:
        edge = edges.get(edge_id)
        shape = _edge_shape(edge) if edge is not None else ""
        if shape:
            shapes.append(
                {
                    "edge_id": edge_id,
                    "shape": shape,
                    "geometry_source": geometry_source,
                }
            )
    return shapes


def _edge_shape(edge: ET.Element) -> str:
    for lane in edge.findall("lane"):
        shape = str(lane.attrib.get("shape", "")).strip()
        if shape:
            return shape
    return str(edge.attrib.get("shape", "")).strip()


def _operation_location(operation: Mapping[str, Any], root: ET.Element) -> dict[str, float]:
    params = dict(operation.get("params") or {})
    if "x" in params and "y" in params:
        return _numeric_location(params)
    junction_id = str(params.get("node_id", ""))
    junctions = {junction.attrib.get("id", ""): junction for junction in root.findall("junction")}
    if junction_id in junctions:
        return _junction_location(junctions[junction_id])
    from_id = str(params.get("from", "")).strip()
    to_id = str(params.get("to", "")).strip()
    from_location = _junction_location(junctions.get(from_id))
    to_location = _junction_location(junctions.get(to_id))
    if from_location and to_location:
        return {
            "x": (from_location["x"] + to_location["x"]) / 2.0,
            "y": (from_location["y"] + to_location["y"]) / 2.0,
        }
    edges = _normal_edges(root)
    locations = [_edge_location(edges[edge_id]) for edge_id in operation.get("target_ids", []) if edge_id in edges]
    locations = [location for location in locations if location]
    if not locations:
        return {}
    return {
        "x": sum(float(location["x"]) for location in locations) / len(locations),
        "y": sum(float(location["y"]) for location in locations) / len(locations),
    }


def _edge_location(edge: ET.Element) -> dict[str, float]:
    points: list[tuple[float, float]] = []
    for lane in edge.findall("lane"):
        shape = lane.attrib.get("shape", "")
        for token in shape.split():
            try:
                x_text, y_text = token.split(",", 1)[:2]
                points.append((float(x_text), float(y_text)))
            except (ValueError, IndexError):
                continue
        if points:
            break
    if not points:
        return {}
    x = sum(point[0] for point in points) / len(points)
    y = sum(point[1] for point in points) / len(points)
    return {"x": x, "y": y}


def _junction_location(junction: ET.Element | None) -> dict[str, float]:
    if junction is None:
        return {}
    return _numeric_location(junction.attrib)


def _numeric_location(values: Mapping[str, Any]) -> dict[str, float]:
    try:
        return {"x": float(values["x"]), "y": float(values["y"])}
    except (KeyError, TypeError, ValueError):
        return {}


def _normal_edges(root: ET.Element | None) -> dict[str, ET.Element]:
    if root is None:
        return {}
    return {
        edge.attrib["id"]: edge
        for edge in root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib["id"].startswith(":")
    }


def _edge_duplicate_signature(edge: ET.Element) -> tuple[Any, ...]:
    params = tuple(sorted((item.attrib.get("key", ""), item.attrib.get("value", "")) for item in edge.findall("param")))
    return (
        edge.attrib.get("from", ""),
        edge.attrib.get("to", ""),
        edge.attrib.get("type", ""),
        edge.attrib.get("name", ""),
        params,
        _edge_lane_signature(edge),
    )


def _edge_lane_signature(edge: ET.Element) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            lane.attrib.get("index", ""),
            lane.attrib.get("speed", ""),
            lane.attrib.get("allow", ""),
            lane.attrib.get("disallow", ""),
            lane.attrib.get("width", ""),
        )
        for lane in edge.findall("lane")
    )


def _controlled_edges(root: ET.Element) -> set[str]:
    return {
        edge_id
        for connection in root.findall("connection")
        if connection.attrib.get("tl")
        for edge_id in (connection.attrib.get("from", ""), connection.attrib.get("to", ""))
        if edge_id
    }


def _is_protected_edge(edge: ET.Element, root: ET.Element) -> bool:
    return _is_structurally_protected_edge(edge) or edge.attrib.get("id", "") in _controlled_edges(root)


def _is_structurally_protected_edge(edge: ET.Element) -> bool:
    return (
        edge.attrib.get("function", "") in _PROTECTED_FUNCTIONS
        or _is_rail_edge(edge)
        or any(edge.attrib.get(field) in {"true", "yes"} for field in _PROTECTED_EDGE_ATTRS)
    )


def _local_controlled_target_edges(root: ET.Element, target_ids: set[str]) -> set[str]:
    """Return target edges in a TLS connection whose both sides are merged."""
    return {
        edge_id
        for connection in root.findall("connection")
        if connection.attrib.get("tl")
        and connection.attrib.get("from", "") in target_ids
        and connection.attrib.get("to", "") in target_ids
        for edge_id in (connection.attrib.get("from", ""), connection.attrib.get("to", ""))
    }


def _is_rail_edge(edge: ET.Element) -> bool:
    values = " ".join(
        [edge.attrib.get("type", ""), edge.attrib.get("allow", ""), edge.attrib.get("disallow", "")]
        + [param.attrib.get("value", "") for param in edge.findall("param")]
    ).lower()
    return any(token in values for token in _RAIL_TOKENS)


def _deduplicate_operations(operations: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for operation in operations:
        normalized = normalize_edit_operation(operation)
        unique.setdefault(normalized["id"], normalized)
    return [unique[key] for key in sorted(unique)]


def _decision_summary(operations: list[dict[str, Any]], validation: Mapping[str, Any]) -> dict[str, Any]:
    by_operation = Counter(str(item.get("operation", "")) for item in operations)
    by_status = Counter(str(item.get("status", "candidate")) for item in operations)
    return {
        "by_operation": dict(sorted(by_operation.items())),
        "by_status": dict(sorted(by_status.items())),
        "validation_status": validation.get("status", "fail"),
        "human_review_required": bool(validation.get("errors") or by_status.get("candidate") or by_status.get("review")),
    }


def _write_materialization_outcome(
    *,
    output_dir: Path,
    report_file: Path,
    manifest_file: Path,
    command_file: Path,
    report: Mapping[str, Any],
    source_net_file: Path | None,
    candidate_net_file: Path | None = None,
    ledger_file: Path | None = None,
    plan_file: Path | None = None,
    additional_plan_files: Iterable[tuple[Path, str]] | None = None,
    extra_artifacts: Iterable[tuple[Path, str]] | None = None,
) -> dict[str, Any]:
    """Persist both successful and blocked materialization decisions."""
    persisted = dict(report)
    persisted["report_file"] = str(report_file)
    persisted["manifest_file"] = str(manifest_file)
    write_json_atomic(report_file, persisted, sort_keys=True)
    artifact_candidates = [
        (source_net_file, "source_net"),
        (ledger_file, "edit_ledger"),
        (plan_file, "materialization_plan"),
        (command_file, "netconvert_command"),
        (candidate_net_file, "candidate_net"),
        (report_file, "materialization_report"),
    ]
    artifact_candidates.extend((path, kind) for path, kind in (additional_plan_files or []))
    artifact_candidates.extend((path, kind) for path, kind in (extra_artifacts or []))
    artifacts = [
        _artifact_record(path, kind)
        for path, kind in artifact_candidates
        if path is not None and path.exists()
    ]
    manifest = {
        "schema": "torii.corridor_materialization_manifest.v1",
        "status": persisted.get("status", "blocked"),
        "claim_status": persisted.get("claim_status", "blocked"),
        "materialization_status": persisted.get("materialization_status", "blocked"),
        "source_net_file": str(source_net_file.resolve()) if source_net_file is not None else "",
        "candidate_net_file": (
            str(candidate_net_file.resolve())
            if candidate_net_file is not None and candidate_net_file.exists()
            else ""
        ),
        "artifacts": artifacts,
        "source_overwrite_forbidden": True,
        "warnings": list(persisted.get("warnings") or []),
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return persisted


def _write_candidate_gate_outcome(
    *,
    report: Mapping[str, Any],
    report_file: Path,
    manifest_file: Path,
    source_net_file: Path,
    candidate_net_file: Path,
    materialization_report: Mapping[str, Any] | None,
    review_decision: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Persist a candidate-gate decision, including all blocked preflight cases."""
    persisted = dict(report)
    persisted["report_file"] = str(report_file)
    persisted["manifest_file"] = str(manifest_file)
    write_json_atomic(report_file, persisted, sort_keys=True)

    artifact_candidates: list[tuple[Path | None, str]] = [
        (source_net_file, "source_net"),
        (candidate_net_file, "candidate_net"),
    ]
    if isinstance(materialization_report, Mapping):
        materialization_path = materialization_report.get(
            "_materialization_report_file",
            materialization_report.get("report_file"),
        )
        if materialization_path:
            artifact_candidates.append((Path(str(materialization_path)), "materialization_report"))
        for key, kind in (
            ("manifest_file", "materialization_manifest"),
            ("map_review_evidence_file", "map_review_evidence"),
            ("accepted_review_additional_xml", "accepted_review_additional_xml"),
            ("candidate_review_html_file", "candidate_review_html"),
            ("review_decision_template_file", "candidate_review_decision_template"),
        ):
            value = materialization_report.get(key)
            if value:
                artifact_candidates.append((Path(str(value)), kind))
    if isinstance(review_decision, Mapping):
        review_path = review_decision.get("_review_decision_file", review_decision.get("review_file"))
        if review_path:
            artifact_candidates.append((Path(str(review_path)), "candidate_review_decision"))
    for key, kind in (
        ("report_file", "routeability_report"),
        ("manifest_file", "routeability_manifest"),
    ):
        value = report.get("routeability_report", {}).get(key) if isinstance(report.get("routeability_report"), Mapping) else None
        if value:
            artifact_candidates.append((Path(str(value)), kind))
    for key, kind in (
        ("report_file", "topology_report"),
        ("manifest_file", "topology_manifest"),
    ):
        value = report.get("topology_report", {}).get(key) if isinstance(report.get("topology_report"), Mapping) else None
        if value:
            artifact_candidates.append((Path(str(value)), kind))
    artifact_candidates.append((report_file, "candidate_gate_report"))

    artifacts: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for path, kind in artifact_candidates:
        if path is None or not path.is_file():
            continue
        resolved = str(path.resolve())
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        artifacts.append(_artifact_record(path.resolve(), kind))

    gates = persisted.get("gates") if isinstance(persisted.get("gates"), Mapping) else {}
    manifest = {
        "schema": "torii.corridor_candidate_manifest.v2",
        "status": persisted.get("status", "blocked"),
        "claim_status": persisted.get("claim_status", "blocked"),
        "source_net_file": str(source_net_file.resolve()),
        "candidate_net_file": str(candidate_net_file.resolve()),
        "source_sha256": persisted.get("source_sha256", ""),
        "candidate_sha256": persisted.get("candidate_sha256", ""),
        "gates": {
            str(name): gate.get("status", "blocked") if isinstance(gate, Mapping) else "blocked"
            for name, gate in gates.items()
        },
        "artifacts": artifacts,
        "source_overwrite_forbidden": True,
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return persisted


def _quote_command_token(value: Any) -> str:
    token = str(value)
    if not token or any(character.isspace() for character in token):
        return '"' + token.replace('"', '\\"') + '"'
    return token


def _artifact_record(path: Path, kind: str) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "kind": kind,
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": file_sha256(resolved),
    }


def _xml_text(element: ET.Element) -> str:
    return ET.tostring(element, encoding="unicode")


def _looks_like_ramp(edge_type: str) -> bool:
    normalized = edge_type.lower()
    return normalized.endswith("_link") or "ramp" in normalized


def _format_number(value: Any) -> str:
    number = float(value)
    if math.isfinite(number) and number.is_integer():
        return str(int(number))
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _failure(error: str) -> dict[str, Any]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "corridor_edit_ledger_status": "failed",
        "error": error,
        "warnings": [error],
    }
