from __future__ import annotations

import gzip
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, BinaryIO
import xml.etree.ElementTree as ET


def normalize_road_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def source_node_ids(node_id: str) -> tuple[str, ...]:
    if not node_id.startswith("cluster_"):
        return (node_id,)
    parts = tuple(part for part in node_id[len("cluster_") :].split("_") if part)
    return parts or (node_id,)


def corridor_key_from_osm(name: str = "", ref: str = "", highway: str = "") -> str:
    normalized_name = normalize_road_name(name)
    if normalized_name:
        return f"name:{normalized_name}"
    normalized_ref = normalize_road_name(ref)
    if normalized_ref:
        return f"ref:{normalized_ref}"
    normalized_highway = normalize_road_name(highway)
    if normalized_highway:
        return f"highway:{normalized_highway}"
    return ""


def parse_osm_way_context(osm_file: Path) -> dict[str, Any]:
    if not osm_file.exists():
        raise FileNotFoundError(osm_file)

    node_context: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"names": set(), "refs": set(), "highways": set(), "corridor_keys": set()}
    )
    highway_way_count = 0
    with _open_osm(osm_file) as handle:
        for _event, elem in ET.iterparse(handle, events=("end",)):
            if _local_name(elem.tag) != "way":
                continue

            tags = {
                child.attrib.get("k", ""): child.attrib.get("v", "")
                for child in elem
                if _local_name(child.tag) == "tag"
            }
            highway = tags.get("highway", "").strip()
            if not highway:
                elem.clear()
                continue

            node_refs = [
                child.attrib["ref"]
                for child in elem
                if _local_name(child.tag) == "nd" and child.attrib.get("ref")
            ]
            name = tags.get("name", "").strip()
            ref = tags.get("ref", "").strip()
            corridor_key = corridor_key_from_osm(name=name, ref=ref, highway=highway)
            highway_way_count += 1
            for node_ref in node_refs:
                record = node_context[node_ref]
                if name:
                    record["names"].add(name)
                if ref:
                    record["refs"].add(ref)
                record["highways"].add(highway)
                if corridor_key:
                    record["corridor_keys"].add(corridor_key)
            elem.clear()

    return {
        "osm_file": str(osm_file),
        "way_count": highway_way_count,
        "node_count": len(node_context),
        "node_way_context": {
            node_id: {field: sorted(values) for field, values in sorted(record.items())}
            for node_id, record in sorted(node_context.items())
        },
    }


def node_corridor_keys(osm_context: dict[str, Any], node_id: str) -> tuple[str, ...]:
    keys: set[str] = set()
    node_way_context = osm_context.get("node_way_context", {}) or {}
    for source_id in source_node_ids(node_id):
        record = node_way_context.get(source_id, {}) or {}
        for key in record.get("corridor_keys", []) or []:
            key_text = str(key)
            if key_text:
                keys.add(key_text)
    return tuple(sorted(keys))


def audit_node_group_corridors(
    graph: dict[str, Any],
    osm_context: dict[str, Any],
    node_ids: list[str] | tuple[str, ...],
    group_id: str,
    source: str,
) -> dict[str, Any]:
    junctions = graph.get("junctions", {}) or {}
    node_ids = [str(node_id) for node_id in node_ids]
    per_node_keys = {node_id: node_corridor_keys(osm_context, node_id) for node_id in node_ids}

    named_corridors = sorted(
        {
            key
            for keys in per_node_keys.values()
            for key in keys
            if _is_named_corridor(key)
        }
    )
    unnamed_corridors = sorted(
        {
            key
            for keys in per_node_keys.values()
            for key in keys
            if key and not _is_named_corridor(key)
        }
    )
    cell_signatures = _intersection_cell_signatures(per_node_keys)
    partition_counts = Counter(_partition_key(keys) for keys in per_node_keys.values())
    if "" in partition_counts:
        del partition_counts[""]
    decision, reason = corridor_decision(
        {
            "node_count": len(node_ids),
            "max_pair_distance_m": _max_pair_distance(junctions, node_ids),
            "named_corridor_count": len(named_corridors),
            "unnamed_corridor_count": len(unnamed_corridors),
            "intersection_cell_count": len(cell_signatures),
        }
    )

    return {
        "group_id": group_id,
        "source": source,
        "node_count": len(node_ids),
        "node_ids": node_ids,
        "missing_node_count": sum(1 for node_id in node_ids if node_id not in junctions),
        "max_pair_distance_m": round(_max_pair_distance(junctions, node_ids), 3),
        "named_corridor_count": len(named_corridors),
        "unnamed_corridor_count": len(unnamed_corridors),
        "intersection_cell_count": len(cell_signatures),
        "intersection_cell_signatures": cell_signatures,
        "corridor_partition_count": len(partition_counts),
        "max_corridor_partition_node_count": max(partition_counts.values(), default=0),
        "corridor_decision": decision,
        "corridor_reason": reason,
        "named_corridors": named_corridors,
        "unnamed_corridors": unnamed_corridors,
        "top_corridor_partitions": [
            {"signature": signature, "node_count": count}
            for signature, count in partition_counts.most_common(5)
        ],
    }


def corridor_decision(audit: dict[str, Any]) -> tuple[str, str]:
    node_count = int(audit.get("node_count", 0) or 0)
    intersection_cell_count = int(audit.get("intersection_cell_count", 0) or 0)
    named_corridor_count = int(audit.get("named_corridor_count", 0) or 0)
    max_pair_distance_m = float(audit.get("max_pair_distance_m", 0.0) or 0.0)
    if node_count >= 20 and intersection_cell_count >= 3:
        return "reject", "large group spans three or more named intersection cells"
    if intersection_cell_count >= 4:
        return "reject", "group spans four or more named intersection cells"
    if named_corridor_count >= 7:
        return "reject", "group touches seven or more named road corridors"
    if max_pair_distance_m > 90.0 and intersection_cell_count >= 2:
        return "reject", "wide group spans multiple named cells"
    if intersection_cell_count == 0 and named_corridor_count == 0:
        return "allow", "insufficient name evidence; preserve for topology and map review"
    return "allow", "within one local named-road cell or insufficient name evidence"


def graph_from_topology_inputs(
    junctions: list[dict[str, Any]],
    edges: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    incident_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges or []:
        from_node = str(edge.get("from", ""))
        to_node = str(edge.get("to", ""))
        if from_node:
            incident_edges[from_node].append(edge)
        if to_node:
            incident_edges[to_node].append(edge)
    return {
        "junctions": {str(junction["id"]): junction for junction in junctions},
        "edges": list(edges or []),
        "incident_edges": dict(incident_edges),
    }


def enrich_clusters_with_corridor_audit(
    clusters: list[dict[str, Any]],
    *,
    graph: dict[str, Any],
    osm_context: dict[str, Any],
    source: str = "topology_audit",
) -> list[dict[str, Any]]:
    enriched = []
    for cluster in clusters:
        audit = audit_node_group_corridors(
            graph,
            osm_context,
            [str(node_id) for node_id in cluster.get("node_ids", []) or []],
            str(cluster.get("cluster_id", "")),
            source,
        )
        enriched_cluster = dict(cluster)
        enriched_cluster.update(
            {
                "corridor_decision": audit["corridor_decision"],
                "corridor_reason": audit["corridor_reason"],
                "corridor_named_corridor_count": audit["named_corridor_count"],
                "corridor_unnamed_corridor_count": audit["unnamed_corridor_count"],
                "corridor_intersection_cell_count": audit["intersection_cell_count"],
                "corridor_intersection_cell_signatures": audit["intersection_cell_signatures"],
                "corridor_partition_count": audit["corridor_partition_count"],
                "corridor_max_partition_node_count": audit["max_corridor_partition_node_count"],
                "corridor_named_corridors": audit["named_corridors"],
                "corridor_unnamed_corridors": audit["unnamed_corridors"],
                "corridor_top_partitions": audit["top_corridor_partitions"],
            }
        )
        enriched.append(enriched_cluster)
    return enriched


def _open_osm(osm_file: Path) -> BinaryIO:
    if osm_file.suffix.lower() == ".gz":
        return gzip.open(osm_file, "rb")
    return osm_file.open("rb")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _is_named_corridor(key: str) -> bool:
    return key.startswith("name:") or key.startswith("ref:")


def _intersection_cell_signatures(per_node_keys: dict[str, tuple[str, ...]]) -> list[str]:
    signatures = {
        _partition_key(keys)
        for keys in per_node_keys.values()
        if sum(1 for key in keys if _is_named_corridor(key)) >= 2
    }
    signatures.discard("")
    return sorted(signatures)


def _partition_key(keys: tuple[str, ...]) -> str:
    named = sorted(key for key in keys if _is_named_corridor(key))
    if len(named) >= 2:
        return "|".join(named)
    if len(named) == 1:
        return named[0]
    fallback = sorted(keys)
    return "|".join(fallback)


def _max_pair_distance(junctions: dict[str, dict[str, Any]], node_ids: list[str]) -> float:
    coordinates = [
        (float(junctions[node_id]["x"]), float(junctions[node_id]["y"]))
        for node_id in node_ids
        if node_id in junctions
    ]
    max_distance = 0.0
    for left in range(len(coordinates)):
        for right in range(left + 1, len(coordinates)):
            max_distance = max(
                max_distance,
                math.hypot(coordinates[left][0] - coordinates[right][0], coordinates[left][1] - coordinates[right][1]),
            )
    return max_distance
