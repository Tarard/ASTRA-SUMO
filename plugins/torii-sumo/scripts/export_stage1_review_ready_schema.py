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
    build_atomic_conflict_ledger_schema,
    build_controlled_pedestrian_binding_census_schema,
    build_effective_tls_program_inventory_schema,
    build_lossless_review_compression_schema,
    build_row1_experiment_report_schema,
    build_stage1_machine_review_ready_provenance_schema,
)


SCHEMA_DIR = REPOSITORY_ROOT / "schemas" / "research" / "corridor"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Torii Stage 1-M REVIEW_READY JSON Schemas."
    )
    parser.add_argument(
        "--program-inventory-output",
        type=Path,
        default=SCHEMA_DIR / "torii.corridor.effective-tls-program-inventory.v1.schema.json",
    )
    parser.add_argument(
        "--binding-census-output",
        type=Path,
        default=SCHEMA_DIR / "torii.corridor.controlled-pedestrian-binding-census.v1.schema.json",
    )
    parser.add_argument(
        "--atomic-conflict-ledger-output",
        type=Path,
        default=SCHEMA_DIR / "torii.corridor.atomic-conflict-ledger.v1.schema.json",
    )
    parser.add_argument(
        "--review-compression-output",
        type=Path,
        default=SCHEMA_DIR / "torii.corridor.lossless-review-compression.v1.schema.json",
    )
    parser.add_argument(
        "--row-experiment-output",
        type=Path,
        default=SCHEMA_DIR / "torii.corridor.row-1-experiment-report.v1.schema.json",
    )
    parser.add_argument(
        "--machine-review-ready-provenance-output",
        type=Path,
        default=SCHEMA_DIR / "torii.corridor.stage1m-machine-review-ready-provenance.v3.schema.json",
    )
    args = parser.parse_args()
    for path, schema in (
        (args.program_inventory_output, build_effective_tls_program_inventory_schema()),
        (args.binding_census_output, build_controlled_pedestrian_binding_census_schema()),
        (args.atomic_conflict_ledger_output, build_atomic_conflict_ledger_schema()),
        (args.review_compression_output, build_lossless_review_compression_schema()),
        (args.row_experiment_output, build_row1_experiment_report_schema()),
        (args.machine_review_ready_provenance_output, build_stage1_machine_review_ready_provenance_schema()),
    ):
        write_json_atomic(path, schema, sort_keys=True)


if __name__ == "__main__":
    main()
