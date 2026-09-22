from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from torii_sumo.core.hamburg_compound_official_tls import (
    HamburgCompoundOfficialTlsError,
    materialize_hamburg_compound_official_tls_candidate,
)


_NODES = ("2349", "2394")
_KINDS = ("map_xml", "map_kml", "ocit_xml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bind Hamburg 2349/2394 MAP and OCIT-C evidence to a preserved "
            "multi-owner SUMO corridor topology."
        )
    )
    parser.add_argument("--source-net", required=True, type=Path)
    parser.add_argument("--join-evidence", required=True, type=Path)
    parser.add_argument("--signal-asset-dir", required=True, type=Path)
    parser.add_argument("--asset-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--named-scope-manifest",
        type=Path,
        help=(
            "optional hash-bound named-corridor contract; when supplied, the signal-stage "
            "gate must prove a complete 2349/2394 scope before any output is written"
        ),
    )
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--expected-join-evidence-sha256", required=True)
    parser.add_argument("--netconvert-binary", default="netconvert")
    parser.add_argument("--sumo-binary", default="sumo")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    return parser


def _asset_hashes(path: Path) -> dict[str, str]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != (
        "torii.hamburg-official-signal-asset-bundle/v1"
    ):
        raise HamburgCompoundOfficialTlsError(
            "asset manifest must use torii.hamburg-official-signal-asset-bundle/v1"
        )
    rows = payload.get("assets")
    if not isinstance(rows, list):
        raise HamburgCompoundOfficialTlsError("asset manifest has no assets list")
    hashes: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        node_id = str(row.get("node_id", ""))
        kind = str(row.get("kind", ""))
        if node_id not in _NODES or kind not in _KINDS:
            continue
        role = f"{node_id}_{kind}"
        digest = str(row.get("sha256", "")).lower()
        if role in hashes:
            raise HamburgCompoundOfficialTlsError(
                f"asset manifest contains duplicate role: {role}"
            )
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise HamburgCompoundOfficialTlsError(
                f"asset manifest has an invalid SHA-256 for {role}"
            )
        hashes[role] = digest
    expected = {f"{node}_{kind}" for node in _NODES for kind in _KINDS}
    if set(hashes) != expected:
        missing = ", ".join(sorted(expected - set(hashes)))
        raise HamburgCompoundOfficialTlsError(
            f"asset manifest does not contain the complete 2349/2394 bundle: {missing}"
        )
    return hashes


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = materialize_hamburg_compound_official_tls_candidate(
            source_net_file=args.source_net,
            join_evidence_file=args.join_evidence,
            signal_asset_dir=args.signal_asset_dir,
            output_dir=args.output_dir,
            expected_source_sha256=args.expected_source_sha256,
            expected_join_evidence_sha256=args.expected_join_evidence_sha256,
            expected_asset_sha256=_asset_hashes(args.asset_manifest),
            named_scope_manifest_file=args.named_scope_manifest,
            netconvert_binary=args.netconvert_binary,
            sumo_binary=args.sumo_binary,
            timeout_seconds=args.timeout_seconds,
        )
    except (HamburgCompoundOfficialTlsError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema": "torii.hamburg-compound-official-tls-cli-error/v1",
                    "status": "blocked",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "human_action_required": False,
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("status") == "topology_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
