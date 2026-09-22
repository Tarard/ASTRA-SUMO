from __future__ import annotations

import argparse
import json
from pathlib import Path

from torii_sumo.core.reference_teacher_curriculum import (
    build_reference_teacher_curriculum,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a spatially held-out Ingolstadt raw-versus-human-cleaned curriculum "
            "from Torii's existing same-bbox audits."
        )
    )
    parser.add_argument("--teacher-action-contracts", required=True, type=Path)
    parser.add_argument("--reference-join-audit", required=True, type=Path)
    parser.add_argument("--raw-net", required=True, type=Path)
    parser.add_argument("--teacher-net", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--spatial-tile-m", type=float, default=500.0)
    parser.add_argument("--held-out-modulus", type=int, default=5)
    parser.add_argument("--screenshot-case-limit", type=int, default=18)
    args = parser.parse_args()

    report = build_reference_teacher_curriculum(
        teacher_action_contracts_file=args.teacher_action_contracts,
        reference_join_audit_file=args.reference_join_audit,
        raw_net_file=args.raw_net,
        teacher_net_file=args.teacher_net,
        output_dir=args.output_dir,
        spatial_tile_m=args.spatial_tile_m,
        held_out_modulus=args.held_out_modulus,
        screenshot_case_limit=args.screenshot_case_limit,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "case_count": report["case_count"],
                "split_counts": report["split_counts"],
                "action_family_counts": report["action_family_counts"],
                "screenshot_queue_count": len(report["screenshot_queue"]),
                "promotion_gate_status": report["promotion_gate_status"],
                "report_file": report["report_file"],
                "manifest_file": report["manifest_file"],
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "review_ready" else 3


if __name__ == "__main__":
    raise SystemExit(main())
