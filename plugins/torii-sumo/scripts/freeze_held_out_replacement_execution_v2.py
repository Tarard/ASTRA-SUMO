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

from torii_sumo.corridor.held_out_review_v2 import (  # noqa: E402
    freeze_replacement_execution_v2,
)


BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze Stage 1-M replacement attempts and the effective 30-case corpus "
            "without consulting machine or human labels."
        )
    )
    parser.add_argument(
        "--base-corpus",
        type=Path,
        default=BENCHMARK_DIR / "held_out_corpus.v1.json",
    )
    parser.add_argument(
        "--reserve-corpus",
        type=Path,
        default=BENCHMARK_DIR / "held_out_reserve_corpus.v2.json",
    )
    parser.add_argument(
        "--replacement-plan",
        type=Path,
        default=BENCHMARK_DIR / "held_out_replacement_plan.v2.json",
    )
    parser.add_argument(
        "--source-snapshot-protocol",
        type=Path,
        default=BENCHMARK_DIR / "held_out_source_snapshot_protocol.v2.json",
    )
    parser.add_argument("--reserve-snapshot-catalog", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument(
        "--rank-evidence",
        action="append",
        required=True,
        help="Rank and evidence directory as RANK=PATH; repeat for each attempted rank.",
    )
    parser.add_argument(
        "--superseded-machine-report",
        action="append",
        type=Path,
        default=[],
    )
    parser.add_argument(
        "--effective-corpus-output",
        type=Path,
        default=BENCHMARK_DIR / "held_out_effective_corpus.v2.json",
    )
    parser.add_argument(
        "--attempt-ledger-output",
        type=Path,
        default=BENCHMARK_DIR / "held_out_replacement_attempt_ledger.v2.json",
    )
    args = parser.parse_args()
    rank_dirs = _parse_rank_evidence(args.rank_evidence)
    corpus, ledger = freeze_replacement_execution_v2(
        base_corpus_file=args.base_corpus,
        reserve_corpus_file=args.reserve_corpus,
        replacement_plan_file=args.replacement_plan,
        source_snapshot_protocol_file=args.source_snapshot_protocol,
        reserve_snapshot_catalog_file=args.reserve_snapshot_catalog,
        machine_evidence_dirs_by_rank=rank_dirs,
        evidence_root=args.evidence_root,
        effective_corpus_output=args.effective_corpus_output,
        attempt_ledger_output=args.attempt_ledger_output,
        superseded_machine_report_files=tuple(args.superseded_machine_report),
    )
    print(
        json.dumps(
            {
                "effective_corpus_id": corpus.corpus_id,
                "effective_case_count": len(corpus.corridors),
                "ledger_id": ledger.ledger_id,
                "selected_replacements": {
                    slot.invalid_corridor_key: slot.selected_corridor_key for slot in ledger.slots
                },
                "automatic_promotion_gate": ledger.automatic_promotion_gate.value,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _parse_rank_evidence(values: list[str]) -> dict[int, Path]:
    parsed: dict[int, Path] = {}
    for value in values:
        raw_rank, separator, raw_path = value.partition("=")
        if not separator or not raw_rank.isdigit() or not raw_path.strip():
            raise ValueError(f"Invalid --rank-evidence value: {value!r}")
        rank = int(raw_rank)
        if rank < 1 or rank in parsed:
            raise ValueError(f"Duplicate or invalid replacement rank: {rank}")
        parsed[rank] = Path(raw_path)
    return parsed


if __name__ == "__main__":
    main()
