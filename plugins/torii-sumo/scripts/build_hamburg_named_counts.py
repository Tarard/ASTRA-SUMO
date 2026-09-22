from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from torii_sumo.core.hamburg_named_count_scope import (
    HamburgNamedCountScopeError,
    load_lsa_node_references,
    materialize_hamburg_named_count_scope,
)
from torii_sumo.core.hamburg_official import SensorThingsClient


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a declared Hamburg official detector scope, select a complete "
            "Saturday window with warm-up, and write SUMO-ready 15-minute counts."
        )
    )
    parser.add_argument("--lsa-identity-file", required=True, type=Path)
    parser.add_argument("--count-node-id", action="append", required=True)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--saturday-date", type=date.fromisoformat)
    parser.add_argument("--max-saturdays-to-try", type=int, default=8)
    parser.add_argument("--warmup-seconds", type=int, default=1800)
    parser.add_argument("--formal-duration-seconds", type=int, default=7200)
    parser.add_argument("--source-bin-seconds", type=int, default=300)
    parser.add_argument("--output-bin-seconds", type=int, default=900)
    parser.add_argument("--max-distance-m", type=float, default=250.0)
    parser.add_argument("--api-base", default="https://iot.hamburg.de/v1.1/")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    args = parser.parse_args()

    try:
        signal_nodes = load_lsa_node_references(
            args.lsa_identity_file,
            expected_node_ids=args.count_node_id,
        )
        report = materialize_hamburg_named_count_scope(
            output_dir=args.output_dir,
            client=SensorThingsClient(args.api_base, timeout_seconds=args.timeout_seconds),
            signal_nodes=signal_nodes,
            requested_count_node_ids=args.count_node_id,
            scope_id=args.scope_id,
            saturday_date=args.saturday_date,
            max_saturdays_to_try=args.max_saturdays_to_try,
            warmup_seconds=args.warmup_seconds,
            formal_duration_seconds=args.formal_duration_seconds,
            source_bin_seconds=args.source_bin_seconds,
            output_bin_seconds=args.output_bin_seconds,
            max_distance_m=args.max_distance_m,
        )
    except (OSError, ValueError, HamburgNamedCountScopeError) as exc:
        print(json.dumps({"status": "error", "automatic_promotion_gate": "blocked", "error": str(exc)}))
        return 2

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    if report.get("status") == "pass" and report.get("automatic_promotion_gate") == "pass":
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
