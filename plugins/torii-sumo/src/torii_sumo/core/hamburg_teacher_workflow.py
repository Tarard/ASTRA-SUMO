from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
from pathlib import Path
import shutil
from typing import Any

from .command_runner import run_command
from .hamburg_shared_teacher import materialize_hamburg_shared_teacher
from .hamburg_teacher_cell import HamburgTeacherCellContract
from .osm_workflow import _run_direct_local_teacher_replay


TeacherMaterializer = Callable[..., Mapping[str, object]]
ReplayRunner = Callable[..., Mapping[str, object]]


_SHARED_REPLAY_DELEGATED_GATE_PREFIXES = (
    "official_approach_candidate_lanes_unresolved:",
    "official_approach_candidate_edges_mismatch:",
    "candidate_boundary_passenger_lane_subset:",
    "candidate_boundary_edge_reused_by_multiple_teacher_edges:",
)


def run_hamburg_teacher_replay_workflow(
    *,
    source_net_file: Path,
    contracts: Sequence[HamburgTeacherCellContract],
    output_dir: Path,
    prefix: str = "hamburg_teacher_replay",
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 120.0,
    command_runner: Callable[..., Any] = run_command,
    single_teacher_materializer: TeacherMaterializer | None = None,
    shared_teacher_materializer: TeacherMaterializer | None = None,
    replay_runner: ReplayRunner = _run_direct_local_teacher_replay,
) -> dict[str, object]:
    """Sequentially apply official Hamburg teachers through Torii's native replay.

    This module deliberately owns only orchestration.  Teacher construction is
    delegated to the Hamburg adapters and every network mutation, normalization,
    SUMO load, routeability smoke, and via-semantics gate remains inside Torii's
    existing ``_run_direct_local_teacher_replay`` pipeline.
    """

    source_net_file = source_net_file.resolve()
    output_dir = output_dir.resolve()
    report_file = output_dir / f"{prefix}_report.json"
    if not source_net_file.is_file():
        return _write_workflow_report(
            report_file,
            {
                "status": "blocked",
                "claim_status": "blocked",
                "reason": "source_net_file_missing",
                "source_net_file": str(source_net_file),
                "stage_reports": [],
                "final_net_file": None,
            },
        )
    if not contracts:
        return _write_workflow_report(
            report_file,
            {
                "status": "blocked",
                "claim_status": "blocked",
                "reason": "hamburg_teacher_contracts_missing",
                "source_net_file": str(source_net_file),
                "stage_reports": [],
                "final_net_file": None,
            },
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    single_materializer = single_teacher_materializer or _default_single_teacher_materializer
    shared_materializer = shared_teacher_materializer or materialize_hamburg_shared_teacher
    current_net_file = source_net_file
    stage_reports: list[dict[str, object]] = []
    seen_controller_ids: set[str] = set()

    for stage_index, contract in enumerate(contracts, start=1):
        controller_id = _contract_controller_id(contract)
        shared_controller = len(contract.approach_components) > 1
        stage_dir = output_dir / f"stage_{stage_index:03d}"
        stage_dir.mkdir(parents=True, exist_ok=True)
        stage_report_file = stage_dir / "stage_report.json"
        stage_report: dict[str, object] = {
            "stage_index": stage_index,
            "controller_id": controller_id,
            "input_net_file": str(current_net_file),
            "teacher_mode": "shared_controller" if shared_controller else "single_cell",
            "contract_topology_status": contract.topology_status,
            "contract_review_gates": list(contract.review_gates),
            "delegated_contract_review_gates": _delegated_contract_review_gates(
                contract,
                shared_controller=shared_controller,
            ),
            "exact_signal_replay_gates": list(contract.exact_signal_replay_gates),
            "status": "blocked",
        }

        preflight_reason = _contract_preflight_reason(
            contract,
            controller_id=controller_id,
            shared_controller=shared_controller,
            seen_controller_ids=seen_controller_ids,
        )
        if preflight_reason:
            stage_report["reason"] = preflight_reason
            _write_json(stage_report_file, stage_report)
            stage_report["stage_report_file"] = str(stage_report_file)
            stage_reports.append(stage_report)
            return _blocked_workflow_report(
                report_file=report_file,
                source_net_file=source_net_file,
                current_net_file=current_net_file,
                stage_reports=stage_reports,
                controller_id=controller_id,
                reason=preflight_reason,
            )
        seen_controller_ids.add(controller_id)

        materializer = shared_materializer if shared_controller else single_materializer
        try:
            materialization = dict(
                materializer(
                    contract=contract,
                    output_dir=stage_dir / "teacher",
                    prefix=f"{prefix}_stage_{stage_index:03d}_teacher",
                )
            )
        except Exception as exc:
            stage_report.update(
                {
                    "reason": "teacher_materialization_exception",
                    "teacher_materialization_error": f"{type(exc).__name__}: {exc}",
                }
            )
            _write_json(stage_report_file, stage_report)
            stage_report["stage_report_file"] = str(stage_report_file)
            stage_reports.append(stage_report)
            return _blocked_workflow_report(
                report_file=report_file,
                source_net_file=source_net_file,
                current_net_file=current_net_file,
                stage_reports=stage_reports,
                controller_id=controller_id,
                reason="teacher_materialization_exception",
            )
        stage_report["teacher_materialization"] = materialization
        if materialization.get("status") != "pass":
            stage_report["reason"] = "teacher_materialization_not_pass"
            _write_json(stage_report_file, stage_report)
            stage_report["stage_report_file"] = str(stage_report_file)
            stage_reports.append(stage_report)
            return _blocked_workflow_report(
                report_file=report_file,
                source_net_file=source_net_file,
                current_net_file=current_net_file,
                stage_reports=stage_reports,
                controller_id=controller_id,
                reason="teacher_materialization_not_pass",
            )

        teacher_net_file = _materialized_teacher_net_file(materialization)
        if teacher_net_file is None or not teacher_net_file.is_file():
            stage_report["reason"] = "materialized_teacher_net_file_missing"
            _write_json(stage_report_file, stage_report)
            stage_report["stage_report_file"] = str(stage_report_file)
            stage_reports.append(stage_report)
            return _blocked_workflow_report(
                report_file=report_file,
                source_net_file=source_net_file,
                current_net_file=current_net_file,
                stage_reports=stage_reports,
                controller_id=controller_id,
                reason="materialized_teacher_net_file_missing",
            )

        teacher_controller_id = str(
            materialization.get("teacher_controller_id") or controller_id
        ).strip()
        queue_report = _build_native_replay_queue(
            contract=contract,
            teacher_net_file=teacher_net_file,
            teacher_controller_id=teacher_controller_id,
            queue_file=stage_dir / "native_replay_queue.json",
            shared_controller=shared_controller,
        )
        if queue_report.get("status") == "blocked":
            stage_report.update(
                {
                    "reason": str(queue_report.get("reason", "native_replay_queue_blocked")),
                    "native_replay_queue": queue_report,
                }
            )
            _write_json(stage_report_file, stage_report)
            stage_report["stage_report_file"] = str(stage_report_file)
            stage_reports.append(stage_report)
            return _blocked_workflow_report(
                report_file=report_file,
                source_net_file=source_net_file,
                current_net_file=current_net_file,
                stage_reports=stage_reports,
                controller_id=controller_id,
                reason=str(queue_report.get("reason", "native_replay_queue_blocked")),
            )

        queue_file = Path(str(queue_report["queue_file"]))
        _write_json(queue_file, queue_report)
        stage_report["native_replay_queue_file"] = str(queue_file)

        shared_source_net_file: Path | None = None
        if shared_controller:
            # Torii's shared-controller safety path requires a distinct promotion
            # candidate.  This is a byte-identical staging copy; Torii still owns
            # every semantic network change and every validation gate.
            shared_source_net_file = stage_dir / "shared_controller_source.net.xml"
            shutil.copy2(current_net_file, shared_source_net_file)
            stage_report["shared_controller_source_net_file"] = str(shared_source_net_file)

        try:
            replay = dict(
                replay_runner(
                    queue_report=queue_report,
                    source_net_file=current_net_file,
                    output_dir=stage_dir / "native_replay",
                    prefix=f"{prefix}_stage_{stage_index:03d}",
                    netconvert_binary=netconvert_binary,
                    sumo_binary=sumo_binary,
                    timeout_seconds=timeout_seconds,
                    command_runner=command_runner,
                    shared_controller_source_net_file=shared_source_net_file,
                )
            )
        except Exception as exc:
            stage_report.update(
                {
                    "reason": "native_teacher_replay_exception",
                    "native_teacher_replay_error": f"{type(exc).__name__}: {exc}",
                }
            )
            _write_json(stage_report_file, stage_report)
            stage_report["stage_report_file"] = str(stage_report_file)
            stage_reports.append(stage_report)
            return _blocked_workflow_report(
                report_file=report_file,
                source_net_file=source_net_file,
                current_net_file=current_net_file,
                stage_reports=stage_reports,
                controller_id=controller_id,
                reason="native_teacher_replay_exception",
            )

        stage_report["native_teacher_replay"] = replay
        final_net_file = _replay_final_net_file(replay)
        if replay.get("status") != "pass" or final_net_file is None or not final_net_file.is_file():
            stage_report["reason"] = (
                "native_teacher_replay_not_pass"
                if replay.get("status") != "pass"
                else "native_teacher_replay_final_net_file_missing"
            )
            _write_json(stage_report_file, stage_report)
            stage_report["stage_report_file"] = str(stage_report_file)
            stage_reports.append(stage_report)
            return _blocked_workflow_report(
                report_file=report_file,
                source_net_file=source_net_file,
                current_net_file=current_net_file,
                stage_reports=stage_reports,
                controller_id=controller_id,
                reason=str(stage_report["reason"]),
            )

        current_net_file = final_net_file.resolve()
        stage_report.update(
            {
                "status": "pass",
                "claim_status": "diagnostic-demo",
                "final_net_file": str(current_net_file),
            }
        )
        _write_json(stage_report_file, stage_report)
        stage_report["stage_report_file"] = str(stage_report_file)
        stage_reports.append(stage_report)

    return _write_workflow_report(
        report_file,
        {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "source_net_file": str(source_net_file),
            "controller_count": len(stage_reports),
            "stage_reports": stage_reports,
            "final_net_file": str(current_net_file),
            "policy": (
                "contracts are applied in input order; every stage starts from the prior "
                "native replay final_net_file; any non-pass stage stops promotion"
            ),
        },
    )


def _default_single_teacher_materializer(**kwargs: object) -> Mapping[str, object]:
    # Imported lazily so this thin orchestrator can land independently while the
    # Hamburg single-cell adapter evolves behind its public function boundary.
    from . import hamburg_teacher_cell

    materializer = getattr(
        hamburg_teacher_cell,
        "materialize_hamburg_single_teacher_cell",
        None,
    ) or getattr(hamburg_teacher_cell, "materialize_hamburg_teacher_cell", None)
    if materializer is None:
        return {"status": "blocked", "reason": "single_teacher_materializer_unavailable"}
    return materializer(**kwargs)


def _contract_controller_id(contract: HamburgTeacherCellContract) -> str:
    return str(contract.ir.control.tls_id or contract.ir.core.core_id).strip()


def _contract_preflight_reason(
    contract: HamburgTeacherCellContract,
    *,
    controller_id: str,
    shared_controller: bool,
    seen_controller_ids: set[str],
) -> str | None:
    if not controller_id:
        return "teacher_controller_id_missing"
    if controller_id in seen_controller_ids:
        return "duplicate_teacher_controller_id"
    if not contract.candidate_junction_id:
        return "candidate_controller_id_missing"
    if not contract.candidate_junction_ids:
        return "candidate_junction_scope_missing"
    blocking_review_gates = _blocking_contract_review_gates(
        contract,
        shared_controller=shared_controller,
    )
    if blocking_review_gates:
        return "teacher_contract_review_gates_not_resolved"
    if not shared_controller and contract.topology_status != "ready_for_scoped_teacher_replay":
        return "teacher_contract_topology_not_ready"
    if shared_controller and len(contract.candidate_junction_ids) <= 1:
        return "shared_controller_candidate_scope_not_expanded"
    return None


def _delegated_contract_review_gates(
    contract: HamburgTeacherCellContract,
    *,
    shared_controller: bool,
) -> list[str]:
    if not shared_controller:
        return []
    return [
        gate
        for gate in contract.review_gates
        if gate.startswith(
            "multiple_official_approach_cells_require_shared_controller_replay:"
        )
        or gate.startswith(_SHARED_REPLAY_DELEGATED_GATE_PREFIXES)
    ]


def _blocking_contract_review_gates(
    contract: HamburgTeacherCellContract,
    *,
    shared_controller: bool,
) -> list[str]:
    delegated = set(
        _delegated_contract_review_gates(
            contract,
            shared_controller=shared_controller,
        )
    )
    return [gate for gate in contract.review_gates if gate not in delegated]


def _materialized_teacher_net_file(materialization: Mapping[str, object]) -> Path | None:
    value = materialization.get("grouped_teacher_net_file") or materialization.get(
        "teacher_net_file"
    )
    if not value:
        return None
    return Path(str(value)).resolve()


def _build_native_replay_queue(
    *,
    contract: HamburgTeacherCellContract,
    teacher_net_file: Path,
    teacher_controller_id: str,
    queue_file: Path,
    shared_controller: bool,
) -> dict[str, object]:
    approach_pairs = [dict(pair) for pair in contract.approach_pairs]
    if not approach_pairs:
        return {
            "status": "blocked",
            "reason": "official_candidate_approach_pairs_missing",
            "queue_file": str(queue_file),
        }
    if any(
        not str(pair.get("reference_edge_id", "")).strip()
        or not str(pair.get("candidate_edge_id", "")).strip()
        for pair in approach_pairs
    ):
        return {
            "status": "blocked",
            "reason": "official_candidate_approach_pair_incomplete",
            "queue_file": str(queue_file),
        }

    candidate_junction_ids = sorted(
        {str(value) for value in contract.candidate_junction_ids if str(value)}
    )
    scoped_candidate = len(candidate_junction_ids) > 1
    if shared_controller and not scoped_candidate:
        return {
            "status": "blocked",
            "reason": "shared_controller_candidate_scope_not_expanded",
            "queue_file": str(queue_file),
        }
    candidate: dict[str, object] = {
        "candidate_status": (
            "needs_expanded_rebuild_scope"
            if scoped_candidate
            else "ready_for_teacher_guided_variant"
        ),
        "tls_reference_tl_id": teacher_controller_id,
        "tls_candidate_tl_id": contract.candidate_junction_id,
        "tls_candidate_junction_ids": candidate_junction_ids,
        "tls_approach_pairs": approach_pairs,
    }
    if not scoped_candidate:
        candidate.update(
            {
                "junction_id": contract.candidate_junction_id,
                "reference_id": teacher_controller_id,
                "edge_map": {
                    str(pair["reference_edge_id"]): str(pair["candidate_edge_id"])
                    for pair in approach_pairs
                },
            }
        )
    return {
        "schema": "torii.hamburg-native-replay-queue/v1",
        "teacher_net_file": str(teacher_net_file),
        "queue_file": str(queue_file),
        "ready_candidate_count": 0 if scoped_candidate else 1,
        "expanded_scope_candidate_count": 1 if scoped_candidate else 0,
        "repair_candidate_count": 1,
        "repair_candidates": [candidate],
    }


def _replay_final_net_file(replay: Mapping[str, object]) -> Path | None:
    value = replay.get("final_net_file") or replay.get("variant_file")
    if not value:
        return None
    return Path(str(value)).resolve()


def _blocked_workflow_report(
    *,
    report_file: Path,
    source_net_file: Path,
    current_net_file: Path,
    stage_reports: list[dict[str, object]],
    controller_id: str,
    reason: str,
) -> dict[str, object]:
    return _write_workflow_report(
        report_file,
        {
            "status": "blocked",
            "claim_status": "blocked",
            "reason": reason,
            "blocked_controller_id": controller_id,
            "source_net_file": str(source_net_file),
            "last_passed_net_file": (
                str(current_net_file) if current_net_file != source_net_file else None
            ),
            "stage_reports": stage_reports,
            "final_net_file": None,
            "policy": "fail-closed; no partial composite is promoted",
        },
    )


def _write_workflow_report(
    report_file: Path,
    report: dict[str, object],
) -> dict[str, object]:
    report["report_file"] = str(report_file)
    _write_json(report_file, report)
    return report


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
