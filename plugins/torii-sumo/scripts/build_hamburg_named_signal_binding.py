from __future__ import annotations

import argparse
import json
from pathlib import Path

from torii_sumo.core.hamburg_named_signal_binding import (
    HamburgSignalBindingError,
    materialize_hamburg_named_signal_binding,
)


def _manifest(value: str) -> tuple[str, Path]:
    node_id, separator, path = value.partition("=")
    if not separator or not node_id.strip() or not path.strip():
        raise argparse.ArgumentTypeError("expected NODE_ID=MANIFEST_PATH")
    return node_id.strip(), Path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bind official Hamburg TLD signal streams to MAP/SUMO movements.")
    parser.add_argument("--net-file", required=True, type=Path)
    parser.add_argument("--intersection-manifest", required=True, action="append", type=_manifest)
    parser.add_argument("--signal-stream-file", required=True, action="append", type=Path)
    parser.add_argument("--compound-tls-manifest", type=Path)
    parser.add_argument("--required-node-id", action="append", default=["2349", "2394", "2403"])
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = materialize_hamburg_named_signal_binding(
            net_file=args.net_file,
            intersection_manifests=dict(args.intersection_manifest),
            signal_stream_files=args.signal_stream_file,
            output_dir=args.output_dir,
            required_node_ids=args.required_node_id,
            compound_tls_manifest=args.compound_tls_manifest,
        )
    except (OSError, ValueError, HamburgSignalBindingError) as exc:
        print(json.dumps({"status": "error", "automatic_promotion_gate": "blocked", "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["execution_gate"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
