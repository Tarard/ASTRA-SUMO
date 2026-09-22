from __future__ import annotations

import argparse
import json
from pathlib import Path

from torii_sumo.core.reference_spatial_registration import (
    build_reference_spatial_registration,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register same-place raw and human-cleaned Ingolstadt junctions."
    )
    parser.add_argument("--teacher-actions", type=Path, required=True)
    parser.add_argument("--raw-net", type=Path, required=True)
    parser.add_argument("--teacher-net", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--core-residual-m", type=float, default=10.0)
    args = parser.parse_args()
    report = build_reference_spatial_registration(
        teacher_action_contracts_file=args.teacher_actions,
        raw_net_file=args.raw_net,
        teacher_net_file=args.teacher_net,
        output_dir=args.out_dir,
        core_residual_m=args.core_residual_m,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "review_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
