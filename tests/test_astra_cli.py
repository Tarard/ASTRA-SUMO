from __future__ import annotations

import json

import pytest

from astra_sumo.cli import main


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


def test_json_result_is_printed_as_one_indented_document(capsys) -> None:
    from astra_sumo import cli

    result = {"status": "pass", "nested": {"b": [1, 2.5, None], "a": "ä </x>"}, "empty": {}}
    assert cli._emit(result, json_output=True) == 0
    assert capsys.readouterr().out == json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with pytest.raises(TypeError):
        cli._emit({"status": "pass", "bad": object()}, json_output=True)
    assert capsys.readouterr().out == ""


def test_json_result_stdout_cannot_encode_prints_nothing(monkeypatch) -> None:
    import io
    import sys

    from astra_sumo import cli

    raw = io.BytesIO()
    stdout = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="")
    monkeypatch.setattr(sys, "stdout", stdout)
    # The characters stdout cannot encode come after more JSON than one copied block.
    result = {"a": ["Straße"] * 3, "b": "x" * (3 << 20), "c": "Děčín", "status": "pass"}
    with pytest.raises(UnicodeEncodeError):
        cli._emit(result, json_output=True)
    stdout.flush()
    assert raw.getvalue() == b""

    replacing = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="replace", newline="")
    monkeypatch.setattr(sys, "stdout", replacing)
    assert cli._emit(result, json_output=True) == 0
    replacing.flush()
    expected = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("cp1252", "replace")
    assert replacing.buffer.getvalue() == expected
