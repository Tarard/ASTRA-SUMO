from __future__ import annotations

import argparse
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.corridor.schema import (
    build_official_sumo_benchmark_report_schema,
    build_official_sumo_benchmark_schema,
)


SCHEMA_DIR = REPOSITORY_ROOT / "schemas" / "research" / "corridor"
DEFAULT_SPEC_OUTPUT = SCHEMA_DIR / "torii.corridor.official-sumo-benchmark.v1.schema.json"
DEFAULT_REPORT_OUTPUT = SCHEMA_DIR / "torii.corridor.official-sumo-benchmark-report.v1.schema.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the ASTRA Stage-1 official SUMO benchmark schemas."
    )
    parser.add_argument("--spec-output", type=Path, default=DEFAULT_SPEC_OUTPUT)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT_OUTPUT)
    args = parser.parse_args()
    write_json_atomic(args.spec_output, build_official_sumo_benchmark_schema(), sort_keys=True)
    write_json_atomic(args.report_output, build_official_sumo_benchmark_report_schema(), sort_keys=True)


if __name__ == "__main__":
    main()
