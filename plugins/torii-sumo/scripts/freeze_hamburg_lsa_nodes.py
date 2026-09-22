from __future__ import annotations

import argparse
import json
from pathlib import Path

from torii_sumo.core.hamburg_lsa_nodes import (
    GeographicBBox,
    HamburgLsaNodeRequest,
    freeze_hamburg_lsa_node_evidence,
)


def _request(value: str) -> HamburgLsaNodeRequest:
    node_id, separator, roads = value.partition("=")
    if not separator or not node_id.strip() or not roads.strip():
        raise argparse.ArgumentTypeError("--node must use NODE=ROAD1|ROAD2")
    road_names = tuple(item.strip() for item in roads.split("|"))
    try:
        return HamburgLsaNodeRequest.create(
            expected_node_id=node_id,
            road_name_components=road_names,
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze official Hamburg LSA node identity and point geometry "
            "from the OGC API."
        )
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--bbox", required=True, type=GeographicBBox.parse)
    parser.add_argument(
        "--node",
        action="append",
        required=True,
        type=_request,
        metavar="NODE=ROAD1|ROAD2",
        help="repeat once per requested node; road names are matched exactly after normalization",
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    args = parser.parse_args()
    try:
        report = freeze_hamburg_lsa_node_evidence(
            args.output_dir,
            bbox=args.bbox,
            requests=tuple(args.node),
            limit=args.limit,
            timeout_seconds=args.timeout_seconds,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "decision": "review_required", "error": str(exc)}))
        return 2
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if report["decision"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
