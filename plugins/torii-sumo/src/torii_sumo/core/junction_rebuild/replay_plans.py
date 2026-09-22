"""Plan scoped and shared-controller teacher replays."""

from __future__ import annotations

import math
from collections import Counter
import xml.etree.ElementTree as ET
from pathlib import Path
from .artifacts import _failure
from .edge_mapping import _teacher_boundary_edge_ids_touching_internal_subgraph
from .network import _edge_family_id, _opposite_direction_edge_id, _signed_edge_family_id, _split


def build_scoped_teacher_tls_cell_replay_plan(
    *,
    candidate_net_file: Path,
    teacher_net_file: Path,
    teacher_junction_id: str,
    candidate_junction_id: str,
    candidate_junction_ids: set[str] | list[str] | None = None,
    approach_pairs: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Derive an auditable boundary map for a split TLS cell.

    The plan follows same-way edge chains through the candidate member nodes.
    It does not invent a destination edge: a reference boundary with no
    candidate family is left as an explicit identity/copy boundary and marked
    for review in the returned evidence.
    """

    if not candidate_net_file.exists() or not teacher_net_file.exists():
        return _failure("candidate or teacher net file is missing")
    try:
        candidate_root = ET.parse(candidate_net_file).getroot()
        teacher_root = ET.parse(teacher_net_file).getroot()
    except (ET.ParseError, OSError, KeyError, ValueError) as exc:
        return _failure(f"TLS cell replay plan parse failed: {type(exc).__name__}: {exc}")
    teacher_edges = {
        edge.attrib["id"]: edge
        for edge in teacher_root.findall("edge")
        if edge.attrib.get("id")
    }
    candidate_edges = {
        edge.attrib["id"]: edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    teacher_junction = teacher_root.find(f"junction[@id='{teacher_junction_id}']")
    candidate_junction = candidate_root.find(f"junction[@id='{candidate_junction_id}']")
    if teacher_junction is None or candidate_junction is None:
        return _failure("TLS cell replay plan target junction is missing")
    member_ids = {
        str(value)
        for value in (candidate_junction_ids or set())
        if str(value)
    }
    member_ids.add(candidate_junction_id)
    teacher_boundary_edge_ids = _teacher_boundary_edge_ids_touching_internal_subgraph(
        teacher_root.findall("connection"),
        teacher_edges,
        teacher_junction_id,
    )
    pair_edge_map = {
        str(pair.get("reference_edge_id", "")): str(pair.get("candidate_edge_id", ""))
        for pair in (approach_pairs or [])
        if isinstance(pair, dict)
        and str(pair.get("reference_edge_id", ""))
        and str(pair.get("candidate_edge_id", ""))
    }

    def lane_count(edge: ET.Element | None) -> int:
        return len(edge.findall("lane")) if edge is not None else 0

    def boundary_direction(edge: ET.Element) -> str:
        return "outgoing" if edge.attrib.get("from") == teacher_junction_id else "incoming"

    def candidate_boundary_side_matches(edge: ET.Element, direction: str) -> bool:
        """Require the candidate edge to leave/enter the split cell correctly.

        An OSM split often contains an edge with the same signed id as a
        teacher boundary edge, but on the opposite side of the physical cell.
        Treating that id as an identity mapping silently reverses the approach
        and can create duplicate candidate boundary aliases.
        """

        member_endpoint = edge.attrib.get("from" if direction == "outgoing" else "to", "")
        return member_endpoint in member_ids

    def family_candidates(
        teacher_edge: ET.Element,
        direction: str,
        *,
        excluded_candidate_ids: set[str] | None = None,
        family_override: str | None = None,
    ) -> list[tuple[int, str, ET.Element]]:
        teacher_id = teacher_edge.attrib.get("id", "")
        teacher_family = family_override or _signed_edge_family_id(teacher_id)
        expected_lane_count = lane_count(teacher_edge)
        excluded_candidate_ids = excluded_candidate_ids or set()
        ranked: list[tuple[int, str, ET.Element]] = []
        for candidate_id, candidate_edge in candidate_edges.items():
            if candidate_id.startswith(":") or candidate_edge.attrib.get("function") in {
                "internal",
                "crossing",
                "walkingarea",
            }:
                continue
            if candidate_id in excluded_candidate_ids:
                continue
            if _signed_edge_family_id(candidate_id) != teacher_family:
                continue
            if not candidate_boundary_side_matches(candidate_edge, direction):
                continue
            score = abs(lane_count(candidate_edge) - expected_lane_count) * 100
            frontier_endpoint = candidate_edge.attrib.get("from" if direction == "incoming" else "to", "")
            if frontier_endpoint not in member_ids:
                score -= 50
            if candidate_id == teacher_id:
                score -= 1000
            ranked.append((score, candidate_id, candidate_edge))
        return sorted(ranked, key=lambda item: (item[0], item[1]))

    effective_edge_map: dict[str, str] = {}
    inferred_boundary_edge_ids: list[str] = []
    copied_boundary_edge_ids: list[str] = []
    mapping_conflicts: list[dict[str, object]] = []
    used_candidate_boundary_ids: set[str] = set()

    def counterpart_candidate_for_teacher_edge(teacher_edge_id: str, direction: str) -> str:
        """Use a paired approach's opposite candidate edge when OSM renamed a way."""

        opposite_teacher_id = _opposite_direction_edge_id(teacher_edge_id)
        paired_candidate_id = pair_edge_map.get(opposite_teacher_id, "")
        if not paired_candidate_id:
            return ""
        opposite_candidate_id = _opposite_direction_edge_id(paired_candidate_id)
        opposite_candidate = candidate_edges.get(opposite_candidate_id)
        if opposite_candidate is not None and candidate_boundary_side_matches(opposite_candidate, direction):
            return opposite_candidate_id
        candidate_family = _edge_family_id(paired_candidate_id)
        alternatives = [
            (candidate_id, edge)
            for candidate_id, edge in candidate_edges.items()
            if _edge_family_id(candidate_id) == candidate_family
            and candidate_boundary_side_matches(edge, direction)
            and candidate_id not in used_candidate_boundary_ids
        ]
        alternatives.sort(key=lambda item: item[0])
        return alternatives[0][0] if alternatives else ""

    for teacher_edge_id in teacher_boundary_edge_ids:
        teacher_edge = teacher_edges[teacher_edge_id]
        direction = boundary_direction(teacher_edge)
        candidate_edge_id = pair_edge_map.get(teacher_edge_id, "")
        if candidate_edge_id and (
            candidate_edge_id not in candidate_edges
            or not candidate_boundary_side_matches(candidate_edges[candidate_edge_id], direction)
        ):
            mapping_conflicts.append(
                {
                    "teacher_edge_id": teacher_edge_id,
                    "candidate_edge_id": candidate_edge_id,
                    "reason": "approach_pair_candidate_is_not_on_expected_boundary_side",
                    "direction": direction,
                }
            )
            candidate_edge_id = ""
        if not candidate_edge_id:
            candidate_edge_id = counterpart_candidate_for_teacher_edge(teacher_edge_id, direction)
        if not candidate_edge_id:
            exact_candidate = candidate_edges.get(teacher_edge_id)
            if exact_candidate is not None and candidate_boundary_side_matches(exact_candidate, direction):
                candidate_edge_id = teacher_edge_id
            else:
                ranked = family_candidates(
                    teacher_edge,
                    direction,
                    excluded_candidate_ids=used_candidate_boundary_ids,
                )
                candidate_edge_id = ranked[0][1] if ranked else ""
        if candidate_edge_id and candidate_edge_id in used_candidate_boundary_ids:
            mapping_conflicts.append(
                {
                    "teacher_edge_id": teacher_edge_id,
                    "candidate_edge_id": candidate_edge_id,
                    "reason": "candidate_boundary_edge_reused",
                    "direction": direction,
                }
            )
        if candidate_edge_id:
            effective_edge_map[teacher_edge_id] = candidate_edge_id
            inferred_boundary_edge_ids.append(teacher_edge_id)
            used_candidate_boundary_ids.add(candidate_edge_id)
        else:
            effective_edge_map[teacher_edge_id] = teacher_edge_id
            copied_boundary_edge_ids.append(teacher_edge_id)

    def follow_chain_endpoint(
        candidate_edge: ET.Element,
        direction: str,
        family: str,
    ) -> str:
        endpoint = candidate_edge.attrib.get("to" if direction == "outgoing" else "from", "")
        visited = {candidate_edge.attrib.get("id", "")}
        while endpoint in member_ids:
            if direction == "outgoing":
                next_edges = [
                    edge
                    for edge in candidate_edges.values()
                    if edge.attrib.get("id") not in visited
                    and not edge.attrib.get("id", "").startswith(":")
                    and _signed_edge_family_id(edge.attrib.get("id", "")) == family
                    and edge.attrib.get("from") == endpoint
                ]
                next_edges.sort(key=lambda edge: edge.attrib.get("id", ""))
                if not next_edges:
                    break
                next_edge = next_edges[0]
                visited.add(next_edge.attrib.get("id", ""))
                endpoint = next_edge.attrib.get("to", "")
            else:
                previous_edges = [
                    edge
                    for edge in candidate_edges.values()
                    if edge.attrib.get("id") not in visited
                    and not edge.attrib.get("id", "").startswith(":")
                    and _signed_edge_family_id(edge.attrib.get("id", "")) == family
                    and edge.attrib.get("to") == endpoint
                ]
                previous_edges.sort(key=lambda edge: edge.attrib.get("id", ""))
                if not previous_edges:
                    break
                previous_edge = previous_edges[0]
                visited.add(previous_edge.attrib.get("id", ""))
                endpoint = previous_edge.attrib.get("from", "")
        return endpoint

    junction_map: dict[str, str] = {}
    for teacher_edge_id in teacher_boundary_edge_ids:
        teacher_edge = teacher_edges[teacher_edge_id]
        candidate_edge = candidate_edges.get(effective_edge_map.get(teacher_edge_id, ""))
        if candidate_edge is None:
            continue
        direction = boundary_direction(teacher_edge)
        teacher_external_endpoint = (
            teacher_edge.attrib.get("to", "")
            if direction == "outgoing"
            else teacher_edge.attrib.get("from", "")
        )
        candidate_external_endpoint = follow_chain_endpoint(
            candidate_edge,
            direction,
            _signed_edge_family_id(candidate_edge.attrib.get("id", "")),
        )
        if (
            teacher_external_endpoint
            and teacher_external_endpoint != teacher_junction_id
            and candidate_external_endpoint
            and candidate_external_endpoint not in member_ids
        ):
            junction_map[teacher_external_endpoint] = candidate_external_endpoint

    teacher_junctions_by_id = {
        junction.attrib["id"]: junction
        for junction in teacher_root.findall("junction")
        if junction.attrib.get("id")
    }
    candidate_junctions_by_id = {
        junction.attrib["id"]: junction
        for junction in candidate_root.findall("junction")
        if junction.attrib.get("id")
    }

    def internal_owner(value: str) -> str:
        for junction_id in sorted(teacher_junctions_by_id, key=len, reverse=True):
            if value.startswith(f":{junction_id}_"):
                return junction_id
        return ""

    controller_connections = [
        connection
        for connection in teacher_root.findall("connection")
        if connection.attrib.get("tl") == teacher_junction_id and connection.attrib.get("linkIndex")
    ]
    teacher_controller_owner_ids = sorted(
        {
            owner
            for connection in controller_connections
            for owner in [internal_owner(connection.attrib.get("via", ""))]
            if owner
        }
    )
    owner_connection_counts = Counter(
        internal_owner(connection.attrib.get("via", ""))
        for connection in controller_connections
        if internal_owner(connection.attrib.get("via", ""))
    )
    try:
        controller_dx = float(candidate_junction.attrib.get("x", "0") or 0) - float(
            teacher_junction.attrib.get("x", "0") or 0
        )
        controller_dy = float(candidate_junction.attrib.get("y", "0") or 0) - float(
            teacher_junction.attrib.get("y", "0") or 0
        )
    except (TypeError, ValueError):
        controller_dx = controller_dy = 0.0

    controller_owner_candidates: dict[str, list[dict[str, object]]] = {}
    controller_owner_map: dict[str, str] = {}
    for owner_id in teacher_controller_owner_ids:
        if owner_id == teacher_junction_id:
            controller_owner_map[owner_id] = candidate_junction_id
            continue
        teacher_owner = teacher_junctions_by_id.get(owner_id)
        if teacher_owner is None:
            continue
        try:
            expected_x = float(teacher_owner.attrib.get("x", "0") or 0) + controller_dx
            expected_y = float(teacher_owner.attrib.get("y", "0") or 0) + controller_dy
        except (TypeError, ValueError):
            expected_x = expected_y = 0.0
        candidates: list[dict[str, object]] = []
        for candidate_owner_id, candidate_owner in candidate_junctions_by_id.items():
            try:
                distance_m = math.hypot(
                    float(candidate_owner.attrib.get("x", "0") or 0) - expected_x,
                    float(candidate_owner.attrib.get("y", "0") or 0) - expected_y,
                )
            except (TypeError, ValueError):
                continue
            candidates.append(
                {
                    "candidate_junction_id": candidate_owner_id,
                    "candidate_type": candidate_owner.attrib.get("type", ""),
                    "distance_m": round(distance_m, 3),
                    "candidate_is_traffic_light": candidate_owner.attrib.get("type") == "traffic_light",
                    "candidate_is_declared_cell_member": candidate_owner_id in member_ids,
                }
            )
        candidates.sort(
            key=lambda item: (
                not bool(item["candidate_is_traffic_light"]),
                not bool(item["candidate_is_declared_cell_member"]),
                float(item["distance_m"]),
                str(item["candidate_junction_id"]),
            )
        )
        controller_owner_candidates[owner_id] = candidates[:10]
        if candidates and float(candidates[0]["distance_m"]) <= 100.0:
            controller_owner_map[owner_id] = str(candidates[0]["candidate_junction_id"])

    teacher_controller_edge_ids = sorted(
        {
            edge_id
            for connection in controller_connections
            for edge_id in (connection.attrib.get("from", ""), connection.attrib.get("to", ""))
            if edge_id and not edge_id.startswith(":")
        }
    )
    identity_available_controller_edge_ids = sorted(
        edge_id
        for edge_id in teacher_controller_edge_ids
        if edge_id not in effective_edge_map and edge_id in candidate_edges
    )
    unmapped_controller_edge_ids = sorted(
        edge_id
        for edge_id in teacher_controller_edge_ids
        if edge_id not in effective_edge_map and edge_id not in candidate_edges
    )
    extra_controller_owner_ids = [
        owner_id for owner_id in teacher_controller_owner_ids if owner_id != teacher_junction_id
    ]
    shared_controller_scope = {
        "status": "pass" if not extra_controller_owner_ids else "needs_expanded_scope",
        "teacher_controller_id": teacher_junction_id,
        "teacher_controller_connection_count": len(controller_connections),
        "teacher_controller_link_indices": sorted(
            {str(connection.attrib["linkIndex"]) for connection in controller_connections},
            key=lambda value: int(value) if value.isdigit() else value,
        ),
        "teacher_internal_owner_ids": teacher_controller_owner_ids,
        "extra_teacher_internal_owner_ids": extra_controller_owner_ids,
        "teacher_internal_owner_connection_counts": dict(sorted(owner_connection_counts.items())),
        "candidate_owner_map": dict(sorted(controller_owner_map.items())),
        "candidate_owner_candidates": controller_owner_candidates,
        "teacher_controller_edge_ids": teacher_controller_edge_ids,
        "identity_available_controller_edge_ids": identity_available_controller_edge_ids,
        "unmapped_controller_edge_ids": unmapped_controller_edge_ids,
        "policy": (
            "shared TLS controllers require an explicit owner closure and boundary edge mapping; "
            "nearest candidate junctions are evidence only and do not authorize adoption"
        ),
    }

    return {
        "status": "pass" if teacher_boundary_edge_ids else "blocked",
        "claim_status": "diagnostic-demo",
        "teacher_junction_id": teacher_junction_id,
        "candidate_junction_id": candidate_junction_id,
        "candidate_junction_ids": sorted(member_ids),
        "teacher_boundary_edge_ids": teacher_boundary_edge_ids,
        "edge_map": dict(sorted(effective_edge_map.items())),
        "junction_map": dict(sorted(junction_map.items())),
        "approach_edge_map": dict(sorted(pair_edge_map.items())),
        "inferred_boundary_edge_ids": sorted(inferred_boundary_edge_ids),
        "copied_boundary_edge_ids": sorted(copied_boundary_edge_ids),
        "mapping_conflicts": mapping_conflicts,
        "candidate_boundary_edge_reuse_count": sum(
            1
            for value, count in Counter(effective_edge_map.values()).items()
            if count > 1
        ),
        "shared_controller_scope": shared_controller_scope,
        "mapping_policy": "approach-pair_then_same_signed_family_then_explicit_teacher_boundary_copy",
    }


def build_shared_teacher_tls_controller_replay_plan(
    *,
    candidate_net_file: Path,
    teacher_net_file: Path,
    teacher_controller_id: str,
    candidate_controller_id: str,
    candidate_junction_ids: set[str] | list[str] | None = None,
    approach_pairs: list[dict[str, object]] | None = None,
    collapse_junction_ids: set[str] | list[str] | None = None,
    candidate_owner_map: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build an explicit replay plan for a shared TLS controller.

    The ordinary scoped planner intentionally reports a shared controller as
    ``needs_expanded_scope``.  This planner closes that scope only when every
    teacher internal owner has a concrete candidate owner.  Boundary matching
    prefers the queue's approach pair, then an exact signed edge family with
    compatible owner-side topology.  A missing family is represented by a
    deterministic teacher-boundary copy id; it is never silently reused from a
    nearby road.
    """

    if not candidate_net_file.exists() or not teacher_net_file.exists():
        return _failure("candidate or teacher net file is missing")
    try:
        candidate_root = ET.parse(candidate_net_file).getroot()
        teacher_root = ET.parse(teacher_net_file).getroot()
    except (ET.ParseError, OSError, ValueError) as exc:
        return _failure(f"shared TLS replay plan parse failed: {type(exc).__name__}: {exc}")

    candidate_edges = {
        edge.attrib["id"]: edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    teacher_edges = {
        edge.attrib["id"]: edge
        for edge in teacher_root.findall("edge")
        if edge.attrib.get("id")
    }
    candidate_junctions = {
        junction.attrib["id"]: junction
        for junction in candidate_root.findall("junction")
        if junction.attrib.get("id")
    }
    teacher_junctions = {
        junction.attrib["id"]: junction
        for junction in teacher_root.findall("junction")
        if junction.attrib.get("id")
    }
    base_candidate_ids = {
        str(item)
        for item in (candidate_junction_ids or set())
        if str(item)
    }
    base_candidate_ids.add(candidate_controller_id)
    base_plan = build_scoped_teacher_tls_cell_replay_plan(
        candidate_net_file=candidate_net_file,
        teacher_net_file=teacher_net_file,
        teacher_junction_id=teacher_controller_id,
        candidate_junction_id=candidate_controller_id,
        candidate_junction_ids=base_candidate_ids,
        approach_pairs=approach_pairs,
    )
    if base_plan.get("status") != "pass":
        return {
            **base_plan,
            "shared_controller_replay_status": "blocked",
            "shared_controller_replay_reason": "base_boundary_plan_not_pass",
        }
    shared_scope = base_plan.get("shared_controller_scope", {})
    if not isinstance(shared_scope, dict):
        return _failure("base plan did not provide shared_controller_scope evidence")
    inferred_owner_map = {
        str(key): str(value)
        for key, value in (shared_scope.get("candidate_owner_map", {}) or {}).items()
        if str(key) and str(value)
    }
    if candidate_owner_map:
        inferred_owner_map.update(
            {
                str(key): str(value)
                for key, value in candidate_owner_map.items()
                if str(key) and str(value)
            }
        )
    inferred_owner_map.setdefault(teacher_controller_id, candidate_controller_id)
    # A reference owner can be represented by a pre-joined candidate cluster.
    # The base planner prefers declared cell members for stability, which is
    # correct for a single-owner cell but wrong for a shared controller when a
    # nearby traffic-light cluster is present.  Prefer that explicit cluster
    # representation when it is available; otherwise retain the base planner
    # evidence (for example the unjoined OSM source).
    for owner_id in list(inferred_owner_map):
        if owner_id == teacher_controller_id:
            continue
        candidates = [
            item
            for item in (shared_scope.get("candidate_owner_candidates", {}).get(owner_id, []) or [])
            if isinstance(item, dict)
            and str(item.get("candidate_junction_id", "")) in candidate_junctions
            and str(item.get("candidate_junction_id", "")).startswith("cluster_")
            and item.get("candidate_type") == "traffic_light"
        ]
        if candidates:
            candidates.sort(
                key=lambda item: (
                    float(item.get("distance_m", float("inf"))),
                    str(item.get("candidate_junction_id", "")),
                )
            )
            inferred_owner_map[owner_id] = str(candidates[0]["candidate_junction_id"])
    missing_owner_ids = [
        owner_id
        for owner_id, candidate_owner_id in inferred_owner_map.items()
        if owner_id not in teacher_junctions or candidate_owner_id not in candidate_junctions
    ]
    if missing_owner_ids:
        return {
            **base_plan,
            "status": "blocked",
            "shared_controller_replay_status": "blocked",
            "shared_controller_replay_reason": "owner_closure_missing",
            "owner_map": dict(sorted(inferred_owner_map.items())),
            "missing_owner_ids": missing_owner_ids,
        }

    teacher_owner_ids = sorted(inferred_owner_map, key=len, reverse=True)

    def owner_for(value: str) -> str:
        for owner_id in teacher_owner_ids:
            if value.startswith(f":{owner_id}_"):
                return owner_id
        return ""

    def edge_owner(edge: ET.Element) -> str:
        for endpoint in (edge.attrib.get("from", ""), edge.attrib.get("to", "")):
            if endpoint in teacher_owner_ids:
                return endpoint
        return ""

    def has_owner_side(edge: ET.Element, owner_id: str, side: str) -> bool:
        endpoint = edge.attrib.get("from" if side == "from" else "to", "")
        return endpoint == owner_id or endpoint in {
            inferred_owner_map.get(owner_id, ""),
            *{
                str(item)
                for item in (collapse_junction_ids or set())
                if str(item)
            },
        }

    relevant_connections = [
        connection
        for connection in teacher_root.findall("connection")
        if (
            owner_for(connection.attrib.get("from", ""))
            or owner_for(connection.attrib.get("to", ""))
            or owner_for(connection.attrib.get("via", ""))
            or connection.attrib.get("tl") == teacher_controller_id
        )
    ]
    boundary_edge_ids: list[str] = []
    seen_boundary_edge_ids: set[str] = set()
    for connection in relevant_connections:
        for attr in ("from", "to"):
            edge_id = connection.attrib.get(attr, "")
            if not edge_id or edge_id.startswith(":") or edge_id not in teacher_edges:
                continue
            if edge_id not in seen_boundary_edge_ids:
                seen_boundary_edge_ids.add(edge_id)
                boundary_edge_ids.append(edge_id)
    for edge in teacher_edges.values():
        if not owner_for(edge.attrib.get("id", "")):
            continue
        for edge_id in _split(edge.attrib.get("crossingEdges", "")):
            if edge_id in teacher_edges and edge_id not in seen_boundary_edge_ids:
                seen_boundary_edge_ids.add(edge_id)
                boundary_edge_ids.append(edge_id)

    pair_map = {
        str(pair.get("reference_edge_id", "")): str(pair.get("candidate_edge_id", ""))
        for pair in (approach_pairs or [])
        if isinstance(pair, dict)
        and str(pair.get("reference_edge_id", ""))
        and str(pair.get("candidate_edge_id", ""))
    }
    requested_edge_map = {
        str(key): str(value)
        for key, value in (base_plan.get("edge_map", {}) or {}).items()
        if str(key) and str(value)
    }
    base_edge_map = dict(requested_edge_map)
    requested_edge_map.update(pair_map)
    resolved_edge_map: dict[str, str] = {}
    edge_mapping_sources: dict[str, str] = {}
    mapping_conflicts: list[dict[str, object]] = []
    mapping_rejections: list[dict[str, object]] = []
    used_candidate_edge_ids: set[str] = set()
    target_candidate_ids = {
        str(item)
        for item in (collapse_junction_ids or base_candidate_ids)
        if str(item)
    }
    target_candidate_ids.update(inferred_owner_map.values())

    def compatible_pair(teacher_edge_id: str, candidate_edge_id: str) -> bool:
        teacher_edge = teacher_edges.get(teacher_edge_id)
        candidate_edge = candidate_edges.get(candidate_edge_id)
        if teacher_edge is None or candidate_edge is None:
            return False
        if candidate_edge_id.startswith(":") or candidate_edge.attrib.get("function") in {
            "internal",
            "crossing",
            "walkingarea",
        }:
            return False
        teacher_local_at_start = teacher_edge.attrib.get("from", "") in teacher_owner_ids
        teacher_local_at_end = teacher_edge.attrib.get("to", "") in teacher_owner_ids
        candidate_local_at_start = candidate_edge.attrib.get("from", "") in target_candidate_ids
        candidate_local_at_end = candidate_edge.attrib.get("to", "") in target_candidate_ids
        # An explicit approach pair is topology evidence and commonly maps a
        # synthetic teacher id to a differently named OSM way.  Validate its
        # directed cell side, not an impossible signed-id family equality.
        return (
            teacher_local_at_start != teacher_local_at_end
            and candidate_local_at_start != candidate_local_at_end
            and teacher_local_at_start == candidate_local_at_start
        )

    def generated_edge_id(teacher_edge_id: str) -> str:
        safe = "".join(
            character if character.isalnum() or character in "_.-" else "_"
            for character in teacher_edge_id
        ).strip("_") or "edge"
        candidate = f"torii_shared_{safe}"
        suffix = 2
        while candidate in candidate_edges or candidate in used_candidate_edge_ids:
            candidate = f"torii_shared_{safe}_{suffix}"
            suffix += 1
        return candidate

    for teacher_edge_id in boundary_edge_ids:
        teacher_edge = teacher_edges[teacher_edge_id]
        chosen = requested_edge_map.get(teacher_edge_id, "")
        requested_source = (
            "explicit_approach_pair"
            if teacher_edge_id in pair_map
            else "base_plan_edge_map"
            if teacher_edge_id in base_edge_map
            else ""
        )
        if (
            chosen
            and compatible_pair(teacher_edge_id, chosen)
            and (
                teacher_edge_id in pair_map
                or (requested_source == "base_plan_edge_map" and chosen == teacher_edge_id)
            )
        ):
            edge_mapping_sources[teacher_edge_id] = (
                "explicit_approach_pair" if teacher_edge_id in pair_map else "base_plan_edge_map"
            )
        else:
            if chosen and chosen in candidate_edges:
                mapping_rejections.append(
                    {
                        "teacher_edge_id": teacher_edge_id,
                        "candidate_edge_id": chosen,
                        "reason": "explicit_candidate_missing_or_boundary_side_mismatch",
                    }
                )
            chosen = ""
        if not chosen:
            candidates: list[tuple[int, str]] = []
            teacher_from_owner = teacher_edge.attrib.get("from", "") in teacher_owner_ids
            teacher_to_owner = teacher_edge.attrib.get("to", "") in teacher_owner_ids
            for candidate_edge_id, candidate_edge in candidate_edges.items():
                if (
                    candidate_edge_id.startswith(":")
                    or candidate_edge.attrib.get("function") in {"internal", "crossing", "walkingarea"}
                    or candidate_edge_id in used_candidate_edge_ids
                    or _signed_edge_family_id(candidate_edge_id) != _signed_edge_family_id(teacher_edge_id)
                ):
                    continue
                score = abs(len(candidate_edge.findall("lane")) - len(teacher_edge.findall("lane"))) * 100
                if candidate_edge_id == teacher_edge_id:
                    score -= 1000
                if teacher_from_owner:
                    score += 0 if has_owner_side(candidate_edge, inferred_owner_map[teacher_edge.attrib["from"]], "from") else 1000
                if teacher_to_owner:
                    score += 0 if has_owner_side(candidate_edge, inferred_owner_map[teacher_edge.attrib["to"]], "to") else 1000
                candidates.append((score, candidate_edge_id))
            if candidates:
                candidates.sort()
                best_score, best_id = candidates[0]
                owner_side_match = (
                    (teacher_from_owner and has_owner_side(candidate_edges[best_id], inferred_owner_map[teacher_edge.attrib["from"]], "from"))
                    or (teacher_to_owner and has_owner_side(candidate_edges[best_id], inferred_owner_map[teacher_edge.attrib["to"]], "to"))
                )
                if best_score < 1000 or (best_score == 1000 and owner_side_match):
                    chosen = best_id
                    edge_mapping_sources[teacher_edge_id] = "signed_family_owner_side"
                elif (
                    requested_source == "base_plan_edge_map"
                    and len(candidates) == 1
                    and _signed_edge_family_id(best_id) == _signed_edge_family_id(teacher_edge_id)
                ):
                    # The OSM network may contain one split fragment with the
                    # same signed family but no endpoint at the collapsed
                    # owner.  Replacing that exact family edge with the
                    # teacher boundary is still explicit and auditable; a
                    # different-family nearest edge is never accepted here.
                    chosen = best_id
                    edge_mapping_sources[teacher_edge_id] = "base_plan_signed_family_identity"
        if not chosen:
            chosen = generated_edge_id(teacher_edge_id)
            edge_mapping_sources[teacher_edge_id] = "explicit_teacher_boundary_copy"
        if chosen in used_candidate_edge_ids and chosen != resolved_edge_map.get(teacher_edge_id):
            # Bridges may legitimately be shared by two owners, but a single
            # teacher boundary id must still have one deterministic target.
            previous = next(
                (source for source, target in resolved_edge_map.items() if target == chosen),
                "",
            )
            if previous and previous != teacher_edge_id:
                mapping_conflicts.append(
                    {
                        "teacher_edge_id": teacher_edge_id,
                        "candidate_edge_id": chosen,
                        "reason": "candidate_boundary_edge_reused",
                    }
                )
        used_candidate_edge_ids.add(chosen)
        resolved_edge_map[teacher_edge_id] = chosen

    resolved_junction_map: dict[str, str] = {}
    # Use a selected candidate edge to map the remote endpoint of an approach
    # pair.  This is especially important for a way whose OSM id differs from
    # the teacher edge id (for example gneE18 -> 816287496#0).
    def add_endpoint_evidence(teacher_edge: ET.Element, candidate_edge: ET.Element | None) -> None:
        if candidate_edge is None:
            return
        for side in ("from", "to"):
            teacher_endpoint = teacher_edge.attrib.get(side, "")
            if not teacher_endpoint or teacher_endpoint in teacher_owner_ids:
                continue
            candidate_endpoint = candidate_edge.attrib.get(side, "")
            if candidate_endpoint and candidate_endpoint not in target_candidate_ids:
                resolved_junction_map.setdefault(teacher_endpoint, candidate_endpoint)

    for teacher_edge_id, candidate_edge_id in resolved_edge_map.items():
        add_endpoint_evidence(teacher_edges[teacher_edge_id], candidate_edges.get(candidate_edge_id))
    # Preserve endpoint evidence from a queue approach pair even if the edge
    # family itself is rejected and the teacher edge is copied.  This is a
    # valid topology hint for a renamed OSM way, not permission to reuse that
    # candidate edge as the teacher boundary.
    for teacher_edge_id, candidate_edge_id in pair_map.items():
        if teacher_edge_id in teacher_edges and candidate_edge_id in candidate_edges:
            pair_teacher_edge = teacher_edges[teacher_edge_id]
            pair_candidate_edge = candidate_edges[candidate_edge_id]
            for side in ("from", "to"):
                teacher_endpoint = pair_teacher_edge.attrib.get(side, "")
                candidate_endpoint = pair_candidate_edge.attrib.get(side, "")
                if (
                    teacher_endpoint
                    and teacher_endpoint not in teacher_owner_ids
                    and candidate_endpoint
                    and candidate_endpoint not in target_candidate_ids
                ):
                    resolved_junction_map[teacher_endpoint] = candidate_endpoint

    teacher_link_indices = sorted(
        {
            str(connection.attrib.get("linkIndex", ""))
            for connection in teacher_root.findall("connection")
            if connection.attrib.get("tl") == teacher_controller_id and connection.attrib.get("linkIndex") is not None
        },
        key=lambda value: int(value) if value.isdigit() else value,
    )
    generated_boundary_edge_ids = sorted(
        teacher_edge_id
        for teacher_edge_id, source in edge_mapping_sources.items()
        if source == "explicit_teacher_boundary_copy"
    )
    status = "pass" if not missing_owner_ids and not mapping_conflicts and set(resolved_edge_map) == set(boundary_edge_ids) else "blocked"
    return {
        "status": status,
        "claim_status": "diagnostic-demo",
        "shared_controller_replay_status": status,
        "teacher_controller_id": teacher_controller_id,
        "candidate_controller_id": candidate_controller_id,
        "owner_map": dict(sorted(inferred_owner_map.items())),
        "candidate_junction_ids": sorted(target_candidate_ids),
        "teacher_owner_ids": teacher_owner_ids,
        "teacher_boundary_edge_ids": boundary_edge_ids,
        "edge_map": dict(sorted(resolved_edge_map.items())),
        "edge_mapping_sources": dict(sorted(edge_mapping_sources.items())),
        "generated_boundary_edge_ids": generated_boundary_edge_ids,
        "junction_map": dict(sorted(resolved_junction_map.items())),
        "teacher_controller_link_indices": teacher_link_indices,
        "teacher_controller_connection_count": len(
            [
                connection
                for connection in teacher_root.findall("connection")
                if connection.attrib.get("tl") == teacher_controller_id and connection.attrib.get("linkIndex") is not None
            ]
        ),
        "mapping_conflicts": mapping_conflicts,
        "mapping_rejections": mapping_rejections,
        "base_plan": base_plan,
        "mapping_policy": "explicit_owner_closure_then_topology_checked_approach_pair_then_signed_family_owner_side_then_teacher_boundary_copy",
    }
