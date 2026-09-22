"""Read measured engineering-plan records without claiming built road conditions."""

from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any

from ...core.candidate_contracts import file_sha256


REQUEST_SCHEMA = "torii.engineering-plan-observations-request/v1"
REPORT_SCHEMA = "torii.engineering-plan-observations/v1"
_UNIT_METRES = {"m": 1.0, "cm": 0.01, "mm": 0.001}
_DOCUMENT_KINDS = {"design", "as_built", "survey", "evaluation"}
_WIDTH_BASES = {"clear_width", "including_markings", "whole_carriageway", "standard_lane_width", "unknown"}


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text.")
    return value.strip()


def _date(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError(f"{label} must use YYYY-MM-DD or null.")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise ValueError(f"{label} must be a valid date.") from error


def read_engineering_plan_observations(
    request_file: str | Path, target_date: str | None = None,
) -> dict[str, Any]:
    """Validate manual length observations and retain their source and measurement basis."""
    path = Path(request_file).expanduser().resolve(strict=True)
    request_sha256 = file_sha256(path)
    request = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(request, dict) or request.get("schema") != REQUEST_SCHEMA:
        raise ValueError(f"Use request schema {REQUEST_SCHEMA}.")
    target = _date(target_date, "target_date")
    source = request.get("source")
    if not isinstance(source, dict):
        raise ValueError("source must contain PDF identity and document metadata.")
    source_path = Path(_text(source.get("path"), "source.path")).expanduser()
    source_path = (source_path if source_path.is_absolute() else path.parent / source_path).resolve(strict=True)
    expected = source.get("sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise ValueError("source requires a valid SHA-256.")
    source_sha256 = file_sha256(source_path)
    if source_sha256 != expected.lower():
        raise ValueError("Source PDF SHA-256 does not match.")
    with source_path.open("rb") as handle:
        if not handle.read(5).startswith(b"%PDF-"):
            raise ValueError("source.path must reference a PDF file.")
    title = _text(source.get("title"), "source.title")
    kind = source.get("document_kind")
    if not isinstance(kind, str) or kind not in _DOCUMENT_KINDS:
        raise ValueError("document_kind must be design, as_built, survey, or evaluation.")
    if "document_date" not in source:
        raise ValueError("source.document_date is required. Use null for an unknown date.")
    document_date = _date(source["document_date"], "source.document_date")
    raw_rows = request.get("observations")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("observations must contain at least one manual record.")
    observations, identities = [], set()
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise ValueError("Each observation must be an object.")
        identity = _text(raw.get("id"), "observation.id")
        if identity in identities:
            raise ValueError("Observation IDs must be unique.")
        identities.add(identity)
        page = raw.get("page")
        if type(page) is not int or page < 1:
            raise ValueError("Observation page must be a one-based positive integer.")
        location = _text(raw.get("location"), "observation.location")
        property_name = _text(raw.get("property"), "observation.property")
        value, unit = raw.get("value"), raw.get("unit")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Observation value must be a finite nonnegative number.")
        try:
            numeric = float(value)
        except OverflowError as error:
            raise ValueError("Observation value must fit a finite number.") from error
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError("Observation value must be a finite nonnegative number.")
        if "width" in property_name.lower() and value <= 0:
            raise ValueError("A width must be greater than zero.")
        if not isinstance(unit, str) or unit not in _UNIT_METRES:
            raise ValueError("Observation unit must be m, cm, or mm.")
        width_basis = raw.get("width_basis", "unknown")
        width_basis = width_basis if isinstance(width_basis, str) and width_basis in _WIDTH_BASES else "unknown"
        scope = _text(raw["scope"], "observation.scope") if "scope" in raw else None
        source_date = _date(raw["date"], "observation.date") if "date" in raw else document_date
        alignment = "unknown" if target is None or source_date is None else (
            "same_date_only" if target == source_date else "different_date")
        observations.append({
            "id": identity, "page": page, "location": location, "property": property_name,
            "raw": dict(raw), "value_m": numeric * _UNIT_METRES[unit], "width_basis": width_basis,
            "scope": scope, "source_date": source_date, "date_alignment": alignment,
            "document_kind": kind, "field_status": "not_verified", "decision": "review_required",
        })
    if file_sha256(path) != request_sha256 or file_sha256(source_path) != source_sha256:
        raise ValueError("An input changed while its observations were read.")
    return {
        "schema": REPORT_SCHEMA, "status": "pass", "decision": "review_required",
        "request": {"path": str(path), "sha256": request_sha256},
        "source": {**source, "path": str(source_path), "sha256": source_sha256,
                   "title": title, "document_date": document_date, "document_kind": kind},
        "target_date": target, "observations": observations, "network_changed": False,
        "page_bounds_verified": False, "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "These manually transcribed dimensions remain document observations. The PDF hash and positive page numbers are checked. "
            "Page contents, page bounds, measurement accuracy, and built conditions are not verified. "
            "A design or evaluation document does not establish as-built conditions. A matching date does not establish current conditions. "
            "No lane, permission, or connection change is authorized."
        ),
    }
