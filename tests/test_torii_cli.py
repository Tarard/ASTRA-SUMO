from __future__ import annotations

import json

from torii_sumo.cli import main


def test_cli_preflight_emits_structured_json(capsys) -> None:
    exit_code = main(["preflight", "--json"])
    out = capsys.readouterr().out

    payload = json.loads(out)

    assert exit_code == 0
    assert payload["status"] == "pass"
    assert payload["summary"]
    assert "claim_status" in payload
    assert "next_actions" in payload


def test_cli_invalid_tool_input_fails_without_traceback(capsys) -> None:
    exit_code = main(["config", "inspect", "missing-a.sumocfg", "missing-b.sumocfg", "--json"])
    out = capsys.readouterr().out

    payload = json.loads(out)

    assert exit_code == 3
    assert payload["status"] in {"fail", "error"}
    assert "Traceback" not in out


def test_cli_help_lists_primary_groups(capsys) -> None:
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("argparse --help should exit")

    out = capsys.readouterr().out

    assert "preflight" in out
    assert "network" in out
    assert "mcp" in out
