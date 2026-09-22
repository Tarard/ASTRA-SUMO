from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from torii_sumo.corridor.candidates import (
    CandidateGraph,
    CandidateVariant,
    Hypothesis,
    PatchOperation,
    SemanticDelta,
)
from torii_sumo.corridor.benchmark import BenchmarkLock, BenchmarkSpecV1
from torii_sumo.corridor.enums import (
    ArtifactRole,
    AutomationAction,
    DeltaAction,
    FindingSeverity,
    GateStatus,
    HypothesisType,
    QualityDimensionName,
    ScopeMembership,
    TrafficSide,
    WorkflowStage,
)
from torii_sumo.corridor.evidence import Finding, InvariantResult
from torii_sumo.corridor.ids import (
    canonical_json_bytes,
    make_approach_id,
    make_boundary_port_id,
    make_lane_role_id,
    make_movement_id,
    make_physical_cell_id,
    stable_id,
)
from torii_sumo.corridor.manifest import ArtifactIdentity, ArtifactManifestV1
from torii_sumo.corridor.scope import BoundaryPort, ScopeSpec
from torii_sumo.corridor.schema import (
    build_controlled_pedestrian_binding_census_schema,
    build_corridor_schema,
    build_effective_tls_program_inventory_schema,
    build_row1_experiment_report_schema,
)
from torii_sumo.corridor.toolchain import ToolIdentity, ToolchainLock
from torii_sumo.corridor.workflow import (
    NetworkQualityVectorV1,
    QualityDimension,
    StageOutcome,
    WorkflowExecution,
    WorkflowTransition,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _semantic_fixture() -> dict[str, str]:
    port_a = make_boundary_port_id(
        source_anchor_refs=("osm-way/1",),
        source_geometry_sha256=SHA_A,
        lane_semantic_keys=({"ordinal_from_curb": 0, "role": "through"},),
        traffic_side="right",
    )
    port_b = make_boundary_port_id(
        source_anchor_refs=("osm-way/2",),
        source_geometry_sha256=SHA_B,
        lane_semantic_keys=({"ordinal_from_curb": 0, "role": "through"},),
        traffic_side="right",
    )
    cell = make_physical_cell_id(
        boundary_port_ids=(port_a, port_b),
        grade_separation_signature={"layer": 0, "bridge": False, "tunnel": False},
    )
    approach_a = make_approach_id(
        physical_cell_id=cell,
        boundary_port_id=port_a,
        flow="incoming",
    )
    approach_b = make_approach_id(
        physical_cell_id=cell,
        boundary_port_id=port_b,
        flow="outgoing",
    )
    lane_a = make_lane_role_id(
        approach_id=approach_a,
        ordinal_from_curb=0,
        role="through",
        modes=("passenger",),
        traffic_side="right",
    )
    lane_b = make_lane_role_id(
        approach_id=approach_b,
        ordinal_from_curb=0,
        role="through",
        modes=("passenger",),
        traffic_side="right",
    )
    movement = make_movement_id(
        physical_cell_id=cell,
        source_boundary_port_id=port_a,
        source_lane_role_id=lane_a,
        destination_boundary_port_id=port_b,
        destination_lane_role_id=lane_b,
        mode="passenger",
        turn_class="straight",
    )
    return {
        "port_a": port_a,
        "port_b": port_b,
        "cell": cell,
        "approach_a": approach_a,
        "approach_b": approach_b,
        "lane_a": lane_a,
        "lane_b": lane_b,
        "movement": movement,
        "scope": stable_id("scope", {"cell": cell}),
    }


def _quality_vector(status: GateStatus = GateStatus.PASS) -> NetworkQualityVectorV1:
    return NetworkQualityVectorV1(
        dimensions=tuple(
            QualityDimension(name=name, status=status)
            for name in QualityDimensionName
        )
    )


def _operation_and_delta(ids: dict[str, str]) -> tuple[PatchOperation, SemanticDelta]:
    operation = PatchOperation(
        operation_id=stable_id("operation", {"movement": ids["movement"]}),
        operation_type="replace-movement-path",
        target_ids=(ids["movement"],),
        preconditions=({"signature": stable_id("path", {"state": "before"})},),
        forward_patch={"set": {"turn": "straight"}},
        inverse_patch={"restore": {"turn": "unknown"}},
    )
    delta = SemanticDelta(
        delta_id=stable_id("delta", {"delta": ids["movement"]}),
        entity_kind="movement",
        stable_entity_id=ids["movement"],
        action=DeltaAction.MODIFIED,
        before_signature=stable_id("path", {"state": "before"}),
        after_signature=stable_id("path", {"state": "after"}),
        reason="fixed-topology experiment",
        scope_membership=ScopeMembership.TARGET,
    )
    return operation, delta


def test_stable_ids_ignore_mapping_and_set_order_but_preserve_sequence_order() -> None:
    first = canonical_json_bytes({"b": {"truck", "passenger"}, "a": [1, 2]})
    second = canonical_json_bytes({"a": [1, 2], "b": {"passenger", "truck"}})
    reversed_sequence = canonical_json_bytes({"a": [2, 1], "b": {"passenger", "truck"}})

    assert first == second
    assert first != reversed_sequence
    assert stable_id("movement", {"a": 1, "b": 2}) == stable_id(
        "movement", {"b": 2, "a": 1}
    )


def test_stable_movement_id_uses_semantic_roles_not_sumo_internal_ids() -> None:
    ids = _semantic_fixture()

    assert ids["movement"].startswith("movement_")
    assert ":cluster" not in ids["movement"]
    assert len(ids["movement"]) == len("movement_") + 24


def test_boundary_port_requires_complete_ordered_cross_section() -> None:
    ids = _semantic_fixture()
    valid = BoundaryPort(
        boundary_port_id=ids["port_a"],
        center_xy=(0.0, 0.0),
        tangent_xy=(1.0, 0.0),
        normal_xy=(0.0, 1.0),
        lane_role_ids=(ids["lane_a"],),
        lane_widths_m=(3.2,),
        mode_permissions={ids["lane_a"]: frozenset({"passenger"})},
        source_anchor_refs=("osm-way/1",),
        source_geometry_sha256=SHA_A,
        traffic_side=TrafficSide.RIGHT,
    )

    assert valid.model_dump()["schema"] == "torii.corridor.contracts/v1"
    with pytest.raises(ValidationError, match="mode_permissions"):
        BoundaryPort(
            **{
                **valid.model_dump(),
                "mode_permissions": {},
            }
        )


def test_scope_requires_disjoint_target_guard_and_explicit_traffic_side() -> None:
    ids = _semantic_fixture()
    port = BoundaryPort(
        boundary_port_id=ids["port_a"],
        center_xy=(0.0, 0.0),
        tangent_xy=(1.0, 0.0),
        normal_xy=(0.0, 1.0),
        lane_role_ids=(ids["lane_a"],),
        lane_widths_m=(3.2,),
        mode_permissions={ids["lane_a"]: frozenset({"passenger"})},
        source_anchor_refs=("osm-way/1",),
        source_geometry_sha256=SHA_A,
        traffic_side=TrafficSide.RIGHT,
    )

    with pytest.raises(ValidationError, match="disjoint"):
        ScopeSpec(
            scope_id=ids["scope"],
            physical_cell_ids=frozenset({ids["cell"]}),
            target_entity_ids=frozenset({ids["movement"]}),
            guard_entity_ids=frozenset({ids["movement"]}),
            closure_rules=("movement-closure",),
            boundary_ports=(port,),
            traffic_side=TrafficSide.RIGHT,
        )


def test_candidate_graph_rejects_missing_parents_and_cycles() -> None:
    ids = _semantic_fixture()
    operation, delta = _operation_and_delta(ids)
    hypothesis_id = stable_id("hypothesis", {"scope": ids["scope"], "kind": "partial"})
    first_id = stable_id("candidate", {"variant": 1})
    second_id = stable_id("candidate", {"variant": 2})

    first = CandidateVariant(
        variant_id=first_id,
        parent_variant_ids=(second_id,),
        hypothesis_id=hypothesis_id,
        scope_id=ids["scope"],
        operations=(operation,),
        expected_delta=(delta,),
    )
    second = CandidateVariant(
        variant_id=second_id,
        parent_variant_ids=(first_id,),
        hypothesis_id=hypothesis_id,
        scope_id=ids["scope"],
        operations=(operation.model_copy(update={"operation_id": stable_id("operation", {"v": 2})}),),
        expected_delta=(delta.model_copy(update={"delta_id": stable_id("delta", {"v": 2})}),),
    )

    with pytest.raises(ValidationError, match="cycle"):
        CandidateGraph(variants=(first, second))
    with pytest.raises(ValidationError, match="missing"):
        CandidateGraph(variants=(first,))


def test_hypothesis_requires_falsifiers_and_explicit_type() -> None:
    ids = _semantic_fixture()
    hypothesis_id = stable_id("hypothesis", {"scope": ids["scope"]})

    with pytest.raises(ValidationError, match="falsifiers"):
        Hypothesis(
            hypothesis_id=hypothesis_id,
            hypothesis_type=HypothesisType.MERGE_PHYSICAL_CELL,
            scope_id=ids["scope"],
            assumptions=("one conflict envelope",),
            predicted_changes=("merge nodes",),
            falsifiers=(),
        )


def test_workflow_is_an_executable_fail_closed_state_machine() -> None:
    workflow = WorkflowExecution(workflow_id=stable_id("manifest", {"workflow": 1}))
    passed = StageOutcome(stage=WorkflowStage.INGESTED, status=GateStatus.PASS)
    canonicalized = workflow.advance(WorkflowStage.CANONICALIZED, passed)

    assert canonicalized.current_stage is WorkflowStage.CANONICALIZED
    assert canonicalized.action is AutomationAction.SUGGEST

    ids = _semantic_fixture()
    failed_invariant = InvariantResult(
        invariant_id=stable_id("invariant", {"rule": "source-immutable"}),
        rule_id="source-immutable",
        subject_id=ids["movement"],
        status=GateStatus.FAIL,
        hard_gate=True,
        witness={"source_sha_changed": True},
    )
    failed = StageOutcome(
        stage=WorkflowStage.INGESTED,
        status=GateStatus.FAIL,
        invariant_results=(failed_invariant,),
    )
    with pytest.raises(ValidationError, match="only transition to BLOCKED"):
        WorkflowTransition(
            transition_id=stable_id("transition", {"bad": 1}),
            from_stage=WorkflowStage.INGESTED,
            to_stage=WorkflowStage.CANONICALIZED,
            outcome=failed,
        )


def test_auto_certification_rejects_unresolved_review_tasks() -> None:
    outcome = StageOutcome(
        stage=WorkflowStage.RUNTIME_VERIFIED,
        status=GateStatus.PASS,
        unresolved_review_task_ids=(stable_id("review", {"task": 1}),),
    )

    with pytest.raises(ValidationError, match="unresolved"):
        WorkflowTransition(
            transition_id=stable_id("transition", {"runtime": 1}),
            from_stage=WorkflowStage.RUNTIME_VERIFIED,
            to_stage=WorkflowStage.AUTO_CERTIFIED,
            outcome=outcome,
        )


def test_toolchain_lock_fingerprint_rejects_tampering() -> None:
    lock = ToolchainLock.build(
        frozen_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        platform="portable-ci",
        python_version="3.12",
        dependencies={"pydantic": "2.13.4"},
        tools=(
            ToolIdentity(name="sumo", executable="sumo", version="1.27.1"),
            ToolIdentity(name="netconvert", executable="netconvert", version="1.27.1"),
        ),
        command_parameters={"pytest": ("-q",)},
        random_seeds=(0,),
    )

    assert lock.toolchain_id.startswith("toolchain_")
    with pytest.raises(ValidationError, match="does not match"):
        ToolchainLock.model_validate(
            {
                **lock.model_dump(),
                "python_version": "3.13",
            }
        )


def test_artifact_manifest_blocks_source_candidate_identity_collision() -> None:
    toolchain_id = stable_id("toolchain", {"version": 1})
    source_id = stable_id("artifact", {"role": "source"})
    candidate_id = stable_id("artifact", {"role": "candidate"})
    source = ArtifactIdentity(
        artifact_schema="sumo.net.xml",
        artifact_id=source_id,
        logical_name="source_net",
        role=ArtifactRole.SOURCE_NET,
        path="source.net.xml",
        sha256=SHA_A,
        producer="netconvert",
        toolchain_id=toolchain_id,
    )
    candidate = ArtifactIdentity(
        artifact_schema="sumo.net.xml",
        artifact_id=candidate_id,
        logical_name="candidate_net",
        role=ArtifactRole.CANDIDATE_NET,
        path="candidate.net.xml",
        sha256=SHA_A,
        producer="torii",
        toolchain_id=toolchain_id,
    )

    with pytest.raises(ValidationError, match="distinct paths and hashes"):
        ArtifactManifestV1(
            manifest_id=stable_id("manifest", {"artifacts": 1}),
            created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
            toolchain_id=toolchain_id,
            source_artifact_id=source_id,
            candidate_artifact_ids=(candidate_id,),
            artifacts=(source, candidate),
            gate_trace={"identity": GateStatus.PASS},
        )


def test_artifact_identity_from_file_is_hash_bound_and_stable(tmp_path: Path) -> None:
    artifact_file = tmp_path / "candidate.net.xml"
    artifact_file.write_text("<net version=\"1.21\"/>", encoding="utf-8")
    toolchain_id = stable_id("toolchain", {"version": 1})

    first = ArtifactIdentity.from_file(
        artifact_file,
        logical_name="candidate_net",
        role=ArtifactRole.CANDIDATE_NET,
        artifact_schema="sumo.net.xml",
        producer="torii",
        toolchain_id=toolchain_id,
    )
    second = ArtifactIdentity.from_file(
        artifact_file,
        logical_name="candidate_net",
        role=ArtifactRole.CANDIDATE_NET,
        artifact_schema="sumo.net.xml",
        producer="torii",
        toolchain_id=toolchain_id,
    )

    assert first == second
    assert Path(first.path) == artifact_file.resolve()
    assert first.sha256 == hashlib.sha256(artifact_file.read_bytes()).hexdigest()


def test_artifact_identity_from_file_fails_closed_for_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ArtifactIdentity.from_file(
            tmp_path / "missing.json",
            logical_name="missing_report",
            role=ArtifactRole.REPORT,
            artifact_schema="application/json",
            producer="torii",
            toolchain_id=stable_id("toolchain", {"version": 1}),
        )


def test_artifact_manifest_rehashes_files_before_use(tmp_path: Path) -> None:
    source_file = tmp_path / "source.net.xml"
    candidate_file = tmp_path / "candidate.net.xml"
    source_file.write_text("<net version=\"source\"/>", encoding="utf-8")
    candidate_file.write_text("<net version=\"candidate\"/>", encoding="utf-8")
    toolchain_id = stable_id("toolchain", {"version": 1})
    source = ArtifactIdentity.from_file(
        source_file,
        logical_name="source_net",
        role=ArtifactRole.SOURCE_NET,
        artifact_schema="sumo.net.xml",
        producer="netconvert",
        toolchain_id=toolchain_id,
    )
    candidate = ArtifactIdentity.from_file(
        candidate_file,
        logical_name="candidate_net",
        role=ArtifactRole.CANDIDATE_NET,
        artifact_schema="sumo.net.xml",
        producer="torii",
        toolchain_id=toolchain_id,
    )
    manifest = ArtifactManifestV1(
        manifest_id=stable_id("manifest", {"artifacts": 2}),
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        toolchain_id=toolchain_id,
        source_artifact_id=source.artifact_id,
        candidate_artifact_ids=(candidate.artifact_id,),
        artifacts=(source, candidate),
        gate_trace={"identity": GateStatus.PASS},
    )

    manifest.assert_artifact_files_unchanged()
    candidate_file.write_text("<net version=\"tampered\"/>", encoding="utf-8")

    with pytest.raises(ValueError, match="candidate_net"):
        manifest.assert_artifact_files_unchanged()


def test_artifact_manifest_blocks_missing_file_during_rehash(tmp_path: Path) -> None:
    source_file = tmp_path / "source.net.xml"
    candidate_file = tmp_path / "candidate.net.xml"
    source_file.write_text("<net version=\"source\"/>", encoding="utf-8")
    candidate_file.write_text("<net version=\"candidate\"/>", encoding="utf-8")
    toolchain_id = stable_id("toolchain", {"version": 1})
    source = ArtifactIdentity.from_file(
        source_file,
        logical_name="source_net",
        role=ArtifactRole.SOURCE_NET,
        artifact_schema="sumo.net.xml",
        producer="netconvert",
        toolchain_id=toolchain_id,
    )
    candidate = ArtifactIdentity.from_file(
        candidate_file,
        logical_name="candidate_net",
        role=ArtifactRole.CANDIDATE_NET,
        artifact_schema="sumo.net.xml",
        producer="torii",
        toolchain_id=toolchain_id,
    )
    manifest = ArtifactManifestV1(
        manifest_id=stable_id("manifest", {"artifacts": "missing"}),
        created_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        toolchain_id=toolchain_id,
        source_artifact_id=source.artifact_id,
        candidate_artifact_ids=(candidate.artifact_id,),
        artifacts=(source, candidate),
        gate_trace={"identity": GateStatus.PASS},
    )
    candidate_file.unlink()

    with pytest.raises(FileNotFoundError):
        manifest.assert_artifact_files_unchanged()


def test_quality_vector_is_not_a_weighted_partial_score() -> None:
    quality = _quality_vector()

    assert {dimension.name for dimension in quality.dimensions} == set(QualityDimensionName)
    with pytest.raises(ValidationError, match="every dimension"):
        NetworkQualityVectorV1(
            dimensions=(
                QualityDimension(
                    name=QualityDimensionName.TOPOLOGY,
                    status=GateStatus.PASS,
                ),
            )
        )


def test_finding_requires_stable_witness_identity() -> None:
    ids = _semantic_fixture()
    finding = Finding(
        finding_id=stable_id("finding", {"movement": ids["movement"]}),
        category="internal_path_target_mismatch",
        severity=FindingSeverity.STRUCTURAL,
        subject_id=ids["movement"],
        witness={"declared_target": "lane-role-a", "actual_target": "lane-role-b"},
        witness_signature=stable_id("signature", {"witness": "target-mismatch"}),
        confidence=1.0,
    )

    assert finding.witness["actual_target"] == "lane-role-b"


def test_frozen_stage0_benchmark_toolchain_and_plan_hashes_close() -> None:
    benchmark_path = REPOSITORY_ROOT / "benchmarks/corridor_human_modeling_v1/benchmark.v1.json"
    toolchain_path = REPOSITORY_ROOT / "benchmarks/corridor_human_modeling_v1/toolchain.lock.json"
    lock_path = REPOSITORY_ROOT / "benchmarks/corridor_human_modeling_v1/stage0.lock.json"

    benchmark = BenchmarkSpecV1.model_validate_json(benchmark_path.read_text(encoding="utf-8"))
    toolchain = ToolchainLock.model_validate_json(toolchain_path.read_text(encoding="utf-8"))
    lock = BenchmarkLock.model_validate_json(lock_path.read_text(encoding="utf-8"))

    assert benchmark.frozen is True
    assert toolchain.toolchain_id == "toolchain_48d9790480acc11e5c4b6b5d"
    for relative_path, expected_sha256 in (
        (lock.benchmark_path, lock.benchmark_sha256),
        (lock.toolchain_lock_path, lock.toolchain_lock_sha256),
        (lock.research_plan_path, lock.research_plan_sha256),
    ):
        payload = (REPOSITORY_ROOT / relative_path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_sha256


def test_exported_contract_schema_is_current() -> None:
    schema_path = REPOSITORY_ROOT / "schemas/torii.corridor.research-bundle.v1.schema.json"
    expected = json.dumps(
        build_corridor_schema(),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )
    assert schema_path.read_text(encoding="utf-8") == expected


@pytest.mark.parametrize(
    ("filename", "builder"),
    (
        (
            "torii.corridor.effective-tls-program-inventory.v1.schema.json",
            build_effective_tls_program_inventory_schema,
        ),
        (
            "torii.corridor.controlled-pedestrian-binding-census.v1.schema.json",
            build_controlled_pedestrian_binding_census_schema,
        ),
        (
            "torii.corridor.row-1-experiment-report.v1.schema.json",
            build_row1_experiment_report_schema,
        ),
    ),
)
def test_exported_stage1m_pcb_schemas_are_current(
    filename: str,
    builder,
) -> None:
    schema_path = REPOSITORY_ROOT / "schemas" / filename
    expected = json.dumps(
        builder(),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )
    assert schema_path.read_text(encoding="utf-8") == expected
