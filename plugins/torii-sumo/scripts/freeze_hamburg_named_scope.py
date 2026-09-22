from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from torii_sumo.core.hamburg_named_scope import (
    HamburgNamedScopeError,
    freeze_hamburg_named_scope,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze the named Hamburg Am Sandtorkai 2349/2394/2403 scope contract."
    )
    parser.add_argument("--lsa-identity", required=True, type=Path)
    parser.add_argument("--corridor-scope", required=True, type=Path)
    parser.add_argument("--signal-discovery", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # Hamburg road names contain non-ASCII characters (for example, Großer).
    # Keep the CLI deterministic on Windows even when the console is still
    # using the legacy system code page.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        report = freeze_hamburg_named_scope(
            lsa_identity_file=args.lsa_identity,
            corridor_scope_file=args.corridor_scope,
            signal_asset_discovery_file=args.signal_discovery,
            output_file=args.output,
        )
    except (HamburgNamedScopeError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema": "torii.hamburg-named-corridor-scope-cli-error/v1",
                    "status": "blocked",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["decision"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
