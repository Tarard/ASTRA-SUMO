from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256

from .enums import GateStatus
from .held_out_corpus_contracts import (
    HeldOutCorpusMachineManifest,
    HeldOutCorpusMachineReport,
    HeldOutCorpusSnapshotReport,
    HeldOutCorpusSpec,
)
from .held_out_review_v2_contracts import (
    HeldOutReplacementAttemptLedgerV2,
    HeldOutReviewExecutionParentV2R2,
    HeldOutReviewPolicyV2,
    HeldOutReviewTrialInstanceV2R2,
    HeldOutSourceSnapshotProtocolV2,
    ReviewStudySamplingPolicyV2R2,
)
from .ids import stable_id
from .net_replay import NetReplayReport
from .run_identity import HeldOutMachineRunIdentity


def freeze_review_trial_v2_r2(
    *,
    base_review_parent_file: Path,
    base_review_policy_file: Path,
    parent_sampling_policy_file: Path,
    effective_corpus_file: Path,
    replacement_attempt_ledger_file: Path,
    source_snapshot_protocol_file: Path,
    snapshot_report_file: Path,
    snapshot_manifest_file: Path,
    machine_run_identity_file: Path,
    machine_report_file: Path,
    machine_manifest_file: Path,
    study_sampling_policy_output: Path,
    execution_parent_output: Path,
    trial_instance_output: Path,
    restricted_seed_output: Path,
) -> tuple[
    ReviewStudySamplingPolicyV2R2,
    HeldOutReviewExecutionParentV2R2,
    HeldOutReviewTrialInstanceV2R2,
]:
    """Freeze the executable v2-R2 trial before drawing any review sample."""

    base_parent_path = base_review_parent_file.resolve(strict=True)
    base_policy_path = base_review_policy_file.resolve(strict=True)
    parent_sampling_path = parent_sampling_policy_file.resolve(strict=True)
    effective_path = effective_corpus_file.resolve(strict=True)
    ledger_path = replacement_attempt_ledger_file.resolve(strict=True)
    protocol_path = source_snapshot_protocol_file.resolve(strict=True)
    snapshot_report_path = snapshot_report_file.resolve(strict=True)
    snapshot_manifest_path = snapshot_manifest_file.resolve(strict=True)
    run_identity_path = machine_run_identity_file.resolve(strict=True)
    machine_report_path = machine_report_file.resolve(strict=True)
    machine_manifest_path = machine_manifest_file.resolve(strict=True)
    base_policy = HeldOutReviewPolicyV2.model_validate_json(base_policy_path.read_text(encoding="utf-8"))
    effective = HeldOutCorpusSpec.model_validate_json(effective_path.read_text(encoding="utf-8"))
    ledger = HeldOutReplacementAttemptLedgerV2.model_validate_json(ledger_path.read_text(encoding="utf-8"))
    HeldOutSourceSnapshotProtocolV2.model_validate_json(protocol_path.read_text(encoding="utf-8"))
    snapshot_report = HeldOutCorpusSnapshotReport.model_validate_json(snapshot_report_path.read_text(encoding="utf-8"))
    run_identity = HeldOutMachineRunIdentity.model_validate_json(run_identity_path.read_text(encoding="utf-8"))
    machine_report = HeldOutCorpusMachineReport.model_validate_json(machine_report_path.read_text(encoding="utf-8"))
    machine_manifest = HeldOutCorpusMachineManifest.model_validate_json(
        machine_manifest_path.read_text(encoding="utf-8")
    )
    if ledger.effective_corpus_sha256 != file_sha256(effective_path):
        raise ValueError("Replacement ledger does not bind the effective corpus.")
    if snapshot_report.corpus_id != effective.corpus_id:
        raise ValueError("Snapshot report belongs to another effective corpus.")
    if snapshot_report.status is not GateStatus.PASS or len(snapshot_report.corridors) != 30:
        raise ValueError("V2-R2 requires 30 passing source snapshots.")
    _verify_snapshot_manifest(snapshot_manifest_path)
    if (
        machine_report.processed_case_count != 30
        or machine_report.blockers
        or any(result.pipeline_status is not GateStatus.PASS for result in machine_report.results)
    ):
        raise ValueError("V2-R2 requires 30 complete machine corridor packages.")
    if machine_report.run_identity_sha256 != file_sha256(run_identity_path):
        raise ValueError("Machine report run identity hash mismatch.")
    if machine_manifest.producer != run_identity.producer:
        raise ValueError("Machine manifest and run identity producers differ.")
    if not _verify_machine_manifest(machine_manifest):
        raise ValueError("Machine manifest artifact closure failed.")
    replay_pass_count = _count_replay_passes(machine_report_path.parent, machine_report)
    if replay_pass_count != 30:
        raise ValueError("V2-R2 requires 30 semantic replay passes.")

    sampling_payload = {
        "parent_sampling_policy_sha256": file_sha256(parent_sampling_path),
        "automatic_promotion_gate": GateStatus.BLOCKED,
    }
    provisional_sampling = ReviewStudySamplingPolicyV2R2.model_construct(
        policy_id=stable_id("policy", {"pending": True}),
        **sampling_payload,
    )
    sampling = ReviewStudySamplingPolicyV2R2(
        policy_id=stable_id("policy", provisional_sampling.identity_payload()),
        **sampling_payload,
    )
    sampling_path = study_sampling_policy_output.resolve()
    write_json_atomic(
        sampling_path,
        sampling.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )

    parent_payload = {
        "base_review_parent_sha256": file_sha256(base_parent_path),
        "base_review_policy_sha256": file_sha256(base_policy_path),
        "effective_corpus_sha256": file_sha256(effective_path),
        "replacement_attempt_ledger_sha256": file_sha256(ledger_path),
        "source_snapshot_protocol_sha256": file_sha256(protocol_path),
        "snapshot_report_sha256": file_sha256(snapshot_report_path),
        "snapshot_manifest_sha256": file_sha256(snapshot_manifest_path),
        "machine_run_identity_sha256": file_sha256(run_identity_path),
        "machine_report_sha256": file_sha256(machine_report_path),
        "machine_manifest_sha256": file_sha256(machine_manifest_path),
        "producer": run_identity.producer,
        "automatic_promotion_gate": GateStatus.BLOCKED,
    }
    provisional_parent = HeldOutReviewExecutionParentV2R2.model_construct(
        parent_id=stable_id("manifest", {"pending": True}),
        **parent_payload,
    )
    parent = HeldOutReviewExecutionParentV2R2(
        parent_id=stable_id("manifest", provisional_parent.identity_payload()),
        **parent_payload,
    )
    parent_path = execution_parent_output.resolve()
    write_json_atomic(
        parent_path,
        parent.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )

    seed = _load_or_create_seed(restricted_seed_output.resolve())
    seed_sha256 = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    trial_payload = {
        "base_review_policy_sha256": file_sha256(base_policy_path),
        "execution_parent_sha256": file_sha256(parent_path),
        "study_sampling_policy_sha256": file_sha256(sampling_path),
        "blinding_seed_sha256": seed_sha256,
        "predecessor_trial_id": base_policy.trial_id,
        "successor_reason": ("predecessor-seed-preimage-unavailable-and-source-protocol-corrected"),
        "automatic_promotion_gate": GateStatus.BLOCKED,
    }
    provisional_trial = HeldOutReviewTrialInstanceV2R2.model_construct(
        trial_id=stable_id("review", {"pending": True}),
        **trial_payload,
    )
    trial = HeldOutReviewTrialInstanceV2R2(
        trial_id=stable_id("review", provisional_trial.identity_payload()),
        **trial_payload,
    )
    write_json_atomic(
        trial_instance_output.resolve(),
        trial.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    return sampling, parent, trial


def _load_or_create_seed(path: Path) -> str:
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        seed = str(payload.get("blinding_seed", ""))
        digest = str(payload.get("blinding_seed_sha256", ""))
        if len(seed) < 32 or hashlib.sha256(seed.encode("utf-8")).hexdigest() != digest:
            raise ValueError("Restricted blinding seed artifact is invalid.")
        return seed
    seed = secrets.token_hex(32)
    write_json_atomic(
        path,
        {
            "schema": "torii.corridor.restricted-blinding-seed/v2-r2",
            "blinding_seed": seed,
            "blinding_seed_sha256": hashlib.sha256(seed.encode("utf-8")).hexdigest(),
            "generated_once_before_sampling": True,
            "do_not_publish_with_reviewer_materials": True,
        },
        sort_keys=True,
    )
    return seed


def _verify_snapshot_manifest(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    artifacts = payload.get("artifacts", ())
    if not artifacts:
        raise ValueError("Snapshot manifest is empty.")
    for item in artifacts:
        artifact = Path(str(item["path"]))
        if not artifact.is_file() or file_sha256(artifact) != item["sha256"]:
            raise ValueError(f"Snapshot manifest artifact failed: {artifact}")


def _verify_machine_manifest(manifest: HeldOutCorpusMachineManifest) -> bool:
    return all((path := Path(item.path)).is_file() and file_sha256(path) == item.sha256 for item in manifest.artifacts)


def _count_replay_passes(
    machine_root: Path,
    report: HeldOutCorpusMachineReport,
) -> int:
    count = 0
    for result in report.results:
        path = machine_root / "cases" / result.corridor_key / "net-replay.json"
        replay = NetReplayReport.model_validate_json(path.read_text(encoding="utf-8"))
        count += replay.status is GateStatus.PASS
    return count
