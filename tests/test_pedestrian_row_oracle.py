from __future__ import annotations

from pathlib import Path

from torii_sumo.core.artifact_io import write_text_atomic
from torii_sumo.corridor.enums import GateStatus, TrafficSide
from torii_sumo.corridor.pedestrian_row_oracle import (
    assess_row_static_consistency,
    build_row_geometry_evidence,
    build_row_model_claim_evidence,
    infer_source_row_class,
    make_source_row_bundle,
    parse_plain_source_row_bundle,
)


def _plain_source_files(
    root: Path,
    *,
    priority: bool | None,
    node_type: str = "priority",
) -> tuple[Path, Path]:
    nodes = root / "case.nod.xml"
    connections = root / "case.con.xml"
    priority_attribute = (
        ""
        if priority is None
        else f' priority="{str(priority).lower()}"'
    )
    write_text_atomic(
        nodes,
        f'<nodes><node id="C" x="0" y="0" type="{node_type}"/></nodes>',
    )
    write_text_atomic(
        connections,
        '<connections><crossing node="C" edges="WC CW"'
        f'{priority_attribute}/></connections>',
    )
    return nodes, connections


def _source_bundle(
    root: Path,
    *,
    priority: bool | None,
    node_type: str = "priority",
    traffic_side: TrafficSide = TrafficSide.RIGHT,
):
    nodes, connections = _plain_source_files(
        root,
        priority=priority,
        node_type=node_type,
    )
    return parse_plain_source_row_bundle(
        nodes_file=nodes,
        connections_file=connections,
        crossing_node_id="C",
        crossing_edge_ids=("WC", "CW"),
        traffic_side=traffic_side,
    )


class _FakeEdge:
    def __init__(self, edge_id: str) -> None:
        self._edge_id = edge_id

    def getID(self) -> str:
        return self._edge_id


class _FakeConnection:
    def __init__(
        self,
        from_edge: str,
        to_edge: str,
        index: int,
        *,
        controller: str = "",
        state: str = "M",
    ) -> None:
        self._from = _FakeEdge(from_edge)
        self._to = _FakeEdge(to_edge)
        self._index = index
        self._controller = controller
        self._state = state

    def getFrom(self):
        return self._from

    def getTo(self):
        return self._to

    def getJunctionIndex(self) -> int:
        return self._index

    def getTLSID(self) -> str:
        return self._controller

    def getState(self) -> str:
        return self._state


class _FakeNode:
    def __init__(self, connections: tuple[_FakeConnection, ...]) -> None:
        self._connections = connections

    def getConnections(self):
        return self._connections


class _FakeNetwork:
    def __init__(self, connections: tuple[_FakeConnection, ...]) -> None:
        self._node = _FakeNode(connections)

    def getNode(self, _node_id: str):
        return self._node


def _candidate_net(
    root: Path,
    *,
    model_priority: bool,
    vehicle_cont: bool = False,
) -> Path:
    pedestrian_response = "00" if model_priority else "10"
    vehicle_response = "01" if model_priority else "00"
    path = root / (
        "priority.net.xml" if model_priority else "unpriority.net.xml"
    )
    write_text_atomic(
        path,
        f"""<net>
  <edge id="WC"><lane id="WC_0" index="0" shape="-10,0 -2,0"/></edge>
  <edge id="CE"><lane id="CE_0" index="0" shape="2,0 10,0"/></edge>
  <edge id=":C_c3" function="crossing"><lane id=":C_c3_0" shape="0,-2 0,2"/></edge>
  <edge id=":C_0" function="internal"><lane id=":C_0_0" shape="-2,0 2,0"/></edge>
  <junction id="C" type="priority">
    <request index="0" response="{pedestrian_response}" foes="11" cont="0"/>
    <request index="1" response="{vehicle_response}" foes="11" cont="{int(vehicle_cont)}"/>
  </junction>
  <connection from="WC" to="CE" fromLane="0" toLane="0" via=":C_0_0" dir="s" state="M"/>
</net>""",
    )
    return path


def _install_fake_sumolib(
    monkeypatch,
    *,
    controller: str = "",
) -> None:
    network = _FakeNetwork(
        (
            _FakeConnection(":C_w0", ":C_c3", 0, controller=controller),
            _FakeConnection("WC", "CE", 1, controller=controller),
        )
    )
    monkeypatch.setattr(
        "torii_sumo.corridor.pedestrian_row_oracle.sumolib.net.readNet",
        lambda *_args, **_kwargs: network,
    )


def test_source_oracle_distinguishes_known_classes_and_abstains(
    tmp_path: Path,
) -> None:
    priority = infer_source_row_class(
        _source_bundle(tmp_path / "priority", priority=True)
    )
    unprioritized = infer_source_row_class(
        _source_bundle(tmp_path / "unpriority", priority=False)
    )
    signalized = infer_source_row_class(
        _source_bundle(
            tmp_path / "signalized",
            priority=True,
            node_type="traffic_light",
        )
    )
    unknown = infer_source_row_class(
        _source_bundle(tmp_path / "unknown", priority=None)
    )

    assert priority.expected_class == "priority-unsignalized"
    assert unprioritized.expected_class == "unprioritized-unsignalized"
    assert signalized.expected_class == "signalized"
    assert unknown.expected_class == "unknown-unsignalized"
    assert unknown.abstained is True
    assert unknown.status is GateStatus.REVIEW
    assert all(
        not decision.model_claim_fields_read
        for decision in (priority, unprioritized, signalized, unknown)
    )


def test_source_oracle_handles_left_hand_split_and_unsupported(
    tmp_path: Path,
) -> None:
    base = _source_bundle(
        tmp_path / "left",
        priority=True,
        traffic_side=TrafficSide.LEFT,
    )
    split = make_source_row_bundle(
        crossing_node_id=base.crossing_node_id,
        crossing_edge_ids=base.crossing_edge_ids,
        traffic_side=TrafficSide.LEFT,
        crossing_stage_count=2,
        junction_control_kind="unsignalized",
        explicit_crossing_priority=True,
        source_status="complete",
        observations=base.observations,
    )
    unsupported = make_source_row_bundle(
        crossing_node_id=base.crossing_node_id,
        crossing_edge_ids=base.crossing_edge_ids,
        traffic_side=TrafficSide.LEFT,
        crossing_stage_count=1,
        junction_control_kind="shared-space-or-unsupported",
        explicit_crossing_priority=None,
        source_status="unsupported",
        observations=base.observations,
    )

    assert infer_source_row_class(split).expected_class == (
        "priority-unsignalized"
    )
    unsupported_decision = infer_source_row_class(unsupported)
    assert unsupported_decision.expected_class == (
        "shared-space-or-unsupported"
    )
    assert unsupported_decision.abstained is True


def test_model_claim_reads_request_bits_right_to_left_and_not_as_truth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_fake_sumolib(monkeypatch)
    priority_net = _candidate_net(tmp_path, model_priority=True)
    unpriority_net = _candidate_net(tmp_path, model_priority=False)

    priority = build_row_model_claim_evidence(
        priority_net,
        junction_id="C",
        crossing_edge_id=":C_c3",
        vehicle_from_edge_id="WC",
        vehicle_to_edge_id="CE",
    )
    unpriority = build_row_model_claim_evidence(
        unpriority_net,
        junction_id="C",
        crossing_edge_id=":C_c3",
        vehicle_from_edge_id="WC",
        vehicle_to_edge_id="CE",
    )

    assert priority.inferred_class == "priority-unsignalized"
    assert priority.relation is not None
    assert priority.relation.vehicle_response_to_pedestrian is True
    assert priority.relation.pedestrian_response_to_vehicle is False
    assert unpriority.inferred_class == "unprioritized-unsignalized"
    assert unpriority.relation is not None
    assert unpriority.relation.pedestrian_response_to_vehicle is True
    assert priority.ground_truth_authority is False
    assert unpriority.ground_truth_authority is False


def test_independent_geometry_does_not_infer_priority(tmp_path: Path) -> None:
    net_file = _candidate_net(tmp_path, model_priority=True)

    geometry = build_row_geometry_evidence(
        net_file,
        crossing_edge_id=":C_c3",
        vehicle_from_edge_id="WC",
        vehicle_to_edge_id="CE",
    )

    assert geometry.centerline_intersects is True
    assert geometry.minimum_centerline_distance_m == 0.0
    assert geometry.crossing_angle_deg == 90.0
    assert geometry.right_of_way_inference == "not-inferred"
    assert geometry.request_foes_fields_read == ()


def test_static_oracle_detects_co_self_consistent_model_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_fake_sumolib(monkeypatch)
    source = infer_source_row_class(
        _source_bundle(tmp_path / "source", priority=True)
    )
    candidate = _candidate_net(tmp_path, model_priority=False)
    geometry = build_row_geometry_evidence(
        candidate,
        crossing_edge_id=":C_c3",
        vehicle_from_edge_id="WC",
        vehicle_to_edge_id="CE",
    )
    claim = build_row_model_claim_evidence(
        candidate,
        junction_id="C",
        crossing_edge_id=":C_c3",
        vehicle_from_edge_id="WC",
        vehicle_to_edge_id="CE",
    )

    assessment = assess_row_static_consistency(source, geometry, claim)

    assert source.expected_class == "priority-unsignalized"
    assert claim.inferred_class == "unprioritized-unsignalized"
    assert assessment.status is GateStatus.BLOCKED
    assert assessment.source_model_consistent is False
    assert assessment.contradictions
    assert assessment.expected_answer_source_bundle_only is True
    assert assessment.automatic_promotion_gate is GateStatus.BLOCKED


def test_unknown_source_never_becomes_pass_from_model_self_consistency(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_fake_sumolib(monkeypatch)
    source = infer_source_row_class(
        _source_bundle(tmp_path / "source", priority=None)
    )
    candidate = _candidate_net(tmp_path, model_priority=True)
    geometry = build_row_geometry_evidence(
        candidate,
        crossing_edge_id=":C_c3",
        vehicle_from_edge_id="WC",
        vehicle_to_edge_id="CE",
    )
    claim = build_row_model_claim_evidence(
        candidate,
        junction_id="C",
        crossing_edge_id=":C_c3",
        vehicle_from_edge_id="WC",
        vehicle_to_edge_id="CE",
    )

    assessment = assess_row_static_consistency(source, geometry, claim)

    assert source.abstained is True
    assert claim.inferred_class == "priority-unsignalized"
    assert assessment.status is GateStatus.REVIEW
    assert assessment.source_model_consistent is None


def test_signalized_source_requires_phase_closure_before_static_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_fake_sumolib(monkeypatch, controller="C")
    source = infer_source_row_class(
        _source_bundle(
            tmp_path / "source",
            priority=True,
            node_type="traffic_light",
        )
    )
    candidate = _candidate_net(tmp_path, model_priority=True)
    geometry = build_row_geometry_evidence(
        candidate,
        crossing_edge_id=":C_c3",
        vehicle_from_edge_id="WC",
        vehicle_to_edge_id="CE",
    )
    claim = build_row_model_claim_evidence(
        candidate,
        junction_id="C",
        crossing_edge_id=":C_c3",
        vehicle_from_edge_id="WC",
        vehicle_to_edge_id="CE",
    )

    assessment = assess_row_static_consistency(source, geometry, claim)

    assert source.expected_class == "signalized"
    assert claim.inferred_class == "signalized"
    assert assessment.status is GateStatus.REVIEW
    assert "signal_phase_and_g_G_closure_not_proven" in assessment.limitations


def test_multistage_and_continuation_paths_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_fake_sumolib(monkeypatch)
    base = _source_bundle(tmp_path / "source", priority=True)
    split = make_source_row_bundle(
        crossing_node_id=base.crossing_node_id,
        crossing_edge_ids=base.crossing_edge_ids,
        traffic_side=base.traffic_side,
        crossing_stage_count=2,
        junction_control_kind="unsignalized",
        explicit_crossing_priority=True,
        source_status="complete",
        observations=base.observations,
    )
    source = infer_source_row_class(split)
    candidate = _candidate_net(
        tmp_path,
        model_priority=True,
        vehicle_cont=True,
    )
    geometry = build_row_geometry_evidence(
        candidate,
        crossing_edge_id=":C_c3",
        vehicle_from_edge_id="WC",
        vehicle_to_edge_id="CE",
    )
    claim = build_row_model_claim_evidence(
        candidate,
        junction_id="C",
        crossing_edge_id=":C_c3",
        vehicle_from_edge_id="WC",
        vehicle_to_edge_id="CE",
    )

    assessment = assess_row_static_consistency(source, geometry, claim)

    assert claim.inferred_class == "ambiguous"
    assert "continuation_request_closure_not_supported" in claim.limitations
    assert assessment.status is GateStatus.REVIEW
    assert assessment.automatic_promotion_gate is GateStatus.BLOCKED
