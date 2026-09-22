from __future__ import annotations

from pathlib import Path

import pytest

from torii_sumo.core.digital_twin import MapConnection, MapLane, SignalStream, parse_mapem
from torii_sumo.core.hamburg_official import hamburg_sandtorkai_primary_signal_snapshot
from torii_sumo.core.ocit_c import (
    SATURDAY_PLAN_SEMANTICS,
    VEHICLE_TOPOLOGY_GROUP_POLICY,
    build_vehicle_topology_inventory,
    parse_ocit_c,
    topology_control_index_by_node,
    validate_primary_signal_groups,
)


def _write_fixture(path: Path, *, movement_vt: str = "8") -> None:
    path.write_text(
        f"""<?xml version="1.0"?>
        <lsa:OIVD xmlns:lsa="urn:lsa" xmlns:vendor="urn:vendor">
          <lsa:GrundversorgungsdatenLSA>
            <lsa:Kopfdaten>
              <lsa:Kurzbezeichnung>0228</lsa:Kurzbezeichnung>
              <lsa:Name>Test junction</lsa:Name>
            </lsa:Kopfdaten>
            <lsa:SignalgruppeListe>
              <lsa:Signalgruppe>
                <lsa:BezeichnungKurz>K8</lsa:BezeichnungKurz>
                <lsa:OCITOutstationNr>8</lsa:OCITOutstationNr>
                <lsa:Verkehrsart>Kfz</lsa:Verkehrsart>
                <lsa:Lampenausgaenge>
                  <lsa:Rot><lsa:Lampe><lsa:Bezeichnung>K8a</lsa:Bezeichnung></lsa:Lampe></lsa:Rot>
                </lsa:Lampenausgaenge>
              </lsa:Signalgruppe>
              <lsa:Signalgruppe>
                <lsa:BezeichnungKurz>F9</lsa:BezeichnungKurz>
                <lsa:Verkehrsart>Fussgaenger</lsa:Verkehrsart>
              </lsa:Signalgruppe>
            </lsa:SignalgruppeListe>
            <vendor:Signalgeber>
              <vendor:SignalgeberListe>
                <vendor:Signalgeber>
                  <vendor:BezeichnungKurz>K8b</vendor:BezeichnungKurz>
                  <vendor:SgrBezeichnung>K8</vendor:SgrBezeichnung>
                </vendor:Signalgeber>
                <vendor:Signalgeber>
                  <vendor:BezeichnungKurz>K8c</vendor:BezeichnungKurz>
                  <vendor:SgrBezeichnung>K8</vendor:SgrBezeichnung>
                </vendor:Signalgeber>
              </vendor:SignalgeberListe>
            </vendor:Signalgeber>
            <lsa:PhaseListe>
              <lsa:Phase>
                <lsa:BezeichnungKurz>Phase 1</lsa:BezeichnungKurz>
                <lsa:OCITOutstationNr>1</lsa:OCITOutstationNr>
                <lsa:PhasenElementeintrag>
                  <lsa:Signalgruppe>K8</lsa:Signalgruppe><lsa:Signalbild>30</lsa:Signalbild>
                </lsa:PhasenElementeintrag>
                <lsa:PhasenElementeintrag>
                  <lsa:Signalgruppe>F9</lsa:Signalgruppe><lsa:Signalbild>03</lsa:Signalbild>
                </lsa:PhasenElementeintrag>
              </lsa:Phase>
            </lsa:PhaseListe>
            <lsa:SignalprogrammListe>
              <lsa:Signalprogramm><lsa:BezeichnungKurz>1</lsa:BezeichnungKurz></lsa:Signalprogramm>
              <lsa:Signalprogramm><lsa:BezeichnungKurz>2</lsa:BezeichnungKurz></lsa:Signalprogramm>
            </lsa:SignalprogrammListe>
            <lsa:Schaltuhr><lsa:TagesplanListe>
              <lsa:Tagesplan>
                <lsa:BezeichnungKurz>Sa</lsa:BezeichnungKurz><lsa:BezeichnungLang>Samstag</lsa:BezeichnungLang>
                <lsa:OCITOutstationNr>2</lsa:OCITOutstationNr>
                <lsa:Befehl><lsa:Uhrzeit>00:00:00</lsa:Uhrzeit><lsa:Programm>1</lsa:Programm>
                  <lsa:KnotenEinAus>Ein</lsa:KnotenEinAus><lsa:VA>true</lsa:VA></lsa:Befehl>
                <lsa:Befehl><lsa:Uhrzeit>09:00:00</lsa:Uhrzeit><lsa:Programm>2</lsa:Programm>
                  <lsa:KnotenEinAus>Ein</lsa:KnotenEinAus><lsa:VA>true</lsa:VA></lsa:Befehl>
              </lsa:Tagesplan>
            </lsa:TagesplanListe></lsa:Schaltuhr>
            <lsa:LichtsignalsteuerungVersorgungVAVerfahren><lsa:VASteuerverfahren/></lsa:LichtsignalsteuerungVersorgungVAVerfahren>
            <lsa:MAP><lsa:MAPXmlListe><lsa:MAPXmldaten><lsa:MAPXml>
              <lsa:TrafficStreamConfigData>
                <lsa:refLaneId>1</lsa:refLaneId><lsa:refConnectTo>2</lsa:refConnectTo>
                <lsa:signalGroups><lsa:primary><lsa:vt>{movement_vt}</lsa:vt></lsa:primary></lsa:signalGroups>
                <lsa:staticRegulations><lsa:sign><lsa:unavailable/></lsa:sign></lsa:staticRegulations>
              </lsa:TrafficStreamConfigData>
            </lsa:MAPXml></lsa:MAPXmldaten></lsa:MAPXmlListe></lsa:MAP>
          </lsa:GrundversorgungsdatenLSA>
        </lsa:OIVD>""",
        encoding="utf-8",
    )


def _stream(*, node_id: str = "228", group: str = "K8", layer: str = "primary_signal") -> SignalStream:
    return SignalStream(
        stream_id=1,
        thing_id=2,
        node_id=node_id,
        connection_id="3",
        ingress_lane_id="1",
        egress_lane_id="2",
        lane_type="KFZ",
        signal_group=group,
        layer_name=layer,
        name="test stream",
    )


def _map_lane(lane_id: str) -> MapLane:
    return MapLane(
        node_id="228",
        lane_id=lane_id,
        lane_type="vehicle",
        ingress_approach="1" if lane_id == "1" else "",
        egress_approach="2" if lane_id == "2" else "",
        ref_longitude=9.0,
        ref_latitude=53.0,
        points_m=((0.0, 0.0), (1.0, 0.0)),
    )


def test_namespace_independent_ocit_parser_preserves_control_evidence(tmp_path: Path) -> None:
    fixture = tmp_path / "ocit.xml"
    _write_fixture(fixture)

    config = parse_ocit_c(fixture)

    assert (config.node_id, config.node_name) == ("0228", "Test junction")
    assert config.motor_group_ids == ("K8",)
    assert config.motor_signal_groups[0].signal_heads == ("K8a", "K8b", "K8c")
    assert [(item.group_id, item.signal_image) for item in config.phases[0].group_signals] == [
        ("K8", "30"),
        ("F9", "03"),
    ]
    assert config.signal_program_ids == ("1", "2")
    assert config.saturday_program_ids == ("1", "2")
    assert [command.time for command in config.saturday_plans[0].commands] == ["00:00:00", "09:00:00"]
    assert config.has_vehicle_actuated_control is True
    assert config.saturday_vehicle_actuated is True
    assert config.saturday_plan_semantics == SATURDAY_PLAN_SEMANTICS
    assert "do not establish a fixed second-by-second" in config.saturday_plan_semantics
    assert len(config.vehicle_movements) == 1
    movement = config.vehicle_movements[0]
    assert (movement.node_id, movement.ingress_lane_id, movement.egress_lane_id) == (
        "0228",
        "1",
        "2",
    )
    assert movement.primary_motor_groups == ("K8",)
    assert movement.secondary_motor_groups == ()
    assert movement.unavailable is False


def test_unmapped_movement_vt_fails_closed_or_is_preserved_for_audit(tmp_path: Path) -> None:
    fixture = tmp_path / "ocit.xml"
    _write_fixture(fixture, movement_vt="99")

    with pytest.raises(ValueError, match=r"movement 1->2.*primary vt 99"):
        parse_ocit_c(fixture)

    config = parse_ocit_c(fixture, strict_movement_vt=False)
    movement = config.vehicle_movements[0]
    assert movement.primary_motor_groups == ()
    assert movement.unmapped_primary_vt == ("99",)
    assert movement.unmapped_secondary_vt == ()


def test_known_non_motor_vt_is_ignored_only_when_explicitly_enabled(tmp_path: Path) -> None:
    fixture = tmp_path / "ocit.xml"
    _write_fixture(fixture, movement_vt="9")
    text = fixture.read_text(encoding="utf-8")
    text = text.replace(
        "<lsa:BezeichnungKurz>F9</lsa:BezeichnungKurz>",
        "<lsa:BezeichnungKurz>F9</lsa:BezeichnungKurz>"
        "<lsa:OCITOutstationNr>9</lsa:OCITOutstationNr>",
    )
    fixture.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match=r"movement 1->2.*primary vt 9"):
        parse_ocit_c(fixture)

    config = parse_ocit_c(fixture, ignore_non_motor_vt=True)
    assert len(config.vehicle_movements) == 1
    assert config.vehicle_movements[0].non_motor_only is True


def test_primary_tld_groups_validate_by_normalized_node_and_group(tmp_path: Path) -> None:
    fixture = tmp_path / "ocit.xml"
    _write_fixture(fixture)
    config = parse_ocit_c(fixture)

    report = validate_primary_signal_groups([_stream(node_id="228", group="k08")], [config])

    assert report.status == "pass"
    assert report.primary_stream_count == 1
    assert report.checked_groups == ("228/K8",)


def test_missing_primary_tld_group_fails_closed(tmp_path: Path) -> None:
    fixture = tmp_path / "ocit.xml"
    _write_fixture(fixture)
    config = parse_ocit_c(fixture)

    with pytest.raises(ValueError, match="no motor signal group K7"):
        validate_primary_signal_groups([_stream(group="K7")], [config])


def test_non_primary_or_non_k_streams_are_outside_motor_group_validation(tmp_path: Path) -> None:
    fixture = tmp_path / "ocit.xml"
    _write_fixture(fixture)
    config = parse_ocit_c(fixture)

    report = validate_primary_signal_groups(
        [_stream(group="F9"), _stream(group="K7", layer="cycle_second")],
        [config],
    )

    assert report.status == "pass"
    assert report.primary_stream_count == 0
    assert report.checked_group_count == 0


def test_vehicle_topology_inventory_uses_ocit_not_observation_completeness(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "ocit.xml"
    _write_fixture(fixture)
    config = parse_ocit_c(fixture)
    connection = MapConnection(
        node_id="228",
        connection_id="3",
        ingress_lane_id="1",
        egress_lane_id="2",
        signal_group="4",
        maneuver_bits="100000000000",
    )

    inventory = build_vehicle_topology_inventory(
        [config],
        [_map_lane("1"), _map_lane("2")],
        [connection],
        [_stream(node_id="228", group="K8")],
    )

    assert inventory.status == "pass"
    assert inventory.movement_count == 1
    assert inventory.observed_stream_count == 1
    assert inventory.observed_match_count == 1
    assert inventory.group_resolution_policy == VEHICLE_TOPOLOGY_GROUP_POLICY
    movement = inventory.movements[0]
    assert movement.topology_control_key == "P_K8__S_NONE"
    assert movement.observed_stream_ids == (1,)
    assert movement.observed_signal_groups == ("K8",)
    topology_stream = inventory.topology_streams[0]
    assert topology_stream.stream_id < 0
    assert topology_stream.connection_id == "3"
    assert topology_stream.signal_group == "P_K8__S_NONE"


def test_vehicle_topology_inventory_rejects_tld_group_contradiction(tmp_path: Path) -> None:
    fixture = tmp_path / "ocit.xml"
    _write_fixture(fixture)
    config = parse_ocit_c(fixture)
    connection = MapConnection("228", "3", "1", "2", "4", "100000000000")

    with pytest.raises(ValueError, match="contradicts the OCIT-C motor-group references"):
        build_vehicle_topology_inventory(
            [config],
            [_map_lane("1"), _map_lane("2")],
            [connection],
            [_stream(node_id="228", group="K7")],
        )


_REAL_ASSET_DIR = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "hamburg_sandtorkai_twin_20260711"
    / "twin"
    / "official"
    / "signals"
    / "assets"
)
_REAL_OCIT_FILES = tuple(_REAL_ASSET_DIR / f"{node}_ocit_xml.xml" for node in ("0228", "2394", "2421"))


@pytest.mark.skipif(
    not all(path.is_file() for path in _REAL_OCIT_FILES),
    reason="current Hamburg corridor OCIT-C artifacts are not present",
)
def test_current_three_hamburg_ocit_files_cover_primary_signal_snapshot() -> None:
    configs = [parse_ocit_c(path) for path in _REAL_OCIT_FILES]
    by_node = {str(int(config.node_id)): config for config in configs}

    assert set(by_node) == {"228", "2394", "2421"}
    assert set(by_node["228"].motor_group_ids) >= {f"K{index}" for index in range(1, 9)}
    assert set(by_node["228"].motor_signal_groups[7].signal_heads) >= {"K8a", "K8b", "K8c"}
    assert len(by_node["2394"].phases) == 3
    assert len(by_node["2421"].phases) == 5
    assert all(config.saturday_program_ids for config in configs)
    assert all(config.saturday_vehicle_actuated for config in configs)
    assert {node: len(config.vehicle_movements) for node, config in by_node.items()} == {
        "228": 23,
        "2394": 9,
        "2421": 11,
    }

    movement_2421_10_9 = next(
        movement
        for movement in by_node["2421"].vehicle_movements
        if (movement.ingress_lane_id, movement.egress_lane_id) == ("10", "9")
    )
    assert movement_2421_10_9.primary_motor_groups == ("K5",)
    assert movement_2421_10_9.secondary_motor_groups == ("K2",)
    movement_2421_12_2 = next(
        movement
        for movement in by_node["2421"].vehicle_movements
        if (movement.ingress_lane_id, movement.egress_lane_id) == ("12", "2")
    )
    assert movement_2421_12_2.primary_motor_groups == ()
    assert movement_2421_12_2.secondary_motor_groups == ("K2",)

    movement_2394_6_4 = next(
        movement
        for movement in by_node["2394"].vehicle_movements
        if (movement.ingress_lane_id, movement.egress_lane_id) == ("6", "4")
    )
    assert movement_2394_6_4.primary_motor_groups == ("K3",)
    assert movement_2394_6_4.secondary_motor_groups == ("K4",)

    report = validate_primary_signal_groups(hamburg_sandtorkai_primary_signal_snapshot(), configs)
    assert report.status == "pass"
    assert report.primary_stream_count == 27

    map_lanes = []
    map_connections = []
    for node in ("0228", "2394", "2421"):
        node_lanes, node_connections = parse_mapem(_REAL_ASSET_DIR / f"{node}_map_xml.xml")
        map_lanes.extend(node_lanes)
        map_connections.extend(node_connections)
    topology = build_vehicle_topology_inventory(
        configs,
        map_lanes,
        map_connections,
        hamburg_sandtorkai_primary_signal_snapshot(),
    )
    assert topology.status == "pass"
    assert topology.source_movement_count == 43
    assert topology.excluded_non_vehicle_movement_count == 10
    assert topology.movement_count == 33
    assert topology.observed_stream_count == topology.observed_match_count == 27
    groups_by_node = {
        node: {
            group
            for movement in topology.movements
            if str(int(movement.node_id)) == node
            for group in (
                *movement.primary_motor_groups,
                *movement.secondary_motor_groups,
            )
        }
        for node in ("228", "2394", "2421")
    }
    assert groups_by_node == {
        "228": {"K1", "K2", "K3", "K4", "K6", "K7", "K8"},
        "2394": {"K1", "K2", "K3", "K4", "K5", "K6", "K7"},
        "2421": {"K1", "K2", "K3", "K4Z", "K5"},
    }
    movement_2421_10_9 = next(
        movement
        for movement in topology.movements
        if str(int(movement.node_id)) == "2421"
        and (movement.ingress_lane_id, movement.egress_lane_id) == ("10", "9")
    )
    assert movement_2421_10_9.topology_control_key == "P_K5__S_K2"
    assert movement_2421_10_9.primary_motor_groups == ("K5",)
    assert movement_2421_10_9.secondary_motor_groups == ("K2",)
    control_indices = topology_control_index_by_node(topology)
    assert {node: len(indices) for node, indices in control_indices.items()} == {
        "228": 9,
        "2394": 6,
        "2421": 6,
    }
    assert control_indices["2421"]["P_K1__S_NONE"] == 0
    assert control_indices["2421"]["P_K5__S_K2"] == 3
    assert control_indices["2421"]["P_NONE__S_K3"] == 5
