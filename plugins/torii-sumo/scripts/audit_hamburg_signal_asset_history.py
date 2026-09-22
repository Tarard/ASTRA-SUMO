from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.hamburg_official import (
    audit_hamburg_signal_asset_directory_history,
)


_ALLOWED_INDEX_HOSTS = frozenset(
    {"daten-hamburg.de", "archiv.transparenz.hamburg.de"}
)


def _snapshot(value: str) -> tuple[str, str, str]:
    label, separator, sources = value.partition("=")
    if not separator or not label.strip():
        raise argparse.ArgumentTypeError(
            "expected LABEL=MAP_INDEX_PATH_OR_URL=OCIT_INDEX_PATH_OR_URL"
        )
    map_source, separator, ocit_source = sources.partition("=")
    if not separator or not map_source.strip() or not ocit_source.strip():
        raise argparse.ArgumentTypeError(
            "expected LABEL=MAP_INDEX_PATH_OR_URL=OCIT_INDEX_PATH_OR_URL"
        )
    return label.strip(), map_source.strip(), ocit_source.strip()


def _read_source(source: str, *, timeout_seconds: float) -> bytes:
    if source.startswith("https://"):
        parsed = urlparse(source)
        if parsed.netloc.casefold() not in _ALLOWED_INDEX_HOSTS:
            raise ValueError(
                "signal-asset index URL must use an official Hamburg directory host"
            )
        request = Request(
            source,
            headers={"Accept": "text/html", "User-Agent": "Torii-SUMO/1.1"},
        )
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            return response.read()
    if "://" in source:
        raise ValueError("signal-asset index URL must use HTTPS")
    return Path(source).expanduser().resolve().read_bytes()


def _snapshot_file(output_dir: Path, label: str, kind: str, content: bytes) -> Path:
    digest = hashlib.sha256(content).hexdigest()
    path = output_dir / "directory" / f"{label}.{kind}.{digest[:12]}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError(f"content-addressed snapshot cache conflict: {path}")
    if not path.is_file():
        path.write_bytes(content)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit current and archived official Hamburg MAP/OCIT directory indexes "
            "without substituting historical assets for current ones."
        )
    )
    parser.add_argument("--node-id", action="append", required=True)
    parser.add_argument("--snapshot", action="append", required=True, type=_snapshot)
    parser.add_argument("--current-label", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve()
    snapshots: list[dict[str, object]] = []
    for label, map_source, ocit_source in args.snapshot:
        map_html = _read_source(map_source, timeout_seconds=args.timeout_seconds)
        ocit_html = _read_source(ocit_source, timeout_seconds=args.timeout_seconds)
        map_path = _snapshot_file(output_dir, label, "map", map_html)
        ocit_path = _snapshot_file(output_dir, label, "ocit_c", ocit_html)
        snapshots.append(
            {
                "label": label,
                "map_index_html": map_html,
                "ocit_c_index_html": ocit_html,
                "map_index_url": map_source,
                "ocit_c_index_url": ocit_source,
                "snapshot_paths": {
                    "map": str(map_path),
                    "ocit_c": str(ocit_path),
                },
            }
        )

    report = audit_hamburg_signal_asset_directory_history(
        args.node_id,
        snapshots=snapshots,
        current_snapshot_label=args.current_label,
    )
    report["snapshot_paths"] = [item["snapshot_paths"] for item in snapshots]
    report["fetched_or_loaded_at"] = datetime.now(timezone.utc).isoformat()
    canonical = json.dumps(report, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    report_path = output_dir / (
        "hamburg-signal-asset-history-audit."
        f"{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:12]}.json"
    )
    write_json_atomic(report_path, report, ensure_ascii=False, sort_keys=True)
    report["report_path"] = str(report_path)
    report["report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if report["decision"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
