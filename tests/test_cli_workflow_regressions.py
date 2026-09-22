from __future__ import annotations

import json
import subprocess
import sys

import pytest

from torii_sumo import cli
from torii_sumo import mcp_contract_tools as contract


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("complete", 0),
        ("ready", 0),
        ("review_ready", 1),
        ("topology_ready", 1),
        ("classified", 1),
        ("fail", 3),
        ("blocked", 3),
        ("error", 3),
        ("timeout", 3),
        ("cleanup_failed", 3),
        ("fetch_error", 3),
        ("blocked_pending_review", 3),
        ("invalid", 3),
        (None, 3),
    ],
)
def test_cli_maps_domain_statuses_to_exit_codes(status: str | None, expected: int) -> None:
    assert cli._exit_code({"status": status}) == expected


def test_cli_network_routeability_runs_the_long_check(monkeypatch, capsys) -> None:
    calls: list[dict[str, object]] = []

    def fake_routeability(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {"status": "pass", "claim_status": "construction-check"}

    monkeypatch.setattr(cli, "sumo_network_routeability_audit", fake_routeability, raising=False)

    exit_code = cli.main(
        [
            "network",
            "routeability",
            "network.net.xml",
            "audit-output",
            "--vehicle-count",
            "12",
            "--timeout-seconds",
            "90",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "pass"
    assert calls == [
        {
            "net_file": "network.net.xml",
            "output_dir": "audit-output",
            "prefix": "routeability_audit",
            "vehicle_count": 12,
            "seed": 42,
            "initial_end": 300,
            "max_end": 2400,
            "timeout_seconds": 90.0,
        }
    ]


def test_cli_import_does_not_load_the_legacy_bundle() -> None:
    deferred_modules = {
        "torii_sumo.legacy_tools",
        "torii_sumo.tools.digital_twin_tools",
        "torii_sumo.tools.road_network_tools",
        "torii_sumo.tools.run_tools",
        "torii_sumo.tools.workflow_tools",
    }
    script = (
        "import sys; "
        "import torii_sumo.cli; "
        f"deferred={deferred_modules!r}; "
        "assert deferred.isdisjoint(sys.modules), deferred.intersection(sys.modules)"
    )

    subprocess.run([sys.executable, "-c", script], check=True)


def test_manifest_workflow_exposes_long_and_specialized_families() -> None:
    from torii_sumo.legacy_tools import WORKFLOW_TOOLS

    expected = {
        "sumo_run_config",
        "sumo_osm_cleanup_workflow",
        "sumo_network_corridor_candidate_gates",
        "sumo_intersection_scene_workflow",
        "sumo_detector_route_sampler_calibrate",
        "sumo_hamburg_sandtorkai_execution_plan",
        "sumo_collect_evidence",
    }

    assert expected <= set(cli._WORKFLOW_TOOL_NAMES)
    assert set(cli._WORKFLOW_TOOL_NAMES) == set(WORKFLOW_TOOLS)


def test_manifest_workflow_calls_allowlisted_function(monkeypatch, tmp_path, capsys) -> None:
    from torii_sumo.legacy_tools import WORKFLOW_TOOLS

    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "output_dir": "out",
                "bbox": "1,2,3,4",
                "profile": "standard",
                "traffic_layers": "passenger",
            }
        ),
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def fake_workflow(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {"status": "pass", "claim_status": "construction-check"}

    monkeypatch.setitem(WORKFLOW_TOOLS, "sumo_osm_cleanup_workflow", fake_workflow)

    exit_code = cli.main(["workflow", "sumo_osm_cleanup_workflow", str(request), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "pass"
    assert calls == [
        {
            "output_dir": "out",
            "bbox": "1,2,3,4",
            "profile": "standard",
            "traffic_layers": "passenger",
        }
    ]


def test_manifest_workflow_rejects_non_object_json(tmp_path, capsys) -> None:
    request = tmp_path / "request.json"
    request.write_text("[]", encoding="utf-8")

    exit_code = cli.main(["workflow", "sumo_run_config", str(request), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 3
    assert payload["status"] == "error"
    assert "JSON object" in payload["error"]


def test_persistent_session_abort_preserves_the_core_default_reason(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_session(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {"status": "pass", "operation": kwargs["operation"]}

    monkeypatch.setattr(contract, "sumo_netedit_session", fake_session)

    result = contract.torii_netedit_close("session-2", mode="abort")
    assert result.status == "pass"
    assert calls[0]["reason"] == "caller_aborted"
