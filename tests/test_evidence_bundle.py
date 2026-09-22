from datetime import datetime, timezone
from pathlib import Path

from torii_sumo.evidence import bundle as evidence_bundle
from torii_sumo.evidence.bundle import write_evidence_bundle


def test_write_evidence_bundle_creates_json_and_markdown(tmp_path: Path) -> None:
    bundle = write_evidence_bundle(
        tmp_path,
        label="smoke",
        payload={
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "artifacts": ["summary.xml", "tripinfo.xml"],
        },
    )

    assert bundle.status == "pass"
    assert Path(bundle.json_path).exists()
    assert Path(bundle.markdown_path).exists()
    assert "diagnostic-demo" in Path(bundle.markdown_path).read_text(encoding="utf-8")


def test_write_evidence_bundle_avoids_overwriting_same_sanitized_label(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FixedDatetime:
        @classmethod
        def now(cls, tz: timezone) -> datetime:
            return datetime(2026, 6, 20, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(evidence_bundle, "datetime", FixedDatetime)

    first = write_evidence_bundle(
        tmp_path,
        label="same label",
        payload={"status": "pass", "claim_status": "diagnostic-demo"},
    )
    second = write_evidence_bundle(
        tmp_path,
        label="same/label",
        payload={"status": "pass", "claim_status": "diagnostic-demo"},
    )

    assert Path(first.json_path).exists()
    assert Path(second.json_path).exists()
    assert first.json_path != second.json_path
