from __future__ import annotations

import argparse
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from astra_sumo.core.artifact_io import write_json_atomic  # noqa: E402
from astra_sumo.corridor.schema import (  # noqa: E402
    build_attention_evaluation_key_v2_r2_schema,
    build_blinded_attention_dataset_v2_r2_schema,
    build_held_out_replacement_attempt_ledger_v2_schema,
    build_held_out_replacement_plan_v2_schema,
    build_held_out_replacement_policy_v2_schema,
    build_held_out_reserve_corpus_v2_schema,
    build_held_out_review_parent_v2_schema,
    build_held_out_review_policy_v2_schema,
    build_held_out_review_v2_contract_bundle_schema,
    build_held_out_review_v2_report_schema,
    build_held_out_review_execution_parent_v2_r2_schema,
    build_held_out_review_package_manifest_v2_r2_schema,
    build_held_out_review_trial_instance_v2_r2_schema,
    build_held_out_source_snapshot_protocol_v2_schema,
    build_review_witness_sampling_policy_v2_schema,
    build_review_sampling_ledger_v2_r2_schema,
    build_review_study_sampling_policy_v2_r2_schema,
    build_review_unit_adjudication_v2_r2_schema,
    build_review_unit_decision_v2_r2_schema,
)


SCHEMA_DIR = REPOSITORY_ROOT / "schemas" / "research" / "corridor"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export ASTRA Stage 1-M held-out review v2 schemas.")
    outputs = (
        ("reserve", "torii.corridor.held-out-reserve-corpus.v2.schema.json", build_held_out_reserve_corpus_v2_schema),
        ("replacement-policy", "torii.corridor.held-out-replacement-policy.v2.schema.json", build_held_out_replacement_policy_v2_schema),
        ("replacement-plan", "torii.corridor.held-out-replacement-plan.v2.schema.json", build_held_out_replacement_plan_v2_schema),
        ("source-snapshot-protocol", "torii.corridor.held-out-source-snapshot-protocol.v2.schema.json", build_held_out_source_snapshot_protocol_v2_schema),
        ("replacement-attempt-ledger", "torii.corridor.held-out-replacement-attempt-ledger.v2.schema.json", build_held_out_replacement_attempt_ledger_v2_schema),
        ("sampling-policy", "torii.corridor.review-witness-sampling-policy.v2.schema.json", build_review_witness_sampling_policy_v2_schema),
        ("parent", "torii.corridor.held-out-review-parent.v2.schema.json", build_held_out_review_parent_v2_schema),
        ("policy", "torii.corridor.held-out-review-policy.v2.schema.json", build_held_out_review_policy_v2_schema),
        ("bundle", "torii.corridor.held-out-review-contract-bundle.v2.schema.json", build_held_out_review_v2_contract_bundle_schema),
        ("report", "torii.corridor.held-out-review-report.v2.schema.json", build_held_out_review_v2_report_schema),
        ("study-sampling-policy-r2", "torii.corridor.review-study-sampling-policy.v2-r2.schema.json", build_review_study_sampling_policy_v2_r2_schema),
        ("execution-parent-r2", "torii.corridor.held-out-review-execution-parent.v2-r2.schema.json", build_held_out_review_execution_parent_v2_r2_schema),
        ("trial-instance-r2", "torii.corridor.held-out-review-trial-instance.v2-r2.schema.json", build_held_out_review_trial_instance_v2_r2_schema),
        ("blinded-dataset-r2", "torii.corridor.blinded-attention-dataset.v2-r2.schema.json", build_blinded_attention_dataset_v2_r2_schema),
        ("evaluation-key-r2", "torii.corridor.attention-evaluation-key.v2-r2.schema.json", build_attention_evaluation_key_v2_r2_schema),
        ("sampling-ledger-r2", "torii.corridor.review-sampling-ledger.v2-r2.schema.json", build_review_sampling_ledger_v2_r2_schema),
        ("unit-decision-r2", "torii.corridor.review-unit-decision.v2-r2.schema.json", build_review_unit_decision_v2_r2_schema),
        ("unit-adjudication-r2", "torii.corridor.review-unit-adjudication.v2-r2.schema.json", build_review_unit_adjudication_v2_r2_schema),
        ("package-manifest-r2", "torii.corridor.held-out-review-package-manifest.v2-r2.schema.json", build_held_out_review_package_manifest_v2_r2_schema),
    )
    for name, filename, _builder in outputs:
        parser.add_argument(f"--{name}-output", type=Path, default=SCHEMA_DIR / filename)
    args = parser.parse_args()
    for name, _filename, builder in outputs:
        path = getattr(args, f"{name.replace('-', '_')}_output")
        write_json_atomic(path, builder(), sort_keys=True)


if __name__ == "__main__":
    main()
