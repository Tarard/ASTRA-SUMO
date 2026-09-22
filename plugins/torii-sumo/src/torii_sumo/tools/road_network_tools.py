"""Read-only MCP adapters for evidence-bound road-network semantics."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.intersection.road_sumo_binding import bind_intersection_road_detail_to_sumo

from .intersection_tools import sumo_intersection_archetype_classify


def sumo_road_semantic_bridge(
    osm_file: str,
    sumo_net_file: str,
    hh_sib_snapshot_file: str,
    hh_sib_request_url: str,
    hh_sib_bbox: list[float],
    target_time: str,
    retrieved_at: str,
    valid_from: str | None = None,
    valid_to: str | None = None,
    reviewed_assignment_file: str | None = None,
    hvs_snapshot_file: str | None = None,
    hvs_request_url: str | None = None,
    hvs_bbox: list[float] | None = None,
    hvs_expected_sha256: str | None = None,
    output_dir: str | None = None,
    sumo_imported_from: str = "unknown",
    sumo_imported_source_sha256: str | None = None,
    osm_expected_sha256: str | None = None,
    sumo_expected_sha256: str | None = None,
    hh_sib_expected_sha256: str | None = None,
    official_osm_search_radius_m: float = 30.0,
    official_osm_overlap_tolerance_m: float = 10.0,
    osm_sumo_overlap_tolerance_m: float = 8.0,
) -> dict[str, Any]:
    """Bridge frozen OSM, SUMO, and HH-SIB evidence into road semantics.

    The tool only parses caller-supplied local snapshots and, where supplied,
    a reviewed relation/property-assignment JSON file.  It never downloads a
    source, changes a source or candidate network, generates SUMO plain XML,
    or authorizes junction, lane, TLS, demand, or simulation changes.

    When ``output_dir`` is supplied, it writes only three new, hash-listed JSON
    evidence artifacts there: the bridge report, the road-network-evidence
    projection, and a manifest.  It never writes beside an input snapshot.

    OSM-to-SUMO lineage additionally requires caller-declared import
    provenance: ``sumo_imported_from='osm'`` plus the exact source OSM SHA-256.
    Without it, the bridge remains blocked rather than guessing an import.
    """

    osm_path = _existing_file(osm_file, "osm_file")
    sumo_path = _existing_file(sumo_net_file, "sumo_net_file")
    hh_sib_path = _existing_file(hh_sib_snapshot_file, "hh_sib_snapshot_file")
    if len({osm_path, sumo_path, hh_sib_path}) != 3:
        raise ValueError("osm_file, sumo_net_file, and hh_sib_snapshot_file must be distinct local files")

    parsed_target_time = _parse_aware_datetime(target_time, "target_time")
    parsed_retrieved_at = _parse_aware_datetime(retrieved_at, "retrieved_at")
    parsed_valid_from = _parse_optional_aware_datetime(valid_from, "valid_from")
    parsed_valid_to = _parse_optional_aware_datetime(valid_to, "valid_to")
    if parsed_valid_from is not None and parsed_valid_to is not None and parsed_valid_to <= parsed_valid_from:
        raise ValueError("valid_to must be later than valid_from")

    normalized_bbox = _validated_bbox(hh_sib_bbox)
    normalized_request_url = _validated_hh_sib_request_url(hh_sib_request_url)
    normalized_hashes = {
        "osm_expected_sha256": _optional_sha256(osm_expected_sha256, "osm_expected_sha256"),
        "sumo_expected_sha256": _optional_sha256(sumo_expected_sha256, "sumo_expected_sha256"),
        "hh_sib_expected_sha256": _optional_sha256(
            hh_sib_expected_sha256,
            "hh_sib_expected_sha256",
        ),
    }
    normalized_sumo_imported_from = str(sumo_imported_from).strip().casefold() or "unknown"
    normalized_sumo_imported_source_sha256 = _optional_sha256(
        sumo_imported_source_sha256,
        "sumo_imported_source_sha256",
    )
    radii = {
        "official_osm_search_radius_m": _positive_number(
            official_osm_search_radius_m,
            "official_osm_search_radius_m",
        ),
        "official_osm_overlap_tolerance_m": _positive_number(
            official_osm_overlap_tolerance_m,
            "official_osm_overlap_tolerance_m",
        ),
        "osm_sumo_overlap_tolerance_m": _positive_number(
            osm_sumo_overlap_tolerance_m,
            "osm_sumo_overlap_tolerance_m",
        ),
    }
    reviewed_input, reviewed_input_summary = _read_reviewed_assignment_file(
        reviewed_assignment_file,
    )
    hvs_source, hvs_input_summary = _read_optional_hvs_source(
        snapshot_file=hvs_snapshot_file,
        request_url=hvs_request_url,
        bbox=hvs_bbox,
        fallback_bbox=normalized_bbox,
        target_time=parsed_target_time,
        retrieved_at=parsed_retrieved_at,
        valid_from=parsed_valid_from,
        valid_to=parsed_valid_to,
        expected_sha256=_optional_sha256(hvs_expected_sha256, "hvs_expected_sha256"),
    )
    category_sources = (*reviewed_input["official_category_sources"], *(() if hvs_source is None else (hvs_source,)))
    forbidden_output_dirs = {osm_path.parent, sumo_path.parent, hh_sib_path.parent}
    for optional_input in (hvs_snapshot_file, reviewed_assignment_file):
        if optional_input is not None and str(optional_input).strip():
            forbidden_output_dirs.add(Path(str(optional_input)).expanduser().resolve().parent)
    artifact_dir = _optional_output_dir(output_dir, forbidden_dirs=forbidden_output_dirs)

    # Deferred import keeps the MCP server importable while the generic road
    # semantics core is developed independently.  It is intentionally called
    # only after every local input has been validated and parsed.
    try:
        from torii_sumo.road_network.semantic_bridge import build_road_semantic_bridge
    except ImportError as exc:  # pragma: no cover - only protects incomplete plugin installs.
        return _blocked_unavailable_report(
            error=f"road semantic bridge core is unavailable: {exc}",
            inputs=_input_summary(
                osm_path,
                sumo_path,
                hh_sib_path,
                parsed_target_time,
                parsed_retrieved_at,
                reviewed_input_summary,
                hvs_input_summary,
                normalized_sumo_imported_from,
                normalized_sumo_imported_source_sha256,
            ),
        )

    bridge_report = build_road_semantic_bridge(
        osm_path=osm_path,
        sumo_path=sumo_path,
        hh_sib_snapshot_file=hh_sib_path,
        hh_sib_request_url=normalized_request_url,
        hh_sib_bbox=normalized_bbox,
        target_time=parsed_target_time,
        retrieved_at=parsed_retrieved_at,
        valid_from=parsed_valid_from,
        valid_to=parsed_valid_to,
        sumo_imported_from=normalized_sumo_imported_from,
        sumo_imported_source_sha256=normalized_sumo_imported_source_sha256,
        reviewed_official_osm_selections=reviewed_input["reviewed_official_osm_selections"],
        reviewed_property_assignments=reviewed_input["reviewed_property_assignments"],
        official_category_sources=category_sources,
        **normalized_hashes,
        **radii,
    )
    road_network_evidence = bridge_report.get("road_network_evidence")
    if not isinstance(road_network_evidence, Mapping):
        raise ValueError("road semantic bridge core returned no road_network_evidence artifact")

    result = {
        "status": str(bridge_report.get("status", "blocked")),
        "claim_status": "classification_only",
        "automatic_promotion_gate": "blocked",
        "classification_only": True,
        "source_inputs": _input_summary(
            osm_path,
            sumo_path,
            hh_sib_path,
            parsed_target_time,
            parsed_retrieved_at,
            reviewed_input_summary,
            hvs_input_summary,
            normalized_sumo_imported_from,
            normalized_sumo_imported_source_sha256,
        ),
        "bridge_report": bridge_report,
        "road_network_evidence": dict(road_network_evidence),
        "claim_boundary": _CLAIM_BOUNDARY,
    }
    if artifact_dir is not None:
        result["artifacts"] = _write_bridge_artifacts(
            artifact_dir,
            bridge_report=bridge_report,
            road_network_evidence=dict(road_network_evidence),
            source_inputs=result["source_inputs"],
        )
    else:
        result["artifacts"] = []
    return result


def sumo_intersection_road_sumo_bind(
    osm_file: str,
    seed_osm_node_id: str,
    road_network_evidence_file: str,
    bridge_report_file: str,
    traffic_side: str = "right",
    output_file: str | None = None,
) -> dict[str, Any]:
    """Bind classified road arms to immutable OSM-to-SUMO lineage for review.

    This is intentionally a read-only composition of the road-detail classifier
    and the semantic arm-to-edge binder.  A returned SUMO edge is lineage
    evidence, not a lane-to-lane connection, legal movement, stop line,
    junction owner, signal group, or materialization instruction.

    When ``output_file`` is supplied, the immutable binding payload itself is
    written as one new JSON artifact.  A later materializer can bind its exact
    bytes, but still must complete the separate lane/geometry/control gate.
    """

    bridge_path, bridge_report, bridge_sha256 = _read_json_object(
        bridge_report_file,
        "bridge_report_file",
    )
    lineage = bridge_report.get("osm_sumo_lineage")
    if not isinstance(lineage, Mapping):
        raise ValueError("bridge_report_file must contain an osm_sumo_lineage object")
    if bridge_report.get("classification_only") is not True:
        raise ValueError("bridge_report_file must declare classification_only=true")
    if bridge_report.get("automatic_promotion_gate") != "blocked":
        raise ValueError("bridge_report_file must retain automatic_promotion_gate=blocked")
    bridge_id = str(bridge_report.get("bridge_id", "")).strip()
    if not bridge_id:
        raise ValueError("bridge_report_file must contain a non-empty bridge_id")
    evidence_path, evidence, evidence_sha256 = _read_json_object(
        road_network_evidence_file,
        "road_network_evidence_file",
    )
    if str(evidence.get("bridge_id", "")).strip() != bridge_id:
        raise ValueError(
            "road_network_evidence_file bridge_id must equal bridge_report_file bridge_id"
        )

    classification = sumo_intersection_archetype_classify(
        osm_file=osm_file,
        seed_osm_node_id=seed_osm_node_id,
        traffic_side=traffic_side,
        road_network_evidence_file=road_network_evidence_file,
    )
    source_sha256 = str(classification["source_sha256"]).casefold()
    source_binding = lineage.get("source_sha256_binding")
    if not isinstance(source_binding, Mapping):
        raise ValueError("bridge_report_file osm_sumo_lineage requires source_sha256_binding")
    if str(source_binding.get("osm_source_sha256", "")).casefold() != source_sha256:
        raise ValueError(
            "bridge_report_file OSM lineage does not bind the exact local OSM snapshot used for classification"
        )

    road_detail = classification["evidence_artifacts"]["road_detail"]
    if not isinstance(road_detail, Mapping):
        raise ValueError("intersection classification returned no road_detail artifact")
    road_sumo_binding = bind_intersection_road_detail_to_sumo(road_detail, lineage)
    result = {
        "status": road_sumo_binding["status"],
        "claim_status": "classification_only",
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "classification": classification,
        "road_sumo_binding": road_sumo_binding,
        "bridge_report_provenance": {
            "source_file": str(bridge_path),
            "source_sha256": bridge_sha256,
        },
        "road_network_evidence_provenance": {
            "source_file": str(evidence_path),
            "source_sha256": evidence_sha256,
        },
        "claim_boundary": (
            "This is a review-only arm-to-edge lineage binding. It does not emit or authorize "
            "SUMO lane connections, junction edits, channelization, traffic-light binding, timing, "
            "demand, simulation, or digital-twin promotion."
        ),
    }
    artifact_path = _new_optional_artifact_path(
        output_file,
        "output_file",
        forbidden_paths={bridge_path, evidence_path, Path(str(osm_file)).expanduser().resolve()},
    )
    if artifact_path is not None:
        # The direct binding payload is deliberately written without a wrapper:
        # downstream materializers validate the binding schema and its bytes
        # rather than trusting an MCP-result envelope or a mutable log file.
        write_json_atomic(artifact_path, road_sumo_binding, sort_keys=True)
        result["road_sumo_binding_artifact"] = {
            "path": str(artifact_path),
            "sha256": _sha256_file(artifact_path),
        }
    else:
        result["road_sumo_binding_artifact"] = None
    return result


_CLAIM_BOUNDARY = (
    "This is a read-only semantic evidence bridge. It preserves immutable local source snapshots, "
    "candidate conflation evidence, and pass-only reviewed identity/property assertions. It does not "
    "prove geometric reconstruction, authorize road or junction edits, infer lane connections, bind "
    "traffic signals, create SUMO artifacts, download data, or promote a digital-twin claim."
)

_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


def _existing_file(value: str, field_name: str) -> Path:
    path = Path(str(value).strip()).expanduser().resolve()
    if not str(value).strip() or not path.is_file():
        raise ValueError(f"{field_name} must be an existing local file: {value}")
    return path


def _new_optional_artifact_path(
    value: str | None,
    field_name: str,
    *,
    forbidden_paths: set[Path],
) -> Path | None:
    if value is None or not str(value).strip():
        return None
    path = Path(str(value).strip()).expanduser().resolve()
    if path.suffix.casefold() != ".json":
        raise ValueError(f"{field_name} must use a .json filename")
    if path in {item.resolve() for item in forbidden_paths}:
        raise ValueError(f"{field_name} must not overwrite an input evidence file")
    if path.exists():
        raise ValueError(f"{field_name} must name a new artifact file: {path}")
    return path


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json_object(value: str, field_name: str) -> tuple[Path, dict[str, Any], str]:
    path = _existing_file(value, field_name)
    raw = path.read_bytes()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"{field_name} must be UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must contain valid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError(f"{field_name} JSON root must be an object")
    return path, dict(parsed), hashlib.sha256(raw).hexdigest()


def _parse_aware_datetime(value: str, field_name: str) -> datetime:
    raw = str(value).strip()
    if not raw:
        raise ValueError(f"{field_name} is required and must be an ISO-8601 timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return parsed


def _parse_optional_aware_datetime(value: str | None, field_name: str) -> datetime | None:
    return None if value is None or not str(value).strip() else _parse_aware_datetime(value, field_name)


def _validated_bbox(value: list[float]) -> tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("hh_sib_bbox must be [west, south, east, north]")
    try:
        bbox = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ValueError("hh_sib_bbox values must be finite numbers") from exc
    if not all(math.isfinite(item) for item in bbox):
        raise ValueError("hh_sib_bbox values must be finite numbers")
    west, south, east, north = bbox
    if not west < east or not south < north:
        raise ValueError("hh_sib_bbox must satisfy west < east and south < north")
    return bbox


def _validated_hh_sib_request_url(value: str) -> str:
    normalized = str(value).strip()
    required = "https://api.hamburg.de/datasets/v1/strassen_und_wegenetz/collections/strassennetz_gesamt/items"
    if not normalized.startswith(required):
        raise ValueError("hh_sib_request_url must target the official HH-SIB HTTPS items endpoint")
    return normalized


def _optional_sha256(value: str | None, field_name: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    normalized = str(value).strip().casefold()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256")
    return normalized


def _positive_number(value: float, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be finite and positive") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field_name} must be finite and positive")
    return number


def _read_reviewed_assignment_file(
    value: str | None,
) -> tuple[dict[str, tuple[dict[str, Any], ...]], dict[str, Any] | None]:
    if value is None or not str(value).strip():
        return {
            "reviewed_official_osm_selections": (),
            "reviewed_property_assignments": (),
            "official_category_sources": (),
        }, None
    path = _existing_file(value, "reviewed_assignment_file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("reviewed_assignment_file must contain valid UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("reviewed_assignment_file root must be a JSON object")
    allowed_keys = {
        "reviewed_official_osm_selections",
        "reviewed_property_assignments",
        "official_category_sources",
    }
    unknown_keys = sorted(set(payload) - allowed_keys)
    if unknown_keys:
        raise ValueError(f"reviewed_assignment_file has unsupported keys: {', '.join(unknown_keys)}")
    raw_selections = payload.get("reviewed_official_osm_selections", [])
    raw_assignments = payload.get("reviewed_property_assignments", [])
    raw_category_sources = payload.get("official_category_sources", [])
    if not isinstance(raw_selections, list) or not isinstance(raw_assignments, list) or not isinstance(
        raw_category_sources,
        list,
    ):
        raise ValueError(
            "reviewed_assignment_file keys reviewed_official_osm_selections, "
            "reviewed_property_assignments, and official_category_sources must each be arrays"
        )
    selections = tuple(_selection_from_json(item) for item in raw_selections)
    assignments = tuple(_assignment_json(item) for item in raw_assignments)
    category_sources = tuple(_category_source_from_json(item) for item in raw_category_sources)
    return (
        {
            "reviewed_official_osm_selections": selections,
            "reviewed_property_assignments": assignments,
            "official_category_sources": category_sources,
        },
        {
            "path": str(path),
            "selection_count": len(selections),
            "property_assignment_count": len(assignments),
            "official_category_source_count": len(category_sources),
        },
    )


def _selection_from_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("each reviewed_official_osm_selections item must be an object")
    allowed = {"candidate_set_id", "review_decision_id", "reason"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"reviewed official-to-OSM selection has unsupported keys: {', '.join(unknown)}")
    candidate_set_id = str(value.get("candidate_set_id", "")).strip()
    review_decision_id = str(value.get("review_decision_id", "")).strip()
    if not candidate_set_id or not review_decision_id:
        raise ValueError("reviewed official-to-OSM selection needs candidate_set_id and review_decision_id")
    return {
        "candidate_set_id": candidate_set_id,
        "review_decision_id": review_decision_id,
        "reason": str(value.get("reason", "")).strip(),
    }


def _assignment_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("each reviewed_property_assignments item must be an object")
    required = {
        "assignment_id",
        "target_ref",
        "property_name",
        "classification_scheme",
        "direction",
        "evidence_refs",
        "status",
    }
    missing = sorted(key for key in required if key not in value)
    if missing:
        raise ValueError(f"reviewed property assignment is missing keys: {', '.join(missing)}")
    if str(value["status"]) != "pass":
        raise ValueError("reviewed property assignments must have status='pass'")
    if not isinstance(value["target_ref"], Mapping) or not isinstance(value["evidence_refs"], list):
        raise ValueError("reviewed property assignment target_ref must be an object and evidence_refs an array")
    return dict(value)


def _category_source_from_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("each official_category_sources item must be an object")
    required = {"status", "source_snapshot"}
    missing = sorted(key for key in required if key not in value)
    if missing:
        raise ValueError(f"official category source is missing keys: {', '.join(missing)}")
    if str(value["status"]) != "pass":
        raise ValueError("official category sources must have status='pass'")
    snapshot = value["source_snapshot"]
    if not isinstance(snapshot, Mapping) or not _SHA256_RE.fullmatch(str(snapshot.get("sha256", ""))):
        raise ValueError("official category source source_snapshot.sha256 must be a SHA-256")
    source_id = str(value.get("source_id") or snapshot.get("dataset_id") or "").strip()
    if not source_id:
        raise ValueError("official category source needs source_id or source_snapshot.dataset_id")
    return {**dict(value), "source_id": source_id}


def _read_optional_hvs_source(
    *,
    snapshot_file: str | None,
    request_url: str | None,
    bbox: list[float] | None,
    fallback_bbox: tuple[float, float, float, float],
    target_time: datetime,
    retrieved_at: datetime,
    valid_from: datetime | None,
    valid_to: datetime | None,
    expected_sha256: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    supplied = [snapshot_file is not None and str(snapshot_file).strip(), request_url is not None and str(request_url).strip()]
    if any(supplied) and not all(supplied):
        raise ValueError("hvs_snapshot_file and hvs_request_url must be supplied together")
    if not all(supplied):
        if bbox is not None or expected_sha256 is not None:
            raise ValueError("hvs_bbox and hvs_expected_sha256 require hvs_snapshot_file and hvs_request_url")
        return None, None

    hvs_path = _existing_file(str(snapshot_file), "hvs_snapshot_file")
    normalized_hvs_bbox = fallback_bbox if bbox is None else _validated_bbox(bbox)
    normalized_hvs_url = _validated_hvs_request_url(str(request_url))
    from torii_sumo.road_network.adapters.hamburg_hvs import read_hamburg_hvs_snapshot

    report = read_hamburg_hvs_snapshot(
        hvs_path,
        request_url=normalized_hvs_url,
        bbox=normalized_hvs_bbox,
        target_time=target_time,
        retrieved_at=retrieved_at,
        valid_from=valid_from,
        valid_to=valid_to,
        expected_sha256=expected_sha256,
    )
    source = report.get("official_category_source")
    if not isinstance(source, Mapping):
        raise ValueError("HVS adapter returned no official_category_source")
    return dict(source), {
        "path": str(hvs_path),
        "source_id": str(source.get("source_id", "")),
        "status": str(source.get("status", "blocked")),
        "sha256": str(source.get("source_snapshot", {}).get("sha256", "")),
    }


def _validated_hvs_request_url(value: str) -> str:
    normalized = str(value).strip()
    required = "https://api.hamburg.de/datasets/v1/hauptverkehrsstrassen/collections/hauptverkehrsstrassen/items"
    if not normalized.startswith(required):
        raise ValueError("hvs_request_url must target the official HVS HTTPS items endpoint")
    return normalized


def _optional_output_dir(value: str | None, *, forbidden_dirs: set[Path]) -> Path | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        raise ValueError("output_dir must be a non-empty local directory path when supplied")
    path = Path(raw).expanduser().resolve()
    if path.exists() and not path.is_dir():
        raise ValueError(f"output_dir must be a directory path: {value}")
    if path in forbidden_dirs:
        raise ValueError("output_dir must be separate from every source-input directory")
    return path


def _write_bridge_artifacts(
    output_dir: Path,
    *,
    bridge_report: Mapping[str, Any],
    road_network_evidence: Mapping[str, Any],
    source_inputs: Any,
) -> list[dict[str, str]]:
    from torii_sumo.core.artifact_io import write_json_atomic

    output_dir.mkdir(parents=True, exist_ok=True)
    bridge_file = output_dir / "road_semantic_bridge.json"
    evidence_file = output_dir / "road_network_evidence.json"
    manifest_file = output_dir / "road_semantic_bridge.manifest.json"
    existing = [path.name for path in (bridge_file, evidence_file, manifest_file) if path.exists()]
    if existing:
        raise ValueError(
            "output_dir already contains road semantic bridge artifacts; choose a new directory: "
            + ", ".join(existing)
        )
    write_json_atomic(bridge_file, dict(bridge_report), sort_keys=True)
    write_json_atomic(evidence_file, dict(road_network_evidence), sort_keys=True)
    artifacts = [
        {"kind": "bridge_report", "path": str(bridge_file), "sha256": _sha256_file(bridge_file)},
        {
            "kind": "road_network_evidence",
            "path": str(evidence_file),
            "sha256": _sha256_file(evidence_file),
        },
    ]
    manifest = {
        "schema": "torii.road-semantic-bridge-artifact-manifest/v1",
        "bridge_id": str(bridge_report.get("bridge_id", "")),
        "source_sha256s": list(bridge_report.get("source_sha256s", ())),
        "road_network_evidence_bridge_id": str(road_network_evidence.get("bridge_id", "")),
        "source_inputs": source_inputs,
        "artifacts": artifacts,
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": _CLAIM_BOUNDARY,
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return [
        *artifacts,
        {"kind": "manifest", "path": str(manifest_file), "sha256": _sha256_file(manifest_file)},
    ]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _input_summary(
    osm_path: Path,
    sumo_path: Path,
    hh_sib_path: Path,
    target_time: datetime,
    retrieved_at: datetime,
    reviewed_input: Mapping[str, Any] | None,
    hvs_input: Mapping[str, Any] | None,
    sumo_imported_from: str,
    sumo_imported_source_sha256: str | None,
) -> dict[str, Any]:
    return {
        "osm_file": str(osm_path),
        "sumo_net_file": str(sumo_path),
        "hh_sib_snapshot_file": str(hh_sib_path),
        "target_time": target_time.isoformat(),
        "retrieved_at": retrieved_at.isoformat(),
        "reviewed_assignment_input": dict(reviewed_input) if reviewed_input else None,
        "hvs_source_input": dict(hvs_input) if hvs_input else None,
        "sumo_import_provenance": {
            "imported_from": sumo_imported_from,
            "imported_source_sha256": sumo_imported_source_sha256,
        },
    }


def _blocked_unavailable_report(*, error: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
    road_network_evidence = {
        "schema": "torii.road-detail-evidence-projection/v1",
        "status": "blocked",
        "by_way_id": {},
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": _CLAIM_BOUNDARY,
    }
    return {
        "status": "blocked",
        "claim_status": "blocked",
        "automatic_promotion_gate": "blocked",
        "classification_only": True,
        "source_inputs": dict(inputs),
        "bridge_report": {"status": "blocked", "error": error},
        "road_network_evidence": road_network_evidence,
        "claim_boundary": _CLAIM_BOUNDARY,
    }
