from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from torii_sumo.core.ingolstadt_netedit_ab_evidence import (
    INGOLSTADT_NETEDIT_AB_EVIDENCE_SCHEMA,
    IngolstadtNeteditABEvidenceError,
    build_ingolstadt_netedit_ab_evidence,
)


SCRIPT = Path("plugins/torii-sumo/scripts/build_ingolstadt_netedit_ab_evidence.py")
VIEW_MODES = {
    "overview": ("inspect", "none", ""),
    "inspect": ("inspect", "junction", "junction-test"),
    "tls": ("tls", "junction", "junction-test"),
    "connection": ("connection", "junction", "junction-test"),
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_direct_audit(
    root: Path,
    *,
    role: str,
    target_junction_id: str,
    missing_view: str | None = None,
    net_offset: tuple[float, float] = (-100.0, -200.0),
    projected_view_center: tuple[float, float] = (201.0, 402.0),
) -> Path:
    directory = root / role
    directory.mkdir(parents=True)
    network = directory / f"{role}.net.xml"
    local_view_center = (
        projected_view_center[0] + net_offset[0],
        projected_view_center[1] + net_offset[1],
    )
    network.write_text(
        (
            f"<net role='{role}'><location netOffset='{net_offset[0]},{net_offset[1]}' "
            "projParameter='+proj=utm +zone=32 +datum=WGS84 +units=m +no_defs'/>"
            "</net>"
        ),
        encoding="utf-8",
    )
    network_hash = _sha(network)
    keyboard = {
        "status": "pass",
        "is_chinese": False,
        "lang_id": "0x0409",
        "layout_name": "00000409",
    }
    captures = []
    for view_role, (mode, selection_type, selection_id) in VIEW_MODES.items():
        if view_role == missing_view:
            continue
        png = directory / f"capture-{view_role}.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + role.encode() + view_role.encode())
        captures.append(
            {
                "name": f"direct-test-{view_role}",
                "mode": mode,
                "selection_type": selection_type,
                "selection_id": selection_id,
                "screenshot_file": str(png),
                "sha256": _sha(png),
                "render_quality": "pass",
                "foreground_unchanged": True,
                "foreground_context_restored": True,
                "window_presentation": {"status": "pass", "window_state": "maximized"},
                "keyboard_layout": keyboard,
                "mode_delivery": {"status": "pass" if mode != "inspect" else "not_required"},
            }
        )
    audit = directory / "direct-audit.json"
    audit.write_text(
        json.dumps(
            {
                "schema": "torii.netedit-background-review.direct/v1",
                "status": "review_material_ready",
                "candidate_file": str(network),
                "candidate_sha256_before": network_hash,
                "candidate_sha256_after": network_hash,
                "candidate_unchanged": True,
                "target_junction": {
                    "id": target_junction_id,
                    "type": "traffic_light",
                    "x": local_view_center[0],
                    "y": local_view_center[1],
                },
                "view_center": list(local_view_center),
                "window_size": "1600,1000",
                "global_keyboard_or_mouse_input_used": False,
                "foreground_context_restored": True,
                "keyboard_layout_context": keyboard,
                "automatic_promotion_gate": "blocked",
                "captures": captures,
            }
        ),
        encoding="utf-8",
    )
    return audit


def test_builds_hash_bound_auxiliary_ab_manifest(tmp_path: Path) -> None:
    raw_audit = _write_direct_audit(tmp_path, role="raw", target_junction_id="raw-10")
    human_audit = _write_direct_audit(
        tmp_path,
        role="human",
        target_junction_id="human-20",
        net_offset=(-90.0, -190.0),
    )
    output = tmp_path / "evidence" / "ab.manifest.json"

    report = build_ingolstadt_netedit_ab_evidence(
        case_id="case-001",
        action_id="bounded-conflict-core-join-001",
        raw_audit_file=raw_audit,
        human_audit_file=human_audit,
        output_file=output,
    )

    assert report["schema"] == INGOLSTADT_NETEDIT_AB_EVIDENCE_SCHEMA
    assert report["status"] == "review_material_ready"
    assert report["screenshots_are_auxiliary"] is True
    assert report["image_inference_performed"] is False
    assert report["network_mutation_performed"] is False
    assert report["source_unchanged"] is True
    assert report["view_center_registration"]["status"] == "pass"
    assert report["view_center_registration"]["delta_m"] == 0.0
    assert report["promotion_gate_status"] == "blocked"
    assert report["view_roles"] == ["overview", "inspect", "tls", "connection"]
    assert report["raw_network_sha256"] == _sha(tmp_path / "raw" / "raw.net.xml")
    assert report["human_network_sha256"] == _sha(tmp_path / "human" / "human.net.xml")
    assert report["audit_report_hashes"] == {
        "raw": _sha(raw_audit),
        "human": _sha(human_audit),
    }
    connection = report["paired_views"][3]
    assert connection["case_id"] == "case-001"
    assert connection["action_id"] == "bounded-conflict-core-join-001"
    assert connection["raw"]["view_mode"] == "connection"
    assert connection["raw"]["target_junction_id"] == "raw-10"
    assert connection["human"]["target_junction_id"] == "human-20"
    assert connection["raw"]["png_sha256"] == _sha(
        tmp_path / "raw" / "capture-connection.png"
    )
    assert connection["raw"]["audit_report_sha256"] == _sha(raw_audit)
    assert connection["raw"]["keyboard_layout"]["layout_name"] == "00000409"
    assert connection["raw"]["window_presentation"]["window_state"] == "maximized"
    assert json.loads(output.read_text(encoding="utf-8"))["automatic_promotion_gate"] == "blocked"


def test_rejects_stale_png_hash(tmp_path: Path) -> None:
    raw_audit = _write_direct_audit(tmp_path, role="raw", target_junction_id="raw-10")
    human_audit = _write_direct_audit(
        tmp_path,
        role="human",
        target_junction_id="human-20",
        net_offset=(-90.0, -190.0),
    )
    (tmp_path / "raw" / "capture-tls.png").write_bytes(
        b"\x89PNG\r\n\x1a\nchanged"
    )

    with pytest.raises(IngolstadtNeteditABEvidenceError, match="PNG hash"):
        build_ingolstadt_netedit_ab_evidence(
            case_id="case-001",
            action_id="action-001",
            raw_audit_file=raw_audit,
            human_audit_file=human_audit,
            output_file=tmp_path / "ab.json",
        )


def test_rejects_stale_network_or_incomplete_view_pair(tmp_path: Path) -> None:
    raw_audit = _write_direct_audit(tmp_path, role="raw", target_junction_id="raw-10")
    human_audit = _write_direct_audit(
        tmp_path,
        role="human",
        target_junction_id="human-20",
        missing_view="tls",
        net_offset=(-90.0, -190.0),
    )
    with pytest.raises(IngolstadtNeteditABEvidenceError, match="same view roles"):
        build_ingolstadt_netedit_ab_evidence(
            case_id="case-001",
            action_id="action-001",
            raw_audit_file=raw_audit,
            human_audit_file=human_audit,
            output_file=tmp_path / "ab.json",
        )


def test_blocks_unregistered_projected_view_centers(tmp_path: Path) -> None:
    raw_audit = _write_direct_audit(tmp_path, role="raw", target_junction_id="raw-10")
    human_audit = _write_direct_audit(
        tmp_path,
        role="human",
        target_junction_id="human-20",
        net_offset=(-90.0, -190.0),
        projected_view_center=(205.0, 405.0),
    )
    output = tmp_path / "ab.json"

    report = build_ingolstadt_netedit_ab_evidence(
        case_id="case-001",
        action_id="action-001",
        raw_audit_file=raw_audit,
        human_audit_file=human_audit,
        output_file=output,
    )

    assert report["status"] == "blocked"
    assert report["view_center_registration"]["status"] == "blocked"
    assert report["view_center_registration"]["delta_m"] == 5.0
    assert report["promotion_gate_status"] == "blocked"
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "blocked"

    (tmp_path / "raw" / "raw.net.xml").write_text(
        (
            "<net changed='yes'><location netOffset='-100.0,-200.0' "
            "projParameter='+proj=utm +zone=32 +datum=WGS84 +units=m +no_defs'/>"
            "</net>"
        ),
        encoding="utf-8",
    )
    with pytest.raises(IngolstadtNeteditABEvidenceError, match="network hash"):
        build_ingolstadt_netedit_ab_evidence(
            case_id="case-001",
            action_id="action-001",
            raw_audit_file=raw_audit,
            human_audit_file=raw_audit,
            output_file=tmp_path / "ab.json",
        )


def test_cli_forwards_direct_audits(monkeypatch, tmp_path: Path, capsys) -> None:
    spec = importlib.util.spec_from_file_location("build_ingolstadt_netedit_ab_evidence", SCRIPT)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = script
    spec.loader.exec_module(script)
    captured: dict[str, object] = {}

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return {
            "status": "review_material_ready",
            "case_id": kwargs["case_id"],
            "action_id": kwargs["action_id"],
            "paired_views": [{}, {}],
            "screenshots_are_auxiliary": True,
            "view_center_registration": {"status": "pass"},
            "promotion_gate_status": "blocked",
            "manifest_file": str(kwargs["output_file"]),
        }

    monkeypatch.setattr(script, "build_ingolstadt_netedit_ab_evidence", fake_builder)
    result = script.main(
        [
            "--case-id",
            "case-001",
            "--action-id",
            "action-001",
            "--raw-audit",
            "raw.json",
            "--human-audit",
            "human.json",
            "--output-file",
            str(tmp_path / "ab.json"),
        ]
    )

    assert result == 0
    assert captured == {
        "case_id": "case-001",
        "action_id": "action-001",
        "raw_audit_file": Path("raw.json"),
        "human_audit_file": Path("human.json"),
        "output_file": tmp_path / "ab.json",
    }
    summary = json.loads(capsys.readouterr().out)
    assert summary["screenshots_are_auxiliary"] is True
    assert summary["promotion_gate_status"] == "blocked"
