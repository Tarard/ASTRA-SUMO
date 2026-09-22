import json
from pathlib import Path

from torii_sumo.intersection.clean import clean_intersection


FIXTURES = Path(__file__).parent / "fixtures"


def test_clean_intersection_writes_ir_validation_and_plain_files(tmp_path: Path) -> None:
    result = clean_intersection(FIXTURES / "t3_priority.osm.xml", tmp_path, compile_net=True)

    assert result["status"] in {"pass", "blocked", "fail"}
    assert result["intersection_id"] == "core_1"
    assert result["topology_type"] == "T3"
    assert result["approach_count"] == 3
    assert result["movement_count"] > 0
    assert result["claim_status"] in {"intersection-cleaned", "blocked"}
    assert result["sumo_load_status"] in {"pass", "fail"}
    assert result["route_probe_status"] == "skipped"
    assert result["tls_linkindex_status"] in {"pass", "fail", "skipped"}
    for name in ["intersection_ir.json", "validation.json", "intersection.nod.xml", "intersection.edg.xml", "intersection.con.xml"]:
        assert (tmp_path / name).exists()


def test_clean_intersection_summary_reports_next_phase_counts(tmp_path: Path) -> None:
    result = clean_intersection(FIXTURES / "x4_signalized.osm.xml", tmp_path, compile_net=False)

    assert result["restriction_warning_count"] == 0
    assert result["direction_blocked_approach_count"] == 0
    assert result["custom_tllogic_applied"] is True


def test_clean_intersection_applies_xml_turn_restriction_to_ir(tmp_path: Path) -> None:
    source_xml = (FIXTURES / "t3_priority.osm.xml").read_text(encoding="utf-8")
    restricted_xml = source_xml.replace(
        "</osm>",
        """  <relation id="r_clean_no_right">
    <member type="way" ref="10" role="from"/>
    <member type="node" ref="1" role="via"/>
    <member type="way" ref="11" role="to"/>
    <tag k="type" v="restriction"/>
    <tag k="restriction" v="no_right_turn"/>
  </relation>
</osm>""",
    )
    osm_file = tmp_path / "t3_restricted.osm.xml"
    osm_file.write_text(restricted_xml, encoding="utf-8")

    result = clean_intersection(osm_file, tmp_path, compile_net=False)
    ir = json.loads(Path(result["intersection_ir_file"]).read_text(encoding="utf-8"))
    evidence = [
        item
        for movement in ir["movement_matrix"]["movements"]
        for item in movement["evidence"]
    ]

    assert ir["movement_matrix"]["restriction_blocked_count"] == 1
    assert "osm_restriction:r_clean_no_right:no_right_turn" in evidence
