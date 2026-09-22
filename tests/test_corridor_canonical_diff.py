from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from torii_sumo.corridor.canonicalizer import canonicalize_raw_network
from torii_sumo.corridor.calibration import (
    ConnectionAuditCalibration,
    ConnectionAuditCalibrationPolicy,
)
from torii_sumo.corridor.audit_pipeline import (
    build_exact_semantic_regression_artifacts,
)
from torii_sumo.corridor.audit_adapter import (
    build_scope_from_junction_ids,
    canonicalize_connection_mode_findings,
    finding_category_counts,
)
from torii_sumo.corridor.enums import FindingSeverity, GateStatus, TrafficSide
from torii_sumo.corridor.exact_diff import build_finding, compare_canonical_snapshots
from torii_sumo.corridor.ids import stable_id
from torii_sumo.corridor.netxml import parse_net_xml
from torii_sumo.corridor.scope import BoundaryPort, ScopeSpec
from torii_sumo.core.candidate_contracts import file_sha256


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _network_xml(
    *,
    internal_edge_id: str = ":j_0",
    internal_lane_id: str = ":j_0_0",
    tls_id: str = "tls0",
    reverse_connections: bool = False,
    incoming_near_x: float = -1.0,
) -> str:
    direct = (
        f'<connection from="in" to="out" fromLane="0" toLane="0" '
        f'via="{internal_lane_id}" dir="s" state="O" tl="{tls_id}" linkIndex="0"/>'
    )
    internal = (
        f'<connection from="{internal_edge_id}" to="out" '
        'fromLane="0" toLane="0" dir="s" state="M"/>'
    )
    connections = internal + direct if reverse_connections else direct + internal
    return f"""<net>
  <edge id="in" from="a" to="j" type="highway.primary" priority="3">
    <lane id="in_0" index="0" speed="13.9" length="100" width="3.2" allow="passenger"
          shape="-100,0 -50,0 {incoming_near_x},0"/>
    <param key="origId" value="osm-way-1"/>
  </edge>
  <edge id="out" from="j" to="b" type="highway.primary" priority="3">
    <lane id="out_0" index="0" speed="13.9" length="100" width="3.2" allow="passenger"
          shape="1,0 50,0 100,0"/>
    <param key="origId" value="osm-way-2"/>
  </edge>
  <edge id="{internal_edge_id}" function="internal">
    <lane id="{internal_lane_id}" index="0" speed="13.9" length="2" width="3.2"
          shape="-1,0 1,0"/>
  </edge>
  <junction id="a" type="dead_end" x="-100" y="0" incLanes="" intLanes=""/>
  <junction id="j" type="traffic_light" x="0" y="0"
            incLanes="in_0" intLanes="{internal_lane_id}">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <junction id="b" type="dead_end" x="100" y="0" incLanes="out_0" intLanes=""/>
  {connections}
  <tlLogic id="{tls_id}" type="static" programID="0" offset="0">
    <phase duration="30" state="G"/>
    <phase duration="4" state="y"/>
    <phase duration="2" state="r"/>
  </tlLogic>
</net>"""


def _snapshot(xml: str):
    return canonicalize_raw_network(
        parse_net_xml(ET.fromstring(xml)),
        traffic_side=TrafficSide.RIGHT,
    )


def _unrelated_scope() -> ScopeSpec:
    port_id = stable_id("port", {"scope": "unrelated"})
    lane_role_id = stable_id("lane_role", {"scope": "unrelated"})
    return ScopeSpec(
        scope_id=stable_id("scope", {"scope": "unrelated"}),
        physical_cell_ids=frozenset({stable_id("cell", {"scope": "unrelated"})}),
        target_entity_ids=frozenset({stable_id("movement", {"scope": "unrelated"})}),
        guard_entity_ids=frozenset(),
        closure_rules=("explicit-target-only",),
        boundary_ports=(
            BoundaryPort(
                boundary_port_id=port_id,
                center_xy=(0.0, 0.0),
                tangent_xy=(1.0, 0.0),
                normal_xy=(0.0, 1.0),
                lane_role_ids=(lane_role_id,),
                lane_widths_m=(3.2,),
                mode_permissions={lane_role_id: frozenset({"passenger"})},
                source_anchor_refs=("unrelated",),
                source_geometry_sha256="c" * 64,
                traffic_side=TrafficSide.RIGHT,
            ),
        ),
        traffic_side=TrafficSide.RIGHT,
    )


def _semantic_index(snapshot) -> dict[tuple[str, str], str]:
    return {
        (entity.kind, entity.stable_entity_id): entity.semantic_signature
        for entity in snapshot.entities
    }


def test_internal_and_tls_raw_id_renumbering_has_zero_semantic_delta() -> None:
    source = _snapshot(_network_xml())
    candidate = _snapshot(
        _network_xml(
            internal_edge_id=":renumbered_42",
            internal_lane_id=":renumbered_42_0",
            tls_id="controller-renumbered",
            reverse_connections=True,
        )
    )

    assert _semantic_index(source) == _semantic_index(candidate)
    report = compare_canonical_snapshots(
        source,
        candidate,
        scope=_unrelated_scope(),
    )
    assert report.status is GateStatus.PASS
    assert report.entity_deltas == ()


def test_canonical_snapshot_declares_independent_safety_coverage() -> None:
    snapshot = _snapshot(_network_xml())
    coverage = next(
        entity for entity in snapshot.entities if entity.kind == "safety_coverage"
    )

    assert coverage.payload["canonical_movement_count"] == 1
    assert coverage.payload["controlled_connection_count"] == 1
    assert coverage.payload["unsupported_controlled_connection_count"] == 0
    assert coverage.payload["movement_mode_class_counts"] == {"road-motorized": 1}


def test_near_junction_geometry_change_preserves_port_identity_but_changes_lane_signature() -> None:
    source = _snapshot(_network_xml(incoming_near_x=-1.0))
    candidate = _snapshot(_network_xml(incoming_near_x=-4.0))
    source_ports = source.raw_id_maps["edge_flow_to_boundary_port"]
    candidate_ports = candidate.raw_id_maps["edge_flow_to_boundary_port"]

    assert source_ports == candidate_ports
    report = compare_canonical_snapshots(
        source,
        candidate,
        scope=_unrelated_scope(),
    )
    lane_deltas = [
        delta
        for delta in report.entity_deltas
        if delta.entity_kind == "lane_role"
    ]
    assert lane_deltas
    assert report.status is GateStatus.BLOCKED
    assert "outside_scope_exact_semantic_delta" in report.blockers


def test_exact_finding_diff_detects_equal_count_witness_substitution() -> None:
    snapshot = _snapshot(_network_xml())
    source_subject = stable_id("movement", {"outside": "source"})
    candidate_subject = stable_id("movement", {"outside": "candidate"})
    source_finding = build_finding(
        category="lane_rank_jump",
        severity=FindingSeverity.REVIEW,
        subject_id=source_subject,
        witness={"normalized_rank_delta": 0.75},
        confidence=0.8,
    )
    candidate_finding = build_finding(
        category="lane_rank_jump",
        severity=FindingSeverity.REVIEW,
        subject_id=candidate_subject,
        witness={"normalized_rank_delta": 0.75},
        confidence=0.8,
    )

    assert source_finding.category == candidate_finding.category
    report = compare_canonical_snapshots(
        snapshot,
        snapshot,
        scope=_unrelated_scope(),
        source_findings=(source_finding,),
        candidate_findings=(candidate_finding,),
    )

    assert len(report.finding_delta.resolved) == 1
    assert len(report.finding_delta.added) == 1
    assert report.status is GateStatus.BLOCKED
    assert report.outside_scope_added_finding_ids == (candidate_finding.finding_id,)


def test_identical_finding_witness_is_unchanged() -> None:
    snapshot = _snapshot(_network_xml())
    finding = build_finding(
        category="lane_rank_jump",
        severity=FindingSeverity.REVIEW,
        subject_id=stable_id("movement", {"outside": "same"}),
        witness={"normalized_rank_delta": 0.75},
        confidence=0.8,
    )

    report = compare_canonical_snapshots(
        snapshot,
        snapshot,
        scope=_unrelated_scope(),
        source_findings=(finding,),
        candidate_findings=(finding,),
    )

    assert report.finding_delta.added == ()
    assert report.finding_delta.resolved == ()
    assert report.finding_delta.unchanged_finding_ids == (finding.finding_id,)
    assert report.status is GateStatus.PASS


def test_canonicalization_fails_closed_without_traffic_side() -> None:
    network = parse_net_xml(ET.fromstring(_network_xml()))

    with pytest.raises(ValueError, match="traffic side"):
        canonicalize_raw_network(network, traffic_side=TrafficSide.UNKNOWN)


def test_canonicalization_blocks_traffic_side_that_contradicts_network() -> None:
    left_hand_xml = _network_xml().replace("<net>", '<net lefthand="true">', 1)
    network = parse_net_xml(ET.fromstring(left_hand_xml))

    snapshot = canonicalize_raw_network(network, traffic_side=TrafficSide.LEFT)
    assert snapshot.traffic_side is TrafficSide.LEFT
    with pytest.raises(ValueError, match="contradicts"):
        canonicalize_raw_network(network, traffic_side=TrafficSide.RIGHT)


def test_string_finding_adapter_ignores_connection_index_renumbering() -> None:
    source = _snapshot(_network_xml())
    candidate = _snapshot(
        _network_xml(
            internal_edge_id=":renumbered_42",
            internal_lane_id=":renumbered_42_0",
            tls_id="controller-renumbered",
            reverse_connections=True,
        )
    )
    source_findings = canonicalize_connection_mode_findings(
        {
            "junctions": [
                {
                    "junction_id": "j",
                    "connection_mode_audit": {
                        "structural_failures": [
                            "path_endpoint_gap:0:entry:3.000m"
                        ],
                        "review_findings": [],
                    },
                    "tls_link_binding_audit": {
                        "structural_failures": [],
                        "review_findings": [],
                    },
                }
            ],
            "tls_link_binding_audit": {
                "structural_failures": [],
                "review_findings": [],
            },
        },
        source,
    )
    candidate_findings = canonicalize_connection_mode_findings(
        {
            "junctions": [
                {
                    "junction_id": "j",
                    "connection_mode_audit": {
                        "structural_failures": [
                            "path_endpoint_gap:1:entry:3.000m"
                        ],
                        "review_findings": [],
                    },
                    "tls_link_binding_audit": {
                        "structural_failures": [],
                        "review_findings": [],
                    },
                }
            ],
            "tls_link_binding_audit": {
                "structural_failures": [],
                "review_findings": [],
            },
        },
        candidate,
    )

    assert finding_category_counts(source_findings) == {"path_endpoint_gap": 1}
    assert source_findings == candidate_findings


def test_raw_junction_scope_expands_to_stable_target_entity_closure() -> None:
    source = _snapshot(_network_xml(incoming_near_x=-1.0))
    candidate = _snapshot(_network_xml(incoming_near_x=-4.0))
    scope = build_scope_from_junction_ids(
        source,
        candidate,
        target_source_junction_ids=("j",),
        target_candidate_junction_ids=("j",),
    )

    assert scope.physical_cell_ids
    assert scope.boundary_ports
    assert all(
        port.traffic_side is TrafficSide.RIGHT
        for port in scope.boundary_ports
    )
    report = compare_canonical_snapshots(source, candidate, scope=scope)
    assert report.outside_scope_delta_ids == ()
    assert report.status is GateStatus.PASS


def test_stage1_pipeline_emits_hash_closed_read_only_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text(_network_xml(), encoding="utf-8")
    candidate.write_text(
        _network_xml(
            internal_edge_id=":renumbered_42",
            internal_lane_id=":renumbered_42_0",
            tls_id="controller-renumbered",
            reverse_connections=True,
        ),
        encoding="utf-8",
    )
    source_sha256 = file_sha256(source)

    result = build_exact_semantic_regression_artifacts(
        source,
        candidate,
        output_dir=tmp_path / "audit",
        toolchain_lock_file=(
            REPOSITORY_ROOT
            / "benchmarks/corridor_human_modeling_v1/toolchain.lock.json"
        ),
        traffic_side=TrafficSide.RIGHT,
        target_source_junction_ids=("j",),
        target_candidate_junction_ids=("j",),
        endpoint_tolerance_m=2.0,
        normalized_lane_rank_tolerance=0.5,
    )

    assert result["status"] == "pass"
    assert result["automatic_promotion_gate"] == "pass"
    assert result["entity_delta_count"] == 0
    assert result["new_finding_count"] == 0
    assert result["source_network_mutation"] is False
    assert file_sha256(source) == source_sha256
    manifest = json.loads(
        Path(result["files"]["manifest"]).read_text(encoding="utf-8")
    )
    assert manifest["source_mutated"] is False
    assert manifest["gate_trace"]["exact_semantic_diff"] == "pass"
    assert result["candidate_safety_finding_count"] == 0
    assert len(manifest["artifacts"]) == 11
    assert len(manifest["dependencies"]) == 11


def test_stage1_pipeline_rejects_candidate_with_source_content(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    payload = _network_xml()
    source.write_text(payload, encoding="utf-8")
    candidate.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match="contents must be distinct"):
        build_exact_semantic_regression_artifacts(
            source,
            candidate,
            output_dir=tmp_path / "audit",
            toolchain_lock_file=(
                REPOSITORY_ROOT
                / "benchmarks/corridor_human_modeling_v1/toolchain.lock.json"
            ),
            traffic_side=TrafficSide.RIGHT,
            target_source_junction_ids=("j",),
            target_candidate_junction_ids=("j",),
            endpoint_tolerance_m=2.0,
            normalized_lane_rank_tolerance=0.5,
        )


def test_stage1_pipeline_binds_a_passing_source_calibration(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text(_network_xml(), encoding="utf-8")
    candidate.write_text(
        _network_xml(
            internal_edge_id=":renumbered_42",
            internal_lane_id=":renumbered_42_0",
            tls_id="controller-renumbered",
            reverse_connections=True,
        ),
        encoding="utf-8",
    )
    policy = ConnectionAuditCalibrationPolicy.build(minimum_endpoint_samples=1)
    calibration = ConnectionAuditCalibration(
        calibration_id=stable_id("calibration", {"source": file_sha256(source)}),
        source_sha256=file_sha256(source),
        traffic_side=TrafficSide.RIGHT,
        policy=policy,
        status=GateStatus.PASS,
        endpoint_path_count=1,
        endpoint_sample_count=2,
        rejected_path_count=0,
        coordinate_precision_m=0.01,
        coordinate_precision_evidence="serialized_lane_shape_decimals",
        median_lane_width_m=3.2,
        lane_width_evidence="locked_sumo_default_lane_width",
        observed_gap_quantile_m=0.0,
        maximum_observed_gap_m=0.0,
        lane_width_cap_m=0.8,
        endpoint_tolerance_m=0.02,
    )
    calibration_file = tmp_path / "source.calibration.json"
    calibration_file.write_text(
        calibration.model_dump_json(by_alias=True),
        encoding="utf-8",
    )

    result = build_exact_semantic_regression_artifacts(
        source,
        candidate,
        output_dir=tmp_path / "audit",
        toolchain_lock_file=(
            REPOSITORY_ROOT
            / "benchmarks/corridor_human_modeling_v1/toolchain.lock.json"
        ),
        traffic_side=TrafficSide.RIGHT,
        target_source_junction_ids=("j",),
        target_candidate_junction_ids=("j",),
        calibration_file=calibration_file,
    )

    assert result["status"] == "pass"
    assert result["tolerance_provenance"] == "hash_bound_calibration"
    assert result["endpoint_tolerance_m"] == 0.02
    manifest = json.loads(Path(result["files"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["gate_trace"]["connection_audit_calibration"] == "pass"
    assert len(manifest["artifacts"]) == 12
    assert len(manifest["dependencies"]) == 13
