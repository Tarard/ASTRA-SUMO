from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
import string
import threading
from typing import Any, Literal
from uuid import uuid4
import xml.etree.ElementTree as ET

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.connection_mode_audit import build_connection_mode_regression_audit
from torii_sumo.core.netedit import NeteditTargetSession
from torii_sumo.core.sumo_commands import run_sumo_load_audit
from torii_sumo.core.surface_overlap_audit import (
    audit_sumo_lane_junction_surface_overlaps,
    compare_sumo_surface_overlap_reports,
)


_ACTIVE_SESSION: NeteditTargetSession | None = None
_ACTIVE_SESSION_ID = ""
_SESSION_LOCK = threading.Lock()
NeteditOperation = Literal["open", "observe", "act", "finalize", "abort"]
NeteditAction = Literal[
    "click",
    "drag",
    "inspect_mode",
    "connection_mode",
    "delete_mode",
    "edge_mode",
    "move_mode",
    "select_mode",
    "tls_mode",
    "recompute",
    "join_selected_junctions",
    "accept",
    "cancel",
    "undo",
    "redo",
]
NeteditObjectType = Literal["junction", "edge", "lane", "connection", "tlLogic"]
_SHORTCUTS: dict[str, tuple[int, tuple[int, ...]]] = {
    "inspect_mode": (ord("I"), ()),
    "connection_mode": (ord("C"), ()),
    "delete_mode": (ord("D"), ()),
    "edge_mode": (ord("E"), ()),
    "move_mode": (ord("M"), ()),
    "select_mode": (ord("S"), ()),
    "tls_mode": (ord("T"), ()),
    "recompute": (0x74, ()),  # F5
    "join_selected_junctions": (0x76, ()),  # F7
    "accept": (0x0D, ()),
    "cancel": (0x1B, ()),
    "undo": (ord("Z"), (0x11,)),
    "redo": (ord("Y"), (0x11,)),
}


def _blocked(operation: str, reason: str, session_id: str | None) -> dict[str, Any]:
    return {
        "schema": "torii.sumo-netedit-session/v1",
        "status": "blocked",
        "operation": operation,
        "session_id": session_id or "",
        "reason": reason,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": "NetEdit GUI actions create diagnostic candidate evidence only.",
    }


def _required(value: str | None, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value


def _sha256(value: str | None, name: str) -> str:
    digest = _required(value, name).lower()
    if len(digest) != 64 or any(character not in string.hexdigits for character in digest):
        raise ValueError(f"{name} must be a 64-character hexadecimal SHA-256")
    return digest


def _core_action(
    action: NeteditAction | None,
    *,
    expected_screenshot_sha256: str | None,
    x: int | None,
    y: int | None,
    to_x: int | None,
    to_y: int | None,
) -> dict[str, Any]:
    name = _required(action, "action")
    screenshot_hash = _sha256(expected_screenshot_sha256, "expected_screenshot_sha256")
    if name == "click":
        return {"type": "click", "x": x, "y": y, "expected_screenshot_sha256": screenshot_hash}
    if name == "drag":
        return {
            "type": "drag",
            "x": x,
            "y": y,
            "to_x": to_x,
            "to_y": to_y,
            "expected_screenshot_sha256": screenshot_hash,
        }
    try:
        virtual_key, modifier_keys = _SHORTCUTS[name]
    except KeyError as exc:
        allowed = ", ".join(("click", "drag", *_SHORTCUTS))
        raise ValueError(f"unsupported NetEdit action {name!r}; expected one of: {allowed}") from exc
    return {
        "type": "key",
        "virtual_key": virtual_key,
        "modifier_keys": list(modifier_keys),
        "expected_screenshot_sha256": screenshot_hash,
    }


def _persisted_scene(
    candidate: Path,
    *,
    object_type: NeteditObjectType | None,
    object_id: str | None,
) -> dict[str, Any]:
    payload = candidate.read_bytes()
    root = ET.fromstring(payload)
    collections = {
        "junction": root.findall("junction"),
        "edge": root.findall("edge"),
        "lane": root.findall(".//lane"),
        "connection": root.findall("connection"),
        "tlLogic": root.findall("tlLogic"),
    }
    report: dict[str, Any] = {
        "scope": "persisted_candidate_on_disk",
        "candidate_net_file": str(candidate),
        "candidate_sha256": hashlib.sha256(payload).hexdigest(),
        "counts": {name: len(elements) for name, elements in collections.items()},
        "live_unsaved_gui_state_included": False,
    }
    if bool(object_type) != bool(object_id):
        raise ValueError("object_type and object_id must be supplied together")
    if not object_type:
        return report
    if object_type not in collections:
        raise ValueError("object_type must be junction, edge, lane, connection, or tlLogic")
    if object_type == "connection":
        parts = object_id.split("|")
        if len(parts) != 4:
            raise ValueError("connection object_id must be from|fromLane|to|toLane")
        matches = [
            item
            for item in collections[object_type]
            if tuple(item.get(key, "") for key in ("from", "fromLane", "to", "toLane")) == tuple(parts)
        ]
    elif object_type == "tlLogic":
        parts = object_id.split("|")
        if len(parts) == 1:
            matches = [item for item in collections[object_type] if item.get("id") == object_id]
        elif len(parts) == 2:
            matches = [
                item
                for item in collections[object_type]
                if (item.get("id"), item.get("programID", "")) == tuple(parts)
            ]
        else:
            raise ValueError("tlLogic object_id must be id or id|programID")
    else:
        matches = [item for item in collections[object_type] if item.get("id") == object_id]
    if len(matches) != 1:
        raise ValueError(f"persisted {object_type} object must match exactly once: {object_id}")
    element = matches[0]
    report["object"] = {
        "type": object_type,
        "id": object_id,
        "attributes": dict(element.attrib),
        "children": [{"tag": child.tag, "attributes": dict(child.attrib)} for child in element],
    }
    return report


def _audit_error(schema: str, exc: Exception) -> dict[str, Any]:
    return {
        "schema": schema,
        "status": "fail",
        "error": f"{type(exc).__name__}: {exc}",
        "automatic_promotion_gate": "blocked",
    }


def _audit_summary(report: dict[str, Any], *fields: str) -> dict[str, Any]:
    keys = (
        "schema",
        "status",
        "error",
        "reason",
        "report_file",
        "manifest_file",
        "review_overlay_file",
        *fields,
    )
    summary = {key: report[key] for key in keys if key in report}
    for key in ("report_file", "manifest_file", "review_overlay_file"):
        value = summary.get(key)
        if isinstance(value, str) and Path(value).is_file():
            summary[f"{key}_sha256"] = hashlib.sha256(Path(value).read_bytes()).hexdigest()
    return summary


def _retryable_act_error(exc: Exception) -> bool:
    if isinstance(exc, ValueError):
        return True
    message = str(exc)
    return any(
        text in message
        for text in (
            "previous NetEdit screenshot artifact no longer matches",
            "NetEdit viewport or protected action target changed",
            "NetEdit observation is not a usable rendered client viewport",
            "NetEdit screenshot and action coordinates are not in the same client space",
            "physical keyboard or mouse input is active",
        )
    )


def _canonical_xml(element: ET.Element) -> tuple[Any, ...]:
    return (
        element.tag,
        tuple(sorted((str(key), str(value)) for key, value in element.attrib.items())),
        tuple(_canonical_xml(child) for child in element),
    )


def _scope_preservation_gate(session: NeteditTargetSession) -> dict[str, Any]:
    """Require exact preservation outside the junction scope declared before editing."""

    source_root = ET.parse(session.source).getroot()
    candidate_root = ET.parse(session.candidate).getroot()
    source_targets = set(session.target_source_junction_ids)
    candidate_targets = set(session.target_candidate_junction_ids)

    def network_edges(root: ET.Element) -> dict[str, ET.Element]:
        return {
            str(edge.get("id")): edge
            for edge in root.findall("edge")
            if edge.get("id")
        }

    source_edges = network_edges(source_root)
    candidate_edges = network_edges(candidate_root)
    source_incident = {
        edge_id
        for edge_id, edge in source_edges.items()
        if not edge_id.startswith(":")
        and (edge.get("from") in source_targets or edge.get("to") in source_targets)
    }

    def target_internal_edges(
        root: ET.Element,
        edges: dict[str, ET.Element],
        targets: set[str],
    ) -> set[str]:
        lane_to_edge = {
            str(lane.get("id")): edge_id
            for edge_id, edge in edges.items()
            if edge_id.startswith(":")
            for lane in edge.findall("lane")
            if lane.get("id")
        }
        target_edges: set[str] = set()
        for junction in root.findall("junction"):
            junction_id = str(junction.get("id", ""))
            if junction_id not in targets:
                continue
            for lane_id in str(junction.get("intLanes", "")).split():
                edge_id = lane_to_edge.get(lane_id)
                if edge_id is None or "_" not in edge_id:
                    continue
                # SUMO internal edge ids are :<junction-id>_<link-index>.
                # Parse from the right so junction ids containing underscores
                # remain exact, and never trust candidate intLanes alone to
                # expand the predeclared target scope.
                owner_id = edge_id[1:].rsplit("_", 1)[0]
                if owner_id == junction_id:
                    target_edges.add(edge_id)
        return target_edges

    source_internal = target_internal_edges(source_root, source_edges, source_targets)
    candidate_internal = target_internal_edges(
        candidate_root,
        candidate_edges,
        candidate_targets,
    )
    # External scope is frozen exclusively from the source network.  Candidate
    # topology may replace target-owned internal edges, but it may never make a
    # previously unrelated external edge "incident" and thereby exempt it.
    allowed_edge_ids = source_incident | source_internal | candidate_internal

    def outside_junctions(root: ET.Element, targets: set[str]) -> dict[str, tuple[Any, ...]]:
        return {
            str(junction.get("id")): _canonical_xml(junction)
            for junction in root.findall("junction")
            if junction.get("id")
            and not str(junction.get("id")).startswith(":")
            and junction.get("id") not in targets
        }

    def outside_edges(edges: dict[str, ET.Element]) -> dict[str, tuple[Any, ...]]:
        return {
            edge_id: _canonical_xml(edge)
            for edge_id, edge in edges.items()
            if edge_id not in allowed_edge_ids
        }

    def connection_is_in_scope(connection: ET.Element) -> bool:
        edge_ids = (str(connection.get("from", "")), str(connection.get("to", "")))
        return any(edge_id in allowed_edge_ids for edge_id in edge_ids)

    def outside_connections(root: ET.Element) -> Counter[tuple[Any, ...]]:
        return Counter(
            _canonical_xml(connection)
            for connection in root.findall("connection")
            if not connection_is_in_scope(connection)
        )

    affected_tls_ids = source_targets | candidate_targets
    affected_tls_ids.update(
        str(connection.get("tl"))
        for connection in source_root.findall("connection")
        if connection.get("tl") and connection_is_in_scope(connection)
    )

    def outside_tls(root: ET.Element) -> Counter[tuple[Any, ...]]:
        return Counter(
            _canonical_xml(logic)
            for logic in root.findall("tlLogic")
            if logic.get("id") not in affected_tls_ids
        )

    def other_top_level(root: ET.Element) -> Counter[tuple[Any, ...]]:
        return Counter(
            _canonical_xml(element)
            for element in root
            if element.tag not in {"edge", "junction", "connection", "tlLogic"}
        )

    source_parts = {
        "junctions": outside_junctions(source_root, source_targets),
        "edges": outside_edges(source_edges),
        "connections": outside_connections(source_root),
        "tls": outside_tls(source_root),
        "other_top_level": other_top_level(source_root),
    }
    candidate_parts = {
        "junctions": outside_junctions(candidate_root, candidate_targets),
        "edges": outside_edges(candidate_edges),
        "connections": outside_connections(candidate_root),
        "tls": outside_tls(candidate_root),
        "other_top_level": other_top_level(candidate_root),
    }
    changed_parts = sorted(
        name for name in source_parts if source_parts[name] != candidate_parts[name]
    )
    status = "pass" if not changed_parts else "fail"
    return {
        "schema": "torii.netedit-scope-preservation-gate/v1",
        "status": status,
        "reason": (
            "all persisted network state outside the declared junction scope is unchanged"
            if status == "pass"
            else "persisted network state changed outside the declared junction scope"
        ),
        "changed_parts": changed_parts,
        "source_target_junction_ids": sorted(source_targets),
        "candidate_target_junction_ids": sorted(candidate_targets),
        "allowed_incident_edge_ids": sorted(allowed_edge_ids),
        "source_incident_external_edge_ids": sorted(source_incident),
        "source_target_internal_edge_ids": sorted(source_internal),
        "candidate_target_internal_edge_ids": sorted(candidate_internal),
        "source_outside_counts": {name: len(value) for name, value in source_parts.items()},
        "candidate_outside_counts": {name: len(value) for name, value in candidate_parts.items()},
    }


def _declared_junction_identity_gate(session: NeteditTargetSession) -> dict[str, Any]:
    join_requested = any(
        step.get("kind") == "act"
        and step.get("detail", {}).get("type") == "key"
        and step.get("detail", {}).get("virtual_key") == 0x76
        for step in getattr(session, "steps", ())
    )
    source_root = ET.parse(session.source).getroot()
    candidate_root = ET.parse(session.candidate).getroot()

    def ids(root: ET.Element) -> set[str]:
        return {
            str(junction.get("id"))
            for junction in root.findall("junction")
            if junction.get("id") and not str(junction.get("id")).startswith(":")
        }

    source_ids = ids(source_root)
    candidate_ids = ids(candidate_root)
    removed = sorted(source_ids - candidate_ids)
    added = sorted(candidate_ids - source_ids)
    expected_removed = sorted(getattr(session, "target_source_junction_ids", ()))
    expected_added = sorted(getattr(session, "target_candidate_junction_ids", ()))
    if not join_requested:
        status = "fail" if removed or added else "not_applicable"
        return {
            "schema": "torii.netedit-junction-identity-gate/v1",
            "status": status,
            "reason": (
                "external junction identities changed without a declared F7 join"
                if status == "fail"
                else "session did not change external junction identities"
            ),
            "expected_removed_junction_ids": [],
            "observed_removed_junction_ids": removed,
            "expected_added_junction_ids": [],
            "observed_added_junction_ids": added,
        }
    status = (
        "pass"
        if expected_removed
        and expected_added
        and removed == expected_removed
        and added == expected_added
        else "fail"
    )
    return {
        "schema": "torii.netedit-junction-identity-gate/v1",
        "status": status,
        "reason": (
            "F7 junction identity delta matches the scope fixed before editing"
            if status == "pass"
            else "F7 junction identity delta differs from the scope fixed before editing"
        ),
        "expected_removed_junction_ids": expected_removed,
        "observed_removed_junction_ids": removed,
        "expected_added_junction_ids": expected_added,
        "observed_added_junction_ids": added,
    }


def _finalize_audits(session: NeteditTargetSession) -> dict[str, Any]:
    destination = session.output_dir / "finalize-audits"
    destination.mkdir(parents=True, exist_ok=True)
    source_hash_before = hashlib.sha256(session.source.read_bytes()).hexdigest()
    candidate_hash_before = hashlib.sha256(session.candidate.read_bytes()).hexdigest()
    try:
        sumo_load = run_sumo_load_audit(
            net_file=session.candidate,
            output_dir=destination / "sumo-load",
        )
    except Exception as exc:
        sumo_load = _audit_error("torii.sumo-load-audit/v1", exc)

    try:
        source_surface = audit_sumo_lane_junction_surface_overlaps(
            session.source,
            report_file=destination / "surface-source.json",
        )
        candidate_surface = audit_sumo_lane_junction_surface_overlaps(
            session.candidate,
            report_file=destination / "surface-candidate.json",
        )
    except Exception as exc:
        source_surface = _audit_error("torii.sumo-surface-overlap-audit/v1", exc)
        candidate_surface = _audit_error("torii.sumo-surface-overlap-audit/v1", exc)

    focus = tuple(
        dict.fromkeys(
            (*session.target_source_junction_ids, *session.target_candidate_junction_ids)
        )
    )
    if source_surface.get("error") or candidate_surface.get("error"):
        surface_comparison = {
            "schema": "torii.sumo-surface-overlap-comparison/v1",
            "status": "fail",
            "reason": "source or candidate surface audit failed before comparison",
            "focus_junction_ids": list(focus),
            "automatic_promotion_gate": "blocked",
        }
    elif focus:
        try:
            surface_comparison = compare_sumo_surface_overlap_reports(
                source_surface,
                candidate_surface,
                focus_junction_ids=focus,
                report_file=destination / "surface-comparison.json",
            )
        except Exception as exc:
            surface_comparison = _audit_error(
                "torii.sumo-surface-overlap-comparison/v1",
                exc,
            )
    else:
        surface_comparison = {
            "schema": "torii.sumo-surface-overlap-comparison/v1",
            "status": "not_run",
            "reason": (
                "declare target_source_junction_ids and/or target_candidate_junction_ids "
                "when opening the session to enable bounded surface comparison"
            ),
            "focus_junction_ids": list(focus),
            "automatic_promotion_gate": "blocked",
        }

    try:
        connection_mode = build_connection_mode_regression_audit(
            session.source,
            session.candidate,
            output_dir=destination / "connection-mode",
            target_source_junction_ids=session.target_source_junction_ids,
            target_candidate_junction_ids=session.target_candidate_junction_ids,
        )
    except Exception as exc:
        connection_mode = _audit_error("torii.connection_mode_regression.v1", exc)
    try:
        junction_identity = _declared_junction_identity_gate(session)
    except Exception as exc:
        junction_identity = _audit_error("torii.netedit-junction-identity-gate/v1", exc)
    try:
        scope_preservation = _scope_preservation_gate(session)
    except Exception as exc:
        scope_preservation = _audit_error("torii.netedit-scope-preservation-gate/v1", exc)

    try:
        source_hash_after = hashlib.sha256(session.source.read_bytes()).hexdigest()
        candidate_hash_after = hashlib.sha256(session.candidate.read_bytes()).hexdigest()
        source_stable = source_hash_before == source_hash_after
        candidate_stable = candidate_hash_before == candidate_hash_after
        audit_integrity = {
            "schema": "torii.netedit-finalize-audit-integrity/v1",
            "status": "pass" if source_stable and candidate_stable else "fail",
            "reason": (
                "source and candidate remained byte-identical during finalize audits"
                if source_stable and candidate_stable
                else "source or candidate changed while finalize audits were running"
            ),
            "source_sha256_before_audits": source_hash_before,
            "source_sha256_after_audits": source_hash_after,
            "candidate_sha256_before_audits": candidate_hash_before,
            "candidate_sha256_after_audits": candidate_hash_after,
            "source_unchanged_during_audits": source_stable,
            "candidate_unchanged_during_audits": candidate_stable,
        }
    except Exception as exc:
        source_hash_after = None
        candidate_hash_after = None
        audit_integrity = {
            **_audit_error("torii.netedit-finalize-audit-integrity/v1", exc),
            "source_sha256_before_audits": source_hash_before,
            "source_sha256_after_audits": source_hash_after,
            "candidate_sha256_before_audits": candidate_hash_before,
            "candidate_sha256_after_audits": candidate_hash_after,
            "source_unchanged_during_audits": False,
            "candidate_unchanged_during_audits": False,
        }

    statuses = {
        "sumo_load": str(sumo_load.get("status", "fail")),
        "surface_comparison": str(surface_comparison.get("status", "fail")),
        "connection_mode": str(connection_mode.get("status", "fail")),
        "junction_identity": str(junction_identity.get("status", "fail")),
        "scope_preservation": str(scope_preservation.get("status", "fail")),
        "audit_integrity": str(audit_integrity.get("status", "fail")),
    }
    accepted_statuses = {"pass", "not_applicable", "not_run", "review_required"}
    if any(value not in accepted_statuses for value in statuses.values()):
        machine_gate_status = "fail"
    elif "not_run" in statuses.values() or "review_required" in statuses.values():
        machine_gate_status = "review_required"
    else:
        machine_gate_status = "pass"
    status = "fail" if machine_gate_status == "fail" else "review_required"
    report = {
        "schema": "torii.netedit-finalize-audits/v1",
        "status": status,
        "machine_gate_status": machine_gate_status,
        "source_net_file": str(session.source),
        "candidate_net_file": str(session.candidate),
        "source_sha256_before_audits": source_hash_before,
        "source_sha256_after_audits": source_hash_after,
        "candidate_sha256_before_audits": candidate_hash_before,
        "candidate_sha256_after_audits": candidate_hash_after,
        "declared_edit_scope": {
            "target_source_junction_ids": list(session.target_source_junction_ids),
            "target_candidate_junction_ids": list(session.target_candidate_junction_ids),
            "fixed_before_gui_edit": True,
        },
        "gate_statuses": statuses,
        "sumo_load": _audit_summary(sumo_load, "source_network_mutation"),
        "surface_source": _audit_summary(
            source_surface,
            "junction_junction_overlap_count",
            "external_lane_non_owner_junction_overlap_count",
            "geometry_error_count",
        ),
        "surface_candidate": _audit_summary(
            candidate_surface,
            "junction_junction_overlap_count",
            "external_lane_non_owner_junction_overlap_count",
            "geometry_error_count",
        ),
        "surface_comparison": _audit_summary(
            surface_comparison,
            "focus_junction_ids",
            "introduced_finding_count",
            "candidate_focus_finding_count",
            "resolved_finding_count",
        ),
        "connection_mode": _audit_summary(
            connection_mode,
            "blockers",
            "outside_scope_regression_junction_ids",
            "target_scope_flagged_junction_ids",
            "target_scope_new_review_finding_count",
            "target_scope_new_structural_finding_count",
        ),
        "junction_identity": junction_identity,
        "scope_preservation": scope_preservation,
        "audit_integrity": audit_integrity,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "These machine gates validate a diagnostic candidate only; visual and official-data "
            "review remain required."
        ),
    }
    report_file = destination / "summary.json"
    write_json_atomic(report_file, report, sort_keys=True)
    return {
        **report,
        "report_file": str(report_file),
        "report_file_sha256": hashlib.sha256(report_file.read_bytes()).hexdigest(),
    }


def sumo_netedit_session(
    operation: NeteditOperation,
    session_id: str | None = None,
    source_net_file: str | None = None,
    candidate_net_file: str | None = None,
    output_dir: str | None = None,
    expected_source_sha256: str | None = None,
    gui_settings_file: str | None = None,
    selection_file: str | None = None,
    target_source_junction_ids: list[str] | None = None,
    target_candidate_junction_ids: list[str] | None = None,
    window_size: str = "1400,1000",
    window_pos: str = "20,20",
    action: NeteditAction | None = None,
    expected_screenshot_sha256: str | None = None,
    x: int | None = None,
    y: int | None = None,
    to_x: int | None = None,
    to_y: int | None = None,
    label: str = "observe",
    object_type: NeteditObjectType | None = None,
    object_id: str | None = None,
    reason: str = "caller_aborted",
) -> dict[str, Any]:
    """Run one bounded operation against the single active NetEdit GUI session.

    ``open`` requires source/candidate/output paths and the source SHA-256;
    declare target source/candidate junction ids there when a bounded edit is
    planned so finalize can compare geometry and Connection Mode without
    inventing scope after the edit.
    ``observe`` may inspect one persisted junction, edge, lane, connection, or
    tlLogic. ``act`` requires the latest screenshot SHA-256 and exactly one
    whitelisted ``action``: ``click``, ``drag``, a ``*_mode`` action,
    ``recompute``, ``join_selected_junctions``, ``accept``, ``cancel``,
    ``undo``, or ``redo``.
    ``finalize`` also requires the latest screenshot hash; ``abort`` does not.
    """

    global _ACTIVE_SESSION, _ACTIVE_SESSION_ID
    operation = operation.strip().lower() if isinstance(operation, str) else ""
    with _SESSION_LOCK:
        try:
            if operation not in {"open", "observe", "act", "finalize", "abort"}:
                raise ValueError("operation must be open, observe, act, finalize, or abort")
            if operation == "open":
                if _ACTIVE_SESSION is not None:
                    raise RuntimeError(f"NetEdit session {_ACTIVE_SESSION_ID} is already active")
                if session_id:
                    raise ValueError("open creates the session_id; omit session_id")
                source = Path(_required(source_net_file, "source_net_file")).resolve()
                candidate = Path(_required(candidate_net_file, "candidate_net_file")).resolve()
                destination = Path(_required(output_dir, "output_dir")).resolve()
                session = NeteditTargetSession(
                    source,
                    candidate,
                    destination,
                    expected_source_sha256=_sha256(expected_source_sha256, "expected_source_sha256"),
                    gui_settings_file=Path(gui_settings_file).resolve() if gui_settings_file else None,
                    selection_file=Path(selection_file).resolve() if selection_file else None,
                    target_source_junction_ids=target_source_junction_ids or (),
                    target_candidate_junction_ids=target_candidate_junction_ids or (),
                    window_size=window_size,
                    window_pos=window_pos,
                )
                result = session.open()
                _ACTIVE_SESSION = session
                _ACTIVE_SESSION_ID = uuid4().hex
                active_id = _ACTIVE_SESSION_ID
                state = "open"
            else:
                if _ACTIVE_SESSION is None:
                    raise RuntimeError("no NetEdit session is active")
                if not session_id or session_id != _ACTIVE_SESSION_ID:
                    raise ValueError("session_id does not match the active NetEdit session")
                active_id = _ACTIVE_SESSION_ID
                session = _ACTIVE_SESSION
                if operation == "observe":
                    result = session.observe(label)
                    persisted_scene = _persisted_scene(
                        session.candidate,
                        object_type=object_type,
                        object_id=object_id,
                    )
                    state = "open"
                elif operation == "act":
                    result = session.act(
                        _core_action(
                            action,
                            expected_screenshot_sha256=expected_screenshot_sha256,
                            x=x,
                            y=y,
                            to_x=to_x,
                            to_y=to_y,
                        )
                    )
                    state = "open"
                elif operation == "finalize":
                    release_session = True
                    try:
                        try:
                            result = session.finalize(
                                expected_screenshot_sha256=_sha256(
                                    expected_screenshot_sha256,
                                    "expected_screenshot_sha256",
                                )
                            )
                            result["finalize_audits"] = _finalize_audits(session)
                        except Exception as exc:
                            if _retryable_act_error(exc):
                                release_session = False
                                raise
                            try:
                                session.abort("finalize_failed")
                            except Exception:
                                pass
                            raise
                        state = "finalized"
                    finally:
                        if release_session:
                            _ACTIVE_SESSION = None
                            _ACTIVE_SESSION_ID = ""
                elif operation == "abort":
                    try:
                        result = session.abort(reason)
                        state = "aborted"
                    finally:
                        _ACTIVE_SESSION = None
                        _ACTIVE_SESSION_ID = ""
            response = {
                "schema": "torii.sumo-netedit-session/v1",
                "status": "pass",
                "operation_status": "pass",
                "operation": operation,
                "session_id": active_id,
                "session_state": state,
                "result": result,
                "automatic_promotion_gate": "blocked",
                "claim_boundary": "NetEdit GUI actions create diagnostic candidate evidence only.",
            }
            if operation == "finalize":
                response["status"] = result["finalize_audits"]["status"]
            if operation == "observe":
                response["persisted_scene"] = persisted_scene
            return response
        except Exception as exc:
            cleanup: dict[str, Any] | None = None
            if (
                operation == "act"
                and _ACTIVE_SESSION is not None
                and session_id == _ACTIVE_SESSION_ID
                and not _retryable_act_error(exc)
            ):
                try:
                    cleanup = _ACTIVE_SESSION.abort("act_failed")
                except Exception as cleanup_exc:
                    cleanup = {
                        "status": "cleanup_failed",
                        "error": f"{type(cleanup_exc).__name__}: {cleanup_exc}",
                    }
                finally:
                    _ACTIVE_SESSION = None
                    _ACTIVE_SESSION_ID = ""
            elif (
                operation in {"finalize", "abort"}
                and _ACTIVE_SESSION is not None
                and session_id == _ACTIVE_SESSION_ID
                and not (operation == "finalize" and _retryable_act_error(exc))
            ):
                _ACTIVE_SESSION = None
                _ACTIVE_SESSION_ID = ""
            response = _blocked(operation, str(exc), session_id)
            if cleanup is not None:
                response["cleanup"] = cleanup
            return response
