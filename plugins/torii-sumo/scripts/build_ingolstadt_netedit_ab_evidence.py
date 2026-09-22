from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from torii_sumo.core.ingolstadt_netedit_ab_evidence import (
    build_ingolstadt_netedit_ab_evidence,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bind existing raw and human-cleaned Ingolstadt direct NetEdit audits "
            "and PNGs into one non-promoting A/B evidence manifest."
        )
    )
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--action-id", required=True)
    parser.add_argument("--raw-audit", required=True, type=Path)
    parser.add_argument("--human-audit", required=True, type=Path)
    parser.add_argument("--output-file", required=True, type=Path)
    args = parser.parse_args(argv)

    report = build_ingolstadt_netedit_ab_evidence(
        case_id=args.case_id,
        action_id=args.action_id,
        raw_audit_file=args.raw_audit,
        human_audit_file=args.human_audit,
        output_file=args.output_file,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "case_id": report["case_id"],
                "action_id": report["action_id"],
                "paired_view_count": len(report["paired_views"]),
                "screenshots_are_auxiliary": report["screenshots_are_auxiliary"],
                "view_center_registration": report["view_center_registration"]["status"],
                "promotion_gate_status": report["promotion_gate_status"],
                "manifest_file": report["manifest_file"],
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "review_material_ready" else 3


if __name__ == "__main__":
    raise SystemExit(main())
