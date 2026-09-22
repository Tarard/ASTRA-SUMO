from __future__ import annotations

import json
import hashlib
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from .command_runner import run_command


def build_tls_gap_destination_mapping(
    *,
    reference_net_file: Path,
    candidate_net_file: Path,
    alignment_report: dict[str, Any],
    output_dir: Path,
    prefix: str = "tls_gap_destination_mapping",
) -> dict[str, Any]:
    """Map high-confidence TLS gaps to candidate destination edges.

    The result is a repair plan only.  SUMO internal ``via`` edges and signal
    phase semantics must still be rebuilt and gated before any network variant
    can be promoted.
    """

    try:
        reference_root = ET.parse(reference_net_file).getroot()
        candidate_root = ET.parse(candidate_net_file).getroot()
    except (OSError, ET.ParseError) as exc:
        return {"status": "fail", "claim_status": "blocked", "error": f"{type(exc).__name__}: {exc}"}

    reference_edges = _edge_catalog(reference_root)
    candidate_edges = _edge_catalog(candidate_root)
    reference_connections = _connection_catalog(reference_root, reference_edges)
    candidate_connections = _connection_catalog(candidate_root, candidate_edges)
    alignment = alignment_report.get("tls_controller_alignment", alignment_report)
    queue = alignment.get("high_confidence_movement_gap_queue", []) if isinstance(alignment, dict) else []
    alignment_pairs_by_candidate_tl = {
        str(pair.get("candidate_tl_id", "")): pair
        for pair in (alignment.get("pairs", []) if isinstance(alignment, dict) else [])
        if isinstance(pair, dict) and str(pair.get("candidate_tl_id", "")).strip()
    }

    records: list[dict[str, Any]] = []
    for queue_index, item in enumerate(queue or [], start=1):
        if not isinstance(item, dict):
            continue
        reference_tl = str(item.get("reference_tl_id", ""))
        candidate_tl = str(item.get("candidate_tl_id", ""))
        reference_from = str(item.get("reference_edge_id", ""))
        candidate_from = str(item.get("candidate_edge_id", ""))
        candidate_alignment_pair = alignment_pairs_by_candidate_tl.get(candidate_tl, {})
        candidate_controller_junction_ids = {
            str(junction_id)
            for junction_id in candidate_alignment_pair.get("candidate_junction_ids", []) or []
            if str(junction_id).strip()
        }
        reference_movement = [
            connection
            for connection in reference_connections
            if connection["tl"] == reference_tl and connection["from"] == reference_from
        ]
        candidate_movement = [
            connection
            for connection in candidate_connections
            if connection["tl"] == candidate_tl and connection["from"] == candidate_from
        ]
        reference_counter = Counter(_movement_rank_key(connection, reference_edges) for connection in reference_movement)
        candidate_counter = Counter(_movement_rank_key(connection, candidate_edges) for connection in candidate_movement)
        missing_connections = []
        for connection in reference_movement:
            key = _movement_rank_key(connection, reference_edges)
            if candidate_counter[key] <= 0:
                destination_candidates = _destination_candidates(
                    candidate_edges,
                    reference_edges.get(connection["to"], {}),
                    candidate_edges.get(candidate_from, {}),
                    candidate_controller_junction_ids=candidate_controller_junction_ids,
                )
                mapping_status = "unmapped_destination_edge"
                if destination_candidates:
                    # A TLS group can contain several adjacent junctions, but
                    # that does not make an edge-to-edge connection legal.  A
                    # repair is endpoint-confirmed only when the selected
                    # destination starts at the exact terminal junction of
                    # the candidate source edge.  Group membership is useful
                    # for ordering/review evidence, never for silently
                    # authorizing a cross-junction connection.
                    if any(
                        item.get("from") == candidate_edges.get(candidate_from, {}).get("to")
                        for item in destination_candidates
                    ):
                        mapping_status = "destination_edge_and_endpoint_mapped"
                    else:
                        mapping_status = "destination_root_mapped_endpoint_review"
                candidate_to = destination_candidates[0] if destination_candidates else {}
                reference_from_metadata = reference_edges.get(reference_from, {})
                reference_to_metadata = reference_edges.get(connection["to"], {})
                from_rank = reference_from_metadata.get("passenger_lane_rank_by_index", {}).get(
                    connection["from_lane"]
                )
                to_rank = reference_to_metadata.get("passenger_lane_rank_by_index", {}).get(
                    connection["to_lane"]
                )
                candidate_from_lane = _lane_for_rank(candidate_edges.get(candidate_from, {}), from_rank)
                candidate_to_lane = _lane_for_rank(candidate_to, to_rank)
                lane_mapping_status = "passenger_lane_rank_unavailable"
                if candidate_from_lane is not None and candidate_to_lane is not None:
                    lane_mapping_status = "passenger_lane_rank_mapped"
                if (
                    mapping_status == "destination_edge_and_endpoint_mapped"
                    and lane_mapping_status != "passenger_lane_rank_mapped"
                ):
                    mapping_status = "destination_edge_endpoint_mapped_lane_review"
                missing_connections.append(
                    {
                        "reference_connection_index": connection["index"],
                        "reference_link_index": connection["link_index"],
                        "reference_direction": connection["dir"],
                        "reference_to_edge_id": connection["to"],
                        "reference_to_split_root": connection["to_split_root"],
                        "reference_from_lane": connection["from_lane"],
                        "reference_to_lane": connection["to_lane"],
                        "candidate_destination_candidates": destination_candidates,
                        "candidate_destination_edge_id": candidate_to.get("id", ""),
                        "mapping_status": mapping_status,
                        "reference_from_passenger_lane_rank": from_rank,
                        "reference_to_passenger_lane_rank": to_rank,
                        "candidate_from_lane": candidate_from_lane,
                        "candidate_to_lane": candidate_to_lane,
                        "lane_mapping_status": lane_mapping_status,
                        "repair_action": "rebuild_with_netconvert_connection_semantics",
                    }
                )
                candidate_counter[key] += 1

        if not missing_connections:
            continue
        records.append(
            {
                "queue_index": queue_index,
                "reference_tl_id": reference_tl,
                "candidate_tl_id": candidate_tl,
                "reference_from_edge_id": reference_from,
                "candidate_from_edge_id": candidate_from,
                "controller_distance_m": item.get("controller_distance_m", ""),
                "bearing_delta_deg": item.get("bearing_delta_deg", ""),
                "missing_direction_counts": item.get("missing_direction_counts", {}),
                "reference_movement_count": sum(reference_counter.values()),
                "candidate_movement_count": sum(candidate_counter.values()),
                "missing_connection_count": len(missing_connections),
                "missing_connections": missing_connections,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    report_file = output_dir / f"{prefix}.json"
    report = {
        "schema_version": 1,
        "status": "pass",
        "claim_status": "diagnostic-only",
        "reference_net_file": str(reference_net_file),
        "candidate_net_file": str(candidate_net_file),
        "output_dir": str(output_dir),
        "high_confidence_gap_count": len(queue or []),
        "mapped_gap_count": len(records),
        "missing_connection_count": sum(item["missing_connection_count"] for item in records),
        "destination_edge_and_endpoint_mapped_count": sum(
            1
            for item in records
            for connection in item["missing_connections"]
            if connection["mapping_status"] == "destination_edge_and_endpoint_mapped"
        ),
        "destination_root_mapped_endpoint_review_count": sum(
            1
            for item in records
            for connection in item["missing_connections"]
            if connection["mapping_status"] == "destination_root_mapped_endpoint_review"
        ),
        "destination_edge_endpoint_mapped_lane_review_count": sum(
            1
            for item in records
            for connection in item["missing_connections"]
            if connection["mapping_status"] == "destination_edge_endpoint_mapped_lane_review"
        ),
        "passenger_lane_rank_mapped_count": sum(
            1
            for item in records
            for connection in item["missing_connections"]
            if connection["lane_mapping_status"] == "passenger_lane_rank_mapped"
        ),
        "unmapped_destination_edge_count": sum(
            1
            for item in records
            for connection in item["missing_connections"]
            if connection["mapping_status"] == "unmapped_destination_edge"
        ),
        "repair_variant_status": "not_created",
        "repair_safe": False,
        "records": records,
        "warning": "destination mapping does not authorize direct .net.xml edits or TLS phase changes",
    }
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report["report_file"] = str(report_file)
    return report


def build_tls_gap_repair_variant(
    *,
    mapping_report: dict[str, Any],
    candidate_net_file: Path,
    output_dir: Path,
    prefix: str = "tls_gap_repair",
    netconvert_binary: str = "netconvert",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., Any] = run_command,
) -> dict[str, Any]:
    """Build a non-promoted variant from only exact endpoint/lane mappings.

    The patch deliberately contains no guessed destination, lane, phase, or
    internal ``via`` value.  netconvert owns internal-connection generation;
    the result remains a review candidate until the workflow's semantic and
    topology gates pass.
    """

    candidate_net_file = candidate_net_file.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    patch_file = output_dir / f"{prefix}_connections.con.xml"
    variant_file = output_dir / f"{prefix}.net.xml"
    report_file = output_dir / f"{prefix}.json"
    before_hash = _sha256_file(candidate_net_file)

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for record in mapping_report.get("records", []) or []:
        if not isinstance(record, dict):
            continue
        for connection in record.get("missing_connections", []) or []:
            if not isinstance(connection, dict):
                continue
            if connection.get("mapping_status") != "destination_edge_and_endpoint_mapped":
                continue
            if connection.get("lane_mapping_status") != "passenger_lane_rank_mapped":
                continue
            key = (
                str(record.get("candidate_tl_id", "")),
                str(record.get("candidate_from_edge_id", "")),
                str(connection.get("candidate_destination_edge_id", "")),
                str(connection.get("candidate_from_lane", "")),
                str(connection.get("candidate_to_lane", "")),
            )
            if not all(key) or key in seen:
                continue
            seen.add(key)
            selected.append(
                {
                    "candidate_tl_id": key[0],
                    "candidate_from_edge_id": key[1],
                    "candidate_destination_edge_id": key[2],
                    "candidate_from_lane": key[3],
                    "candidate_to_lane": key[4],
                    "reference_tl_id": str(record.get("reference_tl_id", "")),
                    "reference_connection_index": connection.get("reference_connection_index", ""),
                    "direction": str(connection.get("reference_direction", "")),
                }
            )

    patch_root = ET.Element("connections")
    for item in selected:
        ET.SubElement(
            patch_root,
            "connection",
            {
                "from": item["candidate_from_edge_id"],
                "to": item["candidate_destination_edge_id"],
                "fromLane": item["candidate_from_lane"],
                "toLane": item["candidate_to_lane"],
                "tl": item["candidate_tl_id"],
                "dir": item["direction"],
            },
        )
    ET.indent(patch_root, space="    ")
    ET.ElementTree(patch_root).write(patch_file, encoding="utf-8", xml_declaration=True)

    command = [
        netconvert_binary,
        "--sumo-net-file",
        str(candidate_net_file),
        "--connection-files",
        str(patch_file),
        "--output-file",
        str(variant_file),
    ]
    raw_result = command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds)
    if hasattr(raw_result, "to_dict"):
        netconvert_report = raw_result.to_dict()
    elif isinstance(raw_result, dict):
        netconvert_report = dict(raw_result)
    else:
        netconvert_report = {
            "status": getattr(raw_result, "status", "fail"),
            "returncode": getattr(raw_result, "returncode", None),
        }
    if "status" not in netconvert_report:
        netconvert_report["status"] = "pass" if netconvert_report.get("returncode") == 0 else "fail"

    variant_connections: list[dict[str, str]] = []
    phase_capacity_status = "skipped"
    parse_error = ""
    if variant_file.exists():
        try:
            variant_root = ET.parse(variant_file).getroot()
            selected_keys = {
                (
                    item["candidate_tl_id"],
                    item["candidate_from_edge_id"],
                    item["candidate_destination_edge_id"],
                    item["candidate_from_lane"],
                    item["candidate_to_lane"],
                )
                for item in selected
            }
            for connection in variant_root.findall("connection"):
                key = (
                    connection.attrib.get("tl", ""),
                    connection.attrib.get("from", ""),
                    connection.attrib.get("to", ""),
                    connection.attrib.get("fromLane", ""),
                    connection.attrib.get("toLane", ""),
                )
                if key in selected_keys:
                    variant_connections.append(dict(connection.attrib))
            phase_lengths = {
                tl_logic.attrib.get("id", ""): max(
                    (len(phase.attrib.get("state", "")) for phase in tl_logic.findall("phase")),
                    default=0,
                )
                for tl_logic in variant_root.findall("tlLogic")
            }
            phase_capacity_status = (
                "pass"
                if all(
                    connection.get("linkIndex", "").isdigit()
                    and int(connection["linkIndex"]) < phase_lengths.get(connection.get("tl", ""), 0)
                    for connection in variant_connections
                )
                else "fail"
            )
        except (OSError, ET.ParseError) as exc:
            parse_error = f"{type(exc).__name__}: {exc}"

    status = (
        "pass"
        if netconvert_report.get("status") == "pass" and variant_file.exists() and not parse_error
        else "fail"
    )
    report = {
        "schema_version": 1,
        "status": status,
        "claim_status": "diagnostic-only" if status == "pass" else "construction-invalid",
        "repair_variant_status": "created_for_review" if status == "pass" else "not_created",
        "repair_safe": False,
        "decision": "do_not_apply",
        "candidate_net_file": str(candidate_net_file),
        "candidate_net_sha256_before": before_hash,
        "candidate_net_sha256_after": _sha256_file(candidate_net_file),
        "main_network_mutated": before_hash != _sha256_file(candidate_net_file),
        "patch_file": str(patch_file),
        "variant_file": str(variant_file) if variant_file.exists() else "",
        "report_file": str(report_file),
        "selected_connection_count": len(selected),
        "selected_connections": selected,
        "variant_selected_connection_count": len(variant_connections),
        "variant_connections_with_via_count": sum(1 for item in variant_connections if item.get("via")),
        "variant_phase_capacity_status": phase_capacity_status,
        "netconvert": netconvert_report,
        "parse_error": parse_error,
        "warning": "variant is not promotable until SUMO, routeability, TLS phase/linkIndex/via, and topology gates pass",
    }
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def build_tls_repair_decision_report(
    *,
    mapping_report: dict[str, Any],
    variant_report: dict[str, Any] | None = None,
    sumo_load_report: dict[str, Any] | None = None,
    semantic_report: dict[str, Any] | None = None,
    tls_variant_semantic_report: dict[str, Any] | None = None,
    output_dir: Path,
    prefix: str = "tls_repair_decision",
) -> dict[str, Any]:
    """Write an explicit repair decision when a safe variant is not justified."""

    output_dir.mkdir(parents=True, exist_ok=True)
    fully_mapped = int(mapping_report.get("destination_edge_and_endpoint_mapped_count", 0) or 0)
    lane_review = int(mapping_report.get("destination_edge_endpoint_mapped_lane_review_count", 0) or 0)
    endpoint_review = int(mapping_report.get("destination_root_mapped_endpoint_review_count", 0) or 0)
    unmapped = int(mapping_report.get("unmapped_destination_edge_count", 0) or 0)
    lane_mapped = int(mapping_report.get("passenger_lane_rank_mapped_count", 0) or 0)
    missing = int(mapping_report.get("missing_connection_count", 0) or 0)
    blocked_reasons = []
    if unmapped:
        blocked_reasons.append(f"{unmapped} destination edge(s) cannot be mapped")
    if endpoint_review:
        blocked_reasons.append(f"{endpoint_review} destination root match(es) lack endpoint confirmation")
    if lane_review:
        blocked_reasons.append(f"{lane_review} mapped destination(s) lack passenger lane-rank confirmation")
    if lane_mapped < missing:
        blocked_reasons.append(
            f"only {lane_mapped}/{missing} missing movements have passenger lane-rank mapping"
        )
    variant_status = str((variant_report or {}).get("status", "not_created"))
    if variant_status != "pass":
        blocked_reasons.append("TLS repair variant was not successfully generated by netconvert")
    elif str((variant_report or {}).get("variant_phase_capacity_status", "fail")) != "pass":
        blocked_reasons.append("TLS repair variant has phase/linkIndex capacity gaps")
    if sumo_load_report is not None and sumo_load_report.get("status") != "pass":
        blocked_reasons.append("TLS repair variant failed SUMO load")
    if semantic_report is None:
        blocked_reasons.append("TLS repair variant reference semantic audit was not run")
    elif _semantic_parity_status(semantic_report) != "pass":
        blocked_reasons.append("TLS repair variant reference semantic audit failed")
    if tls_variant_semantic_report is None:
        blocked_reasons.append("TLS phase-state/linkIndex/via parity audit was not run")
    elif tls_variant_semantic_report.get("status") != "pass":
        blocked_reasons.append("TLS phase-state/linkIndex/via parity audit failed")
    status = "pass" if not blocked_reasons else "blocked"
    repair_safe = status == "pass"
    report = {
        "schema_version": 1,
        "status": status,
        "claim_status": "formal-evidence" if status == "pass" else "construction-invalid",
        "repair_variant_status": str((variant_report or {}).get("repair_variant_status", "not_created")),
        "repair_safe": repair_safe,
        "decision": "promote_variant" if status == "pass" else "do_not_apply",
        "source_mapping_report_file": str(mapping_report.get("report_file", "")),
        "missing_connection_count": missing,
        "fully_endpoint_and_lane_mapped_count": fully_mapped,
        "passenger_lane_rank_mapped_count": lane_mapped,
        "endpoint_review_count": endpoint_review,
        "lane_review_count": lane_review,
        "unmapped_destination_edge_count": unmapped,
        "blocked_reasons": blocked_reasons,
        "repair_variant_file": str((variant_report or {}).get("variant_file", "")),
        "repair_variant_report_file": str((variant_report or {}).get("report_file", "")),
        "repair_variant_netconvert_status": str((variant_report or {}).get("status", "skipped")),
        "repair_variant_sumo_load_status": "skipped"
        if sumo_load_report is None
        else str(sumo_load_report.get("status", "fail")),
        "tls_variant_semantic_status": "skipped"
        if tls_variant_semantic_report is None
        else str(tls_variant_semantic_report.get("status", "fail")),
        "tls_variant_semantic_report_file": ""
        if tls_variant_semantic_report is None
        else str(tls_variant_semantic_report.get("report_file", "")),
        "repair_variant_reference_audit_status": "skipped"
        if semantic_report is None
        else str(semantic_report.get("status", "fail")),
        "repair_variant_reference_parity_status": _semantic_parity_status(semantic_report),
        "required_before_variant": [
            "resolve destination endpoint for every candidate connection",
            "resolve passenger lane rank for every candidate connection",
            "rebuild SUMO internal via edges with netconvert",
            "assign new TLS linkIndex values and phase states from evidence",
            "run SUMO load, routeability, connection semantics, TLS and topology gates",
        ],
        "main_network_mutated": False,
    }
    report_file = output_dir / f"{prefix}.json"
    report["report_file"] = str(report_file)
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def audit_tls_gap_variant_semantics(
    *,
    mapping_report: dict[str, Any],
    variant_report: dict[str, Any],
    candidate_net_file: Path,
    variant_net_file: Path,
    output_dir: Path,
    prefix: str = "tls_gap_variant_semantic_parity",
) -> dict[str, Any]:
    """Verify the exact TLS properties introduced by a repair candidate.

    This is intentionally a local parity check.  It proves that every
    selected movement survived netconvert with a valid direction, TLS
    linkIndex, phase capacity, and internal ``via`` edge, while leaving the
    broader reference-network parity decision to the reference audit.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    report_file = _short_output_path(output_dir, prefix, ".json")
    selected = [
        item
        for item in variant_report.get("selected_connections", []) or []
        if isinstance(item, dict)
    ]
    errors: list[str] = []
    checked: list[dict[str, Any]] = []
    candidate_root: ET.Element | None = None
    variant_root: ET.Element | None = None
    try:
        candidate_root = ET.parse(candidate_net_file).getroot()
        variant_root = ET.parse(variant_net_file).getroot()
    except (OSError, ET.ParseError) as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    if candidate_root is not None and variant_root is not None:
        candidate_keys = {
            _tls_connection_key(connection)
            for connection in candidate_root.findall("connection")
            if connection.attrib.get("tl") and connection.attrib.get("linkIndex")
        }
        variant_connections = [
            connection
            for connection in variant_root.findall("connection")
            if connection.attrib.get("tl")
        ]
        variant_edges = {
            edge.attrib.get("id", "")
            for edge in variant_root.findall("edge")
            if edge.attrib.get("id")
        }
        variant_lanes = {
            lane.attrib.get("id", "")
            for edge in variant_root.findall("edge")
            for lane in edge.findall("lane")
            if lane.attrib.get("id")
        }
        phase_lengths = {
            logic.attrib.get("id", ""): max(
                (len(phase.attrib.get("state", "")) for phase in logic.findall("phase")),
                default=0,
            )
            for logic in variant_root.findall("tlLogic")
        }
        for item in selected:
            key = (
                str(item.get("candidate_tl_id", "")),
                str(item.get("candidate_from_edge_id", "")),
                str(item.get("candidate_destination_edge_id", "")),
                str(item.get("candidate_from_lane", "")),
                str(item.get("candidate_to_lane", "")),
            )
            matches = [
                connection
                for connection in variant_connections
                if _tls_connection_key(connection) == key
            ]
            row: dict[str, Any] = {"key": key, "match_count": len(matches)}
            if not all(key):
                row["status"] = "fail"
                row["reason"] = "selected movement key is incomplete"
                errors.append("selected movement key is incomplete")
                checked.append(row)
                continue
            if _tls_connection_key_from_values(key) in candidate_keys:
                row["status"] = "fail"
                row["reason"] = "selected movement already existed in candidate network"
                errors.append(f"movement already existed: {key}")
                checked.append(row)
                continue
            if len(matches) != 1:
                row["status"] = "fail"
                row["reason"] = "variant does not contain exactly one selected movement"
                errors.append(f"variant match count {len(matches)} for {key}")
                checked.append(row)
                continue
            connection = matches[0]
            expected_dir = str(item.get("direction", ""))
            actual_dir = connection.attrib.get("dir", "")
            link_index = connection.attrib.get("linkIndex", "")
            phase_capacity = phase_lengths.get(connection.attrib.get("tl", ""), 0)
            via = connection.attrib.get("via", "")
            row.update(
                {
                    "direction": actual_dir,
                    "expected_direction": expected_dir,
                    "linkIndex": link_index,
                    "phase_capacity": phase_capacity,
                    "via": via,
                    # SUMO's connection ``via`` normally references an internal
                    # lane id (for example ``:J0_0_0``), while some hand-authored
                    # fixtures use the internal edge id. Accept both exact forms.
                    "via_exists": not via or via in variant_edges or via in variant_lanes,
                }
            )
            row_errors: list[str] = []
            if expected_dir and actual_dir != expected_dir:
                row_errors.append("direction mismatch")
            if not link_index.isdigit() or int(link_index) >= phase_capacity:
                row_errors.append("linkIndex is outside TLS phase capacity")
            if not via:
                row_errors.append("netconvert did not emit an internal via edge")
            elif via not in variant_edges and via not in variant_lanes:
                row_errors.append("via edge is missing from variant")
            row["status"] = "pass" if not row_errors else "fail"
            if row_errors:
                row["reason"] = "; ".join(row_errors)
                errors.extend(f"{key}: {reason}" for reason in row_errors)
            checked.append(row)

    status = "pass" if not errors and variant_report.get("status") == "pass" else "fail"
    report = {
        "schema_version": 1,
        "status": status,
        "claim_status": "diagnostic-only" if status == "pass" else "construction-invalid",
        "candidate_net_file": str(candidate_net_file),
        "variant_net_file": str(variant_net_file),
        "mapping_report_file": str(mapping_report.get("report_file", "")),
        "variant_report_file": str(variant_report.get("report_file", "")),
        "selected_connection_count": len(selected),
        "checked_connection_count": len(checked),
        "direction_parity_status": "pass" if all(
            item.get("expected_direction", "") == item.get("direction", "")
            for item in checked
            if item.get("status") == "pass"
        ) and not any("direction mismatch" in error for error in errors) else "fail",
        "phase_linkindex_parity_status": "pass"
        if not any("linkIndex" in error for error in errors)
        else "fail",
        "via_parity_status": "pass" if not any("via" in error for error in errors) else "fail",
        "checked_connections": checked,
        "errors": errors,
        "report_file": str(report_file),
    }
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _semantic_parity_status(report: dict[str, Any] | None) -> str:
    if report is None:
        return "skipped"
    if report.get("status") != "pass":
        return "fail"
    if report.get("network_structural_delta_status") not in {None, "pass", "skipped"}:
        return "fail"
    if report.get("junction_pattern_comparison_status") not in {None, "pass", "skipped"}:
        return "fail"
    return "pass"


def _tls_connection_key(connection: ET.Element) -> tuple[str, str, str, str, str]:
    return _tls_connection_key_from_values(
        (
            connection.attrib.get("tl", ""),
            connection.attrib.get("from", ""),
            connection.attrib.get("to", ""),
            connection.attrib.get("fromLane", ""),
            connection.attrib.get("toLane", ""),
        )
    )


def _tls_connection_key_from_values(
    values: tuple[str, str, str, str, str],
) -> tuple[str, str, str, str, str]:
    return values


def _short_output_path(output_dir: Path, prefix: str, suffix: str) -> Path:
    candidate = output_dir / f"{prefix}{suffix}"
    if len(str(candidate.resolve())) < 239:
        return candidate
    digest = hashlib.sha1(str(candidate).encode("utf-8")).hexdigest()[:10]
    return output_dir / f"p_{digest}{suffix}"


def _edge_catalog(root: ET.Element) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id:
            continue
        passenger_lanes = [
            lane
            for lane in edge.findall("lane")
            if "passenger" in lane.attrib.get("allow", "").split()
            or not lane.attrib.get("allow")
        ]
        lane_indexes = sorted(
            (lane.attrib.get("index", "") for lane in passenger_lanes),
            key=lambda value: (0, int(value)) if value.isdigit() else (1, value),
        )
        catalog[edge_id] = {
            "id": edge_id,
            "from": edge.attrib.get("from", ""),
            "to": edge.attrib.get("to", ""),
            "split_root": _split_root(edge_id),
            "passenger_lane_indexes": lane_indexes,
            "passenger_lane_rank_by_index": {
                lane_index: rank for rank, lane_index in enumerate(lane_indexes)
            },
        }
    return catalog


def _sha256_file(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connection_catalog(root: ET.Element, edges: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for index, connection in enumerate(root.findall("connection")):
        tl = connection.attrib.get("tl", "")
        if not tl or not connection.attrib.get("linkIndex"):
            continue
        from_edge = connection.attrib.get("from", "")
        to_edge = connection.attrib.get("to", "")
        records.append(
            {
                "index": index,
                "tl": tl,
                "from": from_edge,
                "to": to_edge,
                "link_index": connection.attrib.get("linkIndex", ""),
                "to_split_root": edges.get(to_edge, {}).get("split_root", _split_root(to_edge)),
                "from_lane": connection.attrib.get("fromLane", ""),
                "to_lane": connection.attrib.get("toLane", ""),
                "dir": connection.attrib.get("dir", ""),
                "via": connection.attrib.get("via", ""),
            }
        )
    return records


def _movement_key(connection: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(connection.get("dir", "")),
        str(connection.get("to_split_root", "")),
        str(connection.get("from_lane", "")),
        str(connection.get("to_lane", "")),
    )


def _movement_rank_key(
    connection: dict[str, Any],
    edges: dict[str, dict[str, Any]],
) -> tuple[str, str, str, str]:
    """Compare movements across networks with different raw lane numbering."""
    from_edge = edges.get(str(connection.get("from", "")), {})
    to_edge = edges.get(str(connection.get("to", "")), {})
    from_rank = from_edge.get("passenger_lane_rank_by_index", {}).get(
        str(connection.get("from_lane", "")),
        f"lane:{connection.get('from_lane', '')}",
    )
    to_rank = to_edge.get("passenger_lane_rank_by_index", {}).get(
        str(connection.get("to_lane", "")),
        f"lane:{connection.get('to_lane', '')}",
    )
    return (
        str(connection.get("dir", "")),
        str(to_edge.get("split_root", connection.get("to_split_root", ""))),
        str(from_rank),
        str(to_rank),
    )


def _destination_candidates(
    candidate_edges: dict[str, dict[str, Any]],
    reference_to_edge: dict[str, Any],
    candidate_from_edge: dict[str, Any],
    *,
    candidate_controller_junction_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    root = str(reference_to_edge.get("split_root", ""))
    if not root:
        return []
    candidates = [
        edge
        for edge in candidate_edges.values()
        if edge.get("split_root") == root
    ]
    candidate_junction = str(candidate_from_edge.get("to", ""))
    controller_junctions = candidate_controller_junction_ids or set()
    return sorted(
        candidates,
        key=lambda edge: (
            0 if str(edge.get("from", "")) == candidate_junction else 1,
            0 if str(edge.get("from", "")) in controller_junctions else 1,
            str(edge.get("id", "")),
        ),
    )


def _lane_for_rank(edge: dict[str, Any], rank: int | None) -> str | None:
    if rank is None:
        return None
    lanes = edge.get("passenger_lane_indexes", [])
    if not isinstance(lanes, list) or rank < 0 or rank >= len(lanes):
        return None
    return str(lanes[rank])


def _split_root(edge_id: str) -> str:
    return edge_id.split("#", 1)[0]
