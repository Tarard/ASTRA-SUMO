from __future__ import annotations

import hashlib
import html
import itertools
import math
import re
import shutil
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .connection_mode_audit import (
    audit_standard_connection_mode,
    build_connection_mode_catalog,
    lane_supports_motorized,
    resolve_network_traffic_side,
)
from .routeability_audit import run_routeability_audit
from .sumo_commands import discover_binaries


_CONNECTION_TAG = re.compile(r"<connection(?=\s)[^<>]*/>", re.DOTALL)
_TLLOGIC_BLOCK = re.compile(r"<tlLogic(?=\s)[^>]*>.*?</tlLogic>", re.DOTALL)
_PEDESTRIAN_EDGE_FUNCTIONS = frozenset({"crossing", "walkingarea"})
_SUPPORTED_JUNCTION_TYPES = frozenset({"traffic_light"})
_LEFT_PHASES = frozenset({1, 3, 5, 7})
_TURN_LABELS = {"l": "l", "r": "r", "s": "s", "t": "t"}
_MAX_ARM_PAIR_ERROR_DEG = 25.0
_MAX_IN_OUT_ARM_ERROR_DEG = 20.0
_MIN_ARM_SEPARATION_DEG = 35.0


CommandRunner = Callable[..., Any]
RouteabilityRunner = Callable[..., dict[str, Any]]


def build_standard_nema_phase_binding(
    net_file: Path,
    *,
    output_dir: Path,
    prefix: str = "standard_nema_binding",
    junction_id: str | None = None,
    run_runtime_checks: bool = True,
    run_routeability: bool = True,
    routeability_vehicle_count: int = 12,
    netconvert_binary: str | None = None,
    sumo_binary: str | None = None,
    random_trips_script: str | None = None,
    timeout_seconds: float = 240.0,
    command_runner: CommandRunner = run_command,
    routeability_runner: RouteabilityRunner = run_routeability_audit,
) -> dict[str, Any]:
    """Scan or materialize one fail-closed standard NEMA binding candidate.

    With no ``junction_id`` this function writes a review queue only. With a
    target it creates a separate, SHA-bound network candidate only when the
    controller is an isolated, vehicle-only, geometrically standard three- or
    four-arm junction. The source network is never modified and promotion is
    always left in ``review_required`` state because generic NEMA timings are
    not a field-calibrated signal plan.
    """

    source = Path(net_file).resolve()
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    paths = _artifact_paths(destination, prefix)
    _remove_stale_candidate_artifacts(paths)

    requested_junction = (junction_id or "").strip()
    if not source.is_file():
        return _persist_input_failure(
            source=source,
            paths=paths,
            error=f"SUMO network does not exist: {source}",
            requested_junction=requested_junction,
        )
    if source.suffix.casefold() == ".gz":
        return _persist_input_failure(
            source=source,
            paths=paths,
            error="standard NEMA binding requires an uncompressed .net.xml source",
            requested_junction=requested_junction,
        )
    if paths["candidate_file"] == source:
        return _persist_input_failure(
            source=source,
            paths=paths,
            error="candidate output must be distinct from the source network",
            requested_junction=requested_junction,
        )

    source_sha256 = file_sha256(source)
    try:
        source_text = source.read_text(encoding="utf-8")
        root = ET.fromstring(source_text)
    except (OSError, UnicodeError, ET.ParseError) as exc:
        return _persist_input_failure(
            source=source,
            paths=paths,
            error=f"{type(exc).__name__}: {exc}",
            requested_junction=requested_junction,
            source_sha256=source_sha256,
        )

    catalog = _build_catalog(root)
    records = _scan_standard_junctions(catalog)
    counts = _scan_counts(records)
    _write_connection_mode_report(
        paths["connection_mode_report_file"],
        source=source,
        source_sha256=source_sha256,
        records=records,
    )
    _write_review_overlay(paths["overlay_file"], records, source_sha256=source_sha256)

    selected = next((record for record in records if record["junction_id"] == requested_junction), None)
    plan = _base_plan(
        source=source,
        source_sha256=source_sha256,
        requested_junction=requested_junction,
        records=records,
        counts=counts,
        traffic_side=str(catalog["traffic_side"]["effective"]),
    )

    if not requested_junction:
        plan.update(
            {
                "status": "scan_complete",
                "claim_status": "diagnostic-demo",
                "warnings": [
                    "No network was modified; pass one eligible junction_id to materialize a candidate",
                    "review additional.xml is display-only and cannot alter traffic-light behavior",
                ],
            }
        )
        write_json_atomic(paths["plan_file"], plan, sort_keys=True)
        decision = _decision_template(
            source=source,
            source_sha256=source_sha256,
            selected=None,
            candidate_file=None,
            candidate_sha256="",
        )
        write_json_atomic(paths["decision_file"], decision, sort_keys=True)
        report = _base_report(
            source=source,
            source_sha256=source_sha256,
            paths=paths,
            records=records,
            counts=counts,
            requested_junction="",
        )
        report.update(
            {
                "status": "pass",
                "claim_status": "diagnostic-demo",
                "nema_binding_status": "scan_complete",
                "promotion_status": "not_applicable",
                "candidate_net_file": "",
                "warnings": list(plan["warnings"]),
            }
        )
        _persist_review_package(report, decision, paths, source=source)
        return report

    if selected is None:
        plan.update(
            {
                "status": "blocked",
                "claim_status": "construction-invalid",
                "warnings": [f"junction_id was not found in the traffic-light review queue: {requested_junction}"],
            }
        )
        write_json_atomic(paths["plan_file"], plan, sort_keys=True)
        decision = _decision_template(
            source=source,
            source_sha256=source_sha256,
            selected=None,
            candidate_file=None,
            candidate_sha256="",
        )
        decision.update({"status": "ineligible", "reason": "junction_not_found"})
        write_json_atomic(paths["decision_file"], decision, sort_keys=True)
        report = _base_report(
            source=source,
            source_sha256=source_sha256,
            paths=paths,
            records=records,
            counts=counts,
            requested_junction=requested_junction,
        )
        report.update(
            {
                "status": "blocked",
                "claim_status": "construction-invalid",
                "nema_binding_status": "blocked_unknown_junction",
                "promotion_status": "blocked",
                "candidate_net_file": "",
                "warnings": list(plan["warnings"]),
            }
        )
        _persist_review_package(report, decision, paths, source=source)
        return report

    if selected["eligibility_status"] != "eligible":
        plan.update(
            {
                "status": "blocked",
                "claim_status": "blocked",
                "selected_candidate": selected,
                "warnings": [
                    "The target does not satisfy the strict automatic NEMA policy; no candidate was written",
                    "Resolve the listed blockers or use a reviewed joined-controller/pedestrian-aware workflow",
                ],
            }
        )
        write_json_atomic(paths["plan_file"], plan, sort_keys=True)
        decision = _decision_template(
            source=source,
            source_sha256=source_sha256,
            selected=selected,
            candidate_file=None,
            candidate_sha256="",
        )
        decision.update({"status": "ineligible", "reason": "strict_eligibility_blockers"})
        write_json_atomic(paths["decision_file"], decision, sort_keys=True)
        report = _base_report(
            source=source,
            source_sha256=source_sha256,
            paths=paths,
            records=records,
            counts=counts,
            requested_junction=requested_junction,
        )
        report.update(
            {
                "status": "blocked",
                "claim_status": "blocked",
                "nema_binding_status": "blocked_ineligible",
                "promotion_status": "blocked",
                "selected_candidate": selected,
                "candidate_net_file": "",
                "warnings": list(plan["warnings"]),
            }
        )
        _persist_review_package(report, decision, paths, source=source)
        return report

    operations = _binding_operations(catalog, selected)
    replacement_logic = _build_nema_tllogic(selected)
    patched_text, patch_evidence = _patch_network_text(
        source_text=source_text,
        root=root,
        operations=operations,
        controller_id=str(selected["controller_id"]),
        replacement_logic=replacement_logic,
    )
    if patched_text is None:
        plan.update(
            {
                "status": "blocked",
                "claim_status": "construction-invalid",
                "selected_candidate": selected,
                "operations": operations,
                "minimal_patch": patch_evidence,
                "warnings": [str(patch_evidence.get("error", "minimal text patch failed"))],
            }
        )
        write_json_atomic(paths["plan_file"], plan, sort_keys=True)
        decision = _decision_template(
            source=source,
            source_sha256=source_sha256,
            selected=selected,
            candidate_file=None,
            candidate_sha256="",
        )
        decision.update({"status": "ineligible", "reason": "minimal_patch_unavailable"})
        write_json_atomic(paths["decision_file"], decision, sort_keys=True)
        report = _base_report(
            source=source,
            source_sha256=source_sha256,
            paths=paths,
            records=records,
            counts=counts,
            requested_junction=requested_junction,
        )
        report.update(
            {
                "status": "blocked",
                "claim_status": "construction-invalid",
                "nema_binding_status": "blocked_minimal_patch_unavailable",
                "promotion_status": "blocked",
                "selected_candidate": selected,
                "minimal_patch": patch_evidence,
                "candidate_net_file": "",
                "warnings": list(plan["warnings"]),
            }
        )
        _persist_review_package(report, decision, paths, source=source)
        return report

    write_text_atomic(paths["candidate_file"], patched_text)
    candidate_sha256 = file_sha256(paths["candidate_file"])
    validation = _validate_candidate(
        source_root=root,
        candidate_file=paths["candidate_file"],
        selected=selected,
        operations=operations,
    )
    source_after_sha256 = file_sha256(source)
    source_preserved = source_after_sha256 == source_sha256
    candidate_distinct = candidate_sha256 != source_sha256
    construction_pass = validation["status"] == "pass" and source_preserved and candidate_distinct

    runtime = _not_run_runtime("runtime checks disabled")
    if construction_pass and run_runtime_checks:
        runtime = _run_runtime_validation(
            candidate_file=paths["candidate_file"],
            selected=selected,
            operations=operations,
            paths=paths,
            run_routeability=run_routeability,
            routeability_vehicle_count=routeability_vehicle_count,
            netconvert_binary=netconvert_binary,
            sumo_binary=sumo_binary,
            random_trips_script=random_trips_script,
            timeout_seconds=timeout_seconds,
            command_runner=command_runner,
            routeability_runner=routeability_runner,
        )

    runtime_pass = runtime["status"] == "pass" or not run_runtime_checks
    candidate_ready = construction_pass and runtime_pass
    plan.update(
        {
            "status": "candidate_ready_for_review" if candidate_ready else "blocked",
            "claim_status": "diagnostic-demo" if candidate_ready else "construction-invalid",
            "selected_candidate": selected,
            "candidate_net_file": str(paths["candidate_file"]),
            "candidate_sha256": candidate_sha256,
            "source_after_sha256": source_after_sha256,
            "operations": operations,
            "replacement_tllogic": _element_payload(replacement_logic),
            "minimal_patch": patch_evidence,
            "candidate_validation": validation,
            "runtime_validation": runtime,
            "rollback": {
                "strategy": "discard candidate and keep the immutable source, or restore operation.before_link_index and before_tllogic_xml",
                "source_network_immutable": True,
                "before_tllogic_xml": selected["existing_tllogic_xml"],
            },
            "warnings": [
                "Generic NEMA timing is not field calibrated; human review remains mandatory before promotion",
                "The review additional.xml is display-only; the operational proposal is the separate candidate network",
            ],
        }
    )
    write_json_atomic(paths["plan_file"], plan, sort_keys=True)

    decision = _decision_template(
        source=source,
        source_sha256=source_sha256,
        selected=selected,
        candidate_file=paths["candidate_file"],
        candidate_sha256=candidate_sha256,
    )
    decision["status"] = "pending_human_review" if candidate_ready else "blocked_validation"
    write_json_atomic(paths["decision_file"], decision, sort_keys=True)

    report = _base_report(
        source=source,
        source_sha256=source_sha256,
        paths=paths,
        records=records,
        counts=counts,
        requested_junction=requested_junction,
    )
    report.update(
        {
            "status": "pass" if candidate_ready else "blocked",
            "claim_status": "diagnostic-demo" if candidate_ready else "construction-invalid",
            "nema_binding_status": "candidate_ready_for_review" if candidate_ready else "candidate_failed_validation",
            "promotion_status": "review_required" if candidate_ready else "blocked",
            "selected_candidate": selected,
            "candidate_net_file": str(paths["candidate_file"]),
            "candidate_sha256": candidate_sha256,
            "validated_net_file": str(paths["validated_file"]) if paths["validated_file"].is_file() else "",
            "source_after_sha256": source_after_sha256,
            "source_preservation_status": "pass" if source_preserved else "fail",
            "candidate_identity_status": "distinct" if candidate_distinct else "identity-copy",
            "minimal_patch": patch_evidence,
            "candidate_validation": validation,
            "runtime_validation": runtime,
            "warnings": list(plan["warnings"]),
        }
    )
    _persist_review_package(report, decision, paths, source=source)
    return report


def _artifact_paths(output_dir: Path, prefix: str) -> dict[str, Path]:
    return {
        "output_dir": output_dir,
        "plan_file": output_dir / f"{prefix}.plan.json",
        "report_file": output_dir / f"{prefix}.json",
        "manifest_file": output_dir / f"{prefix}.manifest.json",
        "overlay_file": output_dir / f"{prefix}.review.add.xml",
        "review_html_file": output_dir / f"{prefix}.review.html",
        "decision_file": output_dir / f"{prefix}.decision.json",
        "candidate_file": output_dir / f"{prefix}.candidate.net.xml",
        "validated_file": output_dir / f"{prefix}.netconvert.net.xml",
        "netconvert_report_file": output_dir / f"{prefix}.netconvert.json",
        "sumo_load_report_file": output_dir / f"{prefix}.sumo_load.json",
        "connection_mode_report_file": output_dir / f"{prefix}.connection_mode.json",
        "routeability_dir": output_dir / f"{prefix}.routeability",
    }


def _remove_stale_candidate_artifacts(paths: Mapping[str, Path]) -> None:
    file_keys = (
        "plan_file",
        "report_file",
        "manifest_file",
        "overlay_file",
        "review_html_file",
        "decision_file",
        "candidate_file",
        "validated_file",
        "netconvert_report_file",
        "sumo_load_report_file",
        "connection_mode_report_file",
    )
    for key in file_keys:
        try:
            paths[key].unlink(missing_ok=True)
        except OSError:
            # The later write/validation path will surface a precise error.
            pass
    routeability_dir = paths["routeability_dir"]
    if routeability_dir.is_dir() and routeability_dir.parent == paths["output_dir"]:
        try:
            shutil.rmtree(routeability_dir)
        except OSError:
            pass


def _build_catalog(root: ET.Element) -> dict[str, Any]:
    junctions = {
        element.attrib["id"]: element
        for element in root.findall("junction")
        if element.attrib.get("id")
    }
    edges = {
        element.attrib["id"]: element
        for element in root.findall("edge")
        if element.attrib.get("id")
    }
    connections = root.findall("connection")
    tl_logics = root.findall("tlLogic")
    logics_by_id: dict[str, list[tuple[int, ET.Element]]] = defaultdict(list)
    for index, logic in enumerate(tl_logics):
        if logic.attrib.get("id"):
            logics_by_id[logic.attrib["id"]].append((index, logic))

    lane_owners: dict[str, list[str]] = defaultdict(list)
    for junction_id, junction in junctions.items():
        for lane_id in junction.attrib.get("incLanes", "").split():
            lane_owners[lane_id].append(junction_id)

    connection_owners: list[str | None] = []
    for connection in connections:
        connection_owners.append(
            _connection_owner(
                connection,
                junctions=junctions,
                edges=edges,
                lane_owners=lane_owners,
            )
        )
    return {
        "root": root,
        "traffic_side": resolve_network_traffic_side(root),
        "junctions": junctions,
        "edges": edges,
        "connections": connections,
        "connection_owners": connection_owners,
        "tl_logics": tl_logics,
        "logics_by_id": logics_by_id,
        "connection_mode_catalog": build_connection_mode_catalog(root),
    }


def _connection_owner(
    connection: ET.Element,
    *,
    junctions: Mapping[str, ET.Element],
    edges: Mapping[str, ET.Element],
    lane_owners: Mapping[str, list[str]],
) -> str | None:
    from_edge = edges.get(connection.attrib.get("from", ""))
    lane = _edge_lane(from_edge, connection.attrib.get("fromLane", ""))
    lane_id = lane.attrib.get("id", "") if lane is not None else ""
    owners = lane_owners.get(lane_id, [])
    if len(owners) == 1:
        return owners[0]

    for value in (connection.attrib.get("via", ""), connection.attrib.get("from", "")):
        derived = _junction_prefix(value, junctions)
        if derived:
            return derived
    return None


def _junction_prefix(value: str, junctions: Mapping[str, ET.Element]) -> str | None:
    if not value.startswith(":"):
        return None
    body = value[1:]
    for position in reversed([index for index, char in enumerate(body) if char == "_"]):
        candidate = body[:position]
        if candidate in junctions:
            return candidate
    return body if body in junctions else None


def _scan_standard_junctions(catalog: Mapping[str, Any]) -> list[dict[str, Any]]:
    junctions: Mapping[str, ET.Element] = catalog["junctions"]
    connections: list[ET.Element] = catalog["connections"]
    owners: list[str | None] = catalog["connection_owners"]
    candidate_ids: set[str] = {
        junction_id
        for junction_id, junction in junctions.items()
        if junction.attrib.get("type", "").startswith("traffic_light")
    }
    candidate_ids.update(
        owner
        for index, owner in enumerate(owners)
        if owner and connections[index].attrib.get("tl")
    )

    records = [_classify_standard_junction(catalog, junction_id) for junction_id in sorted(candidate_ids)]
    return records


def _classify_standard_junction(catalog: Mapping[str, Any], junction_id: str) -> dict[str, Any]:
    junction: ET.Element = catalog["junctions"][junction_id]
    edges: Mapping[str, ET.Element] = catalog["edges"]
    connections: list[ET.Element] = catalog["connections"]
    owners: list[str | None] = catalog["connection_owners"]
    blockers: list[str] = []
    warnings: list[str] = []

    traffic_side = catalog["traffic_side"]
    blockers.extend(
        f"traffic_side_contract:{failure}"
        for failure in traffic_side.get("failures", [])
    )
    if traffic_side.get("effective") != "right":
        blockers.append(
            f"traffic_side_not_certified_for_vehicle_nema:{traffic_side.get('effective', 'unknown')}"
        )

    junction_type = junction.attrib.get("type", "")
    if junction_type not in _SUPPORTED_JUNCTION_TYPES:
        blockers.append(f"unsupported_junction_type:{junction_type or 'missing'}")
    position = _junction_position(junction)
    if position is None:
        blockers.append("junction_coordinates_missing_or_invalid")
        position = (0.0, 0.0)

    owned_indices = [index for index, owner in enumerate(owners) if owner == junction_id]
    direct_indices = [
        index
        for index in owned_indices
        if _is_direct_external_connection(connections[index], edges)
    ]
    if not direct_indices:
        blockers.append("no_direct_external_movements")

    vehicle_indices: list[int] = []
    unsupported_direct: list[int] = []
    for index in direct_indices:
        connection = connections[index]
        from_lane = _edge_lane(
            edges.get(connection.attrib.get("from", "")),
            connection.attrib.get("fromLane", ""),
        )
        to_lane = _edge_lane(
            edges.get(connection.attrib.get("to", "")),
            connection.attrib.get("toLane", ""),
        )
        if lane_supports_motorized(from_lane) and lane_supports_motorized(to_lane):
            vehicle_indices.append(index)
        else:
            unsupported_direct.append(index)
    if unsupported_direct:
        blockers.append(f"unsupported_non_motorized_direct_movements:{len(unsupported_direct)}")

    controller_ids = {
        connections[index].attrib.get("tl", "")
        for index in vehicle_indices
        if connections[index].attrib.get("tl")
    }
    uncontrolled_count = sum(1 for index in vehicle_indices if not connections[index].attrib.get("tl"))
    if uncontrolled_count:
        blockers.append(f"uncontrolled_vehicle_movements:{uncontrolled_count}")
    if len(controller_ids) != 1:
        blockers.append(f"vehicle_controller_count_not_one:{len(controller_ids)}")
    controller_id = next(iter(controller_ids), "")

    logic_entries = catalog["logics_by_id"].get(controller_id, []) if controller_id else []
    if controller_id and len(logic_entries) != 1:
        blockers.append(f"embedded_tllogic_count_not_one:{len(logic_entries)}")

    controller_connection_indices = [
        index
        for index, connection in enumerate(connections)
        if controller_id and connection.attrib.get("tl") == controller_id
    ]
    foreign_owners = sorted(
        {
            owners[index] or "unresolved"
            for index in controller_connection_indices
            if owners[index] != junction_id
        }
    )
    if foreign_owners:
        blockers.append(f"controller_spans_other_junctions:{','.join(foreign_owners)}")
    nondirect_controlled = [
        index
        for index in controller_connection_indices
        if owners[index] == junction_id and index not in vehicle_indices
    ]
    if nondirect_controlled:
        blockers.append(f"controller_has_pedestrian_or_internal_links:{len(nondirect_controlled)}")
    if any(connections[index].attrib.get("linkIndex2") for index in controller_connection_indices):
        blockers.append("controller_uses_linkIndex2")
    missing_link_index = sum(
        1 for index in vehicle_indices if connections[index].attrib.get("linkIndex", "") == ""
    )
    if missing_link_index:
        blockers.append(f"controlled_movements_missing_linkIndex:{missing_link_index}")

    layout = _classify_layout(
        root=catalog["root"],
        connection_mode_catalog=catalog["connection_mode_catalog"],
        junction_id=junction_id,
        junction_position=position,
        vehicle_indices=vehicle_indices,
        connections=connections,
        edges=edges,
    )
    blockers.extend(layout["blockers"])
    warnings.extend(layout["warnings"])
    blockers = list(dict.fromkeys(blockers))

    existing_logic = logic_entries[0][1] if len(logic_entries) == 1 else None
    record = {
        "junction_id": junction_id,
        "junction_type": junction_type,
        "junction_position": {"x": position[0], "y": position[1]},
        "controller_id": controller_id,
        "eligibility_status": "eligible" if not blockers else "review_required",
        "automatic_materialization_allowed": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "owned_connection_count": len(owned_indices),
        "direct_vehicle_movement_count": len(vehicle_indices),
        "controller_connection_count": len(controller_connection_indices),
        "controller_foreign_owners": foreign_owners,
        "existing_program": (
            {
                "type": existing_logic.attrib.get("type", ""),
                "programID": existing_logic.attrib.get("programID", ""),
                "offset": existing_logic.attrib.get("offset", "0"),
                "phase_count": len(existing_logic.findall("phase")),
                "state_length": max(
                    (len(phase.attrib.get("state", "")) for phase in existing_logic.findall("phase")),
                    default=0,
                ),
            }
            if existing_logic is not None
            else {}
        ),
        "existing_tllogic_xml": (
            ET.tostring(existing_logic, encoding="unicode")
            if existing_logic is not None and not blockers
            else ""
        ),
        **{key: value for key, value in layout.items() if key not in {"blockers", "warnings"}},
    }
    return record


def _classify_layout(
    *,
    root: ET.Element,
    connection_mode_catalog: Mapping[str, Any],
    junction_id: str,
    junction_position: tuple[float, float],
    vehicle_indices: list[int],
    connections: list[ET.Element],
    edges: Mapping[str, ET.Element],
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    incoming_edge_ids = sorted({connections[index].attrib.get("from", "") for index in vehicle_indices})
    outgoing_edge_ids = sorted({connections[index].attrib.get("to", "") for index in vehicle_indices})
    arm_count = len(incoming_edge_ids)
    if arm_count not in {3, 4}:
        blockers.append(f"incoming_arm_count_not_three_or_four:{arm_count}")
    if len(outgoing_edge_ids) != arm_count:
        blockers.append(f"incoming_outgoing_arm_count_mismatch:{arm_count}:{len(outgoing_edge_ids)}")

    arms: list[dict[str, Any]] = []
    for ordinal, edge_id in enumerate(incoming_edge_ids):
        edge = edges.get(edge_id)
        vector = _edge_arm_vector(edge, junction_position)
        if vector is None:
            blockers.append(f"incoming_arm_geometry_unavailable:{edge_id}")
            continue
        arms.append(
            {
                "arm_id": f"arm_{ordinal}",
                "incoming_edge": edge_id,
                "outgoing_edge": "",
                "unit_x": vector[0],
                "unit_y": vector[1],
                "angle_deg": _angle_deg(vector),
                "priority": _safe_int(edge.attrib.get("priority", ""), default=0) if edge is not None else 0,
                "lane_count": len(edge.findall("lane")) if edge is not None else 0,
                "speed_mps": _edge_speed(edge),
            }
        )

    outgoing_vectors: dict[str, tuple[float, float]] = {}
    for edge_id in outgoing_edge_ids:
        vector = _edge_arm_vector(edges.get(edge_id), junction_position)
        if vector is None:
            blockers.append(f"outgoing_arm_geometry_unavailable:{edge_id}")
        else:
            outgoing_vectors[edge_id] = vector

    if len(arms) == arm_count and len(outgoing_vectors) == len(outgoing_edge_ids):
        unmatched = set(outgoing_edge_ids)
        for arm in sorted(arms, key=lambda item: item["incoming_edge"]):
            choices = sorted(
                (
                    _angle_distance(float(arm["angle_deg"]), _angle_deg(outgoing_vectors[edge_id])),
                    edge_id,
                )
                for edge_id in unmatched
            )
            if not choices:
                blockers.append(f"outgoing_arm_unmatched:{arm['incoming_edge']}")
                continue
            error, edge_id = choices[0]
            if error > _MAX_IN_OUT_ARM_ERROR_DEG:
                blockers.append(
                    f"incoming_outgoing_arm_geometry_mismatch:{arm['incoming_edge']}:{edge_id}:{error:.1f}deg"
                )
                continue
            arm["outgoing_edge"] = edge_id
            arm["in_out_alignment_error_deg"] = round(error, 3)
            unmatched.remove(edge_id)
        if unmatched:
            blockers.append(f"unmatched_outgoing_arms:{','.join(sorted(unmatched))}")

    for first, second in itertools.combinations(arms, 2):
        separation = _angle_distance(float(first["angle_deg"]), float(second["angle_deg"]))
        if separation < _MIN_ARM_SEPARATION_DEG:
            blockers.append(
                f"incoming_edges_share_one_physical_arm:{first['incoming_edge']}:{second['incoming_edge']}"
            )

    layout_type = "unknown"
    phase_by_arm: dict[str, dict[str, int]] = {}
    opposite_pairs: list[list[str]] = []
    main_pair: list[str] = []
    minor_pair: list[str] = []
    stem_arm = ""
    if len(arms) == 4 and arm_count == 4:
        layout_type = "four_way"
        pairing = _four_way_pairing(arms)
        if pairing is None:
            blockers.append("four_way_opposite_pairing_unavailable")
        else:
            opposite_pairs = [list(pair) for pair in pairing["pairs"]]
            for error in pairing["errors"]:
                if error > _MAX_ARM_PAIR_ERROR_DEG:
                    blockers.append(f"four_way_opposite_axis_error:{error:.1f}deg")
            ranked_pairs = sorted(
                pairing["pairs"],
                key=lambda pair: (_pair_score(pair, arms), _pair_token(pair)),
                reverse=True,
            )
            main = _ordered_pair(ranked_pairs[0], arms)
            minor = _ordered_pair(ranked_pairs[1], arms)
            main_pair = list(main)
            minor_pair = list(minor)
            phase_by_arm = {
                main[0]: {"left": 5, "through_right": 2},
                main[1]: {"left": 1, "through_right": 6},
                minor[0]: {"left": 7, "through_right": 4},
                minor[1]: {"left": 3, "through_right": 8},
            }
    elif len(arms) == 3 and arm_count == 3:
        layout_type = "three_way"
        pairing = _three_way_pairing(arms)
        if pairing is None:
            blockers.append("three_way_main_axis_unavailable")
        else:
            main = _ordered_pair(pairing["main_pair"], arms)
            stem = pairing["stem"]
            main_pair = list(main)
            stem_arm = stem
            opposite_pairs = [list(main)]
            if pairing["main_error"] > _MAX_ARM_PAIR_ERROR_DEG:
                blockers.append(f"three_way_main_axis_error:{pairing['main_error']:.1f}deg")
            for angle in pairing["stem_angles"]:
                if not 55.0 <= angle <= 125.0:
                    blockers.append(f"three_way_stem_angle_not_standard:{angle:.1f}deg")
            phase_by_arm = {
                main[0]: {"left": 5, "through_right": 2},
                main[1]: {"left": 1, "through_right": 6},
                stem: {"left": 4, "through_right": 4},
            }

    arms_by_in = {str(arm["incoming_edge"]): arm for arm in arms}
    arms_by_out = {
        str(arm["outgoing_edge"]): arm
        for arm in arms
        if arm.get("outgoing_edge")
    }
    movement_rows: list[dict[str, Any]] = []
    lane_turns: dict[tuple[str, str], set[str]] = defaultdict(set)
    movement_pairs: set[tuple[str, str]] = set()
    for connection_index in vehicle_indices:
        connection = connections[connection_index]
        from_edge = connection.attrib.get("from", "")
        to_edge = connection.attrib.get("to", "")
        from_arm = arms_by_in.get(from_edge)
        to_arm = arms_by_out.get(to_edge)
        if from_arm is None or to_arm is None:
            blockers.append(f"movement_arm_unresolved:{connection_index}")
            continue
        delta = _normalize_signed_angle(
            float(to_arm["angle_deg"]) - (float(from_arm["angle_deg"]) + 180.0)
        )
        geometry_turn = _turn_from_delta(delta)
        source_turn = _TURN_LABELS.get(connection.attrib.get("dir", "").casefold(), "")
        if geometry_turn == "ambiguous":
            blockers.append(f"movement_geometry_ambiguous:{connection_index}:{delta:.1f}deg")
        if not source_turn:
            blockers.append(f"movement_turn_label_unsupported:{connection_index}")
        elif source_turn != geometry_turn:
            blockers.append(
                f"movement_turn_geometry_mismatch:{connection_index}:{source_turn}:{geometry_turn}"
            )
        is_turnaround = source_turn == "t" or geometry_turn == "t"
        if is_turnaround:
            blockers.append(f"turnaround_movement_present:{connection_index}")
        # A U-turn may traverse a physically separated junction and therefore look
        # like a left or right turn when only the local arm vectors are compared.
        # SUMO's explicit ``dir=t`` must remain authoritative for this safety case;
        # otherwise the audit can assign a NEMA through/right phase to a U-turn.
        turn = (
            "t"
            if is_turnaround
            else source_turn
            if source_turn in {"l", "r", "s"}
            else geometry_turn
        )
        lane_key = (from_edge, connection.attrib.get("fromLane", ""))
        if turn:
            lane_turns[lane_key].add(turn)
        movement_pairs.add((str(from_arm["arm_id"]), str(to_arm["arm_id"])))
        phase_config = phase_by_arm.get(str(from_arm["arm_id"]), {})
        turn_semantics_agree = source_turn == geometry_turn and turn in {"l", "r", "s"}
        phase = (
            phase_config.get("left" if turn == "l" else "through_right")
            if turn_semantics_agree
            else None
        )
        movement_rows.append(
            {
                "connection_index": connection_index,
                "from": from_edge,
                "fromLane": connection.attrib.get("fromLane", ""),
                "to": to_edge,
                "toLane": connection.attrib.get("toLane", ""),
                "source_dir": source_turn,
                "geometry_dir": geometry_turn,
                "effective_dir": turn,
                "turn_angle_deg": round(delta, 3),
                "from_arm": from_arm["arm_id"],
                "to_arm": to_arm["arm_id"],
                "old_linkIndex": connection.attrib.get("linkIndex", ""),
                "nema_phase": phase,
                "new_linkIndex": phase - 1 if phase is not None else None,
                "nema_assignment_status": (
                    "assigned" if phase is not None else "blocked_turn_semantics"
                ),
            }
        )

    shared_left_lanes: set[tuple[str, str]] = set()
    for (edge_id, lane_index), turns in sorted(lane_turns.items()):
        if "l" in turns and turns - {"l"}:
            shared_left_lanes.add((edge_id, lane_index))
            blockers.append(
                f"protected_left_lane_not_dedicated:{edge_id}:{lane_index}:{','.join(sorted(turns))}"
            )
    for row in movement_rows:
        lane_key = (str(row["from"]), str(row["fromLane"]))
        if row.get("effective_dir") == "l" and lane_key in shared_left_lanes:
            row["nema_phase"] = None
            row["new_linkIndex"] = None
            row["nema_assignment_status"] = "blocked_shared_left_lane"

    arm_ids = {str(arm["arm_id"]) for arm in arms}
    for from_arm in sorted(arm_ids):
        for to_arm in sorted(arm_ids - {from_arm}):
            if (from_arm, to_arm) not in movement_pairs:
                blockers.append(f"required_arm_to_arm_movement_missing:{from_arm}:{to_arm}")

    used_phases = sorted(
        {
            int(row["nema_phase"])
            for row in movement_rows
            if isinstance(row.get("nema_phase"), int)
        }
    )
    if layout_type == "four_way" and used_phases != list(range(1, 9)):
        blockers.append(f"four_way_nema_phase_set_incomplete:{','.join(map(str, used_phases))}")
    if layout_type == "three_way" and not {2, 4, 6}.issubset(used_phases):
        blockers.append(f"three_way_required_nema_phases_missing:{','.join(map(str, used_phases))}")
    if layout_type == "three_way" and len({1, 5} & set(used_phases)) != 1:
        blockers.append(f"three_way_main_left_phase_count_not_one:{','.join(map(str, used_phases))}")
    compatibility = _nema_compatibility_contract(
        layout_type=layout_type,
        phase_by_arm=phase_by_arm,
        main_pair=main_pair,
        minor_pair=minor_pair,
        stem_arm=stem_arm,
    )
    blockers.extend(compatibility["blockers"])
    connection_mode = audit_standard_connection_mode(
        root,
        junction_id=junction_id,
        movement_rows=movement_rows,
        layout_type=layout_type,
        catalog=connection_mode_catalog,
    )
    blockers.extend(connection_mode["blockers"])
    warnings.extend(connection_mode["warnings"])

    params = _nema_params(layout_type, used_phases)
    return {
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": warnings,
        "layout_type": layout_type,
        "arm_count": arm_count,
        "incoming_edge_count": len(incoming_edge_ids),
        "outgoing_edge_count": len(outgoing_edge_ids),
        "arms": arms,
        "opposite_pairs": opposite_pairs,
        "main_pair": main_pair,
        "minor_pair": minor_pair,
        "stem_arm": stem_arm,
        "phase_by_arm": phase_by_arm,
        "movement_map": sorted(
            movement_rows,
            key=lambda row: (
                row["new_linkIndex"] if row["new_linkIndex"] is not None else 99,
                row["from"],
                row["fromLane"],
                row["to"],
            ),
        ),
        "used_nema_phases": used_phases,
        "nema_params": params,
        "state_length": max(used_phases, default=0),
        "mapping_policy": {
            "main_approach_ownership": "left/through-right phases 5/2 and 1/6",
            "minor_approach_ownership": "left/through-right phases 7/4 and 3/8",
            "left_turns": "odd protected phases",
            "through_and_right": "even phases",
            "main_street_through": "phases 2 and 6",
            "same_approach_pairing": "left and through/right use different rings (5+2, 1+6, 7+4, 3+8)",
            "opposing_left_pairing": "1+5 and 3+7",
            "three_way_missing_ring": "0 placeholders plus repeated phase 4 across the second barrier",
        },
        "nema_compatibility": compatibility,
        "connection_mode_audit": connection_mode,
        "junction_id": junction_id,
    }


def _nema_compatibility_contract(
    *,
    layout_type: str,
    phase_by_arm: Mapping[str, Mapping[str, int]],
    main_pair: list[str],
    minor_pair: list[str],
    stem_arm: str,
) -> dict[str, Any]:
    blockers: list[str] = []
    expected: dict[str, dict[str, int]] = {}
    if len(main_pair) == 2:
        expected.update(
            {
                main_pair[0]: {"left": 5, "through_right": 2},
                main_pair[1]: {"left": 1, "through_right": 6},
            }
        )
    if layout_type == "four_way" and len(minor_pair) == 2:
        expected.update(
            {
                minor_pair[0]: {"left": 7, "through_right": 4},
                minor_pair[1]: {"left": 3, "through_right": 8},
            }
        )
    if layout_type == "three_way" and stem_arm:
        expected[stem_arm] = {"left": 4, "through_right": 4}
    for arm_id, expected_phases in expected.items():
        actual = dict(phase_by_arm.get(arm_id, {}))
        if actual != expected_phases:
            blockers.append(f"nema_cross_ring_phase_ownership_mismatch:{arm_id}")
    return {
        "status": "pass" if not blockers else "fail",
        "blockers": blockers,
        "phase_ownership": {key: dict(value) for key, value in phase_by_arm.items()},
        "compatible_main_combinations": ["1+5", "1+6", "2+5", "2+6"],
        "compatible_minor_combinations": (
            ["3+7", "3+8", "4+7", "4+8"] if layout_type == "four_way" else ["4+4"]
        ),
        "safety_rule": "same-approach protected left and through/right are cross-ring; opposing throughs and opposing protected lefts may run concurrently",
    }


def _four_way_pairing(arms: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(arms) != 4:
        return None
    ids = [str(arm["arm_id"]) for arm in arms]
    by_id = {str(arm["arm_id"]): arm for arm in arms}
    pairings = (
        ((ids[0], ids[1]), (ids[2], ids[3])),
        ((ids[0], ids[2]), (ids[1], ids[3])),
        ((ids[0], ids[3]), (ids[1], ids[2])),
    )
    scored: list[tuple[float, tuple[tuple[str, str], tuple[str, str]], list[float]]] = []
    for pairs in pairings:
        errors = [
            abs(180.0 - _angle_distance(float(by_id[a]["angle_deg"]), float(by_id[b]["angle_deg"])))
            for a, b in pairs
        ]
        scored.append((sum(errors), pairs, errors))
    _, pairs, errors = min(scored, key=lambda item: (item[0], item[1]))
    return {"pairs": pairs, "errors": errors}


def _three_way_pairing(arms: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(arms) != 3:
        return None
    by_id = {str(arm["arm_id"]): arm for arm in arms}
    choices: list[tuple[float, tuple[str, str], str]] = []
    for first, second in itertools.combinations(sorted(by_id), 2):
        error = abs(
            180.0
            - _angle_distance(
                float(by_id[first]["angle_deg"]),
                float(by_id[second]["angle_deg"]),
            )
        )
        stem = next(iter(set(by_id) - {first, second}))
        choices.append((error, (first, second), stem))
    error, pair, stem = min(choices, key=lambda item: (item[0], item[1]))
    stem_angles = [
        _angle_distance(float(by_id[stem]["angle_deg"]), float(by_id[arm_id]["angle_deg"]))
        for arm_id in pair
    ]
    return {
        "main_pair": pair,
        "main_error": error,
        "stem": stem,
        "stem_angles": stem_angles,
    }


def _pair_score(pair: tuple[str, str], arms: list[dict[str, Any]]) -> tuple[int, int, float]:
    by_id = {str(arm["arm_id"]): arm for arm in arms}
    selected = [by_id[arm_id] for arm_id in pair]
    return (
        sum(int(arm["priority"]) for arm in selected),
        sum(int(arm["lane_count"]) for arm in selected),
        round(sum(float(arm["speed_mps"]) for arm in selected), 6),
    )


def _pair_token(pair: tuple[str, str]) -> str:
    return "|".join(sorted(pair))


def _ordered_pair(pair: tuple[str, str], arms: list[dict[str, Any]]) -> tuple[str, str]:
    by_id = {str(arm["arm_id"]): arm for arm in arms}
    ordered = sorted(
        pair,
        key=lambda arm_id: (
            round(float(by_id[arm_id]["unit_x"]), 9),
            round(float(by_id[arm_id]["unit_y"]), 9),
            str(by_id[arm_id]["incoming_edge"]),
        ),
    )
    return ordered[0], ordered[1]


def _nema_params(layout_type: str, used_phases: list[int]) -> dict[str, str]:
    base = {
        "detector-length": "20",
        "detector-length-leftTurnLane": "10",
        "total-cycle-length": "90",
    }
    if layout_type == "three_way":
        used = set(used_phases)
        base.update(
            {
                "ring1": f"{'1' if 1 in used else '0'},2,0,4",
                "ring2": f"{'5' if 5 in used else '0'},6,0,4",
                "barrierPhases": "2,6",
                "coordinate-mode": "false",
                "barrier2Phases": "4,4",
                "minRecall": "2,6",
                "maxRecall": "",
                "whetherOutputState": "true",
                "fixForceOff": "false",
            }
        )
    else:
        base.update(
            {
                "ring1": "1,2,3,4",
                "ring2": "5,6,7,8",
                "barrierPhases": "2,6",
                "coordinate-mode": "false",
                "barrier2Phases": "4,8",
                "minRecall": "2,6",
                "maxRecall": "",
                "whetherOutputState": "true",
                "fixForceOff": "false",
            }
        )
    return base


def _edge_arm_vector(
    edge: ET.Element | None,
    junction_position: tuple[float, float],
) -> tuple[float, float] | None:
    if edge is None:
        return None
    points: list[tuple[float, float]] = []
    for lane in edge.findall("lane"):
        points.extend(_shape_points(lane.attrib.get("shape", "")))
    if not points:
        points.extend(_shape_points(edge.attrib.get("shape", "")))
    if not points:
        return None
    outer = max(points, key=lambda point: _distance_sq(point, junction_position))
    dx = outer[0] - junction_position[0]
    dy = outer[1] - junction_position[1]
    length = math.hypot(dx, dy)
    if not math.isfinite(length) or length < 2.0:
        return None
    return dx / length, dy / length


def _shape_points(value: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for token in value.split():
        try:
            x_value, y_value = token.split(",", maxsplit=1)
            x = float(x_value)
            y = float(y_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            points.append((x, y))
    return points


def _junction_position(junction: ET.Element) -> tuple[float, float] | None:
    try:
        x = float(junction.attrib["x"])
        y = float(junction.attrib["y"])
    except (KeyError, ValueError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    return x, y


def _angle_deg(vector: tuple[float, float]) -> float:
    return math.degrees(math.atan2(vector[1], vector[0]))


def _angle_distance(first: float, second: float) -> float:
    return abs(_normalize_signed_angle(first - second))


def _normalize_signed_angle(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def _turn_from_delta(delta: float) -> str:
    magnitude = abs(delta)
    if magnitude <= 35.0:
        return "s"
    if 45.0 <= magnitude <= 135.0:
        return "l" if delta > 0 else "r"
    if magnitude >= 145.0:
        return "t"
    return "ambiguous"


def _distance_sq(first: tuple[float, float], second: tuple[float, float]) -> float:
    return (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2


def _edge_speed(edge: ET.Element | None) -> float:
    if edge is None:
        return 0.0
    speeds = [_safe_float(lane.attrib.get("speed", ""), default=0.0) for lane in edge.findall("lane")]
    return max(speeds, default=_safe_float(edge.attrib.get("speed", ""), default=0.0))


def _edge_lane(edge: ET.Element | None, lane_index: str) -> ET.Element | None:
    if edge is None:
        return None
    try:
        index = int(lane_index)
    except ValueError:
        return None
    lanes = edge.findall("lane")
    if not 0 <= index < len(lanes):
        return None
    return lanes[index]


def _is_direct_external_connection(
    connection: ET.Element,
    edges: Mapping[str, ET.Element],
) -> bool:
    from_id = connection.attrib.get("from", "")
    to_id = connection.attrib.get("to", "")
    if from_id.startswith(":") or to_id.startswith(":"):
        return False
    from_edge = edges.get(from_id)
    to_edge = edges.get(to_id)
    if from_edge is None or to_edge is None:
        return False
    return (
        from_edge.attrib.get("function", "") not in _PEDESTRIAN_EDGE_FUNCTIONS
        and to_edge.attrib.get("function", "") not in _PEDESTRIAN_EDGE_FUNCTIONS
    )


def _safe_int(value: str, *, default: int) -> int:
    try:
        return int(value)
    except ValueError:
        return default


def _safe_float(value: str, *, default: float) -> float:
    try:
        number = float(value)
    except ValueError:
        return default
    return number if math.isfinite(number) else default


def _binding_operations(
    catalog: Mapping[str, Any],
    selected: Mapping[str, Any],
) -> list[dict[str, Any]]:
    connections: list[ET.Element] = catalog["connections"]
    operations: list[dict[str, Any]] = []
    for ordinal, movement in enumerate(selected.get("movement_map", []), start=1):
        connection_index = int(movement["connection_index"])
        connection = connections[connection_index]
        before_link_index = connection.attrib.get("linkIndex", "")
        new_link_index = str(int(movement["new_linkIndex"]))
        operations.append(
            {
                "operation_id": f"nema-link-binding-{ordinal:04d}",
                "operation_type": "replace_tls_link_index",
                "connection_index": connection_index,
                "connection_locator": {
                    key: connection.attrib.get(key, "")
                    for key in ("from", "to", "fromLane", "toLane", "via", "dir", "tl")
                },
                "before_attributes": dict(connection.attrib),
                "before_link_index": before_link_index,
                "after_link_index": new_link_index,
                "nema_phase": int(movement["nema_phase"]),
                "reason": (
                    "bind the verified movement to its canonical NEMA movement phase; "
                    "linkIndex is phase minus one"
                ),
                "rollback": {
                    "action": "restore_linkIndex",
                    "value": before_link_index,
                },
            }
        )
    return operations


def _build_nema_tllogic(selected: Mapping[str, Any]) -> ET.Element:
    controller_id = str(selected["controller_id"])
    existing_program = selected.get("existing_program", {})
    offset = "0"
    if isinstance(existing_program, Mapping):
        offset = str(existing_program.get("offset", "0") or "0")
    logic = ET.Element(
        "tlLogic",
        {
            "id": controller_id,
            "type": "NEMA",
            "programID": "Torii_NEMA_90",
            "offset": offset,
        },
    )
    for key, value in selected.get("nema_params", {}).items():
        ET.SubElement(logic, "param", {"key": str(key), "value": str(value)})
    state_length = int(selected.get("state_length", 0))
    for phase_number in selected.get("used_nema_phases", []):
        phase = int(phase_number)
        state = ["r"] * state_length
        state[phase - 1] = "G"
        ET.SubElement(
            logic,
            "phase",
            {
                "duration": "99",
                "minDur": "5",
                "maxDur": "20" if phase in _LEFT_PHASES else "35",
                "vehext": "2",
                "yellow": "3",
                "red": "1",
                "name": str(phase),
                "state": "".join(state),
            },
        )
    ET.indent(logic, space="    ")
    return logic


def _patch_network_text(
    *,
    source_text: str,
    root: ET.Element,
    operations: list[dict[str, Any]],
    controller_id: str,
    replacement_logic: ET.Element,
) -> tuple[str | None, dict[str, Any]]:
    connection_patched, connection_evidence = _patch_connection_link_indices(
        source_text,
        parsed_connections=root.findall("connection"),
        operations=operations,
    )
    if connection_patched is None:
        return None, connection_evidence
    logic_patched, logic_evidence = _patch_tllogic_block(
        connection_patched,
        parsed_logics=root.findall("tlLogic"),
        controller_id=controller_id,
        replacement_logic=replacement_logic,
    )
    if logic_patched is None:
        return None, logic_evidence
    return logic_patched, {
        "status": "pass",
        "all_other_source_text_preserved": True,
        "connections": connection_evidence,
        "tllogic": logic_evidence,
    }


def _patch_connection_link_indices(
    source_text: str,
    *,
    parsed_connections: list[ET.Element],
    operations: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any]]:
    matches = list(_CONNECTION_TAG.finditer(source_text))
    if len(matches) != len(parsed_connections):
        return None, {
            "status": "blocked",
            "error": (
                "minimal text patch could not bind parsed connections to self-closing tags: "
                f"parsed={len(parsed_connections)}, tags={len(matches)}"
            ),
        }
    targets = {int(operation["connection_index"]): operation for operation in operations}
    chunks: list[str] = []
    cursor = 0
    changed_count = 0
    identity_count = 0
    for connection_index, match in enumerate(matches):
        chunks.append(source_text[cursor : match.start()])
        tag_text = match.group(0)
        operation = targets.get(connection_index)
        if operation is not None:
            try:
                parsed_tag = ET.fromstring(tag_text)
            except ET.ParseError as exc:
                return None, {
                    "status": "blocked",
                    "error": f"connection tag {connection_index} could not be parsed: {exc}",
                }
            if dict(parsed_tag.attrib) != operation["before_attributes"]:
                return None, {
                    "status": "blocked",
                    "error": f"connection tag {connection_index} did not match parsed source attributes",
                }
            new_value = str(operation["after_link_index"])
            if parsed_tag.attrib.get("linkIndex") == new_value:
                identity_count += 1
            else:
                attribute_pattern = re.compile(
                    r"(?P<prefix>\s+linkIndex\s*=\s*)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)"
                )

                def replace_value(match_value: re.Match[str]) -> str:
                    return f"{match_value.group('prefix')}{match_value.group('quote')}{new_value}{match_value.group('quote')}"

                tag_text, count = attribute_pattern.subn(replace_value, tag_text, count=1)
                if count != 1:
                    return None, {
                        "status": "blocked",
                        "error": f"linkIndex on connection tag {connection_index} could not be replaced exactly once",
                    }
                changed_count += 1
        chunks.append(tag_text)
        cursor = match.end()
    chunks.append(source_text[cursor:])
    return "".join(chunks), {
        "status": "pass",
        "parsed_connection_count": len(parsed_connections),
        "bound_connection_count": len(operations),
        "changed_connection_tag_count": changed_count,
        "already_canonical_connection_count": identity_count,
    }


def _patch_tllogic_block(
    source_text: str,
    *,
    parsed_logics: list[ET.Element],
    controller_id: str,
    replacement_logic: ET.Element,
) -> tuple[str | None, dict[str, Any]]:
    matches = list(_TLLOGIC_BLOCK.finditer(source_text))
    if len(matches) != len(parsed_logics):
        return None, {
            "status": "blocked",
            "error": (
                "minimal text patch could not bind parsed tlLogic elements to XML blocks: "
                f"parsed={len(parsed_logics)}, blocks={len(matches)}"
            ),
        }
    target_indices = [
        index for index, logic in enumerate(parsed_logics) if logic.attrib.get("id") == controller_id
    ]
    if len(target_indices) != 1:
        return None, {
            "status": "blocked",
            "error": f"controller {controller_id} has {len(target_indices)} tlLogic blocks, expected exactly one",
        }
    target_index = target_indices[0]
    match = matches[target_index]
    try:
        parsed_block = ET.fromstring(match.group(0))
    except ET.ParseError as exc:
        return None, {"status": "blocked", "error": f"target tlLogic block could not be parsed: {exc}"}
    if _element_digest(parsed_block) != _element_digest(parsed_logics[target_index]):
        return None, {
            "status": "blocked",
            "error": "target tlLogic text block did not match the parsed source element",
        }
    line_start = source_text.rfind("\n", 0, match.start()) + 1
    leading = source_text[line_start : match.start()]
    indentation = leading if not leading.strip() else ""
    replacement = ET.tostring(replacement_logic, encoding="unicode")
    if indentation:
        replacement = replacement.replace("\n", f"\n{indentation}")
    patched = source_text[: match.start()] + replacement + source_text[match.end() :]
    return patched, {
        "status": "pass",
        "controller_id": controller_id,
        "replaced_tllogic_count": 1,
        "before_sha256": _element_digest(parsed_block),
        "after_sha256": _element_digest(replacement_logic),
    }


def _validate_candidate(
    *,
    source_root: ET.Element,
    candidate_file: Path,
    selected: Mapping[str, Any],
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        candidate_root = ET.parse(candidate_file).getroot()
    except (OSError, ET.ParseError) as exc:
        return {"status": "fail", "failures": [f"candidate_parse_failed:{type(exc).__name__}:{exc}"]}

    failures: list[str] = []
    source_connections = source_root.findall("connection")
    candidate_connections = candidate_root.findall("connection")
    targets = {int(operation["connection_index"]): operation for operation in operations}
    if len(source_connections) != len(candidate_connections):
        failures.append("connection_count_changed")
    else:
        for index, (before, after) in enumerate(zip(source_connections, candidate_connections)):
            expected = dict(before.attrib)
            if index in targets:
                expected["linkIndex"] = str(targets[index]["after_link_index"])
            if dict(after.attrib) != expected:
                failures.append(f"connection_attributes_changed_unexpectedly:{index}")

    source_children = list(source_root)
    candidate_children = list(candidate_root)
    if len(source_children) != len(candidate_children):
        failures.append("top_level_element_count_changed")
    else:
        for index, (before, after) in enumerate(zip(source_children, candidate_children)):
            if before.tag == "connection":
                continue
            if before.tag == "tlLogic" and before.attrib.get("id") == selected.get("controller_id"):
                continue
            if _element_digest(before) != _element_digest(after):
                failures.append(f"non_target_element_changed:{index}:{before.tag}")

    controller_id = str(selected["controller_id"])
    candidate_logics = candidate_root.findall(f"tlLogic[@id='{controller_id}']")
    expected_logic = _build_nema_tllogic(selected)
    if len(candidate_logics) != 1:
        failures.append(f"candidate_tllogic_count_not_one:{len(candidate_logics)}")
    elif _element_digest(candidate_logics[0]) != _element_digest(expected_logic):
        failures.append("candidate_nema_tllogic_mismatch")

    candidate_catalog = _build_catalog(candidate_root)
    post = _classify_standard_junction(candidate_catalog, str(selected["junction_id"]))
    if post["eligibility_status"] != "eligible":
        failures.extend(f"post_binding:{blocker}" for blocker in post["blockers"])
    expected_bindings = {
        int(operation["connection_index"]): int(operation["after_link_index"])
        for operation in operations
    }
    actual_bindings = {
        index: int(candidate_connections[index].attrib["linkIndex"])
        for index in expected_bindings
        if index < len(candidate_connections) and candidate_connections[index].attrib.get("linkIndex", "").isdigit()
    }
    if actual_bindings != expected_bindings:
        failures.append("candidate_link_index_binding_map_mismatch")
    verified_binding_count = sum(
        actual_bindings.get(index) == expected
        for index, expected in expected_bindings.items()
    )
    return {
        "status": "pass" if not failures else "fail",
        "failures": list(dict.fromkeys(failures)),
        "connection_count_preservation_status": (
            "pass" if len(source_connections) == len(candidate_connections) else "fail"
        ),
        "non_target_element_preservation_status": (
            "pass" if not any(item.startswith("non_target_element_changed") for item in failures) else "fail"
        ),
        "post_binding_eligibility_status": post["eligibility_status"],
        "verified_binding_count": verified_binding_count,
        "expected_binding_count": len(expected_bindings),
        "candidate_tllogic_sha256": (
            _element_digest(candidate_logics[0]) if len(candidate_logics) == 1 else ""
        ),
    }


def _element_digest(element: ET.Element) -> str:
    digest = hashlib.sha256()

    def visit(node: ET.Element) -> None:
        digest.update(node.tag.encode("utf-8"))
        digest.update(b"\0")
        for key, value in sorted(node.attrib.items()):
            digest.update(key.encode("utf-8"))
            digest.update(b"=")
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
        text = (node.text or "").strip()
        if text:
            digest.update(b"text=")
            digest.update(text.encode("utf-8"))
            digest.update(b"\0")
        for child in node:
            visit(child)
        digest.update(b"\xff")

    visit(element)
    return digest.hexdigest()


def _element_payload(element: ET.Element) -> dict[str, Any]:
    return {
        "attributes": dict(element.attrib),
        "params": [dict(child.attrib) for child in element.findall("param")],
        "phases": [dict(child.attrib) for child in element.findall("phase")],
        "xml": ET.tostring(element, encoding="unicode"),
    }


def _run_runtime_validation(
    *,
    candidate_file: Path,
    selected: Mapping[str, Any],
    operations: list[dict[str, Any]],
    paths: Mapping[str, Path],
    run_routeability: bool,
    routeability_vehicle_count: int,
    netconvert_binary: str | None,
    sumo_binary: str | None,
    random_trips_script: str | None,
    timeout_seconds: float,
    command_runner: CommandRunner,
    routeability_runner: RouteabilityRunner,
) -> dict[str, Any]:
    discovered = discover_binaries()
    binaries = {
        **discovered,
        "netconvert": netconvert_binary or discovered.get("netconvert"),
        "sumo": sumo_binary or discovered.get("sumo"),
        "randomTrips": random_trips_script or discovered.get("randomTrips"),
    }
    missing = [name for name in ("netconvert", "sumo") if not binaries.get(name)]
    if run_routeability and not binaries.get("randomTrips"):
        missing.append("randomTrips")
    if missing:
        return {
            "status": "blocked",
            "netconvert_status": "blocked",
            "netconvert_semantic_status": "blocked",
            "sumo_load_status": "blocked",
            "routeability_status": "blocked" if run_routeability else "skipped",
            "warnings": [f"missing required SUMO tool: {name}" for name in missing],
        }

    netconvert_result = _result_to_dict(
        command_runner(
            [
                str(binaries["netconvert"]),
                "-s",
                str(candidate_file),
                "-o",
                str(paths["validated_file"]),
            ],
            cwd=paths["output_dir"],
            timeout_seconds=timeout_seconds,
        )
    )
    netconvert_pass = (
        netconvert_result.get("status") == "pass"
        and netconvert_result.get("returncode") == 0
        and paths["validated_file"].is_file()
    )
    if not netconvert_pass:
        write_json_atomic(paths["netconvert_report_file"], netconvert_result, sort_keys=True)
        return {
            "status": "fail",
            "netconvert_status": "fail",
            "netconvert_semantic_status": "blocked",
            "sumo_load_status": "blocked",
            "routeability_status": "blocked",
            "netconvert": netconvert_result,
            "warnings": ["netconvert did not accept the NEMA candidate"],
        }

    roundtrip_semantics = _validate_netconvert_roundtrip(
        validated_file=paths["validated_file"],
        selected=selected,
        operations=operations,
    )
    netconvert_result["roundtrip_semantic_validation"] = roundtrip_semantics
    write_json_atomic(paths["netconvert_report_file"], netconvert_result, sort_keys=True)
    if roundtrip_semantics["status"] != "pass":
        return {
            "status": "fail",
            "netconvert_status": "pass",
            "netconvert_semantic_status": "fail",
            "sumo_load_status": "blocked",
            "routeability_status": "blocked",
            "netconvert": netconvert_result,
            "roundtrip_semantic_validation": roundtrip_semantics,
            "warnings": ["netconvert changed the proposed NEMA signal-group semantics"],
        }

    sumo_result = _result_to_dict(
        command_runner(
            [
                str(binaries["sumo"]),
                "-n",
                str(paths["validated_file"]),
                "--begin",
                "0",
                "--end",
                "0",
                "--no-step-log",
                "true",
                "--duration-log.disable",
                "true",
            ],
            cwd=paths["output_dir"],
            timeout_seconds=timeout_seconds,
        )
    )
    write_json_atomic(paths["sumo_load_report_file"], sumo_result, sort_keys=True)
    sumo_pass = sumo_result.get("status") == "pass" and sumo_result.get("returncode") == 0
    if not sumo_pass:
        return {
            "status": "fail",
            "netconvert_status": "pass",
            "netconvert_semantic_status": "pass",
            "sumo_load_status": "fail",
            "routeability_status": "blocked",
            "netconvert": netconvert_result,
            "roundtrip_semantic_validation": roundtrip_semantics,
            "sumo_load": sumo_result,
            "warnings": ["SUMO could not load the netconvert-validated NEMA candidate"],
        }

    routeability: dict[str, Any] = {
        "status": "skipped",
        "routeability_status": "skipped",
        "warnings": [],
    }
    if run_routeability:
        try:
            routeability = routeability_runner(
                net_file=paths["validated_file"],
                output_dir=paths["routeability_dir"],
                prefix="nema_candidate",
                vehicle_count=routeability_vehicle_count,
                seed=42,
                initial_end=300,
                max_end=2400,
                timeout_seconds=timeout_seconds,
                binaries={
                    "randomTrips": binaries.get("randomTrips"),
                    "sumo": binaries.get("sumo"),
                },
                command_runner=command_runner,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            routeability = {
                "status": "fail",
                "routeability_status": "runner_exception",
                "warnings": [f"{type(exc).__name__}: {exc}"],
            }
    routeability_pass = not run_routeability or (
        routeability.get("status") == "pass" and routeability.get("routeability_status") == "pass"
    )
    return {
        "status": "pass" if routeability_pass else "fail",
        "netconvert_status": "pass",
        "netconvert_semantic_status": "pass",
        "sumo_load_status": "pass",
        "routeability_status": (
            str(routeability.get("routeability_status", "fail")) if run_routeability else "skipped"
        ),
        "validated_net_file": str(paths["validated_file"]),
        "validated_net_sha256": file_sha256(paths["validated_file"]),
        "netconvert": netconvert_result,
        "roundtrip_semantic_validation": roundtrip_semantics,
        "sumo_load": sumo_result,
        "routeability": routeability,
        "warnings": list(routeability.get("warnings", [])) if not routeability_pass else [],
    }


def _validate_netconvert_roundtrip(
    *,
    validated_file: Path,
    selected: Mapping[str, Any],
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        root = ET.parse(validated_file).getroot()
    except (OSError, ET.ParseError) as exc:
        return {"status": "fail", "failures": [f"roundtrip_parse_failed:{type(exc).__name__}:{exc}"]}
    failures: list[str] = []
    controller_id = str(selected["controller_id"])
    logics = root.findall(f"tlLogic[@id='{controller_id}']")
    expected_logic = _build_nema_tllogic(selected)
    if len(logics) != 1:
        failures.append(f"roundtrip_tllogic_count_not_one:{len(logics)}")
    elif _nema_logic_semantics(logics[0]) != _nema_logic_semantics(expected_logic):
        failures.append("roundtrip_nema_tllogic_mismatch")

    connections = root.findall("connection")
    verified = 0
    for operation in operations:
        locator = operation["connection_locator"]
        core_keys = ("from", "to", "fromLane", "toLane", "dir", "tl")
        matches = [
            connection
            for connection in connections
            if all(connection.attrib.get(key, "") == str(locator.get(key, "")) for key in core_keys)
        ]
        via = str(locator.get("via", ""))
        if len(matches) > 1 and via:
            matches = [connection for connection in matches if connection.attrib.get("via", "") == via]
        if len(matches) != 1:
            failures.append(
                f"roundtrip_movement_locator_count_not_one:{operation['operation_id']}:{len(matches)}"
            )
            continue
        if matches[0].attrib.get("linkIndex", "") != str(operation["after_link_index"]):
            failures.append(f"roundtrip_link_index_mismatch:{operation['operation_id']}")
            continue
        verified += 1

    catalog = _build_catalog(root)
    post = _classify_standard_junction(catalog, str(selected["junction_id"]))
    if post["eligibility_status"] != "eligible":
        failures.extend(f"roundtrip_post_binding:{blocker}" for blocker in post["blockers"])
    return {
        "status": "pass" if not failures else "fail",
        "failures": list(dict.fromkeys(failures)),
        "verified_binding_count": verified,
        "expected_binding_count": len(operations),
        "post_binding_eligibility_status": post["eligibility_status"],
        "validated_net_file": str(validated_file),
        "validated_net_sha256": file_sha256(validated_file),
    }


def _nema_logic_semantics(logic: ET.Element) -> dict[str, Any]:
    return {
        "attributes": dict(logic.attrib),
        "params": {
            param.attrib.get("key", ""): param.attrib.get("value", "")
            for param in logic.findall("param")
        },
        "phases": [dict(phase.attrib) for phase in logic.findall("phase")],
        "unexpected_child_tags": sorted(
            child.tag for child in logic if child.tag not in {"param", "phase"}
        ),
    }


def _result_to_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        return dict(result.to_dict())
    if isinstance(result, Mapping):
        return dict(result)
    return {
        "status": "fail",
        "returncode": None,
        "error": f"unexpected command result type: {type(result).__name__}",
    }


def _not_run_runtime(reason: str) -> dict[str, Any]:
    return {
        "status": "not_run",
        "netconvert_status": "not_run",
        "netconvert_semantic_status": "not_run",
        "sumo_load_status": "not_run",
        "routeability_status": "not_run",
        "warnings": [reason],
    }


def _scan_counts(records: list[Mapping[str, Any]]) -> dict[str, int]:
    return {
        "traffic_light_junction_count": len(records),
        "eligible_count": sum(record.get("eligibility_status") == "eligible" for record in records),
        "review_required_count": sum(
            record.get("eligibility_status") != "eligible" for record in records
        ),
        "three_way_count": sum(record.get("layout_type") == "three_way" for record in records),
        "four_way_count": sum(record.get("layout_type") == "four_way" for record in records),
    }


def _base_plan(
    *,
    source: Path,
    source_sha256: str,
    requested_junction: str,
    records: list[dict[str, Any]],
    counts: Mapping[str, int],
    traffic_side: str,
) -> dict[str, Any]:
    return {
        "schema": "torii.standard_nema_binding_plan.v1",
        "status": "planning",
        "claim_status": "diagnostic-demo",
        "source_net_file": str(source),
        "source_sha256": source_sha256,
        "source_network_mutation": False,
        "requested_junction_id": requested_junction,
        "scan_counts": dict(counts),
        "network_traffic_side": traffic_side,
        "eligibility_policy": {
            "traffic_side": "right_hand_only_certified; left-hand networks fail closed",
            "junction_types": sorted(_SUPPORTED_JUNCTION_TYPES),
            "arm_counts": [3, 4],
            "controller_scope": "one explicit program controlling exactly one physical junction",
            "modes": "motorized direct movements only; pedestrian, bicycle-only, and rail control block automation",
            "turnarounds": "blocked",
            "linkIndex2": "blocked",
            "turn_semantics": "SUMO dir must agree with lane geometry",
            "left_turns": "protected left requires a dedicated incoming lane",
            "movement_completeness": "every non-U-turn arm-to-arm movement must exist",
            "connection_mode": (
                "hard gate on fromLane-toLane-via continuity, explicit traffic-side lane roles, "
                "request/foe integrity, and every concurrently serviceable NEMA movement pair"
            ),
            "promotion": "human review required even after all runtime gates pass",
        },
        "nema_policy": {
            "odd_phases": "protected left turns",
            "even_phases": "through and right turns",
            "main_street": "cross-ring approach ownership 5/2 and 1/6",
            "minor_street": "cross-ring approach ownership 7/4 and 3/8",
            "three_way_missing_phases": "0 placeholders and repeated phase 4",
            "generic_cycle_seconds": 90,
            "field_calibrated": False,
        },
        "candidates": records,
        "operations": [],
        "rollback": {
            "strategy": "discard candidate and retain source",
            "source_network_immutable": True,
        },
        "warnings": [],
    }


def _base_report(
    *,
    source: Path,
    source_sha256: str,
    paths: Mapping[str, Path],
    records: list[dict[str, Any]],
    counts: Mapping[str, int],
    requested_junction: str,
) -> dict[str, Any]:
    return {
        "schema": "torii.standard_nema_binding_report.v1",
        "status": "blocked",
        "claim_status": "blocked",
        "nema_binding_status": "blocked",
        "source_net_file": str(source),
        "source_sha256": source_sha256,
        "source_network_mutation": False,
        "requested_junction_id": requested_junction,
        "scan_counts": dict(counts),
        "candidates": records,
        "candidate_net_file": "",
        "validated_net_file": "",
        "plan_file": str(paths["plan_file"]),
        "report_file": str(paths["report_file"]),
        "manifest_file": str(paths["manifest_file"]),
        "review_overlay_file": str(paths["overlay_file"]),
        "review_html_file": str(paths["review_html_file"]),
        "review_decision_file": str(paths["decision_file"]),
        "connection_mode_report_file": str(paths["connection_mode_report_file"]),
    }


def _decision_template(
    *,
    source: Path,
    source_sha256: str,
    selected: Mapping[str, Any] | None,
    candidate_file: Path | None,
    candidate_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": "torii.standard_nema_review_decision.v1",
        "status": "select_target" if selected is None else "pending_human_review",
        "review_required": selected is not None,
        "junction_id": str(selected.get("junction_id", "")) if selected else "",
        "controller_id": str(selected.get("controller_id", "")) if selected else "",
        "source_net_file": str(source),
        "source_sha256": source_sha256,
        "candidate_net_file": str(candidate_file) if candidate_file else "",
        "candidate_sha256": candidate_sha256,
        "decision": "",
        "allowed_decisions": ["accept_candidate", "reject_candidate", "request_changes"],
        "review_checks": {
            "movement_to_phase_table_matches_visible_lane arrows": None,
            "protected_left_lanes_are_physically_dedicated": None,
            "pedestrian_and_bicycle_control_is_not_required_or_is_separately_modeled": None,
            "yellow_red_and_min_max_green_are_locally_appropriate": None,
            "main_street_selection_matches_map_and_field_evidence": None,
            "netedit_connection_mode_review_completed": None,
        },
        "reviewer": "",
        "reviewed_at": "",
        "notes": "",
    }


def _write_review_overlay(
    path: Path,
    records: list[Mapping[str, Any]],
    *,
    source_sha256: str,
) -> None:
    root = ET.Element("additional")
    for record in records:
        position = record.get("junction_position", {})
        status = str(record.get("eligibility_status", "review_required"))
        color = "0,180,0" if status == "eligible" else "255,165,0"
        junction_id = str(record.get("junction_id", ""))
        poi = ET.SubElement(
            root,
            "poi",
            {
                "id": f"torii_nema_review_{junction_id}",
                "type": f"torii.review.standard_nema.{status}",
                "color": color,
                "layer": "1000",
                "x": str(position.get("x", 0.0)),
                "y": str(position.get("y", 0.0)),
                "name": f"NEMA review {junction_id}: {status}",
            },
        )
        values = (
            ("display_only", "true"),
            ("junction_id", junction_id),
            ("controller_id", record.get("controller_id", "")),
            ("layout_type", record.get("layout_type", "unknown")),
            ("eligibility_status", status),
            (
                "connection_mode_status",
                record.get("connection_mode_audit", {}).get("status", "not_run"),
            ),
            ("blockers", "; ".join(map(str, record.get("blockers", [])))),
            ("source_sha256", source_sha256),
            ("operational_change", "separate candidate .net.xml only"),
        )
        for key, value in values:
            ET.SubElement(poi, "param", {"key": str(key), "value": str(value)})
    ET.indent(root, space="    ")
    write_text_atomic(
        path,
        ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8"),
    )


def _write_connection_mode_report(
    path: Path,
    *,
    source: Path,
    source_sha256: str,
    records: Sequence[Mapping[str, Any]],
) -> None:
    junctions = [
        {
            "junction_id": str(record.get("junction_id", "")),
            "controller_id": str(record.get("controller_id", "")),
            "layout_type": str(record.get("layout_type", "unknown")),
            "eligibility_status": str(record.get("eligibility_status", "review_required")),
            "connection_mode_audit": dict(record.get("connection_mode_audit", {})),
        }
        for record in records
    ]
    statuses = Counter(
        str(item["connection_mode_audit"].get("status", "not_run"))
        for item in junctions
    )
    status = (
        "fail"
        if statuses["fail"]
        else "review_required"
        if statuses["review_required"] or statuses["not_run"] or not junctions
        else "pass"
    )
    write_json_atomic(
        path,
        {
            "schema": "torii.standard_nema_connection_mode_report.v1",
            "status": status,
            "source_net_file": str(source),
            "source_sha256": source_sha256,
            "junction_count": len(junctions),
            "pass_count": statuses["pass"],
            "review_required_count": statuses["review_required"],
            "fail_count": statuses["fail"],
            "not_run_count": statuses["not_run"],
            "direct_movement_count": sum(
                item["connection_mode_audit"].get("direct_movement_count", 0)
                for item in junctions
            ),
            "verified_internal_path_count": sum(
                item["connection_mode_audit"].get("verified_internal_path_count", 0)
                for item in junctions
            ),
            "structural_failure_count": sum(
                len(item["connection_mode_audit"].get("structural_failures", []))
                for item in junctions
            ),
            "junctions": junctions,
        },
        sort_keys=True,
    )


def _persist_review_package(
    report: dict[str, Any],
    decision: Mapping[str, Any],
    paths: Mapping[str, Path],
    *,
    source: Path,
) -> None:
    write_json_atomic(paths["report_file"], report, sort_keys=True)
    _write_review_html(paths["review_html_file"], report, decision)
    _write_manifest(
        paths["manifest_file"],
        status=str(report.get("status", "blocked")),
        source=source if source.is_file() else None,
        output_dir=paths["output_dir"],
    )


def _write_review_html(
    path: Path,
    report: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> None:
    rows: list[str] = []
    for record in report.get("candidates", []):
        if not isinstance(record, Mapping):
            continue
        blockers = "; ".join(map(str, record.get("blockers", []))) or "—"
        connection_mode_status = str(
            record.get("connection_mode_audit", {}).get("status", "not_run")
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(record.get('junction_id', '')))}</td>"
            f"<td>{html.escape(str(record.get('controller_id', '')))}</td>"
            f"<td>{html.escape(str(record.get('layout_type', 'unknown')))}</td>"
            f"<td>{html.escape(str(record.get('eligibility_status', '')))}</td>"
            f"<td>{html.escape(connection_mode_status)}</td>"
            f"<td>{html.escape(blockers)}</td>"
            "</tr>"
        )
    selected = report.get("selected_candidate")
    movement_rows: list[str] = []
    if isinstance(selected, Mapping):
        connection_checks = {
            int(check.get("connection_index", -1)): check
            for check in selected.get("connection_mode_audit", {}).get("movement_checks", [])
            if isinstance(check, Mapping)
        }
        for movement in selected.get("movement_map", []):
            if not isinstance(movement, Mapping):
                continue
            check = connection_checks.get(int(movement.get("connection_index", -1)), {})
            internal_path = check.get("internal_path", {}) if isinstance(check, Mapping) else {}
            via_chain = " → ".join(map(str, internal_path.get("internal_lane_chain", []))) or "—"
            movement_rows.append(
                "<tr>"
                f"<td>{html.escape(str(movement.get('from', '')))}:{html.escape(str(movement.get('fromLane', '')))}</td>"
                f"<td>{html.escape(str(movement.get('to', '')))}:{html.escape(str(movement.get('toLane', '')))}</td>"
                f"<td>{html.escape(str(movement.get('geometry_dir', '')))}</td>"
                f"<td>{html.escape(str(movement.get('nema_phase', '')))}</td>"
                f"<td>{html.escape(str(movement.get('old_linkIndex', '')))}</td>"
                f"<td>{html.escape(str(movement.get('new_linkIndex', '')))}</td>"
                f"<td>{html.escape(via_chain)}</td>"
                f"<td>{html.escape(str(check.get('status', 'not_run')))}</td>"
                "</tr>"
            )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Torii standard NEMA review</title>
  <style>
    body {{ font: 15px/1.45 system-ui, sans-serif; margin: 2rem; color: #172033; }}
    h1, h2 {{ line-height: 1.2; }}
    .notice {{ padding: 1rem; border-left: 5px solid #e89018; background: #fff7e8; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0 2rem; }}
    th, td {{ border: 1px solid #ccd3df; padding: .55rem; text-align: left; vertical-align: top; }}
    th {{ background: #edf2f7; }}
    code {{ background: #edf2f7; padding: .1rem .25rem; }}
  </style>
</head>
<body>
  <h1>Standard three/four-way NEMA review</h1>
  <p class="notice"><strong>Review layer only.</strong> The additional.xml contains POIs and metadata; it does not change TLS behavior. The operational proposal, when eligible, is a separate candidate network and still requires human review.</p>
  <p>Status: <code>{html.escape(str(report.get('nema_binding_status', '')))}</code>; promotion: <code>{html.escape(str(report.get('promotion_status', '')))}</code>.</p>
  <h2>Junction queue</h2>
  <table><thead><tr><th>Junction</th><th>Controller</th><th>Layout</th><th>Status</th><th>Connection Mode</th><th>Blockers</th></tr></thead>
  <tbody>{''.join(rows) or '<tr><td colspan="6">No TLS junctions found.</td></tr>'}</tbody></table>
  <h2>Selected movement binding</h2>
  <table><thead><tr><th>From lane</th><th>To lane</th><th>Turn</th><th>NEMA phase</th><th>Old linkIndex</th><th>New linkIndex</th><th>Internal via chain</th><th>Connection audit</th></tr></thead>
  <tbody>{''.join(movement_rows) or '<tr><td colspan="8">Select an eligible junction to materialize a candidate.</td></tr>'}</tbody></table>
  <h2>Decision contract</h2>
  <p>Decision status: <code>{html.escape(str(decision.get('status', '')))}</code>. Generic 90-second timing is a starting point, not a field-calibrated plan.</p>
</body>
</html>
"""
    write_text_atomic(path, document)


def _write_manifest(
    path: Path,
    *,
    status: str,
    source: Path | None,
    output_dir: Path,
) -> None:
    artifacts: list[dict[str, Any]] = []
    if source is not None and source.is_file():
        artifacts.append(_artifact_record(source, role="source_input"))
    for artifact in sorted(output_dir.rglob("*")):
        if not artifact.is_file() or artifact.resolve() == path.resolve():
            continue
        artifacts.append(_artifact_record(artifact, role="generated"))
    write_json_atomic(
        path,
        {
            "schema": "torii.standard_nema_binding_manifest.v1",
            "status": status,
            "source_overwrite_forbidden": True,
            "review_overlay_display_only": True,
            "artifacts": artifacts,
        },
        sort_keys=True,
    )


def _artifact_record(path: Path, *, role: str) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "role": role,
        "size_bytes": resolved.stat().st_size,
        "sha256": file_sha256(resolved),
    }


def _persist_input_failure(
    *,
    source: Path,
    paths: Mapping[str, Path],
    error: str,
    requested_junction: str,
    source_sha256: str = "",
) -> dict[str, Any]:
    plan = {
        "schema": "torii.standard_nema_binding_plan.v1",
        "status": "blocked",
        "claim_status": "construction-invalid",
        "source_net_file": str(source),
        "source_sha256": source_sha256,
        "requested_junction_id": requested_junction,
        "source_network_mutation": False,
        "candidates": [],
        "operations": [],
        "error": error,
    }
    write_json_atomic(paths["plan_file"], plan, sort_keys=True)
    _write_review_overlay(paths["overlay_file"], [], source_sha256=source_sha256)
    decision = _decision_template(
        source=source,
        source_sha256=source_sha256,
        selected=None,
        candidate_file=None,
        candidate_sha256="",
    )
    decision.update({"status": "ineligible", "reason": "invalid_input"})
    write_json_atomic(paths["decision_file"], decision, sort_keys=True)
    report = _base_report(
        source=source,
        source_sha256=source_sha256,
        paths=paths,
        records=[],
        counts=_scan_counts([]),
        requested_junction=requested_junction,
    )
    report.update(
        {
            "status": "blocked",
            "claim_status": "construction-invalid",
            "nema_binding_status": "blocked_input",
            "promotion_status": "blocked",
            "error": error,
            "warnings": [error],
        }
    )
    _persist_review_package(report, decision, paths, source=source)
    return report
