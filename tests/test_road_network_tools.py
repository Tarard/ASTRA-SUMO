from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from torii_sumo.tools.road_network_tools import (
    sumo_intersection_road_sumo_bind,
    sumo_road_semantic_bridge,
)


FIXTURES = Path(__file__).parent / "fixtures" / "road_network"
HH_SIB_URL = (
    "https://api.hamburg.de/datasets/v1/strassen_und_wegenetz/collections/"
    "strassennetz_gesamt/items?f=json&strassenname=Am%20Sandtorkai"
)
TARGET_TIME = "2026-07-19T12:00:00+00:00"
RETRIEVED_AT = "2026-07-19T12:05:00+00:00"
VALID_FROM = "2026-07-19T00:00:00+00:00"
VALID_TO = "2026-07-20T00:00:00+00:00"


def _call_bridge(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "osm_file": str(FIXTURES / "r1.osm.xml"),
        "sumo_net_file": str(FIXTURES / "r1.net.xml"),
        "hh_sib_snapshot_file": str(FIXTURES / "hh_sib_sample.geojson"),
        "hh_sib_request_url": HH_SIB_URL,
        "hh_sib_bbox": [9.9798, 53.5398, 9.9822, 53.5403],
        "target_time": TARGET_TIME,
        "retrieved_at": RETRIEVED_AT,
        "valid_from": VALID_FROM,
        "valid_to": VALID_TO,
        "sumo_imported_from": "osm",
        "sumo_imported_source_sha256": hashlib.sha256((FIXTURES / "r1.osm.xml").read_bytes()).hexdigest(),
    }
    values.update(overrides)
    return sumo_road_semantic_bridge(**values)  # type: ignore[arg-type]


def test_road_semantic_bridge_is_read_only_and_surfaces_projectable_evidence() -> None:
    source_paths = [
        FIXTURES / "r1.osm.xml",
        FIXTURES / "r1.net.xml",
        FIXTURES / "hh_sib_sample.geojson",
    ]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}

    report = _call_bridge()

    assert report["status"] == "review_required"
    assert report["claim_status"] == "classification_only"
    assert report["classification_only"] is True
    assert report["automatic_promotion_gate"] == "blocked"
    assert "bridge_report" in report
    evidence = report["road_network_evidence"]
    assert isinstance(evidence, dict)
    assert evidence["classification_only"] is True
    assert evidence["automatic_promotion_gate"] == "blocked"
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}


def test_road_semantic_bridge_writes_reusable_evidence_artifacts_and_keeps_hvs_separate(
    tmp_path: Path,
) -> None:
    report = _call_bridge(
        hvs_snapshot_file=str(FIXTURES / "hamburg_hvs_sample.geojson"),
        hvs_request_url=(
            "https://api.hamburg.de/datasets/v1/hauptverkehrsstrassen/collections/"
            "hauptverkehrsstrassen/items?f=json"
        ),
        output_dir=str(tmp_path),
    )

    source_inputs = report["source_inputs"]
    assert isinstance(source_inputs, dict)
    assert source_inputs["hvs_source_input"]["source_id"] == "hamburg_hauptverkehrsstrassen"
    artifacts = report["artifacts"]
    assert isinstance(artifacts, list)
    assert {item["kind"] for item in artifacts} == {
        "bridge_report",
        "road_network_evidence",
        "manifest",
    }
    for item in artifacts:
        path = Path(item["path"])
        assert path.is_file()
        assert item["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    evidence_file = next(Path(item["path"]) for item in artifacts if item["kind"] == "road_network_evidence")
    evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert evidence["automatic_promotion_gate"] == "blocked"
    bridge_file = next(Path(item["path"]) for item in artifacts if item["kind"] == "bridge_report")
    bridge = json.loads(bridge_file.read_text(encoding="utf-8"))
    manifest_file = next(Path(item["path"]) for item in artifacts if item["kind"] == "manifest")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert evidence["bridge_id"] == bridge["bridge_id"] == manifest["bridge_id"]
    assert manifest["road_network_evidence_bridge_id"] == bridge["bridge_id"]
    before = {Path(item["path"]): hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() for item in artifacts}
    with pytest.raises(ValueError, match="already contains road semantic bridge artifacts"):
        _call_bridge(output_dir=str(tmp_path))
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}


def test_review_assignment_file_accepts_candidate_selection_not_raw_relation(tmp_path: Path) -> None:
    preview = _call_bridge()
    bridge_report = preview["bridge_report"]
    assert isinstance(bridge_report, dict)
    candidate_sets = bridge_report["official_osm_candidates"]["candidate_sets"]
    assert candidate_sets
    selection_file = tmp_path / "reviewed-selection.json"
    selection_file.write_text(
        json.dumps(
            {
                "reviewed_official_osm_selections": [
                    {
                        "candidate_set_id": candidate_sets[0]["candidate_set_id"],
                        "review_decision_id": "review-2026-07-19-001",
                        "reason": "fixture-only reviewed identity selection",
                    }
                ],
                "reviewed_property_assignments": [],
                "official_category_sources": [],
            }
        ),
        encoding="utf-8",
    )

    report = _call_bridge(reviewed_assignment_file=str(selection_file))

    bridge = report["bridge_report"]
    assert isinstance(bridge, dict)
    assert bridge["reviewed_official_osm_relations"]
    assert report["automatic_promotion_gate"] == "blocked"


def test_review_assignment_file_rejects_unbound_raw_relation_format(tmp_path: Path) -> None:
    selection_file = tmp_path / "invalid-review.json"
    selection_file.write_text(
        json.dumps({"reviewed_official_osm_relations": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported keys"):
        _call_bridge(reviewed_assignment_file=str(selection_file))


def test_road_semantic_bridge_rejects_naive_time_and_bad_hh_sib_endpoint() -> None:
    with pytest.raises(ValueError, match="timezone"):
        _call_bridge(target_time="2026-07-19T12:00:00")
    with pytest.raises(ValueError, match="official HH-SIB"):
        _call_bridge(hh_sib_request_url="https://example.com/items")
    with pytest.raises(ValueError, match="separate from every source-input directory"):
        _call_bridge(output_dir=str(FIXTURES))


def test_intersection_road_sumo_bind_is_read_only_and_hash_binds_bridge_lineage(
    tmp_path: Path,
) -> None:
    osm_file = Path(__file__).parent / "intersection" / "fixtures" / "x4_signalized.osm.xml"
    osm_sha256 = hashlib.sha256(osm_file.read_bytes()).hexdigest()
    sumo_sha256 = "b" * 64
    evidence_file = tmp_path / "road-network-evidence.json"
    evidence_file.write_text(
        json.dumps(
            {
                "schema": "torii.road-detail-evidence-projection/v1",
                "bridge_id": "bridge-fixture-a",
                "status": "pass",
                "by_way_id": {
                    way_id: {
                        "authority_category": "hvs",
                        "network_role": "arterial",
                        "functional_category": "HS III",
                        "source_evidence_id": f"relation-{way_id}",
                        "source_relation_ids": [f"relation-{way_id}"],
                        "source_assignment_ids": [f"assignment-{way_id}"],
                        "source_sha256s": [osm_sha256],
                        "mapping_status": "pass",
                    }
                    for way_id in ("10", "11")
                },
                "conflicts": [],
                "excluded_relation_ids": [],
                "classification_only": True,
                "automatic_promotion_gate": "blocked",
                "claim_boundary": "Fixture-only classification evidence.",
            }
        ),
        encoding="utf-8",
    )
    bridge_file = tmp_path / "bridge-report.json"
    bridge_file.write_text(
        json.dumps(
            {
                "schema": "torii.road-semantic-bridge/v1",
                "bridge_id": "bridge-fixture-a",
                "status": "review_required",
                "classification_only": True,
                "automatic_promotion_gate": "blocked",
                "osm_sumo_lineage": {
                    "schema": "torii.road-network-semantics/v1/conflation-candidates/v1",
                    "relation_layer": "osm_to_sumo",
                    "status": "review_required",
                    "automatic_promotion_gate": "blocked",
                    "source_sha256_binding": {
                        "osm_source_sha256": osm_sha256,
                        "sumo_source_sha256": sumo_sha256,
                        "status": "pass",
                    },
                    "relations": [
                        {
                            "relation_id": f"lineage-{way_id}",
                            "status": "pass",
                            "direction": "both",
                            "left_refs": [
                                {
                                    "namespace": "osm",
                                    "object_type": "way",
                                    "object_id": way_id,
                                    "source_sha256": osm_sha256,
                                }
                            ],
                            "right_refs": [
                                {
                                    "namespace": "sumo",
                                    "object_type": "edge",
                                    "object_id": f"edge-{way_id}",
                                    "source_sha256": sumo_sha256,
                                }
                            ],
                            "review_reasons": [],
                        }
                        for way_id in ("10", "11")
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (osm_file, evidence_file, bridge_file)}

    report = sumo_intersection_road_sumo_bind(
        str(osm_file),
        "1",
        str(evidence_file),
        str(bridge_file),
        output_file=str(tmp_path / "road-sumo-binding.json"),
    )

    assert report["claim_status"] == "classification_only"
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["classification"]["source_sha256"] == osm_sha256
    binding = report["road_sumo_binding"]
    assert binding["lineage_source_sha256_binding"] == {
        "osm_source_sha256": osm_sha256,
        "sumo_source_sha256": sumo_sha256,
        "status": "pass",
    }
    assert binding["automatic_promotion_gate"] == "blocked"
    artifact = report["road_sumo_binding_artifact"]
    assert artifact is not None
    artifact_path = Path(artifact["path"])
    assert artifact_path.is_file()
    assert artifact["sha256"] == hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    assert json.loads(artifact_path.read_text(encoding="utf-8"))["road_sumo_binding_id"] == binding[
        "road_sumo_binding_id"
    ]
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}


def test_intersection_road_sumo_bind_rejects_bridge_for_another_osm_snapshot(tmp_path: Path) -> None:
    osm_file = Path(__file__).parent / "intersection" / "fixtures" / "x4_signalized.osm.xml"
    source_sha256 = hashlib.sha256(osm_file.read_bytes()).hexdigest()
    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_text(
        json.dumps(
            {
                "schema": "torii.road-detail-evidence-projection/v1",
                "bridge_id": "bridge-fixture-a",
                "status": "pass",
                "by_way_id": {},
                "conflicts": [],
                "excluded_relation_ids": [],
                "classification_only": True,
                "automatic_promotion_gate": "blocked",
            }
        ),
        encoding="utf-8",
    )
    bridge_file = tmp_path / "other-bridge.json"
    bridge_file.write_text(
        json.dumps(
            {
                "bridge_id": "bridge-fixture-a",
                "classification_only": True,
                "automatic_promotion_gate": "blocked",
                "osm_sumo_lineage": {
                    "source_sha256_binding": {
                        "osm_source_sha256": "a" * 64,
                        "sumo_source_sha256": "b" * 64,
                        "status": "pass",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not bind the exact local OSM snapshot"):
        sumo_intersection_road_sumo_bind(
            str(osm_file),
            "1",
            str(evidence_file),
            str(bridge_file),
        )
    assert hashlib.sha256(osm_file.read_bytes()).hexdigest() == source_sha256


def test_intersection_road_sumo_bind_rejects_evidence_from_a_different_bridge(tmp_path: Path) -> None:
    osm_file = Path(__file__).parent / "intersection" / "fixtures" / "x4_signalized.osm.xml"
    evidence_file = tmp_path / "other-evidence.json"
    evidence_file.write_text(
        json.dumps(
            {
                "schema": "torii.road-detail-evidence-projection/v1",
                "bridge_id": "bridge-a",
                "status": "pass",
                "by_way_id": {},
                "conflicts": [],
                "excluded_relation_ids": [],
                "classification_only": True,
                "automatic_promotion_gate": "blocked",
            }
        ),
        encoding="utf-8",
    )
    bridge_file = tmp_path / "bridge-b.json"
    bridge_file.write_text(
        json.dumps(
            {
                "bridge_id": "bridge-b",
                "classification_only": True,
                "automatic_promotion_gate": "blocked",
                "osm_sumo_lineage": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="bridge_id must equal"):
        sumo_intersection_road_sumo_bind(
            str(osm_file),
            "1",
            str(evidence_file),
            str(bridge_file),
        )
