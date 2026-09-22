from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .reference_policy import analyze_reference_network_policy, load_reference_policy_report
from .road_scope import DRIVE_HIGHWAYS, resolve_highway_classes


MOVEMENT_LAYER_OPTIONS = ("passenger", "bicycle", "pedestrian", "bus")
NETWORK_DETAIL_OPTIONS = (
    "arterial_core",
    "passenger_vehicle",
    "passenger_plus_service",
    "bicycle",
    "pedestrian",
    "multimodal",
    "reference_matched",
)
NETWORK_PLAN_QUESTION = (
    "Which traffic layers should Torii model: passenger vehicles, service/access roads, "
    "bicycle, pedestrian, bus/public transport, or a reference-matched SUMO network?"
)
REFERENCE_PLAN_QUESTION = (
    "Which reference SUMO .net.xml or reference policy report should Torii use to infer the matched road layers?"
)
REFERENCE_MATCHED_VALIDATION_GATES = (
    "reference_join_audit",
    "reference_join_aggregation",
    "junction_pattern_index",
    "road_connectivity_parity",
    "connection_semantics_parity",
    "tls_semantics_parity",
    "internal_junction_parity",
    "connection_mode_audit",
    "teacher_guided_junction_parity",
)

_BICYCLE_HIGHWAYS = {"cycleway", "path"}
_PEDESTRIAN_HIGHWAYS = {"footway", "pedestrian", "path", "steps"}


def _as_tokens(value: str | Iterable[str] | None) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        pieces = value.replace(";", ",").split(",")
    else:
        pieces = list(value)
    return {str(item).strip().lower().replace("-", "_") for item in pieces if str(item).strip()}


def _normalize_network_profile(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("-", "_")
    aliases = {
        "reference": "reference_matched",
        "reference_match": "reference_matched",
        "matched_reference": "reference_matched",
        "reference_policy": "reference_matched",
        "manual_reference": "reference_matched",
    }
    return aliases.get(normalized, normalized)


def _infer_reference_profile(user_request: str | None) -> str:
    text = " ".join((user_request or "").lower().split())
    reference_tokens = (
        "reference",
        "reference-matched",
        "reference matched",
        "match",
        "matching",
        "same layer policy",
        "same road layer",
        "same as",
        "similar to",
        "like",
        "imitate",
        "mimic",
        "manually cleaned",
    )
    if any(token in text for token in reference_tokens):
        return "reference_matched"
    return ""


def _infer_reference_target(user_request: str | None, network_profile: str | None) -> str:
    raw_text = (user_request or "").strip()
    normalized = " ".join(raw_text.lower().split())
    if "manual" in normalized and "reference" in normalized:
        return "manually cleaned reference network"
    if "reference" in normalized:
        return "specified reference"
    if _normalize_network_profile(network_profile) == "reference_matched":
        return "specified reference"
    return ""


def _highways_for_layers(layers: set[str]) -> set[str]:
    highways = set()
    if not layers or layers & {"passenger", "vehicle", "car", "bus"}:
        highways |= set(DRIVE_HIGHWAYS) | {"unclassified", "service"}
    if layers & {"service", "access", "passenger_plus_service"}:
        highways |= set(DRIVE_HIGHWAYS) | {"unclassified", "service"}
    if layers & {"bicycle", "bike", "bikeway", "cycleway"}:
        highways |= _BICYCLE_HIGHWAYS
    if layers & {"pedestrian", "walk", "footway"}:
        highways |= _PEDESTRIAN_HIGHWAYS
    return highways


def _blocked_plan() -> dict[str, Any]:
    return {
        "status": "blocked",
        "claim_status": "blocked",
        "network_plan_status": "needs_user_confirmation",
        "missing_blockers": ["network_plan"],
        "next_question": NETWORK_PLAN_QUESTION,
        "traffic_layer_options": list(MOVEMENT_LAYER_OPTIONS),
        "network_detail_options": list(NETWORK_DETAIL_OPTIONS),
        "recommended_network_detail": "arterial_core",
        "movement_layers": [],
        "highway_classes": [],
        "reference_visual_detail_modal_way_tags": {},
        "reference_visual_detail_modal_edge_type_counts": {},
        "service_passenger_policy": "sumo_default",
        "validation_gates": [],
    }


def _blocked_reference_plan(reference_target: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "claim_status": "blocked",
        "network_plan_status": "needs_reference_artifact",
        "network_profile": "reference_matched",
        "reference_target": reference_target or "specified reference",
        "network_detail_target": "reference_matched",
        "missing_blockers": ["reference_network_or_policy"],
        "next_question": REFERENCE_PLAN_QUESTION,
        "traffic_layer_options": list(MOVEMENT_LAYER_OPTIONS),
        "network_detail_options": list(NETWORK_DETAIL_OPTIONS),
        "recommended_network_detail": "reference_matched",
        "movement_layers": [],
        "highway_classes": [],
        "service_passenger_policy": "sumo_default",
        "validation_gates": [],
    }


def _failed_reference_plan(reference_policy: Mapping[str, Any], reference_target: str) -> dict[str, Any]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "network_plan_status": str(reference_policy.get("reference_policy_status", "failed")),
        "network_profile": "reference_matched",
        "reference_target": reference_target or "specified reference",
        "reference_net_file": str(reference_policy.get("reference_net_file", "")),
        "network_detail_target": "reference_matched",
        "movement_layers": [],
        "highway_classes": [],
        "service_passenger_policy": "sumo_default",
        "reference_policy": dict(reference_policy),
        "validation_gates": [],
        "warnings": list(reference_policy.get("warnings", [])),
    }


def _reference_policy_plan(
    *,
    reference_policy: Mapping[str, Any],
    reference_target: str,
    service_passenger_policy: str | None,
) -> dict[str, Any]:
    if reference_policy.get("status") != "pass":
        return _failed_reference_plan(reference_policy, reference_target)

    selected_highways = sorted(str(item) for item in reference_policy.get("selected_highway_classes", []))
    vehicle_core_highways = sorted(
        str(item) for item in reference_policy.get("vehicle_core_highway_classes", selected_highways)
    )
    visual_detail_highways = sorted(
        str(item)
        for item in reference_policy.get(
            "visual_detail_highway_classes",
            reference_policy.get("reference_visual_detail_highway_classes", vehicle_core_highways),
        )
    )
    visual_detail_only_highways = sorted(set(visual_detail_highways) - set(vehicle_core_highways))
    auxiliary_layers = sorted(str(item) for item in reference_policy.get("auxiliary_modal_layers", []))
    movement_layers = sorted(str(item) for item in reference_policy.get("movement_layers", ["passenger", *auxiliary_layers]))
    if "passenger" not in movement_layers:
        movement_layers.insert(0, "passenger")
    reference_source_way_ids = sorted(str(item) for item in reference_policy.get("reference_source_way_ids", []))

    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "network_plan_status": "inferred_from_reference_policy",
        "network_profile": "reference_matched",
        "reference_target": reference_target or "specified reference",
        "reference_net_file": str(reference_policy.get("reference_net_file", "")),
        "network_detail_target": "reference_matched",
        "primary_network_layer": str(reference_policy.get("primary_network_layer", "passenger_vehicle")),
        "default_routeability_layer": "vehicle_core",
        "default_netedit_comparison_layer": "reference_visual_detail",
        "comparison_scope_mode": "vehicle_core_and_reference_visual_detail",
        "auxiliary_modal_layers": auxiliary_layers,
        "auxiliary_modal_highway_classes": dict(reference_policy.get("auxiliary_modal_highway_classes", {})),
        "movement_layers": movement_layers,
        "highway_classes": selected_highways,
        "vehicle_core_highway_classes": vehicle_core_highways,
        "reference_visual_detail_highway_classes": visual_detail_highways,
        "reference_visual_detail_only_highway_classes": visual_detail_only_highways,
        "reference_visual_detail_modal_way_tags": dict(
            reference_policy.get("visual_detail_modal_way_tags", {})
        ),
        "reference_visual_detail_modal_edge_type_counts": dict(
            reference_policy.get("visual_detail_modal_edge_type_counts", {})
        ),
        "reference_source_way_ids": reference_source_way_ids,
        "reference_source_way_id_count": len(reference_source_way_ids),
        "service_passenger_policy": service_passenger_policy
        or str(reference_policy.get("service_passenger_policy", "sumo_default")),
        "cleanup_policy": [
            "derive vehicle road hierarchy from the reference passenger-drivable layer",
            "build a separate reference visual-detail layer for full-reference Netedit comparison",
            "limit OSM ways to reference source ids when the reference exposes OSM-derived edge ids",
            "record bicycle, pedestrian, and bus layers as auxiliary modal layers",
            "retain reference-observed railway modal ways in the visual-detail layer",
            "apply reference service-road passenger permissions only when the reference uses them",
        ],
        "validation_gates": [
            "passenger_connectivity",
            "routeability_audit",
            "topology_audit",
            "standard_nema_scan",
            "scope_matched_reference_comparison",
            *REFERENCE_MATCHED_VALIDATION_GATES,
            "netedit_launch",
        ],
        "reference_policy": dict(reference_policy),
    }


def derive_network_plan(
    *,
    user_request: str | None = None,
    highway_classes: str | set[str] | None = None,
    traffic_layers: str | Iterable[str] | None = None,
    network_profile: str | None = None,
    reference_net_file: str | Path | None = None,
    reference_policy_report: str | Path | Mapping[str, Any] | None = None,
    service_passenger_policy: str | None = None,
) -> dict[str, Any]:
    profile = _normalize_network_profile(network_profile) or _infer_reference_profile(user_request)
    reference_target = _infer_reference_target(user_request, network_profile)
    if profile == "reference_matched":
        if reference_policy_report is not None:
            reference_policy = load_reference_policy_report(reference_policy_report)
        elif reference_net_file is not None:
            reference_policy = analyze_reference_network_policy(reference_net_file)
        else:
            return _blocked_reference_plan(reference_target)
        return _reference_policy_plan(
            reference_policy=reference_policy,
            reference_target=reference_target,
            service_passenger_policy=service_passenger_policy,
        )

    selected_highways = resolve_highway_classes(highway_classes, default_to_recommended=False)
    requested_layers = _as_tokens(traffic_layers)
    if selected_highways is not None:
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "network_plan_status": "confirmed",
            "network_profile": profile,
            "reference_target": "",
            "network_detail_target": "custom_road_scope",
            "movement_layers": sorted(requested_layers or {"passenger"}),
            "highway_classes": sorted(selected_highways),
            "service_passenger_policy": service_passenger_policy or "sumo_default",
            "cleanup_policy": ["use user-selected road classes"],
            "validation_gates": [
                "passenger_connectivity",
                "routeability_audit",
                "topology_audit",
                "standard_nema_scan",
                "netedit_launch",
            ],
        }
    if requested_layers:
        inferred_highways = _highways_for_layers(requested_layers)
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "network_plan_status": "inferred_from_traffic_layers",
            "network_profile": profile,
            "reference_target": "",
            "network_detail_target": "traffic_layer_selection",
            "movement_layers": sorted(requested_layers),
            "highway_classes": sorted(inferred_highways),
            "service_passenger_policy": service_passenger_policy
            or ("allow_vehicle_service" if requested_layers & {"service", "access", "passenger_plus_service"} else "sumo_default"),
            "cleanup_policy": ["derive OSM highway classes from requested traffic layers"],
            "validation_gates": [
                "passenger_connectivity",
                "routeability_audit",
                "topology_audit",
                "standard_nema_scan",
                "netedit_launch",
            ],
        }
    return _blocked_plan()
