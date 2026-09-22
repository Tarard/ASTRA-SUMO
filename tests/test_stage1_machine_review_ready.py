from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from torii_sumo.corridor.enums import GateStatus
from torii_sumo.corridor.ids import stable_id
from torii_sumo.corridor.run_identity import CodeProducerIdentity
from torii_sumo.corridor.schema import build_stage1_machine_review_ready_provenance_schema
from torii_sumo.corridor.stage1_review_ready_contracts import (
    Stage1CoverageGapEvidence,
    Stage1CoverageSummary,
    Stage1EvidenceArtifact,
    Stage1EvidenceDependency,
    Stage1GateEvidence,
    Stage1MachineReviewReadyProvenance,
    Stage1MachineSummary,
    Stage1PCBSummary,
    Stage1ReviewPackageSummary,
    Stage1RWCReplacementContribution,
    Stage1RWCSummary,
    Stage1ROWSummary,
    Stage1SnapshotSummary,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_FILE = (
    REPOSITORY_ROOT
    / "schemas/torii.corridor.stage1m-machine-review-ready-provenance.v3.schema.json"
)
PROVENANCE_FILE = (
    REPOSITORY_ROOT
    / "benchmarks/corridor_human_modeling_v1/evidence/"
    "stage1m_machine_review_ready_provenance_20260714.v3.json"
)

_GATE_IDS = (
    "contract-conformance",
    "provenance-identity-closure",
    "thirty-blinded-review-cases",
    "stable-coverage-gap-entities",
    "controlled-binding-census",
    "lossless-conflict-ledger",
    "sampling-and-hidden-member-protocol",
    "trial-and-statistics-frozen",
    "automatic-promotion-blocked",
    "review-ready-stage-semantics-explicit",
)


def test_stage1_machine_review_ready_contract_keeps_human_and_promotion_gates_blocked() -> None:
    provenance = _valid_provenance()

    assert provenance.status == "review-ready"
    assert provenance.stage_1m_machine_review_ready_gate is GateStatus.PASS
    assert provenance.stage_1h_human_validation_gate is GateStatus.BLOCKED
    assert provenance.automatic_promotion_gate is GateStatus.BLOCKED
    assert provenance.human_decisions_present is False
    assert provenance.review_ready_is_not_stage1_exit is True
    assert provenance.pcb.frozen_unresolved_binding_count == 453
    assert provenance.pcb.effective_unresolved_binding_count == 459
    assert provenance.rwc.frozen_atomic_witness_count == 88423
    assert provenance.rwc.effective_atomic_witness_count == 102398
    assert provenance.review_package.review_unit_count == 384


def test_stage1_machine_review_ready_contract_rejects_artifact_cycles() -> None:
    provenance = _valid_provenance()
    payload = provenance.model_dump(mode="json", by_alias=True)
    payload["dependencies"] = [
        {
            "schema": "torii.corridor.contracts/v1",
            "parent_path": "evidence/00.json",
            "child_path": "evidence/01.json",
            "relation": "depends-on",
        },
        {
            "schema": "torii.corridor.contracts/v1",
            "parent_path": "evidence/01.json",
            "child_path": "evidence/00.json",
            "relation": "depends-on",
        },
    ]

    with pytest.raises(ValidationError, match="must form a DAG"):
        Stage1MachineReviewReadyProvenance.model_validate(payload)


def test_stage1_machine_review_ready_schema_is_current() -> None:
    expected = json.dumps(
        build_stage1_machine_review_ready_provenance_schema(),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )

    assert SCHEMA_FILE.read_text(encoding="utf-8") == expected


def test_authoritative_stage1_machine_review_ready_provenance_is_closed() -> None:
    raw = PROVENANCE_FILE.read_text(encoding="utf-8")
    provenance = Stage1MachineReviewReadyProvenance.model_validate_json(raw)

    assert provenance.producer.revision == "a21fc73ace1c6f68a6931ef6455b62311a89cbae"
    assert provenance.machine_evidence_producer.revision == "f14eb888b25ffec7f27cdbe0c40ce10a481fb544"
    assert provenance.review_package_producer.revision == "c5c9cef9410b373f38390420409548eaaeca67d3"
    assert provenance.snapshot.manifest_artifact_count == 40
    assert provenance.machine.manifest_artifact_count == 1404
    assert provenance.pcb.effective_unresolved_binding_count == 459
    assert provenance.rwc.effective_atomic_witness_count == 102398
    assert provenance.coverage.effective_coverage_gap_count == 3
    assert provenance.review_package.review_unit_count == 384
    assert provenance.review_package.repeat_hash_difference_count == 0
    assert all(gate.status is GateStatus.PASS for gate in provenance.gates)
    assert '"blinding_seed"' not in raw
    assert "restricted-blinding-seed" not in raw


def _valid_provenance() -> Stage1MachineReviewReadyProvenance:
    producer = CodeProducerIdentity(
        repository_url="https://github.com/Tarard/Torii-SUMO.git",
        revision="1" * 40,
        tree_revision="2" * 40,
        branch="codex/test",
        working_tree_clean=True,
    )
    artifacts = tuple(
        Stage1EvidenceArtifact(
            logical_name=f"artifact-{index:02d}",
            role="test-evidence",
            path=f"evidence/{index:02d}.json",
            sha256=f"{index + 1:064x}",
            visibility="tracked-public",
            tracked_in_git=True,
        )
        for index in range(10)
    )
    dependencies = tuple(
        Stage1EvidenceDependency(
            parent_path=f"evidence/{index:02d}.json",
            child_path=f"evidence/{index + 1:02d}.json",
            relation="depends-on",
        )
        for index in range(9)
    )
    gates = tuple(
        Stage1GateEvidence(
            gate_id=gate_id,
            evidence_paths=(f"evidence/{index:02d}.json",),
            conclusion=f"Frozen test conclusion {index}.",
        )
        for index, gate_id in enumerate(_GATE_IDS)
    )
    gaps = (
        Stage1CoverageGapEvidence(
            corridor_key="london-liverpool-street",
            coverage_gap_id="coverage_6bd0ab011400a059cc11da27",
            crossing_signature="signature_9ad24b6a99c8b98c4fec979e",
            position_xy=(3622.97, 277.65),
            primary_classification="safety-coverage-blocker",
            secondary_classifications=("review-required", "out-of-domain"),
            rejection_reasons=("pedestrian_owner_unresolved",),
            certification_site_group="london-liverpool-street-facility",
        ),
        Stage1CoverageGapEvidence(
            corridor_key="paris-porte-maillot",
            coverage_gap_id="coverage_849e1b18356ae0d0ce9612e8",
            crossing_signature="signature_4aaaeebd3aaa97b8a2739679",
            position_xy=(1895.26, 1363.745),
            primary_classification="safety-coverage-blocker",
            secondary_classifications=("review-required", "out-of-domain"),
            rejection_reasons=("pedestrian_owner_unresolved",),
            certification_site_group="paris-porte-maillot-complex",
        ),
        Stage1CoverageGapEvidence(
            corridor_key="paris-porte-maillot",
            coverage_gap_id="coverage_b063d4f7f07b0db6e6cdb050",
            crossing_signature="signature_1cdf3be8eb48a50e151b6db4",
            position_xy=(3238.07, 1583.44),
            primary_classification="safety-coverage-blocker",
            secondary_classifications=("review-required", "out-of-domain"),
            rejection_reasons=("pedestrian_owner_unresolved",),
            certification_site_group="paris-porte-maillot-complex",
        ),
    )
    payload = {
        "recorded_at": datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        "producer": producer,
        "machine_evidence_producer": producer,
        "review_package_producer": producer,
        "trial_id": "review_a784b285271ca7f510ed79f2",
        "artifacts": artifacts,
        "dependencies": dependencies,
        "gates": gates,
        "snapshot": Stage1SnapshotSummary(),
        "machine": Stage1MachineSummary(
            machine_label_counts={"defect": 30},
            manifest_artifact_count=1404,
            report_sha256="3" * 64,
            manifest_sha256="4" * 64,
            run_identity_sha256="5" * 64,
        ),
        "pcb": Stage1PCBSummary(
            effective_unresolved_binding_count=459,
            replacement_contribution_count=6,
            class_counts={
                "ordinary-program-truly-absent": 21,
                "runtime-special-controller": 438,
            },
            hard_structural_count=21,
            ambiguous_count=0,
            exact_review_position_count=459,
            unique_primary_classification_count=459,
        ),
        "rwc": Stage1RWCSummary(
            effective_confirmed_count=39725,
            effective_potential_count=62673,
            effective_atomic_witness_count=102398,
            effective_cluster_count=98041,
            effective_site_review_case_count=15878,
            replacement_contributions=(
                Stage1RWCReplacementContribution(
                    corridor_key="london-liverpool-street",
                    confirmed_count=4546,
                    potential_count=7200,
                    atomic_witness_count=11746,
                    cluster_count=11181,
                    site_review_case_count=1759,
                ),
                Stage1RWCReplacementContribution(
                    corridor_key="melbourne-dandenong-orrong",
                    confirmed_count=495,
                    potential_count=1178,
                    atomic_witness_count=1673,
                    cluster_count=1588,
                    site_review_case_count=246,
                ),
                Stage1RWCReplacementContribution(
                    corridor_key="sydney-m5-east-kingsgrove",
                    confirmed_count=191,
                    potential_count=365,
                    atomic_witness_count=556,
                    cluster_count=548,
                    site_review_case_count=91,
                ),
            ),
        ),
        "coverage": Stage1CoverageSummary(gaps=gaps),
        "row_1": Stage1ROWSummary(
            report_id="manifest_63f6594cbc1bb25cde1b605d",
            report_sha256="6" * 64,
            independent_repeat_sha256="6" * 64,
        ),
        "review_package": Stage1ReviewPackageSummary(
            trial_id="review_a784b285271ca7f510ed79f2",
            atomic_witness_population_count=102398,
            dataset_sha256="7" * 64,
            sampling_ledger_sha256="8" * 64,
            restricted_evaluation_key_sha256="9" * 64,
            package_manifest_sha256="a" * 64,
        ),
    }
    provisional = Stage1MachineReviewReadyProvenance.model_construct(
        provenance_id=stable_id("manifest", {"pending": True}),
        **payload,
    )
    return Stage1MachineReviewReadyProvenance(
        provenance_id=stable_id("manifest", provisional.identity_payload()),
        **payload,
    )
