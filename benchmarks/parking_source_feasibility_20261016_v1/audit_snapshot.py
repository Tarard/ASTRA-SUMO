"""Audit the committed snapshot without fetching data or assigning a time zone."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def audit_snapshot():
    folder = Path(__file__).resolve().parent
    record = json.loads((folder / "speicherstadt-source-check.json").read_text(encoding="utf-8"))
    raw = (folder / "hamburg-parking.gml").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == record["source"]["raw_sha256"], "The frozen response changed."
    root = ET.fromstring(raw)
    members = root.findall("{http://www.opengis.net/wfs/2.0}member")
    assert len(members) == record["source"]["actual_feature_members"]
    rows = [{child.tag.rsplit("}", 1)[-1]: child.text for child in member[0] if not list(child)} for member in members]
    available = [row for row in rows if all(row.get(key) is not None for key in ("frei", "gesamt", "received"))]
    numeric_findings, different_statuses, incomplete_statuses, totals_differ = [], [], [], []
    receipts = []
    for row in available:
        total, free = int(row["gesamt"]), int(row["frei"])
        if total <= 0 or not 0 <= free <= total:
            numeric_findings.append({"name": row["name"], "reported_total": total, "reported_free": free,
                "stock_derivation": "unavailable", "reason": "total_unknown_or_nonpositive" if total <= 0 else "free_outside_reported_capacity"})
        if row.get("status") is None or row.get("situation") is None:
            incomplete_statuses.append({"name": row["name"], "status": row.get("status"), "situation": row.get("situation")})
        elif row["status"] != row["situation"]:
            different_statuses.append({"name": row["name"], "status": row.get("status"), "situation": row.get("situation")})
        if row.get("stellplaetze_gesamt") and int(row["stellplaetze_gesamt"]) != total:
            totals_differ.append({"name": row["name"], "static_reported_total": int(row["stellplaetze_gesamt"]), "dynamic_reported_total": total})
        receipts.append((row["name"], datetime.strptime(row["received"], "%d.%m.%Y, %H:%M")))
    response_time = datetime.fromisoformat(root.get("timeStamp").replace("Z", "+00:00"))
    hypotheses = {}
    for offset in (0, 2):
        ages = [(name, (response_time - received.replace(tzinfo=timezone(timedelta(hours=offset)))).total_seconds())
                for name, received in receipts]
        hypotheses[f"UTC+{offset:02d}"] = {"future_receipt_count": sum(age < 0 for _, age in ages),
            "age_range_seconds": [min(age for _, age in ages), max(age for _, age in ages)],
            "receipts_older_than_one_day": [{"name": name, "age_seconds": age} for name, age in ages if age > 86400]}
    return {"kind": "frozen_parking_snapshot_audit", "promotion_authority": False,
        "raw_sha256": record["source"]["raw_sha256"], "actual_feature_members": len(rows),
        "records_with_availability_fields": len(available), "records_without_availability_fields": len(rows) - len(available),
        "stock_derivation_findings": numeric_findings, "textually_different_status_fields": different_statuses,
        "incomplete_status_fields": incomplete_statuses,
        "different_static_and_dynamic_totals": totals_differ, "receipt_time_hypotheses": hypotheses,
        "time_zone": "not_declared_by_source", "receipt_timestamp_meaning": "unknown",
        "claim_boundary": "Field presence, numeric consistency and timestamp hypotheses only. Closed or fault states do not by themselves invalidate a reported count. No occupancy series, stock meaning, source measurement time or field accuracy is established."}


if __name__ == "__main__":
    print(json.dumps(audit_snapshot(), indent=2, ensure_ascii=True))
