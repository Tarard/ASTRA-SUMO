from pathlib import Path

from torii_sumo.core.overlapping_junction_audit import audit_overlapping_junctions


def test_overlapping_junction_audit_flags_close_top_level_junctions(tmp_path: Path) -> None:
    net_file = tmp_path / "overlap.net.xml"
    net_file.write_text(
        """<net>
  <edge id="main_a" from="a" to="b" type="highway.unclassified">
    <lane id="main_a_0" index="0" length="5" shape="0,0 5,0"/>
  </edge>
  <edge id="main_b" from="b" to="c" type="highway.unclassified">
    <lane id="main_b_0" index="0" length="6" shape="5,0 11,0"/>
  </edge>
  <edge id="foot" from="b" to="p" type="highway.footway">
    <lane id="foot_0" index="0" length="4" allow="pedestrian" shape="5,0 5,4"/>
  </edge>
  <junction id="a" x="0" y="0" type="priority"/>
  <junction id="b" x="5" y="0" type="traffic_light"/>
  <junction id="c" x="11" y="0" type="priority"/>
  <junction id="p" x="5" y="4" type="priority"/>
  <junction id="far" x="80" y="80" type="priority"/>
</net>
""",
        encoding="utf-8",
    )

    report = audit_overlapping_junctions(
        net_file=net_file,
        output_dir=tmp_path / "audit",
        prefix="demo",
        overlap_radius_m=12,
        short_edge_length_m=15,
        min_group_nodes=3,
    )

    assert report["status"] == "pass"
    assert report["overlapping_junction_group_count"] == 1
    group = report["overlapping_junction_groups"][0]
    assert set(group["node_ids"]) == {"a", "b", "c", "p"}
    assert group["recommendation"] == "same_physical_intersection_review"
    assert group["has_vehicle_edges"] is True
    assert group["has_pedestrian_or_bike_edges"] is True
    assert group["direct_edge_ids"] == ["foot", "main_a", "main_b"]
    assert Path(report["groups_file"]).is_file()
    assert Path(report["summary_file"]).is_file()


def test_overlapping_junction_audit_ignores_internal_crossing_layers(tmp_path: Path) -> None:
    net_file = tmp_path / "single_joined.net.xml"
    net_file.write_text(
        """<net>
  <edge id=":joined_0" function="internal">
    <lane id=":joined_0_0" index="0" length="5" shape="0,0 5,0"/>
  </edge>
  <edge id=":joined_c0" function="crossing">
    <lane id=":joined_c0_0" index="0" length="4" allow="pedestrian" shape="0,1 5,1"/>
  </edge>
  <edge id=":joined_w0" function="walkingarea">
    <lane id=":joined_w0_0" index="0" length="4" allow="pedestrian" shape="0,2 5,2"/>
  </edge>
  <junction id="joined" x="0" y="0" type="traffic_light"/>
  <junction id=":joined_0_0" x="1" y="1" type="internal"/>
</net>
""",
        encoding="utf-8",
    )

    report = audit_overlapping_junctions(
        net_file=net_file,
        output_dir=tmp_path / "audit",
        prefix="joined",
    )

    assert report["status"] == "pass"
    assert report["top_level_junction_count"] == 1
    assert report["overlapping_junction_group_count"] == 0
    assert report["ignored_internal_layer_count"] == 3


def test_overlapping_junction_audit_does_not_chain_whole_corridors(tmp_path: Path) -> None:
    net_file = tmp_path / "chain.net.xml"
    net_file.write_text(
        """<net>
  <edge id="ab" from="a" to="b"><lane id="ab_0" index="0" length="10" shape="0,0 10,0"/></edge>
  <edge id="bc" from="b" to="c"><lane id="bc_0" index="0" length="10" shape="10,0 20,0"/></edge>
  <edge id="cd" from="c" to="d"><lane id="cd_0" index="0" length="10" shape="20,0 30,0"/></edge>
  <edge id="de" from="d" to="e"><lane id="de_0" index="0" length="10" shape="30,0 40,0"/></edge>
  <junction id="a" x="0" y="0" type="priority"/>
  <junction id="b" x="10" y="0" type="priority"/>
  <junction id="c" x="20" y="0" type="traffic_light"/>
  <junction id="d" x="30" y="0" type="priority"/>
  <junction id="e" x="40" y="0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )

    report = audit_overlapping_junctions(
        net_file=net_file,
        output_dir=tmp_path / "audit",
        prefix="chain",
        overlap_radius_m=12,
        short_edge_length_m=12,
        min_group_nodes=2,
    )

    assert report["overlapping_junction_group_count"] > 0
    assert all(group["max_pair_distance_m"] <= 24 for group in report["overlapping_junction_groups"])
    assert all(set(group["node_ids"]) != {"a", "b", "c", "d", "e"} for group in report["overlapping_junction_groups"])


def test_overlapping_junction_audit_marks_reference_join_supported_group(tmp_path: Path) -> None:
    net_file = tmp_path / "torii.net.xml"
    net_file.write_text(
        """<net>
  <edge id="ab" from="a" to="b" type="highway.unclassified">
    <lane id="ab_0" index="0" length="5" shape="0,0 5,0"/>
  </edge>
  <edge id="bc" from="b" to="c" type="highway.unclassified">
    <lane id="bc_0" index="0" length="6" shape="5,0 11,0"/>
  </edge>
  <junction id="a" x="0" y="0" type="priority"/>
  <junction id="b" x="5" y="0" type="traffic_light"/>
  <junction id="c" x="11" y="0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )
    reference_join_report = {
        "matched_cases": [
            {
                "reference_id": "cluster_a_b_c",
                "matched_candidate_node_ids": ["a", "b", "c"],
                "learned_rule": "tum_like_join_candidate",
            }
        ]
    }

    report = audit_overlapping_junctions(
        net_file=net_file,
        output_dir=tmp_path / "audit",
        prefix="reference",
        overlap_radius_m=12,
        reference_join_audit_report=reference_join_report,
    )

    group = report["overlapping_junction_groups"][0]
    assert group["reference_join_status"] == "reference_join_supported"
    assert group["reference_join_ids"] == ["cluster_a_b_c"]
    assert group["recommendation"] == "reference_join_supported"


def test_overlapping_junction_audit_skips_plain_vehicle_pairs_without_review_signal(tmp_path: Path) -> None:
    net_file = tmp_path / "plain_pairs.net.xml"
    net_file.write_text(
        """<net>
  <edge id="ab" from="a" to="b" type="highway.residential">
    <lane id="ab_0" index="0" length="5" shape="0,0 5,0"/>
  </edge>
  <junction id="a" x="0" y="0" type="priority"/>
  <junction id="b" x="5" y="0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )

    report = audit_overlapping_junctions(
        net_file=net_file,
        output_dir=tmp_path / "audit",
        prefix="plain",
        overlap_radius_m=12,
    )

    assert report["overlapping_junction_group_count"] == 0
