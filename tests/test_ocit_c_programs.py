import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.ocit_c_programs import read_ocit_fixed_program
from copy import deepcopy


def _source(tmp_path, *, start="10", end="40", release="30", constant=None):
    switch = (f"<DauerSignalbild>{constant}</DauerSignalbild>" if constant else
        f"<Schaltzeit><Schaltzeitpunkt>{start}</Schaltzeitpunkt><Signalbild>{release}</Signalbild></Schaltzeit>"
        f"<Schaltzeit><Schaltzeitpunkt>{end}</Schaltzeitpunkt><Signalbild>03</Signalbild></Schaltzeit>")
    root = ET.fromstring(f'''<OIVD xmlns="urn:ocit"><Kopfdaten><Kurzbezeichnung>test</Kurzbezeichnung></Kopfdaten>
      <Signalgruppe><BezeichnungKurz>K1</BezeichnungKurz><Verkehrsart>Kfz</Verkehrsart><OCITOutstationNr>1</OCITOutstationNr>
        <ZulaessigeSignalbilder><Frei><Standard>{release}</Standard></Frei><Gesperrt><Standard>03</Standard></Gesperrt></ZulaessigeSignalbilder>
        <AnwurfUebergang><Uebergangselement><Signalbild>0F</Signalbild><Zeitdauer>1</Zeitdauer></Uebergangselement></AnwurfUebergang>
        <AbwurfUebergang><Uebergangselement><Signalbild>0C</Signalbild><Zeitdauer>3</Zeitdauer></Uebergangselement></AbwurfUebergang>
      </Signalgruppe><Signalprogramm><BezeichnungKurz>1</BezeichnungKurz><OCITOutstationNr>7</OCITOutstationNr>
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
    assert result["program_name"] == "1" and result["program_number"] == "7" and result["signal_times_offset_native"] == "7"
    assert _intervals(result) == [(0, 10, "03", False), (10, 11, "0F", True), (11, 40, "30", False),
                                  (40, 43, "0C", True), (43, 90, "03", False)]
    assert result["safety_intergreens"]["status"] == "unavailable"
    with pytest.raises(ValueError, match="reference name"):
        read_ocit_fixed_program(source, "7")


def _matrix_source(tmp_path, *, first_start="10", first_end="40", second_start="44", second_end="70"):
    source = _source(tmp_path, start=first_start, end=first_end)
    tree = ET.parse(source)
    root = tree.getroot()
    group = deepcopy(root.find("{*}Signalgruppe"))
    group.find("{*}BezeichnungKurz").text = "K2"
    root.append(group)
    program = root.find("{*}Signalprogramm")
    row = deepcopy(program.find("{*}SPZeile"))
    row.find("{*}Signalgruppe").text = "K2"
    row.findall("{*}Schaltzeit/{*}Schaltzeitpunkt")[0].text = second_start
    row.findall("{*}Schaltzeit/{*}Schaltzeitpunkt")[1].text = second_end
    program.append(row)
    matrix = ET.SubElement(root, "{urn:ocit}SicherheitsrelevanteZwischenzeitenmatrix")
    ET.SubElement(matrix, "{urn:ocit}BezeichnungKurz").text = "source matrix"
    for first, second, seconds in [("K1", "K2", "5"), ("K2", "K1", "32")]:
        pair = ET.SubElement(matrix, "{urn:ocit}ZwiZt")
        for tag, value in [("Raeumer", first), ("Einfahrer", second), ("T", seconds)]:
            ET.SubElement(pair, "{urn:ocit}"+tag).text = value
    tree.write(source, encoding="utf-8", xml_declaration=True)
    return source


def test_intergreens_measure_direction_and_cycle_wrap_without_changing_source(tmp_path):
    source = _matrix_source(tmp_path)
    before = source.read_bytes()
    report = read_ocit_fixed_program(source, "1")["safety_intergreens"]
    first, second = report["rows"]
    assert first["minimum_intergreen_s"] == 5 and first["slack_s"] == 0 and first["status"] == "satisfied"
    assert second["minimum_intergreen_s"] == 31 and second["slack_s"] == -1 and second["status"] == "violated"
    assert report["violated_pair_count"] == 1 and source.read_bytes() == before
    wrapped = read_ocit_fixed_program(_matrix_source(tmp_path, first_start="80", first_end="10", second_start="14", second_end="40"), "1")
    assert wrapped["safety_intergreens"]["rows"][0]["minimum_intergreen_s"] == 5


def test_intergreens_report_overlap_and_keep_other_measurements_when_a_row_is_bad(tmp_path):
    source = _matrix_source(tmp_path, second_start="20", second_end="60")
    report = read_ocit_fixed_program(source, "1")["safety_intergreens"]
    assert report["rows"][0]["release_overlap_s"] == 19 and report["rows"][0]["status"] == "violated"
    tree = ET.parse(source)
    tree.getroot().find(".//{*}ZwiZt/{*}T").text = "-1"
    tree.write(source, encoding="utf-8", xml_declaration=True)
    report = read_ocit_fixed_program(source, "1")
    assert report["groups"][0]["status"] == "measured"
    assert report["safety_intergreens"]["rows"][0]["status"] == "unresolved"
    assert report["safety_intergreens"]["rows"][1]["status"] == "violated"


def test_repeated_intergreen_pair_is_unresolved_without_selecting_one_requirement(tmp_path):
    source = _matrix_source(tmp_path)
    tree = ET.parse(source)
    matrix = tree.getroot().find(".//{*}SicherheitsrelevanteZwischenzeitenmatrix")
    matrix.append(deepcopy(matrix.find("{*}ZwiZt")))
    tree.write(source, encoding="utf-8", xml_declaration=True)
    report = read_ocit_fixed_program(source, "1")["safety_intergreens"]
    assert [row["status"] for row in report["rows"]] == ["unresolved", "violated", "unresolved"]


@pytest.mark.parametrize("ambiguous", [False, True])
def test_constant_and_ambiguous_groups_do_not_supply_false_intergreen_passes(tmp_path, ambiguous):
    source = _matrix_source(tmp_path)
    tree = ET.parse(source)
    root = tree.getroot()
    row = root.find(".//{*}SPZeile")
    for child in list(row):
        if child.tag.rsplit("}", 1)[-1] != "Signalgruppe":
            row.remove(child)
    ET.SubElement(row, "{urn:ocit}DauerSignalbild").text = "30" if ambiguous else "03"
    if ambiguous:
        root.find(".//{*}Signalgruppe/{*}ZulaessigeSignalbilder/{*}Gesperrt/{*}Standard").text = "30"
    tree.write(source, encoding="utf-8", xml_declaration=True)
    report = read_ocit_fixed_program(source, "1")["safety_intergreens"]
    assert all(row["status"] == ("unresolved" if ambiguous else "not_applicable") for row in report["rows"])


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


@pytest.mark.parametrize("node,program", [("104", "5"), ("104", "1"), ("118", "5"), ("119", "1"),
                                        ("200", "5"), ("535", "1"), ("535", "10")])
def test_frozen_ring1_programs_expand_motor_transitions_without_inventing_missing_rows(tmp_path, node, program):
    package = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_official_sources_20261020_v1"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    record = next(row for row in manifest["files"] if row["node_id"] == node and row["kind"] == "ocit_xml")
    source = tmp_path / "ocit.xml"
    source.write_bytes(gzip.decompress((package / record["path"]).read_bytes()))
    assert file_sha256(source) == record["sha256"]
    result = read_ocit_fixed_program(source, program)
    assert result["cycle_s"] == 90 and result["source_unchanged"]
    intergreens = result["safety_intergreens"]
    assert intergreens["status"] == "measured" and intergreens["violated_pair_count"] == intergreens["unresolved_pair_count"] == 0
    assert len(intergreens["rows"]) == {"104": 58, "118": 260, "119": 98, "200": 140, "535": 118}[node]
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
        pairs = {(r["clearing_group"], r["entering_group"]): r for r in intergreens["rows"]}
        assert pairs["K6", "K3"]["required_s"] == 4 and pairs["K6", "K3"]["minimum_intergreen_s"] == 8
        assert pairs["K3", "K6"]["required_s"] == 7 and pairs["K3", "K6"]["minimum_intergreen_s"] == 25
    if node == "200":
        assert groups["R6"]["status"] == "measured"
        assert groups["K3"]["status"] == groups["K7"]["status"] == "unresolved"
        assert groups["K3"]["allowed_image_securing_states"]["00"] == ["release", "stop"]
    if node == "535" and program == "10":
        assert result["program_number"] == "4"
