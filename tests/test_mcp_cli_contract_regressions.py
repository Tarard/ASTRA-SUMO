from __future__ import annotations

import json

import anyio
import pytest

from torii_sumo import cli
from torii_sumo.server import create_server
from torii_sumo import mcp_contract_tools as contracts


DEFAULT_TOOL_NAMES = [
    "torii.preflight",
    "torii.config.inspect",
    "torii.run.compare",
    "torii.place.resolve",
    "torii.intersection.classify",
    "torii.signal.classify",
    "torii.network.audit",
    "torii.network.compare",
    "torii.demand.audit",
    "torii.review.create",
]


def test_create_server_defaults_to_reduced_tool_surface() -> None:
    tools = anyio.run(create_server().list_tools)

    assert [tool.name for tool in tools] == DEFAULT_TOOL_NAMES


@pytest.mark.parametrize("profile", ["default", "netedit"])
def test_reduced_profiles_publish_explicit_boolean_annotations(profile: str) -> None:
    tools = anyio.run(create_server(profile).list_tools)

    for tool in tools:
        assert tool.annotations is not None
        assert isinstance(tool.annotations.readOnlyHint, bool)
        assert isinstance(tool.annotations.destructiveHint, bool)
        assert isinstance(tool.annotations.idempotentHint, bool)
        assert isinstance(tool.annotations.openWorldHint, bool)


@pytest.mark.parametrize("profile", ["default", "netedit"])
def test_reduced_profiles_describe_every_input(profile: str) -> None:
    tools = anyio.run(create_server(profile).list_tools)

    for tool in tools:
        for schema in tool.inputSchema.get("properties", {}).values():
            assert schema.get("description")


def test_network_audit_schema_explains_its_bounded_profiles() -> None:
    tools = anyio.run(create_server("default").list_tools)
    audit = next(tool for tool in tools if tool.name == "torii.network.audit")
    profile = audit.inputSchema["properties"]["profile"]

    assert profile["enum"] == ["quick", "standard"]
    assert "topology" in profile["description"]
    assert "routeability" in profile["description"]


def test_intersection_traffic_side_schema_is_bounded() -> None:
    tools = anyio.run(create_server("default").list_tools)
    classify = next(tool for tool in tools if tool.name == "torii.intersection.classify")

    assert classify.inputSchema["properties"]["traffic_side"]["enum"] == [
        "left",
        "right",
    ]


def test_netedit_close_schema_requires_mode_and_describes_conditional_hash() -> None:
    tools = anyio.run(create_server("netedit").list_tools)
    close = next(tool for tool in tools if tool.name == "torii.netedit.close")
    screenshot_hash = close.inputSchema["properties"]["expected_screenshot_sha256"]

    assert "mode" in close.inputSchema["required"]
    assert "finalize" in screenshot_hash["description"]
    assert close.inputSchema["properties"]["reason"]["default"] == "caller_aborted"
    assert any(
        branch.get("pattern") == "^[0-9a-fA-F]{64}$"
        for branch in screenshot_hash["anyOf"]
    )


def test_network_compare_schema_does_not_advertise_unimplemented_promotion() -> None:
    tools = anyio.run(create_server("default").list_tools)
    tool = next(tool for tool in tools if tool.name == "torii.network.compare")

    assert "profile" not in tool.inputSchema["properties"]


def test_network_compare_accepts_the_legacy_standard_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        contracts,
        "sumo_network_connection_mode_regression_audit",
        lambda **_: {"status": "pass", "claim_status": "construction-check"},
    )

    result = contracts.torii_network_compare(
        "source.net.xml",
        "candidate.net.xml",
        "audit",
        profile="standard",
    )

    assert result.status == "pass"


def test_network_compare_rejects_the_removed_promotion_profile() -> None:
    with pytest.raises(ValueError, match="must be standard"):
        contracts.torii_network_compare(
            "source.net.xml",
            "candidate.net.xml",
            "audit",
            profile="promotion",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("profile", "bad_step", "bad_status"),
    [
        ("quick", "topology", "fail"),
        ("quick", "topology", "blocked"),
        ("standard", "topology", "fail"),
        ("standard", "topology", "blocked"),
        ("standard", "connection", "fail"),
        ("standard", "connection", "blocked"),
        ("standard", "overlap", "fail"),
        ("standard", "overlap", "blocked"),
    ],
)
def test_network_audit_preserves_failed_substep_status(
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
    bad_step: str,
    bad_status: str,
) -> None:
    passed = {"status": "pass", "claim_status": "construction-check"}
    failed = {"status": bad_status, "claim_status": "blocked"}
    monkeypatch.setattr(
        contracts,
        "sumo_network_topology_audit",
        lambda **_: failed if bad_step == "topology" else passed,
    )
    monkeypatch.setattr(
        contracts,
        "sumo_network_connection_mode_audit",
        lambda **_: failed if bad_step == "connection" else passed,
        raising=False,
    )
    monkeypatch.setattr(
        contracts,
        "sumo_network_overlapping_junction_audit",
        lambda **_: failed if bad_step == "overlap" else passed,
        raising=False,
    )

    result = contracts.torii_network_audit("network.net.xml", "audit", profile=profile)

    assert result.status == bad_status


def test_network_audit_uses_claim_from_the_highest_severity_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        contracts,
        "sumo_network_topology_audit",
        lambda **_: {"status": "blocked", "claim_status": "topology-blocked"},
    )
    monkeypatch.setattr(
        contracts,
        "sumo_network_connection_mode_audit",
        lambda **_: {"status": "fail", "claim_status": "connection-failed"},
    )
    monkeypatch.setattr(
        contracts,
        "sumo_network_overlapping_junction_audit",
        lambda **_: {"status": "pass", "claim_status": "construction-check"},
    )

    result = contracts.torii_network_audit(
        "network.net.xml",
        "audit",
        profile="standard",
    )

    assert result.status == "fail"
    assert result.claim_status == "connection-failed"


def test_network_audit_surfaces_actionable_overlap_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passed = {"status": "pass", "claim_status": "construction-check"}
    group = {"group_id": "OJ001", "node_ids": ["a", "b"]}
    monkeypatch.setattr(contracts, "sumo_network_topology_audit", lambda **_: passed)
    monkeypatch.setattr(contracts, "sumo_network_connection_mode_audit", lambda **_: passed)
    monkeypatch.setattr(
        contracts,
        "sumo_network_overlapping_junction_audit",
        lambda **_: {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "overlapping_junction_group_count": 1,
            "overlapping_junction_groups": [group],
            "groups_file": "overlap.csv",
            "summary_file": "overlap.json",
        },
    )

    result = contracts.torii_network_audit(
        "network.net.xml",
        "audit",
        profile="standard",
    )

    assert result.status == "review_required"
    assert result.findings == [group]
    assert {artifact.path for artifact in result.artifacts} == {
        "overlap.csv",
        "overlap.json",
    }


def test_default_network_audit_does_not_run_routeability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        contracts,
        "sumo_network_topology_audit",
        lambda **_: {"status": "pass", "claim_status": "construction-check"},
    )
    monkeypatch.setattr(
        contracts,
        "sumo_network_connection_mode_audit",
        lambda **_: {"status": "pass", "claim_status": "construction-check"},
        raising=False,
    )
    monkeypatch.setattr(
        contracts,
        "sumo_network_overlapping_junction_audit",
        lambda **_: {"status": "pass", "claim_status": "construction-check"},
        raising=False,
    )

    def reject_routeability(**_: object) -> dict[str, object]:
        pytest.fail("default network audit must not run the long routeability step")

    monkeypatch.setattr(
        contracts,
        "sumo_network_routeability_audit",
        reject_routeability,
        raising=False,
    )

    result = contracts.torii_network_audit("network.net.xml", "audit")

    assert result.status == "pass"


def test_network_audit_rejects_removed_profile_before_running_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        contracts,
        "sumo_network_topology_audit",
        lambda **_: pytest.fail("invalid profiles must fail before audit work starts"),
    )

    with pytest.raises(ValueError, match="quick or standard"):
        contracts.torii_network_audit(
            "network.net.xml",
            "audit",
            profile="promotion",  # type: ignore[arg-type]
        )


def test_cli_warn_status_returns_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "torii_preflight",
        lambda: contracts.ToriiToolResult(status="warn", summary="Review the warning."),
    )

    exit_code = cli.main(["preflight", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "warn"


def test_global_json_flag_applies_to_preflight(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "torii_preflight",
        lambda: contracts.ToriiToolResult(status="pass", summary="Ready."),
    )

    exit_code = cli.main(["--json", "preflight"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "pass"
