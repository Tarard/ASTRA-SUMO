from __future__ import annotations

import hashlib
import json
import math
import shutil
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .command_runner import run_command
from .digital_twin import SignalStream
from .digital_twin_mapping import MapLaneBinding


OFFICIAL_TLS_REBUILD_SCHEMA_ID = "torii.official-tls-rebuild.v1"
HAMBURG_SANDTORKAI_TLS_PRESET_ID = "hamburg-sandtorkai-0228-2421-2394"
HAMBURG_SANDTORKAI_TLS_PRESET_VERSION = "2026-07-18.v2"
HAMBURG_SANDTORKAI_GROUP_INDEX_BY_NODE: dict[str, dict[str, int]] = {
    "0228": {"K1": 0, "K2": 1, "K3": 2, "K4": 3, "K6": 4, "K7": 5, "K8": 6},
    "2421": {"K1": 0, "K2": 1, "K3": 2},
    "2394": {"K1": 0, "K2": 1, "K4": 2, "K5": 3, "K7": 4},
}


class OfficialTlsPlanError(ValueError):
    """Raised when a declared official TLS plan cannot be applied without guessing."""


@dataclass(frozen=True, order=True)
class PhysicalControlledLink:
    """One physical SUMO connection controlled by an official signal group."""

    from_edge: str
    from_lane: int
    to_edge: str
    to_lane: int

    @property
    def key(self) -> tuple[str, int, str, int]:
        return (self.from_edge, self.from_lane, self.to_edge, self.to_lane)


@dataclass(frozen=True)
class OfficialTlsGroup:
    """An official signal group and every physical SUMO link that it drives."""

    official_node_id: str
    signal_group: str
    tls_id: str
    link_index: int
    physical_links: tuple[PhysicalControlledLink, ...]


@dataclass(frozen=True, order=True)
class ConnectionRepair:
    """A whitelisted external-lane connection proven by an official MAP movement."""

    official_node_id: str
    from_edge: str
    from_lane: int
    to_edge: str
    to_lane: int
    evidence: str = "official_map"
    reason: str = ""
    attributes: tuple[tuple[str, str], ...] = ()

    @property
    def key(self) -> tuple[str, int, str, int]:
        return (self.from_edge, self.from_lane, self.to_edge, self.to_lane)


@dataclass(frozen=True)
class OfficialTlsPlan:
    """A complete, declarative plan for one or more official joined controllers."""

    plan_id: str
    version: str
    groups: tuple[OfficialTlsGroup, ...]
    repairs: tuple[ConnectionRepair, ...] = ()
    retired_tls_ids: tuple[str, ...] = ()
    demoted_links: tuple[PhysicalControlledLink, ...] = ()


@dataclass(frozen=True)
class _DerivationArc:
    from_lane_id: str
    to_lane_id: str
    physical_link: PhysicalControlledLink
    tls_id: str
    link_index: int | None
    to_lane_length_m: float
    is_declared_repair: bool
    repair_evidence: str = ""


@dataclass(frozen=True)
class _PendingMovement:
    stream: SignalStream
    normalized_node_id: str
    declared: Mapping[str, Any]
    ingress: MapLaneBinding
    egress: MapLaneBinding
    path: tuple[_DerivationArc, ...]


HAMBURG_SANDTORKAI_CONNECTION_REPAIRS: tuple[ConnectionRepair, ...] = (
    ConnectionRepair(
        "0228",
        "22649708#1",
        0,
        "74547371#0",
        0,
        reason="official MAP movement 41->38, K4",
    ),
    ConnectionRepair(
        "0228",
        "1231234769#1",
        0,
        "31274978",
        1,
        reason="official MAP movement 9->7, K8",
    ),
    ConnectionRepair(
        "0228",
        "24732668#1",
        0,
        "158068424",
        0,
        reason="official MAP movement 17->24 to Niederbaumbruecke, K6",
    ),
    ConnectionRepair(
        "2421",
        "30390250#2",
        1,
        "9718800",
        0,
        reason="official MAP movement 3->11, K3",
    ),
    ConnectionRepair(
        "2421",
        "-647842957",
        0,
        "-9718800",
        1,
        reason="official MAP movement 10->6, primary K5 and secondary K2",
    ),
    ConnectionRepair(
        "2394",
        "381540198#2",
        0,
        "193847534#0",
        1,
        reason="official MAP movement 6->5, K4",
    ),
    ConnectionRepair(
        "2394",
        "60578519",
        1,
        "193847534#0",
        0,
        reason="official MAP movement 11->4, K7",
    ),
    ConnectionRepair(
        "2394",
        "60578519",
        2,
        "193847534#0",
        1,
        reason="official MAP movement 12->5, K7",
    ),
    ConnectionRepair(
        "2394",
        "381540198#2",
        0,
        "-9702435",
        0,
        reason="official MAP movement 7->14, K5",
    ),
)


def hamburg_sandtorkai_official_tls_plan(
    *,
    groups: Sequence[OfficialTlsGroup],
    retired_tls_ids: Sequence[str] = (),
) -> OfficialTlsPlan:
    """Build the versioned corridor preset while keeping its physical-link plan declarative."""

    return OfficialTlsPlan(
        plan_id=HAMBURG_SANDTORKAI_TLS_PRESET_ID,
        version=HAMBURG_SANDTORKAI_TLS_PRESET_VERSION,
        groups=tuple(groups),
        repairs=HAMBURG_SANDTORKAI_CONNECTION_REPAIRS,
        retired_tls_ids=tuple(retired_tls_ids),
    )


def derive_official_tls_plan(
    *,
    signal_streams: Sequence[SignalStream],
    lane_bindings: Sequence[MapLaneBinding],
    source_net_file: Path,
    repairs: Sequence[ConnectionRepair],
    group_index_by_node: Mapping[str, Mapping[str, int]],
    plan_id: str,
    version: str,
    retired_tls_ids: Sequence[str] | None = None,
    tls_id_by_node: Mapping[str, str] | None = None,
    uncontrolled_path_policy: str = "fail",
    unclaimed_retired_link_policy: str = "fail",
    max_path_hops: int = 6,
    max_path_span_m: float = 150.0,
    max_candidate_paths: int = 64,
) -> tuple[OfficialTlsPlan, dict[str, Any]]:
    """Derive a joined-TLS plan from primary movements and MAP-confirmed lane bindings.

    The source network is read only. Declared repairs are inserted into the in-memory lane graph
    before path search, but ordinary repairs prove connectivity only. A unique
    ``official_map_stopline`` repair is authoritative; otherwise the first source-TLS arc in the
    private movement core is selected. The caller's first-arc visual-review policy is the only
    fallback when neither exists. Any other source TLS arcs are demoted as duplicate controls. When
    ``retired_tls_ids`` is omitted, old source controller ids are discovered from selected and
    demoted source arcs; an explicitly supplied sequence preserves strict caller-owned retirement.
    """

    if uncontrolled_path_policy not in {"fail", "first_arc_visual_review"}:
        raise OfficialTlsPlanError(
            "uncontrolled_path_policy must be 'fail' or 'first_arc_visual_review'"
        )
    if unclaimed_retired_link_policy not in {
        "fail",
        "demote_after_complete_official_inventory",
    }:
        raise OfficialTlsPlanError(
            "unclaimed_retired_link_policy must be 'fail' or "
            "'demote_after_complete_official_inventory'"
        )
    if max_path_hops <= 0 or max_candidate_paths <= 0:
        raise OfficialTlsPlanError("path search hop and candidate limits must be positive")
    if max_path_span_m < 0:
        raise OfficialTlsPlanError("max_path_span_m must be non-negative")
    auto_discover_retired_tls_ids = retired_tls_ids is None
    explicit_retired_tls_ids = () if retired_tls_ids is None else tuple(retired_tls_ids)
    declared_nodes = _normalize_declared_group_indices(group_index_by_node, tls_id_by_node)
    if not declared_nodes:
        raise OfficialTlsPlanError("group_index_by_node must declare at least one official node")
    graph, network_lane_ids, source_connection_index = _build_derivation_lane_graph(
        source_net_file,
        repairs,
    )
    binding_index: dict[tuple[str, str], list[MapLaneBinding]] = {}
    for binding in lane_bindings:
        binding_index.setdefault(
            (_normalize_official_node_id(binding.node_id), binding.map_lane_id), []
        ).append(binding)

    primary_streams = sorted(
        (stream for stream in signal_streams if stream.layer_name == "primary_signal"),
        key=lambda stream: (_normalize_official_node_id(stream.node_id), stream.stream_id),
    )
    if not primary_streams:
        raise OfficialTlsPlanError("at least one primary_signal stream is required")
    if len({stream.stream_id for stream in primary_streams}) != len(primary_streams):
        raise OfficialTlsPlanError("primary signal stream ids must be unique")

    pending_movements: list[_PendingMovement] = []
    path_owners: dict[
        tuple[str, int, str, int], set[tuple[str, str]]
    ] = {}
    for stream in primary_streams:
        normalized_node_id = _normalize_official_node_id(stream.node_id)
        declared = declared_nodes.get(normalized_node_id)
        if declared is None:
            raise OfficialTlsPlanError(
                f"stream {stream.stream_id} references undeclared official node {stream.node_id!r}"
            )
        signal_group = stream.signal_group.strip().upper()
        if signal_group not in declared["group_indices"]:
            raise OfficialTlsPlanError(
                f"stream {stream.stream_id} references undeclared group {stream.node_id}/{signal_group}"
            )
        ingress = _one_active_lane_binding(
            binding_index,
            normalized_node_id,
            stream.ingress_lane_id,
            stream.stream_id,
            "ingress",
        )
        egress = _one_active_lane_binding(
            binding_index,
            normalized_node_id,
            stream.egress_lane_id,
            stream.stream_id,
            "egress",
        )
        if ingress.sumo_lane not in network_lane_ids or egress.sumo_lane not in network_lane_ids:
            raise OfficialTlsPlanError(
                f"stream {stream.stream_id} references a SUMO lane absent from the source network: "
                f"{ingress.sumo_lane!r}->{egress.sumo_lane!r}"
            )
        paths, overflow = _derive_lane_paths(
            graph,
            ingress.sumo_lane,
            egress.sumo_lane,
            max_hops=max_path_hops,
            max_span_m=max_path_span_m,
            max_paths=max_candidate_paths,
        )
        if overflow:
            raise OfficialTlsPlanError(
                f"stream {stream.stream_id} lane path search exceeded {max_candidate_paths} candidates"
            )
        if not paths:
            raise OfficialTlsPlanError(
                f"stream {stream.stream_id} has no lane path for "
                f"{ingress.sumo_lane}->{egress.sumo_lane} within "
                f"{max_path_hops} hops/{max_path_span_m:g}m"
            )
        if len(paths) != 1:
            raise OfficialTlsPlanError(
                f"stream {stream.stream_id} has {len(paths)} ambiguous bounded lane paths for "
                f"{ingress.sumo_lane}->{egress.sumo_lane}"
            )
        path = paths[0]
        owner = (normalized_node_id, signal_group)
        pending_movements.append(
            _PendingMovement(stream, normalized_node_id, declared, ingress, egress, path)
        )
        for arc in path:
            path_owners.setdefault(arc.physical_link.key, set()).add(owner)

    cross_group_keys = {
        key for key, owners in path_owners.items() if len(owners) > 1
    }
    target_tls_ids = {str(row["tls_id"]) for row in declared_nodes.values()}
    group_links: dict[
        tuple[str, str], dict[tuple[str, int, str, int], PhysicalControlledLink]
    ] = {}
    physical_owners: dict[tuple[str, int, str, int], tuple[str, str]] = {}
    demoted_links: dict[
        tuple[str, int, str, int], PhysicalControlledLink
    ] = {}
    movement_audits: list[dict[str, Any]] = []
    hit_repairs: set[tuple[str, int, str, int]] = set()
    discovered_source_tls_ids: set[str] = set()
    visual_review_count = 0
    for pending in pending_movements:
        stream = pending.stream
        normalized_node_id = pending.normalized_node_id
        declared = pending.declared
        ingress = pending.ingress
        egress = pending.egress
        path = pending.path
        signal_group = stream.signal_group.strip().upper()
        shared_indices = [
            index
            for index, arc in enumerate(path)
            if arc.physical_link.key in cross_group_keys
        ]
        private_indices = [
            index
            for index, arc in enumerate(path)
            if arc.physical_link.key not in cross_group_keys
        ]
        if private_indices:
            core_start = private_indices[0]
            core_end = private_indices[-1]
            interior_shared = [
                path[index].physical_link.key
                for index in shared_indices
                if core_start <= index <= core_end
            ]
            if interior_shared:
                raise OfficialTlsPlanError(
                    f"stream {stream.stream_id} has a cross-group shared arc inside its "
                    "movement core: "
                    + ", ".join(_format_connection_key(key) for key in interior_shared)
                )
            core_path = path[core_start : core_end + 1]
            boundary_path = (*path[:core_start], *path[core_end + 1 :])
        else:
            core_path = ()
            boundary_path = path

        for arc in boundary_path:
            if arc.physical_link.key not in cross_group_keys:
                continue
            if arc.repair_evidence == "official_map_stopline":
                raise OfficialTlsPlanError(
                    "official stop-line connection is shared by different control groups: "
                    + _format_connection_key(arc.physical_link.key)
                )
            if arc.tls_id and arc.link_index is not None:
                demoted_links[arc.physical_link.key] = arc.physical_link
                if arc.tls_id not in target_tls_ids:
                    discovered_source_tls_ids.add(arc.tls_id)

        stopline_arcs = [
            arc for arc in core_path if arc.repair_evidence == "official_map_stopline"
        ]
        if len(stopline_arcs) > 1:
            raise OfficialTlsPlanError(
                f"stream {stream.stream_id} has multiple official stop-line control arcs"
            )
        source_control_arcs = [
            arc for arc in core_path if arc.tls_id and arc.link_index is not None
        ]
        if stopline_arcs:
            selected_arcs = stopline_arcs
            selection_policy = "explicit_official_stopline_control_arc"
        else:
            selected_arcs = source_control_arcs[:1]
            selection_policy = "first_private_core_source_tls_arc"
        review_required = False
        visual_review_reasons: list[str] = []
        if not selected_arcs:
            if not core_path:
                raise OfficialTlsPlanError(
                    f"stream {stream.stream_id} has no cross-group-unshared private movement core"
                )
            if uncontrolled_path_policy != "first_arc_visual_review":
                raise OfficialTlsPlanError(
                    f"stream {stream.stream_id} first private movement-core arc has no source "
                    "TLS or official stop-line authority; "
                    "explicit first_arc_visual_review policy is required"
                )
            selected_arcs = [core_path[0]]
            selection_policy = "first_private_core_arc_visual_review"
            review_required = True
            visual_review_reasons.append("first_arc_without_control_authority")

        selected_key = selected_arcs[0].physical_link.key
        selected_index = next(
            index for index, arc in enumerate(path) if arc.physical_link.key == selected_key
        )
        selected_core_index = next(
            index for index, arc in enumerate(core_path) if arc.physical_link.key == selected_key
        )
        if selected_index > 0:
            review_required = True
            visual_review_reasons.append("selected_control_arc_not_on_bound_ingress")
        if review_required:
            visual_review_count += 1
        for arc in path:
            if arc.is_declared_repair:
                hit_repairs.add(arc.physical_link.key)
        duplicate_control_arcs = [
            arc
            for arc in source_control_arcs
            if arc.physical_link.key != selected_key
        ]
        for arc in duplicate_control_arcs:
            if arc.tls_id and arc.link_index is not None:
                demoted_links[arc.physical_link.key] = arc.physical_link
                if arc.tls_id not in target_tls_ids:
                    discovered_source_tls_ids.add(arc.tls_id)

        owner = (normalized_node_id, signal_group)
        bucket = group_links.setdefault(owner, {})
        selected_rows = []
        for arc in selected_arcs:
            key = arc.physical_link.key
            previous_owner = physical_owners.get(key)
            if previous_owner is not None and previous_owner != owner:
                raise OfficialTlsPlanError(
                    f"physical connection {_format_connection_key(key)} is claimed by different "
                    f"official groups {previous_owner[1]} and {signal_group}"
                )
            physical_owners[key] = owner
            bucket[key] = arc.physical_link
            evidence = []
            if arc.tls_id and arc.link_index is not None:
                evidence.append("source_tls")
                if arc.tls_id != declared["tls_id"]:
                    discovered_source_tls_ids.add(arc.tls_id)
            if arc.repair_evidence == "official_map_stopline":
                evidence.append("declared_repair")
                evidence.append("official_map_stopline")
            evidence.extend(visual_review_reasons)
            selected_rows.append(
                {
                    **_connection_key_dict(key),
                    "evidence": evidence,
                    "source_tls_id": arc.tls_id,
                    "source_link_index": arc.link_index,
                }
            )
        movement_audits.append(
            {
                "stream_id": stream.stream_id,
                "official_node_id": declared["display_node_id"],
                "connection_id": stream.connection_id,
                "signal_group": signal_group,
                "official_ingress_lane": stream.ingress_lane_id,
                "official_egress_lane": stream.egress_lane_id,
                "sumo_ingress_lane": ingress.sumo_lane,
                "sumo_egress_lane": egress.sumo_lane,
                "candidate_path_count": 1,
                "path_lane_ids": [ingress.sumo_lane, *(arc.to_lane_id for arc in path)],
                "path_hops": len(path),
                "path_span_m": sum(arc.to_lane_length_m for arc in path[:-1]),
                "selected_control_arc_path_index": selected_index,
                "selected_control_arc_core_index": selected_core_index,
                "movement_core_physical_links": [
                    _connection_key_dict(arc.physical_link.key) for arc in core_path
                ],
                "demoted_shared_boundary_links": [
                    _connection_key_dict(arc.physical_link.key)
                    for arc in boundary_path
                    if arc.tls_id and arc.link_index is not None
                ],
                "selection_policy": selection_policy,
                "visual_review_required": review_required,
                "visual_review_reasons": visual_review_reasons,
                "selected_physical_links": selected_rows,
                "demoted_duplicate_control_links": [
                    _connection_key_dict(arc.physical_link.key)
                    for arc in duplicate_control_arcs
                ],
            }
        )

    groups = []
    for (normalized_node_id, signal_group), links in sorted(group_links.items()):
        declared = declared_nodes[normalized_node_id]
        groups.append(
            OfficialTlsGroup(
                official_node_id=str(declared["display_node_id"]),
                signal_group=signal_group,
                tls_id=str(declared["tls_id"]),
                link_index=int(declared["group_indices"][signal_group]),
                physical_links=tuple(links[key] for key in sorted(links)),
            )
        )
    resolved_retired_tls_ids = (
        tuple(sorted(discovered_source_tls_ids))
        if auto_discover_retired_tls_ids
        else explicit_retired_tls_ids
    )
    plan = OfficialTlsPlan(
        plan_id=plan_id,
        version=version,
        groups=tuple(groups),
        repairs=tuple(repairs),
        retired_tls_ids=resolved_retired_tls_ids,
        demoted_links=tuple(demoted_links[key] for key in sorted(demoted_links)),
    )
    assignments, capacities = _validate_plan(plan)
    classified_keys = set(assignments) | set(demoted_links)
    unclaimed_retired_keys = sorted(
        key
        for key, element in source_connection_index.items()
        if element.attrib.get("tl", "") in plan.retired_tls_ids
        and key not in classified_keys
    )
    unclaimed_retired_rows: list[dict[str, Any]] = []
    if (
        unclaimed_retired_keys
        and unclaimed_retired_link_policy
        == "demote_after_complete_official_inventory"
    ):
        declared_group_owners = {
            (node_id, signal_group)
            for node_id, row in declared_nodes.items()
            for signal_group in row["group_indices"]
        }
        observed_group_owners = {
            (
                pending.normalized_node_id,
                pending.stream.signal_group.strip().upper(),
            )
            for pending in pending_movements
        }
        missing_group_owners = sorted(declared_group_owners - observed_group_owners)
        if missing_group_owners:
            raise OfficialTlsPlanError(
                "cannot demote unclaimed retired links because the official primary inventory "
                "does not cover declared groups: "
                + ", ".join(f"{node}/{group}" for node, group in missing_group_owners)
            )
        for key in unclaimed_retired_keys:
            element = source_connection_index[key]
            demoted_links[key] = PhysicalControlledLink(*key)
            raw_link_index = element.attrib.get("linkIndex")
            unclaimed_retired_rows.append(
                {
                    **_connection_key_dict(key),
                    "source_tls_id": element.attrib.get("tl", ""),
                    "source_link_index": (
                        int(raw_link_index)
                        if raw_link_index not in (None, "")
                        else None
                    ),
                    "classification": "demoted",
                    "reason": "outside_complete_official_primary_inventory",
                }
            )
        plan = OfficialTlsPlan(
            plan_id=plan.plan_id,
            version=plan.version,
            groups=plan.groups,
            repairs=plan.repairs,
            retired_tls_ids=plan.retired_tls_ids,
            demoted_links=tuple(demoted_links[key] for key in sorted(demoted_links)),
        )
        assignments, capacities = _validate_plan(plan)
    _audit_retired_controller_takeover(
        source_connection_index,
        assignments,
        plan.retired_tls_ids,
        demoted_keys=set(demoted_links),
    )
    _audit_assigned_source_controllers(
        source_connection_index,
        assignments,
        plan.retired_tls_ids,
    )
    repair_keys = {repair.key for repair in repairs}
    unused_repairs = sorted(repair_keys - hit_repairs)
    audit_status = "visual_review_required" if visual_review_count else "pass"
    audit = {
        "schema_id": "torii.official-tls-plan-derivation.v1",
        "status": audit_status,
        "plan_id": plan_id,
        "plan_version": version,
        "source_net_file": str(source_net_file.resolve()),
        "primary_stream_count": len(primary_streams),
        "derived_group_count": len(groups),
        "derived_physical_link_count": len(assignments),
        "demoted_physical_link_count": len(demoted_links),
        "demoted_physical_links": [
            _connection_key_dict(key) for key in sorted(demoted_links)
        ],
        "cross_group_path_owners": [
            {
                **_connection_key_dict(key),
                "owners": [
                    {"official_node_id": owner[0], "signal_group": owner[1]}
                    for owner in sorted(path_owners[key])
                ],
            }
            for key in sorted(cross_group_keys)
        ],
        "visual_review_required_count": visual_review_count,
        "retired_tls_resolution": (
            "auto_from_selected_source_arcs"
            if auto_discover_retired_tls_ids
            else "explicit"
        ),
        "discovered_source_tls_ids": sorted(discovered_source_tls_ids),
        "retired_tls_ids": list(resolved_retired_tls_ids),
        "unclaimed_retired_link_policy": unclaimed_retired_link_policy,
        "unclaimed_retired_links": unclaimed_retired_rows,
        "path_policy": {
            "max_hops": max_path_hops,
            "max_span_m": max_path_span_m,
            "max_candidate_paths": max_candidate_paths,
            "uncontrolled_path_policy": uncontrolled_path_policy,
            "deterministic_path_requirement": "exactly_one_bounded_lane_path",
        },
        "controller_capacities": capacities,
        "repair_count": len(repairs),
        "hit_repair_count": len(hit_repairs),
        "unused_repairs": [_connection_key_dict(key) for key in unused_repairs],
        "movements": movement_audits,
    }
    return plan, audit


def apply_official_tls_plan_to_plain(
    *,
    source_connections_file: Path,
    source_tllogic_file: Path | None,
    output_connections_file: Path,
    output_tllogic_file: Path,
    plan: OfficialTlsPlan,
    source_nodes_file: Path | None = None,
    output_nodes_file: Path | None = None,
) -> dict[str, Any]:
    """Apply an official joined-TLS plan to plain-XML copies, never to the source files."""

    assignments, controller_capacities = _validate_plan(plan)
    connection_tree = ET.parse(source_connections_file)
    connection_root = connection_tree.getroot()
    if connection_root.tag != "connections":
        raise OfficialTlsPlanError(
            f"plain connection root must be <connections>, got <{connection_root.tag}>"
    )
    preserved_plain_connection_directive_count = sum(
        _is_plain_connection_directive(connection)
        for connection in connection_root.findall("connection")
    )
    original_connections = _index_connections(connection_root)
    tllogic_tree = _read_or_create_tllogic_tree(source_tllogic_file)
    tllogic_root = tllogic_tree.getroot()
    source_tls_bindings = _index_source_tls_bindings(
        connection_root=connection_root,
        tllogic_root=tllogic_root,
    )
    demoted_keys = {link.key for link in plan.demoted_links}
    _audit_retired_controller_takeover(
        source_tls_bindings,
        assignments,
        plan.retired_tls_ids,
        demoted_keys=demoted_keys,
    )
    _audit_assigned_source_controllers(
        source_tls_bindings,
        assignments,
        plan.retired_tls_ids,
    )
    source_tls_replacements: dict[str, set[str]] = {}
    for key, group in assignments.items():
        source = source_tls_bindings.get(key)
        source_tls_id = source.attrib.get("tl", "") if source is not None else ""
        if source_tls_id in plan.retired_tls_ids:
            source_tls_replacements.setdefault(source_tls_id, set()).add(group.tls_id)

    repairs_added: list[tuple[str, int, str, int]] = []
    repairs_existing: list[tuple[str, int, str, int]] = []
    for repair in plan.repairs:
        existing = original_connections.get(repair.key)
        if existing is None:
            existing = ET.SubElement(connection_root, "connection", _repair_attributes(repair))
            original_connections[repair.key] = existing
            repairs_added.append(repair.key)
        else:
            _validate_existing_repair(existing, repair)
            repairs_existing.append(repair.key)

    missing_links = sorted((set(assignments) | demoted_keys) - set(original_connections))
    if missing_links:
        raise OfficialTlsPlanError(
            "declared assigned or demoted physical TLS links do not exist after repairs: "
            + ", ".join(_format_connection_key(key) for key in missing_links)
        )

    # The plain connection schema contains geometry only.  TLS ownership belongs to
    # root-level <connection> directives in the .tll.xml file, not the .con.xml file.
    # Strip legacy attributes from every geometry row so even migrated inputs produce
    # schema-valid output while their unaffected bindings are preserved below.
    for connection in original_connections.values():
        connection.attrib.pop("tl", None)
        connection.attrib.pop("linkIndex", None)
    for key in demoted_keys:
        connection = original_connections[key]
        connection.set("uncontrolled", "true")
    for key in assignments:
        connection = original_connections[key]
        # Explicitly force control.  Merely removing ``uncontrolled=true`` is
        # insufficient when a formerly-priority straight continuation is
        # converted into a multi-node TLS owner: netconvert may infer it as
        # uncontrolled again and silently discard the .tll binding.
        connection.set("uncontrolled", "false")

    output_connections_file.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(connection_tree, space="    ")
    connection_tree.write(output_connections_file, encoding="utf-8", xml_declaration=True)

    node_application = _apply_retired_tls_node_policy(
        source_nodes_file=source_nodes_file,
        output_nodes_file=output_nodes_file,
        retired_tls_ids=set(plan.retired_tls_ids),
        source_tls_replacements=source_tls_replacements,
    )

    replacement_ids = set(controller_capacities) | set(plan.retired_tls_ids)
    removed_tllogics = [
        element.attrib.get("id", "")
        for element in list(tllogic_root.findall("tlLogic"))
        if element.attrib.get("id", "") in replacement_ids
    ]
    for element in list(tllogic_root.findall("tlLogic")):
        if element.attrib.get("id", "") in replacement_ids:
            tllogic_root.remove(element)
    for element in list(tllogic_root.findall("connection")):
        tllogic_root.remove(element)
    for tls_id, capacity in sorted(controller_capacities.items()):
        logic = ET.SubElement(
            tllogic_root,
            "tlLogic",
            {
                "id": tls_id,
                "type": "static",
                "programID": "official-all-red-placeholder",
                "offset": "0",
            },
        )
        ET.SubElement(logic, "phase", {"duration": "1", "state": "r" * capacity})

    preserved_tls_bindings = {
        key: dict(element.attrib)
        for key, element in source_tls_bindings.items()
        if key not in assignments
        and key not in demoted_keys
        and element.attrib.get("tl", "") not in replacement_ids
    }
    output_tls_bindings = dict(preserved_tls_bindings)
    for key, group in assignments.items():
        output_tls_bindings[key] = {
            "from": key[0],
            "to": key[2],
            "fromLane": str(key[1]),
            "toLane": str(key[3]),
            "tl": group.tls_id,
            "linkIndex": str(group.link_index),
        }
    for key in sorted(output_tls_bindings):
        ET.SubElement(tllogic_root, "connection", output_tls_bindings[key])
    output_tllogic_file.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tllogic_tree, space="    ")
    tllogic_tree.write(output_tllogic_file, encoding="utf-8", xml_declaration=True)

    physical_link_counts: dict[str, int] = {}
    for group in plan.groups:
        key = f"{group.tls_id}[{group.link_index}]"
        physical_link_counts[key] = physical_link_counts.get(key, 0) + len(group.physical_links)
    return {
        "status": "pass",
        "plan_id": plan.plan_id,
        "plan_version": plan.version,
        "repair_declared_count": len(plan.repairs),
        "repair_added_count": len(repairs_added),
        "repair_existing_count": len(repairs_existing),
        "repairs_added": [_connection_key_dict(key) for key in repairs_added],
        "repairs_existing": [_connection_key_dict(key) for key in repairs_existing],
        "physical_controlled_link_count": len(assignments),
        "demoted_physical_link_count": len(demoted_keys),
        "demoted_physical_links": [
            _connection_key_dict(key) for key in sorted(demoted_keys)
        ],
        "preserved_plain_connection_directive_count": (
            preserved_plain_connection_directive_count
        ),
        "plain_node_application": node_application,
        "physical_link_counts_by_shared_index": physical_link_counts,
        "source_tls_binding_count": len(source_tls_bindings),
        "preserved_tls_binding_count": len(preserved_tls_bindings),
        "output_tls_binding_count": len(output_tls_bindings),
        "controller_capacities": controller_capacities,
        "retired_tls_ids": sorted(set(plan.retired_tls_ids)),
        "removed_or_replaced_tllogics": sorted(removed_tllogics),
        "placeholder_program": "all_red",
    }


def build_official_tls_rebuild_variant(
    *,
    source_net_file: Path,
    plan: OfficialTlsPlan,
    output_dir: Path,
    netconvert_binary: str = "netconvert",
    timeout_seconds: float = 240.0,
    max_external_lane_shape_deviation_m: float = 10.0,
    command_runner: Callable[..., Any] = run_command,
) -> dict[str, Any]:
    """Export, patch, and rebuild a separate official-TLS network variant."""

    source_net_file = source_net_file.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = output_dir / "official_tls_rebuild.manifest.json"
    rebuilt_net_file = output_dir / "official_tls_rebuilt.net.xml"
    source_hash_before = _sha256_file(source_net_file) if source_net_file.is_file() else ""
    report: dict[str, Any] = {
        "schema_id": OFFICIAL_TLS_REBUILD_SCHEMA_ID,
        "status": "fail",
        "claim_status": "construction-invalid",
        "plan": asdict(plan),
        "source_net_file": str(source_net_file),
        "source_net_sha256_before": source_hash_before,
        "rebuilt_net_file": str(rebuilt_net_file),
    }
    try:
        if not source_net_file.is_file():
            raise OfficialTlsPlanError(f"source SUMO network does not exist: {source_net_file}")
        if (
            not math.isfinite(max_external_lane_shape_deviation_m)
            or max_external_lane_shape_deviation_m < 0
        ):
            raise OfficialTlsPlanError(
                "max_external_lane_shape_deviation_m must be finite and non-negative"
            )
        _validate_plan(plan)
        source_signature = edge_lane_signature(source_net_file)
        base_prefix = output_dir / "osm_base"
        export_command = [
            netconvert_binary,
            "--sumo-net-file",
            str(source_net_file),
            "--plain-output-prefix",
            str(base_prefix),
            "--plain-output.lanes",
            "true",
        ]
        export_result = _result_to_dict(
            command_runner(export_command, cwd=output_dir, timeout_seconds=timeout_seconds)
        )
        report["plain_export_command"] = export_command
        report["plain_export_result"] = export_result
        if export_result.get("status") != "pass":
            raise OfficialTlsPlanError("netconvert plain-XML export failed")

        base_paths = _plain_paths(base_prefix)
        for required in (base_paths["nodes"], base_paths["edges"], base_paths["connections"]):
            if not required.is_file():
                raise OfficialTlsPlanError(f"netconvert did not create required plain XML: {required}")
        official_prefix = output_dir / "official_tls"
        official_paths = _plain_paths(official_prefix)
        shutil.copy2(base_paths["edges"], official_paths["edges"])
        if base_paths["types"].is_file():
            shutil.copy2(base_paths["types"], official_paths["types"])
        application = apply_official_tls_plan_to_plain(
            source_connections_file=base_paths["connections"],
            source_tllogic_file=base_paths["tllogic"] if base_paths["tllogic"].is_file() else None,
            output_connections_file=official_paths["connections"],
            output_tllogic_file=official_paths["tllogic"],
            plan=plan,
            source_nodes_file=base_paths["nodes"],
            output_nodes_file=official_paths["nodes"],
        )
        report["plain_application"] = application

        rebuild_command = [
            netconvert_binary,
            "--node-files",
            str(official_paths["nodes"]),
            "--edge-files",
            str(official_paths["edges"]),
            "--connection-files",
            str(official_paths["connections"]),
            "--tllogic-files",
            str(official_paths["tllogic"]),
        ]
        if official_paths["types"].is_file():
            rebuild_command.extend(["--type-files", str(official_paths["types"])])
        rebuild_command.extend(
            [
                "--offset.disable-normalization",
                "true",
                "--tls.discard-loaded",
                "false",
                "--output-file",
                str(rebuilt_net_file),
                "--error-log",
                str(output_dir / "netconvert.log"),
            ]
        )
        rebuild_result = _result_to_dict(
            command_runner(rebuild_command, cwd=output_dir, timeout_seconds=timeout_seconds)
        )
        report["rebuild_command"] = rebuild_command
        report["rebuild_result"] = rebuild_result
        if rebuild_result.get("status") != "pass" or not rebuilt_net_file.is_file():
            raise OfficialTlsPlanError("netconvert official TLS rebuild failed")

        source_hash_after = _sha256_file(source_net_file)
        rebuilt_signature = edge_lane_signature(rebuilt_net_file)
        lane_geometry = audit_external_lane_geometry(
            source_net_file,
            rebuilt_net_file,
            max_shape_deviation_m=max_external_lane_shape_deviation_m,
        )
        connection_delta = external_connection_delta(source_net_file, rebuilt_net_file)
        phase_capacity = audit_phase_capacity(rebuilt_net_file, {group.tls_id for group in plan.groups})
        retired_tls_absence = audit_retired_tls_absence(
            rebuilt_net_file, set(plan.retired_tls_ids)
        )
        expected_added = {
            repair.key for repair in plan.repairs if repair.key not in _external_connection_keys(source_net_file)
        }
        actual_added = {_connection_key_from_dict(row) for row in connection_delta["added"]}
        unexpected_added = sorted(actual_added - expected_added)
        missing_added = sorted(expected_added - actual_added)
        source_unchanged = source_hash_before == source_hash_after
        signature_match = source_signature["sha256"] == rebuilt_signature["sha256"]
        delta_ok = not connection_delta["removed"] and not unexpected_added and not missing_added
        status = (
            "pass"
            if source_unchanged
            and signature_match
            and lane_geometry["status"] == "pass"
            and delta_ok
            and phase_capacity["status"] == "pass"
            and retired_tls_absence["status"] == "pass"
            else "fail"
        )
        report.update(
            {
                "status": status,
                "claim_status": "official-tls-review-variant" if status == "pass" else "construction-invalid",
                "source_net_sha256_after": source_hash_after,
                "source_net_unchanged": source_unchanged,
                "source_edge_lane_signature": source_signature,
                "rebuilt_edge_lane_signature": rebuilt_signature,
                "edge_lane_signature_match": signature_match,
                "external_lane_geometry_audit": lane_geometry,
                "connection_delta": connection_delta,
                "unexpected_added_connections": [_connection_key_dict(key) for key in unexpected_added],
                "missing_declared_added_connections": [_connection_key_dict(key) for key in missing_added],
                "phase_capacity_audit": phase_capacity,
                "retired_tls_absence_audit": retired_tls_absence,
                "placeholder_program_policy": (
                    "all-red topology placeholder only; replace with OCIT program or complete official replay before use"
                ),
            }
        )
    except (ET.ParseError, OSError, OfficialTlsPlanError, ValueError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["source_net_sha256_after"] = (
            _sha256_file(source_net_file) if source_net_file.is_file() else ""
        )
        report["source_net_unchanged"] = (
            bool(source_hash_before) and report["source_net_sha256_after"] == source_hash_before
        )

    _write_json(manifest_file, report)
    report["manifest_file"] = str(manifest_file)
    report["manifest_sha256"] = _sha256_file(manifest_file)
    return report


def edge_lane_signature(net_file: Path) -> dict[str, Any]:
    """Hash stable non-internal edge/lane structure and operational attributes.

    A plain-XML round trip may clip lane endpoints at a rebuilt junction, recalculate
    ``length``, and add ``customShape``.  Those geometry fields are audited separately
    with an explicit metric tolerance; every other external edge/lane attribute remains
    part of this exact hash.
    """

    root = ET.parse(net_file).getroot()
    records: list[dict[str, Any]] = []
    lane_count = 0
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function"):
            continue
        lanes = []
        for lane in edge.findall("lane"):
            lanes.append(
                sorted(
                    (key, value)
                    for key, value in lane.attrib.items()
                    if key not in {"shape", "length", "customShape"}
                )
            )
            lane_count += 1
        records.append(
            {
                "edge": sorted(
                    (key, value) for key, value in edge.attrib.items() if key != "shape"
                ),
                "lanes": sorted(lanes),
            }
        )
    records.sort(key=lambda row: dict(row["edge"]).get("id", ""))
    payload = json.dumps(records, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "edge_count": len(records),
        "lane_count": lane_count,
    }


def audit_external_lane_geometry(
    source_net_file: Path,
    rebuilt_net_file: Path,
    *,
    max_shape_deviation_m: float = 10.0,
    max_length_deviation_m: float | None = None,
) -> dict[str, Any]:
    """Audit CRS preservation and bounded external-lane geometry changes.

    ``max_length_deviation_m=None`` permits the normal length recalculation
    caused by bounded endpoint clipping.  Geometry-preserving patch workflows
    should pass ``0`` so a length-only mutation cannot escape the shape audit.
    """

    if not math.isfinite(max_shape_deviation_m) or max_shape_deviation_m < 0:
        raise ValueError("max_shape_deviation_m must be finite and non-negative")
    if max_length_deviation_m is not None and (
        not math.isfinite(max_length_deviation_m) or max_length_deviation_m < 0
    ):
        raise ValueError("max_length_deviation_m must be finite and non-negative")
    source_root = ET.parse(source_net_file).getroot()
    rebuilt_root = ET.parse(rebuilt_net_file).getroot()
    source_location = source_root.find("location")
    rebuilt_location = rebuilt_root.find("location")
    source_location_attributes = dict(source_location.attrib) if source_location is not None else {}
    rebuilt_location_attributes = (
        dict(rebuilt_location.attrib) if rebuilt_location is not None else {}
    )
    location_match = source_location_attributes == rebuilt_location_attributes
    source_lanes = _external_lane_shapes(source_root)
    rebuilt_lanes = _external_lane_shapes(rebuilt_root)
    source_lengths = _external_lane_lengths(source_root)
    rebuilt_lengths = _external_lane_lengths(rebuilt_root)
    missing_lane_ids = sorted(set(source_lanes) - set(rebuilt_lanes))
    unexpected_lane_ids = sorted(set(rebuilt_lanes) - set(source_lanes))
    deviations: list[tuple[str, float]] = []
    invalid_shape_lane_ids: list[str] = []
    for lane_id in sorted(set(source_lanes) & set(rebuilt_lanes)):
        source_shape = source_lanes[lane_id]
        rebuilt_shape = rebuilt_lanes[lane_id]
        if len(source_shape) < 2 or len(rebuilt_shape) < 2:
            invalid_shape_lane_ids.append(lane_id)
            continue
        deviations.append(
            (
                lane_id,
                max(
                    _directed_vertex_to_polyline_distance(source_shape, rebuilt_shape),
                    _directed_vertex_to_polyline_distance(rebuilt_shape, source_shape),
                ),
            )
        )
    violations = [
        {"lane_id": lane_id, "max_shape_deviation_m": deviation}
        for lane_id, deviation in deviations
        if deviation > max_shape_deviation_m + 1e-9
    ]
    length_deviations = [
        (lane_id, abs(source_lengths[lane_id] - rebuilt_lengths[lane_id]))
        for lane_id in sorted(set(source_lengths) & set(rebuilt_lengths))
    ]
    length_violations = (
        [
            {"lane_id": lane_id, "absolute_length_deviation_m": deviation}
            for lane_id, deviation in length_deviations
            if deviation > max_length_deviation_m + 1e-9
        ]
        if max_length_deviation_m is not None
        else []
    )
    maximum = max((deviation for _lane_id, deviation in deviations), default=0.0)
    maximum_length = max(
        (deviation for _lane_id, deviation in length_deviations),
        default=0.0,
    )
    status = (
        "pass"
        if location_match
        and not missing_lane_ids
        and not unexpected_lane_ids
        and not invalid_shape_lane_ids
        and not violations
        and not length_violations
        else "fail"
    )
    return {
        "status": status,
        "policy": "symmetric maximum vertex-to-polyline distance on every external lane",
        "max_allowed_shape_deviation_m": max_shape_deviation_m,
        "max_allowed_length_deviation_m": max_length_deviation_m,
        "maximum_observed_shape_deviation_m": maximum,
        "maximum_observed_length_deviation_m": maximum_length,
        "source_location": source_location_attributes,
        "rebuilt_location": rebuilt_location_attributes,
        "location_match": location_match,
        "source_lane_count": len(source_lanes),
        "rebuilt_lane_count": len(rebuilt_lanes),
        "missing_lane_ids": missing_lane_ids,
        "unexpected_lane_ids": unexpected_lane_ids,
        "invalid_shape_lane_ids": invalid_shape_lane_ids,
        "violations": violations,
        "length_violations": length_violations,
    }


def _external_lane_shapes(root: ET.Element) -> dict[str, tuple[tuple[float, float], ...]]:
    result: dict[str, tuple[tuple[float, float], ...]] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function"):
            continue
        for lane in edge.findall("lane"):
            lane_id = lane.attrib.get("id", "")
            if not lane_id:
                continue
            if lane_id in result:
                raise ValueError(f"duplicate external lane id {lane_id!r}")
            result[lane_id] = _parse_shape(lane.attrib.get("shape", ""))
    return result


def _external_lane_lengths(root: ET.Element) -> dict[str, float]:
    result: dict[str, float] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function"):
            continue
        for lane in edge.findall("lane"):
            lane_id = lane.attrib.get("id", "")
            if not lane_id:
                continue
            if lane_id in result:
                raise ValueError(f"duplicate external lane id {lane_id!r}")
            try:
                length = float(lane.attrib.get("length", ""))
            except ValueError as exc:
                raise ValueError(f"invalid external lane length on {lane_id!r}") from exc
            if not math.isfinite(length) or length < 0:
                raise ValueError(f"invalid external lane length on {lane_id!r}")
            result[lane_id] = length
    return result


def _parse_shape(value: str) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for token in value.split():
        parts = token.split(",")
        if len(parts) < 2:
            raise ValueError(f"invalid SUMO shape token {token!r}")
        points.append((float(parts[0]), float(parts[1])))
    return tuple(points)


def _directed_vertex_to_polyline_distance(
    source: Sequence[tuple[float, float]],
    target: Sequence[tuple[float, float]],
) -> float:
    return max(
        min(
            _point_to_segment_distance(point, start, end)
            for start, end in zip(target, target[1:])
        )
        for point in source
    )


def _point_to_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    squared = dx * dx + dy * dy
    if squared <= 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    projection = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / squared
    projection = min(1.0, max(0.0, projection))
    nearest = (start[0] + projection * dx, start[1] + projection * dy)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1])


def external_connection_delta(source_net_file: Path, rebuilt_net_file: Path) -> dict[str, Any]:
    """Compare only external edge/lane movements, excluding regenerated internal edges."""

    source = _external_connection_keys(source_net_file)
    rebuilt = _external_connection_keys(rebuilt_net_file)
    return {
        "source_count": len(source),
        "rebuilt_count": len(rebuilt),
        "added": [_connection_key_dict(key) for key in sorted(rebuilt - source)],
        "removed": [_connection_key_dict(key) for key in sorted(source - rebuilt)],
    }


def source_tls_controller_ids(net_file: Path) -> tuple[str, ...]:
    """Return every TLS controller referenced by a compiled SUMO network."""

    root = ET.parse(net_file).getroot()
    controller_ids = {
        element.attrib.get("id", "").strip() for element in root.findall("tlLogic")
    }
    controller_ids.update(
        connection.attrib.get("tl", "").strip()
        for connection in root.findall("connection")
        if connection.attrib.get("tl", "").strip()
    )
    controller_ids.discard("")
    return tuple(sorted(controller_ids))


def audit_retired_tls_absence(
    net_file: Path,
    retired_tls_ids: set[str],
) -> dict[str, Any]:
    """Fail when netconvert regenerated a retired controller or connection reference."""

    root = ET.parse(net_file).getroot()
    retired_tllogic_ids = sorted(
        {
            logic.attrib.get("id", "")
            for logic in root.findall("tlLogic")
            if logic.attrib.get("id", "") in retired_tls_ids
        }
    )
    retired_connection_tls_ids = sorted(
        {
            connection.attrib.get("tl", "")
            for connection in root.findall("connection")
            if connection.attrib.get("tl", "") in retired_tls_ids
        }
    )
    status = (
        "pass"
        if not retired_tllogic_ids and not retired_connection_tls_ids
        else "fail"
    )
    return {
        "status": status,
        "retired_tls_ids": sorted(retired_tls_ids),
        "retired_tllogic_ids": retired_tllogic_ids,
        "retired_connection_tls_ids": retired_connection_tls_ids,
    }


def audit_phase_capacity(net_file: Path, tls_ids: set[str]) -> dict[str, Any]:
    """Require every planned tlLogic phase to cover its maximum shared linkIndex."""

    root = ET.parse(net_file).getroot()
    logic_by_id = {
        logic.attrib.get("id", ""): logic for logic in root.findall("tlLogic") if logic.attrib.get("id")
    }
    indices: dict[str, set[int]] = {tls_id: set() for tls_id in tls_ids}
    for connection in root.findall("connection"):
        tls_id = connection.attrib.get("tl", "")
        if tls_id not in indices or connection.attrib.get("linkIndex") in (None, ""):
            continue
        indices[tls_id].add(int(connection.attrib["linkIndex"]))
    controllers: dict[str, Any] = {}
    all_pass = True
    for tls_id in sorted(tls_ids):
        capacity = max(indices[tls_id], default=-1) + 1
        logic = logic_by_id.get(tls_id)
        state_lengths = [len(phase.attrib.get("state", "")) for phase in logic.findall("phase")] if logic else []
        status = "pass" if capacity > 0 and state_lengths and all(length == capacity for length in state_lengths) else "fail"
        all_pass &= status == "pass"
        controllers[tls_id] = {
            "status": status,
            "used_link_indices": sorted(indices[tls_id]),
            "required_state_length": capacity,
            "phase_state_lengths": state_lengths,
        }
    return {"status": "pass" if tls_ids and all_pass else "fail", "controllers": controllers}


def _normalize_declared_group_indices(
    group_index_by_node: Mapping[str, Mapping[str, int]],
    tls_id_by_node: Mapping[str, str] | None,
) -> dict[str, dict[str, Any]]:
    normalized_tls_ids = {
        _normalize_official_node_id(node_id): tls_id.strip()
        for node_id, tls_id in (tls_id_by_node or {}).items()
    }
    declared: dict[str, dict[str, Any]] = {}
    for display_node_id, raw_indices in group_index_by_node.items():
        normalized_node_id = _normalize_official_node_id(display_node_id)
        if not normalized_node_id:
            raise OfficialTlsPlanError("declared official node id cannot be empty")
        if normalized_node_id in declared:
            raise OfficialTlsPlanError(
                f"duplicate normalized official node declaration for {display_node_id!r}"
            )
        group_indices: dict[str, int] = {}
        index_owners: dict[int, str] = {}
        for raw_group, raw_index in raw_indices.items():
            signal_group = raw_group.strip().upper()
            if not signal_group or raw_index < 0:
                raise OfficialTlsPlanError(
                    f"invalid declared group index {display_node_id}/{raw_group}={raw_index}"
                )
            if signal_group in group_indices:
                raise OfficialTlsPlanError(
                    f"duplicate declared signal group {display_node_id}/{signal_group}"
                )
            previous_group = index_owners.get(raw_index)
            if previous_group is not None:
                raise OfficialTlsPlanError(
                    f"declared linkIndex {raw_index} is shared by different groups "
                    f"{display_node_id}/{previous_group} and {signal_group}"
                )
            group_indices[signal_group] = int(raw_index)
            index_owners[int(raw_index)] = signal_group
        if not group_indices:
            raise OfficialTlsPlanError(f"official node {display_node_id} has no declared groups")
        tls_id = normalized_tls_ids.get(normalized_node_id, f"HH_{display_node_id}")
        if not tls_id:
            raise OfficialTlsPlanError(f"official node {display_node_id} has an empty TLS id")
        declared[normalized_node_id] = {
            "display_node_id": display_node_id,
            "tls_id": tls_id,
            "group_indices": group_indices,
        }
    unknown_tls_nodes = sorted(set(normalized_tls_ids) - set(declared))
    if unknown_tls_nodes:
        raise OfficialTlsPlanError(
            "tls_id_by_node references undeclared official nodes: " + ", ".join(unknown_tls_nodes)
        )
    return declared


def _build_derivation_lane_graph(
    source_net_file: Path,
    repairs: Sequence[ConnectionRepair],
) -> tuple[
    dict[str, list[_DerivationArc]],
    set[str],
    dict[tuple[str, int, str, int], ET.Element],
]:
    root = ET.parse(source_net_file).getroot()
    lane_by_edge_index: dict[tuple[str, int], str] = {}
    lane_lengths: dict[str, float] = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function"):
            continue
        for lane in edge.findall("lane"):
            lane_id = lane.attrib.get("id", "")
            try:
                lane_index = int(lane.attrib.get("index", ""))
                lane_length = float(lane.attrib.get("length", "0"))
            except ValueError as exc:
                raise OfficialTlsPlanError(
                    f"invalid lane index or length on {edge_id}: {lane.attrib}"
                ) from exc
            if not lane_id:
                raise OfficialTlsPlanError(f"edge {edge_id} has a lane without an id")
            key = (edge_id, lane_index)
            if key in lane_by_edge_index:
                raise OfficialTlsPlanError(f"duplicate lane index {edge_id}[{lane_index}]")
            lane_by_edge_index[key] = lane_id
            lane_lengths[lane_id] = lane_length

    repair_by_key: dict[tuple[str, int, str, int], ConnectionRepair] = {}
    for repair in repairs:
        _validate_connection_key(repair.key)
        previous = repair_by_key.get(repair.key)
        if previous is not None and previous != repair:
            raise OfficialTlsPlanError(
                f"conflicting declared repairs for {_format_connection_key(repair.key)}"
            )
        if previous is not None:
            raise OfficialTlsPlanError(
                f"duplicate declared repair for {_format_connection_key(repair.key)}"
            )
        repair_by_key[repair.key] = repair

    graph: dict[str, list[_DerivationArc]] = {}
    source_connection_index: dict[tuple[str, int, str, int], ET.Element] = {}
    for connection in root.findall("connection"):
        from_edge = connection.attrib.get("from", "")
        to_edge = connection.attrib.get("to", "")
        if not from_edge or not to_edge or from_edge.startswith(":") or to_edge.startswith(":"):
            continue
        key = _element_connection_key(connection)
        if key in source_connection_index:
            raise OfficialTlsPlanError(
                f"duplicate source network connection: {_format_connection_key(key)}"
            )
        source_connection_index[key] = connection
        graph.setdefault(_lane_id_for_connection_key(key, lane_by_edge_index, "from"), []).append(
            _derivation_arc(
                key,
                connection.attrib.get("tl", ""),
                connection.attrib.get("linkIndex"),
                lane_by_edge_index,
                lane_lengths,
                is_declared_repair=key in repair_by_key,
                repair_evidence=(repair_by_key[key].evidence if key in repair_by_key else ""),
            )
        )
    for key in sorted(set(repair_by_key) - set(source_connection_index)):
        graph.setdefault(_lane_id_for_connection_key(key, lane_by_edge_index, "from"), []).append(
            _derivation_arc(
                key,
                "",
                None,
                lane_by_edge_index,
                lane_lengths,
                is_declared_repair=True,
                repair_evidence=repair_by_key[key].evidence,
            )
        )
    for arcs in graph.values():
        arcs.sort(
            key=lambda arc: (
                arc.to_lane_id,
                arc.physical_link.key,
                arc.tls_id,
                arc.link_index if arc.link_index is not None else -1,
            )
        )
    return graph, set(lane_lengths), source_connection_index


def _derivation_arc(
    key: tuple[str, int, str, int],
    tls_id: str,
    raw_link_index: str | None,
    lane_by_edge_index: Mapping[tuple[str, int], str],
    lane_lengths: Mapping[str, float],
    *,
    is_declared_repair: bool,
    repair_evidence: str,
) -> _DerivationArc:
    from_lane_id = _lane_id_for_connection_key(key, lane_by_edge_index, "from")
    to_lane_id = _lane_id_for_connection_key(key, lane_by_edge_index, "to")
    link_index: int | None = None
    if tls_id and raw_link_index not in (None, ""):
        try:
            link_index = int(raw_link_index)
        except ValueError as exc:
            raise OfficialTlsPlanError(
                f"invalid source linkIndex {raw_link_index!r} on {_format_connection_key(key)}"
            ) from exc
    return _DerivationArc(
        from_lane_id=from_lane_id,
        to_lane_id=to_lane_id,
        physical_link=PhysicalControlledLink(*key),
        tls_id=tls_id,
        link_index=link_index,
        to_lane_length_m=lane_lengths[to_lane_id],
        is_declared_repair=is_declared_repair,
        repair_evidence=repair_evidence,
    )


def _lane_id_for_connection_key(
    key: tuple[str, int, str, int],
    lane_by_edge_index: Mapping[tuple[str, int], str],
    endpoint: str,
) -> str:
    edge_lane = (key[0], key[1]) if endpoint == "from" else (key[2], key[3])
    lane_id = lane_by_edge_index.get(edge_lane)
    if lane_id is None:
        raise OfficialTlsPlanError(
            f"connection {_format_connection_key(key)} references missing {endpoint} lane "
            f"{edge_lane[0]}[{edge_lane[1]}]"
        )
    return lane_id


def _one_active_lane_binding(
    binding_index: Mapping[tuple[str, str], Sequence[MapLaneBinding]],
    normalized_node_id: str,
    map_lane_id: str,
    stream_id: int,
    role: str,
) -> MapLaneBinding:
    candidates = list(binding_index.get((normalized_node_id, map_lane_id), ()))
    if len(candidates) != 1:
        raise OfficialTlsPlanError(
            f"stream {stream_id} {role} MAP lane {map_lane_id!r} has "
            f"{len(candidates)} SUMO bindings"
        )
    binding = candidates[0]
    if binding.mapping_status != "active" or not binding.sumo_lane:
        raise OfficialTlsPlanError(
            f"stream {stream_id} {role} MAP lane {map_lane_id!r} binding is not active"
        )
    return binding


def _derive_lane_paths(
    graph: Mapping[str, Sequence[_DerivationArc]],
    start_lane: str,
    target_lane: str,
    *,
    max_hops: int,
    max_span_m: float,
    max_paths: int,
) -> tuple[list[tuple[_DerivationArc, ...]], bool]:
    queue: deque[tuple[str, tuple[_DerivationArc, ...], frozenset[str], float]] = deque(
        [(start_lane, (), frozenset({start_lane}), 0.0)]
    )
    paths: list[tuple[_DerivationArc, ...]] = []
    while queue:
        lane_id, path, seen, span_m = queue.popleft()
        if lane_id == target_lane and path:
            paths.append(path)
            if len(paths) > max_paths:
                return paths[:max_paths], True
            continue
        if len(path) >= max_hops:
            continue
        for arc in graph.get(lane_id, ()):
            if arc.to_lane_id in seen:
                continue
            next_span = span_m + (
                0.0 if arc.to_lane_id == target_lane else arc.to_lane_length_m
            )
            if next_span > max_span_m:
                continue
            queue.append(
                (
                    arc.to_lane_id,
                    (*path, arc),
                    seen | {arc.to_lane_id},
                    next_span,
                )
            )
    return paths, False


def _normalize_official_node_id(value: str) -> str:
    text = str(value).strip()
    if text.isdigit():
        return str(int(text))
    return text


def _validate_plan(
    plan: OfficialTlsPlan,
) -> tuple[dict[tuple[str, int, str, int], OfficialTlsGroup], dict[str, int]]:
    if not plan.plan_id.strip() or not plan.version.strip():
        raise OfficialTlsPlanError("plan_id and version are required")
    if not plan.groups:
        raise OfficialTlsPlanError("official TLS plan must declare at least one signal group")
    assignments: dict[tuple[str, int, str, int], OfficialTlsGroup] = {}
    group_index: dict[tuple[str, str], int] = {}
    index_group: dict[tuple[str, int], str] = {}
    capacities: dict[str, int] = {}
    for group in plan.groups:
        if not group.official_node_id.strip() or not group.signal_group.strip() or not group.tls_id.strip():
            raise OfficialTlsPlanError("official node id, signal group, and tls id are required")
        if group.link_index < 0:
            raise OfficialTlsPlanError(f"negative linkIndex for {group.tls_id}/{group.signal_group}")
        if not group.physical_links:
            raise OfficialTlsPlanError(f"{group.tls_id}/{group.signal_group} has no physical links")
        group_key = (group.tls_id, group.signal_group)
        previous_index = group_index.setdefault(group_key, group.link_index)
        if previous_index != group.link_index:
            raise OfficialTlsPlanError(
                f"official group {group.tls_id}/{group.signal_group} has multiple linkIndex values"
            )
        index_key = (group.tls_id, group.link_index)
        previous_group = index_group.setdefault(index_key, group.signal_group)
        if previous_group != group.signal_group:
            raise OfficialTlsPlanError(
                f"{group.tls_id}[{group.link_index}] is assigned to both {previous_group} and {group.signal_group}"
            )
        capacities[group.tls_id] = max(capacities.get(group.tls_id, 0), group.link_index + 1)
        for physical_link in group.physical_links:
            _validate_connection_key(physical_link.key)
            previous = assignments.get(physical_link.key)
            if previous is not None:
                raise OfficialTlsPlanError(
                    "physical connection assigned more than once: "
                    f"{_format_connection_key(physical_link.key)} ({previous.signal_group}, {group.signal_group})"
                )
            assignments[physical_link.key] = group
    repairs: dict[tuple[str, int, str, int], ConnectionRepair] = {}
    for repair in plan.repairs:
        _validate_connection_key(repair.key)
        previous = repairs.get(repair.key)
        if previous is not None and previous != repair:
            raise OfficialTlsPlanError(
                f"conflicting repairs for {_format_connection_key(repair.key)}"
            )
        if previous is not None:
            raise OfficialTlsPlanError(f"duplicate repair for {_format_connection_key(repair.key)}")
        repairs[repair.key] = repair
        reserved = {"from", "to", "fromLane", "toLane", "tl", "linkIndex"}
        invalid = sorted(reserved & {key for key, _ in repair.attributes})
        if invalid:
            raise OfficialTlsPlanError(
                f"repair attributes cannot override {', '.join(invalid)} for {_format_connection_key(repair.key)}"
            )
    if len(set(plan.retired_tls_ids)) != len(plan.retired_tls_ids):
        raise OfficialTlsPlanError("retired_tls_ids contains duplicates")
    reused_controller_ids = sorted(set(capacities) & set(plan.retired_tls_ids))
    if reused_controller_ids:
        raise OfficialTlsPlanError(
            "retired TLS ids cannot also be target TLS ids: "
            + ", ".join(reused_controller_ids)
        )
    demoted_keys: set[tuple[str, int, str, int]] = set()
    for physical_link in plan.demoted_links:
        _validate_connection_key(physical_link.key)
        if physical_link.key in demoted_keys:
            raise OfficialTlsPlanError(
                f"duplicate demoted physical connection: "
                f"{_format_connection_key(physical_link.key)}"
            )
        if physical_link.key in assignments:
            raise OfficialTlsPlanError(
                f"physical connection cannot be both assigned and demoted: "
                f"{_format_connection_key(physical_link.key)}"
            )
        demoted_keys.add(physical_link.key)
    return assignments, capacities


def _audit_retired_controller_takeover(
    source_connections: Mapping[tuple[str, int, str, int], ET.Element],
    assignments: Mapping[tuple[str, int, str, int], OfficialTlsGroup],
    retired_tls_ids: Sequence[str],
    *,
    demoted_keys: set[tuple[str, int, str, int]] | None = None,
) -> None:
    retired = set(retired_tls_ids)
    classified_keys = set(assignments) | set(demoted_keys or ())
    uncovered = sorted(
        key
        for key, element in source_connections.items()
        if element.attrib.get("tl", "") in retired and key not in classified_keys
    )
    if uncovered:
        raise OfficialTlsPlanError(
            "retired source TLS controllers are not fully taken over; uncovered connections: "
            + ", ".join(_format_connection_key(key) for key in uncovered)
        )


def _audit_assigned_source_controllers(
    source_connections: Mapping[tuple[str, int, str, int], ET.Element],
    assignments: Mapping[tuple[str, int, str, int], OfficialTlsGroup],
    retired_tls_ids: Sequence[str],
) -> None:
    retired = set(retired_tls_ids)
    for key, group in assignments.items():
        element = source_connections.get(key)
        if element is None:
            continue
        source_tls_id = element.attrib.get("tl", "")
        if source_tls_id and source_tls_id != group.tls_id and source_tls_id not in retired:
            raise OfficialTlsPlanError(
                f"{_format_connection_key(key)} is controlled by non-retired source TLS {source_tls_id!r}"
            )


def _index_source_tls_bindings(
    *,
    connection_root: ET.Element,
    tllogic_root: ET.Element,
) -> dict[tuple[str, int, str, int], ET.Element]:
    """Index TLS bindings from their real plain schema, migrating legacy rows safely.

    ``netconvert --plain-output-prefix`` writes geometric connections to ``.con.xml``
    and controller bindings as root-level ``<connection>`` elements in ``.tll.xml``.
    Older Torii output incorrectly placed ``tl``/``linkIndex`` on geometry rows.  Reading
    those attributes as a compatibility input lets us rewrite that output without losing
    an unrelated controller, but conflicting declarations still fail closed.
    """

    indexed: dict[tuple[str, int, str, int], ET.Element] = {}

    def add(element: ET.Element, source: str) -> None:
        key = _element_connection_key(element)
        tls_id = element.attrib.get("tl", "")
        raw_link_index = element.attrib.get("linkIndex")
        if not tls_id or raw_link_index in (None, ""):
            raise OfficialTlsPlanError(
                f"invalid TLS binding in {source} for {_format_connection_key(key)}: "
                "both tl and linkIndex are required"
            )
        try:
            link_index = int(raw_link_index)
        except ValueError as exc:
            raise OfficialTlsPlanError(
                f"invalid TLS linkIndex {raw_link_index!r} in {source} for "
                f"{_format_connection_key(key)}"
            ) from exc
        if link_index < 0:
            raise OfficialTlsPlanError(
                f"negative TLS linkIndex in {source} for {_format_connection_key(key)}"
            )
        previous = indexed.get(key)
        if previous is not None:
            previous_control = (
                previous.attrib.get("tl", ""),
                previous.attrib.get("linkIndex", ""),
            )
            current_control = (tls_id, str(link_index))
            if previous_control != current_control:
                raise OfficialTlsPlanError(
                    f"conflicting TLS bindings for {_format_connection_key(key)}: "
                    f"{previous_control[0]}[{previous_control[1]}] versus "
                    f"{current_control[0]}[{current_control[1]}]"
                )
            return
        normalized = ET.Element("connection", dict(element.attrib))
        normalized.set("linkIndex", str(link_index))
        indexed[key] = normalized

    for element in tllogic_root.findall("connection"):
        add(element, "plain tllogic file")
    for element in connection_root.findall("connection"):
        if _is_plain_connection_directive(element):
            continue
        has_tls = bool(element.attrib.get("tl", ""))
        has_link_index = element.attrib.get("linkIndex") not in (None, "")
        if has_tls or has_link_index:
            add(element, "legacy plain connection file")
    return indexed


def _index_connections(root: ET.Element) -> dict[tuple[str, int, str, int], ET.Element]:
    index: dict[tuple[str, int, str, int], ET.Element] = {}
    for connection in root.findall("connection"):
        if _is_plain_connection_directive(connection):
            continue
        key = _element_connection_key(connection)
        if key in index:
            raise OfficialTlsPlanError(f"duplicate plain connection: {_format_connection_key(key)}")
        index[key] = connection
    return index


def _is_plain_connection_directive(element: ET.Element) -> bool:
    """Recognize netconvert's from-only delete/roundtrip preservation sentinel."""

    if set(element.attrib) != {"from"} or not element.attrib.get("from", ""):
        return False
    return all(child.tag == "stopOffset" for child in element)


def _element_connection_key(element: ET.Element) -> tuple[str, int, str, int]:
    try:
        key = (
            element.attrib["from"],
            int(element.attrib["fromLane"]),
            element.attrib["to"],
            int(element.attrib["toLane"]),
        )
    except (KeyError, ValueError) as exc:
        raise OfficialTlsPlanError(f"invalid plain connection attributes: {element.attrib}") from exc
    _validate_connection_key(key)
    return key


def _validate_connection_key(key: tuple[str, int, str, int]) -> None:
    if not key[0] or not key[2] or key[1] < 0 or key[3] < 0:
        raise OfficialTlsPlanError(f"invalid connection key: {key!r}")


def _repair_attributes(repair: ConnectionRepair) -> dict[str, str]:
    attributes = {
        "from": repair.from_edge,
        "to": repair.to_edge,
        "fromLane": str(repair.from_lane),
        "toLane": str(repair.to_lane),
    }
    attributes.update(dict(repair.attributes))
    return attributes


def _validate_existing_repair(element: ET.Element, repair: ConnectionRepair) -> None:
    for key, expected in repair.attributes:
        actual = element.attrib.get(key)
        if actual not in (None, expected):
            raise OfficialTlsPlanError(
                f"existing repair {_format_connection_key(repair.key)} has {key}={actual!r}, expected {expected!r}"
            )
        if actual is None:
            element.set(key, expected)


def _read_or_create_tllogic_tree(source_tllogic_file: Path | None) -> ET.ElementTree:
    if source_tllogic_file is None or not source_tllogic_file.is_file():
        return ET.ElementTree(ET.Element("tlLogics"))
    tree = ET.parse(source_tllogic_file)
    if tree.getroot().tag not in {"tlLogics", "additional"}:
        raise OfficialTlsPlanError(
            f"plain tlLogic root must be <tlLogics> or <additional>, got <{tree.getroot().tag}>"
        )
    return tree


def _apply_retired_tls_node_policy(
    *,
    source_nodes_file: Path | None,
    output_nodes_file: Path | None,
    retired_tls_ids: set[str],
    source_tls_replacements: Mapping[str, set[str]],
) -> dict[str, Any]:
    if source_nodes_file is None and output_nodes_file is None:
        return {"status": "not_requested"}
    if source_nodes_file is None or output_nodes_file is None:
        raise OfficialTlsPlanError(
            "source_nodes_file and output_nodes_file must be provided together"
        )
    if source_nodes_file.resolve() == output_nodes_file.resolve():
        raise OfficialTlsPlanError("plain node policy requires a separate output file")
    tree = ET.parse(source_nodes_file)
    root = tree.getroot()
    if root.tag != "nodes":
        raise OfficialTlsPlanError(
            f"plain node root must be <nodes>, got <{root.tag}>"
        )
    reassigned: list[dict[str, str]] = []
    demoted: list[dict[str, str]] = []
    matched_retired_ids: set[str] = set()
    for node in root.findall("node"):
        node_id = node.attrib.get("id", "")
        node_tls_id = node.attrib.get("tl", "")
        matches = {value for value in (node_tls_id, node_id) if value in retired_tls_ids}
        if not matches:
            continue
        matched_retired_ids.update(matches)
        replacements = {
            replacement
            for retired_id in matches
            for replacement in source_tls_replacements.get(retired_id, set())
        }
        if len(replacements) > 1:
            raise OfficialTlsPlanError(
                f"retired TLS node {node_id!r} maps to multiple target controllers: "
                + ", ".join(sorted(replacements))
            )
        if replacements:
            replacement = next(iter(replacements))
            node.set("type", "traffic_light")
            node.set("tl", replacement)
            reassigned.append(
                {"node_id": node_id, "target_tls_id": replacement}
            )
        else:
            node.set("type", "priority")
            for attribute in ("tl", "tlType", "tlLayout"):
                node.attrib.pop(attribute, None)
            demoted.append({"node_id": node_id})
    stale_node_tls = sorted(
        {
            node.attrib.get("tl", "")
            for node in root.findall("node")
            if node.attrib.get("tl", "") in retired_tls_ids
        }
    )
    if stale_node_tls:
        raise OfficialTlsPlanError(
            "retired TLS ids remain on plain nodes: " + ", ".join(stale_node_tls)
        )
    output_nodes_file.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="    ")
    tree.write(output_nodes_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "reassigned_nodes": reassigned,
        "demoted_nodes": demoted,
        "unmatched_retired_tls_ids": sorted(retired_tls_ids - matched_retired_ids),
    }


def _plain_paths(prefix: Path) -> dict[str, Path]:
    return {
        "nodes": prefix.parent / f"{prefix.name}.nod.xml",
        "edges": prefix.parent / f"{prefix.name}.edg.xml",
        "connections": prefix.parent / f"{prefix.name}.con.xml",
        "tllogic": prefix.parent / f"{prefix.name}.tll.xml",
        "types": prefix.parent / f"{prefix.name}.typ.xml",
    }


def _external_connection_keys(net_file: Path) -> set[tuple[str, int, str, int]]:
    root = ET.parse(net_file).getroot()
    keys = set()
    for connection in root.findall("connection"):
        from_edge = connection.attrib.get("from", "")
        to_edge = connection.attrib.get("to", "")
        if not from_edge or not to_edge or from_edge.startswith(":") or to_edge.startswith(":"):
            continue
        keys.add(_element_connection_key(connection))
    return keys


def _connection_key_dict(key: tuple[str, int, str, int]) -> dict[str, str | int]:
    return {"from_edge": key[0], "from_lane": key[1], "to_edge": key[2], "to_lane": key[3]}


def _connection_key_from_dict(row: Mapping[str, Any]) -> tuple[str, int, str, int]:
    return (
        str(row["from_edge"]),
        int(row["from_lane"]),
        str(row["to_edge"]),
        int(row["to_lane"]),
    )


def _format_connection_key(key: tuple[str, int, str, int]) -> str:
    return f"{key[0]}[{key[1]}]->{key[2]}[{key[3]}]"


def _result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, Mapping):
        return dict(result)
    if hasattr(result, "to_dict"):
        return dict(result.to_dict())
    if hasattr(result, "model_dump"):
        return dict(result.model_dump(mode="json"))
    raise TypeError(f"unsupported command result: {type(result).__name__}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
