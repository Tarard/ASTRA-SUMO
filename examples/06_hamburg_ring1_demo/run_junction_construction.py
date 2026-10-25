"""Continue a frozen road-scope run through official junctions and field-loop layout."""
import argparse
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.hamburg_junctions.workflow import rebuild_hamburg_official_corridor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("road_case", type=Path)
    parser.add_argument("official_sources", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    case_file = args.road_case.resolve(strict=True)
    case = json.loads(case_file.read_text(encoding="utf-8"))
    selection = json.loads(Path(case["road_selection_report"]).read_text(encoding="utf-8"))
    source = Path(selection["candidate"]["path"]).resolve(strict=True)
    binary_dir = Path(sumo.__file__).resolve().parent / "bin"
    suffix = ".exe" if sys.platform == "win32" else ""
    counts = root / "benchmarks/hamburg_ring1_count_window_20261020_v1"
    report = rebuild_hamburg_official_corridor(source_net=source, official_sources=args.official_sources,
        lsa_identity=root / "benchmarks/hamburg_ring1_official_sources_20261020_v1/lsa-identities.json",
        lane_count_edits=Path(__file__).with_name("junction-edits.json"),
        count_fields=counts / "fields.raw.json", count_stations=counts / "stations.raw.json",
        output_dir=args.output_dir, netconvert_binary=str(binary_dir / f"netconvert{suffix}"),
        sumo_binary=str(binary_dir / f"sumo{suffix}"))
    report["road_scope_case"] = {"path": str(case_file), "sha256": file_sha256(case_file)}
    report["road_scope_candidate_matches"] = file_sha256(source) == selection["candidate"]["sha256"]
    if not report["road_scope_candidate_matches"]:
        report["warnings"].append("The source network differs from the supplied road-scope result. Its actual hash is recorded.")
    reference_file = Path(__file__).with_name("replay-reference-v1.json")
    reference = json.loads(reference_file.read_text(encoding="utf-8"))
    if report.get("content_hashes"):
        actual = {**report["content_hashes"], "official_motor_movements": report["movement_coverage"]["official"],
                  "passed_official_movements": report["movement_coverage"]["passed"], "field_loop_count": report["field_detector_count"]}
        checks = {key: actual.get(key) == value for key, value in reference["expected"].items()}
        report["reproduction"] = {"status": "matches_reference" if all(checks.values()) else "different_reference",
            "reference": {"path": str(reference_file), "sha256": file_sha256(reference_file)},
            "canonicalization": reference["canonicalization"], "actual": actual, "checks": checks}
        if not all(checks.values()):
            report["warnings"].append("[reproduction] The output differs from the saved reference. The measured result remains available.")
    else:
        report["reproduction"] = {"status": "not_run"}
    write_json_atomic(Path(report["report_file"]), report)
    print(json.dumps({key: report.get(key) for key in ("status", "report_file", "movement_coverage", "inputs_unchanged", "error")}), flush=True)
    return 0 if report["status"] == "measured" else 2


if __name__ == "__main__":
    raise SystemExit(main())
