from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from torii_sumo.core.ocit_c_signal_devices import (
    OcitSafetyClass,
    SignalDeviceProfileInventory,
    build_signal_device_profile_inventory_schema,
    classify_ocit_c_signal_device_inventory,
)
from torii_sumo.core.signal_device_profile import (
    DisplayColor,
    DisplaySymbol,
    OutputModality,
    ServedUser,
    index_heads_by_signal_group,
)


ROOT = Path(__file__).resolve().parents[1]


def _fixture_bytes() -> bytes:
    return """<?xml version="1.0" encoding="UTF-8"?>
    <lsa:OIVD xmlns:lsa="urn:ocit" xmlns:vendor="urn:vendor">
      <lsa:GrundversorgungsdatenLSA>
        <lsa:DateiVersion>
          <lsa:VersionDokument>2.0.2</lsa:VersionDokument>
          <lsa:VersionDatenstruktur>2002</lsa:VersionDatenstruktur>
          <lsa:VersionBlockzuordnung>18</lsa:VersionBlockzuordnung>
        </lsa:DateiVersion>
        <lsa:Kopfdaten>
          <lsa:Kurzbezeichnung>0042</lsa:Kurzbezeichnung>
          <lsa:Name>German device fixture</lsa:Name>
          <lsa:KnotenVersionsstand>2026-07-19T12:00:00Z</lsa:KnotenVersionsstand>
          <lsa:Planungsversion>fixture</lsa:Planungsversion>
          <lsa:Richtlinie>RiLSA92</lsa:Richtlinie>
          <lsa:Laenderbezeichnung>Deutschland</lsa:Laenderbezeichnung>
        </lsa:Kopfdaten>
        <lsa:SignalgruppeListe>
          <lsa:Signalgruppe>
            <lsa:BezeichnungKurz>K1</lsa:BezeichnungKurz>
            <lsa:OCITOutstationNr>1</lsa:OCITOutstationNr>
            <lsa:VerkehrstechnischerTeilknoten>1</lsa:VerkehrstechnischerTeilknoten>
            <lsa:AbschaltTeilknoten>1</lsa:AbschaltTeilknoten>
            <lsa:Hauptrichtung>true</lsa:Hauptrichtung>
            <lsa:ZulaessigeSignalbilder>
              <lsa:Frei>
                <lsa:Standard>30</lsa:Standard>
                <lsa:Zusaetzlich><lsa:Signalbild>00</lsa:Signalbild><lsa:IstEndbild>true</lsa:IstEndbild></lsa:Zusaetzlich>
              </lsa:Frei>
              <lsa:Gesperrt><lsa:Standard>03</lsa:Standard></lsa:Gesperrt>
              <lsa:StandardAusDunkel>00</lsa:StandardAusDunkel>
              <lsa:StandardGelbblinken>04</lsa:StandardGelbblinken>
            </lsa:ZulaessigeSignalbilder>
            <lsa:Verkehrsart>Kfz</lsa:Verkehrsart>
            <lsa:Lampenausgaenge>
              <lsa:Rot>
                <lsa:Lampe><lsa:Bezeichnung>K1a</lsa:Bezeichnung></lsa:Lampe>
                <lsa:Lampe><lsa:Bezeichnung>K1b</lsa:Bezeichnung></lsa:Lampe>
              </lsa:Rot>
              <lsa:Gelb>
                <lsa:Lampe><lsa:Bezeichnung>K1a</lsa:Bezeichnung></lsa:Lampe>
                <lsa:Lampe><lsa:Bezeichnung>K1b</lsa:Bezeichnung></lsa:Lampe>
              </lsa:Gelb>
              <lsa:Gruen>
                <lsa:Bezeichnung>Gruen</lsa:Bezeichnung>
                <lsa:Lampe><lsa:Bezeichnung>K1a</lsa:Bezeichnung></lsa:Lampe>
                <lsa:Lampe><lsa:Bezeichnung>K1b</lsa:Bezeichnung></lsa:Lampe>
              </lsa:Gruen>
            </lsa:Lampenausgaenge>
          </lsa:Signalgruppe>
          <lsa:Signalgruppe>
            <lsa:BezeichnungKurz>F2</lsa:BezeichnungKurz>
            <lsa:Verkehrsart>Fussgaenger</lsa:Verkehrsart>
            <lsa:Lampenausgaenge>
              <lsa:Rot><lsa:Lampe><lsa:Bezeichnung>F2</lsa:Bezeichnung></lsa:Lampe></lsa:Rot>
              <lsa:Gruen><lsa:Bezeichnung>Gruen</lsa:Bezeichnung><lsa:Lampe><lsa:Bezeichnung>F2</lsa:Bezeichnung></lsa:Lampe></lsa:Gruen>
            </lsa:Lampenausgaenge>
          </lsa:Signalgruppe>
          <lsa:Signalgruppe>
            <lsa:BezeichnungKurz>H3</lsa:BezeichnungKurz>
            <lsa:Verkehrsart>Kfz</lsa:Verkehrsart>
            <lsa:Lampenausgaenge>
              <lsa:Gelb><lsa:Lampe><lsa:Bezeichnung>H3</lsa:Bezeichnung></lsa:Lampe></lsa:Gelb>
            </lsa:Lampenausgaenge>
          </lsa:Signalgruppe>
          <lsa:Signalgruppe>
            <lsa:BezeichnungKurz>AV4</lsa:BezeichnungKurz>
            <lsa:Verkehrsart>Blinde</lsa:Verkehrsart>
            <lsa:Lampenausgaenge>
              <lsa:Gruen>
                <lsa:Bezeichnung>Ton/Vibra</lsa:Bezeichnung>
                <lsa:Lampe><lsa:Bezeichnung>AV4</lsa:Bezeichnung></lsa:Lampe>
              </lsa:Gruen>
            </lsa:Lampenausgaenge>
          </lsa:Signalgruppe>
          <lsa:Signalgruppe>
            <lsa:BezeichnungKurz>X5</lsa:BezeichnungKurz>
            <lsa:Verkehrsart>Unbekannt</lsa:Verkehrsart>
            <lsa:Lampenausgaenge>
              <lsa:Rot><lsa:Lampe><lsa:Bezeichnung>X5</lsa:Bezeichnung></lsa:Lampe></lsa:Rot>
            </lsa:Lampenausgaenge>
          </lsa:Signalgruppe>
        </lsa:SignalgruppeListe>
        <lsa:NocitListe>
          <vendor:Signalgeber>
            <vendor:SignalgeberListe>
              <vendor:Signalgeber><vendor:BezeichnungKurz>K1a</vendor:BezeichnungKurz><vendor:SgrBezeichnung>K1</vendor:SgrBezeichnung><vendor:Mast><vendor:Bezeichnung>M1</vendor:Bezeichnung><vendor:Anbringung>Grundmast</vendor:Anbringung></vendor:Mast></vendor:Signalgeber>
              <vendor:Signalgeber><vendor:BezeichnungKurz>K1b</vendor:BezeichnungKurz><vendor:SgrBezeichnung>K1</vendor:SgrBezeichnung><vendor:Mast><vendor:Bezeichnung>M1</vendor:Bezeichnung><vendor:Anbringung>Grundmast</vendor:Anbringung></vendor:Mast></vendor:Signalgeber>
              <vendor:Signalgeber><vendor:BezeichnungKurz>F2</vendor:BezeichnungKurz><vendor:SgrBezeichnung>F2</vendor:SgrBezeichnung></vendor:Signalgeber>
              <vendor:Signalgeber><vendor:BezeichnungKurz>H3</vendor:BezeichnungKurz><vendor:SgrBezeichnung>H3</vendor:SgrBezeichnung></vendor:Signalgeber>
              <vendor:Signalgeber><vendor:BezeichnungKurz>AV4</vendor:BezeichnungKurz><vendor:SgrBezeichnung>AV4</vendor:SgrBezeichnung></vendor:Signalgeber>
              <vendor:Signalgeber><vendor:BezeichnungKurz>B1A</vendor:BezeichnungKurz><vendor:Mast><vendor:Anbringung>Grundmast</vendor:Anbringung></vendor:Mast></vendor:Signalgeber>
            </vendor:SignalgeberListe>
          </vendor:Signalgeber>
        </lsa:NocitListe>
      </lsa:GrundversorgungsdatenLSA>
    </lsa:OIVD>""".encode("utf-8")


def _inventory(*, expected_node_id: str = "42") -> SignalDeviceProfileInventory:
    source = _fixture_bytes()
    return classify_ocit_c_signal_device_inventory(
        source,
        source_file="fixture://german-ocit-c.xml",
        expected_node_id=expected_node_id,
    )


def test_namespace_independent_inventory_preserves_source_hash_and_metadata() -> None:
    source = _fixture_bytes()
    inventory = _inventory()

    assert inventory.source_sha256 == hashlib.sha256(source).hexdigest()
    assert inventory.status == "review_required"
    assert inventory.disposition == "classified_signal_device_inventory"
    assert inventory.classification_only is True
    assert inventory.source_metadata.node_id == "0042"
    assert inventory.source_metadata.country_code == "DE"
    assert inventory.source_metadata.guideline_native == "RiLSA92"
    assert inventory.source_metadata.document_version == "2.0.2"
    assert inventory.signal_group_count == 5
    assert inventory.profile_count == 6
    assert inventory.unassigned_signal_generator_count == 1
    assert inventory.automatic_binding_gate.status == "blocked"
    assert all(
        profile.identity.jurisdiction.source_claimed_references == ("RiLSA92",)
        and "source_claimed_reference_unsubstantiated" not in profile.assessment.review_reasons
        for profile in inventory.profiles
    )


def test_one_logical_group_maps_to_multiple_physical_heads_without_symbol_guessing() -> None:
    inventory = _inventory()
    group = next(group for group in inventory.logical_signal_groups if group.group_id == "K1")
    profiles = [
        profile
        for profile in inventory.profiles
        if profile.control_references[0].signal_group_id == "K1"
    ]

    assert group.head_ids == ("K1a", "K1b")
    assert {profile.device_id for profile in profiles} == {"K1a", "K1b"}
    for profile in profiles:
        assert {unit.color for unit in profile.identity.display_units} == {
            DisplayColor.RED,
            DisplayColor.YELLOW,
            DisplayColor.GREEN,
        }
        assert {unit.symbol for unit in profile.identity.display_units} == {DisplaySymbol.UNKNOWN}
        assert profile.assessment.status.value == "review_required"
        assert "display_symbol_not_explicit_in_ocit_c" in profile.assessment.review_reasons


def test_one_physical_head_may_receive_outputs_from_multiple_logical_groups() -> None:
    extra_group = b"""
      <lsa:Signalgruppe>
        <lsa:BezeichnungKurz>T6</lsa:BezeichnungKurz>
        <lsa:Verkehrsart>Strassenbahn</lsa:Verkehrsart>
        <lsa:Lampenausgaenge>
          <lsa:Gruen>
            <lsa:Bezeichnung>Weiss</lsa:Bezeichnung>
            <lsa:Lampe><lsa:Bezeichnung>K1a</lsa:Bezeichnung></lsa:Lampe>
          </lsa:Gruen>
        </lsa:Lampenausgaenge>
      </lsa:Signalgruppe>
    """
    source = _fixture_bytes().replace(
        b"</lsa:SignalgruppeListe>",
        extra_group + b"</lsa:SignalgruppeListe>",
    )
    inventory = classify_ocit_c_signal_device_inventory(
        source,
        source_file="fixture://multi-group-head.xml",
        expected_node_id="42",
    )
    profile = next(profile for profile in inventory.profiles if profile.device_id == "K1a")
    group_index = index_heads_by_signal_group(inventory.profiles)

    assert inventory.signal_group_count == 6
    assert inventory.profile_count == 6
    assert {reference.signal_group_id for reference in profile.control_references} == {"K1", "T6"}
    assert profile.identity.served_users == (ServedUser.MOTOR_VEHICLE, ServedUser.TRAM)
    assert DisplayColor.WHITE in {unit.color for unit in profile.identity.display_units}
    assert "K1a" in group_index["K1"]
    assert "K1a" in group_index["T6"]


def test_ocit_gruen_ton_vibra_is_non_visual_accessibility_output() -> None:
    inventory = _inventory()
    profile = next(profile for profile in inventory.profiles if profile.device_id == "AV4")

    assert profile.identity.served_users == (
        ServedUser.ACCESSIBILITY,
        ServedUser.PEDESTRIAN,
    )
    assert profile.identity.modalities == (OutputModality.AUDIBLE_TACTILE,)
    assert profile.identity.display_units == ()
    assert profile.identity.face_layout is None
    assert len(profile.identity.non_visual_outputs) == 1
    output = profile.identity.non_visual_outputs[0]
    assert output.modality is OutputModality.AUDIBLE_TACTILE
    assert output.source_channel_id == "Gruen"
    assert output.native_code == "Ton/Vibra"
    assert "green" not in profile.derived_alias


def test_single_yellow_and_unknown_traffic_groups_are_not_filtered_or_overclassified() -> None:
    inventory = _inventory()
    by_device = {profile.device_id: profile for profile in inventory.profiles}

    assert {unit.color for unit in by_device["H3"].identity.display_units} == {
        DisplayColor.YELLOW
    }
    assert {unit.symbol for unit in by_device["H3"].identity.display_units} == {
        DisplaySymbol.UNKNOWN
    }
    assert by_device["X5"].identity.served_users == (ServedUser.UNKNOWN,)
    assert "identity_unknown_served_user" in by_device["X5"].assessment.review_reasons


def test_allowed_signal_images_and_unassigned_generators_remain_raw_evidence() -> None:
    inventory = _inventory()
    group = next(group for group in inventory.logical_signal_groups if group.group_id == "K1")

    assert {
        (image.code, image.safety_class, image.source_element, image.is_end_image)
        for image in group.allowed_signal_images
    } == {
        ("30", OcitSafetyClass.RELEASE, "Frei/Standard", None),
        ("00", OcitSafetyClass.RELEASE, "Frei/Zusaetzlich/Signalbild", True),
        ("03", OcitSafetyClass.STOP, "Gesperrt/Standard", None),
        ("00", OcitSafetyClass.OFF_DARK, "StandardAusDunkel", None),
        ("04", OcitSafetyClass.YELLOW_FLASH, "StandardGelbblinken", None),
    }
    assert inventory.unassigned_signal_generators[0].generator_id == "B1A"
    assert inventory.unassigned_signal_generators[0].reason == "signal_group_reference_missing"


def test_expected_node_mismatch_and_unsafe_xml_fail_closed() -> None:
    with pytest.raises(ValueError, match="does not match expected_node_id"):
        _inventory(expected_node_id="43")

    with pytest.raises(ValueError, match="DTD or entity"):
        classify_ocit_c_signal_device_inventory(
            b'<!DOCTYPE x [<!ENTITY y "z">]><x>&y;</x>',
            source_file="fixture://unsafe.xml",
        )


def test_inventory_canonical_json_round_trips_and_checked_schema_is_current() -> None:
    inventory = _inventory()
    canonical = inventory.canonical_json()
    reparsed = SignalDeviceProfileInventory.model_validate_json(canonical)
    expected_schema = json.dumps(
        build_signal_device_profile_inventory_schema(),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )

    assert canonical == reparsed.canonical_json()
    assert (
        ROOT / "schemas" / "torii.signal-device-profile-inventory.v1.schema.json"
    ).read_text(encoding="utf-8") == expected_schema


_REAL_ASSET_DIR = (
    ROOT
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
def test_current_hamburg_ocit_files_preserve_all_groups_heads_and_accessibility() -> None:
    expected_counts = {
        "0228": (24, 71, 0),
        "2394": (19, 29, 3),
        "2421": (15, 28, 0),
    }
    inventories = {}
    for path in _REAL_OCIT_FILES:
        node_id = path.name.split("_", 1)[0]
        inventory = classify_ocit_c_signal_device_inventory(
            path.read_bytes(),
            source_file=str(path.resolve()),
            expected_node_id=node_id,
        )
        inventories[node_id] = inventory
        assert (
            inventory.signal_group_count,
            inventory.profile_count,
            inventory.unassigned_signal_generator_count,
        ) == expected_counts[node_id]

    assert any(
        ServedUser.ACCESSIBILITY in profile.identity.served_users
        for profile in inventories["2421"].profiles
    )
    assert all(
        profile.identity.display_units == ()
        for inventory in inventories.values()
        for profile in inventory.profiles
        if OutputModality.AUDIBLE_TACTILE in profile.identity.modalities
    )
