from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
import xml.etree.ElementTree as ET

from .candidate_contracts import file_sha256
from .command_runner import run_command
from .junction_join_definition import build_junction_join_definition
from .modal_aggregation_policy import classify_edge_modal_role

SHORT_MODAL_SUPPORT_EDGE_MAX_LENGTH_M = 10.0


def audit_join_collapse_residuals(net_file: Path, join_groups: Sequence[Sequence[str]]) -> dict[str, Any]:
    if not net_file.exists():
        return {**_failure(f"net file does not exist: {net_file}"), "residual_group_count": 0, "groups": []}
    try:
        root = ET.parse(net_file).getroot()
    except ET.ParseError as exc:
        return {**_failure(f"invalid SUMO net XML: {exc}"), "residual_group_count": 0, "groups": []}

    junction_ids = {
        junction.attrib.get("id", "")
        for junction in root.findall("junction")
        if junction.attrib.get("id", "") and not junction.attrib.get("id", "").startswith(":")
    }
    edges = root.findall("edge")
    groups = []
    for raw_group in join_groups:
        node_ids = sorted({str(node_id) for node_id in raw_group if str(node_id)})
        if len(node_ids) < 2:
            continue
        node_set = set(node_ids)
        remaining_nodes = [node_id for node_id in node_ids if node_id in junction_ids]
        plain_edges = sorted(
            edge.attrib.get("id", "")
            for edge in edges
            if _edge_is_plain(edge)
            and edge.attrib.get("from", "") in node_set
            and edge.attrib.get("to", "") in node_set
        )
        internal_edges = sorted(
            edge.attrib.get("id", "")
            for edge in edges
            if not _edge_is_plain(edge) and _internal_id_uses_join_node(edge.attrib.get("id", ""), node_set)
        )
        connection_vias = sorted(
            connection.attrib.get("via", "")
            for connection in root.findall("connection")
            if _internal_id_uses_join_node(connection.attrib.get("via", ""), node_set)
        )
        if remaining_nodes or plain_edges or internal_edges or connection_vias:
            groups.append(
                {
                    "node_ids": node_ids,
                    "remaining_core_node_ids": remaining_nodes,
                    "residual_plain_edge_ids": plain_edges,
                    "residual_internal_edge_ids": internal_edges,
                    "residual_connection_via_ids": connection_vias,
                }
            )
    return {
        "status": "pass" if not groups else "needs_cleanup",
        "claim_status": "diagnostic-demo",
        "net_file": str(net_file),
        "residual_group_count": len(groups),
        "groups": groups,
    }


def audit_join_output_presence(net_file: Path, join_groups: Sequence[Sequence[str]]) -> dict[str, Any]:
    if not net_file.exists():
        return {**_failure(f"net file does not exist: {net_file}"), "missing_joined_junction_count": 0}
    try:
        root = ET.parse(net_file).getroot()
    except ET.ParseError as exc:
        return {**_failure(f"invalid SUMO net XML: {exc}"), "missing_joined_junction_count": 0}

    junction_ids = {
        junction.attrib.get("id", "")
        for junction in root.findall("junction")
        if junction.attrib.get("id", "") and not junction.attrib.get("id", "").startswith(":")
    }
    missing = []
    for group in join_groups:
        node_ids = sorted({str(node_id) for node_id in group if str(node_id)})
        if len(node_ids) < 2:
            continue
        expected_id = _sumo_joined_cluster_id(node_ids)
        if expected_id not in junction_ids:
            missing.append({"node_ids": node_ids, "expected_junction_id": expected_id})

    return {
        "status": "pass" if not missing else "missing_joined_junctions",
        "claim_status": "diagnostic-demo",
        "net_file": str(net_file),
        "missing_joined_junction_count": len(missing),
        "missing_joined_junctions": missing,
    }


def audit_junction_aggregation_preservation(
    source_net_file: Path,
    variant_net_file: Path,
    *,
    join_groups: Sequence[Sequence[str]] = (),
) -> dict[str, Any]:
    if not source_net_file.exists():
        return {**_failure(f"source net file does not exist: {source_net_file}"), "removed_normal_edge_count": 0}
    if not variant_net_file.exists():
        return {**_failure(f"variant net file does not exist: {variant_net_file}"), "removed_normal_edge_count": 0}
    try:
        source_root = ET.parse(source_net_file).getroot()
        variant_root = ET.parse(variant_net_file).getroot()
    except ET.ParseError as exc:
        return {**_failure(f"invalid SUMO net XML: {exc}"), "removed_normal_edge_count": 0}

    source_net_file = source_net_file.resolve()
    variant_net_file = variant_net_file.resolve()
    source_edges = _plain_edges_by_id(source_root)
    variant_edges = _plain_edges_by_id(variant_root)
    removed_edge_ids = sorted(set(source_edges) - set(variant_edges))
    removed_edges = [source_edges[edge_id] for edge_id in removed_edge_ids]
    joined_node_sets = [{str(node_id) for node_id in group} for group in join_groups]
    absorbed_join_edge_ids = sorted(
        edge_id
        for edge_id in removed_edge_ids
        if any(
            source_edges[edge_id].attrib.get("from", "") in node_ids
            and source_edges[edge_id].attrib.get("to", "") in node_ids
            for node_ids in joined_node_sets
        )
    )
    unexpected_removed_edge_ids = sorted(set(removed_edge_ids) - set(absorbed_join_edge_ids))
    shared_edge_ids = set(source_edges) & set(variant_edges)
    source_connection_signatures = _shared_connection_signatures(source_root, shared_edge_ids)
    variant_connection_signatures = _shared_connection_signatures(variant_root, shared_edge_ids)
    lost_shared_connections = sorted(source_connection_signatures - variant_connection_signatures)
    boundary_movements = _audit_join_boundary_movements(source_root, variant_root, join_groups)
    source_dangling = _dangling_plain_edge_ids(source_root, shared_edge_ids)
    variant_dangling = _dangling_plain_edge_ids(variant_root, shared_edge_ids)
    new_dangling = sorted(variant_dangling - source_dangling)

    return {
        "schema": "torii.junction-aggregation-preservation/v1",
        "status": (
            "review"
            if unexpected_removed_edge_ids
            or lost_shared_connections
            or new_dangling
            or boundary_movements["status"] not in {"pass", "not_applicable"}
            else "pass"
        ),
        "claim_status": "diagnostic-demo",
        "source_net_file": str(source_net_file),
        "source_sha256": file_sha256(source_net_file),
        "variant_net_file": str(variant_net_file),
        "variant_sha256": file_sha256(variant_net_file),
        "removed_normal_edge_count": len(removed_edge_ids),
        "removed_normal_edge_ids": removed_edge_ids,
        "absorbed_join_edge_count": len(absorbed_join_edge_ids),
        "absorbed_join_edge_ids": absorbed_join_edge_ids,
        "unexpected_removed_normal_edge_count": len(unexpected_removed_edge_ids),
        "unexpected_removed_normal_edge_ids": unexpected_removed_edge_ids,
        "removed_normal_edge_type_counts": dict(sorted(Counter(_edge_type(edge) for edge in removed_edges).items())),
        "removed_normal_edge_mode_counts": dict(
            sorted(Counter(mode for edge in removed_edges for mode in _edge_modes(edge)).items())
        ),
        "lost_shared_connection_count": len(lost_shared_connections),
        "lost_shared_connections": lost_shared_connections,
        "boundary_movement_preservation": boundary_movements,
        "new_dangling_shared_normal_edge_count": len(new_dangling),
        "new_dangling_shared_normal_edge_ids": new_dangling,
    }


def build_junction_aggregation_variant(
    *,
    net_file: Path,
    output_dir: Path,
    prefix: str = "junction_aggregation",
    topology_audit_report: Mapping[str, Any] | None = None,
    reference_join_audit_report: Mapping[str, Any] | None = None,
    overlapping_junction_audit_report: Mapping[str, Any] | None = None,
    join_dist_m: float = 30.0,
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., Any] = run_command,
) -> dict[str, Any]:
    if join_dist_m <= 0:
        return _failure("join_dist_m must be positive")
    if not net_file.exists():
        return _failure(f"net file does not exist: {net_file}")
    net_file = net_file.resolve()
    output_dir = output_dir.resolve()

    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = _aggregation_candidates(
        topology_audit_report=topology_audit_report,
        reference_join_audit_report=reference_join_audit_report,
        overlapping_junction_audit_report=overlapping_junction_audit_report,
    )
    candidates = _filter_modal_support_join_nodes(candidates, net_file)
    plan_file = output_dir / f"{prefix}_plan.json"
    candidates_file = output_dir / f"{prefix}_candidates.csv"
    command_record = output_dir / f"{prefix}_netconvert.cmd.txt"
    variant_file = output_dir / f"{prefix}_junction_aggregated.net.xml"
    joined_junctions_file = output_dir / f"{prefix}_joined_junctions.xml"
    collapse_audit_file = output_dir / f"{prefix}_collapse_audit.json"
    join_output_audit_file = output_dir / f"{prefix}_join_output_audit.json"
    preservation_audit_file = output_dir / f"{prefix}_preservation_audit.json"
    join_definition = build_junction_join_definition(candidates, output_dir=output_dir, prefix=prefix)
    remove_edges_file = output_dir / f"{prefix}_modal_support_remove_edges.txt"
    remove_edge_ids = _short_modal_support_edges(
        net_file,
        join_core_nodes=_explicit_join_nodes(join_definition),
        max_length_m=SHORT_MODAL_SUPPORT_EDGE_MAX_LENGTH_M,
    )
    remove_edges_file.write_text(
        "\n".join(remove_edge_ids) + ("\n" if remove_edge_ids else ""),
        encoding="utf-8",
    )

    plan = {
        "junction_aggregation_status": "not_needed" if not candidates else "planned_for_review_variant",
        "net_file": str(net_file),
        "variant_file": str(variant_file) if candidates else "",
        "joined_junctions_file": str(joined_junctions_file) if candidates else "",
        "nodes_patch_file": join_definition["nodes_patch_file"],
        "join_definition_file": join_definition["definition_file"],
        "join_definition_csv": join_definition["definition_csv"],
        "modal_support_remove_edges_file": str(remove_edges_file),
        "modal_support_remove_edge_count": len(remove_edge_ids),
        "modal_support_remove_edge_max_length_m": SHORT_MODAL_SUPPORT_EDGE_MAX_LENGTH_M,
        "join_dist_m": join_dist_m,
        "join_dist_policy": "recorded for legacy scoring context; precise joins are driven by the nodes patch",
        "candidate_count": len(candidates),
        "explicit_join_count": join_definition["explicit_join_count"],
        "join_exclude_count": join_definition["join_exclude_count"],
        "needs_map_review_count": join_definition["needs_map_review_count"],
        "candidate_sources": sorted({candidate["source"] for candidate in candidates}),
        "review_policy": (
            "create a separate netconvert nodes-patch variant for Netedit and Google Maps review; "
            "do not overwrite the source network"
        ),
        "candidates": candidates,
    }
    plan_file.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_candidates_csv(candidates_file, candidates)

    if not candidates:
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "junction_aggregation_status": "not_needed",
            "junction_aggregation_candidate_count": 0,
            "junction_aggregation_plan_file": str(plan_file),
            "junction_aggregation_candidates_file": str(candidates_file),
            "junction_aggregation_variant_file": "",
            "junction_aggregation_joined_junctions_file": "",
            "junction_aggregation_collapse_audit_file": "",
            "junction_aggregation_collapse_audit_status": "not_needed",
            "junction_join_nodes_patch_file": join_definition["nodes_patch_file"],
            "junction_join_definition_file": join_definition["definition_file"],
            "junction_join_definition_csv": join_definition["definition_csv"],
            "junction_aggregation_modal_support_remove_edges_file": str(remove_edges_file),
            "junction_aggregation_removed_modal_support_edge_count": 0,
            "junction_aggregation_command_record": "",
            "junction_aggregation_netconvert": {},
            "warnings": [],
        }

    command = [
        "netconvert",
        "--sumo-net-file",
        str(net_file),
        "--node-files",
        _command_path(Path(str(join_definition["nodes_patch_file"])), output_dir),
        *(
            [
                "--remove-edges.explicit",
                ",".join(remove_edge_ids),
            ]
            if remove_edge_ids
            else []
        ),
        "--junctions.join-output",
        _command_path(joined_junctions_file, output_dir),
        "--output-file",
        _command_path(variant_file, output_dir),
    ]
    command_record.write_text(" ".join(command) + "\n", encoding="utf-8")
    try:
        result = _result_to_dict(command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds))
    except OSError as exc:
        return {
            **_failure(f"{type(exc).__name__}: {exc}"),
            "junction_aggregation_status": "failed",
            "junction_aggregation_plan_file": str(plan_file),
            "junction_aggregation_candidates_file": str(candidates_file),
            "junction_aggregation_variant_file": str(variant_file),
            "junction_aggregation_joined_junctions_file": str(joined_junctions_file),
            "junction_aggregation_collapse_audit_file": str(collapse_audit_file),
            "junction_aggregation_collapse_audit_status": "not_run",
            "junction_join_nodes_patch_file": join_definition["nodes_patch_file"],
            "junction_join_definition_file": join_definition["definition_file"],
            "junction_join_definition_csv": join_definition["definition_csv"],
            "junction_aggregation_modal_support_remove_edges_file": str(remove_edges_file),
            "junction_aggregation_removed_modal_support_edge_count": len(remove_edge_ids),
            "junction_aggregation_command_record": str(command_record),
        }

    netconvert_ok = result.get("status") == "pass" and variant_file.exists()
    join_groups = _explicit_join_groups(join_definition)
    collapse_audit = audit_join_collapse_residuals(variant_file, join_groups) if netconvert_ok else {}
    join_output_audit = audit_join_output_presence(variant_file, join_groups) if netconvert_ok else {}
    preservation_audit = (
        audit_junction_aggregation_preservation(net_file, variant_file, join_groups=join_groups)
        if netconvert_ok
        else {}
    )
    if collapse_audit:
        collapse_audit_file.write_text(json.dumps(collapse_audit, indent=2, ensure_ascii=False), encoding="utf-8")
    if join_output_audit:
        join_output_audit_file.write_text(json.dumps(join_output_audit, indent=2, ensure_ascii=False), encoding="utf-8")
    if preservation_audit:
        preservation_audit_file.write_text(json.dumps(preservation_audit, indent=2, ensure_ascii=False), encoding="utf-8")
    status = (
        "pass"
        if netconvert_ok
        and collapse_audit.get("status") == "pass"
        and join_output_audit.get("status") == "pass"
        else "fail"
    )
    warnings = [
        "junction aggregation variant requires Google Maps and Netedit review before adoption",
    ]
    if not netconvert_ok:
        warnings.append(f"junction aggregation variant was not created: {variant_file}")
    elif collapse_audit.get("status") != "pass":
        warnings.append("junction aggregation variant still contains uncollapsed core-node topology")
    elif join_output_audit.get("status") != "pass":
        warnings.append("junction aggregation variant is missing one or more planned joined junctions")
    return {
        "status": status,
        "claim_status": "blocked" if status == "pass" else "construction-invalid",
        "junction_aggregation_status": "variant_created_for_review" if status == "pass" else "failed",
        "junction_aggregation_candidate_count": len(candidates),
        "junction_aggregation_plan_file": str(plan_file),
        "junction_aggregation_candidates_file": str(candidates_file),
        "junction_aggregation_variant_file": str(variant_file),
        "junction_aggregation_joined_junctions_file": str(joined_junctions_file),
        "junction_aggregation_collapse_audit_file": str(collapse_audit_file) if collapse_audit else "",
        "junction_aggregation_collapse_audit_status": str(collapse_audit.get("status", "not_run")),
        "junction_aggregation_collapse_residual_group_count": int(collapse_audit.get("residual_group_count", 0)),
        "junction_aggregation_join_output_audit_file": str(join_output_audit_file) if join_output_audit else "",
        "junction_aggregation_join_output_audit_status": str(join_output_audit.get("status", "not_run")),
        "junction_aggregation_missing_joined_junction_count": int(
            join_output_audit.get("missing_joined_junction_count", 0)
        ),
        "junction_aggregation_preservation_audit_file": str(preservation_audit_file) if preservation_audit else "",
        "junction_aggregation_preservation_status": str(preservation_audit.get("status", "not_run")),
        "junction_aggregation_removed_normal_edge_count": int(
            preservation_audit.get("removed_normal_edge_count", 0)
        ),
        "junction_aggregation_removed_normal_edge_type_counts": preservation_audit.get(
            "removed_normal_edge_type_counts", {}
        ),
        "junction_aggregation_removed_normal_edge_mode_counts": preservation_audit.get(
            "removed_normal_edge_mode_counts", {}
        ),
        "junction_aggregation_lost_shared_connection_count": int(
            preservation_audit.get("lost_shared_connection_count", 0)
        ),
        "junction_aggregation_new_dangling_shared_normal_edge_count": int(
            preservation_audit.get("new_dangling_shared_normal_edge_count", 0)
        ),
        "junction_join_nodes_patch_file": join_definition["nodes_patch_file"],
        "junction_join_definition_file": join_definition["definition_file"],
        "junction_join_definition_csv": join_definition["definition_csv"],
        "junction_join_explicit_join_count": join_definition["explicit_join_count"],
        "junction_join_exclude_count": join_definition["join_exclude_count"],
        "junction_join_needs_map_review_count": join_definition["needs_map_review_count"],
        "junction_aggregation_modal_support_remove_edges_file": str(remove_edges_file),
        "junction_aggregation_removed_modal_support_edge_count": len(remove_edge_ids),
        "junction_aggregation_command_record": str(command_record),
        "junction_aggregation_netconvert": result,
        "warnings": warnings,
    }


def _aggregation_candidates(
    *,
    topology_audit_report: Mapping[str, Any] | None,
    reference_join_audit_report: Mapping[str, Any] | None,
    overlapping_junction_audit_report: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if reference_join_audit_report is not None:
        for case in reference_join_audit_report.get("matched_cases", []) or []:
            target_evidence_confirmed = (
                str(case.get("transfer_gate_status", "blocked")) == "pass"
                and str(case.get("target_evidence_status", "blocked")) == "pass"
            )
            candidates.append(
                {
                    "source": "reference_join_audit",
                    "candidate_id": str(case.get("reference_id", "")),
                    "decision": "join" if target_evidence_confirmed else "needs_map_review",
                    "confidence": (
                        "target_evidence_confirmed" if target_evidence_confirmed else "teacher_prior"
                    ),
                    "node_ids": ";".join(_reference_join_candidate_node_ids(case)),
                    "reason": (
                        str(case.get("match_reason", case.get("learned_rule", "")))
                        if target_evidence_confirmed
                        else (
                            "human-cleaned reference is a prior only; independent target geometry, "
                            "movement, boundary, and audit evidence is required"
                        )
                    ),
                    "google_maps_url": str(case.get("google_maps_url", "")),
                }
            )
    if overlapping_junction_audit_report is not None:
        for group in overlapping_junction_audit_report.get("overlapping_junction_groups", []) or []:
            candidate = _candidate_from_overlapping_group(group)
            if candidate is not None:
                candidates.append(candidate)
    if topology_audit_report is not None:
        for cluster in topology_audit_report.get("suspicious_clusters", []) or []:
            decision = str(cluster.get("aggregation_decision", "needs_map_review"))
            if decision not in {"join", "needs_map_review"}:
                continue
            if str(cluster.get("corridor_decision", "")) == "reject":
                continue
            candidates.append(
                {
                    "source": "topology_audit",
                    "candidate_id": str(cluster.get("cluster_id", "")),
                    "decision": decision,
                    "confidence": str(cluster.get("aggregation_confidence", "")),
                    "node_ids": ";".join(str(item) for item in cluster.get("node_ids", []) or []),
                    "reason": str(cluster.get("aggregation_reason", "")),
                    "google_maps_url": str(cluster.get("google_maps_url", "")),
                }
            )
    return _dedupe_join_candidates(candidates)


def _reference_join_candidate_node_ids(case: Mapping[str, Any]) -> list[str]:
    if str(case.get("learned_rule_basis", "")) == "reference_source_nodes":
        source_nodes = [str(item) for item in case.get("matched_reference_source_node_ids", []) or [] if str(item)]
        if len(source_nodes) >= 2:
            return source_nodes
    for key in ("candidate_node_ids", "matched_candidate_node_ids", "matched_reference_source_node_ids"):
        node_ids = [str(item) for item in case.get(key, []) or [] if str(item)]
        if len(node_ids) >= 2:
            return node_ids
    return []


def _command_path(path: Path, cwd: Path) -> str:
    try:
        return str(path.resolve().relative_to(cwd))
    except ValueError:
        return str(path)


def _filter_modal_support_join_nodes(candidates: list[dict[str, Any]], net_file: Path) -> list[dict[str, Any]]:
    vehicle_core_nodes = _vehicle_core_nodes(net_file)
    if not vehicle_core_nodes:
        return candidates

    filtered: list[dict[str, Any]] = []
    for candidate in candidates:
        if str(candidate.get("decision", "")) != "join":
            filtered.append(candidate)
            continue
        if "target_evidence_confirmed" in {
            str(candidate.get("source", "")).lower(),
            str(candidate.get("confidence", "")).lower(),
        }:
            filtered.append(candidate)
            continue
        node_ids = [item for item in str(candidate.get("node_ids", "")).replace(",", ";").split(";") if item]
        kept = [node_id for node_id in node_ids if node_id in vehicle_core_nodes]
        if len(kept) == len(node_ids):
            filtered.append(candidate)
            continue
        updated = dict(candidate)
        updated["node_ids"] = ";".join(kept)
        updated["reason"] = (str(candidate.get("reason", "")) + "; modal support nodes excluded from vehicle core").strip("; ")
        filtered.append(updated)
    return filtered


def _vehicle_core_nodes(net_file: Path) -> set[str]:
    try:
        root = ET.parse(net_file).getroot()
    except ET.ParseError:
        return set()

    nodes: set[str] = set()
    for edge in root.findall("edge"):
        if edge.attrib.get("function") in {"internal", "crossing", "walkingarea"}:
            continue
        attrs = dict(edge.attrib)
        attrs["allow"] = " ".join(lane.attrib.get("allow", "") for lane in edge.findall("lane"))
        attrs["disallow"] = " ".join(lane.attrib.get("disallow", "") for lane in edge.findall("lane"))
        if classify_edge_modal_role(attrs)["modal_aggregation_decision"] != "join_core":
            continue
        for key in ("from", "to"):
            node_id = edge.attrib.get(key)
            if node_id:
                nodes.add(node_id)
    return nodes


def _explicit_join_nodes(join_definition: Mapping[str, Any]) -> set[str]:
    nodes: set[str] = set()
    for record in join_definition.get("records", []) or []:
        if str(record.get("action", "")) != "join":
            continue
        node_ids = record.get("node_ids", []) or []
        if len(node_ids) < 2:
            continue
        nodes.update(str(node_id) for node_id in node_ids if str(node_id))
    return nodes


def _explicit_join_groups(join_definition: Mapping[str, Any]) -> list[list[str]]:
    groups: list[list[str]] = []
    for record in join_definition.get("records", []) or []:
        if str(record.get("action", "")) != "join":
            continue
        node_ids = [str(node_id) for node_id in record.get("node_ids", []) or [] if str(node_id)]
        if len(node_ids) >= 2:
            groups.append(node_ids)
    return groups


def _sumo_joined_cluster_id(node_ids: Sequence[str]) -> str:
    ids = sorted(dict.fromkeys(str(node_id) for node_id in node_ids if str(node_id)))
    if not ids:
        return ""
    head = "_".join(ids[:4])
    suffix = "" if len(ids) <= 4 else f"_#{len(ids) - 4}more"
    return f"cluster_{head}{suffix}"


def _short_modal_support_edges(net_file: Path, *, join_core_nodes: set[str], max_length_m: float) -> list[str]:
    if not join_core_nodes:
        return []
    try:
        root = ET.parse(net_file).getroot()
    except ET.ParseError:
        return []

    remove_ids: list[str] = []
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function") in {"internal", "crossing", "walkingarea"}:
            continue
        if edge.attrib.get("from") not in join_core_nodes or edge.attrib.get("to") not in join_core_nodes:
            continue
        attrs = dict(edge.attrib)
        attrs["allow"] = " ".join(lane.attrib.get("allow", "") for lane in edge.findall("lane"))
        attrs["disallow"] = " ".join(lane.attrib.get("disallow", "") for lane in edge.findall("lane"))
        role = classify_edge_modal_role(attrs)
        if role["modal_aggregation_decision"] not in {"shape_support", "protected_terminal"}:
            continue
        if _edge_length_m(edge) <= max_length_m:
            remove_ids.append(edge_id)
    return sorted(remove_ids)


def _edge_length_m(edge: ET.Element) -> float:
    lengths: list[float] = []
    for lane in edge.findall("lane"):
        try:
            lengths.append(float(lane.attrib.get("length", "0") or 0))
        except ValueError:
            pass
    return max(lengths, default=0.0)


def _edge_is_plain(edge: ET.Element) -> bool:
    return not edge.attrib.get("id", "").startswith(":") and edge.attrib.get("function", "") not in {
        "internal",
        "crossing",
        "walkingarea",
    }


def _plain_edges_by_id(root: ET.Element) -> dict[str, ET.Element]:
    return {
        edge.attrib["id"]: edge
        for edge in root.findall("edge")
        if edge.attrib.get("id") and _edge_is_plain(edge)
    }


def _edge_type(edge: ET.Element) -> str:
    return edge.attrib.get("type", "") or "blank"


def _edge_modes(edge: ET.Element) -> list[str]:
    modes = []
    for lane in edge.findall("lane"):
        modes.extend(lane.attrib.get("allow", "").split())
    return sorted(set(modes))


def _shared_connection_signatures(root: ET.Element, shared_edge_ids: set[str]) -> set[str]:
    signatures = set()
    for connection in root.findall("connection"):
        source = connection.attrib.get("from", "")
        target = connection.attrib.get("to", "")
        if source in shared_edge_ids and target in shared_edge_ids:
            signatures.add(
                "|".join(
                    [
                        source,
                        target,
                        connection.attrib.get("fromLane", ""),
                        connection.attrib.get("toLane", ""),
                        connection.attrib.get("tl", ""),
                        connection.attrib.get("linkIndex", ""),
                    ]
                )
            )
    return signatures


def _audit_join_boundary_movements(
    source_root: ET.Element,
    variant_root: ET.Element,
    join_groups: Sequence[Sequence[str]],
) -> dict[str, Any]:
    groups = []
    for raw_group in join_groups:
        node_ids = {str(node_id) for node_id in raw_group if str(node_id)}
        if len(node_ids) < 2:
            continue
        source_edges = _plain_edges_by_id(source_root)
        internal = {
            edge_id
            for edge_id, edge in source_edges.items()
            if edge.attrib.get("from", "") in node_ids and edge.attrib.get("to", "") in node_ids
        }
        incoming = {
            edge_id
            for edge_id, edge in source_edges.items()
            if edge.attrib.get("to", "") in node_ids and edge.attrib.get("from", "") not in node_ids
        }
        outgoing = {
            edge_id
            for edge_id, edge in source_edges.items()
            if edge.attrib.get("from", "") in node_ids and edge.attrib.get("to", "") not in node_ids
        }
        source_movements = _reachable_boundary_movement_keys(source_root, incoming, internal, outgoing)
        variant_movements = {
            _movement_key(connection)
            for connection in variant_root.findall("connection")
            if connection.attrib.get("from", "") in incoming and connection.attrib.get("to", "") in outgoing
        }
        lost = sorted(source_movements - variant_movements)
        added = sorted(variant_movements - source_movements)
        groups.append(
            {
                "node_ids": sorted(node_ids),
                "joined_junction_id": _sumo_joined_cluster_id(sorted(node_ids)),
                "internal_edge_ids": sorted(internal),
                "incoming_edge_ids": sorted(incoming),
                "outgoing_edge_ids": sorted(outgoing),
                "source_boundary_movement_count": len(source_movements),
                "source_boundary_movements": sorted(source_movements),
                "variant_boundary_movement_count": len(variant_movements),
                "variant_boundary_movements": sorted(variant_movements),
                "lost_boundary_movement_count": len(lost),
                "lost_boundary_movements": lost,
                "added_boundary_movement_count": len(added),
                "added_boundary_movements": added,
            }
        )
    if not groups:
        return {
            "status": "not_applicable",
            "lost_boundary_movement_count": 0,
            "added_boundary_movement_count": 0,
            "groups": [],
        }
    lost_count = sum(group["lost_boundary_movement_count"] for group in groups)
    added_count = sum(group["added_boundary_movement_count"] for group in groups)
    return {
        "status": "pass" if not lost_count and not added_count else "review",
        "lost_boundary_movement_count": lost_count,
        "added_boundary_movement_count": added_count,
        "groups": groups,
    }


def _reachable_boundary_movement_keys(
    root: ET.Element,
    incoming_edge_ids: set[str],
    internal_edge_ids: set[str],
    outgoing_edge_ids: set[str],
) -> set[str]:
    adjacency: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for connection in root.findall("connection"):
        source = connection.attrib.get("from", "")
        target = connection.attrib.get("to", "")
        if source not in incoming_edge_ids | internal_edge_ids or target not in internal_edge_ids | outgoing_edge_ids:
            continue
        adjacency.setdefault((source, connection.attrib.get("fromLane", "")), set()).add(
            (target, connection.attrib.get("toLane", ""))
        )

    movements = set()
    for start in sorted(state for state in adjacency if state[0] in incoming_edge_ids):
        stack = [start]
        visited = set()
        while stack:
            state = stack.pop()
            if state in visited:
                continue
            visited.add(state)
            for target in adjacency.get(state, set()):
                if target[0] in outgoing_edge_ids:
                    movements.add("|".join((start[0], start[1], target[0], target[1])))
                elif target[0] in internal_edge_ids:
                    stack.append(target)
    return movements


def _movement_key(connection: ET.Element) -> str:
    return "|".join(
        (
            connection.attrib.get("from", ""),
            connection.attrib.get("fromLane", ""),
            connection.attrib.get("to", ""),
            connection.attrib.get("toLane", ""),
        )
    )


def _dangling_plain_edge_ids(root: ET.Element, edge_ids: set[str]) -> set[str]:
    incoming = Counter()
    outgoing = Counter()
    for connection in root.findall("connection"):
        source = connection.attrib.get("from", "")
        target = connection.attrib.get("to", "")
        if source in edge_ids:
            outgoing[source] += 1
        if target in edge_ids:
            incoming[target] += 1
    return {edge_id for edge_id in edge_ids if incoming[edge_id] == 0 or outgoing[edge_id] == 0}


def _internal_id_uses_join_node(identifier: str, node_ids: set[str]) -> bool:
    return any(identifier.startswith(f":{node_id}_") for node_id in node_ids)


def _candidate_from_overlapping_group(group: Mapping[str, Any]) -> dict[str, Any] | None:
    reference_nodes = _reference_core_nodes(group)
    if reference_nodes:
        target_evidence_confirmed = (
            str(group.get("transfer_gate_status", "blocked")) == "pass"
            and str(group.get("target_evidence_status", "blocked")) == "pass"
        )
        return {
            "source": "overlapping_junction_audit",
            "candidate_id": str(group.get("group_id", "")),
            "decision": "join" if target_evidence_confirmed else "needs_map_review",
            "confidence": (
                "target_evidence_confirmed" if target_evidence_confirmed else "teacher_prior"
            ),
            "node_ids": ";".join(reference_nodes),
            "reason": (
                "target-city evidence independently confirms the teacher-like conflict core"
                if target_evidence_confirmed
                else "reference join cluster is teacher evidence and cannot authorize this join"
            ),
            "google_maps_url": str(group.get("google_maps_url", "")),
        }

    if not _overlap_group_is_confirmed(group):
        return None
    return {
        "source": "overlapping_junction_audit",
        "candidate_id": str(group.get("group_id", "")),
        "decision": "join",
        "confidence": "map_confirmed",
        "node_ids": ";".join(str(item) for item in group.get("join_node_ids") or group.get("node_ids", []) or []),
        "reason": "human/map review confirms the overlapping top-level junction group",
        "google_maps_url": str(group.get("google_maps_url", "")),
    }


def _reference_core_nodes(group: Mapping[str, Any]) -> list[str]:
    for key in (
        "reference_join_node_ids",
        "reference_join_source_node_ids",
        "matched_candidate_node_ids",
        "matched_reference_source_node_ids",
    ):
        node_ids = group.get(key)
        if isinstance(node_ids, list) and len(node_ids) >= 2:
            return [str(item) for item in node_ids]

    if str(group.get("reference_join_status", "")) != "reference_join_supported":
        return []
    for reference_id in group.get("reference_join_ids", []) or []:
        raw = str(reference_id)
        if raw.startswith("cluster_"):
            node_ids = [item for item in raw.removeprefix("cluster_").split("_") if item]
            if len(node_ids) >= 2:
                return node_ids
    return []


def _overlap_group_is_confirmed(group: Mapping[str, Any]) -> bool:
    tokens = {
        str(group.get("aggregation_decision", "")).lower(),
        str(group.get("manual_correction_status", "")).lower(),
        str(group.get("map_review_status", "")).lower(),
        str(group.get("review_status", "")).lower(),
    }
    return any(token in {"join", "confirmed", "map_confirmed", "confirmed_join"} for token in tokens)


def _dedupe_join_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    joined_nodes: set[str] = set()
    seen_node_sets: set[frozenset[str]] = set()
    for candidate in candidates:
        node_set = frozenset(item for item in str(candidate.get("node_ids", "")).split(";") if item)
        if node_set and node_set in seen_node_sets:
            continue
        if candidate.get("decision") == "join" and node_set:
            if node_set & joined_nodes:
                continue
            joined_nodes.update(node_set)
        elif node_set & joined_nodes:
            continue
        if node_set:
            seen_node_sets.add(node_set)
        selected.append(candidate)
    return selected


def _write_candidates_csv(path: Path, candidates: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source",
                "candidate_id",
                "decision",
                "confidence",
                "node_ids",
                "reason",
                "google_maps_url",
            ],
        )
        writer.writeheader()
        writer.writerows(candidates)


def _result_to_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        return dict(result.to_dict())
    if hasattr(result, "model_dump"):
        return dict(result.model_dump(mode="json"))
    return dict(result)


def _failure(error: str) -> dict[str, Any]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "junction_aggregation_status": "failed",
        "error": error,
        "warnings": [error],
    }
