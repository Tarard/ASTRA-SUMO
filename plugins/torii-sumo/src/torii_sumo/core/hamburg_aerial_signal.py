"""Bind frozen Hamburg primary-signal identities to an aerial SUMO candidate."""

from __future__ import annotations

import csv
import json
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .candidate_contracts import file_sha256
from .digital_twin import SignalStream

REQUEST_SCHEMA = "torii.hamburg-aerial-signal-binding-request/v1"
REPORT_SCHEMA = "torii.hamburg-aerial-signal-binding/v1"
PROTECTED_CANDIDATE_SCHEMA = "torii.hamburg-protected-signal-candidate/v1"
PROTECTED_CANDIDATE_REQUEST_SCHEMA = "torii.hamburg-protected-signal-candidate-request/v1"


def _materialized_node_id(record: Mapping[str, Any]) -> str:
    """Keep official identity separate from a retained SUMO junction name."""
    match = re.fullmatch(r"LSA(\d+)_part\d+", str(record.get("join_id", "")))
    node_id = str(record.get("node_id", "")).strip()
    if node_id and match and node_id != match.group(1):
        raise ValueError("materialized official node conflicts with the legacy join id")
    node_id = node_id or (match.group(1) if match else "")
    if not node_id.isascii() or not node_id.isdigit():
        raise ValueError("materialized movement requires an explicit official node id")
    return node_id


def allocate_stage_green_seconds(
    stages: Sequence[str],
    *,
    link_flows: Mapping[int, float],
    cycle_seconds: int,
    transition_seconds: Sequence[int],
    minimum_green_seconds: int = 5,
) -> list[int]:
    """Allocate one fixed cycle by each stage's largest controlled-link flow."""
    if not stages or len(stages) != len(transition_seconds):
        raise ValueError("stages and transition_seconds must be non-empty and aligned")
    available = cycle_seconds - sum(transition_seconds)
    minimum_total = minimum_green_seconds * len(stages)
    if available < minimum_total:
        raise ValueError("cycle is too short for transitions and minimum green times")
    weights = [
        max((float(link_flows.get(index, 0.0)) for index, value in enumerate(state) if value == "G"), default=0.0)
        for state in stages
    ]
    if not any(weights):
        weights = [1.0] * len(stages)
    extra = available - minimum_total
    total_weight = sum(weights)
    raw = [extra * weight / total_weight for weight in weights]
    additions = [math.floor(value) for value in raw]
    remainder = extra - sum(additions)
    order = sorted(range(len(stages)), key=lambda index: (raw[index] - additions[index], weights[index], -index), reverse=True)
    for index in order[:remainder]:
        additions[index] += 1
    return [minimum_green_seconds + value for value in additions]


def build_protected_signal_stages(
    bindings: Sequence[Mapping[str, Any]],
    *,
    conflict_pairs: set[tuple[int, int]],
    link_count: int,
) -> list[str]:
    """Build deterministic protected-only stages from exact signal groups."""
    if link_count <= 0:
        raise ValueError("link_count must be positive")
    conflicts = {tuple(sorted(pair)) for pair in conflict_pairs}
    groups: dict[str, set[int]] = {}
    for row in bindings:
        if row.get("status") != "active" or not row.get("official_signal_group"):
            continue
        index = int(row["sumo_link_index"])
        if index < 0 or index >= link_count:
            raise ValueError("signal binding link index is outside the TLS state")
        groups.setdefault(str(row["official_signal_group"]), set()).add(index)
    if not groups:
        raise ValueError("no exact official signal groups are available")
    for name, links in groups.items():
        if any(tuple(sorted((a, b))) in conflicts for a in links for b in links if a < b):
            raise ValueError(f"official signal group {name} contains conflicting links")
    stages: list[set[str]] = []
    for name in sorted(groups):
        for stage in stages:
            if all(
                tuple(sorted((a, b))) not in conflicts
                for other in stage
                for a in groups[name]
                for b in groups[other]
            ):
                stage.add(name)
                break
        else:
            stages.append({name})
    result = []
    for stage in stages:
        state = ["r"] * link_count
        for name in stage:
            for index in groups[name]:
                state[index] = "G"
        result.append("".join(state))
    return result


def network_tls_conflict_pairs(net_file: Path, tls_id: str) -> set[tuple[int, int]]:
    """Read one junction's SUMO foe matrix as TLS link-index pairs."""
    root = ET.parse(net_file).getroot()
    junction = next((row for row in root.findall("junction") if row.get("id") == tls_id), None)
    if junction is None:
        raise ValueError(f"network has no junction {tls_id!r}")
    request_by_lane = {
        lane_id: index for index, lane_id in enumerate(junction.get("intLanes", "").split())
    }
    lane_by_edge_index = {
        (edge.get("id", ""), int(lane.get("index", "0"))): lane.get("id", "")
        for edge in root.findall("edge")
        for lane in edge.findall("lane")
        if edge.get("id", "").startswith(":") and lane.get("id")
    }
    next_internal_lanes: dict[str, set[str]] = {}
    for connection in root.findall("connection"):
        source_lane = lane_by_edge_index.get(
            (connection.get("from", ""), int(connection.get("fromLane", "0")))
        )
        if source_lane and connection.get("via"):
            next_internal_lanes.setdefault(source_lane, set()).update(
                connection.get("via", "").split()
            )
    links_by_request: dict[int, set[int]] = {}
    controlled_links: set[int] = set()
    for connection in root.findall("connection"):
        if connection.get("tl") != tls_id or connection.get("linkIndex") is None:
            continue
        link_index = int(connection.get("linkIndex", "-1"))
        controlled_links.add(link_index)
        pending = list(connection.get("via", "").split())
        visited = set()
        while pending:
            via = pending.pop()
            if via in visited:
                continue
            visited.add(via)
            if via in request_by_lane:
                links_by_request.setdefault(request_by_lane[via], set()).add(link_index)
            else:
                pending.extend(next_internal_lanes.get(via, ()))
    pairs: set[tuple[int, int]] = set()
    for request in junction.findall("request"):
        index = int(request.get("index", "-1"))
        foes = request.get("foes", "")
        for other in range(len(foes)):
            if other != index and foes[-1 - other] == "1":
                for first in links_by_request.get(index, ()):
                    for second in links_by_request.get(other, ()):
                        if first != second:
                            pairs.add(tuple(sorted((first, second))))
    mapped_links = set().union(*links_by_request.values()) if links_by_request else set()
    if controlled_links - mapped_links:
        raise ValueError(
            f"TLS {tls_id!r} has controlled links without junction-request identities: "
            f"{sorted(controlled_links - mapped_links)}"
        )
    return pairs


def build_protected_signal_candidate(
    *,
    net_file: Path,
    signal_binding_csv: Path,
    output_dir: Path,
    tls_ids: Sequence[str],
    green_seconds: int = 15,
    yellow_seconds: int = 3,
    route_file: Path | None = None,
    cycle_seconds: int | None = None,
    green_overrides: Mapping[str, Sequence[int]] | None = None,
    offset_overrides: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Write a separate protected-only TLS candidate and its phase evidence."""
    if green_seconds < 5 or yellow_seconds < 3:
        raise ValueError("protected candidate requires green>=5s and yellow>=3s")
    source = Path(net_file).resolve(strict=True)
    binding_path = Path(signal_binding_csv).resolve(strict=True)
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")
    tree = ET.parse(source)
    root = tree.getroot()
    bindings = list(csv.DictReader(binding_path.open(encoding="utf-8", newline="")))
    link_flows = _tls_link_flows(root, route_file, tls_ids) if route_file else {}
    lanes = {
        lane.get("id", ""): lane
        for edge in root.findall("edge")
        for lane in edge.findall("lane")
        if lane.get("id")
    }
    plans = []
    for tls_id in tls_ids:
        tl_logic = next((row for row in root.findall("tlLogic") if row.get("id") == tls_id), None)
        if tl_logic is None:
            raise ValueError(f"network has no tlLogic {tls_id!r}")
        if offset_overrides and tls_id in offset_overrides:
            offset = int(offset_overrides[tls_id])
            if cycle_seconds is not None and not 0 <= offset < cycle_seconds:
                raise ValueError(f"offset override for {tls_id} is outside the cycle")
            tl_logic.set("offset", str(offset))
        old_phases = tl_logic.findall("phase")
        if not old_phases:
            raise ValueError(f"tlLogic {tls_id!r} has no phases")
        link_count = len(old_phases[0].get("state", ""))
        rows = [
            row
            for row in bindings
            if row.get("sumo_tls_id") == tls_id and row.get("status") == "active"
        ]
        bound_links = {int(row["sumo_link_index"]) for row in rows}
        rows.extend(
            {
                "status": "active",
                "official_signal_group": f"UNBOUND_{index:03d}",
                "sumo_link_index": index,
            }
            for index in range(link_count)
            if index not in bound_links
        )
        conflicts = network_tls_conflict_pairs(source, tls_id)
        stages = build_protected_signal_stages(
            rows,
            conflict_pairs=conflicts,
            link_count=link_count,
        )
        via_by_link: dict[int, list[str]] = {}
        for connection in root.findall("connection"):
            if connection.get("tl") == tls_id and connection.get("linkIndex") is not None:
                via_by_link.setdefault(int(connection.get("linkIndex", "-1")), []).extend(
                    connection.get("via", "").split()
                )
        stage_details = []
        for stage_index, state in enumerate(stages):
            active_links = [index for index, value in enumerate(state) if value == "G"]
            clearance = 2
            for index in active_links:
                for via in via_by_link.get(index, []):
                    lane = lanes.get(via)
                    if lane is None:
                        continue
                    length = float(lane.get("length", "0")) + 5.0
                    speed = max(float(lane.get("speed", "5")), 5.0)
                    clearance = max(clearance, min(8, math.ceil(length / speed)))
            stage_details.append((stage_index, state, active_links, clearance))
        if len(stages) == 1 and cycle_seconds is not None:
            green_values = [cycle_seconds]
            transitions = [0]
        else:
            transitions = [yellow_seconds + row[3] for row in stage_details]
            green_values = (
                allocate_stage_green_seconds(
                    stages,
                    link_flows=link_flows.get(tls_id, {}),
                    cycle_seconds=cycle_seconds,
                    transition_seconds=transitions,
                )
                if cycle_seconds is not None
                else [green_seconds] * len(stages)
            )
        if green_overrides and tls_id in green_overrides:
            override = [int(value) for value in green_overrides[tls_id]]
            if len(override) != len(stages) or any(value < 5 for value in override):
                raise ValueError(f"green override for {tls_id} is invalid")
            if cycle_seconds is not None and sum(override) + sum(transitions) != cycle_seconds:
                raise ValueError(f"green override for {tls_id} does not preserve the cycle")
            green_values = override
        for phase in old_phases:
            tl_logic.remove(phase)
        phase_rows = []
        for (stage_index, state, active_links, clearance), stage_green in zip(
            stage_details,
            green_values,
            strict=True,
        ):
            yellow = "".join("y" if value == "G" else "r" for value in state)
            ET.SubElement(tl_logic, "phase", duration=str(stage_green), state=state)
            if transitions[stage_index]:
                ET.SubElement(tl_logic, "phase", duration=str(yellow_seconds), state=yellow)
                ET.SubElement(tl_logic, "phase", duration=str(clearance), state="r" * link_count)
            phase_rows.append(
                {
                    "stage": stage_index,
                    "green_state": state,
                    "green_seconds": stage_green,
                    "yellow_seconds": yellow_seconds if transitions[stage_index] else 0,
                    "red_clearance_seconds": clearance if transitions[stage_index] else 0,
                    "active_links": active_links,
                    "stage_flow": max(
                        (link_flows.get(tls_id, {}).get(index, 0.0) for index in active_links),
                        default=0.0,
                    ),
                }
            )
        plans.append(
            {
                "tls_id": tls_id,
                "link_count": link_count,
                "conflict_pair_count": len(conflicts),
                "stage_count": len(stages),
                "cycle_seconds": sum(
                    row["green_seconds"] + row["yellow_seconds"] + row["red_clearance_seconds"]
                    for row in phase_rows
                ),
                "phases": phase_rows,
            }
        )
    destination.mkdir(parents=True)
    candidate = destination / "protected-signals.net.xml"
    ET.indent(tree, space="    ")
    tree.write(candidate, encoding="utf-8", xml_declaration=True)
    report = {
        "schema": PROTECTED_CANDIDATE_SCHEMA,
        "status": "diagnostic-demo",
        "source_network": {"path": str(source), "sha256": file_sha256(source)},
        "signal_bindings": {"path": str(binding_path), "sha256": file_sha256(binding_path)},
        "route_file": (
            {"path": str(Path(route_file).resolve(strict=True)), "sha256": file_sha256(Path(route_file))}
            if route_file
            else None
        ),
        "candidate_network": {"path": str(candidate), "sha256": file_sha256(candidate)},
        "tls_plans": plans,
        "claim_boundary": (
            "Protected-only stages use exact official signal groups and SUMO conflict pairs. "
            "Unbound links are conservative pseudo-groups. Timing is diagnostic, not official Hamburg timing."
        ),
    }
    manifest = destination / "manifest.json"
    manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**report, "manifest_file": str(manifest), "manifest_sha256": file_sha256(manifest)}


def build_protected_signal_candidate_from_request(
    *,
    request_file: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    request_path = Path(request_file).expanduser().resolve(strict=True)
    request = _load_json_object(request_path, "request")
    if request.get("schema") != PROTECTED_CANDIDATE_REQUEST_SCHEMA:
        raise ValueError(f"request schema must be {PROTECTED_CANDIDATE_REQUEST_SCHEMA}")
    net_file = _verified_artifact(request.get("net_file"), "net_file")
    bindings = _verified_artifact(request.get("signal_binding_csv"), "signal_binding_csv")
    route_file = _verified_artifact(request.get("route_file"), "route_file")
    tls_ids = [str(value) for value in request.get("tls_ids", [])]
    if not tls_ids:
        raise ValueError("request tls_ids must not be empty")
    return build_protected_signal_candidate(
        net_file=net_file,
        signal_binding_csv=bindings,
        output_dir=Path(output_dir),
        tls_ids=tls_ids,
        green_seconds=int(request.get("green_seconds", 15)),
        yellow_seconds=int(request.get("yellow_seconds", 3)),
        route_file=route_file,
        cycle_seconds=int(request["cycle_seconds"]) if request.get("cycle_seconds") else None,
        green_overrides=request.get("green_overrides"),
        offset_overrides=request.get("offset_overrides"),
    )


def _tls_link_flows(
    root: ET.Element,
    route_file: Path,
    tls_ids: Sequence[str],
) -> dict[str, dict[int, float]]:
    pairs: dict[tuple[str, str], list[tuple[str, int]]] = {}
    selected = set(tls_ids)
    for connection in root.findall("connection"):
        tls_id = connection.get("tl", "")
        if tls_id in selected and connection.get("linkIndex") is not None:
            pairs.setdefault((connection.get("from", ""), connection.get("to", "")), []).append(
                (tls_id, int(connection.get("linkIndex", "-1")))
            )
    result: dict[str, dict[int, float]] = {tls_id: {} for tls_id in tls_ids}
    for vehicle in ET.parse(Path(route_file).resolve(strict=True)).getroot().findall("vehicle"):
        route = vehicle.find("route")
        if route is None:
            continue
        edges = route.get("edges", "").split()
        for first, second in zip(edges, edges[1:]):
            matches = pairs.get((first, second), [])
            by_tls: dict[str, set[int]] = {}
            for tls_id, link_index in matches:
                by_tls.setdefault(tls_id, set()).add(link_index)
            for tls_id, links in by_tls.items():
                share = 1.0 / len(links)
                for link_index in links:
                    result[tls_id][link_index] = result[tls_id].get(link_index, 0.0) + share
    return result


def match_official_signal_streams(
    *,
    node_id: str,
    movement_id: str,
    ingress_lane_id: str,
    egress_lane_id: str,
    streams: Sequence[SignalStream],
) -> list[SignalStream]:
    """Return only primary-signal records with the exact physical movement key."""
    key = tuple(map(str, (node_id, movement_id, ingress_lane_id, egress_lane_id)))
    return sorted(
        (
            stream
            for stream in streams
            if stream.layer_name == "primary_signal"
            and tuple(
                map(
                    str,
                    (
                        stream.node_id,
                        stream.connection_id,
                        stream.ingress_lane_id,
                        stream.egress_lane_id,
                    ),
                )
            )
            == key
        ),
        key=lambda stream: stream.stream_id,
    )


def build_hamburg_aerial_signal_binding(
    *,
    request_file: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Audit exact official signal identities on one immutable aerial candidate."""
    request_path = Path(request_file).expanduser().resolve()
    request = _load_json_object(request_path, "request")
    if request.get("schema") != REQUEST_SCHEMA:
        raise ValueError(f"request schema must be {REQUEST_SCHEMA}")
    candidate_manifest = _verified_artifact(request.get("candidate_manifest"), "candidate_manifest")
    movement_summary = _verified_artifact(request.get("movement_summary"), "movement_summary")
    signal_snapshot = _verified_artifact(request.get("signal_stream_snapshot"), "signal_stream_snapshot")
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")

    candidate = _load_json_object(candidate_manifest, "candidate manifest")
    if candidate.get("schema") != "torii.hamburg-aerial-corridor-candidate/v1":
        raise ValueError("candidate manifest schema is invalid")
    network_record = candidate.get("artifacts", {}).get("network", {})
    network = Path(str(network_record.get("path", ""))).expanduser().resolve(strict=True)
    if file_sha256(network) != str(network_record.get("sha256", "")).lower():
        raise ValueError("candidate network hash does not match its manifest")

    summary = _load_json_object(movement_summary, "movement summary")
    if summary.get("schema") != "torii.hamburg-aerial-corridor-plan/v1":
        raise ValueError("movement summary schema is invalid")
    plans: dict[str, dict[str, Any]] = {}
    expected_plan_hashes = candidate.get("inputs", {}).get("movement_plans", {})
    for row in summary.get("intersections", []):
        node_id = str(row["node_id"])
        plan_path = Path(str(row["plan_file"])).expanduser().resolve(strict=True)
        if file_sha256(plan_path) != str(expected_plan_hashes.get(node_id, "")).lower():
            raise ValueError(f"movement plan {node_id} does not match the candidate manifest")
        plans[node_id] = _load_json_object(plan_path, f"movement plan {node_id}")

    streams = _load_normalized_signal_streams(signal_snapshot)
    movement_index = {
        (node_id, str(movement["movement_id"])): movement
        for node_id, plan in plans.items()
        for movement in plan.get("movements", [])
    }
    via_index = _controlled_via_index(network)
    rows = []
    matched_stream_ids: set[int] = set()
    for materialized in candidate.get("movement_materialization", {}).get("movements", []):
        node_id = _materialized_node_id(materialized)
        movement_id = str(materialized.get("movement_id", ""))
        movement = movement_index.get((node_id, movement_id))
        if movement is None:
            raise ValueError(f"candidate movement {node_id}/{movement_id} is absent from the plan")
        matches = match_official_signal_streams(
            node_id=node_id,
            movement_id=movement_id,
            ingress_lane_id=str(movement["ingress_lane_id"]),
            egress_lane_id=str(movement["egress_lane_id"]),
            streams=streams,
        )
        internal_lane_id = str(materialized.get("internal_lane_id", ""))
        controlled_link = via_index.get(internal_lane_id)
        status = "active" if len(matches) == 1 and controlled_link is not None else "needs_review"
        if len(matches) == 0:
            reason = "no exact primary_signal record for the official physical movement key"
        elif len(matches) > 1:
            reason = "multiple primary_signal records share the official physical movement key"
        elif controlled_link is None:
            reason = "materialized internal lane has no unique controlled SUMO link"
        else:
            reason = "exact official movement identity and unique controlled SUMO link"
            matched_stream_ids.add(matches[0].stream_id)
        rows.append(
            {
                "node_id": node_id,
                "movement_id": movement_id,
                "official_ingress_lane": str(movement["ingress_lane_id"]),
                "official_egress_lane": str(movement["egress_lane_id"]),
                "internal_lane_id": internal_lane_id,
                "stream_id": matches[0].stream_id if len(matches) == 1 else "",
                "official_signal_group": matches[0].signal_group if len(matches) == 1 else "",
                "sumo_tls_id": controlled_link[0] if controlled_link else "",
                "sumo_link_index": controlled_link[1] if controlled_link else "",
                "status": status,
                "reason": reason,
            }
        )

    coverage = _stream_coverage(streams, movement_index, matched_stream_ids)
    status_counts = _counts(rows, "status")
    coverage_counts = _counts(coverage, "status")
    destination.mkdir(parents=True)
    binding_file = destination / "official-signal-bindings.csv"
    _write_csv(binding_file, rows)
    coverage_file = destination / "official-signal-stream-coverage.json"
    coverage_file.write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "schema": REPORT_SCHEMA,
        "status": "pass" if status_counts.get("active", 0) > 0 else "blocked",
        "decision": "review_required",
        "claim_status": "diagnostic-demo",
        "inputs": {
            "request": {"path": str(request_path), "sha256": file_sha256(request_path)},
            "candidate_manifest": {
                "path": str(candidate_manifest),
                "sha256": file_sha256(candidate_manifest),
            },
            "network": {"path": str(network), "sha256": file_sha256(network)},
            "movement_summary": {
                "path": str(movement_summary),
                "sha256": file_sha256(movement_summary),
            },
            "signal_stream_snapshot": {
                "path": str(signal_snapshot),
                "sha256": file_sha256(signal_snapshot),
            },
        },
        "counts": {
            "official_primary_signal_streams": len(streams),
            "materialized_candidate_movements": len(rows),
            "binding_status": status_counts,
            "stream_coverage_status": coverage_counts,
        },
        "gates": {
            "source_hashes": "pass",
            "exact_physical_signal_identity": (
                "pass" if status_counts.get("active", 0) == len(rows) else "review_required"
            ),
            "all_official_signal_streams_bound": (
                "pass" if len(matched_stream_ids) == len(streams) else "review_required"
            ),
            "official_signal_timing": "not_run",
            "historical_signal_observations": "not_loaded",
        },
        "artifacts": {
            "bindings": {"path": str(binding_file), "sha256": file_sha256(binding_file)},
            "stream_coverage": {"path": str(coverage_file), "sha256": file_sha256(coverage_file)},
        },
        "claim_boundary": (
            "Exact official movement and signal identities are bound only when node, connection, ingress lane, "
            "and egress lane all agree. This stage does not infer signal timing or repair asset-version mismatches."
        ),
    }
    manifest_file = destination / "manifest.json"
    manifest_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**report, "manifest_file": str(manifest_file), "manifest_sha256": file_sha256(manifest_file)}


def _verified_artifact(value: Any, label: str) -> Path:
    if not isinstance(value, Mapping) or not value.get("path") or not value.get("sha256"):
        raise ValueError(f"request {label} must contain path and sha256")
    path = Path(str(value["path"])).expanduser().resolve(strict=True)
    if file_sha256(path) != str(value["sha256"]).lower():
        raise ValueError(f"{label} SHA-256 does not match")
    return path


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _load_normalized_signal_streams(path: Path) -> list[SignalStream]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("signal_stream_snapshot must be a non-empty JSON list")
    streams = [SignalStream(**row) for row in payload]
    if len({stream.stream_id for stream in streams}) != len(streams):
        raise ValueError("signal stream snapshot repeats stream_id")
    return sorted(streams, key=lambda stream: stream.stream_id)


def _controlled_via_index(network: Path) -> dict[str, tuple[str, int]]:
    result: dict[str, tuple[str, int]] = {}
    for connection in ET.parse(network).getroot().findall("connection"):
        via = connection.get("via", "").split()
        if not via or not connection.get("tl") or connection.get("linkIndex") is None:
            continue
        value = (str(connection.get("tl")), int(str(connection.get("linkIndex"))))
        if via[0] in result and result[via[0]] != value:
            raise ValueError(f"internal lane {via[0]} maps to multiple controlled links")
        result[via[0]] = value
    return result


def _stream_coverage(
    streams: Sequence[SignalStream],
    movement_index: Mapping[tuple[str, str], Mapping[str, Any]],
    matched_stream_ids: set[int],
) -> list[dict[str, Any]]:
    result = []
    for stream in streams:
        movement = movement_index.get((str(stream.node_id), str(stream.connection_id)))
        if stream.stream_id in matched_stream_ids:
            status = "active"
            reason = "exact physical movement is materialized and bound"
        elif movement is None:
            status = "not_in_vehicle_movement_plan"
            reason = "connection id is absent from the frozen official vehicle movement plan"
        elif (
            str(movement["ingress_lane_id"]) != str(stream.ingress_lane_id)
            or str(movement["egress_lane_id"]) != str(stream.egress_lane_id)
        ):
            status = "asset_version_mismatch"
            reason = "connection id exists but MAP/OCIT and TLD lane identities differ"
        else:
            status = "candidate_movement_not_bound"
            reason = "official movement is not materialized or lacks a unique candidate link"
        result.append(
            {
                "stream_id": stream.stream_id,
                "node_id": str(stream.node_id),
                "connection_id": str(stream.connection_id),
                "ingress_lane_id": str(stream.ingress_lane_id),
                "egress_lane_id": str(stream.egress_lane_id),
                "signal_group": str(stream.signal_group),
                "status": status,
                "reason": reason,
            }
        )
    return result


def _counts(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        value = str(row[key])
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise ValueError("signal binding produced no candidate movement rows")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


__all__ = [
    "REPORT_SCHEMA",
    "REQUEST_SCHEMA",
    "allocate_stage_green_seconds",
    "build_hamburg_aerial_signal_binding",
    "build_protected_signal_candidate",
    "build_protected_signal_candidate_from_request",
    "build_protected_signal_stages",
    "match_official_signal_streams",
    "network_tls_conflict_pairs",
]
