from __future__ import annotations

import argparse
import json
from pathlib import Path

from torii_sumo.core.hamburg_named_signal_observations import (
    HamburgSignalObservationError,
    census_hamburg_named_signal_stream_coverage,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Census the bounded first/last official Hamburg primary-signal records "
            "for a named binding manifest."
        )
    )
    parser.add_argument("--binding-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--lsa-identity-manifest",
        type=Path,
        help="optional frozen official Hamburg LSA identity evidence for missing required nodes",
    )
    parser.add_argument(
        "--api-base-url",
        default="https://tld.iot.hamburg.de/v1.0/",
    )
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    args = parser.parse_args()
    try:
        report = census_hamburg_named_signal_stream_coverage(
            binding_manifest=args.binding_manifest,
            output_dir=args.output_dir,
            lsa_identity_manifest=args.lsa_identity_manifest,
            api_base_url=args.api_base_url,
            max_retries=args.max_retries,
            max_workers=args.max_workers,
            timeout_seconds=args.timeout_seconds,
        )
    except (OSError, ValueError, HamburgSignalObservationError) as exc:
        print(json.dumps({"status": "error", "automatic_promotion_gate": "blocked", "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["execution_gate"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
