from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Mapping

from .network_plan import derive_network_plan
from .osm_area import osm_map_url_bbox, resolve_osm_place
from .osm_network import audit_tls_multisource, build_osm_network
from .osm_workflow import run_osm_cleanup_workflow
from ..intersection.clean import clean_intersection
from ..intersection.scene_spec import resolve_intersection_scene_prompt
from ..intersection.scene_workflow import run_intersection_scene_workflow
from ..intersection.schema import PatchSeed
from .workflow_review_html import build_workflow_review_html
from .workflow_state import NetworkQualityVector, StageResult, build_promotion_trace, summarize_workflow_stages


AUTONOMY_MODES = {"ask-first", "safe-autopilot", "inspect-only", "full-local-run"}


WORKFLOW_RECIPES: dict[str, dict[str, Any]] = {
    "intersection_scene": {
        "description": "Resolve one sentence into a structured spec and build a synthetic signalized 3/4/5-way (including four-way) SUMO scene with optional pedestrian, bicycle, ramp, and richer TLS semantics, then run netconvert, SUMO load, routeability, and artifact-manifest checks.",
        "tool_chain": ["sumo_intersection_scene_workflow"],
    },
    "osm_to_sumo": {
        "description": "Resolve or infer a place/bbox, build an OSM-derived SUMO network with conservative defaults, audit TLS and code-native lane connections, check connectivity, and collect review evidence.",
        "tool_chain": [
            "sumo_osm_resolve_place",
            "sumo_osm_cleanup_workflow",
            "sumo_network_connection_mode_audit",
            "sumo_network_standard_nema_phase_binding",
            "sumo_tls_multisource_review",
            "sumo_network_topology_audit",
            "sumo_network_routeability_probe",
            "sumo_network_routeability_audit",
            "sumo_collect_evidence",
        ],
    },
    "tls_review": {
        "description": "Create a region-aware TLS review table with supporting OSM, street-level imagery, inventory, signal-plan, and field-evidence columns.",
        "tool_chain": [
            "sumo_tls_audit",
            "sumo_tls_multisource_review",
            "sumo_network_connection_mode_audit",
            "sumo_network_standard_nema_phase_binding",
            "sumo_collect_evidence",
        ],
    },
    "network_review": {
        "description": "Create an HTML human-review cockpit for a generated or partial SUMO network and available audit artifacts.",
        "tool_chain": ["sumo_network_review_html", "sumo_collect_evidence"],
    },
    "intersection_clean": {
        "description": "Compile a local or bbox-sourced OSM T3/X4 intersection patch into IntersectionIR, SUMO plain files, .net.xml when available, and validation artifacts.",
        "tool_chain": ["sumo_osm_build_network", "sumo_intersection_clean", "sumo_intersection_validate"],
    },
    "routeability": {
        "description": "Snap named route endpoints to passenger-accessible SUMO edges, generate routes, run a bounded smoke check, and report completion before claims.",
        "tool_chain": ["sumo_network_routeability_probe", "sumo_network_routeability_audit", "sumo_compare_outputs"],
    },
    "debug_bad_run": {
        "description": "Treat bad metrics as model feedback, inspect outputs, classify the likely issue, and propose the smallest next probe.",
        "tool_chain": ["sumo_compare_outputs", "sumo_config_pair_preflight", "sumo_collect_evidence"],
    },
    "experiment_audit": {
        "description": "Audit paired controller comparisons before reporting results.",
        "tool_chain": ["sumo_config_pair_preflight", "sumo_compare_outputs", "sumo_collect_evidence"],
    },
    "general": {
        "description": "Build a project-control screen and ask for the minimum missing SUMO artifact before execution.",
        "tool_chain": ["sumo_preflight", "sumo_collect_evidence"],
    },
}


REFERENCE_MATCHED_TOOL_CHAIN = [
    "sumo_network_reference_hierarchy_audit",
    "sumo_network_reference_scope_audit",
    "sumo_network_reference_join_audit",
    "sumo_network_tls_warning_parity",
    "sumo_network_connection_mode_audit",
    "sumo_network_standard_nema_phase_binding",
    "sumo_network_review_html",
]

REFERENCE_MATCHED_SEMANTICS_WORKFLOW = {
    "claim_status": "diagnostic-demo",
    "reference_policy": "audit the candidate against a manual reference net",
    "junction_policy": "report junction differences for human review",
    "connection_policy": "audit connection, TLS, crossing, walkingarea, and internal-junction parity before adoption",
    "mutation_policy": "audit_only",
    "automatic_repair_enabled": False,
    "warning_parity_tool": "sumo_network_tls_warning_parity",
    "connection_mode_audit_tool": "sumo_network_connection_mode_audit",
    "standard_nema_binding_tool": "sumo_network_standard_nema_phase_binding",
    "required_manual_reviews": [
        "map_or_field_evidence_for_connection_review_findings",
        "map_or_field_imagery",
    ],
}

MANUAL_REVIEW_GATES = {
    "connection_mode_audit",
    "reference_join_audit",
    "reference_hierarchy_audit",
    "reference_scope_audit",
    "topology_audit",
}

OSM_WORKFLOW_SUMMARY_KEYS = (
    "workflow_review_html_status",
    "workflow_review_html_file",
    "workflow_report_file",
    "review_manifest_file",
    "artifact_hash_gate",
    "artifact_hash_gate_status",
    "artifact_hashes",
    "reference_join_audit_status",
    "reference_join_audit_mode",
    "reference_join_audit_report_file",
    "reference_hierarchy_audit_status",
    "reference_hierarchy_audit_report_file",
    "reference_scope_audit_status",
    "reference_scope_audit_report_file",
    "routeability_audit_status",
    "connection_mode_audit_status",
    "connection_mode_audit_pass_count",
    "connection_mode_audit_review_required_count",
    "connection_mode_audit_fail_count",
    "connection_mode_audit_report_file",
    "connection_mode_review_overlay_file",
    "connection_mode_manifest_file",
    "standard_nema_scan_status",
    "standard_nema_eligible_count",
    "standard_nema_review_required_count",
    "standard_nema_report_file",
    "standard_nema_connection_mode_report_file",
    "standard_nema_review_overlay_file",
    "standard_nema_review_html_file",
    "review_decisions_source_file",
    "review_decisions_source_status",
)


def _normalized(value: str) -> str:
    return " ".join(value.lower().split())


def _looks_like_osm_generation(text: str) -> bool:
    network = re.search(r"\b(?:osm|openstreetmap|map|network)\b|\bnet\.xml\b", text)
    strong_generation = re.search(
        r"\b(?:build|download|generate|netconvert)\b|open it in sumo|from osm",
        text,
    )
    create_or_make = re.search(r"\b(?:create|make)\b", text)
    explicit_osm_or_map = re.search(r"\b(?:osm|map|openstreetmap)\b", text)
    return bool(
        network
        and (strong_generation or (create_or_make and explicit_osm_or_map))
    )


def _looks_like_intersection_clean(text: str) -> bool:
    return bool(
        re.search(r"\b(?:intersection|junction|crossroad|t3|x4)\b", text)
        and re.search(
            r"\bpatch\b|\blocal[- ](?:osm|openstreetmap)\b|"
            r"\b(?:osm|openstreetmap)[- ](?:file|extract|bbox)\b|\bbbox\b",
            text,
        )
        and re.search(r"\b(?:patch|clean|compile|generate|build|create|make)\b", text)
    )


def _looks_like_intersection_scene(text: str) -> bool:
    if not re.search(r"\b(?:build|generate|create|make)\b", text) or re.search(
        r"\b(?:audit|review|repair|clean)\b", text
    ):
        return False
    try:
        resolve_intersection_scene_prompt(text)
    except ValueError:
        return False
    return True


def detect_workflow(user_request: str) -> str:
    text = _normalized(user_request)
    if any(token in text for token in ("waiting time", "got worse", "teleport", "tripinfo", "summary disagree", "debug")):
        return "debug_bad_run"
    if _looks_like_intersection_clean(text):
        return "intersection_clean"
    if _looks_like_osm_generation(text):
        return "osm_to_sumo"
    if re.search(r"\b(?:compare|baseline|experiment|fixed[- ]time|max[- ]pressure|controllers?)\b", text):
        return "experiment_audit"
    if _looks_like_intersection_scene(text):
        return "intersection_scene"
    tls_signal = re.search(
        r"\b(?:traffic[- ]lights?|tls|signals?|signalized|unsignalized)\b", text
    )
    network_review = (
        any(token in text for token in ("html", "review cockpit", "human review", "review"))
        and any(token in text for token in ("sumo network", "partial sumo network", "network", ".net.xml", "net.xml"))
    )
    if network_review and re.search(r"\bhtml\b|\breview cockpit\b", text):
        return "network_review"
    if tls_signal and re.search(r"\b(?:audit|review)\b", text):
        return "tls_review"
    if network_review:
        return "network_review"
    if tls_signal:
        return "tls_review"
    if re.search(
        r"\b(?:osm|openstreetmap|map|network|netconvert)\b|"
        r"\bopen it in sumo\b|\bbuild a sumo\b",
        text,
    ):
        return "osm_to_sumo"
    if any(token in text for token in ("route", "from ", " to ", "connected", "routeability", "reachable")):
        return "routeability"
    return "general"


def infer_seed_osm_node_id(user_request: str) -> str:
    text = " ".join(user_request.strip().split())
    patterns = [
        r"\bosm_node_id\s*[:=]\s*(?P<id>-?\d+)\b",
        r"\bOSM\s+node\s+(?P<id>-?\d+)\b",
        r"\bnode\s+(?P<id>-?\d+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group("id")
    return ""


def infer_place_name(user_request: str) -> str:
    text = " ".join(user_request.strip().split())
    patterns = [
        r"download\s+the\s+(?P<area>.+?)\s+map\s+in\s+(?P<city>.+?)\s+from\s+OSM",
        r"build\s+(?:a\s+)?SUMO\s+network\s+for\s+(?P<area>.+?)\s+from\s+OSM",
        r"download\s+(?P<area>.+?)\s+from\s+OSM",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        area = match.groupdict().get("area", "").strip(" .,")
        city = match.groupdict().get("city", "").strip(" .,")
        if area and city:
            return f"{area}, {city}"
        if area:
            return area
    return ""


def _base_report(
    *,
    user_request: str,
    detected_workflow: str,
    autonomy_mode: str,
) -> dict[str, Any]:
    recipe = WORKFLOW_RECIPES[detected_workflow]
    return {
        "detected_workflow": detected_workflow,
        "workflow_description": recipe["description"],
        "autonomy_mode": autonomy_mode,
        "user_request": user_request,
        "tool_chain": list(recipe["tool_chain"]),
    }


def _annotate_reference_matched_semantics(report: dict[str, Any], workflow_report: Mapping[str, Any] | None = None) -> None:
    for tool_name in REFERENCE_MATCHED_TOOL_CHAIN:
        if tool_name not in report["tool_chain"]:
            report["tool_chain"].append(tool_name)
    semantics = {
        **REFERENCE_MATCHED_SEMANTICS_WORKFLOW,
        "tool_chain": list(REFERENCE_MATCHED_TOOL_CHAIN),
    }
    if workflow_report is not None:
        semantics["required_manual_reviews"] = _required_manual_reviews_from_gates(workflow_report.get("gate_status"))
    report["reference_matched_semantics_workflow"] = semantics


def _required_manual_reviews_from_gates(gate_status: Any) -> list[str]:
    if not isinstance(gate_status, Mapping):
        return list(REFERENCE_MATCHED_SEMANTICS_WORKFLOW["required_manual_reviews"])
    reviews: list[str] = []
    for gate, status in sorted(gate_status.items()):
        normalized_status = str(status)
        if gate == "connection_mode_audit" and normalized_status == "review_required":
            reviews.append("map_or_field_evidence_for_connection_review_findings")
        elif gate in MANUAL_REVIEW_GATES and normalized_status in {
            "blocked",
            "review_required",
        }:
            reviews.append(str(gate))
    return reviews or list(REFERENCE_MATCHED_SEMANTICS_WORKFLOW["required_manual_reviews"])


def _invalid_mode(user_request: str, autonomy_mode: str) -> dict[str, Any]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "detected_workflow": detect_workflow(user_request),
        "execution_status": "invalid_autonomy_mode",
        "error": f"autonomy_mode must be one of: {', '.join(sorted(AUTONOMY_MODES))}",
    }


def _plan_only(user_request: str, workflow: str, autonomy_mode: str) -> dict[str, Any]:
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "execution_status": "plan-only",
        **_base_report(user_request=user_request, detected_workflow=workflow, autonomy_mode=autonomy_mode),
    }


def _blocked(report: dict[str, Any], *, execution_status: str, missing: list[str], next_question: str) -> dict[str, Any]:
    report.update(
        {
            "status": "blocked",
            "claim_status": "blocked",
            "execution_status": execution_status,
            "missing_blockers": missing,
            "next_question": next_question,
        }
    )
    return report


def run_auto_workflow(
    *,
    user_request: str,
    output_dir: Path,
    work_dir: Path | None = None,
    autonomy_mode: str = "safe-autopilot",
    place_name: str | None = None,
    bbox: str | None = None,
    confirmed_area: bool = False,
    traffic_layers: str | None = None,
    timeout_seconds: float = 240.0,
    seed_osm_node_id: str | None = None,
    reference_net_file: Path | None = None,
    launch_netedit_after_build: bool | None = None,
    net_file: Path | None = None,
    osm_file: Path | None = None,
    official_inventory_csv: Path | None = None,
    signal_plan_csv: Path | None = None,
    field_evidence_csv: Path | None = None,
    workflow_selection: dict[str, Any] | None = None,
    place_resolver: Callable[[str], dict[str, Any]] = resolve_osm_place,
    cleanup_workflow_func: Callable[..., dict[str, Any]] = run_osm_cleanup_workflow,
    intersection_scene_func: Callable[..., dict[str, Any]] = run_intersection_scene_workflow,
    intersection_clean_func: Callable[..., dict[str, Any]] = clean_intersection,
    intersection_osm_build_func: Callable[..., dict[str, Any]] = build_osm_network,
    tls_review_func: Callable[..., dict[str, Any]] = audit_tls_multisource,
    review_html_func: Callable[..., dict[str, Any]] = build_workflow_review_html,
) -> dict[str, Any]:
    if workflow_selection is not None:
        from .workflow_catalog import run_selected_workflow
        if autonomy_mode not in AUTONOMY_MODES:
            return {'status': 'blocked', 'executed': False, 'error': 'Invalid autonomy_mode.'}
        if not isinstance(workflow_selection, dict) or workflow_selection.get('user_request', user_request) != user_request:
            return {'status': 'blocked', 'executed': False, 'error': 'Selection must preserve the original user request.'}
        return run_selected_workflow({**workflow_selection, 'user_request': user_request},
                                     execute=autonomy_mode not in {'inspect-only', 'ask-first'})
    if autonomy_mode not in AUTONOMY_MODES:
        return _invalid_mode(user_request, autonomy_mode)

    workflow = detect_workflow(user_request)
    if autonomy_mode in {"inspect-only", "ask-first"}:
        return _plan_only(user_request, workflow, autonomy_mode)

    report = _base_report(user_request=user_request, detected_workflow=workflow, autonomy_mode=autonomy_mode)
    report["output_dir"] = str(output_dir)
    if work_dir is not None:
        report["work_dir"] = str(work_dir)

    if workflow == "intersection_scene":
        result = intersection_scene_func(
            user_request,
            output_dir,
            launch_netedit_after_build=bool(launch_netedit_after_build),
        )
        report.update(
            {
                "status": result.get("status", "fail"),
                "claim_status": result.get("claim_status", "blocked"),
                "execution_status": "executed",
                "tool_called": "sumo_intersection_scene_workflow",
                "workflow_result": result,
            }
        )
        for key in (
            "artifact_manifest_file",
            "artifact_hash_gate",
            "artifact_hash_gate_status",
            "artifact_hashes",
            "net_file",
            "sumocfg_file",
            "netconvert_status",
            "sumo_load_status",
            "routeability_status",
            "tls_status",
            "netedit_status",
            "resolved_spec",
            "warnings",
        ):
            if key in result:
                report[key] = result[key]
        return report

    if workflow == "osm_to_sumo":
        return _run_osm_to_sumo(
            report=report,
            user_request=user_request,
            output_dir=output_dir,
            place_name=place_name,
            bbox=bbox,
            confirmed_area=confirmed_area,
            traffic_layers=traffic_layers,
            timeout_seconds=timeout_seconds,
            reference_net_file=reference_net_file,
            source_osm_path=osm_file,
            autonomy_mode=autonomy_mode,
            place_resolver=place_resolver,
            cleanup_workflow_func=cleanup_workflow_func,
        )
    if workflow == "intersection_clean":
        return _run_intersection_clean(
            report=report,
            user_request=user_request,
            output_dir=output_dir,
            bbox=bbox,
            place_name=place_name,
            osm_file=osm_file,
            seed_osm_node_id=seed_osm_node_id,
            intersection_clean_func=intersection_clean_func,
            intersection_osm_build_func=intersection_osm_build_func,
        )
    if workflow == "tls_review":
        return _run_tls_review(
            report=report,
            output_dir=output_dir,
            net_file=net_file,
            osm_file=osm_file,
            official_inventory_csv=official_inventory_csv,
            signal_plan_csv=signal_plan_csv,
            field_evidence_csv=field_evidence_csv,
            tls_review_func=tls_review_func,
        )
    if workflow == "network_review":
        return _run_network_review(
            report=report,
            output_dir=output_dir,
            net_file=net_file,
            review_html_func=review_html_func,
        )
    if workflow == "routeability":
        return _blocked(
            report,
            execution_status="needs_route_endpoints",
            missing=["net_file", "route_endpoint_spec"],
            next_question="Which origin and destination should Torii snap to passenger-accessible SUMO edges?",
        )
    if workflow == "debug_bad_run":
        return _blocked(
            report,
            execution_status="needs_outputs",
            missing=["summary_or_tripinfo_or_log"],
            next_question="Which SUMO summary, tripinfo, log, or config should Torii inspect first?",
        )
    if workflow == "experiment_audit":
        return _blocked(
            report,
            execution_status="needs_experiment_pair",
            missing=["baseline_config_or_outputs", "variant_config_or_outputs"],
            next_question="Which baseline and variant configs or outputs should Torii audit as a paired comparison?",
        )
    return _blocked(
        report,
        execution_status="needs_sumo_artifact",
        missing=["sumo_config_or_network_or_outputs"],
        next_question="Which SUMO config, network, route, output, or log should Torii inspect?",
    )


def _run_intersection_clean(
    *,
    report: dict[str, Any],
    user_request: str,
    output_dir: Path,
    bbox: str | None,
    place_name: str | None,
    osm_file: Path | None,
    seed_osm_node_id: str | None,
    intersection_clean_func: Callable[..., dict[str, Any]],
    intersection_osm_build_func: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    if osm_file is None:
        source_bbox = (bbox or "").strip()
        url_bbox = osm_map_url_bbox(source_bbox) or osm_map_url_bbox(place_name or "") or osm_map_url_bbox(user_request)
        if url_bbox:
            source_bbox = url_bbox
            report["area_resolution_status"] = "osm_map_url_bbox"
            report["candidate_bbox"] = url_bbox
        if not source_bbox:
            return _blocked(
                report,
                execution_status="needs_osm_intersection_patch",
                missing=["osm_file_or_bbox"],
                next_question="Which local OSM intersection patch or bbox should Torii compile?",
            )
        source_build = intersection_osm_build_func(
            bbox=source_bbox,
            output_dir=output_dir / "intersection_source",
            prefix="intersection_source",
        )
        report["osm_source_build_status"] = source_build.get("status", "fail")
        report["osm_source_build_result"] = source_build
        source_osm = source_build.get("source_osm_file") or source_build.get("filtered_osm_file")
        if source_build.get("status") != "pass" or not source_osm:
            report.update(
                {
                    "status": source_build.get("status", "fail"),
                    "claim_status": source_build.get("claim_status", "construction-invalid"),
                    "execution_status": "osm_source_build_failed",
                    "tool_called": "sumo_osm_build_network",
                }
            )
            return report
        osm_file = Path(str(source_osm))
        report["intersection_source_osm_file"] = str(osm_file)
    effective_seed_osm_node_id = (seed_osm_node_id or infer_seed_osm_node_id(user_request)).strip()
    seed = PatchSeed(osm_node_id=effective_seed_osm_node_id) if effective_seed_osm_node_id else None
    if effective_seed_osm_node_id:
        report["seed_osm_node_id"] = effective_seed_osm_node_id
    result = intersection_clean_func(osm_file=osm_file, output_dir=output_dir, compile_net=True, seed=seed)
    report.update(
        {
            "status": result.get("status", "fail"),
            "claim_status": result.get("claim_status", "diagnostic-demo"),
            "execution_status": "executed",
            "tool_called": "sumo_intersection_clean",
            "workflow_result": result,
        }
    )
    for key in (
        "intersection_id",
        "topology_type",
        "approach_count",
        "movement_count",
        "sumo_load_status",
        "route_probe_status",
        "tls_linkindex_status",
        "missing_movement_count",
        "disconnected_edge_count",
        "internal_fragment_count",
        "approach_mode_counts",
        "vehicle_approach_count",
        "vehicle_topology_type",
        "legal_movement_mode_counts",
        "forbidden_cross_mode_movement_count",
        "net_file",
        "intersection_ir_file",
        "validation_file",
    ):
        if key in result:
            report[key] = result[key]
    stage = StageResult(
        stage_name="intersection_compile_validate",
        status=str(result.get("status", "fail")),
        input_artifacts={"osm": str(osm_file)},
        output_artifacts={
            key: str(result[key])
            for key in ("net_file", "intersection_ir_file", "validation_file")
            if result.get(key)
        },
        after_quality=NetworkQualityVector(
            connectivity={
                "missing_movement_count": result.get("missing_movement_count", 0),
                "disconnected_edge_count": result.get("disconnected_edge_count", 0),
            },
            routeability={"route_probe_status": result.get("route_probe_status", "skipped")},
            topology_fragmentation={"internal_fragment_count": result.get("internal_fragment_count", 0)},
            tls_semantic_delta={"tls_linkindex_status": result.get("tls_linkindex_status", "skipped")},
        ),
        promotion_decision=str(result.get("status", "fail")),
        claim_status=str(result.get("claim_status", "diagnostic-demo")),
        evidence_files=[str(result[key]) for key in ("intersection_ir_file", "validation_file") if result.get(key)],
        warnings=list(result.get("warnings", [])) if isinstance(result.get("warnings"), list) else [],
    )
    report["workflow_stage_results"] = [stage.as_dict()]
    report["workflow_promotion_trace"] = build_promotion_trace(
        case_id="intersection_clean",
        claim_status=str(result.get("claim_status", "diagnostic-demo")),
        source_artifact=str(result.get("validation_file", "")),
        stages=[stage],
    )
    return report


def _run_osm_to_sumo(
    *,
    report: dict[str, Any],
    user_request: str,
    output_dir: Path,
    place_name: str | None,
    bbox: str | None,
    confirmed_area: bool,
    traffic_layers: str | None,
    timeout_seconds: float,
    reference_net_file: Path | None,
    source_osm_path: Path | None,
    autonomy_mode: str,
    place_resolver: Callable[[str], dict[str, Any]],
    cleanup_workflow_func: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    explicit_bbox = (bbox or "").strip()
    bbox = explicit_bbox or None
    url_bbox = osm_map_url_bbox(explicit_bbox)
    if not explicit_bbox:
        url_bbox = osm_map_url_bbox(place_name or "") or osm_map_url_bbox(user_request)
    if url_bbox:
        bbox = url_bbox
        report["area_resolution_status"] = "osm_map_url_bbox"
        report["candidate_bbox"] = url_bbox

    inferred = (place_name or "").strip() or infer_place_name(user_request)
    if inferred:
        report["inferred_place_name"] = inferred

    if not bbox and not inferred:
        return _blocked(
            report,
            execution_status="needs_area",
            missing=["place_name_or_bbox"],
            next_question="Which OSM place name or bbox should Torii use?",
        )
    if not bbox:
        candidate = place_resolver(inferred)
        report.update(candidate)
        resolved_bbox = str(candidate.get("candidate_bbox", ""))
        if candidate.get("status") != "pass" or not resolved_bbox:
            report["execution_status"] = "needs_user_confirmation"
            report["status"] = "blocked"
            report["claim_status"] = "blocked"
            report["next_question"] = "Which OSM area or bbox should Torii use?"
            return report
        if autonomy_mode == "ask-first" and not confirmed_area:
            report["execution_status"] = "needs_user_confirmation"
            report["status"] = "blocked"
            report["claim_status"] = "blocked"
            report["next_question"] = "Confirm this OSM area and bbox before network construction?"
            return report
        bbox = resolved_bbox
        report["execution_status"] = "auto_area_candidate"

    network_plan = derive_network_plan(
        user_request=user_request,
        traffic_layers=traffic_layers,
        network_profile="reference_matched" if reference_net_file is not None else None,
        reference_net_file=reference_net_file,
    )
    if network_plan.get("status") == "blocked":
        report.update(network_plan)
        if network_plan.get("network_profile") == "reference_matched":
            _annotate_reference_matched_semantics(report)
        report["execution_status"] = "needs_network_plan"
        return report
    if network_plan.get("status") != "pass":
        report.update(network_plan)
        if network_plan.get("network_profile") == "reference_matched":
            _annotate_reference_matched_semantics(report)
        report["status"] = "fail"
        report["claim_status"] = "construction-invalid"
        report["execution_status"] = "network_plan_failed"
        return report
    if network_plan.get("network_profile") == "reference_matched":
        _annotate_reference_matched_semantics(report)
    reference_matched = reference_net_file is not None
    workflow_report = cleanup_workflow_func(
        output_dir=output_dir,
        bbox=bbox,
        profile="reference_matched" if reference_matched else "standard",
        source_osm_path=source_osm_path,
        traffic_layers=None if reference_matched else ",".join(network_plan.get("movement_layers", [])),
        reference_net_file=reference_net_file,
        timeout_seconds=timeout_seconds,
    )
    report.update(
        {
            "status": workflow_report.get("status", "fail"),
            "claim_status": workflow_report.get("claim_status", "diagnostic-demo"),
            "execution_status": "executed",
            "tool_called": "sumo_osm_cleanup_workflow",
            "network_plan": network_plan,
            "workflow_result": workflow_report,
        }
    )
    for key in OSM_WORKFLOW_SUMMARY_KEYS:
        if key in workflow_report:
            report[key] = workflow_report[key]
    stage_results = summarize_workflow_stages(workflow_report)
    if stage_results:
        report["workflow_stage_results"] = [stage.as_dict() for stage in stage_results]
        report["workflow_promotion_trace"] = build_promotion_trace(
            case_id=str(network_plan.get("network_profile") or "osm_to_sumo"),
            claim_status=str(workflow_report.get("claim_status") or "diagnostic-demo"),
            source_artifact=str(workflow_report.get("workflow_report_file") or ""),
            stages=stage_results,
        )
    if network_plan.get("network_profile") == "reference_matched":
        _annotate_reference_matched_semantics(report, workflow_report)
    return report
def _run_tls_review(
    *,
    report: dict[str, Any],
    output_dir: Path,
    net_file: Path | None,
    osm_file: Path | None,
    official_inventory_csv: Path | None,
    signal_plan_csv: Path | None,
    field_evidence_csv: Path | None,
    tls_review_func: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    if net_file is None:
        return _blocked(
            report,
            execution_status="needs_network",
            missing=["net_file"],
            next_question="Which SUMO .net.xml should Torii use for the TLS review?",
        )
    review = tls_review_func(
        net_file=net_file,
        output_dir=output_dir,
        osm_file=osm_file,
        official_inventory_csv=official_inventory_csv,
        signal_plan_csv=signal_plan_csv,
        field_evidence_csv=field_evidence_csv,
    )
    report.update(
        {
            "status": review.get("status", "fail"),
            "claim_status": review.get("claim_status", "diagnostic-demo"),
            "execution_status": "executed",
            "tool_called": "sumo_tls_multisource_review",
            "workflow_result": review,
        }
    )
    return report


def _run_network_review(
    *,
    report: dict[str, Any],
    output_dir: Path,
    net_file: Path | None,
    review_html_func: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    if net_file is None:
        return _blocked(
            report,
            execution_status="needs_network",
            missing=["net_file"],
            next_question="Which partial SUMO .net.xml should Torii use for the HTML review cockpit?",
        )
    review = review_html_func(
        output_dir=output_dir,
        net_file=net_file,
        title="SUMO Network Review",
        claim_status="diagnostic-demo",
    )
    report.update(
        {
            "status": review.get("status", "fail"),
            "claim_status": review.get("claim_status", "diagnostic-demo"),
            "execution_status": "executed",
            "tool_called": "sumo_network_review_html",
            "workflow_result": review,
        }
    )
    return report
