from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256

from .enums import GateStatus
from .held_out_corpus_contracts import (
    CroppedCorridorSnapshot,
    HeldOutCorpusMachineManifest,
    HeldOutCorpusMachineReport,
    HeldOutCorpusSpec,
)
from .held_out_review_v2_contracts import (
    HeldOutReplacementAttemptLedgerV2,
    HeldOutReplacementPlanV2,
    HeldOutReplacementPolicyV2,
    HeldOutReserveCorpusV2,
    HeldOutSourceSnapshotProtocolV2,
    RankedReserveCandidateV2,
    ReplacementAttemptV2,
    ReplacementEvidenceArtifactV2,
    ReplacementSlotAttemptLedgerV2,
    ReplacementSlotPlanV2,
    SupersededReplacementExecutionV2,
)
from .ids import stable_id
from .net_replay import NetReplayReport
from .run_identity import HeldOutMachineRunIdentity


def build_deterministic_replacement_plan_v2(
    *,
    reserve_corpus_file: Path,
    replacement_policy_file: Path,
) -> HeldOutReplacementPlanV2:
    reserve_path = reserve_corpus_file.resolve()
    policy_path = replacement_policy_file.resolve()
    reserve = HeldOutReserveCorpusV2.model_validate_json(reserve_path.read_text(encoding="utf-8"))
    policy = HeldOutReplacementPolicyV2.model_validate_json(policy_path.read_text(encoding="utf-8"))
    if policy.reserve_corpus_sha256 != file_sha256(reserve_path):
        raise ValueError("Replacement policy is not bound to the reserve corpus.")
    if policy.parent_corpus_sha256 != reserve.parent_corpus_sha256:
        raise ValueError("Replacement policy and reserve parent corpus differ.")
    slots: list[ReplacementSlotPlanV2] = []
    for slot in reserve.slots:
        ranked = sorted(
            (
                _rank_candidate(
                    policy.public_selection_seed,
                    slot.invalid_corridor_key,
                    candidate.selection.corridor_key,
                    candidate.selection.selection_id,
                )
                for candidate in slot.candidates
            ),
            key=lambda item: item["ranking_digest"],
        )
        slots.append(
            ReplacementSlotPlanV2(
                invalid_corridor_key=slot.invalid_corridor_key,
                invalid_selection_id=slot.invalid_selection_id,
                ordered_candidates=tuple(
                    RankedReserveCandidateV2(rank=index, **candidate) for index, candidate in enumerate(ranked, start=1)
                ),
            )
        )
    payload = {
        "reserve_corpus_sha256": file_sha256(reserve_path),
        "replacement_policy_sha256": file_sha256(policy_path),
        "slots": tuple(slots),
        "automatic_promotion_gate": GateStatus.BLOCKED,
    }
    provisional = HeldOutReplacementPlanV2.model_construct(
        replacement_plan_id=stable_id("manifest", {"pending": True}),
        **payload,
    )
    return HeldOutReplacementPlanV2(
        replacement_plan_id=stable_id("manifest", provisional.identity_payload()),
        **payload,
    )


def _rank_candidate(
    seed: str,
    invalid_corridor_key: str,
    corridor_key: str,
    selection_id: str,
) -> dict[str, str]:
    value = f"{seed}|{invalid_corridor_key}|{selection_id}"
    return {
        "corridor_key": corridor_key,
        "selection_id": selection_id,
        "ranking_digest": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }


def freeze_replacement_execution_v2(
    *,
    base_corpus_file: Path,
    reserve_corpus_file: Path,
    replacement_plan_file: Path,
    source_snapshot_protocol_file: Path,
    reserve_snapshot_catalog_file: Path,
    machine_evidence_dirs_by_rank: Mapping[int, Path],
    evidence_root: Path,
    effective_corpus_output: Path,
    attempt_ledger_output: Path,
    superseded_machine_report_files: Sequence[Path] = (),
) -> tuple[HeldOutCorpusSpec, HeldOutReplacementAttemptLedgerV2]:
    """Freeze first-technically-valid replacements without consulting labels."""

    base_path = base_corpus_file.resolve(strict=True)
    reserve_path = reserve_corpus_file.resolve(strict=True)
    plan_path = replacement_plan_file.resolve(strict=True)
    protocol_path = source_snapshot_protocol_file.resolve(strict=True)
    catalog_path = reserve_snapshot_catalog_file.resolve(strict=True)
    root = evidence_root.resolve(strict=True)
    base = HeldOutCorpusSpec.model_validate_json(base_path.read_text(encoding="utf-8"))
    reserve = HeldOutReserveCorpusV2.model_validate_json(reserve_path.read_text(encoding="utf-8"))
    plan = HeldOutReplacementPlanV2.model_validate_json(plan_path.read_text(encoding="utf-8"))
    HeldOutSourceSnapshotProtocolV2.model_validate_json(protocol_path.read_text(encoding="utf-8"))
    if plan.reserve_corpus_sha256 != file_sha256(reserve_path):
        raise ValueError("Replacement plan does not bind the reserve corpus.")
    catalog_payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    snapshots = tuple(CroppedCorridorSnapshot.model_validate(item) for item in catalog_payload.get("snapshots", ()))
    snapshot_by_selection = {item.selection_id: item for item in snapshots}
    if len(snapshot_by_selection) != len(snapshots):
        raise ValueError("Reserve snapshot catalog contains duplicate selections.")
    selection_by_id = {
        candidate.selection.selection_id: candidate.selection for slot in reserve.slots for candidate in slot.candidates
    }
    plan_slot_by_key = {slot.invalid_corridor_key: slot for slot in plan.slots}
    resolved_slots: list[ReplacementSlotAttemptLedgerV2] = []
    selected_by_invalid_id: dict[str, str] = {}
    for invalid_key in sorted(plan_slot_by_key):
        slot = plan_slot_by_key[invalid_key]
        attempts: list[ReplacementAttemptV2] = []
        for ranked in slot.ordered_candidates:
            evidence_dir = machine_evidence_dirs_by_rank.get(ranked.rank)
            if evidence_dir is None:
                raise ValueError(f"Missing evidence directory for rank {ranked.rank}.")
            snapshot = snapshot_by_selection.get(ranked.selection_id)
            if snapshot is None:
                raise ValueError(f"Missing reserve snapshot for {ranked.selection_id}.")
            attempt = _build_replacement_attempt(
                invalid_corridor_key=invalid_key,
                ranked=ranked,
                snapshot=snapshot,
                source_snapshot_protocol_sha256=file_sha256(protocol_path),
                evidence_dir=evidence_dir.resolve(strict=True),
                evidence_root=root,
            )
            attempts.append(attempt)
            if attempt.technical_outcome == "eligible":
                break
        if not attempts or attempts[-1].technical_outcome != "eligible":
            raise ValueError(f"Replacement slot has no eligible candidate: {invalid_key}")
        selected = attempts[-1]
        selected_by_invalid_id[slot.invalid_selection_id] = selected.selection_id
        resolved_slots.append(
            ReplacementSlotAttemptLedgerV2(
                invalid_corridor_key=invalid_key,
                invalid_selection_id=slot.invalid_selection_id,
                attempts=tuple(attempts),
                selected_rank=selected.rank,
                selected_corridor_key=selected.corridor_key,
                selected_selection_id=selected.selection_id,
            )
        )
    effective = _build_effective_corpus(
        base=base,
        selection_by_id=selection_by_id,
        selected_by_invalid_id=selected_by_invalid_id,
    )
    effective_path = effective_corpus_output.resolve()
    write_json_atomic(
        effective_path,
        effective.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    superseded = ()
    if superseded_machine_report_files:
        superseded = (
            SupersededReplacementExecutionV2(
                source_snapshot_protocol=("bbox-intersection-without-roundabout-closure"),
                reason="source-snapshot-protocol-superseded",
                machine_report_sha256s=tuple(
                    file_sha256(path.resolve(strict=True)) for path in superseded_machine_report_files
                ),
            ),
        )
    ledger_payload = {
        "base_corpus_sha256": file_sha256(base_path),
        "reserve_corpus_sha256": file_sha256(reserve_path),
        "replacement_plan_sha256": file_sha256(plan_path),
        "source_snapshot_protocol_sha256": file_sha256(protocol_path),
        "effective_corpus_sha256": file_sha256(effective_path),
        "slots": tuple(resolved_slots),
        "superseded_executions": superseded,
        "automatic_promotion_gate": GateStatus.BLOCKED,
    }
    provisional = HeldOutReplacementAttemptLedgerV2.model_construct(
        ledger_id=stable_id("manifest", {"pending": True}),
        **ledger_payload,
    )
    ledger = HeldOutReplacementAttemptLedgerV2(
        ledger_id=stable_id("manifest", provisional.identity_payload()),
        **ledger_payload,
    )
    write_json_atomic(
        attempt_ledger_output.resolve(),
        ledger.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    return effective, ledger


def _build_replacement_attempt(
    *,
    invalid_corridor_key: str,
    ranked: RankedReserveCandidateV2,
    snapshot: CroppedCorridorSnapshot,
    source_snapshot_protocol_sha256: str,
    evidence_dir: Path,
    evidence_root: Path,
) -> ReplacementAttemptV2:
    report_path = evidence_dir / "held_out_corpus.machine-report.json"
    identity_path = evidence_dir / "held_out_corpus.run-identity.json"
    manifest_path = evidence_dir / "held_out_corpus.machine-manifest.json"
    replay_path = evidence_dir / "cases" / ranked.corridor_key / "net-replay.json"
    report = HeldOutCorpusMachineReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    identity = HeldOutMachineRunIdentity.model_validate_json(identity_path.read_text(encoding="utf-8"))
    manifest = HeldOutCorpusMachineManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    replay = NetReplayReport.model_validate_json(replay_path.read_text(encoding="utf-8"))
    result_by_selection = {item.selection_id: item for item in report.results}
    result = result_by_selection.get(ranked.selection_id)
    if result is None or result.corridor_key != ranked.corridor_key:
        raise ValueError(f"Machine report lacks ranked candidate {ranked.corridor_key}.")
    closure_complete = _manifest_artifacts_close(manifest)
    if snapshot.status is not GateStatus.PASS:
        outcome = "failed"
        failure_reason = "source-extraction-failed"
    elif not closure_complete:
        outcome = "failed"
        failure_reason = "artifact-closure-incomplete"
    elif replay.status is not GateStatus.PASS:
        outcome = "failed"
        failure_reason = "semantic-replay-invalid"
    elif result.pipeline_status is GateStatus.PASS:
        outcome = "eligible"
        failure_reason = None
    else:
        outcome = "failed"
        failure_reason = "netconvert-build-failed"
    artifact_paths = {
        "source-snapshot": Path(snapshot.path).resolve(strict=True),
        "run-identity": identity_path,
        "machine-report": report_path,
        "net-replay-report": replay_path,
        "machine-manifest": manifest_path,
    }
    artifacts = tuple(
        ReplacementEvidenceArtifactV2(
            role=role,
            path=path.relative_to(evidence_root).as_posix(),
            sha256=file_sha256(path),
        )
        for role, path in sorted(artifact_paths.items())
    )
    attempt_payload = {
        "invalid_corridor_key": invalid_corridor_key,
        "rank": ranked.rank,
        "corridor_key": ranked.corridor_key,
        "selection_id": ranked.selection_id,
        "source_snapshot_protocol_sha256": source_snapshot_protocol_sha256,
        "producer": identity.producer,
        "snapshot_status": snapshot.status,
        "semantic_replay_status": replay.status,
        "artifact_closure_complete": closure_complete,
        "technical_outcome": outcome,
        "failure_reason": failure_reason,
        "artifacts": artifacts,
    }
    provisional = ReplacementAttemptV2.model_construct(
        attempt_id=stable_id("manifest", {"pending": True}),
        **attempt_payload,
    )
    return ReplacementAttemptV2(
        attempt_id=stable_id("manifest", provisional.identity_payload()),
        **attempt_payload,
    )


def _build_effective_corpus(
    *,
    base: HeldOutCorpusSpec,
    selection_by_id: Mapping[str, object],
    selected_by_invalid_id: Mapping[str, str],
) -> HeldOutCorpusSpec:
    corridors = [case for case in base.corridors if case.selection_id not in selected_by_invalid_id]
    corridors.extend(selection_by_id[selected_id] for selected_id in selected_by_invalid_id.values())
    corridors.sort(key=lambda item: item.corridor_key)
    identity_payload = base.identity_payload()
    identity_payload["corridors"] = [item.model_dump(mode="json", by_alias=True) for item in corridors]
    payload = base.model_dump(
        mode="json",
        by_alias=True,
        exclude={"corpus_id", "corridors"},
    )
    return HeldOutCorpusSpec(
        corpus_id=stable_id("manifest", identity_payload),
        corridors=tuple(corridors),
        **payload,
    )


def _manifest_artifacts_close(manifest: HeldOutCorpusMachineManifest) -> bool:
    return all((path := Path(item.path)).is_file() and file_sha256(path) == item.sha256 for item in manifest.artifacts)
