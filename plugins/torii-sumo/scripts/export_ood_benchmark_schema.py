from __future__ import annotations

import argparse
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.corridor.schema import (
    build_certification_applicability_report_schema,
    build_certification_envelope_schema,
    build_ood_benchmark_report_schema,
    build_ood_benchmark_schema,
)


SCHEMA_DIR = REPOSITORY_ROOT / "schemas" / "research" / "corridor"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Torii Stage-1 certification-domain and OOD schemas."
    )
    parser.add_argument(
        "--envelope-output",
        type=Path,
        default=SCHEMA_DIR / "torii.corridor.certification-envelope.v1.schema.json",
    )
    parser.add_argument(
        "--applicability-output",
        type=Path,
        default=SCHEMA_DIR / "torii.corridor.certification-applicability-report.v1.schema.json",
    )
    parser.add_argument(
        "--spec-output",
        type=Path,
        default=SCHEMA_DIR / "torii.corridor.ood-benchmark.v1.schema.json",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        default=SCHEMA_DIR / "torii.corridor.ood-benchmark-report.v1.schema.json",
    )
    args = parser.parse_args()
    for path, schema in (
        (args.envelope_output, build_certification_envelope_schema()),
        (args.applicability_output, build_certification_applicability_report_schema()),
        (args.spec_output, build_ood_benchmark_schema()),
        (args.report_output, build_ood_benchmark_report_schema()),
    ):
        write_json_atomic(path, schema, sort_keys=True)


if __name__ == "__main__":
    main()
