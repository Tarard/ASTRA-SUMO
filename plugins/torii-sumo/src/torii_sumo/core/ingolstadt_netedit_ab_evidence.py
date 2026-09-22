"""Hash-bound A/B NetEdit evidence for Ingolstadt teacher cases.

This module deliberately does not open NetEdit, inspect image pixels beyond
the PNG file signature, or modify either network.  It binds existing direct
background-review reports and their screenshots into one auxiliary evidence
manifest.  The result is review material only and can never promote a network.
"""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256


INGOLSTADT_NETEDIT_AB_EVIDENCE_SCHEMA = "torii.ingolstadt-netedit-ab-visual-evidence/v1"
DIRECT_NETEDIT_AUDIT_SCHEMA = "torii.netedit-background-review.direct/v1"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_VIEW_ORDER = ("overview", "inspect", "tls", "connection")
# Direct-review centers are persisted to centimetre precision.  A looser
# tolerance would silently accept two independently centered junction views
# as an A/B registration even when the human-cleaned junction moved.
_MAX_PROJECTED_VIEW_CENTER_DELTA_M = 0.05


class IngolstadtNeteditABEvidenceError(ValueError):
    """Raised when existing NetEdit evidence is stale or incomplete."""


def build_ingolstadt_netedit_ab_evidence(
    *,
    case_id: str,
    action_id: str,
    raw_audit_file: Path,
    human_audit_file: Path,
    output_file: Path,
) -> dict[str, Any]:
    """Bind one raw/human NetEdit screenshot pair without interpreting it."""

    normalized_case_id = _required_token(case_id, "case_id")
    normalized_action_id = _required_token(action_id, "action_id")
    raw = _load_direct_audit(
        role="raw",
        case_id=normalized_case_id,
        action_id=normalized_action_id,
        audit_file=raw_audit_file,
    )
    human = _load_direct_audit(
        role="human",
        case_id=normalized_case_id,
        action_id=normalized_action_id,
        audit_file=human_audit_file,
    )

    raw_views = {item["view_role"]: item for item in raw["views"]}
    human_views = {item["view_role"]: item for item in human["views"]}
    if set(raw_views) != set(human_views):
        raise IngolstadtNeteditABEvidenceError(
            "raw and human direct audits must contain the same view roles: "
            f"raw={sorted(raw_views)}, human={sorted(human_views)}"
        )

    paired_views = [
        {
            "case_id": normalized_case_id,
            "action_id": normalized_action_id,
            "view_role": view_role,
            "raw": raw_views[view_role],
            "human": human_views[view_role],
        }
        for view_role in _sorted_view_roles(raw_views)
    ]
    source_unchanged = bool(raw["source_unchanged"] and human["source_unchanged"])
    if not source_unchanged:
        raise IngolstadtNeteditABEvidenceError(
            "both raw and human network sources must remain byte-identical"
        )

    registration = _audit_projected_view_center_registration(raw=raw, human=human)
    status = "review_material_ready" if registration["status"] == "pass" else "blocked"
    manifest = {
        "schema": INGOLSTADT_NETEDIT_AB_EVIDENCE_SCHEMA,
        "status": status,
        "case_id": normalized_case_id,
        "action_id": normalized_action_id,
        "screenshots_are_auxiliary": True,
        "image_inference_performed": False,
        "network_mutation_performed": False,
        "source_unchanged": source_unchanged,
        "raw_network_sha256": raw["network_sha256"],
        "human_network_sha256": human["network_sha256"],
        "audit_report_hashes": {
            "raw": raw["audit_report_sha256"],
            "human": human["audit_report_sha256"],
        },
        "raw": raw,
        "human": human,
        "paired_views": paired_views,
        "view_roles": [item["view_role"] for item in paired_views],
        "view_center_registration": registration,
        "promotion_gate_status": "blocked",
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "The screenshots are auxiliary, hash-bound NetEdit context only. "
            "No image inference was performed, neither network was modified, "
            "and this manifest cannot authorize topology transfer or promotion. "
            "A/B review material is not ready unless both local view centers map "
            "to the same projected UTM location."
        ),
    }
    write_json_atomic(output_file, manifest, sort_keys=True)
    return {**manifest, "manifest_file": str(output_file.resolve())}


def _load_direct_audit(
    *,
    role: str,
    case_id: str,
    action_id: str,
    audit_file: Path,
) -> dict[str, Any]:
    report_path = audit_file.expanduser().resolve(strict=True)
    audit_hash_before = file_sha256(report_path)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IngolstadtNeteditABEvidenceError(
            f"{role} direct audit is not valid JSON: {report_path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise IngolstadtNeteditABEvidenceError(f"{role} direct audit must be a JSON object")
    if payload.get("schema") != DIRECT_NETEDIT_AUDIT_SCHEMA:
        raise IngolstadtNeteditABEvidenceError(
            f"{role} direct audit schema must be {DIRECT_NETEDIT_AUDIT_SCHEMA}"
        )
    if payload.get("status") != "review_material_ready":
        raise IngolstadtNeteditABEvidenceError(
            f"{role} direct audit is not review_material_ready: {payload.get('status')!r}"
        )
    if payload.get("automatic_promotion_gate") != "blocked":
        raise IngolstadtNeteditABEvidenceError(
            f"{role} direct audit must keep automatic promotion blocked"
        )

    network_file = _resolve_recorded_path(
        payload.get("candidate_file"),
        report_path.parent,
        field=f"{role}.candidate_file",
    )
    network_hash_before = file_sha256(network_file)
    coordinate_frame = _read_network_coordinate_frame(network_file)
    declared_before = str(payload.get("candidate_sha256_before", "")).lower()
    declared_after = str(payload.get("candidate_sha256_after", "")).lower()
    if payload.get("candidate_unchanged") is not True:
        raise IngolstadtNeteditABEvidenceError(
            f"{role} direct audit does not declare candidate_unchanged=true"
        )
    if not declared_before or declared_before != declared_after:
        raise IngolstadtNeteditABEvidenceError(
            f"{role} direct audit has inconsistent network hashes"
        )
    if network_hash_before.lower() != declared_before:
        raise IngolstadtNeteditABEvidenceError(
            f"{role} network hash no longer matches its direct audit"
        )

    keyboard_layout = _validate_keyboard_layout(
        payload.get("keyboard_layout_context"),
        field=f"{role}.keyboard_layout_context",
    )
    if payload.get("global_keyboard_or_mouse_input_used") is not False:
        raise IngolstadtNeteditABEvidenceError(
            f"{role} direct audit must prove no global keyboard or mouse input was used"
        )
    if payload.get("foreground_context_restored") is not True:
        raise IngolstadtNeteditABEvidenceError(
            f"{role} direct audit must prove foreground context was restored"
        )

    target_junction = payload.get("target_junction")
    if not isinstance(target_junction, Mapping) or not str(target_junction.get("id", "")).strip():
        raise IngolstadtNeteditABEvidenceError(
            f"{role} direct audit is missing target_junction.id"
        )
    local_view_center = _coordinate_pair(
        payload.get("view_center"),
        field=f"{role}.view_center",
    )
    projected_view_center = (
        local_view_center[0] - coordinate_frame["net_offset"][0],
        local_view_center[1] - coordinate_frame["net_offset"][1],
    )

    captures = payload.get("captures")
    if not isinstance(captures, list) or not captures:
        raise IngolstadtNeteditABEvidenceError(f"{role} direct audit has no captures")
    views: list[dict[str, Any]] = []
    seen_roles: set[str] = set()
    for capture in captures:
        if not isinstance(capture, Mapping):
            raise IngolstadtNeteditABEvidenceError(
                f"{role} direct audit contains a non-object capture"
            )
        view_role = _view_role(capture)
        if view_role in seen_roles:
            raise IngolstadtNeteditABEvidenceError(
                f"{role} direct audit repeats view role {view_role!r}"
            )
        seen_roles.add(view_role)
        views.append(
            _bind_capture(
                role=role,
                case_id=case_id,
                action_id=action_id,
                report_path=report_path,
                audit_report_sha256=audit_hash_before,
                network_file=network_file,
                network_sha256=network_hash_before,
                target_junction=target_junction,
                local_view_center=local_view_center,
                projected_view_center=projected_view_center,
                top_keyboard_layout=keyboard_layout,
                capture=capture,
                view_role=view_role,
            )
        )

    audit_hash_after = file_sha256(report_path)
    network_hash_after = file_sha256(network_file)
    source_unchanged = (
        audit_hash_before == audit_hash_after
        and network_hash_before == network_hash_after
        and network_hash_after.lower() == declared_after
    )
    if not source_unchanged:
        raise IngolstadtNeteditABEvidenceError(
            f"{role} source or direct audit changed while evidence was assembled"
        )
    return {
        "role": role,
        "audit_report_file": str(report_path),
        "audit_report_sha256": audit_hash_before,
        "audit_report_unchanged": audit_hash_before == audit_hash_after,
        "network_file": str(network_file),
        "network_sha256": network_hash_before,
        "declared_network_sha256_before": declared_before,
        "declared_network_sha256_after": declared_after,
        "source_unchanged": source_unchanged,
        "target_junction": dict(target_junction),
        "coordinate_frame": coordinate_frame,
        "local_view_center": list(local_view_center),
        "projected_view_center": list(projected_view_center),
        "keyboard_layout_context": keyboard_layout,
        "window_size": str(payload.get("window_size", "")),
        "global_keyboard_or_mouse_input_used": False,
        "foreground_context_restored": True,
        "views": sorted(views, key=lambda item: _view_sort_key(item["view_role"])),
    }


def _bind_capture(
    *,
    role: str,
    case_id: str,
    action_id: str,
    report_path: Path,
    audit_report_sha256: str,
    network_file: Path,
    network_sha256: str,
    target_junction: Mapping[str, Any],
    local_view_center: tuple[float, float],
    projected_view_center: tuple[float, float],
    top_keyboard_layout: Mapping[str, Any],
    capture: Mapping[str, Any],
    view_role: str,
) -> dict[str, Any]:
    screenshot = _resolve_recorded_path(
        capture.get("screenshot_file"),
        report_path.parent,
        field=f"{role}.{view_role}.screenshot_file",
    )
    if screenshot.suffix.lower() != ".png" or screenshot.read_bytes()[:8] != _PNG_SIGNATURE:
        raise IngolstadtNeteditABEvidenceError(
            f"{role} {view_role} screenshot is not a PNG file: {screenshot}"
        )
    png_hash = file_sha256(screenshot)
    if png_hash.lower() != str(capture.get("sha256", "")).lower():
        raise IngolstadtNeteditABEvidenceError(
            f"{role} {view_role} PNG hash no longer matches its direct audit"
        )
    if capture.get("render_quality") != "pass":
        raise IngolstadtNeteditABEvidenceError(
            f"{role} {view_role} capture render quality did not pass"
        )
    if capture.get("foreground_unchanged") is not True:
        raise IngolstadtNeteditABEvidenceError(
            f"{role} {view_role} did not preserve foreground context"
        )
    if capture.get("foreground_context_restored") is not True:
        raise IngolstadtNeteditABEvidenceError(
            f"{role} {view_role} did not restore foreground context"
        )
    window = capture.get("window_presentation")
    if (
        not isinstance(window, Mapping)
        or window.get("status") != "pass"
        or not str(window.get("window_state", "")).strip()
    ):
        raise IngolstadtNeteditABEvidenceError(
            f"{role} {view_role} capture has no passing window state"
        )
    keyboard = _validate_keyboard_layout(
        capture.get("keyboard_layout"),
        field=f"{role}.{view_role}.keyboard_layout",
    )
    if keyboard.get("layout_name") != top_keyboard_layout.get("layout_name"):
        raise IngolstadtNeteditABEvidenceError(
            f"{role} {view_role} keyboard layout differs from the audit session"
        )

    return {
        "case_id": case_id,
        "action_id": action_id,
        "source_role": role,
        "view_role": view_role,
        "view_mode": str(capture.get("mode", "")),
        "selection_type": str(capture.get("selection_type", "")),
        "selection_id": str(capture.get("selection_id", "")),
        "target_junction_id": str(target_junction["id"]),
        "local_view_center": list(local_view_center),
        "projected_view_center": list(projected_view_center),
        "network_file": str(network_file),
        "network_sha256": network_sha256,
        "audit_report_file": str(report_path),
        "audit_report_sha256": audit_report_sha256,
        "screenshot_file": str(screenshot),
        "png_sha256": png_hash,
        "source_unchanged": True,
        "keyboard_layout": keyboard,
        "window_presentation": dict(window),
        "foreground_unchanged": True,
        "foreground_context_restored": True,
        "mode_delivery": dict(capture.get("mode_delivery", {})),
        "render_quality": "pass",
    }


def _validate_keyboard_layout(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IngolstadtNeteditABEvidenceError(f"{field} must be an object")
    if value.get("status") != "pass" or value.get("is_chinese") is not False:
        raise IngolstadtNeteditABEvidenceError(
            f"{field} must record a passing non-Chinese keyboard layout"
        )
    if not str(value.get("layout_name", "")).strip():
        raise IngolstadtNeteditABEvidenceError(f"{field} is missing layout_name")
    return dict(value)


def _read_network_coordinate_frame(network_file: Path) -> dict[str, Any]:
    try:
        location = next(
            element
            for _event, element in ET.iterparse(network_file, events=("start",))
            if element.tag == "location"
        )
    except (ET.ParseError, StopIteration) as exc:
        raise IngolstadtNeteditABEvidenceError(
            f"network has no valid SUMO location element: {network_file}"
        ) from exc
    net_offset = _coordinate_pair(
        str(location.get("netOffset", "")).split(","),
        field=f"{network_file}.location.netOffset",
    )
    projection = " ".join(str(location.get("projParameter", "")).split())
    if "+proj=utm" not in projection:
        raise IngolstadtNeteditABEvidenceError(
            f"Ingolstadt A/B evidence requires an explicit UTM projection: {network_file}"
        )
    return {
        "net_offset": list(net_offset),
        "proj_parameter": projection,
        "coordinate_restore_rule": "projected_xy = local_sumo_xy - netOffset",
    }


def _audit_projected_view_center_registration(
    *,
    raw: Mapping[str, Any],
    human: Mapping[str, Any],
) -> dict[str, Any]:
    raw_projection = str(raw["coordinate_frame"]["proj_parameter"])
    human_projection = str(human["coordinate_frame"]["proj_parameter"])
    raw_center = tuple(float(value) for value in raw["projected_view_center"])
    human_center = tuple(float(value) for value in human["projected_view_center"])
    delta_m = math.dist(raw_center, human_center)
    projection_matches = raw_projection == human_projection
    status = (
        "pass"
        if projection_matches and delta_m <= _MAX_PROJECTED_VIEW_CENTER_DELTA_M
        else "blocked"
    )
    reasons: list[str] = []
    if not projection_matches:
        reasons.append("raw and human networks do not use the same UTM projection")
    if delta_m > _MAX_PROJECTED_VIEW_CENTER_DELTA_M:
        reasons.append(
            "raw and human NetEdit view centers are not registered in projected coordinates "
            f"({delta_m:.3f}m > {_MAX_PROJECTED_VIEW_CENTER_DELTA_M:.3f}m)"
        )
    return {
        "status": status,
        "projection_matches": projection_matches,
        "proj_parameter": raw_projection if projection_matches else None,
        "raw_projected_view_center": list(raw_center),
        "human_projected_view_center": list(human_center),
        "delta_m": delta_m,
        "max_delta_m": _MAX_PROJECTED_VIEW_CENTER_DELTA_M,
        "reasons": reasons,
    }


def _coordinate_pair(value: Any, *, field: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise IngolstadtNeteditABEvidenceError(f"{field} must contain two coordinates")
    try:
        pair = (float(value[0]), float(value[1]))
    except (TypeError, ValueError) as exc:
        raise IngolstadtNeteditABEvidenceError(f"{field} must contain finite numbers") from exc
    if not all(math.isfinite(item) for item in pair):
        raise IngolstadtNeteditABEvidenceError(f"{field} must contain finite numbers")
    return pair


def _resolve_recorded_path(value: Any, base: Path, *, field: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise IngolstadtNeteditABEvidenceError(f"{field} is missing")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base / path
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise IngolstadtNeteditABEvidenceError(f"{field} does not exist: {path}") from exc


def _view_role(capture: Mapping[str, Any]) -> str:
    name = str(capture.get("name", "")).lower()
    for role in _VIEW_ORDER:
        if name.endswith(f"-{role}"):
            return role
    raise IngolstadtNeteditABEvidenceError(
        f"capture name does not declare a supported view role: {capture.get('name')!r}"
    )


def _view_sort_key(role: str) -> tuple[int, str]:
    try:
        return (_VIEW_ORDER.index(role), role)
    except ValueError:
        return (len(_VIEW_ORDER), role)


def _sorted_view_roles(views: Mapping[str, Any]) -> list[str]:
    return sorted(views, key=_view_sort_key)


def _required_token(value: str, field: str) -> str:
    token = str(value).strip()
    if not token:
        raise IngolstadtNeteditABEvidenceError(f"{field} must not be empty")
    return token
