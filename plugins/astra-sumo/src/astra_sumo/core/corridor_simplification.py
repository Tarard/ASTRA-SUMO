from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable
import xml.etree.ElementTree as ET

from .command_runner import run_command


PRESERVED_JUNCTION_ATTRS = ("type", "rightOfWay", "fringe")


def find_removable_corridor_geometry_nodes(
    net_file: Path,
    *,
    reference_net_file: Path | None = None,
    max_micro_edge_length_m: float = 1.0,
) -> list[dict[str, Any]]:
    """Find locally provable geometry-only corridor nodes.

    A candidate must be a priority node absent from the manual reference, have
    exactly one straight lane-preserving continuation per incoming lane, and
    connect edges with identical type, lane semantics, OSM params, and a
    non-empty road name. At least one incident edge must be a micro segment.
    """
    if max_micro_edge_length_m <= 0:
        raise ValueError("max_micro_edge_length_m must be positive")
    root = ET.parse(net_file).getroot()
    reference_node_ids = _reference_node_ids(reference_net_file)
    edges = {
        edge.attrib["id"]: edge
        for edge in root.findall("edge")
        if edge.attrib.get("id")
        and not edge.attrib["id"].startswith(":")
        and edge.attrib.get("from")
        and edge.attrib.get("to")
    }
    incident: dict[str, list[ET.Element]] = defaultdict(list)
    for edge in edges.values():
        incident[edge.attrib["from"]].append(edge)
        incident[edge.attrib["to"]].append(edge)

    local_connections: dict[str, list[ET.Element]] = defaultdict(list)
    for connection in root.findall("connection"):
        from_edge = edges.get(connection.attrib.get("from", ""))
        to_edge = edges.get(connection.attrib.get("to", ""))
        if from_edge is None or to_edge is None:
            continue
        node_id = from_edge.attrib.get("to", "")
        if node_id and node_id == to_edge.attrib.get("from", ""):
            local_connections[node_id].append(connection)
    modal_internal_node_ids = {
        _internal_owner_id(edge.attrib.get("id", ""))
        for edge in root.findall("edge")
        if edge.attrib.get("function") in {"crossing", "walkingarea"}
    }
    crossing_protected_edge_ids = {
        edge_id
        for edge in root.findall("edge")
        if edge.attrib.get("function") == "crossing"
        for edge_id in edge.attrib.get("crossingEdges", "").split()
        if edge_id
    }

    candidates: list[dict[str, Any]] = []
    for junction in root.findall("junction"):
        node_id = junction.attrib.get("id", "")
        node_edges = incident.get(node_id, [])
        if not node_id or node_id.startswith(":") or junction.attrib.get("type") != "priority":
            continue
        if node_id in modal_internal_node_ids:
            continue
        if reference_node_ids is not None and node_id in reference_node_ids:
            continue
        if len(node_edges) not in {2, 4} or any(edge.attrib.get("function") for edge in node_edges):
            continue
        if any(edge.attrib.get("id", "") in crossing_protected_edge_ids for edge in node_edges):
            continue
        edge_lengths = [_edge_length(edge) for edge in node_edges]
        if not edge_lengths or min(edge_lengths) > max_micro_edge_length_m:
            continue
        names = {_edge_name(edge) for edge in node_edges}
        if len(names) != 1 or not next(iter(names), ""):
            continue
        if len({_edge_params(edge) for edge in node_edges}) != 1:
            continue
        if len({edge.attrib.get("type", "") for edge in node_edges}) != 1:
            continue
        if len({_lane_semantic_signature(edge) for edge in node_edges}) != 1:
            continue

        incoming = [edge for edge in node_edges if edge.attrib.get("to") == node_id]
        outgoing = [edge for edge in node_edges if edge.attrib.get("from") == node_id]
        external_nodes = {
            edge.attrib.get("from") if edge.attrib.get("to") == node_id else edge.attrib.get("to")
            for edge in node_edges
        }
        connections = local_connections.get(node_id, [])
        expected_connection_count = sum(len(edge.findall("lane")) for edge in incoming)
        if len(incoming) != len(outgoing) or len(external_nodes) != 2:
            continue
        if len(connections) != expected_connection_count or any(connection.attrib.get("tl") for connection in connections):
            continue
        if not _connections_are_lane_preserving_straights(connections, edges):
            continue

        params = dict(_edge_params(node_edges[0]))
        candidates.append(
            {
                "node_id": node_id,
                "corridor_name": next(iter(names)),
                "corridor_ref": params.get("ref", ""),
                "edge_type": node_edges[0].attrib.get("type", ""),
                "incident_edge_ids": sorted(edge.attrib["id"] for edge in node_edges),
                "incoming_edge_count": len(incoming),
                "outgoing_edge_count": len(outgoing),
                "lane_count_per_edge": len(node_edges[0].findall("lane")),
                "minimum_incident_edge_length_m": round(min(edge_lengths), 3),
                "reference_node_absent": reference_node_ids is not None,
                "proof": "same_corridor_same_semantics_lane_preserving_micro_segment",
            }
        )
    return sorted(candidates, key=lambda item: str(item["node_id"]))


def build_corridor_geometry_simplification_variant(
    *,
    net_file: Path,
    output_dir: Path,
    prefix: str = "corridor_geometry_simplification",
    reference_net_file: Path | None = None,
    max_micro_edge_length_m: float = 1.0,
    netconvert_binary: str = "netconvert",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., Any] = run_command,
) -> dict[str, Any]:
    if not net_file.exists():
        return _failure(f"net file does not exist: {net_file}")
    if reference_net_file is not None and not reference_net_file.exists():
        return _failure(f"reference net file does not exist: {reference_net_file}")
    try:
        candidates = find_removable_corridor_geometry_nodes(
            net_file,
            reference_net_file=reference_net_file,
            max_micro_edge_length_m=max_micro_edge_length_m,
        )
    except (OSError, ET.ParseError, ValueError) as exc:
        return _failure(f"{type(exc).__name__}: {exc}")

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_file = output_dir / f"{prefix}_plan.json"
    candidates_file = output_dir / f"{prefix}_candidates.csv"
    keep_edges_file = output_dir / f"{prefix}_keep_edges.txt"
    command_file = output_dir / f"{prefix}_netconvert.cmd.txt"
    variant_file = output_dir / f"{prefix}_simplified.net.xml"
    _write_candidates(candidates_file, candidates)

    source_root = ET.parse(net_file).getroot()
    normal_edge_ids = {
        edge.attrib["id"]
        for edge in source_root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib["id"].startswith(":")
    }
    candidate_edge_ids = {
        edge_id for candidate in candidates for edge_id in candidate["incident_edge_ids"]
    }
    keep_edge_ids = sorted(normal_edge_ids - candidate_edge_ids)
    keep_edges_file.write_text("\n".join(keep_edge_ids) + ("\n" if keep_edge_ids else ""), encoding="utf-8")
    plan = {
        "status": "planned" if candidates else "not_needed",
        "net_file": str(net_file.resolve()),
        "reference_net_file": str(reference_net_file.resolve()) if reference_net_file is not None else "",
        "variant_file": str(variant_file) if candidates else "",
        "candidate_node_count": len(candidates),
        "candidate_edge_count": len(candidate_edge_ids),
        "keep_edge_count": len(keep_edge_ids),
        "max_micro_edge_length_m": max_micro_edge_length_m,
        "candidates": candidates,
    }
    plan_file.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    if not candidates:
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "corridor_geometry_simplification_status": "not_needed",
            "candidate_node_count": 0,
            "plan_file": str(plan_file),
            "candidates_file": str(candidates_file),
            "variant_file": "",
            "warnings": [],
        }

    command = [
        netconvert_binary,
        "--sumo-net-file",
        str(net_file.resolve()),
        "--geometry.remove",
        "--geometry.remove.keep-edges.input-file",
        str(keep_edges_file),
        "--geometry.remove.keep-ptstops",
        "--output.removed-nodes",
        "--output-file",
        str(variant_file),
    ]
    command_file.write_text(" ".join(command) + "\n", encoding="utf-8")
    try:
        result = _result_to_dict(command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds))
    except OSError as exc:
        return _failure(f"{type(exc).__name__}: {exc}")
    if result.get("status") != "pass" or not variant_file.exists():
        return {
            **_failure("netconvert did not create the corridor simplification variant"),
            "netconvert": result,
            "variant_file": str(variant_file),
            "plan_file": str(plan_file),
        }

    restored_junction_count = _restore_surviving_junction_semantics(net_file, variant_file)
    connection_audit = audit_alias_normalized_connections(net_file, variant_file)
    source_counts = _semantic_counts(net_file)
    candidate_counts = _semantic_counts(variant_file)
    source_junctions = _junction_ids(net_file)
    candidate_junctions = _junction_ids(variant_file)
    removed_nodes = sorted(source_junctions - candidate_junctions)
    planned_nodes = {str(candidate["node_id"]) for candidate in candidates}
    unexpected_removed_nodes = sorted(set(removed_nodes) - planned_nodes)
    preservation_fields = (
        "controlled_connection_count",
        "active_tl_logic_count",
        "crossing_edge_count",
        "walkingarea_edge_count",
    )
    preservation_deltas = {
        field: candidate_counts[field] - source_counts[field]
        for field in preservation_fields
        if candidate_counts[field] != source_counts[field]
    }
    source_active_tls_ids = _active_tls_ids(net_file)
    candidate_active_tls_ids = _active_tls_ids(variant_file)
    missing_active_tls_ids = sorted(source_active_tls_ids - candidate_active_tls_ids)
    extra_active_tls_ids = sorted(candidate_active_tls_ids - source_active_tls_ids)
    preservation_status = (
        "pass"
        if not preservation_deltas
        and not unexpected_removed_nodes
        and not missing_active_tls_ids
        and not extra_active_tls_ids
        and connection_audit["controlled_missing_count"] == 0
        and connection_audit["controlled_extra_count"] == 0
        else "fail"
    )
    return {
        "status": "pass" if preservation_status == "pass" else "fail",
        "claim_status": "blocked" if preservation_status == "pass" else "construction-invalid",
        "corridor_geometry_simplification_status": (
            "variant_created_for_review" if preservation_status == "pass" else "semantic_preservation_failed"
        ),
        "variant_file": str(variant_file),
        "plan_file": str(plan_file),
        "candidates_file": str(candidates_file),
        "keep_edges_file": str(keep_edges_file),
        "command_file": str(command_file),
        "candidate_node_count": len(candidates),
        "removed_node_count": len(removed_nodes),
        "removed_node_ids": removed_nodes,
        "missed_candidate_node_ids": sorted(planned_nodes - set(removed_nodes)),
        "unexpected_removed_node_ids": unexpected_removed_nodes,
        "restored_junction_semantics_count": restored_junction_count,
        "semantic_preservation_status": preservation_status,
        "semantic_preservation_deltas": preservation_deltas,
        "missing_active_tls_ids": missing_active_tls_ids,
        "extra_active_tls_ids": extra_active_tls_ids,
        "edge_alias_count": connection_audit["edge_alias_count"],
        "edge_aliases": connection_audit["edge_aliases"],
        "alias_normalized_connection_audit": connection_audit,
        "source_counts": source_counts,
        "candidate_counts": candidate_counts,
        "netconvert": result,
        "warnings": ["corridor geometry simplification requires SUMO, routeability, topology, and alias-normalized connection review"],
    }


def _connections_are_lane_preserving_straights(
    connections: list[ET.Element], edges: dict[str, ET.Element]
) -> bool:
    seen_from_lanes: set[tuple[str, str]] = set()
    for connection in connections:
        from_edge = edges[connection.attrib["from"]]
        to_edge = edges[connection.attrib["to"]]
        if from_edge.attrib.get("from") == to_edge.attrib.get("to"):
            return False
        if connection.attrib.get("fromLane") != connection.attrib.get("toLane"):
            return False
        if connection.attrib.get("dir") not in {"s", ""}:
            return False
        signature = (connection.attrib["from"], connection.attrib.get("fromLane", ""))
        if signature in seen_from_lanes:
            return False
        seen_from_lanes.add(signature)
    return True


def audit_alias_normalized_connections(source_file: Path, candidate_file: Path) -> dict[str, Any]:
    source_root = ET.parse(source_file).getroot()
    candidate_root = ET.parse(candidate_file).getroot()
    aliases = _corridor_edge_aliases(source_root, candidate_root)
    source_all, source_controlled, collapsed = _connection_counters(source_root, aliases)
    candidate_all, candidate_controlled, _ = _connection_counters(candidate_root, {})
    all_missing = source_all - candidate_all
    all_extra = candidate_all - source_all
    controlled_missing = source_controlled - candidate_controlled
    controlled_extra = candidate_controlled - source_controlled
    return {
        "status": "pass" if not controlled_missing and not controlled_extra else "fail",
        "edge_alias_count": len(aliases),
        "edge_aliases": dict(sorted(aliases.items())),
        "collapsed_source_connection_count": collapsed,
        "normal_missing_count": sum(all_missing.values()),
        "normal_extra_count": sum(all_extra.values()),
        "controlled_missing_count": sum(controlled_missing.values()),
        "controlled_extra_count": sum(controlled_extra.values()),
        "normal_missing_sample": _counter_sample(all_missing),
        "normal_extra_sample": _counter_sample(all_extra),
        "controlled_missing_sample": _counter_sample(controlled_missing),
        "controlled_extra_sample": _counter_sample(controlled_extra),
    }


def _corridor_edge_aliases(source_root: ET.Element, candidate_root: ET.Element) -> dict[str, str]:
    source_edges = {
        edge.attrib["id"]: edge
        for edge in source_root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib["id"].startswith(":")
    }
    aliases: dict[str, str] = {}
    for candidate in candidate_root.findall("edge"):
        candidate_id = candidate.attrib.get("id", "")
        if not candidate_id or candidate_id.startswith(":"):
            continue
        removed_nodes = {
            node_id
            for param in candidate.findall("param")
            if param.attrib.get("key") == "removedNodeIds"
            for node_id in param.attrib.get("value", "").split()
            if node_id
        }
        if not removed_nodes:
            continue
        start = candidate.attrib.get("from", "")
        target = candidate.attrib.get("to", "")
        allowed_nodes = {start, target, *removed_nodes}
        current = start
        previous = ""
        path_ids: list[str] = []
        seen_nodes: set[str] = set()
        while current and current != target and current not in seen_nodes:
            seen_nodes.add(current)
            choices = [
                edge
                for edge in source_edges.values()
                if edge.attrib.get("from") == current
                and edge.attrib.get("to") in allowed_nodes
                and edge.attrib.get("to") != previous
                and edge.attrib.get("type", "") == candidate.attrib.get("type", "")
            ]
            if len(choices) != 1:
                path_ids = []
                break
            edge = choices[0]
            path_ids.append(edge.attrib["id"])
            previous = current
            current = edge.attrib.get("to", "")
        if current == target and len(path_ids) >= 2:
            aliases.update({edge_id: candidate_id for edge_id in path_ids})
    return aliases


def _connection_counters(
    root: ET.Element, aliases: dict[str, str]
) -> tuple[Counter[tuple[str, ...]], Counter[tuple[str, ...]], int]:
    all_connections: Counter[tuple[str, ...]] = Counter()
    controlled_connections: Counter[tuple[str, ...]] = Counter()
    collapsed = 0
    for connection in root.findall("connection"):
        from_edge = connection.attrib.get("from", "")
        to_edge = connection.attrib.get("to", "")
        if not from_edge or not to_edge or from_edge.startswith(":") or to_edge.startswith(":"):
            continue
        mapped_from = aliases.get(from_edge, from_edge)
        mapped_to = aliases.get(to_edge, to_edge)
        if mapped_from == mapped_to:
            collapsed += 1
            continue
        signature = (
            mapped_from,
            mapped_to,
            connection.attrib.get("fromLane", ""),
            connection.attrib.get("toLane", ""),
            connection.attrib.get("tl", ""),
            connection.attrib.get("linkIndex", ""),
            connection.attrib.get("linkIndex2", ""),
            connection.attrib.get("dir", ""),
        )
        all_connections[signature] += 1
        if connection.attrib.get("tl"):
            controlled_connections[signature] += 1
    return all_connections, controlled_connections, collapsed


def _counter_sample(counter: Counter[tuple[str, ...]], limit: int = 20) -> list[dict[str, Any]]:
    fields = ("from", "to", "fromLane", "toLane", "tl", "linkIndex", "linkIndex2", "dir")
    return [
        {**dict(zip(fields, signature)), "count": count}
        for signature, count in sorted(counter.items())[:limit]
    ]


def _edge_name(edge: ET.Element) -> str:
    if edge.attrib.get("name"):
        return " ".join(edge.attrib["name"].casefold().split())
    params = dict(_edge_params(edge))
    return " ".join(params.get("name", "").casefold().split())


def _edge_params(edge: ET.Element) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((param.attrib.get("key", ""), param.attrib.get("value", "")) for param in edge.findall("param")))


def _edge_length(edge: ET.Element) -> float:
    values = []
    for lane in edge.findall("lane"):
        try:
            values.append(float(lane.attrib.get("length", "0") or 0))
        except ValueError:
            continue
    return min(values, default=float("inf"))


def _lane_semantic_signature(edge: ET.Element) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            lane.attrib.get("index", ""),
            lane.attrib.get("speed", ""),
            lane.attrib.get("allow", ""),
            lane.attrib.get("disallow", ""),
            lane.attrib.get("width", ""),
        )
        for lane in edge.findall("lane")
    )


def _reference_node_ids(reference_net_file: Path | None) -> set[str] | None:
    if reference_net_file is None:
        return None
    root = ET.parse(reference_net_file).getroot()
    return {junction.attrib["id"] for junction in root.findall("junction") if junction.attrib.get("id")}


def _internal_owner_id(edge_id: str) -> str:
    if not edge_id.startswith(":"):
        return ""
    value = edge_id[1:]
    if "_" not in value:
        return value
    return value.rsplit("_", 1)[0]


def _restore_surviving_junction_semantics(source_file: Path, candidate_file: Path) -> int:
    source_root = ET.parse(source_file).getroot()
    source_by_id = {junction.attrib["id"]: junction for junction in source_root.findall("junction") if junction.attrib.get("id")}
    tree = ET.parse(candidate_file)
    root = tree.getroot()
    restored = 0
    for junction in root.findall("junction"):
        source = source_by_id.get(junction.attrib.get("id", ""))
        if source is None:
            continue
        before = tuple(junction.attrib.get(field) for field in PRESERVED_JUNCTION_ATTRS)
        for field in PRESERVED_JUNCTION_ATTRS:
            if source.attrib.get(field) is None:
                junction.attrib.pop(field, None)
            else:
                junction.set(field, source.attrib[field])
        if before != tuple(junction.attrib.get(field) for field in PRESERVED_JUNCTION_ATTRS):
            restored += 1
    if restored:
        ET.indent(root, space="    ")
        tree.write(candidate_file, encoding="utf-8", xml_declaration=True)
    return restored


def _semantic_counts(net_file: Path) -> dict[str, int]:
    root = ET.parse(net_file).getroot()
    controlled_tls_ids = {connection.attrib["tl"] for connection in root.findall("connection") if connection.attrib.get("tl")}
    tl_logic_ids = {logic.attrib["id"] for logic in root.findall("tlLogic") if logic.attrib.get("id")}
    return {
        "normal_edge_count": sum(1 for edge in root.findall("edge") if not edge.attrib.get("id", "").startswith(":")),
        "junction_count": sum(1 for junction in root.findall("junction") if not junction.attrib.get("id", "").startswith(":")),
        "connection_count": len(root.findall("connection")),
        "controlled_connection_count": sum(1 for connection in root.findall("connection") if connection.attrib.get("tl")),
        "tl_logic_count": len(root.findall("tlLogic")),
        "active_tl_logic_count": len(controlled_tls_ids & tl_logic_ids),
        "orphan_tl_logic_count": len(tl_logic_ids - controlled_tls_ids),
        "crossing_edge_count": sum(1 for edge in root.findall("edge") if edge.attrib.get("function") == "crossing"),
        "walkingarea_edge_count": sum(1 for edge in root.findall("edge") if edge.attrib.get("function") == "walkingarea"),
    }


def _active_tls_ids(net_file: Path) -> set[str]:
    root = ET.parse(net_file).getroot()
    controlled = {connection.attrib["tl"] for connection in root.findall("connection") if connection.attrib.get("tl")}
    programs = {logic.attrib["id"] for logic in root.findall("tlLogic") if logic.attrib.get("id")}
    return controlled & programs


def _junction_ids(net_file: Path) -> set[str]:
    root = ET.parse(net_file).getroot()
    return {junction.attrib["id"] for junction in root.findall("junction") if junction.attrib.get("id") and not junction.attrib["id"].startswith(":")}


def _write_candidates(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "node_id", "corridor_name", "corridor_ref", "edge_type", "incident_edge_ids",
        "incoming_edge_count", "outgoing_edge_count", "lane_count_per_edge",
        "minimum_incident_edge_length_m", "reference_node_absent", "proof",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "incident_edge_ids": ";".join(row["incident_edge_ids"])})


def _result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        report = dict(result)
        if "status" not in report and "returncode" in report:
            report["status"] = "pass" if report.get("returncode") == 0 else "fail"
        return report
    return {
        "status": "pass" if getattr(result, "returncode", 1) == 0 else "fail",
        "returncode": getattr(result, "returncode", 1),
        "stdout": getattr(result, "stdout", ""),
        "stderr": getattr(result, "stderr", ""),
    }


def _failure(error: str) -> dict[str, Any]:
    return {"status": "fail", "claim_status": "construction-invalid", "error": error, "warnings": [error]}
