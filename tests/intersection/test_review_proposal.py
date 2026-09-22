import json
import re
from pathlib import Path

from torii_sumo.intersection.candidate_binding import (
    bind_materialized_candidate_to_dag,
)
from torii_sumo.intersection.candidate_dag import (
    build_candidate_hypothesis_dag,
)
from torii_sumo.intersection.osm_patch import parse_osm_xml
from torii_sumo.intersection.movement_hypotheses import (
    build_vehicle_movement_hypotheses,
)
from torii_sumo.intersection.physical_cell import (
    infer_signal_anchor_physical_cell,
)
from torii_sumo.intersection.review_proposal import (
    build_intersection_review_proposal,
)
from torii_sumo.intersection.schema import OSMRelation


XS1_OSM = Path("examples/03_xs1_four_way_tls/input/xs1-89129156.osm.xml.gz")
XS2_OSM = Path("examples/04_xs2_three_way_tls/input/xs2-7009179660.osm.xml")
XS1_CANDIDATE = Path("examples/03_xs1_four_way_tls/golden/xs1-candidate.net.xml")
XS2_CANDIDATE = Path("examples/04_xs2_three_way_tls/golden/xs2-candidate.net.xml")


def test_signal_anchor_cell_recovers_xs1_scope_without_reviewed_ids(
    tmp_path: Path,
) -> None:
    patch = parse_osm_xml(XS1_OSM)

    first = infer_signal_anchor_physical_cell(
        patch,
        seed_node_id="89129156",
    )
    second = infer_signal_anchor_physical_cell(
        patch,
        seed_node_id="89129156",
    )

    assert first["hypothesis_id"] == second["hypothesis_id"]
    assert set(first["proposed_source_junction_ids"]) == {
        "89129156",
        "9197167804",
        "7622194981",
        "4622372941",
        "321573215",
        "359022505",
        "321573214",
        "9602656752",
        "9602656749",
        "9602656751",
        "9602656750",
    }
    assert len(first["raw_boundary_ports"]) == 4
    assert first["automatic_promotion_gate"] == "blocked"
    movement_hypotheses = build_vehicle_movement_hypotheses(
        patch,
        first,
        traffic_side="right",
    )
    assert movement_hypotheses["variant_comparison"]["status"] == "exact"
    assert [variant["atomic_movement_count"] for variant in movement_hypotheses["variants"]] == [12, 12]
    assert all(variant["lane_coverage"]["status"] == "pass" for variant in movement_hypotheses["variants"])
    candidate_dag = build_candidate_hypothesis_dag(first, movement_hypotheses)
    assert candidate_dag["semantic_equivalence_class_count"] == 1
    assert candidate_dag["candidate_count"] == 3
    assert len(candidate_dag["review_ready_candidate_ids"]) == 3
    assert candidate_dag["selected_candidate_id"] is None
    assert all(
        operation["inverse_operation"]["source_mutation"] is False
        for candidate in candidate_dag["nodes"]
        if candidate["node_kind"] == "candidate_variant"
        for operation in candidate["declared_operations"]
    )
    ownership = json.loads(
        Path("examples/03_xs1_four_way_tls/golden/tls-ownership.public.json").read_text(encoding="utf-8")
    )
    binding = bind_materialized_candidate_to_dag(
        candidate_net=XS1_CANDIDATE,
        target_junction_id=("cluster_321573214_321573215_359022505_4622372941_#7more"),
        expected_controller_ids=("cluster_321573214_321573215_359022505_4622372941_#7more",),
        physical_cell=first,
        movement_hypotheses=movement_hypotheses,
        candidate_dag=candidate_dag,
        tls_ownership=ownership,
    )
    assert binding["binding_status"] == "pass"
    assert binding["semantic_disposition"] == "suggest"
    assert binding["mapped_connection_count"] == 12
    assert len(binding["exact_movement_variant_ids"]) == 2
    timestamp_variant = tmp_path / "timestamp-variant.net.xml"
    timestamp_variant.write_text(
        re.sub(
            r"generated on [^\n]+ by",
            "generated on different-run-metadata by",
            XS1_CANDIDATE.read_text(encoding="utf-8"),
            count=1,
        ),
        encoding="utf-8",
    )
    rebound = bind_materialized_candidate_to_dag(
        candidate_net=timestamp_variant,
        target_junction_id=("cluster_321573214_321573215_359022505_4622372941_#7more"),
        expected_controller_ids=("cluster_321573214_321573215_359022505_4622372941_#7more",),
        physical_cell=first,
        movement_hypotheses=movement_hypotheses,
        candidate_dag=candidate_dag,
        tls_ownership=ownership,
    )
    assert rebound["candidate_net"]["sha256"] != binding["candidate_net"]["sha256"]
    assert rebound["candidate_net"]["normalized_sha256"] == binding["candidate_net"]["normalized_sha256"]
    assert rebound["binding_id"] == binding["binding_id"]


def test_xs2_proposal_separates_cell_membership_from_semantic_uncertainty() -> None:
    proposal = build_intersection_review_proposal(
        osm_file=XS2_OSM,
        seed_node_id="7009179660",
        expected_topology_type="T3",
        expected_vehicle_approach_count=3,
        expected_legal_vehicle_movement_count=7,
        reviewed_source_junction_ids=(
            "7009179655",
            "7009179660",
            "7009179663",
            "7009179673",
            "7009179676",
            "7290797775",
        ),
    )

    signal = proposal["physical_cell_hypotheses"]["signal_anchor_cell"]
    assert proposal["teacher_free_generation"]["seed_authority"] == ("caller_provided_anchor_only")
    assert signal["membership_comparison"]["status"] == "exact"
    assert signal["geometry_shape_node_ids"] == [
        "11623233262",
        "7009179662",
        "7009179664",
    ]
    assert len(signal["raw_boundary_ports"]) == 4
    assert len(signal["physical_approaches"]) == 3
    assert all(item["grouping_status"] == "pass" for item in signal["physical_approaches"])
    approaches_by_name = {tuple(item["road_names"]): item for item in signal["physical_approaches"]}
    assert approaches_by_name[("Schutterstraße",)]["incoming_lane_count"] == 2
    assert approaches_by_name[("Schutterstraße",)]["outgoing_lane_count"] == 1
    assert approaches_by_name[("Schutterstraße",)]["incoming_turn_lanes_raw"] == "left|left"
    assert proposal["reviewed_comparison"]["topology"] == {
        "status": "match",
        "expected": "T3",
        "observed": "T3",
    }
    assert proposal["reviewed_comparison"]["legacy_ir_topology"] == {
        "status": "review_required",
        "expected": "T3",
        "observed": "complex",
    }
    movement_hypotheses = proposal["vehicle_movement_hypotheses"]
    assert movement_hypotheses["variant_comparison"]["status"] == "review_required"
    assert {variant["method"]: variant["atomic_movement_count"] for variant in movement_hypotheses["variants"]} == {
        "osm_turn_lanes_strict": 6,
        "geometry_continuity": 7,
    }
    assert len(movement_hypotheses["nested_restriction_ids"]) == 6
    assert "16200640" in movement_hypotheses["unresolved_restriction_ids"]
    candidate_dag = build_candidate_hypothesis_dag(signal, movement_hypotheses)
    assert candidate_dag["semantic_equivalence_class_count"] == 2
    assert candidate_dag["candidate_count"] == 6
    assert len(candidate_dag["blocked_candidate_ids"]) == 6
    ownership = json.loads(
        Path("examples/04_xs2_three_way_tls/golden/tls-ownership.public.json").read_text(encoding="utf-8")
    )
    binding = bind_materialized_candidate_to_dag(
        candidate_net=XS2_CANDIDATE,
        target_junction_id="xs2_t_junction_7009179660",
        expected_controller_ids=("xs2_tls_7009179660",),
        physical_cell=signal,
        movement_hypotheses=movement_hypotheses,
        candidate_dag=candidate_dag,
        tls_ownership=ownership,
    )
    assert binding["binding_status"] == "pass"
    assert binding["semantic_disposition"] == "review"
    assert binding["mapped_connection_count"] == 7
    assert [item["method"] for item in binding["variant_matches"] if item["status"] == "exact"] == [
        "geometry_continuity"
    ]
    assert proposal["machine_recommendation"] == "movement_variants_disagree_review_required"
    assert proposal["automatic_promotion_gate"] == "blocked"


def test_approach_grouping_abstains_when_oneway_road_identity_disagrees() -> None:
    patch = parse_osm_xml(XS2_OSM)
    patch.ways["1023430508"].tags["name"] = "Different outbound road"

    result = infer_signal_anchor_physical_cell(
        patch,
        seed_node_id="7009179660",
    )

    assert len(result["raw_boundary_ports"]) == 4
    assert len(result["physical_approaches"]) == 4
    assert "physical_approach_grouping_unresolved" in result["risks"]
    assert any(item["grouping_status"] == "review_required" for item in result["physical_approaches"])
    assert result["automatic_promotion_gate"] == "blocked"


def test_approach_grouping_abstains_for_more_than_one_complementary_pair() -> None:
    patch = parse_osm_xml(XS2_OSM)
    duplicate = patch.ways["1023430508"].model_copy(deep=True)
    duplicate.id = "synthetic-duplicate-outbound"
    patch.ways[duplicate.id] = duplicate

    result = infer_signal_anchor_physical_cell(
        patch,
        seed_node_id="7009179660",
    )

    grouped = next(item for item in result["physical_approaches"] if item["member_count"] == 3)
    assert grouped["grouping_status"] == "review_required"
    assert grouped["grouping_reason"] == "boundary_port_flow_or_identity_ambiguous"
    assert "physical_approach_grouping_unresolved" in result["risks"]


def test_unknown_direct_osm_restriction_is_never_silently_ignored() -> None:
    patch = parse_osm_xml(XS1_OSM)
    patch.relations["synthetic-unknown-restriction"] = OSMRelation(
        id="synthetic-unknown-restriction",
        members=[
            {"type": "way", "ref": "1084717760", "role": "from"},
            {"type": "node", "ref": "89129156", "role": "via"},
            {"type": "way", "ref": "1084717759", "role": "to"},
        ],
        tags={"type": "restriction", "restriction": "no_entry"},
    )
    cell = infer_signal_anchor_physical_cell(
        patch,
        seed_node_id="89129156",
    )

    hypotheses = build_vehicle_movement_hypotheses(
        patch,
        cell,
        traffic_side="right",
    )

    record = next(
        item
        for item in hypotheses["restriction_inventory"]
        if item["restriction_id"] == "synthetic-unknown-restriction"
    )
    assert record["support_status"] == "review_required"
    assert "restriction_type_unsupported" in record["evidence_issues"]
    assert hypotheses["unresolved_restriction_ids"] == ["synthetic-unknown-restriction"]
    assert "unsupported_or_incomplete_turn_restriction_evidence" in hypotheses["unresolved_reasons"]
