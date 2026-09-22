from __future__ import annotations

import anyio
import pytest

from torii_sumo import cli
from torii_sumo.server import create_server
from torii_sumo import mcp_contract_tools as contract


SCREENSHOT_SHA256 = "a" * 64


def _passing_close_result(**kwargs: object) -> dict[str, object]:
    return {
        "status": "pass",
        "operation": kwargs["operation"],
    }


def test_netedit_close_finalize_passes_expected_screenshot_sha256(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_session(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return _passing_close_result(**kwargs)

    monkeypatch.setattr(contract, "sumo_netedit_session", fake_session)

    result = contract.torii_netedit_close(
        "session-1",
        mode="finalize",
        expected_screenshot_sha256=SCREENSHOT_SHA256,
    )

    assert result.status == "pass"
    assert calls[0]["operation"] == "finalize"
    assert calls[0]["session_id"] == "session-1"
    assert calls[0]["expected_screenshot_sha256"] == SCREENSHOT_SHA256


def test_netedit_close_abort_does_not_require_screenshot_sha256(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_session(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return _passing_close_result(**kwargs)

    monkeypatch.setattr(contract, "sumo_netedit_session", fake_session)

    result = contract.torii_netedit_close(
        "session-2",
        mode="abort",
        reason="review_rejected",
    )

    assert result.status == "pass"
    assert calls[0]["operation"] == "abort"
    assert calls[0]["session_id"] == "session-2"
    assert calls[0]["reason"] == "review_rejected"


def test_netedit_close_abort_uses_the_mcp_default_reason(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_session(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return _passing_close_result(**kwargs)

    monkeypatch.setattr(contract, "sumo_netedit_session", fake_session)

    contract.torii_netedit_close("session-2", mode="abort")

    assert calls[0]["reason"] == "caller_aborted"


def test_netedit_close_does_not_claim_success_when_finalize_is_blocked(monkeypatch) -> None:
    monkeypatch.setattr(
        contract,
        "sumo_netedit_session",
        lambda **_: {
            "status": "blocked",
            "reason": "latest screenshot no longer matches",
        },
    )

    result = contract.torii_netedit_close(
        "session-2",
        mode="finalize",
        expected_screenshot_sha256=SCREENSHOT_SHA256,
    )

    assert result.status == "blocked"
    assert "blocked" in result.summary.lower()
    assert "finalized and closed" not in result.summary.lower()


@pytest.mark.parametrize("status", ["blocked", "fail"])
def test_netedit_close_does_not_claim_success_when_finalize_audits_fail(
    monkeypatch,
    status: str,
) -> None:
    monkeypatch.setattr(
        contract,
        "sumo_netedit_session",
        lambda **_: {
            "status": status,
            "operation_status": "pass",
            "session_state": "finalized",
        },
    )

    result = contract.torii_netedit_close(
        "session-2",
        mode="finalize",
        expected_screenshot_sha256=SCREENSHOT_SHA256,
    )

    assert result.status == status
    assert "finalized and closed" not in result.summary.lower()


async def _netedit_tools() -> list[object]:
    return await create_server("netedit").list_tools()


def test_netedit_observe_annotations_report_its_write_side_effects() -> None:
    tools = anyio.run(_netedit_tools)
    observe = next(tool for tool in tools if tool.name == "torii.netedit.observe")

    assert observe.annotations is not None
    assert observe.annotations.readOnlyHint is False
    assert observe.annotations.idempotentHint is False


def test_cli_cannot_finalize_a_session_from_another_process(monkeypatch, capsys) -> None:
    calls: list[dict[str, object]] = []

    def fake_close(
        session_id: str,
        *,
        mode: str,
        expected_screenshot_sha256: str | None = None,
        reason: str | None,
    ) -> contract.ToriiToolResult:
        calls.append(
            {
                "session_id": session_id,
                "mode": mode,
                "expected_screenshot_sha256": expected_screenshot_sha256,
                "reason": reason,
            }
        )
        return contract.ToriiToolResult(status="pass", summary="NetEdit session closed.")

    monkeypatch.setattr(cli, "torii_netedit_close", fake_close)

    exit_code = cli.main(
        [
            "netedit",
            "close",
            "session-3",
            "--mode",
            "finalize",
            "--expected-screenshot-sha256",
            SCREENSHOT_SHA256,
            "--json",
        ]
    )
    capsys.readouterr()

    assert exit_code == 3
    assert not calls


def test_cli_cannot_abort_a_session_from_another_process(monkeypatch, capsys) -> None:
    calls: list[dict[str, object]] = []

    def fake_close(
        session_id: str,
        *,
        mode: str,
        expected_screenshot_sha256: str | None = None,
        reason: str | None,
    ) -> contract.ToriiToolResult:
        calls.append(
            {
                "session_id": session_id,
                "mode": mode,
                "expected_screenshot_sha256": expected_screenshot_sha256,
                "reason": reason,
            }
        )
        return contract.ToriiToolResult(status="pass", summary="NetEdit session closed.")

    monkeypatch.setattr(cli, "torii_netedit_close", fake_close)

    exit_code = cli.main(
        [
            "netedit",
            "close",
            "session-4",
            "--mode",
            "abort",
            "--reason",
            "review_rejected",
            "--json",
        ]
    )
    capsys.readouterr()

    assert exit_code == 3
    assert not calls
