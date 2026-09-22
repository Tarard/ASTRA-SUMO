from __future__ import annotations

import argparse
import json
from pathlib import Path

from torii_sumo.core.hamburg_official_road_scope import (
    HamburgOfficialRoadScopeError,
    materialize_hamburg_official_road_feature_scope,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write a bounded, hash-bound subset of an official Hamburg HH-SIB GeoJSON snapshot."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--feature-id", action="append", required=True)
    parser.add_argument("--scope-id", default="hamburg_official_road_scope")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    try:
        report = materialize_hamburg_official_road_feature_scope(
            source_file=args.source,
            feature_ids=args.feature_id,
            output_file=args.output,
            manifest_file=args.manifest,
            scope_id=args.scope_id,
        )
    except (HamburgOfficialRoadScopeError, OSError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
