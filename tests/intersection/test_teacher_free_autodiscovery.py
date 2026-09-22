import hashlib
import json
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from torii_sumo.core.teacher_free_discovery_workflow import (
    run_teacher_free_discovery_workflow,
)
from torii_sumo.core.teacher_free_materialization_workflow import (
    run_teacher_free_materialization_workflow,
)
from torii_sumo.core.teacher_free_topology_workflow import (
    decide_topology_variant_outcomes,
    run_teacher_free_topology_workflow,
)
from torii_sumo.intersection.autodiscovery import (
    discover_teacher_free_intersections,
    discover_teacher_free_intersections_from_patch,
)
from torii_sumo.intersection.candidate_binding import (
    bind_materialized_candidate_to_dag,
)
from torii_sumo.intersection.candidate_dag import build_candidate_hypothesis_dag
from torii_sumo.intersection.materialization_experiment import (
    build_preregistered_materialization_contract,
    write_preregistered_join_patch,
)
from torii_sumo.intersection.osm_patch import parse_osm_xml
from torii_sumo.intersection.topology_discrimination_experiment import (
    build_topology_discrimination_contract,
    write_topology_node_patch,
)


XS1_OSM = Path("examples/03_xs1_four_way_tls/input/xs1-89129156.osm.xml.gz")
XS2_OSM = Path("examples/04_xs2_three_way_tls/input/xs2-7009179660.osm.xml")
HELD_OUT_X4 = Path("tests/intersection/fixtures/x4_signalized.osm.xml")
PAIRED_OFFSET = Path("tests/intersection/fixtures/paired_offset_shared_tls.osm.xml")
PEDESTRIAN_CROSSING = Path(
    "tests/intersection/fixtures/clustered_signalized_crossing.osm.xml"
)
COMPLETE_PEDESTRIAN_CROSSING = Path(
    "tests/intersection/fixtures/complete_signalized_crossing.osm.xml"
)
XS1_CANDIDATE = Path("examples/03_xs1_four_way_tls/golden/xs1-candidate.net.xml")
XS2_CANDIDATE = Path("examples/04_xs2_three_way_tls/golden/xs2-candidate.net.xml")


def _candidate_with_anchors(report, expected: set[str]):
    return next(
        candidate
        for candidate in report["candidates"]
        if set(candidate["anchor_node_ids"]) == expected
    )


def test_xs1_bbox_discovers_reviewed_cell_without_seed_or_scope() -> None:
    report = discover_teacher_free_intersections(
        XS1_OSM,
        traffic_side="right",
    )

    target = _candidate_with_anchors(
        report,
        {
            "321573214",
            "321573215",
            "359022505",
            "4622372941",
            "7622194981",
            "9197167804",
            "9602656749",
            "9602656750",
            "9602656751",
            "9602656752",
        },
    )
    hypothesis = target["hypothesis"]

    assert report["signal_anchor_count"] == 13
    assert report["candidate_count"] == 2
    assert report["review_ready_vehicle_candidate_count"] == 1
    assert target["canonical_seed_selection"]["selected_node_id"] == "89129156"
    assert hypothesis["seed_authority"] == "machine_selected_vehicle_graph_medoid"
    assert set(hypothesis["physical_cell"]["proposed_source_junction_ids"]) == {
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
    assert target["classification"]["kind"] == "vehicle_intersection"
    assert target["disposition"] == "suggest"
    assert target["discovery_blockers"] == []
    assert [
        variant["atomic_movement_count"]
        for variant in hypothesis["vehicle_movement_hypotheses"]["variants"]
    ] == [
        12,
        12,
    ]
    assert "reviewed_comparison" not in hypothesis
    assert (
        hypothesis["forbidden_generation_inputs"]
        == report["forbidden_generation_inputs"]
    )
    archetype = hypothesis["intersection_archetype_profile"]
    dag = hypothesis["candidate_dag"]
    assert hypothesis["classification_review_reasons"] == archetype["review_reasons"]
    assert all(
        not reason.startswith("archetype:type_recognition_")
        for reason in hypothesis["unresolved_reasons"]
    )
    assert archetype["derived_alias"]["value"] == "X4"
    assert dag["parent_archetype_classification_id"] == archetype["classification_id"]
    assert any(
        node["node_kind"] == "intersection_archetype_profile"
        and node["node_id"] == archetype["classification_id"]
        for node in dag["nodes"]
    )
    assert all(
        operation["payload"]["archetype_classification_id"]
        == archetype["classification_id"]
        for node in dag["nodes"]
        if node["node_kind"] == "candidate_variant"
        for operation in node["declared_operations"]
    )
    assert any(
        edge
        == {
            "from_node_id": hypothesis["vehicle_movement_hypotheses"][
                "hypothesis_set_id"
            ],
            "to_node_id": node["node_id"],
            "relation": "movement_semantics_derive_from_movement_hypotheses",
        }
        for node in dag["nodes"]
        if node["node_kind"] == "movement_semantic_class"
        for edge in dag["edges"]
    )


def test_candidate_dag_rejects_mismatched_or_blocked_archetype_profile() -> None:
    report = discover_teacher_free_intersections(XS1_OSM, traffic_side="right")
    hypothesis = _candidate_with_anchors(
        report,
        {
            "321573214",
            "321573215",
            "359022505",
            "4622372941",
            "7622194981",
            "9197167804",
            "9602656749",
            "9602656750",
            "9602656751",
            "9602656752",
        },
    )["hypothesis"]
    physical_cell = hypothesis["physical_cell"]
    movements = hypothesis["vehicle_movement_hypotheses"]
    profile = hypothesis["intersection_archetype_profile"]

    for field, value, message in (
        (
            "parent_physical_cell_hypothesis_id",
            "different-cell",
            "parent_physical_cell_hypothesis_id",
        ),
        (
            "parent_movement_hypothesis_set_id",
            "different-movements",
            "parent_movement_hypothesis_set_id",
        ),
        ("generation_status", "blocked", "generation_status must be pass"),
    ):
        invalid = deepcopy(profile)
        invalid[field] = value
        with pytest.raises(ValueError, match=message):
            build_candidate_hypothesis_dag(
                physical_cell,
                movements,
                archetype_profile=invalid,
            )

    stale_id_profile = deepcopy(profile)
    stale_id_profile["canonical_identity"]["arm_count"] = 999
    with pytest.raises(
        ValueError,
        match="classification_id does not match its canonical content",
    ):
        build_candidate_hypothesis_dag(
            physical_cell,
            movements,
            archetype_profile=stale_id_profile,
        )


def test_xs2_bbox_recovers_cell_but_preserves_movement_uncertainty() -> None:
    report = discover_teacher_free_intersections(
        XS2_OSM,
        traffic_side="right",
    )

    target = _candidate_with_anchors(
        report,
        {"7009179655", "7009179660", "7009179676"},
    )
    hypothesis = target["hypothesis"]
    movements = hypothesis["vehicle_movement_hypotheses"]

    assert target["canonical_seed_selection"]["selected_node_id"] == "7009179663"
    assert set(hypothesis["physical_cell"]["proposed_source_junction_ids"]) == {
        "7009179655",
        "7009179660",
        "7009179663",
        "7009179673",
        "7009179676",
        "7290797775",
    }
    assert len(hypothesis["physical_cell"]["physical_approaches"]) == 3
    assert [variant["atomic_movement_count"] for variant in movements["variants"]] == [
        6,
        7,
    ]
    assert movements["variant_comparison"]["status"] == "review_required"
    assert target["disposition"] == "review"
    assert (
        "teacher_free_hypothesis_has_unresolved_semantics"
        in target["discovery_blockers"]
    )


def test_xs1_preregistered_materialization_contract_uses_no_benchmark_answers(
    tmp_path: Path,
) -> None:
    discovery = discover_teacher_free_intersections(
        XS1_OSM,
        traffic_side="right",
    )

    contract = build_preregistered_materialization_contract(discovery)
    assert contract["status"] == "ready", [
        item["pre_materialization_blockers"]
        for item in contract["candidate_assessments"]
    ]
    plan = contract["candidate_plan"]
    patch_file = tmp_path / "auto-join.nod.xml"
    write_preregistered_join_patch(patch_file, contract=contract)
    root = ET.parse(patch_file).getroot()
    join = root.find("join")

    assert contract["status"] == "ready"
    assert contract["write_candidate_authorized"] is True
    assert contract["eligible_vehicle_candidate_count"] == 1
    assert plan["selection_kind"] == "preregistered_experiment_arm"
    assert plan["selection_is_topology_truth_claim"] is False
    assert plan["movement_metrics"]["movement_count"] == 12
    assert plan["movement_metrics"]["turn_counts"] == {"l": 4, "r": 4, "s": 4}
    assert plan["physical_approach_count"] == 4
    assert plan["protected_review_dimensions"] == [
        "pedestrian_model_phase_and_runtime_closure_unverified"
    ]
    assert join is not None
    assert join.attrib["id"] == plan["target_junction_id"]
    assert join.attrib["tl"] == plan["target_controller_id"]
    assert set(join.attrib["nodes"].split()) == set(plan["source_junction_ids"])
    assert "manual_seed_node" in contract["forbidden_inputs"]
    assert "expected_movement_count" in contract["forbidden_inputs"]
    assert contract["automatic_promotion_gate"] == "blocked"


def test_xs2_preregistered_materialization_contract_blocks_before_writing_candidate(
    tmp_path: Path,
) -> None:
    discovery = discover_teacher_free_intersections(
        XS2_OSM,
        traffic_side="right",
    )

    contract = build_preregistered_materialization_contract(discovery)

    assert contract["status"] == "blocked"
    assert contract["write_candidate_authorized"] is False
    assert contract["candidate_plan"] is None
    assert contract["eligible_vehicle_candidate_count"] == 0
    assert any(
        "movement_semantic_variants_disagree" in item["pre_materialization_blockers"]
        for item in contract["candidate_assessments"]
    )
    with pytest.raises(ValueError, match="ready materialization contract"):
        write_preregistered_join_patch(
            tmp_path / "must-not-exist.nod.xml",
            contract=contract,
        )
    assert not (tmp_path / "must-not-exist.nod.xml").exists()


def test_xs1_v4_contract_freezes_three_independent_topology_arms(
    tmp_path: Path,
) -> None:
    patch = parse_osm_xml(XS1_OSM)
    discovery = discover_teacher_free_intersections_from_patch(
        patch,
        traffic_side="right",
    )

    contract = build_topology_discrimination_contract(discovery, patch)
    plans = {plan["topology_hypothesis"]: plan for plan in contract["candidate_plans"]}

    assert contract["status"] == "ready", contract["candidate_assessments"]
    assert set(plans) == {
        "preserve_split_shared_controller",
        "merge_physical_cell",
        "partial_internal_repair",
    }
    assert all(
        plan["selection_is_topology_truth_claim"] is False for plan in plans.values()
    )
    assert all(plan["automatic_promotion_gate"] == "blocked" for plan in plans.values())
    assert plans["partial_internal_repair"]["conflict_center_node_id"] == ("89129156")

    for topology, plan in plans.items():
        patch_file = tmp_path / f"{topology}.nod.xml"
        write_topology_node_patch(
            patch_file,
            contract=contract,
            candidate_plan_id=plan["candidate_plan_id"],
        )
        root = ET.parse(patch_file).getroot()
        if topology == "merge_physical_cell":
            join = root.find("join")
            assert join is not None
            assert set(join.attrib["nodes"].split()) == set(plan["source_junction_ids"])
        elif topology == "preserve_split_shared_controller":
            assert root.find("join") is None
            assert {node.attrib["tl"] for node in root.findall("node")} == {
                plan["target_controller_id"]
            }
            assert {node.attrib["id"] for node in root.findall("node")} == set(
                plan["signal_anchor_node_ids"]
            )
        else:
            assert root.find("join") is None
            center = root.find("node[@id='89129156']")
            assert center is not None
            assert center.attrib == {
                "id": "89129156",
                "type": "traffic_light",
                "tl": plan["target_controller_id"],
            }
            assert all(
                node.attrib["type"] == "priority"
                for node in root.findall("node")
                if node.attrib["id"] != "89129156"
            )


def test_xs2_v4_contract_blocks_all_topology_arms_before_candidate_write(
    tmp_path: Path,
) -> None:
    patch = parse_osm_xml(XS2_OSM)
    discovery = discover_teacher_free_intersections_from_patch(
        patch,
        traffic_side="right",
    )

    contract = build_topology_discrimination_contract(discovery, patch)

    assert contract["status"] == "blocked"
    assert contract["candidate_plans"] == []
    assert all(
        "movement_semantic_variants_disagree" in arm["pre_materialization_blockers"]
        for assessment in contract["candidate_assessments"]
        for arm in assessment["topology_arms"]
    )
    with pytest.raises(ValueError, match="ready discrimination contract"):
        write_topology_node_patch(
            tmp_path / "must-not-exist.nod.xml",
            contract=contract,
            candidate_plan_id="not-authorized",
        )
    assert not (tmp_path / "must-not-exist.nod.xml").exists()


def test_paired_offset_negative_falsifies_merge_without_using_signal_count() -> None:
    patch = parse_osm_xml(PAIRED_OFFSET)
    discovery = discover_teacher_free_intersections_from_patch(
        patch,
        traffic_side="right",
    )

    contract = build_topology_discrimination_contract(discovery, patch)
    vehicle_assessments = [
        item
        for item in contract["candidate_assessments"]
        if item["classification"]["kind"] == "vehicle_intersection"
    ]

    assert len(vehicle_assessments) == 1, discovery
    evidence = vehicle_assessments[0]["topology_evidence"]
    merge = next(
        arm
        for arm in vehicle_assessments[0]["topology_arms"]
        if arm["topology_hypothesis"] == "merge_physical_cell"
    )
    assert evidence["signal_anchor_count"] == 4
    assert evidence["branch_node_count"] == 2
    assert evidence["storage_capable_connectors"]
    assert merge["pre_materialization_status"] == "blocked"
    assert (
        "merge_requires_exactly_one_vehicle_conflict_center"
        in merge["pre_materialization_blockers"]
    )
    assert (
        "storage_capable_connector_falsifies_single_cell_merge"
        in merge["pre_materialization_blockers"]
    )
    assert all(
        plan["topology_hypothesis"] != "merge_physical_cell"
        for plan in contract["candidate_plans"]
    )


def test_xs2_v4_workflow_blocks_before_tool_lookup_or_candidate_write(
    tmp_path: Path,
) -> None:
    output = tmp_path / "xs2-topology-v4"
    source_before = hashlib.sha256(XS2_OSM.read_bytes()).hexdigest()

    report = run_teacher_free_topology_workflow(
        osm_file=XS2_OSM,
        output_dir=output,
        traffic_side="right",
        toolchain_lock_file=Path(
            "benchmarks/corridor_human_modeling_v1/toolchain.lock.json"
        ),
        binaries={},
    )

    assert report["status"] == "blocked"
    assert report["details"]["terminal_stage"] == "pre_materialization"
    assert report["candidate_written"] is False
    assert any(
        "movement_semantic_variants_disagree" in arm["pre_materialization_blockers"]
        for assessment in report["candidate_assessments"]
        for arm in assessment["topology_arms"]
    )
    assert not (output / "source.net.xml").exists()
    assert not (output / "variants").exists()
    assert hashlib.sha256(XS2_OSM.read_bytes()).hexdigest() == source_before


@pytest.mark.parametrize(
    ("feasible_flags", "status", "decision"),
    [
        (
            [False, False, False],
            "blocked",
            "reject_physical_cell_hypothesis_without_scope_expansion",
        ),
        (
            [False, True, False],
            "review_ready",
            "suggest_single_machine_feasible_arm_for_human_review",
        ),
        (
            [True, True, False],
            "review_ready",
            "blind_review_required",
        ),
    ],
)
def test_v4_zero_one_many_rule_never_selects_or_expands_scope(
    feasible_flags: list[bool],
    status: str,
    decision: str,
) -> None:
    outcome = decide_topology_variant_outcomes(
        [{"machine_feasible": value} for value in feasible_flags]
    )

    assert outcome["status"] == status
    assert outcome["machine_decision"] == decision
    assert outcome["machine_feasible_variant_count"] == sum(feasible_flags)
    assert outcome["scope_expansion_allowed"] is False
    assert outcome["automatic_topology_selection"] is False
    assert outcome["automatic_promotion_gate"] == "blocked"
    assert outcome["field_timing_reconstruction"] is False


def test_no_signal_held_out_patch_is_not_applicable_to_materialization() -> None:
    patch = parse_osm_xml(HELD_OUT_X4)
    for node in patch.nodes.values():
        node.tags.pop("crossing", None)
        if node.tags.get("highway") == "traffic_signals":
            node.tags.pop("highway")
    discovery = discover_teacher_free_intersections_from_patch(
        patch,
        traffic_side="right",
    )

    contract = build_preregistered_materialization_contract(discovery)

    assert discovery["signal_anchor_count"] == 0
    assert discovery["candidate_count"] == 0
    assert contract["status"] == "not_applicable"
    assert contract["write_candidate_authorized"] is False
    assert contract["candidate_plan"] is None
    assert contract["vehicle_candidate_count"] == 0


def test_xs2_workflow_fails_closed_before_candidate_artifacts(
    tmp_path: Path,
) -> None:
    output = tmp_path / "xs2-materialization"
    source_before = hashlib.sha256(XS2_OSM.read_bytes()).hexdigest()

    report = run_teacher_free_materialization_workflow(
        osm_file=XS2_OSM,
        output_dir=output,
        traffic_side="right",
        toolchain_lock_file=Path(
            "benchmarks/corridor_human_modeling_v1/toolchain.lock.json"
        ),
        binaries={},
    )
    contract = json.loads(
        (output / "materialization-contract.json").read_text(encoding="utf-8")
    )

    assert report["status"] == "blocked"
    assert report["details"]["terminal_stage"] == "pre_materialization"
    assert report["candidate_written"] is False
    assert contract["eligible_vehicle_candidate_count"] == 0
    assert any(
        "movement_semantic_variants_disagree" in item["pre_materialization_blockers"]
        for item in contract["candidate_assessments"]
    )
    assert not (output / "candidate-join.nod.xml").exists()
    assert not (output / "source.net.xml").exists()
    assert not (output / "candidate.net.xml").exists()
    assert not (output / "rollback.json").exists()
    assert hashlib.sha256(XS2_OSM.read_bytes()).hexdigest() == source_before


def test_no_signal_held_out_workflow_exits_not_applicable_before_tool_lookup(
    tmp_path: Path,
) -> None:
    source = tmp_path / "x4-no-signal.osm.xml"
    source.write_text(
        HELD_OUT_X4.read_text(encoding="utf-8").replace(
            '<tag k="highway" v="traffic_signals"/>',
            "",
        ),
        encoding="utf-8",
    )
    output = tmp_path / "no-signal-materialization"

    report = run_teacher_free_materialization_workflow(
        osm_file=source,
        output_dir=output,
        traffic_side="right",
        toolchain_lock_file=Path(
            "benchmarks/corridor_human_modeling_v1/toolchain.lock.json"
        ),
        binaries={},
    )

    assert report["status"] == "not_applicable"
    assert report["details"]["terminal_stage"] == "pre_materialization"
    assert report["candidate_written"] is False
    assert not (output / "candidate-join.nod.xml").exists()
    assert not (output / "candidate.net.xml").exists()


@pytest.mark.parametrize(
    (
        "osm_file",
        "anchor_ids",
        "candidate_net",
        "ownership_file",
        "target_junction_id",
        "controller_ids",
        "expected_seed",
        "expected_disposition",
        "expected_movement_count",
        "expected_exact_methods",
    ),
    [
        (
            XS1_OSM,
            {
                "321573214",
                "321573215",
                "359022505",
                "4622372941",
                "7622194981",
                "9197167804",
                "9602656749",
                "9602656750",
                "9602656751",
                "9602656752",
            },
            XS1_CANDIDATE,
            Path("examples/03_xs1_four_way_tls/golden/tls-ownership.public.json"),
            "cluster_321573214_321573215_359022505_4622372941_#7more",
            ("cluster_321573214_321573215_359022505_4622372941_#7more",),
            "89129156",
            "suggest",
            12,
            {"osm_turn_lanes_strict", "geometry_continuity"},
        ),
        (
            XS2_OSM,
            {"7009179655", "7009179660", "7009179676"},
            XS2_CANDIDATE,
            Path("examples/04_xs2_three_way_tls/golden/tls-ownership.public.json"),
            "xs2_t_junction_7009179660",
            ("xs2_tls_7009179660",),
            "7009179663",
            "review",
            7,
            {"geometry_continuity"},
        ),
    ],
)
def test_bbox_discovery_binds_materialized_network_only_as_posthoc_evidence(
    osm_file: Path,
    anchor_ids: set[str],
    candidate_net: Path,
    ownership_file: Path,
    target_junction_id: str,
    controller_ids: tuple[str, ...],
    expected_seed: str,
    expected_disposition: str,
    expected_movement_count: int,
    expected_exact_methods: set[str],
) -> None:
    discovery = discover_teacher_free_intersections(
        osm_file,
        traffic_side="right",
    )
    candidate = _candidate_with_anchors(discovery, anchor_ids)
    hypothesis = candidate["hypothesis"]

    assert candidate["canonical_seed_selection"]["selected_node_id"] == (expected_seed)
    assert "materialized_candidate_network" in hypothesis["forbidden_generation_inputs"]
    binding = bind_materialized_candidate_to_dag(
        candidate_net=candidate_net,
        target_junction_id=target_junction_id,
        expected_controller_ids=controller_ids,
        physical_cell=hypothesis["physical_cell"],
        movement_hypotheses=hypothesis["vehicle_movement_hypotheses"],
        candidate_dag=hypothesis["candidate_dag"],
        tls_ownership=json.loads(ownership_file.read_text(encoding="utf-8")),
    )

    assert binding["binding_status"] == "pass"
    assert binding["semantic_disposition"] == expected_disposition
    assert binding["mapped_connection_count"] == expected_movement_count
    assert {
        item["method"]
        for item in binding["variant_matches"]
        if item["status"] == "exact"
    } == expected_exact_methods
    assert hypothesis["automatic_promotion_gate"] == "blocked"


def test_held_out_x4_is_discovered_and_order_invariant() -> None:
    patch = parse_osm_xml(HELD_OUT_X4)
    first = discover_teacher_free_intersections_from_patch(
        patch,
        traffic_side="right",
    )
    reordered = patch.model_copy(deep=True)
    reordered.nodes = dict(reversed(list(reordered.nodes.items())))
    reordered.ways = dict(reversed(list(reordered.ways.items())))
    reordered.relations = dict(reversed(list(reordered.relations.items())))
    second = discover_teacher_free_intersections_from_patch(
        reordered,
        traffic_side="right",
    )

    candidate = first["candidates"][0]
    movements = candidate["hypothesis"]["vehicle_movement_hypotheses"]
    assert first["discovery_id"] == second["discovery_id"]
    assert first == second
    assert first["candidate_count"] == 1
    assert candidate["canonical_seed_selection"]["selected_node_id"] == "1"
    assert candidate["classification"]["kind"] == "vehicle_intersection"
    assert candidate["disposition"] == "suggest"
    assert [variant["atomic_movement_count"] for variant in movements["variants"]] == [
        12,
        12,
    ]


def test_site_port_and_movement_ids_survive_small_coordinate_perturbation() -> None:
    patch = parse_osm_xml(XS1_OSM)
    first = discover_teacher_free_intersections_from_patch(
        patch,
        traffic_side="right",
    )
    perturbed = patch.model_copy(deep=True)
    for node_id, node in perturbed.nodes.items():
        delta_m = ((sum(ord(character) for character in node_id) % 7) - 3) * 0.01
        node.x = float(node.x or 0.0) + delta_m
        node.y = float(node.y or 0.0) - delta_m / 2
    second = discover_teacher_free_intersections_from_patch(
        perturbed,
        traffic_side="right",
    )
    anchors = {
        "321573214",
        "321573215",
        "359022505",
        "4622372941",
        "7622194981",
        "9197167804",
        "9602656749",
        "9602656750",
        "9602656751",
        "9602656752",
    }
    first_candidate = _candidate_with_anchors(first, anchors)
    second_candidate = _candidate_with_anchors(second, anchors)
    first_cell = first_candidate["hypothesis"]["physical_cell"]
    second_cell = second_candidate["hypothesis"]["physical_cell"]

    assert first_candidate["candidate_id"] == second_candidate["candidate_id"]
    assert (
        first_candidate["canonical_seed_selection"]["selected_node_id"]
        == (second_candidate["canonical_seed_selection"]["selected_node_id"])
    )
    assert {port["boundary_port_id"] for port in first_cell["raw_boundary_ports"]} == {
        port["boundary_port_id"] for port in second_cell["raw_boundary_ports"]
    }
    assert {
        audit["facility_id"] for audit in first_candidate["pedestrian_facility_audits"]
    } == {
        audit["facility_id"] for audit in second_candidate["pedestrian_facility_audits"]
    }
    for first_variant, second_variant in zip(
        first_candidate["hypothesis"]["vehicle_movement_hypotheses"]["variants"],
        second_candidate["hypothesis"]["vehicle_movement_hypotheses"]["variants"],
        strict=True,
    ):
        assert {
            movement["stable_movement_id"]
            for movement in first_variant["atomic_movements"]
        } == {
            movement["stable_movement_id"]
            for movement in second_variant["atomic_movements"]
        }


def test_signalized_crossing_routes_to_pedestrian_audit_not_vehicle_merge() -> None:
    report = discover_teacher_free_intersections(
        PEDESTRIAN_CROSSING,
        traffic_side="right",
    )

    candidate = report["candidates"][0]
    assert report["vehicle_intersection_candidate_count"] == 0
    assert report["pedestrian_facility_candidate_count"] == 1
    assert candidate["classification"] == {
        "kind": "pedestrian_crossing_facility",
        "status": "review",
        "reason": "signalized_crossing_requires_facility_and_row_audit",
        "physical_approach_count": 2,
        "has_vehicle_signal_anchor": False,
        "has_signalized_crossing_anchor": True,
        "physical_cell_risks": [
            "physical_approach_count_outside_standard_t3_x4_domain"
        ],
    }
    assert candidate["disposition"] == "review"
    assert candidate["automatic_promotion_gate"] == "blocked"
    assert report["pedestrian_facility_audit_count"] == 1
    assert report["pedestrian_facility_audit_ready_count"] == 0
    assert report["pedestrian_facility_audit_blocked_count"] == 1
    audit = candidate["pedestrian_facility_audits"][0]
    assert candidate["pedestrian_facility_audit_status"] == "blocked"
    assert audit["source_row_decision"]["expected_class"] == "signalized"
    assert audit["source_row_decision"]["model_claim_fields_read"] == []
    assert audit["topology_evidence"]["request_foes_fields_read"] == []
    assert audit["topology_evidence"]["support_arm_count"] == 1
    assert audit["topology_evidence"]["vehicle_arm_count"] == 2
    assert audit["blockers"] == ["pedestrian_support_arm_count_not_two"]
    assert audit["next_required_gate"] == "source_facility_topology_review"


def test_complete_signalized_crossing_reaches_audit_only_positive_gate() -> None:
    report = discover_teacher_free_intersections(
        COMPLETE_PEDESTRIAN_CROSSING,
        traffic_side="right",
    )

    candidate = report["candidates"][0]
    audit = candidate["pedestrian_facility_audits"][0]

    assert report["candidate_count"] == 1
    assert report["pedestrian_facility_candidate_count"] == 1
    assert report["pedestrian_facility_audit_count"] == 1
    assert report["pedestrian_facility_audit_ready_count"] == 1
    assert report["pedestrian_facility_audit_blocked_count"] == 0
    assert candidate["pedestrian_facility_audit_status"] == "review_ready"
    assert audit["audit_status"] == "review_ready"
    assert audit["blockers"] == []
    assert audit["source_row_decision"]["expected_class"] == "signalized"
    assert audit["source_row_decision"]["reasons"] == [
        "osm_tag_declares_signalized_crossing"
    ]
    assert audit["source_row_decision"]["model_claim_fields_read"] == []
    assert audit["topology_evidence"]["support_arm_count"] == 2
    assert audit["topology_evidence"]["vehicle_arm_count"] == 2
    assert audit["topology_evidence"]["support_opposition_deg"] == 180.0
    assert audit["topology_evidence"]["vehicle_opposition_deg"] == 180.0
    assert audit["topology_evidence"]["axis_crossing_angle_deg"] == 90.0
    assert audit["topology_evidence"]["geometry_infers_right_of_way"] is False
    assert audit["topology_evidence"]["request_foes_fields_read"] == []
    assert audit["automatic_promotion_gate"] == "blocked"
    assert audit["next_required_gate"] == (
        "materialized_model_claim_geometry_and_runtime_audit"
    )


def test_bicycle_only_cycleway_does_not_masquerade_as_pedestrian_support() -> None:
    patch = parse_osm_xml(COMPLETE_PEDESTRIAN_CROSSING)
    path = patch.ways["foot_ns"]
    path.tags = {"highway": "cycleway", "bicycle": "designated"}

    report = discover_teacher_free_intersections_from_patch(
        patch,
        traffic_side="right",
    )
    audit = report["candidates"][0]["pedestrian_facility_audits"][0]

    assert audit["topology_evidence"]["support_arm_count"] == 0
    assert audit["audit_status"] == "blocked"
    assert "pedestrian_support_arm_count_not_two" in audit["blockers"]


def test_discovery_workflow_is_hash_bound_repeatable_and_refuses_foreign_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "owned-output"
    source_before = hashlib.sha256(HELD_OUT_X4.read_bytes()).hexdigest()

    first = run_teacher_free_discovery_workflow(
        osm_file=HELD_OUT_X4,
        output_dir=output,
        traffic_side="right",
    )
    second = run_teacher_free_discovery_workflow(
        osm_file=HELD_OUT_X4,
        output_dir=output,
        traffic_side="right",
    )
    manifest = json.loads(Path(second["manifest_file"]).read_text(encoding="utf-8"))

    assert first["discovery_id"] == second["discovery_id"]
    assert first["status"] == second["status"] == "review_ready"
    assert len(manifest["artifacts"]) == 4
    assert {Path(item["path"]).name for item in manifest["artifacts"]} == {
        "teacher-free-discovery.owner.json",
        "teacher-free-discovery.json",
        "teacher-free-discovery.geojson",
        "teacher-free-discovery.html",
    }
    assert hashlib.sha256(HELD_OUT_X4.read_bytes()).hexdigest() == source_before
    assert manifest["automatic_promotion_gate"] == "blocked"
    assert manifest["source_mutation"] is False

    foreign = tmp_path / "foreign-output"
    foreign.mkdir()
    (foreign / "user-file.txt").write_text("preserve me", encoding="utf-8")
    with pytest.raises(ValueError, match="without Torii ownership"):
        run_teacher_free_discovery_workflow(
            osm_file=HELD_OUT_X4,
            output_dir=foreign,
            traffic_side="right",
        )
    assert (foreign / "user-file.txt").read_text(encoding="utf-8") == "preserve me"

    nested_output = tmp_path / "source-inside-output"
    nested_output.mkdir()
    nested_source = nested_output / "source.osm.xml"
    nested_source.write_bytes(HELD_OUT_X4.read_bytes())
    with pytest.raises(ValueError, match="source OSM must not be stored inside"):
        run_teacher_free_discovery_workflow(
            osm_file=nested_source,
            output_dir=nested_output,
            traffic_side="right",
        )
    assert nested_source.read_bytes() == HELD_OUT_X4.read_bytes()

    copied_owner = tmp_path / "copied-owner"
    copied_owner.mkdir()
    (copied_owner / "teacher-free-discovery.owner.json").write_text(
        (output / "teacher-free-discovery.owner.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (copied_owner / "user-file.txt").write_text("preserve me", encoding="utf-8")
    with pytest.raises(ValueError, match="ownership root"):
        run_teacher_free_discovery_workflow(
            osm_file=HELD_OUT_X4,
            output_dir=copied_owner,
            traffic_side="right",
        )
    assert (copied_owner / "user-file.txt").read_text(encoding="utf-8") == "preserve me"
