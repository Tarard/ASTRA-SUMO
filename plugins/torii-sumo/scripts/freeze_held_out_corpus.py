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
from torii_sumo.corridor.held_out_corpus_preregistration import (
    build_preregistered_held_out_corpus,
)


BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freeze the preregistered 30-case real OSM held-out corpus."
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=BENCHMARK_DIR / "held_out_review_preregistration.v1.json",
    )
    parser.add_argument(
        "--parent-benchmark",
        type=Path,
        default=BENCHMARK_DIR / "benchmark.v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BENCHMARK_DIR / "held_out_corpus.v1.json",
    )
    args = parser.parse_args()
    corpus = build_preregistered_held_out_corpus(
        held_out_review_policy_file=args.policy,
        parent_benchmark_file=args.parent_benchmark,
    )
    write_json_atomic(
        args.output,
        corpus.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )


if __name__ == "__main__":
    main()
