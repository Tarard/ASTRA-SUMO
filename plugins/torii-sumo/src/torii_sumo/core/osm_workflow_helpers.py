"""Dependency-free leaf helpers extracted from ``osm_workflow``.

These helpers have no calls into ``osm_workflow`` or ``osm_workflow_tail``.
The original module re-exports them for compatibility.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import inspect
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Mapping

from .road_connectivity_teacher_model import (
    canonical_road_connectivity_bundle,
    compare_road_connectivity_bundles,
)
from .road_scope import RECOMMENDED_ROAD_LEVEL_SCOPE, ROAD_LEVEL_SCOPE_OPTIONS

TLS_SEMANTIC_DELTA_KEYS = {
    "tl_logic_count",
    "traffic_light_junction_count",
    "tls_controlled_connection_count",
    "multi_junction_tl_logic_count",
    "traffic_light_junction_without_tls_connection_count",
    "tls_shared_linkindex_group_count",
    "tls_sparse_linkindex_tl_logic_count",
}



def _supports_keyword(func: Callable[..., Any], name: str) -> bool:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        or (
            parameter.name == name
            and parameter.kind in {inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
        )
        for parameter in signature.parameters.values()
    )
def _load_review_decisions_file(path: Path | None) -> tuple[dict[str, Any] | None, str, str]:
    """Load an explicit review-decision artifact without weakening the default gate."""

    if path is None:
        return None, "not_supplied", ""
    try:
        resolved = path.resolve()
        loaded = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, "invalid", f"{type(exc).__name__}: {exc}"
    if not isinstance(loaded, Mapping):
        return None, "invalid", "review decisions must be a JSON object"
    locations = loaded.get("locations")
    if not isinstance(locations, list):
        return None, "invalid", "review decisions must contain a locations list"
    return dict(loaded), "loaded", ""
def _osm_highway_classes(osm_file: Path) -> set[str] | None:
    try:
        if osm_file.suffix == ".gz":
            with gzip.open(osm_file, "rt", encoding="utf-8") as handle:
                root = ET.parse(handle).getroot()
        else:
            root = ET.parse(osm_file).getroot()
    except (OSError, ET.ParseError, UnicodeDecodeError):
        return None
    return {
        str(tag.attrib.get("v", "")).strip()
        for way in root.findall("way")
        for tag in way.findall("tag")
        if tag.attrib.get("k") == "highway" and str(tag.attrib.get("v", "")).strip()
    }
def _osm_tag_values(osm_file: Path, key: str) -> set[str] | None:
    try:
        if osm_file.suffix == ".gz":
            with gzip.open(osm_file, "rt", encoding="utf-8") as handle:
                root = ET.parse(handle).getroot()
        else:
            root = ET.parse(osm_file).getroot()
    except (OSError, ET.ParseError, UnicodeDecodeError):
        return None
    return {
        str(tag.attrib.get("v", "")).strip()
        for way in root.findall("way")
        for tag in way.findall("tag")
        if tag.attrib.get("k") == key and str(tag.attrib.get("v", "")).strip()
    }
def _candidate_fields(place_report: Mapping[str, Any] | None) -> dict[str, Any]:
    if place_report is None:
        return {
            "candidate_display_name": "",
            "candidate_osm_type": "",
            "candidate_osm_id": "",
            "candidate_bbox": "",
            "candidate_lat": "",
            "candidate_lon": "",
            "candidate_osm_url": "",
        }
    return {
        "candidate_display_name": str(place_report.get("candidate_display_name", "")),
        "candidate_osm_type": str(place_report.get("candidate_osm_type", "")),
        "candidate_osm_id": str(place_report.get("candidate_osm_id", "")),
        "candidate_bbox": str(place_report.get("candidate_bbox", "")),
        "candidate_lat": str(place_report.get("candidate_lat", "")),
        "candidate_lon": str(place_report.get("candidate_lon", "")),
        "candidate_osm_url": str(place_report.get("candidate_osm_url", "")),
    }
def _road_level_scope_fields() -> dict[str, Any]:
    return {
        "road_level_options": list(ROAD_LEVEL_SCOPE_OPTIONS),
        "recommended_road_level": RECOMMENDED_ROAD_LEVEL_SCOPE,
    }
def _gate_value(report: Mapping[str, Any]) -> str:
    status = str(report.get("status", "fail"))
    if status == "pass":
        return "pass"
    if status == "blocked":
        return "blocked"
    return "fail"
def _connection_mode_gate_value(report: Mapping[str, Any] | None) -> str:
    if report is None:
        return "skipped"
    status = str(report.get("status", "fail"))
    return status if status in {"pass", "review_required", "fail"} else "fail"
def _int_field(report: Mapping[str, Any], key: str) -> int:
    try:
        return int(report.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0
def _list_field_count(report: Mapping[str, Any] | None, key: str) -> int:
    if report is None:
        return 0
    value = report.get(key, [])
    return len(value) if isinstance(value, list) else 0
def _teacher_guided_exemplar_ready_stats(report: Mapping[str, Any] | None) -> tuple[int, int]:
    if report is None:
        return 0, 0
    ready_count = 0
    signature_count = 0
    for candidate in report.get("repair_candidates", []) or []:
        if not isinstance(candidate, Mapping):
            continue
        movement_exemplar = candidate.get("movement_exemplar", {})
        signatures = movement_exemplar.get("movement_signatures", []) if isinstance(movement_exemplar, Mapping) else []
        if (
            candidate.get("candidate_status") != "ready_for_teacher_guided_variant"
            or not candidate.get("slot_edge_map")
            or not isinstance(signatures, list)
            or not signatures
        ):
            continue
        ready_count += 1
        signature_count += len(signatures)
    return ready_count, signature_count
def _intish(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
def _same_path_value(left: Any, right: Path | None) -> bool:
    if not left or right is None:
        return False
    try:
        return Path(str(left)).resolve() == right.resolve()
    except OSError:
        return str(left) == str(right)
def _class_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {item.strip() for item in value.replace(";", ",").split(",") if item.strip()}
    return {str(item) for item in value if str(item)}
def _tls_review_summary(
    tls_report: Mapping[str, Any],
    *,
    review_decisions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cluster_count = int(tls_report.get("tls_cluster_count", 0) or 0)
    candidate_count = int(tls_report.get("tls_candidate_count", 0) or 0)
    review_required = cluster_count > 0 or candidate_count > 0
    expected_location_ids: set[str] = set()
    clusters_file = Path(str(tls_report.get("clusters_file", "")))
    if clusters_file.exists():
        try:
            with clusters_file.open("r", encoding="utf-8", newline="") as handle:
                expected_location_ids = {
                    f"tls_reality_{str(row.get('cluster_id', '')).strip()}"
                    for row in csv.DictReader(handle)
                    if str(row.get("cluster_id", "")).strip()
                }
        except (OSError, csv.Error):
            expected_location_ids = set()
    supplied_by_id = {
        str(item.get("location_id", "")): item
        for item in (review_decisions or {}).get("locations", []) or []
        if isinstance(item, Mapping) and str(item.get("location_id", "")).strip()
    }
    missing_location_ids = sorted(expected_location_ids - set(supplied_by_id))
    pending_location_ids = sorted(
        location_id
        for location_id in expected_location_ids & set(supplied_by_id)
        if str(supplied_by_id[location_id].get("decision", "pending"))
        not in {"approved", "rejected_with_evidence"}
    )
    missing_evidence_location_ids = sorted(
        location_id
        for location_id in expected_location_ids & set(supplied_by_id)
        if str(supplied_by_id[location_id].get("decision", ""))
        in {"approved", "rejected_with_evidence"}
        and not str(supplied_by_id[location_id].get("evidence", "")).strip()
    )
    tls_reality_status = (
        "pass"
        if not review_required
        or (expected_location_ids and not missing_location_ids and not pending_location_ids and not missing_evidence_location_ids)
        else "blocked"
    )
    unresolved_count = len(missing_location_ids) + len(pending_location_ids)
    if review_required and not expected_location_ids:
        # If the cluster manifest is unavailable, retain the conservative
        # audit signal from the TLS detector instead of reporting zero open
        # reviews merely because IDs could not be read.
        unresolved_count = max(unresolved_count, cluster_count or candidate_count)
    return {
        "tls_candidate_count": candidate_count,
        "tls_cluster_count": cluster_count,
        "tls_review_file": str(tls_report.get("clusters_file", "")),
        "tls_review_complete": "yes" if tls_reality_status == "pass" else "no",
        "tls_reality_review_status": tls_reality_status,
        "tls_reality_review_location_count": len(expected_location_ids),
        "tls_reality_review_decision_count": len(expected_location_ids & set(supplied_by_id)),
        "tls_reality_review_missing_location_ids": missing_location_ids,
        "tls_reality_review_pending_location_ids": pending_location_ids,
        "tls_reality_review_missing_evidence_location_ids": missing_evidence_location_ids,
        "tls_google_maps_review_required": "yes" if tls_reality_status != "pass" and review_required else "no",
        "tls_google_maps_review_status": "needs_google_review" if tls_reality_status != "pass" and review_required else "not_required",
        "tls_keep_count": 0,
        "tls_remove_count": 0,
        "tls_downgrade_count": sum(
            1
            for location_id in expected_location_ids & set(supplied_by_id)
            if str(supplied_by_id[location_id].get("decision", "")) == "rejected_with_evidence"
        ),
        "tls_needs_review_count": unresolved_count,
    }
def _teacher_owner_ids(report: Mapping[str, Any] | None, *, max_owner_count: int | None = None) -> list[str]:
    if report is None:
        return []
    if max_owner_count is not None and max_owner_count <= 0:
        return []
    owner_ids: list[str] = []
    seen: set[str] = set()
    for candidate in report.get("repair_candidates", []) or []:
        if not isinstance(candidate, Mapping):
            continue
        owner_id = str(
            candidate.get("reference_id")
            or candidate.get("teacher_junction_id")
            or candidate.get("junction_id")
            or ""
        ).strip()
        if not owner_id or owner_id in seen:
            continue
        seen.add(owner_id)
        owner_ids.append(owner_id)
        if max_owner_count is not None and len(owner_ids) >= max_owner_count:
            break
    return owner_ids
def _road_connectivity_seed_geometry_owner_ids(
    seed_probe_report: Mapping[str, Any] | None,
    teacher_net_file: Path,
) -> list[str]:
    if seed_probe_report is None:
        return []
    parity = seed_probe_report.get("parity", {})
    if not isinstance(parity, Mapping):
        return []
    mismatches = parity.get("common_edge_geometry_mismatches", []) or []
    if not mismatches:
        return []
    teacher_edges = {
        edge.attrib.get("id", ""): edge
        for edge in ET.parse(teacher_net_file).getroot().findall("edge")
        if edge.attrib.get("id")
    }
    owner_ids: list[str] = []
    seen: set[str] = set()
    for mismatch in mismatches:
        if not isinstance(mismatch, Mapping):
            continue
        edge = teacher_edges.get(str(mismatch.get("edge_id", "")))
        if edge is None:
            continue
        for owner_id in (edge.attrib.get("from", ""), edge.attrib.get("to", "")):
            if owner_id and owner_id not in seen:
                owner_ids.append(owner_id)
                seen.add(owner_id)
    return owner_ids
def _road_connectivity_gate_status(report: Mapping[str, Any] | None) -> str:
    if report is None:
        return "skipped"
    audit = report.get("owner_road_connectivity_audit", {})
    audit = audit if isinstance(audit, Mapping) else {}
    return str(audit.get("status", report.get("status", "fail")))
def _filter_teacher_guided_queue_to_mismatch_fields(
    queue_report: Mapping[str, Any],
    delta_report: Mapping[str, Any],
    mismatch_fields: set[str],
    *,
    output_dir: Path,
    prefix: str,
) -> dict[str, Any]:
    target_id_set = {
            str(case.get("junction_id", ""))
            for case in delta_report.get("junction_pattern_comparisons", []) or []
            if isinstance(case, Mapping)
            and {
                str(field)
                for field in case.get("mismatch_fields", []) or []
            }
            & mismatch_fields
            and str(case.get("junction_id", ""))
        }
    for seed in delta_report.get("context_split_cluster_repair_seeds", []) or []:
        if not isinstance(seed, Mapping):
            continue
        reference_id = str(seed.get("reference_id", "")).strip()
        if reference_id:
            target_id_set.add(reference_id)
    target_ids = sorted(target_id_set)
    if not target_ids:
        return dict(queue_report)

    candidates = [
        candidate for candidate in queue_report.get("repair_candidates", []) or [] if isinstance(candidate, Mapping)
    ]
    filtered_candidates = [
        dict(candidate)
        for candidate in candidates
        if (
            {str(candidate.get("junction_id", "")), str(candidate.get("reference_id", ""))} & target_id_set
            or candidate.get("learned_rule")
            in {"tum_like_same_id_tls_candidate", "tum_like_topology_fragmented_tls_candidate"}
        )
    ]
    filtered_report = dict(queue_report)
    filtered_report["repair_candidates"] = filtered_candidates
    filtered_report["repair_candidate_count"] = len(filtered_candidates)
    filtered_report["ready_candidate_count"] = sum(
        1 for candidate in filtered_candidates if candidate.get("candidate_status") == "ready_for_teacher_guided_variant"
    )
    filtered_report["expanded_scope_candidate_count"] = sum(
        1 for candidate in filtered_candidates if candidate.get("candidate_status") == "needs_expanded_rebuild_scope"
    )
    filtered_report["blocked_candidate_count"] = (
        len(filtered_candidates)
        - int(filtered_report["ready_candidate_count"])
        - int(filtered_report["expanded_scope_candidate_count"])
    )
    filtered_report["queued_case_count"] = len(filtered_candidates)
    filtered_report["queue_truncated"] = len(filtered_candidates) < len(candidates)
    filtered_report["queue_filter_policy"] = "mismatch_fields_only"
    filtered_report["queue_filter_mismatch_fields"] = sorted(mismatch_fields)
    filtered_report["queue_filter_target_junction_ids"] = target_ids
    filtered_report["queue_filter_original_repair_candidate_count"] = len(candidates)
    filtered_report["queue_filter_source_queue_file"] = str(queue_report.get("queue_file", ""))
    output_dir.mkdir(parents=True, exist_ok=True)
    queue_file = output_dir / f"{prefix}_filtered_queue.json"
    filtered_report["queue_file"] = str(queue_file)
    filtered_report["queue_csv_file"] = ""
    queue_file.write_text(json.dumps(filtered_report, indent=2, ensure_ascii=False), encoding="utf-8")
    return filtered_report
def _reference_join_audit_can_seed_teacher_guided_queue(
    report: Mapping[str, Any],
    *,
    structural_only: bool,
) -> bool:
    if not structural_only:
        movement_fields = {"movement_signature_counts", "internal_function_counts"}
        field_counts = report.get("junction_pattern_mismatch_field_counts", {})
        if isinstance(field_counts, Mapping) and any(
            int(field_counts.get(field, 0) or 0) > 0 for field in movement_fields
        ):
            return True
        return any(
            isinstance(comparison, Mapping)
            and {str(field) for field in comparison.get("mismatch_fields", []) or []} & movement_fields
            for comparison in report.get("junction_pattern_comparisons", []) or []
        )
    comparisons = report.get("junction_pattern_comparisons", []) or []
    return any(isinstance(comparison, Mapping) and comparison.get("status") == "fail" for comparison in comparisons)
def _run_road_connectivity_seed_probe(
    *,
    teacher_net_file: Path,
    candidate_net_file: Path,
    seed_edge_ids: list[str],
    output_dir: Path,
    prefix: str,
    hop_radius: int = 1,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_seed_edge_ids = [str(edge_id).strip() for edge_id in seed_edge_ids if str(edge_id).strip()]
    teacher = canonical_road_connectivity_bundle(
        teacher_net_file,
        seed_edge_ids=normalized_seed_edge_ids,
        hop_radius=hop_radius,
    )
    candidate = canonical_road_connectivity_bundle(
        candidate_net_file,
        seed_edge_ids=normalized_seed_edge_ids,
        hop_radius=hop_radius,
    )
    parity = compare_road_connectivity_bundles(teacher, candidate)
    edge_ids = parity.get("edge_ids", {})
    connections = parity.get("connections", {})
    edge_delta_count = (
        len(edge_ids.get("missing_in_candidate", []) or [])
        + len(edge_ids.get("extra_in_candidate", []) or [])
        + len(parity.get("common_edge_geometry_mismatches", []) or [])
    )
    connection_delta_count = len(connections.get("missing_in_candidate", []) or []) + len(
        connections.get("extra_in_candidate", []) or []
    )
    report = {
        "status": str(parity.get("status", "fail")),
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "seed_edge_ids": normalized_seed_edge_ids,
        "hop_radius": hop_radius,
        "edge_delta_count": edge_delta_count,
        "connection_delta_count": connection_delta_count,
        "candidate_missing_seed_edge_ids": parity.get("candidate_missing_seed_edge_ids", []),
        "teacher_bundle_summary": teacher.get("summary", {}),
        "candidate_bundle_summary": candidate.get("summary", {}),
        "parity": parity,
    }
    report_file = output_dir / f"{prefix}.json"
    report["report_file"] = str(report_file)
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
def _road_connectivity_split_root_aliases(seed_probe_report: Mapping[str, Any] | None) -> list[dict[str, str]]:
    if seed_probe_report is None:
        return []
    parity = seed_probe_report.get("parity", {})
    if not isinstance(parity, Mapping):
        return []
    edge_ids = parity.get("edge_ids", {})
    if not isinstance(edge_ids, Mapping):
        return []
    return [
        dict(alias)
        for alias in edge_ids.get("split_root_aliases", []) or []
        if isinstance(alias, Mapping)
    ]
def _safe_path_part(value: str, max_len: int = 16) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value.strip())
    safe = safe or "junction"
    if len(safe) <= max_len:
        return safe
    digest = hashlib.sha1(safe.encode("utf-8")).hexdigest()[:8]
    if max_len <= len(digest) + 1:
        return digest[:max_len]
    head_len = max_len - len(digest) - 1
    return f"{safe[:head_len]}_{digest}"
def _queue_path_value(queue_report: Mapping[str, Any] | None, key: str) -> Path | None:
    if queue_report is None:
        return None
    value = str(queue_report.get(key, "")).strip()
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    queue_file = str(queue_report.get("queue_file", "")).strip()
    if queue_file:
        return Path(queue_file).resolve().parent / path
    return path
def _teacher_tls_has_multiple_internal_owners(
    *,
    teacher_net_file: Path,
    teacher_junction_id: str,
) -> bool:
    """Return whether a controller's controlled links span owner prefixes."""

    try:
        root = ET.parse(teacher_net_file).getroot()
    except (ET.ParseError, OSError, ValueError):
        return False
    junction_ids = sorted(
        {
            junction.attrib.get("id", "")
            for junction in root.findall("junction")
            if junction.attrib.get("id")
        },
        key=len,
        reverse=True,
    )
    owners: set[str] = set()
    for connection in root.findall("connection"):
        if (
            connection.attrib.get("tl") != teacher_junction_id
            or connection.attrib.get("linkIndex") is None
        ):
            continue
        via = connection.attrib.get("via", "")
        owner = next(
            (junction_id for junction_id in junction_ids if via.startswith(f":{junction_id}_")),
            "",
        )
        if owner:
            owners.add(owner)
    return len(owners) > 1
def _scoped_tls_batch_artifact_paths(
    *,
    report: Mapping[str, Any],
    source_net_file: Path,
    output_dir: Path,
) -> dict[str, Path]:
    """Collect deterministic batch artifacts without treating a variant as adopted."""

    paths: dict[str, Path] = {"source_net_file": source_net_file}
    path_fields = {
        "variant_file",
        "final_net_file",
        "net_file",
        "output_file",
        "connection_file",
        "normalized_file",
        "source_net_file",
        "target_net_file",
        "route_file",
        "summary_file",
        "tripinfo_file",
    }

    def resolve(value: object) -> Path | None:
        if not value or not isinstance(value, (str, Path)):
            return None
        candidate = Path(str(value))
        if candidate.is_absolute():
            return candidate
        for base in (output_dir, Path.cwd()):
            resolved = base / candidate
            if resolved.exists():
                return resolved
        return candidate

    def visit(value: object, prefix: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_text = str(key)
                if key_text in path_fields:
                    resolved = resolve(item)
                    if resolved is not None:
                        paths[f"{prefix}.{key_text}"] = resolved
                elif isinstance(item, (Mapping, list, tuple)):
                    visit(item, f"{prefix}.{key_text}")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                if isinstance(item, (Mapping, list, tuple)):
                    visit(item, f"{prefix}[{index}]")

    visit(report, "batch")
    return dict(sorted(paths.items()))
def _hash_scoped_tls_batch_artifacts(
    artifacts: Mapping[str, Path],
    *,
    base_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    unreadable: list[str] = []
    hashed_count = 0
    for key, path in artifacts.items():
        resolved = path.resolve()
        try:
            if not resolved.is_file():
                raise FileNotFoundError(str(resolved))
            digest = hashlib.sha256()
            size_bytes = 0
            with resolved.open("rb") as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size_bytes += len(chunk)
        except FileNotFoundError:
            records[key] = {
                "path": os.path.relpath(resolved, base_dir.resolve()).replace("\\", "/"),
                "status": "missing",
            }
            missing.append(key)
        except OSError as exc:
            records[key] = {
                "path": os.path.relpath(resolved, base_dir.resolve()).replace("\\", "/"),
                "status": "unreadable",
                "error": str(exc),
            }
            unreadable.append(key)
        else:
            records[key] = {
                "path": os.path.relpath(resolved, base_dir.resolve()).replace("\\", "/"),
                "status": "pass",
                "kind": "file",
                "sha256": digest.hexdigest(),
                "size_bytes": size_bytes,
            }
            hashed_count += 1
    gate = {
        "status": "pass" if not missing and not unreadable else "fail",
        "algorithm": "sha256",
        "hashed_artifact_count": hashed_count,
        "missing_artifacts": sorted(missing),
        "unreadable_artifacts": sorted(unreadable),
    }
    return records, gate
def _teacher_guided_direct_replay_needed(
    *,
    repair_promotion_report: Mapping[str, Any],
    repair_run_report: Mapping[str, Any] | None,
) -> bool:
    if repair_promotion_report.get("status") != "pass":
        return True
    if repair_run_report is None:
        return False
    return repair_run_report.get("status") != "pass" or repair_run_report.get("parity_gate_status") != "pass"
def _teacher_guided_equivalent_approach_edge_map(report: Mapping[str, Any] | None) -> dict[str, str]:
    if report is None:
        return {}
    edge_map: dict[str, str] = {}
    for variant in report.get("variant_reports", []) or []:
        if not isinstance(variant, Mapping):
            continue
        if variant.get("status") != "pass" or variant.get("parity_gate_status") != "pass":
            continue
        replay = variant.get("target_internal_replay", {})
        effective = replay.get("effective_edge_map", {}) if isinstance(replay, Mapping) else {}
        if isinstance(effective, Mapping):
            edge_map.update({str(key): str(value) for key, value in effective.items() if str(key) and str(value)})
    return edge_map
def _restore_false_traffic_light_plain_node_types(*, source_net_file: Path, node_file: Path) -> dict[str, Any]:
    if not node_file.exists():
        return {
            "status": "skipped",
            "reason": "node_file_missing",
            "restored_false_traffic_light_plain_node_count": 0,
            "restored_false_traffic_light_plain_node_ids": [],
        }
    try:
        source_root = ET.parse(source_net_file).getroot()
        node_tree = ET.parse(node_file)
    except (OSError, ET.ParseError) as exc:
        return {
            "status": "fail",
            "reason": f"{type(exc).__name__}: {exc}",
            "restored_false_traffic_light_plain_node_count": 0,
            "restored_false_traffic_light_plain_node_ids": [],
        }
    source_types = {
        junction.attrib["id"]: junction.attrib.get("type", "")
        for junction in source_root.findall("junction")
        if junction.attrib.get("id") and not junction.attrib["id"].startswith(":")
    }
    restored_ids = []
    for node in node_tree.getroot().findall("node"):
        node_id = node.attrib.get("id", "")
        source_type = source_types.get(node_id, "")
        if node.attrib.get("type") == "traffic_light" and source_type not in {"", "traffic_light"}:
            node.set("type", source_type)
            restored_ids.append(node_id)
    if restored_ids:
        ET.indent(node_tree.getroot(), space="    ")
        node_tree.write(node_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "restored_false_traffic_light_plain_node_count": len(restored_ids),
        "restored_false_traffic_light_plain_node_ids": restored_ids,
    }
def _prune_stale_plain_tllogics(*, node_file: Path, tllogic_file: Path) -> dict[str, Any]:
    if not tllogic_file.exists():
        return {
            "status": "skipped",
            "reason": "tllogic_file_missing",
            "removed_stale_plain_tllogic_count": 0,
            "removed_stale_plain_tllogic_ids": [],
            "removed_stale_plain_tllogic_connection_count": 0,
        }
    if not node_file.exists():
        return {
            "status": "skipped",
            "reason": "node_file_missing",
            "removed_stale_plain_tllogic_count": 0,
            "removed_stale_plain_tllogic_ids": [],
            "removed_stale_plain_tllogic_connection_count": 0,
        }
    try:
        node_root = ET.parse(node_file).getroot()
        tllogic_tree = ET.parse(tllogic_file)
    except (OSError, ET.ParseError) as exc:
        return {
            "status": "fail",
            "reason": f"{type(exc).__name__}: {exc}",
            "removed_stale_plain_tllogic_count": 0,
            "removed_stale_plain_tllogic_ids": [],
            "removed_stale_plain_tllogic_connection_count": 0,
        }

    valid_tls_ids: set[str] = set()
    for node in node_root.findall("node"):
        if not node.attrib.get("type", "").startswith("traffic_light"):
            continue
        for attr in ("id", "tl"):
            value = node.attrib.get(attr)
            if value:
                valid_tls_ids.add(value)

    tllogic_root = tllogic_tree.getroot()
    removed_ids: list[str] = []
    removed_id_set: set[str] = set()
    removed_tllogic_count = 0
    removed_connection_count = 0
    for child in list(tllogic_root):
        if child.tag == "tlLogic":
            tls_id = child.attrib.get("id", "")
            if tls_id and tls_id not in valid_tls_ids:
                tllogic_root.remove(child)
                removed_tllogic_count += 1
                if tls_id not in removed_id_set:
                    removed_ids.append(tls_id)
                    removed_id_set.add(tls_id)
        elif child.tag == "connection":
            tls_id = child.attrib.get("tl", "")
            if tls_id and tls_id not in valid_tls_ids:
                tllogic_root.remove(child)
                removed_connection_count += 1

    if removed_tllogic_count or removed_connection_count:
        ET.indent(tllogic_root, space="    ")
        tllogic_tree.write(tllogic_file, encoding="utf-8", xml_declaration=True)

    return {
        "status": "pass",
        "removed_stale_plain_tllogic_count": removed_tllogic_count,
        "removed_stale_plain_tllogic_ids": removed_ids,
        "removed_stale_plain_tllogic_connection_count": removed_connection_count,
    }
def _plain_output_prefix(output_dir: Path, prefix: str) -> tuple[Path, bool, str]:
    digest = hashlib.sha1(prefix.encode("utf-8")).hexdigest()[:8]
    plain_prefix = output_dir / prefix
    suffix_reserve = len(".nod.xml")
    path_limit = 239
    if len(str(plain_prefix.resolve())) + suffix_reserve < path_limit:
        return plain_prefix, False, digest

    output_dir_text = str(output_dir.resolve())
    max_name_len = path_limit - suffix_reserve - len(output_dir_text) - 1
    if max_name_len <= len(digest) + 2:
        return output_dir / f"p_{digest}", True, digest

    head_len = max_name_len - len(digest) - 1
    head = prefix[:head_len].strip("._-") or "plain"
    shortened = f"{head}_{digest}"
    if len(shortened) > max_name_len:
        shortened = f"p_{digest}"
    return output_dir / shortened, True, digest
def _synthesize_missing_plain_edge_types(raw_edge_file: Path, raw_type_file: Path) -> list[str]:
    if not raw_edge_file.exists():
        return []
    try:
        edge_root = ET.parse(raw_edge_file).getroot()
        if raw_type_file.exists():
            type_tree = ET.parse(raw_type_file)
            type_root = type_tree.getroot()
        else:
            type_root = ET.Element("types")
            type_tree = ET.ElementTree(type_root)
    except ET.ParseError:
        return []

    known_type_ids = {str(item.get("id")) for item in type_root.findall("type") if item.get("id")}
    synthesized = []
    for edge in edge_root.findall("edge"):
        type_id = str(edge.get("type") or "")
        if not type_id or type_id in known_type_ids:
            continue
        attrs = {"id": type_id}
        for attr in ("priority", "numLanes", "speed", "allow", "disallow", "oneway", "width"):
            value = edge.get(attr)
            if value:
                attrs[attr] = value
        if "numLanes" not in attrs:
            lane_count = len(edge.findall("lane"))
            if lane_count:
                attrs["numLanes"] = str(lane_count)
        if "speed" not in attrs:
            first_lane = edge.find("lane")
            if first_lane is not None and first_lane.get("speed"):
                attrs["speed"] = str(first_lane.get("speed"))
        ET.SubElement(type_root, "type", attrs)
        known_type_ids.add(type_id)
        synthesized.append(type_id)

    if synthesized:
        raw_type_file.parent.mkdir(parents=True, exist_ok=True)
        type_tree.write(raw_type_file, encoding="utf-8", xml_declaration=True)
    return synthesized
def _tls_aggregation_preserves_controlled_connections(report: Mapping[str, Any]) -> bool:
    return str(report.get("tls_controlled_connection_preservation_status", "pass")) != "fail"
def _controlled_tls_connection_count_from_net_file(value: Any) -> int | None:
    """Return the TLS controlled-connection count, or ``None`` when unreadable."""

    if not value:
        return None
    try:
        root = ET.parse(Path(str(value))).getroot()
    except (OSError, ET.ParseError, ValueError):
        return None
    connections = root.findall("connection")
    # A tiny synthetic ``<net/>`` is commonly used by injected test/build
    # adapters.  Treat it as unreadable for this metric so a trustworthy
    # count already supplied by the adapter remains the fallback.  A real
    # SUMO network contains connection elements even when its controlled
    # count is zero, so a parsed zero is still authoritative in that case.
    if not connections:
        return None
    return sum(
        1
        for connection in connections
        if connection.attrib.get("tl") and connection.attrib.get("linkIndex")
    )
def _reference_visual_tls_guess_signal_distances(
    *,
    reference_net_file: Path | None,
    network_profile: str,
) -> tuple[float | None, ...]:
    if reference_net_file is not None and network_profile == "reference_matched":
        return (35.0, 20.0, None)
    return (35.0,)
def _tls_guess_signal_distance_label(distance_m: float | None) -> str:
    if distance_m is None:
        return "default"
    if float(distance_m).is_integer():
        return f"guess{int(distance_m)}"
    return "guess" + str(distance_m).replace(".", "p")
def _command_result_report(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        report = result.to_dict()
    elif isinstance(result, Mapping):
        report = dict(result)
    else:
        report = {
            "status": getattr(result, "status", "fail"),
            "returncode": getattr(result, "returncode", None),
            "stdout": getattr(result, "stdout", ""),
            "stderr": getattr(result, "stderr", ""),
            "error": getattr(result, "error", ""),
        }
    if "status" not in report:
        report["status"] = "pass" if report.get("returncode") == 0 else "fail"
    return report
def _has_tls_incompatibility_warning(report: Mapping[str, Any] | None) -> bool:
    if report is None:
        return False
    text = "\n".join(str(report.get(field, "")) for field in ("stdout", "stderr", "error")).lower()
    return "tllogic" in text and "incompatible with logic" in text
def _command_path_for_cwd(path: Path, cwd: Path) -> str:
    try:
        return str(Path(os.path.relpath(path.resolve(), cwd.resolve())))
    except ValueError:
        return str(path)
def _followup_reference_delta_structural_only(
    baseline_delta_report: Mapping[str, Any] | None,
    *,
    default: bool,
) -> bool:
    return bool(default or (baseline_delta_report or {}).get("audit_mode") == "structural_only")
def _delta_failed_fields_by_junction(report: Mapping[str, Any] | None) -> dict[str, set[str]]:
    if report is None:
        return {}
    return {
        str(case.get("junction_id", "")): {str(field) for field in case.get("mismatch_fields", [])}
        for case in report.get("junction_pattern_comparisons", []) or []
        if str(case.get("junction_id", "")) and case.get("mismatch_fields")
    }
def _low_vehicle_control_candidate_limits(delta_report: Mapping[str, Any] | None) -> list[dict[str, int | str | None]]:
    if delta_report is None:
        return []
    queue = [
        item
        for item in delta_report.get("tls_control_review_queue", []) or []
        if isinstance(item, Mapping) and item.get("review_type") == "downgrade_low_vehicle_approach_tls"
    ]
    if not queue:
        return []
    extra_counts = delta_report.get("network_structural_extra_counts", {})
    if not isinstance(extra_counts, Mapping):
        extra_counts = {}
    limits: list[dict[str, int | str | None]] = []
    seen: set[tuple[int | None, int | None]] = set()

    def add_limit(label: str, *, max_removed: int | None = None, max_selected: int | None = None) -> None:
        if max_removed is not None and max_removed <= 0:
            return
        if max_selected is not None:
            if max_selected <= 0:
                return
            max_selected = min(max_selected, len(queue))
        key = (max_removed, max_selected)
        if key in seen:
            return
        seen.add(key)
        limits.append(
            {
                "label": label,
                "max_removed_controlled_connections": max_removed,
                "max_selected_tllogic_count": max_selected,
            }
        )

    extra_controlled = int(extra_counts.get("tls_controlled_connection_count", 0) or 0)
    add_limit(f"controlled{extra_controlled}", max_removed=extra_controlled)

    extra_tllogic = int(extra_counts.get("tl_logic_count", 0) or 0)
    extra_junctions = int(extra_counts.get("traffic_light_junction_count", 0) or 0)
    extra_tls = min(count for count in (extra_tllogic, extra_junctions) if count > 0) if any(
        count > 0 for count in (extra_tllogic, extra_junctions)
    ) else 0
    if extra_tls > 0:
        add_limit(f"tls{max(1, extra_tls // 4)}", max_selected=max(1, extra_tls // 4))
        add_limit(f"tls{max(1, extra_tls // 2)}", max_selected=max(1, extra_tls // 2))
        add_limit(f"tls{extra_tls}", max_selected=extra_tls)
    return limits[:3]
def _tls_control_review_category_counts(report: Mapping[str, Any] | None) -> dict[str, int]:
    if report is None:
        return {}
    counts: dict[str, int] = {}
    for entry in report.get("tls_control_review_queue", []) or []:
        if not isinstance(entry, Mapping):
            continue
        category = str(entry.get("repair_category", ""))
        if category:
            counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items()))
def _tls_representative_id_map(report: Mapping[str, Any] | None) -> dict[str, str]:
    if report is None:
        return {}
    representatives_file = Path(str(report.get("tls_aggregation_representatives_file", "")))
    if not representatives_file.exists():
        return {}
    tls_id_map: dict[str, str] = {}
    with representatives_file.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            representative = str(row.get("representative_node_id", ""))
            if not representative:
                continue
            for tls_id in str(row.get("tls_ids", "")).split(";"):
                if tls_id:
                    tls_id_map[tls_id] = representative
    return tls_id_map
def _delta_count_score(counts: Any) -> int:
    if not isinstance(counts, Mapping):
        return 0
    return sum(int(value or 0) for key, value in counts.items() if key in TLS_SEMANTIC_DELTA_KEYS)
def _junction_aggregation_summary(topology_audit_report: Mapping[str, Any] | None) -> dict[str, Any]:
    if topology_audit_report is None:
        return {
            "junction_aggregation_candidate_count": 0,
            "junction_aggregation_join_candidate_count": 0,
            "junction_aggregation_needs_map_review_count": 0,
            "junction_aggregation_do_not_join_count": 0,
            "junction_aggregation_blocked_by_corridor_count": 0,
            "junction_aggregation_candidates_file": "",
            "junction_aggregation_decision_counts": {},
        }
    clusters = list(topology_audit_report.get("suspicious_clusters", []))
    decision_counts = {
        "join": 0,
        "needs_map_review": 0,
        "do_not_join": 0,
    }
    for cluster in clusters:
        decision = str(cluster.get("aggregation_decision", "needs_map_review"))
        if decision not in decision_counts:
            decision = "needs_map_review"
        if cluster.get("corridor_decision") == "reject" and decision in {"join", "needs_map_review"}:
            decision_counts["blocked_by_corridor"] = decision_counts.get("blocked_by_corridor", 0) + 1
            continue
        decision_counts[decision] += 1
    return {
        "junction_aggregation_candidate_count": decision_counts["join"] + decision_counts["needs_map_review"],
        "junction_aggregation_join_candidate_count": decision_counts["join"],
        "junction_aggregation_needs_map_review_count": decision_counts["needs_map_review"],
        "junction_aggregation_do_not_join_count": decision_counts["do_not_join"],
        "junction_aggregation_blocked_by_corridor_count": decision_counts.get("blocked_by_corridor", 0),
        "junction_aggregation_candidates_file": str(topology_audit_report.get("clusters_file", "")),
        "junction_aggregation_decision_counts": decision_counts,
    }
def _reference_bbox_fields(reference_bbox_report: Mapping[str, Any] | None) -> dict[str, Any]:
    if reference_bbox_report is None:
        return {
            "reference_bbox_status": "not_used",
            "reference_bbox": "",
            "reference_bbox_source": "",
            "reference_bbox_padding_m": "",
            "reference_orig_boundary": "",
            "reference_conv_boundary": "",
            "reference_bbox_report": {},
        }
    return {
        "reference_bbox_status": str(reference_bbox_report.get("reference_bbox_status", "not_used")),
        "reference_bbox": str(reference_bbox_report.get("reference_bbox", "")),
        "reference_bbox_source": str(reference_bbox_report.get("reference_bbox_source", "")),
        "reference_bbox_padding_m": reference_bbox_report.get("reference_bbox_padding_m", ""),
        "reference_orig_boundary": str(reference_bbox_report.get("reference_orig_boundary", "")),
        "reference_conv_boundary": str(reference_bbox_report.get("reference_conv_boundary", "")),
        "reference_bbox_report": dict(reference_bbox_report),
    }
