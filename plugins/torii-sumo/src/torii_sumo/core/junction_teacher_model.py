from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
import subprocess
from typing import Any, Iterable
import xml.etree.ElementTree as ET


def extract_teacher_junction_model(net_file: Path, junction_id: str) -> dict[str, Any]:
    root = ET.parse(net_file).getroot()
    return _extract_teacher_junction_model(root, net_file, junction_id)


def canonical_teacher_junction_bundle(net_file: Path, junction_id: str) -> dict[str, Any]:
    root = ET.parse(net_file).getroot()
    model = _extract_teacher_junction_model(root, net_file, junction_id)
    edges = {edge.attrib["id"]: edge for edge in root.findall("edge") if edge.attrib.get("id")}
    junctions = {
        junction.attrib["id"]: junction
        for junction in root.findall("junction")
        if junction.attrib.get("id")
    }
    internal_prefix = f":{junction_id}_"
    selected_edge_ids = {
        str(edge.get("edge_id", ""))
        for group in (
            model["approaches"]["incoming"],
            model["approaches"]["outgoing"],
            model["internal_edges"],
            model["crossings"],
            model["walking_areas"],
        )
        for edge in group
        if edge.get("edge_id")
    }
    incoming_edge_ids = {str(edge.get("edge_id", "")) for edge in model["approaches"]["incoming"]}
    outgoing_edge_ids = {str(edge.get("edge_id", "")) for edge in model["approaches"]["outgoing"]}

    connections = []
    for connection in root.findall("connection"):
        if _is_target_local_connection(
            connection,
            incoming_edge_ids=incoming_edge_ids,
            outgoing_edge_ids=outgoing_edge_ids,
            internal_prefix=internal_prefix,
        ):
            connections.append(_sorted_attrs(connection))
            selected_edge_ids.update(
                edge_id
                for edge_id in (connection.attrib.get("from", ""), connection.attrib.get("to", ""))
                if edge_id in edges
            )

    selected_junction_ids = {junction_id}
    selected_junction_ids.update(
        junction_id
        for junction_id in junctions
        if junction_id.startswith(internal_prefix)
    )
    for edge_id in selected_edge_ids:
        edge = edges.get(edge_id)
        if edge is None or edge_id.startswith(":"):
            continue
        selected_junction_ids.update(value for value in (edge.attrib.get("from"), edge.attrib.get("to")) if value)
    boundary_inc_lanes: dict[str, list[str]] = {}
    for edge_id in selected_edge_ids:
        edge = edges.get(edge_id)
        if edge is None or edge_id.startswith(":"):
            continue
        target = edge.attrib.get("to", "")
        if target and target != junction_id and not target.startswith(internal_prefix):
            boundary_inc_lanes.setdefault(target, []).extend(
                lane.attrib.get("id", "") for lane in edge.findall("lane") if lane.attrib.get("id")
            )

    tl_ids = {
        str(model.get("traffic_light", {}).get("attributes", {}).get("id", ""))
    } - {""}

    return {
        "junction_id": junction_id,
        "net": _sorted_attrs(root),
        "location": _sorted_attrs(root.find("location")),
        "junctions": [
            _canonical_selected_junction_record(
                junctions[selected_junction_id],
                full=selected_junction_id == junction_id or selected_junction_id.startswith(internal_prefix),
                boundary_inc_lanes=boundary_inc_lanes.get(selected_junction_id, []),
            )
            for selected_junction_id in sorted(selected_junction_ids)
            if selected_junction_id in junctions
        ],
        "edges": [
            _canonical_edge_record(edges[edge_id])
            for edge_id in sorted(selected_edge_ids)
            if edge_id in edges
        ],
        "connections": sorted(connections, key=_canonical_connection_sort_key),
        "tlLogics": [
            _canonical_tl_logic_record(tl_logic)
            for tl_logic in sorted(root.findall("tlLogic"), key=lambda item: item.attrib.get("id", ""))
            if tl_logic.attrib.get("id", "") in tl_ids
        ],
        "summary": {
            "junction_count": len(selected_junction_ids & set(junctions)),
            "edge_count": len(selected_edge_ids & set(edges)),
            "connection_count": len(connections),
            "tl_logic_count": len(tl_ids),
        },
    }


def write_teacher_self_replay_net(
    teacher_net_file: Path,
    junction_id: str,
    output_file: Path,
) -> dict[str, Any]:
    bundle = canonical_teacher_junction_bundle(teacher_net_file, junction_id)
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
    for tl_logic in bundle["tlLogics"]:
        tl_node = ET.SubElement(root, "tlLogic", _record_attrs(tl_logic, "phases"))
        for phase in tl_logic.get("phases", []):
            ET.SubElement(tl_node, "phase", dict(phase))
    for connection in bundle["connections"]:
        ET.SubElement(root, "connection", dict(connection))

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(output_file, encoding="utf-8", xml_declaration=True)
    parity_delta = {} if bundle == canonical_teacher_junction_bundle(output_file, junction_id) else {"canonical_bundle": 1}
    return {
        "status": "pass" if not parity_delta else "fail",
        "output_file": str(output_file),
        "parity_delta": parity_delta,
    }


def build_teacher_self_replay_corpus_report(
    teacher_net_file: Path,
    junction_ids: list[str],
    output_dir: Path,
    *,
    run_sumo: bool = True,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = []
    for junction_id in junction_ids:
        case_name = _safe_file_stem(junction_id)
        replay_net_file = output_dir / f"{case_name}_teacher_self_replay.net.xml"
        bundle_file = output_dir / f"{case_name}_canonical_bundle.json"
        bundle = canonical_teacher_junction_bundle(teacher_net_file, junction_id)
        bundle_file.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
        replay_report = write_teacher_self_replay_net(teacher_net_file, junction_id, replay_net_file)
        sumo_report = _sumo_load_report(replay_net_file) if run_sumo else {
            "sumo_returncode": None,
            "sumo_stdout": "",
            "sumo_stderr": "",
        }
        case_status = "pass" if replay_report["status"] == "pass" and (
            not run_sumo or sumo_report["sumo_returncode"] == 0
        ) else "fail"
        cases.append(
            {
                "junction_id": junction_id,
                "status": case_status,
                "replay_net_file": str(replay_net_file),
                "canonical_bundle_file": str(bundle_file),
                "parity_delta": replay_report["parity_delta"],
                **sumo_report,
            }
        )

    report = {
        "status": "pass" if all(case["status"] == "pass" for case in cases) else "fail",
        "teacher_net_file": str(teacher_net_file),
        "output_dir": str(output_dir),
        "case_count": len(cases),
        "cases": cases,
    }
    (output_dir / "teacher_self_replay_corpus.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def _extract_teacher_junction_model(root: ET.Element, net_file: Path, junction_id: str) -> dict[str, Any]:
    junction = next((node for node in root.findall("junction") if node.attrib.get("id") == junction_id), None)
    if junction is None:
        raise ValueError(f"junction not found: {junction_id}")

    edges = {edge.attrib["id"]: edge for edge in root.findall("edge") if edge.attrib.get("id")}
    lane_to_edge: dict[str, tuple[str, ET.Element, ET.Element]] = {}
    for edge_id, edge in edges.items():
        for lane in edge.findall("lane"):
            lane_id = lane.attrib.get("id")
            if lane_id:
                lane_to_edge[lane_id] = (edge_id, edge, lane)

    internal_prefix = f":{junction_id}_"
    incoming_edges = sorted(
        {
            edge_id
            for lane_id in _split(junction.attrib.get("incLanes", ""))
            if (lane_entry := lane_to_edge.get(lane_id))
            for edge_id, _edge, _lane in [lane_entry]
            if not edge_id.startswith(":")
            and _edge_allows_non_pedestrian(_edge)
        }
    )
    outgoing_edges = {
        edge_id
        for edge_id, edge in edges.items()
        if not edge_id.startswith(":")
        and edge.attrib.get("from") == junction_id
        and _edge_allows_non_pedestrian(edge)
    }
    for connection in root.findall("connection"):
        source = connection.attrib.get("from", "")
        target = connection.attrib.get("to", "")
        target_edge = edges.get(target)
        if source in incoming_edges and target_edge is not None and not target.startswith(":"):
            if _edge_allows_non_pedestrian(target_edge):
                outgoing_edges.add(target)

    crossings = [
        _internal_edge_record(edge, crossing=True)
        for edge in edges.values()
        if edge.attrib.get("id", "").startswith(internal_prefix) and edge.attrib.get("function") == "crossing"
    ]
    walking_areas = [
        _internal_edge_record(edge)
        for edge in edges.values()
        if edge.attrib.get("id", "").startswith(internal_prefix) and edge.attrib.get("function") == "walkingarea"
    ]
    internal_edges = [
        _internal_edge_record(edge)
        for edge in edges.values()
        if edge.attrib.get("id", "").startswith(internal_prefix)
        and edge.attrib.get("function") not in {"crossing", "walkingarea"}
    ]
    internal_junctions = [
        _junction_record(node)
        for node in root.findall("junction")
        if node.attrib.get("id", "").startswith(internal_prefix)
    ]
    outgoing_edges_sorted = sorted(outgoing_edges)

    vehicle_connections = []
    pedestrian_connections = []
    internal_connections = []
    for connection in root.findall("connection"):
        source = connection.attrib.get("from", "")
        target = connection.attrib.get("to", "")
        if source in incoming_edges and target in outgoing_edges_sorted:
            vehicle_connections.append(_connection_record(connection))
        elif _is_pedestrian_connection(connection, edges, internal_prefix):
            pedestrian_connections.append(_connection_record(connection))
        elif source.startswith(internal_prefix) or target.startswith(internal_prefix):
            internal_connections.append(_connection_record(connection))

    controlled_tl_ids = sorted(
        {
            connection["tl"]
            for connection in vehicle_connections + pedestrian_connections
            if connection.get("tl") and connection.get("linkIndex")
        }
    )
    tl_logic = next((tl for tl in root.findall("tlLogic") if tl.attrib.get("id") == junction_id), None)
    if tl_logic is None and controlled_tl_ids:
        tl_logic = next((tl for tl in root.findall("tlLogic") if tl.attrib.get("id") == controlled_tl_ids[0]), None)
    phases = [dict(phase.attrib) for phase in tl_logic.findall("phase")] if tl_logic is not None else []
    requests = [dict(request.attrib) for request in junction.findall("request")]

    return {
        "net_file": str(net_file),
        "junction_id": junction_id,
        "junction": dict(junction.attrib),
        "requests": requests,
        "approaches": {
            "incoming": [_edge_record(edges[edge_id]) for edge_id in incoming_edges],
            "outgoing": [_edge_record(edges[edge_id]) for edge_id in outgoing_edges_sorted],
        },
        "vehicle_connections": vehicle_connections,
        "internal_connections": internal_connections,
        "internal_edges": internal_edges,
        "internal_junctions": internal_junctions,
        "crossings": crossings,
        "walking_areas": walking_areas,
        "pedestrian_connections": pedestrian_connections,
        "traffic_light": {"attributes": dict(tl_logic.attrib) if tl_logic is not None else {}, "phases": phases},
        "summary": {
            "junction_type": junction.attrib.get("type", ""),
            "incoming_vehicle_edge_count": len(incoming_edges),
            "outgoing_vehicle_edge_count": len(outgoing_edges_sorted),
            "vehicle_connection_count": len(vehicle_connections),
            "internal_edge_count": len(internal_edges),
            "internal_junction_count": len(internal_junctions),
            "internal_connection_count": len(internal_connections),
            "pedestrian_connection_count": len(pedestrian_connections),
            "crossing_count": len(crossings),
            "walkingarea_count": len(walking_areas),
            "request_count": len(requests),
            "tl_phase_count": len(phases),
            "vehicle_connection_dirs": dict(Counter(record["dir"] or "blank" for record in vehicle_connections)),
            "internal_mode_counts": _internal_mode_counts(edges.values(), internal_prefix),
        },
    }


def extract_junction_pattern_index(
    net_file: Path,
    *,
    min_approaches: int = 3,
    max_approaches: int = 4,
    junction_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    root = ET.parse(net_file).getroot()
    allowed_junction_ids = set(junction_ids) if junction_ids is not None else None
    tl_by_id = {tl.attrib["id"]: tl for tl in root.findall("tlLogic") if tl.attrib.get("id")}
    lane_to_edge_id = {
        lane.attrib["id"]: edge.attrib["id"]
        for edge in root.findall("edge")
        if edge.attrib.get("id")
        for lane in edge.findall("lane")
        if lane.attrib.get("id")
    }
    records: list[dict[str, Any]] = []
    for junction in root.findall("junction"):
        junction_id = junction.attrib.get("id", "")
        if not junction_id or junction_id.startswith(":") or junction.attrib.get("type") == "internal":
            continue
        if allowed_junction_ids is not None and junction_id not in allowed_junction_ids:
            continue
        incoming_edge_ids = {
            edge_id
            for lane_id in _split(junction.attrib.get("incLanes", ""))
            if (edge_id := lane_to_edge_id.get(lane_id)) and not edge_id.startswith(":")
        }
        if len(incoming_edge_ids) < min_approaches:
            continue

        model = _extract_teacher_junction_model(root, net_file, junction_id)
        arm_count = _approach_arm_count(model)
        if arm_count < min_approaches or arm_count > max_approaches:
            continue

        vehicle_connections = model["vehicle_connections"]
        pedestrian_connections = model["pedestrian_connections"]
        all_connections = vehicle_connections + pedestrian_connections
        controlled_connections = [
            connection for connection in all_connections if connection["tl"] and connection["linkIndex"]
        ]
        controlled_tl_ids = {connection["tl"] for connection in controlled_connections}
        movement_signature_counts = dict(
            Counter(_movement_signature_key(connection) for connection in vehicle_connections)
        )
        tl_phase_count = sum(
            len(tl_by_id[tl_id].findall("phase")) for tl_id in controlled_tl_ids if tl_id in tl_by_id
        )
        pattern_fields = _pattern_fields(model)
        pattern_fields["tl_phase_count"] = tl_phase_count
        pattern_fields["pattern_key"] = _pattern_key(pattern_fields)
        records.append(
            {
                "junction_id": junction_id,
                **pattern_fields,
                "movement_signature_counts": dict(sorted(movement_signature_counts.items())),
            }
        )
    return records


def summarize_junction_pattern_templates(
    records: list[dict[str, Any]],
    *,
    max_examples: int = 3,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        pattern_key = str(record.get("pattern_key", ""))
        if pattern_key:
            groups.setdefault(pattern_key, []).append(record)

    templates = []
    for pattern_key, group in groups.items():
        representative = group[0]
        examples = [
            str(record.get("junction_id", ""))
            for record in group
            if str(record.get("junction_id", ""))
        ][:max(0, max_examples)]
        templates.append(
            {
                "pattern_key": pattern_key,
                "pattern_family": representative.get("pattern_family", ""),
                "arm_count": representative.get("arm_count", 0),
                "count": len(group),
                "example_junction_ids": examples,
                "control_type": representative.get("control_type", ""),
                "has_tls": representative.get("has_tls", False),
                "internal_function_counts": dict(representative.get("internal_function_counts", {}) or {}),
                "dir_counts": dict(representative.get("dir_counts", {}) or {}),
                "movement_signature_counts": dict(representative.get("movement_signature_counts", {}) or {}),
                "request_count": representative.get("request_count", 0),
                "request_bit_lengths_ok": representative.get("request_bit_lengths_ok", False),
                "tl_phase_count": representative.get("tl_phase_count", 0),
                "controlled_link_count": representative.get("controlled_link_count", 0),
                "vehicle_connection_count": representative.get("vehicle_connection_count", 0),
            }
        )
    templates.sort(key=lambda item: (-int(item["count"]), str(item["pattern_family"]), str(item["pattern_key"])))
    return templates


def summarize_junction_pattern_policy(records: list[dict[str, Any]]) -> dict[str, Any]:
    family_counts = Counter(
        str(record.get("pattern_family", ""))
        for record in records
        if str(record.get("pattern_family", ""))
    )
    control_counts = Counter(
        "|".join(
            [
                str(record.get("pattern_family", "")),
                str(record.get("control_type", "")),
                "tls" if bool(record.get("has_tls", False)) else "no_tls",
            ]
        )
        for record in records
    )
    return {
        "record_count": len(records),
        "family_counts": dict(sorted(family_counts.items())),
        "control_counts": dict(sorted(control_counts.items())),
    }


def extract_junction_pattern_exemplar(net_file: Path, junction_id: str) -> dict[str, Any]:
    model = extract_teacher_junction_model(net_file, junction_id)
    incoming = [edge["edge_id"] for edge in model.get("approaches", {}).get("incoming", [])]
    outgoing = [edge["edge_id"] for edge in model.get("approaches", {}).get("outgoing", [])]
    slots = []
    edge_to_slot: dict[str, str] = {}
    for index, edge_id in enumerate(dict.fromkeys(incoming + outgoing)):
        slot_id = f"slot_{index}"
        edge_to_slot[edge_id] = slot_id
        slots.append({"slot_id": slot_id, "members": [edge_id]})

    vehicle_connections = []
    movement_signatures = []
    for connection in model.get("vehicle_connections", []):
        if not isinstance(connection, dict):
            continue
        source_slot = edge_to_slot.get(str(connection.get("from", "")))
        target_slot = edge_to_slot.get(str(connection.get("to", "")))
        if not source_slot or not target_slot:
            continue
        record = {
            "from_slot": source_slot,
            "to_slot": target_slot,
            "fromLane": str(connection.get("fromLane", "")),
            "toLane": str(connection.get("toLane", "")),
            "via": str(connection.get("via", "")),
            "tl": str(connection.get("tl", "")),
            "linkIndex": str(connection.get("linkIndex", "")),
            "dir": str(connection.get("dir", "")),
            "state": str(connection.get("state", "")),
        }
        vehicle_connections.append(record)
        movement_signatures.append(
            {
                "from_slot": source_slot,
                "to_slot": target_slot,
                "fromLane": record["fromLane"],
                "toLane": record["toLane"],
                "dir": record["dir"],
                "state": record["state"],
                "controlled": bool(record["tl"] and record["linkIndex"]),
                "linkIndex": record["linkIndex"],
                "has_internal_via": bool(record["via"]),
            }
        )

    root = ET.parse(net_file).getroot()
    junction = next((node for node in root.findall("junction") if node.attrib.get("id") == junction_id), None)
    requests = [dict(request.attrib) for request in junction.findall("request")] if junction is not None else []
    pattern_fields = _pattern_fields(model)
    return {
        "schema_version": 1,
        "junction_id": junction_id,
        "pattern_family": pattern_fields["pattern_family"],
        "pattern_key": pattern_fields["pattern_key"],
        "approach_slots": slots,
        "vehicle_connections": vehicle_connections,
        "movement_signatures": movement_signatures,
        "traffic_light": model.get("traffic_light", {}),
        "requests": requests,
        "summary": model.get("summary", {}),
    }


def materialize_exemplar_movement_signatures(
    exemplar: dict[str, Any],
    slot_edge_map: dict[str, str],
) -> list[dict[str, Any]]:
    movements = []
    for signature in exemplar.get("movement_signatures", []) or []:
        if not isinstance(signature, dict):
            continue
        source_edge = slot_edge_map.get(str(signature.get("from_slot", "")))
        target_edge = slot_edge_map.get(str(signature.get("to_slot", "")))
        if not source_edge or not target_edge:
            continue
        movements.append(
            {
                "from_edge_id": source_edge,
                "to_edge_id": target_edge,
                "from_slot": str(signature.get("from_slot", "")),
                "to_slot": str(signature.get("to_slot", "")),
                "fromLane": str(signature.get("fromLane", "")),
                "toLane": str(signature.get("toLane", "")),
                "dir": str(signature.get("dir", "")),
                "state": str(signature.get("state", "")),
                "controlled": bool(signature.get("controlled", False)),
                "linkIndex": str(signature.get("linkIndex", "")),
                "has_internal_via": bool(signature.get("has_internal_via", False)),
            }
        )
    return movements


def slot_edge_map_from_exemplar(
    exemplar: dict[str, Any],
    teacher_edge_map: dict[str, str],
) -> dict[str, str]:
    slot_edge_map = {}
    for slot in exemplar.get("approach_slots", []) or []:
        if not isinstance(slot, dict):
            continue
        slot_id = str(slot.get("slot_id", ""))
        if not slot_id:
            continue
        for member in slot.get("members", []) or []:
            candidate_edge = teacher_edge_map.get(str(member))
            if candidate_edge:
                slot_edge_map[slot_id] = candidate_edge
                break
    return slot_edge_map


def evaluate_netedit_semantics_gate(summary: dict[str, Any]) -> dict[str, Any]:
    failed_tables = []
    for table, counts in summary.get("status_counts", {}).items():
        if not isinstance(counts, dict):
            failed_tables.append(str(table))
            continue
        if any(key != "same" and int(value) != 0 for key, value in counts.items()):
            failed_tables.append(str(table))
    if failed_tables:
        return {"status": "fail", "failed_tables": failed_tables, "reason": "non_same_rows_present"}
    return {"status": "pass", "failed_tables": [], "reason": ""}


def match_teacher_approaches(
    teacher_approaches: list[dict[str, Any]],
    candidate_approaches: list[dict[str, Any]],
    max_bearing_delta: float = 30.0,
) -> dict[str, str]:
    matches: dict[str, str] = {}
    used_candidates: set[str] = set()

    for teacher in sorted(teacher_approaches, key=lambda item: item.get("edge_id", "")):
        teacher_id = teacher.get("edge_id", "")
        if not teacher_id:
            continue
        same_id = next(
            (
                candidate
                for candidate in candidate_approaches
                if candidate.get("edge_id", "") == teacher_id and teacher_id not in used_candidates
            ),
            None,
        )
        if same_id is not None:
            matches[teacher_id] = teacher_id
            used_candidates.add(teacher_id)
            continue
        exact = _find_same_source_candidate(teacher, candidate_approaches, used_candidates)
        if exact is not None:
            matches[teacher_id] = exact.get("edge_id", "")
            used_candidates.add(exact.get("edge_id", ""))
            continue

        scored = []
        for candidate in candidate_approaches:
            candidate_id = candidate.get("edge_id", "")
            if not candidate_id or candidate_id in used_candidates:
                continue
            bearing_delta = _bearing_delta(teacher.get("bearing"), candidate.get("bearing"))
            if bearing_delta is None or bearing_delta > max_bearing_delta:
                continue
            lane_delta = abs(int(teacher.get("lane_count", 0) or 0) - int(candidate.get("lane_count", 0) or 0))
            type_penalty = 25.0 if _type_mismatch(teacher, candidate) else 0.0
            scored.append((bearing_delta + lane_delta * 10.0 + type_penalty, bearing_delta, candidate_id))
        if scored:
            _score, _bearing_delta_value, candidate_id = min(scored)
            matches[teacher_id] = candidate_id
            used_candidates.add(candidate_id)

    return matches


def _pattern_fields(model: dict[str, Any]) -> dict[str, Any]:
    summary = model["summary"]
    in_edge_count = int(summary["incoming_vehicle_edge_count"])
    out_edge_count = int(summary["outgoing_vehicle_edge_count"])
    arm_count = _approach_arm_count(model)
    dir_counts = dict(sorted(dict(summary["vehicle_connection_dirs"]).items()))
    all_connections = [
        connection
        for connection in (model.get("vehicle_connections", []) or []) + (model.get("pedestrian_connections", []) or [])
        if isinstance(connection, dict)
    ]
    controlled_link_count = sum(1 for connection in all_connections if connection.get("tl") and connection.get("linkIndex"))
    approach_edge_ids = sorted(
        str(edge.get("edge_id", ""))
        for edge in model.get("approaches", {}).get("incoming", [])
        if isinstance(edge, dict) and edge.get("edge_id")
    )
    internal_function_counts = {
        "crossing": int(summary["crossing_count"]),
        "internal": int(summary.get("internal_edge_count", 0)),
        "walkingarea": int(summary["walkingarea_count"]),
    }
    request_bit_lengths_ok = _request_bit_lengths_ok(model.get("requests", []), int(summary["request_count"]))
    fields = {
        "pattern_family": _pattern_family(arm_count),
        "arm_count": arm_count,
        "control_type": summary.get("junction_type", ""),
        "has_tls": bool(controlled_link_count or int(summary["tl_phase_count"])),
        "approach_edge_ids": approach_edge_ids,
        "in_edge_count": in_edge_count,
        "out_edge_count": out_edge_count,
        "vehicle_connection_count": int(summary["vehicle_connection_count"]),
        "internal_edge_count": int(summary.get("internal_edge_count", 0)),
        "internal_connection_count": int(summary["internal_connection_count"]),
        "internal_function_counts": internal_function_counts,
        "dir_counts": dir_counts,
        "crossing_count": int(summary["crossing_count"]),
        "walkingarea_count": int(summary["walkingarea_count"]),
        "request_count": int(summary["request_count"]),
        "request_bit_lengths_ok": request_bit_lengths_ok,
        "tl_phase_count": int(summary["tl_phase_count"]),
        "controlled_link_count": controlled_link_count,
    }
    return {"pattern_key": _pattern_key(fields), **fields}


def _approach_arm_count(model: dict[str, Any]) -> int:
    approaches = model.get("approaches", {}) if isinstance(model.get("approaches"), dict) else {}
    incoming = _bearing_group_count(approaches.get("incoming", []) or [])
    outgoing = _bearing_group_count(approaches.get("outgoing", []) or [])
    if incoming and outgoing:
        return min(incoming, outgoing)
    return incoming or outgoing


def _bearing_group_count(edges: list[dict[str, Any]], max_delta: float = 20.0) -> int:
    groups: list[float] = []
    unknown = 0
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        try:
            bearing = float(edge["bearing"])
        except (KeyError, TypeError, ValueError):
            unknown += 1
            continue
        if any(_bearing_delta(bearing, group) <= max_delta for group in groups):
            continue
        groups.append(bearing)
    return len(groups) + unknown


def compare_junction_pattern_records(
    teacher: dict[str, Any],
    candidate: dict[str, Any],
    equivalent_approach_edge_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    fields = [
        "approach_edge_ids",
        "control_type",
        "has_tls",
        "internal_function_counts",
        "movement_signature_counts",
        "request_bit_lengths_ok",
    ]
    equivalent_approach_edge_map = equivalent_approach_edge_map or {}
    approach_edge_equivalence_applied = False
    mismatches = []
    for field in fields:
        if field == "approach_edge_ids" and equivalent_approach_edge_map:
            teacher_edges = sorted(equivalent_approach_edge_map.get(edge_id, edge_id) for edge_id in teacher.get(field, []) or [])
            candidate_edges = sorted(candidate.get(field, []) or [])
            if teacher_edges == candidate_edges:
                approach_edge_equivalence_applied = teacher.get(field) != candidate.get(field)
                continue
        if teacher.get(field) != candidate.get(field):
            mismatches.append(field)
    return {
        "status": "fail" if mismatches else "pass",
        "mismatch_fields": mismatches,
        "teacher": {field: teacher.get(field) for field in fields},
        "candidate": {field: candidate.get(field) for field in fields},
        "approach_edge_equivalence_applied": approach_edge_equivalence_applied,
    }


def _pattern_family(arm_count: int) -> str:
    if arm_count == 3:
        return "three_way"
    if arm_count == 4:
        return "four_way"
    return f"{arm_count}_arm"


def _pattern_key(fields: dict[str, Any]) -> str:
    dir_counts = fields.get("dir_counts", {})
    dirs = ",".join(f"{key}:{dir_counts[key]}" for key in sorted(dir_counts)) if isinstance(dir_counts, dict) else ""
    return (
        f"{fields['pattern_family']}|control={fields['control_type'] or 'none'}|dir={dirs or 'none'}|"
        f"veh={fields['vehicle_connection_count']}|tls={fields['controlled_link_count']}/{fields['tl_phase_count']}|"
        f"ped={fields['crossing_count']}/{fields['walkingarea_count']}|"
        f"internal={fields['internal_edge_count']}/{fields['internal_connection_count']}|"
        f"requests={fields['request_count']}"
    )


def _request_bit_lengths_ok(requests: Any, request_count: int) -> bool:
    if not isinstance(requests, list):
        return False
    for request in requests:
        if not isinstance(request, dict):
            return False
        for key in ("response", "foes"):
            value = str(request.get(key, ""))
            if value and len(value) != request_count:
                return False
        cont = str(request.get("cont", ""))
        if cont and len(cont) != 1:
            return False
    return True


def _canonical_edge_record(edge: ET.Element) -> dict[str, Any]:
    return {
        **_sorted_attrs(edge),
        "lanes": [_sorted_attrs(lane) for lane in sorted(edge.findall("lane"), key=_lane_sort_key)],
    }


def _canonical_junction_record(junction: ET.Element) -> dict[str, Any]:
    return {
        **_sorted_attrs(junction),
        "requests": [
            _sorted_attrs(request)
            for request in sorted(junction.findall("request"), key=lambda item: item.attrib.get("index", ""))
        ],
    }


def _canonical_selected_junction_record(
    junction: ET.Element,
    *,
    full: bool,
    boundary_inc_lanes: list[str],
) -> dict[str, Any]:
    if full:
        return _canonical_junction_record(junction)
    record = _canonical_junction_record(junction)
    record["incLanes"] = " ".join(sorted(boundary_inc_lanes))
    record["intLanes"] = ""
    record["requests"] = []
    return record


def _canonical_tl_logic_record(tl_logic: ET.Element) -> dict[str, Any]:
    return {
        **_sorted_attrs(tl_logic),
        "phases": [
            _sorted_attrs(phase)
            for phase in sorted(
                tl_logic.findall("phase"),
                key=lambda item: (
                    item.attrib.get("duration", ""),
                    item.attrib.get("state", ""),
                    item.attrib.get("name", ""),
                ),
            )
        ],
    }


def _sorted_attrs(element: ET.Element | None) -> dict[str, str]:
    return {} if element is None else dict(sorted(element.attrib.items()))


def _record_attrs(record: dict[str, Any], child_key: str) -> dict[str, str]:
    return {str(key): str(value) for key, value in record.items() if key != child_key}


def _is_target_local_connection(
    connection: ET.Element,
    *,
    incoming_edge_ids: set[str],
    outgoing_edge_ids: set[str],
    internal_prefix: str,
) -> bool:
    source = connection.attrib.get("from", "")
    target = connection.attrib.get("to", "")
    via = connection.attrib.get("via", "")
    return (
        via.startswith(internal_prefix)
        or source.startswith(internal_prefix)
        or target.startswith(internal_prefix)
        or (source in incoming_edge_ids and target in outgoing_edge_ids)
    )


def _safe_file_stem(value: str) -> str:
    stem = "".join(char if char.isalnum() or char in "-_." else "_" for char in value)
    return stem or "junction"


def _sumo_load_report(net_file: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "sumo",
                "-n",
                str(net_file),
                "--no-step-log",
                "true",
                "--duration-log.disable",
                "true",
                "--quit-on-end",
                "true",
                "-W",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        return {"sumo_returncode": -1, "sumo_stdout": "", "sumo_stderr": str(exc)}
    return {
        "sumo_returncode": result.returncode,
        "sumo_stdout": result.stdout,
        "sumo_stderr": result.stderr,
    }


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


def _split(value: str) -> list[str]:
    return [part for part in value.split() if part]


def _lane_allows_non_pedestrian(lane: ET.Element) -> bool:
    allow = set(_split(lane.attrib.get("allow", "")))
    return not allow or allow != {"pedestrian"}


def _edge_allows_non_pedestrian(edge: ET.Element) -> bool:
    lanes = edge.findall("lane")
    return not lanes or any(_lane_allows_non_pedestrian(lane) for lane in lanes)


def _edge_record(edge: ET.Element) -> dict[str, Any]:
    lanes = [_lane_record(lane) for lane in edge.findall("lane")]
    return {
        "edge_id": edge.attrib.get("id", ""),
        "from": edge.attrib.get("from", ""),
        "to": edge.attrib.get("to", ""),
        "type": edge.attrib.get("type", ""),
        "function": edge.attrib.get("function", ""),
        "bearing": _shape_bearing(lanes[0]["shape"] if lanes else ""),
        "lane_count": len(lanes),
        "lanes": lanes,
    }


def _internal_edge_record(edge: ET.Element, crossing: bool = False) -> dict[str, Any]:
    record = _edge_record(edge)
    if crossing:
        record["crossingEdges"] = _split(edge.attrib.get("crossingEdges", ""))
    return record


def _junction_record(junction: ET.Element) -> dict[str, str]:
    return {
        "junction_id": junction.attrib.get("id", ""),
        "type": junction.attrib.get("type", ""),
        "x": junction.attrib.get("x", ""),
        "y": junction.attrib.get("y", ""),
        "incLanes": junction.attrib.get("incLanes", ""),
        "intLanes": junction.attrib.get("intLanes", ""),
        "shape": junction.attrib.get("shape", ""),
        "customShape": junction.attrib.get("customShape", ""),
    }


def _lane_record(lane: ET.Element) -> dict[str, str]:
    return {
        "id": lane.attrib.get("id", ""),
        "index": lane.attrib.get("index", ""),
        "allow": lane.attrib.get("allow", ""),
        "disallow": lane.attrib.get("disallow", ""),
        "speed": lane.attrib.get("speed", ""),
        "length": lane.attrib.get("length", ""),
        "width": lane.attrib.get("width", ""),
        "shape": lane.attrib.get("shape", ""),
        "outlineShape": lane.attrib.get("outlineShape", ""),
    }


def _connection_record(connection: ET.Element) -> dict[str, str]:
    return {
        "from": connection.attrib.get("from", ""),
        "to": connection.attrib.get("to", ""),
        "fromLane": connection.attrib.get("fromLane", ""),
        "toLane": connection.attrib.get("toLane", ""),
        "via": connection.attrib.get("via", ""),
        "tl": connection.attrib.get("tl", ""),
        "linkIndex": connection.attrib.get("linkIndex", ""),
        "dir": connection.attrib.get("dir", ""),
        "state": connection.attrib.get("state", ""),
        "pass": connection.attrib.get("pass", ""),
        "uncontrolled": connection.attrib.get("uncontrolled", ""),
        "allow": connection.attrib.get("allow", ""),
        "disallow": connection.attrib.get("disallow", ""),
        "keepClear": connection.attrib.get("keepClear", ""),
        "contPos": connection.attrib.get("contPos", ""),
        "linkIndex2": connection.attrib.get("linkIndex2", ""),
        "shape": connection.attrib.get("shape", ""),
    }


def _movement_signature_key(connection: dict[str, str]) -> str:
    controlled = str(bool(connection.get("tl") and connection.get("linkIndex"))).lower()
    via = str(bool(connection.get("via"))).lower()
    return (
        f"dir={connection.get('dir') or 'blank'}|state={connection.get('state') or 'blank'}|"
        f"fromLane={connection.get('fromLane') or 'blank'}|toLane={connection.get('toLane') or 'blank'}|"
        f"controlled={controlled}|via={via}"
    )


def _is_pedestrian_connection(
    connection: ET.Element,
    edges: dict[str, ET.Element],
    internal_prefix: str,
) -> bool:
    source = connection.attrib.get("from", "")
    target = connection.attrib.get("to", "")
    if not (source.startswith(internal_prefix) or target.startswith(internal_prefix)):
        return False
    return any(_is_pedestrian_edge(edges.get(edge_id)) for edge_id in (source, target))


def _is_pedestrian_edge(edge: ET.Element | None) -> bool:
    if edge is None:
        return False
    if edge.attrib.get("function") in {"crossing", "walkingarea"}:
        return True
    return not _edge_allows_non_pedestrian(edge)


def _internal_mode_counts(edges: Any, internal_prefix: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for edge in edges:
        if not edge.attrib.get("id", "").startswith(internal_prefix):
            continue
        for lane in edge.findall("lane"):
            modes = _split(lane.attrib.get("allow", "")) or ["all"]
            counts.update(modes)
    return dict(counts)


def _find_same_source_candidate(
    teacher: dict[str, Any],
    candidates: list[dict[str, Any]],
    used_candidates: set[str],
) -> dict[str, Any] | None:
    teacher_source = teacher.get("source_node_id")
    if not teacher_source:
        return None
    for candidate in candidates:
        candidate_id = candidate.get("edge_id", "")
        candidate_source = candidate.get("source_node_id")
        if candidate_id and candidate_id not in used_candidates and candidate_source == teacher_source:
            return candidate
    return None


def _bearing_delta(left: Any, right: Any) -> float | None:
    if left is None or right is None:
        return None
    delta = abs(float(left) - float(right)) % 360.0
    return min(delta, 360.0 - delta)


def _type_mismatch(teacher: dict[str, Any], candidate: dict[str, Any]) -> bool:
    teacher_type = teacher.get("type")
    candidate_type = candidate.get("type")
    return bool(teacher_type and candidate_type and teacher_type != candidate_type)


def _shape_bearing(shape: str) -> float | None:
    points = []
    for point in _split(shape):
        parts = point.split(",")
        if len(parts) < 2:
            continue
        points.append((float(parts[0]), float(parts[1])))
    if len(points) < 2:
        return None
    x0, y0 = points[0]
    x1, y1 = points[-1]
    return round(math.degrees(math.atan2(y1 - y0, x1 - x0)) % 360.0, 6)
