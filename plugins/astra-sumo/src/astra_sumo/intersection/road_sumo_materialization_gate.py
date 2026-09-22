"""Fail-closed hand-off from road-arm intent evidence to lane materialization.

``road_sumo_binding`` intentionally stops at semantic road-arm to SUMO-edge
lineage.  A materializer must not mistake that lineage for a permission to
write a ``<connection>``.  This module is the narrow, reusable hand-off:
every planned lane-level connection has to be bound to exactly one ready
road-arm intent, the exact source SUMO snapshot, and the four evidence classes
that the intent declares as still necessary.

The result is still *not* a promotion decision.  It only lets a bounded
candidate materializer proceed to its existing XML/SUMO/geometry gates.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


ROAD_SUMO_BINDING_SCHEMA = "torii.intersection-road-sumo-binding/v1"
ROAD_SUMO_MATERIALIZATION_GATE_SCHEMA = "torii.road-sumo-materialization-gate/v1"

_REQUIRED_EVIDENCE_CLASSES = (
    "junction_facing_lane_and_stop_line_binding",
    "physical_connector_geometry_or_map_evidence",
    "legal_turn_or_signal_control_evidence",
    "SUMO_owner_and_link_index_decision",
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def gate_road_sumo_connection_intents_for_materialization(
    road_sumo_binding: Mapping[str, Any],
    *,
    source_sumo_sha256: str,
    planned_lane_connections: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate planned lane connections against frozen road-arm intent evidence.

    ``planned_lane_connections`` is deliberately lane-level, but this function
    does not infer those lanes.  The caller must supply all four evidence
    classes for each row.  A row is eligible only when exactly one
    ``ready_for_lane_connection_review`` intent covers its directed
    ``from_edge -> to_edge`` pair.  Zero or multiple matches block rather than
    silently choosing a semantic arm.
    """

    binding = _validated_binding(road_sumo_binding)
    normalized_source_sha = _sha256(source_sumo_sha256, "source_sumo_sha256")
    binding_source_sha = _sha256(
        str(binding["lineage_source_sha256_binding"]["sumo_source_sha256"]),
        "road_sumo_binding lineage SUMO source SHA-256",
    )

    source_hash_status = "pass" if binding_source_sha == normalized_source_sha else "blocked"
    ready_intents = [
        intent
        for intent in _mapping_sequence(binding.get("connection_intents"), "connection_intents")
        if str(intent.get("status", "")) == "ready_for_lane_connection_review"
    ]
    rows = [
        _evaluate_planned_connection(item, ready_intents, source_hash_status=source_hash_status)
        for item in planned_lane_connections
    ]
    rows.sort(key=lambda item: str(item["planned_connection_id"]))
    blocked_rows = [
        str(item["planned_connection_id"])
        for item in rows
        if item["status"] != "pass"
    ]
    blocked_reasons = sorted(
        {
            reason
            for item in rows
            for reason in item["blocking_reasons"]
        }
        | (
            {"binding_source_sumo_sha256_mismatch"}
            if source_hash_status != "pass"
            else set()
        )
    )
    status = "pass" if rows and not blocked_rows and source_hash_status == "pass" else "blocked"
    payload: dict[str, Any] = {
        "schema": ROAD_SUMO_MATERIALIZATION_GATE_SCHEMA,
        "status": status,
        "automatic_promotion_gate": "blocked",
        "classification_only": False,
        "road_sumo_binding_id": str(binding["road_sumo_binding_id"]),
        "road_sumo_binding_sha256": _stable_digest(binding),
        "source_sumo_sha256": normalized_source_sha,
        "binding_source_sumo_sha256": binding_source_sha,
        "source_snapshot_binding_status": source_hash_status,
        "planned_lane_connections": rows,
        "counts": {
            "planned_lane_connection_count": len(rows),
            "passed_lane_connection_count": sum(item["status"] == "pass" for item in rows),
            "blocked_lane_connection_count": len(blocked_rows),
            "ready_connection_intent_count": len(ready_intents),
        },
        "blocked_planned_connection_ids": blocked_rows,
        "blocking_reasons": blocked_reasons,
        "claim_boundary": (
            "A pass proves that every caller-supplied lane-level plan is covered by one "
            "ready road-arm intent and declares the required evidence classes for the exact "
            "source SUMO snapshot. It does not prove generated junction geometry, request/foe "
            "matrices, traffic-light timing, or authorize automatic promotion."
        ),
    }
    payload["road_sumo_materialization_gate_id"] = (
        f"road-sumo-materialization-gate-{_stable_digest(payload)[:20]}"
    )
    return payload


def _validated_binding(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if str(value.get("schema", "")) != ROAD_SUMO_BINDING_SCHEMA:
        raise ValueError("road_sumo_binding schema must be torii.intersection-road-sumo-binding/v1")
    if value.get("automatic_promotion_gate") != "blocked":
        raise ValueError("road_sumo_binding must retain automatic_promotion_gate=blocked")
    binding_id = str(value.get("road_sumo_binding_id", "")).strip()
    if not binding_id:
        raise ValueError("road_sumo_binding requires a non-empty road_sumo_binding_id")
    source_binding = value.get("lineage_source_sha256_binding")
    if not isinstance(source_binding, Mapping):
        raise ValueError("road_sumo_binding requires lineage_source_sha256_binding")
    if source_binding.get("status") != "pass":
        raise ValueError("road_sumo_binding lineage source binding must pass")
    _sha256(str(source_binding.get("sumo_source_sha256", "")), "road_sumo_binding SUMO source SHA-256")
    _mapping_sequence(value.get("connection_intents"), "road_sumo_binding connection_intents")
    return value


def _evaluate_planned_connection(
    value: Mapping[str, Any],
    ready_intents: Sequence[Mapping[str, Any]],
    *,
    source_hash_status: str,
) -> dict[str, Any]:
    planned_id = str(value.get("planned_connection_id", "")).strip()
    if not planned_id:
        raise ValueError("each planned lane connection requires planned_connection_id")
    from_edge = str(value.get("from_edge", "")).strip()
    to_edge = str(value.get("to_edge", "")).strip()
    if not from_edge or not to_edge:
        raise ValueError(f"planned lane connection {planned_id!r} requires from_edge and to_edge")
    from_lane = _nonnegative_int(value.get("from_lane"), f"{planned_id}.from_lane")
    to_lane = _nonnegative_int(value.get("to_lane"), f"{planned_id}.to_lane")
    evidence = value.get("evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError(f"planned lane connection {planned_id!r} requires an evidence object")
    missing_evidence = [
        evidence_class
        for evidence_class in _REQUIRED_EVIDENCE_CLASSES
        if not _nonempty_evidence(evidence.get(evidence_class))
    ]
    matching_intents = [
        intent
        for intent in ready_intents
        if from_edge in _string_sequence(intent.get("from_trusted_sumo_edge_ids"))
        and to_edge in _string_sequence(intent.get("to_trusted_sumo_edge_ids"))
    ]
    matching_intent_ids = sorted(
        str(intent.get("connection_intent_id", ""))
        for intent in matching_intents
        if str(intent.get("connection_intent_id", ""))
    )
    blocking_reasons: list[str] = []
    if source_hash_status != "pass":
        blocking_reasons.append("binding_source_sumo_sha256_mismatch")
    if not matching_intent_ids:
        blocking_reasons.append("no_ready_connection_intent_covers_directed_edge_pair")
    elif len(matching_intent_ids) > 1:
        blocking_reasons.append("multiple_ready_connection_intents_cover_directed_edge_pair")
    if missing_evidence:
        blocking_reasons.extend(f"missing_evidence:{item}" for item in missing_evidence)
    return {
        "planned_connection_id": planned_id,
        "from_edge": from_edge,
        "from_lane": from_lane,
        "to_edge": to_edge,
        "to_lane": to_lane,
        "matching_connection_intent_ids": matching_intent_ids,
        "evidence": {
            evidence_class: _normalized_evidence(evidence.get(evidence_class))
            for evidence_class in _REQUIRED_EVIDENCE_CLASSES
            if _nonempty_evidence(evidence.get(evidence_class))
        },
        "missing_evidence_classes": missing_evidence,
        "status": "pass" if not blocking_reasons else "blocked",
        "blocking_reasons": blocking_reasons,
    }


def _mapping_sequence(value: Any, field_name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field_name} must be a sequence of mappings")
    if any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"{field_name} must be a sequence of mappings")
    return list(value)


def _string_sequence(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(sorted({str(item) for item in value if str(item)}))


def _nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a non-negative integer") from exc
    if parsed < 0 or str(parsed) != str(value).strip():
        raise ValueError(f"{field_name} must be a non-negative integer")
    return parsed


def _nonempty_evidence(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return any(bool(str(item).strip()) for item in value)
    return False


def _normalized_evidence(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return sorted({str(item).strip() for item in value if str(item).strip()})
    return []


def _sha256(value: str, field_name: str) -> str:
    normalized = str(value).strip().casefold()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256")
    return normalized


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
