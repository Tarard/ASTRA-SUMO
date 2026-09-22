from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from torii_sumo.corridor.stage1_review_ready import (  # noqa: E402
    freeze_stage1_machine_review_ready_provenance,
)


BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freeze Torii Stage 1-M Machine REVIEW_READY provenance v3."
    )
    parser.add_argument(
        "--snapshot-root",
        type=Path,
        default=REPOSITORY_ROOT / "outputs/stage1m-v2-effective-snapshots",
    )
    parser.add_argument(
        "--machine-root",
        type=Path,
        default=REPOSITORY_ROOT / "outputs/stage1m-v2-machine-final",
    )
    parser.add_argument(
        "--review-package-root",
        type=Path,
        default=REPOSITORY_ROOT / "outputs/stage1m-v2-review-package-final",
    )
    parser.add_argument(
        "--review-package-repeat-root",
        type=Path,
        default=REPOSITORY_ROOT / "outputs/stage1m-v2-review-package-final-rerun",
    )
    parser.add_argument(
        "--row-report",
        type=Path,
        default=REPOSITORY_ROOT / "outputs/row1-dev-experiment-v5/row-1-experiment-report.json",
    )
    parser.add_argument(
        "--row-repeat-report",
        type=Path,
        default=REPOSITORY_ROOT / "outputs/row1-dev-experiment-v6/row-1-experiment-report.json",
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
        "--execution-parent",
        type=Path,
        default=BENCHMARK_DIR / "held_out_review_execution_parent.v2-r2.json",
    )
    parser.add_argument(
        "--trial-instance",
        type=Path,
        default=BENCHMARK_DIR / "held_out_review_trial_instance.v2-r2.json",
    )
    parser.add_argument(
        "--study-sampling-policy",
        type=Path,
        default=BENCHMARK_DIR / "review_study_sampling_policy.v2-r2.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            BENCHMARK_DIR
            / "evidence/stage1m_machine_review_ready_provenance_20260714.v3.json"
        ),
    )
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--recorded-at",
        required=True,
        help="Frozen timezone-aware ISO-8601 provenance timestamp.",
    )
    args = parser.parse_args()
    recorded_at = datetime.fromisoformat(args.recorded_at.replace("Z", "+00:00"))
    provenance = freeze_stage1_machine_review_ready_provenance(
        repository_root=args.repository_root,
        snapshot_root=args.snapshot_root,
        machine_root=args.machine_root,
        review_package_root=args.review_package_root,
        review_package_repeat_root=args.review_package_repeat_root,
        row_report_file=args.row_report,
        row_repeat_report_file=args.row_repeat_report,
        effective_corpus_file=args.effective_corpus,
        replacement_attempt_ledger_file=args.replacement_attempt_ledger,
        execution_parent_file=args.execution_parent,
        trial_instance_file=args.trial_instance,
        study_sampling_policy_file=args.study_sampling_policy,
        output_file=args.output,
        recorded_at=recorded_at,
    )
    print(f"provenance_id={provenance.provenance_id}")
    print(f"status={provenance.status}")
    print(f"effective_pcb_count={provenance.pcb.effective_unresolved_binding_count}")
    print(f"effective_atomic_witness_count={provenance.rwc.effective_atomic_witness_count}")
    print(f"review_unit_count={provenance.review_package.review_unit_count}")
    print(f"stage_1h_human_validation_gate={provenance.stage_1h_human_validation_gate.value}")
    print(f"automatic_promotion_gate={provenance.automatic_promotion_gate.value}")


if __name__ == "__main__":
    main()

