from __future__ import annotations

import argparse
import json
from pathlib import Path

from torii_sumo.road_network.official_connection_plainxml import (
    OfficialConnectionPlainXmlError,
    materialize_hamburg_official_connection_plainxml,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compile hash-bound Hamburg official lane-transition evidence into "
            "a SUMO PlainXML connection-stage input."
        )
    )
    parser.add_argument("--lane-transition-graph", required=True, type=Path)
    parser.add_argument("--edges", required=True, type=Path)
    parser.add_argument("--plainxml-manifest", type=Path)
    parser.add_argument("--expected-transition-graph-sha256")
    parser.add_argument("--expected-edges-sha256")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--prefix", default="official_map_connections")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = materialize_hamburg_official_connection_plainxml(
            lane_transition_graph_file=args.lane_transition_graph,
            edges_file=args.edges,
            plainxml_manifest_file=args.plainxml_manifest,
            expected_transition_graph_sha256=args.expected_transition_graph_sha256,
            expected_edges_sha256=args.expected_edges_sha256,
            output_dir=args.output_dir,
            prefix=args.prefix,
        )
    except (OfficialConnectionPlainXmlError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema": "torii.hamburg-official-connection-plainxml-cli-error/v1",
                    "status": "blocked",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "human_action_required": False,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
