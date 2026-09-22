from pathlib import Path
import xml.etree.ElementTree as ET

from torii_sumo.core.reference_road_alignment import audit_reference_road_alignment


def _write_net(path: Path, *, teacher: bool = False) -> None:
    extra = (
        '<edge id="999" from="a" to="j"><lane id="999_0" index="0" shape="-20,10 0,10" allow="passenger"/></edge>'
        '<junction id="a" x="-20" y="10" type="priority"/>'
    ) if teacher else ""
    lane = (
        '<lane id="123#0_0" index="0" shape="-10,0 0,0" allow="pedestrian"/>'
        '<lane id="123#0_1" index="1" shape="-10,1 0,1" allow="passenger"/>'
        if teacher
        else '<lane id="123#0_0" index="0" shape="-10,0 0,0" allow="passenger"/>'
    )
    path.write_text(
        f"""<net>
  <location netOffset="(0,0)"/>
  <edge id="123#0" from="a" to="j" type="highway.residential">{lane}</edge>
  {extra}
  <junction id="a" x="-10" y="0" type="priority"/>
  <junction id="j" x="0" y="0" type="priority"/>
</net>""",
        encoding="utf-8",
    )


def test_reference_road_alignment_uses_source_way_roots_and_allows_split_segments(tmp_path: Path) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source = tmp_path / "source.osm.xml"
    teacher.write_text(
        """<net><edge id="123#0" from="a" to="j"><lane id="123#0_0" index="0" allow="passenger"/></edge></net>""",
        encoding="utf-8",
    )
    candidate.write_text(
        """<net>
  <edge id="123#0" from="a" to="mid"><lane id="123#0_0" index="0" allow="passenger"/></edge>
  <edge id="123#1" from="mid" to="j"><lane id="123#1_0" index="0" allow="passenger"/></edge>
</net>""",
        encoding="utf-8",
    )
    source.write_text(
        """<osm version="0.6"><way id="123"><nd ref="a"/><nd ref="j"/><tag k="highway" v="residential"/></way></osm>""",
        encoding="utf-8",
    )

    report = audit_reference_road_alignment(teacher, candidate, source_osm_file=source)

    assert report["status"] == "pass"
    assert report["source_coverage"]["status"] == "pass"
    assert report["source_way_counts"]["common_source_way_count"] == 1
    assert report["way_semantics"]["mismatch_count"] == 0


def test_reference_road_alignment_emits_manual_gap_and_additional_overlay(tmp_path: Path) -> None:
    teacher = tmp_path / "teacher.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    source = tmp_path / "source.osm.xml"
    _write_net(teacher, teacher=True)
    _write_net(candidate, teacher=False)
    source.write_text(
        """<osm version="0.6"><way id="123"><nd ref="a"/><nd ref="j"/><tag k="highway" v="residential"/></way></osm>""",
        encoding="utf-8",
    )

    report = audit_reference_road_alignment(
        teacher,
        candidate,
        source_osm_file=source,
        output_dir=tmp_path / "alignment",
        prefix="case",
    )

    assert report["status"] == "needs_review"
    assert report["source_coverage"]["teacher_source_way_absent_from_osm_count"] == 1
    assert report["way_semantics"]["lane_profile_mismatch_count"] == 1
    assert any(row["category"] == "manual_reference_source_gap" for row in report["manual_reference_source_rows"])
    assert Path(report["report_file"]).is_file()
    additional = Path(report["additional_file"])
    assert additional.is_file()
    assert len(ET.parse(additional).getroot().findall("poly")) == report["review_location_count"]
