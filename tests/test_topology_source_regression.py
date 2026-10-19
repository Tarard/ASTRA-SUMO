from copy import deepcopy

from astra_sumo.core.candidate_contracts import validate_topology_evidence
from astra_sumo.core.topology_audit import audit_topology_fragmentation


def test_valid_topology_review_is_not_an_evidence_failure(tmp_path):
    network = tmp_path / "dense.net.xml"
    network.write_text('<net><location netOffset="0,0" projParameter="!"/>'
        '<junction id="a" x="0" y="0" type="priority"/><junction id="b" x="5" y="0" type="priority"/>'
        '<junction id="c" x="10" y="0" type="priority"/>'
        '<edge id="ab" from="a" to="b"><lane id="ab_0" length="5" shape="0,0 5,0"/></edge>'
        '<edge id="bc" from="b" to="c"><lane id="bc_0" length="5" shape="5,0 10,0"/></edge></net>', encoding="utf-8")
    report = audit_topology_fragmentation(net_file=network, output_dir=tmp_path / "audit")
    assert report["topology_fragmentation_status"] == "needs_review"
    result = validate_topology_evidence(report, candidate_net_file=network)
    assert result["status"] == "review_required"
    assert not result["errors"]
    forged = {**report, "status": "pass", "topology_fragmentation_status": "pass"}
    assert validate_topology_evidence(forged, candidate_net_file=network)["status"] == "blocked"
    changed = deepcopy(report)
    changed["suspicious_clusters"][0]["node_ids"] = ["other"]
    assert validate_topology_evidence(changed, candidate_net_file=network)["status"] == "blocked"


def _report(nodes=("a", "b", "c")):
    return {"cluster_radius_m": 30.0, "min_cluster_nodes": 3, "topology_osm_context_sha256": "",
        "suspicious_cluster_count": 1, "suspicious_clusters": [{"cluster_id": "C001", "node_ids": list(nodes),
        "approach_count": 4, "internal_edge_overlap_pair_count": 0, "physical_intersection_shape": "cross",
        "modal_primary_role": "vehicle_core", "aggregation_decision": "needs_map_review", "risk_flags": []}]}


def test_source_findings_are_not_new_regressions():
    from astra_sumo.core.topology_audit import compare_topology_source_findings
    source = _report()
    candidate = deepcopy(source)
    candidate["suspicious_clusters"][0]["cluster_id"] = "C170"
    result = compare_topology_source_findings(candidate, source)
    assert result["status"] == "pass"
    assert result["inherited_finding_count"] == 1
    assert result["whole_network_status"] == "review_required"
    assert result["new_or_changed_findings"] == []


def test_equal_counts_do_not_hide_a_different_or_worsened_cluster():
    from astra_sumo.core.topology_audit import compare_topology_source_findings
    source = _report()
    assert compare_topology_source_findings(_report(("a", "b", "d")), source)["status"] == "review_required"
    changed = deepcopy(source)
    changed["suspicious_clusters"][0]["internal_edge_overlap_pair_count"] = 2
    assert compare_topology_source_findings(changed, source)["status"] == "review_required"


def test_declared_node_absorption_maps_source_membership():
    from astra_sumo.core.topology_audit import compare_topology_source_findings
    source = _report()
    candidate = _report(("joined", "c"))
    assert compare_topology_source_findings(candidate, source, node_aliases={"a": "joined", "b": "joined"})["status"] == "pass"


def test_different_audit_parameters_are_not_comparable():
    from astra_sumo.core.topology_audit import compare_topology_source_findings
    source, candidate = _report(), _report()
    candidate["cluster_radius_m"] = 50
    assert compare_topology_source_findings(candidate, source)["status"] == "review_required"


def test_changed_modal_or_corridor_decisions_require_review():
    from astra_sumo.core.topology_audit import compare_topology_source_findings
    for name in ("modal_aggregation_decision", "modal_review_action", "corridor_decision"):
        source, candidate = _report(), _report()
        candidate["suspicious_clusters"][0][name] = "reject"
        assert compare_topology_source_findings(candidate, source)["status"] == "review_required"


def test_missing_or_ambiguous_source_findings_require_review():
    from astra_sumo.core.topology_audit import compare_topology_source_findings
    source, candidate = _report(), _report()
    candidate["suspicious_clusters"] = [None]
    assert compare_topology_source_findings(candidate, source)["status"] == "review_required"
    candidate = _report()
    candidate["suspicious_clusters"].append(deepcopy(candidate["suspicious_clusters"][0]))
    candidate["suspicious_cluster_count"] = 2
    assert compare_topology_source_findings(candidate, source)["status"] == "review_required"
