from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from torii_sumo.core.digital_twin import parse_iso_datetime
from torii_sumo.core.hamburg_named_signal_observations import (
    HamburgSignalObservationError,
    screen_hamburg_named_signal_windows,
)


def _candidate_window(value: str) -> tuple[str, datetime, datetime]:
    label, separator, bounds = value.partition("=")
    if not separator or not label.strip():
        raise argparse.ArgumentTypeError("candidate window must use LABEL=BEGIN_UTC,END_UTC")
    begin_text, separator, end_text = bounds.partition(",")
    if not separator or not begin_text.strip() or not end_text.strip():
        raise argparse.ArgumentTypeError("candidate window must use LABEL=BEGIN_UTC,END_UTC")
    try:
        begin = parse_iso_datetime(begin_text)
        end = parse_iso_datetime(end_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return label.strip(), begin, end


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Screen candidate Hamburg Saturday signal windows before fetching full official history. "
            "A positive result is a preflight hint, not historical evidence."
        )
    )
    parser.add_argument("--binding-manifest", required=True, type=Path)
    parser.add_argument(
        "--candidate-window",
        action="append",
        required=True,
        type=_candidate_window,
        metavar="LABEL=BEGIN_UTC,END_UTC",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--api-base-url", default="https://tld.iot.hamburg.de/v1.0/")
    parser.add_argument("--max-retries", type=int, default=0)
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    args = parser.parse_args()
    windows = {label: (begin, end) for label, begin, end in args.candidate_window}
    try:
        report = screen_hamburg_named_signal_windows(
            binding_manifest=args.binding_manifest,
            output_dir=args.output_dir,
            candidate_windows=windows,
            api_base_url=args.api_base_url,
            max_retries=args.max_retries,
            max_workers=args.max_workers,
            timeout_seconds=args.timeout_seconds,
        )
    except (OSError, ValueError, HamburgSignalObservationError) as exc:
        print(json.dumps({"status": "error", "execution_gate": "blocked", "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["execution_gate"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
