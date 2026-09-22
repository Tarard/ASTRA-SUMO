from __future__ import annotations

from datetime import UTC, datetime

from torii_sumo.road_network.contracts import (
    CanonicalRoadLink,
    ConflationEvidence,
    RoadCorridor,
    RoadObjectRef,
    RoadPropertyAssignment,
    build_conflation_relation,
    project_road_detail_evidence,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
TARGET_TIME = datetime(2026, 7, 19, 12, tzinfo=UTC)
VALID_FROM = datetime(2026, 7, 19, tzinfo=UTC)
VALID_TO = datetime(2026, 7, 20, tzinfo=UTC)


def _ref(namespace: str, object_type: str, object_id: str, sha256: str) -> RoadObjectRef:
    return RoadObjectRef(
        namespace=namespace,
        object_type=object_type,
        object_id=object_id,
        source_sha256=sha256,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )


def _strong_evidence() -> ConflationEvidence:
    return ConflationEvidence(
        geometry_overlap_ratio=0.96,
        lateral_distance_m=1.2,
        heading_delta_deg=2.5,
        topology_agreement=1.0,
        name_agreement=1.0,
        road_ref_agreement=0.0,
        official_road_key_agreement=1.0,
        carriageway_agreement=0.9,
        lane_profile_agreement=0.8,
    )


def test_conflation_relation_is_many_to_many_hash_and_time_bound() -> None:
    official = _ref("official.hh_sib", "road_link", "A326:295:242500211:242500071", SHA_A)
    osm_left = _ref("osm", "way", "101", SHA_B)
    osm_right = _ref("osm", "way", "102", SHA_B)

    relation = build_conflation_relation(
        left_refs=(official,),
        right_refs=(osm_left, osm_right),
        relation_kind="covers",
        direction="both",
        target_time=TARGET_TIME,
        evidence=_strong_evidence(),
        reason="one official linear-reference link is represented by two OSM ways",
    )

    payload = relation.as_dict()
    assert relation.status == "pass"
    assert len(relation.left_refs) == 1
    assert len(relation.right_refs) == 2
    assert payload["source_sha256s"] == [SHA_A, SHA_B]
    assert payload["target_time"] == "2026-07-19T12:00:00+00:00"
    assert payload["classification_only"] is True
    assert payload["automatic_promotion_gate"] == "blocked"


def test_conflation_identity_is_order_invariant_and_supports_osm_to_sumo_splits() -> None:
    osm = _ref("osm", "way", "101", SHA_B)
    sumo_a = _ref("sumo", "edge", "101#0", SHA_C)
    sumo_b = _ref("sumo", "edge", "101#1", SHA_C)

    first = build_conflation_relation(
        left_refs=(osm,),
        right_refs=(sumo_b, sumo_a),
        relation_kind="covers",
        direction="with",
        target_time=TARGET_TIME,
        evidence=_strong_evidence(),
    )
    second = build_conflation_relation(
        left_refs=(osm,),
        right_refs=(sumo_a, sumo_b),
        relation_kind="covers",
        direction="with",
        target_time=TARGET_TIME,
        evidence=_strong_evidence(),
    )

    assert first.status == "pass"
    assert first.relation_id == second.relation_id
    assert [ref.object_id for ref in first.right_refs] == ["101#0", "101#1"]


def test_canonical_ref_does_not_require_a_fictional_source_validity_interval() -> None:
    canonical = CanonicalRoadLink(
        link_id="canonical-1",
        corridor_id="corridor-1",
        from_node_id="a",
        to_node_id="b",
        length_m=100,
        directionality="bidirectional",
    )
    osm = _ref("osm", "way", "101", SHA_B)

    relation = build_conflation_relation(
        left_refs=(canonical.ref,),
        right_refs=(osm,),
        relation_kind="equivalent",
        direction="both",
        target_time=TARGET_TIME,
        evidence=_strong_evidence(),
    )

    assert relation.status == "pass"
    assert not any("canonical" in reason for reason in relation.review_reasons)


def test_conflation_blocks_time_mismatch_and_keeps_alternatives_for_review() -> None:
    official = _ref("official.hh_sib", "road_link", "official-1", SHA_A)
    historical_osm = RoadObjectRef(
        namespace="osm",
        object_type="way",
        object_id="101",
        source_sha256=SHA_B,
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        valid_to=datetime(2025, 1, 1, tzinfo=UTC),
    )

    blocked = build_conflation_relation(
        left_refs=(official,),
        right_refs=(historical_osm,),
        relation_kind="equivalent",
        direction="both",
        target_time=TARGET_TIME,
        evidence=_strong_evidence(),
    )
    review = build_conflation_relation(
        left_refs=(official,),
        right_refs=(_ref("osm", "way", "102", SHA_C),),
        relation_kind="equivalent",
        direction="both",
        target_time=TARGET_TIME,
        evidence=_strong_evidence(),
        alternative_relation_ids=("relation-other-candidate",),
    )

    assert blocked.status == "blocked"
    assert any("target_time_outside_validity" in gate for gate in blocked.hard_gate_failures)
    assert review.status == "review_required"
    assert review.alternative_relation_ids == ("relation-other-candidate",)


def test_canonical_link_is_source_neutral_and_does_not_authorize_materialization() -> None:
    corridor = RoadCorridor(
        corridor_id="am-sandtorkai",
        names=("Am Sandtorkai", "Am Sandtorkai"),
        jurisdiction="DE-HH",
    )
    link = CanonicalRoadLink(
        link_id="am-sandtorkai-main-001",
        corridor_id=corridor.corridor_id,
        from_node_id="west-boundary",
        to_node_id="east-boundary",
        length_m=986.0,
        directionality="bidirectional",
    )

    payload = link.as_dict()
    assert corridor.as_dict()["names"] == ["Am Sandtorkai"]
    assert payload["source_system"] == "canonical"
    assert payload["classification_only"] is True
    assert payload["automatic_promotion_gate"] == "blocked"


def test_reviewed_relation_projects_orthogonal_official_properties_to_both_osm_ways() -> None:
    official = _ref("official.hh_sib", "road_link", "official-main", SHA_A)
    osm_left = _ref("osm", "way", "101", SHA_B)
    osm_right = _ref("osm", "way", "102", SHA_B)
    relation = build_conflation_relation(
        left_refs=(official,),
        right_refs=(osm_left, osm_right),
        relation_kind="covers",
        direction="both",
        target_time=TARGET_TIME,
        evidence=_strong_evidence(),
    )
    assignments = (
        RoadPropertyAssignment(
            assignment_id="hvs-membership",
            target_ref=official,
            property_name="hamburg_membership",
            classification_scheme="de:hamburg:hvs",
            value="hvs",
            direction="both",
            evidence_refs=(official,),
            status="pass",
        ),
        RoadPropertyAssignment(
            assignment_id="network-role",
            target_ref=official,
            property_name="network_role",
            classification_scheme="torii:network-role:v1",
            value="arterial",
            direction="both",
            evidence_refs=(official,),
            status="pass",
        ),
        RoadPropertyAssignment(
            assignment_id="rin-category",
            target_ref=official,
            property_name="rin_category",
            classification_scheme="de:rin:2008",
            value="HS III",
            direction="both",
            evidence_refs=(official,),
            status="pass",
        ),
    )

    projection = project_road_detail_evidence((relation,), assignments)

    assert projection["status"] == "pass"
    assert set(projection["by_way_id"]) == {"101", "102"}
    for way_id in ("101", "102"):
        item = projection["by_way_id"][way_id]
        assert item["authority_category"] == "hvs"
        assert item["network_role"] == "arterial"
        assert item["functional_category"] == "HS III"
        assert item["official_properties"] == {
            "hamburg_membership": "hvs",
            "network_role": "arterial",
            "rin_category": "HS III",
        }
        assert {assertion["classification_scheme"] for assertion in item["official_property_assertions"]} == {
            "de:hamburg:hvs",
            "de:rin:2008",
            "torii:network-role:v1",
        }
        assert item["source_relation_ids"] == [relation.relation_id]
    assert projection["automatic_promotion_gate"] == "blocked"


def test_review_required_relation_is_not_projected_as_authoritative() -> None:
    official = _ref("official.hh_sib", "road_link", "official-main", SHA_A)
    osm_way = _ref("osm", "way", "101", SHA_B)
    relation = build_conflation_relation(
        left_refs=(official,),
        right_refs=(osm_way,),
        relation_kind="overlaps",
        direction="both",
        target_time=TARGET_TIME,
        evidence=ConflationEvidence(name_agreement=1.0),
    )

    projection = project_road_detail_evidence((relation,), ())

    assert relation.status == "review_required"
    assert projection["status"] == "review_required"
    assert projection["by_way_id"] == {}
    assert projection["excluded_relation_ids"] == [relation.relation_id]


def test_bounded_osm_segment_relation_is_always_review_only_and_not_projected() -> None:
    official = _ref("official.hh_sib", "road_link", "official-main", SHA_A)
    osm_way = _ref("osm", "way", "101", SHA_B)
    relation = build_conflation_relation(
        left_refs=(official,),
        right_refs=(osm_way,),
        relation_kind="contains_bounded_segment",
        direction="with",
        target_time=TARGET_TIME,
        evidence=_strong_evidence(),
        reason="OSM way is only a bounded directional carriageway fragment",
    )

    projection = project_road_detail_evidence((relation,), ())

    assert relation.status == "review_required"
    assert "bounded_segment_relation_requires_explicit_review" in relation.review_reasons
    assert projection["by_way_id"] == {}
    assert projection["excluded_relation_ids"] == [relation.relation_id]


def test_projection_retains_hash_bound_external_category_evidence() -> None:
    hh_sib = _ref("official.hh_sib", "road_link_assertion", "official-main", SHA_A)
    hvs = _ref("official.hamburg_hvs", "hvs_feature", "hvs-1451541", SHA_C)
    osm = _ref("osm", "way", "101", SHA_B)
    relation = build_conflation_relation(
        left_refs=(hh_sib,),
        right_refs=(osm,),
        relation_kind="equivalent",
        direction="both",
        target_time=TARGET_TIME,
        evidence=_strong_evidence(),
    )
    assignment = RoadPropertyAssignment(
        assignment_id="reviewed-hvs-membership",
        target_ref=hh_sib,
        property_name="hamburg_membership",
        classification_scheme="de:hamburg:hvs",
        value="hvs",
        direction="both",
        evidence_refs=(hvs,),
        status="pass",
        reason="reviewed HVS feature to HH-SIB link mapping",
    )

    projection = project_road_detail_evidence((relation,), (assignment,))

    record = projection["by_way_id"]["101"]
    assert record["source_sha256s"] == [SHA_A, SHA_B, SHA_C]
    assertion = record["official_property_assertions"][0]
    assert assertion["evidence_source_sha256s"] == [SHA_C]
    assert assertion["evidence_refs"][0]["namespace"] == "official.hamburg_hvs"
