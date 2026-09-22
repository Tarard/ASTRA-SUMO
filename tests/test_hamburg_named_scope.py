from __future__ import annotations

import json
from pathlib import Path

import pytest

from torii_sumo.core.hamburg_named_scope import (
    HamburgNamedScopeError,
    NAMED_SCOPE_ID,
    freeze_hamburg_named_scope,
    validate_hamburg_named_scope_manifest,
)


def _write_inputs(tmp_path: Path, *, scope_id: str = NAMED_SCOPE_ID) -> tuple[Path, Path, Path]:
    lsa = {
        "schema": "torii.hamburg-lsa-node-identity-evidence/v1",
        "selections": [
            {
                "expected_node_id": node_id,
                "decision": "pass",
                "selected_node": {
                    "node_id": node_id,
                    "official_name": name,
                    "feature_id": int(feature_id),
                    "signal_type": "K-LSA",
                    "point_geometry": {"type": "Point", "coordinates": [9.99 + index / 1000, 53.54]},
                },
            }
            for index, (node_id, name, feature_id) in enumerate(
                (
                    ("2349", "Am Sandtorkai/Großer Grasbrook", "17251"),
                    ("2394", "Am Sandtorkai/Am Sandtorpark", "17277"),
                    ("2403", "Am Sandtorkai/Osakaallee", "17285"),
                )
            )
        ],
    }
    scope = {
        "schema": "torii.hamburg-hh-sib-corridor-scope/v1",
        "scope_id": scope_id,
        "links": [{"link_object_id": "A326:fragment", "geometry_order": "auto"}],
    }
    discovery = {
        "schema": "torii.hamburg-signal-asset-discovery/v1",
        "requested_node_ids": ["2349", "2394", "2403"],
        "resolved_node_ids": ["2349", "2394"],
        "unresolved_node_ids": ["2403"],
    }
    paths = []
    for name, payload in (("lsa.json", lsa), ("scope.json", scope), ("discovery.json", discovery)):
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(path)
    return tuple(paths)  # type: ignore[return-value]


def test_freeze_named_scope_is_partial_and_blocks_incomplete_signal_materialization(tmp_path: Path) -> None:
    lsa, scope, discovery = _write_inputs(tmp_path)
    output = tmp_path / "named-scope.json"

    report = freeze_hamburg_named_scope(
        lsa_identity_file=lsa,
        corridor_scope_file=scope,
        signal_asset_discovery_file=discovery,
        output_file=output,
    )

    assert report["scope_id"] == NAMED_SCOPE_ID
    assert report["status"] == "partial"
    assert report["decision"] == "blocked"
    assert report["nodes"][0]["node_id"] == "2349"
    assert report["signal_assets"]["unresolved_node_ids"] == ["2403"]
    assert validate_hamburg_named_scope_manifest(output)["scope_id"] == NAMED_SCOPE_ID
    with pytest.raises(HamburgNamedScopeError, match="signal-complete"):
        validate_hamburg_named_scope_manifest(output, require_signal_assets=True)


def test_named_scope_manifest_rejects_changed_source(tmp_path: Path) -> None:
    lsa, scope, discovery = _write_inputs(tmp_path)
    output = tmp_path / "named-scope.json"
    freeze_hamburg_named_scope(
        lsa_identity_file=lsa,
        corridor_scope_file=scope,
        signal_asset_discovery_file=discovery,
        output_file=output,
    )
    scope.write_text(scope.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(HamburgNamedScopeError, match="hash changed"):
        validate_hamburg_named_scope_manifest(output)


def test_named_scope_rejects_legacy_or_mismatched_official_scope(tmp_path: Path) -> None:
    lsa, scope, discovery = _write_inputs(tmp_path, scope_id="hamburg_sandtorkai_0228_2421_2394_legacy")

    with pytest.raises(HamburgNamedScopeError, match="not the named"):
        freeze_hamburg_named_scope(
            lsa_identity_file=lsa,
            corridor_scope_file=scope,
            signal_asset_discovery_file=discovery,
            output_file=tmp_path / "named-scope.json",
        )
