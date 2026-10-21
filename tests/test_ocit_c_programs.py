import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.ocit_c_programs import read_ocit_fixed_program


def _source(tmp_path, *, start="10", end="40", release="30", constant=None):
    switch = (f"<DauerSignalbild>{constant}</DauerSignalbild>" if constant else
        f"<Schaltzeit><Schaltzeitpunkt>{start}</Schaltzeitpunkt><Signalbild>{release}</Signalbild></Schaltzeit>"
        f"<Schaltzeit><Schaltzeitpunkt>{end}</Schaltzeitpunkt><Signalbild>03</Signalbild></Schaltzeit>")
    root = ET.fromstring(f'''<OIVD xmlns="urn:ocit"><Kopfdaten><Kurzbezeichnung>test</Kurzbezeichnung></Kopfdaten>
      <Signalgruppe><BezeichnungKurz>K1</BezeichnungKurz><Verkehrsart>Kfz</Verkehrsart><OCITOutstationNr>1</OCITOutstationNr>
        <ZulaessigeSignalbilder><Frei><Standard>{release}</Standard></Frei><Gesperrt><Standard>03</Standard></Gesperrt></ZulaessigeSignalbilder>
        <AnwurfUebergang><Uebergangselement><Signalbild>0F</Signalbild><Zeitdauer>1</Zeitdauer></Uebergangselement></AnwurfUebergang>
        <AbwurfUebergang><Uebergangselement><Signalbild>0C</Signalbild><Zeitdauer>3</Zeitdauer></Uebergangselement></AbwurfUebergang>
      </Signalgruppe><Signalprogramm><BezeichnungKurz>morning</BezeichnungKurz><OCITOutstationNr>1</OCITOutstationNr>
        <SPKopfzeile><TU>90</TU><SignalzeitenVersatz>7</SignalzeitenVersatz></SPKopfzeile>
        <SPZeile><Signalgruppe>K1</Signalgruppe>{switch}</SPZeile></Signalprogramm></OIVD>''')
    path = tmp_path / "source.xml"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def _intervals(report):
    return [(row["begin_s"], row["end_s"], row["signal_image"], row["in_transition"])
            for row in report["groups"][0]["intervals"]]


def test_program_times_start_transitions_and_keep_native_clock_offset_separate(tmp_path):
    source = _source(tmp_path)
    digest = file_sha256(source)
    result = read_ocit_fixed_program(source, "1")
    assert result["source_unchanged"] and file_sha256(source) == digest
    assert result["program_name"] == "morning" and result["signal_times_offset_native"] == "7"
    assert _intervals(result) == [(0, 10, "03", False), (10, 11, "0F", True), (11, 40, "30", False),
                                  (40, 43, "0C", True), (43, 90, "03", False)]
    with pytest.raises(ValueError, match="outstation number"):
        read_ocit_fixed_program(source, "morning")


def test_wrapped_transition_retains_previous_cycle_yellow_at_time_zero(tmp_path):
    result = read_ocit_fixed_program(_source(tmp_path, start="5", end="89"), "1")
    assert _intervals(result) == [(0, 2, "0C", True), (2, 5, "03", False), (5, 6, "0F", True),
                                  (6, 89, "30", False), (89, 90, "0C", True)]


def test_fractional_times_and_coincident_destination_use_the_new_command(tmp_path):
    result = read_ocit_fixed_program(_source(tmp_path, start="0.1", end="1.1"), "1")
    assert result["unresolved_groups"] == []
    assert _intervals(result) == [(0, .1, "03", False), (.1, 1.1, "0F", True),
                                  (1.1, 4.1, "0C", True), (4.1, 90, "03", False)]


def test_dark_release_stays_native_dark_with_group_specific_securing_state(tmp_path):
    result = read_ocit_fixed_program(_source(tmp_path, release="00"), "1")
    group = result["groups"][0]
    assert group["allowed_image_securing_states"]["00"] == ["release"]
    assert next(row for row in group["intervals"] if row["begin_s"] == 11)["signal_image"] == "00"
    assert not any("sumo_state" in row for row in group["intervals"])


def test_constant_dark_keeps_both_declared_securing_states(tmp_path):
    source = _source(tmp_path, release="00", constant="00")
    tree = ET.parse(source)
    tree.getroot().find(".//{*}Gesperrt/{*}Standard").text = "00"
    tree.write(source, encoding="utf-8", xml_declaration=True)
    result = read_ocit_fixed_program(source, "1")
    assert _intervals(result) == [(0, 90, "00", False)]
    assert result["groups"][0]["intervals"][0]["securing_states"] == ["release", "stop"]


@pytest.mark.parametrize("container", ["SPZeile", "Schaltzeit"])
def test_named_transition_is_explicitly_unresolved(tmp_path, container):
    source = _source(tmp_path)
    tree = ET.parse(source)
    ET.SubElement(tree.getroot().find(f".//{{*}}{container}"), "{urn:ocit}Uebergang").text = "special"
    tree.write(source, encoding="utf-8", xml_declaration=True)
    result = read_ocit_fixed_program(source, "1")
    assert result["unresolved_groups"] == ["K1"]
    assert "named transition" in result["groups"][0]["reason"]


@pytest.mark.parametrize("cycle", ["0", "nan", "-1", "1e9999"])
def test_invalid_cycle_has_no_timeline(tmp_path, cycle):
    source = _source(tmp_path)
    tree = ET.parse(source)
    tree.getroot().find(".//{*}SPKopfzeile/{*}TU").text = cycle
    tree.write(source, encoding="utf-8", xml_declaration=True)
    with pytest.raises(ValueError, match="OCIT time"):
        read_ocit_fixed_program(source, "1")


@pytest.mark.parametrize("start,end", [("10", "10"), ("-1", "40"), ("10", "90"), ("10", "10.5"), ("nan", "40")])
def test_invalid_or_overlapping_group_commands_remain_reported(tmp_path, start, end):
    result = read_ocit_fixed_program(_source(tmp_path, start=start, end=end), "1")
    assert result["status"] == "measured" and result["unresolved_groups"] == ["K1"]
    assert result["groups"][0]["intervals"] == [] and result["groups"][0]["source_rows_xml"]


@pytest.mark.parametrize("node,program", [("104", "5"), ("118", "5"), ("119", "1"), ("200", "5"), ("535", "1")])
def test_frozen_ring1_programs_expand_motor_transitions_without_inventing_missing_rows(tmp_path, node, program):
    package = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_official_sources_20261020_v1"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    record = next(row for row in manifest["files"] if row["node_id"] == node and row["kind"] == "ocit_xml")
    source = tmp_path / "ocit.xml"
    source.write_bytes(gzip.decompress((package / record["path"]).read_bytes()))
    assert file_sha256(source) == record["sha256"]
    result = read_ocit_fixed_program(source, program)
    assert result["cycle_s"] == 90 and result["source_unchanged"]
    groups = {row["group_id"]: row for row in result["groups"]}
    assert groups["K1"]["status"] == "measured"
    for group in result["groups"]:
        if group["status"] == "measured":
            assert group["intervals"][0]["begin_s"] == 0
            assert group["intervals"][-1]["end_s"] == 90
            assert sum(row["end_s"] - row["begin_s"] for row in group["intervals"]) == pytest.approx(90)
    if node == "119":
        assert groups["K7"]["allowed_image_securing_states"]["00"] == ["release"]
        assert groups["K8"]["allowed_image_securing_states"]["00"] == ["release"]
    if node == "200":
        assert groups["R6"]["status"] == "measured"
        assert groups["K3"]["status"] == groups["K7"]["status"] == "unresolved"
        assert groups["K3"]["allowed_image_securing_states"]["00"] == ["release", "stop"]
