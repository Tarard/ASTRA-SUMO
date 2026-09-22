"""Resolve physical reconstruction scope and retained junction ownership."""

from __future__ import annotations

import copy
import json
import math
from collections import Counter
from typing import Any
import xml.etree.ElementTree as ET
from pathlib import Path
from .geometry import _edge_touches_context, _junction_within_radius
from .network import (
    TURNAROUND_DIR,
    _edge_drop_requires_review,
    _edge_family_id,
    _int_count,
    _opposite_direction_edge_id,
)


def _endpoint_rewrite_old_endpoint_ids(lane_patch_report: dict[str, object]) -> set[str]:
    old_ids: set[str] = set()
    for field in ("endpoint_rewritten_existing_mapped_edges", "endpoint_rewritten_missing_mapped_edges"):
        entries = lane_patch_report.get(field, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for attr in ("from", "to"):
                change = entry.get(attr)
                if not isinstance(change, dict):
                    continue
                old = str(change.get("old", ""))
                new = str(change.get("new", ""))
                if old and old != new and not old.startswith(":"):
                    old_ids.add(old)
    return old_ids


def _local_junction_context_summary(root: ET.Element, junction_id: str, *, radius_m: float) -> dict[str, object]:
    target = root.find(f"junction[@id='{junction_id}']")
    if target is None:
        return {"status": "fail", "reason": "missing_target_junction", "junction_id": junction_id}
    try:
        center = (float(target.attrib.get("x", "")), float(target.attrib.get("y", "")))
    except ValueError:
        return {"status": "fail", "reason": "invalid_target_coordinate", "junction_id": junction_id}

    junctions = {
        str(junction.attrib.get("id", "")): junction
        for junction in root.findall("junction")
        if junction.attrib.get("id")
    }
    local_junction_ids = {
        jid
        for jid, junction in junctions.items()
        if _junction_within_radius(junction, center=center, radius_m=radius_m)
    }
    local_edges: dict[str, ET.Element] = {}
    for edge in root.findall("edge"):
        edge_id = str(edge.attrib.get("id", ""))
        if not edge_id:
            continue
        if _edge_touches_context(edge, local_junction_ids, center=center, radius_m=radius_m):
            local_edges[edge_id] = edge
    local_edge_ids = set(local_edges)
    local_connections = [
        connection
        for connection in root.findall("connection")
        if connection.attrib.get("from", "") in local_edge_ids or connection.attrib.get("to", "") in local_edge_ids
    ]
    local_tls_ids = {
        str(jid)
        for jid in local_junction_ids
        if junctions.get(jid) is not None and junctions[jid].attrib.get("type") == "traffic_light"
    }
    local_tls_ids.update(
        str(connection.attrib.get("tl", ""))
        for connection in local_connections
        if connection.attrib.get("tl")
    )
    edge_function_counts = Counter(
        str(edge.attrib.get("function", "normal") or "normal") for edge in local_edges.values()
    )
    junction_type_counts = Counter(
        str(junctions[jid].attrib.get("type", "")) for jid in local_junction_ids if jid in junctions
    )
    crossing_elements = [
        crossing
        for crossing in root.findall("crossing")
        if str(crossing.attrib.get("node", "")) in local_junction_ids
        or any(edge_id in local_edge_ids for edge_id in str(crossing.attrib.get("edges", "")).split())
    ]
    local_tllogics = [
        tllogic
        for tllogic in root.findall("tlLogic")
        if str(tllogic.attrib.get("id", "")) in local_tls_ids
    ]
    return {
        "status": "pass",
        "junction_id": junction_id,
        "radius_m": radius_m,
        "junction_ids": sorted(local_junction_ids),
        "junction_count": len(local_junction_ids),
        "traffic_light_junction_count": sum(
            1 for jid in local_junction_ids if junctions[jid].attrib.get("type") == "traffic_light"
        ),
        "traffic_light_junction_ids": sorted(
            jid for jid in local_junction_ids if junctions[jid].attrib.get("type") == "traffic_light"
        ),
        "tl_logic_count": len(local_tllogics),
        "tl_logic_ids": sorted(str(tllogic.attrib.get("id", "")) for tllogic in local_tllogics),
        "edge_count": len(local_edges),
        "edge_function_counts": dict(sorted(edge_function_counts.items())),
        "normal_edge_count": edge_function_counts.get("normal", 0),
        "internal_edge_count": edge_function_counts.get("internal", 0),
        "walkingarea_edge_count": edge_function_counts.get("walkingarea", 0),
        "crossing_edge_count": edge_function_counts.get("crossing", 0),
        "crossing_element_count": len(crossing_elements),
        "connection_count": len(local_connections),
        "tls_controlled_connection_count": sum(1 for connection in local_connections if connection.attrib.get("tl")),
        "turnaround_connection_count": sum(
            1 for connection in local_connections if connection.attrib.get("dir") == TURNAROUND_DIR
        ),
        "junction_type_counts": dict(sorted(junction_type_counts.items())),
    }


def _load_teacher_join_groups_by_cluster(
    definition_value: object,
    *,
    queue_base_dir: Path | None = None,
) -> dict[str, list[str]]:
    """Load authoritative source members for teacher-side joined junctions.

    A SUMO joined junction id is only a generated label; its numeric suffixes
    are not guaranteed to be the complete source-node list.  The join
    definition emitted by the reference aggregation stage is therefore the
    source of truth whenever it is available.
    """
    if not definition_value:
        return {}
    definition_path = Path(str(definition_value))
    if not definition_path.is_absolute() and queue_base_dir is not None:
        definition_path = queue_base_dir / definition_path
    try:
        payload = json.loads(definition_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    groups: dict[str, list[str]] = {}
    for record in payload.get("records", []) or []:
        if not isinstance(record, dict):
            continue
        cluster_id = str(record.get("candidate_id", "")).strip()
        decision = str(record.get("decision", record.get("action", ""))).strip().lower()
        node_ids = sorted({str(node_id) for node_id in record.get("node_ids", []) or [] if str(node_id)})
        encoded_member_ids = set(_sumo_cluster_member_ids(cluster_id))
        # The aggregation report is a candidate repair suggestion, not an
        # authoritative decomposition of an existing teacher cluster.  Accept
        # it only when its source members agree with the generated cluster
        # label; otherwise the label-based fallback remains authoritative.
        if (
            cluster_id.startswith("cluster_")
            and decision == "join"
            and len(node_ids) >= 2
            and encoded_member_ids
            and encoded_member_ids <= set(node_ids)
        ):
            groups[cluster_id] = node_ids
    return groups


def _context_count_delta(
    teacher_context: dict[str, object],
    candidate_context: dict[str, object],
) -> dict[str, int]:
    fields = (
        "junction_count",
        "traffic_light_junction_count",
        "tl_logic_count",
        "edge_count",
        "normal_edge_count",
        "internal_edge_count",
        "walkingarea_edge_count",
        "crossing_edge_count",
        "crossing_element_count",
        "connection_count",
        "tls_controlled_connection_count",
        "turnaround_connection_count",
    )
    return {
        field: _int_count(candidate_context.get(field, 0)) - _int_count(teacher_context.get(field, 0))
        for field in fields
    }


def _join_patch_joined_node_ids(join_patch_file: Path) -> set[str]:
    try:
        join_root = ET.parse(join_patch_file).getroot()
    except (ET.ParseError, OSError):
        return set()
    joined_node_ids = set()
    for join in join_root.findall("join"):
        node_ids = [node_id for node_id in join.attrib.get("nodes", "").split() if node_id]
        if len(node_ids) >= 2:
            joined_node_ids.add(_sumo_joined_cluster_id(node_ids))
    return joined_node_ids


def _join_internal_self_loop_drop_has_witness(
    edge_id: str,
    dropped_edge_ids: list[str],
    surviving_edge_ids: set[str],
) -> bool:
    if _opposite_direction_edge_id(edge_id) in set(dropped_edge_ids):
        return True
    edge_family = _edge_family_id(edge_id)
    return any(_edge_family_id(surviving_edge_id) == edge_family for surviving_edge_id in surviving_edge_ids)


def _join_patch_endpoint_rewrites(join_patch_file: Path) -> dict[str, str]:
    try:
        joins = ET.parse(join_patch_file).getroot().findall("join")
    except (ET.ParseError, OSError):
        return {}
    rewrites: dict[str, str] = {}
    for join in joins:
        node_ids = [node_id for node_id in join.attrib.get("nodes", "").split() if node_id]
        joined_id = _sumo_joined_cluster_id(node_ids)
        if len(node_ids) < 2 or not joined_id:
            continue
        for node_id in node_ids:
            rewrites[node_id] = joined_id
    return rewrites


def _joined_source_node_ids(node_file: Path, junction_id: object) -> set[str]:
    target_junction_ids = _normalize_joined_junction_ids(junction_id)
    if not target_junction_ids:
        return set()
    source_node_ids: set[str] = set()
    for join in ET.parse(node_file).getroot().findall("join"):
        node_ids = [node_id for node_id in join.attrib.get("nodes", "").split() if node_id]
        if _sumo_joined_cluster_id(node_ids) in target_junction_ids:
            source_node_ids.update(node_ids)
    return source_node_ids


def _joined_endpoint_self_loop_edge_ids(
    edge_file: Path,
    join_patch_file: Path,
    joined_junction_id: object,
) -> tuple[list[str], list[str]]:
    if not join_patch_file.is_file() or not _normalize_joined_junction_ids(joined_junction_id):
        return [], []
    source_node_ids = _joined_source_node_ids(join_patch_file, joined_junction_id)
    if not source_node_ids:
        return [], []
    dropped_self_loop_edges = []
    blocking_self_loop_edge_drops = []
    edge_root = ET.parse(edge_file).getroot()
    for edge in edge_root.findall("edge"):
        from_is_join_source = edge.attrib.get("from", "") in source_node_ids
        to_is_join_source = edge.attrib.get("to", "") in source_node_ids
        if from_is_join_source and to_is_join_source:
            edge_id = edge.attrib.get("id", "")
            dropped_self_loop_edges.append(edge_id)
            if _edge_drop_requires_review(edge):
                blocking_self_loop_edge_drops.append(edge_id)
    return dropped_self_loop_edges, blocking_self_loop_edge_drops


def _split_cluster_member_residuals(
    teacher_context: dict[str, object],
    candidate_context: dict[str, object],
    *,
    teacher_join_groups_by_cluster: dict[str, list[str]] | None = None,
) -> list[dict[str, object]]:
    candidate_junction_ids = {str(item) for item in candidate_context.get("junction_ids", []) or [] if str(item)}
    residuals: list[dict[str, object]] = []
    for teacher_junction_id in teacher_context.get("junction_ids", []) or []:
        teacher_junction_id = str(teacher_junction_id)
        if not teacher_junction_id.startswith("cluster_"):
            continue
        member_ids = list(
            (teacher_join_groups_by_cluster or {}).get(teacher_junction_id, [])
        )
        if not member_ids:
            member_ids = [item for item in teacher_junction_id.removeprefix("cluster_").split("_") if item]
        residual_members = sorted(member_id for member_id in member_ids if member_id in candidate_junction_ids)
        if residual_members:
            residuals.append(
                {
                    "teacher_cluster_junction_id": teacher_junction_id,
                    "candidate_member_junction_ids": residual_members,
                }
            )
    return residuals


def _blocking_sequential_overlap_edge_ids(
    edge_ids: set[str],
    edge_file: Path,
    candidate_node_ids: set[str],
    applied_node_ids: set[str],
) -> list[str]:
    if not edge_ids:
        return []
    try:
        edges = {
            edge.attrib["id"]: edge
            for edge in ET.parse(edge_file).getroot().findall("edge")
            if edge.attrib.get("id")
        }
    except (ET.ParseError, OSError):
        return sorted(edge_ids)
    blocking = []
    for edge_id in sorted(edge_ids):
        edge = edges.get(edge_id)
        if edge is None:
            blocking.append(edge_id)
            continue
        endpoints = {edge.attrib.get("from", ""), edge.attrib.get("to", "")}
        if endpoints & candidate_node_ids and endpoints & applied_node_ids:
            continue
        blocking.append(edge_id)
    return blocking


def _normalize_joined_junction_ids(value: object) -> set[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(item) for item in value if str(item)}
    return {str(value)} if str(value) else set()


def _endpoint_rewrites(approach_endpoint_rebuild_plan: object | None) -> dict[str, tuple[str, str]]:
    if not isinstance(approach_endpoint_rebuild_plan, dict):
        return {}
    rewrites = {}
    for item in approach_endpoint_rebuild_plan.get("edge_rebuilds", []) or []:
        if not isinstance(item, dict):
            continue
        edge_id = str(item.get("edge_id", ""))
        desired_from = str(item.get("desired_from", ""))
        desired_to = str(item.get("desired_to", ""))
        if edge_id and desired_from and desired_to:
            rewrites[edge_id] = (desired_from, desired_to)
    return rewrites


def _expand_fragmented_tls_join_scope_candidate(
    candidate: dict[str, Any],
    raw_node_file: Path,
    *,
    max_controller_span_m: float = 120.0,
    max_controller_node_count: int = 20,
) -> dict[str, Any]:
    """Expand a partial OSM signal cluster to its compact shared controller cell."""

    if candidate.get("learned_rule") != "tum_like_topology_fragmented_tls_candidate":
        return candidate
    scope_value = candidate.get("expanded_rebuild_scope", {})
    if not isinstance(scope_value, dict):
        return candidate
    requested_ids = {
        str(value)
        for value in scope_value.get("join_junction_ids", []) or []
        if str(value)
    }
    report: dict[str, object] = {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "requested_join_junction_ids": sorted(requested_ids),
        "automatic_expansion_applied": False,
        "max_controller_span_m": max_controller_span_m,
    }
    if not requested_ids:
        report["reason"] = "no_requested_join_junction_ids"
        return {**candidate, "tls_join_scope_expansion": report}
    try:
        root = ET.parse(raw_node_file).getroot()
    except (ET.ParseError, OSError) as exc:
        report.update(
            {
                "status": "review",
                "reason": "raw_node_file_unreadable",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return {**candidate, "tls_join_scope_expansion": report}

    nodes = {
        str(node.attrib.get("id", "")): node
        for node in root.findall("node")
        if node.attrib.get("id")
    }
    missing_ids = sorted(requested_ids - set(nodes))
    controller_ids = {
        str(nodes[node_id].attrib.get("tl", ""))
        for node_id in requested_ids & set(nodes)
        if nodes[node_id].attrib.get("tl")
    }
    report["missing_requested_join_junction_ids"] = missing_ids
    report["controller_ids"] = sorted(controller_ids)
    if missing_ids or len(controller_ids) != 1:
        report.update(
            {
                "status": "review",
                "reason": "requested join nodes do not resolve to one shared TLS controller",
            }
        )
        return {**candidate, "tls_join_scope_expansion": report}

    controller_id = next(iter(controller_ids))
    controller_node_ids = sorted(
        node_id
        for node_id, node in nodes.items()
        if node.attrib.get("tl", "") == controller_id
    )
    positions = []
    for node_id in controller_node_ids:
        node = nodes[node_id]
        try:
            positions.append((float(node.attrib["x"]), float(node.attrib["y"])))
        except (KeyError, TypeError, ValueError):
            report.update(
                {
                    "status": "review",
                    "reason": "shared TLS controller node lacks finite coordinates",
                    "controller_node_ids": controller_node_ids,
                }
            )
            return {**candidate, "tls_join_scope_expansion": report}
    span_m = max(
        (
            math.hypot(ax - bx, ay - by)
            for index, (ax, ay) in enumerate(positions)
            for bx, by in positions[index + 1 :]
        ),
        default=0.0,
    )
    report.update(
        {
            "controller_node_ids": controller_node_ids,
            "controller_node_count": len(controller_node_ids),
            "controller_span_m": round(span_m, 3),
        }
    )
    if (
        len(controller_node_ids) > max_controller_node_count
        or span_m > max_controller_span_m
    ):
        report.update(
            {
                "status": "review",
                "reason": "shared TLS controller is too broad for automatic physical junction joining",
            }
        )
        return {**candidate, "tls_join_scope_expansion": report}

    expanded_scope = copy.deepcopy(scope_value)
    expanded_scope["join_junction_ids"] = controller_node_ids
    expanded_scope["junction_ids"] = sorted(
        {
            *controller_node_ids,
            *(
                str(value)
                for value in expanded_scope.get("junction_ids", []) or []
                if str(value)
            ),
        }
    )
    added_ids = sorted(set(controller_node_ids) - requested_ids)
    report.update(
        {
            "automatic_expansion_applied": bool(added_ids),
            "added_join_junction_ids": added_ids,
            "expanded_join_junction_ids": controller_node_ids,
            "reason": "compact shared TLS controller cell expanded before physical junction join",
        }
    )
    return {
        **candidate,
        "expanded_rebuild_scope": expanded_scope,
        "tls_join_scope_expansion": report,
    }


def _candidate_connection_mode_scope_ids(
    report: dict[str, object],
) -> tuple[list[str], list[str]]:
    """Return equivalent source/candidate scopes for a differential audit.

    A plain-XML junction join removes several source junction IDs and creates a
    new cluster ID.  Unchanged boundary junctions can also be part of the
    intended edit scope.  Excluding the complete scope on both sides prevents
    those expected identity changes from being mistaken for global collateral
    damage while every other junction remains subject to the regression gate.
    """

    source_ids: set[str] = set()
    matched_ids = report.get("matched_candidate_node_ids", [])
    if isinstance(matched_ids, (list, tuple, set)):
        source_ids.update(str(value) for value in matched_ids if str(value))
    expanded_scope = report.get("expanded_rebuild_scope", {})
    if isinstance(expanded_scope, dict):
        for field in ("junction_ids", "join_junction_ids"):
            values = expanded_scope.get(field, [])
            if isinstance(values, (list, tuple, set)):
                source_ids.update(str(value) for value in values if str(value))
        if not source_ids:
            core_junction_id = str(expanded_scope.get("core_junction_id", ""))
            if core_junction_id:
                source_ids.add(core_junction_id)
    if not source_ids:
        original_junction_id = str(report.get("candidate_original_junction_id", ""))
        if original_junction_id:
            source_ids.add(original_junction_id)

    candidate_ids = set(source_ids)
    candidate_junction_id = str(report.get("junction_id", ""))
    if candidate_junction_id:
        candidate_ids.add(candidate_junction_id)
    return sorted(source_ids), sorted(candidate_ids)


def _conservative_join_node_ids(candidate_node_ids: list[str], matched_source_node_ids: set[str]) -> list[str]:
    matched = [node_id for node_id in candidate_node_ids if node_id in matched_source_node_ids]
    if len(matched) >= 2:
        return matched
    if len(matched) == 1:
        first_other = next((node_id for node_id in candidate_node_ids if node_id != matched[0]), "")
        return [first_other, matched[0]] if first_other else matched
    return candidate_node_ids[:2]


def _sumo_joined_cluster_id(node_ids: list[str]) -> str:
    ids = sorted(dict.fromkeys(node_id for node_id in node_ids if node_id))
    if not ids:
        return ""
    head = "_".join(ids[:4])
    suffix = "" if len(ids) <= 4 else f"_#{len(ids) - 4}more"
    return f"cluster_{head}{suffix}"


def _sumo_cluster_member_ids(node_id: str) -> list[str]:
    """Expand the lossless part of a SUMO joined-cluster id.

    ``netconvert`` names an explicit join from its source node ids as
    ``cluster_<id>_<id>...``.  Expanded teacher scopes carry those generated
    ids in ``junction_ids`` even though the plain OSM source still contains
    the member nodes.  The ``_#Nmore`` suffix is deliberately ignored: it is
    a display-only truncation and must never be treated as a real node id.
    """

    value = str(node_id or "")
    if not value.startswith("cluster_"):
        return [value] if value else []
    members = [
        token
        for token in value.removeprefix("cluster_").split("_")
        if token and not token.startswith("#")
    ]
    return members


def _expanded_rebuild_scope(
    core_junction_id: str,
    approach_endpoint_rebuild_plan: dict[str, Any],
    *,
    blocked_teacher_edge_ids: list[str],
    fallback_junction_ids: list[str] | None = None,
) -> dict[str, Any]:
    if approach_endpoint_rebuild_plan.get("status") == "review":
        neighbor_ids = [
            str(item) for item in approach_endpoint_rebuild_plan.get("affected_neighbor_junction_ids", []) or []
        ]
        missing_ids = [str(item) for item in approach_endpoint_rebuild_plan.get("missing_desired_endpoint_ids", []) or []]
        reason = (
            "approach endpoints differ and at least one missing teacher edge cannot be copied safely"
            if blocked_teacher_edge_ids
            else "approach endpoints differ; rebuild expanded scope before teacher movement replay"
        )
        return {
            "status": "review",
            "recommended_action": "rebuild_plain_xml_scope",
            "core_junction_id": core_junction_id,
            "junction_ids": sorted({core_junction_id, *neighbor_ids, *missing_ids}),
            "join_junction_ids": [core_junction_id] if core_junction_id else [],
            "blocked_teacher_edge_ids": blocked_teacher_edge_ids,
            "missing_desired_endpoint_ids": missing_ids,
            "reason": reason,
        }
    if not blocked_teacher_edge_ids:
        return {"status": "pass", "recommended_action": "none", "junction_ids": []}
    fallback_ids = sorted({str(item) for item in fallback_junction_ids or [] if str(item)})
    if len(fallback_ids) < 2:
        return {"status": "pass", "recommended_action": "none", "junction_ids": []}
    return {
        "status": "review",
        "recommended_action": "rebuild_plain_xml_scope",
        "core_junction_id": core_junction_id,
        "junction_ids": fallback_ids,
        "join_junction_ids": fallback_ids,
        "blocked_teacher_edge_ids": blocked_teacher_edge_ids,
        "missing_desired_endpoint_ids": [],
        "reason": "missing teacher approach edge cannot be copied safely; rebuild from matched candidate source nodes",
    }
