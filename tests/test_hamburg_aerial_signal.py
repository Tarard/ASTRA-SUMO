import json

import pytest

from torii_sumo import cli
from torii_sumo.core import hamburg_aerial_signal
from torii_sumo.core.digital_twin import SignalStream
from torii_sumo.core.hamburg_aerial_signal import (
    allocate_stage_green_seconds,
    build_protected_signal_stages,
    match_official_signal_streams,
    network_tls_conflict_pairs,
)


@pytest.mark.parametrize("record, expected", [
    ({"join_id": "345169804", "node_id": "118"}, "118"),
    ({"join_id": "LSA118_part0", "node_id": "118"}, "118"),
    ({"join_id": "LSA118_part0"}, "118"),
])
def test_official_node_identity_does_not_depend_on_renaming_sumo_nodes(record, expected):
    assert hamburg_aerial_signal._materialized_node_id(record) == expected


def test_materialized_node_identity_rejects_conflicting_or_missing_provenance():
    with pytest.raises(ValueError):
        hamburg_aerial_signal._materialized_node_id({"join_id": "LSA118_part0", "node_id": "119"})
    with pytest.raises(ValueError):
        hamburg_aerial_signal._materialized_node_id({"join_id": "345169804"})


def test_stage_green_allocation_preserves_cycle_and_minimum() -> None:
    values = allocate_stage_green_seconds(
        ["GGrr", "rrGr", "rrrG"],
        link_flows={0: 100, 1: 80, 2: 40, 3: 20},
        cycle_seconds=90,
        transition_seconds=[8, 8, 8],
        minimum_green_seconds=5,
    )

    assert sum(values) + 24 == 90
    assert all(value >= 5 for value in values)
    assert values[0] > values[1] > values[2]


def test_protected_stages_separate_conflicting_signal_groups() -> None:
    stages = build_protected_signal_stages(
        [
            {"status": "active", "official_signal_group": "K1", "sumo_link_index": 1},
            {"status": "active", "official_signal_group": "K1", "sumo_link_index": 2},
            {"status": "active", "official_signal_group": "K2", "sumo_link_index": 3},
        ],
        conflict_pairs={(1, 3), (2, 3)},
        link_count=4,
    )

    assert stages == ["rGG r".replace(" ", ""), "rrrG"]
    assert all("g" not in state for state in stages)


def test_network_tls_conflicts_read_reversed_foe_bits(tmp_path) -> None:
    network = tmp_path / "test.net.xml"
    network.write_text(
        """<net>
        <junction id="tls" type="traffic_light" incLanes="a_0 b_0" intLanes=":tls_0_0 :tls_1_0">
            <request index="0" response="00" foes="10" cont="0"/>
            <request index="1" response="00" foes="01" cont="0"/>
        </junction>
        <connection from="a" to="c" fromLane="0" toLane="0" via=":tls_0_0" tl="tls" linkIndex="0"/>
        <connection from="b" to="d" fromLane="0" toLane="0" via=":tls_1_0" tl="tls" linkIndex="1"/>
        <tlLogic id="tls" type="static" programID="0" offset="0"><phase duration="30" state="Gr"/></tlLogic>
        </net>""",
        encoding="utf-8",
    )

    assert network_tls_conflict_pairs(network, "tls") == {(0, 1)}


def test_network_tls_conflicts_translate_request_index_to_tls_link_index(tmp_path) -> None:
    network = tmp_path / "translated.net.xml"
    network.write_text(
        """<net>
        <junction id="tls" type="traffic_light" intLanes=":tls_req0 :tls_req1 :tls_req2">
            <request index="0" response="000" foes="010" cont="0"/>
            <request index="1" response="000" foes="001" cont="0"/>
            <request index="2" response="000" foes="000" cont="0"/>
        </junction>
        <connection from="a" to="c" via=":tls_req0" tl="tls" linkIndex="2"/>
        <connection from="b" to="d" via=":tls_req1" tl="tls" linkIndex="0"/>
        <connection from="e" to="f" via=":tls_req2" tl="tls" linkIndex="1"/>
        <tlLogic id="tls" type="static" programID="0"><phase duration="30" state="Grr"/></tlLogic>
        </net>""",
        encoding="utf-8",
    )

    assert network_tls_conflict_pairs(network, "tls") == {(0, 2)}


def test_signal_match_requires_exact_node_connection_and_lane_pair() -> None:
    streams = [
        SignalStream(
            stream_id=10,
            thing_id=20,
            node_id="119",
            connection_id="8",
            ingress_lane_id="4",
            egress_lane_id="57",
            lane_type="KFZ",
            signal_group="K8",
            layer_name="primary_signal",
            name="exact",
        ),
        SignalStream(
            stream_id=11,
            thing_id=21,
            node_id="119",
            connection_id="8",
            ingress_lane_id="38",
            egress_lane_id="14",
            lane_type="KFZ",
            signal_group="K7",
            layer_name="primary_signal",
            name="different asset version",
        ),
    ]

    matches = match_official_signal_streams(
        node_id="119",
        movement_id="8",
        ingress_lane_id="4",
        egress_lane_id="57",
        streams=streams,
    )

    assert [stream.stream_id for stream in matches] == [10]


def test_cli_routes_hamburg_aerial_signal_binding(monkeypatch, tmp_path, capsys) -> None:
    request = tmp_path / "request.json"
    request.write_text("{}", encoding="utf-8")
    output = tmp_path / "out"

    def fake_build(*, request_file, output_dir):
        assert request_file == str(request)
        assert output_dir == str(output)
        return {"status": "pass", "decision": "review_required"}

    monkeypatch.setattr(
        cli,
        "build_hamburg_aerial_signal_binding",
        fake_build,
        raising=False,
    )

    exit_code = cli.main(
        ["hamburg", "bind-aerial-signals", str(request), str(output), "--json"]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "review_required"


def test_cli_routes_hamburg_protected_signal_candidate(monkeypatch, tmp_path, capsys) -> None:
    request = tmp_path / "request.json"
    request.write_text("{}", encoding="utf-8")
    output = tmp_path / "out"

    def fake_build(*, request_file, output_dir):
        assert request_file == str(request)
        assert output_dir == str(output)
        return {"status": "pass", "claim_status": "diagnostic-demo"}

    monkeypatch.setattr(
        cli,
        "build_protected_signal_candidate_from_request",
        fake_build,
        raising=False,
    )

    exit_code = cli.main(
        ["hamburg", "build-protected-signals", str(request), str(output), "--json"]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["claim_status"] == "diagnostic-demo"
