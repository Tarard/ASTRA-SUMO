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

from torii_sumo.corridor.held_out_review_v2_r2_package import (  # noqa: E402
    build_held_out_review_package_v2_r2,
)


BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Execute the precommitted Torii Stage 1-M v2-R2 blinded review sample."
    )
    parser.add_argument(
        "--effective-corpus",
        type=Path,
        default=BENCHMARK_DIR / "held_out_effective_corpus.v2.json",
    )
    parser.add_argument(
        "--trial-instance",
        type=Path,
        default=BENCHMARK_DIR / "held_out_review_trial_instance.v2-r2.json",
    )
    parser.add_argument(
        "--execution-parent",
        type=Path,
        default=BENCHMARK_DIR / "held_out_review_execution_parent.v2-r2.json",
    )
    parser.add_argument(
        "--study-sampling-policy",
        type=Path,
        default=BENCHMARK_DIR / "review_study_sampling_policy.v2-r2.json",
    )
    parser.add_argument("--restricted-seed", type=Path, required=True)
    parser.add_argument("--machine-root", type=Path, required=True)
    parser.add_argument("--base-review-package", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--created-at",
        required=True,
        help="Frozen timezone-aware ISO-8601 package timestamp.",
    )
    args = parser.parse_args()
    created_at = datetime.fromisoformat(args.created_at.replace("Z", "+00:00"))
    base_review_package = args.base_review_package or args.machine_root / "review-package"
    dataset, ledger, manifest = build_held_out_review_package_v2_r2(
        effective_corpus_file=args.effective_corpus,
        trial_instance_file=args.trial_instance,
        execution_parent_file=args.execution_parent,
        study_sampling_policy_file=args.study_sampling_policy,
        restricted_seed_file=args.restricted_seed,
        machine_root=args.machine_root,
        base_review_package_dir=base_review_package,
        output_dir=args.output,
        repository_root=args.repository_root,
        created_at=created_at,
    )
    print(f"trial_id={dataset.trial_id}")
    print(f"corridor_packages={len(dataset.cases)}")
    print(f"review_units={sum(len(case.units) for case in dataset.cases)}")
    print(f"atomic_witness_population={ledger.atomic_witness_population_count}")
    print(f"selected_conflict_sites={ledger.selected_conflict_site_count}")
    print(f"selected_negative_pairs={ledger.selected_negative_pair_count}")
    print(f"controlled_binding_hard_census={ledger.controlled_binding_hard_count}")
    print(f"pedestrian_coverage_gap_census={ledger.pedestrian_coverage_gap_count}")
    print(f"automatic_promotion_gate={manifest.automatic_promotion_gate.value}")


if __name__ == "__main__":
    main()
