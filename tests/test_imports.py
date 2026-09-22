import anyio


EXPECTED_TOOL_NAMES = sorted(
    [
        "sumo_get_environment",
        "sumo_preflight",
        "sumo_config_pair_preflight",
        "sumo_run_config",
        "sumo_run_minimal_smoke",
        "sumo_compare_outputs",
        "sumo_collect_evidence",
        "sumo_netedit_session",
        "sumo_detector_count_audit",
        "sumo_detector_count_constraints",
        "sumo_detector_route_support",
        "sumo_detector_route_sampler_calibrate",
        "sumo_digital_twin_replay_validate",
        "sumo_hamburg_2394_archetype_classify",
        "sumo_hamburg_2394_compound_geometry_first_pass",
        "sumo_hamburg_2394_tls_topology_materialize",
        "sumo_hamburg_sandtorkai_corridor_geometry_materialize",
        "sumo_hamburg_sandtorkai_mainline_scope_materialize",
        "sumo_hamburg_sandtorkai_corridor_tls_materialize",
        "sumo_hamburg_cached_detector_demand",
        "sumo_hamburg_corridor_candidate_detector_demand",
        "sumo_hamburg_corridor_candidate_map_bindings",
        "sumo_hamburg_corridor_candidate_signal_bindings",
        "sumo_hamburg_sandtorkai_corridor_candidate_package",
        "sumo_hamburg_sandtorkai_geometry_safe_digital_twin",
        "sumo_hamburg_official_tls_rebuild",
        "sumo_hamburg_sandtorkai_digital_twin",
        "sumo_hamburg_named_count_scope",
        "sumo_hamburg_sandtorkai_signal_observations",
        "sumo_hamburg_sandtorkai_named_replay",
        "sumo_hamburg_sandtorkai_execution_plan",
        "sumo_network_surface_overlap_audit",
        "sumo_network_surface_overlap_comparison",
        "sumo_intersection_archetype_classify",
        "sumo_intersection_road_sumo_bind",
        "sumo_road_semantic_bridge",
        "sumo_signal_device_profile_classify",
        "sumo_intersection_clean",
        "sumo_intersection_model",
        "sumo_intersection_scene_workflow",
        "sumo_intersection_validate",
        "sumo_nema_four_way_reference_workflow",
        "torii_auto_workflow",
        "sumo_osm_resolve_place",
        "sumo_osm_build_network",
        "sumo_tls_audit",
        "sumo_tls_multisource_review",
        "sumo_network_connected_core",
        "sumo_network_routeability_probe",
        "sumo_network_routeability_audit",
        "sumo_network_connection_mode_audit",
        "sumo_network_connection_mode_calibration",
        "sumo_network_connection_mode_regression_audit",
        "sumo_network_exact_semantic_regression_audit",
        "sumo_network_topology_audit",
        "sumo_network_overlapping_junction_audit",
        "sumo_network_reference_join_audit",
        "sumo_network_reference_hierarchy_audit",
        "sumo_network_corridor_geometry_simplification_variant",
        "sumo_network_corridor_edit_ledger",
        "sumo_network_corridor_candidate_gates",
        "sumo_network_corridor_materialize_variant",
        "sumo_network_reference_scope_audit",
        "sumo_network_junction_aggregation_variant",
        "sumo_network_scope_pruning_variant",
        "sumo_network_teacher_guided_junction_variant",
        "sumo_network_teacher_guided_repair_queue",
        "sumo_network_teacher_corridor_comparison",
        "sumo_network_tls_aggregation_variant",
        "sumo_network_tls_reference_cleanup_variant",
        "sumo_network_standard_nema_phase_binding",
        "sumo_network_tls_warning_parity",
        "sumo_network_review_html",
    ]
)


def test_package_imports() -> None:
    import torii_sumo

    assert torii_sumo.__version__ == "1.1.0"


def test_server_factory_imports() -> None:
    from torii_sumo.server import create_server

    server = create_server()
    assert server is not None


def test_server_registers_expected_tool_names() -> None:
    from torii_sumo.server import create_server

    async def _list_tool_names() -> list[str]:
        server = create_server("legacy")
        tools = await server.list_tools()
        return sorted(tool.name for tool in tools)

    assert anyio.run(_list_tool_names) == EXPECTED_TOOL_NAMES


def test_cli_keeps_osm_cleanup_workflow() -> None:
    from torii_sumo.legacy_tools import WORKFLOW_TOOLS

    assert "sumo_osm_cleanup_workflow" in WORKFLOW_TOOLS


def test_server_describes_narrow_scene_and_conditional_auto_routing() -> None:
    from torii_sumo.server import create_server

    async def _tool_descriptions() -> dict[str, str]:
        tools = await create_server("legacy").list_tools()
        return {tool.name: tool.description or "" for tool in tools}

    descriptions = anyio.run(_tool_descriptions)
    auto = descriptions["torii_auto_workflow"].casefold()
    scene = descriptions["sumo_intersection_scene_workflow"].casefold()

    assert all(term in auto for term in ("workflow_selection", "catalog", "without keyword", "ask-first"))
    assert all(
        term in scene for term in ("synthetic", "passenger-only", "defaulted nema", "not an osm or city-network")
    )


def test_server_smoke_tool_reports_blocked_without_real_sumo(tmp_path) -> None:
    from torii_sumo.server import create_server

    async def _call_minimal_smoke() -> dict[str, object]:
        server = create_server("legacy")
        _content, structured = await server.call_tool(
            "sumo_run_minimal_smoke",
            {
                "work_dir": str(tmp_path),
                "require_real_sumo": False,
            },
        )
        return structured

    result = anyio.run(_call_minimal_smoke)

    assert result["status"] == "blocked"
    assert result["claim_status"] == "blocked"
    assert result["work_dir"] == str(tmp_path)
    assert result["commands"] == []
    assert result["artifacts"] == []


def test_server_netedit_session_exposes_constrained_operation_schema() -> None:
    from torii_sumo.server import create_server

    async def _schema() -> dict[str, object]:
        tools = await create_server("legacy").list_tools()
        tool = next(item for item in tools if item.name == "sumo_netedit_session")
        return tool.inputSchema

    schema = anyio.run(_schema)

    assert schema["properties"]["operation"]["enum"] == [
        "open",
        "observe",
        "act",
        "finalize",
        "abort",
    ]
