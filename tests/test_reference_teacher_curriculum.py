from __future__ import annotations

import json
from pathlib import Path

from torii_sumo.core.reference_teacher_curriculum import (
    build_reference_teacher_curriculum,
)


def _write_net(path: Path, *, teacher: bool) -> None:
    if teacher:
        path.write_text(
            '<net><location netOffset="-90,-180" projParameter="+proj=utm +zone=32"/>'
            '<junction id="cluster_1_2" type="traffic_light" x="21" y="30" '
            'incLanes="in_0 in_1" shape="19,19 21,19 21,21 19,21"/>'
            '<edge id="in" from="outside" to="cluster_1_2" type="highway.primary">'
            '<lane id="in_0" speed="13.9"/><lane id="in_1" speed="13.9"/></edge>'
            '<connection from="in" to="out" fromLane="0" toLane="0" '
            'tl="cluster_1_2" linkIndex="0"/></net>',
            encoding="utf-8",
        )
    else:
        path.write_text(
            '<net><location netOffset="-100,-200" projParameter="+proj=utm +zone=32"/>'
            '<junction id="1" type="traffic_light" x="10" y="10"/>'
            '<junction id="2" type="priority" x="12" y="10"/>'
            '<edge id="in" from="outside" to="1" type="highway.primary">'
            '<lane id="in_0" speed="13.9"/></edge>'
            '<edge id="core" from="1" to="2" type="highway.primary">'
            '<lane id="core_0" speed="13.9"/></edge></net>',
            encoding="utf-8",
        )


def test_builds_spatially_grouped_teacher_state_and_screenshot_targets(tmp_path: Path) -> None:
    raw = tmp_path / "raw.net.xml"
    teacher = tmp_path / "teacher.net.xml"
    _write_net(raw, teacher=False)
    _write_net(teacher, teacher=True)
    topology = tmp_path / "topology.json"
    topology.write_text(
        json.dumps(
            {
                "suspicious_clusters": [
                    {
                        "cluster_id": "C001",
                        "centroid_x": 11,
                        "centroid_y": 10,
                        "node_count": 2,
                        "node_ids": ["1", "2"],
                        "internal_edge_ids": ["core"],
                        "boundary_edge_ids": ["in"],
                        "internal_edge_count": 1,
                        "boundary_edge_count": 1,
                        "approach_count": 3,
                        "traffic_light_node_count": 1,
                        "physical_intersection_shape": "t_or_y",
                        "physical_intersection_score": 0.9,
                        "approach_axis_count": 3,
                        "internal_edge_overlap_pair_count": 0,
                        "aggregation_decision": "needs_map_review",
                        "aggregation_confidence": "medium",
                        "modal_primary_role": "vehicle_core",
                        "risk_flags": ["pedestrian_support"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    audit = tmp_path / "audit.json"
    audit.write_text(
        json.dumps(
            {
                "candidate_net_file": str(raw),
                "candidate_topology_audit_file": str(topology),
                "all_cases": [
                    {
                        "reference_id": "cluster_1_2",
                        "reference_type": "traffic_light",
                        "reference_x": 21,
                        "reference_y": 30,
                        "matched_candidate_cluster_id": "C001",
                        "matched_candidate_node_ids": ["1", "2"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    actions = tmp_path / "actions.json"
    actions.write_text(
        json.dumps(
            {
                "schema": "torii.reference_teacher_action_contracts.v1",
                "actions": [
                    {
                        "reference_id": "cluster_1_2",
                        "action_family": "bounded_conflict_core_join",
                        "teacher_action": {
                            "absorbed_source_node_ids": ["1", "2"],
                            "absorbed_internal_edge_ids": ["core"],
                            "retained_boundary_edge_ids": ["in"],
                        },
                        "counterexample_evidence": {"blockers": []},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_reference_teacher_curriculum(
        teacher_action_contracts_file=actions,
        reference_join_audit_file=audit,
        raw_net_file=raw,
        teacher_net_file=teacher,
        output_dir=tmp_path / "out",
        spatial_tile_m=100,
        screenshot_case_limit=2,
    )

    assert report["status"] == "review_ready"
    assert report["case_count"] == 1
    case = report["cases"][0]
    assert case["input_state"]["physical_intersection_shape"] == "t_or_y"
    assert case["input_state"]["boundary_road_type_counts"] == {"highway.primary": 1}
    assert case["human_cleaned_state"]["controlled_connection_count"] == 1
    assert case["review_targets"]["raw"]["junction_id"] in {"1", "2"}
    assert case["review_targets"]["human_cleaned"]["junction_id"] == "cluster_1_2"
    assert case["spatial_registration"]["aligned_view"]["projected_center"] == [111.0, 210.0]
    assert case["review_targets"]["raw"]["view_center"] == [11.0, 10.0]
    assert case["review_targets"]["human_cleaned"]["view_center"] == [21.0, 30.0]
    assert report["promotion_gate_status"] == "blocked"
    assert Path(report["report_file"]).is_file()
    assert Path(report["manifest_file"]).is_file()


def test_spatial_tile_keeps_neighboring_cases_in_one_split(tmp_path: Path) -> None:
    raw = tmp_path / "raw.net.xml"
    teacher = tmp_path / "teacher.net.xml"
    raw.write_text(
        '<net><location netOffset="0,0" projParameter="+proj=utm +zone=32"/>'
        '<junction id="1" x="10" y="10"/><junction id="2" x="11" y="11"/></net>',
        encoding="utf-8",
    )
    teacher.write_text(
        '<net><location netOffset="10,10" projParameter="+proj=utm +zone=32"/>'
        '<junction id="cluster_1" x="20" y="20"/>'
        '<junction id="cluster_2" x="21" y="21"/></net>',
        encoding="utf-8",
    )
    topology = tmp_path / "topology.json"
    topology.write_text(
        json.dumps(
            {
                "suspicious_clusters": [
                    {"cluster_id": "C1", "centroid_x": 10, "centroid_y": 10, "node_count": 2},
                    {"cluster_id": "C2", "centroid_x": 11, "centroid_y": 11, "node_count": 2},
                ]
            }
        ),
        encoding="utf-8",
    )
    audit = tmp_path / "audit.json"
    audit.write_text(
        json.dumps(
            {
                "candidate_net_file": str(raw),
                "candidate_topology_audit_file": str(topology),
                "all_cases": [
                    {"reference_id": "cluster_1", "matched_candidate_cluster_id": "C1", "matched_candidate_node_ids": ["1"]},
                    {"reference_id": "cluster_2", "matched_candidate_cluster_id": "C2", "matched_candidate_node_ids": ["2"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    actions = tmp_path / "actions.json"
    actions.write_text(
        json.dumps(
            {
                "schema": "torii.reference_teacher_action_contracts.v1",
                "actions": [
                    {
                        "reference_id": "cluster_1",
                        "action_family": "source_identity_join_review",
                        "teacher_action": {"absorbed_source_node_ids": ["1"]},
                    },
                    {
                        "reference_id": "cluster_2",
                        "action_family": "source_identity_join_review",
                        "teacher_action": {"absorbed_source_node_ids": ["2"]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_reference_teacher_curriculum(
        teacher_action_contracts_file=actions,
        reference_join_audit_file=audit,
        raw_net_file=raw,
        teacher_net_file=teacher,
        output_dir=tmp_path / "out",
        spatial_tile_m=100,
    )

    assert {case["spatial_group_id"] for case in report["cases"]} == {"tile_0_0"}
    assert len({case["dataset_split"] for case in report["cases"]}) == 1
