from __future__ import annotations

import gzip
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROAD_MODE_GROUPS: tuple[tuple[str, frozenset[str]], ...] = (
    ("passenger", frozenset({"passenger", "private", "taxi", "delivery", "truck", "trailer"})),
    ("bus", frozenset({"bus", "coach"})),
    ("bicycle", frozenset({"bicycle"})),
    ("pedestrian", frozenset({"pedestrian", "wheelchair"})),
    ("motorcycle", frozenset({"motorcycle", "moped", "scooter"})),
    ("rail", frozenset({"tram", "rail_urban", "rail", "rail_electric", "rail_fast"})),
)


def audit_reference_road_alignment(
    teacher_net_file: Path,
    candidate_net_file: Path,
    *,
    source_osm_file: Path | None = None,
    output_dir: Path | None = None,
    prefix: str = "reference_road_alignment",
    max_examples: int = 20,
    max_review_locations: int = 200,
) -> dict[str, Any]:
    """Compare an OSM-derived road network with a manual reference by source way.

    SUMO edge IDs are not a stable comparison key: netconvert may split one OSM
    way into several ``#`` segments and may add a reverse direction.  This
    audit therefore compares numeric source-way roots, direction, effective
    lane roles, and external-to-external movement signatures.  Teacher edges
    without a source way are retained as explicit review items instead of
    being silently treated as OSM omissions.

    The function is diagnostic and non-destructive.  Its additional XML is a
    marker layer for NetEdit; it never changes the candidate network.
    """

    try:
        teacher_root, teacher_edges = _read_external_edges(teacher_net_file)
        candidate_root, candidate_edges = _read_external_edges(candidate_net_file)
        source_way_ids = _read_source_way_ids(source_osm_file) if source_osm_file else None
    except (OSError, ET.ParseError, ValueError, TypeError) as exc:
        report = {
            "status": "blocked",
            "claim_status": "reference-audit",
            "comparison_basis": "source_way_semantic_alignment",
            "teacher_net_file": str(teacher_net_file),
            "candidate_net_file": str(candidate_net_file),
            "source_osm_file": str(source_osm_file) if source_osm_file else "",
            "blocking_reason": f"reference_road_alignment_failed:{type(exc).__name__}",
            "error": str(exc),
            "review_locations": [],
            "warnings": [],
        }
        return _write_report(report, output_dir=output_dir, prefix=prefix)

    teacher_groups = _group_edges(teacher_edges)
    candidate_groups = _group_edges(candidate_edges)
    teacher_source_ids = {key for key in teacher_groups if _is_numeric_source_key(key)}
    candidate_source_ids = {key for key in candidate_groups if _is_numeric_source_key(key)}
    common_source_ids = teacher_source_ids & candidate_source_ids
    teacher_only_source_ids = teacher_source_ids - candidate_source_ids
    candidate_only_source_ids = candidate_source_ids - teacher_source_ids
    teacher_synthetic_keys = sorted(key for key in teacher_groups if not _is_numeric_source_key(key))

    source_coverage = _source_coverage_report(
        source_way_ids,
        teacher_source_ids=teacher_source_ids,
        candidate_source_ids=candidate_source_ids,
        teacher_only_source_ids=teacher_only_source_ids,
        candidate_only_source_ids=candidate_only_source_ids,
    )

    way_rows: list[dict[str, Any]] = []
    direction_mismatch_count = 0
    lane_profile_mismatch_count = 0
    for source_id in sorted(common_source_ids):
        teacher_items = teacher_groups[source_id]
        candidate_items = candidate_groups[source_id]
        teacher_directions = sorted({_edge_direction(edge_id) for edge_id, _ in teacher_items})
        candidate_directions = sorted({_edge_direction(edge_id) for edge_id, _ in candidate_items})
        teacher_profiles = sorted(
            {
                _edge_lane_role_signature(edge)
                for edge_id, edge in teacher_items
            }
        )
        candidate_profiles = sorted(
            {
                _edge_lane_role_signature(edge)
                for edge_id, edge in candidate_items
            }
        )
        direction_mismatch = teacher_directions != candidate_directions
        lane_profile_mismatch = teacher_profiles != candidate_profiles
        if direction_mismatch:
            direction_mismatch_count += 1
        if lane_profile_mismatch:
            lane_profile_mismatch_count += 1
        if direction_mismatch or lane_profile_mismatch:
            candidate_edge_id = sorted(edge_id for edge_id, _ in candidate_items)[0]
            teacher_edge_id = sorted(edge_id for edge_id, _ in teacher_items)[0]
            way_rows.append(
                {
                    "source_way_id": source_id,
                    "status": "needs_review",
                    "direction_mismatch": direction_mismatch,
                    "lane_profile_mismatch": lane_profile_mismatch,
                    "teacher_directions": teacher_directions,
                    "candidate_directions": candidate_directions,
                    "teacher_lane_role_profiles": [list(profile) for profile in teacher_profiles],
                    "candidate_lane_role_profiles": [list(profile) for profile in candidate_profiles],
                    "teacher_segment_count": len(teacher_items),
                    "candidate_segment_count": len(candidate_items),
                    "teacher_example_edge_ids": sorted(edge_id for edge_id, _ in teacher_items)[:max_examples],
                    "candidate_example_edge_ids": sorted(edge_id for edge_id, _ in candidate_items)[:max_examples],
                    "candidate_edge_id": candidate_edge_id,
                    "teacher_edge_id": teacher_edge_id,
                }
            )

    teacher_movement_counts, teacher_manual_movement_count = _external_movement_signatures(
        teacher_root,
        teacher_edges,
        candidate_source_ids,
    )
    candidate_movement_counts, _ = _external_movement_signatures(
        candidate_root,
        candidate_edges,
        candidate_source_ids,
    )
    missing_movement_counts = teacher_movement_counts - candidate_movement_counts
    extra_movement_counts = candidate_movement_counts - teacher_movement_counts
    movement_missing_count = sum(missing_movement_counts.values())
    movement_extra_count = sum(extra_movement_counts.values())

    manual_source_rows = _manual_source_rows(
        teacher_groups,
        teacher_only_source_ids=teacher_only_source_ids,
        source_way_ids=source_way_ids,
        max_examples=max_examples,
    )
    review_locations = _build_review_locations(
        teacher_groups=teacher_groups,
        candidate_groups=candidate_groups,
        teacher_root=teacher_root,
        candidate_root=candidate_root,
        way_rows=way_rows,
        manual_source_rows=manual_source_rows,
        missing_movement_counts=missing_movement_counts,
        max_review_locations=max_review_locations,
    )

    source_gap_count = len(manual_source_rows)
    unresolved_gap_count = (
        source_gap_count
        + len(way_rows)
        + movement_missing_count
        + movement_extra_count
        + len(candidate_only_source_ids)
    )
    status = "pass" if unresolved_gap_count == 0 else "needs_review"
    report: dict[str, Any] = {
        "status": status,
        "claim_status": "reference-audit",
        "comparison_basis": "source_way_direction_lane_role_and_external_movement",
        "teacher_net_file": str(teacher_net_file),
        "candidate_net_file": str(candidate_net_file),
        "source_osm_file": str(source_osm_file) if source_osm_file else "",
        "net_offsets": {
            "teacher": list(_net_offset(teacher_root)),
            "candidate": list(_net_offset(candidate_root)),
        },
        "source_coverage": source_coverage,
        "source_way_counts": {
            "teacher_numeric_source_way_count": len(teacher_source_ids),
            "candidate_numeric_source_way_count": len(candidate_source_ids),
            "common_source_way_count": len(common_source_ids),
            "teacher_only_source_way_count": len(teacher_only_source_ids),
            "candidate_only_source_way_count": len(candidate_only_source_ids),
            "teacher_synthetic_or_manual_group_count": len(teacher_synthetic_keys),
        },
        "way_semantics": {
            "direction_mismatch_count": direction_mismatch_count,
            "lane_profile_mismatch_count": lane_profile_mismatch_count,
            "mismatch_count": len(way_rows),
            "rows": way_rows,
        },
        "external_movement_semantics": {
            "teacher_external_movement_count": sum(teacher_movement_counts.values()) + teacher_manual_movement_count,
            "candidate_external_movement_count": sum(candidate_movement_counts.values()),
            "teacher_manual_or_unmapped_movement_count": teacher_manual_movement_count,
            "teacher_common_source_movement_count": sum(teacher_movement_counts.values()),
            "candidate_common_source_movement_count": sum(candidate_movement_counts.values()),
            "missing_movement_count": movement_missing_count,
            "extra_movement_count": movement_extra_count,
            "missing_signature_count": len(missing_movement_counts),
            "extra_signature_count": len(extra_movement_counts),
            "missing_examples": _movement_examples(missing_movement_counts, max_examples),
            "extra_examples": _movement_examples(extra_movement_counts, max_examples),
        },
        "manual_reference_source_rows": manual_source_rows,
        "review_locations": review_locations,
        "review_location_count": len(review_locations),
        "unresolved_gap_count": unresolved_gap_count,
        "warnings": (
            [
                "Manual/reference-only road objects are reported as review overlays; they are not silently injected into the OSM network.",
            ]
            if manual_source_rows
            else []
        ),
    }
    return _write_report(report, output_dir=output_dir, prefix=prefix)


def _read_external_edges(net_file: Path) -> tuple[ET.Element, dict[str, ET.Element]]:
    root = ET.parse(net_file).getroot()
    edges = {
        edge.attrib["id"]: edge
        for edge in root.findall("edge")
        if edge.attrib.get("id")
        and not edge.attrib["id"].startswith(":")
        and edge.attrib.get("function") != "internal"
    }
    return root, edges


def _read_source_way_ids(source_osm_file: Path) -> set[str]:
    opener = gzip.open if source_osm_file.suffix.lower() == ".gz" else open
    with opener(source_osm_file, "rt", encoding="utf-8") as handle:
        root = ET.parse(handle).getroot()
    return {way.attrib["id"] for way in root.findall("way") if way.attrib.get("id")}


def _group_edges(edges: dict[str, ET.Element]) -> dict[str, list[tuple[str, ET.Element]]]:
    groups: dict[str, list[tuple[str, ET.Element]]] = defaultdict(list)
    for edge_id, edge in edges.items():
        groups[_source_group_key(edge_id)].append((edge_id, edge))
    for items in groups.values():
        items.sort(key=lambda item: item[0])
    return dict(groups)


def _source_group_key(edge_id: str) -> str:
    raw = edge_id.split("#", 1)[0]
    unsigned = raw[1:] if raw.startswith("-") else raw
    if unsigned.isdigit():
        return unsigned
    return f"synthetic:{raw}"


def _is_numeric_source_key(value: str) -> bool:
    return value.isdigit()


def _edge_direction(edge_id: str) -> str:
    return "reverse" if edge_id.startswith("-") else "forward"


def _edge_lane_role_signature(edge: ET.Element) -> tuple[tuple[str, ...], ...]:
    lanes = sorted(edge.findall("lane"), key=_lane_sort_key)
    return tuple(_lane_roles(lane, edge.attrib.get("type", "")) for lane in lanes)


def _lane_sort_key(lane: ET.Element) -> tuple[int, str]:
    raw = lane.attrib.get("index", "")
    try:
        return int(raw), lane.attrib.get("id", "")
    except ValueError:
        return 10**9, lane.attrib.get("id", "")


def _lane_roles(lane: ET.Element, edge_type: str) -> tuple[str, ...]:
    allow = set(lane.attrib.get("allow", "").split())
    disallow = set(lane.attrib.get("disallow", "").split())
    if allow:
        effective = allow
    elif disallow:
        effective = {
            token
            for _, aliases in ROAD_MODE_GROUPS
            for token in aliases
            if token not in disallow
        }
    else:
        base_type = edge_type.lower().split(".")[-1].split("|")[-1]
        if base_type in {"footway", "pedestrian", "steps"}:
            effective = {"pedestrian"}
        elif base_type == "cycleway":
            effective = {"bicycle"}
        elif base_type == "path":
            effective = {"bicycle", "pedestrian"}
        elif edge_type.lower().startswith("railway."):
            effective = {"rail"}
        else:
            effective = {"passenger", "bus", "bicycle", "motorcycle"}
    return tuple(role for role, aliases in ROAD_MODE_GROUPS if effective & aliases)


def _source_coverage_report(
    source_way_ids: set[str] | None,
    *,
    teacher_source_ids: set[str],
    candidate_source_ids: set[str],
    teacher_only_source_ids: set[str],
    candidate_only_source_ids: set[str],
) -> dict[str, Any]:
    if source_way_ids is None:
        return {
            "status": "not_checked",
            "source_way_count": None,
            "teacher_source_way_present_in_osm_count": None,
            "teacher_source_way_absent_from_osm_count": None,
            "candidate_source_way_present_in_osm_count": None,
            "teacher_only_source_way_ids": sorted(teacher_only_source_ids),
            "candidate_only_source_way_ids": sorted(candidate_only_source_ids),
        }
    teacher_absent = sorted(teacher_source_ids - source_way_ids)
    candidate_absent = sorted(candidate_source_ids - source_way_ids)
    status = "pass" if not candidate_absent else "needs_review"
    return {
        "status": status,
        "source_way_count": len(source_way_ids),
        "teacher_source_way_present_in_osm_count": len(teacher_source_ids & source_way_ids),
        "teacher_source_way_absent_from_osm_count": len(teacher_absent),
        "teacher_source_way_absent_from_osm_ids": teacher_absent,
        "candidate_source_way_present_in_osm_count": len(candidate_source_ids & source_way_ids),
        "candidate_source_way_absent_from_osm_count": len(candidate_absent),
        "candidate_source_way_absent_from_osm_ids": candidate_absent,
        "teacher_only_source_way_ids": sorted(teacher_only_source_ids),
        "candidate_only_source_way_ids": sorted(candidate_only_source_ids),
    }


def _manual_source_rows(
    teacher_groups: dict[str, list[tuple[str, ET.Element]]],
    *,
    teacher_only_source_ids: set[str],
    source_way_ids: set[str] | None,
    max_examples: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group_key in sorted(teacher_groups):
        is_numeric = _is_numeric_source_key(group_key)
        is_teacher_only = is_numeric and group_key in teacher_only_source_ids
        if is_numeric and not is_teacher_only:
            continue
        items = teacher_groups[group_key]
        source_present = bool(source_way_ids is not None and group_key in source_way_ids)
        if is_numeric:
            category = "teacher_source_way_not_in_candidate"
            if source_way_ids is not None and not source_present:
                category = "manual_reference_source_gap"
            source_way_id = group_key
        else:
            category = "manual_reference_synthetic_edge"
            source_way_id = ""
        rows.append(
            {
                "status": "needs_review",
                "category": category,
                "source_way_id": source_way_id,
                "teacher_edge_count": len(items),
                "teacher_edge_ids": sorted(edge_id for edge_id, _ in items)[:max_examples],
                "source_way_present_in_osm": source_present if source_way_ids is not None else None,
                "teacher_example_edge_id": sorted(edge_id for edge_id, _ in items)[0],
            }
        )
    return rows


def _external_movement_signatures(
    root: ET.Element,
    edges: dict[str, ET.Element],
    comparable_source_ids: set[str],
) -> tuple[Counter[tuple[Any, ...]], int]:
    counts: Counter[tuple[Any, ...]] = Counter()
    manual_or_unmapped = 0
    for connection in root.findall("connection"):
        from_id = connection.attrib.get("from", "")
        to_id = connection.attrib.get("to", "")
        if from_id not in edges or to_id not in edges:
            continue
        from_source = _source_group_key(from_id)
        to_source = _source_group_key(to_id)
        if (
            not _is_numeric_source_key(from_source)
            or not _is_numeric_source_key(to_source)
            or from_source not in comparable_source_ids
            or to_source not in comparable_source_ids
        ):
            manual_or_unmapped += 1
            continue
        from_edge = edges[from_id]
        to_edge = edges[to_id]
        key = (
            from_source,
            _edge_direction(from_id),
            _lane_roles_for_index(from_edge, connection.attrib.get("fromLane", "")),
            to_source,
            _edge_direction(to_id),
            _lane_roles_for_index(to_edge, connection.attrib.get("toLane", "")),
            connection.attrib.get("dir", ""),
            bool(connection.attrib.get("tl", "")),
        )
        counts[key] += 1
    return counts, manual_or_unmapped


def _lane_roles_for_index(edge: ET.Element, raw_index: str) -> tuple[str, ...]:
    try:
        index = int(raw_index)
    except ValueError:
        return ("missing",)
    for lane in edge.findall("lane"):
        try:
            if int(lane.attrib.get("index", "")) == index:
                return _lane_roles(lane, edge.attrib.get("type", ""))
        except ValueError:
            continue
    return ("missing",)


def _movement_examples(counts: Counter[tuple[Any, ...]], max_examples: int) -> list[dict[str, Any]]:
    rows = []
    for key, count in counts.most_common(max_examples):
        rows.append(
            {
                "count": int(count),
                "from_source_way_id": key[0],
                "from_direction": key[1],
                "from_lane_roles": list(key[2]),
                "to_source_way_id": key[3],
                "to_direction": key[4],
                "to_lane_roles": list(key[5]),
                "dir": key[6],
                "tls_controlled": key[7],
            }
        )
    return rows


def _build_review_locations(
    *,
    teacher_groups: dict[str, list[tuple[str, ET.Element]]],
    candidate_groups: dict[str, list[tuple[str, ET.Element]]],
    teacher_root: ET.Element,
    candidate_root: ET.Element,
    way_rows: list[dict[str, Any]],
    manual_source_rows: list[dict[str, Any]],
    missing_movement_counts: Counter[tuple[Any, ...]],
    max_review_locations: int,
) -> list[dict[str, Any]]:
    locations: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(location: dict[str, Any]) -> None:
        location_id = str(location.get("location_id", ""))
        if not location_id or location_id in seen or len(locations) >= max_review_locations:
            return
        seen.add(location_id)
        locations.append(location)

    for row in manual_source_rows:
        group_key = row.get("source_way_id") or row.get("teacher_example_edge_id", "")
        edge_id = str(row.get("teacher_example_edge_id", ""))
        point = _edge_point_in_candidate_space(
            teacher_groups.get(str(row.get("source_way_id", "")), [(edge_id, _find_edge(teacher_root, edge_id))])[0][1]
            if edge_id and _find_edge(teacher_root, edge_id) is not None
            else None,
            teacher_root,
            candidate_root,
        )
        if point is None:
            continue
        add(
            {
                "location_id": f"road_source_gap_{_safe_id(str(group_key))}",
                "category": row.get("category", "manual_reference_source_gap"),
                "color_group": "red",
                "reason": (
                    f"teacher/reference road object {group_key} is not represented by the current OSM-derived candidate; "
                    "inspect the source version or retain as an explicit manual overlay"
                ),
                "source_way_id": row.get("source_way_id", ""),
                "edge_id": edge_id,
                "x": point[0],
                "y": point[1],
                "radius_m": 10.0,
            }
        )

    for row in sorted(
        way_rows,
        key=lambda item: (
            not bool(item.get("lane_profile_mismatch")),
            not bool(item.get("direction_mismatch")),
            str(item.get("source_way_id", "")),
        ),
    ):
        source_id = str(row.get("source_way_id", ""))
        candidate_edge_id = str(row.get("candidate_edge_id", ""))
        candidate_edge = _find_edge(candidate_root, candidate_edge_id)
        point = _edge_local_midpoint(candidate_edge) if candidate_edge is not None else None
        if point is None:
            continue
        reasons = []
        if row.get("direction_mismatch"):
            reasons.append("direction")
        if row.get("lane_profile_mismatch"):
            reasons.append("lane roles")
        add(
            {
                "location_id": f"road_way_semantics_{_safe_id(source_id)}",
                "category": "road_way_semantics",
                "color_group": "amber",
                "reason": f"source way {source_id} differs in {', '.join(reasons)} from the manual reference",
                "source_way_id": source_id,
                "edge_id": candidate_edge_id,
                "x": point[0],
                "y": point[1],
                "radius_m": 8.0,
            }
        )

    for key, count in missing_movement_counts.most_common(max_review_locations):
        source_id = str(key[0])
        candidate_items = candidate_groups.get(source_id, [])
        candidate_edge = candidate_items[0][1] if candidate_items else None
        point = _edge_local_midpoint(candidate_edge) if candidate_edge is not None else None
        if point is None:
            continue
        add(
            {
                "location_id": f"road_movement_gap_{_safe_id(source_id)}_{_safe_id(str(key[3]))}",
                "category": "road_movement_semantics",
                "color_group": "red",
                "reason": (
                    f"source-way movement {source_id} -> {key[3]} is missing or not semantically aligned "
                    f"({int(count)} connection(s)); inspect lane mapping and junction topology"
                ),
                "source_way_id": source_id,
                "to_source_way_id": str(key[3]),
                "edge_id": candidate_items[0][0] if candidate_items else "",
                "x": point[0],
                "y": point[1],
                "radius_m": 10.0,
                "missing_connection_count": int(count),
            }
        )
    return locations


def _find_edge(root: ET.Element, edge_id: str) -> ET.Element | None:
    if not edge_id:
        return None
    for edge in root.findall("edge"):
        if edge.attrib.get("id") == edge_id:
            return edge
    return None


def _edge_local_midpoint(edge: ET.Element | None) -> tuple[float, float] | None:
    if edge is None:
        return None
    lane = next(iter(edge.findall("lane")), None)
    if lane is None:
        return None
    points = _shape_points(lane.attrib.get("shape", ""))
    if len(points) < 2:
        return None
    return ((points[0][0] + points[-1][0]) / 2.0, (points[0][1] + points[-1][1]) / 2.0)


def _edge_point_in_candidate_space(
    edge: ET.Element | None,
    teacher_root: ET.Element,
    candidate_root: ET.Element,
) -> tuple[float, float] | None:
    point = _edge_local_midpoint(edge)
    if point is None:
        return None
    teacher_offset = _net_offset(teacher_root)
    candidate_offset = _net_offset(candidate_root)
    global_point = (point[0] - teacher_offset[0], point[1] - teacher_offset[1])
    return (global_point[0] + candidate_offset[0], global_point[1] + candidate_offset[1])


def _shape_points(value: str) -> list[tuple[float, float]]:
    points = []
    for token in value.split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return points


def _net_offset(root: ET.Element) -> tuple[float, float]:
    location = root.find("location")
    raw = location.attrib.get("netOffset", "") if location is not None else ""
    match = re.match(r"\(?\s*([-+0-9.eE]+)[, ]+([-+0-9.eE]+)", raw)
    if not match:
        return 0.0, 0.0
    return float(match.group(1)), float(match.group(2))


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return safe or "unknown"


def _write_report(
    report: dict[str, Any],
    *,
    output_dir: Path | None,
    prefix: str,
) -> dict[str, Any]:
    if output_dir is None:
        return report
    output_dir.mkdir(parents=True, exist_ok=True)
    report_file = output_dir / f"{prefix}.json"
    additional_file = output_dir / f"{prefix}.add.xml"
    report["report_file"] = str(report_file)
    report["additional_file"] = str(additional_file)
    _write_additional_overlay(additional_file, report.get("review_locations", []))
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _write_additional_overlay(path: Path, locations: list[dict[str, Any]]) -> None:
    root = ET.Element(
        "additional",
        {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": "http://sumo.dlr.de/xsd/additional_file.xsd",
        },
    )
    colors = {
        "red": "1,0,0,0.85",
        "amber": "1,0.65,0,0.8",
        "green": "0,0.8,0,0.8",
    }
    for location in locations:
        try:
            x = float(location["x"])
            y = float(location["y"])
            radius = max(2.0, float(location.get("radius_m", 8.0)))
        except (KeyError, TypeError, ValueError):
            continue
        color = colors.get(str(location.get("color_group", "amber")), colors["amber"])
        shape = " ".join(
            (
                f"{x - radius},{y - radius}",
                f"{x + radius},{y - radius}",
                f"{x + radius},{y + radius}",
                f"{x - radius},{y + radius}",
                f"{x - radius},{y - radius}",
            )
        )
        ET.SubElement(
            root,
            "poly",
            {
                "id": f"torii_{_safe_id(str(location.get('location_id', 'review')))}",
                "color": color,
                "layer": "100",
                "shape": shape,
                "name": str(location.get("reason", "reference road review")),
            },
        )
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
