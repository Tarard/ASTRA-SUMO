from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Mapping
import xml.etree.ElementTree as ET

from .road_scope import HIGHWAY_CLASS_PRESETS


VEHICLE_HIGHWAY_CLASSES = set(HIGHWAY_CLASS_PRESETS["full_vehicle"])
BICYCLE_HIGHWAYS = {"cycleway", "path"}
PEDESTRIAN_HIGHWAYS = {"footway", "pedestrian", "path", "steps"}
REFERENCE_PASSENGER_RATIO_THRESHOLD = 0.5
REFERENCE_MODAL_WAY_CLASSES = {
    "railway": {
        "rail",
        "light_rail",
        "tram",
        "subway",
        "monorail",
        "narrow_gauge",
        "funicular",
    },
}


def _base_highway_class(edge_type: str) -> str:
    normalized = edge_type.strip().lower()
    if normalized.startswith("highway."):
        normalized = normalized[len("highway.") :]
    return normalized


def _lane_allows(lane: ET.Element, vclass: str, *, default_if_unspecified: bool) -> bool:
    allow_value = lane.attrib.get("allow", "").strip()
    disallow_value = lane.attrib.get("disallow", "").strip()
    if allow_value:
        return vclass in allow_value.split()
    if disallow_value:
        return vclass not in disallow_value.split()
    return default_if_unspecified


def _edge_lanes(edge: ET.Element) -> list[ET.Element]:
    return list(edge.findall("lane"))


def _edge_allows(edge: ET.Element, vclass: str, *, base_class: str) -> bool:
    lanes = _edge_lanes(edge)
    if not lanes:
        return False
    if vclass in {"passenger", "bus"}:
        default_if_unspecified = base_class in VEHICLE_HIGHWAY_CLASSES
    elif vclass == "bicycle":
        default_if_unspecified = base_class in BICYCLE_HIGHWAYS
    elif vclass == "pedestrian":
        default_if_unspecified = base_class in PEDESTRIAN_HIGHWAYS
    else:
        default_if_unspecified = False
    return any(_lane_allows(lane, vclass, default_if_unspecified=default_if_unspecified) for lane in lanes)


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _source_way_id(edge_id: str) -> str:
    normalized = edge_id.strip()
    if not normalized or normalized.startswith(":"):
        return ""
    if normalized.startswith("-"):
        normalized = normalized[1:]
    normalized = normalized.split("#", 1)[0]
    return normalized if normalized.isdigit() else ""


def analyze_reference_network_policy(reference_net_file: str | Path) -> dict[str, Any]:
    net_file = Path(reference_net_file)
    if not net_file.exists():
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "reference_policy_status": "failed",
            "reference_net_file": str(net_file),
            "warnings": [f"reference SUMO .net.xml not found: {net_file}"],
        }

    edge_type_counts: Counter[str] = Counter()
    passenger_edge_type_counts: Counter[str] = Counter()
    bicycle_edge_type_counts: Counter[str] = Counter()
    pedestrian_edge_type_counts: Counter[str] = Counter()
    bus_edge_type_counts: Counter[str] = Counter()
    service_edge_count = 0
    service_passenger_edge_count = 0
    selected_highways: set[str] = set()
    visual_detail_highways: set[str] = set()
    source_way_ids: set[str] = set()
    visual_detail_edge_type_counts: Counter[str] = Counter()
    visual_detail_modal_way_tags: dict[str, set[str]] = defaultdict(set)
    visual_detail_modal_edge_type_counts: Counter[str] = Counter()
    auxiliary_highways: dict[str, set[str]] = defaultdict(set)

    root = ET.parse(net_file).getroot()
    for edge in root.findall("edge"):
        if edge.attrib.get("function") == "internal" or edge.attrib.get("id", "").startswith(":"):
            continue
        source_way_id = _source_way_id(edge.attrib.get("id", ""))
        if source_way_id:
            source_way_ids.add(source_way_id)
        edge_type = edge.attrib.get("type", "").strip().lower()
        base_class = _base_highway_class(edge_type)
        if not edge_type or not base_class:
            continue

        edge_type_counts[edge_type] += 1
        if edge_type.startswith("highway."):
            visual_detail_highways.add(base_class)
            visual_detail_edge_type_counts[edge_type] += 1
        else:
            tag_key, _, tag_value = edge_type.partition(".")
            if tag_value in REFERENCE_MODAL_WAY_CLASSES.get(tag_key, set()):
                visual_detail_modal_way_tags[tag_key].add(tag_value)
                visual_detail_modal_edge_type_counts[edge_type] += 1
        allows_passenger = _edge_allows(edge, "passenger", base_class=base_class)
        allows_bicycle = _edge_allows(edge, "bicycle", base_class=base_class)
        allows_pedestrian = _edge_allows(edge, "pedestrian", base_class=base_class)
        allows_bus = _edge_allows(edge, "bus", base_class=base_class)

        if base_class == "service":
            service_edge_count += 1
            if allows_passenger:
                service_passenger_edge_count += 1
        if allows_passenger:
            passenger_edge_type_counts[edge_type] += 1
            if base_class in VEHICLE_HIGHWAY_CLASSES and base_class != "service":
                selected_highways.add(base_class)
        if allows_bicycle:
            bicycle_edge_type_counts[edge_type] += 1
            auxiliary_highways["bicycle"].add(base_class)
        if allows_pedestrian:
            pedestrian_edge_type_counts[edge_type] += 1
            auxiliary_highways["pedestrian"].add(base_class)
        if allows_bus:
            bus_edge_type_counts[edge_type] += 1
            auxiliary_highways["bus"].add(base_class)

    auxiliary_layers = [
        layer
        for layer, counter in (
            ("bicycle", bicycle_edge_type_counts),
            ("pedestrian", pedestrian_edge_type_counts),
            ("bus", bus_edge_type_counts),
        )
        if counter
    ]
    service_ratio = service_passenger_edge_count / service_edge_count if service_edge_count else 0.0
    service_policy = "reference_match" if service_ratio >= REFERENCE_PASSENGER_RATIO_THRESHOLD else "sumo_default"

    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "reference_policy_status": "analyzed",
        "reference_net_file": str(net_file),
        "primary_network_layer": "passenger_vehicle",
        "selected_highway_classes": sorted(selected_highways),
        "vehicle_core_highway_classes": sorted(selected_highways),
        "visual_detail_highway_classes": sorted(visual_detail_highways),
        "visual_detail_only_highway_classes": sorted(visual_detail_highways - selected_highways),
        "reference_source_way_ids": sorted(source_way_ids, key=int),
        "reference_source_way_id_count": len(source_way_ids),
        "auxiliary_modal_layers": auxiliary_layers,
        "auxiliary_modal_highway_classes": {
            layer: sorted(values) for layer, values in sorted(auxiliary_highways.items())
        },
        "movement_layers": ["passenger", *auxiliary_layers],
        "service_passenger_policy": service_policy,
        "service_edge_count": service_edge_count,
        "service_passenger_edge_count": service_passenger_edge_count,
        "service_passenger_ratio": round(service_ratio, 3),
        "edge_type_counts": _sorted_counter(edge_type_counts),
        "visual_detail_edge_type_counts": _sorted_counter(visual_detail_edge_type_counts),
        "visual_detail_modal_way_tags": {
            key: sorted(values)
            for key, values in sorted(visual_detail_modal_way_tags.items())
        },
        "visual_detail_modal_edge_type_counts": _sorted_counter(
            visual_detail_modal_edge_type_counts
        ),
        "passenger_edge_type_counts": _sorted_counter(passenger_edge_type_counts),
        "bicycle_edge_type_counts": _sorted_counter(bicycle_edge_type_counts),
        "pedestrian_edge_type_counts": _sorted_counter(pedestrian_edge_type_counts),
        "bus_edge_type_counts": _sorted_counter(bus_edge_type_counts),
        "warnings": [],
    }


def load_reference_policy_report(report: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(report, Mapping):
        payload = dict(report)
    else:
        report_text = str(report)
        try:
            path = Path(report_text)
            is_file = path.exists()
        except OSError:
            is_file = False
        if is_file:
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            payload = json.loads(report_text)

    selected = payload.get("selected_highway_classes") or payload.get("highway_classes") or []
    vehicle_core = payload.get("vehicle_core_highway_classes") or selected
    visual_detail = payload.get("visual_detail_highway_classes") or payload.get("reference_visual_detail_highway_classes") or selected
    visual_detail_set = {str(item) for item in visual_detail}
    vehicle_core_set = {str(item) for item in vehicle_core}
    movement_layers = payload.get("movement_layers") or ["passenger", *payload.get("auxiliary_modal_layers", [])]
    source_way_ids = sorted({str(item) for item in payload.get("reference_source_way_ids", payload.get("source_way_ids", []))})
    return {
        "status": str(payload.get("status", "pass")),
        "claim_status": str(payload.get("claim_status", "diagnostic-demo")),
        "reference_policy_status": str(payload.get("reference_policy_status", "loaded")),
        "reference_net_file": str(payload.get("reference_net_file", "")),
        "primary_network_layer": str(payload.get("primary_network_layer", "passenger_vehicle")),
        "selected_highway_classes": sorted(str(item) for item in selected),
        "vehicle_core_highway_classes": sorted(vehicle_core_set),
        "visual_detail_highway_classes": sorted(visual_detail_set),
        "visual_detail_only_highway_classes": sorted(visual_detail_set - vehicle_core_set),
        "reference_source_way_ids": source_way_ids,
        "reference_source_way_id_count": int(payload.get("reference_source_way_id_count", len(source_way_ids)) or 0),
        "auxiliary_modal_layers": sorted(str(item) for item in payload.get("auxiliary_modal_layers", [])),
        "auxiliary_modal_highway_classes": dict(payload.get("auxiliary_modal_highway_classes", {})),
        "movement_layers": sorted(str(item) for item in movement_layers),
        "service_passenger_policy": str(payload.get("service_passenger_policy", "sumo_default")),
        "edge_type_counts": dict(payload.get("edge_type_counts", {})),
        "visual_detail_edge_type_counts": dict(payload.get("visual_detail_edge_type_counts", {})),
        "passenger_edge_type_counts": dict(payload.get("passenger_edge_type_counts", {})),
        "bicycle_edge_type_counts": dict(payload.get("bicycle_edge_type_counts", {})),
        "pedestrian_edge_type_counts": dict(payload.get("pedestrian_edge_type_counts", {})),
        "bus_edge_type_counts": dict(payload.get("bus_edge_type_counts", {})),
        "warnings": list(payload.get("warnings", [])),
    }
