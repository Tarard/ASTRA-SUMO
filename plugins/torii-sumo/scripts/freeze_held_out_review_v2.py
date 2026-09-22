from __future__ import annotations

import argparse
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from torii_sumo.core.artifact_io import write_json_atomic  # noqa: E402
from torii_sumo.corridor.held_out_review_v2 import (  # noqa: E402
    build_deterministic_replacement_plan_v2,
)
from torii_sumo.corridor.held_out_review_v2_preregistration import (  # noqa: E402
    build_held_out_replacement_policy_v2,
    build_held_out_reserve_corpus_v2,
    build_held_out_review_parent_v2,
    build_held_out_review_policy_v2,
    build_held_out_source_snapshot_protocol_v2,
    build_review_witness_sampling_policy_v2,
)


BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"
SCHEMA_DIR = REPOSITORY_ROOT / "schemas" / "research" / "corridor"


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze Torii Stage 1-M held-out review v2 contracts.")
    parser.add_argument(
        "--base-benchmark",
        type=Path,
        default=BENCHMARK_DIR / "benchmark.v1.json",
    )
    parser.add_argument(
        "--held-out-corpus",
        type=Path,
        default=BENCHMARK_DIR / "held_out_corpus.v1.json",
    )
    parser.add_argument(
        "--compression-schema",
        type=Path,
        default=(SCHEMA_DIR / "torii.corridor.lossless-review-compression.v1.schema.json"),
    )
    parser.add_argument(
        "--reserve-output",
        type=Path,
        default=BENCHMARK_DIR / "held_out_reserve_corpus.v2.json",
    )
    parser.add_argument(
        "--replacement-policy-output",
        type=Path,
        default=BENCHMARK_DIR / "held_out_replacement_policy.v2.json",
    )
    parser.add_argument(
        "--sampling-policy-output",
        type=Path,
        default=BENCHMARK_DIR / "review_witness_sampling_policy.v2.json",
    )
    parser.add_argument(
        "--parent-output",
        type=Path,
        default=BENCHMARK_DIR / "held_out_review_parent.v2.json",
    )
    parser.add_argument(
        "--review-policy-output",
        type=Path,
        default=BENCHMARK_DIR / "held_out_review_preregistration.v2.json",
    )
    parser.add_argument(
        "--replacement-plan-output",
        type=Path,
        default=BENCHMARK_DIR / "held_out_replacement_plan.v2.json",
    )
    parser.add_argument(
        "--source-snapshot-protocol-output",
        type=Path,
        default=BENCHMARK_DIR / "held_out_source_snapshot_protocol.v2.json",
    )
    args = parser.parse_args()

    reserve = build_held_out_reserve_corpus_v2(parent_corpus_file=args.held_out_corpus)
    _write(args.reserve_output, reserve)
    replacement = build_held_out_replacement_policy_v2(
        parent_corpus_file=args.held_out_corpus,
        reserve_corpus_file=args.reserve_output,
    )
    _write(args.replacement_policy_output, replacement)
    sampling = build_review_witness_sampling_policy_v2()
    _write(args.sampling_policy_output, sampling)
    parent = build_held_out_review_parent_v2(
        base_benchmark_file=args.base_benchmark,
        held_out_corpus_file=args.held_out_corpus,
        reserve_corpus_file=args.reserve_output,
        replacement_policy_file=args.replacement_policy_output,
        sampling_policy_file=args.sampling_policy_output,
        lossless_compression_schema_file=args.compression_schema,
    )
    _write(args.parent_output, parent)
    policy = build_held_out_review_policy_v2(
        parent_review_benchmark_file=args.parent_output,
        reserve_corpus_file=args.reserve_output,
        replacement_policy_file=args.replacement_policy_output,
        sampling_policy_file=args.sampling_policy_output,
    )
    _write(args.review_policy_output, policy)
    plan = build_deterministic_replacement_plan_v2(
        reserve_corpus_file=args.reserve_output,
        replacement_policy_file=args.replacement_policy_output,
    )
    _write(args.replacement_plan_output, plan)
    protocol = build_held_out_source_snapshot_protocol_v2()
    _write(args.source_snapshot_protocol_output, protocol)


def _write(path: Path, model: object) -> None:
    write_json_atomic(
        path,
        model.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )


if __name__ == "__main__":
    main()
