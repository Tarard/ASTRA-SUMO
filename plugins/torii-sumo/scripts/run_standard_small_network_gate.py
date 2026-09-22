from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_SRC = REPOSITORY_ROOT / "plugins" / "torii-sumo" / "src"
if str(PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SRC))

from torii_sumo.core.sumo_commands import discover_binaries  # noqa: E402
from torii_sumo.corridor.standard_small_network_gate import (  # noqa: E402
    run_standard_small_network_gate,
)


BENCHMARK_DIR = REPOSITORY_ROOT / "benchmarks" / "corridor_human_modeling_v1"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the narrow vehicle-only small-network gate, including the "
            "official SUMO regression, positive strict NEMA reference, and "
            "official NEMA fail-closed applicability probe."
        )
    )
    parser.add_argument(
        "--output-dir",
        default=str(REPOSITORY_ROOT / "outputs" / "standard-small-network-gate"),
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    binaries = discover_binaries()
    missing = [name for name in ("netconvert", "sumo") if not binaries.get(name)]
    if missing:
        print(json.dumps({"status": "blocked", "missing": missing}, indent=2))
        return 2
    report = run_standard_small_network_gate(
        official_spec_file=(BENCHMARK_DIR / "official_sumo_scenarios.v1.json"),
        parent_benchmark_file=BENCHMARK_DIR / "benchmark.v1.json",
        toolchain_lock_file=BENCHMARK_DIR / "toolchain.lock.json",
        official_source_root=BENCHMARK_DIR / "official_sumo_v1_27_1",
        output_dir=Path(args.output_dir),
        netconvert_binary=str(binaries["netconvert"]),
        sumo_binary=str(binaries["sumo"]),
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "automatic_promotion_gate": report["automatic_promotion_gate"],
                "gates": report["gates"],
                "next_stage": report["modal_expansion_decision"]["next_stage"],
                "report_file": report["report_file"],
                "manifest_file": report["manifest_file"],
            },
            indent=2,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
