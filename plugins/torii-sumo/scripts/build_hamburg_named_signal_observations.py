from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

from torii_sumo.core.digital_twin import parse_iso_datetime
from torii_sumo.core.hamburg_named_signal_observations import (
    HamburgSignalObservationError,
    materialize_hamburg_named_signal_observations,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch and audit official Hamburg primary signal history for a bound corridor."
    )
    parser.add_argument("--binding-manifest", required=True, type=Path)
    parser.add_argument("--begin-utc", required=True, help="ISO-8601 UTC timestamp, for example 2026-07-18T14:30:00Z")
    parser.add_argument("--end-utc", required=True, help="ISO-8601 UTC timestamp")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--api-base-url", default="https://tld.iot.hamburg.de/v1.0/")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--preceding-lookback-hours", type=float, default=168.0)
    parser.add_argument("--chunk-minutes", type=float, default=10.0)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument(
        "--retry-incomplete-cache",
        action="store_true",
        help="retry cached partial/error streams or primary streams without a t=0 state",
    )
    parser.add_argument(
        "--allow-signal-group-projection",
        action="store_true",
        help="derive a silent connection from a complete sibling with the same official node and signalGroupID",
    )
    args = parser.parse_args()
    try:
        report = materialize_hamburg_named_signal_observations(
            binding_manifest=args.binding_manifest,
            output_dir=args.output_dir,
            begin_utc=parse_iso_datetime(args.begin_utc),
            end_utc=parse_iso_datetime(args.end_utc),
            api_base_url=args.api_base_url,
            cache_dir=args.cache_dir,
            preceding_lookback=timedelta(hours=args.preceding_lookback_hours),
            chunk_duration=timedelta(minutes=args.chunk_minutes),
            max_retries=args.max_retries,
            max_workers=args.max_workers,
            timeout_seconds=args.timeout_seconds,
            retry_incomplete_cache=args.retry_incomplete_cache,
            allow_signal_group_projection=args.allow_signal_group_projection,
        )
    except (OSError, ValueError, HamburgSignalObservationError) as exc:
        print(json.dumps({"status": "error", "execution_gate": "blocked", "automatic_promotion_gate": "blocked", "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["execution_gate"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
