from __future__ import annotations

import hashlib
import json
from pathlib import Path

from astra_sumo.core.osm_workflow_record import write_osm_workflow_record


def _report(tmp_path: Path, **overrides) -> dict:
    net = tmp_path / "core.net.xml"
    net.write_text("<net/>", encoding="utf-8")
    audit = tmp_path / "boundary" / "report.json"
    audit.parent.mkdir()
    audit.write_text("{}", encoding="utf-8")
    osm = tmp_path / "area.osm.xml"
    osm.write_text("<osm/>", encoding="utf-8")
    report = {
        "status": "review_required", "claim_status": "diagnostic-demo", "net_file": str(net),
        "gate_status": {"sumo_gui": "blocked", "boundary_completeness": "review_required", "network_build": "pass",
                        "area_confirmation": "pass", "tls_aggregation": "blocked", "topology_audit": "blocked",
                        "routeability_audit": "pass", "tls_scoped_cell_batch": "skipped", "extra_gate": "pass"},
        "review_required_gates": [{"gate": "topology_audit", "reason": "suspicious_dense_junction_clusters_found"},
                                  {"gate": "boundary_completeness", "reason": "boundary_endpoints_need_review"}],
        "sumo_gui_status": "skipped", "tls_aggregation_status": "variant_created_for_review",
        "artifact_hashes": {"boundary_completeness_report_file": {"path": "../boundary/report.json", "sha256": "a" * 64}},
        "build": {"source_osm_file": str(osm), "filtered_osm_file": "",
                  "netconvert_signal_grouping": {
                      "stop_line_centres": {"centre_count": 3}, "demoted_signal_node_count": 7, "master_rebuild_count": 2,
                      "node_file": "groups.nod.xml", "node_file_sha256": "c" * 64, "lane_widening": {"lane_count": 5},
                      "split_junction_joins": {"join_count": 4, "dropped_joins": ["cluster_1_2"], "deleted_turn_count": 1}}},
        "warnings": ["w"],
    }
    report.update(overrides)
    return report


def test_record_lists_stages_in_workflow_order_with_reasons_and_the_first_incomplete_stage(tmp_path: Path) -> None:
    summary = write_osm_workflow_record(_report(tmp_path), output_dir=tmp_path)

    manifest = json.loads((tmp_path / "workflow.manifest.json").read_text(encoding="utf-8"))
    rows = {row["stage"]: row for row in manifest["stages"]}
    assert [row["stage"] for row in manifest["stages"]] == [
        "area_confirmation", "network_build", "tls_aggregation", "topology_audit", "routeability_audit",
        "boundary_completeness", "tls_scoped_cell_batch", "sumo_gui", "extra_gate"]
    # A gate the workflow lists for review keeps that reason; an unrequested launch and a skipped step are not blockers.
    assert (rows["topology_audit"]["status"], rows["topology_audit"]["reason"]) == (
        "review_required", "suspicious_dense_junction_clusters_found")
    assert (rows["tls_aggregation"]["status"], rows["tls_aggregation"]["reason"]) == (
        "review_required", "variant_created_for_review")
    assert rows["sumo_gui"] == {"stage": "sumo_gui", "status": "not_applicable", "reason": "launch_not_requested"}
    assert rows["tls_scoped_cell_batch"]["status"] == "not_applicable"
    assert rows["boundary_completeness"]["artifact"] == {"path": str((tmp_path / "boundary" / "report.json").resolve()),
                                                         "sha256": "a" * 64}
    assert manifest["status"] == "review_required" and manifest["first_incomplete_stage"] == "tls_aggregation"
    assert manifest["next_action"] == summary["next_action"] == "review_tls_aggregation: variant_created_for_review"
    assert manifest["stage_counts"] == {"pass": 4, "review_required": 3, "blocked": 0, "not_applicable": 2}
    assert manifest["inputs"] == [{"role": "source_osm", "path": str((tmp_path / "area.osm.xml").resolve()),
                                   "sha256": hashlib.sha256(b"<osm/>").hexdigest(), "bytes": 6}]
    assert manifest["changes"]["joined_junctions"] == 4 and manifest["changes"]["deleted_turns"] == 1
    assert manifest["changes"]["dropped_joins"] == 1 and manifest["changes"]["signal_centres"] == 3

    handoff = json.loads((tmp_path / "network-handoff.json").read_text(encoding="utf-8"))
    assert handoff["construction_decision"] == "review_required"
    assert handoff["networks"]["passenger"]["sha256"] == hashlib.sha256(b"<net/>").hexdigest()
    assert handoff["workflow"]["sha256"] == hashlib.sha256((tmp_path / "workflow.manifest.json").read_bytes()).hexdigest()


def test_record_names_a_blocked_stage_and_a_complete_run(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    write_osm_workflow_record(_report(blocked, status="blocked", review_required_gates=[],
                                      gate_status={"network_build": "pass", "connectivity": "fail"}), output_dir=blocked)
    manifest = json.loads((blocked / "workflow.manifest.json").read_text(encoding="utf-8"))
    assert manifest["first_incomplete_stage"] == "connectivity" and manifest["next_action"] == "return_to_connectivity: fail"

    done = tmp_path / "done"
    done.mkdir()
    summary = write_osm_workflow_record(_report(done, status="pass", review_required_gates=[],
                                                gate_status={"network_build": "pass", "sumo_gui": "blocked"}), output_dir=done)
    assert summary["first_incomplete_stage"] is None and summary["next_action"] == "hand_the_network_on"
