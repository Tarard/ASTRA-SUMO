from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence
import xml.etree.ElementTree as ET

from .artifact_io import copy_file_atomic, write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .junction_aggregation import (
    audit_join_collapse_residuals,
    audit_join_output_presence,
)
from .junction_join_definition import build_junction_join_definition
from .osm_workflow import export_plain_net_for_teacher_guided_repair


MATERIALIZER_SCHEMA = "torii.hamburg-2394-compound-geometry/v1"
CLASSIFICATION_SCHEMA = "torii.composable-intersection-archetype/v2"

EXPECTED_CELL_NODES = frozenset(
    {
        "3847369287",
        "757036909",
        "76463166",
        "3847369288",
        "759714726",
        "2761334279",
        "757036795",
        "3847369285",
    }
)
EXPECTED_JOIN_GROUPS = frozenset(
    {
        frozenset({"3847369287", "757036909", "76463166"}),
        frozenset({"2761334279", "757036795"}),
    }
)
EXPECTED_OWNER_COMPONENTS = frozenset(
    {
        frozenset({"3847369287", "757036909", "76463166"}),
        frozenset({"3847369288"}),
        frozenset({"759714726"}),
        frozenset({"2761334279", "757036795"}),
        frozenset({"3847369285"}),
    }
)
EXPECTED_ABSORBED_EDGE_IDS = frozenset(
    {
        "-381540198#2",
        "381540198#2",
        "-9702435",
        "9702435",
        "9702432#1",
    }
)
EXPECTED_OSM_TLS_IDS = frozenset(
    {
        "2761334279",
        "3847369285",
        "3847369287",
        "3847369288",
        "757036795",
        "759714726",
    }
)


class HamburgCompoundGeometryError(ValueError):
    """Raised before materialization when frozen 2394 evidence is not exact."""


def classification_report_sha256(report: Mapping[str, Any] | Path) -> str:
    """Return the binding digest used by the materializer.

    Files are bound byte-for-byte.  In-memory mappings are bound through a
    canonical JSON representation, so a caller cannot accidentally validate a
    different report after the acceptance decision.
    """

    if isinstance(report, Path):
        return file_sha256(report.resolve(strict=True))
    if not isinstance(report, Mapping):
        raise HamburgCompoundGeometryError(
            "classification_report must be a JSON mapping or a JSON file path"
        )
    payload = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def materialize_hamburg_2394_compound_geometry_first_pass(
    *,
    source_net_file: Path,
    classification_report: Mapping[str, Any] | Path,
    accepted_classification_id: str,
    expected_source_sha256: str,
    expected_classification_sha256: str,
    output_dir: Path,
    netconvert_binary: str = "netconvert",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., Any] = run_command,
) -> dict[str, Any]:
    """Build a review-only PlainXML geometry pass for Hamburg node 2394.

    The function implements one deliberately narrow transition: two exact,
    classification-authorized SUMO node groups are joined and the obsolete OSM
    TLS bindings owned by the eight-node 2394 cell are retired.  It does not
    install an official controller or claim a promotion-ready digital twin.
    """

    source_net = source_net_file.resolve(strict=True)
    destination = output_dir.resolve()
    if destination == source_net.parent or _is_within(source_net, destination):
        raise HamburgCompoundGeometryError(
            "output_dir must not contain or overwrite the frozen source network"
        )
    _require_sha256(expected_source_sha256, "expected_source_sha256")
    _require_sha256(expected_classification_sha256, "expected_classification_sha256")
    source_hash_before = file_sha256(source_net)
    if source_hash_before.lower() != expected_source_sha256.lower():
        raise HamburgCompoundGeometryError(
            "frozen source network hash mismatch: "
            f"expected {expected_source_sha256.lower()}, got {source_hash_before}"
        )

    report, classification_basis = _load_classification_report(classification_report)
    classification_hash = classification_report_sha256(classification_report)
    if classification_hash.lower() != expected_classification_sha256.lower():
        raise HamburgCompoundGeometryError(
            "classification report hash mismatch: "
            f"expected {expected_classification_sha256.lower()}, got {classification_hash}"
        )
    acceptance = _validate_2394_classification(
        report,
        accepted_classification_id=accepted_classification_id,
    )
    source_inventory = _validate_frozen_source(source_net)

    destination.mkdir(parents=True, exist_ok=True)
    manifest_file = destination / "hamburg_2394_compound_geometry_manifest.json"
    candidate_net = destination / "hamburg_2394_compound_geometry_first_pass.net.xml"
    joined_junctions_file = destination / "hamburg_2394_joined_junctions.xml"
    command_file = destination / "hamburg_2394_geometry_netconvert.cmd.txt"

    export_dir = destination / "plain_export"
    export = export_plain_net_for_teacher_guided_repair(
        net_file=source_net,
        output_dir=export_dir,
        prefix="hamburg_2394_source_plain",
        netconvert_binary=netconvert_binary,
        timeout_seconds=timeout_seconds,
        command_runner=command_runner,
    )
    if export.get("status") != "pass":
        return _write_blocked_manifest(
            manifest_file,
            reason="plain_export_failed",
            source_net=source_net,
            source_hash=source_hash_before,
            classification_hash=classification_hash,
            classification_basis=classification_basis,
            accepted_classification_id=accepted_classification_id,
            acceptance=acceptance,
            export=export,
        )

    raw_nodes = _required_export_path(export, "raw_node_file")
    raw_edges = _required_export_path(export, "raw_edge_file")
    raw_connections = _required_export_path(export, "raw_connection_file")
    raw_tllogic = _required_export_path(export, "raw_tllogic_file")
    raw_types = _optional_export_path(export, "raw_type_file")
    absorbed_edges = _authorized_absorbed_edges(raw_edges)

    join_definition = build_junction_join_definition(
        [
            {
                "source": "accepted_hamburg_2394_classification",
                "candidate_id": f"{accepted_classification_id}:join:{index}",
                "decision": "join",
                "confidence": "confirmed_by_accepted_classification",
                "review_status": "confirmed",
                "node_ids": sorted(group, key=_natural_key),
                "reason": "exact local join group bound to accepted classification id and hash",
            }
            for index, group in enumerate(_ordered_groups(EXPECTED_JOIN_GROUPS), start=1)
        ],
        output_dir=destination / "join_definition",
        prefix="hamburg_2394_compound",
    )
    _validate_join_definition(join_definition)

    staged_dir = destination / "staged_plain"
    staged_dir.mkdir(parents=True, exist_ok=True)
    staged_nodes = staged_dir / "hamburg_2394_geometry.nod.xml"
    staged_edges = staged_dir / "hamburg_2394_geometry.edg.xml"
    staged_connections = staged_dir / "hamburg_2394_geometry.con.xml"
    staged_tllogic = staged_dir / "hamburg_2394_geometry.tll.xml"
    staged_types = staged_dir / "hamburg_2394_geometry.typ.xml"
    copy_file_atomic(raw_edges, staged_edges)
    if raw_types is not None:
        copy_file_atomic(raw_types, staged_types)
    node_demotion = _stage_nodes_with_join_and_tls_demotion(
        raw_nodes,
        Path(str(join_definition["nodes_patch_file"])),
        staged_nodes,
    )
    connection_demotion = _stage_connections_without_retired_tls(
        raw_connections,
        staged_connections,
    )
    tllogic_demotion = _stage_tllogic_without_retired_tls(
        raw_tllogic,
        staged_tllogic,
    )
    tls_demotion = {
        "status": "pass",
        "retired_tls_ids": sorted(EXPECTED_OSM_TLS_IDS, key=_natural_key),
        "node_policy": node_demotion,
        "connection_policy": connection_demotion,
        "tllogic_policy": tllogic_demotion,
        "official_tls_restoration": "not_run",
    }

    command = [
        netconvert_binary,
        "--node-files",
        str(staged_nodes),
        "--edge-files",
        str(staged_edges),
        "--connection-files",
        str(staged_connections),
        "--tllogic-files",
        str(staged_tllogic),
    ]
    if raw_types is not None:
        command.extend(["--type-files", str(staged_types)])
    command.extend(
        [
            "--no-turnarounds",
            "--offset.disable-normalization",
            "true",
            "--junctions.join-output",
            str(joined_junctions_file),
            "--output-file",
            str(candidate_net),
        ]
    )
    write_text_atomic(command_file, " ".join(command) + "\n")
    netconvert = _result_to_dict(
        command_runner(command, cwd=destination, timeout_seconds=timeout_seconds)
    )
    netconvert_pass = (
        netconvert.get("status") == "pass"
        and netconvert.get("returncode") in {0, None}
        and candidate_net.is_file()
    )

    source_hash_after = file_sha256(source_net)
    source_immutable = source_hash_after == source_hash_before
    if netconvert_pass:
        collapse = audit_join_collapse_residuals(
            candidate_net,
            [sorted(group, key=_natural_key) for group in EXPECTED_JOIN_GROUPS],
        )
        join_presence = audit_join_output_presence(
            candidate_net,
            [sorted(group, key=_natural_key) for group in EXPECTED_JOIN_GROUPS],
        )
        edge_scope = _audit_absorbed_edge_scope(source_net, candidate_net)
        tls_scope = _audit_tls_scope(
            source_inventory=source_inventory,
            candidate_net=candidate_net,
        )
        outside_connections = _audit_outside_cell_connections(source_net, candidate_net)
    else:
        collapse = {"status": "not_run"}
        join_presence = {"status": "not_run"}
        edge_scope = {"status": "not_run"}
        tls_scope = {"status": "not_run"}
        outside_connections = {"status": "not_run"}

    gates = {
        "classification_binding": "pass",
        "source_immutable": "pass" if source_immutable else "fail",
        "plain_export": "pass",
        "join_definition": "pass",
        "targeted_osm_tls_retirement": "pass",
        "netconvert": "pass" if netconvert_pass else "fail",
        "join_collapse": str(collapse.get("status", "fail")),
        "join_output_presence": str(join_presence.get("status", "fail")),
        "authorized_absorbed_edge_scope": str(edge_scope.get("status", "fail")),
        "tls_scope": str(tls_scope.get("status", "fail")),
        "outside_cell_connection_exactness": str(
            outside_connections.get("status", "fail")
        ),
        "official_tls_restoration": "not_run",
    }
    machine_pass = all(
        gates[name] == "pass"
        for name in (
            "classification_binding",
            "source_immutable",
            "plain_export",
            "join_definition",
            "targeted_osm_tls_retirement",
            "netconvert",
            "join_collapse",
            "join_output_presence",
            "authorized_absorbed_edge_scope",
            "tls_scope",
            "outside_cell_connection_exactness",
        )
    )
    manifest: dict[str, Any] = {
        "schema": MATERIALIZER_SCHEMA,
        "status": "review_ready" if machine_pass else "blocked",
        "claim_status": "geometry_first_pass_only",
        "automatic_promotion_gate": "blocked",
        "reason": "official_tls_restoration_and_render_review_are_outside_this_pass",
        "source": {
            "path": str(source_net),
            "expected_sha256": expected_source_sha256.lower(),
            "sha256_before": source_hash_before,
            "sha256_after": source_hash_after,
            "immutable": source_immutable,
        },
        "classification_acceptance": {
            "accepted_classification_id": accepted_classification_id,
            "expected_sha256": expected_classification_sha256.lower(),
            "actual_sha256": classification_hash,
            "digest_basis": classification_basis,
            **acceptance,
        },
        "authorized_join_groups": [
            sorted(group, key=_natural_key) for group in _ordered_groups(EXPECTED_JOIN_GROUPS)
        ],
        "preserved_owner_components": [
            sorted(group, key=_natural_key)
            for group in _ordered_groups(EXPECTED_OWNER_COMPONENTS)
        ],
        "authorized_absorbed_edges": absorbed_edges,
        "targeted_osm_tls_retirement": tls_demotion,
        "final_official_tls_restoration": {
            "status": "not_run",
            "required_next_stage": True,
        },
        "gates": gates,
        "audits": {
            "join_collapse": collapse,
            "join_output_presence": join_presence,
            "absorbed_edge_scope": edge_scope,
            "tls_scope": tls_scope,
            "outside_cell_connections": outside_connections,
        },
        "plain_export": export,
        "join_definition": join_definition,
        "netconvert": netconvert,
        "artifacts": {
            "candidate_net": _artifact(candidate_net),
            "manifest": str(manifest_file),
            "command": _artifact(command_file),
            "joined_junctions": _artifact(joined_junctions_file),
            "staged_nodes": _artifact(staged_nodes),
            "staged_edges": _artifact(staged_edges),
            "staged_connections": _artifact(staged_connections),
            "staged_tllogic": _artifact(staged_tllogic),
            "staged_types": _artifact(staged_types),
        },
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return manifest


def _load_classification_report(
    report_or_file: Mapping[str, Any] | Path,
) -> tuple[dict[str, Any], str]:
    if isinstance(report_or_file, Path):
        path = report_or_file.resolve(strict=True)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HamburgCompoundGeometryError(
                f"classification report is not valid UTF-8 JSON: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise HamburgCompoundGeometryError("classification report JSON must be an object")
        return value, "file_bytes"
    if not isinstance(report_or_file, Mapping):
        raise HamburgCompoundGeometryError(
            "classification_report must be a JSON mapping or a JSON file path"
        )
    return dict(report_or_file), "canonical_mapping_json"


def _validate_2394_classification(
    report: Mapping[str, Any],
    *,
    accepted_classification_id: str,
) -> dict[str, Any]:
    if not accepted_classification_id.strip():
        raise HamburgCompoundGeometryError("accepted_classification_id is required")
    required_equals = {
        "schema_id": CLASSIFICATION_SCHEMA,
        "junction_id": "2394",
        "prototype_id": "hamburg_2394_v1",
        "status": "review_required",
        "automatic_promotion_gate": "blocked",
        "classification_id": accepted_classification_id,
    }
    for field, expected in required_equals.items():
        actual = report.get(field)
        if actual != expected:
            raise HamburgCompoundGeometryError(
                f"classification {field} mismatch: expected {expected!r}, got {actual!r}"
            )

    classification = _mapping(report.get("classification"), "classification")
    expected_type_fields = {
        "base_skeleton": "T3",
        "physical_arrangement": "compound_candidate",
        "control_domain": "multi_owner_single_controller_candidate",
        "movement_graph_class": "complete_no_uturn_arm_graph_with_lane_adjacency",
        "family": "channelized_T3_family",
    }
    for field, expected in expected_type_fields.items():
        actual = classification.get(field)
        if actual != expected:
            raise HamburgCompoundGeometryError(
                f"classification.{field} mismatch: expected {expected!r}, got {actual!r}"
            )
    expected_channelization = {
        "distributed_stopline_markers",
        "lane_fanout",
        "merge_diverge",
        "pedestrian_crossing",
        "preserved_internal_connectors",
    }
    if _string_set(classification.get("channelization_modifiers"), "channelization_modifiers") != expected_channelization:
        raise HamburgCompoundGeometryError(
            "classification.channelization_modifiers does not match the frozen 2394 prototype"
        )
    if _string_set(
        classification.get("mode_and_restriction_modifiers"),
        "mode_and_restriction_modifiers",
    ) != {"bicycle", "motor_vehicle", "pedestrian"}:
        raise HamburgCompoundGeometryError(
            "classification.mode_and_restriction_modifiers does not match the frozen 2394 prototype"
        )

    counts = _mapping(report.get("counts"), "counts")
    expected_counts = {
        "raw_node_count": 8,
        "classification_join_group_count": 2,
        "physical_conflict_core_count": None,
        "owner_count_after_rebuild_candidate": 5,
        "controller_domain_count": 1,
    }
    for field, expected in expected_counts.items():
        actual = counts.get(field)
        if actual != expected or (isinstance(actual, bool) and isinstance(expected, int)):
            raise HamburgCompoundGeometryError(
                f"classification counts.{field} mismatch: expected {expected!r}, got {actual!r}"
            )
    if report.get("physical_conflict_core_status") != "unknown_pending_conflict_analysis":
        raise HamburgCompoundGeometryError(
            "2394 physical conflict-core status must remain unknown in this first pass"
        )
    if _string_set(report.get("official_intersection_parts"), "official_intersection_parts") != {"0"}:
        raise HamburgCompoundGeometryError("2394 official intersectionPart must be exactly {'0'}")

    hint = _mapping(report.get("execution_hint"), "execution_hint")
    if hint.get("strategy") != "local_join_candidates_preserve_split_shared_controller":
        raise HamburgCompoundGeometryError("classification execution strategy mismatch")
    if hint.get("classification_only") is not True:
        raise HamburgCompoundGeometryError("execution_hint.classification_only must be true")
    if hint.get("automatic_authorization") != "blocked":
        raise HamburgCompoundGeometryError("automatic geometry authorization must remain blocked")
    if hint.get("authorization_status") != "review_required":
        raise HamburgCompoundGeometryError("execution authorization_status must be review_required")
    if _string_set(hint.get("controller_domain_ids"), "controller_domain_ids") != {"2394"}:
        raise HamburgCompoundGeometryError("execution controller domain must be exactly 2394")

    join_groups = _group_set(hint.get("local_join_candidate_groups"), "local_join_candidate_groups")
    owner_components = _group_set(hint.get("preserve_owner_components"), "preserve_owner_components")
    if join_groups != EXPECTED_JOIN_GROUPS:
        raise HamburgCompoundGeometryError(
            f"2394 local join groups mismatch: expected {_display_groups(EXPECTED_JOIN_GROUPS)}, "
            f"got {_display_groups(join_groups)}"
        )
    if owner_components != EXPECTED_OWNER_COMPONENTS:
        raise HamburgCompoundGeometryError(
            f"2394 owner components mismatch: expected {_display_groups(EXPECTED_OWNER_COMPONENTS)}, "
            f"got {_display_groups(owner_components)}"
        )
    flattened = [node for group in owner_components for node in group]
    if len(flattened) != len(set(flattened)) or set(flattened) != EXPECTED_CELL_NODES:
        raise HamburgCompoundGeometryError(
            "2394 owner components must partition exactly the frozen eight-node cell"
        )
    return {
        "status": "pass",
        "prototype_id": "hamburg_2394_v1",
        "classification_id": accepted_classification_id,
        "human_acceptance_scope": "two_exact_local_join_groups_only",
        "classification_automatic_authorization": "blocked",
    }


def _validate_frozen_source(source_net: Path) -> dict[str, Any]:
    root = ET.parse(source_net).getroot()
    junction_ids = {
        junction.attrib.get("id", "")
        for junction in root.findall("junction")
        if junction.attrib.get("id", "") and not junction.attrib.get("id", "").startswith(":")
    }
    missing_nodes = sorted(EXPECTED_CELL_NODES - junction_ids, key=_natural_key)
    if missing_nodes:
        raise HamburgCompoundGeometryError(
            f"frozen source is missing 2394 cell nodes: {missing_nodes}"
        )
    absorbed = _authorized_absorbed_edges_from_root(root)
    actual_absorbed_ids = {row["edge_id"] for row in absorbed}
    if actual_absorbed_ids != EXPECTED_ABSORBED_EDGE_IDS:
        raise HamburgCompoundGeometryError(
            "frozen source join-internal edge set mismatch: "
            f"expected {sorted(EXPECTED_ABSORBED_EDGE_IDS)}, got {sorted(actual_absorbed_ids)}"
        )
    source_tls_ids = {
        element.attrib.get("id", "")
        for element in root.findall("tlLogic")
        if element.attrib.get("id", "")
    }
    missing_tls = sorted(EXPECTED_OSM_TLS_IDS - source_tls_ids, key=_natural_key)
    if missing_tls:
        raise HamburgCompoundGeometryError(
            f"frozen source is missing expected 2394 OSM TLS programs: {missing_tls}"
        )
    target_binding_owners: dict[str, set[str]] = {
        tls_id: set() for tls_id in EXPECTED_OSM_TLS_IDS
    }
    for connection in root.findall("connection"):
        tls_id = connection.attrib.get("tl", "")
        if tls_id not in EXPECTED_OSM_TLS_IDS:
            continue
        owner = _via_owner(connection.attrib.get("via", ""))
        if not owner or owner not in EXPECTED_CELL_NODES:
            raise HamburgCompoundGeometryError(
                f"target OSM TLS {tls_id!r} controls a connection outside the 2394 cell"
            )
        target_binding_owners[tls_id].add(owner)
    empty_bindings = sorted(
        [tls_id for tls_id, owners in target_binding_owners.items() if not owners],
        key=_natural_key,
    )
    if empty_bindings:
        raise HamburgCompoundGeometryError(
            f"expected 2394 OSM TLS programs have no source bindings: {empty_bindings}"
        )
    return {
        "source_tls_ids": sorted(source_tls_ids, key=_natural_key),
        "target_tls_binding_owners": {
            key: sorted(value, key=_natural_key)
            for key, value in sorted(target_binding_owners.items(), key=lambda item: _natural_key(item[0]))
        },
    }


def _authorized_absorbed_edges(edge_file: Path) -> list[dict[str, Any]]:
    rows = _authorized_absorbed_edges_from_root(ET.parse(edge_file).getroot())
    actual_ids = {row["edge_id"] for row in rows}
    if actual_ids != EXPECTED_ABSORBED_EDGE_IDS:
        raise HamburgCompoundGeometryError(
            "PlainXML export changed the authorized absorbed edge set: "
            f"expected {sorted(EXPECTED_ABSORBED_EDGE_IDS)}, got {sorted(actual_ids)}"
        )
    return rows


def _authorized_absorbed_edges_from_root(root: ET.Element) -> list[dict[str, Any]]:
    group_index = {
        node_id: index
        for index, group in enumerate(_ordered_groups(EXPECTED_JOIN_GROUPS), start=1)
        for node_id in group
    }
    rows: list[dict[str, Any]] = []
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function") in {
            "internal",
            "crossing",
            "walkingarea",
        }:
            continue
        from_id = edge.attrib.get("from", "")
        to_id = edge.attrib.get("to", "")
        if group_index.get(from_id) is None or group_index.get(from_id) != group_index.get(to_id):
            continue
        rows.append(
            {
                "edge_id": edge_id,
                "from_junction_id": from_id,
                "to_junction_id": to_id,
                "authorized_join_group_index": group_index[from_id],
                "lane_count": len(edge.findall("lane")),
                "type": edge.attrib.get("type", ""),
            }
        )
    rows.sort(key=lambda row: _natural_key(str(row["edge_id"])))
    return rows


def _validate_join_definition(report: Mapping[str, Any]) -> None:
    if report.get("status") != "pass" or report.get("explicit_join_count") != 2:
        raise HamburgCompoundGeometryError("junction join helper did not emit exactly two joins")
    if report.get("join_exclude_count") != 0 or report.get("needs_map_review_count") != 0:
        raise HamburgCompoundGeometryError("junction join helper downgraded an accepted exact join")
    actual = _group_set(
        [
            row.get("node_ids", [])
            for row in report.get("records", [])
            if isinstance(row, Mapping) and row.get("action") == "join"
        ],
        "join_definition.records",
    )
    if actual != EXPECTED_JOIN_GROUPS:
        raise HamburgCompoundGeometryError("junction join helper output changed the exact groups")


def _stage_nodes_with_join_and_tls_demotion(
    raw_nodes: Path,
    join_patch: Path,
    output_file: Path,
) -> dict[str, Any]:
    tree = ET.parse(raw_nodes)
    root = tree.getroot()
    if root.tag != "nodes":
        raise HamburgCompoundGeometryError(f"plain node root must be <nodes>, got <{root.tag}>")
    nodes = {
        node.attrib.get("id", ""): node
        for node in root.findall("node")
        if node.attrib.get("id", "")
    }
    missing = sorted(EXPECTED_CELL_NODES - set(nodes), key=_natural_key)
    if missing:
        raise HamburgCompoundGeometryError(f"PlainXML node export is missing cell nodes: {missing}")
    demoted: list[str] = []
    for tls_id in sorted(EXPECTED_OSM_TLS_IDS, key=_natural_key):
        node = nodes[tls_id]
        node.set("type", "priority")
        for attribute in ("tl", "tlType", "tlLayout", "controlledInner"):
            node.attrib.pop(attribute, None)
        demoted.append(tls_id)
    join_root = ET.parse(join_patch).getroot()
    joins = [copy.deepcopy(element) for element in join_root.findall("join")]
    if _group_set(
        [element.attrib.get("nodes", "").split() for element in joins],
        "join_patch",
    ) != EXPECTED_JOIN_GROUPS:
        raise HamburgCompoundGeometryError("join patch changed after definition validation")
    root.extend(joins)
    ET.indent(tree, space="    ")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "demoted_node_ids": demoted,
        "appended_join_count": len(joins),
    }


def _stage_connections_without_retired_tls(
    raw_connections: Path,
    output_file: Path,
) -> dict[str, Any]:
    tree = ET.parse(raw_connections)
    root = tree.getroot()
    changed = 0
    for connection in root.findall("connection"):
        if connection.attrib.get("tl", "") not in EXPECTED_OSM_TLS_IDS:
            continue
        for attribute in ("tl", "linkIndex", "linkIndex2"):
            connection.attrib.pop(attribute, None)
        changed += 1
    stale = sorted(
        {
            element.attrib.get("tl", "")
            for element in root.findall("connection")
            if element.attrib.get("tl", "") in EXPECTED_OSM_TLS_IDS
        },
        key=_natural_key,
    )
    if stale:
        raise HamburgCompoundGeometryError(f"retired TLS remains in plain connections: {stale}")
    ET.indent(tree, space="    ")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {"status": "pass", "removed_legacy_tls_binding_count": changed}


def _stage_tllogic_without_retired_tls(
    raw_tllogic: Path,
    output_file: Path,
) -> dict[str, Any]:
    tree = ET.parse(raw_tllogic)
    root = tree.getroot()
    if root.tag not in {"tlLogics", "additional"}:
        raise HamburgCompoundGeometryError(
            f"plain tlLogic root must be <tlLogics> or <additional>, got <{root.tag}>"
        )
    removed_programs: list[str] = []
    removed_bindings = 0
    for child in list(root):
        if child.tag == "tlLogic" and child.attrib.get("id", "") in EXPECTED_OSM_TLS_IDS:
            removed_programs.append(child.attrib.get("id", ""))
            root.remove(child)
        elif child.tag == "connection" and child.attrib.get("tl", "") in EXPECTED_OSM_TLS_IDS:
            removed_bindings += 1
            root.remove(child)
    if set(removed_programs) != EXPECTED_OSM_TLS_IDS:
        raise HamburgCompoundGeometryError(
            "PlainXML TLS export did not contain exactly the expected 2394 OSM programs: "
            f"got {sorted(removed_programs, key=_natural_key)}"
        )
    stale = sorted(
        {
            child.attrib.get("id", "")
            for child in root.findall("tlLogic")
            if child.attrib.get("id", "") in EXPECTED_OSM_TLS_IDS
        }
        | {
            child.attrib.get("tl", "")
            for child in root.findall("connection")
            if child.attrib.get("tl", "") in EXPECTED_OSM_TLS_IDS
        },
        key=_natural_key,
    )
    if stale:
        raise HamburgCompoundGeometryError(f"retired TLS remains in staged tllogic: {stale}")
    ET.indent(tree, space="    ")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "removed_program_ids": sorted(removed_programs, key=_natural_key),
        "removed_binding_count": removed_bindings,
    }


def _audit_absorbed_edge_scope(source_net: Path, candidate_net: Path) -> dict[str, Any]:
    source_ids = _plain_edge_ids(ET.parse(source_net).getroot())
    candidate_ids = _plain_edge_ids(ET.parse(candidate_net).getroot())
    removed = source_ids - candidate_ids
    added = candidate_ids - source_ids
    unexpected_removed = removed - EXPECTED_ABSORBED_EDGE_IDS
    missing_absorption = EXPECTED_ABSORBED_EDGE_IDS - removed
    return {
        "status": (
            "pass"
            if not unexpected_removed and not missing_absorption and not added
            else "fail"
        ),
        "authorized_absorbed_edge_ids": sorted(EXPECTED_ABSORBED_EDGE_IDS),
        "actual_removed_edge_ids": sorted(removed),
        "unexpected_removed_edge_ids": sorted(unexpected_removed),
        "authorized_edges_not_absorbed": sorted(missing_absorption),
        "unexpected_added_plain_edge_ids": sorted(added),
    }


def _audit_tls_scope(
    *,
    source_inventory: Mapping[str, Any],
    candidate_net: Path,
) -> dict[str, Any]:
    root = ET.parse(candidate_net).getroot()
    candidate_tls_ids = {
        element.attrib.get("id", "")
        for element in root.findall("tlLogic")
        if element.attrib.get("id", "")
    }
    stale_programs = candidate_tls_ids & EXPECTED_OSM_TLS_IDS
    stale_bindings = sorted(
        {
            connection.attrib.get("tl", "")
            for connection in root.findall("connection")
            if connection.attrib.get("tl", "") in EXPECTED_OSM_TLS_IDS
        },
        key=_natural_key,
    )
    source_non_target = set(source_inventory["source_tls_ids"]) - EXPECTED_OSM_TLS_IDS
    missing_non_target = source_non_target - candidate_tls_ids
    added = candidate_tls_ids - source_non_target
    return {
        "status": (
            "pass"
            if not stale_programs
            and not stale_bindings
            and not missing_non_target
            and not added
            else "fail"
        ),
        "retired_program_ids_still_present": sorted(stale_programs, key=_natural_key),
        "retired_binding_ids_still_present": stale_bindings,
        "missing_non_target_source_tls_ids": sorted(missing_non_target, key=_natural_key),
        "unexpected_candidate_tls_ids": sorted(added, key=_natural_key),
    }


def _audit_outside_cell_connections(
    source_net: Path,
    candidate_net: Path,
) -> dict[str, Any]:
    source_root = ET.parse(source_net).getroot()
    candidate_root = ET.parse(candidate_net).getroot()
    cell_incident_edges = {
        edge.attrib.get("id", "")
        for edge in source_root.findall("edge")
        if edge.attrib.get("id", "")
        and (
            edge.attrib.get("from", "") in EXPECTED_CELL_NODES
            or edge.attrib.get("to", "") in EXPECTED_CELL_NODES
        )
    }

    def signatures(root: ET.Element) -> set[tuple[str, ...]]:
        result: set[tuple[str, ...]] = set()
        for connection in root.findall("connection"):
            from_edge = connection.attrib.get("from", "")
            to_edge = connection.attrib.get("to", "")
            if (
                not from_edge
                or not to_edge
                or from_edge.startswith(":")
                or to_edge.startswith(":")
                or from_edge in cell_incident_edges
                or to_edge in cell_incident_edges
            ):
                continue
            result.add(
                (
                    from_edge,
                    connection.attrib.get("fromLane", ""),
                    to_edge,
                    connection.attrib.get("toLane", ""),
                    connection.attrib.get("tl", ""),
                    connection.attrib.get("linkIndex", ""),
                    connection.attrib.get("dir", ""),
                )
            )
        return result

    source_signatures = signatures(source_root)
    candidate_signatures = signatures(candidate_root)
    missing = source_signatures - candidate_signatures
    added = candidate_signatures - source_signatures
    return {
        "status": "pass" if not missing and not added else "fail",
        "scope_policy": "connections with both normal edges outside the eight-node 2394 cell",
        "source_connection_count": len(source_signatures),
        "candidate_connection_count": len(candidate_signatures),
        "missing_source_signatures": [list(item) for item in sorted(missing)],
        "unexpected_candidate_signatures": [list(item) for item in sorted(added)],
        "no_turnarounds_outside_cell_delta": bool(missing or added),
    }


def _write_blocked_manifest(
    manifest_file: Path,
    *,
    reason: str,
    source_net: Path,
    source_hash: str,
    classification_hash: str,
    classification_basis: str,
    accepted_classification_id: str,
    acceptance: Mapping[str, Any],
    export: Mapping[str, Any],
) -> dict[str, Any]:
    source_hash_after = file_sha256(source_net)
    manifest = {
        "schema": MATERIALIZER_SCHEMA,
        "status": "blocked",
        "claim_status": "construction_invalid",
        "automatic_promotion_gate": "blocked",
        "reason": reason,
        "source": {
            "path": str(source_net),
            "sha256_before": source_hash,
            "sha256_after": source_hash_after,
            "immutable": source_hash_after == source_hash,
        },
        "classification_acceptance": {
            "accepted_classification_id": accepted_classification_id,
            "actual_sha256": classification_hash,
            "digest_basis": classification_basis,
            **dict(acceptance),
        },
        "gates": {
            "classification_binding": "pass",
            "source_immutable": "pass" if source_hash_after == source_hash else "fail",
            "plain_export": "fail",
            "netconvert": "not_run",
            "official_tls_restoration": "not_run",
        },
        "plain_export": dict(export),
        "final_official_tls_restoration": {
            "status": "not_run",
            "required_next_stage": True,
        },
        "artifacts": {"manifest": str(manifest_file)},
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return manifest


def _required_export_path(export: Mapping[str, Any], field: str) -> Path:
    value = str(export.get(field, ""))
    if not value:
        raise HamburgCompoundGeometryError(f"plain export did not declare {field}")
    path = Path(value).resolve()
    if not path.is_file():
        raise HamburgCompoundGeometryError(f"plain export {field} does not exist: {path}")
    return path


def _optional_export_path(export: Mapping[str, Any], field: str) -> Path | None:
    value = str(export.get(field, ""))
    if not value:
        return None
    path = Path(value).resolve()
    if not path.is_file():
        raise HamburgCompoundGeometryError(f"plain export {field} does not exist: {path}")
    return path


def _artifact(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "sha256": file_sha256(path) if path.is_file() else None,
    }


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HamburgCompoundGeometryError(f"{field} must be an object")
    return value


def _string_set(value: Any, field: str) -> set[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise HamburgCompoundGeometryError(f"{field} must be a list")
    result: set[str] = set()
    for item in value:
        if item is None or isinstance(item, (Mapping, Sequence)) and not isinstance(item, str):
            raise HamburgCompoundGeometryError(f"{field} contains a non-string value")
        token = str(item).strip()
        if not token:
            raise HamburgCompoundGeometryError(f"{field} contains an empty value")
        if token in result:
            raise HamburgCompoundGeometryError(f"{field} contains duplicate value {token!r}")
        result.add(token)
    return result


def _group_set(value: Any, field: str) -> frozenset[frozenset[str]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise HamburgCompoundGeometryError(f"{field} must be a list of node lists")
    groups: list[frozenset[str]] = []
    for index, group in enumerate(value):
        nodes = _string_set(group, f"{field}[{index}]")
        if not nodes:
            raise HamburgCompoundGeometryError(f"{field}[{index}] must not be empty")
        frozen = frozenset(nodes)
        if frozen in groups:
            raise HamburgCompoundGeometryError(f"{field} contains duplicate groups")
        groups.append(frozen)
    return frozenset(groups)


def _ordered_groups(
    groups: frozenset[frozenset[str]],
) -> list[frozenset[str]]:
    return sorted(
        groups,
        key=lambda group: tuple(_natural_key(item) for item in sorted(group, key=_natural_key)),
    )


def _display_groups(groups: frozenset[frozenset[str]]) -> list[list[str]]:
    return [sorted(group, key=_natural_key) for group in _ordered_groups(groups)]


def _plain_edge_ids(root: ET.Element) -> set[str]:
    return {
        edge.attrib.get("id", "")
        for edge in root.findall("edge")
        if edge.attrib.get("id", "")
        and not edge.attrib.get("id", "").startswith(":")
        and edge.attrib.get("function", "") not in {"internal", "crossing", "walkingarea"}
    }


def _via_owner(via: str) -> str:
    if not via.startswith(":"):
        return ""
    token = via[1:].split("_", 1)[0]
    return token


def _result_to_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        payload = dict(result.to_dict())
    elif isinstance(result, Mapping):
        payload = dict(result)
    else:
        payload = {
            "status": getattr(result, "status", "fail"),
            "returncode": getattr(result, "returncode", None),
        }
    if "status" not in payload:
        payload["status"] = "pass" if payload.get("returncode") == 0 else "fail"
    return payload


def _require_sha256(value: str, field: str) -> None:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(value)):
        raise HamburgCompoundGeometryError(f"{field} must be a 64-character SHA-256")


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _natural_key(value: str) -> tuple[object, ...]:
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", str(value))
    )
