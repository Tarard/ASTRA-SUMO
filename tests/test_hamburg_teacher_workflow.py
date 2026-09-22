from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from torii_sumo.core.hamburg_teacher_workflow import (
    run_hamburg_teacher_replay_workflow,
)


def test_hamburg_teacher_replay_is_sequential_and_promotes_last_native_net(
    tmp_path: Path,
) -> None:
    source_net = _write_net(tmp_path / "source.net.xml", "source")
    materialized_controllers: list[str] = []
    replay_calls: list[dict[str, object]] = []

    def materialize(**kwargs: object) -> dict[str, object]:
        contract = kwargs["contract"]
        output_dir = Path(str(kwargs["output_dir"]))
        controller_id = contract.ir.control.tls_id
        materialized_controllers.append(controller_id)
        teacher_net = _write_net(output_dir / "grouped_teacher.net.xml", controller_id)
        return {
            "status": "pass",
            "teacher_controller_id": controller_id,
            "grouped_teacher_net_file": str(teacher_net),
        }

    def replay(**kwargs: object) -> dict[str, object]:
        replay_calls.append(kwargs)
        output_dir = Path(str(kwargs["output_dir"]))
        final_net = _write_net(
            output_dir / "normalized.net.xml",
            f"stage-{len(replay_calls)}",
        )
        return {"status": "pass", "final_net_file": str(final_net)}

    report = run_hamburg_teacher_replay_workflow(
        source_net_file=source_net,
        contracts=[_contract("HH_2421"), _contract("HH_2394")],
        output_dir=tmp_path / "workflow",
        single_teacher_materializer=materialize,
        shared_teacher_materializer=_must_not_materialize,
        replay_runner=replay,
    )

    assert report["status"] == "pass"
    assert materialized_controllers == ["HH_2421", "HH_2394"]
    assert len(replay_calls) == 2
    assert Path(str(replay_calls[0]["source_net_file"])) == source_net.resolve()
    first_final = Path(
        str(report["stage_reports"][0]["native_teacher_replay"]["final_net_file"])
    ).resolve()
    assert Path(str(replay_calls[1]["source_net_file"])) == first_final
    assert Path(str(report["final_net_file"])) == Path(
        str(report["stage_reports"][1]["final_net_file"])
    )
    assert Path(str(report["report_file"])).is_file()
    assert all(
        Path(str(stage["stage_report_file"])).is_file()
        for stage in report["stage_reports"]
    )


def test_hamburg_teacher_replay_stops_at_first_blocked_native_stage(
    tmp_path: Path,
) -> None:
    source_net = _write_net(tmp_path / "source.net.xml", "source")
    materialize_calls: list[str] = []
    replay_calls: list[str] = []

    def materialize(**kwargs: object) -> dict[str, object]:
        contract = kwargs["contract"]
        controller_id = contract.ir.control.tls_id
        materialize_calls.append(controller_id)
        teacher_net = _write_net(
            Path(str(kwargs["output_dir"])) / "teacher.net.xml",
            controller_id,
        )
        return {
            "status": "pass",
            "teacher_controller_id": controller_id,
            "teacher_net_file": str(teacher_net),
        }

    def replay(**kwargs: object) -> dict[str, object]:
        queue = kwargs["queue_report"]
        controller_id = queue["repair_candidates"][0]["tls_reference_tl_id"]
        replay_calls.append(controller_id)
        if controller_id == "HH_2394":
            return {"status": "blocked", "reason": "native_gate_failed"}
        final_net = _write_net(
            Path(str(kwargs["output_dir"])) / "normalized.net.xml",
            controller_id,
        )
        return {"status": "pass", "final_net_file": str(final_net)}

    report = run_hamburg_teacher_replay_workflow(
        source_net_file=source_net,
        contracts=[_contract("HH_2421"), _contract("HH_2394"), _contract("HH_228")],
        output_dir=tmp_path / "workflow",
        single_teacher_materializer=materialize,
        shared_teacher_materializer=materialize,
        replay_runner=replay,
    )

    assert report["status"] == "blocked"
    assert report["blocked_controller_id"] == "HH_2394"
    assert report["final_net_file"] is None
    assert Path(str(report["last_passed_net_file"])).is_file()
    assert materialize_calls == ["HH_2421", "HH_2394"]
    assert replay_calls == ["HH_2421", "HH_2394"]
    assert len(report["stage_reports"]) == 2


def test_hamburg_228_uses_shared_teacher_and_native_shared_source(
    tmp_path: Path,
) -> None:
    source_net = _write_net(tmp_path / "source.net.xml", "source")
    shared_calls: list[dict[str, object]] = []
    replay_calls: list[dict[str, object]] = []

    def shared_materialize(**kwargs: object) -> dict[str, object]:
        shared_calls.append(kwargs)
        teacher_net = _write_net(
            Path(str(kwargs["output_dir"])) / "shared_teacher.net.xml",
            "HH_228",
        )
        return {
            "status": "pass",
            "teacher_controller_id": "HH_228",
            "grouped_teacher_net_file": str(teacher_net),
        }

    def replay(**kwargs: object) -> dict[str, object]:
        replay_calls.append(kwargs)
        final_net = _write_net(
            Path(str(kwargs["output_dir"])) / "normalized.net.xml",
            "HH_228",
        )
        return {"status": "pass", "final_net_file": str(final_net)}

    contract = _contract("HH_228", component_count=4)
    contract.review_gates = (
        *contract.review_gates,
        "official_approach_candidate_edges_mismatch:ingress:4:edges=['a','b']",
        "candidate_boundary_passenger_lane_subset:a:used=[0]:expected=[0,1]",
    )

    report = run_hamburg_teacher_replay_workflow(
        source_net_file=source_net,
        contracts=[contract],
        output_dir=tmp_path / "workflow",
        single_teacher_materializer=_must_not_materialize,
        shared_teacher_materializer=shared_materialize,
        replay_runner=replay,
    )

    assert report["status"] == "pass"
    assert len(shared_calls) == 1
    assert len(replay_calls) == 1
    queue = replay_calls[0]["queue_report"]
    candidate = queue["repair_candidates"][0]
    assert candidate["candidate_status"] == "needs_expanded_rebuild_scope"
    assert queue["expanded_scope_candidate_count"] == 1
    shared_source = Path(str(replay_calls[0]["shared_controller_source_net_file"]))
    assert shared_source.is_file()
    assert shared_source.read_bytes() == source_net.read_bytes()
    assert Path(str(replay_calls[0]["source_net_file"])) == source_net.resolve()
    assert report["stage_reports"][0]["teacher_mode"] == "shared_controller"
    assert len(report["stage_reports"][0]["delegated_contract_review_gates"]) == 3


def test_hamburg_shared_teacher_blocks_unresolved_boundary_review_gate(
    tmp_path: Path,
) -> None:
    source_net = _write_net(tmp_path / "source.net.xml", "source")
    contract = _contract("HH_228", component_count=4)
    contract.review_gates = (
        *contract.review_gates,
        "missing_official_movement_paths:1",
    )

    report = run_hamburg_teacher_replay_workflow(
        source_net_file=source_net,
        contracts=[contract],
        output_dir=tmp_path / "workflow",
        single_teacher_materializer=_must_not_materialize,
        shared_teacher_materializer=_must_not_materialize,
        replay_runner=_must_not_replay,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "teacher_contract_review_gates_not_resolved"
    assert report["stage_reports"][0]["teacher_mode"] == "shared_controller"


def _contract(controller_id: str, *, component_count: int = 1) -> SimpleNamespace:
    candidate_id = f"candidate_{controller_id.removeprefix('HH_')}"
    review_gates = (
        (f"multiple_official_approach_cells_require_shared_controller_replay:{component_count}",)
        if component_count > 1
        else ()
    )
    return SimpleNamespace(
        ir=SimpleNamespace(
            control=SimpleNamespace(tls_id=controller_id),
            core=SimpleNamespace(core_id=controller_id),
        ),
        approach_components=tuple(object() for _ in range(component_count)),
        approach_pairs=(
            {
                "reference_edge_id": f"{controller_id}__teacher_ingress_00",
                "candidate_edge_id": f"{candidate_id}_in",
            },
            {
                "reference_edge_id": f"{controller_id}__teacher_egress_00",
                "candidate_edge_id": f"{candidate_id}_out",
            },
        ),
        candidate_junction_id=candidate_id,
        candidate_junction_ids=(candidate_id, f"{candidate_id}_member"),
        topology_status=(
            "blocked" if component_count > 1 else "ready_for_scoped_teacher_replay"
        ),
        review_gates=review_gates,
        exact_signal_replay_gates=(
            "topology_basis_phases_are_not_an_official_signal_program",
        ),
    )


def _write_net(path: Path, marker: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'<net marker="{marker}"/>', encoding="utf-8")
    return path.resolve()


def _must_not_materialize(**_kwargs: object) -> dict[str, object]:
    raise AssertionError("unexpected materializer call")


def _must_not_replay(**_kwargs: object) -> dict[str, object]:
    raise AssertionError("unexpected replay call")
