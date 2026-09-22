from __future__ import annotations

from pathlib import Path

from torii_sumo.core.external_micro_junction_audit import (
    audit_external_micro_junctions,
)


def _write_net(
    path: Path,
    *,
    forward_length: str = "0.20",
    forward_shape: str = "0,0 0.2,0",
    extra_edges: str = "",
    extra_junctions: str = "",
    extra_connections: str = "",
    forward_params: str = "",
    reverse_params: str = "",
) -> None:
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<net>
  <edge id="24498193#2" from="a" to="b" priority="1" type="highway.service">
    <lane id="24498193#2_0" index="0" allow="passenger bicycle" speed="5.56"
          length="{forward_length}" shape="{forward_shape}"/>
    <param key="highway" value="service"/>
    <param key="service" value="parking_aisle"/>
    {forward_params}
  </edge>
  <edge id="-24498193#2" from="b" to="a" priority="1" type="highway.service">
    <lane id="-24498193#2_0" index="0" allow="passenger bicycle" speed="5.56"
          length="0.20" shape="0.2,1 0,1"/>
    <param key="highway" value="service"/>
    <param key="service" value="parking_aisle"/>
    {reverse_params}
  </edge>
  {extra_edges}
  <junction id="a" type="right_before_left" x="0" y="0" incLanes="-24498193#2_0"
            intLanes=""/>
  <junction id="b" type="right_before_left" x="0.2" y="0"
            incLanes="24498193#2_0" intLanes=""/>
  {extra_junctions}
  {extra_connections}
</net>
""",
        encoding="utf-8",
    )


def test_audit_classifies_matching_reciprocal_micro_edges_without_authorizing_edit(
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "micro.net.xml"
    _write_net(net_file)

    report = audit_external_micro_junctions(
        net_file,
        junction_ids=("a", "b"),
    )

    assert report["status"] == "pass"
    assert report["policy"]["automatic_edit_authorization"] == "blocked"
    assert report["reciprocal_micro_pair_count"] == 1
    pair = report["reciprocal_micro_pairs"][0]
    assert pair["classification"] == "geometry_fragment_candidate"
    assert pair["lineage"]["shared_osm_way_ids"] == ["24498193"]
    assert pair["lane_semantics_compatible"] is True
    assert pair["small_loop_risk"]["traversable_two_edge_loop"] is False
    assert {row["edge_id"] for row in pair["edges"]} == {
        "-24498193#2",
        "24498193#2",
    }
    assert all(row["fully_micro"] for row in pair["edges"])


def test_audit_detects_dir_t_turnarounds_and_traversable_two_edge_loop(
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "turnaround.net.xml"
    _write_net(
        net_file,
        extra_connections="""
  <connection from="24498193#2" to="-24498193#2" fromLane="0" toLane="0"
              via=":b_0_0" dir="t" state="M"/>
  <connection from="-24498193#2" to="24498193#2" fromLane="0" toLane="0"
              via=":a_0_0" dir="t" state="M"/>
""",
    )

    report = audit_external_micro_junctions(net_file, junction_ids=("a", "b"))

    assert report["dir_t_turnaround_count"] == 2
    assert all(
        row["audit_disposition"] == "observed_not_authorizing_edit"
        for row in report["dir_t_turnarounds"]
    )
    pair = report["reciprocal_micro_pairs"][0]
    assert pair["classification"] == "geometry_fragment_candidate"
    assert pair["small_loop_risk"] == {
        "reciprocal_directed_edge_pair": True,
        "direct_dir_t_connection_count": 2,
        "traversable_two_edge_loop": True,
        "status": "observed",
        "classification_effect": "none_without_independent_protection_evidence",
    }


def test_audit_protects_threshold_disagreement_controller_stopline_and_crossing(
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "protected.net.xml"
    _write_net(
        net_file,
        forward_length="0.20",
        forward_shape="0,0 1.2,0",
        forward_params='<param key="stop_line" value="surveyed"/>',
        extra_edges="""
  <edge id="in" from="c" to="a"><lane id="in_0" index="0" length="5"
        shape="-5,0 0,0"/></edge>
  <edge id=":a_cross" function="crossing"><lane id=":a_cross_0" index="0"
        allow="pedestrian" length="3" shape="0,-1 0,2"/></edge>
""",
        extra_junctions='<junction id="c" type="priority" x="-5" y="0" incLanes="" intLanes=""/>',
        extra_connections="""
  <connection from="in" to="24498193#2" fromLane="0" toLane="0"
              via=":a_0_0" tl="tls-a" linkIndex="0" dir="s" state="o"/>
""",
    )
    # Attach the crossing to the audited junction without changing the fixture helper.
    text = net_file.read_text(encoding="utf-8").replace(
        'incLanes="-24498193#2_0"\n            intLanes=""',
        'incLanes="-24498193#2_0"\n            intLanes=":a_cross_0"',
    )
    net_file.write_text(text, encoding="utf-8")

    report = audit_external_micro_junctions(net_file, junction_ids=("a", "b"))

    pair = report["reciprocal_micro_pairs"][0]
    assert pair["classification"] == "protected_or_review"
    assert "declared_and_rendered_lengths_cross_micro_threshold" in pair["classification_reasons"]
    protection = pair["protection_evidence"]
    assert protection["controller_ids"] == ["tls-a"]
    assert protection["stopline_markers"]
    assert protection["crossing_or_walkingarea_by_junction"]["a"] == [
        {"edge_id": ":a_cross", "function": "crossing"}
    ]


def test_audit_keeps_explicit_roundabout_separate_from_geometry_fragment(tmp_path: Path) -> None:
    net_file = tmp_path / "roundabout.net.xml"
    _write_net(
        net_file,
        forward_params='<param key="junction" value="roundabout"/>',
        reverse_params='<param key="junction" value="roundabout"/>',
    )

    report = audit_external_micro_junctions(net_file, junction_ids=("a", "b"))

    pair = report["reciprocal_micro_pairs"][0]
    assert pair["classification"] == "explicit_roundabout"
    assert pair["automatic_edit_authorization"] == "blocked"
    assert pair["explicit_roundabout_evidence"]
