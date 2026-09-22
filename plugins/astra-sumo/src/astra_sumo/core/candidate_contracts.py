from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping

from .map_review import (
    build_map_review_decision_binding,
    validate_map_review_decisions,
    validate_map_review_evidence,
)


MATERIALIZATION_SCHEMA = "torii.corridor_materialization.v1"
REVIEW_DECISION_SCHEMA = "torii.corridor_candidate_review.v1"

# These are the only protected-count increases that the current additive
# corridor materializer can intentionally create.  TLS programs are reviewed
# by exact signature rather than by count.
REVIEWABLE_SEMANTIC_DELTA_KEYS = frozenset(
    {
        "controlled_connection_count",
        "crossing_edge_count",
        "walkingarea_edge_count",
    }
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_artifact_identity(source_net_file: Path, candidate_net_file: Path) -> dict[str, Any]:
    source = source_net_file.resolve()
    candidate = candidate_net_file.resolve()
    errors: list[dict[str, Any]] = []
    if source == candidate:
        errors.append({"code": "source_candidate_path_collision"})

    source_hash = _hash_artifact(source, "source", errors)
    candidate_hash = _hash_artifact(candidate, "candidate", errors)
    if source_hash and candidate_hash and source_hash == candidate_hash:
        errors.append({"code": "candidate_identical_to_source"})

    return {
        "status": "pass" if not errors else "blocked",
        "source_net_file": str(source),
        "candidate_net_file": str(candidate),
        "source_sha256": source_hash,
        "candidate_sha256": candidate_hash,
        "errors": errors,
    }


def validate_materialization_evidence(
    report: Mapping[str, Any] | None,
    *,
    source_net_file: Path,
    candidate_net_file: Path,
) -> dict[str, Any]:
    identity = build_artifact_identity(source_net_file, candidate_net_file)
    errors: list[dict[str, Any]] = []
    if identity["status"] != "pass":
        errors.extend(dict(error) for error in identity["errors"])
    if not isinstance(report, Mapping):
        errors.append({"code": "materialization_report_required"})
        return {"status": "blocked", "errors": errors, "identity": identity}

    if str(report.get("schema", "")) != MATERIALIZATION_SCHEMA:
        errors.append(
            {
                "code": "materialization_schema_mismatch",
                "expected": MATERIALIZATION_SCHEMA,
                "actual": str(report.get("schema", "")),
            }
        )
    if str(report.get("status", "")) != "pass":
        errors.append({"code": "materialization_status_not_pass"})
    if str(report.get("materialization_status", "")) != "variant_created_for_review":
        errors.append({"code": "candidate_not_materialized_for_review"})
    if str(report.get("candidate_variant_status", "")) != "review_only":
        errors.append({"code": "candidate_variant_status_not_review_only"})

    command_result = report.get("command_result")
    if not isinstance(command_result, Mapping) or str(command_result.get("status", "")) != "pass":
        errors.append({"code": "netconvert_command_evidence_not_pass"})
    elif type(command_result.get("returncode")) is not int or command_result.get("returncode") != 0:
        errors.append({"code": "netconvert_returncode_not_zero"})

    command = report.get("command")
    if not isinstance(command, list) or not all(isinstance(token, str) for token in command):
        errors.append({"code": "netconvert_command_required"})
    else:
        if not _command_targets(command, "--sumo-net-file", source_net_file):
            errors.append({"code": "netconvert_command_source_mismatch"})
        if not _command_targets(command, "--output-file", candidate_net_file):
            errors.append({"code": "netconvert_command_candidate_mismatch"})

    expected_source = str(source_net_file.resolve())
    expected_candidate = str(candidate_net_file.resolve())
    if not _same_path(report.get("source_net_file"), source_net_file):
        errors.append(
            {
                "code": "materialization_source_path_mismatch",
                "expected": expected_source,
                "actual": str(report.get("source_net_file", "")),
            }
        )
    if not _same_path(report.get("candidate_net_file"), candidate_net_file):
        errors.append(
            {
                "code": "materialization_candidate_path_mismatch",
                "expected": expected_candidate,
                "actual": str(report.get("candidate_net_file", "")),
            }
        )

    for key in ("source_sha256", "candidate_sha256"):
        expected = str(identity.get(key, ""))
        actual = str(report.get(key, ""))
        if not actual:
            errors.append({"code": f"materialization_{key}_required"})
        elif actual != expected:
            errors.append(
                {
                    "code": f"materialization_{key}_mismatch",
                    "expected": expected,
                    "actual": actual,
                }
            )

    map_review_validation = _validate_materialization_map_review(
        report,
        source_net_file=source_net_file,
        candidate_net_file=candidate_net_file,
        errors=errors,
    )

    evidence_path = _evidence_path(
        report,
        hidden_key="_materialization_report_file",
        public_key="report_file",
    )
    persisted_report = _load_persisted_mapping(evidence_path, "materialization", errors)
    if persisted_report is not None:
        _compare_persisted_fields(
            report,
            persisted_report,
            (
                "schema",
                "status",
                "materialization_status",
                "candidate_variant_status",
                "source_net_file",
                "candidate_net_file",
                "source_sha256",
                "candidate_sha256",
                "map_review_evidence_file",
                "map_review_evidence_sha256",
                "map_review_evidence_status",
                "map_review_readiness_status",
                "map_review_required_location_ids",
                "accepted_review_additional_xml",
                "accepted_review_additional_xml_status",
                "candidate_review_html_file",
                "candidate_review_html_status",
                "command",
                "command_result",
            ),
            "materialization",
            errors,
        )

    return {
        "status": "pass" if not errors else "blocked",
        "errors": errors,
        "identity": identity,
        "materialization_report_file": str(evidence_path) if evidence_path is not None else "",
        "map_review_validation": map_review_validation,
        "map_review_evidence": map_review_validation.get("evidence", {}),
        "map_review_evidence_file": map_review_validation.get("evidence_file", ""),
        "map_review_evidence_sha256": map_review_validation.get("evidence_sha256", ""),
    }


def build_review_decision_template(
    *,
    source_net_file: Path,
    candidate_net_file: Path,
    semantic_allowances: Mapping[str, int] | None = None,
    tls_logic_allowances: Mapping[str, Mapping[str, Any]] | None = None,
    map_review_evidence: Mapping[str, Any] | None = None,
    map_review_evidence_file: Path | None = None,
    map_review_evidence_sha256: str = "",
) -> dict[str, Any]:
    identity = build_artifact_identity(source_net_file, candidate_net_file)
    normalized_semantic = _normalize_semantic_allowances(semantic_allowances)
    normalized_tls = {
        str(logic_id): dict(signature)
        for logic_id, signature in (tls_logic_allowances or {}).items()
        if isinstance(signature, Mapping)
    }
    map_review = build_map_review_decision_binding(
        map_review_evidence,
        evidence_file=map_review_evidence_file,
        evidence_sha256=map_review_evidence_sha256,
    )
    review_required = bool(
        normalized_semantic
        or normalized_tls
        or map_review.get("required_location_ids")
    )
    return {
        "schema": REVIEW_DECISION_SCHEMA,
        "status": "pending" if review_required else "not_required",
        "source_net_file": identity["source_net_file"],
        "candidate_net_file": identity["candidate_net_file"],
        "source_sha256": identity["source_sha256"],
        "candidate_sha256": identity["candidate_sha256"],
        "semantic_allowances": normalized_semantic,
        "tls_logic_allowances": normalized_tls,
        "map_review": map_review,
        "rationale": "",
        "evidence": [],
        "rollback": {"action": "discard_candidate_and_restore_source"},
        "review_required": review_required,
        "instructions": (
            "Set status to accepted only after reviewing the exact semantic deltas, TLS signatures, and "
            "every required structured map-review decision; add rationale and evidence without changing "
            "the artifact binding fields."
        ),
    }


def validate_review_decision(
    decision: Mapping[str, Any] | None,
    *,
    source_net_file: Path,
    candidate_net_file: Path,
    map_review_evidence: Mapping[str, Any] | None = None,
    map_review_evidence_file: Path | None = None,
    map_review_evidence_sha256: str = "",
) -> dict[str, Any]:
    required_map_location_ids = sorted(
        str(value) for value in (map_review_evidence or {}).get("required_location_ids", []) or []
    )
    if decision is None:
        map_review_validation = validate_map_review_decisions(
            None,
            evidence=map_review_evidence,
            evidence_file=map_review_evidence_file,
            evidence_sha256=map_review_evidence_sha256,
            candidate_net_file=candidate_net_file,
        )
        return {
            "status": map_review_validation["status"],
            "decision_status": "not_supplied",
            "review_required": bool(required_map_location_ids),
            "semantic_allowances": {},
            "tls_logic_allowances": {},
            "map_review": map_review_validation,
            "errors": list(map_review_validation.get("errors", [])),
        }

    errors: list[dict[str, Any]] = []
    identity = build_artifact_identity(source_net_file, candidate_net_file)
    if identity["status"] != "pass":
        errors.extend(dict(error) for error in identity["errors"])
    if str(decision.get("schema", "")) != REVIEW_DECISION_SCHEMA:
        errors.append(
            {
                "code": "review_schema_mismatch",
                "expected": REVIEW_DECISION_SCHEMA,
                "actual": str(decision.get("schema", "")),
            }
        )
    if str(decision.get("status", "")) != "accepted":
        errors.append({"code": "review_decision_not_accepted"})
    if not _same_path(decision.get("source_net_file"), source_net_file):
        errors.append({"code": "review_source_path_mismatch"})
    if not _same_path(decision.get("candidate_net_file"), candidate_net_file):
        errors.append({"code": "review_candidate_path_mismatch"})
    if str(decision.get("source_sha256", "")) != str(identity.get("source_sha256", "")):
        errors.append({"code": "review_source_hash_mismatch"})
    if str(decision.get("candidate_sha256", "")) != str(identity.get("candidate_sha256", "")):
        errors.append({"code": "review_candidate_hash_mismatch"})
    if not str(decision.get("rationale", "")).strip():
        errors.append({"code": "review_rationale_required"})
    evidence = decision.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append({"code": "review_evidence_required"})
    rollback = decision.get("rollback")
    if not isinstance(rollback, Mapping) or not str(rollback.get("action", "")).strip():
        errors.append({"code": "review_rollback_required"})

    raw_map_review = decision.get("map_review")
    map_review_validation = validate_map_review_decisions(
        raw_map_review if isinstance(raw_map_review, Mapping) else None,
        evidence=map_review_evidence,
        evidence_file=map_review_evidence_file,
        evidence_sha256=map_review_evidence_sha256,
        candidate_net_file=candidate_net_file,
    )
    errors.extend(dict(error) for error in map_review_validation.get("errors", []))

    raw_semantic = decision.get("semantic_allowances")
    if raw_semantic is not None and not isinstance(raw_semantic, Mapping):
        errors.append({"code": "review_semantic_allowances_must_be_object"})
        raw_semantic = {}
    semantic_allowances = _normalize_semantic_allowances(raw_semantic)
    for key in (raw_semantic or {}):
        if str(key) not in REVIEWABLE_SEMANTIC_DELTA_KEYS:
            errors.append({"code": "review_semantic_allowance_not_supported", "key": str(key)})
        try:
            value = int((raw_semantic or {})[key])
        except (TypeError, ValueError):
            errors.append({"code": "review_semantic_allowance_invalid", "key": str(key)})
            continue
        if value < 0:
            errors.append({"code": "review_semantic_allowance_negative", "key": str(key)})

    raw_tls = decision.get("tls_logic_allowances")
    if raw_tls is not None and not isinstance(raw_tls, Mapping):
        errors.append({"code": "review_tls_logic_allowances_must_be_object"})
        raw_tls = {}
    tls_logic_allowances: dict[str, dict[str, Any]] = {}
    for logic_id, signature in (raw_tls or {}).items():
        if not isinstance(signature, Mapping):
            errors.append({"code": "review_tls_logic_signature_invalid", "logic_id": str(logic_id)})
            continue
        tls_logic_allowances[str(logic_id)] = dict(signature)

    evidence_path = _evidence_path(
        decision,
        hidden_key="_review_decision_file",
        public_key="review_file",
    )
    persisted_decision = _load_persisted_mapping(evidence_path, "review", errors)
    if persisted_decision is not None:
        _compare_persisted_fields(
            decision,
            persisted_decision,
            (
                "schema",
                "status",
                "source_net_file",
                "candidate_net_file",
                "source_sha256",
                "candidate_sha256",
                "semantic_allowances",
                "tls_logic_allowances",
                "rationale",
                "evidence",
                "rollback",
                "map_review",
            ),
            "review",
            errors,
        )

    return {
        "status": "pass" if not errors else "blocked",
        "decision_status": str(decision.get("status", "")),
        "review_required": bool(
            semantic_allowances or tls_logic_allowances or required_map_location_ids
        ),
        "semantic_allowances": semantic_allowances if not errors else {},
        "tls_logic_allowances": tls_logic_allowances if not errors else {},
        "errors": errors,
        "map_review": map_review_validation,
        "identity": identity,
        "review_decision_file": str(evidence_path) if evidence_path is not None else "",
    }


def _validate_materialization_map_review(
    report: Mapping[str, Any],
    *,
    source_net_file: Path,
    candidate_net_file: Path,
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    required_ids = sorted(
        str(value) for value in report.get("map_review_required_location_ids", []) or []
    )
    evidence_value = str(report.get("map_review_evidence_file", "")).strip()
    evidence_sha256 = str(report.get("map_review_evidence_sha256", "")).strip()
    if not evidence_value:
        if required_ids:
            errors.append({"code": "required_map_review_evidence_file_missing"})
            return {
                "status": "blocked",
                "required_location_ids": required_ids,
                "evidence_file": "",
                "evidence_sha256": "",
                "errors": [{"code": "required_map_review_evidence_file_missing"}],
            }
        return {
            "status": "pass",
            "review_readiness_status": "not_emitted_legacy",
            "required_location_ids": [],
            "evidence_file": "",
            "evidence_sha256": "",
            "errors": [],
            "evidence": {},
        }

    evidence_file = Path(evidence_value).resolve()
    map_errors: list[dict[str, Any]] = []
    persisted = _load_persisted_mapping(evidence_file, "map_review", map_errors)
    if persisted is None:
        validation = {
            "status": "blocked",
            "required_location_ids": required_ids,
            "evidence_file": str(evidence_file),
            "evidence_sha256": evidence_sha256,
            "errors": map_errors,
            "evidence": {},
        }
    else:
        validation = validate_map_review_evidence(
            persisted,
            source_net_file=source_net_file,
            candidate_net_file=candidate_net_file,
            evidence_file=evidence_file,
            evidence_sha256=evidence_sha256,
        )
        map_errors.extend(dict(error) for error in validation.get("errors", []))
        if sorted(validation.get("required_location_ids", [])) != required_ids:
            map_errors.append({"code": "materialization_map_review_required_ids_mismatch"})
        if str(report.get("map_review_evidence_status", "")) != str(validation.get("status", "")):
            map_errors.append({"code": "materialization_map_review_status_mismatch"})
        if str(report.get("map_review_readiness_status", "")) != str(
            validation.get("review_readiness_status", "")
        ):
            map_errors.append({"code": "materialization_map_review_readiness_mismatch"})
        map_errors.extend(
            _validate_map_review_renderings(
                report,
                evidence=persisted,
                candidate_sha256=str(validation.get("evidence", {}).get("candidate_sha256", "")),
                map_review_evidence_sha256=evidence_sha256,
            )
        )
        validation = {
            **validation,
            "status": "pass" if not map_errors else "blocked",
            "errors": map_errors,
        }
    errors.extend(dict(error) for error in map_errors)
    return validation


def _validate_map_review_renderings(
    report: Mapping[str, Any],
    *,
    evidence: Mapping[str, Any],
    candidate_sha256: str,
    map_review_evidence_sha256: str,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    overlay_value = str(report.get("accepted_review_additional_xml", "")).strip()
    if str(report.get("accepted_review_additional_xml_status", "")) != "pass":
        errors.append({"code": "review_overlay_status_not_pass"})
    if not overlay_value:
        errors.append({"code": "review_overlay_file_required"})
    else:
        overlay_file = Path(overlay_value).resolve()
        if not overlay_file.is_file():
            errors.append({"code": "review_overlay_file_missing", "path": str(overlay_file)})
        else:
            try:
                overlay_root = ET.parse(overlay_file).getroot()
            except (OSError, ET.ParseError) as exc:
                errors.append(
                    {
                        "code": "review_overlay_xml_invalid",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            else:
                tags = {element.tag for element in overlay_root.iter()}
                unsupported_tags = sorted(tags - {"additional", "poi", "poly", "param"})
                if overlay_root.tag != "additional" or unsupported_tags:
                    errors.append(
                        {
                            "code": "review_overlay_contains_side_effect_elements",
                            "unsupported_tags": unsupported_tags,
                        }
                    )
                expected_proposals = {
                    str(item.get("proposal_id", ""))
                    for item in evidence.get("locations", []) or []
                    if isinstance(item, Mapping) and str(item.get("proposal_id", "")).strip()
                }
                observed_proposals: set[str] = set()
                for poi in overlay_root.findall("poi"):
                    params = {
                        str(item.attrib.get("key", "")): str(item.attrib.get("value", ""))
                        for item in poi.findall("param")
                    }
                    proposal_id = params.get("proposal_id", "")
                    if proposal_id:
                        observed_proposals.add(proposal_id)
                    if params.get("candidate_sha256", "") != candidate_sha256:
                        errors.append(
                            {
                                "code": "review_overlay_candidate_hash_mismatch",
                                "proposal_id": proposal_id,
                            }
                        )
                    if params.get("map_review_evidence_sha256", "") != map_review_evidence_sha256:
                        errors.append(
                            {
                                "code": "review_overlay_map_evidence_hash_mismatch",
                                "proposal_id": proposal_id,
                            }
                        )
                missing_proposals = sorted(expected_proposals - observed_proposals)
                if missing_proposals:
                    errors.append(
                        {
                            "code": "review_overlay_proposal_markers_missing",
                            "proposal_ids": missing_proposals,
                        }
                    )

    html_value = str(report.get("candidate_review_html_file", "")).strip()
    if str(report.get("candidate_review_html_status", "")) != "pass":
        errors.append({"code": "candidate_review_html_status_not_pass"})
    if not html_value:
        errors.append({"code": "candidate_review_html_file_required"})
    else:
        html_file = Path(html_value).resolve()
        if not html_file.is_file():
            errors.append({"code": "candidate_review_html_file_missing", "path": str(html_file)})
        else:
            try:
                html_text = html_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                errors.append(
                    {
                        "code": "candidate_review_html_invalid",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            else:
                if candidate_sha256 not in html_text:
                    errors.append({"code": "candidate_review_html_candidate_hash_missing"})
                if map_review_evidence_sha256 not in html_text:
                    errors.append({"code": "candidate_review_html_map_evidence_hash_missing"})
    return errors


def validate_routeability_evidence(
    report: Mapping[str, Any] | None,
    *,
    candidate_net_file: Path,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    if not isinstance(report, Mapping):
        return {"status": "blocked", "errors": [{"code": "routeability_report_required"}]}
    if str(report.get("schema", "")) != "torii.routeability_audit.v2":
        errors.append({"code": "routeability_schema_mismatch"})
    if report.get("status") != "pass" or report.get("routeability_status") != "pass":
        errors.append({"code": "routeability_result_not_pass"})
    if not _same_path(report.get("net_file"), candidate_net_file):
        errors.append({"code": "routeability_net_path_mismatch"})
    expected_hash = file_sha256(candidate_net_file) if candidate_net_file.is_file() else ""
    if str(report.get("net_sha256", "")) != expected_hash:
        errors.append({"code": "routeability_net_hash_mismatch"})

    report_path = _evidence_path(report, hidden_key="_routeability_report_file", public_key="report_file")
    persisted_report = _load_persisted_mapping(report_path, "routeability", errors)
    if persisted_report is not None:
        _compare_persisted_fields(
            report,
            persisted_report,
            (
                "schema",
                "status",
                "routeability_status",
                "net_file",
                "net_sha256",
                "vehicle_count",
                "route_generation",
                "attempts",
                "final_attempt",
            ),
            "routeability",
            errors,
        )

    manifest_path = _evidence_path(
        report,
        hidden_key="_routeability_manifest_file",
        public_key="manifest_file",
    )
    persisted_manifest = _load_persisted_mapping(manifest_path, "routeability_manifest", errors)
    if persisted_manifest is not None:
        if persisted_manifest.get("schema") != "torii.routeability_manifest.v2":
            errors.append({"code": "routeability_manifest_schema_mismatch"})
        if persisted_manifest.get("status") != "pass":
            errors.append({"code": "routeability_manifest_status_not_pass"})
        if not _same_path(persisted_manifest.get("net_file"), candidate_net_file):
            errors.append({"code": "routeability_manifest_net_path_mismatch"})
        if str(persisted_manifest.get("net_sha256", "")) != expected_hash:
            errors.append({"code": "routeability_manifest_net_hash_mismatch"})

    return {
        "status": "pass" if not errors else "blocked",
        "routeability_status": str(report.get("routeability_status", "blocked")),
        "report_file": str(report_path) if report_path is not None else "",
        "manifest_file": str(manifest_path) if manifest_path is not None else "",
        "net_sha256": expected_hash,
        "errors": errors,
    }


def validate_topology_evidence(
    report: Mapping[str, Any] | None,
    *,
    candidate_net_file: Path,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    if not isinstance(report, Mapping):
        return {"status": "blocked", "errors": [{"code": "topology_report_required"}]}
    if str(report.get("schema", "")) != "torii.topology_audit.v2":
        errors.append({"code": "topology_schema_mismatch"})
    if report.get("status") != "pass" or report.get("topology_fragmentation_status") != "pass":
        errors.append({"code": "topology_result_not_pass"})
    if not _same_path(report.get("net_file"), candidate_net_file):
        errors.append({"code": "topology_net_path_mismatch"})
    expected_hash = file_sha256(candidate_net_file) if candidate_net_file.is_file() else ""
    if str(report.get("net_sha256", "")) != expected_hash:
        errors.append({"code": "topology_net_hash_mismatch"})

    report_path = _evidence_path(report, hidden_key="_topology_report_file", public_key="report_file")
    persisted_report = _load_persisted_mapping(report_path, "topology", errors)
    if persisted_report is not None:
        _compare_persisted_fields(
            report,
            persisted_report,
            (
                "schema",
                "status",
                "topology_fragmentation_status",
                "net_file",
                "net_sha256",
                "cluster_radius_m",
                "min_cluster_nodes",
                "suspicious_cluster_count",
                "topology_canonical_cell_records",
            ),
            "topology",
            errors,
        )

    manifest_path = _evidence_path(
        report,
        hidden_key="_topology_manifest_file",
        public_key="manifest_file",
    )
    persisted_manifest = _load_persisted_mapping(manifest_path, "topology_manifest", errors)
    if persisted_manifest is not None:
        if persisted_manifest.get("schema") != "torii.topology_manifest.v2":
            errors.append({"code": "topology_manifest_schema_mismatch"})
        if persisted_manifest.get("status") != "pass":
            errors.append({"code": "topology_manifest_status_not_pass"})
        if not _same_path(persisted_manifest.get("net_file"), candidate_net_file):
            errors.append({"code": "topology_manifest_net_path_mismatch"})
        if str(persisted_manifest.get("net_sha256", "")) != expected_hash:
            errors.append({"code": "topology_manifest_net_hash_mismatch"})

    return {
        "status": "pass" if not errors else "blocked",
        "topology_fragmentation_status": str(report.get("topology_fragmentation_status", "blocked")),
        "report_file": str(report_path) if report_path is not None else "",
        "manifest_file": str(manifest_path) if manifest_path is not None else "",
        "net_sha256": expected_hash,
        "errors": errors,
    }


def _normalize_semantic_allowances(values: Mapping[str, Any] | None) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for key, value in (values or {}).items():
        key_text = str(key)
        if key_text not in REVIEWABLE_SEMANTIC_DELTA_KEYS:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            normalized[key_text] = parsed
    return normalized


def _same_path(value: Any, expected: Path) -> bool:
    if not value:
        return False
    try:
        return Path(str(value)).resolve() == expected.resolve()
    except OSError:
        return False


def _hash_artifact(path: Path, role: str, errors: list[dict[str, Any]]) -> str:
    if not path.is_file():
        errors.append({"code": f"{role}_artifact_missing", "path": str(path)})
        return ""
    try:
        return file_sha256(path)
    except OSError as exc:
        errors.append(
            {
                "code": f"{role}_artifact_unreadable",
                "path": str(path),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return ""


def _command_targets(command: list[str], option: str, expected: Path) -> bool:
    try:
        value = command[command.index(option) + 1]
    except (ValueError, IndexError):
        return False
    return _same_path(value, expected)


def _evidence_path(
    report: Mapping[str, Any],
    *,
    hidden_key: str,
    public_key: str,
) -> Path | None:
    value = report.get(hidden_key, report.get(public_key))
    if not value:
        return None
    return Path(str(value)).resolve()


def _load_persisted_mapping(
    path: Path | None,
    role: str,
    errors: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if path is None:
        errors.append({"code": f"{role}_evidence_file_required"})
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(
            {
                "code": f"{role}_evidence_file_invalid",
                "path": str(path),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return None
    if not isinstance(loaded, dict):
        errors.append({"code": f"{role}_evidence_file_not_object", "path": str(path)})
        return None
    return loaded


def _compare_persisted_fields(
    supplied: Mapping[str, Any],
    persisted: Mapping[str, Any],
    fields: tuple[str, ...],
    role: str,
    errors: list[dict[str, Any]],
) -> None:
    mismatches = [field for field in fields if supplied.get(field) != persisted.get(field)]
    if mismatches:
        errors.append({"code": f"{role}_evidence_content_mismatch", "fields": mismatches})
