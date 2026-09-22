from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_sumo.corridor.held_out_review_contracts import (
    BlindReviewDecision,
)
from astra_sumo.corridor.ids import stable_id
from astra_sumo.corridor.schema import (
    build_held_out_review_contract_bundle_schema,
    build_held_out_review_policy_schema,
    build_held_out_review_report_schema,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_false_independence_attestation_is_rejected() -> None:
    payload = {
        "decision_id": stable_id("review", {"decision": "false-attestation"}),
        "trial_id": stable_id("review", {"trial": "false-attestation"}),
        "case_code": "case-0123456789ab",
        "reviewer_id": stable_id("review", {"reviewer": "false-attestation"}),
        "label": "defect",
        "started_at": "2026-07-14T12:00:00+00:00",
        "decided_at": "2026-07-14T12:01:00+00:00",
        "observed_facts": ["fact"],
        "rationale": "rationale",
        "independent_review_attested": False,
    }

    with pytest.raises(ValueError):
        BlindReviewDecision.model_validate(payload)


def test_held_out_review_schemas_are_current() -> None:
    schemas = {
        "torii.corridor.held-out-review-policy.v1.schema.json": (
            build_held_out_review_policy_schema()
        ),
        "torii.corridor.held-out-review-contract-bundle.v1.schema.json": (
            build_held_out_review_contract_bundle_schema()
        ),
        "torii.corridor.held-out-review-report.v1.schema.json": (
            build_held_out_review_report_schema()
        ),
    }
    for filename, schema in schemas.items():
        expected = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True)
        assert (REPOSITORY_ROOT / "schemas" / filename).read_text(
            encoding="utf-8"
        ) == expected
