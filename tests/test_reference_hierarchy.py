from pathlib import Path
import json
import xml.etree.ElementTree as ET

from torii_sumo.core.reference_hierarchy import audit_reference_hierarchy, build_reference_hierarchy_type_repair_variant


def _write_net(path: Path, edge_specs: list[tuple[str, str, str, str, float, str]]) -> None:
    _write_named_net(
        path,
        [
            (edge_id, from_node, to_node, edge_type, length, shape, edge_id)
            for edge_id, from_node, to_node, edge_type, length, shape in edge_specs
        ],
    )


def _write_named_net(path: Path, edge_specs: list[tuple[str, str, str, str, float, str, str]]) -> None:
    junction_positions: dict[str, tuple[float, float]] = {}
    for _edge_id, from_node, to_node, _edge_type, _length, shape, _name in edge_specs:
        points = _shape_points(shape)
        junction_positions.setdefault(from_node, points[0])
        junction_positions.setdefault(to_node, points[-1])
    junction_xml = "\n".join(
        f'  <junction id="{junction_id}" x="{x:.1f}" y="{y:.1f}" type="priority"/>'
        for junction_id, (x, y) in sorted(junction_positions.items())
    )
    edge_xml = "\n".join(
        f'''  <edge id="{edge_id}" from="{from_node}" to="{to_node}" type="{edge_type}" name="{name}">
    <lane id="{edge_id}_0" index="0" speed="13.9" length="{length:.1f}" shape="{shape}"/>
  </edge>'''
        for edge_id, from_node, to_node, edge_type, length, shape, name in edge_specs
    )
    path.write_text(f"<net>\n{junction_xml}\n{edge_xml}\n</net>\n", encoding="utf-8")


def _shape_points(shape: str) -> list[tuple[float, float]]:
    return [tuple(float(value) for value in point.split(",")) for point in shape.split()]


def test_reference_hierarchy_audit_classifies_high_road_gap_modes(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    _write_net(
        reference_net,
        [
            ("ref_primary_corridor", "ra", "rd", "highway.primary", 300.0, "0,0 300,0"),
            ("ref_secondary_corridor", "se", "sf", "highway.secondary", 180.0, "0,100 180,100"),
            ("ref_residential_context", "lo", "lp", "highway.residential", 120.0, "0,200 120,200"),
        ],
    )
    _write_net(
        candidate_net,
        [
            ("cand_primary_a", "ca", "cb", "highway.primary", 90.0, "0,1 90,1"),
            ("cand_primary_b", "cb", "cc", "highway.primary", 95.0, "90,1 185,1"),
            ("cand_primary_c", "cc", "cd", "highway.primary", 110.0, "185,1 295,1"),
            ("cand_primary_far", "cf", "cg", "highway.primary", 130.0, "900,0 1030,0"),
            ("cand_primary_on_local", "ch", "ci", "highway.primary", 80.0, "0,201 80,201"),
            ("cand_secondary_link", "cj", "ck", "highway.secondary_link", 45.0, "100,100 145,115"),
        ],
    )

    report = audit_reference_hierarchy(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / "hierarchy",
        prefix="demo",
        match_distance_m=20.0,
        oversplit_length_ratio=0.5,
        min_extra_edges=1,
    )

    assert report["status"] == "blocked"
    assert report["reference_hierarchy_status"] == "needs_review"
    assert report["candidate_high_hierarchy_edge_count"] == 6
    assert report["high_hierarchy_issue_count"] == 6
    assert report["decision_counts"] == {
        "link_or_slip_lane": 1,
        "matched_but_oversplit": 3,
        "out_of_reference_scope": 1,
        "type_hierarchy_mismatch": 1,
    }
    cases_by_id = {case["candidate_edge_id"]: case for case in report["candidate_cases"]}
    assert cases_by_id["cand_primary_a"]["hierarchy_decision"] == "matched_but_oversplit"
    assert cases_by_id["cand_primary_a"]["nearest_same_type_reference_edge_id"] == "ref_primary_corridor"
    assert cases_by_id["cand_primary_far"]["hierarchy_decision"] == "out_of_reference_scope"
    assert cases_by_id["cand_primary_on_local"]["hierarchy_decision"] == "type_hierarchy_mismatch"
    assert cases_by_id["cand_primary_on_local"]["nearest_any_reference_edge_type"] == "highway.residential"
    assert cases_by_id["cand_secondary_link"]["hierarchy_decision"] == "link_or_slip_lane"
    assert cases_by_id["cand_secondary_link"]["recommended_action"] == "protect_for_map_review"
    primary_comparison = {
        row["edge_type"]: row for row in report["type_comparisons"]
    }["highway.primary"]
    assert primary_comparison["hierarchy_scope_decision"] == "overrepresented_in_candidate"
    assert Path(report["cases_file"]).is_file()
    assert Path(report["type_comparison_file"]).is_file()
    assert Path(report["summary_file"]).is_file()


def test_reference_hierarchy_can_resolve_proven_equivalent_osm_fragmentation(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference_fragmented.net.xml"
    candidate_net = tmp_path / "candidate_fragmented.net.xml"
    _write_named_net(
        reference_net,
        [("ref_corridor", "ra", "rd", "highway.primary", 300.0, "0,0 300,0", "Ringstrasse")],
    )
    _write_named_net(
        candidate_net,
        [
            ("cand_a", "ca", "cb", "highway.primary", 90.0, "0,1 90,1", "ringstrasse"),
            ("cand_b", "cb", "cc", "highway.primary", 95.0, "90,1 185,1", "ringstrasse"),
            ("cand_c", "cc", "cd", "highway.primary", 110.0, "185,1 295,1", "ringstrasse"),
        ],
    )

    report = audit_reference_hierarchy(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / "hierarchy_equivalent",
        prefix="equivalent",
        match_distance_m=20.0,
        oversplit_length_ratio=0.5,
        min_extra_edges=1,
        resolve_equivalent_fragmentation=True,
    )

    assert report["status"] == "pass"
    assert report["reference_hierarchy_status"] == "pass"
    assert report["high_hierarchy_issue_count"] == 0
    assert report["raw_high_hierarchy_issue_count"] == 3
    assert report["equivalent_fragmentation_count"] == 3
    assert report["decision_counts"] == {"aligned": 3}
    assert all(
        case["hierarchy_resolution"] == "equivalent_fragmentation"
        for case in report["candidate_cases"]
    )


def test_reference_hierarchy_audit_shortens_long_artifact_paths(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference_short.net.xml"
    candidate_net = tmp_path / "candidate_short.net.xml"
    _write_net(reference_net, [("ref_primary", "a", "b", "highway.primary", 100.0, "0,0 100,0")])
    _write_net(candidate_net, [("cand_primary", "c", "d", "highway.primary", 100.0, "0,0 100,0")])

    report = audit_reference_hierarchy(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / ("hierarchy_" + "x" * 100),
        prefix="p" * 140,
    )

    assert Path(report["cases_file"]).is_file()
    assert Path(report["type_comparison_file"]).is_file()
    assert Path(report["summary_file"]).is_file()
    assert Path(report["cases_file"]).name.startswith("p_")


def test_reference_hierarchy_audit_passes_when_high_roads_are_aligned(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    _write_net(
        reference_net,
        [("ref_primary", "ra", "rb", "highway.primary", 150.0, "0,0 150,0")],
    )
    _write_net(
        candidate_net,
        [("cand_primary", "ca", "cb", "highway.primary", 152.0, "0,2 152,2")],
    )

    report = audit_reference_hierarchy(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / "hierarchy",
        prefix="aligned",
        match_distance_m=10.0,
        min_extra_edges=1,
    )

    assert report["status"] == "pass"
    assert report["reference_hierarchy_status"] == "pass"
    assert report["high_hierarchy_issue_count"] == 0
    assert report["decision_counts"] == {"aligned": 1}


def test_reference_hierarchy_audit_treats_compound_reference_types_as_high_roads(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    _write_named_net(
        reference_net,
        [
            (
                "ref_tertiary_cycle_track",
                "ra",
                "rb",
                "cycleway.track|highway.tertiary",
                120.0,
                "0,0 120,0",
                "Jahnstrasse",
            )
        ],
    )
    _write_named_net(
        candidate_net,
        [
            (
                "cand_tertiary",
                "ca",
                "cb",
                "highway.tertiary",
                118.0,
                "0,2 118,2",
                "Jahnstrasse",
            )
        ],
    )

    report = audit_reference_hierarchy(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / "hierarchy",
        prefix="compound_type",
        match_distance_m=10.0,
        min_extra_edges=1,
    )

    assert report["status"] == "pass"
    assert report["reference_high_hierarchy_edge_count"] == 1
    assert report["decision_counts"] == {"aligned": 1}
    assert report["type_comparisons"] == [
        {
            "edge_type": "highway.tertiary",
            "reference_count": 1,
            "candidate_count": 1,
            "extra_edge_count": 0,
            "candidate_to_reference_ratio": 1.0,
            "hierarchy_scope_decision": "reference_aligned",
        }
    ]


def test_reference_hierarchy_audit_keeps_link_when_reference_has_same_link_type(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    _write_named_net(
        reference_net,
        [
            (
                "ref_link",
                "ra",
                "rb",
                "cycleway.track|highway.primary_link",
                80.0,
                "0,0 80,0",
                "Hindenburgstrasse",
            )
        ],
    )
    _write_named_net(
        candidate_net,
        [
            (
                "cand_link",
                "ca",
                "cb",
                "cycleway.track|highway.primary_link",
                78.0,
                "0,3 78,3",
                "Hindenburgstrasse",
            )
        ],
    )

    report = audit_reference_hierarchy(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / "hierarchy",
        prefix="same_link",
        match_distance_m=10.0,
        min_extra_edges=1,
    )

    assert report["status"] == "pass"
    assert report["decision_counts"] == {"aligned": 1}
    assert report["candidate_cases"][0]["nearest_same_type_reference_edge_id"] == "ref_link"


def test_reference_hierarchy_audit_keeps_high_edge_when_reference_type_is_missing(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    reference_net.write_text(
        """<net>
  <junction id="ra" x="0.0" y="0.0" type="priority"/>
  <junction id="rb" x="100.0" y="0.0" type="priority"/>
  <edge id="ref_missing" from="ra" to="rb">
    <lane id="ref_missing_0" index="0" length="100.0" shape="0,0 100,0"/>
  </edge>
</net>
""",
        encoding="utf-8",
    )
    _write_named_net(
        candidate_net,
        [("cand_primary", "ca", "cb", "highway.primary", 98.0, "0,1 98,1", "B 13")],
    )

    report = audit_reference_hierarchy(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / "hierarchy",
        prefix="missing_reference_type",
        match_distance_m=10.0,
        min_extra_edges=1,
    )

    assert report["status"] == "pass"
    assert report["candidate_cases"][0]["corridor_match_basis"] == "reference_type_missing"


def test_reference_hierarchy_audit_uses_same_name_same_type_corridor_beyond_distance_gate(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    _write_named_net(
        reference_net,
        [
            ("ref_context", "ra", "rb", "highway.unclassified", 100.0, "0,1 100,1", "Context"),
            ("ref_secondary", "rc", "rd", "highway.secondary", 100.0, "0,40 100,40", "Ringstrasse"),
        ],
    )
    _write_named_net(
        candidate_net,
        [("cand_secondary", "ca", "cb", "highway.secondary", 98.0, "0,0 98,0", "Ringstrasse")],
    )

    report = audit_reference_hierarchy(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / "hierarchy",
        prefix="same_name_same_type",
        match_distance_m=10.0,
        min_extra_edges=1,
    )

    assert report["status"] == "pass"
    assert report["candidate_cases"][0]["corridor_match_basis"] == "same_name"


def test_reference_hierarchy_audit_uses_same_road_name_as_corridor_evidence(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    _write_named_net(
        reference_net,
        [("ref_ring", "ra", "rb", "highway.primary", 300.0, "0,0 300,0", "Ringstrasse")],
    )
    _write_named_net(
        candidate_net,
        [
            ("cand_ring_a", "ca", "cb", "highway.primary", 80.0, "0,45 80,45", " Ringstrasse "),
            ("cand_ring_b", "cb", "cc", "highway.primary", 85.0, "80,45 165,45", "ringstrasse"),
            ("cand_ring_c", "cc", "cd", "highway.primary", 95.0, "165,45 260,45", "RINGSTRASSE"),
        ],
    )

    report = audit_reference_hierarchy(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / "hierarchy",
        prefix="same_name",
        match_distance_m=20.0,
        oversplit_length_ratio=0.5,
        min_extra_edges=1,
    )

    assert report["status"] == "blocked"
    assert report["decision_counts"] == {"matched_but_oversplit": 3}
    assert report["corridor_match_basis_counts"] == {"same_name": 3}
    assert report["same_name_match_status_counts"] == {"matched_by_name": 3}
    cases_by_id = {case["candidate_edge_id"]: case for case in report["candidate_cases"]}
    assert cases_by_id["cand_ring_a"]["hierarchy_decision"] == "matched_but_oversplit"
    assert cases_by_id["cand_ring_a"]["recommended_action"] == "corridor_merge_review"
    assert cases_by_id["cand_ring_a"]["same_name_reference_edge_id"] == "ref_ring"
    assert cases_by_id["cand_ring_a"]["same_name_match_status"] == "matched_by_name"
    assert cases_by_id["cand_ring_a"]["corridor_match_basis"] == "same_name"
    assert cases_by_id["cand_ring_a"]["candidate_edge_name_normalized"] == "ringstrasse"
    assert cases_by_id["cand_ring_a"]["same_name_reference_distance_m"] > 20.0


def test_reference_hierarchy_audit_matches_networks_with_different_net_offsets(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference_offset.net.xml"
    candidate_net = tmp_path / "candidate_offset.net.xml"
    reference_net.write_text(
        """<net>
  <location netOffset="-1000.00,-2000.00" convBoundary="675000.00,5400000.00,675150.00,5400000.00" origBoundary="11.0,48.0,11.1,48.1" projParameter="+proj=utm +zone=32 +ellps=WGS84 +datum=WGS84 +units=m +no_defs"/>
  <junction id="ra" x="675000.0" y="5400000.0" type="priority"/>
  <junction id="rb" x="675150.0" y="5400000.0" type="priority"/>
  <edge id="ref_primary" from="ra" to="rb" type="highway.primary" name="same road">
    <lane id="ref_primary_0" index="0" length="150.0" shape="675000.0,5400000.0 675150.0,5400000.0"/>
  </edge>
</net>
""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <location netOffset="-2000.00,-3000.00" convBoundary="674000.00,5399000.00,674150.00,5399000.00" origBoundary="11.0,48.0,11.1,48.1" projParameter="+proj=utm +zone=32 +ellps=WGS84 +datum=WGS84 +units=m +no_defs"/>
  <junction id="ca" x="674000.0" y="5399000.0" type="priority"/>
  <junction id="cb" x="674150.0" y="5399000.0" type="priority"/>
  <edge id="cand_primary" from="ca" to="cb" type="highway.primary" name="same road">
    <lane id="cand_primary_0" index="0" length="150.0" shape="674000.0,5399000.0 674150.0,5399000.0"/>
  </edge>
</net>
""",
        encoding="utf-8",
    )

    report = audit_reference_hierarchy(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / "hierarchy",
        prefix="offsets",
        match_distance_m=10.0,
        min_extra_edges=1,
    )

    assert report["status"] == "pass"
    assert report["candidate_cases"][0]["hierarchy_decision"] == "aligned"
    assert report["candidate_cases"][0]["nearest_same_type_reference_distance_m"] < 1.0


def test_reference_hierarchy_type_repair_variant_copies_same_name_reference_type(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    _write_named_net(
        reference_net,
        [("ref_ring", "ra", "rb", "highway.secondary", 120.0, "0,0 120,0", "Ringstrasse")],
    )
    _write_named_net(
        candidate_net,
        [("cand_ring", "ca", "cb", "highway.primary", 118.0, "0,2 118,2", "Ringstrasse")],
    )
    audit = audit_reference_hierarchy(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / "hierarchy",
        prefix="before",
        match_distance_m=10.0,
        min_extra_edges=1,
    )

    repair = build_reference_hierarchy_type_repair_variant(
        candidate_net_file=candidate_net,
        reference_hierarchy_report=audit,
        output_dir=tmp_path / "repair",
        prefix="repair",
    )

    assert repair["status"] == "pass"
    assert repair["reference_hierarchy_type_repair_status"] == "variant_created_for_review"
    assert repair["reference_hierarchy_type_repair_count"] == 1
    variant_root = ET.parse(repair["reference_hierarchy_type_repair_variant_file"]).getroot()
    assert variant_root.find("edge[@id='cand_ring']").attrib["type"] == "highway.secondary"
    repaired_audit = audit_reference_hierarchy(
        reference_net_file=reference_net,
        candidate_net_file=Path(repair["reference_hierarchy_type_repair_variant_file"]),
        output_dir=tmp_path / "hierarchy_repaired",
        prefix="after",
        match_distance_m=10.0,
        min_extra_edges=1,
    )
    assert repaired_audit["status"] == "pass"


def test_reference_hierarchy_type_repair_preserves_candidate_modal_decoration(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    _write_named_net(
        reference_net,
        [
            (
                "ref_ring",
                "ra",
                "rb",
                "cycleway.track|highway.tertiary",
                120.0,
                "0,0 120,0",
                "Ringstrasse",
            )
        ],
    )
    _write_named_net(
        candidate_net,
        [("cand_ring", "ca", "cb", "highway.secondary", 118.0, "0,2 118,2", "Ringstrasse")],
    )
    audit = audit_reference_hierarchy(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / "hierarchy",
        prefix="before",
        match_distance_m=10.0,
        min_extra_edges=1,
    )

    repair = build_reference_hierarchy_type_repair_variant(
        candidate_net_file=candidate_net,
        reference_hierarchy_report=audit,
        output_dir=tmp_path / "repair",
        prefix="repair",
    )

    assert repair["reference_hierarchy_type_repair_count"] == 1
    plan = json.loads(Path(repair["reference_hierarchy_type_repair_plan_file"]).read_text(encoding="utf-8"))
    assert plan["repairs"][0]["reference_edge_type"] == "cycleway.track|highway.tertiary"
    assert plan["repairs"][0]["applied_edge_type"] == "highway.tertiary"
    variant_root = ET.parse(repair["reference_hierarchy_type_repair_variant_file"]).getroot()
    assert variant_root.find("edge[@id='cand_ring']").attrib["type"] == "highway.tertiary"


def test_reference_hierarchy_type_repair_variant_skips_ambiguous_mismatch(tmp_path: Path) -> None:
    candidate_net = tmp_path / "candidate.net.xml"
    _write_named_net(
        candidate_net,
        [("cand_b13", "ca", "cb", "highway.primary", 50.0, "0,0 50,0", "B 13")],
    )
    report = {
        "match_distance_m": 35.0,
        "candidate_cases": [
            {
                "candidate_edge_id": "cand_b13",
                "candidate_edge_name": "B 13",
                "candidate_edge_type": "highway.primary",
                "hierarchy_decision": "type_hierarchy_mismatch",
                "same_name_match_status": "no_same_name_reference",
                "same_name_reference_edge_type": "",
                "same_name_reference_distance_m": "",
            }
        ],
    }

    repair = build_reference_hierarchy_type_repair_variant(
        candidate_net_file=candidate_net,
        reference_hierarchy_report=report,
        output_dir=tmp_path / "repair",
        prefix="repair",
    )

    assert repair["status"] == "pass"
    assert repair["reference_hierarchy_type_repair_status"] == "not_needed"
    assert repair["reference_hierarchy_type_repair_count"] == 0
