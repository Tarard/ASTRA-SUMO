from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from torii_sumo.intersection.scene_workflow import run_intersection_scene_workflow


PROMPT = "Build a four-way signalized intersection for passenger cars."


def _fake_movement_map() -> list[dict[str, Any]]:
    return [
        {
            "from": f"in_{index}",
            "fromLane": str(index % 3),
            "to": f"out_{index}",
            "toLane": str(index % 3),
            "linkIndex": index % 8,
            "nemaPhase": str(index % 8 + 1),
        }
        for index in range(12)
    ]


def _write_fake_backend_outputs(
    output_dir: Path,
    prefix: str,
    *,
    netconvert_returncode: int = 0,
    sumo_status: str = "pass",
    sumo_returncode: int = 0,
) -> dict[str, Any]:
    paths = {
        "net_file": output_dir / f"{prefix}.net.xml",
        "sumocfg_file": output_dir / f"{prefix}.sumocfg",
        "summary_file": output_dir / f"{prefix}_summary.xml",
        "tripinfo_file": output_dir / f"{prefix}_tripinfo.xml",
        "audit_file": output_dir / f"{prefix}_nema_audit.json",
        "netconvert_report_file": output_dir / f"{prefix}_netconvert_report.json",
        "sumo_report_file": output_dir / f"{prefix}_sumo_report.json",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    paths["net_file"].write_text("<net/>", encoding="utf-8")
    paths["sumocfg_file"].write_text("<configuration/>", encoding="utf-8")
    paths["summary_file"].write_text(
        '<summary><step time="600" loaded="2" inserted="2" arrived="2" '
        'running="0" waiting="0" teleports="0" collisions="0"/></summary>',
        encoding="utf-8",
    )
    paths["tripinfo_file"].write_text(
        '<tripinfos><tripinfo id="a" duration="10" waitingTime="0" timeLoss="1"/>'
        '<tripinfo id="b" duration="11" waitingTime="0" timeLoss="1"/></tripinfos>',
        encoding="utf-8",
    )
    audit = {
        "tls_id": "J0",
        "controlled_link_count": 12,
        "tls_signal_group_count": 8,
        "phase_order": [str(index) for index in range(1, 9)],
        "lane_rule": {
            "lane0": "right turn",
            "lane1": "through",
            "lane2": "protected left turn",
        },
        "params": {"ring1": "1,2,3,4", "ring2": "5,6,7,8"},
        "movement_map": _fake_movement_map(),
    }
    paths["audit_file"].write_text(json.dumps(audit), encoding="utf-8")
    netconvert_command = ["netconvert-test", "-o", str(paths["net_file"])]
    sumo_command = ["sumo-test", "-c", str(paths["sumocfg_file"])]
    paths["netconvert_report_file"].write_text(
        json.dumps(
            {
                "command": netconvert_command,
                "cwd": None,
                "status": "pass",
                "returncode": netconvert_returncode,
                "stdout": "net built\n",
                "stderr": "",
                "error": "",
            }
        ),
        encoding="utf-8",
    )
    paths["sumo_report_file"].write_text(
        json.dumps(
            {
                "command": sumo_command,
                "cwd": None,
                "status": sumo_status,
                "returncode": sumo_returncode,
                "stdout": "sumo output\n",
                "stderr": "bad sumo\n" if sumo_status != "pass" else "",
                "error": "",
            }
        ),
        encoding="utf-8",
    )
    return {
        "status": "pass" if sumo_status == "pass" else "fail",
        "claim_status": "diagnostic-demo" if sumo_status == "pass" else "construction-invalid",
        "netconvert_status": "pass",
        "sumo_smoke_status": sumo_status,
        **{key: str(path) for key, path in paths.items()},
    }


def test_scene_workflow_runs_real_sumo_toolchain(tmp_path: Path) -> None:
    missing = [name for name in ("netconvert", "sumo") if shutil.which(name) is None]
    if missing:
        pytest.skip(f"missing SUMO tools: {', '.join(missing)}")

    report = run_intersection_scene_workflow(PROMPT, tmp_path, prefix="real")

    assert report["status"] == "pass"
    assert report["netconvert_status"] == "pass"
    assert report["sumo_load_status"] == "pass"
    assert report["routeability_status"] == "pass"
    assert report["tls_status"] == "pass"
    assert report["netedit_status"] == "not_requested"
    manifest_path = Path(report["artifact_manifest_file"])
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["commands"]["netconvert"]["returncode"] == 0
    assert manifest["commands"]["sumo"]["returncode"] == 0
    assert report["artifact_hash_gate_status"] == "pass"
    assert manifest["artifact_hash_gate"]["algorithm"] == "sha256"
    assert manifest["artifact_hashes"]["net_file"]["status"] == "pass"
    assert manifest["artifact_hashes"]["artifact_manifest_file"]["status"] == "excluded"


def test_scene_workflow_records_relative_output_inventory_and_verbatim_commands(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        calls.append({"output_dir": output_dir, **kwargs})
        return _write_fake_backend_outputs(output_dir, kwargs["prefix"])

    def unexpected_netedit(_path: Path) -> dict[str, Any]:
        raise AssertionError("NetEdit must not launch by default")

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="scene",
        builder_func=fake_builder,
        netedit_func=unexpected_netedit,
    )

    assert calls == [
        {
            "output_dir": tmp_path,
            "prefix": "scene",
            "run_sumo_smoke": True,
            "require_real_sumo": True,
        }
    ]
    assert report["status"] == "pass"
    assert report["netconvert_status"] == "pass"
    assert report["sumo_load_status"] == "pass"
    assert report["routeability_status"] == "pass"
    assert report["tls_status"] == "pass"
    assert report["netedit_status"] == "not_requested"

    manifest_path = Path(report["artifact_manifest_file"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "intersection-scene-artifacts/v1"
    assert manifest["input_prompt"] == PROMPT
    assert manifest["resolved_spec"] == report["resolved_spec"]
    assert manifest["status"] == "pass"
    assert manifest["claim_status"] == "diagnostic-demo"
    assert report["artifact_hash_gate_status"] == "pass"
    assert manifest["artifact_hash_gate"]["status"] == "pass"
    assert manifest["path_contract"] == {
        "output_files": "relative to the artifact manifest directory",
        "commands": "verbatim execution evidence; path arguments may be absolute or relative to command cwd",
    }
    assert manifest["output_files"]["artifact_manifest_file"] == "scene_artifact_manifest.json"
    assert manifest["output_files"]["net_file"] == "scene.net.xml"
    assert all(not Path(path).is_absolute() for path in manifest["output_files"].values())
    assert manifest["commands"]["netconvert"] == {
        "command": ["netconvert-test", "-o", str(tmp_path / "scene.net.xml")],
        "cwd": None,
        "status": "pass",
        "returncode": 0,
        "stdout": "net built\n",
        "stderr": "",
        "error": "",
    }
    assert manifest["commands"]["sumo"] == {
        "command": ["sumo-test", "-c", str(tmp_path / "scene.sumocfg")],
        "cwd": None,
        "status": "pass",
        "returncode": 0,
        "stdout": "sumo output\n",
        "stderr": "",
        "error": "",
    }
    assert manifest["checks"]["netconvert"]["status"] == "pass"
    assert manifest["checks"]["sumo_load"]["status"] == "pass"
    assert manifest["checks"]["routeability"]["status"] == "pass"
    assert manifest["checks"]["routeability"]["summary"]["arrived"] == 2
    assert manifest["checks"]["routeability"]["tripinfo"]["trip_count"] == 2
    assert manifest["checks"]["tls"] == {"status": "pass"}
    assert manifest["tls_explanation"] == {
        "tls_id": "J0",
        "controlled_link_count": 12,
        "tls_signal_group_count": 8,
        "phase_order": [str(index) for index in range(1, 9)],
        "lane_rule": {
            "lane0": "right turn",
            "lane1": "through",
            "lane2": "protected left turn",
        },
        "params": {"ring1": "1,2,3,4", "ring2": "5,6,7,8"},
        "movement_map": _fake_movement_map(),
    }
    assert manifest["netedit"] == {
        "command": [],
        "status": "not_requested",
        "process_id": None,
    }
    assert manifest["warnings"] == []


@pytest.mark.parametrize(
    "prefix",
    ["", ".", "..", "../sentinel", r"..\sentinel", "nested/scene", "C:\\scene"],
)
def test_scene_workflow_rejects_unsafe_prefix_before_filesystem_actions(
    tmp_path: Path,
    prefix: str,
) -> None:
    output_dir = tmp_path / "output"
    sentinel = tmp_path / "sentinel.net.xml"
    sentinel.write_text("keep", encoding="utf-8")
    builder_called = False

    def fake_builder(_output_dir: Path, **_kwargs: Any) -> dict[str, Any]:
        nonlocal builder_called
        builder_called = True
        return {}

    with pytest.raises(ValueError, match="prefix"):
        run_intersection_scene_workflow(
            PROMPT,
            output_dir,
            prefix=prefix,
            builder_func=fake_builder,
        )

    assert builder_called is False
    assert output_dir.exists() is False
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_scene_workflow_uses_expected_evidence_paths_not_builder_external_paths(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    external_dir = tmp_path / "external"

    def fake_builder(directory: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(directory, kwargs["prefix"])
        external = _write_fake_backend_outputs(
            external_dir,
            "other",
            netconvert_returncode=7,
        )
        for key in (
            "net_file",
            "sumocfg_file",
            "audit_file",
            "netconvert_report_file",
            "sumo_report_file",
        ):
            result[key] = external[key]
        return result

    report = run_intersection_scene_workflow(
        PROMPT,
        output_dir,
        prefix="scene",
        builder_func=fake_builder,
    )

    manifest = json.loads(Path(report["artifact_manifest_file"]).read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["net_file"] == str(output_dir / "scene.net.xml")
    assert report["sumocfg_file"] == str(output_dir / "scene.sumocfg")
    assert all(not path.startswith("..") for path in manifest["output_files"].values())
    assert manifest["commands"]["netconvert"]["returncode"] == 0


@pytest.mark.parametrize(
    ("missing_key", "failed_check"),
    [("net_file", "netconvert"), ("sumocfg_file", "sumo_load")],
)
def test_scene_workflow_requires_expected_core_files(
    tmp_path: Path,
    missing_key: str,
    failed_check: str,
) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(output_dir, kwargs["prefix"])
        Path(result[missing_key]).unlink()
        return result

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="missing-core",
        builder_func=fake_builder,
    )

    manifest = json.loads(Path(report["artifact_manifest_file"]).read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert manifest["checks"][failed_check]["status"] == "fail"


@pytest.mark.parametrize(
    ("report_key", "command", "failed_check"),
    [
        ("netconvert_report_file", ["netconvert-test", "-o", "elsewhere.net.xml"], "netconvert"),
        ("sumo_report_file", ["sumo-test", "-c", "elsewhere.sumocfg"], "sumo_load"),
        ("netconvert_report_file", [], "netconvert"),
        ("sumo_report_file", [], "sumo_load"),
    ],
)
def test_scene_workflow_requires_nonempty_commands_targeting_expected_files(
    tmp_path: Path,
    report_key: str,
    command: list[str],
    failed_check: str,
) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(output_dir, kwargs["prefix"])
        report_path = Path(result[report_key])
        evidence = json.loads(report_path.read_text(encoding="utf-8"))
        evidence["command"] = command
        report_path.write_text(json.dumps(evidence), encoding="utf-8")
        return result

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="bad-command",
        builder_func=fake_builder,
    )

    manifest = json.loads(Path(report["artifact_manifest_file"]).read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert manifest["checks"][failed_check]["status"] == "fail"


def test_scene_workflow_keeps_nonzero_sumo_result_in_failed_manifest(tmp_path: Path) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        return _write_fake_backend_outputs(
            output_dir,
            kwargs["prefix"],
            sumo_status="fail",
            sumo_returncode=9,
        )

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="broken",
        builder_func=fake_builder,
    )

    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert report["sumo_load_status"] == "fail"
    manifest = json.loads(Path(report["artifact_manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["status"] == "fail"
    assert manifest["commands"]["sumo"]["status"] == "fail"
    assert manifest["commands"]["sumo"]["returncode"] == 9
    assert manifest["commands"]["sumo"]["stderr"] == "bad sumo\n"
    assert manifest["checks"]["routeability"]["status"] == "pass"
    assert manifest["checks"]["tls"]["status"] == "pass"


def test_scene_workflow_downgrades_diagnostic_claim_for_incomplete_routeability(
    tmp_path: Path,
) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(output_dir, kwargs["prefix"])
        Path(result["summary_file"]).write_text(
            '<summary><step time="600" loaded="2" inserted="2" arrived="1" '
            'running="0" waiting="0" teleports="0" collisions="0"/></summary>',
            encoding="utf-8",
        )
        Path(result["tripinfo_file"]).write_text(
            '<tripinfos><tripinfo id="a" duration="10" waitingTime="0" '
            'timeLoss="1"/></tripinfos>',
            encoding="utf-8",
        )
        return result

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="incomplete",
        builder_func=fake_builder,
    )

    manifest = json.loads(Path(report["artifact_manifest_file"]).read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert manifest["claim_status"] == "construction-invalid"
    assert manifest["checks"]["routeability"]["status"] == "fail"


def test_scene_workflow_discards_stale_evidence_when_builder_is_blocked(
    tmp_path: Path,
) -> None:
    prefix = "stale"
    stale = _write_fake_backend_outputs(tmp_path, prefix)
    manifest_path = tmp_path / f"{prefix}_artifact_manifest.json"
    manifest_path.write_text('{"status": "pass"}', encoding="utf-8")
    owned_paths = [
        tmp_path / f"{prefix}{suffix}"
        for suffix in (
            ".nod.xml",
            ".edg.xml",
            ".con.xml",
            ".tll.xml",
            ".add.xml",
            ".net.xml",
            ".rou.xml",
            ".sumocfg",
            "_summary.xml",
            "_tripinfo.xml",
            "_nema_audit.json",
            "_evidence.md",
            "_netconvert_report.json",
            "_sumo_report.json",
            "_sumo.log",
            "_sumo_errors.log",
            "_artifact_manifest.json",
        )
    ]
    for path in owned_paths:
        if not path.exists():
            path.write_text("stale", encoding="utf-8")

    def blocked_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        assert all(not path.exists() for path in owned_paths)
        return {
            "status": "blocked",
            "claim_status": "blocked",
            "netconvert_status": "blocked",
            "sumo_smoke_status": "blocked",
            "net_file": stale["net_file"],
            "sumocfg_file": stale["sumocfg_file"],
            "audit_file": stale["audit_file"],
            "netconvert_report_file": stale["netconvert_report_file"],
            "sumo_report_file": stale["sumo_report_file"],
            "warnings": ["SUMO tools unavailable"],
        }

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix=prefix,
        builder_func=blocked_builder,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["claim_status"] == "blocked"
    assert manifest["claim_status"] == "blocked"
    assert manifest["commands"] == {"netconvert": {}, "sumo": {}}
    assert manifest["checks"]["netconvert"]["status"] == "blocked"
    assert manifest["checks"]["sumo_load"]["status"] == "blocked"
    assert manifest["checks"]["routeability"]["status"] == "blocked"
    assert manifest["checks"]["tls"]["status"] == "blocked"
    assert manifest["tls_explanation"] == {}
    assert manifest["output_files"] == {
        "artifact_manifest_file": f"{prefix}_artifact_manifest.json"
    }


def test_scene_workflow_preserves_tls_pass_when_only_sumo_is_blocked(
    tmp_path: Path,
) -> None:
    def blocked_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(output_dir, kwargs["prefix"])
        for key in ("summary_file", "tripinfo_file", "sumo_report_file"):
            Path(result[key]).unlink()
        return {
            **result,
            "status": "blocked",
            "claim_status": "blocked",
            "sumo_smoke_status": "blocked",
        }

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="sumo-blocked",
        builder_func=blocked_builder,
    )

    manifest = json.loads(Path(report["artifact_manifest_file"]).read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["claim_status"] == "blocked"
    assert report["netconvert_status"] == "pass"
    assert report["sumo_load_status"] == "blocked"
    assert report["routeability_status"] == "blocked"
    assert report["tls_status"] == "pass"
    assert manifest["checks"]["tls"] == {"status": "pass"}


def test_scene_workflow_rejects_nonzero_netconvert_returncode(tmp_path: Path) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        return _write_fake_backend_outputs(
            output_dir,
            kwargs["prefix"],
            netconvert_returncode=7,
        )

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="bad-netconvert",
        builder_func=fake_builder,
    )

    manifest = json.loads(Path(report["artifact_manifest_file"]).read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert report["netconvert_status"] == "fail"
    assert manifest["commands"]["netconvert"]["returncode"] == 7
    assert manifest["checks"]["netconvert"]["status"] == "fail"


def test_scene_workflow_rejects_out_of_range_tls_movement_link(tmp_path: Path) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(output_dir, kwargs["prefix"])
        audit_path = Path(result["audit_file"])
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit["movement_map"][0]["linkIndex"] = audit["controlled_link_count"]
        audit_path.write_text(json.dumps(audit), encoding="utf-8")
        return result

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="bad-tls",
        builder_func=fake_builder,
    )

    manifest = json.loads(Path(report["artifact_manifest_file"]).read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert report["tls_status"] == "fail"
    assert manifest["checks"]["tls"]["status"] == "fail"


def test_scene_workflow_rejects_missing_tls_movement_row(tmp_path: Path) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(output_dir, kwargs["prefix"])
        audit_path = Path(result["audit_file"])
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit["movement_map"].pop()
        audit_path.write_text(json.dumps(audit), encoding="utf-8")
        return result

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="missing-tls-row",
        builder_func=fake_builder,
    )

    manifest = json.loads(Path(report["artifact_manifest_file"]).read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert report["tls_status"] == "fail"
    assert manifest["checks"]["tls"]["status"] == "fail"


def test_scene_workflow_rejects_zero_demand_routeability_smoke(tmp_path: Path) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(output_dir, kwargs["prefix"])
        Path(result["summary_file"]).write_text(
            '<summary><step time="600" loaded="0" inserted="0" arrived="0" '
            'running="0" waiting="0" teleports="0" collisions="0"/></summary>',
            encoding="utf-8",
        )
        Path(result["tripinfo_file"]).write_text("<tripinfos/>", encoding="utf-8")
        return result

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="zero-demand",
        builder_func=fake_builder,
    )

    manifest = json.loads(Path(report["artifact_manifest_file"]).read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["routeability_status"] == "fail"
    assert manifest["checks"]["routeability"]["status"] == "fail"
    assert any(
        "loaded == inserted == arrived == trip_count > 0" in warning
        for warning in manifest["warnings"]
    )


def test_scene_workflow_rejects_incomplete_inserted_routeability_count(
    tmp_path: Path,
) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(output_dir, kwargs["prefix"])
        Path(result["summary_file"]).write_text(
            '<summary><step time="600" loaded="2" inserted="1" arrived="2" '
            'running="0" waiting="0" teleports="0" collisions="0"/></summary>',
            encoding="utf-8",
        )
        return result

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="incomplete-inserted",
        builder_func=fake_builder,
    )

    manifest = json.loads(Path(report["artifact_manifest_file"]).read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert manifest["checks"]["routeability"]["status"] == "fail"
    assert any(
        "loaded == inserted == arrived == trip_count > 0" in warning
        for warning in manifest["warnings"]
    )


@pytest.mark.parametrize(
    "mutation",
    ["one-arbitrary-group", "duplicate-phase-order", "wrong-phase-mapping", "missing-ring"],
)
def test_scene_workflow_enforces_fixed_phase_one_tls_contract(
    tmp_path: Path,
    mutation: str,
) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(output_dir, kwargs["prefix"])
        audit_path = Path(result["audit_file"])
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if mutation == "one-arbitrary-group":
            audit.update(
                {
                    "controlled_link_count": 1,
                    "tls_signal_group_count": 1,
                    "phase_order": ["A"],
                    "movement_map": [
                        {
                            "from": "in",
                            "fromLane": "0",
                            "to": "out",
                            "toLane": "0",
                            "linkIndex": 0,
                            "nemaPhase": "A",
                        }
                    ],
                }
            )
        elif mutation == "duplicate-phase-order":
            audit["phase_order"][-1] = "7"
            for movement in audit["movement_map"]:
                if movement["linkIndex"] == 7:
                    movement["nemaPhase"] = "7"
        elif mutation == "wrong-phase-mapping":
            audit["movement_map"][0]["nemaPhase"] = "2"
        else:
            del audit["params"]["ring1"]
        audit_path.write_text(json.dumps(audit), encoding="utf-8")
        return result

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="fixed-tls",
        builder_func=fake_builder,
    )

    assert report["status"] == "fail"
    assert report["tls_status"] == "fail"


def test_scene_workflow_concrete_failure_precedes_blocked_status(tmp_path: Path) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(
            output_dir,
            kwargs["prefix"],
            netconvert_returncode=7,
        )
        return {
            **result,
            "status": "blocked",
            "claim_status": "blocked",
            "netconvert_status": "fail",
            "sumo_smoke_status": "blocked",
        }

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="failed-and-blocked",
        builder_func=fake_builder,
    )

    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert report["netconvert_status"] == "fail"
    assert report["sumo_load_status"] == "blocked"


@pytest.mark.parametrize(
    ("stage_key", "report_key", "check_key", "build_stage", "report_stage"),
    [
        ("netconvert_status", "netconvert_report_file", "netconvert", "blocked", "fail"),
        ("netconvert_status", "netconvert_report_file", "netconvert", "fail", "blocked"),
        ("sumo_smoke_status", "sumo_report_file", "sumo_load", "blocked", "fail"),
        ("sumo_smoke_status", "sumo_report_file", "sumo_load", "fail", "blocked"),
    ],
)
def test_scene_workflow_stage_failure_precedes_conflicting_blocked_source(
    tmp_path: Path,
    stage_key: str,
    report_key: str,
    check_key: str,
    build_stage: str,
    report_stage: str,
) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(output_dir, kwargs["prefix"])
        report_path = Path(result[report_key])
        evidence = json.loads(report_path.read_text(encoding="utf-8"))
        evidence["status"] = report_stage
        report_path.write_text(json.dumps(evidence), encoding="utf-8")
        return {
            **result,
            "status": "blocked",
            "claim_status": "blocked",
            stage_key: build_stage,
        }

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="conflicting-stage",
        builder_func=fake_builder,
    )

    manifest = json.loads(Path(report["artifact_manifest_file"]).read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert manifest["checks"][check_key]["status"] == "fail"


@pytest.mark.parametrize(
    "case",
    [
        "nonzero-rc",
        "invalid-rc",
        "empty-cwd",
        "list-cwd",
        "non-string-target",
        "missing-command",
    ],
)
def test_scene_workflow_invalid_evidence_precedes_blocked_and_writes_manifest(
    tmp_path: Path,
    case: str,
) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(output_dir, kwargs["prefix"])
        report_path = Path(result["netconvert_report_file"])
        evidence = json.loads(report_path.read_text(encoding="utf-8"))
        if case == "nonzero-rc":
            evidence["returncode"] = 7
        elif case == "invalid-rc":
            evidence["returncode"] = "0"
        elif case in {"empty-cwd", "list-cwd"}:
            evidence["command"][-1] = Path(result["net_file"]).name
            evidence["cwd"] = [] if case == "empty-cwd" else ["bad"]
        elif case == "non-string-target":
            evidence["command"][-1] = 7
        else:
            del evidence["command"]
        report_path.write_text(json.dumps(evidence), encoding="utf-8")
        return {
            **result,
            "status": "blocked",
            "claim_status": "blocked",
            "netconvert_status": "pass"
            if case in {"empty-cwd", "list-cwd"}
            else "blocked",
        }

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="invalid-evidence",
        builder_func=fake_builder,
    )

    manifest_path = Path(report["artifact_manifest_file"])
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["netconvert_status"] == "fail"
    assert manifest["checks"]["netconvert"]["status"] == "fail"


@pytest.mark.parametrize(
    ("case", "check_keys"),
    [
        ("build-netconvert", ("netconvert",)),
        ("build-sumo", ("sumo_load",)),
        ("report-netconvert", ("netconvert",)),
        ("report-sumo", ("sumo_load",)),
        ("blocked-unknown-build-stages", ("netconvert", "sumo_load")),
    ],
)
def test_scene_workflow_unhashable_status_values_write_failed_manifest(
    tmp_path: Path,
    case: str,
    check_keys: tuple[str, ...],
) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(output_dir, kwargs["prefix"])
        if case == "build-netconvert":
            result["netconvert_status"] = []
        elif case == "build-sumo":
            result["sumo_smoke_status"] = []
        elif case == "blocked-unknown-build-stages":
            result.update(
                status="blocked",
                netconvert_status=[],
                sumo_smoke_status=[],
            )
        else:
            report_key = (
                "netconvert_report_file"
                if case == "report-netconvert"
                else "sumo_report_file"
            )
            report_path = Path(result[report_key])
            evidence = json.loads(report_path.read_text(encoding="utf-8"))
            evidence["status"] = []
            report_path.write_text(json.dumps(evidence), encoding="utf-8")
        return result

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="unhashable-status",
        builder_func=fake_builder,
    )

    manifest_path = Path(report["artifact_manifest_file"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert all(manifest["checks"][key]["status"] == "fail" for key in check_keys)


def test_scene_workflow_invalid_utf8_evidence_writes_failure_manifest(
    tmp_path: Path,
) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(output_dir, kwargs["prefix"])
        Path(result["netconvert_report_file"]).write_bytes(b"\xff")
        return result

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="invalid-utf8",
        builder_func=fake_builder,
    )

    manifest_path = Path(report["artifact_manifest_file"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert manifest["checks"]["netconvert"]["status"] == "fail"
    assert any("UnicodeDecodeError" in warning for warning in manifest["warnings"])


@pytest.mark.parametrize(
    ("netedit_status", "overall_status"),
    [("fail", "fail"), ("blocked", "blocked")],
)
def test_scene_workflow_netedit_status_does_not_downgrade_construction_claim(
    tmp_path: Path,
    netedit_status: str,
    overall_status: str,
) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        return _write_fake_backend_outputs(output_dir, kwargs["prefix"])

    def fake_netedit(_net_file: Path) -> dict[str, Any]:
        return {
            "status": netedit_status,
            "command": ["netedit-test", "-s", "scene.net.xml"],
            "warnings": [f"NetEdit {netedit_status}"],
        }

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="netedit-result",
        launch_netedit_after_build=True,
        builder_func=fake_builder,
        netedit_func=fake_netedit,
    )

    assert report["status"] == overall_status
    assert report["claim_status"] == "diagnostic-demo"
    assert report["netedit_status"] == netedit_status


def test_scene_workflow_records_cleanup_failure_without_calling_builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = "locked"
    stale = _write_fake_backend_outputs(tmp_path, prefix)
    locked_path = Path(stale["net_file"])
    original_unlink = Path.unlink
    builder_called = False
    attempted: list[Path] = []

    def locked_unlink(path: Path, *args: Any, **kwargs: Any) -> None:
        attempted.append(path)
        if path == locked_path:
            raise PermissionError("file is locked")
        original_unlink(path, *args, **kwargs)

    def fake_builder(_output_dir: Path, **_kwargs: Any) -> dict[str, Any]:
        nonlocal builder_called
        builder_called = True
        return {}

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix=prefix,
        builder_func=fake_builder,
    )

    manifest_path = tmp_path / f"{prefix}_artifact_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert builder_called is False
    assert report["status"] == "fail"
    assert manifest["checks"]["cleanup"]["status"] == "fail"
    assert "PermissionError" in manifest["checks"]["cleanup"]["error"]
    assert manifest["checks"]["routeability"]["status"] == "fail"
    assert "summary" not in manifest["checks"]["routeability"]
    assert tmp_path / f"{prefix}_sumo_report.json" in attempted
    assert Path(stale["summary_file"]).exists() is False
    assert manifest["output_files"] == {
        "artifact_manifest_file": f"{prefix}_artifact_manifest.json"
    }
    assert any("cleanup failed" in warning for warning in manifest["warnings"])


def test_scene_workflow_inventories_expected_sumo_logs_when_present(tmp_path: Path) -> None:
    def fake_builder(output_dir: Path, **kwargs: Any) -> dict[str, Any]:
        result = _write_fake_backend_outputs(output_dir, kwargs["prefix"])
        (output_dir / f'{kwargs["prefix"]}_sumo.log').write_text("ok", encoding="utf-8")
        (output_dir / f'{kwargs["prefix"]}_sumo_errors.log').write_text("", encoding="utf-8")
        return result

    report = run_intersection_scene_workflow(
        PROMPT,
        tmp_path,
        prefix="with-logs",
        builder_func=fake_builder,
    )

    manifest = json.loads(Path(report["artifact_manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["output_files"]["sumo_log_file"] == "with-logs_sumo.log"
    assert manifest["output_files"]["sumo_error_log_file"] == "with-logs_sumo_errors.log"
