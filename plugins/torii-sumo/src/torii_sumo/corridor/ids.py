from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel


STABLE_ID_SCHEMA = "torii.corridor.stable-id/v1"
_STABLE_ID_RE = re.compile(r"^(?P<kind>[a-z][a-z0-9_]*)_(?P<digest>[0-9a-f]{24})$")
_KNOWN_KINDS = frozenset(
    {
        "approach",
        "artifact",
        "binding",
        "calibration",
        "candidate",
        "cell",
        "controller",
        "coverage",
        "conflict",
        "delta",
        "evidence",
        "finding",
        "hypothesis",
        "invariant",
        "lane_role",
        "manifest",
        "movement",
        "operation",
        "path",
        "policy",
        "port",
        "program",
        "request",
        "review",
        "signature",
        "scope",
        "signal_group",
        "toolchain",
        "transition",
    }
)


def canonicalize(value: Any) -> Any:
    """Return a deterministic JSON-compatible semantic representation."""

    if isinstance(value, BaseModel):
        return canonicalize(value.model_dump(mode="json", exclude_none=False))
    if is_dataclass(value) and not isinstance(value, type):
        return canonicalize(asdict(value))
    if isinstance(value, Enum):
        return canonicalize(value.value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Canonical mapping keys must be strings.")
            normalized[key] = canonicalize(item)
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, (set, frozenset)):
        items = [canonicalize(item) for item in value]
        return sorted(items, key=_canonical_json)
    if isinstance(value, (tuple, list)):
        return [canonicalize(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Stable semantic payloads cannot contain NaN or infinity.")
        return 0.0 if value == 0.0 else value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"Unsupported stable semantic payload type: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        canonicalize(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_json_bytes(value: Any) -> bytes:
    return _canonical_json(value).encode("utf-8")


def stable_digest(namespace: str, payload: Any) -> str:
    if not namespace.strip():
        raise ValueError("Stable digest namespace cannot be empty.")
    digest = hashlib.sha256()
    digest.update(STABLE_ID_SCHEMA.encode("utf-8"))
    digest.update(b"\0")
    digest.update(namespace.encode("utf-8"))
    digest.update(b"\0")
    digest.update(canonical_json_bytes(payload))
    return digest.hexdigest()


def stable_id(kind: str, semantic_key: Any) -> str:
    normalized_kind = kind.strip().lower().replace("-", "_")
    if normalized_kind not in _KNOWN_KINDS:
        raise ValueError(f"Unknown stable ID kind: {kind}")
    return f"{normalized_kind}_{stable_digest(normalized_kind, semantic_key)[:24]}"


def is_stable_id(value: str, *, kind: str | None = None) -> bool:
    match = _STABLE_ID_RE.fullmatch(str(value))
    if match is None or match.group("kind") not in _KNOWN_KINDS:
        return False
    return kind is None or match.group("kind") == kind


def require_stable_id(value: str, *, kind: str | None = None) -> str:
    if not is_stable_id(value, kind=kind):
        expected = f" of kind {kind!r}" if kind else ""
        raise ValueError(f"Expected a Torii stable ID{expected}: {value!r}")
    return value


def make_boundary_port_id(
    *,
    source_anchor_refs: Sequence[str],
    source_geometry_sha256: str,
    lane_semantic_keys: Sequence[Mapping[str, Any]],
    traffic_side: str,
) -> str:
    return stable_id(
        "port",
        {
            "source_anchor_refs": sorted({str(value) for value in source_anchor_refs}),
            "source_geometry_sha256": source_geometry_sha256,
            "lane_semantic_keys": list(lane_semantic_keys),
            "traffic_side": traffic_side,
        },
    )


def make_physical_cell_id(
    *,
    boundary_port_ids: Sequence[str],
    grade_separation_signature: Mapping[str, Any],
) -> str:
    for port_id in boundary_port_ids:
        require_stable_id(port_id, kind="port")
    return stable_id(
        "cell",
        {
            "boundary_port_ids": sorted(set(boundary_port_ids)),
            "grade_separation": grade_separation_signature,
        },
    )


def make_approach_id(*, physical_cell_id: str, boundary_port_id: str, flow: str) -> str:
    require_stable_id(physical_cell_id, kind="cell")
    require_stable_id(boundary_port_id, kind="port")
    return stable_id(
        "approach",
        {
            "physical_cell_id": physical_cell_id,
            "boundary_port_id": boundary_port_id,
            "flow": flow,
        },
    )


def make_lane_role_id(
    *,
    approach_id: str,
    ordinal_from_curb: int,
    role: str,
    modes: Sequence[str],
    traffic_side: str,
) -> str:
    require_stable_id(approach_id, kind="approach")
    if ordinal_from_curb < 0:
        raise ValueError("Lane ordinal from curb must be non-negative.")
    return stable_id(
        "lane_role",
        {
            "approach_id": approach_id,
            "ordinal_from_curb": ordinal_from_curb,
            "role": role,
            "modes": sorted(set(modes)),
            "traffic_side": traffic_side,
        },
    )


def make_movement_id(
    *,
    physical_cell_id: str,
    source_boundary_port_id: str,
    source_lane_role_id: str,
    destination_boundary_port_id: str,
    destination_lane_role_id: str,
    mode: str,
    turn_class: str,
) -> str:
    require_stable_id(physical_cell_id, kind="cell")
    require_stable_id(source_boundary_port_id, kind="port")
    require_stable_id(source_lane_role_id, kind="lane_role")
    require_stable_id(destination_boundary_port_id, kind="port")
    require_stable_id(destination_lane_role_id, kind="lane_role")
    return stable_id(
        "movement",
        {
            "physical_cell_id": physical_cell_id,
            "source_boundary_port_id": source_boundary_port_id,
            "source_lane_role_id": source_lane_role_id,
            "destination_boundary_port_id": destination_boundary_port_id,
            "destination_lane_role_id": destination_lane_role_id,
            "mode": mode,
            "turn_class": turn_class,
        },
    )


def make_internal_path_signature(
    *,
    movement_id: str,
    ordered_segment_semantics: Sequence[Mapping[str, Any]],
) -> str:
    require_stable_id(movement_id, kind="movement")
    return stable_id(
        "path",
        {
            "movement_id": movement_id,
            "ordered_segment_semantics": list(ordered_segment_semantics),
        },
    )


def make_signal_group_id(*, controller_scope_id: str, movement_ids: Sequence[str]) -> str:
    for movement_id in movement_ids:
        require_stable_id(movement_id, kind="movement")
    return stable_id(
        "signal_group",
        {
            "controller_scope_id": controller_scope_id,
            "movement_ids": sorted(set(movement_ids)),
        },
    )


def make_controller_program_signature(
    *,
    signal_group_ids: Sequence[str],
    ordered_phases: Sequence[Mapping[str, Any]],
) -> str:
    for signal_group_id in signal_group_ids:
        require_stable_id(signal_group_id, kind="signal_group")
    return stable_id(
        "program",
        {
            "signal_group_ids": sorted(set(signal_group_ids)),
            "ordered_phases": list(ordered_phases),
        },
    )
