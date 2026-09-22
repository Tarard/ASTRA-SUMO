"""Create an immutable, bounded subset of an official HH-SIB snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256


HAMBURG_OFFICIAL_ROAD_SCOPE_SCHEMA = "torii.hamburg-official-road-scope/v1"


class HamburgOfficialRoadScopeError(ValueError):
    """Raised when a bounded official-road scope cannot be created."""


def materialize_hamburg_official_road_feature_scope(
    *,
    source_file: Path,
    feature_ids: Sequence[str],
    output_file: Path,
    manifest_file: Path | None = None,
    scope_id: str = "hamburg_official_road_scope",
) -> dict[str, Any]:
    """Write a content-addressed feature subset without changing the source.

    This is a bounded acquisition step, not a conflation decision.  The
    adapter can then parse only the selected official records, so an unrelated
    malformed feature in a large OGC response cannot invalidate a declared
    corridor scope silently.
    """

    source = Path(source_file).expanduser().resolve()
    destination = Path(output_file).expanduser().resolve()
    if not source.is_file():
        raise HamburgOfficialRoadScopeError(f"source_file does not exist: {source}")
    if source == destination:
        raise HamburgOfficialRoadScopeError("output_file must be separate from source_file")
    requested = tuple(str(value).strip() for value in feature_ids)
    if not requested or any(not value for value in requested) or len(set(requested)) != len(requested):
        raise HamburgOfficialRoadScopeError("feature_ids must be a unique non-empty sequence")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HamburgOfficialRoadScopeError(f"invalid source GeoJSON: {source}") from exc
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise HamburgOfficialRoadScopeError("source must be a GeoJSON FeatureCollection")
    by_id = {
        str(feature.get("id")): feature
        for feature in payload["features"]
        if isinstance(feature, dict) and feature.get("id") is not None
    }
    missing = sorted(set(requested) - set(by_id))
    if missing:
        raise HamburgOfficialRoadScopeError("requested official feature ids are missing: " + ", ".join(missing))
    scoped = dict(payload)
    scoped["features"] = [by_id[feature_id] for feature_id in requested]
    scoped["numberReturned"] = len(scoped["features"])
    scoped["numberMatched"] = len(scoped["features"])
    scoped["torii_scope"] = {
        "schema": HAMBURG_OFFICIAL_ROAD_SCOPE_SCHEMA,
        "scope_id": str(scope_id).strip() or "hamburg_official_road_scope",
        "source_sha256": file_sha256(source),
        "selected_feature_ids": list(requested),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(destination, scoped, sort_keys=False)
    report: dict[str, Any] = {
        "schema": HAMBURG_OFFICIAL_ROAD_SCOPE_SCHEMA,
        "status": "pass",
        "scope_id": scoped["torii_scope"]["scope_id"],
        "source": {"path": str(source), "sha256": file_sha256(source), "bytes": source.stat().st_size},
        "output": {"path": str(destination), "sha256": file_sha256(destination), "bytes": destination.stat().st_size},
        "selected_feature_ids": list(requested),
        "claim_boundary": {
            "proves": ["the output contains exactly the declared official source features"],
            "does_not_prove": ["road conflation, lane binding, legal movements, or SUMO network correctness"],
        },
    }
    if manifest_file is not None:
        manifest = Path(manifest_file).expanduser().resolve()
        if manifest in {source, destination}:
            raise HamburgOfficialRoadScopeError("manifest_file must be separate from source and output")
        write_json_atomic(manifest, report, sort_keys=True)
        report["manifest_file"] = str(manifest)
        report["manifest_sha256"] = file_sha256(manifest)
    return report


__all__ = [
    "HAMBURG_OFFICIAL_ROAD_SCOPE_SCHEMA",
    "HamburgOfficialRoadScopeError",
    "materialize_hamburg_official_road_feature_scope",
]
