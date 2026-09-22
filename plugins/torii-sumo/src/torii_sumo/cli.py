"""Small argparse-based Torii command line interface.

The CLI is intentionally a thin entry layer over the same business functions
used by the MCP tools.  It does not call the MCP server and the MCP server does
not spawn this CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import anyio

from .server import create_server
from .mcp_contract_tools import (
    ToriiToolResult,
    torii_config_inspect,
    torii_demand_audit,
    torii_intersection_classify,
    torii_netedit_close,
    torii_netedit_observe,
    torii_netedit_open,
    torii_network_audit,
    torii_network_compare,
    torii_place_resolve,
    torii_preflight,
    torii_review_create,
    torii_run_compare,
    torii_signal_classify,
)
from .tools.osm_tools import sumo_network_routeability_audit
from .core.movement_routeability import run_candidate_movement_probes
from .core.hamburg_aerial_movement import build_hamburg_aerial_corridor_plan
from .core.hamburg_aerial_corridor_candidate import (
    build_hamburg_aerial_combined_candidate,
)
from .core.hamburg_aerial_signal import (
    build_hamburg_aerial_signal_binding,
    build_protected_signal_candidate_from_request,
)
from .core.hamburg_aerial_count import build_hamburg_aerial_count_binding
from .core.hamburg_aerial_demand import generate_hamburg_aerial_demand
from .core.hamburg_lane_connection_repair import (
    build_hamburg_lane_connection_repair,
)


_WORKFLOW_TOOL_NAMES = (
    "hamburg_corridor_bind_tls_clusters",
    "hamburg_corridor_select",
    "sumo_run_config",
    "sumo_run_minimal_smoke",
    "sumo_collect_evidence",
    "sumo_osm_build_network",
    "sumo_osm_cleanup_workflow",
    "sumo_tls_audit",
    "sumo_tls_multisource_review",
    "sumo_network_connected_core",
    "sumo_network_routeability_probe",
    "sumo_network_routeability_audit",
    "sumo_network_connection_mode_audit",
    "sumo_network_connection_mode_calibration",
    "sumo_network_exact_semantic_regression_audit",
    "sumo_network_overlapping_junction_audit",
    "sumo_network_reference_join_audit",
    "sumo_network_reference_hierarchy_audit",
    "sumo_network_reference_scope_audit",
    "sumo_network_tls_warning_parity",
    "sumo_network_surface_overlap_audit",
    "sumo_network_surface_overlap_comparison",
    "sumo_network_junction_aggregation_variant",
    "sumo_network_scope_pruning_variant",
    "sumo_network_corridor_geometry_simplification_variant",
    "sumo_network_corridor_edit_ledger",
    "sumo_network_corridor_materialize_variant",
    "sumo_network_corridor_candidate_gates",
    "sumo_network_teacher_corridor_comparison",
    "sumo_network_tls_reference_cleanup_variant",
    "sumo_network_standard_nema_phase_binding",
    "sumo_network_teacher_guided_junction_variant",
    "sumo_network_teacher_guided_repair_queue",
    "sumo_network_tls_aggregation_variant",
    "sumo_intersection_model",
    "sumo_intersection_clean",
    "sumo_intersection_validate",
    "sumo_nema_four_way_reference_workflow",
    "sumo_intersection_scene_workflow",
    "sumo_road_semantic_bridge",
    "sumo_intersection_road_sumo_bind",
    "sumo_detector_route_support",
    "sumo_detector_count_constraints",
    "sumo_detector_route_sampler_calibrate",
    "sumo_hamburg_sandtorkai_digital_twin",
    "sumo_hamburg_named_count_scope",
    "sumo_hamburg_named_detector_bindings",
    "sumo_hamburg_sandtorkai_signal_observations",
    "sumo_hamburg_sandtorkai_named_replay",
    "sumo_hamburg_sandtorkai_execution_plan",
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
    "sumo_digital_twin_replay_validate",
)

def _exit_code(result: ToriiToolResult | dict[str, Any]) -> int:
    status = result.get("status") if isinstance(result, dict) else result.status
    if not isinstance(status, str) or not status.strip():
        return 3
    status = status.strip().lower()
    if status in {"pass", "ok", "success", "complete", "ready"}:
        return 0
    if status in {"error", "fail", "failed", "invalid", "timeout"} or (
        status.startswith("blocked")
        or status.endswith(("_error", "-error", "_failed", "-failed"))
    ):
        return 3
    return 1


def _emit(result: ToriiToolResult | dict[str, Any], *, json_output: bool) -> int:
    if json_output:
        if isinstance(result, ToriiToolResult):
            print(result.model_dump_json(indent=2))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if isinstance(result, ToriiToolResult):
            print(f"{result.status}: {result.summary}")
            if result.next_actions:
                for action in result.next_actions:
                    print(f"next: {action}")
        else:
            print(str(result))
    return _exit_code(result)


def _add_output_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit a structured JSON result.",
    )


def _load_request(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("workflow request must be a JSON object")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="torii")
    parser.add_argument("--json", action="store_true", help="Emit a structured JSON result for every subcommand.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="Check Python, SUMO, and Torii environment.")
    _add_output_argument(preflight)

    config = subparsers.add_parser("config", help="Inspect config pairs.")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    inspect = config_sub.add_parser("inspect", help="Inspect a baseline and variant .sumocfg pair.")
    inspect.add_argument("baseline_config")
    inspect.add_argument("variant_config")
    _add_output_argument(inspect)

    run = subparsers.add_parser("run", help="Run bounded SUMO comparisons.")
    run_sub = run.add_subparsers(dest="run_command", required=True)
    compare = run_sub.add_parser("compare", help="Compare baseline and variant outputs.")
    compare.add_argument("--baseline-summary")
    compare.add_argument("--baseline-tripinfo")
    compare.add_argument("--variant-summary")
    compare.add_argument("--variant-tripinfo")
    _add_output_argument(compare)

    place = subparsers.add_parser("place", help="Resolve OSM places.")
    place_sub = place.add_subparsers(dest="place_command", required=True)
    resolve = place_sub.add_parser("resolve", help="Resolve a place name to a candidate OSM area.")
    resolve.add_argument("place_name")
    resolve.add_argument("--limit", type=int, default=1)
    _add_output_argument(resolve)

    intersection = subparsers.add_parser("intersection", help="Intersection classification.")
    intersection_sub = intersection.add_subparsers(dest="intersection_command", required=True)
    classify = intersection_sub.add_parser("classify", help="Classify one local OSM intersection.")
    classify.add_argument("osm_file")
    classify.add_argument("seed_osm_node_id")
    classify.add_argument("--traffic-side", default="right", choices=("left", "right"))
    classify.add_argument("--road-network-evidence-file")
    _add_output_argument(classify)

    signal = subparsers.add_parser("signal", help="Signal inventory classification.")
    signal_sub = signal.add_subparsers(dest="signal_command", required=True)
    signal_classify = signal_sub.add_parser("classify", help="Classify one OCIT-C signal inventory.")
    signal_classify.add_argument("ocit_file")
    signal_classify.add_argument("--expected-node-id")
    _add_output_argument(signal_classify)

    network = subparsers.add_parser("network", help="Network audits and comparisons.")
    network_sub = network.add_subparsers(dest="network_command", required=True)
    audit = network_sub.add_parser("audit", help="Audit one local SUMO network.")
    audit.add_argument("net_file")
    audit.add_argument("output_dir")
    audit.add_argument("--profile", default="standard", choices=("quick", "standard"))
    audit.add_argument("--prefix", default="network_audit")
    _add_output_argument(audit)
    network_compare = network_sub.add_parser("compare", help="Compare source and candidate networks.")
    network_compare.add_argument("source_net_file")
    network_compare.add_argument("candidate_net_file")
    network_compare.add_argument("output_dir")
    network_compare.add_argument("--prefix", default="network_compare")
    _add_output_argument(network_compare)
    routeability = network_sub.add_parser(
        "routeability",
        help="Run the long completion-aware SUMO routeability audit outside MCP.",
    )
    routeability.add_argument("net_file")
    routeability.add_argument("output_dir")
    routeability.add_argument("--prefix", default="routeability_audit")
    routeability.add_argument("--vehicle-count", type=int, default=100)
    routeability.add_argument("--seed", type=int, default=42)
    routeability.add_argument("--initial-end", type=int, default=300)
    routeability.add_argument("--max-end", type=int, default=2400)
    routeability.add_argument("--timeout-seconds", type=float, default=240.0)
    _add_output_argument(routeability)
    movements = network_sub.add_parser("movement-probes", help="Test every declared official movement through its full internal lane chain.")
    movements.add_argument("candidate_manifest")
    movements.add_argument("output_dir")
    movements.add_argument("--sumo-binary", default="sumo")
    movements.add_argument("--seed", type=int, default=104)
    movements.add_argument("--end", type=int, default=600)
    movements.add_argument("--timeout-seconds", type=float, default=120.0)
    _add_output_argument(movements)

    demand = subparsers.add_parser("demand", help="Demand and detector audits.")
    demand_sub = demand.add_subparsers(dest="demand_command", required=True)
    audit_counts = demand_sub.add_parser("audit", help="Compare expected and observed detector counts.")
    audit_counts.add_argument("expected_counts_csv")
    audit_counts.add_argument("detector_output_xml")
    audit_counts.add_argument("output_dir")
    audit_counts.add_argument("--prefix", default="demand_audit")
    _add_output_argument(audit_counts)

    review = subparsers.add_parser("review", help="Create review artifacts.")
    review_sub = review.add_subparsers(dest="review_command", required=True)
    create = review_sub.add_parser("create", help="Create a network review HTML page.")
    create.add_argument("output_dir")
    create.add_argument("--net-file")
    create.add_argument("--title", default="SUMO Network Review")
    create.add_argument("--claim-status", default="diagnostic-demo")
    create.add_argument("--raw-net-file")
    create.add_argument("--connected-core-file")
    create.add_argument("--tls-review-file")
    create.add_argument("--topology-audit-report-file")
    create.add_argument("--junction-aggregation-report-file")
    create.add_argument("--routeability-audit-report-file")
    _add_output_argument(create)

    hamburg = subparsers.add_parser("hamburg", help="Hamburg official-data workflows.")
    hamburg_sub = hamburg.add_subparsers(dest="hamburg_command", required=True)
    topology = hamburg_sub.add_parser(
        "build-network",
        help="Rebuild and check a road network from OSM, MAP/KML, and aerial images without count calibration.",
    )
    topology.add_argument("request_file")
    topology.add_argument("output_dir")
    _add_output_argument(topology)
    road_uses = hamburg_sub.add_parser(
        "inspect-road-uses", help="Review local road-use evidence without changing the network.",
    )
    road_uses.add_argument("request_file")
    road_uses.add_argument("output_dir")
    _add_output_argument(road_uses)
    aerial_movements = hamburg_sub.add_parser(
        "aerial-movements",
        help="Select official or aerial-traced movement geometry from a hash-bound request.",
    )
    aerial_movements.add_argument("request_file")
    aerial_movements.add_argument("output_dir")
    _add_output_argument(aerial_movements)
    combined_aerial = hamburg_sub.add_parser(
        "combine-aerial-movements",
        help="Build and test a separate SUMO corridor candidate from selected movements.",
    )
    combined_aerial.add_argument("request_file")
    combined_aerial.add_argument("output_dir")
    _add_output_argument(combined_aerial)
    aerial_signals = hamburg_sub.add_parser(
        "bind-aerial-signals",
        help="Bind exact official primary-signal identities to an aerial candidate.",
    )
    aerial_signals.add_argument("request_file")
    aerial_signals.add_argument("output_dir")
    _add_output_argument(aerial_signals)
    protected_signals = hamburg_sub.add_parser(
        "build-protected-signals",
        help="Build a hash-bound protected-only signal candidate.",
    )
    protected_signals.add_argument("request_file")
    protected_signals.add_argument("output_dir")
    _add_output_argument(protected_signals)
    lane_repair = hamburg_sub.add_parser(
        "repair-lane-connections",
        help="Rebuild the reviewed Hamburg fanouts and diverge as a separate candidate.",
    )
    lane_repair.add_argument("source_net")
    lane_repair.add_argument("output_dir")
    lane_repair.add_argument("--connection-patch", help="Apply this explicit lane patch instead of the legacy LSA119 case repair.")
    lane_repair.add_argument("--patch-sha256", help="Expected SHA-256 of --connection-patch.")
    lane_repair.add_argument("--netconvert-binary", default="netconvert")
    lane_repair.add_argument("--sumo-binary", default="sumo")
    lane_repair.add_argument("--timeout-seconds", type=float, default=240.0)
    _add_output_argument(lane_repair)
    aerial_counts = hamburg_sub.add_parser(
        "bind-aerial-counts",
        help="Bind frozen official count fields to an aerial candidate.",
    )
    aerial_counts.add_argument("request_file")
    aerial_counts.add_argument("output_dir")
    _add_output_argument(aerial_counts)
    aerial_demand = hamburg_sub.add_parser(
        "generate-aerial-demand",
        help="Generate detector-constrained plausible demand for an aerial candidate.",
    )
    aerial_demand.add_argument("request_file")
    aerial_demand.add_argument("output_dir")
    _add_output_argument(aerial_demand)

    netedit = subparsers.add_parser("netedit", help="One-shot NetEdit review; multi-step editing uses persistent MCP.")
    netedit_sub = netedit.add_subparsers(dest="netedit_command", required=True)
    netedit_review = netedit_sub.add_parser("review", help="Open, capture, and close a diagnostic copy in one process.")
    netedit_review.add_argument("source_net_file")
    netedit_review.add_argument("output_dir")
    netedit_review.add_argument("expected_source_sha256")
    netedit_review.add_argument("--gui-settings-file")
    _add_output_argument(netedit_review)
    netedit_open = netedit_sub.add_parser("open", help="Open a hash-bound NetEdit review session.")
    netedit_open.add_argument("source_net_file")
    netedit_open.add_argument("candidate_net_file")
    netedit_open.add_argument("output_dir")
    netedit_open.add_argument("expected_source_sha256")
    netedit_open.add_argument("--gui-settings-file")
    netedit_open.add_argument("--selection-file")
    netedit_open.add_argument("--target-source-junction-ids", nargs="*")
    netedit_open.add_argument("--target-candidate-junction-ids", nargs="*")
    _add_output_argument(netedit_open)
    netedit_observe = netedit_sub.add_parser("observe", help="Observe the active NetEdit session.")
    netedit_observe.add_argument("session_id")
    netedit_observe.add_argument("--object-type", choices=("junction", "edge", "lane", "connection", "tlLogic"))
    netedit_observe.add_argument("--object-id")
    _add_output_argument(netedit_observe)
    netedit_act = netedit_sub.add_parser("act", help="Execute one whitelisted NetEdit action.")
    netedit_act.add_argument("session_id")
    netedit_act.add_argument("action")
    netedit_act.add_argument("expected_screenshot_sha256")
    netedit_act.add_argument("--x", type=int)
    netedit_act.add_argument("--y", type=int)
    netedit_act.add_argument("--to-x", type=int)
    netedit_act.add_argument("--to-y", type=int)
    _add_output_argument(netedit_act)
    netedit_close = netedit_sub.add_parser(
        "close",
        help="Finalize or abort the active NetEdit session.",
    )
    netedit_close.add_argument("session_id")
    netedit_close.add_argument("--mode", choices=("finalize", "abort"), required=True)
    netedit_close.add_argument("--expected-screenshot-sha256")
    netedit_close.add_argument("--reason")
    _add_output_argument(netedit_close)

    workflow = subparsers.add_parser(
        "workflow",
        help="Run an allowlisted long or specialized workflow from a JSON request file.",
    )
    workflow.add_argument("tool", choices=(*_WORKFLOW_TOOL_NAMES, 'selected'), metavar="TOOL")
    workflow.add_argument("request_file")
    workflow.add_argument('--execute', action='store_true', help='Execute a validated model selection; selected mode otherwise only checks readiness.')
    _add_output_argument(workflow)
    catalog = subparsers.add_parser('workflows', help='List scenarios and real workflow/function inputs for host-LLM selection.')
    catalog.add_argument('--scenario', help='Show one scenario by its registered ID.')
    _add_output_argument(catalog)

    mcp = subparsers.add_parser("mcp", help="Run the Torii MCP server.")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    serve = mcp_sub.add_parser("serve", help="Start the stdio MCP server.")
    serve.add_argument("--profile", choices=("legacy", "default", "netedit"), default="default")

    return parser


def _dispatch(args: argparse.Namespace) -> int:
    json_output = bool(getattr(args, "json", False))

    if args.command == "preflight":
        return _emit(torii_preflight(), json_output=json_output)
    if args.command == "config" and args.config_command == "inspect":
        return _emit(
            torii_config_inspect(args.baseline_config, args.variant_config),
            json_output=json_output,
        )
    if args.command == "run" and args.run_command == "compare":
        return _emit(
            torii_run_compare(
                baseline_summary=args.baseline_summary,
                baseline_tripinfo=args.baseline_tripinfo,
                variant_summary=args.variant_summary,
                variant_tripinfo=args.variant_tripinfo,
            ),
            json_output=json_output,
        )
    if args.command == "place" and args.place_command == "resolve":
        return _emit(torii_place_resolve(args.place_name, limit=args.limit), json_output=json_output)
    if args.command == "intersection" and args.intersection_command == "classify":
        return _emit(
            torii_intersection_classify(
                args.osm_file,
                args.seed_osm_node_id,
                traffic_side=args.traffic_side,
                road_network_evidence_file=args.road_network_evidence_file,
            ),
            json_output=json_output,
        )
    if args.command == "signal" and args.signal_command == "classify":
        return _emit(
            torii_signal_classify(args.ocit_file, expected_node_id=args.expected_node_id),
            json_output=json_output,
        )
    if args.command == "network" and args.network_command == "audit":
        return _emit(
            torii_network_audit(
                args.net_file,
                args.output_dir,
                profile=args.profile,
                prefix=args.prefix,
            ),
            json_output=json_output,
        )
    if args.command == "network" and args.network_command == "compare":
        return _emit(
            torii_network_compare(
                args.source_net_file,
                args.candidate_net_file,
                args.output_dir,
                prefix=args.prefix,
            ),
            json_output=json_output,
        )
    if args.command == "network" and args.network_command == "movement-probes":
        return _emit(
            run_candidate_movement_probes(candidate_manifest=args.candidate_manifest, output_dir=args.output_dir,
                sumo_binary=args.sumo_binary, seed=args.seed, end_time_s=args.end, timeout_seconds=args.timeout_seconds),
            json_output=json_output,
        )
    if args.command == "network" and args.network_command == "routeability":
        return _emit(
            sumo_network_routeability_audit(
                net_file=args.net_file,
                output_dir=args.output_dir,
                prefix=args.prefix,
                vehicle_count=args.vehicle_count,
                seed=args.seed,
                initial_end=args.initial_end,
                max_end=args.max_end,
                timeout_seconds=args.timeout_seconds,
            ),
            json_output=json_output,
        )
    if args.command == "demand" and args.demand_command == "audit":
        return _emit(
            torii_demand_audit(
                args.expected_counts_csv,
                args.detector_output_xml,
                args.output_dir,
                prefix=args.prefix,
            ),
            json_output=json_output,
        )
    if args.command == "review" and args.review_command == "create":
        return _emit(
            torii_review_create(
                args.output_dir,
                net_file=args.net_file,
                title=args.title,
                claim_status=args.claim_status,
                raw_net_file=args.raw_net_file,
                connected_core_file=args.connected_core_file,
                tls_review_file=args.tls_review_file,
                topology_audit_report_file=args.topology_audit_report_file,
                junction_aggregation_report_file=args.junction_aggregation_report_file,
                routeability_audit_report_file=args.routeability_audit_report_file,
            ),
            json_output=json_output,
        )
    if args.command == "netedit":
        if args.netedit_command != "review":
            return _emit({"status": "blocked", "error": "Multi-step NetEdit commands require a persistent MCP session. Use 'torii netedit review' for a one-shot CLI review."}, json_output=json_output)
        destination = Path(args.output_dir).resolve()
        if destination.exists():
            raise ValueError("Choose a new NetEdit review output directory.")
        opened = torii_netedit_open(args.source_net_file, str(destination / "candidate.net.xml"), str(destination),
                                    args.expected_source_sha256, gui_settings_file=args.gui_settings_file)
        if opened.status != "pass":
            return _emit(opened, json_output=json_output)
        session_id = opened.payload["session_id"]
        try:
            observed = torii_netedit_observe(session_id)
        finally:
            closed = torii_netedit_close(session_id, mode="abort", reason="one_shot_review_complete")
        if closed.status != "pass":
            return _emit(closed, json_output=json_output)
        if observed.status != "pass":
            return _emit(observed, json_output=json_output)
        return _emit({"status": "pass", "claim_status": "diagnostic-demo",
                      "summary": "Captured and closed the NetEdit review copy without saving edits.",
                      "observation": observed.model_dump(mode="json"), "close": closed.model_dump(mode="json")},
                     json_output=json_output)
    if args.command == 'workflows':
        from .core.workflow_catalog import get_workflow_catalog
        return _emit(get_workflow_catalog(args.scenario), json_output=json_output)
    if args.command == "workflow":
        if args.tool == 'selected':
            from .core.workflow_catalog import run_selected_workflow
            return _emit(run_selected_workflow(_load_request(args.request_file), execute=args.execute), json_output=json_output)
        if args.execute:
            raise ValueError('--execute is only used with workflow selected.')
        from .legacy_tools import WORKFLOW_TOOLS

        return _emit(
            WORKFLOW_TOOLS[args.tool](**_load_request(args.request_file)),
            json_output=json_output,
        )
    if args.command == "hamburg" and args.hamburg_command == "build-network":
        from .core.hamburg_topology_workflow import build_hamburg_topology_workflow

        return _emit(
            build_hamburg_topology_workflow(request_file=args.request_file, output_dir=args.output_dir),
            json_output=json_output,
        )
    if args.command == "hamburg" and args.hamburg_command == "inspect-road-uses":
        from .core.hamburg_road_use_review import build_hamburg_road_use_review

        return _emit(
            build_hamburg_road_use_review(request_file=args.request_file, output_dir=args.output_dir),
            json_output=json_output,
        )
    if args.command == "hamburg" and args.hamburg_command == "aerial-movements":
        return _emit(
            build_hamburg_aerial_corridor_plan(
                request_file=args.request_file,
                output_dir=args.output_dir,
            ),
            json_output=json_output,
        )
    if (
        args.command == "hamburg"
        and args.hamburg_command == "combine-aerial-movements"
    ):
        return _emit(
            build_hamburg_aerial_combined_candidate(
                request_file=args.request_file,
                output_dir=args.output_dir,
            ),
            json_output=json_output,
        )
    if args.command == "hamburg" and args.hamburg_command == "bind-aerial-signals":
        return _emit(
            build_hamburg_aerial_signal_binding(
                request_file=args.request_file,
                output_dir=args.output_dir,
            ),
            json_output=json_output,
        )
    if args.command == "hamburg" and args.hamburg_command == "build-protected-signals":
        return _emit(
            build_protected_signal_candidate_from_request(
                request_file=args.request_file,
                output_dir=args.output_dir,
            ),
            json_output=json_output,
        )
    if args.command == "hamburg" and args.hamburg_command == "repair-lane-connections":
        return _emit(
            build_hamburg_lane_connection_repair(
                source_net=args.source_net,
                output_dir=args.output_dir,
                connection_patch_file=args.connection_patch,
                expected_connection_patch_sha256=args.patch_sha256,
                netconvert_binary=args.netconvert_binary,
                sumo_binary=args.sumo_binary,
                timeout_seconds=args.timeout_seconds,
            ),
            json_output=json_output,
        )
    if args.command == "hamburg" and args.hamburg_command == "bind-aerial-counts":
        return _emit(
            build_hamburg_aerial_count_binding(
                request_file=args.request_file,
                output_dir=args.output_dir,
            ),
            json_output=json_output,
        )
    if args.command == "hamburg" and args.hamburg_command == "generate-aerial-demand":
        return _emit(
            generate_hamburg_aerial_demand(
                request_file=args.request_file,
                output_dir=args.output_dir,
            ),
            json_output=json_output,
        )
    if args.command == "mcp" and args.mcp_command == "serve":
        server = create_server(args.profile)

        async def _run() -> None:
            await server.run_stdio_async()

        anyio.run(_run)
        return 0

    parser = _build_parser()
    parser.print_help(sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except Exception as exc:  # noqa: BLE001 - CLI boundary must not leak tracebacks.
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
