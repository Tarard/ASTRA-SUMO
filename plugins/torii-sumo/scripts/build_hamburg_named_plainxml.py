from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from torii_sumo.road_network.official_plainxml import (
    OfficialPlainXmlError,
    materialize_hamburg_hh_sib_snapshot_plainxml_candidate,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a hash-bound, geometry-only HH-SIB PlainXML candidate for the "
            "named Am Sandtorkai 2349/2394/2403 corridor."
        )
    )
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--request-url", required=True)
    parser.add_argument("--bbox", required=True, nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    parser.add_argument("--target-time", required=True)
    parser.add_argument("--retrieved-at", required=True)
    parser.add_argument("--corridor-scope", required=True, type=Path)
    parser.add_argument("--named-scope", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--prefix", default="named_corridor_official")
    return parser


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include an explicit UTC offset")
    return parsed


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        scope = json.loads(args.corridor_scope.resolve(strict=True).read_text(encoding="utf-8"))
        if not isinstance(scope, dict):
            raise ValueError("--corridor-scope must contain a JSON object")
        report = materialize_hamburg_hh_sib_snapshot_plainxml_candidate(
            snapshot_file=args.snapshot,
            request_url=args.request_url,
            bbox=tuple(args.bbox),
            target_time=_utc(args.target_time),
            retrieved_at=_utc(args.retrieved_at),
            corridor_scope=scope,
            output_dir=args.output_dir,
            prefix=args.prefix,
            named_scope_manifest_file=args.named_scope,
        )
    except (OfficialPlainXmlError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "schema": "torii.hamburg-named-plainxml-cli-error/v1",
                    "status": "blocked",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "schema": "torii.hamburg-named-plainxml-cli-result/v1",
                "status": report["status"],
                "candidate_id": report["candidate_id"],
                "output_dir": report["output_dir"],
                "gates": report["gates"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
