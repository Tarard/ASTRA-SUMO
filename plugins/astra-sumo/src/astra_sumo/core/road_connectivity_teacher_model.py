from __future__ import annotations

import copy
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


# These are the operational mode groups that matter for the OSM-to-reference
# comparison.  SUMO accepts a larger and version-dependent vClass vocabulary;
# the raw allow/disallow fields remain in the report, while this stable subset
# prevents equivalent permission expressions from becoming false road gaps.
ROAD_SEMANTIC_MODE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("passenger", ("passenger", "private", "taxi", "delivery", "truck", "trailer")),
    ("bus", ("bus", "coach")),
    ("bicycle", ("bicycle",)),
    ("pedestrian", ("pedestrian", "wheelchair")),
    ("motorcycle", ("motorcycle", "moped", "scooter")),
    ("rail", ("tram", "rail_urban", "rail", "rail_electric", "rail_fast")),
)


def canonical_road_connectivity_bundle(
    net_file: Path,
    *,
    seed_edge_ids: list[str],
    hop_radius: int = 1,
) -> dict[str, Any]:
    root = ET.parse(net_file).getroot()
    edges = {edge.attrib["id"]: edge for edge in root.findall("edge") if edge.attrib.get("id")}
    missing_seed_edge_ids = sorted(edge_id for edge_id in seed_edge_ids if edge_id not in edges)
    selected = {edge_id for edge_id in seed_edge_ids if edge_id in edges and not edge_id.startswith(":")}
    for _ in range(max(0, hop_radius)):
        endpoints = {
            value
            for edge_id in selected
            for value in (edges[edge_id].attrib.get("from", ""), edges[edge_id].attrib.get("to", ""))
            if value
        }
        selected.update(
            edge_id
            for edge_id, edge in edges.items()
            if not edge_id.startswith(":")
            and (edge.attrib.get("from", "") in endpoints or edge.attrib.get("to", "") in endpoints)
        )

    selected_junction_ids = {
        value
        for edge_id in selected
        for value in (edges[edge_id].attrib.get("from", ""), edges[edge_id].attrib.get("to", ""))
        if value
    }
    selected_lane_ids = {
        lane.attrib["id"]
        for edge_id in selected
        for lane in edges[edge_id].findall("lane")
        if lane.attrib.get("id")
    }
    junctions = {
        junction.attrib["id"]: junction
        for junction in root.findall("junction")
        if junction.attrib.get("id")
    }
    connections = [
        _canonical_road_connection_record(connection)
        for connection in root.findall("connection")
        if connection.attrib.get("from", "") in selected and connection.attrib.get("to", "") in selected
    ]
    request_counts = _request_counts_by_junction(connections, edges)
    bundle = {
        "net": _sorted_attrs(root),
        "location": _sorted_attrs(root.find("location")),
        "edges": [_canonical_edge_record(edges[edge_id]) for edge_id in sorted(selected)],
        "junctions": [
            _canonical_junction_record(
                junctions[junction_id],
                selected_lane_ids,
                request_counts.get(junction_id, 0),
            )
            for junction_id in sorted(selected_junction_ids)
            if junction_id in junctions
        ],
        "connections": sorted(connections, key=_canonical_connection_sort_key),
    }
    bundle["summary"] = {
        "edge_count": len(bundle["edges"]),
        "junction_count": len(bundle["junctions"]),
        "connection_count": len(bundle["connections"]),
        "missing_reference_count": _missing_reference_count(bundle),
        "seed_edge_count": len(seed_edge_ids),
        "missing_seed_edge_ids": missing_seed_edge_ids,
    }
    return bundle


def write_road_connectivity_self_replay_net(
    teacher_net_file: Path,
    seed_edge_ids: list[str],
    output_file: Path,
    *,
    hop_radius: int = 1,
) -> dict[str, Any]:
    bundle = canonical_road_connectivity_bundle(
        teacher_net_file,
        seed_edge_ids=seed_edge_ids,
        hop_radius=hop_radius,
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root = ET.Element("net", bundle.get("net", {}))
    if bundle["location"]:
        ET.SubElement(root, "location", bundle["location"])
    for edge in bundle["edges"]:
        edge_node = ET.SubElement(root, "edge", _record_attrs(edge, "lanes"))
        for lane in edge.get("lanes", []):
            ET.SubElement(edge_node, "lane", dict(lane))
    for junction in bundle["junctions"]:
        junction_node = ET.SubElement(root, "junction", _record_attrs(junction, "requests"))
        for request in junction.get("requests", []):
            ET.SubElement(junction_node, "request", dict(request))
    for connection in bundle["connections"]:
        ET.SubElement(root, "connection", dict(connection))

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(output_file, encoding="utf-8", xml_declaration=True)
    replay_bundle = canonical_road_connectivity_bundle(
        output_file,
        seed_edge_ids=seed_edge_ids,
        hop_radius=hop_radius,
    )
    parity_delta = {} if bundle == replay_bundle else {"canonical_bundle": 1}
    return {
        "status": "pass" if not parity_delta else "fail",
        "output_file": str(output_file),
        "parity_delta": parity_delta,
    }


def write_road_connectivity_split_root_alias_repair_candidate(
    candidate_net_file: Path,
    output_file: Path,
    split_root_aliases: list[dict[str, str]],
) -> dict[str, Any]:
    tree = ET.parse(candidate_net_file)
    root = tree.getroot()
    edges = {
        edge.attrib.get("id", ""): edge
        for edge in root.findall("edge")
        if edge.attrib.get("id")
    }
    renamed_edge_count = 0
    skipped_aliases = []
    for alias in split_root_aliases:
        candidate_edge_id = str(alias.get("candidate_edge_id", "")).strip()
        teacher_edge_id = str(alias.get("teacher_edge_id", "")).strip()
        if (
            not candidate_edge_id
            or not teacher_edge_id
            or candidate_edge_id == teacher_edge_id
            or candidate_edge_id not in edges
            or teacher_edge_id in edges
        ):
            skipped_aliases.append(alias)
            continue
        edge = edges.pop(candidate_edge_id)
        edge.attrib["id"] = teacher_edge_id
        for lane in edge.findall("lane"):
            lane_id = lane.attrib.get("id", "")
            renamed_lane_id = _renamed_lane_id(lane_id, candidate_edge_id, teacher_edge_id)
            if renamed_lane_id != lane_id:
                lane.attrib["id"] = renamed_lane_id
        _rename_edge_references(root, candidate_edge_id, teacher_edge_id)
        edges[teacher_edge_id] = edge
        renamed_edge_count += 1

    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "candidate_net_file": str(candidate_net_file),
        "output_file": str(output_file),
        "selected_alias_count": len(split_root_aliases),
        "renamed_edge_count": renamed_edge_count,
        "skipped_alias_count": len(skipped_aliases),
        "skipped_aliases": skipped_aliases,
        "warnings": [],
    }


def compare_road_connectivity_bundles(
    teacher: dict[str, Any],
    candidate: dict[str, Any],
    *,
    geometry_tolerance: float = 0.5,
) -> dict[str, Any]:
    teacher_edge_ids = _record_ids(teacher.get("edges", []))
    candidate_edge_ids = _record_ids(candidate.get("edges", []))
    common_edge_ids = teacher_edge_ids & candidate_edge_ids
    missing_edges = sorted(teacher_edge_ids - candidate_edge_ids)
    extra_edges = sorted(candidate_edge_ids - teacher_edge_ids)
    split_root_aliases = _unambiguous_split_root_aliases(missing_edges, extra_edges)
    teacher_edge_alias_map = {
        alias["teacher_edge_id"]: alias["candidate_edge_id"]
        for alias in split_root_aliases
    }
    geometry_mismatches = _common_edge_geometry_mismatches(
        teacher,
        candidate,
        common_edge_ids,
        geometry_tolerance,
    )
    teacher_connections = _aliased_road_connections(teacher.get("connections", []), teacher_edge_alias_map)
    candidate_connections = _aliased_road_connections(candidate.get("connections", []), {})
    missing_connections = _missing_records(teacher_connections, candidate_connections)
    extra_connections = _missing_records(candidate_connections, teacher_connections)
    candidate_missing_seed_edge_ids = sorted(
        str(edge_id)
        for edge_id in candidate.get("summary", {}).get("missing_seed_edge_ids", [])
    )
    status = "fail" if any(
        [
            candidate_missing_seed_edge_ids,
            missing_edges,
            extra_edges,
            geometry_mismatches,
            missing_connections,
            extra_connections,
        ]
    ) else "pass"
    edge_id_report: dict[str, Any] = {
        "missing_in_candidate": missing_edges,
        "extra_in_candidate": extra_edges,
    }
    summary: dict[str, Any] = {
        "teacher_edge_count": len(teacher.get("edges", [])),
        "candidate_edge_count": len(candidate.get("edges", [])),
        "common_edge_count": len(common_edge_ids),
        "common_edge_geometry_mismatch_count": len(geometry_mismatches),
        "teacher_connection_count": len(teacher.get("connections", [])),
        "candidate_connection_count": len(candidate.get("connections", [])),
    }
    if split_root_aliases:
        edge_id_report["split_root_aliases"] = split_root_aliases
        summary["split_root_alias_count"] = len(split_root_aliases)
    return {
        "status": status,
        "candidate_missing_seed_edge_ids": candidate_missing_seed_edge_ids,
        "edge_ids": edge_id_report,
        "common_edge_geometry_mismatches": geometry_mismatches,
        "connections": {
            "missing_in_candidate": missing_connections,
            "extra_in_candidate": extra_connections,
        },
        "summary": summary,
    }


def compare_road_template_summaries(
    teacher_templates: list[dict[str, Any]],
    candidate_templates: list[dict[str, Any]],
    *,
    key_fields: list[str],
) -> dict[str, Any]:
    teacher_by_key = {
        _template_summary_key(template, key_fields): template
        for template in teacher_templates
    }
    candidate_by_key = {
        _template_summary_key(template, key_fields): template
        for template in candidate_templates
    }
    common_keys = set(teacher_by_key) & set(candidate_by_key)
    missing_templates = _sort_templates_by_count(
        teacher_by_key[key] for key in set(teacher_by_key) - set(candidate_by_key)
    )
    extra_templates = _sort_templates_by_count(
        candidate_by_key[key] for key in set(candidate_by_key) - set(teacher_by_key)
    )
    common_count_delta_sum = sum(
        abs(
            int(teacher_by_key[key].get("count", 0))
            - int(candidate_by_key[key].get("count", 0))
        )
        for key in common_keys
    )
    status = "fail" if missing_templates or extra_templates or common_count_delta_sum else "pass"
    return {
        "status": status,
        "missing_template_count": len(missing_templates),
        "extra_template_count": len(extra_templates),
        "common_template_count": len(common_keys),
        "common_count_delta_sum": common_count_delta_sum,
        "missing_templates": missing_templates,
        "extra_templates": extra_templates,
    }


def build_road_template_repair_queue(
    parity_report: dict[str, Any],
    *,
    max_items: int = 10,
) -> list[dict[str, Any]]:
    items = []
    for layer, summary_key in (
        ("lane", "lane_template_summary"),
        ("connection", "connection_template_summary"),
    ):
        parity = parity_report.get(summary_key, {}).get("parity", {})
        for difference, template_key in (
            ("missing_teacher_template", "missing_templates"),
            ("extra_candidate_template", "extra_templates"),
        ):
            for template in parity.get(template_key, []):
                if not isinstance(template, dict):
                    continue
                items.append(
                    {
                        "layer": layer,
                        "difference": difference,
                        "priority": int(template.get("count", 0)),
                        "template": dict(template),
                    }
                )
    return sorted(
        items,
        key=lambda item: (
            -int(item["priority"]),
            str(item["layer"]),
            str(item["difference"]),
            _record_key(item["template"]),
        ),
    )[:max_items]


def build_road_lane_template_repair_candidates(
    parity_report: dict[str, Any],
    *,
    max_items: int = 10,
) -> list[dict[str, Any]]:
    parity = parity_report.get("lane_template_summary", {}).get("parity", {})
    missing_templates = _sort_templates_by_count(parity.get("missing_templates", []))
    extra_templates = _sort_templates_by_count(parity.get("extra_templates", []))
    candidates = []
    for extra in extra_templates:
        extra_signature = [str(item) for item in extra.get("lane_signature", [])]
        extra_indexes = _lane_signature_indexes(extra_signature)
        for missing in missing_templates:
            missing_signature = [str(item) for item in missing.get("lane_signature", [])]
            if (
                str(extra.get("type", "")) != str(missing.get("type", ""))
                or len(extra_signature) != len(missing_signature)
                or extra_indexes != _lane_signature_indexes(missing_signature)
            ):
                continue
            candidates.append(
                {
                    "action": "replace_lane_signature",
                    "type": str(extra.get("type", "")),
                    "priority": min(int(extra.get("count", 0)), int(missing.get("count", 0))),
                    "candidate_count": int(extra.get("count", 0)),
                    "teacher_count": int(missing.get("count", 0)),
                    "from_lane_signature": extra_signature,
                    "to_lane_signature": missing_signature,
                }
            )
    return sorted(
        candidates,
        key=lambda item: (-int(item["priority"]), str(item["type"]), _record_key(item)),
    )[:max_items]


def build_road_lane_template_edge_subset_repair_candidates(
    teacher_net_file: Path,
    candidate_net_file: Path,
    parity_report: dict[str, Any],
    *,
    max_items: int = 10,
) -> list[dict[str, Any]]:
    teacher_edges = _road_lane_template_edge_index(teacher_net_file)
    candidate_edges = _road_lane_template_edge_index(candidate_net_file)
    candidates = []
    for candidate in build_road_lane_template_repair_candidates(parity_report, max_items=max_items):
        from_signature = tuple(str(item) for item in candidate.get("from_lane_signature", []))
        to_signature = tuple(str(item) for item in candidate.get("to_lane_signature", []))
        edge_type = str(candidate.get("type", ""))
        edge_ids = [
            edge_id
            for edge_id, candidate_record in candidate_edges.items()
            if candidate_record == (edge_type, from_signature)
            and teacher_edges.get(edge_id) == (edge_type, to_signature)
        ]
        if not edge_ids:
            continue
        subset_candidate = dict(candidate)
        subset_candidate["edge_ids"] = sorted(edge_ids)
        subset_candidate["edge_count"] = len(edge_ids)
        subset_candidate["priority"] = len(edge_ids)
        candidates.append(subset_candidate)
    return sorted(
        candidates,
        key=lambda item: (-int(item["edge_count"]), str(item["type"]), _record_key(item)),
    )[:max_items]


def build_road_lane_template_single_edge_repair_candidates(
    teacher_net_file: Path,
    candidate_net_file: Path,
    parity_report: dict[str, Any],
    *,
    max_items: int = 10,
) -> list[dict[str, Any]]:
    candidates = []
    for candidate in build_road_lane_template_edge_subset_repair_candidates(
        teacher_net_file,
        candidate_net_file,
        parity_report,
        max_items=max_items,
    ):
        for edge_id in candidate.get("edge_ids", []):
            single_edge_candidate = dict(candidate)
            single_edge_candidate["edge_ids"] = [str(edge_id)]
            single_edge_candidate["edge_count"] = 1
            single_edge_candidate["priority"] = 1
            candidates.append(single_edge_candidate)
    return candidates[:max_items]


def write_road_lane_template_repair_candidate(
    candidate_net_file: Path,
    output_file: Path,
    repair_candidates: list[dict[str, Any]],
    *,
    max_candidates: int | None = None,
) -> dict[str, Any]:
    selected_candidates = repair_candidates[:max_candidates] if max_candidates is not None else repair_candidates
    tree = ET.parse(candidate_net_file)
    root = tree.getroot()
    changed_edge_count = 0
    changed_lane_count = 0
    applied_candidate_ids: set[int] = set()
    for edge in root.findall("edge"):
        if edge.attrib.get("function") == "internal":
            continue
        edge_id = str(edge.attrib.get("id", ""))
        for candidate_index, candidate in enumerate(selected_candidates):
            if str(candidate.get("action", "")) != "replace_edge_lanes":
                continue
            edge_ids = {str(edge_id) for edge_id in candidate.get("edge_ids", [])}
            if edge_id != str(candidate.get("edge_id", "")) and edge_id not in edge_ids:
                continue
            changed_lanes = _replace_edge_lanes(edge, candidate.get("lanes", []))
            if changed_lanes:
                changed_edge_count += 1
                changed_lane_count += changed_lanes
                applied_candidate_ids.add(candidate_index)
            break

        edge_record = _canonical_edge_record(edge)
        edge_signature = _lane_signature(edge_record)
        edge_type = str(edge_record.get("type", ""))
        for candidate_index, candidate in enumerate(selected_candidates):
            edge_ids = {str(edge_id) for edge_id in candidate.get("edge_ids", [])}
            if (
                str(candidate.get("action", "")) != "replace_lane_signature"
                or edge_type != str(candidate.get("type", ""))
                or edge_signature != [str(item) for item in candidate.get("from_lane_signature", [])]
                or (edge_ids and str(edge.attrib.get("id", "")) not in edge_ids)
            ):
                continue
            changed_lanes = _apply_lane_signature(edge, [str(item) for item in candidate.get("to_lane_signature", [])])
            if changed_lanes:
                changed_edge_count += 1
                changed_lane_count += changed_lanes
                applied_candidate_ids.add(candidate_index)
            break

    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "candidate_net_file": str(candidate_net_file),
        "output_file": str(output_file),
        "selected_candidate_count": len(selected_candidates),
        "applied_candidate_count": len(applied_candidate_ids),
        "changed_edge_count": changed_edge_count,
        "changed_lane_count": changed_lane_count,
        "warnings": [],
    }


LANE_TEMPLATE_GATE_METRICS = (
    "lane_missing_template_count",
    "lane_extra_template_count",
    "lane_common_count_delta_sum",
)

ROAD_TEMPLATE_GATE_METRICS = (
    *LANE_TEMPLATE_GATE_METRICS,
    "connection_missing_template_count",
    "connection_extra_template_count",
    "connection_common_count_delta_sum",
)


def evaluate_road_template_repair_promotion(
    before_report: dict[str, Any],
    after_report: dict[str, Any],
    *,
    metric_scope: str = "road",
) -> dict[str, Any]:
    metrics = LANE_TEMPLATE_GATE_METRICS if metric_scope == "lane" else ROAD_TEMPLATE_GATE_METRICS
    before_metrics = _road_template_gate_metrics(before_report, metrics)
    after_metrics = _road_template_gate_metrics(after_report, metrics)
    deltas = {
        key: after_metrics[key] - before_metrics[key]
        for key in metrics
    }
    improved_metrics = {key: value for key, value in deltas.items() if value < 0}
    worsened_metrics = {key: value for key, value in deltas.items() if value > 0}
    before_score = sum(before_metrics.values())
    after_score = sum(after_metrics.values())
    if worsened_metrics:
        promotion_status = "blocked"
        reason = "road_template_gate_metric_worsened"
    elif after_score < before_score:
        promotion_status = "pass"
        reason = "road_template_gate_improved"
    else:
        promotion_status = "blocked"
        reason = "road_template_gate_not_improved"
    return {
        "status": "pass" if promotion_status == "pass" else "fail",
        "claim_status": "diagnostic-demo",
        "metric_scope": metric_scope,
        "promotion_status": promotion_status,
        "reason": reason,
        "before_score": before_score,
        "after_score": after_score,
        "score_delta": after_score - before_score,
        "improved_metrics": improved_metrics,
        "worsened_metrics": worsened_metrics,
    }


def _evaluate_road_lane_local_replay_promotion(
    repair_candidate: dict[str, Any],
    repair_report: dict[str, Any],
) -> dict[str, Any]:
    expected_edges = int(repair_candidate.get("edge_count", len(repair_candidate.get("edge_ids", []))))
    changed_edges = int(repair_report.get("changed_edge_count", 0))
    changed_lanes = int(repair_report.get("changed_lane_count", 0))
    promotion_status = "pass" if expected_edges and changed_edges == expected_edges and changed_lanes else "blocked"
    return {
        "status": "pass" if promotion_status == "pass" else "fail",
        "claim_status": "diagnostic-demo",
        "metric_scope": "local_lane",
        "promotion_status": promotion_status,
        "reason": "road_lane_local_replay_applied"
        if promotion_status == "pass"
        else "road_lane_local_replay_not_applied",
        "before_score": expected_edges,
        "after_score": max(0, expected_edges - changed_edges),
        "score_delta": -changed_edges,
        "improved_metrics": {"changed_edge_count": changed_edges} if changed_edges else {},
        "worsened_metrics": {},
    }


def run_road_lane_template_repair_probe(
    teacher_net_file: Path,
    candidate_net_file: Path,
    output_dir: Path,
    *,
    prefix: str = "road_lane_template_repair",
    max_candidates: int = 10,
    max_examples: int = 3,
    use_teacher_edge_subset: bool = True,
    use_single_edge: bool = False,
    promotion_metric_scope: str = "lane",
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    before_report = compare_net_road_template_parity(
        teacher_net_file,
        candidate_net_file,
        max_examples=max_examples,
    )
    if use_single_edge:
        repair_scope = "teacher_single_edge"
        repair_candidates = build_road_lane_template_single_edge_repair_candidates(
            teacher_net_file,
            candidate_net_file,
            before_report,
            max_items=max_candidates,
        )
    elif use_teacher_edge_subset:
        repair_scope = "teacher_edge_subset"
        repair_candidates = build_road_lane_template_edge_subset_repair_candidates(
            teacher_net_file,
            candidate_net_file,
            before_report,
            max_items=max_candidates,
        )
    else:
        repair_scope = "template"
        repair_candidates = build_road_lane_template_repair_candidates(
            before_report,
            max_items=max_candidates,
        )
    candidate_reports = []
    for index, repair_candidate in enumerate(repair_candidates, start=1):
        variant_file = output_dir / f"{prefix}_{index:03d}.net.xml"
        repair_report = write_road_lane_template_repair_candidate(
            candidate_net_file,
            variant_file,
            [repair_candidate],
        )
        after_report = compare_net_road_template_parity(
            teacher_net_file,
            variant_file,
            max_examples=max_examples,
        )
        promotion_gate = (
            _evaluate_road_lane_local_replay_promotion(repair_candidate, repair_report)
            if promotion_metric_scope == "local_lane"
            else evaluate_road_template_repair_promotion(
                before_report,
                after_report,
                metric_scope=promotion_metric_scope,
            )
        )
        candidate_reports.append(
            {
                "candidate_index": index,
                "variant_file": str(variant_file),
                "repair_candidate": repair_candidate,
                "repair_report": repair_report,
                "promotion_gate": promotion_gate,
                "after_gate": after_report["gate"],
            }
        )

    pass_candidates = [
        item
        for item in candidate_reports
        if item["promotion_gate"].get("promotion_status") == "pass"
    ]
    best_candidate = min(
        pass_candidates,
        key=lambda item: (
            int(item["promotion_gate"].get("after_score", 0)),
            int(item["candidate_index"]),
        ),
        default=None,
    )
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "road_lane_template_repair_status": "evaluated" if repair_candidates else "no_candidates",
        "repair_scope": repair_scope,
        "promotion_metric_scope": promotion_metric_scope,
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "output_dir": str(output_dir),
        "candidate_count": len(candidate_reports),
        "pass_candidate_count": len(pass_candidates),
        "blocked_candidate_count": len(candidate_reports) - len(pass_candidates),
        "best_candidate_index": 0 if best_candidate is None else int(best_candidate["candidate_index"]),
        "best_variant_file": "" if best_candidate is None else str(best_candidate["variant_file"]),
        "before_gate": before_report["gate"],
        "candidates": candidate_reports,
        "warnings": [],
    }


def run_road_lane_template_batch_repair_probe(
    teacher_net_file: Path,
    candidate_net_file: Path,
    output_file: Path,
    *,
    max_candidates: int = 30,
    max_examples: int = 3,
) -> dict[str, Any]:
    before_report = compare_net_road_template_parity(
        teacher_net_file,
        candidate_net_file,
        max_examples=max_examples,
    )
    repair_candidates = build_road_lane_template_single_edge_repair_candidates(
        teacher_net_file,
        candidate_net_file,
        before_report,
        max_items=max_candidates,
    )
    repair_report = write_road_lane_template_repair_candidate(
        candidate_net_file,
        output_file,
        repair_candidates,
    )
    after_report = compare_net_road_template_parity(
        teacher_net_file,
        output_file,
        max_examples=max_examples,
    )
    expected_candidate = {
        "edge_count": sum(int(candidate.get("edge_count", 0)) for candidate in repair_candidates)
    }
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "repair_scope": "teacher_single_edge_batch",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "output_file": str(output_file),
        "selected_candidate_count": len(repair_candidates),
        "repair_candidates": repair_candidates,
        "repair_report": repair_report,
        "promotion_gate": _evaluate_road_lane_local_replay_promotion(expected_candidate, repair_report),
        "before_gate": before_report["gate"],
        "after_gate": after_report["gate"],
        "warnings": [],
    }


def build_road_connection_topology_replay_audit(
    teacher_net_file: Path,
    candidate_net_file: Path,
    *,
    max_examples: int = 20,
) -> dict[str, Any]:
    teacher_root = ET.parse(teacher_net_file).getroot()
    candidate_root = ET.parse(candidate_net_file).getroot()
    candidate_edges = {
        edge.attrib.get("id", ""): edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    candidate_lane_ids = {
        lane.attrib.get("id", "")
        for edge in candidate_edges.values()
        for lane in edge.findall("lane")
        if lane.attrib.get("id")
    }
    existing_topology_keys = {
        _connection_topology_key(_connection_replay_record(connection))
        for connection in candidate_root.findall("connection")
    }
    already_present_connections = []
    replayable_connections = []
    blocked_connections = []
    for connection in teacher_root.findall("connection"):
        record = _connection_replay_record(connection)
        if not record.get("from") or not record.get("to"):
            continue
        if _connection_topology_key(record) in existing_topology_keys:
            already_present_connections.append(record)
            continue
        blocking_reason = _connection_replay_blocking_reason(record, candidate_edges, candidate_lane_ids)
        if blocking_reason:
            blocked_record = dict(record)
            blocked_record["blocking_reason"] = blocking_reason
            blocked_connections.append(blocked_record)
        else:
            replayable_connections.append(record)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "teacher_connection_count": len(already_present_connections) + len(replayable_connections) + len(blocked_connections),
        "already_present_connection_count": len(already_present_connections),
        "replayable_connection_count": len(replayable_connections),
        "blocked_connection_count": len(blocked_connections),
        "already_present_connections": already_present_connections[:max_examples],
        "replayable_connections": replayable_connections[:max_examples],
        "blocked_connections": blocked_connections[:max_examples],
        "warnings": [],
    }


def build_internal_movement_replay_audit(
    teacher_net_file: Path,
    candidate_net_file: Path,
    *,
    max_examples: int = 5,
) -> dict[str, Any]:
    audit = build_road_connection_topology_replay_audit(
        teacher_net_file,
        candidate_net_file,
        max_examples=1_000_000,
    )
    owner_records: dict[str, list[dict[str, str]]] = {}
    for connection in audit["replayable_connections"]:
        if not _is_internal_movement_connection(connection):
            continue
        owner_id = _internal_movement_owner_id(connection)
        if owner_id:
            owner_records.setdefault(owner_id, []).append(connection)

    owners = [
        _internal_movement_owner_summary(owner_id, records, max_examples=max_examples)
        for owner_id, records in owner_records.items()
    ]
    owners.sort(key=lambda item: (-int(item["missing_connection_count"]), str(item["owner_id"])))
    internal_movement_missing_count = sum(
        int(owner["missing_connection_count"]) for owner in owners
    )
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "internal_movement_missing_count": internal_movement_missing_count,
        "tls_controlled_missing_count": sum(
            int(owner["tls_controlled_connection_count"]) for owner in owners
        ),
        "turnaround_missing_count": sum(
            int(owner["turnaround_connection_count"]) for owner in owners
        ),
        "non_turnaround_missing_count": sum(
            int(owner["non_turnaround_connection_count"]) for owner in owners
        ),
        "owner_count": len(owners),
        "owners": owners,
        "source_audit_summary": {
            "teacher_connection_count": audit["teacher_connection_count"],
            "already_present_connection_count": audit["already_present_connection_count"],
            "replayable_connection_count": audit["replayable_connection_count"],
            "blocked_connection_count": audit["blocked_connection_count"],
        },
    }


def build_internal_movement_owner_road_connectivity_parity_audit(
    teacher_net_file: Path,
    candidate_net_file: Path,
    *,
    owner_id: str,
    teacher_edge_map: dict[str, str] | None = None,
    max_examples: int = 20,
) -> dict[str, Any]:
    teacher_root = ET.parse(teacher_net_file).getroot()
    candidate_root = ET.parse(candidate_net_file).getroot()
    if teacher_edge_map is None:
        teacher_edge_map = build_internal_movement_owner_approach_edge_map(
            teacher_net_file,
            candidate_net_file,
            owner_id=owner_id,
        )["edge_map"]
    teacher_edges = {
        edge.attrib.get("id", ""): edge
        for edge in teacher_root.findall("edge")
        if edge.attrib.get("id")
    }
    candidate_edges = {
        edge.attrib.get("id", ""): edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    teacher_outgoing = _lane_connection_index(
        teacher_root,
        edge_ids=set(teacher_edge_map),
        edge_key="from",
        lane_key="fromLane",
    )
    candidate_outgoing = _lane_connection_index(
        candidate_root,
        edge_ids=set(teacher_edge_map.values()),
        edge_key="from",
        lane_key="fromLane",
    )
    teacher_incoming = _lane_connection_index(
        teacher_root,
        edge_ids=set(teacher_edge_map),
        edge_key="to",
        lane_key="toLane",
    )
    candidate_incoming = _lane_connection_index(
        candidate_root,
        edge_ids=set(teacher_edge_map.values()),
        edge_key="to",
        lane_key="toLane",
    )

    lane_deltas = []
    for teacher_edge_id, candidate_edge_id in sorted(teacher_edge_map.items()):
        teacher_edge = teacher_edges.get(teacher_edge_id)
        candidate_edge = candidate_edges.get(candidate_edge_id)
        if teacher_edge is None or candidate_edge is None:
            continue
        for lane in teacher_edge.findall("lane"):
            if not _lane_allows_vehicle(lane):
                continue
            lane_index = lane.attrib.get("index", "0")
            outgoing = _lane_connection_delta(
                teacher_edge_id,
                candidate_edge_id,
                lane_index,
                teacher_outgoing.get((teacher_edge_id, lane_index), []),
                candidate_outgoing.get((candidate_edge_id, lane_index), []),
            )
            incoming = _lane_connection_delta(
                teacher_edge_id,
                candidate_edge_id,
                lane_index,
                teacher_incoming.get((teacher_edge_id, lane_index), []),
                candidate_incoming.get((candidate_edge_id, lane_index), []),
            )
            if _lane_connection_delta_is_reportable(outgoing) or _lane_connection_delta_is_reportable(incoming):
                lane_deltas.append(
                    {
                        "teacher_edge_id": teacher_edge_id,
                        "candidate_edge_id": candidate_edge_id,
                        "lane_index": lane_index,
                        "outgoing": outgoing,
                        "incoming": incoming,
                    }
                )

    gate = {
        "lane_delta_count": len(lane_deltas),
        "missing_non_turnaround_outgoing_count": _lane_delta_flag_count(
            lane_deltas,
            "outgoing",
            "missing_non_turnaround_vehicle_connection",
        ),
        "turnaround_only_outgoing_count": _lane_delta_flag_count(
            lane_deltas,
            "outgoing",
            "turnaround_only_candidate",
        ),
        "missing_turnaround_outgoing_count": _lane_delta_flag_count(
            lane_deltas,
            "outgoing",
            "missing_turnaround",
        ),
        "missing_non_turnaround_incoming_count": _lane_delta_flag_count(
            lane_deltas,
            "incoming",
            "missing_non_turnaround_vehicle_connection",
        ),
        "turnaround_only_incoming_count": _lane_delta_flag_count(
            lane_deltas,
            "incoming",
            "turnaround_only_candidate",
        ),
        "missing_turnaround_incoming_count": _lane_delta_flag_count(
            lane_deltas,
            "incoming",
            "missing_turnaround",
        ),
    }
    return {
        "status": "pass" if not lane_deltas else "fail",
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "owner_id": owner_id,
        "mapped_edge_count": len(teacher_edge_map),
        "gate": gate,
        "lane_deltas": lane_deltas[:max_examples],
    }


def build_internal_movement_owner_approach_edge_map(
    teacher_net_file: Path,
    candidate_net_file: Path,
    *,
    owner_id: str,
    candidate_owner_id: str | None = None,
) -> dict[str, Any]:
    candidate_owner_id = candidate_owner_id or owner_id
    teacher_root = ET.parse(teacher_net_file).getroot()
    candidate_root = ET.parse(candidate_net_file).getroot()
    teacher_edges = _owner_external_approach_edges(teacher_root, owner_id)
    candidate_edges = _owner_external_approach_edges(candidate_root, candidate_owner_id)
    teacher_edge_nodes = {
        edge.attrib.get("id", ""): edge
        for edge in teacher_root.findall("edge")
        if edge.attrib.get("id")
    }
    candidate_edge_nodes = {
        edge.attrib.get("id", ""): edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    candidate_index: dict[tuple[str, str], list[str]] = {}
    candidate_osm_way_index: dict[tuple[str, str], list[str]] = {}
    for edge_id, direction in candidate_edges.items():
        candidate_index.setdefault((direction, _split_edge_root(edge_id)), []).append(edge_id)
        candidate_osm_way_index.setdefault((direction, _osm_way_edge_root(edge_id)), []).append(edge_id)

    edge_map = {}
    exact_candidate_edge_ids = set(teacher_edges) & set(candidate_edges)
    used_candidate_edge_ids: set[str] = set(exact_candidate_edge_ids)
    reserved_mismatched_exact_edges = []
    ambiguous_teacher_edges = []
    unmapped_teacher_edges = []
    candidate_edge_collisions = []

    # Reserve exact ids first. Without this pass, an earlier split segment can
    # consume the only candidate edge and a later exact teacher id can be
    # mapped onto that same edge as well. Reusing a candidate edge makes the
    # replay copy two distinct teacher movement bundles into one SUMO edge.
    for teacher_edge_id, direction in sorted(teacher_edges.items()):
        if teacher_edge_id not in exact_candidate_edge_ids:
            continue
        if candidate_edges[teacher_edge_id] == direction:
            edge_map[teacher_edge_id] = teacher_edge_id
        else:
            # Keep the exact id reserved for same-id replacement. Letting a
            # neighbouring teacher split consume it causes the later missing
            # edge repair to merge two teacher segments into one candidate.
            reserved_mismatched_exact_edges.append(teacher_edge_id)

    for teacher_edge_id, direction in sorted(teacher_edges.items()):
        if teacher_edge_id in edge_map:
            continue
        matches = sorted(candidate_index.get((direction, _split_edge_root(teacher_edge_id)), []))
        if not matches:
            matches = sorted(candidate_osm_way_index.get((direction, _osm_way_edge_root(teacher_edge_id)), []))
        available_matches = [edge_id for edge_id in matches if edge_id not in used_candidate_edge_ids]
        if matches and not available_matches:
            candidate_edge_collisions.append(
                {
                    "teacher_edge_id": teacher_edge_id,
                    "candidate_edge_ids": matches,
                }
            )
            unmapped_teacher_edges.append(teacher_edge_id)
            continue
        matches = available_matches
        if len(matches) == 1:
            endpoint_matches = _same_terminal_endpoint_matches(
                teacher_edge_nodes.get(teacher_edge_id),
                [candidate_edge_nodes[edge_id] for edge_id in matches if edge_id in candidate_edge_nodes],
                owner_id=owner_id,
                direction=direction,
            )
            if (
                not endpoint_matches
                and teacher_edge_id in candidate_edge_nodes
                and teacher_edge_id != matches[0]
                and not _edge_touches_terminal_endpoint(
                    teacher_edge_nodes.get(teacher_edge_id),
                    candidate_edge_nodes.get(teacher_edge_id),
                    owner_id=owner_id,
                    direction=direction,
                )
            ):
                unmapped_teacher_edges.append(teacher_edge_id)
            else:
                edge_map[teacher_edge_id] = matches[0]
                used_candidate_edge_ids.add(matches[0])
        elif len(matches) > 1:
            endpoint_matches = _same_terminal_endpoint_matches(
                teacher_edge_nodes.get(teacher_edge_id),
                [candidate_edge_nodes[edge_id] for edge_id in matches if edge_id in candidate_edge_nodes],
                owner_id=owner_id,
                direction=direction,
            )
            if len(endpoint_matches) == 1:
                edge_map[teacher_edge_id] = endpoint_matches[0]
                used_candidate_edge_ids.add(endpoint_matches[0])
            else:
                ambiguous_teacher_edges.append(teacher_edge_id)
        else:
            unmapped_teacher_edges.append(teacher_edge_id)

    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "owner_id": owner_id,
        "candidate_owner_id": candidate_owner_id,
        "mapped_edge_count": len(edge_map),
        "teacher_edge_count": len(teacher_edges),
        "candidate_edge_count": len(candidate_edges),
        "edge_map": edge_map,
        "ambiguous_teacher_edges": ambiguous_teacher_edges,
        "unmapped_teacher_edges": unmapped_teacher_edges,
        "candidate_edge_collisions": candidate_edge_collisions,
        "reserved_mismatched_exact_edges": reserved_mismatched_exact_edges,
    }


def build_internal_movement_owner_approach_edge_chain_map(
    teacher_net_file: Path,
    candidate_net_file: Path,
    *,
    owner_id: str,
    max_chain_edges: int = 8,
) -> dict[str, Any]:
    teacher_root = ET.parse(teacher_net_file).getroot()
    candidate_root = ET.parse(candidate_net_file).getroot()
    teacher_edges = {
        edge.attrib.get("id", ""): edge
        for edge in teacher_root.findall("edge")
        if edge.attrib.get("id")
    }
    candidate_edges = {
        edge.attrib.get("id", ""): edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id") and not _is_internal_edge(edge)
    }
    edge_chain_map = {}
    fragmented_teacher_edges = []
    unmapped_teacher_edges = []
    ambiguous_teacher_edges = []
    for teacher_edge_id, direction in sorted(_owner_external_approach_edges(teacher_root, owner_id).items()):
        teacher_edge = teacher_edges.get(teacher_edge_id)
        if teacher_edge is None:
            continue
        terminal_junction_id = teacher_edge.attrib.get("from" if direction == "incoming" else "to", "")
        chain, reason = _candidate_approach_edge_chain(
            candidate_edges,
            root_edge_id=_split_edge_root(teacher_edge_id),
            owner_id=owner_id,
            terminal_junction_id=terminal_junction_id,
            direction=direction,
            max_chain_edges=max_chain_edges,
        )
        if reason == "ambiguous":
            ambiguous_teacher_edges.append(teacher_edge_id)
            continue
        if not chain:
            unmapped_teacher_edges.append(teacher_edge_id)
            continue
        edge_chain_map[teacher_edge_id] = chain
        if len(chain) > 1:
            fragmented_teacher_edges.append(
                {
                    "teacher_edge_id": teacher_edge_id,
                    "candidate_edge_ids": chain,
                    "direction": direction,
                }
            )

    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "owner_id": owner_id,
        "edge_chain_map": edge_chain_map,
        "fragmented_teacher_edge_count": len(fragmented_teacher_edges),
        "fragmented_teacher_edges": fragmented_teacher_edges,
        "unmapped_teacher_edges": unmapped_teacher_edges,
        "ambiguous_teacher_edges": ambiguous_teacher_edges,
    }


def build_internal_movement_owner_road_span_repair_candidates(
    teacher_net_file: Path,
    candidate_net_file: Path,
    *,
    owner_id: str,
    max_chain_edges: int = 8,
) -> list[dict[str, Any]]:
    chain_map = build_internal_movement_owner_approach_edge_chain_map(
        teacher_net_file,
        candidate_net_file,
        owner_id=owner_id,
        max_chain_edges=max_chain_edges,
    )
    candidate_edges = {
        edge.attrib.get("id", ""): edge
        for edge in ET.parse(candidate_net_file).getroot().findall("edge")
        if edge.attrib.get("id") and not _is_internal_edge(edge)
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in chain_map["fragmented_teacher_edges"]:
        groups.setdefault(
            _bidirectional_span_key(str(item["teacher_edge_id"])),
            [],
        ).append(item)

    candidates = []
    for span_key, items in sorted(groups.items()):
        chain_edge_ids = sorted(
            {
                edge_id
                for item in items
                for edge_id in item["candidate_edge_ids"]
            }
        )
        teacher_edge_ids = sorted(str(item["teacher_edge_id"]) for item in items)
        keep_edge_ids = sorted(edge_id for edge_id in teacher_edge_ids if edge_id in chain_edge_ids)
        remove_edge_ids = sorted(set(chain_edge_ids) - set(keep_edge_ids))
        intermediate_junction_ids = _candidate_chain_intermediate_junction_ids(items, candidate_edges)
        blocked_incident_edge_ids = _blocked_intermediate_incident_edge_ids(
            candidate_edges,
            intermediate_junction_ids=intermediate_junction_ids,
            chain_edge_ids=set(chain_edge_ids),
        )
        candidates.append(
            {
                "action": "replace_split_approach_road_span",
                "status": "ready" if not blocked_incident_edge_ids else "blocked",
                "span_key": span_key,
                "teacher_edge_ids": teacher_edge_ids,
                "keep_edge_ids": keep_edge_ids,
                "remove_edge_ids": remove_edge_ids,
                "intermediate_junction_ids": intermediate_junction_ids,
                "blocked_incident_edge_ids": blocked_incident_edge_ids,
                "fragmented_teacher_edges": sorted(
                    items,
                    key=lambda item: str(item["teacher_edge_id"]),
                ),
            }
        )
    return candidates


def write_internal_movement_owner_road_span_repair_candidate(
    teacher_net_file: Path,
    candidate_net_file: Path,
    output_file: Path,
    repair_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    teacher_edges = {
        edge.attrib.get("id", ""): edge
        for edge in ET.parse(teacher_net_file).getroot().findall("edge")
        if edge.attrib.get("id")
    }
    candidate_tree = ET.parse(candidate_net_file)
    candidate_root = candidate_tree.getroot()
    applied_candidate_count = 0
    skipped_blocked_candidate_count = 0
    removed_edge_count = 0
    removed_junction_count = 0
    removed_connection_count = 0
    replaced_edge_count = 0

    for candidate in repair_candidates:
        if candidate.get("action") != "replace_split_approach_road_span":
            continue
        if candidate.get("status") != "ready":
            skipped_blocked_candidate_count += 1
            continue
        remove_edge_ids = set(candidate.get("remove_edge_ids", []))
        intermediate_junction_ids = set(candidate.get("intermediate_junction_ids", []))
        internal_remove_edge_ids = {
            edge.attrib.get("id", "")
            for edge in candidate_root.findall("edge")
            if _internal_owner_id(edge.attrib.get("id", "")) in intermediate_junction_ids
        }
        all_remove_edge_ids = remove_edge_ids | internal_remove_edge_ids
        removed_connection_count += _remove_children(
            candidate_root,
            lambda child, edges=all_remove_edge_ids, junctions=intermediate_junction_ids: (
                child.tag == "connection"
                and (
                    child.attrib.get("from", "") in edges
                    or child.attrib.get("to", "") in edges
                    or _internal_owner_id(child.attrib.get("from", "")) in junctions
                    or _internal_owner_id(child.attrib.get("to", "")) in junctions
                    or _internal_owner_id(child.attrib.get("via", "")) in junctions
                )
            ),
        )
        removed_edge_count += _remove_children(
            candidate_root,
            lambda child, edges=all_remove_edge_ids: child.tag == "edge" and child.attrib.get("id", "") in edges,
        )
        removed_junction_count += _remove_children(
            candidate_root,
            lambda child, junctions=intermediate_junction_ids: child.tag == "junction"
            and child.attrib.get("id", "") in junctions,
        )
        _remove_children(
            candidate_root,
            lambda child, junctions=intermediate_junction_ids: child.tag == "tlLogic"
            and child.attrib.get("id", "") in junctions,
        )
        for teacher_edge_id in candidate.get("teacher_edge_ids", []):
            teacher_edge = teacher_edges.get(str(teacher_edge_id))
            if teacher_edge is None:
                continue
            _remove_children(
                candidate_root,
                lambda child, edge_id=str(teacher_edge_id): child.tag == "edge"
                and child.attrib.get("id", "") == edge_id,
            )
            _insert_after_last(candidate_root, "edge", copy.deepcopy(teacher_edge))
            replaced_edge_count += 1
        applied_candidate_count += 1

    _filter_junction_lane_references(candidate_root)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(candidate_root, space="  ")
    candidate_tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "candidate_net_file": str(candidate_net_file),
        "output_file": str(output_file),
        "selected_candidate_count": len(repair_candidates),
        "applied_candidate_count": applied_candidate_count,
        "skipped_blocked_candidate_count": skipped_blocked_candidate_count,
        "removed_edge_count": removed_edge_count,
        "removed_junction_count": removed_junction_count,
        "removed_connection_count": removed_connection_count,
        "replaced_edge_count": replaced_edge_count,
        "warnings": [],
    }


def write_internal_movement_owner_blocked_road_span_overlay_candidate(
    teacher_net_file: Path,
    candidate_net_file: Path,
    output_file: Path,
    repair_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    teacher_root = ET.parse(teacher_net_file).getroot()
    teacher_edges = {
        edge.attrib.get("id", ""): edge
        for edge in teacher_root.findall("edge")
        if edge.attrib.get("id")
    }
    teacher_junctions = {
        junction.attrib.get("id", ""): junction
        for junction in teacher_root.findall("junction")
        if junction.attrib.get("id")
    }
    candidate_tree = ET.parse(candidate_net_file)
    candidate_root = candidate_tree.getroot()
    existing_edge_ids = {
        edge.attrib.get("id", "")
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    existing_junction_ids = {
        junction.attrib.get("id", "")
        for junction in candidate_root.findall("junction")
        if junction.attrib.get("id")
    }
    selected_candidates = [
        candidate
        for candidate in repair_candidates
        if candidate.get("action") == "replace_split_approach_road_span"
        and candidate.get("status") == "blocked"
    ]
    added_edge_count = 0
    added_junction_count = 0
    for candidate in selected_candidates:
        for teacher_edge_id in candidate.get("teacher_edge_ids", []):
            edge_id = str(teacher_edge_id)
            teacher_edge = teacher_edges.get(edge_id)
            if teacher_edge is None or edge_id in existing_edge_ids:
                continue
            for endpoint in (teacher_edge.attrib.get("from", ""), teacher_edge.attrib.get("to", "")):
                teacher_junction = teacher_junctions.get(endpoint)
                if not endpoint or endpoint in existing_junction_ids or teacher_junction is None:
                    continue
                _insert_after_last(
                    candidate_root,
                    "junction",
                    ET.Element("junction", _minimal_endpoint_junction_attrs(teacher_junction)),
                )
                existing_junction_ids.add(endpoint)
                added_junction_count += 1
            _insert_after_last(candidate_root, "edge", copy.deepcopy(teacher_edge))
            existing_edge_ids.add(edge_id)
            added_edge_count += 1

    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(candidate_root, space="  ")
    candidate_tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "repair_scope": "blocked_road_span_overlay",
        "candidate_net_file": str(candidate_net_file),
        "output_file": str(output_file),
        "selected_candidate_count": len(selected_candidates),
        "added_edge_count": added_edge_count,
        "added_junction_count": added_junction_count,
        "warnings": [],
    }


def write_internal_movement_owner_ready_road_span_endpoint_replay_candidate(
    teacher_net_file: Path,
    candidate_net_file: Path,
    output_file: Path,
    *,
    owner_id: str,
    copy_tls: bool = False,
    max_ready_spans: int = 1,
) -> dict[str, Any]:
    repair_candidates = build_internal_movement_owner_road_span_repair_candidates(
        teacher_net_file,
        candidate_net_file,
        owner_id=owner_id,
    )
    owner_mapped_edge_ids = set(
        build_internal_movement_owner_approach_edge_map(
            teacher_net_file,
            candidate_net_file,
            owner_id=owner_id,
        )["edge_map"].values()
    )
    all_ready_candidates = [
        candidate for candidate in repair_candidates if candidate.get("status") == "ready"
    ]
    skipped_owner_terminal_candidates = [
        candidate
        for candidate in all_ready_candidates
        if set(candidate.get("remove_edge_ids", [])) & owner_mapped_edge_ids
    ]
    ready_candidates = [
        candidate
        for candidate in all_ready_candidates
        if not set(candidate.get("remove_edge_ids", [])) & owner_mapped_edge_ids
    ][:max_ready_spans]
    blocked_candidates = [
        candidate for candidate in repair_candidates if candidate.get("status") == "blocked"
    ]
    road_span_file = output_file.with_name(f"{output_file.stem}_road_spans{output_file.suffix}")
    warnings = []
    if skipped_owner_terminal_candidates:
        warnings.append("skipped_owner_terminal_road_span_candidate")

    if ready_candidates:
        road_span_repair = write_internal_movement_owner_road_span_repair_candidate(
            teacher_net_file,
            candidate_net_file,
            road_span_file,
            ready_candidates,
        )
    else:
        road_span_file.parent.mkdir(parents=True, exist_ok=True)
        tree = ET.parse(candidate_net_file)
        ET.indent(tree.getroot(), space="  ")
        tree.write(road_span_file, encoding="utf-8", xml_declaration=True)
        road_span_repair = {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "candidate_net_file": str(candidate_net_file),
            "output_file": str(road_span_file),
            "selected_candidate_count": 0,
            "applied_candidate_count": 0,
            "skipped_blocked_candidate_count": 0,
            "warnings": [],
        }
        warnings.append("no_ready_road_span_candidate")

    endpoint_owner_ids = _teacher_endpoint_owner_ids_for_edges(
        teacher_net_file,
        [
            str(edge_id)
            for candidate in ready_candidates
            for edge_id in candidate.get("teacher_edge_ids", [])
        ],
    )
    replay_owner_ids = sorted(endpoint for endpoint in endpoint_owner_ids if endpoint != owner_id)
    endpoint_replay_reports = []
    current_file = road_span_file
    for index, endpoint_owner_id in enumerate(replay_owner_ids):
        next_file = (
            output_file
            if index == len(replay_owner_ids) - 1
            else output_file.with_name(f"{output_file.stem}_endpoint_{index + 1}{output_file.suffix}")
        )
        endpoint_replay_reports.append(
            write_internal_movement_owner_teacher_replay_candidate(
                teacher_net_file,
                current_file,
                next_file,
                owner_id=endpoint_owner_id,
                copy_tls=copy_tls,
            )
        )
        current_file = next_file

    if not replay_owner_ids:
        tree = ET.parse(current_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        ET.indent(tree.getroot(), space="  ")
        tree.write(output_file, encoding="utf-8", xml_declaration=True)

    owner_road_connectivity_audit = build_internal_movement_owner_road_connectivity_parity_audit(
        teacher_net_file,
        output_file,
        owner_id=owner_id,
    )
    endpoint_replay_failed = any(report.get("status") != "pass" for report in endpoint_replay_reports)
    return {
        "status": "fail" if endpoint_replay_failed else road_span_repair["status"],
        "claim_status": "diagnostic-demo",
        "repair_scope": "ready_road_span_endpoint_replay",
        "owner_id": owner_id,
        "copy_tls": copy_tls,
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "output_file": str(output_file),
        "road_span_file": str(road_span_file),
        "selected_ready_road_span_candidate_count": len(ready_candidates),
        "skipped_blocked_road_span_candidate_count": len(blocked_candidates),
        "skipped_owner_terminal_road_span_candidate_count": len(skipped_owner_terminal_candidates),
        "replayed_endpoint_owner_ids": replay_owner_ids,
        "road_span_repair": road_span_repair,
        "endpoint_replay_reports": endpoint_replay_reports,
        "owner_road_connectivity_audit": owner_road_connectivity_audit,
        "warnings": warnings,
    }


def write_internal_movement_owner_layered_teacher_replay_candidate(
    teacher_net_file: Path,
    candidate_net_file: Path,
    output_file: Path,
    *,
    owner_id: str,
    copy_tls: bool = False,
    max_ready_spans: int = 1,
    pre_repair_ready_road_spans: bool = False,
    replay_blocked_road_span_endpoint_owners: bool = False,
    max_blocked_endpoint_owner_replays: int = 1,
) -> dict[str, Any]:
    owner_input_file = candidate_net_file
    pre_road_span_repair = {
        "status": "pass",
        "selected_candidate_count": 0,
        "applied_candidate_count": 0,
    }
    pre_replayed_endpoint_owner_ids: list[str] = []
    if pre_repair_ready_road_spans:
        pre_road_span_file = output_file.with_name(f"{output_file.stem}_pre_road_spans{output_file.suffix}")
        pre_road_span_candidates = [
            candidate
            for candidate in build_internal_movement_owner_road_span_repair_candidates(
                teacher_net_file,
                candidate_net_file,
                owner_id=owner_id,
            )
            if candidate.get("status") == "ready"
        ][:max_ready_spans]
        pre_road_span_repair = write_internal_movement_owner_road_span_repair_candidate(
            teacher_net_file,
            candidate_net_file,
            pre_road_span_file,
            pre_road_span_candidates,
        )
        pre_replayed_endpoint_owner_ids = [
            endpoint
            for endpoint in _teacher_endpoint_owner_ids_for_edges(
                teacher_net_file,
                [
                    str(edge_id)
                    for candidate in pre_road_span_candidates
                    for edge_id in candidate.get("teacher_edge_ids", [])
                ],
            )
            if endpoint != owner_id
        ]
        owner_input_file = pre_road_span_file

    blocked_road_span_overlay = {
        "status": "pass",
        "selected_candidate_count": 0,
        "added_edge_count": 0,
        "added_junction_count": 0,
    }
    blocked_overlay_replayed_endpoint_owner_ids: list[str] = []
    if replay_blocked_road_span_endpoint_owners:
        blocked_overlay_candidates = [
            candidate
            for candidate in build_internal_movement_owner_road_span_repair_candidates(
                teacher_net_file,
                owner_input_file,
                owner_id=owner_id,
            )
            if candidate.get("status") == "blocked"
        ][:max_blocked_endpoint_owner_replays]
        if blocked_overlay_candidates:
            blocked_overlay_file = output_file.with_name(
                f"{output_file.stem}_blocked_span_overlay{output_file.suffix}"
            )
            blocked_road_span_overlay = (
                write_internal_movement_owner_blocked_road_span_overlay_candidate(
                    teacher_net_file,
                    owner_input_file,
                    blocked_overlay_file,
                    blocked_overlay_candidates,
                )
            )
            blocked_overlay_replayed_endpoint_owner_ids = [
                endpoint
                for endpoint in _teacher_endpoint_owner_ids_for_edges(
                    teacher_net_file,
                    [
                        str(edge_id)
                        for candidate in blocked_overlay_candidates
                        for edge_id in candidate.get("teacher_edge_ids", [])
                    ],
                )
                if endpoint != owner_id
            ]
            owner_input_file = blocked_overlay_file

    owner_replay_file = output_file.with_name(f"{output_file.stem}_owner_replay{output_file.suffix}")
    owner_replay_report = write_internal_movement_owner_teacher_replay_candidate(
        teacher_net_file,
        owner_input_file,
        owner_replay_file,
        owner_id=owner_id,
        copy_tls=copy_tls,
    )
    if owner_replay_report.get("status") != "pass":
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(Path(owner_input_file).read_bytes())
        owner_road_connectivity_audit = build_internal_movement_owner_road_connectivity_parity_audit(
            teacher_net_file,
            output_file,
            owner_id=owner_id,
        )
        return {
            "status": "fail",
            "claim_status": "diagnostic-demo",
            "repair_scope": "layered_internal_movement_owner_teacher_replay",
            "owner_id": owner_id,
            "copy_tls": copy_tls,
            "pre_repair_ready_road_spans": pre_repair_ready_road_spans,
            "replay_blocked_road_span_endpoint_owners": replay_blocked_road_span_endpoint_owners,
            "teacher_net_file": str(teacher_net_file),
            "candidate_net_file": str(candidate_net_file),
            "output_file": str(output_file),
            "owner_input_file": str(owner_input_file),
            "owner_replay_file": str(owner_replay_file),
            "pre_road_span_repair": pre_road_span_repair,
            "blocked_road_span_overlay": blocked_road_span_overlay,
            "pre_replayed_endpoint_owner_ids": pre_replayed_endpoint_owner_ids,
            "blocked_overlay_replayed_endpoint_owner_ids": blocked_overlay_replayed_endpoint_owner_ids,
            "pre_endpoint_owner_ids": [],
            "pre_endpoint_replay_reports": [],
            "owner_replay_report": owner_replay_report,
            "road_span_endpoint_replay_report": {"status": "skipped"},
            "blocked_replayed_endpoint_owner_ids": [],
            "blocked_endpoint_replay_reports": [],
            "owner_road_connectivity_audit": owner_road_connectivity_audit,
            "warnings": ["owner_replay_blocked"],
        }
    current_file = owner_replay_file
    pre_endpoint_replay_reports = []
    mapped_endpoint_replayed_owner_ids = [
        endpoint
        for endpoint in _teacher_endpoint_owner_ids_for_edges(
            teacher_net_file,
            list(owner_replay_report.get("edge_map", {})),
        )
        if endpoint != owner_id
    ]
    pre_endpoint_owner_ids = list(
        dict.fromkeys(
            [
                *pre_replayed_endpoint_owner_ids,
                *blocked_overlay_replayed_endpoint_owner_ids,
                *mapped_endpoint_replayed_owner_ids,
            ]
        )
    )
    for index, endpoint_owner_id in enumerate(pre_endpoint_owner_ids, start=1):
        next_file = output_file.with_name(f"{output_file.stem}_pre_endpoint_{index}{output_file.suffix}")
        pre_endpoint_replay_reports.append(
            write_internal_movement_owner_teacher_replay_candidate(
                teacher_net_file,
                current_file,
                next_file,
                owner_id=endpoint_owner_id,
                copy_tls=copy_tls,
            )
        )
        current_file = next_file

    post_endpoint_owner_refresh_report: dict[str, Any] = {
        "status": "pass",
        "refresh_status": "skipped",
    }
    if pre_endpoint_replay_reports:
        post_endpoint_owner_refresh_file = output_file.with_name(
            f"{output_file.stem}_post_endpoint_owner_refresh{output_file.suffix}"
        )
        post_endpoint_owner_refresh_report = write_internal_movement_owner_teacher_replay_candidate(
            teacher_net_file,
            current_file,
            post_endpoint_owner_refresh_file,
            owner_id=owner_id,
            copy_tls=copy_tls,
        )
        current_file = post_endpoint_owner_refresh_file

    post_road_span_output_file = (
        output_file.with_name(f"{output_file.stem}_post_road_spans{output_file.suffix}")
        if replay_blocked_road_span_endpoint_owners
        else output_file
    )
    road_span_endpoint_replay_report = write_internal_movement_owner_ready_road_span_endpoint_replay_candidate(
        teacher_net_file,
        current_file,
        post_road_span_output_file,
        owner_id=owner_id,
        copy_tls=copy_tls,
        max_ready_spans=max_ready_spans,
    )
    current_file = post_road_span_output_file
    blocked_replayed_endpoint_owner_ids: list[str] = []
    blocked_endpoint_replay_reports = []
    if replay_blocked_road_span_endpoint_owners:
        blocked_candidates = [
            candidate
            for candidate in build_internal_movement_owner_road_span_repair_candidates(
                teacher_net_file,
                current_file,
                owner_id=owner_id,
            )
            if candidate.get("status") == "blocked"
        ]
        already_replayed_owner_ids = {
            owner_id,
            *pre_endpoint_owner_ids,
            *road_span_endpoint_replay_report["replayed_endpoint_owner_ids"],
        }
        blocked_endpoint_owner_ids = [
            endpoint
            for endpoint in _teacher_endpoint_owner_ids_for_edges(
                teacher_net_file,
                [
                    str(edge_id)
                    for candidate in blocked_candidates
                    for edge_id in candidate.get("teacher_edge_ids", [])
                ],
            )
            if endpoint not in already_replayed_owner_ids
        ][:max_blocked_endpoint_owner_replays]
        blocked_replayed_endpoint_owner_ids = blocked_endpoint_owner_ids
        for index, endpoint_owner_id in enumerate(blocked_endpoint_owner_ids, start=1):
            next_file = (
                output_file
                if index == len(blocked_endpoint_owner_ids)
                else output_file.with_name(f"{output_file.stem}_blocked_endpoint_{index}{output_file.suffix}")
            )
            blocked_endpoint_replay_reports.append(
                write_internal_movement_owner_teacher_replay_candidate(
                    teacher_net_file,
                    current_file,
                    next_file,
                    owner_id=endpoint_owner_id,
                    copy_tls=copy_tls,
                )
            )
            current_file = next_file
        if not blocked_endpoint_owner_ids:
            tree = ET.parse(current_file)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            ET.indent(tree.getroot(), space="  ")
            tree.write(output_file, encoding="utf-8", xml_declaration=True)

    owner_road_connectivity_audit = build_internal_movement_owner_road_connectivity_parity_audit(
        teacher_net_file,
        output_file,
        owner_id=owner_id,
    )
    convergence_endpoint_replay_reports: list[dict[str, Any]] = []
    convergence_status = "not_needed"
    before_convergence_lane_delta_count = int(
        owner_road_connectivity_audit.get("gate", {}).get("lane_delta_count", 0)
    )
    after_convergence_lane_delta_count = before_convergence_lane_delta_count
    if before_convergence_lane_delta_count > 0 and pre_endpoint_owner_ids:
        convergence_status = "no_improvement"
        convergence_current_file = output_file
        best_convergence_file = output_file
        best_convergence_audit = owner_road_connectivity_audit
        best_lane_delta_count = before_convergence_lane_delta_count
        for index, endpoint_owner_id in enumerate(pre_endpoint_owner_ids, start=1):
            convergence_file = output_file.with_name(
                f"{output_file.stem}_convergence_endpoint_{index}{output_file.suffix}"
            )
            convergence_report = write_internal_movement_owner_teacher_replay_candidate(
                teacher_net_file,
                convergence_current_file,
                convergence_file,
                owner_id=endpoint_owner_id,
                copy_tls=copy_tls,
            )
            convergence_report = dict(convergence_report)
            convergence_endpoint_replay_reports.append(convergence_report)
            if convergence_report.get("status") != "pass" or not convergence_file.exists():
                break
            convergence_current_file = convergence_file
            convergence_audit = build_internal_movement_owner_road_connectivity_parity_audit(
                teacher_net_file,
                convergence_file,
                owner_id=owner_id,
            )
            convergence_report["target_owner_road_connectivity_audit"] = convergence_audit
            lane_delta_count = int(convergence_audit.get("gate", {}).get("lane_delta_count", 0))
            if lane_delta_count < best_lane_delta_count:
                best_lane_delta_count = lane_delta_count
                best_convergence_file = convergence_file
                best_convergence_audit = convergence_audit
            if lane_delta_count == 0:
                break
        if best_lane_delta_count < before_convergence_lane_delta_count:
            if best_convergence_file != output_file:
                output_file.write_bytes(best_convergence_file.read_bytes())
            owner_road_connectivity_audit = best_convergence_audit
            after_convergence_lane_delta_count = best_lane_delta_count
            convergence_status = "promoted"
    return {
        "status": (
            "pass"
            if all(
                report.get("status") == "pass"
                for report in [
                    pre_road_span_repair,
                    blocked_road_span_overlay,
                    owner_replay_report,
                    *pre_endpoint_replay_reports,
                    post_endpoint_owner_refresh_report,
                    road_span_endpoint_replay_report,
                    *blocked_endpoint_replay_reports,
                ]
            )
            else "fail"
        ),
        "claim_status": "diagnostic-demo",
        "repair_scope": "layered_internal_movement_owner_teacher_replay",
        "owner_id": owner_id,
        "copy_tls": copy_tls,
        "pre_repair_ready_road_spans": pre_repair_ready_road_spans,
        "replay_blocked_road_span_endpoint_owners": replay_blocked_road_span_endpoint_owners,
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "output_file": str(output_file),
        "owner_input_file": str(owner_input_file),
        "owner_replay_file": str(owner_replay_file),
        "pre_road_span_repair": pre_road_span_repair,
        "blocked_road_span_overlay": blocked_road_span_overlay,
        "pre_replayed_endpoint_owner_ids": pre_replayed_endpoint_owner_ids,
        "blocked_overlay_replayed_endpoint_owner_ids": blocked_overlay_replayed_endpoint_owner_ids,
        "mapped_endpoint_replayed_owner_ids": mapped_endpoint_replayed_owner_ids,
        "pre_endpoint_owner_ids": pre_endpoint_owner_ids,
        "pre_endpoint_replay_reports": pre_endpoint_replay_reports,
        "post_endpoint_owner_refresh_report": post_endpoint_owner_refresh_report,
        "owner_replay_report": owner_replay_report,
        "road_span_endpoint_replay_report": road_span_endpoint_replay_report,
        "blocked_replayed_endpoint_owner_ids": blocked_replayed_endpoint_owner_ids,
        "blocked_endpoint_replay_reports": blocked_endpoint_replay_reports,
        "convergence_status": convergence_status,
        "convergence_endpoint_replay_reports": convergence_endpoint_replay_reports,
        "before_convergence_lane_delta_count": before_convergence_lane_delta_count,
        "after_convergence_lane_delta_count": after_convergence_lane_delta_count,
        "owner_road_connectivity_audit": owner_road_connectivity_audit,
        "warnings": [],
    }


def build_internal_movement_owner_road_lane_repair_candidates(
    teacher_net_file: Path,
    candidate_net_file: Path,
    *,
    owner_id: str,
    teacher_edge_map: dict[str, str],
) -> list[dict[str, Any]]:
    teacher_root = ET.parse(teacher_net_file).getroot()
    candidate_root = ET.parse(candidate_net_file).getroot()
    teacher_edges = {
        edge.attrib.get("id", ""): edge
        for edge in teacher_root.findall("edge")
        if edge.attrib.get("id")
    }
    candidate_edges = {
        edge.attrib.get("id", ""): edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    teacher_offset = _root_net_offset(teacher_root)
    candidate_offset = _root_net_offset(candidate_root)
    candidates = []
    for teacher_edge_id, candidate_edge_id in sorted(teacher_edge_map.items()):
        teacher_edge = teacher_edges.get(teacher_edge_id)
        candidate_edge = candidate_edges.get(candidate_edge_id)
        if (
            teacher_edge is None
            or candidate_edge is None
            or _is_internal_edge(teacher_edge)
            or _is_internal_edge(candidate_edge)
            or owner_id not in (teacher_edge.attrib.get("from", ""), teacher_edge.attrib.get("to", ""))
        ):
            continue
        teacher_signature = _lane_signature(_canonical_edge_record(teacher_edge))
        candidate_signature = _lane_signature(_canonical_edge_record(candidate_edge))
        if teacher_signature == candidate_signature:
            continue
        candidates.append(
            {
                "action": "replace_edge_lanes",
                "teacher_edge_id": teacher_edge_id,
                "edge_id": candidate_edge_id,
                "edge_ids": [candidate_edge_id],
                "edge_count": 1,
                "priority": 1,
                "from_lane_signature": candidate_signature,
                "to_lane_signature": teacher_signature,
                "lanes": [
                    _mapped_edge_lane_attrs(
                        lane.attrib,
                        teacher_edge_id,
                        candidate_edge_id,
                        source_offset=teacher_offset,
                        target_offset=candidate_offset,
                    )
                    for lane in teacher_edge.findall("lane")
                ],
            }
        )
    return candidates


def build_internal_movement_owner_missing_approach_edge_repair_candidates(
    teacher_net_file: Path,
    candidate_net_file: Path,
    *,
    owner_id: str,
    teacher_edge_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    teacher_root = ET.parse(teacher_net_file).getroot()
    candidate_root = ET.parse(candidate_net_file).getroot()
    teacher_offset = _root_net_offset(teacher_root)
    candidate_offset = _root_net_offset(candidate_root)
    teacher_edge_map = teacher_edge_map or {}
    candidate_edges = {
        edge.attrib.get("id", ""): edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    candidate_junction_ids = {
        junction.attrib.get("id", "")
        for junction in candidate_root.findall("junction")
        if junction.attrib.get("id")
    }
    teacher_junctions = {
        junction.attrib.get("id", ""): junction
        for junction in teacher_root.findall("junction")
        if junction.attrib.get("id")
    }
    candidates = []
    for edge in teacher_root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id in teacher_edge_map or _is_internal_edge(edge):
            continue
        endpoints = (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
        if owner_id not in endpoints:
            continue
        existing_edge = candidate_edges.get(edge_id)
        replace_existing_edge = False
        if existing_edge is not None:
            replace_existing_edge = (
                endpoints != (existing_edge.attrib.get("from", ""), existing_edge.attrib.get("to", ""))
                or _lane_signature(_canonical_edge_record(edge))
                != _lane_signature(_canonical_edge_record(existing_edge))
            )
            if not replace_existing_edge:
                continue
        missing_endpoint_junctions = [
            _minimal_endpoint_junction_attrs(
                teacher_junctions[endpoint],
                source_offset=teacher_offset,
                target_offset=candidate_offset,
            )
            for endpoint in endpoints
            if endpoint and endpoint != owner_id and endpoint not in candidate_junction_ids and endpoint in teacher_junctions
        ]
        candidates.append(
            {
                "action": "add_missing_approach_edge",
                "edge_id": edge_id,
                "edge": _reproject_edge_record(
                    _edge_attrs_with_lanes(edge),
                    source_offset=teacher_offset,
                    target_offset=candidate_offset,
                ),
                "edge_map_addition": {edge_id: edge_id},
                "replace_existing_edge": replace_existing_edge,
                "missing_endpoint_junctions": missing_endpoint_junctions,
            }
        )
    return candidates


def write_internal_movement_owner_missing_approach_edge_repair_candidate(
    candidate_net_file: Path,
    output_file: Path,
    repair_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    tree = ET.parse(candidate_net_file)
    root = tree.getroot()
    existing_edge_ids = {
        edge.attrib.get("id", "")
        for edge in root.findall("edge")
        if edge.attrib.get("id")
    }
    existing_junction_ids = {
        junction.attrib.get("id", "")
        for junction in root.findall("junction")
        if junction.attrib.get("id")
    }
    added_edge_count = 0
    added_junction_count = 0
    replaced_edge_count = 0
    removed_connection_count = 0
    edge_map_additions: dict[str, str] = {}
    for candidate in repair_candidates:
        if str(candidate.get("action", "")) != "add_missing_approach_edge":
            continue
        for junction_attrs in candidate.get("missing_endpoint_junctions", []):
            junction_id = str(junction_attrs.get("id", "")) if isinstance(junction_attrs, dict) else ""
            if not junction_id or junction_id in existing_junction_ids:
                continue
            _insert_after_last(root, "junction", ET.Element("junction", dict(junction_attrs)))
            existing_junction_ids.add(junction_id)
            added_junction_count += 1
        edge_record = candidate.get("edge", {})
        edge_id = str(edge_record.get("id", "")) if isinstance(edge_record, dict) else ""
        if not edge_id:
            continue
        if edge_id in existing_edge_ids:
            if not candidate.get("replace_existing_edge"):
                continue
            removed_connection_count += _remove_children(
                root,
                lambda child, target=edge_id: child.tag == "connection"
                and (
                    child.attrib.get("from", "") == target
                    or child.attrib.get("to", "") == target
                ),
            )
            replaced_edge_count += _remove_children(
                root,
                lambda child, target=edge_id: child.tag == "edge" and child.attrib.get("id", "") == target,
            )
            existing_edge_ids.discard(edge_id)
        if edge_id in existing_edge_ids:
            continue
        edge_node = ET.Element("edge", _record_attrs(edge_record, "lanes"))
        for lane in edge_record.get("lanes", []):
            if isinstance(lane, dict):
                ET.SubElement(edge_node, "lane", dict(lane))
        _insert_after_last(root, "edge", edge_node)
        existing_edge_ids.add(edge_id)
        edge_map_additions.update(
            {
                str(key): str(value)
                for key, value in candidate.get("edge_map_addition", {}).items()
            }
        )
        added_edge_count += 1

    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "candidate_net_file": str(candidate_net_file),
        "output_file": str(output_file),
        "selected_candidate_count": len(repair_candidates),
        "added_edge_count": added_edge_count,
        "added_junction_count": added_junction_count,
        "replaced_edge_count": replaced_edge_count,
        "removed_connection_count": removed_connection_count,
        "edge_map_additions": edge_map_additions,
        "warnings": [],
    }


def write_internal_movement_owner_teacher_replay_candidate(
    teacher_net_file: Path,
    candidate_net_file: Path,
    output_file: Path,
    *,
    owner_id: str,
    copy_tls: bool = False,
) -> dict[str, Any]:
    approach_edge_map = build_internal_movement_owner_approach_edge_map(
        teacher_net_file,
        candidate_net_file,
        owner_id=owner_id,
    )
    edge_map = dict(approach_edge_map["edge_map"])
    road_lane_file = output_file.with_name(f"{output_file.stem}_road_lanes{output_file.suffix}")
    road_lane_candidates = build_internal_movement_owner_road_lane_repair_candidates(
        teacher_net_file,
        candidate_net_file,
        owner_id=owner_id,
        teacher_edge_map=edge_map,
    )
    road_lane_repair = write_road_lane_template_repair_candidate(
        candidate_net_file,
        road_lane_file,
        road_lane_candidates,
    )
    missing_approach_file = output_file.with_name(f"{output_file.stem}_missing_approaches{output_file.suffix}")
    missing_approach_candidates = build_internal_movement_owner_missing_approach_edge_repair_candidates(
        teacher_net_file,
        road_lane_file,
        owner_id=owner_id,
        teacher_edge_map=edge_map,
    )
    missing_approach_repair = write_internal_movement_owner_missing_approach_edge_repair_candidate(
        road_lane_file,
        missing_approach_file,
        missing_approach_candidates,
    )
    edge_map.update(missing_approach_repair["edge_map_additions"])
    bundle_replay = write_internal_movement_owner_bundle_replacement_candidate(
        teacher_net_file,
        missing_approach_file,
        output_file,
        owner_id=owner_id,
        teacher_edge_map=edge_map,
        copy_tls=copy_tls,
    )
    if bundle_replay.get("status") != "pass" and not output_file.exists():
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(missing_approach_file.read_bytes())
    road_connectivity_audit = build_internal_movement_owner_road_connectivity_parity_audit(
        teacher_net_file,
        output_file,
        owner_id=owner_id,
        teacher_edge_map=edge_map,
    )
    approach_edge_chain_map = build_internal_movement_owner_approach_edge_chain_map(
        teacher_net_file,
        output_file,
        owner_id=owner_id,
    )
    return {
        "status": bundle_replay["status"],
        "claim_status": "diagnostic-demo",
        "repair_scope": "internal_movement_owner_teacher_replay",
        "owner_id": owner_id,
        "copy_tls": copy_tls,
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "output_file": str(output_file),
        "road_lane_file": str(road_lane_file),
        "missing_approach_file": str(missing_approach_file),
        "edge_map": edge_map,
        "approach_edge_map": approach_edge_map,
        "road_lane_repair_candidate_count": len(road_lane_candidates),
        "missing_approach_edge_repair_candidate_count": len(missing_approach_candidates),
        "road_lane_repair": road_lane_repair,
        "missing_approach_edge_repair": missing_approach_repair,
        "bundle_replay": bundle_replay,
        "road_connectivity_audit": road_connectivity_audit,
        "approach_edge_chain_map": approach_edge_chain_map,
        "warnings": [],
    }


def build_internal_movement_owner_internal_lane_map(
    teacher_net_file: Path,
    candidate_net_file: Path,
    *,
    owner_id: str,
    teacher_edge_map: dict[str, str],
) -> dict[str, Any]:
    teacher_root = ET.parse(teacher_net_file).getroot()
    candidate_root = ET.parse(candidate_net_file).getroot()
    candidate_index: dict[tuple[str, str, str, str, str], list[str]] = {}
    for connection in candidate_root.findall("connection"):
        record = _connection_replay_record(connection)
        via = record.get("via", "")
        if not _internal_id_belongs_to_owner(via, owner_id):
            continue
        candidate_index.setdefault(_movement_lane_key(record), []).append(via)

    internal_lane_map = {}
    ambiguous_teacher_lanes = []
    unmapped_teacher_lanes = []
    for connection in teacher_root.findall("connection"):
        record = _mapped_external_connection(
            _connection_replay_record(connection),
            teacher_edge_map,
        )
        via = record.get("via", "")
        if not _internal_id_belongs_to_owner(via, owner_id):
            continue
        matches = sorted(set(candidate_index.get(_movement_lane_key(record), [])))
        if len(matches) == 1:
            if matches[0] != via:
                internal_lane_map[via] = matches[0]
        elif len(matches) > 1:
            ambiguous_teacher_lanes.append(via)
        else:
            unmapped_teacher_lanes.append(via)

    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "owner_id": owner_id,
        "mapped_internal_lane_count": len(internal_lane_map),
        "internal_lane_map": internal_lane_map,
        "ambiguous_teacher_lanes": sorted(set(ambiguous_teacher_lanes)),
        "unmapped_teacher_lanes": sorted(set(unmapped_teacher_lanes)),
    }


def write_road_connection_topology_replay_candidate(
    teacher_net_file: Path,
    candidate_net_file: Path,
    output_file: Path,
    *,
    max_connections: int = 100,
    include_internal_via: bool = False,
) -> dict[str, Any]:
    audit = build_road_connection_topology_replay_audit(
        teacher_net_file,
        candidate_net_file,
        max_examples=1_000_000,
    )
    candidate_tree = ET.parse(candidate_net_file)
    candidate_root = candidate_tree.getroot()
    candidate_edges = {
        edge.attrib.get("id", ""): edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    candidate_junctions = {
        junction.attrib.get("id", ""): junction
        for junction in candidate_root.findall("junction")
        if junction.attrib.get("id")
    }
    existing_topology_keys = {
        _connection_topology_key(_connection_replay_record(connection))
        for connection in candidate_root.findall("connection")
    }
    added_connection_count = 0
    skipped_connection_count = 0
    skipped_internal_via_connection_count = 0
    skipped_candidate_internal_junction_connection_count = 0
    skipped_internal_edge_connection_count = 0
    skipped_non_adjacent_connection_count = 0
    selected_connection_count = 0
    for connection in audit["replayable_connections"]:
        if added_connection_count >= max(0, max_connections):
            break
        selected_connection_count += 1
        from_edge = candidate_edges.get(connection.get("from", ""))
        to_edge = candidate_edges.get(connection.get("to", ""))
        if _is_internal_edge(from_edge) or _is_internal_edge(to_edge):
            skipped_connection_count += 1
            skipped_internal_edge_connection_count += 1
            continue
        if connection.get("via") and not include_internal_via:
            skipped_connection_count += 1
            skipped_internal_via_connection_count += 1
            continue
        from_junction_id = "" if from_edge is None else from_edge.attrib.get("to", "")
        to_junction_id = "" if to_edge is None else to_edge.attrib.get("from", "")
        if from_junction_id and to_junction_id and from_junction_id != to_junction_id:
            skipped_connection_count += 1
            skipped_non_adjacent_connection_count += 1
            continue
        candidate_junction = candidate_junctions.get(from_junction_id)
        if candidate_junction is not None and candidate_junction.attrib.get("intLanes", "").strip():
            skipped_connection_count += 1
            skipped_candidate_internal_junction_connection_count += 1
            continue
        topology_key = _connection_topology_key(connection)
        if topology_key in existing_topology_keys:
            skipped_connection_count += 1
            continue
        ET.SubElement(
            candidate_root,
            "connection",
            _road_connection_topology_replay_attrs(connection),
        )
        existing_topology_keys.add(topology_key)
        added_connection_count += 1

    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(candidate_root, space="  ")
    candidate_tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "repair_scope": "road_connection_topology",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "output_file": str(output_file),
        "selected_connection_count": selected_connection_count,
        "added_connection_count": added_connection_count,
        "skipped_connection_count": skipped_connection_count,
        "skipped_internal_via_connection_count": skipped_internal_via_connection_count,
        "skipped_candidate_internal_junction_connection_count": skipped_candidate_internal_junction_connection_count,
        "skipped_internal_edge_connection_count": skipped_internal_edge_connection_count,
        "skipped_non_adjacent_connection_count": skipped_non_adjacent_connection_count,
        "audit_summary": {
            "teacher_connection_count": audit["teacher_connection_count"],
            "already_present_connection_count": audit["already_present_connection_count"],
            "replayable_connection_count": audit["replayable_connection_count"],
            "blocked_connection_count": audit["blocked_connection_count"],
        },
        "warnings": [],
    }


def write_internal_movement_owner_replay_candidate(
    teacher_net_file: Path,
    candidate_net_file: Path,
    output_file: Path,
    *,
    owner_id: str,
    copy_tls: bool = True,
    teacher_edge_map: dict[str, str] | None = None,
    teacher_internal_lane_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    audit = build_road_connection_topology_replay_audit(
        teacher_net_file,
        candidate_net_file,
        max_examples=1_000_000,
    )
    candidate_tree = ET.parse(candidate_net_file)
    candidate_root = candidate_tree.getroot()
    candidate_edges = {
        edge.attrib.get("id", ""): edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    candidate_lane_ids = {
        lane.attrib.get("id", "")
        for edge in candidate_edges.values()
        for lane in edge.findall("lane")
        if lane.attrib.get("id")
    }
    existing_topology_keys = {
        _connection_topology_key(_connection_replay_record(connection))
        for connection in candidate_root.findall("connection")
    }
    added_connection_count = 0
    skipped_connection_count = 0
    skipped_nonlocal_edge_connection_count = 0
    mapped_external_edge_count = 0
    mapped_internal_lane_count = 0
    for connection in [*audit["replayable_connections"], *audit["blocked_connections"]]:
        if not _is_internal_movement_connection(connection):
            continue
        if _internal_movement_owner_id(connection) != owner_id:
            continue
        connection = _mapped_external_connection(connection, teacher_edge_map or {})
        mapped_external_edge_count += int(connection.get("from") != connection.get("_teacher_from", connection.get("from")))
        mapped_external_edge_count += int(connection.get("to") != connection.get("_teacher_to", connection.get("to")))
        connection = _mapped_internal_lane_connection(connection, teacher_internal_lane_map or {})
        mapped_internal_lane_count += int(connection.get("via") != connection.get("_teacher_via", connection.get("via")))
        if _connection_replay_blocking_reason(connection, candidate_edges, candidate_lane_ids):
            skipped_connection_count += 1
            continue
        if not _internal_movement_connection_is_candidate_local(connection, candidate_edges, owner_id):
            skipped_connection_count += 1
            skipped_nonlocal_edge_connection_count += 1
            continue
        topology_key = _connection_topology_key(connection)
        if topology_key in existing_topology_keys:
            skipped_connection_count += 1
            continue
        ET.SubElement(
            candidate_root,
            "connection",
            _internal_movement_replay_attrs(connection, copy_tls=copy_tls),
        )
        existing_topology_keys.add(topology_key)
        added_connection_count += 1

    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(candidate_root, space="  ")
    candidate_tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "repair_scope": "internal_movement_owner",
        "owner_id": owner_id,
        "copy_tls": copy_tls,
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "output_file": str(output_file),
        "added_connection_count": added_connection_count,
        "skipped_connection_count": skipped_connection_count,
        "skipped_nonlocal_edge_connection_count": skipped_nonlocal_edge_connection_count,
        "mapped_external_edge_count": mapped_external_edge_count,
        "mapped_internal_lane_count": mapped_internal_lane_count,
        "source_audit_summary": {
            "teacher_connection_count": audit["teacher_connection_count"],
            "already_present_connection_count": audit["already_present_connection_count"],
            "replayable_connection_count": audit["replayable_connection_count"],
            "blocked_connection_count": audit["blocked_connection_count"],
        },
        "warnings": [],
    }


def write_internal_movement_owner_bundle_replacement_candidate(
    teacher_net_file: Path,
    candidate_net_file: Path,
    output_file: Path,
    *,
    owner_id: str,
    teacher_edge_map: dict[str, str],
    copy_tls: bool = False,
) -> dict[str, Any]:
    teacher_root = ET.parse(teacher_net_file).getroot()
    candidate_tree = ET.parse(candidate_net_file)
    candidate_root = candidate_tree.getroot()
    candidate_owner_junction = candidate_root.find(f"junction[@id='{owner_id}']")
    candidate_owner_type = "" if candidate_owner_junction is None else candidate_owner_junction.attrib.get("type", "")
    dependency_blockers = _owner_bundle_road_dependency_blockers(
        teacher_root,
        candidate_root,
        owner_id,
        teacher_edge_map,
    )
    if dependency_blockers:
        return {
            "status": "blocked",
            "claim_status": "diagnostic-demo",
            "repair_scope": "internal_movement_owner_bundle",
            "blocking_reason": "missing_road_dependencies",
            "owner_id": owner_id,
            "copy_tls": copy_tls,
            "teacher_net_file": str(teacher_net_file),
            "candidate_net_file": str(candidate_net_file),
            "output_file": str(output_file),
            "blocked_dependency_count": len(dependency_blockers),
            "blocked_dependencies": dependency_blockers[:50],
            "warnings": ["owner bundle replay requires road-connectivity repair first"],
        }

    removed_internal_edge_count = _remove_children(
        candidate_root,
        lambda child: child.tag == "edge" and _internal_id_belongs_to_owner(child.attrib.get("id", ""), owner_id),
    )
    removed_internal_junction_count = _remove_children(
        candidate_root,
        lambda child: child.tag == "junction" and _internal_id_belongs_to_owner(child.attrib.get("id", ""), owner_id),
    )
    candidate_edges = {
        edge.attrib.get("id", ""): edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    removed_connection_count = _remove_children(
        candidate_root,
        lambda child: child.tag == "connection"
        and _connection_touches_owner_bundle(child, owner_id, candidate_edges),
    )

    added_internal_edge_count = 0
    for edge in teacher_root.findall("edge"):
        if not _internal_id_belongs_to_owner(edge.attrib.get("id", ""), owner_id):
            continue
        _insert_after_last(candidate_root, "edge", copy.deepcopy(edge))
        added_internal_edge_count += 1

    teacher_owner_junction = teacher_root.find(f"junction[@id='{owner_id}']")
    if teacher_owner_junction is not None:
        target_junction = candidate_root.find(f"junction[@id='{owner_id}']")
        if target_junction is None:
            target_junction = ET.Element("junction")
            _insert_after_last(candidate_root, "junction", target_junction)
        _replace_junction_contents(
            target_junction,
            teacher_owner_junction,
            teacher_edge_map=teacher_edge_map,
            copy_tls=copy_tls,
            fallback_type=candidate_owner_type,
        )

    added_internal_junction_count = 0
    for junction in teacher_root.findall("junction"):
        if not _internal_id_belongs_to_owner(junction.attrib.get("id", ""), owner_id):
            continue
        _insert_after_last(
            candidate_root,
            "junction",
            _mapped_junction_copy(
                junction,
                teacher_edge_map=teacher_edge_map,
                copy_tls=copy_tls,
                fallback_type="",
            ),
        )
        added_internal_junction_count += 1

    copied_tl_logic_count = _copy_owner_tl_logics(
        teacher_root,
        candidate_root,
        owner_id,
    ) if copy_tls else 0

    candidate_edges = {
        edge.attrib.get("id", ""): edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    candidate_lane_ids = {
        lane.attrib.get("id", "")
        for edge in candidate_edges.values()
        for lane in edge.findall("lane")
        if lane.attrib.get("id")
    }
    added_connection_count = 0
    skipped_connection_count = 0
    for connection in teacher_root.findall("connection"):
        record = _connection_replay_record(connection)
        if not _record_touches_owner_bundle(record, owner_id):
            continue
        record = _mapped_external_connection(record, teacher_edge_map)
        if _connection_replay_blocking_reason(record, candidate_edges, candidate_lane_ids):
            skipped_connection_count += 1
            continue
        ET.SubElement(
            candidate_root,
            "connection",
            _internal_movement_replay_attrs(record, copy_tls=copy_tls),
        )
        added_connection_count += 1

    stale_tls_sanitizer = (
        _downgrade_stale_owner_tls_connections(candidate_root, owner_id=owner_id)
        if copy_tls
        else {
            "status": "skipped",
            "downgraded_stale_tls_connection_count": 0,
            "downgraded_stale_tls_connections": [],
        }
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(candidate_root, space="  ")
    candidate_tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "repair_scope": "internal_movement_owner_bundle",
        "owner_id": owner_id,
        "copy_tls": copy_tls,
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "output_file": str(output_file),
        "removed_internal_edge_count": removed_internal_edge_count,
        "removed_internal_junction_count": removed_internal_junction_count,
        "removed_connection_count": removed_connection_count,
        "added_internal_edge_count": added_internal_edge_count,
        "added_internal_junction_count": added_internal_junction_count,
        "copied_tl_logic_count": copied_tl_logic_count,
        "added_connection_count": added_connection_count,
        "skipped_connection_count": skipped_connection_count,
        "stale_tls_sanitizer": stale_tls_sanitizer,
        "downgraded_stale_tls_connection_count": stale_tls_sanitizer[
            "downgraded_stale_tls_connection_count"
        ],
        "downgraded_stale_tls_connections": stale_tls_sanitizer["downgraded_stale_tls_connections"],
        "warnings": [],
    }


def compare_net_road_template_parity(
    teacher_net_file: Path,
    candidate_net_file: Path,
    *,
    max_examples: int = 3,
    semantic_gate: bool = False,
) -> dict[str, Any]:
    teacher_lane_templates = summarize_net_road_lane_model_templates(
        teacher_net_file,
        max_examples=max_examples,
    )
    candidate_lane_templates = summarize_net_road_lane_model_templates(
        candidate_net_file,
        max_examples=max_examples,
    )
    teacher_connection_templates = summarize_net_road_connection_templates(
        teacher_net_file,
        max_examples=max_examples,
    )
    candidate_connection_templates = summarize_net_road_connection_templates(
        candidate_net_file,
        max_examples=max_examples,
    )
    teacher_semantic_lane_templates = summarize_net_road_lane_model_templates(
        teacher_net_file,
        max_examples=max_examples,
        include_semantic=True,
    )
    candidate_semantic_lane_templates = summarize_net_road_lane_model_templates(
        candidate_net_file,
        max_examples=max_examples,
        include_semantic=True,
    )
    teacher_semantic_connection_templates = summarize_net_road_connection_templates(
        teacher_net_file,
        max_examples=max_examples,
        include_semantic=True,
    )
    candidate_semantic_connection_templates = summarize_net_road_connection_templates(
        candidate_net_file,
        max_examples=max_examples,
        include_semantic=True,
    )
    lane_parity = compare_road_template_summaries(
        teacher_lane_templates,
        candidate_lane_templates,
        key_fields=["type", "lane_signature"],
    )
    connection_parity = compare_road_template_summaries(
        teacher_connection_templates,
        candidate_connection_templates,
        key_fields=[
            "dir",
            "from_type",
            "from_lane",
            "from_lane_signature",
            "to_type",
            "to_lane",
            "to_lane_signature",
        ],
    )
    connection_topology_parity = compare_road_template_summaries(
        teacher_connection_templates,
        candidate_connection_templates,
        key_fields=[
            "dir",
            "from_type",
            "from_lane",
            "to_type",
            "to_lane",
        ],
    )
    semantic_lane_parity = compare_road_template_summaries(
        teacher_semantic_lane_templates,
        candidate_semantic_lane_templates,
        key_fields=["type", "lane_semantic_signature"],
    )
    semantic_connection_parity = compare_road_template_summaries(
        teacher_semantic_connection_templates,
        candidate_semantic_connection_templates,
        key_fields=[
            "dir",
            "from_type",
            "from_lane",
            "from_lane_semantic_signature",
            "to_type",
            "to_lane",
            "to_lane_semantic_signature",
        ],
    )
    active_lane_parity = semantic_lane_parity if semantic_gate else lane_parity
    active_connection_parity = semantic_connection_parity if semantic_gate else connection_parity
    status = (
        "pass"
        if active_lane_parity["status"] == active_connection_parity["status"] == "pass"
        else "fail"
    )
    gate = _road_template_gate_summary(active_lane_parity, active_connection_parity)
    if semantic_gate:
        gate["comparison_basis"] = "semantic"
        gate["raw_road_layer_status"] = (
            "pass" if lane_parity["status"] == connection_parity["status"] == "pass" else "fail"
        )
    report = {
        "status": status,
        "gate": gate,
        "comparison_basis": "semantic" if semantic_gate else "raw_xml",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "lane_template_summary": {
            "teacher_edge_count": _template_count_total(teacher_lane_templates),
            "candidate_edge_count": _template_count_total(candidate_lane_templates),
            "teacher_template_count": len(teacher_lane_templates),
            "candidate_template_count": len(candidate_lane_templates),
            "parity": lane_parity,
        },
        "connection_template_summary": {
            "teacher_connection_count": _template_count_total(teacher_connection_templates),
            "candidate_connection_count": _template_count_total(candidate_connection_templates),
            "teacher_template_count": len(teacher_connection_templates),
            "candidate_template_count": len(candidate_connection_templates),
            "parity": connection_parity,
        },
        "semantic_lane_template_summary": {
            "teacher_edge_count": _template_count_total(teacher_semantic_lane_templates),
            "candidate_edge_count": _template_count_total(candidate_semantic_lane_templates),
            "teacher_template_count": len(teacher_semantic_lane_templates),
            "candidate_template_count": len(candidate_semantic_lane_templates),
            "parity": semantic_lane_parity,
        },
        "semantic_connection_template_summary": {
            "teacher_connection_count": _template_count_total(teacher_semantic_connection_templates),
            "candidate_connection_count": _template_count_total(candidate_semantic_connection_templates),
            "teacher_template_count": len(teacher_semantic_connection_templates),
            "candidate_template_count": len(candidate_semantic_connection_templates),
            "parity": semantic_connection_parity,
        },
        "connection_topology_summary": {
            "teacher_connection_count": _template_count_total(teacher_connection_templates),
            "candidate_connection_count": _template_count_total(candidate_connection_templates),
            "teacher_template_count": len(teacher_connection_templates),
            "candidate_template_count": len(candidate_connection_templates),
            "parity": connection_topology_parity,
        },
    }
    report["repair_queue"] = build_road_template_repair_queue(report)
    return report


def audit_road_connectivity_parity(
    teacher_net_file: Path,
    candidate_net_file: Path,
    *,
    source_osm_file: Path | None = None,
    output_dir: Path | None = None,
    prefix: str = "road_connectivity_parity",
    max_examples: int = 20,
) -> dict[str, Any]:
    """Audit the complete road layer before any junction replay is promoted.

    The owner replay helpers are intentionally local probes: they can pass for
    one junction even while the selected network still differs materially from
    the teacher.  This audit is the workflow-level baseline.  It combines the
    lane/road template comparison with the complete teacher connection replay
    inventory and the internal-movement inventory, and it never mutates either
    input network.
    """

    report: dict[str, Any]
    try:
        from .reference_road_alignment import audit_reference_road_alignment

        road_template_report = compare_net_road_template_parity(
            teacher_net_file,
            candidate_net_file,
            max_examples=max_examples,
            semantic_gate=True,
        )
        connection_topology_report = build_road_connection_topology_replay_audit(
            teacher_net_file,
            candidate_net_file,
            max_examples=max_examples,
        )
        internal_movement_report = build_internal_movement_replay_audit(
            teacher_net_file,
            candidate_net_file,
            max_examples=max_examples,
        )
        reference_road_alignment_report = audit_reference_road_alignment(
            teacher_net_file,
            candidate_net_file,
            source_osm_file=source_osm_file,
            output_dir=None,
            prefix=f"{prefix}_reference_road_alignment",
            max_examples=max_examples,
        )
        topology_gate = {
            "status": (
                "pass"
                if int(connection_topology_report.get("replayable_connection_count", 0)) == 0
                and int(connection_topology_report.get("blocked_connection_count", 0)) == 0
                else "fail"
            ),
            "replayable_connection_count": int(
                connection_topology_report.get("replayable_connection_count", 0)
            ),
            "blocked_connection_count": int(
                connection_topology_report.get("blocked_connection_count", 0)
            ),
            "already_present_connection_count": int(
                connection_topology_report.get("already_present_connection_count", 0)
            ),
        }
        movement_gate = {
            "status": (
                "pass"
                if int(internal_movement_report.get("internal_movement_missing_count", 0)) == 0
                else "fail"
            ),
            "internal_movement_missing_count": int(
                internal_movement_report.get("internal_movement_missing_count", 0)
            ),
            "tls_controlled_missing_count": int(
                internal_movement_report.get("tls_controlled_missing_count", 0)
            ),
            "turnaround_missing_count": int(
                internal_movement_report.get("turnaround_missing_count", 0)
            ),
            "non_turnaround_missing_count": int(
                internal_movement_report.get("non_turnaround_missing_count", 0)
            ),
        }
        template_gate = dict(road_template_report.get("gate", {}))
        overall_status = (
            "pass"
            if str(road_template_report.get("status", "fail")) == "pass"
            and topology_gate["status"] == "pass"
            and movement_gate["status"] == "pass"
            else "fail"
        )
        report = {
            "status": overall_status,
            "claim_status": "reference-audit",
            "audit_scope": "complete_road_and_connection_layer",
            "teacher_net_file": str(teacher_net_file),
            "candidate_net_file": str(candidate_net_file),
            "road_template_status": str(road_template_report.get("status", "fail")),
            "road_template_gate": template_gate,
            "connection_topology_gate": topology_gate,
            "internal_movement_gate": movement_gate,
            "road_template_report": road_template_report,
            "connection_topology_report": connection_topology_report,
            "internal_movement_report": internal_movement_report,
            "reference_road_alignment": reference_road_alignment_report,
            "repair_queue": road_template_report.get("repair_queue", {}),
            "warnings": [],
        }
    except (ET.ParseError, OSError, ValueError, TypeError, KeyError) as exc:
        report = {
            "status": "blocked",
            "claim_status": "reference-audit",
            "audit_scope": "complete_road_and_connection_layer",
            "teacher_net_file": str(teacher_net_file),
            "candidate_net_file": str(candidate_net_file),
            "blocking_reason": f"road_connectivity_parity_audit_failed:{type(exc).__name__}",
            "error": str(exc),
            "road_template_status": "blocked",
            "road_template_gate": {"road_layer_status": "blocked"},
            "connection_topology_gate": {"status": "blocked"},
            "internal_movement_gate": {"status": "blocked"},
            "road_template_report": {},
            "connection_topology_report": {},
            "internal_movement_report": {},
            "reference_road_alignment": {},
            "repair_queue": {},
            "warnings": [],
        }

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        component_files = {
            "road_template_report_file": output_dir / f"{prefix}_road_template.json",
            "connection_topology_report_file": output_dir / f"{prefix}_connection_topology.json",
            "internal_movement_report_file": output_dir / f"{prefix}_internal_movement.json",
            "reference_road_alignment_report_file": output_dir / f"{prefix}_reference_road_alignment.json",
        }
        for key, path in component_files.items():
            payload_key = {
                "road_template_report_file": "road_template_report",
                "connection_topology_report_file": "connection_topology_report",
                "internal_movement_report_file": "internal_movement_report",
                "reference_road_alignment_report_file": "reference_road_alignment",
            }[key]
            if key.endswith("_report_file"):
                path.write_text(
                    json.dumps(report.get(payload_key, {}), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            report[key] = str(path)
        alignment = report.get("reference_road_alignment", {})
        if isinstance(alignment, dict):
            from .reference_road_alignment import _write_additional_overlay

            additional_path = output_dir / f"{prefix}_reference_road_alignment.add.xml"
            _write_additional_overlay(
                additional_path,
                alignment.get("review_locations", []),
            )
            report["reference_road_alignment_additional_file"] = str(additional_path)
        report_file = output_dir / f"{prefix}.json"
        report["report_file"] = str(report_file)
        report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def summarize_road_lane_model_templates(
    bundle: dict[str, Any],
    *,
    max_examples: int = 3,
    include_semantic: bool = False,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for edge in bundle.get("edges", []):
        if not isinstance(edge, dict):
            continue
        lane_signature = (
            _lane_semantic_signature(edge)
            if include_semantic
            else _lane_signature(edge)
        )
        key = (str(edge.get("type", "")), tuple(lane_signature))
        edge_id = str(edge.get("id", ""))
        if edge_id:
            groups.setdefault(key, []).append(edge_id)

    templates = []
    for (edge_type, lane_signature), edge_ids in groups.items():
        template = {
                "type": edge_type,
                "count": len(edge_ids),
                "example_edge_ids": sorted(edge_ids)[:max_examples],
            }
        if include_semantic:
            template["lane_semantic_signature"] = list(lane_signature)
        else:
            template["lane_signature"] = list(lane_signature)
        templates.append(template)
    return sorted(
        templates,
        key=lambda item: (
            -int(item["count"]),
            str(item["type"]),
            str(item.get("lane_signature", item.get("lane_semantic_signature", []))),
        ),
    )


def summarize_net_road_lane_model_templates(
    net_file: Path,
    *,
    max_examples: int = 3,
    include_semantic: bool = False,
) -> list[dict[str, Any]]:
    root = ET.parse(net_file).getroot()
    edges = [
        _canonical_edge_record(edge)
        for edge in root.findall("edge")
        if edge.attrib.get("id")
        and not edge.attrib["id"].startswith(":")
        and edge.attrib.get("function") != "internal"
    ]
    return summarize_road_lane_model_templates(
        {"edges": edges},
        max_examples=max_examples,
        include_semantic=include_semantic,
    )


def summarize_net_road_connection_templates(
    net_file: Path,
    *,
    max_examples: int = 3,
    include_semantic: bool = False,
) -> list[dict[str, Any]]:
    root = ET.parse(net_file).getroot()
    edges = {
        edge.attrib["id"]: _canonical_edge_record(edge)
        for edge in root.findall("edge")
        if edge.attrib.get("id")
        and not edge.attrib["id"].startswith(":")
        and edge.attrib.get("function") != "internal"
    }
    groups: dict[tuple[str, str, str, tuple[str, ...], str, str, tuple[str, ...]], list[str]] = {}
    for connection in root.findall("connection"):
        from_edge = edges.get(connection.attrib.get("from", ""))
        to_edge = edges.get(connection.attrib.get("to", ""))
        if from_edge is None or to_edge is None:
            continue
        from_lane = connection.attrib.get("fromLane", "")
        to_lane = connection.attrib.get("toLane", "")
        from_signature = (
            _lane_semantic_signature(from_edge)
            if include_semantic
            else _lane_signature(from_edge)
        )
        to_signature = (
            _lane_semantic_signature(to_edge)
            if include_semantic
            else _lane_signature(to_edge)
        )
        key = (
            connection.attrib.get("dir", ""),
            str(from_edge.get("type", "")),
            from_lane,
            tuple(from_signature),
            str(to_edge.get("type", "")),
            to_lane,
            tuple(to_signature),
        )
        example = f"{connection.attrib.get('from', '')}[{from_lane}]->{connection.attrib.get('to', '')}[{to_lane}]"
        groups.setdefault(key, []).append(example)

    templates = []
    for (
        direction,
        from_type,
        from_lane,
        from_lane_signature,
        to_type,
        to_lane,
        to_lane_signature,
    ), examples in groups.items():
        template = {
                "dir": direction,
                "from_type": from_type,
                "from_lane": from_lane,
                "to_type": to_type,
                "to_lane": to_lane,
                "count": len(examples),
                "example_connections": sorted(examples)[:max_examples],
            }
        if include_semantic:
            template["from_lane_semantic_signature"] = list(from_lane_signature)
            template["to_lane_semantic_signature"] = list(to_lane_signature)
        else:
            template["from_lane_signature"] = list(from_lane_signature)
            template["to_lane_signature"] = list(to_lane_signature)
        templates.append(template)
    return sorted(
        templates,
        key=lambda item: (
            -int(item["count"]),
            str(item["dir"]),
            str(item["from_type"]),
            str(item["from_lane"]),
            str(item["to_type"]),
            str(item["to_lane"]),
        ),
    )


def _canonical_edge_record(edge: ET.Element) -> dict[str, Any]:
    return {
        **_sorted_attrs(edge),
        "lanes": [_sorted_attrs(lane) for lane in sorted(edge.findall("lane"), key=_lane_sort_key)],
    }


def _canonical_junction_record(
    junction: ET.Element,
    selected_lane_ids: set[str],
    request_count: int,
) -> dict[str, Any]:
    record = _sorted_attrs(junction)
    record["incLanes"] = " ".join(lane_id for lane_id in record.get("incLanes", "").split() if lane_id in selected_lane_ids)
    record["intLanes"] = " ".join(lane_id for lane_id in record.get("intLanes", "").split() if lane_id in selected_lane_ids)
    record["requests"] = _neutral_requests(request_count)
    return record


def _canonical_road_connection_record(connection: ET.Element) -> dict[str, str]:
    return {
        key: connection.attrib[key]
        for key in ("dir", "from", "fromLane", "state", "to", "toLane")
        if key in connection.attrib
    }


def _connection_replay_record(connection: ET.Element) -> dict[str, str]:
    return {
        key: connection.attrib[key]
        for key in (
            "dir",
            "from",
            "fromLane",
            "linkIndex",
            "linkIndex2",
            "state",
            "tl",
            "to",
            "toLane",
            "via",
        )
        if key in connection.attrib
    }


def _request_counts_by_junction(
    connections: list[dict[str, str]],
    edges: dict[str, ET.Element],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for connection in connections:
        source_edge = edges.get(connection.get("from", ""))
        if source_edge is None:
            continue
        junction_id = source_edge.attrib.get("to", "")
        if junction_id:
            counts[junction_id] = counts.get(junction_id, 0) + 1
    return counts


def _neutral_requests(count: int) -> list[dict[str, str]]:
    width = "0" * count
    return [
        {"cont": "0", "foes": width, "index": str(index), "response": width}
        for index in range(count)
    ]


def _record_ids(records: Any) -> set[str]:
    return {
        str(record.get("id", ""))
        for record in records
        if isinstance(record, dict) and record.get("id")
    }


def _unambiguous_split_root_aliases(
    teacher_edge_ids: list[str],
    candidate_edge_ids: list[str],
) -> list[dict[str, str]]:
    teacher_by_root = _edge_ids_by_split_root(teacher_edge_ids)
    candidate_by_root = _edge_ids_by_split_root(candidate_edge_ids)
    aliases = []
    for root in sorted(set(teacher_by_root) & set(candidate_by_root)):
        if len(teacher_by_root[root]) == 1 and len(candidate_by_root[root]) == 1:
            aliases.append(
                {
                    "root": root,
                    "teacher_edge_id": teacher_by_root[root][0],
                    "candidate_edge_id": candidate_by_root[root][0],
                }
            )
    return aliases


def _edge_ids_by_split_root(edge_ids: list[str]) -> dict[str, list[str]]:
    by_root: dict[str, list[str]] = {}
    for edge_id in edge_ids:
        by_root.setdefault(_split_edge_root(edge_id), []).append(edge_id)
    return {root: sorted(ids) for root, ids in by_root.items()}


def _aliased_road_connections(records: Any, edge_alias_map: dict[str, str]) -> list[dict[str, Any]]:
    aliased = []
    for record in records:
        if not isinstance(record, dict):
            continue
        mapped = dict(record)
        for key in ("from", "to"):
            mapped[key] = edge_alias_map.get(str(mapped.get(key, "")), str(mapped.get(key, "")))
        aliased.append(mapped)
    return aliased


def _missing_records(left: Any, right: Any) -> list[dict[str, Any]]:
    right_keys = {_record_key(record) for record in right if isinstance(record, dict)}
    return [
        dict(record)
        for record in left
        if isinstance(record, dict) and _record_key(record) not in right_keys
    ]


def _record_key(record: dict[str, Any]) -> str:
    return "|".join(f"{key}={record[key]}" for key in sorted(record))


def _template_summary_key(template: dict[str, Any], key_fields: list[str]) -> str:
    return json.dumps(
        {field: template.get(field) for field in key_fields},
        sort_keys=True,
    )


def _sort_templates_by_count(templates: Any) -> list[dict[str, Any]]:
    return sorted(
        (dict(template) for template in templates if isinstance(template, dict)),
        key=lambda template: (-int(template.get("count", 0)), _record_key(template)),
    )


def _template_count_total(templates: list[dict[str, Any]]) -> int:
    return sum(int(template.get("count", 0)) for template in templates)


def _road_template_gate_summary(
    lane_parity: dict[str, Any],
    connection_parity: dict[str, Any],
) -> dict[str, Any]:
    parity_passed = lane_parity["status"] == connection_parity["status"] == "pass"
    road_layer_status = "pass" if parity_passed else "fail"
    return {
        "road_layer_status": road_layer_status,
        "can_enter_junction_replay": road_layer_status == "pass",
        "blocking_reason": "" if road_layer_status == "pass" else "road_template_parity_failed",
        "lane_missing_template_count": int(lane_parity.get("missing_template_count", 0)),
        "connection_missing_template_count": int(connection_parity.get("missing_template_count", 0)),
        "lane_extra_template_count": int(lane_parity.get("extra_template_count", 0)),
        "connection_extra_template_count": int(connection_parity.get("extra_template_count", 0)),
        "lane_common_count_delta_sum": int(lane_parity.get("common_count_delta_sum", 0)),
        "connection_common_count_delta_sum": int(connection_parity.get("common_count_delta_sum", 0)),
    }


def _road_template_gate_metrics(
    report: dict[str, Any],
    metrics: tuple[str, ...] = ROAD_TEMPLATE_GATE_METRICS,
) -> dict[str, int]:
    gate = report.get("gate", {})
    return {key: int(gate.get(key, 0)) for key in metrics}


def _road_lane_template_edge_index(net_file: Path) -> dict[str, tuple[str, tuple[str, ...]]]:
    root = ET.parse(net_file).getroot()
    index = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function") == "internal":
            continue
        edge_record = _canonical_edge_record(edge)
        index[edge_id] = (str(edge_record.get("type", "")), tuple(_lane_signature(edge_record)))
    return index


def _connection_replay_blocking_reason(
    connection: dict[str, str],
    candidate_edges: dict[str, ET.Element],
    candidate_lane_ids: set[str],
) -> str:
    from_edge = candidate_edges.get(connection.get("from", ""))
    if from_edge is None:
        return "missing_from_edge"
    to_edge = candidate_edges.get(connection.get("to", ""))
    if to_edge is None:
        return "missing_to_edge"
    from_lane = from_edge.find(f"./lane[@index='{connection.get('fromLane', '')}']")
    if from_lane is None:
        return "missing_from_lane"
    to_lane = to_edge.find(f"./lane[@index='{connection.get('toLane', '')}']")
    if to_lane is None:
        return "missing_to_lane"
    via_lane = connection.get("via", "")
    if via_lane and via_lane not in candidate_lane_ids:
        return "missing_via_lane"
    return ""


def _connection_topology_key(connection: dict[str, str]) -> tuple[str, str, str, str, str, str]:
    return (
        connection.get("from", ""),
        connection.get("to", ""),
        connection.get("fromLane", ""),
        connection.get("toLane", ""),
        connection.get("dir", ""),
        connection.get("via", ""),
    )


def _road_connection_topology_replay_attrs(connection: dict[str, str]) -> dict[str, str]:
    return {
        key: connection[key]
        for key in ("from", "to", "fromLane", "toLane", "dir", "state", "via")
        if connection.get(key)
    }


def _internal_movement_replay_attrs(connection: dict[str, str], *, copy_tls: bool = True) -> dict[str, str]:
    tls_fields = ("tl", "linkIndex", "linkIndex2") if copy_tls else ()
    return {
        key: connection[key]
        for key in (
            "from",
            "to",
            "fromLane",
            "toLane",
            "via",
            *tls_fields,
            "dir",
            "state",
        )
        if connection.get(key)
    }


def _mapped_external_connection(
    connection: dict[str, str],
    teacher_edge_map: dict[str, str],
) -> dict[str, str]:
    mapped = dict(connection)
    mapped["_teacher_from"] = mapped.get("from", "")
    mapped["_teacher_to"] = mapped.get("to", "")
    for key in ("from", "to"):
        edge_id = mapped.get(key, "")
        if edge_id and not _looks_internal_edge_id(edge_id):
            mapped[key] = teacher_edge_map.get(edge_id, edge_id)
    return mapped


def _mapped_internal_lane_connection(
    connection: dict[str, str],
    teacher_internal_lane_map: dict[str, str],
) -> dict[str, str]:
    mapped = dict(connection)
    mapped["_teacher_via"] = mapped.get("via", "")
    via = mapped.get("via", "")
    if via:
        mapped["via"] = teacher_internal_lane_map.get(via, via)
    return mapped


def _movement_lane_key(connection: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        connection.get("from", ""),
        connection.get("to", ""),
        connection.get("fromLane", ""),
        connection.get("toLane", ""),
        connection.get("dir", ""),
    )


def _replace_junction_contents(
    target: ET.Element,
    source: ET.Element,
    *,
    teacher_edge_map: dict[str, str],
    copy_tls: bool,
    fallback_type: str,
) -> None:
    copied = _mapped_junction_copy(
        source,
        teacher_edge_map=teacher_edge_map,
        copy_tls=copy_tls,
        fallback_type=fallback_type,
    )
    target.attrib.clear()
    target.attrib.update(copied.attrib)
    for child in list(target):
        target.remove(child)
    for child in copied:
        target.append(copy.deepcopy(child))


def _mapped_junction_copy(
    junction: ET.Element,
    *,
    teacher_edge_map: dict[str, str],
    copy_tls: bool,
    fallback_type: str,
) -> ET.Element:
    copied = copy.deepcopy(junction)
    attrs = _mapped_junction_attrs(copied.attrib, teacher_edge_map)
    if not copy_tls and attrs.get("type") == "traffic_light":
        attrs["type"] = fallback_type or "priority"
    copied.attrib.clear()
    copied.attrib.update(attrs)
    return copied


def _mapped_junction_attrs(attrs: dict[str, str], teacher_edge_map: dict[str, str]) -> dict[str, str]:
    mapped = dict(attrs)
    for key in ("incLanes", "intLanes"):
        if key in mapped:
            mapped[key] = " ".join(_mapped_lane_id(token, teacher_edge_map) for token in mapped[key].split())
    return mapped


def _mapped_lane_id(lane_id: str, teacher_edge_map: dict[str, str]) -> str:
    edge_id, sep, index = lane_id.rpartition("_")
    if not sep:
        return lane_id
    return f"{teacher_edge_map.get(edge_id, edge_id)}_{index}"


def _renamed_lane_id(lane_id: str, old_edge_id: str, new_edge_id: str) -> str:
    edge_id, sep, index = lane_id.rpartition("_")
    if not sep or edge_id != old_edge_id:
        return lane_id
    return f"{new_edge_id}_{index}"


def _rename_edge_references(root: ET.Element, old_edge_id: str, new_edge_id: str) -> None:
    for element in root.iter():
        for key in ("from", "to"):
            if element.attrib.get(key) == old_edge_id:
                element.attrib[key] = new_edge_id
        for key in ("incLanes", "intLanes"):
            if key in element.attrib:
                element.attrib[key] = " ".join(
                    _renamed_lane_id(token, old_edge_id, new_edge_id)
                    for token in element.attrib[key].split()
                )
        if "edges" in element.attrib:
            element.attrib["edges"] = " ".join(
                new_edge_id if token == old_edge_id else token
                for token in element.attrib["edges"].split()
            )


def _connection_touches_owner_bundle(
    connection: ET.Element,
    owner_id: str,
    edges: dict[str, ET.Element],
) -> bool:
    return _record_touches_owner_bundle(_connection_replay_record(connection), owner_id) or (
        edges.get(connection.attrib.get("from", ""), ET.Element("edge")).attrib.get("to", "") == owner_id
        and edges.get(connection.attrib.get("to", ""), ET.Element("edge")).attrib.get("from", "") == owner_id
    )


def _record_touches_owner_bundle(connection: dict[str, str], owner_id: str) -> bool:
    return owner_id in {
        _internal_owner_id(connection.get("from", "")),
        _internal_owner_id(connection.get("to", "")),
        _internal_owner_id(connection.get("via", "")),
    } or any(
        _internal_id_belongs_to_owner(connection.get(key, ""), owner_id)
        for key in ("from", "to", "via")
    )


def _copy_owner_tl_logics(
    teacher_root: ET.Element,
    candidate_root: ET.Element,
    owner_id: str,
) -> int:
    tls_ids = {owner_id}
    owner_junction = teacher_root.find(f"junction[@id='{owner_id}']")
    if owner_junction is not None and owner_junction.attrib.get("tl"):
        tls_ids.add(owner_junction.attrib["tl"])
    for connection in teacher_root.findall("connection"):
        record = _connection_replay_record(connection)
        if _record_touches_owner_bundle(record, owner_id) and record.get("tl"):
            tls_ids.add(record["tl"])

    copied = 0
    for tls_id in sorted(tls_ids):
        teacher_tl_logic = teacher_root.find(f"tlLogic[@id='{tls_id}']")
        if teacher_tl_logic is None:
            continue
        _remove_children(
            candidate_root,
            lambda child, target=tls_id: child.tag == "tlLogic" and child.attrib.get("id") == target,
        )
        _insert_before_first(candidate_root, "connection", copy.deepcopy(teacher_tl_logic))
        copied += 1
    return copied


def _downgrade_stale_owner_tls_connections(root: ET.Element, *, owner_id: str) -> dict[str, Any]:
    capacity = _owner_tllogic_min_state_length(root, owner_id)
    downgraded: list[dict[str, str]] = []
    if capacity is None:
        return {
            "status": "pass",
            "owner_id": owner_id,
            "owner_tllogic_state_length": 0,
            "downgraded_stale_tls_connection_count": 0,
            "downgraded_stale_tls_connections": [],
        }
    for connection in root.findall("connection"):
        if connection.attrib.get("tl") != owner_id or not _connection_has_link_index(connection):
            continue
        if _connection_link_indices_fit_capacity(connection, capacity):
            continue
        downgraded.append(dict(connection.attrib))
        for attr in ("tl", "linkIndex", "linkIndex2"):
            connection.attrib.pop(attr, None)
        connection.set("uncontrolled", "true")
    return {
        "status": "pass",
        "owner_id": owner_id,
        "owner_tllogic_state_length": capacity,
        "downgraded_stale_tls_connection_count": len(downgraded),
        "downgraded_stale_tls_connections": downgraded,
    }


def _owner_tllogic_min_state_length(root: ET.Element, owner_id: str) -> int | None:
    tl_logic = root.find(f"tlLogic[@id='{owner_id}']")
    if tl_logic is None:
        return None
    state_lengths = [
        len(phase.attrib.get("state", ""))
        for phase in tl_logic.findall("phase")
        if phase.attrib.get("state")
    ]
    if not state_lengths:
        return None
    return min(state_lengths)


def _connection_has_link_index(connection: ET.Element) -> bool:
    return bool(connection.attrib.get("linkIndex") or connection.attrib.get("linkIndex2"))


def _connection_link_indices_fit_capacity(connection: ET.Element, capacity: int) -> bool:
    for attr in ("linkIndex", "linkIndex2"):
        value = connection.attrib.get(attr, "")
        if not value:
            continue
        try:
            if int(value) >= capacity:
                return False
        except ValueError:
            return False
    return True


def _owner_bundle_road_dependency_blockers(
    teacher_root: ET.Element,
    candidate_root: ET.Element,
    owner_id: str,
    teacher_edge_map: dict[str, str],
) -> list[dict[str, str]]:
    candidate_edges = {
        edge.attrib.get("id", ""): edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    teacher_internal_edges = {
        edge.attrib.get("id", ""): edge
        for edge in teacher_root.findall("edge")
        if _internal_id_belongs_to_owner(edge.attrib.get("id", ""), owner_id)
    }
    replay_edges = dict(candidate_edges)
    replay_edges.update(teacher_internal_edges)
    available_lane_ids = {
        lane.attrib.get("id", "")
        for edge in replay_edges.values()
        for lane in edge.findall("lane")
        if lane.attrib.get("id")
    }
    blockers: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()

    def append_blocker(blocker: dict[str, str]) -> None:
        key = tuple(f"{name}={blocker.get(name, '')}" for name in sorted(blocker))
        if key in seen:
            return
        seen.add(key)
        blockers.append(blocker)

    for junction in teacher_root.findall("junction"):
        junction_id = junction.attrib.get("id", "")
        if junction_id != owner_id and not _internal_id_belongs_to_owner(junction_id, owner_id):
            continue
        mapped_attrs = _mapped_junction_attrs(junction.attrib, teacher_edge_map)
        for field in ("incLanes", "intLanes"):
            for lane_id in mapped_attrs.get(field, "").split():
                if lane_id not in available_lane_ids:
                    append_blocker(
                        {
                            "kind": "missing_junction_lane",
                            "junction": junction_id,
                            "field": field,
                            "lane": lane_id,
                        }
                    )

    for connection in teacher_root.findall("connection"):
        record = _connection_replay_record(connection)
        if not _record_touches_owner_bundle(record, owner_id):
            continue
        mapped_record = _mapped_external_connection(record, teacher_edge_map)
        reason = _connection_replay_blocking_reason(mapped_record, replay_edges, available_lane_ids)
        if reason:
            append_blocker(
                {
                    "kind": "missing_connection_dependency",
                    "reason": reason,
                    "from": mapped_record.get("from", ""),
                    "to": mapped_record.get("to", ""),
                    "via": mapped_record.get("via", ""),
                    "teacher_from": mapped_record.get("_teacher_from", ""),
                    "teacher_to": mapped_record.get("_teacher_to", ""),
                }
            )

    return blockers


def _remove_children(root: ET.Element, predicate: Any) -> int:
    removed = 0
    for child in list(root):
        if predicate(child):
            root.remove(child)
            removed += 1
    return removed


def _insert_after_last(root: ET.Element, tag: str, element: ET.Element) -> None:
    indexes = [index for index, child in enumerate(list(root)) if child.tag == tag]
    root.insert((indexes[-1] + 1) if indexes else len(root), element)


def _insert_before_first(root: ET.Element, tag: str, element: ET.Element) -> None:
    indexes = [index for index, child in enumerate(list(root)) if child.tag == tag]
    root.insert(indexes[0] if indexes else len(root), element)


def _internal_movement_connection_is_candidate_local(
    connection: dict[str, str],
    candidate_edges: dict[str, ET.Element],
    owner_id: str,
) -> bool:
    source = connection.get("from", "")
    target = connection.get("to", "")
    if not _looks_internal_edge_id(source):
        source_edge = candidate_edges.get(source)
        if source_edge is not None and source_edge.attrib.get("to", "") != owner_id:
            return False
    if not _looks_internal_edge_id(target):
        target_edge = candidate_edges.get(target)
        if target_edge is not None and target_edge.attrib.get("from", "") != owner_id:
            return False
    return True


def _is_internal_movement_connection(connection: dict[str, str]) -> bool:
    return bool(
        connection.get("via", "")
        or _looks_internal_edge_id(connection.get("from", ""))
        or _looks_internal_edge_id(connection.get("to", ""))
    )


def _internal_movement_owner_id(connection: dict[str, str]) -> str:
    for value in (connection.get("via", ""), connection.get("from", ""), connection.get("to", "")):
        owner_id = _internal_owner_id(value)
        if owner_id:
            return owner_id
    return ""


def _internal_owner_id(edge_or_lane_id: str) -> str:
    if not edge_or_lane_id.startswith(":"):
        return ""
    return edge_or_lane_id[1:].split("_", 1)[0]


def _internal_id_belongs_to_owner(edge_or_lane_id: str, owner_id: str) -> bool:
    return bool(owner_id and edge_or_lane_id.startswith(f":{owner_id}_"))


def _looks_internal_edge_id(edge_id: str) -> bool:
    return edge_id.startswith(":")


def _internal_movement_owner_summary(
    owner_id: str,
    records: list[dict[str, str]],
    *,
    max_examples: int,
) -> dict[str, Any]:
    dir_counts = Counter(record.get("dir", "") or "blank" for record in records)
    return {
        "owner_id": owner_id,
        "missing_connection_count": len(records),
        "internal_via_connection_count": sum(1 for record in records if record.get("via", "")),
        "internal_edge_connection_count": sum(
            1
            for record in records
            if _looks_internal_edge_id(record.get("from", ""))
            or _looks_internal_edge_id(record.get("to", ""))
        ),
        "tls_controlled_connection_count": sum(
            1 for record in records if record.get("tl", "") and record.get("linkIndex", "")
        ),
        "turnaround_connection_count": sum(1 for record in records if record.get("dir", "") == "t"),
        "non_turnaround_connection_count": sum(1 for record in records if record.get("dir", "") != "t"),
        "dir_counts": dict(sorted(dir_counts.items())),
        "example_connections": records[:max_examples],
    }


def _owner_external_approach_edges(root: ET.Element, owner_id: str) -> dict[str, str]:
    edges = {}
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or _is_internal_edge(edge):
            continue
        if edge.attrib.get("to", "") == owner_id:
            edges[edge_id] = "incoming"
        elif edge.attrib.get("from", "") == owner_id:
            edges[edge_id] = "outgoing"
    return edges


def _same_terminal_endpoint_matches(
    teacher_edge: ET.Element | None,
    candidate_edges: list[ET.Element],
    *,
    owner_id: str,
    direction: str,
) -> list[str]:
    if teacher_edge is None:
        return []
    endpoint_key = "from" if direction == "incoming" else "to"
    teacher_endpoint = teacher_edge.attrib.get(endpoint_key, "")
    if not teacher_endpoint or teacher_endpoint == owner_id:
        return []
    return [
        edge.attrib["id"]
        for edge in candidate_edges
        if edge.attrib.get(endpoint_key, "") == teacher_endpoint
    ]


def _edge_touches_terminal_endpoint(
    teacher_edge: ET.Element | None,
    candidate_edge: ET.Element | None,
    *,
    owner_id: str,
    direction: str,
) -> bool:
    if teacher_edge is None or candidate_edge is None:
        return False
    endpoint_key = "from" if direction == "incoming" else "to"
    teacher_endpoint = teacher_edge.attrib.get(endpoint_key, "")
    return bool(
        teacher_endpoint
        and teacher_endpoint != owner_id
        and teacher_endpoint in {candidate_edge.attrib.get("from", ""), candidate_edge.attrib.get("to", "")}
    )


def _lane_allows_vehicle(lane: ET.Element) -> bool:
    allow = set(lane.attrib.get("allow", "").split())
    if allow:
        return bool(allow - {"pedestrian", "bicycle"})
    return "passenger" not in set(lane.attrib.get("disallow", "").split())


def _lane_connection_index(
    root: ET.Element,
    *,
    edge_ids: set[str],
    edge_key: str,
    lane_key: str,
) -> dict[tuple[str, str], list[dict[str, str]]]:
    index: dict[tuple[str, str], list[dict[str, str]]] = {}
    for connection in root.findall("connection"):
        edge_id = connection.attrib.get(edge_key, "")
        if edge_id not in edge_ids:
            continue
        lane_index = connection.attrib.get(lane_key, "0")
        index.setdefault((edge_id, lane_index), []).append(_connection_replay_record(connection))
    return index


def _lane_connection_delta(
    teacher_edge_id: str,
    candidate_edge_id: str,
    lane_index: str,
    teacher_records: list[dict[str, str]],
    candidate_records: list[dict[str, str]],
) -> dict[str, Any]:
    teacher_turnaround = sum(1 for record in teacher_records if record.get("dir", "") == "t")
    candidate_turnaround = sum(1 for record in candidate_records if record.get("dir", "") == "t")
    teacher_non_turnaround = len(teacher_records) - teacher_turnaround
    candidate_non_turnaround = len(candidate_records) - candidate_turnaround
    flags = []
    if teacher_non_turnaround > 0 and candidate_non_turnaround == 0:
        flags.append("missing_non_turnaround_vehicle_connection")
        if candidate_turnaround > 0:
            flags.append("turnaround_only_candidate")
    if teacher_turnaround > 0 and candidate_turnaround == 0:
        flags.append("missing_turnaround")
    return {
        "teacher_edge_id": teacher_edge_id,
        "candidate_edge_id": candidate_edge_id,
        "lane_index": lane_index,
        "teacher_non_turnaround": teacher_non_turnaround,
        "candidate_non_turnaround": candidate_non_turnaround,
        "teacher_turnaround": teacher_turnaround,
        "candidate_turnaround": candidate_turnaround,
        "teacher_dirs": sorted(record.get("dir", "") for record in teacher_records),
        "candidate_dirs": sorted(record.get("dir", "") for record in candidate_records),
        "flags": flags,
    }


def _lane_connection_delta_is_reportable(delta: dict[str, Any]) -> bool:
    return bool(delta["flags"]) or (
        delta["teacher_non_turnaround"],
        delta["teacher_turnaround"],
    ) != (
        delta["candidate_non_turnaround"],
        delta["candidate_turnaround"],
    )


def _lane_delta_flag_count(
    lane_deltas: list[dict[str, Any]],
    side: str,
    flag: str,
) -> int:
    return sum(1 for delta in lane_deltas if flag in delta[side]["flags"])


def _candidate_approach_edge_chain(
    candidate_edges: dict[str, ET.Element],
    *,
    root_edge_id: str,
    owner_id: str,
    terminal_junction_id: str,
    direction: str,
    max_chain_edges: int,
) -> tuple[list[str], str]:
    if direction == "incoming":
        current_to = owner_id
        reversed_chain = []
        seen = set()
        for _ in range(max_chain_edges):
            matches = sorted(
                edge_id
                for edge_id, edge in candidate_edges.items()
                if _split_edge_root(edge_id) == root_edge_id
                and edge.attrib.get("to", "") == current_to
            )
            if len(matches) > 1:
                return [], "ambiguous"
            if not matches:
                return list(reversed(reversed_chain)), ""
            edge_id = matches[0]
            if edge_id in seen:
                return [], "ambiguous"
            seen.add(edge_id)
            reversed_chain.append(edge_id)
            current_to = candidate_edges[edge_id].attrib.get("from", "")
            if current_to == terminal_junction_id:
                return list(reversed(reversed_chain)), ""
        return list(reversed(reversed_chain)), ""

    current_from = owner_id
    chain = []
    seen = set()
    for _ in range(max_chain_edges):
        matches = sorted(
            edge_id
            for edge_id, edge in candidate_edges.items()
            if _split_edge_root(edge_id) == root_edge_id
            and edge.attrib.get("from", "") == current_from
        )
        if len(matches) > 1:
            return [], "ambiguous"
        if not matches:
            return chain, ""
        edge_id = matches[0]
        if edge_id in seen:
            return [], "ambiguous"
        seen.add(edge_id)
        chain.append(edge_id)
        current_from = candidate_edges[edge_id].attrib.get("to", "")
        if current_from == terminal_junction_id:
            return chain, ""
    return chain, ""


def _bidirectional_span_key(edge_id: str) -> str:
    return edge_id.lstrip("-")


def _candidate_chain_intermediate_junction_ids(
    fragmented_teacher_edges: list[dict[str, Any]],
    candidate_edges: dict[str, ET.Element],
) -> list[str]:
    junction_ids = set()
    for item in fragmented_teacher_edges:
        edge_ids = [
            edge_id
            for edge_id in item["candidate_edge_ids"]
            if edge_id in candidate_edges
        ]
        if len(edge_ids) < 2:
            continue
        endpoints = {
            candidate_edges[edge_ids[0]].attrib.get("from", ""),
            candidate_edges[edge_ids[-1]].attrib.get("to", ""),
        }
        for edge_id in edge_ids:
            edge = candidate_edges[edge_id]
            for junction_id in (edge.attrib.get("from", ""), edge.attrib.get("to", "")):
                if junction_id and junction_id not in endpoints:
                    junction_ids.add(junction_id)
    return sorted(junction_ids)


def _blocked_intermediate_incident_edge_ids(
    candidate_edges: dict[str, ET.Element],
    *,
    intermediate_junction_ids: list[str],
    chain_edge_ids: set[str],
) -> list[str]:
    intermediate_junction_set = set(intermediate_junction_ids)
    return sorted(
        edge_id
        for edge_id, edge in candidate_edges.items()
        if edge_id not in chain_edge_ids
        and (
            edge.attrib.get("from", "") in intermediate_junction_set
            or edge.attrib.get("to", "") in intermediate_junction_set
        )
    )


def _filter_junction_lane_references(root: ET.Element) -> None:
    lane_ids = {
        lane.attrib.get("id", "")
        for edge in root.findall("edge")
        for lane in edge.findall("lane")
        if lane.attrib.get("id")
    }
    for junction in root.findall("junction"):
        for key in ("incLanes", "intLanes"):
            if key in junction.attrib:
                junction.attrib[key] = " ".join(
                    lane_id
                    for lane_id in junction.attrib[key].split()
                    if lane_id in lane_ids
                )


def _teacher_endpoint_owner_ids_for_edges(
    teacher_net_file: Path,
    teacher_edge_ids: list[str],
) -> list[str]:
    requested_edge_ids = set(teacher_edge_ids)
    endpoints = set()
    for edge in ET.parse(teacher_net_file).getroot().findall("edge"):
        if edge.attrib.get("id", "") not in requested_edge_ids:
            continue
        endpoints.update(
            endpoint
            for endpoint in (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
            if endpoint
        )
    return sorted(endpoints)


def _split_edge_root(edge_id: str) -> str:
    head, sep, tail = edge_id.rpartition("#")
    return head if sep and tail.isdigit() else edge_id


def _osm_way_edge_root(edge_id: str) -> str:
    root = _split_edge_root(edge_id).lstrip("-")
    match = re.match(r"(\d+)(?:_|$)", root)
    return match.group(1) if match else root


def _is_internal_edge(edge: ET.Element | None) -> bool:
    return edge is not None and (
        edge.attrib.get("id", "").startswith(":") or edge.attrib.get("function") == "internal"
    )


def _lane_signature_indexes(signature: list[str]) -> tuple[str, ...]:
    return tuple(_lane_signature_attrs(item).get("index", "") for item in signature)


def _lane_signature_attrs(signature: str) -> dict[str, str]:
    attrs = {}
    for part in signature.split("|"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        attrs[key] = value
    return attrs


def _apply_lane_signature(edge: ET.Element, signature: list[str]) -> int:
    changed = 0
    lanes = list(edge.findall("lane"))
    if len(lanes) != len(signature):
        return 0
    for lane, lane_signature in zip(lanes, signature):
        attrs = _lane_signature_attrs(lane_signature)
        before = (lane.attrib.get("allow"), lane.attrib.get("disallow"))
        for attr_name in ("allow", "disallow"):
            value = attrs.get(attr_name, "")
            if value:
                lane.set(attr_name, value)
            else:
                lane.attrib.pop(attr_name, None)
        after = (lane.attrib.get("allow"), lane.attrib.get("disallow"))
        changed += int(before != after)
    return changed


def _replace_edge_lanes(edge: ET.Element, lanes: Any) -> int:
    new_lanes = [dict(lane) for lane in lanes if isinstance(lane, dict)]
    if not new_lanes:
        return 0
    before = [dict(lane.attrib) for lane in edge.findall("lane")]
    if before == new_lanes:
        return 0
    for lane in list(edge.findall("lane")):
        edge.remove(lane)
    for lane in new_lanes:
        ET.SubElement(edge, "lane", lane)
    return len(new_lanes)


def _mapped_edge_lane_attrs(
    attrs: dict[str, str],
    teacher_edge_id: str,
    candidate_edge_id: str,
    *,
    source_offset: tuple[float, float] = (0.0, 0.0),
    target_offset: tuple[float, float] = (0.0, 0.0),
) -> dict[str, str]:
    mapped = dict(attrs)
    lane_id = mapped.get("id", "")
    if lane_id:
        mapped["id"] = _mapped_lane_id(lane_id, {teacher_edge_id: candidate_edge_id})
    elif mapped.get("index", ""):
        mapped["id"] = f"{candidate_edge_id}_{mapped['index']}"
    if mapped.get("shape"):
        mapped["shape"] = _reproject_shape(mapped["shape"], source_offset, target_offset)
    return mapped


def _root_net_offset(root: ET.Element) -> tuple[float, float]:
    location = root.find("location")
    raw = "" if location is None else str(location.attrib.get("netOffset", ""))
    parts = raw.split(",")
    if len(parts) < 2:
        return (0.0, 0.0)
    return (float(parts[0]), float(parts[1]))


def _reproject_shape(
    shape: str,
    source_offset: tuple[float, float],
    target_offset: tuple[float, float],
) -> str:
    if source_offset == target_offset:
        return shape
    points = []
    for part in shape.split():
        coords = part.split(",")
        if len(coords) < 2:
            continue
        x = float(coords[0]) - source_offset[0] + target_offset[0]
        y = float(coords[1]) - source_offset[1] + target_offset[1]
        points.append(f"{x:.2f},{y:.2f}")
    return " ".join(points) if points else shape


def _reproject_coord(value: str, source_offset: float, target_offset: float) -> str:
    return f"{float(value) - source_offset + target_offset:.2f}"


def _edge_attrs_with_lanes(edge: ET.Element) -> dict[str, Any]:
    record = dict(edge.attrib)
    record["lanes"] = [dict(lane.attrib) for lane in edge.findall("lane")]
    return record


def _reproject_edge_record(
    edge: dict[str, Any],
    *,
    source_offset: tuple[float, float],
    target_offset: tuple[float, float],
) -> dict[str, Any]:
    if source_offset == target_offset:
        return edge
    mapped = dict(edge)
    if mapped.get("shape"):
        mapped["shape"] = _reproject_shape(str(mapped["shape"]), source_offset, target_offset)
    mapped["lanes"] = [
        {
            **lane,
            **(
                {"shape": _reproject_shape(str(lane["shape"]), source_offset, target_offset)}
                if isinstance(lane, dict) and lane.get("shape")
                else {}
            ),
        }
        for lane in mapped.get("lanes", [])
        if isinstance(lane, dict)
    ]
    return mapped


def _minimal_endpoint_junction_attrs(
    junction: ET.Element,
    *,
    source_offset: tuple[float, float] = (0.0, 0.0),
    target_offset: tuple[float, float] = (0.0, 0.0),
) -> dict[str, str]:
    attrs = {
        key: junction.attrib[key]
        for key in ("id", "x", "y", "shape")
        if key in junction.attrib
    }
    attrs["type"] = "priority"
    if source_offset != target_offset:
        if "x" in attrs:
            attrs["x"] = _reproject_coord(attrs["x"], source_offset[0], target_offset[0])
        if "y" in attrs:
            attrs["y"] = _reproject_coord(attrs["y"], source_offset[1], target_offset[1])
        if "shape" in attrs:
            attrs["shape"] = _reproject_shape(attrs["shape"], source_offset, target_offset)
    return attrs


def _common_edge_geometry_mismatches(
    teacher: dict[str, Any],
    candidate: dict[str, Any],
    common_edge_ids: set[str],
    geometry_tolerance: float,
) -> list[dict[str, Any]]:
    teacher_edges = {edge["id"]: edge for edge in teacher.get("edges", []) if isinstance(edge, dict) and edge.get("id")}
    candidate_edges = {edge["id"]: edge for edge in candidate.get("edges", []) if isinstance(edge, dict) and edge.get("id")}
    teacher_offset = _net_offset(teacher)
    candidate_offset = _net_offset(candidate)
    mismatches = []
    for edge_id in sorted(common_edge_ids):
        teacher_edge = teacher_edges[edge_id]
        candidate_edge = candidate_edges[edge_id]
        endpoint_delta = _edge_endpoint_delta(teacher_edge, teacher_offset, candidate_edge, candidate_offset)
        teacher_lane_count = len(teacher_edge.get("lanes", []))
        candidate_lane_count = len(candidate_edge.get("lanes", []))
        if endpoint_delta > geometry_tolerance or teacher_lane_count != candidate_lane_count:
            mismatches.append(
                {
                    "edge_id": edge_id,
                    "endpoint_delta": round(endpoint_delta, 6),
                    "teacher_from": str(teacher_edge.get("from", "")),
                    "candidate_from": str(candidate_edge.get("from", "")),
                    "teacher_to": str(teacher_edge.get("to", "")),
                    "candidate_to": str(candidate_edge.get("to", "")),
                    "teacher_type": str(teacher_edge.get("type", "")),
                    "candidate_type": str(candidate_edge.get("type", "")),
                    "teacher_lane_count": teacher_lane_count,
                    "candidate_lane_count": candidate_lane_count,
                    "teacher_lane_signature": _lane_signature(teacher_edge),
                    "candidate_lane_signature": _lane_signature(candidate_edge),
                }
            )
    return mismatches


def _net_offset(bundle: dict[str, Any]) -> tuple[float, float]:
    raw = str(bundle.get("location", {}).get("netOffset", "0,0"))
    parts = raw.split(",")
    if len(parts) < 2:
        return (0.0, 0.0)
    return (float(parts[0]), float(parts[1]))


def _edge_endpoint_delta(
    teacher_edge: dict[str, Any],
    teacher_offset: tuple[float, float],
    candidate_edge: dict[str, Any],
    candidate_offset: tuple[float, float],
) -> float:
    teacher_points = _lane_world_points(teacher_edge, teacher_offset)
    candidate_points = _lane_world_points(candidate_edge, candidate_offset)
    if len(teacher_points) < 2 or len(candidate_points) < 2:
        return 0.0 if teacher_points == candidate_points else math.inf
    same_direction = _point_distance(teacher_points[0], candidate_points[0]) + _point_distance(
        teacher_points[-1],
        candidate_points[-1],
    )
    reverse_direction = _point_distance(teacher_points[0], candidate_points[-1]) + _point_distance(
        teacher_points[-1],
        candidate_points[0],
    )
    return min(same_direction, reverse_direction)


def _lane_world_points(edge: dict[str, Any], offset: tuple[float, float]) -> list[tuple[float, float]]:
    lanes = edge.get("lanes", [])
    if not lanes:
        return []
    shape = str(lanes[0].get("shape", ""))
    points = []
    for part in shape.split():
        coords = part.split(",")
        if len(coords) < 2:
            continue
        points.append((float(coords[0]) - offset[0], float(coords[1]) - offset[1]))
    return points


def _lane_signature(edge: dict[str, Any]) -> list[str]:
    return [
        f"index={lane.get('index', '')}|allow={lane.get('allow', '')}|disallow={lane.get('disallow', '')}"
        for lane in edge.get("lanes", [])
        if isinstance(lane, dict)
    ]


def _lane_semantic_signature(edge: dict[str, Any]) -> list[str]:
    """Return stable effective mode roles for a lane model.

    SUMO accepts both explicit ``allow`` lists and complement-style
    ``disallow`` lists.  Comparing those XML strings directly reports false
    differences (especially after netconvert adds version-specific classes).
    The semantic signature keeps lane order and the effective operational
    roles, while the raw signature remains available for diagnostics and
    repair proposals.
    """

    edge_type = str(edge.get("type", "")).lower()
    signatures = []
    for lane in edge.get("lanes", []):
        if not isinstance(lane, dict):
            continue
        allow = set(str(lane.get("allow", "")).split())
        disallow = set(str(lane.get("disallow", "")).split())
        if allow:
            effective = allow
        elif disallow:
            # Only the stable role aliases are needed here.  Unknown SUMO
            # classes are intentionally retained in raw XML diagnostics.
            effective = {
                token
                for _, aliases in ROAD_SEMANTIC_MODE_GROUPS
                for token in aliases
                if token not in disallow
            }
        else:
            base_type = edge_type.split(".")[-1].split("|")[-1]
            if base_type in {"footway", "pedestrian", "steps"}:
                effective = {"pedestrian"}
            elif base_type == "cycleway":
                effective = {"bicycle"}
            elif base_type == "path":
                effective = {"bicycle", "pedestrian"}
            elif edge_type.startswith("railway."):
                effective = {"rail"}
            else:
                effective = {"passenger", "bus", "bicycle", "motorcycle"}

        roles = []
        for role, aliases in ROAD_SEMANTIC_MODE_GROUPS:
            if effective.intersection(aliases):
                roles.append(role)
        signatures.append(
            f"index={lane.get('index', '')}|roles={','.join(roles)}"
        )
    return signatures


def _point_distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _sorted_attrs(element: ET.Element | None) -> dict[str, str]:
    return {} if element is None else dict(sorted(element.attrib.items()))


def _record_attrs(record: dict[str, Any], child_key: str) -> dict[str, str]:
    return {str(key): str(value) for key, value in record.items() if key != child_key}


def _missing_reference_count(bundle: dict[str, Any]) -> int:
    edge_ids = {edge["id"] for edge in bundle["edges"]}
    lane_ids = {lane["id"] for edge in bundle["edges"] for lane in edge.get("lanes", [])}
    junction_ids = {junction["id"] for junction in bundle["junctions"]}
    missing = 0
    for edge in bundle["edges"]:
        missing += int(edge.get("from", "") not in junction_ids)
        missing += int(edge.get("to", "") not in junction_ids)
    for junction in bundle["junctions"]:
        missing += sum(1 for lane_id in str(junction.get("incLanes", "")).split() if lane_id not in lane_ids)
        missing += sum(1 for lane_id in str(junction.get("intLanes", "")).split() if lane_id not in lane_ids)
    for connection in bundle["connections"]:
        missing += int(connection.get("from", "") not in edge_ids)
        missing += int(connection.get("to", "") not in edge_ids)
    return missing


def _lane_sort_key(lane: ET.Element) -> tuple[int, str]:
    try:
        index = int(lane.attrib.get("index", "0"))
    except ValueError:
        index = 0
    return (index, lane.attrib.get("id", ""))


def _canonical_connection_sort_key(connection: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        connection.get("from", ""),
        connection.get("fromLane", ""),
        connection.get("to", ""),
        connection.get("toLane", ""),
    )
