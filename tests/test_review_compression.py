from __future__ import annotations

from xml.etree import ElementTree as ET

from torii_sumo.corridor.canonicalizer import canonicalize_raw_network
from torii_sumo.corridor.conflict_graph import (
    audit_independent_movement_safety,
)
from torii_sumo.corridor.enums import GateStatus, TrafficSide
from torii_sumo.corridor.ids import stable_id
from torii_sumo.corridor.netxml import parse_net_xml
from torii_sumo.corridor.review_compression import (
    build_lossless_review_compression,
)


_SOURCE_OSM_SHA256 = "1" * 64
_CANDIDATE_NET_SHA256 = "2" * 64
_TOOLCHAIN_ID = stable_id("toolchain", {"test": "rwc-1"})


def _rwc_snapshot():
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j" type="primary">
    <lane id="in_0" index="0" allow="passenger" speed="13.9" width="3.2" shape="-10,-0.4 -1,-0.4"/>
    <lane id="in_1" index="1" allow="passenger" speed="13.9" width="3.2" shape="-10,0.4 -1,0.4"/>
    <lane id="in_2" index="2" allow="passenger" speed="13.9" width="3.2" shape="-10,3.8 -1,3.8"/>
    <lane id="in_3" index="3" allow="passenger" speed="13.9" width="3.2" shape="-10,8 -1,8"/>
  </edge>
  <edge id="out" from="j" to="b" type="primary">
    <lane id="out_0" index="0" allow="passenger" speed="13.9" width="3.2" shape="1,-0.4 10,-0.4"/>
    <lane id="out_1" index="1" allow="passenger" speed="13.9" width="3.2" shape="1,0.4 10,0.4"/>
    <lane id="out_2" index="2" allow="passenger" speed="13.9" width="3.2" shape="1,3.8 10,3.8"/>
    <lane id="out_3" index="3" allow="passenger" speed="13.9" width="3.2" shape="1,8 10,8"/>
  </edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" allow="passenger" speed="13.9" width="3.2" shape="-1,-0.4 1,-0.4"/></edge>
  <edge id=":j_1" function="internal"><lane id=":j_1_0" index="0" allow="passenger" speed="13.9" width="3.2" shape="-1,0.4 1,0.4"/></edge>
  <edge id=":j_2" function="internal"><lane id=":j_2_0" index="0" allow="passenger" speed="13.9" width="3.2" shape="-1,3.8 1,3.8"/></edge>
  <edge id=":j_3" function="internal"><lane id=":j_3_0" index="0" allow="passenger" speed="13.9" width="3.2" shape="-1,8 1,8"/></edge>
  <edge id=":j_c0" function="crossing" crossingEdges="in out"><lane id=":j_c0_0" index="0" allow="pedestrian" width="4" shape="0,-2 0,2"/></edge>
  <edge id=":j_w1" function="walkingarea"><lane id=":j_w1_0" index="0" allow="pedestrian" width="4" shape="-2,-2 2,-2"/></edge>
  <edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0" allow="pedestrian" width="4" shape="-2,2 2,2"/></edge>
  <junction id="a" type="dead_end" incLanes="" intLanes=""/>
  <junction id="j" type="priority" incLanes="in_0 in_1 in_2 in_3 :j_w1_0" intLanes=":j_0_0 :j_1_0 :j_2_0 :j_3_0 :j_c0_0">
    <request index="0" response="00000" foes="00000" cont="0"/>
    <request index="1" response="00000" foes="00000" cont="0"/>
    <request index="2" response="00000" foes="00000" cont="0"/>
    <request index="3" response="00000" foes="00000" cont="0"/>
    <request index="4" response="00000" foes="00000" cont="0"/>
  </junction>
  <junction id="b" type="dead_end" incLanes="out_0 out_1 out_2 out_3" intLanes=""/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" dir="s" state="M"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s" state="M"/>
  <connection from="in" to="out" fromLane="1" toLane="1" via=":j_1_0" dir="s" state="M"/>
  <connection from=":j_1" to="out" fromLane="0" toLane="1" dir="s" state="M"/>
  <connection from="in" to="out" fromLane="2" toLane="2" via=":j_2_0" dir="s" state="M"/>
  <connection from=":j_2" to="out" fromLane="0" toLane="2" dir="s" state="M"/>
  <connection from="in" to="out" fromLane="3" toLane="3" via=":j_3_0" dir="s" state="M"/>
  <connection from=":j_3" to="out" fromLane="0" toLane="3" dir="s" state="M"/>
  <connection from=":j_w1" to=":j_c0" fromLane="0" toLane="0" dir="s" state="M"/>
  <connection from=":j_c0" to=":j_w0" fromLane="0" toLane="0" dir="s" state="M"/>
</net>"""
    )
    return canonicalize_raw_network(
        parse_net_xml(root),
        traffic_side=TrafficSide.RIGHT,
    )


def _build_report():
    snapshot = _rwc_snapshot()
    safety = audit_independent_movement_safety(snapshot)
    report = build_lossless_review_compression(
        snapshot,
        safety,
        source_osm_sha256=_SOURCE_OSM_SHA256,
        candidate_net_sha256=_CANDIDATE_NET_SHA256,
        toolchain_id=_TOOLCHAIN_ID,
        sampling_seed="rwc-1-frozen-seed",
        corridor_morphology="synthetic-standard-crossing",
    )
    return snapshot, safety, report


def test_rwc_atomic_ledger_is_lossless_and_strictly_partitioned() -> None:
    _, safety, report = _build_report()

    assert report.ledger.witness_count == 3
    assert report.ledger.confirmed_count == 2
    assert report.ledger.potential_count == 1
    assert report.ledger.source_finding_count == 3
    assert len(safety.conflict_graph.conflicts) == report.ledger.witness_count
    assert report.atomic_membership_coverage == 1.0
    assert report.lost_witness_count == 0
    assert report.duplicate_membership_count == 0
    assert report.extraneous_membership_count == 0
    assert report.mixed_hard_key_violation_count == 0
    assert report.machine_review_ready_gate is GateStatus.PASS
    assert report.automatic_promotion_gate is GateStatus.BLOCKED

    assert len(report.clusters) == 2
    confirmed = next(
        cluster for cluster in report.clusters if cluster.key.certainty == "confirmed"
    )
    potential = next(
        cluster for cluster in report.clusters if cluster.key.certainty == "potential"
    )
    assert confirmed.membership_count == 2
    assert confirmed.hidden_witness_independent is True
    assert confirmed.hidden_witness_id not in (
        confirmed.visible_representative_witness_ids
    )
    assert potential.membership_count == 1
    assert potential.hidden_witness_independent is False
    assert confirmed.key.request_foes_mapping_status == "unmapped"
    assert (
        confirmed.key.request_foes_relation
        == potential.key.request_foes_relation
    )
    assert confirmed.key.grade_evidence_signature


def test_rwc_site_unit_negative_sample_and_sampling_weights_close() -> None:
    _, _, report = _build_report()

    assert len(report.site_review_cases) == 1
    site = report.site_review_cases[0]
    assert site.witness_count == 3
    assert set(site.cluster_ids) == {cluster.cluster_id for cluster in report.clusters}

    assert report.population_strata
    assert all(stratum.census_required for stratum in report.population_strata)
    assert all(
        stratum.inclusion_probability == 1.0
        for stratum in report.population_strata
    )
    assert sum(
        stratum.selected_count for stratum in report.negative_pair_strata
    ) == 1
    negative = report.negative_pair_strata[0]
    assert negative.population_count == 1
    assert negative.inclusion_probability == 1.0
    assert negative.samples[0].stratum_id == negative.stratum_id
    assert negative.samples[0].machine_finding_absent is True


def test_rwc_is_exactly_deterministic_for_a_frozen_seed() -> None:
    _, _, first = _build_report()
    _, _, second = _build_report()

    assert first.model_dump(mode="json", by_alias=True) == second.model_dump(
        mode="json",
        by_alias=True,
    )
    assert first.report_id == second.report_id
    assert first.ledger.witness_set_signature == (
        second.ledger.witness_set_signature
    )
    assert [cluster.membership_merkle_root for cluster in first.clusters] == [
        cluster.membership_merkle_root for cluster in second.clusters
    ]
