from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from torii_sumo.road_network.adapters.hamburg_hh_sib import read_hamburg_hh_sib_snapshot
from torii_sumo.road_network.adapters.osm import read_osm_road_snapshot
from torii_sumo.road_network.adapters.sumo import read_sumo_road_snapshot
from torii_sumo.road_network.conflation import (
    build_osm_subset_derivation_relations,
    build_osm_sumo_lineage_relations,
    generate_official_osm_conflation_candidates,
)


FIXTURES = Path(__file__).parent / "fixtures" / "road_network"
OSM_SOURCE_SHA256 = hashlib.sha256((FIXTURES / "r1.osm.xml").read_bytes()).hexdigest()
TARGET_TIME = datetime(2026, 7, 19, 12, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 7, 19, 12, 5, tzinfo=UTC)
VALID_FROM = datetime(2026, 7, 19, tzinfo=UTC)
VALID_TO = datetime(2026, 7, 20, tzinfo=UTC)


def _osm_report() -> dict:
    return read_osm_road_snapshot(
        FIXTURES / "r1.osm.xml",
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )


def _sumo_report() -> dict:
    return read_sumo_road_snapshot(
        FIXTURES / "r1.net.xml",
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
        imported_from="osm",
        imported_source_sha256=OSM_SOURCE_SHA256,
    )


def _official_report() -> dict:
    return read_hamburg_hh_sib_snapshot(
        FIXTURES / "hh_sib_sample.geojson",
        request_url=(
            "https://api.hamburg.de/datasets/v1/strassen_und_wegenetz/collections/"
            "strassennetz_gesamt/items?f=json&strassenname=Am%20Sandtorkai"
        ),
        bbox=(9.9798, 53.5398, 9.9822, 53.5403),
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )


def _official_report_with_missing_road_key() -> dict:
    return read_hamburg_hh_sib_snapshot(
        FIXTURES / "hh_sib_missing_strassenschluessel.geojson",
        request_url=(
            "https://api.hamburg.de/datasets/v1/strassen_und_wegenetz/collections/"
            "strassennetz_gesamt/items?f=json&strassenname=Current%20HH-SIB%20Shape"
        ),
        bbox=(9.9798, 53.5398, 9.9822, 53.5403),
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )


def test_official_osm_candidates_keep_components_hard_gates_and_alternatives() -> None:
    report = generate_official_osm_conflation_candidates(
        _official_report(),
        _osm_report(),
        target_time=TARGET_TIME,
        search_radius_m=25,
        overlap_tolerance_m=8,
    )

    assert report["status"] == "review_required"
    assert report["counts"] == {
        "official_motor_link_count": 1,
        "osm_way_count": 4,
        "pairwise_candidate_count": 4,
        "identity_eligible_pair_count": 3,
        "groupable_pair_count": 2,
        "review_only_pair_count": 1,
        "hard_gated_pair_count": 1,
        "candidate_set_count": 1,
        "eligible_candidate_set_count": 1,
        "relation_proposal_count": 1,
        "bounded_segment_candidate_count": 0,
        "bounded_segment_review_candidate_count": 0,
        "bounded_segment_blocked_candidate_count": 0,
        "bounded_segment_relation_proposal_count": 0,
    }
    candidates = {item["osm_way_id"]: item for item in report["pairwise_candidates"]}
    assert candidates["100"]["decision"] == "eligible"
    assert candidates["101"]["decision"] == "eligible"
    assert candidates["200"]["decision"] == "eligible"
    assert candidates["200"]["selection_disposition"] == "review_only"
    assert "transport_role_mismatch" in candidates["200"]["property_projection_gate_failures"]
    assert candidates["300"]["decision"] == "blocked"
    assert candidates["300"]["evidence"]["name_agreement"] == 0.0
    assert "aggregate_score" not in candidates["100"]
    assert set(candidates["100"]["evidence"]) == {
        "geometry_overlap_ratio",
        "official_coverage_ratio",
        "lateral_distance_m",
        "lateral_distance_p95_m",
        "lateral_distance_max_m",
        "official_to_candidate_lateral_p95_m",
        "candidate_to_official_lateral_p95_m",
        "heading_delta_deg",
        "topology_agreement",
        "name_agreement",
        "road_ref_agreement",
        "official_road_key_agreement",
        "carriageway_agreement",
        "lane_profile_agreement",
        "role_agreement",
    }
    assert (
        "official_stationing_direction_not_verified_by_nullpunkte"
        in candidates["100"]["property_projection_gate_failures"]
    )
    assert "explicit_name_mismatch_soft_evidence" in candidates["300"]["review_reasons"]
    proposal = report["relation_proposals"][0]
    assert proposal["status"] == "review_required"
    assert {item["object_id"] for item in proposal["right_refs"]} == {"100", "101"}
    assert "unreviewed_group_selection" in proposal["review_reasons"]
    assert report["candidate_sets"][0]["member_osm_way_ids"] == ["100", "101"]
    assert report["candidate_sets"][0]["endpoint_topology"]["finite_form"] == "directional_pair"
    assert report["candidate_sets"][0]["endpoint_topology"]["strand_count"] == 2
    assert proposal["automatic_promotion_gate"] == "blocked"


def test_official_link_contains_bounded_osm_fragment_as_review_only_segment() -> None:
    official = _official_report()
    osm = _osm_report()
    official_feature = official["raw_feature_assertions"][0]
    official_feature["geometry"] = {
        "type": "LineString",
        "coordinates": [[9.980000, 53.540025], [9.995000, 53.540025]],
    }
    official_link = official["motor_road_link_assertions"][0]
    official_link["length_m"] = 986.0
    official_link["station_coverage"] = {
        "begin_m": 0.0,
        "end_m": 986.0,
        "gap_count": 0,
        "overlap_count": 0,
        "status": "pass",
    }
    bounded_way = next(item for item in osm["way_assertions"] if item["way_id"] == "100")
    bounded_way["geometry_lonlat"] = [[9.985000, 53.540025], [9.986250, 53.540025]]
    osm["way_assertions"] = [bounded_way]

    report = generate_official_osm_conflation_candidates(
        official,
        osm,
        target_time=TARGET_TIME,
        search_radius_m=25,
        overlap_tolerance_m=8,
    )

    # The short way is not accidentally promoted through the full-link set.
    assert len(report["candidate_sets"]) == 1
    assert report["candidate_sets"][0]["decision"] == "blocked"
    assert report["relation_proposals"] == []
    assert report["counts"]["bounded_segment_review_candidate_count"] == 1
    candidate = report["bounded_segment_candidates"][0]
    assert candidate["decision"] == "review_candidate"
    assert candidate["relation_kind"] == "contains_bounded_segment"
    assert candidate["reference_map_review_required"] is True
    assert candidate["reference_map_review"]["automated_acquisition"] == "disabled"
    assert candidate["fragment_aggregation"]["automatic_aggregation"] == "disabled"
    assert candidate["fragment_aggregation"]["candidate_osm_way_ids"] == ["100"]
    assert candidate["fragment_cause"]["value"] == "unknown"
    assert candidate["segment_evidence"]["candidate_geometry_coverage_ratio"] == 1.0
    assert candidate["segment_evidence"]["geometry_linear_reference"]["status"] == "pass"
    assert candidate["segment_evidence"]["hh_sib_stationing_hypotheses"]["status"] == "orientation_hypotheses_available"
    assert candidate["property_projection_disposition"] == "excluded_pending_explicit_segment_review_contract"
    proposal = report["bounded_segment_relation_proposals"][0]
    assert proposal["relation_kind"] == "contains_bounded_segment"
    assert proposal["status"] == "review_required"
    assert "bounded_segment_relation_requires_explicit_review" in proposal["review_reasons"]


def test_official_osm_candidates_fail_closed_when_hh_sib_link_identity_is_unresolved() -> None:
    report = generate_official_osm_conflation_candidates(
        _official_report_with_missing_road_key(),
        _osm_report(),
        target_time=TARGET_TIME,
        search_radius_m=25,
        overlap_tolerance_m=8,
    )

    assert report["status"] == "review_required"
    assert report["counts"]["official_motor_link_count"] == 0
    assert report["counts"]["pairwise_candidate_count"] == 0
    assert report["candidate_sets"] == []
    assert report["relation_proposals"] == []
    assert len(report["review_reasons"]) == 2
    assert all(reason.startswith("official_link_identity_not_resolved:") for reason in report["review_reasons"])


def test_osm_sumo_lineage_relations_pass_only_for_observed_orig_id() -> None:
    report = build_osm_sumo_lineage_relations(
        _osm_report(),
        _sumo_report(),
        target_time=TARGET_TIME,
    )

    by_way = {item["left_refs"][0]["object_id"]: item for item in report["relations"]}
    assert by_way["100"]["status"] == "pass"
    assert {item["object_id"] for item in by_way["100"]["right_refs"]} == {
        "-100#0",
        "100#0",
        "100#1",
    }
    assert by_way["100"]["direction"] == "both"
    assert by_way["100"]["direction_evidence"]["status"] == "geometry_verified"
    assert by_way["101"]["status"] == "review_required"
    assert "sumo_osm_lineage_rule_derived" in by_way["101"]["review_reasons"]
    assert report["source_sha256_binding"]["sumo_source_sha256"] == _sumo_report()[
        "source_snapshot"
    ]["sha256"]
    assert not any(
        ref["object_id"].startswith(":") for relation in report["relations"] for ref in relation["right_refs"]
    )
    assert report["automatic_promotion_gate"] == "blocked"


def test_osm_sumo_lineage_blocks_when_declared_import_hash_is_not_the_osm_snapshot() -> None:
    sumo_report = read_sumo_road_snapshot(
        FIXTURES / "r1.net.xml",
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
        imported_from="osm",
        imported_source_sha256="f" * 64,
    )

    report = build_osm_sumo_lineage_relations(_osm_report(), sumo_report, target_time=TARGET_TIME)

    assert report["status"] == "blocked"
    assert report["relations"] == []
    assert report["blocking_reasons"] == ["sumo_imported_osm_sha256_mismatch"]


def test_osm_subset_derivation_preserves_retained_ids_and_reports_omitted_modes() -> None:
    parent = _osm_report()
    derived = read_osm_road_snapshot(
        FIXTURES / "r1_subset.osm.xml",
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
        derived_from_source_sha256=parent["source_snapshot"]["sha256"],
        derivation_kind="highway_subset_filter",
    )

    report = build_osm_subset_derivation_relations(parent, derived, target_time=TARGET_TIME)

    assert report["status"] == "pass"
    assert report["counts"] == {
        "parent_highway_way_count": 4,
        "derived_highway_way_count": 2,
        "retained_way_relation_count": 2,
        "omitted_parent_way_count": 2,
        "missing_parent_way_count": 0,
        "changed_way_count": 0,
    }
    assert {item["left_refs"][0]["object_id"] for item in report["relations"]} == {"100", "200"}
    assert report["omitted_role_counts"] == {"motor_vehicle": 2, "pedestrian": 0, "bicycle": 0}
    assert report["automatic_promotion_gate"] == "blocked"


def test_osm_subset_derivation_blocks_parent_hash_mismatch() -> None:
    derived = read_osm_road_snapshot(
        FIXTURES / "r1_subset.osm.xml",
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
        derived_from_source_sha256="f" * 64,
        derivation_kind="highway_subset_filter",
    )

    report = build_osm_subset_derivation_relations(_osm_report(), derived, target_time=TARGET_TIME)

    assert report["status"] == "blocked"
    assert report["blocking_reasons"] == ["derived_osm_parent_sha256_mismatch"]
