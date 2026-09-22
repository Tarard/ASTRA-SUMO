from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


TINY_SUMO_NET = """<?xml version="1.0" encoding="UTF-8"?>
<net version="1.20">
    <location netOffset="0.00,0.00" convBoundary="0.00,0.00,120.00,90.00" origBoundary="0.00,0.00,120.00,90.00" projParameter="!"/>
    <edge id="north" from="n0" to="tls0" type="highway.primary">
        <lane id="north_0" index="0" speed="13.89" length="50.0" shape="60.0,90.0 60.0,50.0"/>
    </edge>
    <edge id="east" from="tls0" to="n1" type="highway.residential">
        <lane id="east_0" index="0" speed="8.33" length="60.0" shape="60.0,50.0 120.0,50.0"/>
    </edge>
    <edge id="walk" from="n2" to="n3" type="highway.footway">
        <lane id="walk_0" index="0" speed="1.4" length="100.0" shape="10.0,20.0 100.0,20.0"/>
    </edge>
    <junction id="n0" type="priority" x="60.0" y="90.0" incLanes="" intLanes=""/>
    <junction id="tls0" type="traffic_light" x="60.0" y="50.0" incLanes="north_0" intLanes=""/>
    <junction id="n1" type="priority" x="120.0" y="50.0" incLanes="east_0" intLanes=""/>
    <junction id="n2" type="priority" x="10.0" y="20.0" incLanes="" intLanes=""/>
    <junction id="n3" type="priority" x="100.0" y="20.0" incLanes="walk_0" intLanes=""/>
</net>
"""


def test_write_json_preserves_existing_file_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from torii_sumo.core import artifact_io, workflow_review_html

    destination = tmp_path / "review.json"
    original = '{"status": "old"}'
    destination.write_text(original, encoding="utf-8")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(artifact_io.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        workflow_review_html._write_json(destination, {"status": "new"})

    assert destination.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".torii-*.tmp"))


def test_artifact_hashes_cover_files_directories_and_missing_paths(tmp_path: Path) -> None:
    from torii_sumo.core.workflow_review_html import _artifact_hashes

    payload = tmp_path / "payload.txt"
    payload.write_text("stable artifact\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "nested.txt").write_text("nested artifact\n", encoding="utf-8")
    records, gate = _artifact_hashes(
        {
            "payload": payload,
            "bundle": bundle,
            "missing": tmp_path / "missing.bin",
            "manifest": tmp_path / "manifest.json",
        },
        base_dir=tmp_path,
        excluded_keys={"manifest"},
    )

    assert records["payload"]["status"] == "pass"
    assert records["payload"]["kind"] == "file"
    assert len(records["payload"]["sha256"]) == 64
    assert records["bundle"]["status"] == "pass"
    assert records["bundle"]["kind"] == "directory"
    assert records["missing"]["status"] == "missing"
    assert records["manifest"]["status"] == "excluded"
    assert gate["algorithm"] == "sha256"
    assert gate["status"] == "fail"
    assert gate["missing_artifacts"] == ["missing"]


def test_network_visualization_writes_nonempty_png(tmp_path: Path) -> None:
    from PIL import Image
    from torii_sumo.core.network_visualization import build_network_review_visuals

    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(TINY_SUMO_NET, encoding="utf-8")
    topology = {
        "suspicious_clusters": [
            {"cluster_id": "c1", "centroid_x": 60.0, "centroid_y": 50.0, "aggregation_decision": "join"}
        ]
    }

    report = build_network_review_visuals(
        output_dir=tmp_path / "visuals",
        prefix="review",
        net_file=net_file,
        topology_audit_report=topology,
    )

    assert report["status"] == "pass"
    assert report["visualization_status"] == "pass"
    assert Path(report["network_overview_png"]).is_file()
    assert Path(report["problem_overlay_png"]).is_file()
    assert report["cluster_zoom_pngs"][0]["cluster_id"] == "c1"
    assert Path(report["cluster_zoom_pngs"][0]["image_file"]).is_file()
    assert report["map_layers"]["bounds"]["min_x"] <= 10.0
    assert report["map_layers"]["bounds"]["max_x"] >= 120.0
    assert {edge["category"] for edge in report["map_layers"]["edges"]} == {"major", "vehicle", "soft"}
    assert report["map_layers"]["traffic_lights"] == [{"x": 60.0, "y": 50.0}]
    image = Image.open(report["network_overview_png"])
    assert image.size[0] >= 400
    assert image.size[1] >= 300


def test_network_visualization_skips_out_of_bounds_cluster_points(tmp_path: Path) -> None:
    from torii_sumo.core.network_visualization import build_network_review_visuals

    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(TINY_SUMO_NET, encoding="utf-8")
    topology = {
        "suspicious_clusters": [
            {"cluster_id": "bad", "centroid_x": 1.0e100, "centroid_y": 1.0e100},
            {"cluster_id": "valid", "centroid_x": 60.0, "centroid_y": 50.0},
        ]
    }

    report = build_network_review_visuals(
        output_dir=tmp_path / "visuals",
        prefix="review",
        net_file=net_file,
        topology_audit_report=topology,
    )

    assert report["status"] == "pass"
    assert Path(report["network_overview_png"]).is_file()
    assert Path(report["problem_overlay_png"]).is_file()
    assert [cluster["cluster_id"] for cluster in report["cluster_zoom_pngs"]] == ["valid"]
    assert Path(report["cluster_zoom_pngs"][0]["image_file"]).is_file()


def test_workflow_review_html_writes_visual_cockpit_and_sidecars(tmp_path: Path) -> None:
    from torii_sumo.core.workflow_review_html import build_workflow_review_html

    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(TINY_SUMO_NET, encoding="utf-8")
    best_variant = tmp_path / "teacher_guided_best.net.xml"
    best_variant.write_text(TINY_SUMO_NET, encoding="utf-8")
    teacher_delta = tmp_path / "junction_teacher_delta.json"
    teacher_delta.write_text('{"schema_version": 1}', encoding="utf-8")
    pattern_comparisons = tmp_path / "junction_pattern_comparisons.csv"
    pattern_comparisons.write_text("junction_id,mismatch_fields\nj,internal_function_counts\n", encoding="utf-8")
    pattern_templates = tmp_path / "junction_pattern_templates.json"
    pattern_templates.write_text('{"reference_templates": []}', encoding="utf-8")
    repair_queue = tmp_path / "teacher_guided_queue.json"
    repair_queue.write_text('{"repair_candidates": []}', encoding="utf-8")
    repair_queue_csv = tmp_path / "teacher_guided_queue.csv"
    repair_queue_csv.write_text("reference_id,junction_pattern_mismatch_fields\ncluster_j,has_tls\n", encoding="utf-8")
    repair_run = tmp_path / "teacher_guided_run.json"
    repair_run.write_text('{"status": "blocked"}', encoding="utf-8")
    tls_repair_variant = tmp_path / "tls_connection_repaired.net.xml"
    tls_repair_variant.write_text(TINY_SUMO_NET, encoding="utf-8")
    tls_repair_summary = tmp_path / "tls_connection_repair.json"
    tls_repair_summary.write_text('{"status": "pass"}', encoding="utf-8")
    tls_repair_delta = tmp_path / "tls_connection_repair_delta.json"
    tls_repair_delta.write_text('{"network_structural_delta_status": "fail"}', encoding="utf-8")
    tls_gap_mapping = tmp_path / "tls_gap_destination_mapping.json"
    tls_gap_mapping.write_text('{"status": "pass"}', encoding="utf-8")
    tls_repair_decision = tmp_path / "tls_repair_decision.json"
    tls_repair_decision.write_text('{"status": "blocked", "decision": "do_not_apply"}', encoding="utf-8")
    road_connectivity_best = tmp_path / "road_connectivity_best.net.xml"
    road_connectivity_best.write_text(TINY_SUMO_NET, encoding="utf-8")
    road_connectivity_run = tmp_path / "road_connectivity_run.json"
    road_connectivity_run.write_text('{"status": "pass"}', encoding="utf-8")

    report = build_workflow_review_html(
        output_dir=tmp_path / "review",
        prefix="workflow",
        title="SUMO Network Review",
        claim_status="construction-invalid",
        summary={
            "status": "fail",
            "claim_status": "construction-invalid",
            "net_file": str(net_file),
            "teacher_guided_repair_best_variant_file": str(best_variant),
            "reference_visual_detail_comparison_net_file": str(best_variant),
            "reference_join_junction_teacher_delta_file": str(teacher_delta),
            "reference_join_junction_pattern_comparisons_file": str(pattern_comparisons),
            "reference_join_junction_pattern_templates_file": str(pattern_templates),
            "reference_join_junction_pattern_comparison_status": "fail",
            "reference_join_junction_pattern_mismatch_count": 4,
            "reference_join_junction_pattern_comparison_sample_count": 5,
            "reference_join_post_teacher_junction_pattern_mismatch_count": 2,
            "reference_join_post_teacher_network_structural_missing_counts": {
                "connection_count": 8,
                "crossing_edge_count": 3,
            },
            "reference_join_post_teacher_network_structural_extra_counts": {
                "walkingarea_edge_count": 5,
            },
            "teacher_guided_repair_movement_gap_candidate_count": 1,
            "teacher_guided_repair_max_vehicle_movement_matrix_missing_count": 12,
            "teacher_guided_repair_missing_movement_plan_count": 2,
            "teacher_guided_repair_application_scope": "single_best_variant",
            "teacher_guided_repair_applied_candidate_count": 1,
            "teacher_guided_repair_unapplied_pass_candidate_count": 4,
            "teacher_guided_repair_top_movement_gaps": [
                {
                    "reference_id": "cluster_a_b",
                    "first_missing_teacher_movement": {
                        "from_edge_id": "cand_in",
                        "to_edge_id": "cand_out",
                        "fromLane": "1",
                        "toLane": "0",
                        "dir": "l",
                        "tl": "tlsA",
                        "linkIndex": "7",
                        "via": ":tlsA_7_0",
                    },
                }
            ],
            "teacher_guided_repair_queue_file": str(repair_queue),
            "teacher_guided_repair_queue_csv_file": str(repair_queue_csv),
            "teacher_guided_repair_run_report_file": str(repair_run),
            "teacher_guided_repair_promotion_gate_file": str(tmp_path / "teacher_guided_promotion_gate.json"),
            "reference_visual_detail_tls_connection_repair_variant_file": str(tls_repair_variant),
            "reference_visual_detail_tls_connection_repair_summary_file": str(tls_repair_summary),
            "reference_visual_detail_tls_connection_repair_reference_delta_file": str(tls_repair_delta),
            "tls_gap_destination_mapping_report_file": str(tls_gap_mapping),
            "tls_repair_decision_report_file": str(tls_repair_decision),
            "road_connectivity_replay_status": "pass",
            "road_connectivity_replay_gate_status": "pass",
            "road_connectivity_replay_sumo_load_status": "pass",
            "road_connectivity_replay_best_variant_file": str(road_connectivity_best),
            "road_connectivity_replay_run_report_file": str(road_connectivity_run),
            "road_connectivity_replay_gate_counts": {
                "owner_road_connectivity": {"pass": 1, "fail": 0, "failure_count": 0}
            },
            "teacher_guided_repair_template_contexts": [
                {
                    "teacher_pattern_key": "three_way|control=right_before_left",
                    "teacher_pattern_family": "three_way",
                    "teacher_pattern_template_count": 127,
                    "teacher_pattern_template_examples": ["cluster_template_1"],
                }
            ],
        },
        net_file=net_file,
        gate_status={"routeability_audit": "fail", "topology_audit": "blocked"},
        topology_audit_report={
            "topology_fragmentation_status": "needs_review",
            "suspicious_cluster_count": 1,
            "modal_decision_counts": {"review_required": 1},
            "modal_review_action_counts": {"review_vehicle_core_boundary": 1},
            "junction_aggregation_blocked_by_modal_count": 1,
            "suspicious_clusters": [
                {
                    "cluster_id": "c1",
                    "centroid_x": 60.0,
                    "centroid_y": 50.0,
                    "node_count": 3,
                    "node_ids": ["n0", "tls0", "n1"],
                    "cluster_radius_m": 16.0,
                    "aggregation_decision": "join",
                    "aggregation_confidence": "medium",
                    "modal_aggregation_decision": "review_required",
                    "modal_review_action": "review_vehicle_core_boundary",
                    "modal_reason": "vehicle core mixed with support or terminal infrastructure",
                    "google_maps_url": "https://www.google.com/maps/@48.765391,11.423800,40m",
                }
            ],
        },
        routeability_audit_report={"routeability_status": "teleport-failure", "arrived": 55, "vehicle_count": 100},
    )

    html = Path(report["workflow_review_html_file"]).read_text(encoding="utf-8")
    data_match = re.search(
        r'<script type="application/json" id="torii-review-data">(.+?)</script>',
        html,
        re.DOTALL,
    )
    assert data_match is not None
    review_data = json.loads(data_match.group(1))
    manifest_file = Path(report["review_manifest_file"])
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    workflow_json = json.loads(Path(report["workflow_report_file"]).read_text(encoding="utf-8"))
    manifest_dir = manifest_file.parent
    netedit_additional = Path(report["netedit_review_additional_file"])
    netedit_sumocfg = Path(report["netedit_review_sumocfg_file"])
    netedit_selection = Path(report["netedit_review_selection_files"][0])
    netedit_viewsettings = Path(report["netedit_review_viewsettings_files"][0])
    additional_root = ET.parse(netedit_additional).getroot()
    sumocfg_root = ET.parse(netedit_sumocfg).getroot()
    viewsettings_root = ET.parse(netedit_viewsettings).getroot()

    assert manifest["artifact_hash_gate"]["algorithm"] == "sha256"
    assert manifest["artifact_hashes"]["html_file"]["status"] == "pass"
    assert manifest["artifact_hashes"]["review_manifest_file"]["status"] == "excluded"
    assert report["artifact_hash_gate_status"] == manifest["artifact_hash_gate"]["status"]

    assert "Gate Dashboard" in html
    assert "Torii-SUMO" in html
    assert "Cleanup Review" in html
    assert 'class="torii-review-app"' in html
    assert ".torii-review-app { display: grid; grid-template-columns: 240px minmax(620px, 1fr) 420px; height: 100vh;" in html
    assert 'class="torii-sidebar"' in html
    assert 'class="torii-map-shell"' in html
    assert 'class="torii-review-panel"' in html
    assert 'id="review-panel-toggle"' in html
    assert 'id="map-viewport"' in html
    assert 'id="map-canvas"' in html
    assert 'class="network-svg"' in html
    assert 'class="map-edge edge-major"' in html
    assert '<circle class="junction-marker marker-green"' in html
    assert "zoomInMap" in html
    assert "toggleReviewPanel" in html
    assert "applySelectedJunctions" in html
    assert "Export review plan" in html
    assert "Netedit overlay" in html
    assert "workflow_netedit_review.sumocfg" in html
    assert "workflow_netedit_review_c1_selection.txt" in html
    assert "workflow_netedit_review_c1.view.xml" in html
    assert "teacher_guided_best.net.xml" in html
    assert "junction_teacher_delta.json" in html
    assert "junction_pattern_comparisons.csv" in html
    assert "junction_pattern_templates.json" in html
    assert "teacher_guided_queue.json" in html
    assert "tls_connection_repaired.net.xml" in html
    assert "tls_connection_repair.json" in html
    assert "tls_connection_repair_delta.json" in html
    assert "copyNeteditCommand" in html
    assert "Aggregate selected junctions" in html
    assert "Network Preview" in html
    assert "Problem Map" in html
    assert "Cluster Zooms" in html
    assert "c1" in html
    assert "google.com/maps" in html
    assert "Junction Aggregation Review" in html
    assert 'data-color-group="green"' in html
    assert 'data-select-color="green"' in html
    assert 'data-cluster-id="c1"' in html
    assert 'data-aggregate-checkbox="c1"' in html
    assert 'data-zoom-src=' in html
    assert 'id="zoom-modal"' in html
    assert "selectColorGroup" in html
    assert "toggleClusterSelection" in html
    assert "openZoom" in html
    assert 'id="cluster-filter"' not in html
    assert "localStorage" not in html
    assert "Review Queue" in html
    assert "Evidence Summary" in html
    assert "topology_fragmentation_status=needs_review" in html
    assert "reference_join_patterns" in html
    assert "reference_join_junction_pattern_comparison_status=fail" in html
    assert "reference_join_junction_pattern_mismatch_count=4" in html
    assert "reference_join_junction_pattern_comparison_sample_count=5" in html
    assert "reference_join_post_teacher" in html
    assert "reference_join_post_teacher_junction_pattern_mismatch_count=2" in html
    assert "missing: connection_count=8, crossing_edge_count=3" in html
    assert "extra: walkingarea_edge_count=5" in html
    assert "teacher_movement_plans" in html
    assert "teacher_guided_repair_movement_gap_candidate_count=1" in html
    assert "teacher_guided_repair_max_vehicle_movement_matrix_missing_count=12" in html
    assert "teacher_guided_repair_missing_movement_plan_count=2" in html
    assert "teacher_guided_repair_application_scope=single_best_variant" in html
    assert "teacher_guided_repair_applied_candidate_count=1" in html
    assert "teacher_guided_repair_unapplied_pass_candidate_count=4" in html
    assert "first_missing_teacher_movement" in html
    assert "from_edge_id=cand_in" in html
    assert "to_edge_id=cand_out" in html
    assert "linkIndex=7" in html
    assert "via=:tlsA_7_0" in html
    assert "road_connectivity_replay" in html
    assert "road_connectivity_replay_status=pass" in html
    assert "road_connectivity_replay_gate_status=pass" in html
    assert "road_connectivity_replay_sumo_load_status=pass" in html
    assert "owner_road_connectivity: pass=1, fail=0, failure_count=0" in html
    assert "road_connectivity_best.net.xml" in html
    assert "road_connectivity_run.json" in html
    assert "modal_decision_counts" in html
    assert "modal_review_action_counts" in html
    assert "junction_aggregation_blocked_by_modal_count" in html
    assert "teacher_templates" in html
    assert "family=three_way; count=127; key=three_way|control=right_before_left" in html
    assert "Modal: review_vehicle_core_boundary / review_required" in html
    assert "workflow_summary" not in html
    assert "<img" in html
    assert "file:///" not in html
    assert "visuals/workflow_network_overview.png" in html
    assert review_data["claim_status"] == "construction-invalid"
    assert review_data["navigation"][0]["label"] == "Junction Review"
    assert review_data["visualizations"]["network_overview_png"] == "visuals/workflow_network_overview.png"
    assert review_data["map_layers"]["bounds"]["min_x"] <= 10.0
    assert review_data["map_layers"]["traffic_lights"] == [{"x": 60.0, "y": 50.0}]
    assert review_data["summary_cards"]["uncertain_junctions"] == 1
    assert review_data["junctions"][0]["cluster_id"] == "c1"
    assert review_data["junctions"][0]["modal_review_action"] == "review_vehicle_core_boundary"
    assert review_data["junctions"][0]["image_file"].startswith("visuals/")
    assert review_data["netedit_review"]["additional_file"] == "workflow_netedit_review.add.xml"
    assert review_data["netedit_review"]["sumocfg_file"] == "workflow_netedit_review.sumocfg"
    assert review_data["netedit_review"]["box_overlay_count"] == 1
    assert review_data["netedit_review"]["edge_overlay_count"] == 0
    assert review_data["netedit_review"]["junction_overlay_count"] == 0
    assert review_data["netedit_review"]["selection_file_count"] == 1
    assert review_data["netedit_review"]["cluster_selection_files"][0]["selection_file"] == "workflow_netedit_review_c1_selection.txt"
    assert review_data["netedit_review"]["cluster_selection_files"][0]["viewsettings_file"] == "workflow_netedit_review_c1.view.xml"
    assert review_data["junctions"][0]["netedit_selection_file"] == "workflow_netedit_review_c1_selection.txt"
    assert review_data["junctions"][0]["netedit_viewsettings_file"] == "workflow_netedit_review_c1.view.xml"
    assert review_data["junctions"][0]["netedit_command"] == (
        'netedit --sumocfg-file "workflow_netedit_review.sumocfg" '
        '-g "workflow_netedit_review_c1.view.xml" '
        '--selection-file "workflow_netedit_review_c1_selection.txt"'
    )
    assert manifest["visualizations"]["network_overview_png"]
    assert manifest["artifacts"]["netedit_review_additional_file"] == "workflow_netedit_review.add.xml"
    assert manifest["artifacts"]["netedit_review_sumocfg_file"] == "workflow_netedit_review.sumocfg"
    assert manifest["artifacts"]["teacher_guided_repair_best_variant_file"] == "../teacher_guided_best.net.xml"
    assert manifest["artifacts"]["reference_visual_detail_comparison_net_file"] == "../teacher_guided_best.net.xml"
    assert manifest["artifacts"]["reference_visual_detail_tls_connection_repair_variant_file"] == "../tls_connection_repaired.net.xml"
    assert manifest["artifacts"]["reference_visual_detail_tls_connection_repair_summary_file"] == "../tls_connection_repair.json"
    assert manifest["artifacts"]["reference_visual_detail_tls_connection_repair_reference_delta_file"] == "../tls_connection_repair_delta.json"
    assert manifest["artifacts"]["tls_gap_destination_mapping_report_file"] == "../tls_gap_destination_mapping.json"
    assert manifest["artifacts"]["tls_repair_decision_report_file"] == "../tls_repair_decision.json"
    assert manifest["artifacts"]["reference_join_junction_teacher_delta_file"] == "../junction_teacher_delta.json"
    assert manifest["artifacts"]["reference_join_junction_pattern_comparisons_file"] == "../junction_pattern_comparisons.csv"
    assert manifest["artifacts"]["reference_join_junction_pattern_templates_file"] == "../junction_pattern_templates.json"
    assert manifest["artifacts"]["teacher_guided_repair_queue_file"] == "../teacher_guided_queue.json"
    assert manifest["artifacts"]["teacher_guided_repair_queue_csv_file"] == "../teacher_guided_queue.csv"
    assert manifest["artifacts"]["teacher_guided_repair_run_report_file"] == "../teacher_guided_run.json"
    assert manifest["artifacts"]["teacher_guided_repair_promotion_gate_file"] == "../teacher_guided_promotion_gate.json"
    assert manifest["artifacts"]["road_connectivity_replay_best_variant_file"] == "../road_connectivity_best.net.xml"
    assert manifest["artifacts"]["road_connectivity_replay_run_report_file"] == "../road_connectivity_run.json"
    assert manifest["netedit_review"]["netedit_command"] == 'netedit --sumocfg-file "workflow_netedit_review.sumocfg"'
    assert manifest["netedit_review"]["selection_file_count"] == 1
    assert manifest["netedit_review"]["viewsettings_file_count"] == 1
    assert manifest["review_app"]["map_layers"]["edges"]
    assert manifest["review_app"]["summary_cards"]["uncertain_junctions"] == 1
    assert manifest["review_app"]["junctions"][0]["cluster_id"] == "c1"
    assert report["workflow_review_net_file"] == str(net_file)
    assert manifest["visualizations"]["problem_overlay_png"]
    assert manifest["visualizations"]["cluster_zoom_pngs"][0]["cluster_id"] == "c1"
    assert (manifest_dir / manifest["visualizations"]["cluster_zoom_pngs"][0]["image_file"]).is_file()
    assert workflow_json["claim_status"] == "construction-invalid"
    assert netedit_additional.is_file()
    assert netedit_sumocfg.is_file()
    assert netedit_selection.read_text(encoding="utf-8").splitlines() == ["junction:n0", "junction:tls0", "junction:n1"]
    assert netedit_viewsettings.is_file()
    assert additional_root.tag == "additional"
    assert additional_root.findall("./poi") == []
    assert len(additional_root.findall("./poly")) == 1
    assert additional_root.find("./poly[@id='torii_c1_review_box']").attrib["shape"].startswith("44,34")
    assert viewsettings_root.tag == "viewsettings"
    assert viewsettings_root.find("./viewport").attrib["x"] == "60"
    assert viewsettings_root.find("./viewport").attrib["y"] == "50"
    assert sumocfg_root.find("./input/net-file").attrib["value"] == "../candidate.net.xml"
    assert sumocfg_root.find("./input/additional-files").attrib["value"] == "workflow_netedit_review.add.xml"


def test_workflow_review_html_marks_blocked_scoped_tls_scope(tmp_path: Path) -> None:
    from torii_sumo.core.workflow_review_html import build_workflow_review_html

    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(TINY_SUMO_NET, encoding="utf-8")
    summary = {
        "reference_join_audit": {
            "candidate_network_structural_summary": {
                "tl_logic_control_records": [{"tl_id": "tls0", "junction_ids": ["tls0"]}]
            },
            "tls_controller_alignment": {"controller_groups": []},
        },
        "teacher_guided_scoped_tls_cell_batch": {
            "cell_reports": [
                {
                    "status": "blocked",
                    "candidate_tl_id": "tls0",
                    "candidate_junction_ids": ["tls0"],
                    "direct_replay": {
                        "variant_reports": [
                            {
                                "scoped_tls_replay_coverage": {
                                    "status": "blocked",
                                    "ignored_off_scope_tls_connection_count": 3,
                                }
                            }
                        ]
                    },
                }
            ]
        },
    }

    report = build_workflow_review_html(
        output_dir=tmp_path / "review",
        prefix="workflow_review",
        summary=summary,
        net_file=net_file,
        topology_audit_report={"suspicious_clusters": []},
    )

    assert report["review_overlay_category_counts"] == {"tls_scoped_scope": 1}
    additional = ET.parse(report["netedit_review_additional_file"]).getroot()
    assert any(poly.attrib["name"].startswith("tls_scoped_scope_") for poly in additional.findall("poly"))


def test_workflow_review_html_additional_file_marks_tls_hierarchy_and_scope_locations(tmp_path: Path) -> None:
    from torii_sumo.core.workflow_review_html import build_workflow_review_html

    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(TINY_SUMO_NET, encoding="utf-8")
    summary = {
        "reference_join_audit": {
            "candidate_network_structural_summary": {
                "tl_logic_control_records": [
                    {"tl_id": "tls0", "junction_ids": ["tls0"]}
                ]
            },
            "tls_controller_alignment": {
                "high_confidence_movement_gap_queue": [
                    {
                        "candidate_tl_id": "tls0",
                        "missing_direction_counts": {"l": 1},
                    }
                ],
                "controller_groups": [],
            },
        },
        "reference_hierarchy_audit": {
            "candidate_cases": [
                {
                    "candidate_edge_id": "east",
                    "candidate_center_x": 90.0,
                    "candidate_center_y": 50.0,
                    "hierarchy_decision": "needs_review",
                    "reason": "hierarchy mismatch",
                }
            ]
        },
        "reference_scope_audit": {
            "prune_candidates": [
                {"candidate_edge_id": "north", "reason": "scope review"}
            ]
        },
    }

    report = build_workflow_review_html(
        output_dir=tmp_path / "review",
        prefix="workflow_review",
        summary=summary,
        net_file=net_file,
        topology_audit_report={"suspicious_clusters": []},
    )

    assert report["review_overlay_location_count"] == 3
    assert report["review_overlay_category_counts"] == {
        "hierarchy": 1,
        "scope": 1,
        "tls_movement_gap": 1,
    }
    additional = Path(report["netedit_review_additional_file"])
    root = ET.parse(additional).getroot()
    names = [poly.attrib["name"] for poly in root.findall("poly")]
    assert any(name.startswith("tls_gap_") for name in names)
    assert any(name.startswith("hierarchy_") for name in names)
    assert any(name.startswith("scope_") for name in names)


def test_workflow_review_html_additional_file_marks_context_join_review_locations(tmp_path: Path) -> None:
    from torii_sumo.core.workflow_review_html import build_workflow_review_html

    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(TINY_SUMO_NET, encoding="utf-8")
    report = build_workflow_review_html(
        output_dir=tmp_path / "review",
        prefix="context",
        net_file=net_file,
        summary={
            "context_join_review": [
                {
                    "location_id": "context_join_tls0",
                    "junction_id": "tls0",
                    "reason": "join evidence review",
                }
            ]
        },
        gate_status={"netconvert": "pass", "sumo_load": "pass"},
    )

    assert report["review_overlay_location_count"] == 1
    assert report["review_overlay_category_counts"] == {"context_join": 1}
    additional = ET.parse(report["netedit_review_additional_file"]).getroot()
    assert any(poly.attrib["name"].startswith("context_join_tls0 ") for poly in additional.findall("poly"))
    sumocfg_root = ET.parse(report["netedit_review_sumocfg_file"]).getroot()
    assert sumocfg_root.find("./input/additional-files").attrib["value"] == "context_netedit_review.add.xml"


def test_workflow_review_html_overlays_standard_nema_queue_and_decisions(tmp_path: Path) -> None:
    from torii_sumo.core.workflow_review_html import build_workflow_review_html

    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(TINY_SUMO_NET, encoding="utf-8")
    nema_report_file = tmp_path / "standard_nema.json"
    nema_overlay_file = tmp_path / "standard_nema.review.add.xml"
    nema_html_file = tmp_path / "standard_nema.review.html"
    nema_connection_mode_file = tmp_path / "standard_nema.connection_mode.json"
    nema_report_file.write_text('{"status":"pass"}', encoding="utf-8")
    nema_overlay_file.write_text("<additional/>", encoding="utf-8")
    nema_html_file.write_text("<!doctype html><title>NEMA</title>", encoding="utf-8")
    nema_connection_mode_file.write_text('{"status":"pass"}', encoding="utf-8")

    report = build_workflow_review_html(
        output_dir=tmp_path / "review",
        prefix="nema",
        net_file=net_file,
        summary={
            "standard_nema_report_file": str(nema_report_file),
            "standard_nema_connection_mode_report_file": str(nema_connection_mode_file),
            "standard_nema_review_overlay_file": str(nema_overlay_file),
            "standard_nema_review_html_file": str(nema_html_file),
            "standard_nema_scan": {
                "status": "pass",
                "nema_binding_status": "scan_complete",
                "candidates": [
                    {
                        "junction_id": "tls0",
                        "controller_id": "tls0",
                        "layout_type": "four_way",
                        "eligibility_status": "eligible",
                        "blockers": [],
                        "connection_mode_audit": {"status": "pass"},
                    },
                    {
                        "junction_id": "n0",
                        "controller_id": "joinedS_n0",
                        "layout_type": "three_way",
                        "eligibility_status": "review_required",
                        "blockers": ["controller_spans_other_junctions:n1"],
                        "connection_mode_audit": {
                            "status": "review_required",
                            "review_findings": [
                                "connection_mode:incoming_motorized_lane_without_connection:edge:1"
                            ],
                        },
                    },
                    {
                        "junction_id": "n1",
                        "controller_id": "joinedS_n1",
                        "layout_type": "three_way",
                        "eligibility_status": "review_required",
                        "blockers": [
                            "connection_mode:request_count_mismatch:2:1"
                        ],
                        "connection_mode_audit": {"status": "fail"},
                    },
                ],
            },
        },
        gate_status={"standard_nema_scan": "pass"},
    )

    assert report["review_overlay_location_count"] == 3
    assert report["review_overlay_category_counts"] == {
        "nema_candidate": 1,
        "nema_connection_mode_blocked": 1,
        "nema_connection_mode_review": 1,
    }
    additional = ET.parse(report["netedit_review_additional_file"]).getroot()
    names = [poly.attrib["name"] for poly in additional.findall("poly")]
    assert any(name.startswith("standard_nema_tls0 ") for name in names)
    assert any("Connection Mode audit blocked" in name for name in names)
    assert any("Connection Mode code audit requires" in name for name in names)
    decisions = json.loads(Path(report["review_decisions_file"]).read_text(encoding="utf-8"))
    assert {item["location_id"] for item in decisions["locations"]} == {
        "standard_nema_n0",
        "standard_nema_n1",
        "standard_nema_tls0",
    }
    manifest = json.loads(Path(report["review_manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["artifacts"]["standard_nema_report_file"] == "../standard_nema.json"
    assert manifest["artifacts"]["standard_nema_connection_mode_report_file"] == (
        "../standard_nema.connection_mode.json"
    )
    assert manifest["artifacts"]["standard_nema_review_overlay_file"] == "../standard_nema.review.add.xml"
    assert manifest["artifacts"]["standard_nema_review_html_file"] == "../standard_nema.review.html"


def test_workflow_review_html_overlays_code_connection_mode_findings(tmp_path: Path) -> None:
    from torii_sumo.core.workflow_review_html import build_workflow_review_html

    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text(TINY_SUMO_NET, encoding="utf-8")

    report = build_workflow_review_html(
        output_dir=tmp_path / "review",
        prefix="connection",
        net_file=net_file,
        summary={
            "connection_mode_audit": {
                "status": "review_required",
                "netedit_required_for_gate": False,
                "junctions": [
                    {
                        "junction_id": "tls0",
                        "status": "review_required",
                        "position": {"x": 60.0, "y": 50.0},
                        "controller_ids": ["tls0"],
                        "connection_mode_audit": {
                            "structural_failures": [],
                            "review_findings": [
                                "connection_mode:incoming_motorized_lane_without_connection:north:1"
                            ],
                        },
                        "tls_link_binding_audit": {
                            "structural_failures": [],
                            "review_findings": [],
                        },
                    }
                ],
            }
        },
        gate_status={"connection_mode_audit": "review_required"},
    )

    assert report["review_overlay_location_count"] == 1
    assert report["review_overlay_category_counts"] == {
        "connection_mode_review": 1
    }
    decisions = json.loads(Path(report["review_decisions_file"]).read_text(encoding="utf-8"))
    location = decisions["locations"][0]
    assert location["location_id"] == "connection_mode_tls0"
    assert "incoming_motorized_lane_without_connection" in location["reason"]
    workflow = json.loads(Path(report["workflow_report_file"]).read_text(encoding="utf-8"))
    assert workflow["connection_mode_audit"]["netedit_required_for_gate"] is False


def test_workflow_review_html_additional_file_marks_discarded_components(tmp_path: Path) -> None:
    from torii_sumo.core.workflow_review_html import build_workflow_review_html

    net_file = tmp_path / "core.net.xml"
    net_file.write_text(
        """<net>
  <location netOffset=\"0.0,0.0\" convBoundary=\"0.0,0.0,100.0,100.0\" origBoundary=\"0.0,0.0,100.0,100.0\" proj=\"!\"/>
  <junction id=\"core0\" x=\"10\" y=\"20\" type=\"priority\"/>
  <edge id=\"core_edge\" from=\"core0\" to=\"core0\"><lane id=\"core_edge_0\" index=\"0\" allow=\"passenger\" shape=\"10,20 10,20\"/></edge>
</net>""",
        encoding="utf-8",
    )
    review_file = tmp_path / "discarded_components_review.json"
    review_file.write_text(
        json.dumps(
            {
                "status": "pending_review",
                "records": [
                    {
                        "location_id": "disconnected_component_002",
                        "component_rank": 2,
                        "component_size": 4,
                        "centroid_x": 42.0,
                        "centroid_y": 24.0,
                        "edge_ids": ["discarded_edge"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_workflow_review_html(
        output_dir=tmp_path / "review",
        prefix="discarded",
        summary={
            "connected_core": {
                "discarded_components_review_file": str(review_file),
            }
        },
        net_file=net_file,
        connected_core_file=net_file,
        claim_status="diagnostic-demo",
        gate_status={},
    )

    assert report["review_overlay_category_counts"] == {"disconnected_component": 1}
    additional = Path(report["netedit_review_additional_file"])
    root = ET.parse(additional).getroot()
    overlay = root.find("./poly[@id='torii_disconnected_component_002_review_overlay']")
    assert overlay is not None
    assert "repair/reintegration" in overlay.attrib["name"]
    decisions = json.loads(Path(report["review_decisions_file"]).read_text(encoding="utf-8"))
    assert decisions["locations"][0]["decision"] == "pending"
