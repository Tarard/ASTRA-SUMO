from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from .junction_teacher_model import (
    extract_junction_pattern_index,
    summarize_junction_pattern_policy,
    summarize_junction_pattern_templates,
)
from .reference_policy import analyze_reference_network_policy


def build_reference_policy_report(net_file: Path, cluster_prefix: str = "cluster_") -> dict[str, Any]:
    policy = analyze_reference_network_policy(net_file)
    if policy.get("status") != "pass":
        return policy

    root = ET.parse(net_file).getroot()
    cluster_histogram: Counter[str] = Counter()
    traffic_light_junction_count = 0
    for junction in root.findall("junction"):
        junction_id = junction.attrib.get("id", "")
        if junction.attrib.get("type") == "traffic_light":
            traffic_light_junction_count += 1
        if junction_id.startswith(cluster_prefix):
            source_count = len([part for part in junction_id.removeprefix(cluster_prefix).split("_") if part])
            cluster_histogram[str(source_count)] += 1

    top_level_connection_count = sum(
        1
        for connection in root.findall("connection")
        if not connection.attrib.get("from", "").startswith(":")
        and not connection.attrib.get("to", "").startswith(":")
    )

    passenger_counts = dict(policy.get("passenger_edge_type_counts", {}))
    service_counts = {
        edge_type: count
        for edge_type, count in policy.get("edge_type_counts", {}).items()
        if edge_type not in passenger_counts
    }
    junction_pattern_records = extract_junction_pattern_index(net_file)
    junction_pattern_policy = summarize_junction_pattern_policy(junction_pattern_records)

    return {
        **policy,
        "reference_policy_status": "teacher_policy_analyzed",
        "road_type_counts": dict(policy.get("edge_type_counts", {})),
        "passenger_drivable_type_counts": passenger_counts,
        "cluster_source_node_count_histogram": dict(sorted(cluster_histogram.items())),
        "traffic_light_junction_count": traffic_light_junction_count,
        "tls_logic_count": len(root.findall("tlLogic")),
        "top_level_connection_count": top_level_connection_count,
        "support_or_service_type_counts": service_counts,
        "junction_pattern_record_count": junction_pattern_policy["record_count"],
        "junction_pattern_family_counts": junction_pattern_policy["family_counts"],
        "junction_pattern_control_counts": junction_pattern_policy["control_counts"],
        "junction_pattern_templates": summarize_junction_pattern_templates(junction_pattern_records),
    }
