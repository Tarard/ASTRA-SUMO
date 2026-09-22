"""Build a deterministic MAP-to-HH-SIB splice plan.

The lane-axis stitch planner deliberately stops at an approach-level corridor
match.  This module turns that evidence into the next, still non-materializing
operation plan: where a local MAP edge must meet the official road axis and
where an official MAP merge event requires a split before that join.

The important distinction is that a valid XML network is not enough.  A
boundary node made from the mean of lane endpoints is only legitimate when
the whole approach has one coherent boundary and the MAP/HH-SIB lane profiles
agree.  Otherwise the plan records the official merge event and refuses to
authorize a one-node join.  A later materializer can consume this plan to
write PlainXML ``split``/``connection`` records, while preserving the source
artifacts and their hashes.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pyproj import Transformer

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256

from .official_lane_stitch import (
    HAMBURG_MAP_BINDING_SCHEMA,
    OFFICIAL_LANE_AXIS_STITCH_SCHEMA,
    OfficialLaneAxisStitchThresholds,
    lane_axis_stitch_identity_payload,
    plan_hamburg_official_map_lane_axis_stitch,
)
from .official_lane_transition import (
    _axis_point_at_station,
    _parse_axes,
    _project_coordinate,
    _project_point_to_axis,
)


OFFICIAL_SPLICE_PLAN_SCHEMA = "torii.hamburg-official-map-hh-sib-splice-plan/v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OfficialSplicePlanError(ValueError):
    """Raised when official splice evidence is missing or inconsistent."""


def build_hamburg_official_splice_plan(
    *,
    map_binding_reports: Sequence[Mapping[str, Any] | str | Path],
    lane_axis_stitch_plan: Mapping[str, Any] | str | Path,
    nodes_file: str | Path,
    edges_file: str | Path,
    plainxml_manifest_file: str | Path,
    stitch_thresholds: OfficialLaneAxisStitchThresholds | None = None,
    maximum_merge_axis_distance_m: float = 15.0,
    merge_point_tolerance_m: float = 2.0,
    output_file: str | Path | None = None,
) -> dict[str, Any]:
    """Return a hash-bound, non-materializing splice operation plan.

    The supplied lane-axis plan is recomputed from the exact MAP reports and
    HH-SIB PlainXML files.  A stale or edited plan therefore cannot authorize
    a splice.  ``review_required`` is an intentional machine result: it means
    the location and operation are understood, but a downstream materializer
    must still prove lane conservation and connection semantics.
    """

    if not math.isfinite(maximum_merge_axis_distance_m) or maximum_merge_axis_distance_m <= 0:
        raise OfficialSplicePlanError("maximum_merge_axis_distance_m must be positive")
    if not math.isfinite(merge_point_tolerance_m) or merge_point_tolerance_m <= 0:
        raise OfficialSplicePlanError("merge_point_tolerance_m must be positive")

    node_path = Path(nodes_file).expanduser().resolve()
    edge_path = Path(edges_file).expanduser().resolve()
    manifest_path = Path(plainxml_manifest_file).expanduser().resolve()
    reports = [_load_json_like(item, "MAP binding report") for item in map_binding_reports]
    if not reports:
        raise OfficialSplicePlanError("map_binding_reports must not be empty")
    reports.sort(key=lambda item: _identifier_key(str(item[0].get("node_id", ""))))
    report_payloads = [item[0] for item in reports]
    _validate_reports(report_payloads)
    movement_destinations = _movement_destination_groups(report_payloads)

    supplied_plan, supplied_identity = _load_json_like(
        lane_axis_stitch_plan, "lane-axis stitch plan"
    )
    supplied_identity["canonical_sha256"] = _stable_digest(
        lane_axis_stitch_identity_payload(supplied_plan)
    )
    limits = (stitch_thresholds or OfficialLaneAxisStitchThresholds()).validated()
    recomputed_plan = plan_hamburg_official_map_lane_axis_stitch(
        map_binding_reports=[
            identity["path"] if identity.get("identity_method") == "file_bytes_sha256" else report
            for report, identity in reports
        ],
        nodes_file=node_path,
        edges_file=edge_path,
        plainxml_manifest_file=manifest_path,
        thresholds=limits,
    )
    if lane_axis_stitch_identity_payload(
        supplied_plan
    ) != lane_axis_stitch_identity_payload(recomputed_plan):
        raise OfficialSplicePlanError(
            "lane-axis stitch plan does not exactly match the recomputed official-input plan"
        )
    if supplied_plan.get("schema") != OFFICIAL_LANE_AXIS_STITCH_SCHEMA:
        raise OfficialSplicePlanError("unexpected lane-axis stitch plan schema")

    manifest = _read_json_file(manifest_path, "PlainXML manifest")
    projection = _projection(manifest)
    transformer = Transformer.from_crs(
        projection["source_crs"], projection["crs"], always_xy=True
    )
    axes = _parse_axes(edge_path)
    lane_rows = [
        item
        for item in recomputed_plan.get("lanes", [])
        if isinstance(item, Mapping)
        and str(item.get("status")) == "pass"
        and str(item.get("map_role")) in {"ingress", "egress"}
    ]
    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in lane_rows:
        grouped[
            (str(row["node_id"]), str(row["map_role"]), str(row["approach_id"]))
        ].append(row)
    conflict_by_key = {
        (
            str(item.get("node_id", "")),
            str(item.get("map_role", "")),
            str(item.get("approach_id", "")),
        ): item
        for item in recomputed_plan.get("approach_geometry_conflicts", [])
        if isinstance(item, Mapping)
    }

    approaches: list[dict[str, Any]] = []
    axis_splits: list[dict[str, Any]] = []
    local_operations: list[dict[str, Any]] = []
    for key, conflict in sorted(conflict_by_key.items(), key=lambda item: _approach_sort_key(item[0])):
        node_id, role, approach_id = key
        members = sorted(grouped.get(key, []), key=lambda item: _identifier_key(str(item.get("lane_id", ""))))
        entry = _build_approach_splice(
            node_id=node_id,
            role=role,
            approach_id=approach_id,
            members=members,
            conflict=conflict,
            axes=axes,
            transformer=transformer,
            maximum_merge_axis_distance_m=maximum_merge_axis_distance_m,
            merge_point_tolerance_m=merge_point_tolerance_m,
            movement_destinations=movement_destinations,
        )
        approaches.append(entry)
        event = entry.get("splice_event")
        if not isinstance(event, Mapping):
            continue
        axis_split = {
            "operation": "split_axis_edge_at_station",
            "status": entry["status"],
            "node_id": event["splice_node_id"],
            "axis_corridor_id": event["axis_corridor_id"],
            "axis_edge_id": event["axis_edge_id"],
            "station_m": event["axis_station_m"],
            "split_reason": event["kind"],
            "lane_count_before": event["lane_count_before"],
            "lane_count_after": event["lane_count_after"],
        }
        axis_splits.append(axis_split)
        local_operations.append(
            {
                "operation": "splice_local_map_approach",
                "status": entry["status"],
                "map_edge_id": entry["map_edge_id"],
                "old_boundary_node_id": entry["old_boundary_node_id"],
                "new_splice_node_id": event["splice_node_id"],
                "kind": event["kind"],
                "map_lane_ids": entry["map_lane_ids"],
                "official_merge_lane_ids": entry["official_merge_lane_ids"],
                "lane_assignment": "not_authorized_until_lane_conservation_gate",
            }
        )

    axis_splits.sort(key=lambda item: (str(item["axis_corridor_id"]), float(item["station_m"]), str(item["node_id"])))
    duplicate_axis_stations = _duplicate_axis_stations(axis_splits)
    gates = {
        "exact_recomputed_stitch_plan": "pass",
        "approach_event_geometry": (
            "pass" if all(item["status"] == "pass" for item in approaches) else "review_required"
        ),
        "axis_split_station_order": "pass" if not duplicate_axis_stations else "review_required",
        "lane_conservation_and_index_assignment": "review_required",
        "network_materialization": "blocked",
    }
    status = "pass" if gates["approach_event_geometry"] == "pass" and not duplicate_axis_stations else "review_required"
    input_identity = {
        "map_binding_reports": [item[1] for item in reports],
        "lane_axis_stitch_plan": supplied_identity,
        "lane_axis_stitch_plan_id": str(recomputed_plan.get("plan_id", "")),
        "nodes": _file_identity(node_path),
        "edges": _file_identity(edge_path),
        "plainxml_manifest": _file_identity(manifest_path),
        "hh_sib_source_sha256": str(recomputed_plan.get("inputs", {}).get("hh_sib_source_sha256", "")),
        "plainxml_candidate_id": str(recomputed_plan.get("inputs", {}).get("plainxml_candidate_id", "")),
    }
    result: dict[str, Any] = {
        "schema": OFFICIAL_SPLICE_PLAN_SCHEMA,
        "status": status,
        "decision": (
            "emit_deterministic_splice_operations"
            if status == "pass"
            else "automatic_abstention_until_official_merge_operations_are_materialized"
        ),
        "human_action_required": False,
        "network_materialization_performed": False,
        "inputs": input_identity,
        "projection": projection,
        "thresholds": {
            "stitch": _threshold_payload(limits),
            "maximum_merge_axis_distance_m": maximum_merge_axis_distance_m,
            "merge_point_tolerance_m": merge_point_tolerance_m,
        },
        "source_policy": {
            "authoritative_sources": [
                "Hamburg_MAP_KML_MAPEM_binding",
                "Hamburg_HH_SIB_PlainXML",
            ],
            "excluded_sources": ["OpenStreetMap", "Google_Maps", "manual_map_review"],
        },
        "approaches": approaches,
        "operations": {
            "axis_splits": axis_splits,
            "local_map_splices": local_operations,
        },
        "duplicate_axis_stations": duplicate_axis_stations,
        "counts": {
            "approach_count": len(approaches),
            "passing_approach_event_count": sum(item["status"] == "pass" for item in approaches),
            "review_required_approach_event_count": sum(item["status"] != "pass" for item in approaches),
            "axis_split_count": len(axis_splits),
            "merge_event_count": sum(
                item.get("splice_event", {}).get("kind") == "official_merge_event"
                for item in approaches
                if isinstance(item.get("splice_event"), Mapping)
            ),
        },
        "gates": gates,
        "claim_boundary": (
            "This artifact locates deterministic official MAP-to-HH-SIB splice events and emits a safe operation "
            "queue. It does not invent lane indices, merge priorities, legal lane changes, signal groups, or a "
            "complete SUMO network. A materializer must prove lane conservation and run SUMO/network surface "
            "audits before a candidate can advance."
        ),
    }
    result["plan_id"] = "official-splice-" + _stable_digest(
        {
            "schema": OFFICIAL_SPLICE_PLAN_SCHEMA,
            "input_hashes": _promotion_input_hashes(input_identity),
            "thresholds": result["thresholds"],
            "approaches": approaches,
            "operations": result["operations"],
        }
    )[:24]

    if output_file is not None:
        destination = Path(output_file).expanduser().resolve()
        input_paths = {
            node_path,
            edge_path,
            manifest_path,
            *(
                Path(str(item["path"])).expanduser().resolve()
                for _report, item in reports
                if item.get("path")
            ),
            *(
                [Path(str(supplied_identity["path"])).expanduser().resolve()]
                if supplied_identity.get("path")
                else []
            ),
        }
        if destination in input_paths:
            raise OfficialSplicePlanError("output_file must not overwrite an input artifact")
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(destination, result, sort_keys=True)
    return result


def _build_approach_splice(
    *,
    node_id: str,
    role: str,
    approach_id: str,
    members: Sequence[Mapping[str, Any]],
    conflict: Mapping[str, Any],
    axes: Mapping[str, Sequence[Any]],
    transformer: Transformer,
    maximum_merge_axis_distance_m: float,
    merge_point_tolerance_m: float,
    movement_destinations: Mapping[str, Mapping[str, frozenset[str]]],
) -> dict[str, Any]:
    axis_counts = [int(value) for value in conflict.get("hh_sib_axis_lane_counts_at_boundary", [])]
    map_count = int(conflict.get("map_lane_count", len(members)))
    corridor_ids = {
        str(item.get("binding", {}).get("corridor_id", ""))
        for item in members
        if isinstance(item.get("binding"), Mapping)
    }
    map_lane_ids = sorted(
        (str(item.get("lane_id", "")) for item in members), key=_identifier_key
    )
    old_boundary = f"hh-map-{_safe_id(node_id)}-a{_safe_id(approach_id)}-boundary"
    edge_suffix = "in" if role == "ingress" else "out"
    map_edge_id = f"hh-map-{_safe_id(node_id)}-a{_safe_id(approach_id)}-{edge_suffix}"
    base = {
        "node_id": node_id,
        "map_role": role,
        "approach_id": approach_id,
        "status": "review_required",
        "decision": "automatic_abstention_no_materialization",
        "map_edge_id": map_edge_id,
        "old_boundary_node_id": old_boundary,
        "map_lane_ids": map_lane_ids,
        "map_lane_count": map_count,
        "hh_sib_axis_lane_counts_at_boundary": axis_counts,
        "official_merge_lane_ids": sorted(
            (str(value) for value in conflict.get("official_merge_lane_ids", [])),
            key=_identifier_key,
        ),
        "reasons": list(conflict.get("reasons", [])),
        "splice_event": None,
    }
    if len(corridor_ids) != 1 or not members:
        base["reasons"].append("approach_has_no_unique_passing_axis_corridor")
        return base
    corridor_id = next(iter(corridor_ids))
    axis = axes.get(corridor_id)
    if not axis:
        base["reasons"].append("bound_axis_corridor_not_found_in_plainxml")
        return base
    if len(axis_counts) != 1:
        base["reasons"].append("axis_lane_count_is_not_unique")
        return base
    axis_count = axis_counts[0]
    boundary_stations = [
        float(item["binding"]["boundary_station_m"])
        for item in members
        if isinstance(item.get("binding"), Mapping)
        and item["binding"].get("boundary_station_m") is not None
    ]
    boundary_points = [
        tuple(float(value) for value in item["boundary_evidence"]["boundary_xy"][:2])
        for item in members
        if isinstance(item.get("boundary_evidence"), Mapping)
        and isinstance(item["boundary_evidence"].get("boundary_xy"), Sequence)
        and len(item["boundary_evidence"]["boundary_xy"]) >= 2
    ]
    if not boundary_stations or not boundary_points:
        base["reasons"].append("approach_boundary_evidence_is_incomplete")
        return base

    conflict_status = str(conflict.get("status", "review_required"))
    merge_points = _unique_merge_points(
        members,
        transformer=transformer,
        tolerance_m=merge_point_tolerance_m,
    )
    if merge_points is None:
        base["reasons"].append("official_merge_point_geometry_is_invalid")
        return base
    if merge_points and len(merge_points) != 1:
        base["reasons"].append("official_merge_points_are_not_unique")
        return base
    if merge_points:
        event_xy = merge_points[0]
        projected = _project_point_to_axis(event_xy, axis)
        if float(projected["distance_m"]) > maximum_merge_axis_distance_m:
            base["reasons"].append("official_merge_point_is_too_far_from_bound_axis")
            return base
        event_kind = "official_merge_event"
        event_source = "official_MAP_lane_merge_points"
        event_raw_xy = [round(value, 6) for value in event_xy]
        event_station = float(projected["station_m"])
        status = "review_required"
        decision = "split_at_official_merge_point_then_prove_lane_conservation"
    else:
        if conflict_status != "pass" or map_count != axis_count:
            base["reasons"].append("lane_profile_mismatch_has_no_official_merge_coordinate")
            return base
        event_station = sum(boundary_stations) / len(boundary_stations)
        event_xy = (
            sum(point[0] for point in boundary_points) / len(boundary_points),
            sum(point[1] for point in boundary_points) / len(boundary_points),
        )
        projected = _project_point_to_axis(event_xy, axis)
        event_kind = "coherent_boundary"
        event_source = "official_MAP_boundary_endpoint_A_and_HH_SIB_projection"
        event_raw_xy = [round(value, 6) for value in event_xy]
        event_station = float(projected["station_m"])
        status = "pass"
        decision = "splice_at_coherent_boundary_then_prove_lane_order"

    merge_lane_ids_starting_at_event = (
        [
            lane_id
            for lane_id in base["official_merge_lane_ids"]
            if any(
                str(member.get("lane_id")) == lane_id
                and isinstance(member.get("binding"), Mapping)
                and member["binding"].get("boundary_station_m") is not None
                and abs(float(member["binding"]["boundary_station_m"]) - event_station) <= 2.0
                for member in members
            )
        ]
        if event_kind == "official_merge_event"
        else []
    )
    if event_kind == "official_merge_event" and not merge_lane_ids_starting_at_event:
        base["reasons"].append("official_merge_event_has_no_boundary_lane_start_identity")
        return base

    proven_through_lane_ids = (
        _merge_through_lane_proof(
            map_lane_ids=map_lane_ids,
            axis_count=axis_count,
            merge_lane_ids_starting_at_event=merge_lane_ids_starting_at_event,
            destination_groups=movement_destinations.get(node_id, {}),
        )
        if event_kind == "official_merge_event"
        else []
    )
    if proven_through_lane_ids:
        status = "pass"
        decision = "split_at_official_merge_point_then_use_destination_provenance"

    axis_point = _axis_point_at_station(axis, event_station)
    junction_stations = [
        float(item["binding"]["junction_station_m"])
        for item in members
        if isinstance(item.get("binding"), Mapping)
        and item["binding"].get("junction_station_m") is not None
    ]
    splice_node_id = (
        f"hh-splice-{_safe_id(node_id)}-a{_safe_id(approach_id)}-"
        f"{_safe_id(role)}-{_safe_id(event_kind)}"
    )
    base.update(
        {
            "status": status,
            "decision": decision,
            "splice_event": {
                "kind": event_kind,
                "source": event_source,
                "splice_node_id": splice_node_id,
                "axis_corridor_id": corridor_id,
                "axis_edge_id": str(projected["edge_id"]),
                "axis_station_m": _rounded(event_station),
                "junction_station_m": (
                    _rounded(sum(junction_stations) / len(junction_stations))
                    if junction_stations
                    else None
                ),
                "axis_projection_distance_m": _rounded(float(projected["distance_m"])),
                "axis_xy": [_rounded(float(value)) for value in axis_point["point_xy"]],
                "map_event_xy": event_raw_xy,
                "lane_count_before": (
                    axis_count if role == "ingress" and event_kind == "official_merge_event" else map_count
                ),
                "lane_count_after": (
                    map_count if role == "ingress" and event_kind == "official_merge_event" else axis_count
                ),
                "map_lane_ids_starting_at_event": (
                    merge_lane_ids_starting_at_event if event_kind == "official_merge_event" else []
                ),
                "map_lane_ids_not_marked_as_starting_at_event": (
                    [lane_id for lane_id in map_lane_ids if lane_id not in merge_lane_ids_starting_at_event]
                    if event_kind == "official_merge_event"
                    else map_lane_ids
                ),
                "official_merge_lane_ids": base["official_merge_lane_ids"],
                "map_lane_ids_proven_through": proven_through_lane_ids,
                "merge_lane_provenance": (
                    {
                        "status": "pass",
                        "basis": "unique_destination_approach_group",
                        "destination_approach_ids": sorted(
                            movement_destinations[node_id][proven_through_lane_ids[0]]
                        ),
                    }
                    if proven_through_lane_ids
                    else {"status": "not_proven"}
                ),
            },
        }
    )
    return base


def _movement_destination_groups(
    reports: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, frozenset[str]]]:
    result: dict[str, dict[str, frozenset[str]]] = {}
    for report in reports:
        node_id = str(report.get("node_id", ""))
        egress_approaches = {
            str(lane.get("lane_id", "")): str(lane.get("egress_approach", ""))
            for lane in report.get("lanes", [])
            if isinstance(lane, Mapping)
            and str(lane.get("lane_id", ""))
            and str(lane.get("egress_approach", ""))
        }
        destinations: dict[str, set[str]] = defaultdict(set)
        for connection in report.get("connections", []):
            if not isinstance(connection, Mapping):
                continue
            ingress = str(connection.get("ingress_lane_id", ""))
            egress = str(connection.get("egress_lane_id", ""))
            destination = egress_approaches.get(egress)
            if ingress and destination:
                destinations[ingress].add(destination)
        result[node_id] = {
            lane_id: frozenset(values)
            for lane_id, values in destinations.items()
        }
    return result


def _merge_through_lane_proof(
    *,
    map_lane_ids: Sequence[str],
    axis_count: int,
    merge_lane_ids_starting_at_event: Sequence[str],
    destination_groups: Mapping[str, frozenset[str]],
) -> list[str]:
    known = [
        lane_id
        for lane_id in map_lane_ids
        if lane_id not in set(merge_lane_ids_starting_at_event)
    ]
    if not known or len(known) >= axis_count:
        return []
    signature = destination_groups.get(known[0], frozenset())
    if len(signature) != 1 or any(
        destination_groups.get(lane_id, frozenset()) != signature for lane_id in known
    ):
        return []
    candidates = [lane_id for lane_id in map_lane_ids if destination_groups.get(lane_id, frozenset()) == signature]
    return candidates if len(candidates) == axis_count else []


def _unique_merge_points(
    members: Sequence[Mapping[str, Any]],
    *,
    transformer: Transformer,
    tolerance_m: float,
) -> list[tuple[float, float]] | None:
    points: list[tuple[float, float]] = []
    for member in members:
        raw = member.get("merge_points")
        if not isinstance(raw, list):
            continue
        for coordinate in raw:
            try:
                points.append(_project_coordinate(coordinate, transformer))
            except (TypeError, ValueError, OfficialSplicePlanError):
                return None
    if not points:
        return []
    clusters: list[list[tuple[float, float]]] = []
    for point in sorted(points):
        matching = [cluster for cluster in clusters if any(_distance(point, item) <= tolerance_m for item in cluster)]
        if not matching:
            clusters.append([point])
        else:
            matching[0].append(point)
            for extra in matching[1:]:
                matching[0].extend(extra)
                clusters.remove(extra)
    return [
        (
            sum(value[0] for value in cluster) / len(cluster),
            sum(value[1] for value in cluster) / len(cluster),
        )
        for cluster in clusters
    ]


def _duplicate_axis_stations(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["axis_corridor_id"])].append(row)
    duplicates: list[dict[str, Any]] = []
    for corridor_id, members in sorted(grouped.items()):
        ordered = sorted(members, key=lambda item: (float(item["station_m"]), str(item["node_id"])))
        for left, right in zip(ordered, ordered[1:]):
            if abs(float(right["station_m"]) - float(left["station_m"])) <= 1.0:
                duplicates.append(
                    {
                        "axis_corridor_id": corridor_id,
                        "left_node_id": str(left["node_id"]),
                        "right_node_id": str(right["node_id"]),
                        "left_station_m": _rounded(float(left["station_m"])),
                        "right_station_m": _rounded(float(right["station_m"])),
                    }
                )
    return duplicates


def _validate_reports(reports: Sequence[Mapping[str, Any]]) -> None:
    seen: set[str] = set()
    for report in reports:
        if report.get("schema") != HAMBURG_MAP_BINDING_SCHEMA or report.get("status") != "pass":
            raise OfficialSplicePlanError("passing Hamburg MAP/KML-to-MAPEM binding reports are required")
        node_id = str(report.get("node_id", ""))
        if not node_id or node_id in seen:
            raise OfficialSplicePlanError("MAP binding reports require unique node_id values")
        seen.add(node_id)
        if not isinstance(report.get("lanes"), list) or not isinstance(report.get("connections"), list):
            raise OfficialSplicePlanError("MAP binding report lanes/connections must be lists")


def _projection(manifest: Mapping[str, Any]) -> dict[str, str]:
    raw = manifest.get("projection")
    if not isinstance(raw, Mapping):
        raise OfficialSplicePlanError("PlainXML manifest projection is required")
    source = str(raw.get("source_crs", ""))
    target = str(raw.get("crs", ""))
    if not source or not target:
        raise OfficialSplicePlanError("PlainXML manifest projection is incomplete")
    return {"source_crs": source, "crs": target}


def _load_json_like(value: Mapping[str, Any] | str | Path, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(value, Mapping):
        payload = json.loads(json.dumps(dict(value), ensure_ascii=False))
        return payload, {"path": None, "sha256": _stable_digest(payload), "identity_method": "canonical_json_sha256"}
    path = Path(value).expanduser().resolve()
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialSplicePlanError(f"cannot read valid {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise OfficialSplicePlanError(f"{label} root must be an object")
    return payload, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "identity_method": "file_bytes_sha256"}

def _promotion_input_hashes(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "map_binding_reports": [item["sha256"] for item in identity["map_binding_reports"]],
        "lane_axis_stitch_plan": identity["lane_axis_stitch_plan"]["canonical_sha256"],
        "plainxml_manifest": identity["plainxml_manifest"]["sha256"],
        "nodes": identity["nodes"]["sha256"],
        "edges": identity["edges"]["sha256"],
        "hh_sib_source_sha256": identity["hh_sib_source_sha256"],
        "plainxml_candidate_id": identity["plainxml_candidate_id"],
    }


def _read_json_file(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialSplicePlanError(f"invalid {label}: {path}") from exc
    if not isinstance(value, dict):
        raise OfficialSplicePlanError(f"{label} root must be an object")
    return value


def _file_identity(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise OfficialSplicePlanError(f"input file does not exist: {path}")
    return {"path": str(path), "sha256": file_sha256(path), "bytes": path.stat().st_size}


def _stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _threshold_payload(value: OfficialLaneAxisStitchThresholds) -> dict[str, Any]:
    return {
        "max_endpoint_distance_m": value.max_endpoint_distance_m,
        "max_heading_delta_deg": value.max_heading_delta_deg,
        "minimum_normalized_score_margin": value.minimum_normalized_score_margin,
        "endpoint_identity_tolerance_m": value.endpoint_identity_tolerance_m,
        "edge_node_alignment_tolerance_m": value.edge_node_alignment_tolerance_m,
        "max_intersection_span_m": value.max_intersection_span_m,
        "profile_transition_station_tolerance_m": value.profile_transition_station_tolerance_m,
    }


def _approach_sort_key(key: tuple[str, str, str]) -> tuple[tuple[int, Any], str, tuple[int, Any]]:
    return (_identifier_key(key[0]), key[1], _identifier_key(key[2]))


def _identifier_key(value: str) -> tuple[int, Any]:
    return (0, int(value)) if value.isdigit() else (1, value)


def _safe_id(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return token or "x"


def _distance(first: Sequence[float], second: Sequence[float]) -> float:
    return math.hypot(float(first[0]) - float(second[0]), float(first[1]) - float(second[1]))


def _rounded(value: float) -> float:
    return round(float(value), 6)


__all__ = [
    "OFFICIAL_SPLICE_PLAN_SCHEMA",
    "OfficialSplicePlanError",
    "build_hamburg_official_splice_plan",
]
