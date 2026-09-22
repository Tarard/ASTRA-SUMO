from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from astra_sumo.corridor.held_out_review_v2_contracts import (
    ClusterReviewDecisionV2,
    HeldOutReviewV2Metrics,
    HeldOutReviewV2Report,
)
from astra_sumo.corridor.ids import stable_id
from astra_sumo.corridor.schema import (
    build_held_out_replacement_plan_v2_schema,
    build_held_out_replacement_policy_v2_schema,
    build_held_out_reserve_corpus_v2_schema,
    build_held_out_review_parent_v2_schema,
    build_held_out_review_policy_v2_schema,
    build_held_out_review_v2_contract_bundle_schema,
    build_held_out_review_v2_report_schema,
    build_held_out_source_snapshot_protocol_v2_schema,
    build_held_out_replacement_attempt_ledger_v2_schema,
    build_review_witness_sampling_policy_v2_schema,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPOSITORY_ROOT / "schemas"
COMPRESSION_SCHEMA = SCHEMA_DIR / "torii.corridor.lossless-review-compression.v1.schema.json"


def test_v2_cluster_decision_requires_blinding_attestations() -> None:
    payload = {
        "decision_id": stable_id("review", {"v2-decision": 1}),
        "trial_id": stable_id("review", {"v2-trial": 1}),
        "case_code": "case-0123456789ab",
        "unit_code": "unit-0123456789ab",
        "reviewer_id": stable_id("review", {"v2-reviewer": 1}),
        "label": "attention-required",
        "witness_labels": {"witness-0123456789ab": "attention-required"},
        "cluster_purity_supported": True,
        "started_at": datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        "decided_at": datetime(2026, 7, 14, 12, 1, tzinfo=UTC),
        "observed_facts": ("Observed right-of-way evidence.",),
        "rationale": "Independent blinded decision.",
        "machine_label_was_hidden": False,
    }

    with pytest.raises(ValueError):
        ClusterReviewDecisionV2.model_validate(payload)


def test_v2_report_can_never_authorize_promotion() -> None:
    payload = {
        "trial_id": stable_id("review", {"v2-report": 1}),
        "policy_sha256": "0" * 64,
        "parent_review_benchmark_sha256": "1" * 64,
        "status": "pass",
        "stage_1m_machine_review_ready_gate": "pass",
        "stage_1h_human_validation_gate": "pass",
        "automatic_promotion_gate": "pass",
        "metrics": HeldOutReviewV2Metrics(
            valid_corridor_package_count=30,
            reproducibility_only_case_count=3,
            completed_cluster_review_count=30,
            raw_agreement=1.0,
            cohen_kappa=1.0,
            median_review_seconds=60.0,
            weighted_attention_precision=1.0,
            weighted_attention_recall=1.0,
            safety_critical_false_negative_count=0,
            hidden_member_disagreement_count=0,
            hidden_member_disagreement_upper_bound=0.05,
            auto_precision_status="not-applicable",
            auto_precision=None,
            auto_precision_one_sided_lower_bound=None,
            auto_coverage=None,
        ),
        "blockers": (),
    }
    with pytest.raises(ValueError):
        HeldOutReviewV2Report.model_validate(payload)


def test_v2_schemas_are_current() -> None:
    schemas = {
        "torii.corridor.held-out-reserve-corpus.v2.schema.json": (build_held_out_reserve_corpus_v2_schema()),
        "torii.corridor.held-out-replacement-policy.v2.schema.json": (build_held_out_replacement_policy_v2_schema()),
        "torii.corridor.held-out-replacement-plan.v2.schema.json": (build_held_out_replacement_plan_v2_schema()),
        "torii.corridor.held-out-source-snapshot-protocol.v2.schema.json": (
            build_held_out_source_snapshot_protocol_v2_schema()
        ),
        "torii.corridor.held-out-replacement-attempt-ledger.v2.schema.json": (
            build_held_out_replacement_attempt_ledger_v2_schema()
        ),
        "torii.corridor.review-witness-sampling-policy.v2.schema.json": (
            build_review_witness_sampling_policy_v2_schema()
        ),
        "torii.corridor.held-out-review-parent.v2.schema.json": (build_held_out_review_parent_v2_schema()),
        "torii.corridor.held-out-review-policy.v2.schema.json": (build_held_out_review_policy_v2_schema()),
        "torii.corridor.held-out-review-contract-bundle.v2.schema.json": (
            build_held_out_review_v2_contract_bundle_schema()
        ),
        "torii.corridor.held-out-review-report.v2.schema.json": (build_held_out_review_v2_report_schema()),
    }
    for filename, schema in schemas.items():
        expected = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True)
        assert (SCHEMA_DIR / filename).read_text(encoding="utf-8") == expected
