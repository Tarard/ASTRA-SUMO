"""Hash-bound scope contract for the named Hamburg Sandtorkai corridor.

The repository contains an older diagnostic preset using nodes 0228/2421/2394.
That preset is useful for regression comparisons, but it is not the corridor
requested by the current project.  This module makes the requested scope an
explicit, reusable input contract so later geometry, signal, and demand stages
cannot silently consume the legacy data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256


NAMED_SCOPE_SCHEMA = "torii.hamburg-named-corridor-scope/v1"
OFFICIAL_NODE_IDENTITY_SCHEMA = "torii.hamburg-lsa-node-identity-evidence/v1"
OFFICIAL_CORRIDOR_SCOPE_SCHEMA = "torii.hamburg-hh-sib-corridor-scope/v1"
SIGNAL_ASSET_DISCOVERY_SCHEMA = "torii.hamburg-signal-asset-discovery/v1"
NAMED_SCOPE_ID = "hamburg_sandtorkai_2349_2394_2403_named_entries_v1"

NAMED_NODE_SPECS: tuple[dict[str, str], ...] = (
    {
        "node_id": "2349",
        "official_name": "Am Sandtorkai/Großer Grasbrook",
        "role": "west",
    },
    {
        "node_id": "2394",
        "official_name": "Am Sandtorkai/Am Sandtorpark",
        "role": "middle",
    },
    {
        "node_id": "2403",
        "official_name": "Am Sandtorkai/Osakaallee",
        "role": "east",
    },
)
NAMED_NODE_IDS = tuple(item["node_id"] for item in NAMED_NODE_SPECS)
LEGACY_NODE_IDS = ("0228", "2421", "2394")


class HamburgNamedScopeError(ValueError):
    """Raised when a named-corridor scope cannot be frozen safely."""


def freeze_hamburg_named_scope(
    *,
    lsa_identity_file: Path,
    corridor_scope_file: Path,
    signal_asset_discovery_file: Path,
    output_file: Path,
) -> dict[str, Any]:
    """Build and persist the W0 named-corridor contract.

    The function intentionally does not materialize a SUMO network.  It only
    freezes authoritative node identities, the official HH-SIB link selectors,
    and the current signal-asset availability.  A missing 2403 signal triplet
    therefore yields a hash-bound partial manifest with a blocked downstream
    promotion gate rather than an invented controller.
    """

    lsa_path, lsa = _read_json_artifact(lsa_identity_file, "LSA identity")
    scope_path, corridor_scope = _read_json_artifact(corridor_scope_file, "corridor scope")
    signal_path, signal_discovery = _read_json_artifact(
        signal_asset_discovery_file,
        "signal asset discovery",
    )

    nodes = _validate_lsa_identity(lsa)
    _validate_corridor_scope(corridor_scope)
    signal_summary = _validate_signal_discovery(signal_discovery)

    missing_signal_assets = tuple(
        node_id for node_id in NAMED_NODE_IDS if node_id not in signal_summary["resolved_node_ids"]
    )
    signal_decision = "pass" if not missing_signal_assets else "blocked"
    manifest: dict[str, Any] = {
        "schema": NAMED_SCOPE_SCHEMA,
        "scope_id": NAMED_SCOPE_ID,
        "status": "ready" if signal_decision == "pass" else "partial",
        "decision": "pass" if signal_decision == "pass" else "blocked",
        "automatic_action": (
            "allow_downstream_stages"
            if signal_decision == "pass"
            else "allow_scope_only_and_abstain_from_incomplete_signal_materialization"
        ),
        "scope_policy": {
            "ordered_node_ids": list(NAMED_NODE_IDS),
            "roles": {item["node_id"]: item["role"] for item in NAMED_NODE_SPECS},
            "official_main_axis": "Am Sandtorkai",
            "retained_side_arms": [
                "Großer Grasbrook",
                "Am Sandtorpark",
                "Osakaallee/Singapurstraße",
            ],
            "legacy_presets_rejected": [
                {
                    "scope_id": "hamburg_sandtorkai_0228_2421_2394_legacy",
                    "node_ids": list(LEGACY_NODE_IDS),
                    "reason": "diagnostic_baseline_not_named_corridor",
                }
            ],
            "osm_role": "topology_hint_only",
        },
        "nodes": nodes,
        "official_road_scope": {
            "path": str(corridor_scope_file.resolve()),
            "sha256": file_sha256(corridor_scope_file.resolve()),
            "scope_id": str(corridor_scope["scope_id"]),
            "link_count": len(corridor_scope["links"]),
        },
        "signal_assets": {
            "path": str(signal_path),
            "sha256": file_sha256(signal_path),
            "requested_node_ids": list(signal_summary["requested_node_ids"]),
            "resolved_node_ids": list(signal_summary["resolved_node_ids"]),
            "unresolved_node_ids": list(signal_summary["unresolved_node_ids"]),
            "decision": signal_decision,
            "missing_triplet_action": (
                "autonomous_abstention_no_materialization"
                if missing_signal_assets
                else None
            ),
        },
        "sources": {
            "lsa_identity": _source_identity(lsa_path, lsa.get("schema")),
            "official_road_scope": _source_identity(scope_path, corridor_scope.get("schema")),
            "signal_asset_discovery": _source_identity(signal_path, signal_discovery.get("schema")),
        },
        "claim_boundary": {
            "proves": [
                "the requested three-node named corridor scope",
                "official LSA identity and point geometry for each named node",
                "the selected official HH-SIB link selectors",
                "which official signal asset triplets are currently available",
            ],
            "does_not_prove": [
                "SUMO lane geometry or junction topology",
                "MAP-to-lane or OCIT-to-movement binding",
                "historical signal timing",
                "vehicle demand reconstruction",
            ],
        },
    }
    write_json_atomic(output_file, manifest, ensure_ascii=False, sort_keys=True)
    return manifest


def validate_hamburg_named_scope_manifest(
    manifest_file: Path,
    *,
    require_signal_assets: bool = False,
) -> dict[str, Any]:
    """Validate a frozen W0 manifest before a downstream workflow stage."""

    path = manifest_file.resolve(strict=True)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HamburgNamedScopeError(f"cannot read named scope manifest: {path}") from exc
    if not isinstance(payload, Mapping):
        raise HamburgNamedScopeError("named scope manifest must be a JSON object")
    if payload.get("schema") != NAMED_SCOPE_SCHEMA:
        raise HamburgNamedScopeError(f"manifest schema must be {NAMED_SCOPE_SCHEMA}")
    if payload.get("scope_id") != NAMED_SCOPE_ID:
        raise HamburgNamedScopeError("manifest does not describe the requested named corridor")
    nodes = payload.get("nodes")
    if not isinstance(nodes, Sequence) or isinstance(nodes, (str, bytes)):
        raise HamburgNamedScopeError("named scope manifest.nodes must be a list")
    node_ids = tuple(str(item.get("node_id")) for item in nodes if isinstance(item, Mapping))
    if node_ids != NAMED_NODE_IDS:
        raise HamburgNamedScopeError(
            f"named scope manifest node order must be {NAMED_NODE_IDS!r}, got {node_ids!r}"
        )
    signal_assets = payload.get("signal_assets")
    if not isinstance(signal_assets, Mapping):
        raise HamburgNamedScopeError("named scope manifest.signal_assets is required")
    if require_signal_assets and signal_assets.get("decision") != "pass":
        raise HamburgNamedScopeError("named scope is not signal-complete for this stage")
    road_scope = payload.get("official_road_scope")
    if not isinstance(road_scope, Mapping) or road_scope.get("scope_id") != NAMED_SCOPE_ID:
        raise HamburgNamedScopeError("named scope manifest official road scope is not the requested scope")
    for source in (payload.get("sources") or {},):
        if not isinstance(source, Mapping):
            raise HamburgNamedScopeError("named scope manifest.sources must be an object")
        for label, identity in source.items():
            if not isinstance(identity, Mapping):
                raise HamburgNamedScopeError(f"named scope source identity is invalid: {label}")
            source_path = Path(str(identity.get("path", ""))).resolve()
            expected_sha256 = str(identity.get("sha256", "")).lower()
            if source_path.is_file() and file_sha256(source_path) != expected_sha256:
                raise HamburgNamedScopeError(f"named scope source hash changed: {label}")
    return dict(payload)


def _read_json_artifact(path: Path, label: str) -> tuple[Path, dict[str, Any]]:
    resolved = path.resolve(strict=True)
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HamburgNamedScopeError(f"cannot read {label} artifact: {resolved}") from exc
    if not isinstance(value, dict):
        raise HamburgNamedScopeError(f"{label} artifact must be a JSON object: {resolved}")
    return resolved, value


def _validate_lsa_identity(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    if payload.get("schema") != OFFICIAL_NODE_IDENTITY_SCHEMA:
        raise HamburgNamedScopeError(
            f"LSA identity schema must be {OFFICIAL_NODE_IDENTITY_SCHEMA}"
        )
    selections = payload.get("selections")
    if not isinstance(selections, list):
        raise HamburgNamedScopeError("LSA identity selections must be a list")
    by_id: dict[str, Mapping[str, Any]] = {}
    for item in selections:
        if not isinstance(item, Mapping):
            raise HamburgNamedScopeError("LSA identity selections must contain objects")
        node_id = str(item.get("expected_node_id", ""))
        if node_id in by_id:
            raise HamburgNamedScopeError(f"duplicate LSA identity selection: {node_id}")
        by_id[node_id] = item
    if tuple(by_id) != NAMED_NODE_IDS:
        raise HamburgNamedScopeError(
            f"LSA identity must contain exactly the named nodes in order {NAMED_NODE_IDS!r}"
        )
    normalized: list[dict[str, Any]] = []
    for spec in NAMED_NODE_SPECS:
        item = by_id[spec["node_id"]]
        if item.get("decision") != "pass":
            raise HamburgNamedScopeError(f"official LSA identity is not proven for {spec['node_id']}")
        selected = item.get("selected_node")
        if not isinstance(selected, Mapping):
            raise HamburgNamedScopeError(f"selected_node is missing for {spec['node_id']}")
        if str(selected.get("node_id")) != spec["node_id"]:
            raise HamburgNamedScopeError(f"selected LSA node mismatch for {spec['node_id']}")
        if str(selected.get("official_name", "")).strip() != spec["official_name"]:
            raise HamburgNamedScopeError(
                f"official LSA name mismatch for {spec['node_id']}: "
                f"expected {spec['official_name']!r}"
            )
        geometry = selected.get("point_geometry")
        coordinates = geometry.get("coordinates") if isinstance(geometry, Mapping) else None
        if not isinstance(coordinates, Sequence) or isinstance(coordinates, (str, bytes)) or len(coordinates) != 2:
            raise HamburgNamedScopeError(f"official LSA point geometry is invalid for {spec['node_id']}")
        normalized.append(
            {
                "node_id": spec["node_id"],
                "role": spec["role"],
                "official_name": spec["official_name"],
                "feature_id": selected.get("feature_id"),
                "coordinates": [float(coordinates[0]), float(coordinates[1])],
                "signal_type": str(selected.get("signal_type", "")),
            }
        )
    return normalized


def _validate_corridor_scope(payload: Mapping[str, Any]) -> None:
    if payload.get("schema") != OFFICIAL_CORRIDOR_SCOPE_SCHEMA:
        raise HamburgNamedScopeError(
            f"official corridor scope schema must be {OFFICIAL_CORRIDOR_SCOPE_SCHEMA}"
        )
    if payload.get("scope_id") != NAMED_SCOPE_ID:
        raise HamburgNamedScopeError(
            "official corridor scope is not the named 2349/2394/2403 corridor"
        )
    links = payload.get("links")
    if not isinstance(links, list) or not links:
        raise HamburgNamedScopeError("official corridor scope must contain at least one link")


def _validate_signal_discovery(payload: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    if payload.get("schema") != SIGNAL_ASSET_DISCOVERY_SCHEMA:
        raise HamburgNamedScopeError(
            f"signal discovery schema must be {SIGNAL_ASSET_DISCOVERY_SCHEMA}"
        )
    requested = _node_id_tuple(payload.get("requested_node_ids"), "requested_node_ids")
    resolved = _node_id_tuple(payload.get("resolved_node_ids"), "resolved_node_ids")
    unresolved = _node_id_tuple(payload.get("unresolved_node_ids"), "unresolved_node_ids")
    if requested != NAMED_NODE_IDS:
        raise HamburgNamedScopeError(
            f"signal discovery must request exactly {NAMED_NODE_IDS!r}, got {requested!r}"
        )
    if set(resolved) | set(unresolved) != set(requested) or set(resolved) & set(unresolved):
        raise HamburgNamedScopeError("signal discovery resolved/unresolved IDs do not partition the request")
    return {
        "requested_node_ids": requested,
        "resolved_node_ids": resolved,
        "unresolved_node_ids": unresolved,
    }


def _node_id_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise HamburgNamedScopeError(f"signal discovery {label} must be a list")
    values = tuple(str(item) for item in value)
    if len(set(values)) != len(values):
        raise HamburgNamedScopeError(f"signal discovery {label} contains duplicate node IDs")
    return values


def _source_identity(path: Path, schema: Any) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
        "schema": str(schema),
    }
