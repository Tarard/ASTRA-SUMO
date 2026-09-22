from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from torii_sumo.corridor.enums import GateStatus
from torii_sumo.corridor.plainxml_normalization import normalize_osm_plainxml_bundle


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize a locked OSM-derived SUMO PlainXML bundle without mutating its source."
    )
    parser.add_argument("source_prefix", type=Path)
    parser.add_argument("output_prefix", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()

    report = normalize_osm_plainxml_bundle(
        args.source_prefix,
        args.output_prefix,
        args.report,
    )
    print(
        json.dumps(
            {
                "status": report.status.value,
                "blockers": list(report.blockers),
                "source_mutated": report.source_mutated,
                "output_bundle_sha256": report.output_bundle_sha256,
                "report": str(args.report.resolve()),
            },
            indent=2,
            sort_keys=True,
        )
    )
    if report.status is not GateStatus.PASS:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
