from pathlib import Path

from torii_sumo.core.topology_audit import audit_topology_fragmentation, compare_topology_canonical_cells


def _canonical_report(cells: list[dict[str, object]]) -> dict[str, object]:
    return {
        "topology_osm_context_sha256": "same-osm-context",
        "topology_canonical_cell_records": cells,
    }


def _canonical_cell(
    cluster_id: str,
    *,
    nodes: list[str],
    lat: float = 48.0,
    lon: float = 11.0,
    role: str = "vehicle_core",
) -> dict[str, object]:
    return {
        "cluster_id": cluster_id,
        "identity_node_ids": nodes,
        "corridor_signatures": ["way:main|way:side"],
        "centroid_lat": lat,
        "centroid_lon": lon,
        "physical_intersection_shape": "cross",
        "approach_count": 4,
        "modal_primary_role": role,
        "traffic_light_node_count": 0,
    }


def test_canonical_topology_allows_extra_sumocut_subdivision_inside_one_cell() -> None:
    candidate = _canonical_report([_canonical_cell("C001", nodes=["1", "2", "3"])])
    reference = _canonical_report([_canonical_cell("R001", nodes=["1", "2"])])

    result = compare_topology_canonical_cells(candidate, reference)

    assert result["status"] == "pass"
    assert result["matched_cell_count"] == 1


def test_canonical_topology_blocks_unmatched_physical_cell() -> None:
    candidate = _canonical_report(
        [
            _canonical_cell("C001", nodes=["1", "2"]),
            _canonical_cell("C002", nodes=["9", "10"], lat=48.001),
        ]
    )
    reference = _canonical_report([_canonical_cell("R001", nodes=["1", "2"])])

    result = compare_topology_canonical_cells(candidate, reference)

    assert result["status"] == "blocked"
    assert result["unmatched_candidate_cells"] == ["C002"]


def test_canonical_topology_blocks_modal_role_mismatch() -> None:
    candidate = _canonical_report([_canonical_cell("C001", nodes=["1", "2"])])
    reference = _canonical_report(
        [_canonical_cell("R001", nodes=["1", "2"], role="rail_or_modal")]
    )

    result = compare_topology_canonical_cells(candidate, reference)

    assert result["status"] == "blocked"
    assert result["modal_mismatches"]


def test_topology_audit_flags_compact_connection_cell(tmp_path: Path) -> None:
    net_file = tmp_path / "cell.net.xml"
    net_file.write_text(
        """<net>
  <edge id="west_in" from="w" to="a" type="highway.primary"><lane id="west_in_0" index="0" allow="passenger" length="20" shape="-20,0 0,0"/></edge>
  <edge id="east_out" from="b" to="e" type="highway.primary"><lane id="east_out_0" index="0" allow="passenger" length="20" shape="6,0 26,0"/></edge>
  <edge id="north_in" from="n" to="c" type="highway.secondary"><lane id="north_in_0" index="0" allow="passenger" length="20" shape="3,20 3,4"/></edge>
  <edge id="south_out" from="a" to="s" type="highway.secondary"><lane id="south_out_0" index="0" allow="passenger" length="20" shape="0,0 0,-20"/></edge>
  <edge id="ab" from="a" to="b" type="highway.primary"><lane id="ab_0" index="0" allow="passenger" length="6" shape="0,0 6,0"/></edge>
  <edge id="bc" from="b" to="c" type="highway.secondary"><lane id="bc_0" index="0" allow="passenger" length="5" shape="6,0 3,4"/></edge>
  <junction id="w" x="-20" y="0" type="priority"/>
  <junction id="a" x="0" y="0" type="traffic_light"/>
  <junction id="b" x="6" y="0" type="priority"/>
  <junction id="c" x="3" y="4" type="priority"/>
  <junction id="e" x="26" y="0" type="priority"/>
  <junction id="n" x="3" y="20" type="priority"/>
  <junction id="s" x="0" y="-20" type="priority"/>
</net>
""",
        encoding="utf-8",
    )

    report = audit_topology_fragmentation(
        net_file=net_file,
        output_dir=tmp_path / "audit",
        cluster_radius_m=8,
        min_cluster_nodes=3,
    )

    assert report["topology_connection_cell_candidate_count"] == 1
    cell = report["topology_connection_cell_candidates"][0]
    assert set(cell["node_ids"]) == {"a", "b", "c"}
    assert cell["external_vehicle_approach_count"] == 4
    assert cell["connection_cell_decision"] == "needs_review"


def test_topology_audit_rejects_long_connector_between_close_intersections(tmp_path: Path) -> None:
    net_file = tmp_path / "pair.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a_in" from="a0" to="a" type="highway.primary"><lane id="a_in_0" index="0" allow="passenger" length="20" shape="-20,0 0,0"/></edge>
  <edge id="a_to_b" from="a" to="b" type="highway.primary"><lane id="a_to_b_0" index="0" allow="passenger" length="45" shape="0,0 45,0"/></edge>
  <edge id="b_out" from="b" to="b1" type="highway.primary"><lane id="b_out_0" index="0" allow="passenger" length="20" shape="45,0 65,0"/></edge>
  <junction id="a0" x="-20" y="0" type="priority"/>
  <junction id="a" x="0" y="0" type="traffic_light"/>
  <junction id="b" x="45" y="0" type="traffic_light"/>
  <junction id="b1" x="65" y="0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )

    report = audit_topology_fragmentation(
        net_file=net_file,
        output_dir=tmp_path / "audit",
        cluster_radius_m=50,
        min_cluster_nodes=2,
    )

    assert report["topology_connection_cell_candidate_count"] == 0


def test_topology_audit_shortens_long_artifact_paths(tmp_path: Path) -> None:
    net_file = tmp_path / "single.net.xml"
    net_file.write_text(
        "<net><junction id=\"j\" x=\"0\" y=\"0\" type=\"priority\"/></net>",
        encoding="utf-8",
    )
    output_dir = tmp_path / ("output_" + "x" * 100)
    report = audit_topology_fragmentation(
        net_file=net_file,
        output_dir=output_dir,
        prefix="p" * 140,
    )

    assert Path(report["clusters_file"]).is_file()
    assert Path(report["topology_connection_cell_candidates_file"]).is_file()
    assert Path(report["report_file"]).is_file()
    assert Path(report["clusters_file"]).name.startswith("p_")
