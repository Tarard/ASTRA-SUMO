from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from torii_sumo.core.hamburg_official_intersection_plainxml import (
    HamburgOfficialIntersectionPlainXmlError,
    materialize_hamburg_official_intersection_plainxml,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build one OSM-free SUMO intersection candidate from Hamburg's "
            "official MAP XML, MAP KML, and OCIT-C files after a hash-bound "
            "classification proves a single physical owner."
        )
    )
    parser.add_argument("--map-xml", required=True, type=Path)
    parser.add_argument("--map-kml", required=True, type=Path)
    parser.add_argument("--ocit-c", required=True, type=Path)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--classification-file", required=True, type=Path)
    parser.add_argument("--accepted-classification-id", required=True)
    parser.add_argument("--expected-classification-sha256", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--prefix")
    parser.add_argument("--expected-map-xml-sha256")
    parser.add_argument("--expected-map-kml-sha256")
    parser.add_argument("--expected-ocit-c-sha256")
    parser.add_argument("--netconvert-binary", default="netconvert")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument(
        "--no-compile",
        action="store_true",
        help="Write PlainXML only; do not invoke netconvert.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    expected_sha256 = {
        key: value
        for key, value in {
            "map_xml": args.expected_map_xml_sha256,
            "map_kml": args.expected_map_kml_sha256,
            "ocit_c": args.expected_ocit_c_sha256,
        }.items()
        if value is not None
    }
    try:
        report = materialize_hamburg_official_intersection_plainxml(
            map_xml_file=args.map_xml,
            map_kml_file=args.map_kml,
            ocit_c_file=args.ocit_c,
            output_dir=args.output_dir,
            classification_file=args.classification_file,
            accepted_classification_id=args.accepted_classification_id,
            expected_classification_sha256=args.expected_classification_sha256,
            expected_node_id=args.node_id,
            expected_sha256=expected_sha256 or None,
            prefix=args.prefix,
            compile_net=not args.no_compile,
            netconvert_binary=args.netconvert_binary,
            timeout_seconds=args.timeout_seconds,
        )
    except (HamburgOfficialIntersectionPlainXmlError, OSError, ValueError) as exc:
        error = {
            "schema": "torii.hamburg-official-intersection-cli-error/v1",
            "status": "blocked",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "human_action_required": False,
        }
        print(json.dumps(error, indent=2, sort_keys=True), file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
