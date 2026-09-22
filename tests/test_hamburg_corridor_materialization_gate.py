from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import torii_sumo.core.hamburg_corridor_workflow as corridor_workflow
from torii_sumo.core.hamburg_corridor_tls_materializer import (
    HamburgCorridorTlsMaterializationError,
    _road_sumo_materialization_gate,
    materialize_hamburg_sandtorkai_corridor_tls_candidate,
)


SOURCE_SHA = "b" * 64
PATHS_SHA = "c" * 64
ENDPOINTS_SHA = "d" * 64


def _binding() -> dict[str, object]:
    return {
        "schema": "torii.intersection-road-sumo-binding/v1",
        "road_sumo_binding_id": "road-sumo-binding-fixture",
        "automatic_promotion_gate": "blocked",
        "lineage_source_sha256_binding": {
            "osm_source_sha256": "a" * 64,
            "sumo_source_sha256": SOURCE_SHA,
            "status": "pass",
        },
        "connection_intents": [
            {
                "connection_intent_id": "intent-fixture",
                "status": "ready_for_lane_connection_review",
                "from_trusted_sumo_edge_ids": ["west-in"],
                "to_trusted_sumo_edge_ids": ["east-out"],
            }
        ],
    }


def _selection() -> dict[str, object]:
    return {
        "bindings": [
            {
                "node_id": "0228",
                "connection_id": "movement-1",
                "from_edge": "west-in",
                "from_lane": 1,
                "to_edge": "east-out",
                "to_lane": 0,
                "owner_id": "owner-west-east",
                "link_index": 2,
            }
        ]
    }


def test_corridor_gate_turns_selected_map_ocit_rows_into_explicit_intent_evidence() -> None:
    result = _road_sumo_materialization_gate(
        road_sumo_binding=_binding(),
        source_sumo_sha256=SOURCE_SHA,
        selection=_selection(),
        movement_paths_sha256=PATHS_SHA,
        movement_endpoints_sha256=ENDPOINTS_SHA,
    )

    assert result["status"] == "pass"
    row = result["planned_lane_connections"][0]
    assert row["planned_connection_id"] == "official:0228:movement-1"
    assert row["matching_connection_intent_ids"] == ["intent-fixture"]
    assert row["evidence"]["SUMO_owner_and_link_index_decision"] == [
        f"sumo-owner:{SOURCE_SHA}:owner-west-east:link-index:2"
    ]


def test_corridor_tls_materializer_fails_before_plain_export_without_intent_artifact(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    paths = tmp_path / "paths.json"
    endpoints = tmp_path / "endpoints.json"
    source.write_text("<net/>\n", encoding="utf-8")
    paths.write_text("[]\n", encoding="utf-8")
    endpoints.write_text('{"movements": []}\n', encoding="utf-8")

    with pytest.raises(HamburgCorridorTlsMaterializationError, match="required before any corridor"):
        materialize_hamburg_sandtorkai_corridor_tls_candidate(
            source_net_file=source,
            movement_paths_file=paths,
            movement_endpoints_file=endpoints,
            expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            expected_movement_paths_sha256=hashlib.sha256(paths.read_bytes()).hexdigest(),
            expected_movement_endpoints_sha256=hashlib.sha256(endpoints.read_bytes()).hexdigest(),
            output_dir=tmp_path / "out",
        )


def test_geometry_safe_workflow_stops_before_tls_when_exact_candidate_binding_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry_net = tmp_path / "geometry.net.xml"
    geometry_manifest = tmp_path / "geometry.manifest.json"
    geometry_net.write_text("<net/>\n", encoding="utf-8")
    geometry_manifest.write_text("{}\n", encoding="utf-8")

    def fake_geometry(**_: object) -> dict[str, object]:
        return {
            "status": "review_ready",
            "surface_overlap_comparison": {"status": "pass"},
            "artifacts": {
                "network": str(geometry_net),
                "manifest": str(geometry_manifest),
            },
        }

    def tls_must_not_run(**_: object) -> dict[str, object]:
        raise AssertionError("TLS materializer must not run without a road SUMO binding")

    monkeypatch.setattr(
        corridor_workflow,
        "materialize_hamburg_sandtorkai_geometry_safe_candidate",
        fake_geometry,
    )
    monkeypatch.setattr(
        corridor_workflow,
        "materialize_hamburg_sandtorkai_corridor_tls_candidate",
        tls_must_not_run,
    )

    result = corridor_workflow.prepare_hamburg_sandtorkai_geometry_safe_corridor_package(
        source_net_file=tmp_path / "source.net.xml",
        expected_source_sha256="a" * 64,
        movement_paths_file=tmp_path / "movement-paths.json",
        movement_endpoints_file=tmp_path / "movement-endpoints.json",
        expected_movement_paths_sha256="b" * 64,
        expected_movement_endpoints_sha256="c" * 64,
        map_xml_files=(),
        signal_stream_file=tmp_path / "signals.csv",
        count_stream_snapshot=tmp_path / "counts.json",
        canonical_count_file=tmp_path / "canonical.csv",
        output_dir=tmp_path / "workflow",
    )

    assert result["status"] == "blocked"
    assert result["stages"]["road_sumo_binding"]["status"] == "blocked"
    assert result["stages"]["tls"] == {"status": "not_run"}
    assert Path(result["manifest_file"]).is_file()
