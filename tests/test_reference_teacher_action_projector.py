from __future__ import annotations

import hashlib
import json
from pathlib import Path

from torii_sumo.core.reference_teacher_action_projector import (
    REFERENCE_TEACHER_ACTION_CONTRACTS_V2_SCHEMA,
    project_reference_teacher_actions_v2,
)


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reference_join_audit() -> dict[str, object]:
    return {
        "status": "pass",
        "tls_controller_alignment": {
            "high_confidence_movement_gap_queue": [
                {
                    "reference_tl_id": "teacher-tls",
                    "candidate_tl_id": "candidate-tls",
                    "reference_edge_id": "teacher-in",
                    "candidate_edge_id": "candidate-in",
                    "missing_direction_counts": {"l": 1},
                    "missing_direction_instance_count": 1,
                }
            ],
            "controller_groups": [
                {
                    "reference_tl_ids": ["teacher-tls"],
                    "candidate_tl_ids": ["candidate-a", "candidate-b"],
                    "reference_controller_count": 1,
                    "candidate_controller_count": 2,
                    "controlled_connection_delta": 3,
                }
            ],
        },
        "tls_control_review_queue": [
            {
                "repair_category": "tls_controller_cardinality_repair",
                "review_type": "split_multi_junction_tls",
                "tl_id": "candidate-tls",
                "junction_ids": ["j1", "j2"],
                "controlled_connection_count": 8,
            },
            {
                "repair_category": "tls_linkindex_phase_repair",
                "review_type": "inspect_sparse_linkindex",
                "tl_id": "candidate-tls",
                "controlled_linkindex_count": 3,
                "phase_state_length": 5,
                "linkindexes": [0, 2, 4],
            },
        ],
        "junction_pattern_comparisons": [
            {
                "junction_id": "j-pattern",
                "status": "fail",
                "mismatch_fields": ["movement_signature_counts", "internal_function_counts"],
                "teacher": {
                    "movement_signature_counts": {"0>1:l": 1},
                    "internal_function_counts": {"crossing": 2, "walkingarea": 1},
                },
                "candidate": {
                    "movement_signature_counts": {},
                    "internal_function_counts": {"crossing": 0, "walkingarea": 0},
                },
            }
        ],
        "network_structural_missing_counts": {"crossing_edge_count": 4},
        "network_structural_extra_counts": {"walkingarea_edge_count": 2},
    }


def _join_contracts() -> dict[str, object]:
    return {
        "schema": "torii.reference_teacher_action_contracts.v1",
        "status": "pass",
        "actions": [
            {
                "reference_id": "cluster_1_2",
                "action_family": "bounded_conflict_core_join",
                "teacher_action": {
                    "absorbed_source_node_ids": ["1", "2"],
                    "absorbed_internal_edge_ids": ["micro"],
                    "retained_boundary_edge_ids": ["in", "out"],
                    "reference_approach_edge_ids": ["in", "out"],
                },
            }
        ],
    }


def _hierarchy_audit() -> dict[str, object]:
    return {
        "status": "blocked",
        "candidate_cases": [
            {
                "candidate_edge_id": "candidate-road",
                "candidate_edge_type": "highway.primary",
                "same_id_reference_edge_type": "highway.secondary",
                "nearest_any_reference_edge_type": "highway.secondary",
                "hierarchy_decision": "type_hierarchy_mismatch",
                "recommended_action": "copy_same_edge_id_reference_hierarchy",
            },
            {
                "candidate_edge_id": "already-aligned",
                "hierarchy_decision": "aligned",
                "recommended_action": "keep",
            },
        ],
    }


def _scope_audit() -> dict[str, object]:
    return {
        "status": "blocked",
        "prune_candidates": [
            {
                "edge_id": "detail-fragment",
                "edge_type": "highway.service",
                "length_m": 12.5,
                "scope_decision": "absent_in_reference",
                "prune_decision": "prune_candidate",
            }
        ],
    }


def test_projects_all_existing_teacher_findings_as_blocked_typed_reviews(tmp_path: Path) -> None:
    join_audit = _write_json(tmp_path / "reference-join.json", _reference_join_audit())
    join_contracts = _write_json(tmp_path / "join-actions.json", _join_contracts())
    hierarchy = _write_json(tmp_path / "hierarchy.json", _hierarchy_audit())
    scope = _write_json(tmp_path / "scope.json", _scope_audit())
    output = tmp_path / "projected" / "teacher-actions-v2.json"

    report = project_reference_teacher_actions_v2(
        reference_join_audit_file=join_audit,
        join_action_contracts_file=join_contracts,
        reference_hierarchy_audit_file=hierarchy,
        reference_scope_audit_file=scope,
        output_file=output,
    )

    assert report["schema"] == REFERENCE_TEACHER_ACTION_CONTRACTS_V2_SCHEMA
    assert report["status"] == "pass"
    assert report["projection_mode"] == "read_only"
    assert report["promotion_gate_status"] == "blocked"
    assert report["action_count"] == 10
    assert report["action_type_counts"] == {
        "edge_type_review": 1,
        "junction_join_review": 1,
        "junction_pattern_review": 1,
        "movement_gap_review": 1,
        "pedestrian_internal_delta_review": 2,
        "scope_pruning_review": 1,
        "tls_controller_scope_review": 2,
        "tls_linkindex_review": 1,
    }
    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert report["input_artifacts"]["reference_join_audit"]["sha256"] == _sha256(join_audit)
    for action in report["actions"]:
        assert action["action_id"].startswith("teacher-v2-")
        assert len(action["input_artifact"]["sha256"]) == 64
        assert action["evidence_field"].startswith("/")
        assert action["materialization_status"] == "not_authorized"
        assert action["transfer_gate_status"] == "blocked"
        assert len(action["target_city_requirements"]) >= 4

    join_action = next(action for action in report["actions"] if action["action_type"] == "junction_join_review")
    assert join_action["target_scope"]["absorbed_source_node_ids"] == ["1", "2"]
    assert "shared TLS ownership" in " ".join(join_action["target_city_requirements"])


def test_missing_required_reference_audit_fails_closed_and_emits_no_actions(tmp_path: Path) -> None:
    output = tmp_path / "blocked.json"

    report = project_reference_teacher_actions_v2(
        reference_join_audit_file=tmp_path / "missing.json",
        output_file=output,
    )

    assert report["status"] == "blocked"
    assert report["promotion_gate_status"] == "blocked"
    assert report["action_count"] == 0
    assert report["actions"] == []
    assert "does not exist" in report["blockers"][0]
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_malformed_supplied_optional_artifact_blocks_whole_projection(tmp_path: Path) -> None:
    join_audit = _write_json(tmp_path / "reference-join.json", _reference_join_audit())
    malformed_scope = _write_json(tmp_path / "scope.json", {"status": "blocked", "prune_candidates": {}})

    report = project_reference_teacher_actions_v2(
        reference_join_audit_file=join_audit,
        reference_scope_audit_file=malformed_scope,
    )

    assert report["status"] == "blocked"
    assert report["action_count"] == 0
    assert report["actions"] == []
    assert report["blockers"] == ["reference_scope_audit is incompatible: prune_candidates must be an array"]


def test_omitted_optional_layers_are_recorded_and_never_inferred(tmp_path: Path) -> None:
    join_audit = _write_json(tmp_path / "reference-join.json", _reference_join_audit())

    report = project_reference_teacher_actions_v2(reference_join_audit_file=join_audit)

    assert report["status"] == "pass"
    assert report["input_artifacts"]["join_action_contracts_v1"]["status"] == "not_supplied"
    assert report["input_artifacts"]["reference_hierarchy_audit"]["status"] == "not_supplied"
    assert report["input_artifacts"]["reference_scope_audit"]["status"] == "not_supplied"
    assert "junction_join_review" not in report["action_type_counts"]
    assert "edge_type_review" not in report["action_type_counts"]
    assert "scope_pruning_review" not in report["action_type_counts"]


def test_projection_is_deterministic_for_identical_artifacts(tmp_path: Path) -> None:
    join_audit = _write_json(tmp_path / "reference-join.json", _reference_join_audit())
    join_contracts = _write_json(tmp_path / "join-actions.json", _join_contracts())

    first = project_reference_teacher_actions_v2(
        reference_join_audit_file=join_audit,
        join_action_contracts_file=join_contracts,
    )
    second = project_reference_teacher_actions_v2(
        reference_join_audit_file=join_audit,
        join_action_contracts_file=join_contracts,
    )

    assert first == second
    assert [action["action_id"] for action in first["actions"]] == [
        action["action_id"] for action in second["actions"]
    ]


def test_clean_audit_produces_no_speculative_actions(tmp_path: Path) -> None:
    join_audit = _write_json(
        tmp_path / "reference-join.json",
        {
            "status": "pass",
            "tls_controller_alignment": {
                "high_confidence_movement_gap_queue": [],
                "controller_groups": [],
            },
            "tls_control_review_queue": [],
            "junction_pattern_comparisons": [],
            "network_structural_missing_counts": {},
            "network_structural_extra_counts": {},
        },
    )

    report = project_reference_teacher_actions_v2(reference_join_audit_file=join_audit)

    assert report["status"] == "pass"
    assert report["action_count"] == 0
    assert report["actions"] == []
    assert report["promotion_gate_status"] == "blocked"
