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
    build_held_out_corpus_schema,
    build_held_out_corpus_machine_report_schema,
    build_held_out_corpus_machine_manifest_schema,
    build_held_out_corpus_snapshot_report_schema,
    build_held_out_machine_run_identity_schema,
    build_held_out_review_contract_bundle_schema,
    build_held_out_review_policy_schema,
    build_held_out_review_report_schema,
    build_net_replay_report_schema,
)


SCHEMA_DIR = REPOSITORY_ROOT / "schemas" / "research" / "corridor"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Torii Stage-1 held-out blind-review JSON Schemas."
    )
    parser.add_argument(
        "--policy-output",
        type=Path,
        default=SCHEMA_DIR / "torii.corridor.held-out-review-policy.v1.schema.json",
    )
    parser.add_argument(
        "--corpus-machine-report-output",
        type=Path,
        default=(
            SCHEMA_DIR / "torii.corridor.held-out-corpus-machine-report.v2.schema.json"
        ),
    )
    parser.add_argument(
        "--run-identity-output",
        type=Path,
        default=(
            SCHEMA_DIR
            / "torii.corridor.held-out-machine-run-identity.v1.schema.json"
        ),
    )
    parser.add_argument(
        "--corpus-machine-manifest-output",
        type=Path,
        default=(
            SCHEMA_DIR
            / "torii.corridor.held-out-corpus-machine-manifest.v2.schema.json"
        ),
    )
    parser.add_argument(
        "--bundle-output",
        type=Path,
        default=(
            SCHEMA_DIR
            / "torii.corridor.held-out-review-contract-bundle.v1.schema.json"
        ),
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        default=SCHEMA_DIR / "torii.corridor.held-out-review-report.v1.schema.json",
    )
    parser.add_argument(
        "--corpus-output",
        type=Path,
        default=SCHEMA_DIR / "torii.corridor.held-out-corpus.v1.schema.json",
    )
    parser.add_argument(
        "--corpus-report-output",
        type=Path,
        default=(
            SCHEMA_DIR
            / "torii.corridor.held-out-corpus-snapshot-report.v1.schema.json"
        ),
    )
    parser.add_argument(
        "--net-replay-report-output",
        type=Path,
        default=SCHEMA_DIR / "torii.corridor.net-replay-report.v1.schema.json",
    )
    args = parser.parse_args()
    for path, schema in (
        (args.policy_output, build_held_out_review_policy_schema()),
        (args.bundle_output, build_held_out_review_contract_bundle_schema()),
        (args.report_output, build_held_out_review_report_schema()),
        (args.corpus_output, build_held_out_corpus_schema()),
        (
            args.corpus_report_output,
            build_held_out_corpus_snapshot_report_schema(),
        ),
        (
            args.corpus_machine_report_output,
            build_held_out_corpus_machine_report_schema(),
        ),
        (
            args.run_identity_output,
            build_held_out_machine_run_identity_schema(),
        ),
        (
            args.corpus_machine_manifest_output,
            build_held_out_corpus_machine_manifest_schema(),
        ),
        (args.net_replay_report_output, build_net_replay_report_schema()),
    ):
        write_json_atomic(path, schema, sort_keys=True)


if __name__ == "__main__":
    main()
