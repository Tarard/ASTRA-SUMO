from pathlib import Path

from torii_sumo.core.reference_scope import (
    audit_reference_scope,
    build_reference_bbox_variant,
    build_scope_pruning_variant,
)


def _write_net(path: Path, edge_specs: list[tuple[str, str, str, str, float, str]]) -> None:
    junction_ids = sorted({spec[1] for spec in edge_specs} | {spec[2] for spec in edge_specs})
    junction_xml = "\n".join(
        f'  <junction id="{junction_id}" x="{index * 10}.0" y="0.0" type="priority"/>'
        for index, junction_id in enumerate(junction_ids)
    )
    edge_xml = "\n".join(
        f'''  <edge id="{edge_id}" from="{from_node}" to="{to_node}" type="{edge_type}">
    <lane id="{edge_id}_0" index="0" speed="13.9" length="{length:.1f}" allow="{allow}"/>
  </edge>'''
        for edge_id, from_node, to_node, edge_type, length, allow in edge_specs
    )
    path.write_text(f"<net>\n{junction_xml}\n{edge_xml}\n</net>\n", encoding="utf-8")


def test_reference_scope_audit_flags_overrepresented_short_dead_end_detail_edges(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    _write_net(
        reference_net,
        [
            ("ref_main", "a", "b", "highway.residential", 120.0, "passenger"),
            ("ref_service", "b", "c", "highway.service", 45.0, "passenger"),
            ("ref_foot", "c", "d", "highway.footway", 35.0, "pedestrian"),
        ],
    )
    _write_net(
        candidate_net,
        [
            ("cand_main", "a", "b", "highway.residential", 120.0, "passenger"),
            ("cand_service_keep", "b", "c", "highway.service", 110.0, "passenger"),
            ("cand_service_short_dead_end", "c", "s1", "highway.service", 25.0, "passenger"),
            ("cand_foot_keep", "c", "d", "highway.footway", 90.0, "pedestrian"),
            ("cand_foot_short_dead_end", "d", "f1", "highway.footway", 20.0, "pedestrian"),
            ("cand_path_absent", "d", "p1", "highway.path", 18.0, "pedestrian"),
        ],
    )

    report = audit_reference_scope(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / "scope",
        prefix="demo",
        overrepresentation_ratio=1.5,
        min_extra_edges=1,
        max_prune_edge_length_m=40.0,
    )

    assert report["status"] == "blocked"
    assert report["reference_scope_status"] == "needs_pruning_review"
    assert report["candidate_edge_count"] == 6
    assert report["reference_edge_count"] == 3
    type_decisions = {row["edge_type"]: row["scope_decision"] for row in report["type_comparisons"]}
    assert type_decisions["highway.service"] == "overrepresented_in_candidate"
    assert type_decisions["highway.path"] == "absent_in_reference"
    prune_ids = {row["edge_id"] for row in report["prune_candidates"]}
    assert prune_ids == {"cand_service_short_dead_end", "cand_foot_short_dead_end", "cand_path_absent"}
    assert Path(report["type_comparison_file"]).is_file()
    assert Path(report["prune_candidates_file"]).is_file()


def test_scope_pruning_variant_removes_only_audit_prune_candidates(tmp_path: Path) -> None:
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_command_runner(command, **_kwargs):
        calls.append(command)
        remove_file = Path(command[command.index("--remove-edges.input-file") + 1])
        assert remove_file.read_text(encoding="utf-8").splitlines() == ["edge_a", "edge_b"]
        output_file = Path(command[command.index("--output-file") + 1])
        output_file.write_text(
            """<net>
  <junction id="j1" type="priority"/>
  <edge id="kept" from="j1" to="j1" type="highway.residential">
    <lane id="kept_0" index="0" length="20.0"/>
  </edge>
</net>""",
            encoding="utf-8",
        )
        return {"status": "pass", "returncode": 0, "stdout": "Success.", "stderr": "", "error": ""}

    report = build_scope_pruning_variant(
        net_file=net_file,
        reference_scope_report={
            "status": "blocked",
            "reference_scope_status": "needs_pruning_review",
            "prune_candidates": [
                {"edge_id": "edge_a", "prune_decision": "prune_candidate"},
                {"edge_id": "edge_b", "prune_decision": "prune_candidate"},
                {"edge_id": "edge_keep", "prune_decision": "keep"},
            ],
        },
        output_dir=tmp_path / "pruning",
        prefix="demo",
        command_runner=fake_command_runner,
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "blocked"
    assert report["scope_pruning_status"] == "variant_created_for_review"
    assert report["scope_pruning_removed_edge_count"] == 2
    assert report["scope_pruned_edge_count"] == 1
    assert Path(report["scope_pruning_variant_file"]).is_file()
    assert "--remove-edges.input-file" in calls[0]


def test_reference_scope_protects_short_detail_edges_with_tls_connections(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    _write_net(
        reference_net,
        [("ref_main", "a", "b", "highway.residential", 120.0, "passenger")],
    )
    _write_net(
        candidate_net,
        [
            ("cand_main", "a", "b", "highway.residential", 120.0, "passenger"),
            ("cand_short", "b", "leaf", "highway.service", 20.0, "passenger"),
        ],
    )
    candidate_net.write_text(
        candidate_net.read_text(encoding="utf-8").replace(
            "</net>",
            '<connection from="cand_short" to="cand_main" fromLane="0" toLane="0" tl="tls" linkIndex="0"/>\n</net>',
        ),
        encoding="utf-8",
    )

    report = audit_reference_scope(
        reference_net_file=reference_net,
        candidate_net_file=candidate_net,
        output_dir=tmp_path / "scope_protected",
        prefix="protected",
        min_extra_edges=1,
        max_prune_edge_length_m=40.0,
    )

    assert report["protected_controlled_edge_ids"] == ["cand_short"]
    assert "cand_short" not in {row["edge_id"] for row in report["prune_candidates"]}
    assert any("protected" in warning for warning in report["warnings"])


def test_reference_bbox_variant_projects_bbox_and_keeps_source_unchanged(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference.net.xml"
    reference_net.write_text(
        """<net>
  <location netOffset="-672401.23,-5399632.08" projParameter="+proj=utm +zone=32 +ellps=WGS84 +units=m"/>
  <junction id="a" x="4947.6" y="3455.8" type="priority"/>
  <edge id="road" from="a" to="b" type="highway.primary">
    <lane id="road_0" index="0" length="10.0" shape="4947.6,3455.8 4957.6,3455.8"/>
  </edge>
  <junction id="b" x="4957.6" y="3455.8" type="dead_end"/>
</net>""",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_command_runner(command, **_kwargs):
        calls.append(command)
        output_file = Path(command[command.index("--output-file") + 1])
        output_file.write_text(reference_net.read_text(encoding="utf-8"), encoding="utf-8")
        return {"status": "pass", "returncode": 0, "stdout": "Success.", "stderr": "", "error": ""}

    report = build_reference_bbox_variant(
        reference_net_file=reference_net,
        bbox="11.413800,48.755391,11.433800,48.775391",
        output_dir=tmp_path / "bbox_scope",
        prefix="teacher",
        command_runner=fake_command_runner,
    )

    assert report["status"] == "pass"
    assert report["reference_bbox_scope_status"] == "variant_created"
    assert report["reference_projection_zone"] == 32
    assert report["reference_xy_boundary"]["min_x"] < report["reference_xy_boundary"]["max_x"]
    assert report["reference_xy_boundary"]["min_y"] < report["reference_xy_boundary"]["max_y"]
    assert calls[0][calls[0].index("--keep-edges.in-boundary") + 1] == "4935.864,3442.882,6476.162,5712.616"
    assert "--keep-edges.postload" in calls[0]
    assert reference_net.read_text(encoding="utf-8").startswith("<net>")
    assert Path(report["variant_file"]).is_file()


def test_reference_bbox_variant_blocks_without_projection_metadata(tmp_path: Path) -> None:
    reference_net = tmp_path / "reference.net.xml"
    reference_net.write_text("<net/>\n", encoding="utf-8")

    report = build_reference_bbox_variant(
        reference_net_file=reference_net,
        bbox="11.4,48.7,11.5,48.8",
        output_dir=tmp_path / "bbox_scope",
    )

    assert report["status"] == "blocked"
    assert report["reference_bbox_scope_status"] == "blocked"
    assert "projection metadata" in report["error"]
