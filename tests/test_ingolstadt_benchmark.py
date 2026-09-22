from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmarks" / "ingolstadt_reference_matched"
EXAMPLE = ROOT / "examples" / "02_one_prompt_osm_network"


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_ingolstadt_reference_matched_benchmark_files_are_present() -> None:
    expected = {
        "cases.yaml",
        "baseline_raw_netconvert.json",
        "torii_vehicle_core.json",
        "torii_reference_visual_detail.json",
        "promotion_trace.json",
        "review_load.json",
        "semantic_counts.json",
        "semantic_gaps.json",
        "summary_table.json",
        "teacher_probe_signatures.json",
    }

    assert expected <= {path.name for path in BENCHMARK.iterdir()}

    case_text = (BENCHMARK / "cases.yaml").read_text(encoding="utf-8")
    assert "11.413800,48.755391,11.433800,48.775391" in case_text
    assert "network_profile: reference_matched" in case_text
    assert "claim_status: diagnostic-demo" in case_text


def test_ingolstadt_teacher_probe_signatures_are_frozen() -> None:
    signatures = _read_json(BENCHMARK / "teacher_probe_signatures.json")
    probes = {probe["junction_id"]: probe for probe in signatures["probe_cases"]}

    assert signatures["schema_version"] == 1
    assert signatures["teacher_net_file"] == (
        "examples/02_one_prompt_osm_network/networks/"
        "tum_ingolstadt_center_reference.net.xml"
    )
    assert set(probes) == {"1433119620", "267380207", "267517510", "1031988198"}
    assert probes["267517510"]["pattern_family"] == "four_way"
    assert probes["267517510"]["control_type"] == "traffic_light"
    assert probes["267517510"]["movement_signature_count"] == 10


def test_ingolstadt_summary_table_matches_committed_example_counts() -> None:
    example_summary = _read_json(EXAMPLE / "validation" / "run_2026-06-24_5_5" / "summary.json")
    summary_table = _read_json(BENCHMARK / "summary_table.json")

    by_stage = {stage["stage_id"]: stage for stage in summary_table["stages"]}

    assert list(by_stage) == [
        "raw_netconvert",
        "torii_scoped_build",
        "tls_aggregated",
        "junction_candidate_review",
        "teacher_guided_repair",
        "final_review_html",
    ]
    assert by_stage["raw_netconvert"]["counts"] == example_summary["counts"]["torii_raw_vehicle"]
    assert by_stage["torii_scoped_build"]["counts"] == example_summary["counts"]["torii_connected_core"]
    assert by_stage["tls_aggregated"]["counts"] == example_summary["counts"][
        "torii_tls_aggregated_reference_visual_detail"
    ]
    assert by_stage["final_review_html"]["review_artifact"] == (
        "examples/02_one_prompt_osm_network/validation/"
        "run_2026-06-25_review_html/ingolstadt_workflow_review.html"
    )
    for artifact in summary_table["source_artifacts"]:
        assert (ROOT / artifact).exists()


def test_ingolstadt_review_load_tracks_current_review_cockpit_queue() -> None:
    review_load = _read_json(BENCHMARK / "review_load.json")
    review_dir = EXAMPLE / "validation" / "run_2026-06-25_review_html"
    cluster_png_count = len(list((review_dir / "visuals").glob("*cluster_*.png")))

    assert review_load["claim_status"] == "diagnostic-demo"
    assert review_load["dense_junction_cluster_review_item_count"] == cluster_png_count
    assert review_load["blocked_gates"] == [
        "tls_reality_audit",
        "tls_aggregation",
        "topology_audit",
        "reference_join_aggregation",
        "sumo_gui",
    ]
    assert (ROOT / review_load["review_html_file"]).exists()


def test_ingolstadt_promotion_trace_does_not_promote_blocked_review_stages() -> None:
    promotion_trace = _read_json(BENCHMARK / "promotion_trace.json")

    assert promotion_trace["claim_status"] == "construction-invalid"
    decisions = {stage["stage_id"]: stage["promotion_decision"] for stage in promotion_trace["stages"]}
    assert decisions["routeability_audit"] == "pass"
    assert decisions["tls_aggregation"] == "blocked_review_required"
    assert decisions["junction_candidate_review"] == "blocked_review_required"
    assert decisions["teacher_guided_repair"] == "not_materialized_current_example"


def test_ingolstadt_promotion_trace_blocks_on_semantic_gaps() -> None:
    semantic_gaps = _read_json(BENCHMARK / "semantic_gaps.json")
    promotion_trace = _read_json(BENCHMARK / "promotion_trace.json")
    summary_table = _read_json(BENCHMARK / "summary_table.json")

    semantic_gate = next(
        stage for stage in promotion_trace["stages"] if stage["stage_id"] == "semantic_gap_gate"
    )

    assert "benchmarks/ingolstadt_reference_matched/semantic_gaps.json" in summary_table["source_artifacts"]
    assert semantic_gate["promotion_decision"] == semantic_gaps["promotion_decision"]
    assert semantic_gate["after_quality"]["tls_semantic_delta"] == {
        gap["gap_id"]: gap["status"] for gap in semantic_gaps["gaps"]
    }
    assert semantic_gate["delta_quality"] == {
        gap["gap_id"]: gap["next_gate"] for gap in semantic_gaps["gaps"]
    }


def test_ingolstadt_semantic_counts_match_committed_networks() -> None:
    semantic_counts = _read_json(BENCHMARK / "semantic_counts.json")

    for network_id, expected in semantic_counts["networks"].items():
        net_file = ROOT / expected["network_file"]
        assert expected == _network_semantic_counts(net_file)

    assert semantic_counts["networks"]["tum_reference"]["crossing_edge_count"] > 0
    assert semantic_counts["networks"]["tum_reference"]["walkingarea_edge_count"] > 0
    assert semantic_counts["networks"]["torii_reference_visual_detail"]["crossing_edge_count"] == 0
    assert semantic_counts["networks"]["torii_reference_visual_detail"]["walkingarea_edge_count"] == 0


def test_ingolstadt_semantic_gaps_are_derived_from_counts() -> None:
    semantic_counts = _read_json(BENCHMARK / "semantic_counts.json")["networks"]
    semantic_gaps = _read_json(BENCHMARK / "semantic_gaps.json")
    tum = semantic_counts["tum_reference"]
    torii = semantic_counts["torii_reference_visual_detail"]

    by_gap = {gap["gap_id"]: gap for gap in semantic_gaps["gaps"]}

    assert semantic_gaps["claim_status"] == "construction-invalid"
    assert semantic_gaps["candidate_network_id"] == "torii_reference_visual_detail"
    assert by_gap["walkingarea_crossing"]["status"] == "blocked"
    assert by_gap["walkingarea_crossing"]["tum"] == {
        "crossing_edge_count": tum["crossing_edge_count"],
        "walkingarea_edge_count": tum["walkingarea_edge_count"],
    }
    assert by_gap["walkingarea_crossing"]["candidate"] == {
        "crossing_edge_count": torii["crossing_edge_count"],
        "walkingarea_edge_count": torii["walkingarea_edge_count"],
    }
    assert by_gap["controlled_tls_connections"]["delta"] == {
        "controlled_connection_count": torii["controlled_connection_count"] - tum["controlled_connection_count"],
        "tlLogic_phase_count": torii["tlLogic_phase_count"] - tum["tlLogic_phase_count"],
    }
    assert by_gap["turnaround_dir_t"]["status"] == "blocked"
    assert by_gap["turnaround_dir_t"]["delta"] == {
        "dir_t": torii["connection_dir_counts"]["t"] - tum["connection_dir_counts"]["t"],
    }
    assert semantic_gaps["promotion_decision"] == "blocked_semantic_gaps"


def _network_semantic_counts(path: Path) -> dict:
    root = ET.parse(path).getroot()
    edges = root.findall("edge")
    junctions = root.findall("junction")
    connections = root.findall("connection")
    tl_logics = root.findall("tlLogic")
    edge_functions = Counter(edge.attrib.get("function") or "plain" for edge in edges)
    junction_types = Counter(junction.attrib.get("type") or "unknown" for junction in junctions)
    dir_counts = Counter(connection.attrib.get("dir") or "blank" for connection in connections)
    controlled_connections = [
        connection for connection in connections if connection.attrib.get("tl") or connection.attrib.get("linkIndex")
    ]
    top_external_connections = [
        connection
        for connection in connections
        if not connection.attrib.get("from", "").startswith(":") and not connection.attrib.get("to", "").startswith(":")
    ]
    internal_related_connections = [
        connection
        for connection in connections
        if connection.attrib.get("from", "").startswith(":")
        or connection.attrib.get("to", "").startswith(":")
        or connection.attrib.get("via", "").startswith(":")
    ]
    return {
        "network_file": str(path.relative_to(ROOT)).replace("\\", "/"),
        "edge_function_counts": dict(sorted(edge_functions.items())),
        "junction_type_counts": dict(sorted(junction_types.items())),
        "connection_count": len(connections),
        "top_external_connection_count": len(top_external_connections),
        "internal_related_connection_count": len(internal_related_connections),
        "controlled_connection_count": len(controlled_connections),
        "connection_dir_counts": dict(sorted(dir_counts.items())),
        "tlLogic_count": len(tl_logics),
        "tlLogic_phase_count": sum(len(tl_logic.findall("phase")) for tl_logic in tl_logics),
        "request_count": len(root.findall(".//request")),
        "internal_edge_count": edge_functions.get("internal", 0),
        "crossing_edge_count": edge_functions.get("crossing", 0),
        "walkingarea_edge_count": edge_functions.get("walkingarea", 0),
        "internal_junction_count": junction_types.get("internal", 0),
        "traffic_light_junction_count": junction_types.get("traffic_light", 0),
    }
