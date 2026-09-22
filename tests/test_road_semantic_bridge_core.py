from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from torii_sumo.road_network.adapters.hamburg_hh_sib import read_hamburg_hh_sib_snapshot
from torii_sumo.road_network.adapters.osm import read_osm_road_snapshot
from torii_sumo.road_network.conflation import generate_official_osm_conflation_candidates
from torii_sumo.road_network.contracts import RoadObjectRef, RoadPropertyAssignment
from torii_sumo.road_network.semantic_bridge import build_road_semantic_bridge


FIXTURES = Path(__file__).parent / "fixtures" / "road_network"
TARGET_TIME = datetime(2026, 7, 19, 12, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 7, 19, 12, 5, tzinfo=UTC)
VALID_FROM = datetime(2026, 7, 19, tzinfo=UTC)
VALID_TO = datetime(2026, 7, 20, tzinfo=UTC)
HH_SIB_URL = (
    "https://api.hamburg.de/datasets/v1/strassen_und_wegenetz/collections/"
    "strassennetz_gesamt/items?f=json&strassenname=Am%20Sandtorkai"
)
HH_SIB_BBOX = (9.9798, 53.5398, 9.9822, 53.5403)
HVS_SHA256 = "d" * 64


def _source_context() -> tuple[dict, dict, dict, str]:
    osm_report = read_osm_road_snapshot(
        FIXTURES / "r1.osm.xml",
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )
    hh_sib_report = read_hamburg_hh_sib_snapshot(
        FIXTURES / "hh_sib_sample.geojson",
        request_url=HH_SIB_URL,
        bbox=HH_SIB_BBOX,
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )
    candidates = generate_official_osm_conflation_candidates(
        hh_sib_report,
        osm_report,
        target_time=TARGET_TIME,
        search_radius_m=25,
        overlap_tolerance_m=8,
    )
    return osm_report, hh_sib_report, candidates, candidates["candidate_sets"][0]["candidate_set_id"]


def _ref_from_payload(payload: dict) -> RoadObjectRef:
    return RoadObjectRef(
        namespace=payload["namespace"],
        object_type=payload["object_type"],
        object_id=payload["object_id"],
        source_sha256=payload["source_sha256"],
        valid_from=datetime.fromisoformat(payload["valid_from"]),
        valid_to=datetime.fromisoformat(payload["valid_to"]),
        provider=payload.get("provider", ""),
        dataset=payload.get("dataset", ""),
        edition=payload.get("edition", ""),
        jurisdiction=payload.get("jurisdiction", ""),
    )


def _reviewed_assignments(hh_sib_report: dict) -> tuple[RoadPropertyAssignment, ...]:
    target_ref = _ref_from_payload(hh_sib_report["road_link_assertions"][0]["source_ref"])
    hvs_ref = RoadObjectRef(
        namespace="official.hamburg_hvs",
        object_type="hvs_feature",
        object_id="hvs-am-sandtorkai",
        source_sha256=HVS_SHA256,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
        provider="Freie und Hansestadt Hamburg",
        dataset="hauptverkehrsstrassen",
        jurisdiction="DE-HH",
    )
    return tuple(
        RoadPropertyAssignment(
            assignment_id=assignment_id,
            target_ref=target_ref,
            property_name=property_name,
            classification_scheme=scheme,
            value=value,
            direction="both",
            evidence_refs=(hvs_ref,),
            status="pass",
            reason="explicit reviewed HVS-to-HH-SIB category assignment",
        )
        for assignment_id, property_name, scheme, value in (
            ("hvs-membership", "hamburg_membership", "de:hamburg:hvs", "hvs"),
            ("network-role", "network_role", "torii:network-role:v1", "arterial"),
            ("rin-category", "rin_category", "de:rin:2008", "HS III"),
        )
    )


def _category_sources() -> list[dict]:
    return [
        {
            "source_id": "hamburg_hauptverkehrsstrassen",
            "status": "pass",
            "source_snapshot": {
                "sha256": HVS_SHA256,
                "path": "frozen-hamburg-hvs.geojson",
                "target_time": TARGET_TIME.isoformat(),
            },
        }
    ]


def _bridge(*, selections: list[dict], assignments: tuple[RoadPropertyAssignment | dict, ...], category_sources: list[dict]) -> dict:
    osm_report, _, _, _ = _source_context()
    return build_road_semantic_bridge(
        osm_path=FIXTURES / "r1.osm.xml",
        sumo_path=FIXTURES / "r1.net.xml",
        hh_sib_snapshot_file=FIXTURES / "hh_sib_sample.geojson",
        hh_sib_request_url=HH_SIB_URL,
        hh_sib_bbox=HH_SIB_BBOX,
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
        sumo_imported_from="osm",
        sumo_imported_source_sha256=osm_report["source_snapshot"]["sha256"],
        official_osm_search_radius_m=25,
        official_osm_overlap_tolerance_m=8,
        reviewed_official_osm_selections=selections,
        reviewed_property_assignments=assignments,
        official_category_sources=category_sources,
    )


def test_bridge_projects_only_explicit_hvs_category_evidence_and_preserves_sources() -> None:
    osm_report, hh_sib_report, _, candidate_set_id = _source_context()
    source_paths = (FIXTURES / "r1.osm.xml", FIXTURES / "r1.net.xml", FIXTURES / "hh_sib_sample.geojson")
    before = {path: path.read_bytes() for path in source_paths}

    # Use serialized assignments to exercise the public JSON-compatible path.
    report = _bridge(
        selections=[{"candidate_set_id": candidate_set_id, "review_decision_id": "review-20260719-01"}],
        assignments=tuple(item.as_dict() for item in _reviewed_assignments(hh_sib_report)),
        category_sources=_category_sources(),
    )

    assert {path: path.read_bytes() for path in source_paths} == before
    assert report["status"] == "review_required"  # fixture retains one rule-derived SUMO origin mapping.
    assert report["gates"] == {
        "source_snapshots": "pass",
        "official_osm_reviewed_selection": "pass",
        "canonical_official_categories": "pass",
        "road_detail_projection": "pass",
        "osm_sumo_lineage": "review_required",
        "automatic_promotion": "blocked",
    }
    assert report["raw_hh_sib_inventory"]["canonical_category_inference"] == "disabled"
    assert report["raw_hh_sib_inventory"]["source_sha256"] == hh_sib_report["source_snapshot"]["sha256"]
    assert report["bounded_official_osm_segment_candidates"] == []
    assert report["bounded_official_osm_segment_relation_proposals"] == []
    assert report["road_network_evidence"]["status"] == "pass"
    assert set(report["road_network_evidence"]["by_way_id"]) == {"100", "101"}
    for way_id in ("100", "101"):
        projected = report["road_network_evidence"]["by_way_id"][way_id]
        assert projected["authority_category"] == "hvs"
        assert projected["network_role"] == "arterial"
        assert projected["functional_category"] == "HS III"
        assert projected["category_source_sha256s"] == [HVS_SHA256]
        assert {
            (item["namespace"], item["object_type"], item["source_sha256"])
            for item in projected["assignment_evidence_refs"]
        } == {("official.hamburg_hvs", "hvs_feature", HVS_SHA256)}
        assert "hh_sib_class_raw" not in projected["official_properties"]
    assert report["sumo_lineage_by_osm_way_id"]["100"]["mapping_status"] == "pass"
    assert set(report["sumo_lineage_by_osm_way_id"]["100"]["sumo_edge_ids"]) == {"-100#0", "100#0", "100#1"}
    assert report["osm_sumo_lineage"]["source_sha256_binding"]["sumo_source_sha256"] == hashlib.sha256(
        (FIXTURES / "r1.net.xml").read_bytes()
    ).hexdigest()
    assert report["classification_only"] is True
    assert report["automatic_promotion_gate"] == "blocked"
    assert osm_report["source_snapshot"]["sha256"] in report["source_sha256s"]


def test_bridge_refuses_hvs_assignment_without_distinct_category_source() -> None:
    _, hh_sib_report, _, candidate_set_id = _source_context()
    report = _bridge(
        selections=[{"candidate_set_id": candidate_set_id, "review_decision_id": "review-20260719-01"}],
        assignments=_reviewed_assignments(hh_sib_report),
        category_sources=[],
    )

    assert report["status"] == "blocked"
    assert "canonical_assignment_category_source_evidence_required:hvs-membership" in report["blocking_reasons"]
    assert report["road_network_evidence"]["by_way_id"]["100"]["authority_category"] == "unknown"
    assert "hh_sib_class_raw" not in report["road_network_evidence"]["by_way_id"]["100"]["official_properties"]


def test_bridge_does_not_project_generated_candidate_without_a_reviewed_selection() -> None:
    _, hh_sib_report, _, _ = _source_context()
    report = _bridge(
        selections=[],
        assignments=_reviewed_assignments(hh_sib_report),
        category_sources=_category_sources(),
    )

    assert report["status"] == "review_required"
    assert report["reviewed_official_osm_relations"] == []
    assert report["road_network_evidence"]["by_way_id"] == {}
    assert report["road_network_evidence"]["excluded_relation_ids"] == []
    assert any(reason.startswith("eligible_official_osm_candidate_set_unreviewed:") for reason in report["review_reasons"])
