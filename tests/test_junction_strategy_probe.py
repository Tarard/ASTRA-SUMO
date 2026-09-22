import json
from pathlib import Path

from torii_sumo.core.junction_strategy_probe import (
    convex_hull,
    parse_net,
    probe_junction_strategies,
    strategy_node_sets,
)


def test_convex_hull_discards_inner_points() -> None:
    hull = convex_hull([(0, 0), (1, 0), (1, 1), (0, 1), (0.4, 0.4)])

    assert hull == [(0, 0), (1, 0), (1, 1), (0, 1)]


def test_strategy_node_sets_keep_vehicle_core_separate_from_modal_stubs(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(
        """<net>
  <edge id="ab" from="a" to="b" type="highway.unclassified">
    <lane id="ab_0" index="0" length="0.2" shape="0,0 0.2,0"/>
  </edge>
  <edge id="bc" from="b" to="c" type="highway.residential">
    <lane id="bc_0" index="0" length="0.2" shape="0.2,0 0.4,0"/>
  </edge>
  <edge id="bp" from="b" to="p" type="highway.footway">
    <lane id="bp_0" index="0" length="0.2" shape="0.2,0 0.2,0.2"/>
  </edge>
  <junction id="a" x="0" y="0" type="priority"/>
  <junction id="b" x="0.2" y="0" type="priority"/>
  <junction id="c" x="0.4" y="0" type="priority"/>
  <junction id="p" x="0.2" y="0.2" type="priority"/>
</net>
""",
        encoding="utf-8",
    )
    net = parse_net(net_file)

    strategies = strategy_node_sets(net, seed_node_ids=["a", "b"], radius_m=5, short_edge_m=1)

    assert strategies["radius_all_top_level"] == {"a", "b", "c", "p"}
    assert strategies["short_vehicle_core"] == {"a", "b", "c"}
    assert strategies["short_all_core"] == {"a", "b", "c", "p"}


def test_probe_writes_comparison_artifacts(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    reference = tmp_path / "reference.net.xml"
    candidate.write_text(
        """<net>
  <edge id="ab" from="a" to="b" type="highway.unclassified"><lane id="ab_0" index="0" length="0.2" shape="0,0 1,0"/></edge>
  <edge id="bp" from="b" to="p" type="highway.footway"><lane id="bp_0" index="0" length="0.2" shape="1,0 1,1"/></edge>
  <junction id="a" x="0" y="0" type="priority" shape="0,0 0,1 1,1 1,0"/>
  <junction id="b" x="1" y="0" type="priority" shape="1,0 1,1 2,1 2,0"/>
  <junction id="p" x="1" y="1" type="priority"/>
</net>
""",
        encoding="utf-8",
    )
    reference.write_text(
        """<net>
  <junction id="cluster_a_b" x="1000.5" y="1000" type="traffic_light" shape="1000,1000 1000,1001 1002,1001 1002,1000"/>
</net>
""",
        encoding="utf-8",
    )

    report = probe_junction_strategies(
        candidate_net_file=candidate,
        reference_net_file=reference,
        reference_junction_id="cluster_a_b",
        output_dir=tmp_path / "out",
        radius_m=5,
        short_edge_m=1,
    )

    assert report["status"] == "pass"
    assert {"reference_core", "radius_all_top_level", "short_vehicle_core", "short_all_core"} <= set(
        report["strategies"]
    )
    assert Path(report["summary_file"]).is_file()
    assert Path(report["csv_file"]).is_file()
    assert Path(report["svg_file"]).is_file()
    assert Path(report["png_file"]).is_file()
    assert json.loads(Path(report["summary_file"]).read_text(encoding="utf-8"))["status"] == "pass"
    assert report["strategies"]["reference_core"]["footprint"]["centroid_distance_to_reference"] < 2


def test_edge_bounded_strategy_limits_footprint_to_incident_edge_gates(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    reference = tmp_path / "reference.net.xml"
    candidate.write_text(
        """<net>
  <edge id="ab" from="a" to="b" type="highway.unclassified"><lane id="ab_0" index="0" length="0.5" shape="0,0 0.5,0"/></edge>
  <edge id="na" from="n" to="a" type="highway.unclassified"><lane id="na_0" index="0" length="4" shape="0,5 0,1"/></edge>
  <edge id="be" from="b" to="e" type="highway.unclassified"><lane id="be_0" index="0" length="4" shape="1,0 5,0"/></edge>
  <edge id="wb" from="w" to="a" type="highway.unclassified"><lane id="wb_0" index="0" length="4" shape="-5,0 -1,0"/></edge>
  <edge id="bs" from="b" to="s" type="highway.unclassified"><lane id="bs_0" index="0" length="4" shape="0,-1 0,-5"/></edge>
  <junction id="a" x="0" y="0" type="priority" shape="-10,-10 -10,10 0,10 0,-10"/>
  <junction id="b" x="0.5" y="0" type="priority" shape="0,-10 0,10 10,10 10,-10"/>
  <junction id="n" x="0" y="5" type="priority"/>
  <junction id="e" x="5" y="0" type="priority"/>
  <junction id="w" x="-5" y="0" type="priority"/>
  <junction id="s" x="0" y="-5" type="priority"/>
</net>
""",
        encoding="utf-8",
    )
    reference.write_text(
        """<net>
  <junction id="cluster_a_b" x="0.25" y="0" type="traffic_light" shape="-1,-1 -1,1 1,1 1,-1"/>
</net>
""",
        encoding="utf-8",
    )

    report = probe_junction_strategies(
        candidate_net_file=candidate,
        reference_net_file=reference,
        reference_junction_id="cluster_a_b",
        output_dir=tmp_path / "out",
        radius_m=8,
        short_edge_m=1,
    )

    bounded = report["strategies"]["edge_bounded_short_core"]["footprint"]["area"]
    unbounded = report["strategies"]["short_all_core"]["footprint"]["area"]

    assert bounded < unbounded / 10


def test_approach_setback_strategy_uses_points_behind_intersection_core(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    reference = tmp_path / "reference.net.xml"
    candidate.write_text(
        """<net>
  <edge id="ab" from="a" to="b" type="highway.unclassified"><lane id="ab_0" index="0" length="0.5" shape="0,0 1,0"/></edge>
  <edge id="na" from="n" to="a" type="highway.unclassified"><lane id="na_0" index="0" length="5" shape="0,5 0,0"/></edge>
  <edge id="be" from="b" to="e" type="highway.unclassified"><lane id="be_0" index="0" length="5" shape="1,0 6,0"/></edge>
  <edge id="wa" from="w" to="a" type="highway.unclassified"><lane id="wa_0" index="0" length="5" shape="-5,0 0,0"/></edge>
  <edge id="bs" from="b" to="s" type="highway.unclassified"><lane id="bs_0" index="0" length="5" shape="1,0 1,-5"/></edge>
  <junction id="a" x="0" y="0" type="priority"/>
  <junction id="b" x="1" y="0" type="priority"/>
  <junction id="n" x="0" y="5" type="priority"/>
  <junction id="e" x="6" y="0" type="priority"/>
  <junction id="w" x="-5" y="0" type="priority"/>
  <junction id="s" x="1" y="-5" type="priority"/>
</net>
""",
        encoding="utf-8",
    )
    reference.write_text(
        """<net>
  <junction id="cluster_a_b" x="0.5" y="0" type="traffic_light" shape="-2,-2 -2,2 3,2 3,-2"/>
</net>
""",
        encoding="utf-8",
    )

    report = probe_junction_strategies(
        candidate_net_file=candidate,
        reference_net_file=reference,
        reference_junction_id="cluster_a_b",
        output_dir=tmp_path / "out",
        radius_m=8,
        short_edge_m=1,
        approach_setback_m=2,
    )

    strategy = report["strategies"]["approach_setback_core"]

    assert strategy["polygon"] == [(-2.0, 0.0), (1.0, -2.0), (3.0, 0.0), (0.0, 2.0)]
    assert strategy["collapse_node_ids"] == ["a", "b"]
    assert strategy["inside_plain_edge_ids"] == ["ab"]
    assert strategy["boundary_edge_ids"] == ["be", "bs", "na", "wa"]


def test_approach_setback_strategy_uses_service_edges_as_shape_support(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    reference = tmp_path / "reference.net.xml"
    candidate.write_text(
        """<net>
  <edge id="ab" from="a" to="b" type="highway.unclassified"><lane id="ab_0" index="0" length="0.5" shape="0,0 1,0"/></edge>
  <edge id="na" from="n" to="a" type="highway.unclassified"><lane id="na_0" index="0" length="5" shape="0,5 0,0"/></edge>
  <edge id="be" from="b" to="e" type="highway.unclassified"><lane id="be_0" index="0" length="5" shape="1,0 6,0"/></edge>
  <edge id="wa" from="w" to="a" type="highway.unclassified"><lane id="wa_0" index="0" length="5" shape="-5,0 0,0"/></edge>
  <edge id="bs_service" from="b" to="service" type="highway.service"><lane id="bs_service_0" index="0" length="5" shape="1,0 1,-5"/></edge>
  <junction id="a" x="0" y="0" type="priority"/>
  <junction id="b" x="1" y="0" type="priority"/>
  <junction id="n" x="0" y="5" type="priority"/>
  <junction id="e" x="6" y="0" type="priority"/>
  <junction id="w" x="-5" y="0" type="priority"/>
  <junction id="service" x="1" y="-5" type="priority"/>
</net>
""",
        encoding="utf-8",
    )
    reference.write_text(
        """<net>
  <junction id="cluster_a_b" x="0.5" y="0" type="traffic_light" shape="-2,-2 -2,2 3,2 3,-2"/>
</net>
""",
        encoding="utf-8",
    )

    report = probe_junction_strategies(
        candidate_net_file=candidate,
        reference_net_file=reference,
        reference_junction_id="cluster_a_b",
        output_dir=tmp_path / "out",
        radius_m=8,
        short_edge_m=1,
        approach_setback_m=2,
    )

    strategy = report["strategies"]["approach_setback_core"]

    assert (1.0, -2.0) in strategy["polygon"]
    assert strategy["collapse_node_ids"] == ["a", "b"]
    assert strategy["shape_support_edge_ids"] == ["be", "bs_service", "na", "wa"]


def test_lane_buffered_approach_strategy_uses_lane_width_from_net_xml(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    reference = tmp_path / "reference.net.xml"
    candidate.write_text(
        """<net>
  <edge id="ab" from="a" to="b" type="highway.unclassified">
    <lane id="ab_0" index="0" length="0.5" shape="0,0 1,0"/>
  </edge>
  <edge id="na" from="n" to="a" type="highway.unclassified">
    <lane id="na_0" index="0" width="3.2" length="5" shape="0,5 0,0"/>
  </edge>
  <edge id="be" from="b" to="e" type="highway.unclassified">
    <lane id="be_0" index="0" width="3.5" length="5" shape="1,0 6,0"/>
    <lane id="be_1" index="1" width="3.5" length="5" shape="1,0.2 6,0.2"/>
  </edge>
  <edge id="wa" from="w" to="a" type="highway.unclassified">
    <lane id="wa_0" index="0" width="3.5" length="5" shape="-5,0 0,0"/>
    <lane id="wa_1" index="1" width="3.5" length="5" shape="-5,-0.2 0,-0.2"/>
  </edge>
  <edge id="bs" from="b" to="s" type="highway.service">
    <lane id="bs_0" index="0" width="2.8" length="5" shape="1,0 1,-5"/>
  </edge>
  <edge id="bp" from="b" to="p" type="highway.footway">
    <lane id="bp_0" index="0" width="1.5" length="5" shape="1,0 1,5"/>
  </edge>
  <junction id="a" x="0" y="0" type="priority"/>
  <junction id="b" x="1" y="0" type="priority"/>
  <junction id="n" x="0" y="5" type="priority"/>
  <junction id="e" x="6" y="0" type="priority"/>
  <junction id="w" x="-5" y="0" type="priority"/>
  <junction id="s" x="1" y="-5" type="priority"/>
  <junction id="p" x="1" y="5" type="priority"/>
</net>
""",
        encoding="utf-8",
    )
    reference.write_text(
        """<net>
  <junction id="cluster_a_b" x="0.5" y="0" type="traffic_light" shape="-2,-2 -2,2 3,2 3,-2"/>
</net>
""",
        encoding="utf-8",
    )

    report = probe_junction_strategies(
        candidate_net_file=candidate,
        reference_net_file=reference,
        reference_junction_id="cluster_a_b",
        output_dir=tmp_path / "out",
        radius_m=8,
        short_edge_m=1,
        approach_setback_m=2,
    )

    buffered = report["strategies"]["lane_buffered_approach_setback_core"]
    centerline = report["strategies"]["approach_setback_core"]

    assert buffered["shape_support_edge_ids"] == ["be", "bs", "na", "wa"]
    assert buffered["review_support_edge_ids"] == ["bp"]
    assert buffered["buffer_point_count"] == 8
    assert buffered["footprint"]["area"] > centerline["footprint"]["area"]


def test_approach_setback_strategy_reports_sharp_corner_audit(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    reference = tmp_path / "reference.net.xml"
    candidate.write_text(
        """<net>
  <edge id="an" from="a" to="n" type="highway.unclassified"><lane id="an_0" index="0" length="5" shape="0,0 0,5"/></edge>
  <edge id="ae" from="a" to="e" type="highway.unclassified"><lane id="ae_0" index="0" length="0.5" shape="0,0 0.5,0"/></edge>
  <edge id="aw" from="a" to="w" type="highway.unclassified"><lane id="aw_0" index="0" length="0.5" shape="0,0 -0.5,0"/></edge>
  <junction id="a" x="0" y="0" type="priority"/>
  <junction id="n" x="0" y="5" type="priority"/>
  <junction id="e" x="0.5" y="0" type="priority"/>
  <junction id="w" x="-0.5" y="0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )
    reference.write_text(
        """<net>
  <junction id="cluster_a" x="0" y="0" type="traffic_light" shape="-0.5,0 0.5,0 0,5"/>
</net>
""",
        encoding="utf-8",
    )

    report = probe_junction_strategies(
        candidate_net_file=candidate,
        reference_net_file=reference,
        reference_junction_id="cluster_a",
        output_dir=tmp_path / "out",
        radius_m=8,
        short_edge_m=1,
        approach_setback_m=5,
    )

    audit = report["strategies"]["approach_setback_core"]["footprint"]["sharp_corner_audit"]

    assert audit["sharp_corner_count"] == 1
    assert audit["min_corner_angle_deg"] < 35
    assert audit["status"] == "needs_review"


def test_protected_terminal_strategy_keeps_modal_exits_out_of_join_core(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    reference = tmp_path / "reference.net.xml"
    candidate.write_text(
        """<net>
  <edge id="ab" from="a" to="b" type="highway.unclassified"><lane id="ab_0" index="0" length="0.2" shape="0,0 0.2,0"/></edge>
  <edge id="bs" from="b" to="s" type="highway.unclassified"><lane id="bs_0" index="0" length="0.2" shape="0.2,0 0.4,0"/></edge>
  <edge id="bp" from="b" to="p" type="highway.footway"><lane id="bp_0" index="0" length="0.2" shape="0.2,0 0.2,0.2"/></edge>
  <edge id="px" from="p" to="x" type="highway.footway"><lane id="px_0" index="0" length="12" shape="0.2,0.2 0.2,12"/></edge>
  <junction id="a" x="0" y="0" type="priority"/>
  <junction id="b" x="0.2" y="0" type="priority"/>
  <junction id="s" x="0.4" y="0" type="priority"/>
  <junction id="p" x="0.2" y="0.2" type="priority"/>
  <junction id="x" x="0.2" y="12" type="priority"/>
</net>
""",
        encoding="utf-8",
    )
    reference.write_text(
        """<net>
  <junction id="cluster_a_b" x="0.1" y="0" type="traffic_light" shape="0,0 0,1 1,1 1,0"/>
</net>
""",
        encoding="utf-8",
    )

    report = probe_junction_strategies(
        candidate_net_file=candidate,
        reference_net_file=reference,
        reference_junction_id="cluster_a_b",
        output_dir=tmp_path / "out",
        radius_m=5,
        short_edge_m=1,
    )

    strategy = report["strategies"]["short_all_core_with_protected_terminals"]

    assert set(strategy["node_ids"]) == {"a", "b", "s"}
    assert strategy["protected_terminal_ids"] == ["p"]
    assert strategy["shape_support_node_ids"] == ["a", "b", "p", "s"]


def test_nearby_conflict_zone_audit_flags_close_reference_clusters(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.net.xml"
    reference = tmp_path / "reference.net.xml"
    candidate.write_text(
        """<net>
  <edge id="ab" from="a" to="b" type="highway.unclassified"><lane id="ab_0" index="0" length="0.2" shape="0,0 1,0"/></edge>
  <junction id="a" x="0" y="0" type="priority"/>
  <junction id="b" x="1" y="0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )
    reference.write_text(
        """<net>
  <location netOffset="0.00,0.00" convBoundary="0.00,0.00,100.00,100.00" origBoundary="11.000000,48.000000,11.001000,48.001000" projParameter="!"/>
  <junction id="cluster_a_b" x="10" y="10" type="traffic_light" shape="9,9 9,11 11,11 11,9"/>
  <junction id="plain_priority" x="10" y="20" type="priority"/>
  <junction id="cluster_dead_end" x="10" y="25" type="dead_end"/>
  <junction id="cluster_c_d" x="10" y="30" type="traffic_light" shape="9,29 9,31 11,31 11,29"/>
</net>
""",
        encoding="utf-8",
    )

    report = probe_junction_strategies(
        candidate_net_file=candidate,
        reference_net_file=reference,
        reference_junction_id="cluster_a_b",
        output_dir=tmp_path / "out",
        radius_m=5,
        short_edge_m=1,
    )

    audit = report["nearby_conflict_zone_audit"]

    assert audit["status"] == "nearby_core_review"
    assert audit["nearby_count"] == 1
    assert audit["nearby_conflict_zones"][0]["junction_id"] == "cluster_c_d"
    assert audit["nearby_conflict_zones"][0]["distance_m"] == 20.0
    assert audit["nearby_conflict_zones"][0]["google_maps_url"].startswith("https://www.google.com/maps/@")
    assert audit["nearby_conflict_zones"][0]["osm_url"].startswith("https://www.openstreetmap.org/#map=19/")
