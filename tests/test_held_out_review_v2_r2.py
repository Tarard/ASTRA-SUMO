from __future__ import annotations

import json
from pathlib import Path

from astra_sumo.corridor.enums import TrafficSide
from astra_sumo.corridor.held_out_review_v2_contracts import (
    BlindedReviewUnitV2R2,
)
from astra_sumo.corridor.held_out_review_v2_r2_sampling import (
    allocate_stratified_sample_sizes,
    deterministic_sample,
    select_negative_pairs,
)
from astra_sumo.corridor.review_compression_contracts import (
    NegativePairSample,
    NegativePairStratum,
)
from astra_sumo.corridor.schema import (
    build_attention_evaluation_key_v2_r2_schema,
    build_blinded_attention_dataset_v2_r2_schema,
    build_held_out_review_execution_parent_v2_r2_schema,
    build_held_out_review_package_manifest_v2_r2_schema,
    build_held_out_review_trial_instance_v2_r2_schema,
    build_review_sampling_ledger_v2_r2_schema,
    build_review_study_sampling_policy_v2_r2_schema,
    build_review_unit_adjudication_v2_r2_schema,
    build_review_unit_decision_v2_r2_schema,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPOSITORY_ROOT / "schemas"


def test_v2_r2_reviewer_unit_does_not_expose_machine_role_or_weight() -> None:
    unit = BlindedReviewUnitV2R2(
        unit_code="unit-0123456789ab",
        review_domain="pedestrian-path-relation",
        witness_codes=("witness-0123456789ab",),
        exact_question="Do the displayed paths require a right-of-way relation?",
        required_observations=("at-grade path occupancy",),
        evidence_path="reviewer-visible/case-0123456789ab/units/unit-0123456789ab.json",
    )

    payload = unit.model_dump(mode="json", by_alias=True)
    assert "unit_kind" not in payload
    assert "inclusion_probability" not in payload
    assert set(payload) == {
        "schema",
        "unit_code",
        "review_domain",
        "witness_codes",
        "exact_question",
        "required_observations",
        "evidence_path",
    }


def test_v2_r2_stratified_sampling_is_order_independent_and_covers_each_stratum() -> None:
    populations = {
        ("confirmed-only", "same-cell"): 100,
        ("potential-only", "cross-cell-or-rail"): 10,
        ("mixed", "same-cell"): 1,
    }
    first = allocate_stratified_sample_sizes(
        populations,
        8,
        seed="test-review-seed-0123456789abcdef",
        namespace="test-allocation",
    )
    second = allocate_stratified_sample_sizes(
        dict(reversed(tuple(populations.items()))),
        8,
        seed="test-review-seed-0123456789abcdef",
        namespace="test-allocation",
    )

    assert first == second
    assert sum(first.values()) == 8
    assert all(count >= 1 for count in first.values())
    assert deterministic_sample(
        ("c", "a", "b"),
        2,
        seed="test-review-seed-0123456789abcdef",
        namespace="members",
    ) == deterministic_sample(
        ("b", "c", "a"),
        2,
        seed="test-review-seed-0123456789abcdef",
        namespace="members",
    )


def test_v2_r2_negative_pair_weight_combines_both_sampling_stages() -> None:
    def sample(index: int, stratum_id: str) -> NegativePairSample:
        return NegativePairSample.model_construct(
            sample_id=f"evidence_{index:024x}",
            stratum_id=stratum_id,
            pedestrian_movement_id=f"movement_{index:024x}",
            conflicting_movement_id=f"movement_{index + 100:024x}",
            physical_cell_id=f"cell_{index:024x}",
            control_class="unknown-unsignalized",
            conflicting_turn_classes=("straight",),
            traffic_side=TrafficSide.RIGHT,
            inclusion_probability=0.05,
            machine_finding_absent=True,
        )

    strata = tuple(
        NegativePairStratum.model_construct(
            stratum_id=f"scope_{stratum_index:024x}",
            population_kind="same-physical-cell-no-conflict-finding",
            control_class=f"control-{stratum_index}",
            conflicting_turn_classes=("straight",),
            traffic_side=TrafficSide.RIGHT,
            population_count=100 * (stratum_index + 1),
            selected_count=5,
            inclusion_probability=5 / (100 * (stratum_index + 1)),
            samples=tuple(
                sample(stratum_index * 10 + index, f"scope_{stratum_index:024x}")
                for index in range(5)
            ),
        )
        for stratum_index in range(2)
    )
    selected, ledger_strata = select_negative_pairs(
        corridor_key="synthetic-corridor",
        strata=strata,
        target=4,
        seed="test-review-seed-0123456789abcdef",
    )

    assert len(selected) == 4
    assert {item.selected_count for item in ledger_strata} == {2}
    assert {item.inclusion_probability for item in ledger_strata} == {0.02, 0.01}


def test_v2_r2_schemas_are_current() -> None:
    schemas = {
        "torii.corridor.review-study-sampling-policy.v2-r2.schema.json": (
            build_review_study_sampling_policy_v2_r2_schema()
        ),
        "torii.corridor.held-out-review-execution-parent.v2-r2.schema.json": (
            build_held_out_review_execution_parent_v2_r2_schema()
        ),
        "torii.corridor.held-out-review-trial-instance.v2-r2.schema.json": (
            build_held_out_review_trial_instance_v2_r2_schema()
        ),
        "torii.corridor.blinded-attention-dataset.v2-r2.schema.json": (
            build_blinded_attention_dataset_v2_r2_schema()
        ),
        "torii.corridor.attention-evaluation-key.v2-r2.schema.json": (
            build_attention_evaluation_key_v2_r2_schema()
        ),
        "torii.corridor.review-sampling-ledger.v2-r2.schema.json": (
            build_review_sampling_ledger_v2_r2_schema()
        ),
        "torii.corridor.review-unit-decision.v2-r2.schema.json": (
            build_review_unit_decision_v2_r2_schema()
        ),
        "torii.corridor.review-unit-adjudication.v2-r2.schema.json": (
            build_review_unit_adjudication_v2_r2_schema()
        ),
        "torii.corridor.held-out-review-package-manifest.v2-r2.schema.json": (
            build_held_out_review_package_manifest_v2_r2_schema()
        ),
    }
    for filename, schema in schemas.items():
        expected = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True)
        assert (SCHEMA_DIR / filename).read_text(encoding="utf-8") == expected
