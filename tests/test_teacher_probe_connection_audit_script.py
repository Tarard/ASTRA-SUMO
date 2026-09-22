import importlib.util
import json
from pathlib import Path
import subprocess
import sys


SCRIPT = Path("plugins/torii-sumo/scripts/teacher_probe_connection_audit.py")


def load_script():
    spec = importlib.util.spec_from_file_location("teacher_probe_connection_audit", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _net(path: Path, *, center: tuple[int, int]) -> None:
    path.write_text(
        f"""<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger"/></edge>
  <edge id="out" from="j" to="b"><lane id="out_0" index="0" allow="passenger"/></edge>
  <edge id=":j_c0" function="crossing"><lane id=":j_c0_0" index="0" allow="pedestrian"/></edge>
  <edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0" allow="pedestrian"/></edge>
  <junction id="a" x="-10" y="0" type="priority"/>
  <junction id="j" x="{center[0]}" y="{center[1]}" type="priority"/>
  <junction id="b" x="10" y="0" type="priority"/>
  <connection from="in" to="out" fromLane="0" toLane="0" dir="s"/>
</net>
""",
        encoding="utf-8",
    )


def test_summarize_connection_audit_writes_compact_signature_and_centers(tmp_path: Path) -> None:
    module = load_script()
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    _net(teacher, center=(1, 2))
    _net(candidate, center=(3, 4))

    report = module.summarize_connection_audit(teacher, candidate, "j")

    assert report["equal_signature"] is True
    assert report["teacher_center"] == "1,2"
    assert report["candidate_center"] == "3,4"
    assert report["teacher"]["connections_by_dir"] == {"s": 1}
    assert report["candidate"]["crossing_count"] == 1
    assert report["candidate"]["walkingarea_count"] == 1


def test_resolve_inputs_can_read_teacher_guided_report(tmp_path: Path) -> None:
    module = load_script()
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    report_file = tmp_path / "teacher_guided_report.json"
    _net(teacher, center=(1, 2))
    _net(candidate, center=(3, 4))
    report_file.write_text(
        json.dumps({"junction_id": "j", "teacher_net_file": str(teacher), "final_net_file": str(candidate)}),
        encoding="utf-8",
    )

    resolved = module.resolve_inputs(report_file=report_file)

    assert resolved == (teacher, candidate, "j")


def test_script_runs_without_external_pythonpath(tmp_path: Path) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    output = tmp_path / "audit.json"
    _net(teacher, center=(1, 2))
    _net(candidate, center=(3, 4))

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--teacher-net",
            str(teacher),
            "--candidate-net",
            str(candidate),
            "--junction-id",
            "j",
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert '"equal_signature": true' in completed.stdout
    assert json.loads(output.read_text(encoding="utf-8"))["candidate_center"] == "3,4"
