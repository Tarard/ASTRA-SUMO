from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from torii_sumo.tools.signal_tools import sumo_signal_device_profile_classify


def _write_fixture(path: Path) -> bytes:
    source = """<?xml version="1.0" encoding="UTF-8"?>
    <OIVD xmlns="urn:ocit">
      <GrundversorgungsdatenLSA>
        <DateiVersion><VersionDokument>2.0.2</VersionDokument></DateiVersion>
        <Kopfdaten>
          <Kurzbezeichnung>0007</Kurzbezeichnung>
          <Name>Tool fixture</Name>
          <Laenderbezeichnung>Deutschland</Laenderbezeichnung>
          <Richtlinie>Andere</Richtlinie>
        </Kopfdaten>
        <SignalgruppeListe>
          <Signalgruppe>
            <BezeichnungKurz>K1</BezeichnungKurz>
            <Verkehrsart>Kfz</Verkehrsart>
            <Lampenausgaenge>
              <Rot><Lampe><Bezeichnung>K1a</Bezeichnung></Lampe></Rot>
              <Gelb><Lampe><Bezeichnung>K1a</Bezeichnung></Lampe></Gelb>
              <Gruen><Bezeichnung>Gruen</Bezeichnung><Lampe><Bezeichnung>K1a</Bezeichnung></Lampe></Gruen>
            </Lampenausgaenge>
          </Signalgruppe>
        </SignalgruppeListe>
      </GrundversorgungsdatenLSA>
    </OIVD>""".encode("utf-8")
    path.write_bytes(source)
    return source


def test_signal_device_tool_is_read_only_and_returns_public_inventory(tmp_path: Path) -> None:
    source_path = tmp_path / "ocit.xml"
    source = _write_fixture(source_path)

    result = sumo_signal_device_profile_classify(str(source_path), expected_node_id="7")

    assert source_path.read_bytes() == source
    assert result["schema"] == "torii.signal-device-profile-inventory/v1"
    assert result["status"] == "review_required"
    assert result["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert result["source_metadata"]["node_id"] == "0007"
    assert result["signal_group_count"] == 1
    assert result["profile_count"] == 1
    assert result["automatic_binding_gate"]["status"] == "blocked"
    assert result["profiles"][0]["device_id"] == "K1a"
    assert "control_method" not in result["profiles"][0]["identity"]
    assert "runtime_state" not in result["profiles"][0]["identity"]


def test_signal_device_tool_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.xml"

    with pytest.raises(ValueError, match="existing local file"):
        sumo_signal_device_profile_classify(str(missing))
