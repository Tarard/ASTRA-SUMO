import sys
import time
import os
from pathlib import Path
import subprocess
import json

import pytest

from astra_sumo.core import command_runner

from astra_sumo.core.command_runner import run_command


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


def test_native_fatal_message_does_not_wait_for_full_timeout(monkeypatch):
    monkeypatch.setattr(command_runner, "_is_sumo_command", lambda _command: True, raising=False)
    started = time.monotonic()
    result = run_command([sys.executable, "-c",
        "import sys,time;print('load failed',file=sys.stderr,flush=True);"
        "print('Quitting (on error).',file=sys.stderr,flush=True);time.sleep(10)"], timeout_seconds=3)
    assert result.status == "fail"
    assert time.monotonic() - started < 2.5
    assert "load failed" in result.stderr and "Quitting (on error)." in result.stderr


def test_fatal_words_in_an_ordinary_command_are_not_interpreted():
    result = run_command([sys.executable, "-c",
        "import time;print('Quitting (on error).',flush=True);time.sleep(1.2);print('handled')"], timeout_seconds=4)
    assert result.status == "pass"
    assert "handled" in result.stdout


def test_timeout_preserves_partial_utf8_output():
    result = run_command([sys.executable, "-c",
        "import os,time;os.write(1,'汉堡\\n'.encode());os.write(2,b'partial error\\n');time.sleep(10)"], timeout_seconds=.3)
    assert result.status == "timeout"
    assert result.stdout == "汉堡\n" and result.stderr == "partial error\n"


def test_file_capture_preserves_large_output_cwd_and_environment(tmp_path):
    result = run_command([sys.executable, "-c",
        "import os;os.write(1,b'x'*1000000);os.write(2,os.environ['ASTRA_CAPTURE_TEST'].encode());"
        "assert os.getcwd()==os.environ['ASTRA_CAPTURE_CWD']"], cwd=tmp_path,
        env={**os.environ, "ASTRA_CAPTURE_TEST": "preserved", "ASTRA_CAPTURE_CWD": str(tmp_path)}, timeout_seconds=5)
    assert result.status == "pass" and result.stdout == "x" * 1000000 and result.stderr == "preserved"


def test_quoted_fatal_words_are_not_a_native_exit_message(monkeypatch):
    monkeypatch.setattr(command_runner, "_is_sumo_command", lambda _command: True)
    result = run_command([sys.executable, "-c",
        "import time;print('Error example: Quitting (on error).',flush=True);time.sleep(1.2);print('done')"], timeout_seconds=4)
    assert result.status == "pass" and "done" in result.stdout


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1])
def test_invalid_deadlines_do_not_launch_a_command(timeout):
    result = run_command([sys.executable, "-c", "raise AssertionError('must not run')"], timeout_seconds=timeout)
    assert result.status == "fail" and result.returncode is None
    assert "finite" in result.error


def test_empty_command_and_missing_executable_are_reported():
    assert run_command([]).status == "fail"
    result = run_command(["astra-nonexistent-command-82d10"])
    assert result.status == "fail" and result.returncode is None


def test_timeout_stops_owned_children(tmp_path):
    marker = tmp_path / "child-survived.txt"
    child = "import time,pathlib;time.sleep(1.5);pathlib.Path(" + repr(str(marker)) + ").write_text('alive')"
    parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c'," + repr(child) + "]);print('started',flush=True);time.sleep(10)"
    result = run_command([sys.executable, "-c", parent], timeout_seconds=.4)
    assert result.status == "timeout"
    time.sleep(1.6)
    assert not marker.exists()


@pytest.fixture(scope="module")
def windows_native_fatal_case(tmp_path_factory):
    if os.name != "nt":
        pytest.skip("Windows native fatal-exit regression")
    import sumo
    from astra_sumo.core.osm_network import build_osm_network
    from astra_sumo.core.candidate_contracts import file_sha256
    import xml.etree.ElementTree as ET

    binary = Path(sumo.__file__).parent / "bin"
    if not (binary / "sumo.exe").is_file():
        pytest.skip("Locked SUMO distribution required")
    root = tmp_path_factory.mktemp("native-fatal-commands")
    source = Path(__file__).parent / "fixtures/osm_bus_access_118.osm.xml"
    source_hash = file_sha256(source)
    built = build_osm_network(bbox="9.985,53.554,9.993,53.560", output_dir=root / "network",
        source_osm_path=source, allowed_highways={"service"}, clip_source_ways_to_bbox=False,
        netconvert_binary=str(binary / "netconvert.exe"))
    assert built["status"] == "pass", built
    network = Path(built["net_file"])
    edge = next(row.get("id") for row in ET.parse(network).getroot().findall("edge")
                if not row.get("function") and "219999179" in row.get("id"))
    routes = ET.Element("routes")
    ET.SubElement(routes, "vType", id="forbidden", vClass="bicycle")
    vehicle = ET.SubElement(routes, "vehicle", id="forbidden", type="forbidden", depart="0")
    ET.SubElement(vehicle, "route", edges=edge)
    route_file = root / "forbidden.rou.xml"
    ET.ElementTree(routes).write(route_file, encoding="utf-8", xml_declaration=True)
    assert file_sha256(source) == source_hash
    return network, route_file, {"native": binary / "sumo.exe", "launcher": Path(sys.executable).parent / "sumo.exe"}


@pytest.mark.parametrize("binary_kind", ["native", "launcher"])
@pytest.mark.parametrize("repeat", range(10))
def test_windows_native_fatal_exit_is_bounded_and_retains_error(windows_native_fatal_case, tmp_path, binary_kind, repeat):
    network, route_file, binaries = windows_native_fatal_case
    command = [str(binaries[binary_kind]), "-n", str(network), "-r", str(route_file), "--end", "2",
        "--no-step-log", "true", "--duration-log.disable", "true", "--error-log", str(tmp_path / "native error.log")]
    request = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request.write_text(json.dumps(command), encoding="utf-8")
    worker = ("import json,sys,time;from pathlib import Path;from astra_sumo.core.command_runner import run_command;"
        "started=time.monotonic();r=run_command(json.loads(Path(sys.argv[1]).read_text()),cwd=Path(sys.argv[2]),timeout_seconds=10);"
        "Path(sys.argv[3]).write_text(json.dumps({'elapsed':time.monotonic()-started,'result':r.to_dict()}),encoding='utf-8')")
    with (tmp_path / "worker.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-c", worker, str(request), str(tmp_path), str(result_path)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        try:
            process.wait(timeout=8)
        finally:
            if process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=3, check=False)
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)
    assert process.returncode == 0, (tmp_path / "worker.log").read_text(encoding="utf-8")
    outcome = json.loads(result_path.read_text(encoding="utf-8"))
    assert outcome["elapsed"] < 6
    result = outcome["result"]
    assert result["status"] == "fail" and result["returncode"] not in (None, 0)
    assert "not allowed to depart" in result["stderr"] and "Quitting (on error)" in result["stderr"]
    assert result["command"] == command
