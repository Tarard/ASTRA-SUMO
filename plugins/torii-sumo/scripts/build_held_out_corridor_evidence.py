from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from torii_sumo.corridor.held_out_corridor_runner import (
    build_held_out_corridor_machine_evidence,
)


BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build SUMO networks, machine audits, runtime smoke evidence, and a "
            "blinded human-review queue for frozen held-out OSM corridors."
        )
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=BENCHMARK_DIR / "held_out_corpus.v1.json",
    )
    parser.add_argument("--snapshot-report", type=Path, required=True)
    parser.add_argument(
        "--policy",
        type=Path,
        default=BENCHMARK_DIR / "held_out_review_preregistration.v1.json",
    )
    parser.add_argument(
        "--certification-envelope",
        type=Path,
        default=BENCHMARK_DIR / "vehicle_x4_certification_envelope.v1.json",
    )
    parser.add_argument(
        "--toolchain-lock",
        type=Path,
        default=BENCHMARK_DIR / "toolchain.lock.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sumo-home", type=Path, required=True)
    parser.add_argument(
        "--only-corridor",
        action="append",
        default=[],
        help="Process one corridor key; repeat for more. A partial run stays blocked.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    args = parser.parse_args()
    report = build_held_out_corridor_machine_evidence(
        args.spec,
        snapshot_report_file=args.snapshot_report,
        held_out_review_policy_file=args.policy,
        certification_envelope_file=args.certification_envelope,
        toolchain_lock_file=args.toolchain_lock,
        output_dir=args.output_dir,
        sumo_home=args.sumo_home,
        only_corridor_keys=tuple(args.only_corridor),
        timeout_seconds=args.timeout_seconds,
    )
    print(
        json.dumps(
            {
                "evidence_build_status": report["evidence_build_status"],
                "automatic_promotion_gate": report["automatic_promotion_gate"],
                "processed_case_count": report["processed_case_count"],
                "machine_label_counts": {
                    label: sum(
                        item["machine_label"] == label for item in report["results"]
                    )
                    for label in ("defect", "ambiguous", "acceptable")
                },
                "blockers": report["blockers"],
                "report_file": report["report_file"],
                "manifest_file": report["manifest_file"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
