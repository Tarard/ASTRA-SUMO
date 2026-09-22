from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from torii_sumo.core.junction_connection_audit import build_connection_signature


def _compact_signature(net_file: Path, junction_id: str) -> dict[str, Any]:
    signature = build_connection_signature(net_file, junction_id)
    records = signature["connection_records"]
    return {
        "incoming_edges": len(signature["incoming_edges"]),
        "outgoing_edges": len(signature["outgoing_edges"]),
        "scoped_connection_count": len(records),
        "connections_by_dir": {
            key: int(value) for key, value in sorted(Counter((record.get("dir") or "") for record in records).items())
        },
        "top_external_dir_counts": signature["top_external_dir_counts"],
        "crossing_count": signature["crossing_count"],
        "walkingarea_count": signature["walkingarea_count"],
        "controlled_link_count": signature["controlled_link_count"],
    }


def _junction_center(net_file: Path, junction_id: str) -> str:
    root = ET.parse(net_file).getroot()
    junction = root.find(f"junction[@id='{junction_id}']")
    if junction is None:
        raise SystemExit(f"missing junction {junction_id!r} in {net_file}")
    return f"{junction.attrib['x']},{junction.attrib['y']}"


def summarize_connection_audit(teacher_net_file: Path, candidate_net_file: Path, junction_id: str) -> dict[str, Any]:
    teacher = _compact_signature(teacher_net_file, junction_id)
    candidate = _compact_signature(candidate_net_file, junction_id)
    return {
        "teacher": teacher,
        "candidate": candidate,
        "equal_signature": teacher == candidate,
        "teacher_center": _junction_center(teacher_net_file, junction_id),
        "candidate_center": _junction_center(candidate_net_file, junction_id),
    }


def resolve_inputs(
    *,
    report_file: Path | None = None,
    teacher_net_file: Path | None = None,
    candidate_net_file: Path | None = None,
    junction_id: str | None = None,
) -> tuple[Path, Path, str]:
    if report_file is not None:
        report = json.loads(report_file.read_text(encoding="utf-8"))
        teacher_net_file = Path(report["teacher_net_file"])
        candidate_net_file = Path(report["final_net_file"])
        junction_id = str(report["junction_id"])
    if teacher_net_file is None or candidate_net_file is None or not junction_id:
        raise SystemExit("provide --report-file or --teacher-net/--candidate-net/--junction-id")
    return teacher_net_file, candidate_net_file, junction_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Write compact TUM-vs-candidate connection audit evidence.")
    parser.add_argument("--report-file", type=Path)
    parser.add_argument("--teacher-net", type=Path)
    parser.add_argument("--candidate-net", type=Path)
    parser.add_argument("--junction-id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    teacher, candidate, junction_id = resolve_inputs(
        report_file=args.report_file,
        teacher_net_file=args.teacher_net,
        candidate_net_file=args.candidate_net,
        junction_id=args.junction_id,
    )
    report = summarize_connection_audit(teacher, candidate, junction_id)
    body = json.dumps(report, indent=2, ensure_ascii=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(body + "\n", encoding="utf-8")
    print(body)


if __name__ == "__main__":
    main()
