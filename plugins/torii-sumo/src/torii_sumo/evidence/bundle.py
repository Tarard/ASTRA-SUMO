from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class EvidenceBundleResult(BaseModel):
    status: str
    label: str
    output_dir: str
    json_path: str
    markdown_path: str
    claim_status: str

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


def write_evidence_bundle(
    output_dir: Path,
    *,
    label: str,
    payload: dict[str, Any],
) -> EvidenceBundleResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_label = _safe_label(label)
    json_path, markdown_path = _unique_bundle_paths(output_dir, timestamp, safe_label)
    claim_status = str(payload.get("claim_status", "diagnostic-demo"))
    enriched = {
        "label": label,
        "created_at_utc": timestamp,
        "payload": payload,
    }
    json_path.write_text(json.dumps(enriched, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(_render_markdown(label, timestamp, payload), encoding="utf-8")
    return EvidenceBundleResult(
        status="pass",
        label=label,
        output_dir=str(output_dir),
        json_path=str(json_path),
        markdown_path=str(markdown_path),
        claim_status=claim_status,
    )


def _render_markdown(label: str, timestamp: str, payload: dict[str, Any]) -> str:
    lines = [
        f"# Evidence Bundle: {label}",
        "",
        f"- created_at_utc: `{timestamp}`",
        f"- status: `{payload.get('status', 'unknown')}`",
        f"- claim_status: `{payload.get('claim_status', 'diagnostic-demo')}`",
        "",
        "## Payload",
        "",
        "```json",
        json.dumps(payload, indent=2, sort_keys=True),
        "```",
        "",
    ]
    return "\n".join(lines)


def _safe_label(label: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in label)
    return "-".join(part for part in cleaned.split("-") if part) or "evidence"


def _unique_bundle_paths(output_dir: Path, timestamp: str, safe_label: str) -> tuple[Path, Path]:
    stem = f"{timestamp}-{safe_label}"
    suffix = 1
    while True:
        candidate_stem = stem if suffix == 1 else f"{stem}-{suffix}"
        json_path = output_dir / f"{candidate_stem}.json"
        markdown_path = output_dir / f"{candidate_stem}.md"
        if not json_path.exists() and not markdown_path.exists():
            return json_path, markdown_path
        suffix += 1
