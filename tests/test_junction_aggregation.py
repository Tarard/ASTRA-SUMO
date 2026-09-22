import xml.etree.ElementTree as ET
from pathlib import Path

from torii_sumo.core.junction_aggregation import (
    _aggregation_candidates,
    audit_join_collapse_residuals,
    audit_junction_aggregation_preservation,
    build_junction_aggregation_variant,
)
from torii_sumo.core.junction_join_definition import (
    build_junction_join_definition,
    netconvert_join_patch_args,
)
from torii_sumo.core.osm_workflow import _junction_aggregation_summary


def _command_path(command: list[str], option: str, cwd: Path) -> Path:
    path = Path(command[command.index(option) + 1])
    return path if path.is_absolute() else cwd / path


def test_corridor_rejected_topology_clusters_do_not_become_join_candidates() -> None:
    topology_report = {
        "clusters_file": "clusters.csv",
        "suspicious_clusters": [
            {
                "cluster_id": "C001",
                "aggregation_decision": "needs_map_review",
                "aggregation_confidence": "low",
                "corridor_decision": "reject",
                "corridor_reason": "large group spans three or more named intersection cells",
                "node_ids": ["a", "b", "c"],
            },
            {
                "cluster_id": "C002",
                "aggregation_decision": "join",
                "aggregation_confidence": "medium",
                "corridor_decision": "allow",
                "node_ids": ["d", "e", "f"],
            },
        ],
    }

    candidates = _aggregation_candidates(
        topology_audit_report=topology_report,
        reference_join_audit_report=None,
    )
    summary = _junction_aggregation_summary(topology_report)

    assert [candidate["candidate_id"] for candidate in candidates] == ["C002"]
    assert summary["junction_aggregation_candidate_count"] == 1
    assert summary["junction_aggregation_join_candidate_count"] == 1
    assert summary["junction_aggregation_blocked_by_corridor_count"] == 1


def test_overlapping_reference_supported_group_uses_reference_core_nodes() -> None:
    overlap_report = {
        "overlapping_junction_groups": [
            {
                "group_id": "OJ009",
                "node_ids": ["281967823", "305519232", "extra_footway", "extra_dead_end"],
                "reference_join_status": "reference_join_supported",
                "reference_join_ids": [
                    "cluster_281967823_305519232_7009179649_7626856596_7626856598_7626856599"
                ],
                "recommendation": "reference_join_supported",
            },
            {
                "group_id": "OJ010",
                "node_ids": ["needs_a", "needs_b", "needs_c"],
                "recommendation": "same_physical_intersection_review",
            },
        ]
    }

    candidates = _aggregation_candidates(
        topology_audit_report=None,
        reference_join_audit_report=None,
        overlapping_junction_audit_report=overlap_report,
    )

    assert candidates == [
        {
            "source": "overlapping_junction_audit",
            "candidate_id": "OJ009",
            "decision": "needs_map_review",
            "confidence": "teacher_prior",
            "node_ids": "281967823;305519232;7009179649;7626856596;7626856598;7626856599",
            "reason": "reference join cluster is teacher evidence and cannot authorize this join",
            "google_maps_url": "",
        }
    ]


def test_reference_join_audit_uses_matched_candidate_node_ids() -> None:
    reference_join_report = {
        "matched_cases": [
            {
                "reference_id": "cluster_a_b_c",
                "matched_candidate_node_ids": ["a", "b", "c"],
                "learned_rule": "tum_like_join_candidate",
            }
        ]
    }

    candidates = _aggregation_candidates(
        topology_audit_report=None,
        reference_join_audit_report=reference_join_report,
    )

    assert candidates[0]["decision"] == "needs_map_review"
    assert candidates[0]["confidence"] == "teacher_prior"
    assert candidates[0]["node_ids"] == "a;b;c"


def test_reference_source_join_uses_matched_reference_source_nodes_before_spatial_cluster() -> None:
    reference_join_report = {
        "matched_cases": [
            {
                "reference_id": "cluster_a_b",
                "learned_rule_basis": "reference_source_nodes",
                "matched_reference_source_node_ids": ["a", "b"],
                "matched_candidate_node_ids": ["a", "b", "nearby_c", "nearby_d"],
                "learned_rule": "tum_like_join_candidate",
            }
        ]
    }

    candidates = _aggregation_candidates(
        topology_audit_report=None,
        reference_join_audit_report=reference_join_report,
    )

    assert candidates[0]["node_ids"] == "a;b"


def test_duplicate_overlapping_join_candidates_are_collapsed() -> None:
    overlap_report = {
        "overlapping_junction_groups": [
            {
                "group_id": "OJ001",
                "reference_join_status": "reference_join_supported",
                "reference_join_ids": ["cluster_a_b_c"],
            },
            {
                "group_id": "OJ002",
                "reference_join_status": "reference_join_supported",
                "reference_join_ids": ["cluster_a_b_c"],
            },
            {
                "group_id": "OJ003",
                "reference_join_status": "reference_join_supported",
                "reference_join_ids": ["cluster_c_d"],
            },
        ]
    }

    candidates = _aggregation_candidates(
        topology_audit_report=None,
        reference_join_audit_report=None,
        overlapping_junction_audit_report=overlap_report,
    )

    assert [candidate["candidate_id"] for candidate in candidates] == ["OJ001", "OJ003"]


def test_overlapping_human_confirmed_group_uses_join_node_ids() -> None:
    overlap_report = {
        "overlapping_junction_groups": [
            {
                "group_id": "OJ011",
                "node_ids": ["core_a", "core_b", "sidewalk_node"],
                "join_node_ids": ["core_a", "core_b"],
                "manual_correction_status": "confirmed",
            }
        ]
    }

    candidates = _aggregation_candidates(
        topology_audit_report=None,
        reference_join_audit_report=None,
        overlapping_junction_audit_report=overlap_report,
    )

    assert candidates[0]["decision"] == "join"
    assert candidates[0]["confidence"] == "map_confirmed"
    assert candidates[0]["node_ids"] == "core_a;core_b"


def test_junction_join_definition_writes_sumo_plainxml_join_patch(tmp_path) -> None:
    candidates = [
        {
            "source": "topology_audit",
            "candidate_id": "C001",
            "decision": "join",
            "confidence": "map_confirmed",
            "node_ids": ["n1", "n2", "n3"],
            "reason": "Google Maps default map confirms one physical intersection",
            "google_maps_url": "https://www.google.com/maps/@48.0,11.0,50m",
        },
        {
            "source": "topology_audit",
            "candidate_id": "C002",
            "decision": "needs_map_review",
            "node_ids": "r1;r2",
            "reason": "traffic-signal semantics require map review",
        },
        {
            "source": "topology_audit",
            "candidate_id": "C003",
            "decision": "do_not_join",
            "node_ids": ["ramp_a", "ramp_b"],
            "reason": "parallel ramp pair should not become one city junction",
        },
    ]

    report = build_junction_join_definition(candidates, output_dir=tmp_path, prefix="demo")

    assert report["status"] == "pass"
    assert report["sumo_join_semantics"] == "plain_nodes_join_patch"
    assert report["explicit_join_count"] == 1
    assert report["join_exclude_count"] == 2
    assert report["needs_map_review_count"] == 1

    nodes_patch = tmp_path / "demo_junction_join.nod.xml"
    assert report["nodes_patch_file"] == str(nodes_patch)
    assert netconvert_join_patch_args(nodes_patch) == ["--node-files", str(nodes_patch)]

    root = ET.parse(nodes_patch).getroot()
    assert [element.attrib["nodes"] for element in root.findall("join")] == ["n1 n2 n3"]
    assert [element.attrib["nodes"] for element in root.findall("joinExclude")] == [
        "r1 r2",
        "ramp_a ramp_b",
    ]


def test_junction_join_definition_skips_invalid_single_node_groups(tmp_path) -> None:
    report = build_junction_join_definition(
        [
            {
                "source": "topology_audit",
                "candidate_id": "C001",
                "decision": "join",
                "node_ids": "n1",
                "reason": "not enough nodes to join",
            }
        ],
        output_dir=tmp_path,
        prefix="demo",
    )

    assert report["status"] == "pass"
    assert report["explicit_join_count"] == 0
    assert report["invalid_candidate_count"] == 1
    assert "C001" in report["invalid_candidates"][0]["candidate_id"]


def test_junction_aggregation_preserves_reference_confirmed_modal_nodes(tmp_path) -> None:
    net_file = tmp_path / "source.net.xml"
    net_file.write_text(
        """<net>
    <edge id="veh_a" from="core_a" to="core_b" type="highway.residential">
        <lane id="veh_a_0" index="0" allow="passenger" speed="13.9" length="20"/>
    </edge>
    <edge id="veh_b" from="outside" to="core_a" type="highway.unclassified">
        <lane id="veh_b_0" index="0" allow="passenger" speed="13.9" length="20"/>
    </edge>
    <edge id="foot" from="foot_node" to="outside_foot" type="highway.footway">
        <lane id="foot_0" index="0" allow="pedestrian" speed="1.4" length="8"/>
    </edge>
    <edge id="bike" from="cycle_node" to="outside_bike" type="highway.cycleway">
        <lane id="bike_0" index="0" allow="bicycle" speed="5.0" length="8"/>
    </edge>
    <edge id="service" from="service_node" to="outside_service" type="highway.service">
        <lane id="service_0" index="0" allow="passenger" speed="5.0" length="8"/>
    </edge>
</net>""",
        encoding="utf-8",
    )

    def fake_command(command, cwd, timeout_seconds):
        patch_file = _command_path(command, "--node-files", cwd)
        variant_file = _command_path(command, "--output-file", cwd)
        variant_file.write_text(
            '<net><junction id="cluster_core_a_core_b_cycle_node_foot_node_#1more" x="0" y="0"/></net>',
            encoding="utf-8",
        )
        root = ET.parse(patch_file).getroot()
        assert [element.attrib["nodes"] for element in root.findall("join")] == [
            "core_a core_b foot_node cycle_node service_node"
        ]
        return {"status": "pass", "stdout": "", "stderr": "", "command": command}

    report = build_junction_aggregation_variant(
        net_file=net_file,
        output_dir=tmp_path,
        prefix="demo",
        reference_join_audit_report={
            "matched_cases": [
                {
                    "reference_id": "cluster_core_a_core_b_foot_node_cycle_node_service_node",
                    "candidate_node_ids": ["core_a", "core_b", "foot_node", "cycle_node", "service_node"],
                        "match_reason": "reference_matched",
                        "transfer_gate_status": "pass",
                        "target_evidence_status": "pass",
                }
            ]
        },
        command_runner=fake_command,
    )

    assert report["status"] == "pass"


def test_junction_aggregation_prunes_only_internal_short_modal_support_edges(tmp_path) -> None:
    net_file = tmp_path / "source.net.xml"
    net_file.write_text(
        """<net>
    <edge id="veh_a" from="core_a" to="core_b" type="highway.residential">
        <lane id="veh_a_0" index="0" allow="passenger" speed="13.9" length="20"/>
    </edge>
    <edge id="veh_short_keep" from="core_a" to="outside_vehicle" type="highway.residential">
        <lane id="veh_short_keep_0" index="0" allow="passenger" speed="13.9" length="5"/>
    </edge>
    <edge id="foot_internal_prune" from="core_a" to="core_b" type="highway.footway">
        <lane id="foot_internal_prune_0" index="0" allow="pedestrian" speed="1.4" length="6"/>
    </edge>
    <edge id="bike_internal_prune" from="core_b" to="core_a" type="highway.cycleway">
        <lane id="bike_internal_prune_0" index="0" allow="bicycle" speed="5.0" length="7"/>
    </edge>
    <edge id="foot_external_keep" from="core_a" to="outside_foot" type="highway.footway">
        <lane id="foot_external_keep_0" index="0" allow="pedestrian" speed="1.4" length="6"/>
    </edge>
    <edge id="bike_external_keep" from="core_b" to="outside_bike" type="highway.cycleway">
        <lane id="bike_external_keep_0" index="0" allow="bicycle" speed="5.0" length="7"/>
    </edge>
    <edge id="service_external_keep" from="core_b" to="outside_service" type="highway.service">
        <lane id="service_external_keep_0" index="0" allow="passenger" speed="5.0" length="6"/>
    </edge>
    <edge id="service_long_keep" from="core_b" to="outside_service_long" type="highway.service">
        <lane id="service_long_keep_0" index="0" allow="passenger" speed="5.0" length="35"/>
    </edge>
</net>""",
        encoding="utf-8",
    )

    def fake_command(command, cwd, timeout_seconds):
        assert "--remove-edges.input-file" not in command
        assert command[command.index("--remove-edges.explicit") + 1] == "bike_internal_prune,foot_internal_prune"
        remove_file = tmp_path / "demo_modal_support_remove_edges.txt"
        assert remove_file.read_text(encoding="utf-8").splitlines() == [
            "bike_internal_prune",
            "foot_internal_prune",
        ]
        variant_file = _command_path(command, "--output-file", cwd)
        variant_file.write_text('<net><junction id="cluster_core_a_core_b" x="0" y="0"/></net>', encoding="utf-8")
        return {"status": "pass", "stdout": "", "stderr": "", "command": command}

    report = build_junction_aggregation_variant(
        net_file=net_file,
        output_dir=tmp_path,
        prefix="demo",
        reference_join_audit_report={
            "matched_cases": [
                {
                    "reference_id": "cluster_core_a_core_b",
                    "candidate_node_ids": ["core_a", "core_b"],
                        "match_reason": "reference_matched",
                        "transfer_gate_status": "pass",
                        "target_evidence_status": "pass",
                }
            ]
        },
        command_runner=fake_command,
    )

    assert report["status"] == "pass"
    assert report["junction_aggregation_removed_modal_support_edge_count"] == 2


def test_junction_aggregation_variant_reports_failed_collapse_audit(tmp_path) -> None:
    net_file = tmp_path / "source.net.xml"
    net_file.write_text(
        """<net>
    <edge id="veh_a" from="core_a" to="core_b" type="highway.residential">
        <lane id="veh_a_0" index="0" allow="passenger" speed="13.9" length="20"/>
    </edge>
    <edge id="veh_b" from="outside" to="core_a" type="highway.residential">
        <lane id="veh_b_0" index="0" allow="passenger" speed="13.9" length="20"/>
    </edge>
</net>""",
        encoding="utf-8",
    )

    def fake_command(command, cwd, timeout_seconds):
        variant_file = _command_path(command, "--output-file", cwd)
        variant_file.write_text(
            """<net>
  <edge id="veh_a" from="core_a" to="core_b" type="highway.residential">
    <lane id="veh_a_0" index="0" length="20"/>
  </edge>
  <junction id="core_a" x="0" y="0" type="traffic_light"/>
  <junction id="core_b" x="1" y="0" type="traffic_light"/>
</net>""",
            encoding="utf-8",
        )
        return {"status": "pass", "stdout": "", "stderr": "", "command": command}

    report = build_junction_aggregation_variant(
        net_file=net_file,
        output_dir=tmp_path,
        prefix="demo",
        reference_join_audit_report={
            "matched_cases": [
                {
                    "reference_id": "cluster_core_a_core_b",
                    "candidate_node_ids": ["core_a", "core_b"],
                        "match_reason": "reference_matched",
                        "transfer_gate_status": "pass",
                        "target_evidence_status": "pass",
                }
            ]
        },
        command_runner=fake_command,
    )

    assert report["status"] == "fail"
    assert report["junction_aggregation_collapse_audit_status"] == "needs_cleanup"
    assert Path(report["junction_aggregation_collapse_audit_file"]).is_file()


def test_junction_aggregation_variant_fails_when_planned_join_is_missing_from_output(tmp_path) -> None:
    net_file = tmp_path / "source.net.xml"
    net_file.write_text(
        """<net>
  <edge id="ab" from="a" to="b" type="highway.residential">
    <lane id="ab_0" index="0" length="5"/>
  </edge>
  <junction id="a" x="0" y="0" type="traffic_light"/>
  <junction id="b" x="1" y="0" type="traffic_light"/>
</net>""",
        encoding="utf-8",
    )

    def fake_command(command, cwd, timeout_seconds):
        variant_file = _command_path(command, "--output-file", cwd)
        variant_file.write_text("<net/>", encoding="utf-8")
        return {"status": "pass", "stdout": "", "stderr": "", "command": command}

    report = build_junction_aggregation_variant(
        net_file=net_file,
        output_dir=tmp_path,
        prefix="demo",
        reference_join_audit_report={
            "matched_cases": [
                {
                    "reference_id": "cluster_a_b",
                    "candidate_node_ids": ["a", "b"],
                        "match_reason": "reference_matched",
                        "transfer_gate_status": "pass",
                        "target_evidence_status": "pass",
                }
            ]
        },
        command_runner=fake_command,
    )

    assert report["status"] == "fail"
    assert report["junction_aggregation_join_output_audit_status"] == "missing_joined_junctions"
    assert report["junction_aggregation_missing_joined_junction_count"] == 1


def test_junction_aggregation_variant_reports_normal_edge_and_connection_loss(tmp_path) -> None:
    net_file = tmp_path / "source.net.xml"
    net_file.write_text(
        """<net>
  <edge id="approach_in" from="outside" to="core_a" type="highway.primary">
    <lane id="approach_in_0" index="0" allow="passenger" length="20"/>
  </edge>
  <edge id="service_keep" from="core_b" to="service" type="highway.service">
    <lane id="service_keep_0" index="0" allow="passenger" length="20"/>
  </edge>
  <edge id="plain_removed" from="core_a" to="core_b" type="highway.residential">
    <lane id="plain_removed_0" index="0" allow="passenger bicycle" length="20"/>
  </edge>
  <edge id="outside_stable" from="x" to="outside" type="highway.primary">
    <lane id="outside_stable_0" index="0" allow="passenger" length="20"/>
  </edge>
  <junction id="outside" type="priority" incLanes="outside_stable_0" intLanes=""/>
  <junction id="core_a" type="priority" incLanes="approach_in_0" intLanes=""/>
  <junction id="core_b" type="priority" incLanes="plain_removed_0" intLanes=""/>
  <junction id="service" type="priority" incLanes="service_keep_0" intLanes=""/>
  <junction id="x" type="priority" incLanes="" intLanes=""/>
  <connection from="approach_in" to="plain_removed" fromLane="0" toLane="0"/>
  <connection from="outside_stable" to="approach_in" fromLane="0" toLane="0"/>
</net>""",
        encoding="utf-8",
    )

    def fake_command(command, cwd, timeout_seconds):
        variant_file = _command_path(command, "--output-file", cwd)
        variant_file.write_text(
            """<net>
  <edge id="approach_in" from="outside" to="cluster_core_a_core_b" type="highway.primary">
    <lane id="approach_in_0" index="0" allow="passenger" length="20"/>
  </edge>
  <edge id="outside_stable" from="x" to="outside" type="highway.primary">
    <lane id="outside_stable_0" index="0" allow="passenger" length="20"/>
  </edge>
  <junction id="outside" type="priority" incLanes="outside_stable_0" intLanes=""/>
  <junction id="cluster_core_a_core_b" type="priority" incLanes="approach_in_0" intLanes=""/>
  <junction id="x" type="priority" incLanes="" intLanes=""/>
</net>""",
            encoding="utf-8",
        )
        return {"status": "pass", "stdout": "", "stderr": "", "command": command}

    report = build_junction_aggregation_variant(
        net_file=net_file,
        output_dir=tmp_path,
        prefix="demo",
        reference_join_audit_report={
            "matched_cases": [
                {
                    "reference_id": "cluster_core_a_core_b",
                    "candidate_node_ids": ["core_a", "core_b"],
                        "match_reason": "reference_matched",
                        "transfer_gate_status": "pass",
                        "target_evidence_status": "pass",
                }
            ]
        },
        command_runner=fake_command,
    )

    assert report["junction_aggregation_preservation_status"] == "review"
    assert report["junction_aggregation_removed_normal_edge_count"] == 2
    assert report["junction_aggregation_removed_normal_edge_type_counts"] == {
        "highway.residential": 1,
        "highway.service": 1,
    }
    assert report["junction_aggregation_removed_normal_edge_mode_counts"] == {
        "bicycle": 1,
        "passenger": 2,
    }
    assert report["junction_aggregation_lost_shared_connection_count"] == 1
    assert report["junction_aggregation_new_dangling_shared_normal_edge_count"] == 1
    assert Path(report["junction_aggregation_preservation_audit_file"]).is_file()


def test_junction_aggregation_preservation_accepts_join_absorption_and_via_renumbering(tmp_path) -> None:
    source = tmp_path / "source.net.xml"
    variant = tmp_path / "variant.net.xml"
    source.write_text(
        """<net>
  <edge id="in" from="outside" to="a"><lane id="in_0" index="0" length="20"/></edge>
  <edge id="seam" from="a" to="b"><lane id="seam_0" index="0" length="0.2"/></edge>
  <edge id="out" from="b" to="outside2"><lane id="out_0" index="0" length="20"/></edge>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":a_0_0"/>
</net>""",
        encoding="utf-8",
    )
    variant.write_text(
        """<net>
  <edge id="in" from="outside" to="cluster_a_b"><lane id="in_0" index="0" length="20"/></edge>
  <edge id="out" from="cluster_a_b" to="outside2"><lane id="out_0" index="0" length="20"/></edge>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":cluster_a_b_0_0"/>
</net>""",
        encoding="utf-8",
    )

    report = audit_junction_aggregation_preservation(source, variant, join_groups=(("a", "b"),))

    assert report["schema"] == "torii.junction-aggregation-preservation/v1"
    assert report["status"] == "pass"
    assert report["source_net_file"] == str(source.resolve())
    assert report["variant_net_file"] == str(variant.resolve())
    assert report["source_sha256"]
    assert report["variant_sha256"]
    assert report["absorbed_join_edge_ids"] == ["seam"]
    assert report["unexpected_removed_normal_edge_count"] == 0
    assert report["lost_shared_connection_count"] == 0
    assert report["boundary_movement_preservation"]["status"] == "pass"


def test_junction_aggregation_preservation_flags_new_join_boundary_movement(tmp_path) -> None:
    source = tmp_path / "source.net.xml"
    variant = tmp_path / "variant.net.xml"
    source.write_text(
        """<net>
  <edge id="in" from="outside" to="a"><lane id="in_0" index="0" length="20"/></edge>
  <edge id="seam" from="a" to="b"><lane id="seam_0" index="0" length="0.2"/></edge>
  <edge id="out" from="b" to="outside2"><lane id="out_0" index="0" length="20"/></edge>
  <edge id="extra" from="b" to="outside3"><lane id="extra_0" index="0" length="20"/></edge>
  <connection from="in" to="seam" fromLane="0" toLane="0"/>
  <connection from="seam" to="out" fromLane="0" toLane="0"/>
</net>""",
        encoding="utf-8",
    )
    variant.write_text(
        """<net>
  <edge id="in" from="outside" to="cluster_a_b"><lane id="in_0" index="0" length="20"/></edge>
  <edge id="out" from="cluster_a_b" to="outside2"><lane id="out_0" index="0" length="20"/></edge>
  <edge id="extra" from="cluster_a_b" to="outside3"><lane id="extra_0" index="0" length="20"/></edge>
  <connection from="in" to="out" fromLane="0" toLane="0"/>
  <connection from="in" to="extra" fromLane="0" toLane="0"/>
</net>""",
        encoding="utf-8",
    )

    report = audit_junction_aggregation_preservation(source, variant, join_groups=(("a", "b"),))

    assert report["status"] == "review"
    boundary = report["boundary_movement_preservation"]
    assert boundary["lost_boundary_movement_count"] == 0
    assert boundary["added_boundary_movement_count"] == 1
    assert boundary["groups"][0]["added_boundary_movements"] == ["in|0|extra|0"]


def test_join_collapse_audit_flags_residual_nodes_edges_and_connections(tmp_path) -> None:
    net_file = tmp_path / "not_collapsed.net.xml"
    net_file.write_text(
        """<net>
  <edge id="ab" from="a" to="b" type="highway.residential">
    <lane id="ab_0" index="0" length="0.2"/>
  </edge>
  <edge id=":a_0" function="internal" from="a" to="b">
    <lane id=":a_0_0" index="0" length="0.2"/>
  </edge>
  <junction id="a" x="0" y="0" type="traffic_light"/>
  <junction id="b" x="1" y="0" type="traffic_light"/>
  <connection from="ab" to="ab" via=":a_0_0"/>
</net>""",
        encoding="utf-8",
    )

    report = audit_join_collapse_residuals(net_file, [["a", "b"]])

    assert report["status"] == "needs_cleanup"
    assert report["residual_group_count"] == 1
    assert report["groups"][0]["remaining_core_node_ids"] == ["a", "b"]
    assert report["groups"][0]["residual_plain_edge_ids"] == ["ab"]
    assert report["groups"][0]["residual_internal_edge_ids"] == [":a_0"]
    assert report["groups"][0]["residual_connection_via_ids"] == [":a_0_0"]


def test_join_collapse_audit_passes_when_cluster_replaces_core(tmp_path) -> None:
    net_file = tmp_path / "collapsed.net.xml"
    net_file.write_text(
        """<net>
  <edge id="north" from="n" to="cluster_a_b" type="highway.residential"/>
  <edge id="east" from="cluster_a_b" to="e" type="highway.residential"/>
  <edge id=":cluster_a_b_0" function="internal" from="cluster_a_b" to="cluster_a_b">
    <lane id=":cluster_a_b_0_0" index="0" length="5"/>
  </edge>
  <junction id="cluster_a_b" x="0.5" y="0" type="traffic_light"/>
  <junction id="n" x="0" y="10" type="priority"/>
  <junction id="e" x="10" y="0" type="priority"/>
  <connection from="north" to="east" via=":cluster_a_b_0_0"/>
</net>""",
        encoding="utf-8",
    )

    report = audit_join_collapse_residuals(net_file, [["a", "b"]])

    assert report["status"] == "pass"
    assert report["residual_group_count"] == 0


def test_unconfirmed_topology_join_candidate_stays_in_map_review(tmp_path) -> None:
    report = build_junction_join_definition(
        [
            {
                "source": "topology_audit",
                "candidate_id": "C001",
                "decision": "join",
                "confidence": "medium",
                "node_ids": ["n1", "n2", "n3"],
                "reason": "approach-axis geometry indicates a cross intersection",
            }
        ],
        output_dir=tmp_path,
        prefix="demo",
    )

    assert report["explicit_join_count"] == 0
    assert report["join_exclude_count"] == 1
    assert report["needs_map_review_count"] == 1
    assert report["records"][0]["decision"] == "needs_map_review"


def test_unconfirmed_text_cannot_be_misread_as_a_confirmed_join(tmp_path) -> None:
    report = build_junction_join_definition(
        [
            {
                "source": "teacher_probe",
                "candidate_id": "C-unconfirmed",
                "decision": "join",
                "confidence": "unconfirmed_reference_match",
                "review_status": "not_confirmed",
                "node_ids": ["n1", "n2"],
            }
        ],
        output_dir=tmp_path,
        prefix="strict",
    )

    assert report["explicit_join_count"] == 0
    assert report["join_exclude_count"] == 1
    assert report["records"][0]["decision"] == "needs_map_review"


def test_reference_matched_teacher_prior_cannot_authorize_a_join(tmp_path) -> None:
    report = build_junction_join_definition(
        [
            {
                "source": "reference_join_audit",
                "candidate_id": "teacher-core",
                "decision": "join",
                "confidence": "reference_matched",
                "node_ids": ["n1", "n2"],
            }
        ],
        output_dir=tmp_path,
        prefix="teacher-prior",
    )

    assert report["explicit_join_count"] == 0
    assert report["records"][0]["decision"] == "needs_map_review"


def test_junction_aggregation_variant_runs_netconvert_with_cwd_relative_outputs(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path("candidate.net.xml").write_text("<net/>", encoding="utf-8")

    def fake_command(command, **kwargs):
        assert kwargs["cwd"] == Path("out").resolve()
        assert Path(command[2]).is_absolute()
        assert command[command.index("--node-files") + 1] == "junction_aggregation_junction_join.nod.xml"
        output_file = _command_path(command, "--output-file", kwargs["cwd"])
        assert command[command.index("--output-file") + 1] == "junction_aggregation_junction_aggregated.net.xml"
        output_file.write_text('<net><junction id="cluster_a_b" x="0" y="0"/></net>', encoding="utf-8")
        return {"status": "pass", "returncode": 0}

    report = build_junction_aggregation_variant(
        net_file=Path("candidate.net.xml"),
        output_dir=Path("out"),
        topology_audit_report={
            "suspicious_clusters": [
                {
                    "cluster_id": "C001",
                    "aggregation_decision": "join",
                    "aggregation_confidence": "map_confirmed",
                    "node_ids": ["a", "b"],
                }
            ]
        },
        command_runner=fake_command,
    )

    assert report["status"] == "pass"
