from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from torii_sumo.corridor.held_out_review_v2_r2 import (  # noqa: E402
    freeze_review_trial_v2_r2,
)


BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze the executable Stage 1-M v2-R2 trial identity and one-time "
            "blinding-seed commitment before review sampling."
        )
    )
    parser.add_argument(
        "--base-review-parent",
        type=Path,
        default=BENCHMARK_DIR / "held_out_review_parent.v2.json",
    )
    parser.add_argument(
        "--base-review-policy",
        type=Path,
        default=BENCHMARK_DIR / "held_out_review_preregistration.v2.json",
    )
    parser.add_argument(
        "--parent-sampling-policy",
        type=Path,
        default=BENCHMARK_DIR / "review_witness_sampling_policy.v2.json",
    )
    parser.add_argument(
        "--effective-corpus",
        type=Path,
        default=BENCHMARK_DIR / "held_out_effective_corpus.v2.json",
    )
    parser.add_argument(
        "--replacement-attempt-ledger",
        type=Path,
        default=BENCHMARK_DIR / "held_out_replacement_attempt_ledger.v2.json",
    )
    parser.add_argument(
        "--source-snapshot-protocol",
        type=Path,
        default=BENCHMARK_DIR / "held_out_source_snapshot_protocol.v2.json",
    )
    parser.add_argument("--snapshot-report", type=Path, required=True)
    parser.add_argument("--snapshot-manifest", type=Path, required=True)
    parser.add_argument("--machine-run-identity", type=Path, required=True)
    parser.add_argument("--machine-report", type=Path, required=True)
    parser.add_argument("--machine-manifest", type=Path, required=True)
    parser.add_argument(
        "--study-sampling-policy-output",
        type=Path,
        default=BENCHMARK_DIR / "review_study_sampling_policy.v2-r2.json",
    )
    parser.add_argument(
        "--execution-parent-output",
        type=Path,
        default=BENCHMARK_DIR / "held_out_review_execution_parent.v2-r2.json",
    )
    parser.add_argument(
        "--trial-instance-output",
        type=Path,
        default=BENCHMARK_DIR / "held_out_review_trial_instance.v2-r2.json",
    )
    parser.add_argument("--restricted-seed-output", type=Path, required=True)
    args = parser.parse_args()
    sampling, parent, trial = freeze_review_trial_v2_r2(
        base_review_parent_file=args.base_review_parent,
        base_review_policy_file=args.base_review_policy,
        parent_sampling_policy_file=args.parent_sampling_policy,
        effective_corpus_file=args.effective_corpus,
        replacement_attempt_ledger_file=args.replacement_attempt_ledger,
        source_snapshot_protocol_file=args.source_snapshot_protocol,
        snapshot_report_file=args.snapshot_report,
        snapshot_manifest_file=args.snapshot_manifest,
        machine_run_identity_file=args.machine_run_identity,
        machine_report_file=args.machine_report,
        machine_manifest_file=args.machine_manifest,
        study_sampling_policy_output=args.study_sampling_policy_output,
        execution_parent_output=args.execution_parent_output,
        trial_instance_output=args.trial_instance_output,
        restricted_seed_output=args.restricted_seed_output,
    )
    print(
        json.dumps(
            {
                "trial_id": trial.trial_id,
                "execution_parent_id": parent.parent_id,
                "study_sampling_policy_id": sampling.policy_id,
                "blinding_seed_sha256": trial.blinding_seed_sha256,
                "sampling_executed": False,
                "automatic_promotion_gate": trial.automatic_promotion_gate.value,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
