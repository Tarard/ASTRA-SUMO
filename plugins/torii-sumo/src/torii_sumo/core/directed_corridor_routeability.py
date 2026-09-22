from __future__ import annotations

from collections import deque
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any, TypeAlias
from xml.etree import ElementTree as ET

from .candidate_contracts import file_sha256
from .detector_demand import connection_allows_passenger, lane_allows_passenger
from .digital_twin_mapping import (
    LanePathArc,
    build_local_lane_graph,
    find_local_lane_paths,
)


ControlledArcKey: TypeAlias = tuple[str, str, str, int]
ForbiddenEdgePolicy: TypeAlias = Callable[[str, Mapping[str, str]], str | None]


def audit_directed_corridor_routeability(
    net_file: Path,
    *,
    expected_net_sha256: str,
    forward_start_lane: str,
    forward_target_lane: str,
    expected_forward_controller_order: Sequence[str],
    reverse_boundary_lane_sets: Sequence[Collection[str]],
    expected_reverse_controller_order: Sequence[str],
    validated_controlled_arc_keys: (Collection[ControlledArcKey] | Mapping[ControlledArcKey, str] | None) = None,
    controller_owner_by_tls: Mapping[str, str] | None = None,
    forbidden_edge_ids: Collection[str] = (),
    forbidden_edge_types: Collection[str] = (),
    forbidden_edge_policy: ForbiddenEdgePolicy | None = None,
    max_forward_hops: int = 128,
    max_forward_span_m: float = 20_000.0,
    max_forward_paths: int = 64,
    max_reverse_states: int = 100_000,
) -> dict[str, Any]:
    """Prove the permitted direction of a hash-bound SUMO lane corridor.

    The positive half of the audit requires exactly one policy-compliant lane
    path and an exact, collapsed TLS-owner order.  The negative half performs
    an exhaustive finite-state search: it only passes after the state space is
    exhausted without finding the declared reverse owner order through the
    ordered reverse boundary sets.  A state cap is therefore a hard failure,
    never evidence of absence.
    """

    source = net_file.resolve(strict=True)
    actual_sha256 = file_sha256(source)
    expected_hash = expected_net_sha256.strip().lower()
    owner_by_tls = dict(controller_owner_by_tls or {})
    expected_forward = tuple(map(str, expected_forward_controller_order))
    expected_reverse = tuple(map(str, expected_reverse_controller_order))
    reverse_boundaries = tuple(frozenset(map(str, boundary)) for boundary in reverse_boundary_lane_sets)
    validated_arcs = _normalize_validated_arcs(validated_controlled_arc_keys)
    errors: list[str] = []

    report: dict[str, Any] = {
        "schema": "torii.directed-corridor-routeability/v1",
        "status": "fail",
        "net_file": str(source),
        "net_sha256": actual_sha256,
        "expected_net_sha256": expected_hash,
        "hash_binding_pass": actual_sha256 == expected_hash,
        "vehicle_scope": "passenger/private lanes only",
        "policy": {
            "forbidden_edge_ids": sorted(set(map(str, forbidden_edge_ids))),
            "forbidden_edge_types": sorted(set(map(str, forbidden_edge_types))),
            "built_in_forbidden_type_markers": ["closed", "construction"],
            "custom_policy": (_callable_name(forbidden_edge_policy) if forbidden_edge_policy else None),
        },
        "validated_controlled_arc_count": (len(validated_arcs) if validated_arcs is not None else None),
        "forward": {
            "start_lane": forward_start_lane,
            "target_lane": forward_target_lane,
            "expected_controller_order": list(expected_forward),
            "path_count": 0,
            "path_search_overflow": False,
            "lane_path": [],
            "edge_path": [],
            "controller_order": [],
            "controller_blocks": [],
            "controlled_arcs": [],
        },
        "reverse_proof": {
            "claim": "expected reverse controller order is unreachable",
            "boundary_lane_sets": [sorted(boundary) for boundary in reverse_boundaries],
            "expected_controller_order": list(expected_reverse),
            "complete": False,
            "expected_order_path_found": False,
            "explored_state_count": 0,
            "explored_transition_count": 0,
            "state_limit": max_reverse_states,
            "witness": None,
        },
        "checks": {},
        "blocked_lanes": [],
        "errors": errors,
    }

    _validate_limits(
        max_forward_hops=max_forward_hops,
        max_forward_span_m=max_forward_span_m,
        max_forward_paths=max_forward_paths,
        max_reverse_states=max_reverse_states,
    )
    if len(expected_hash) != 64 or any(character not in "0123456789abcdef" for character in expected_hash):
        errors.append("expected_net_sha256 is not a lowercase-normalizable SHA-256 digest")
    if actual_sha256 != expected_hash:
        errors.append("network SHA-256 does not match the expected hash binding")
        report["checks"] = _checks(report, errors)
        return report
    if len(reverse_boundaries) < 2:
        errors.append("reverse_boundary_lane_sets must contain at least start and target sets")
    if any(not boundary for boundary in reverse_boundaries):
        errors.append("reverse boundary lane sets must be nonempty")
    duplicated_boundary_lanes = _duplicate_boundary_lanes(reverse_boundaries)
    if duplicated_boundary_lanes:
        errors.append("reverse boundary lane sets must be disjoint: " + ", ".join(duplicated_boundary_lanes))
    if errors:
        report["checks"] = _checks(report, errors)
        return report

    try:
        root = ET.parse(source).getroot()
        graph, all_lane_ids = build_local_lane_graph(source)
        lane_metadata, connection_permissions = _read_lane_metadata(root)
    except (ET.ParseError, OSError, ValueError) as exc:
        errors.append(f"failed to read SUMO lane graph: {type(exc).__name__}: {exc}")
        report["checks"] = _checks(report, errors)
        return report

    policy_failures: list[str] = []
    blocked_lanes: dict[str, str] = {}
    for lane_id in sorted(all_lane_ids):
        metadata = lane_metadata.get(lane_id)
        if metadata is None:
            blocked_lanes[lane_id] = "lane metadata is missing"
            continue
        reason = _blocked_lane_reason(
            metadata,
            forbidden_edge_ids=set(map(str, forbidden_edge_ids)),
            forbidden_edge_types=set(map(str, forbidden_edge_types)),
            forbidden_edge_policy=forbidden_edge_policy,
            policy_failures=policy_failures,
        )
        if reason:
            blocked_lanes[lane_id] = reason
    errors.extend(policy_failures)
    report["blocked_lanes"] = [
        {"lane_id": lane_id, "reason": reason} for lane_id, reason in sorted(blocked_lanes.items())
    ]
    permitted_graph = _filter_graph(
        graph,
        blocked_lanes=frozenset(blocked_lanes),
        connection_permissions=connection_permissions,
    )

    boundary_lanes = set().union(*reverse_boundaries)
    referenced_lanes = {
        str(forward_start_lane),
        str(forward_target_lane),
        *boundary_lanes,
    }
    missing_lanes = sorted(referenced_lanes - set(all_lane_ids))
    if missing_lanes:
        errors.append("referenced corridor lanes are missing: " + ", ".join(missing_lanes))
    blocked_references = sorted(referenced_lanes & set(blocked_lanes))
    if blocked_references:
        errors.append(
            "referenced corridor lanes are not passenger/private policy-compliant: " + ", ".join(blocked_references)
        )

    if not missing_lanes and not blocked_references and not policy_failures:
        paths, overflow = find_local_lane_paths(
            permitted_graph,
            str(forward_start_lane),
            str(forward_target_lane),
            max_hops=max_forward_hops,
            max_span_m=max_forward_span_m,
            max_paths=max_forward_paths,
        )
        report["forward"]["path_count"] = len(paths)
        report["forward"]["path_search_overflow"] = overflow
        if overflow:
            errors.append("forward lane-path search exceeded the candidate-path limit")
        if len(paths) != 1:
            errors.append(f"forward corridor requires exactly one lane path; found {len(paths)}")
        if len(paths) == 1 and not overflow:
            forward = _describe_path(
                paths[0],
                start_lane=str(forward_start_lane),
                lane_metadata=lane_metadata,
                owner_by_tls=owner_by_tls,
                validated_arcs=validated_arcs,
            )
            report["forward"].update(forward)
            if forward["malformed_controlled_arc_count"]:
                errors.append("forward path contains TLS arcs without a valid link index")
            if forward["controller_order"] != list(expected_forward):
                errors.append(
                    "forward collapsed controller order mismatch: "
                    f"actual={forward['controller_order']}, expected={list(expected_forward)}"
                )
            if forward["unvalidated_controlled_arc_count"]:
                errors.append("forward path contains controlled arcs absent from validated movement evidence")

        reverse = _search_reverse_absence(
            graph=permitted_graph,
            boundary_lane_sets=reverse_boundaries,
            expected_controller_order=expected_reverse,
            lane_metadata=lane_metadata,
            owner_by_tls=owner_by_tls,
            validated_arcs=validated_arcs,
            max_states=max_reverse_states,
        )
        report["reverse_proof"].update(reverse)
        if reverse["state_limit_exceeded"]:
            errors.append("reverse state search hit its limit; absence is not proven")
        elif reverse["expected_order_path_found"]:
            errors.append("a forbidden reverse path with the expected controller order exists")
        elif not reverse["complete"]:
            errors.append("reverse path absence proof is incomplete")

    ending_sha256 = file_sha256(source)
    report["source_immutable"] = ending_sha256 == actual_sha256
    if not report["source_immutable"]:
        errors.append("network changed while the directed corridor audit was running")
    report["checks"] = _checks(report, errors)
    report["status"] = "pass" if all(report["checks"].values()) else "fail"
    return report


def corridor_audit_to_movement_binding(
    audit: Mapping[str, Any],
    *,
    stable_movement_id: str = "directed_corridor_forward",
    topology_hypothesis: str = "hash_bound_directed_corridor",
    candidate_plan_id: str = "directed_corridor_routeability",
    binding_id: str | None = None,
) -> dict[str, Any]:
    """Adapt a passing corridor proof for ``run_bound_cell_movement_smoke``."""

    if audit.get("status") != "pass":
        raise ValueError("only a passing directed corridor audit can become a movement binding")
    forward = audit.get("forward")
    if not isinstance(forward, Mapping):
        raise ValueError("directed corridor audit is missing its forward result")
    edge_ids = list(map(str, forward.get("edge_path", ())))
    if len(edge_ids) < 2:
        raise ValueError("directed corridor path has fewer than two external edges")
    from_lane_index = forward.get("start_lane_index")
    to_lane_index = forward.get("target_lane_index")
    if not isinstance(from_lane_index, int) or not isinstance(to_lane_index, int):
        raise ValueError("directed corridor audit is missing boundary lane indices")
    net_sha256 = str(audit.get("net_sha256", ""))
    return {
        "schema": "torii.directed-corridor-movement-binding/v1",
        "binding_status": "pass",
        "topology_hypothesis": topology_hypothesis,
        "candidate_plan_id": candidate_plan_id,
        "binding_id": binding_id or f"directed-corridor:{net_sha256[:16]}",
        "net_file": audit.get("net_file"),
        "net_sha256": net_sha256,
        "movement_records": [
            {
                "stable_movement_id": stable_movement_id,
                "edge_ids": edge_ids,
                "from_lane_index": from_lane_index,
                "to_lane_index": to_lane_index,
                "controller_binding_status": "pass",
                "controller_order": list(forward.get("controller_order", ())),
                "controlled_arc_keys": [
                    list(item["key"])
                    for item in forward.get("controlled_arcs", ())
                    if isinstance(item, Mapping) and "key" in item
                ],
            }
        ],
    }


def _read_lane_metadata(
    root: ET.Element,
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], bool]]:
    lane_metadata: dict[str, dict[str, Any]] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge.attrib.get("function") or edge_id.startswith(":"):
            continue
        edge_attributes = dict(edge.attrib)
        edge_parameters = {
            parameter.attrib.get("key", ""): parameter.attrib.get("value", "")
            for parameter in edge.findall("param")
            if parameter.attrib.get("key")
        }
        edge_permitted = lane_allows_passenger(edge)
        for lane in edge.findall("lane"):
            lane_id = lane.attrib.get("id", "")
            if not lane_id:
                continue
            try:
                lane_index = int(lane.attrib.get("index", ""))
            except ValueError:
                lane_index = -1
            lane_metadata[lane_id] = {
                "lane_id": lane_id,
                "lane_index": lane_index,
                "edge_id": edge_id,
                "edge_type": edge.attrib.get("type", ""),
                "edge_attributes": edge_attributes,
                "edge_parameters": edge_parameters,
                "allows_passenger_private": edge_permitted and lane_allows_passenger(lane),
            }

    connection_permissions: dict[tuple[str, str], bool] = {}
    for connection in root.findall("connection"):
        from_edge = connection.attrib.get("from", "")
        to_edge = connection.attrib.get("to", "")
        if not from_edge or not to_edge or from_edge.startswith(":") or to_edge.startswith(":"):
            continue
        key = (
            f"{from_edge}_{connection.attrib.get('fromLane', '')}",
            f"{to_edge}_{connection.attrib.get('toLane', '')}",
        )
        connection_permissions[key] = connection_allows_passenger(connection)
    return lane_metadata, connection_permissions


def _blocked_lane_reason(
    metadata: Mapping[str, Any],
    *,
    forbidden_edge_ids: set[str],
    forbidden_edge_types: set[str],
    forbidden_edge_policy: ForbiddenEdgePolicy | None,
    policy_failures: list[str],
) -> str:
    edge_id = str(metadata["edge_id"])
    edge_type = str(metadata["edge_type"])
    if not metadata["allows_passenger_private"]:
        return "lane or parent edge does not permit passenger/private traffic"
    if edge_id in forbidden_edge_ids:
        return "parent edge id is forbidden by corridor policy"
    if edge_type in forbidden_edge_types:
        return "parent edge type is forbidden by corridor policy"
    normalized_type = edge_type.casefold()
    if "construction" in normalized_type or "closed" in normalized_type:
        return "parent edge type is construction/closed"
    parameters = metadata.get("edge_parameters", {})
    for key, value in parameters.items():
        normalized_key = str(key).casefold()
        if ("construction" in normalized_key or "closed" in normalized_key) and _truthy(str(value)):
            return f"parent edge parameter {key!r} marks it construction/closed"
    if forbidden_edge_policy is not None:
        attributes = {
            **dict(metadata.get("edge_attributes", {})),
            **{f"param:{key}": str(value) for key, value in parameters.items()},
        }
        try:
            custom_reason = forbidden_edge_policy(edge_id, attributes)
        except Exception as exc:  # pragma: no cover - defensive fail-closed boundary
            policy_failures.append(f"custom forbidden-edge policy failed for {edge_id}: {type(exc).__name__}: {exc}")
            return "custom forbidden-edge policy raised an exception"
        if custom_reason:
            return f"custom corridor policy: {custom_reason}"
    return ""


def _filter_graph(
    graph: Mapping[str, Sequence[LanePathArc]],
    *,
    blocked_lanes: frozenset[str],
    connection_permissions: Mapping[tuple[str, str], bool],
) -> dict[str, tuple[LanePathArc, ...]]:
    return {
        lane_id: tuple(
            arc
            for arc in arcs
            if lane_id not in blocked_lanes
            and arc.to_lane not in blocked_lanes
            and connection_permissions.get((arc.from_lane, arc.to_lane), True)
        )
        for lane_id, arcs in graph.items()
        if lane_id not in blocked_lanes
    }


def _describe_path(
    path: Sequence[LanePathArc],
    *,
    start_lane: str,
    lane_metadata: Mapping[str, Mapping[str, Any]],
    owner_by_tls: Mapping[str, str],
    validated_arcs: Mapping[ControlledArcKey, str] | None,
) -> dict[str, Any]:
    lane_path = [start_lane, *(arc.to_lane for arc in path)]
    edge_path = _collapse_repeats(str(lane_metadata[lane_id]["edge_id"]) for lane_id in lane_path)
    controlled_arcs: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    malformed_count = 0
    unvalidated_count = 0
    for path_index, arc in enumerate(path):
        if not arc.tls_id:
            continue
        if arc.link_index is None:
            malformed_count += 1
            continue
        owner = owner_by_tls.get(arc.tls_id, arc.tls_id)
        key: ControlledArcKey = (
            arc.from_lane,
            arc.to_lane,
            arc.tls_id,
            arc.link_index,
        )
        evidence_id = validated_arcs.get(key, "") if validated_arcs is not None else ""
        validated = validated_arcs is None or key in validated_arcs
        if not validated:
            unvalidated_count += 1
        item = {
            "key": [key[0], key[1], key[2], key[3]],
            "owner": owner,
            "path_arc_index": path_index,
            "movement_evidence_validated": validated,
            "movement_evidence_id": evidence_id or None,
        }
        controlled_arcs.append(item)
        if blocks and blocks[-1]["owner"] == owner:
            blocks[-1]["controlled_arcs"].append(item)
            blocks[-1]["last_path_arc_index"] = path_index
        else:
            blocks.append(
                {
                    "owner": owner,
                    "first_path_arc_index": path_index,
                    "last_path_arc_index": path_index,
                    "controlled_arcs": [item],
                }
            )
    return {
        "lane_path": lane_path,
        "edge_path": edge_path,
        "start_lane_index": int(lane_metadata[lane_path[0]]["lane_index"]),
        "target_lane_index": int(lane_metadata[lane_path[-1]]["lane_index"]),
        "controller_order": [block["owner"] for block in blocks],
        "controller_blocks": blocks,
        "controlled_arcs": controlled_arcs,
        "malformed_controlled_arc_count": malformed_count,
        "unvalidated_controlled_arc_count": unvalidated_count,
    }


def _search_reverse_absence(
    *,
    graph: Mapping[str, Sequence[LanePathArc]],
    boundary_lane_sets: Sequence[frozenset[str]],
    expected_controller_order: tuple[str, ...],
    lane_metadata: Mapping[str, Mapping[str, Any]],
    owner_by_tls: Mapping[str, str],
    validated_arcs: Mapping[ControlledArcKey, str] | None,
    max_states: int,
) -> dict[str, Any]:
    # state = (lane, highest ordered boundary reached, matched owner blocks, last owner)
    State = tuple[str, int, int, str]
    queue: deque[State] = deque()
    parents: dict[State, tuple[State | None, LanePathArc | None]] = {}
    for lane_id in sorted(boundary_lane_sets[0]):
        state: State = (lane_id, 0, 0, "")
        queue.append(state)
        parents[state] = (None, None)

    explored = 0
    transitions = 0
    found: State | None = None
    limit_exceeded = False
    while queue:
        state = queue.popleft()
        explored += 1
        if explored > max_states:
            limit_exceeded = True
            break
        lane_id, boundary_index, matched_count, last_owner = state
        if (
            boundary_index == len(boundary_lane_sets) - 1
            and lane_id in boundary_lane_sets[-1]
            and matched_count == len(expected_controller_order)
        ):
            found = state
            break
        for arc in graph.get(lane_id, ()):
            transitions += 1
            next_boundary = _advance_boundary(boundary_lane_sets, boundary_index, arc.to_lane)
            if next_boundary is None:
                continue
            next_matched = matched_count
            next_owner = last_owner
            if arc.tls_id:
                owner = owner_by_tls.get(arc.tls_id, arc.tls_id)
                if owner != last_owner:
                    if (
                        matched_count >= len(expected_controller_order)
                        or owner != expected_controller_order[matched_count]
                    ):
                        continue
                    next_matched += 1
                    next_owner = owner
            next_state: State = (
                arc.to_lane,
                next_boundary,
                next_matched,
                next_owner,
            )
            if next_state in parents:
                continue
            parents[next_state] = (state, arc)
            queue.append(next_state)

    witness = None
    if found is not None:
        witness_path = _reconstruct_path(found, parents)
        start_lane = _path_start_lane(found, parents)
        witness = _describe_path(
            witness_path,
            start_lane=start_lane,
            lane_metadata=lane_metadata,
            owner_by_tls=owner_by_tls,
            validated_arcs=validated_arcs,
        )
    complete = found is None and not limit_exceeded and not queue
    return {
        "complete": complete,
        "expected_order_path_found": found is not None,
        "state_limit_exceeded": limit_exceeded,
        "explored_state_count": explored,
        "explored_transition_count": transitions,
        "witness": witness,
    }


def _advance_boundary(boundaries: Sequence[frozenset[str]], current_index: int, lane_id: str) -> int | None:
    later_hits = [index for index in range(current_index + 1, len(boundaries)) if lane_id in boundaries[index]]
    if not later_hits:
        return current_index
    if later_hits[0] != current_index + 1:
        return None
    return later_hits[0]


def _reconstruct_path(
    state: tuple[str, int, int, str],
    parents: Mapping[
        tuple[str, int, int, str],
        tuple[tuple[str, int, int, str] | None, LanePathArc | None],
    ],
) -> tuple[LanePathArc, ...]:
    reversed_arcs: list[LanePathArc] = []
    cursor = state
    while True:
        parent, arc = parents[cursor]
        if parent is None or arc is None:
            break
        reversed_arcs.append(arc)
        cursor = parent
    return tuple(reversed(reversed_arcs))


def _path_start_lane(
    state: tuple[str, int, int, str],
    parents: Mapping[
        tuple[str, int, int, str],
        tuple[tuple[str, int, int, str] | None, LanePathArc | None],
    ],
) -> str:
    cursor = state
    while parents[cursor][0] is not None:
        cursor = parents[cursor][0]  # type: ignore[assignment]
    return cursor[0]


def _normalize_validated_arcs(
    value: Collection[ControlledArcKey] | Mapping[ControlledArcKey, str] | None,
) -> dict[ControlledArcKey, str] | None:
    if value is None:
        return None
    raw_items = value.items() if isinstance(value, Mapping) else ((key, "") for key in value)
    normalized: dict[ControlledArcKey, str] = {}
    for raw_key, evidence_id in raw_items:
        if len(raw_key) != 4:
            raise ValueError("validated controlled arc keys must have four fields")
        from_lane, to_lane, tls_id, raw_link_index = raw_key
        key = (str(from_lane), str(to_lane), str(tls_id), int(raw_link_index))
        if key in normalized:
            raise ValueError(f"duplicate validated controlled arc key: {key}")
        normalized[key] = str(evidence_id)
    return normalized


def _checks(report: Mapping[str, Any], errors: Sequence[str]) -> dict[str, bool]:
    forward = report["forward"]
    reverse = report["reverse_proof"]
    return {
        "network_hash_bound": bool(report["hash_binding_pass"]),
        "forward_unique_without_overflow": forward["path_count"] == 1 and not forward["path_search_overflow"],
        "forward_controller_order": forward["controller_order"] == forward["expected_controller_order"],
        "controlled_arcs_validated": forward.get("unvalidated_controlled_arc_count", 0) == 0,
        "no_malformed_controlled_arcs": forward.get("malformed_controlled_arc_count", 0) == 0,
        "reverse_absence_proven": bool(reverse["complete"]) and not reverse["expected_order_path_found"],
        "source_immutable": bool(report.get("source_immutable", False)),
        "no_errors": not errors,
    }


def _validate_limits(
    *,
    max_forward_hops: int,
    max_forward_span_m: float,
    max_forward_paths: int,
    max_reverse_states: int,
) -> None:
    if max_forward_hops <= 0 or max_forward_paths <= 0 or max_reverse_states <= 0:
        raise ValueError("directed corridor search limits must be positive")
    if max_forward_span_m < 0:
        raise ValueError("forward path span must be non-negative")


def _duplicate_boundary_lanes(boundaries: Sequence[frozenset[str]]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for boundary in boundaries:
        duplicates.update(seen & set(boundary))
        seen.update(boundary)
    return sorted(duplicates)


def _collapse_repeats(values: Sequence[str] | Any) -> list[str]:
    collapsed: list[str] = []
    for value in values:
        if not collapsed or collapsed[-1] != value:
            collapsed.append(value)
    return collapsed


def _truthy(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "closed", "construction"}


def _callable_name(function: Callable[..., Any]) -> str:
    return getattr(function, "__qualname__", getattr(function, "__name__", type(function).__name__))
