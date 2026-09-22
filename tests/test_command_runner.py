import sys

from torii_sumo.core.command_runner import run_command


def test_run_command_captures_stdout_and_stderr() -> None:
    result = run_command(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr)",
        ],
        timeout_seconds=5,
    )

    assert result.status == "pass"
    assert result.returncode == 0
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"


def test_run_command_reports_nonzero_exit() -> None:
    result = run_command(
        [sys.executable, "-c", "import sys; print('bad'); sys.exit(7)"],
        timeout_seconds=5,
    )

    assert result.status == "fail"
    assert result.returncode == 7
    assert "bad" in result.stdout


def test_run_command_reports_timeout() -> None:
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        timeout_seconds=0.1,
    )

    assert result.status == "timeout"
    assert result.returncode is None
    assert "timed out" in result.error
