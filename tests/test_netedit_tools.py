from __future__ import annotations

import hashlib
from pathlib import Path

import anyio
import pytest

import torii_sumo.tools.netedit_tools as tools


_REAL_FINALIZE_AUDITS = tools._finalize_audits


class _Session:
    instances: list[_Session] = []

    def __init__(self, source, candidate, output_dir, **kwargs):
        self.source = Path(source)
        self.candidate = Path(candidate)
        self.output_dir = Path(output_dir)
        self.kwargs = kwargs
        self.target_source_junction_ids = tuple(kwargs.get("target_source_junction_ids", ()))
        self.target_candidate_junction_ids = tuple(kwargs.get("target_candidate_junction_ids", ()))
        self.actions = []
        self.__class__.instances.append(self)

    def open(self):
        self.candidate.write_bytes(self.source.read_bytes())
        return {"screenshot_sha256": "1" * 64}

    def observe(self, label):
        return {"label": label, "screenshot_sha256": "2" * 64}

    def act(self, action):
        self.actions.append(action)
        return {"screenshot_sha256": "3" * 64}

    def finalize(self, *, expected_screenshot_sha256):
        return {"status": "finalized", "expected_screenshot_sha256": expected_screenshot_sha256}

    def abort(self, reason):
        return {"status": "aborted", "reason": reason}


@pytest.fixture(autouse=True)
def _isolated_session(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tools, "NeteditTargetSession", _Session)
    monkeypatch.setattr(
        tools,
        "_finalize_audits",
        lambda _session: {
            "schema": "torii.netedit-finalize-audits/v1",
            "status": "review_required",
            "machine_gate_status": "pass",
            "automatic_promotion_gate": "blocked",
        },
    )
    tools._ACTIVE_SESSION = None
    tools._ACTIVE_SESSION_ID = ""
    _Session.instances.clear()
    yield
    tools._ACTIVE_SESSION = None
    tools._ACTIVE_SESSION_ID = ""


def _open(tmp_path: Path) -> dict[str, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.net.xml"
    source.write_text(
        "<net>"
        '<junction id="j1" type="priority"/>'
        '<edge id="e1"><lane id="e1_0" index="0"/></edge>'
        '<connection from="e1" fromLane="0" to="e2" toLane="0"/>'
        '<tlLogic id="t1"><phase duration="30" state="r"/></tlLogic>'
        "</net>",
        encoding="utf-8",
    )
    return tools.sumo_netedit_session(
        operation="open",
        source_net_file=str(source),
        candidate_net_file=str(tmp_path / "candidate.net.xml"),
        output_dir=str(tmp_path / "session"),
        expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )


def test_grouped_session_is_singleton_hash_bound_and_returns_persisted_scene(tmp_path: Path) -> None:
    opened = _open(tmp_path)
    session_id = str(opened["session_id"])

    duplicate = _open(tmp_path / "other")
    wrong = tools.sumo_netedit_session(operation="observe", session_id="wrong")
    observed = tools.sumo_netedit_session(
        operation="observe",
        session_id=session_id,
        label="inspect j1",
        object_type="junction",
        object_id="j1",
    )

    assert opened["status"] == "pass"
    assert duplicate["status"] == "blocked"
    assert wrong["status"] == "blocked"
    assert observed["persisted_scene"]["scope"] == "persisted_candidate_on_disk"
    assert observed["persisted_scene"]["counts"] == {
        "junction": 1,
        "edge": 1,
        "lane": 1,
        "connection": 1,
        "tlLogic": 1,
    }
    assert observed["persisted_scene"]["object"]["attributes"]["id"] == "j1"
    assert observed["persisted_scene"]["live_unsaved_gui_state_included"] is False
    assert observed["automatic_promotion_gate"] == "blocked"


def test_persisted_tllogic_identity_includes_program_id(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    candidate.write_text(
        "<net>"
        '<tlLogic id="tls" programID="weekday"><phase duration="30" state="r"/></tlLogic>'
        '<tlLogic id="tls" programID="weekend"><phase duration="20" state="G"/></tlLogic>'
        "</net>",
        encoding="utf-8",
    )

    report = tools._persisted_scene(
        candidate,
        object_type="tlLogic",
        object_id="tls|weekend",
    )

    assert report["object"]["attributes"]["programID"] == "weekend"
    with pytest.raises(ValueError, match="must match exactly once"):
        tools._persisted_scene(candidate, object_type="tlLogic", object_id="tls")


def test_fastmcp_schema_constrains_netedit_operation_action_and_object_type() -> None:
    from torii_sumo.server import create_server

    async def schema() -> dict[str, object]:
        registered = await create_server("legacy").list_tools()
        return next(tool.inputSchema for tool in registered if tool.name == "sumo_netedit_session")

    properties = anyio.run(schema)["properties"]

    assert set(properties["operation"]["enum"]) == {"open", "observe", "act", "finalize", "abort"}
    assert "netedit_binary" not in properties
    assert set(properties["action"]["anyOf"][0]["enum"]) == {"click", "drag", *tools._SHORTCUTS}
    assert set(properties["object_type"]["anyOf"][0]["enum"]) == {
        "junction",
        "edge",
        "lane",
        "connection",
        "tlLogic",
    }


def test_act_maps_only_one_named_atomic_action_and_finalize_releases_session(tmp_path: Path) -> None:
    opened = _open(tmp_path)
    session_id = str(opened["session_id"])
    acted = tools.sumo_netedit_session(
        operation="act",
        session_id=session_id,
        action="join_selected_junctions",
        expected_screenshot_sha256="1" * 64,
    )
    finalized = tools.sumo_netedit_session(
        operation="finalize",
        session_id=session_id,
        expected_screenshot_sha256="3" * 64,
    )
    reopened = _open(tmp_path / "next")

    assert acted["status"] == "pass"
    assert _Session.instances[0].actions == [
        {
            "type": "key",
            "virtual_key": 0x76,
            "modifier_keys": [],
            "expected_screenshot_sha256": "1" * 64,
        }
    ]
    assert finalized["session_state"] == "finalized"
    assert finalized["operation_status"] == "pass"
    assert finalized["status"] == "review_required"
    assert finalized["result"]["finalize_audits"]["status"] == "review_required"
    assert finalized["automatic_promotion_gate"] == "blocked"
    assert reopened["status"] == "pass"


def test_invalid_action_is_retryable_without_orphaning_session(tmp_path: Path) -> None:
    opened = _open(tmp_path)
    session_id = str(opened["session_id"])
    invalid = tools.sumo_netedit_session(
        operation="act",
        session_id=session_id,
        action="execute_arbitrary_code",
        expected_screenshot_sha256="1" * 64,
    )
    assert invalid["status"] == "blocked"
    assert "unsupported NetEdit action" in invalid["reason"]
    assert "cleanup" not in invalid
    assert tools._ACTIVE_SESSION is _Session.instances[0]
    observed = tools.sumo_netedit_session(operation="observe", session_id=session_id)
    assert observed["status"] == "pass"
    assert tools.sumo_netedit_session(operation="abort", session_id=session_id)["status"] == "pass"


def test_act_preflight_value_error_keeps_session_available_for_observe(tmp_path: Path) -> None:
    opened = _open(tmp_path)
    session_id = str(opened["session_id"])

    def fail_preflight(_action):
        raise ValueError("action requires the exact latest screenshot SHA-256")

    _Session.instances[0].act = fail_preflight
    result = tools.sumo_netedit_session(
        operation="act",
        session_id=session_id,
        action="click",
        x=10,
        y=20,
        expected_screenshot_sha256="1" * 64,
    )

    assert result["status"] == "blocked"
    assert "cleanup" not in result
    assert tools.sumo_netedit_session(operation="observe", session_id=session_id)["status"] == "pass"
    assert tools.sumo_netedit_session(operation="abort", session_id=session_id)["status"] == "pass"


def test_act_delivery_runtime_failure_aborts_session(tmp_path: Path) -> None:
    opened = _open(tmp_path)
    session_id = str(opened["session_id"])

    def fail_delivery(_action):
        raise RuntimeError("SendInput delivered 0/2 events")

    _Session.instances[0].act = fail_delivery
    result = tools.sumo_netedit_session(
        operation="act",
        session_id=session_id,
        action="click",
        x=10,
        y=20,
        expected_screenshot_sha256="1" * 64,
    )

    assert result["status"] == "blocked"
    assert result["cleanup"]["status"] == "aborted"
    assert tools._ACTIVE_SESSION is None


def test_finalize_failure_is_structured_and_releases_registry(tmp_path: Path) -> None:
    opened = _open(tmp_path)
    session_id = str(opened["session_id"])

    def fail_finalize(*, expected_screenshot_sha256):
        raise RuntimeError(f"stale screenshot {expected_screenshot_sha256}")

    _Session.instances[0].finalize = fail_finalize
    result = tools.sumo_netedit_session(
        operation="finalize",
        session_id=session_id,
        expected_screenshot_sha256="1" * 64,
    )

    assert result["status"] == "blocked"
    assert "stale screenshot" in result["reason"]
    assert tools._ACTIVE_SESSION is None
    assert tools._ACTIVE_SESSION_ID == ""


def test_finalize_machine_failure_propagates_to_top_level_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = _open(tmp_path)
    monkeypatch.setattr(
        tools,
        "_finalize_audits",
        lambda _session: {"status": "fail", "machine_gate_status": "fail"},
    )

    finalized = tools.sumo_netedit_session(
        operation="finalize",
        session_id=str(opened["session_id"]),
        expected_screenshot_sha256="1" * 64,
    )

    assert finalized["operation_status"] == "pass"
    assert finalized["status"] == "fail"
    assert finalized["session_state"] == "finalized"


@pytest.mark.parametrize("operation", ["finalize", "abort"])
def test_wrong_session_id_does_not_orphan_active_session(tmp_path: Path, operation: str) -> None:
    opened = _open(tmp_path)
    session_id = str(opened["session_id"])

    wrong = tools.sumo_netedit_session(operation=operation, session_id="wrong")

    assert wrong["status"] == "blocked"
    assert tools._ACTIVE_SESSION is _Session.instances[0]
    assert tools._ACTIVE_SESSION_ID == session_id
    aborted = tools.sumo_netedit_session(operation="abort", session_id=session_id)
    assert aborted["status"] == "pass"
    assert aborted["session_state"] == "aborted"
    assert tools._ACTIVE_SESSION is None


def test_retryable_finalize_preflight_keeps_active_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = _open(tmp_path)
    session_id = str(opened["session_id"])

    def reject_preflight(self, *, expected_screenshot_sha256):
        raise ValueError("finalize requires the exact latest screenshot SHA-256")

    monkeypatch.setattr(_Session, "finalize", reject_preflight)
    blocked = tools.sumo_netedit_session(
        operation="finalize",
        session_id=session_id,
        expected_screenshot_sha256="1" * 64,
    )

    assert blocked["status"] == "blocked"
    assert tools._ACTIVE_SESSION is _Session.instances[0]
    aborted = tools.sumo_netedit_session(operation="abort", session_id=session_id)
    assert aborted["status"] == "pass"


def test_finalize_audits_reuse_sumo_surface_and_connection_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text('<net><junction id="old"/></net>', encoding="utf-8")
    candidate.write_text('<net><junction id="new"/></net>', encoding="utf-8")
    session = type(
        "Session",
        (),
        {
            "source": source,
            "candidate": candidate,
            "output_dir": tmp_path / "session",
            "target_source_junction_ids": ("old",),
            "target_candidate_junction_ids": ("new",),
            "steps": [
                {
                    "kind": "act",
                    "detail": {"type": "key", "virtual_key": 0x76},
                }
            ],
        },
    )()
    monkeypatch.setattr(tools, "run_sumo_load_audit", lambda **_kwargs: {"status": "pass"})
    monkeypatch.setattr(
        tools,
        "audit_sumo_lane_junction_surface_overlaps",
        lambda *_args, **_kwargs: {"status": "pass", "source_network_mutation": False},
    )
    monkeypatch.setattr(
        tools,
        "compare_sumo_surface_overlap_reports",
        lambda *_args, **kwargs: {
            "status": "pass",
            "focus_junction_ids": list(kwargs["focus_junction_ids"]),
        },
    )
    monkeypatch.setattr(
        tools,
        "build_connection_mode_regression_audit",
        lambda *_args, **_kwargs: {"status": "pass"},
    )

    report = _REAL_FINALIZE_AUDITS(session)

    assert report["status"] == "review_required"
    assert report["machine_gate_status"] == "pass"
    assert report["surface_comparison"]["focus_junction_ids"] == ["old", "new"]
    assert report["declared_edit_scope"]["fixed_before_gui_edit"] is True
    assert report["source_sha256_before_audits"] == report["source_sha256_after_audits"]
    assert report["candidate_sha256_before_audits"] == report["candidate_sha256_after_audits"]
    assert report["audit_integrity"]["status"] == "pass"
    assert report["automatic_promotion_gate"] == "blocked"
    assert Path(report["report_file"]).is_file()


def test_f7_identity_gate_requires_exact_predeclared_junction_delta(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text(
        '<net><junction id="j1"/><junction id="j2"/><junction id="keep"/></net>',
        encoding="utf-8",
    )
    candidate.write_text(
        '<net><junction id="wrong"/><junction id="keep"/></net>',
        encoding="utf-8",
    )
    session = type(
        "Session",
        (),
        {
            "source": source,
            "candidate": candidate,
            "target_source_junction_ids": ("j1", "j2"),
            "target_candidate_junction_ids": ("cluster-j1-j2",),
            "steps": [
                {
                    "kind": "act",
                    "detail": {"type": "key", "virtual_key": 0x76},
                }
            ],
        },
    )()

    blocked = tools._declared_junction_identity_gate(session)
    candidate.write_text(
        '<net><junction id="cluster-j1-j2"/><junction id="keep"/></net>',
        encoding="utf-8",
    )
    passed = tools._declared_junction_identity_gate(session)

    assert blocked["status"] == "fail"
    assert blocked["observed_added_junction_ids"] == ["wrong"]
    assert passed["status"] == "pass"


def test_junction_identity_gate_rejects_undeclared_delta_without_f7(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text('<net><junction id="j1"/></net>', encoding="utf-8")
    candidate.write_text('<net><junction id="j2"/></net>', encoding="utf-8")
    session = type(
        "Session",
        (),
        {
            "source": source,
            "candidate": candidate,
            "target_source_junction_ids": (),
            "target_candidate_junction_ids": (),
            "steps": [],
        },
    )()

    report = tools._declared_junction_identity_gate(session)

    assert report["status"] == "fail"
    assert report["observed_removed_junction_ids"] == ["j1"]
    assert report["observed_added_junction_ids"] == ["j2"]


def test_scope_preservation_gate_rejects_outside_geometry_change(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text(
        '<net><junction id="old"/><junction id="keep" x="1" y="2"/></net>',
        encoding="utf-8",
    )
    candidate.write_text(
        '<net><junction id="cluster"/><junction id="keep" x="9" y="2"/></net>',
        encoding="utf-8",
    )
    session = type(
        "Session",
        (),
        {
            "source": source,
            "candidate": candidate,
            "target_source_junction_ids": ("old",),
            "target_candidate_junction_ids": ("cluster",),
        },
    )()

    report = tools._scope_preservation_gate(session)

    assert report["status"] == "fail"
    assert report["changed_parts"] == ["junctions"]


def test_scope_preservation_gate_cannot_expand_scope_from_candidate_incidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text(
        "<net>"
        '<junction id="old"/><junction id="keep"/><junction id="outside"/>'
        '<edge id="target" from="old" to="keep"><lane id="target_0"/></edge>'
        '<edge id="unrelated" from="keep" to="outside"><lane id="unrelated_0"/></edge>'
        "</net>",
        encoding="utf-8",
    )
    candidate.write_text(
        "<net>"
        '<junction id="cluster"/><junction id="keep"/><junction id="outside"/>'
        '<edge id="target" from="cluster" to="keep"><lane id="target_0"/></edge>'
        '<edge id="unrelated" from="cluster" to="outside"><lane id="unrelated_0"/></edge>'
        "</net>",
        encoding="utf-8",
    )
    session = type(
        "Session",
        (),
        {
            "source": source,
            "candidate": candidate,
            "target_source_junction_ids": ("old",),
            "target_candidate_junction_ids": ("cluster",),
        },
    )()

    report = tools._scope_preservation_gate(session)

    assert report["status"] == "fail"
    assert "edges" in report["changed_parts"]
    assert report["source_incident_external_edge_ids"] == ["target"]


def test_scope_preservation_gate_checks_unrelated_internal_edges_without_substring_scope(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text(
        "<net>"
        '<junction id="12" intLanes=":12_0_0"/><junction id="keep" intLanes=":keep_0_0"/>'
        '<edge id=":12_0" function="internal"><lane id=":12_0_0" speed="1"/></edge>'
        '<edge id=":keep_0" function="internal"><lane id=":keep_0_0" speed="1"/></edge>'
        '<connection from="road-123" to="outside" fromLane="0" toLane="0"/>'
        "</net>",
        encoding="utf-8",
    )
    candidate.write_text(
        "<net>"
        '<junction id="cluster" intLanes=":cluster_0_0"/>'
        '<junction id="keep" intLanes=":keep_0_0"/>'
        '<edge id=":cluster_0" function="internal"><lane id=":cluster_0_0" speed="1"/></edge>'
        '<edge id=":keep_0" function="internal"><lane id=":keep_0_0" speed="9"/></edge>'
        '<connection from="road-123" to="changed" fromLane="0" toLane="0"/>'
        "</net>",
        encoding="utf-8",
    )
    session = type(
        "Session",
        (),
        {
            "source": source,
            "candidate": candidate,
            "target_source_junction_ids": ("12",),
            "target_candidate_junction_ids": ("cluster",),
        },
    )()

    report = tools._scope_preservation_gate(session)

    assert report["status"] == "fail"
    assert {"edges", "connections"}.issubset(report["changed_parts"])
    assert report["source_target_internal_edge_ids"] == [":12_0"]
    assert report["candidate_target_internal_edge_ids"] == [":cluster_0"]


def test_scope_preservation_gate_rejects_candidate_intlanes_scope_expansion(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text(
        "<net>"
        '<junction id="old" intLanes=":old_0_0"/><junction id="keep" intLanes=":keep_0_0"/>'
        '<edge id=":old_0" function="internal"><lane id=":old_0_0" speed="1"/></edge>'
        '<edge id=":keep_0" function="internal"><lane id=":keep_0_0" speed="1"/></edge>'
        "</net>",
        encoding="utf-8",
    )
    candidate.write_text(
        "<net>"
        '<junction id="cluster" intLanes=":keep_0_0"/>'
        '<junction id="keep" intLanes=":keep_0_0"/>'
        '<edge id=":keep_0" function="internal"><lane id=":keep_0_0" speed="9"/></edge>'
        "</net>",
        encoding="utf-8",
    )
    session = type(
        "Session",
        (),
        {
            "source": source,
            "candidate": candidate,
            "target_source_junction_ids": ("old",),
            "target_candidate_junction_ids": ("cluster",),
        },
    )()

    report = tools._scope_preservation_gate(session)

    assert report["status"] == "fail"
    assert "edges" in report["changed_parts"]
    assert report["candidate_target_internal_edge_ids"] == []


def test_finalize_audits_fail_closed_on_unknown_gate_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text('<net><junction id="keep"/></net>', encoding="utf-8")
    candidate.write_bytes(source.read_bytes())
    session = type(
        "Session",
        (),
        {
            "source": source,
            "candidate": candidate,
            "output_dir": tmp_path / "session",
            "target_source_junction_ids": (),
            "target_candidate_junction_ids": (),
            "steps": [],
        },
    )()
    monkeypatch.setattr(tools, "run_sumo_load_audit", lambda **_kwargs: {"status": "mystery"})
    monkeypatch.setattr(
        tools,
        "audit_sumo_lane_junction_surface_overlaps",
        lambda *_args, **_kwargs: {"status": "pass"},
    )
    monkeypatch.setattr(
        tools,
        "compare_sumo_surface_overlap_reports",
        lambda *_args, **_kwargs: {"status": "pass"},
    )
    monkeypatch.setattr(
        tools,
        "build_connection_mode_regression_audit",
        lambda *_args, **_kwargs: {"status": "pass"},
    )

    report = _REAL_FINALIZE_AUDITS(session)

    assert report["status"] == "fail"
    assert report["machine_gate_status"] == "fail"
    assert report["gate_statuses"]["sumo_load"] == "mystery"


@pytest.mark.parametrize("mutated", ["source", "candidate"])
def test_finalize_audits_fail_if_network_changes_during_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutated: str,
) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source.write_text('<net><junction id="old"/></net>', encoding="utf-8")
    candidate.write_text('<net><junction id="new"/></net>', encoding="utf-8")
    session = type(
        "Session",
        (),
        {
            "source": source,
            "candidate": candidate,
            "output_dir": tmp_path / "session",
            "target_source_junction_ids": ("old",),
            "target_candidate_junction_ids": ("new",),
            "steps": [
                {
                    "kind": "act",
                    "detail": {"type": "key", "virtual_key": 0x76},
                }
            ],
        },
    )()

    def mutate_network(**_kwargs):
        target = source if mutated == "source" else candidate
        junction = "old" if mutated == "source" else "new"
        target.write_text(
            f'<net><junction id="{junction}"/><edge id="late"/></net>',
            encoding="utf-8",
        )
        return {"status": "pass"}

    monkeypatch.setattr(tools, "run_sumo_load_audit", mutate_network)
    monkeypatch.setattr(
        tools,
        "audit_sumo_lane_junction_surface_overlaps",
        lambda *_args, **_kwargs: {"status": "pass"},
    )
    monkeypatch.setattr(
        tools,
        "compare_sumo_surface_overlap_reports",
        lambda *_args, **_kwargs: {"status": "pass"},
    )
    monkeypatch.setattr(
        tools,
        "build_connection_mode_regression_audit",
        lambda *_args, **_kwargs: {"status": "pass"},
    )

    report = _REAL_FINALIZE_AUDITS(session)

    assert report["status"] == "fail"
    assert report["machine_gate_status"] == "fail"
    assert report["audit_integrity"]["status"] == "fail"
    assert report["audit_integrity"][f"{mutated}_unchanged_during_audits"] is False
