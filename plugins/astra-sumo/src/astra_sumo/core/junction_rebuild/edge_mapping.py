"""Match teacher and candidate road identities and endpoint plans."""

from __future__ import annotations

from collections import Counter
from typing import Any
import xml.etree.ElementTree as ET
from pathlib import Path
from ..junction_teacher_model import match_teacher_approaches
from .geometry import _edge_lane_shapes, _load_geometry_anchor_edges, _translated_edge_lane_shapes
from .network import (
    _approaches,
    _edge_is_vehicle_continuation_candidate,
    _edge_lane_count,
    _edge_type_signature,
    _mapped_internal_ref,
    _signed_edge_family_id,
    _split,
    _touches_target_internal_subgraph,
)
from .signatures import (
    _approach_endpoint_signatures,
    _mapped_endpoint,
    _mapped_junction_ref,
    _vehicle_connection_signature,
)


def _approach_endpoint_rebuild_plan(
    teacher_model: dict[str, Any],
    candidate_model: dict[str, Any],
    *,
    edge_map: dict[str, str] | None = None,
    teacher_junction_id: str = "",
    candidate_junction_id: str = "",
    candidate_junction_ids: set[str] | None = None,
) -> dict[str, Any]:
    candidate_junction_ids = candidate_junction_ids or set()
    edge_rebuilds = []
    for direction in ("incoming", "outgoing"):
        candidate_by_edge = {str(edge.get("edge_id", "")): edge for edge in _approaches(candidate_model, direction)}
        for teacher_edge in _approaches(teacher_model, direction):
            mapped_edge_id = _mapped_endpoint(str(teacher_edge.get("edge_id", "")), edge_map)
            candidate_edge = candidate_by_edge.get(mapped_edge_id)
            if not mapped_edge_id or candidate_edge is None:
                continue
            candidate_from = str(candidate_edge.get("from", ""))
            candidate_to = str(candidate_edge.get("to", ""))
            desired_from = _mapped_junction_ref(
                str(teacher_edge.get("from", "")), teacher_junction_id, candidate_junction_id
            )
            desired_to = _mapped_junction_ref(str(teacher_edge.get("to", "")), teacher_junction_id, candidate_junction_id)
            if (candidate_from, candidate_to) == (desired_from, desired_to):
                continue
            desired_external = {
                endpoint for endpoint in (desired_from, desired_to) if endpoint and endpoint != candidate_junction_id
            }
            candidate_external = {
                endpoint for endpoint in (candidate_from, candidate_to) if endpoint and endpoint != candidate_junction_id
            }
            missing_desired = sorted(endpoint for endpoint in desired_external if endpoint not in candidate_junction_ids)
            edge_rebuilds.append(
                {
                    "approach_key": f"{direction}:{mapped_edge_id}",
                    "edge_id": mapped_edge_id,
                    "direction": direction,
                    "candidate_from": candidate_from,
                    "candidate_to": candidate_to,
                    "desired_from": desired_from,
                    "desired_to": desired_to,
                    "affected_neighbor_junction_ids": sorted(candidate_external | desired_external),
                    "missing_desired_endpoint_ids": missing_desired,
                    "unsafe_direct_rewrite": True,
                    "reason": "endpoint change affects neighboring junction connections and tlLogic; rebuild expanded scope",
                }
            )

    affected = sorted({junction for item in edge_rebuilds for junction in item["affected_neighbor_junction_ids"]})
    missing = sorted({junction for item in edge_rebuilds for junction in item["missing_desired_endpoint_ids"]})
    return {
        "status": "review" if edge_rebuilds else "pass",
        "claim_status": "diagnostic-demo",
        "recommended_action": "expand_rebuild_scope" if edge_rebuilds else "none",
        "mismatch_count": len(edge_rebuilds),
        "affected_neighbor_junction_ids": affected,
        "missing_desired_endpoint_ids": missing,
        "edge_rebuilds": edge_rebuilds,
    }


def _candidate_nodes_from_exact_teacher_approach_edges(
    teacher_model: dict[str, object],
    candidate_edges_by_id: dict[str, ET.Element],
    candidate_junction_ids: set[str],
) -> tuple[list[str], dict[str, str]]:
    node_ids = []
    edge_map = {}
    for direction, endpoint_attr in (("incoming", "to"), ("outgoing", "from")):
        for approach in _approaches(teacher_model, direction):
            edge_id = str(approach.get("edge_id", ""))
            candidate_edge_id, candidate_edge = _candidate_edge_by_exact_or_unsplit_id(edge_id, candidate_edges_by_id)
            if candidate_edge is None:
                continue
            edge_map[edge_id] = candidate_edge_id
            node_id = candidate_edge.attrib.get(endpoint_attr, "")
            if node_id in candidate_junction_ids:
                node_ids.append(node_id)
    return sorted(dict.fromkeys(node_ids)), dict(sorted(edge_map.items()))


def _stale_case_edge_map_entries(
    case_edge_map: dict[str, str],
    teacher_model: dict[str, object],
    candidate_model: dict[str, object],
    approach_edge_map: dict[str, str],
) -> dict[str, str]:
    stale: dict[str, str] = {}
    for direction in ("incoming", "outgoing"):
        teacher_edge_ids = {str(edge.get("edge_id", "")) for edge in _approaches(teacher_model, direction)}
        candidate_edge_ids = {str(edge.get("edge_id", "")) for edge in _approaches(candidate_model, direction)}
        for teacher_edge_id, candidate_edge_id in case_edge_map.items():
            if teacher_edge_id in approach_edge_map:
                continue
            if teacher_edge_id in teacher_edge_ids and candidate_edge_id not in candidate_edge_ids:
                stale[teacher_edge_id] = candidate_edge_id
    return dict(sorted(stale.items()))


def _edge_map_from_approach_endpoint_rebuild_plan(
    teacher_model: dict[str, object],
    approach_endpoint_rebuild_plan: object,
    *,
    teacher_junction_id: str = "",
    candidate_junction_id: str = "",
    plan_junction_id: str = "",
) -> dict[str, str]:
    if not isinstance(approach_endpoint_rebuild_plan, dict):
        return {}
    candidates_by_endpoint: dict[tuple[str, str, str], list[str]] = {}
    for item in approach_endpoint_rebuild_plan.get("edge_rebuilds", []) or []:
        if not isinstance(item, dict):
            continue
        edge_id = str(item.get("edge_id", "")).strip()
        direction = str(item.get("direction", "")).strip()
        if not direction and ":" in str(item.get("approach_key", "")):
            direction = str(item.get("approach_key", "")).split(":", 1)[0]
        desired_from = str(item.get("desired_from", "")).strip()
        desired_to = str(item.get("desired_to", "")).strip()
        if direction not in {"incoming", "outgoing"} or not edge_id or not desired_from or not desired_to:
            continue
        candidates_by_endpoint.setdefault((direction, desired_from, desired_to), []).append(edge_id)

    edge_map: dict[str, str] = {}
    target_junction_ids = list(dict.fromkeys(item for item in (candidate_junction_id, plan_junction_id) if item))
    for direction in ("incoming", "outgoing"):
        for teacher_edge in _approaches(teacher_model, direction):
            teacher_edge_id = str(teacher_edge.get("edge_id", "")).strip()
            if not teacher_edge_id:
                continue
            matches = []
            for target_junction_id in target_junction_ids or [""]:
                desired_from = _mapped_junction_ref(
                    str(teacher_edge.get("from", "")), teacher_junction_id, target_junction_id
                )
                desired_to = _mapped_junction_ref(str(teacher_edge.get("to", "")), teacher_junction_id, target_junction_id)
                matches.extend(candidates_by_endpoint.get((direction, desired_from, desired_to), []))
            unique_matches = sorted(set(matches))
            if len(unique_matches) == 1:
                edge_map[teacher_edge_id] = unique_matches[0]
    return dict(sorted(edge_map.items()))


def _drop_endpoint_mismatched_edge_map_entries(
    teacher_model: dict[str, object],
    candidate_model: dict[str, object],
    edge_map: dict[str, str],
    *,
    teacher_junction_id: str,
    candidate_junction_id: str,
) -> dict[str, str]:
    teacher_signatures = _approach_endpoint_signatures(
        teacher_model,
        edge_map=edge_map,
        source_junction_id=teacher_junction_id,
        target_junction_id=candidate_junction_id,
    )
    candidate_signatures = _approach_endpoint_signatures(candidate_model)
    keep: dict[str, str] = {}
    for teacher_edge_id, candidate_edge_id in edge_map.items():
        keys = [key for key in teacher_signatures if key.endswith(f":{candidate_edge_id}")]
        if keys and any(teacher_signatures[key] != candidate_signatures.get(key) for key in keys):
            continue
        keep[teacher_edge_id] = candidate_edge_id
    return keep


def _missing_teacher_movement_plan(
    teacher_model: dict[str, object],
    candidate_model: dict[str, object],
    *,
    edge_map: dict[str, str],
    teacher_junction_id: str,
    candidate_junction_id: str,
) -> list[dict[str, object]]:
    candidate_signatures = Counter(
        _vehicle_connection_signature(connection, edge_map=None, source_junction_id="", target_junction_id="")
        for connection in candidate_model.get("vehicle_connections", []) or []
        if isinstance(connection, dict)
    )
    missing = []
    for connection in teacher_model.get("vehicle_connections", []) or []:
        if not isinstance(connection, dict):
            continue
        teacher_from = str(connection.get("from", ""))
        teacher_to = str(connection.get("to", ""))
        source = edge_map.get(teacher_from)
        target = edge_map.get(teacher_to)
        if not source or not target:
            continue
        signature = _vehicle_connection_signature(
            connection,
            edge_map=edge_map,
            source_junction_id=teacher_junction_id,
            target_junction_id=candidate_junction_id,
        )
        if candidate_signatures[signature] > 0:
            candidate_signatures[signature] -= 1
            continue
        via = _mapped_internal_ref(str(connection.get("via", "")), teacher_junction_id, candidate_junction_id)
        missing.append(
            {
                "teacher_from_edge_id": teacher_from,
                "teacher_to_edge_id": teacher_to,
                "from_edge_id": source,
                "to_edge_id": target,
                "fromLane": str(connection.get("fromLane", "")),
                "toLane": str(connection.get("toLane", "")),
                "dir": str(connection.get("dir", "")),
                "state": str(connection.get("state", "")),
                "tl": _mapped_junction_ref(str(connection.get("tl", "")), teacher_junction_id, candidate_junction_id),
                "linkIndex": str(connection.get("linkIndex", "")),
                "via": via,
                "controlled": bool(connection.get("tl") and connection.get("linkIndex")),
                "has_internal_via": bool(via),
                "match_status": "missing_candidate_connection",
            }
        )
    return missing


def _teacher_boundary_edge_ids_touching_internal_subgraph(
    connections: list[ET.Element],
    teacher_edges: dict[str, ET.Element],
    teacher_junction_id: str,
) -> list[str]:
    teacher_internal_prefix = f":{teacher_junction_id}_"
    edge_ids = []
    for connection in connections:
        if not _touches_target_internal_subgraph(connection, teacher_internal_prefix, teacher_junction_id):
            continue
        for attr in ("from", "to"):
            edge_id = connection.attrib.get(attr, "")
            teacher_edge = teacher_edges.get(edge_id)
            if (
                edge_id
                and not edge_id.startswith(teacher_internal_prefix)
                and teacher_edge is not None
                and teacher_junction_id in (teacher_edge.attrib.get("from"), teacher_edge.attrib.get("to"))
            ):
                edge_ids.append(edge_id)
    # A crossing can reference a roadway edge that has no vehicle connection
    # directly touching the target internal graph.  It is still part of the
    # cell boundary and must survive a scoped replay; otherwise the pedestrian
    # overlay would retain a dangling ``crossingEdges`` reference.
    for crossing in teacher_edges.values():
        if crossing.attrib.get("function") != "crossing":
            continue
        if not crossing.attrib.get("id", "").startswith(teacher_internal_prefix):
            continue
        for edge_id in _split(crossing.attrib.get("crossingEdges", "")):
            teacher_edge = teacher_edges.get(edge_id)
            if (
                edge_id
                and teacher_edge is not None
                and teacher_junction_id in (teacher_edge.attrib.get("from"), teacher_edge.attrib.get("to"))
            ):
                edge_ids.append(edge_id)
    return list(dict.fromkeys(edge_ids))


def _teacher_candidate_edge_map(
    teacher_model: dict[str, object],
    candidate_model: dict[str, object],
    *,
    teacher_junction_id: str = "",
    candidate_junction_id: str = "",
    drop_endpoint_mismatches: bool = True,
    max_bearing_delta: float = 30.0,
) -> dict[str, str]:
    edge_map: dict[str, str] = {}
    for direction in ("incoming", "outgoing"):
        edge_map.update(
            match_teacher_approaches(
                _approaches(teacher_model, direction),
                _approaches(candidate_model, direction),
                max_bearing_delta=max_bearing_delta,
            )
        )
    if drop_endpoint_mismatches and teacher_junction_id and candidate_junction_id:
        edge_map = _drop_endpoint_mismatched_edge_map_entries(
            teacher_model,
            candidate_model,
            edge_map,
            teacher_junction_id=teacher_junction_id,
            candidate_junction_id=candidate_junction_id,
        )
    return dict(sorted((source, target) for source, target in edge_map.items() if source and target))


def _needed_unmapped_teacher_boundary_edges(
    connections: list[ET.Element],
    teacher_edges: dict[str, ET.Element],
    edge_map: dict[str, str],
    candidate_edges_by_id: dict[str, ET.Element],
    teacher_internal_prefix: str,
    teacher_junction_id: str,
    candidate_junction_id: str,
    dx: float,
    dy: float,
    *,
    compare_lane_shapes: bool = True,
    replay_existing_edges: bool = True,
) -> list[str]:
    needed = []
    seen = set()
    for connection in connections:
        if not _touches_target_internal_subgraph(connection, teacher_internal_prefix, teacher_junction_id):
            continue
        for attr in ("from", "to"):
            edge_id = connection.attrib.get(attr, "")
            if (
                not edge_id
                or edge_id.startswith(teacher_internal_prefix)
                or edge_id in seen
            ):
                continue
            teacher_edge = teacher_edges.get(edge_id)
            if teacher_edge is None:
                continue
            if teacher_junction_id not in (teacher_edge.attrib.get("from"), teacher_edge.attrib.get("to")):
                continue
            mapped_from = candidate_junction_id if teacher_edge.attrib.get("from") == teacher_junction_id else teacher_edge.attrib.get("from", "")
            mapped_to = candidate_junction_id if teacher_edge.attrib.get("to") == teacher_junction_id else teacher_edge.attrib.get("to", "")
            if not mapped_from or not mapped_to:
                continue
            candidate_edge = candidate_edges_by_id.get(edge_map.get(edge_id, edge_id))
            if candidate_edge is not None and not replay_existing_edges:
                continue
            if (
                candidate_edge is not None
                and candidate_edge.attrib.get("from") == mapped_from
                and candidate_edge.attrib.get("to") == mapped_to
                and (
                    not compare_lane_shapes
                    or _edge_lane_shapes(candidate_edge) == _translated_edge_lane_shapes(teacher_edge, dx, dy)
                )
            ):
                continue
            seen.add(edge_id)
            needed.append(edge_id)
    return needed


def _teacher_boundary_edge_needs_replay(
    teacher_edge: ET.Element,
    edge_map: dict[str, str],
    candidate_edges_by_id: dict[str, ET.Element],
    teacher_junction_id: str,
    candidate_junction_id: str,
    dx: float,
    dy: float,
) -> bool:
    edge_id = teacher_edge.attrib.get("id", "")
    mapped_from = candidate_junction_id if teacher_edge.attrib.get("from") == teacher_junction_id else teacher_edge.attrib.get("from", "")
    mapped_to = candidate_junction_id if teacher_edge.attrib.get("to") == teacher_junction_id else teacher_edge.attrib.get("to", "")
    candidate_edge = candidate_edges_by_id.get(edge_map.get(edge_id, edge_id))
    return not (
        candidate_edge is not None
        and candidate_edge.attrib.get("from") == mapped_from
        and candidate_edge.attrib.get("to") == mapped_to
        and candidate_edge.attrib.get("type", "") == teacher_edge.attrib.get("type", "")
        and _edge_lane_shapes(candidate_edge) == _translated_edge_lane_shapes(teacher_edge, dx, dy)
    )


def _copyable_missing_teacher_edge_ids(
    teacher_connections: list[ET.Element],
    teacher_edges: dict[str, ET.Element],
    candidate_edges_by_id: dict[str, ET.Element],
    *,
    teacher_junction_id: str,
    candidate_junction_id: str,
    edge_map: dict[str, str],
) -> list[str]:
    return _needed_unmapped_teacher_boundary_edges(
        teacher_connections,
        teacher_edges,
        edge_map,
        candidate_edges_by_id,
        f":{teacher_junction_id}_",
        teacher_junction_id,
        candidate_junction_id,
        0.0,
        0.0,
        compare_lane_shapes=False,
        replay_existing_edges=False,
    )


def _augment_candidate_edge_map_from_tls_approach_pairs(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    pair_map: dict[str, str] = {}
    for pair in candidate.get("tls_approach_pairs", []) or []:
        if not isinstance(pair, dict):
            continue
        teacher_edge_id = str(pair.get("reference_edge_id", ""))
        candidate_edge_id = str(pair.get("candidate_edge_id", ""))
        if teacher_edge_id and candidate_edge_id:
            pair_map[teacher_edge_id] = candidate_edge_id
    existing = _valid_edge_map(candidate.get("edge_map", {}))
    merged = {**existing, **pair_map}
    return {
        **candidate,
        "edge_map": dict(sorted(merged.items())),
        "tls_approach_edge_map_evidence": {
            "status": "pass" if pair_map else "review",
            "pair_edge_map": dict(sorted(pair_map.items())),
            "added_or_overridden_count": sum(
                1 for key, value in pair_map.items() if existing.get(key) != value
            ),
            "reason": "bearing-matched TLS approach evidence applied to full controller-cell variant",
        },
    }


def _case_boundary_edge_map(
    case: dict[str, Any],
    candidate_edges_by_id: dict[str, ET.Element],
) -> dict[str, str]:
    teacher_edge_ids = [
        str(edge_id)
        for edge_id in [
            *(case.get("reference_approach_edge_ids") or []),
            *(case.get("matched_reference_source_boundary_edge_ids") or []),
        ]
        if str(edge_id)
    ]
    candidate_edge_ids = [
        str(edge_id)
        for edge_id in case.get("matched_candidate_boundary_edge_ids") or []
        if str(edge_id) in candidate_edges_by_id
    ]
    candidates_by_family: dict[str, list[str]] = {}
    for edge_id in candidate_edge_ids:
        candidates_by_family.setdefault(_signed_edge_family_id(edge_id), []).append(edge_id)

    edge_map = {}
    for teacher_edge_id in teacher_edge_ids:
        if teacher_edge_id in candidate_edge_ids:
            edge_map[teacher_edge_id] = teacher_edge_id
            continue
        matches = sorted(set(candidates_by_family.get(_signed_edge_family_id(teacher_edge_id), [])))
        if len(matches) == 1:
            edge_map[teacher_edge_id] = matches[0]
    return dict(sorted(edge_map.items()))


def _load_geometry_anchor_edge_ids(edge_file: Path | None) -> set[str]:
    return set(_load_geometry_anchor_edges(edge_file))


def _same_family_continuation_edge_map(
    teacher_edges: dict[str, ET.Element],
    candidate_edges_by_id: dict[str, ET.Element],
    edge_map: dict[str, str],
    *,
    teacher_junction_id: str,
    candidate_junction_id: str,
) -> dict[str, str]:
    frontier_endpoints_by_family: dict[str, set[str]] = {}
    used_candidate_edge_ids = {candidate_id for candidate_id in edge_map.values() if candidate_id}
    for teacher_edge_id, candidate_edge_id in edge_map.items():
        teacher_edge = teacher_edges.get(teacher_edge_id)
        candidate_edge = candidate_edges_by_id.get(candidate_edge_id)
        if (
            teacher_edge is None
            or candidate_edge is None
            or teacher_edge_id.startswith(":")
            or _signed_edge_family_id(teacher_edge_id) != _signed_edge_family_id(candidate_edge_id)
            or not _edge_is_vehicle_continuation_candidate(teacher_edge)
            or not _edge_is_vehicle_continuation_candidate(candidate_edge)
        ):
            continue
        family_id = _signed_edge_family_id(teacher_edge_id)
        frontier_endpoints_by_family.setdefault(family_id, set()).update(
            {
                _mapped_junction_ref(teacher_edge.attrib.get("from", ""), teacher_junction_id, candidate_junction_id),
                _mapped_junction_ref(teacher_edge.attrib.get("to", ""), teacher_junction_id, candidate_junction_id),
            }
        )
    if not frontier_endpoints_by_family:
        return {}

    candidate_edges_by_signature: dict[tuple[str, str, str], list[str]] = {}
    for candidate_edge_id, candidate_edge in candidate_edges_by_id.items():
        if candidate_edge_id.startswith(":") or not _edge_is_vehicle_continuation_candidate(candidate_edge):
            continue
        candidate_edges_by_signature.setdefault(
            (
                _signed_edge_family_id(candidate_edge_id),
                candidate_edge.attrib.get("from", ""),
                candidate_edge.attrib.get("to", ""),
            ),
            [],
        ).append(candidate_edge_id)

    additions: dict[str, str] = {}
    for teacher_edge_id, teacher_edge in sorted(teacher_edges.items()):
        if (
            teacher_edge_id in edge_map
            or teacher_edge_id.startswith(":")
            or not _edge_is_vehicle_continuation_candidate(teacher_edge)
        ):
            continue
        family_id = _signed_edge_family_id(teacher_edge_id)
        frontier_endpoints = frontier_endpoints_by_family.get(family_id, set())
        if not frontier_endpoints:
            continue
        desired_from = _mapped_junction_ref(teacher_edge.attrib.get("from", ""), teacher_junction_id, candidate_junction_id)
        desired_to = _mapped_junction_ref(teacher_edge.attrib.get("to", ""), teacher_junction_id, candidate_junction_id)
        if not desired_from or not desired_to or not ({desired_from, desired_to} & frontier_endpoints):
            continue
        candidate_ids = [
            candidate_edge_id
            for candidate_edge_id in candidate_edges_by_signature.get((family_id, desired_from, desired_to), [])
            if candidate_edge_id not in used_candidate_edge_ids
            and _edge_type_signature(candidate_edges_by_id[candidate_edge_id]) == _edge_type_signature(teacher_edge)
            and _edge_lane_count(candidate_edges_by_id[candidate_edge_id]) == _edge_lane_count(teacher_edge)
        ]
        unique_candidate_ids = sorted(set(candidate_ids))
        if len(unique_candidate_ids) != 1:
            continue
        candidate_edge_id = unique_candidate_ids[0]
        additions[teacher_edge_id] = candidate_edge_id
        used_candidate_edge_ids.add(candidate_edge_id)
    return additions


def _teacher_boundary_edge_has_target_junction(teacher_net_file: Path, teacher_junction_id: str, edge_id: str) -> bool:
    try:
        edge = ET.parse(teacher_net_file).getroot().find(f"edge[@id='{edge_id}']")
    except (ET.ParseError, OSError):
        return False
    return edge is not None and teacher_junction_id in (edge.attrib.get("from"), edge.attrib.get("to"))


def _valid_edge_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        return {}
    result: dict[str, str] = {}
    for source, target in value.items():
        if not isinstance(source, str) or not source.strip():
            return {}
        if not isinstance(target, str) or not target.strip():
            return {}
        result[source] = target
    return result


def _prefer_existing_exact_edge_ids(
    edge_map: dict[str, str],
    candidate_edges_by_id: dict[str, ET.Element],
) -> dict[str, str]:
    return {
        teacher_edge_id: teacher_edge_id
        if teacher_edge_id in candidate_edges_by_id
        else candidate_edge_id
        for teacher_edge_id, candidate_edge_id in edge_map.items()
    }


def _candidate_edge_by_exact_or_unsplit_id(
    edge_id: str,
    candidate_edges_by_id: dict[str, ET.Element],
) -> tuple[str, ET.Element | None]:
    candidate_edge = candidate_edges_by_id.get(edge_id)
    if candidate_edge is not None:
        return edge_id, candidate_edge
    if "#" not in edge_id:
        return "", None
    base_edge_id = edge_id.split("#", 1)[0]
    candidate_edge = candidate_edges_by_id.get(base_edge_id)
    if candidate_edge is None:
        return "", None
    return base_edge_id, candidate_edge


def _missing_teacher_edge_endpoint_ids(
    teacher_edges: dict[str, ET.Element],
    missing_teacher_edge_ids: list[str],
    reference_id: str,
) -> list[str]:
    return sorted(
        {
            endpoint
            for edge_id in missing_teacher_edge_ids
            if (edge := teacher_edges.get(edge_id)) is not None
            for endpoint in (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
            if endpoint and endpoint != reference_id
        }
    )
