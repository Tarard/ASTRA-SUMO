from pathlib import Path

import pytest

from torii_sumo.core.workflow_router import (
    WORKFLOW_RECIPES,
    detect_workflow,
    infer_place_name,
    infer_seed_osm_node_id,
    run_auto_workflow,
)
from torii_sumo.tools.workflow_tools import torii_auto_workflow
from torii_sumo.core.osm_network import parse_bbox


CLEANUP_FIELDS = {
    "output_dir",
    "bbox",
    "profile",
    "source_osm_path",
    "traffic_layers",
    "reference_net_file",
    "timeout_seconds",
}


def _write_reference_net(path: Path) -> None:
    path.write_text(
        """<net>
    <edge id="primary_a" type="highway.primary">
        <lane id="primary_a_0" index="0" allow="passenger bus" speed="13.9" length="25.0"/>
    </edge>
    <edge id="service_a" type="highway.service">
        <lane id="service_a_0" index="0" allow="delivery passenger" speed="5.0" length="25.0"/>
    </edge>
    <edge id="cycle_a" type="highway.cycleway">
        <lane id="cycle_a_0" index="0" allow="bicycle" speed="5.0" length="25.0"/>
    </edge>
</net>""",
        encoding="utf-8",
    )


def test_infer_place_name_from_one_prompt_osm_request() -> None:
    request = "Use Torii to download the Altstadt map in Dresden from OSM, clean it up and open it in SUMO"

    assert infer_place_name(request) == "Altstadt, Dresden"


def test_detect_workflow_routes_common_one_sentence_requests() -> None:
    assert detect_workflow("Build a four-way TLS intersection") == "intersection_scene"
    assert detect_workflow("download the Altstadt map from OSM and open it in SUMO") == "osm_to_sumo"
    assert (
        detect_workflow("generate a TUM-like SUMO network from OSM with TLS and connection semantics") == "osm_to_sumo"
    )
    assert (
        detect_workflow(
            "clean the Ingolstadt city-center network from OSM, compare it with the TUM cleaned network, and open it in Netedit"
        )
        == "osm_to_sumo"
    )
    assert detect_workflow("audit the traffic lights in this SUMO network") == "tls_review"
    assert detect_workflow("create a TLS audit for this SUMO network") == "tls_review"
    assert detect_workflow("create an HTML review cockpit for this partial SUMO network") == "network_review"
    assert detect_workflow("check whether this route from station to museum is connected") == "routeability"
    assert detect_workflow("my waiting time got worse after cleanup") == "debug_bad_run"
    assert detect_workflow("compare fixed-time and max-pressure controllers") == "experiment_audit"


def test_intersection_scene_recipe_uses_phase_one_workflow_tool() -> None:
    recipe = WORKFLOW_RECIPES["intersection_scene"]

    assert recipe["tool_chain"] == ["sumo_intersection_scene_workflow"]
    assert "four-way" in recipe["description"]


def test_detect_workflow_keeps_audit_and_osm_patch_requests_out_of_scene_generation() -> None:
    assert detect_workflow("Create a TLS audit for this SUMO network") == "tls_review"
    assert detect_workflow("Build this local OSM four-way TLS intersection patch") == "intersection_clean"
    assert detect_workflow("Create this local OSM four-way TLS intersection patch") == "intersection_clean"
    assert detect_workflow("Patch this local OSM four-way TLS intersection") == "intersection_clean"
    assert detect_workflow("Dispatch this local OSM four-way TLS intersection") != "intersection_clean"


def test_detect_workflow_requires_affirmative_scene_signalization() -> None:
    for prompt in (
        "Build a four-way unsignalized intersection",
        "Build a four-way non-signalized intersection",
        "Build a four-way not signalized intersection",
        "Build a four-way without traffic lights",
        "Build a four-way without any traffic lights",
        "Build a four-way with no traffic lights",
        "Build a four-way intersection that isn't signalized",
        "Build a four-way intersection that does not have traffic lights",
        "Build a four-way intersection that isn't controlled by traffic lights",
        "Build a four-way never-signalized intersection",
        "Build a four-way intersection with neither TLS nor traffic lights",
        "Build a four-way intersection that is not actually signalized",
    ):
        assert detect_workflow(prompt) != "intersection_scene"
        assert detect_workflow(prompt) == "tls_review"


def test_detect_workflow_keeps_negated_signalization_reviews_in_tls_review() -> None:
    assert detect_workflow("Audit why this intersection is not signalized") == "tls_review"
    assert detect_workflow("Review the TLS because this junction is not signalized") == "tls_review"


@pytest.mark.parametrize("near_match", ["traffic-lighting", "tlssuffix", "signalizedness"])
def test_detect_workflow_does_not_treat_signalization_near_matches_as_scene(
    near_match: str,
) -> None:
    assert detect_workflow(f"Build a four-way {near_match} intersection") == "general"


@pytest.mark.parametrize(
    "unsupported_feature",
    [
        "taxi",
        "taxis",
        "all modes",
        "bus",
    ],
)
def test_detect_workflow_shares_scene_feature_limits(unsupported_feature: str) -> None:
    assert detect_workflow(f"Build a four-way TLS intersection with {unsupported_feature} access") == "tls_review"


@pytest.mark.parametrize(
    "supported_feature",
    ["pedestrian", "ped", "bike", "cyclist", "cyclists", "biking", "sidewalk", "sidewalks", "ramp"],
)
def test_detect_workflow_routes_phase_two_scene_features_to_scene_builder(
    supported_feature: str,
) -> None:
    assert detect_workflow(f"Build a four-way TLS intersection with {supported_feature} access") == "intersection_scene"


@pytest.mark.parametrize(
    "prompt",
    [
        "Build a four-way TLS intersection from OSM",
        "Generate a map for a four-way TLS intersection",
        "Create a map for a four-way TLS intersection",
        "Make an OSM four-way TLS intersection",
        "Create an OpenStreetMap four-way TLS intersection",
        "Make an OpenStreetMap four-way TLS intersection",
    ],
)
def test_detect_workflow_prioritizes_osm_generation_over_scene(prompt: str) -> None:
    assert detect_workflow(prompt) == "osm_to_sumo"


@pytest.mark.parametrize("intent", ["compare", "baseline", "fixed-time", "max-pressure"])
def test_detect_workflow_prioritizes_experiments_over_scene(intent: str) -> None:
    assert detect_workflow(f"Build a four-way TLS intersection for a {intent} experiment") == "experiment_audit"


def test_detect_workflow_recognizes_explicit_experiment_intent() -> None:
    assert detect_workflow("Build a four-way TLS intersection experiment") == "experiment_audit"


def test_detect_workflow_prioritizes_tls_review_over_generic_network_review() -> None:
    assert detect_workflow("Review TLS in this SUMO network") == "tls_review"
    assert detect_workflow("Review traffic signals in this SUMO network") == "tls_review"
    assert detect_workflow("Create an HTML review cockpit for this TLS SUMO network") == "network_review"


def test_detect_workflow_does_not_treat_roadmap_as_map_intent() -> None:
    assert detect_workflow("Create a roadmap for later work") == "general"


def test_infer_seed_osm_node_id_requires_node_context() -> None:
    assert infer_seed_osm_node_id("Clean OSM node 98101394 from this bbox") == "98101394"
    assert infer_seed_osm_node_id("clean osm_node_id=1833941950 as the target intersection") == "1833941950"
    assert infer_seed_osm_node_id("Clean this bbox in 2026 for a conference paper") == ""


def test_auto_workflow_ask_first_returns_a_plan_before_place_resolution(tmp_path: Path) -> None:
    def fake_resolver(_place_name: str):
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "area_resolution_status": "candidate_found",
            "candidate_display_name": "Altstadt, Dresden, Sachsen, Deutschland",
            "candidate_osm_type": "relation",
            "candidate_osm_id": "192900",
            "candidate_bbox": "13.6864402,51.0280799,13.7872926,51.0766681",
            "osm_preview_url": "https://www.openstreetmap.org/search?query=Altstadt%2C+Dresden",
            "candidate_osm_url": "https://www.openstreetmap.org/relation/192900",
            "warnings": [],
        }

    report = run_auto_workflow(
        user_request="Use Torii to download the Altstadt map in Dresden from OSM, clean it up and open it in SUMO",
        output_dir=tmp_path,
        autonomy_mode="ask-first",
        place_resolver=fake_resolver,
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"
    assert report["detected_workflow"] == "osm_to_sumo"
    assert report["execution_status"] == "plan-only"
    assert not report.get("candidate_bbox")
    assert report["tool_chain"][:2] == ["sumo_osm_resolve_place", "sumo_osm_cleanup_workflow"]


def test_auto_workflow_safe_autopilot_uses_resolved_bbox_without_confirmation(tmp_path: Path) -> None:
    captured = {}

    def fake_resolver(_place_name: str):
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "area_resolution_status": "candidate_found",
            "candidate_display_name": "Altstadt, Dresden, Sachsen, Deutschland",
            "candidate_osm_type": "relation",
            "candidate_osm_id": "192900",
            "candidate_bbox": "13.6864402,51.0280799,13.7872926,51.0766681",
            "osm_preview_url": "https://www.openstreetmap.org/search?query=Altstadt%2C+Dresden",
            "candidate_osm_url": "https://www.openstreetmap.org/relation/192900",
            "warnings": [],
        }

    def fake_cleanup(**kwargs):
        captured.update(kwargs)
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "net_file": str(tmp_path / "resolved.net.xml"),
            "warnings": ["regional map/TLS reality evidence still needs manual strengthening"],
        }

    report = run_auto_workflow(
        user_request="Use Torii to download the Altstadt map in Dresden from OSM, clean it up and open it in SUMO",
        output_dir=tmp_path,
        traffic_layers="passenger",
        place_resolver=fake_resolver,
        cleanup_workflow_func=fake_cleanup,
    )

    assert report["status"] == "pass"
    assert report["execution_status"] == "executed"
    assert report["tool_called"] == "sumo_osm_cleanup_workflow"
    assert set(captured) == CLEANUP_FIELDS
    assert captured["bbox"] == "13.6864402,51.0280799,13.7872926,51.0766681"
    assert captured["profile"] == "standard"
    assert captured["traffic_layers"] == "passenger"
    assert report["area_resolution_status"] == "candidate_found"


def test_auto_workflow_extracts_bbox_from_osm_map_url(tmp_path: Path) -> None:
    captured = {}

    def fake_cleanup(**kwargs):
        captured.update(kwargs)
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "workflow_review_html_status": "pass",
            "workflow_review_html_file": str(tmp_path / "workflow_review.html"),
        }

    report = run_auto_workflow(
        user_request=(
            "Use Torii-SUMO workflow from "
            "https://www.openstreetmap.org/#map=18/48.768610/11.422681 "
            "to generate a SUMO network"
        ),
        output_dir=tmp_path,
        traffic_layers="passenger",
        cleanup_workflow_func=fake_cleanup,
    )

    parsed = parse_bbox(captured["bbox"])
    assert report["status"] == "pass"
    assert report["tool_called"] == "sumo_osm_cleanup_workflow"
    assert report["area_resolution_status"] == "osm_map_url_bbox"
    assert parsed.west < 11.422681 < parsed.east
    assert parsed.south < 48.768610 < parsed.north
    assert set(captured) == CLEANUP_FIELDS
    assert captured["profile"] == "standard"


def test_auto_workflow_prefers_explicit_bbox_over_prompt_osm_url(tmp_path: Path) -> None:
    captured = {}
    reference_net_file = tmp_path / "reference.net.xml"
    _write_reference_net(reference_net_file)

    def fake_cleanup(**kwargs):
        captured.update(kwargs)
        return {"status": "pass", "claim_status": "diagnostic-demo"}

    report = run_auto_workflow(
        user_request=(
            "Use Torii to clean the Ingolstadt city-center network around "
            "https://www.openstreetmap.org/#map=17/48.765391/11.423800 from OSM"
        ),
        output_dir=tmp_path,
        bbox="11.413800,48.755391,11.433800,48.775391",
        reference_net_file=reference_net_file,
        cleanup_workflow_func=fake_cleanup,
    )

    assert report["status"] == "pass"
    assert set(captured) == CLEANUP_FIELDS
    assert captured["bbox"] == "11.413800,48.755391,11.433800,48.775391"
    assert captured["profile"] == "reference_matched"


def test_auto_workflow_passes_local_osm_file_to_cleanup(tmp_path: Path) -> None:
    captured = {}
    reference_net_file = tmp_path / "reference.net.xml"
    osm_file = tmp_path / "local.osm.xml"
    _write_reference_net(reference_net_file)
    osm_file.write_text("<osm/>", encoding="utf-8")

    def fake_cleanup(**kwargs):
        captured.update(kwargs)
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "net_file": str(tmp_path / "local.net.xml"),
        }

    report = run_auto_workflow(
        user_request="Use Torii to generate a TUM-like SUMO network from this local OSM extract",
        output_dir=tmp_path,
        bbox="11.413800,48.755391,11.433800,48.775391",
        reference_net_file=reference_net_file,
        osm_file=osm_file,
        cleanup_workflow_func=fake_cleanup,
    )

    assert report["status"] == "pass"
    assert report["execution_status"] == "executed"
    assert set(captured) == CLEANUP_FIELDS
    assert captured["source_osm_path"] == osm_file
    assert captured["profile"] == "reference_matched"


def test_auto_workflow_routes_local_osm_intersection_patch_to_intersection_cleaner(tmp_path: Path) -> None:
    captured = {}
    osm_file = tmp_path / "intersection.osm.xml"
    osm_file.write_text("<osm/>", encoding="utf-8")

    def fake_cleanup(**_kwargs):
        raise AssertionError("full OSM cleanup must not run for a local intersection patch")

    def fake_intersection_clean(**kwargs):
        captured.update(kwargs)
        return {
            "status": "pass",
            "claim_status": "intersection-cleaned",
            "intersection_id": "core_1",
            "topology_type": "X4",
            "approach_count": 4,
            "movement_count": 12,
            "net_file": str(tmp_path / "intersection.net.xml"),
            "intersection_ir_file": str(tmp_path / "intersection_ir.json"),
            "validation_file": str(tmp_path / "validation.json"),
            "sumo_load_status": "pass",
            "route_probe_status": "skipped",
            "tls_linkindex_status": "pass",
            "missing_movement_count": 0,
            "disconnected_edge_count": 0,
            "internal_fragment_count": 1,
            "approach_mode_counts": {"passenger": 4, "bicycle": 1},
            "vehicle_approach_count": 4,
            "vehicle_topology_type": "X4",
            "legal_movement_mode_counts": {"passenger": 12},
            "forbidden_cross_mode_movement_count": 8,
        }

    report = run_auto_workflow(
        user_request="Clean this local OSM intersection patch into a SUMO intersection net",
        output_dir=tmp_path,
        osm_file=osm_file,
        cleanup_workflow_func=fake_cleanup,
        intersection_clean_func=fake_intersection_clean,
    )

    assert report["status"] == "pass"
    assert report["detected_workflow"] == "intersection_clean"
    assert report["tool_called"] == "sumo_intersection_clean"
    assert report["execution_status"] == "executed"
    assert report["workflow_result"]["topology_type"] == "X4"
    assert report["sumo_load_status"] == "pass"
    assert report["tls_linkindex_status"] == "pass"
    assert report["missing_movement_count"] == 0
    assert report["approach_mode_counts"] == {"passenger": 4, "bicycle": 1}
    assert report["vehicle_approach_count"] == 4
    assert report["vehicle_topology_type"] == "X4"
    assert report["legal_movement_mode_counts"] == {"passenger": 12}
    assert report["forbidden_cross_mode_movement_count"] == 8
    assert report["workflow_stage_results"][0]["stage_name"] == "intersection_compile_validate"
    assert report["workflow_stage_results"][0]["after_quality"]["connectivity"]["disconnected_edge_count"] == 0
    assert report["workflow_promotion_trace"]["case_id"] == "intersection_clean"
    assert report["workflow_promotion_trace"]["stages"][0]["promotion_decision"] == "pass"
    assert captured["osm_file"] == osm_file
    assert captured["output_dir"] == tmp_path
    assert captured["compile_net"] is True


def test_auto_workflow_runs_synthetic_four_way_tls_scene(tmp_path: Path) -> None:
    prompt = "Generate a four-way signalized intersection"
    calls = {}

    def fake_scene(prompt: str, output_dir: Path, launch_netedit_after_build: bool = False):
        calls.update(
            prompt=prompt,
            output_dir=output_dir,
            launch_netedit_after_build=launch_netedit_after_build,
        )
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "artifact_manifest_file": str(tmp_path / "intersection_scene_artifact_manifest.json"),
            "net_file": str(tmp_path / "intersection_scene.net.xml"),
            "sumocfg_file": str(tmp_path / "intersection_scene.sumocfg"),
            "netconvert_status": "pass",
            "sumo_load_status": "pass",
            "routeability_status": "pass",
            "tls_status": "pass",
            "netedit_status": "not_requested",
        }

    report = run_auto_workflow(
        user_request=prompt,
        output_dir=tmp_path,
        intersection_scene_func=fake_scene,
    )

    assert calls == {
        "prompt": prompt,
        "output_dir": tmp_path,
        "launch_netedit_after_build": False,
    }
    assert report["detected_workflow"] == "intersection_scene"
    assert report["execution_status"] == "executed"
    assert report["tool_called"] == "sumo_intersection_scene_workflow"
    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"
    assert report["artifact_manifest_file"].endswith("_artifact_manifest.json")
    assert report["sumo_load_status"] == "pass"
    assert report["routeability_status"] == "pass"
    assert report["tls_status"] == "pass"
    assert report["workflow_result"]["netconvert_status"] == "pass"


def test_auto_workflow_passes_prompt_seed_to_intersection_cleaner(tmp_path: Path) -> None:
    captured = {}
    osm_file = tmp_path / "intersection.osm.xml"
    osm_file.write_text("<osm/>", encoding="utf-8")

    def fake_intersection_clean(**kwargs):
        captured.update(kwargs)
        return {
            "status": "pass",
            "claim_status": "intersection-cleaned",
            "intersection_id": "core_1",
        }

    report = run_auto_workflow(
        user_request="Clean OSM node 98101394 from this local OSM intersection patch into SUMO",
        output_dir=tmp_path,
        osm_file=osm_file,
        intersection_clean_func=fake_intersection_clean,
    )

    assert report["status"] == "pass"
    assert report["seed_osm_node_id"] == "98101394"
    assert captured["seed"].osm_node_id == "98101394"


def test_auto_workflow_downloads_bbox_for_intersection_clean_when_osm_file_missing(tmp_path: Path) -> None:
    captured_build = {}
    captured_clean = {}
    source_osm = tmp_path / "osm" / "bbox.osm.xml"
    filtered_osm = tmp_path / "osm" / "bbox_filtered.osm.xml"

    def fake_osm_build(**kwargs):
        captured_build.update(kwargs)
        filtered_osm.parent.mkdir(parents=True)
        source_osm.write_text("<osm/>", encoding="utf-8")
        filtered_osm.write_text("<osm/>", encoding="utf-8")
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "source_osm_file": str(source_osm),
            "filtered_osm_file": str(filtered_osm),
        }

    def fake_intersection_clean(**kwargs):
        captured_clean.update(kwargs)
        return {
            "status": "pass",
            "claim_status": "intersection-cleaned",
            "intersection_id": "core_1",
            "topology_type": "X4",
            "approach_count": 4,
            "movement_count": 12,
            "sumo_load_status": "pass",
            "route_probe_status": "skipped",
            "tls_linkindex_status": "pass",
            "missing_movement_count": 0,
            "disconnected_edge_count": 0,
            "internal_fragment_count": 0,
        }

    report = run_auto_workflow(
        user_request="Clean this OSM bbox intersection into a SUMO intersection net",
        output_dir=tmp_path,
        bbox="11.0,48.0,11.001,48.001",
        intersection_osm_build_func=fake_osm_build,
        intersection_clean_func=fake_intersection_clean,
    )

    assert report["status"] == "pass"
    assert report["detected_workflow"] == "intersection_clean"
    assert report["osm_source_build_status"] == "pass"
    assert report["intersection_source_osm_file"] == str(source_osm)
    assert captured_build["bbox"] == "11.0,48.0,11.001,48.001"
    assert captured_build["output_dir"] == tmp_path / "intersection_source"
    assert captured_clean["osm_file"] == source_osm
    assert report["workflow_stage_results"][0]["input_artifacts"]["osm"] == str(source_osm)


def test_auto_workflow_blocks_intersection_clean_without_local_osm_patch(tmp_path: Path) -> None:
    report = run_auto_workflow(
        user_request="Clean this local OSM intersection patch into a SUMO intersection net",
        output_dir=tmp_path,
    )

    assert report["status"] == "blocked"
    assert report["detected_workflow"] == "intersection_clean"
    assert report["execution_status"] == "needs_osm_intersection_patch"
    assert report["missing_blockers"] == ["osm_file_or_bbox"]


def test_auto_workflow_blocks_osm_generation_until_road_level_scope_selected(tmp_path: Path) -> None:
    def fake_resolver(_place_name: str):
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "area_resolution_status": "candidate_found",
            "candidate_display_name": "Altstadt, Dresden, Sachsen, Deutschland",
            "candidate_osm_type": "relation",
            "candidate_osm_id": "192900",
            "candidate_bbox": "13.6864402,51.0280799,13.7872926,51.0766681",
            "osm_preview_url": "https://www.openstreetmap.org/search?query=Altstadt%2C+Dresden",
            "candidate_osm_url": "https://www.openstreetmap.org/relation/192900",
            "warnings": [],
        }

    report = run_auto_workflow(
        user_request="Use Torii to download the Altstadt map in Dresden from OSM, clean it up and open it in SUMO",
        output_dir=tmp_path,
        place_resolver=fake_resolver,
    )

    assert report["status"] == "blocked"
    assert report["claim_status"] == "blocked"
    assert report["execution_status"] == "needs_network_plan"
    assert report["missing_blockers"] == ["network_plan"]
    assert "traffic layers" in report["next_question"]
    assert "reference_matched" in report["network_detail_options"]


def test_auto_workflow_blocks_reference_match_without_reference_artifact(tmp_path: Path) -> None:
    def fake_cleanup(**_kwargs):
        raise AssertionError("cleanup must not run without a reference network or policy report")

    report = run_auto_workflow(
        user_request="Use Torii to build this city-center SUMO network with the same layer policy as a manually cleaned reference network",
        output_dir=tmp_path,
        bbox="11.413800,48.755391,11.433800,48.775391",
        cleanup_workflow_func=fake_cleanup,
    )

    assert report["status"] == "blocked"
    assert report["execution_status"] == "needs_network_plan"
    assert report["network_plan_status"] == "needs_reference_artifact"
    assert report["missing_blockers"] == ["reference_network_or_policy"]


def test_auto_workflow_uses_reference_net_file_for_reference_matched_plan(tmp_path: Path) -> None:
    reference_net_file = tmp_path / "manual-reference.net.xml"
    _write_reference_net(reference_net_file)
    captured = {}

    def fake_cleanup(**kwargs):
        captured.update(kwargs)
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "network_profile": "reference_matched",
            "service_passenger_policy": "reference_match",
            "routeability_audit_status": "pass",
        }

    report = run_auto_workflow(
        user_request="Use Torii to build this city-center SUMO network with the same layer policy as a manually cleaned reference network",
        output_dir=tmp_path,
        bbox="11.413800,48.755391,11.433800,48.775391",
        reference_net_file=reference_net_file,
        cleanup_workflow_func=fake_cleanup,
    )

    assert report["status"] == "pass"
    assert report["execution_status"] == "executed"
    assert set(captured) == CLEANUP_FIELDS
    assert captured["profile"] == "reference_matched"
    assert captured["reference_net_file"] == reference_net_file
    assert captured["traffic_layers"] is None


def test_auto_workflow_resolves_bbox_before_reference_cleanup(tmp_path: Path) -> None:
    reference_net_file = tmp_path / "manual-reference.net.xml"
    _write_reference_net(reference_net_file)
    captured = {}

    def fake_cleanup(**kwargs):
        captured.update(kwargs)
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "network_profile": "reference_matched",
            "candidate_bbox": "11.4062777,48.7483625,11.4382247,48.7803406",
            "reference_bbox_status": "derived_from_reference_geometry",
        }

    def fake_resolver(_place_name: str):
        return {
            "status": "pass",
            "candidate_bbox": "11.4062777,48.7483625,11.4382247,48.7803406",
            "warnings": [],
        }

    report = run_auto_workflow(
        user_request="Clean the Ingolstadt city-center network from OSM, compare it with the TUM cleaned network, and generate a TUM-like SUMO network",
        output_dir=tmp_path,
        place_name="Ingolstadt city center",
        reference_net_file=reference_net_file,
        place_resolver=fake_resolver,
        cleanup_workflow_func=fake_cleanup,
    )

    assert report["status"] == "pass"
    assert report["network_plan"]["network_profile"] == "reference_matched"
    assert set(captured) == CLEANUP_FIELDS
    assert captured["bbox"] == "11.4062777,48.7483625,11.4382247,48.7803406"
    assert captured["profile"] == "reference_matched"
    assert captured["reference_net_file"] == reference_net_file


def test_torii_auto_workflow_uses_cleanup_tool_wrapper(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_run_auto_workflow(**kwargs):
        captured.update(kwargs)
        return {"status": "pass", "claim_status": "diagnostic-demo"}

    monkeypatch.setattr("torii_sumo.tools.workflow_tools.run_auto_workflow", fake_run_auto_workflow)

    report = torii_auto_workflow(
        user_request="Generate a TUM-like SUMO network from OSM using the reference net",
        output_dir=str(tmp_path),
        bbox="11.413800,48.755391,11.433800,48.775391",
        seed_osm_node_id="98101394",
        reference_net_file=str(tmp_path / "reference.net.xml"),
        launch_netedit_after_build=False,
    )

    assert report["status"] == "pass"
    assert captured["cleanup_workflow_func"].__name__ == "sumo_osm_cleanup_workflow"
    assert captured["seed_osm_node_id"] == "98101394"
    assert captured["reference_net_file"] == tmp_path / "reference.net.xml"
    assert captured["launch_netedit_after_build"] is False


def test_auto_workflow_can_call_tls_multisource_review(tmp_path: Path) -> None:
    calls = {}

    def fake_tls_review(**kwargs):
        calls.update(kwargs)
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "tls_candidate_count": 2,
            "needs_manual_review_count": 2,
            "review_file": str(tmp_path / "review.csv"),
            "warnings": ["human review aid"],
        }

    report = run_auto_workflow(
        user_request="Audit the traffic lights in this SUMO network",
        output_dir=tmp_path,
        net_file=Path("network.net.xml"),
        osm_file=Path("network.osm.xml"),
        tls_review_func=fake_tls_review,
    )

    assert report["status"] == "pass"
    assert report["detected_workflow"] == "tls_review"
    assert report["execution_status"] == "executed"
    assert report["tool_called"] == "sumo_tls_multisource_review"
    assert calls["net_file"] == Path("network.net.xml")
    assert calls["osm_file"] == Path("network.osm.xml")


def test_auto_workflow_can_route_partial_network_to_review_html(tmp_path: Path) -> None:
    calls = {}

    def fake_review_html(**kwargs):
        calls.update(kwargs)
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "workflow_review_html_status": "pass",
            "workflow_review_html_file": str(tmp_path / "partial_review.html"),
        }

    report = run_auto_workflow(
        user_request="Create an HTML review cockpit for this partial SUMO network",
        output_dir=tmp_path,
        net_file=Path("partial.net.xml"),
        review_html_func=fake_review_html,
    )

    assert report["status"] == "pass"
    assert report["detected_workflow"] == "network_review"
    assert report["execution_status"] == "executed"
    assert report["tool_called"] == "sumo_network_review_html"
    assert calls["net_file"] == Path("partial.net.xml")
    assert calls["title"] == "SUMO Network Review"


def test_auto_workflow_passes_standard_profile_to_cleanup(tmp_path: Path) -> None:
    captured = {}

    def fake_cleanup(**kwargs):
        captured.update(kwargs)
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "routeability_audit_status": "pass",
        }

    report = run_auto_workflow(
        user_request="Build a SUMO network for Altstadt, Dresden from OSM",
        output_dir=tmp_path,
        bbox="13.6,50.9,13.9,51.1",
        traffic_layers="passenger",
        cleanup_workflow_func=fake_cleanup,
    )

    assert report["status"] == "pass"
    assert set(captured) == CLEANUP_FIELDS
    assert captured["profile"] == "standard"
    assert captured["traffic_layers"] == "passenger"


def test_auto_workflow_inspect_only_returns_plan_without_running_tools(tmp_path: Path) -> None:
    report = run_auto_workflow(
        user_request="Compare fixed-time and max-pressure controllers",
        output_dir=tmp_path,
        autonomy_mode="inspect-only",
    )

    assert report["status"] == "pass"
    assert report["detected_workflow"] == "experiment_audit"
    assert report["execution_status"] == "plan-only"
    assert report["tool_chain"] == ["sumo_config_pair_preflight", "sumo_compare_outputs", "sumo_collect_evidence"]
