from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from torii_sumo.core.hamburg_corridor_candidate import (
    HamburgCorridorCandidateError,
    build_hamburg_corridor_candidate_evidence,
)


def _node_manifest(value: str) -> tuple[str, Path]:
    node_id, separator, path = value.partition("=")
    if not separator or not node_id.strip() or not path.strip():
        raise argparse.ArgumentTypeError("--plainxml must use NODE=PATH")
    return node_id.strip(), Path(path)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(
        description=(
            "Build a hash-bound Hamburg corridor screening manifest from official "
            "LSA, MAP/OCIT, count, PlainXML, and HH-SIB axis evidence."
        )
    )
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--node", action="append", required=True, dest="node_ids")
    parser.add_argument("--lsa-identity-manifest", required=True, type=Path)
    parser.add_argument("--static-signal-manifest", required=True, type=Path)
    parser.add_argument("--count-manifest", required=True, type=Path)
    parser.add_argument("--plainxml", action="append", required=True, type=_node_manifest)
    parser.add_argument("--official-road-snapshot", type=Path)
    parser.add_argument("--axis-path-json", type=Path)
    parser.add_argument("--signal-fetch-manifest", type=Path)
    parser.add_argument("--map-lane-axis-stitch-plan", type=Path)
    parser.add_argument(
        "--required-signal-type",
        action="append",
        default=[],
        dest="required_signal_types",
        help="Allow-listed official LSA type; repeat for multiple accepted types.",
    )
    parser.add_argument("--output-file", required=True, type=Path)
    args = parser.parse_args()

    axis_paths: list[dict[str, object]] = []
    if args.axis_path_json is not None:
        try:
            payload = json.loads(args.axis_path_json.resolve(strict=True).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            parser.error(f"invalid --axis-path-json: {exc}")
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            parser.error("--axis-path-json must contain a JSON array of objects")
        axis_paths = payload

    try:
        report = build_hamburg_corridor_candidate_evidence(
            candidate_id=args.candidate_id,
            ordered_node_ids=args.node_ids,
            lsa_identity_manifest=args.lsa_identity_manifest,
            static_signal_manifest=args.static_signal_manifest,
            count_manifest=args.count_manifest,
            plainxml_manifests=dict(args.plainxml),
            official_road_snapshot=args.official_road_snapshot,
            axis_paths=axis_paths,
            signal_fetch_manifest=args.signal_fetch_manifest,
            map_lane_axis_stitch_plan=args.map_lane_axis_stitch_plan,
            required_signal_types=args.required_signal_types,
            output_file=args.output_file,
        )
    except (HamburgCorridorCandidateError, OSError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "automatic_promotion_gate": "blocked", "error": str(exc)}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "review_required" else 3


if __name__ == "__main__":
    raise SystemExit(main())
