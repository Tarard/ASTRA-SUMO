from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .canonicalizer import CanonicalEntity, CanonicalNetworkSnapshot
from .enums import FindingSeverity, TrafficSide
from .evidence import Finding
from .exact_diff import build_finding
from .ids import stable_id
from .scope import BoundaryPort, ScopeSpec


_CONNECTION_INDEX_CATEGORIES = frozenset(
    {
        "ambiguous_target_lane_fanout",
        "direct_via_lane_not_found",
        "direct_via_lane_not_internal",
        "internal_continuation_lane_not_found",
        "internal_continuation_not_internal",
        "internal_path_bounded_trace_exhausted",
        "internal_path_cycle",
        "internal_path_final_lane_invalid",
        "internal_path_final_lane_mismatch",
        "internal_path_hop_limit_exceeded",
        "internal_path_outgoing_count_not_one",
        "internal_path_target_mismatch",
        "internal_path_unusually_long",
        "lane_rank_jump",
        "lane_shape_missing_or_short",
        "missing_direct_via_lane",
        "path_endpoint_gap",
        "source_lane_invalid",
        "target_lane_invalid",
    }
)


def canonicalize_connection_mode_findings(
    audit: Mapping[str, Any],
    snapshot: CanonicalNetworkSnapshot,
) -> tuple[Finding, ...]:
    """Bind string findings to stable semantic subjects and witnesses."""

    findings: dict[str, Finding] = {}
    for record in audit.get("junctions", ()) or ():
        if not isinstance(record, Mapping):
            continue
        junction_id = str(record.get("junction_id", ""))
        for source_kind, nested_key in (
            ("connection-mode", "connection_mode_audit"),
            ("tls-link-binding", "tls_link_binding_audit"),
        ):
            nested = record.get(nested_key, {})
            if not isinstance(nested, Mapping):
                continue
            for severity, findings_key in (
                (FindingSeverity.STRUCTURAL, "structural_failures"),
                (FindingSeverity.REVIEW, "review_findings"),
            ):
                for raw_finding in nested.get(findings_key, ()) or ():
                    finding = _canonical_finding(
                        str(raw_finding),
                        junction_id=junction_id,
                        source_kind=source_kind,
                        severity=severity,
                        snapshot=snapshot,
                    )
                    findings[finding.finding_id] = finding
    network_tls = audit.get("tls_link_binding_audit", {})
    if isinstance(network_tls, Mapping):
        for severity, findings_key in (
            (FindingSeverity.STRUCTURAL, "structural_failures"),
            (FindingSeverity.REVIEW, "review_findings"),
        ):
            for raw_finding in network_tls.get(findings_key, ()) or ():
                finding = _canonical_finding(
                    str(raw_finding),
                    junction_id="",
                    source_kind="network-tls-link-binding",
                    severity=severity,
                    snapshot=snapshot,
                )
                findings[finding.finding_id] = finding
    return tuple(findings[finding_id] for finding_id in sorted(findings))


def _canonical_finding(
    raw_finding: str,
    *,
    junction_id: str,
    source_kind: str,
    severity: FindingSeverity,
    snapshot: CanonicalNetworkSnapshot,
) -> Finding:
    tokens = raw_finding.split(":")
    if tokens and tokens[0] in {"connection_mode", "tls_link_binding"}:
        source_kind = tokens.pop(0).replace("_", "-")
        if tokens and tokens[0] in snapshot.raw_id_maps.get(
            "junction_to_physical_cell", {}
        ):
            junction_id = tokens.pop(0)
    category = tokens.pop(0) if tokens else "unknown"
    cell_id = snapshot.raw_id_maps.get("junction_to_physical_cell", {}).get(
        junction_id
    )
    subject_id = cell_id or stable_id(
        "manifest",
        {"network_finding_subject": source_kind},
    )
    normalized_tokens: list[Any] = []
    if (
        category in _CONNECTION_INDEX_CATEGORIES
        and tokens
        and tokens[0] in snapshot.raw_id_maps.get(
            "connection_index_to_movement", {}
        )
    ):
        subject_id = snapshot.raw_id_maps["connection_index_to_movement"][
            tokens.pop(0)
        ]
        normalized_tokens.append(subject_id)
    for token in tokens:
        normalized_tokens.append(
            _normalize_token(
                token,
                junction_id=junction_id,
                subject_id=subject_id,
                snapshot=snapshot,
            )
        )
    return build_finding(
        category=category,
        severity=severity,
        subject_id=subject_id,
        witness={
            "source_kind": source_kind,
            "normalized_tokens": normalized_tokens,
        },
        confidence=1.0 if severity is FindingSeverity.STRUCTURAL else 0.5,
    )


def _normalize_token(
    token: str,
    *,
    junction_id: str,
    subject_id: str,
    snapshot: CanonicalNetworkSnapshot,
) -> Any:
    junction_map = snapshot.raw_id_maps.get("junction_to_physical_cell", {})
    if token in junction_map:
        return junction_map[token]
    tls_map = snapshot.raw_id_maps.get("tls_to_controller", {})
    if token in tls_map:
        return tls_map[token]
    lane_map = snapshot.raw_id_maps.get("junction_lane_to_lane_role", {})
    lane_role = lane_map.get(f"{junction_id}|{token}")
    if lane_role:
        return lane_role
    if token.startswith(":") and subject_id.startswith("movement_"):
        return stable_id("path", {"movement_id": subject_id})
    matching_ports = sorted(
        {
            port_id
            for raw_key, port_id in snapshot.raw_id_maps.get(
                "edge_flow_to_boundary_port", {}
            ).items()
            if raw_key.split("|")[1:2] == [token]
        }
    )
    if len(matching_ports) == 1:
        return matching_ports[0]
    if matching_ports:
        return {"boundary_port_ids": matching_ports}
    return token


def finding_category_counts(findings: Iterable[Finding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.category] = counts.get(finding.category, 0) + 1
    return dict(sorted(counts.items()))


def build_scope_from_junction_ids(
    source: CanonicalNetworkSnapshot,
    candidate: CanonicalNetworkSnapshot,
    *,
    target_source_junction_ids: Sequence[str],
    target_candidate_junction_ids: Sequence[str],
    guard_source_junction_ids: Sequence[str] = (),
    guard_candidate_junction_ids: Sequence[str] = (),
) -> ScopeSpec:
    """Translate raw junction selections into one stable source/candidate closure."""

    if source.traffic_side is not candidate.traffic_side:
        raise ValueError("Source and candidate traffic sides differ.")
    if source.traffic_side is TrafficSide.UNKNOWN:
        raise ValueError("Scope construction requires an explicit traffic side.")
    target_cells = _mapped_cells(
        source,
        target_source_junction_ids,
    ) | _mapped_cells(candidate, target_candidate_junction_ids)
    if not target_cells:
        raise ValueError("No target junction could be mapped to a physical cell.")
    guard_cells = (
        _mapped_cells(source, guard_source_junction_ids)
        | _mapped_cells(candidate, guard_candidate_junction_ids)
    ) - target_cells
    all_entities = {
        (entity.kind, entity.stable_entity_id): entity
        for entity in (*source.entities, *candidate.entities)
    }
    target_entity_ids = {
        entity.stable_entity_id
        for entity in all_entities.values()
        if set(entity.owner_physical_cell_ids) & target_cells
    } | target_cells
    guard_entity_ids = {
        entity.stable_entity_id
        for entity in all_entities.values()
        if (
            set(entity.owner_physical_cell_ids) & guard_cells
            and entity.stable_entity_id not in target_entity_ids
        )
    } | guard_cells
    port_entities = {
        entity.stable_entity_id: entity
        for entity in all_entities.values()
        if (
            entity.kind == "boundary_port"
            and set(entity.owner_physical_cell_ids) & target_cells
        )
    }
    lane_entities = {
        entity.stable_entity_id: entity
        for entity in all_entities.values()
        if entity.kind == "lane_role"
    }
    boundary_ports = tuple(
        _boundary_port_contract(
            port_entities[port_id],
            lane_entities=lane_entities,
            traffic_side=source.traffic_side,
        )
        for port_id in sorted(port_entities)
    )
    if not boundary_ports:
        raise ValueError("Target scope has no canonical boundary ports.")
    scope_payload = {
        "target_physical_cell_ids": sorted(target_cells),
        "guard_physical_cell_ids": sorted(guard_cells),
        "boundary_port_ids": [
            boundary_port.boundary_port_id for boundary_port in boundary_ports
        ],
        "traffic_side": source.traffic_side.value,
    }
    return ScopeSpec(
        scope_id=stable_id("scope", scope_payload),
        physical_cell_ids=frozenset(target_cells),
        target_entity_ids=frozenset(target_entity_ids),
        guard_entity_ids=frozenset(guard_entity_ids - target_entity_ids),
        closure_rules=(
            "target physical-cell owner closure",
            "controller-owned stable entity closure",
            "boundary ports immutable outside the target",
            "all unlisted stable entities are outside scope",
        ),
        boundary_ports=boundary_ports,
        traffic_side=source.traffic_side,
    )


def _mapped_cells(
    snapshot: CanonicalNetworkSnapshot,
    junction_ids: Sequence[str],
) -> set[str]:
    mapping = snapshot.raw_id_maps.get("junction_to_physical_cell", {})
    return {
        mapping[junction_id]
        for junction_id in map(str, junction_ids)
        if junction_id in mapping
    }


def _boundary_port_contract(
    entity: CanonicalEntity,
    *,
    lane_entities: Mapping[str, CanonicalEntity],
    traffic_side: TrafficSide,
) -> BoundaryPort:
    payload = entity.payload
    lane_role_ids = tuple(map(str, payload.get("lane_role_ids", ())))
    mode_permissions: dict[str, frozenset[str]] = {}
    for lane_role_id in lane_role_ids:
        lane_entity = lane_entities.get(lane_role_id)
        permissions = (
            lane_entity.payload.get("permissions", {})
            if lane_entity is not None
            else {}
        )
        allow = tuple(map(str, permissions.get("allow", ())))
        disallow = tuple(map(str, permissions.get("disallow", ())))
        tokens = (
            {f"allow:{mode}" for mode in allow}
            or {f"not:{mode}" for mode in disallow}
            or {"allow:default"}
        )
        mode_permissions[lane_role_id] = frozenset(tokens)
    edge_semantics = payload.get("edge_semantics", {})
    params = (
        edge_semantics.get("params", {})
        if isinstance(edge_semantics, Mapping)
        else {}
    )
    return BoundaryPort(
        boundary_port_id=entity.stable_entity_id,
        center_xy=tuple(payload["center_xy"]),
        tangent_xy=tuple(payload["tangent_xy"]),
        normal_xy=tuple(payload["normal_xy"]),
        lane_role_ids=lane_role_ids,
        lane_widths_m=tuple(float(value) for value in payload["lane_widths_m"]),
        mode_permissions=mode_permissions,
        source_anchor_refs=tuple(map(str, payload["source_anchor_refs"])),
        source_geometry_sha256=str(payload["source_geometry_sha256"]),
        traffic_side=traffic_side,
        sidewalk=any("pedestrian" in token for modes in mode_permissions.values() for token in modes),
        bicycle=any("bicycle" in token for modes in mode_permissions.values() for token in modes),
        rail=any(
            any(name in token for name in ("rail", "tram"))
            for modes in mode_permissions.values()
            for token in modes
        ),
        layer=int(str(params.get("layer", "0") or "0")),
        bridge=str(params.get("bridge", "")).lower() not in {"", "no", "false", "0"},
        tunnel=str(params.get("tunnel", "")).lower() not in {"", "no", "false", "0"},
    )
